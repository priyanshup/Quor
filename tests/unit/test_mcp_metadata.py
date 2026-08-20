"""Metadata-Enriched MCP Payloads: verifies `get_repo_context` and
`compress_context` carry the structural/graph-distance anchors an agent
needs to reason about symbol importance, line spans, and dependency
relationships, without disrupting either tool's existing execution path
(dedup, tracking, QB-114 dispatcher-pipeline routing).

- AST node classification (`Defines: Name (kind)`) — `symbol_kinds.py`,
  a fresh single-file parse, exercised here through real Python source
  files written to `tmp_path` (not mocked — the point is proving the
  real `ast_summarize` extractor wiring works end to end).
- Graph-distance tiering (`(N-hop)` annotations on "Relevant repository
  files" matches) — `graph_distance.py`'s BFS, already covered in
  isolation by `test_graph_distance.py`; this file only verifies it's
  correctly wired into `get_repo_context`.
- Line-span/token metadata on `compress_context`'s header.

Fixture pattern (`_fresh_dedup_cache`/`_fresh_tracking_db`) copied from
`test_mcp_server.py`'s own docstring rationale — both are real
module-level, process-lifetime singletons in `quor.mcp.server` that must
be reset per test.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import quor.mcp.server as mcp_server
from quor.mcp.server import compress_context, get_repo_context
from quor.mcp.session_dedup import SessionDedupCache
from quor.pipeline.repo_profile import intel_store
from quor.pipeline.repo_profile.intel_model import FileIntelligenceEntry
from quor.tracking.db import TrackingDB


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


def _seed_file(
    root: Path,
    entries: dict[str, FileIntelligenceEntry],
    rel_path: str,
    source: str,
    *,
    top_symbols: list[str] | None = None,
    imported_files: list[str] | None = None,
) -> None:
    """Write real source to `root / rel_path` and add a matching
    `FileIntelligenceEntry` (real size/mtime_ns, so `_repo_context_block`'s
    staleness check passes) to `entries`. Callers seed every file they need
    into the same dict, then call `intel_store.save_file_intelligence()`
    once at the end."""
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    st = path.stat()
    entries[rel_path] = FileIntelligenceEntry(
        language="python",
        kind="source",
        importance="Low",
        top_symbols=top_symbols or [],
        imported_files=imported_files or [],
        size=st.st_size,
        mtime_ns=st.st_mtime_ns,
    )


_SAMPLE_SOURCE = '''\
class Widget:
    def render(self) -> str:
        return "widget"


def build_widget() -> Widget:
    return Widget()
'''


class TestAstNodeClassificationSingleFile:
    """Requirement 1/4: AST node classifications (class/function/etc.) for
    a single-file context."""

    def test_defines_line_shows_real_ast_kinds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        entries: dict[str, FileIntelligenceEntry] = {}
        _seed_file(
            tmp_path,
            entries,
            "widget.py",
            _SAMPLE_SOURCE,
            top_symbols=["Widget", "build_widget"],
        )
        intel_store.save_file_intelligence(tmp_path, entries)

        result = get_repo_context(file_path="widget.py")

        assert "Defines: Widget (class), build_widget (function)" in result

    def test_graph_depth_focus_marker_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Requirement 1: graph-distance depth — the requested file is
        always this call's own 0-hop anchor."""
        monkeypatch.chdir(tmp_path)
        entries: dict[str, FileIntelligenceEntry] = {}
        _seed_file(tmp_path, entries, "widget.py", _SAMPLE_SOURCE, top_symbols=["Widget"])
        intel_store.save_file_intelligence(tmp_path, entries)

        result = get_repo_context(file_path="widget.py")

        assert "Graph depth: 0-hop (focus)" in result

    def test_unrecognized_extension_falls_back_to_bare_names(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fail-open: a language with no registered AST extractor (or a
        file `symbol_kinds.py` simply can't classify) must never break the
        tool call — names render bare, exactly as before this feature."""
        monkeypatch.chdir(tmp_path)
        entries: dict[str, FileIntelligenceEntry] = {}
        path = tmp_path / "data.txt"
        path.write_text("not real code\n", encoding="utf-8")
        st = path.stat()
        entries["data.txt"] = FileIntelligenceEntry(
            language="unknown",
            kind="source",
            importance="Low",
            top_symbols=["SOME_CONST"],
            size=st.st_size,
            mtime_ns=st.st_mtime_ns,
        )
        intel_store.save_file_intelligence(tmp_path, entries)

        result = get_repo_context(file_path="data.txt")

        assert "Defines: SOME_CONST" in result
        assert "SOME_CONST (" not in result

    def test_no_symbols_still_renders_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        entries: dict[str, FileIntelligenceEntry] = {}
        _seed_file(tmp_path, entries, "empty.py", "# just a comment\n", top_symbols=[])
        intel_store.save_file_intelligence(tmp_path, entries)

        result = get_repo_context(file_path="empty.py")

        assert "Defines: (none)" in result


class TestGraphDistanceMultiFile:
    """Requirement 1/4: correct graph-depth annotations for a multi-file
    (query-driven) context, anchored to a focus file_path."""

    def _seed_chain(self, tmp_path: Path) -> dict[str, FileIntelligenceEntry]:
        """a.py -> b.py -> c.py: b.py is a's direct (1-hop) import, c.py is
        reachable only via b.py (2-hop)."""
        entries: dict[str, FileIntelligenceEntry] = {}
        _seed_file(
            tmp_path,
            entries,
            "a.py",
            "import b\n",
            top_symbols=[],
            imported_files=["b.py"],
        )
        _seed_file(
            tmp_path,
            entries,
            "b.py",
            "class TargetSymbol:\n    pass\n",
            top_symbols=["TargetSymbol"],
            imported_files=["c.py"],
        )
        _seed_file(
            tmp_path,
            entries,
            "c.py",
            "class TargetSymbol:\n    pass\n",
            top_symbols=["TargetSymbol"],
        )
        return entries

    def test_direct_import_annotated_one_hop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        intel_store.save_file_intelligence(tmp_path, self._seed_chain(tmp_path))

        result = get_repo_context(file_path="a.py", query="TargetSymbol")

        assert "- b.py (1-hop)" in result

    def test_transitive_import_annotated_two_hop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        intel_store.save_file_intelligence(tmp_path, self._seed_chain(tmp_path))

        result = get_repo_context(file_path="a.py", query="TargetSymbol")

        assert "- c.py (2-hop)" in result

    def test_no_anchor_means_no_hop_annotation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A query-only call (no file_path) has no anchor to measure
        distance from — matches must render exactly like before this
        feature, with no "(N-hop)" suffix at all."""
        monkeypatch.chdir(tmp_path)
        intel_store.save_file_intelligence(tmp_path, self._seed_chain(tmp_path))

        result = get_repo_context(query="TargetSymbol")

        assert "-hop)" not in result


class TestCompressContextLineTokenMetadata:
    """Requirement 1: original vs. compressed line spans and token
    reduction estimates on compress_context's response."""

    _COMPRESSIBLE_TEXT = ("INFO: heartbeat ok\n" * 300) + "ERROR: distinct\n"

    def test_header_reports_line_and_token_spans(self) -> None:
        result = compress_context(self._COMPRESSIBLE_TEXT)

        assert "lines " in result
        assert "tokens " in result
        header = result.splitlines()[0]
        assert "->" in header

    def test_reported_line_counts_are_self_consistent(self) -> None:
        import re

        result = compress_context(self._COMPRESSIBLE_TEXT)
        header = result.splitlines()[0]

        match = re.search(r"lines (\d+)->(\d+)", header)
        assert match is not None
        original_lines, compressed_lines = int(match.group(1)), int(match.group(2))
        assert original_lines == len(self._COMPRESSIBLE_TEXT.splitlines())
        # Massively repetitive input: the generic filter's dedup stage
        # collapses hundreds of lines down to a couple.
        assert compressed_lines < original_lines

    def test_reported_token_counts_are_self_consistent(self) -> None:
        import re

        from quor.tracking.db import count_tokens

        result = compress_context(self._COMPRESSIBLE_TEXT)
        header = result.splitlines()[0]

        match = re.search(r"tokens (\d+)->(\d+)", header)
        assert match is not None
        original_tokens = int(match.group(1))
        assert original_tokens == count_tokens(self._COMPRESSIBLE_TEXT)


class TestZeroDisruptionToExistingPaths:
    """Requirement 4: zero disruption to compress_context/get_repo_context
    execution — dedup, tracking, and QB-114 dispatcher routing all keep
    working exactly as before this feature."""

    def test_compress_context_still_returns_quor_compressed_prefix(self) -> None:
        result = compress_context("\n".join(f"line {i}" for i in range(50)))
        assert result.startswith("[Quor Compressed:")

    def test_compress_context_dedup_still_fires(self) -> None:
        text = "\n".join(f"line {i}" for i in range(50))
        compress_context(text)
        second = compress_context(text)
        assert "unchanged since last shown" in second

    def test_compress_context_tee_footer_still_appended(self) -> None:
        text = ("INFO: heartbeat ok\n" * 300) + "ERROR: distinct\n"
        result = compress_context(text)
        assert "[full output:" in result

    def test_get_repo_context_bailout_unaffected_when_no_intelligence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        result = get_repo_context(file_path="whatever.py")
        assert "run `quor map`" in result

    def test_get_repo_context_missing_entry_message_unaffected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        intel_store.save_file_intelligence(tmp_path, {})
        result = get_repo_context(file_path="missing.py")
        assert "No repository intelligence entry for 'missing.py'." in result

    def test_no_args_call_unaffected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        intel_store.save_file_intelligence(tmp_path, {"a.py": FileIntelligenceEntry(language="python", kind="source")})
        result = get_repo_context()
        assert "Repository intelligence is available for 1 file(s)" in result
