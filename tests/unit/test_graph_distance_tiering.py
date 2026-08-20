"""R-08 (Graph-Distance AST Tiering): verifies `tiered_collapse.py`'s
0-hop/1-hop/2-hop compression tiers, the cross-file symbol-coherence pass
(requirement 2), per-file fail-open fallback (requirement 3), and the
`compress_context(focal_file=...)` wiring in `quor/mcp/server.py`.

Fixture pattern for the MCP-wiring tests mirrors `test_mcp_metadata.py`'s
own `_fresh_dedup_cache`/`_fresh_tracking_db`/`_seed_file` conventions —
both are real module-level, process-lifetime singletons in
`quor.mcp.server` that must be reset per test.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import quor.mcp.server as mcp_server
from quor.mcp.server import compress_context
from quor.mcp.session_dedup import SessionDedupCache
from quor.pipeline.repo_profile import intel_store
from quor.pipeline.repo_profile.intel_model import FileIntelligenceEntry
from quor.pipeline.repo_profile.tiered_collapse import (
    TIER_FOCUS,
    TIER_OUTLINE,
    TIER_SIGNATURES,
    render_tiered_context,
    render_tiered_payload,
)
from quor.pipeline.repo_profile.type_references import referenced_type_names
from quor.tracking.db import TrackingDB


def _write_chain(tmp_path: Path) -> dict[str, FileIntelligenceEntry]:
    """a.py -> b.py -> c.py, with b.py's kept 1-hop signature referencing
    c.py's `Canvas` class (and NOT its `Unrelated` class) — the exact
    shape requirement 2's cross-file coherence pass needs to prove
    something real."""
    (tmp_path / "a.py").write_text(
        "import b\n\ndef use_widget(w: b.Widget) -> None:\n    print(w)\n",
        encoding="utf-8",
    )
    (tmp_path / "b.py").write_text(
        "import c\n\n"
        'class Widget:\n    """A widget."""\n\n'
        "    def render(self, target: c.Canvas) -> str:\n"
        "        x = 1\n        y = 2\n        return str(x + y)\n\n"
        "def helper() -> int:\n    return 42\n",
        encoding="utf-8",
    )
    (tmp_path / "c.py").write_text(
        "class Canvas:\n    def draw(self):\n        pass\n\n"
        "class Unrelated:\n    def other(self):\n        pass\n",
        encoding="utf-8",
    )

    entries: dict[str, FileIntelligenceEntry] = {}
    for name, imported in (("a.py", ["b.py"]), ("b.py", ["c.py"]), ("c.py", [])):
        st = (tmp_path / name).stat()
        entries[name] = FileIntelligenceEntry(
            language="python",
            kind="source",
            imported_files=imported,
            size=st.st_size,
            mtime_ns=st.st_mtime_ns,
        )
    return entries


class TestZeroHopIsFullContent:
    def test_focal_file_content_unmodified(self, tmp_path: Path) -> None:
        entries = _write_chain(tmp_path)
        result = render_tiered_context(tmp_path, entries, "a.py")

        focus = next(f for f in result.files if f.path == "a.py")
        assert focus.tier == TIER_FOCUS
        assert focus.hop == 0
        assert focus.content == (tmp_path / "a.py").read_text(encoding="utf-8")
        assert focus.fallback_reason is None


class TestOneHopIsSignaturesOnly:
    def test_body_dropped_signature_and_docstring_kept(self, tmp_path: Path) -> None:
        entries = _write_chain(tmp_path)
        result = render_tiered_context(tmp_path, entries, "a.py")

        b = next(f for f in result.files if f.path == "b.py")
        assert b.tier == TIER_SIGNATURES
        assert b.hop == 1
        assert "def render(self, target: c.Canvas) -> str:" in b.content
        assert '"""A widget."""' in b.content
        # Body statements must be gone — this is the whole point of the tier.
        assert "x = 1" not in b.content
        assert "y = 2" not in b.content
        assert "return str(x + y)" not in b.content

    def test_rendered_line_count_is_smaller_than_original(self, tmp_path: Path) -> None:
        entries = _write_chain(tmp_path)
        result = render_tiered_context(tmp_path, entries, "a.py")

        b = next(f for f in result.files if f.path == "b.py")
        assert b.rendered_lines < b.original_lines


class TestTwoHopIsOutlineOnly:
    def test_unreferenced_class_collapses_to_bare_one_liner(self, tmp_path: Path) -> None:
        entries = _write_chain(tmp_path)
        result = render_tiered_context(tmp_path, entries, "a.py")

        c = next(f for f in result.files if f.path == "c.py")
        assert c.tier == TIER_OUTLINE
        assert c.hop == 2
        assert "class Unrelated" in c.content
        # Bare one-liner: no members, no method signatures, for the
        # unreferenced class.
        assert "def other" not in c.content


class TestCrossFileTypePreservation:
    """Requirement 2: a 1-hop signature referencing a 2-hop type forces
    that type's full definition into the 2-hop payload, even though the
    rest of that file stays outline-only."""

    def test_referenced_type_gets_full_signature_form(self, tmp_path: Path) -> None:
        entries = _write_chain(tmp_path)
        result = render_tiered_context(tmp_path, entries, "a.py")

        c = next(f for f in result.files if f.path == "c.py")
        assert "class Canvas:" in c.content
        assert "def draw(self):" in c.content
        assert "..." in c.content

    def test_preserved_types_reported_on_result(self, tmp_path: Path) -> None:
        entries = _write_chain(tmp_path)
        result = render_tiered_context(tmp_path, entries, "a.py")

        assert result.preserved_types == ["Canvas"]

    def test_unreferenced_sibling_type_is_not_escalated(self, tmp_path: Path) -> None:
        """Canvas (referenced) gets full form; Unrelated (not referenced,
        same file) must stay a bare one-liner — proves the escalation is
        selective, not "the whole file becomes full once anything in it
        is referenced.\""""
        entries = _write_chain(tmp_path)
        result = render_tiered_context(tmp_path, entries, "a.py")

        c = next(f for f in result.files if f.path == "c.py")
        lines = c.content.splitlines()
        unrelated_line = next(line for line in lines if "Unrelated" in line)
        assert unrelated_line.strip() == "class Unrelated"

    def test_payload_lists_preserved_types_in_trailer(self, tmp_path: Path) -> None:
        entries = _write_chain(tmp_path)
        result = render_tiered_context(tmp_path, entries, "a.py")
        payload = render_tiered_payload(result)

        assert "Cross-file types preserved" in payload
        assert "Canvas" in payload.rsplit("\n\n", 1)[-1]


class TestReferencedTypeNamesHelper:
    """Direct coverage of the annotation-walking primitive the coherence
    pass is built on — see test_type_references-shaped assertions
    (kept here rather than a separate file since it's small and entirely
    in service of this feature)."""

    def test_filters_builtins_and_typing_names(self) -> None:
        import ast

        tree = ast.parse(
            "def f(a: int, b: Optional[Widget]) -> list[Result]:\n    pass\n"
        )
        fn = tree.body[0]
        names = referenced_type_names(fn)
        assert names == {"Widget", "Result"}


class TestFailOpenFallback:
    """Requirement 3: a malformed file at any tier falls back to its own
    full, unmodified content instead of crashing the whole render or
    silently vanishing from the payload."""

    def test_malformed_one_hop_file_falls_back_to_full_content(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("import b\n", encoding="utf-8")
        (tmp_path / "b.py").write_text("def broken(:\n    pass\n", encoding="utf-8")

        entries: dict[str, FileIntelligenceEntry] = {}
        for name, imported in (("a.py", ["b.py"]), ("b.py", [])):
            st = (tmp_path / name).stat()
            entries[name] = FileIntelligenceEntry(
                language="python", kind="source", imported_files=imported,
                size=st.st_size, mtime_ns=st.st_mtime_ns,
            )

        result = render_tiered_context(tmp_path, entries, "a.py")

        b = next(f for f in result.files if f.path == "b.py")
        assert b.fallback_reason == "AST parse failed"
        assert b.content == (tmp_path / "b.py").read_text(encoding="utf-8")

    def test_malformed_two_hop_file_falls_back_to_full_content(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("import b\n", encoding="utf-8")
        (tmp_path / "b.py").write_text("import c\n", encoding="utf-8")
        (tmp_path / "c.py").write_text("class Broken(:\n    pass\n", encoding="utf-8")

        entries: dict[str, FileIntelligenceEntry] = {}
        for name, imported in (("a.py", ["b.py"]), ("b.py", ["c.py"]), ("c.py", [])):
            st = (tmp_path / name).stat()
            entries[name] = FileIntelligenceEntry(
                language="python", kind="source", imported_files=imported,
                size=st.st_size, mtime_ns=st.st_mtime_ns,
            )

        result = render_tiered_context(tmp_path, entries, "a.py")

        c = next(f for f in result.files if f.path == "c.py")
        assert c.fallback_reason == "AST parse failed"
        assert c.content == (tmp_path / "c.py").read_text(encoding="utf-8")

    def test_one_malformed_file_does_not_break_the_rest_of_the_payload(
        self, tmp_path: Path
    ) -> None:
        """A syntax error in b.py must not prevent a.py (0-hop) and c.py
        (2-hop) from still rendering correctly."""
        (tmp_path / "a.py").write_text(
            "import b\n\ndef entry() -> None:\n    pass\n", encoding="utf-8"
        )
        (tmp_path / "b.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
        (tmp_path / "c.py").write_text("class Canvas:\n    pass\n", encoding="utf-8")

        entries: dict[str, FileIntelligenceEntry] = {}
        for name, imported in (("a.py", ["b.py"]), ("b.py", ["c.py"]), ("c.py", [])):
            st = (tmp_path / name).stat()
            entries[name] = FileIntelligenceEntry(
                language="python", kind="source", imported_files=imported,
                size=st.st_size, mtime_ns=st.st_mtime_ns,
            )

        result = render_tiered_context(tmp_path, entries, "a.py")

        assert {f.path for f in result.files} == {"a.py", "b.py", "c.py"}
        a = next(f for f in result.files if f.path == "a.py")
        assert a.fallback_reason is None
        c = next(f for f in result.files if f.path == "c.py")
        assert c.fallback_reason is None

    def test_unrecognized_language_degrades_to_full_content(self, tmp_path: Path) -> None:
        """No `EXTENSION_TO_LANGUAGE` entry (e.g. a plain `.txt` file
        reached via the dependency graph) — no AST support at all, so
        both the 1-hop and 2-hop tiers fall back to full content rather
        than crashing on a missing analyzer."""
        (tmp_path / "a.py").write_text("import notes\n", encoding="utf-8")
        (tmp_path / "notes.txt").write_text("just some notes\n", encoding="utf-8")

        entries: dict[str, FileIntelligenceEntry] = {}
        st_a = (tmp_path / "a.py").stat()
        entries["a.py"] = FileIntelligenceEntry(
            language="python", kind="source", imported_files=["notes.txt"],
            size=st_a.st_size, mtime_ns=st_a.st_mtime_ns,
        )
        st_notes = (tmp_path / "notes.txt").stat()
        entries["notes.txt"] = FileIntelligenceEntry(
            language="unknown", kind="source", size=st_notes.st_size, mtime_ns=st_notes.st_mtime_ns
        )

        result = render_tiered_context(tmp_path, entries, "a.py")

        notes = next(f for f in result.files if f.path == "notes.txt")
        assert notes.fallback_reason == "no AST analyzer for this language"
        assert notes.content == "just some notes\n"


class TestRenderTieredPayload:
    def test_focal_file_section_appears_first(self, tmp_path: Path) -> None:
        entries = _write_chain(tmp_path)
        result = render_tiered_context(tmp_path, entries, "a.py")
        payload = render_tiered_payload(result)

        assert payload.index("### a.py") < payload.index("### b.py") < payload.index("### c.py")

    def test_tier_labels_present_in_headers(self, tmp_path: Path) -> None:
        entries = _write_chain(tmp_path)
        result = render_tiered_context(tmp_path, entries, "a.py")
        payload = render_tiered_payload(result)

        assert "### a.py (focus, 0-hop, full)" in payload
        assert "### b.py (1-hop, signatures)" in payload
        assert "### c.py (2-hop, outline)" in payload


class TestOriginalAndRenderedTokenTotals:
    def test_rendered_tokens_smaller_than_original(self, tmp_path: Path) -> None:
        entries = _write_chain(tmp_path)
        result = render_tiered_context(tmp_path, entries, "a.py")
        assert result.rendered_tokens < result.original_tokens
        assert result.original_tokens == sum(f.original_tokens for f in result.files)


# ---------------------------------------------------------------------------
# MCP wiring: compress_context(focal_file=...)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_dedup_cache() -> Iterator[None]:
    original = mcp_server._dedup_cache
    mcp_server._dedup_cache = SessionDedupCache()
    try:
        yield
    finally:
        mcp_server._dedup_cache = original


@pytest.fixture(autouse=True)
def _fresh_tracking_db(tmp_path: Path) -> Iterator[TrackingDB]:
    db = TrackingDB(db_path=tmp_path / "quor.db")
    original = mcp_server._tracking_db
    mcp_server._tracking_db = db
    try:
        yield db
    finally:
        db.close()
        mcp_server._tracking_db = original


class TestCompressContextFocalFileWiring:
    def test_header_reports_tiering_application(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        entries = _write_chain(tmp_path)
        intel_store.save_file_intelligence(tmp_path, entries)

        result = compress_context(focal_file="a.py")

        assert result.startswith("[Quor Compressed:")
        assert "tiered:" in result
        assert "1 focus" in result
        assert "1 signatures" in result
        assert "1 outline" in result
        assert "1 type(s) preserved" in result

    def test_body_content_absent_but_signature_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        entries = _write_chain(tmp_path)
        intel_store.save_file_intelligence(tmp_path, entries)

        result = compress_context(focal_file="a.py")

        assert "def render(self, target: c.Canvas) -> str:" in result
        assert "return str(x + y)" not in result

    def test_no_repository_intelligence_bails_out(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        result = compress_context(focal_file="a.py")
        assert "run `quor map`" in result

    def test_missing_entry_reports_clearly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        intel_store.save_file_intelligence(tmp_path, {})
        result = compress_context(focal_file="nope.py")
        assert "No repository intelligence entry for 'nope.py'." in result

    def test_raw_text_path_unaffected_when_focal_file_omitted(self) -> None:
        """Zero disruption (requirement 3/existing contract): the default,
        no-focal_file call must behave exactly as it always has."""
        result = compress_context("\n".join(f"line {i}" for i in range(50)))
        assert result.startswith("[Quor Compressed:")
        assert "tiered:" not in result

    def test_outer_fail_open_falls_back_to_full_focal_content(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Simulates a `render_tiered_context()` failure the per-file
        fallbacks inside it don't already cover (e.g. a genuine bug, or —
        as tested here — an unexpected exception raised from the
        orchestrator itself) — the whole tool call must still return the
        focal file's real content, never raise."""
        monkeypatch.chdir(tmp_path)
        entries = _write_chain(tmp_path)
        intel_store.save_file_intelligence(tmp_path, entries)

        def _boom(*args: object, **kwargs: object) -> None:
            raise RuntimeError("graph resolution boom")

        monkeypatch.setattr(mcp_server, "render_tiered_context", _boom)

        result = compress_context(focal_file="a.py")

        assert "graph-distance tiering failed" in result
        assert "def use_widget(w: b.Widget) -> None:" in result
