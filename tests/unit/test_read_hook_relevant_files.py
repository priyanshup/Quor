"""QB-081: end-to-end "Relevant repository files" coverage for the
`claude-read` hook — `quor/adapters/claude_read.py::_maybe_prepend_relevant_files()`.

Driven through the real stdin -> stdout JSON contract, mirroring
`tests/unit/test_read_hook_repo_context.py`'s harness exactly, extended with
a synthetic `transcript_path` JSONL fixture (the one new input this feature
reads that QB-079's own tests never needed) and a `file_intelligence.json`
built directly via `intel_store.save_file_intelligence()` (never a real
`ensure_repo_intelligence()` build).
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import patch

import orjson
import pytest

from quor.adapters.claude_read import MAX_RELEVANT_FILES, run_hook
from quor.pipeline.repo_profile import intel_store
from quor.pipeline.repo_profile.intel_model import FileIntelligenceEntry

# ---------------------------------------------------------------------------
# Helpers (mirrors test_read_hook_repo_context.py's own)
# ---------------------------------------------------------------------------


class _FakeStdout:
    def __init__(self) -> None:
        self.buffer: io.BytesIO = io.BytesIO()

    def write(self, s: str) -> int:
        return 0

    def flush(self) -> None:
        pass


def _read_payload(file_path: str, tool_response: str, *, transcript_path: str = "") -> dict:
    payload = {
        "tool_name": "Read",
        "tool_input": {"file_path": file_path},
        "tool_response": tool_response,
    }
    if transcript_path:
        payload["transcript_path"] = transcript_path
    return payload


def _run_hook(payload: dict) -> dict:
    raw = orjson.dumps(payload).decode("utf-8")
    fake_stdout = _FakeStdout()
    with (
        patch.object(sys, "stdin", io.StringIO(raw)),
        patch.object(sys, "stdout", fake_stdout),
    ):
        run_hook()
    fake_stdout.buffer.seek(0)
    return orjson.loads(fake_stdout.buffer.read())


def _write_transcript(tmp_path: Path, *user_messages: str, name: str = "transcript.jsonl") -> Path:
    """Write a minimal, realistic-shaped transcript JSONL: one `"type":
    "user"` line per message in `user_messages`, each holding its text in
    a single `"text"`-type content block. Only the *last* message is what
    `_extract_last_user_prompt()` is expected to find."""
    transcript = tmp_path / name
    lines = [
        orjson.dumps(
            {
                "type": "user",
                "message": {"role": "user", "content": [{"type": "text", "text": text}]},
            }
        ).decode()
        for text in user_messages
    ]
    transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return transcript


def _write_entry(root: Path, rel_path: str, **overrides: object) -> None:
    fields: dict[str, object] = {
        "language": "python",
        "kind": "source",
        "importance": "Low",
        "top_symbols": [],
    }
    fields.update(overrides)
    existing = intel_store.load_file_intelligence(root) or {}
    existing[rel_path] = FileIntelligenceEntry(**fields)  # type: ignore[arg-type]
    intel_store.save_file_intelligence(root, existing)


_UNSUPPORTED_SOURCE = "fn main() {\n    println!(\"hello\");\n}\n"
"""A `.rs` file — no built-in filter matches this extension at all, so
`_compress_read_output()` always returns `None` for it. Used to prove
QB-081 injects a block even when nothing else about the Read hook does
anything, the key behavioral difference from QB-079's Repository Context
block (which only ever attaches on the already-compressed source-code
branch)."""


class TestBlockAppears:
    def test_injects_even_for_a_file_type_with_no_compression(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "main.rs").write_text(_UNSUPPORTED_SOURCE, encoding="utf-8")
        _write_entry(tmp_path, "src/auth/login.py", top_symbols=["LoginManager"])
        transcript = _write_transcript(tmp_path, "Where is LoginManager defined?")

        result = _run_hook(
            _read_payload(str(tmp_path / "main.rs"), _UNSUPPORTED_SOURCE, transcript_path=str(transcript))
        )
        updated = result["hookSpecificOutput"].get("updatedToolOutput")

        assert isinstance(updated, str)
        assert "Relevant repository files" in updated
        assert "src/auth/login.py" in updated
        assert "Exact symbol: LoginManager" in updated
        assert updated.rstrip().endswith(_UNSUPPORTED_SOURCE.rstrip())


class TestIdenticalPromptIsDeterministic:
    def test_same_prompt_twice_yields_identical_injected_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "main.rs").write_text(_UNSUPPORTED_SOURCE, encoding="utf-8")
        _write_entry(tmp_path, "src/auth/login.py", top_symbols=["LoginManager"])
        transcript = _write_transcript(tmp_path, "Where is LoginManager defined?")
        payload = _read_payload(str(tmp_path / "main.rs"), _UNSUPPORTED_SOURCE, transcript_path=str(transcript))

        first = _run_hook(payload)
        second = _run_hook(payload)

        assert first == second


class TestDuplicateQueryTerms:
    def test_duplicate_identifier_in_prompt_does_not_duplicate_the_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "main.rs").write_text(_UNSUPPORTED_SOURCE, encoding="utf-8")
        _write_entry(tmp_path, "src/auth/login.py", top_symbols=["LoginManager"])
        transcript = _write_transcript(
            tmp_path, "LoginManager needs a fix. Please check LoginManager carefully."
        )

        result = _run_hook(
            _read_payload(str(tmp_path / "main.rs"), _UNSUPPORTED_SOURCE, transcript_path=str(transcript))
        )
        updated = result["hookSpecificOutput"]["updatedToolOutput"]

        assert updated.count("src/auth/login.py") == 1


class TestMultipleQueriesMatchingSameFile:
    def test_two_different_terms_resolving_to_one_file_show_the_stronger_tier(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "main.rs").write_text(_UNSUPPORTED_SOURCE, encoding="utf-8")
        _write_entry(tmp_path, "src/auth/login.py", top_symbols=["LoginManager"])
        # "login.py" (exact filename) and "LoginManager" (exact symbol)
        # both resolve to the same file — exact_symbol is the stronger tier.
        transcript = _write_transcript(tmp_path, "Check login.py and LoginManager together.")

        result = _run_hook(
            _read_payload(str(tmp_path / "main.rs"), _UNSUPPORTED_SOURCE, transcript_path=str(transcript))
        )
        updated = result["hookSpecificOutput"]["updatedToolOutput"]

        assert updated.count("src/auth/login.py") == 1
        assert "Exact symbol: LoginManager" in updated


class TestCacheUnavailable:
    def test_no_file_intelligence_cache_omits_the_block(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "main.rs").write_text(_UNSUPPORTED_SOURCE, encoding="utf-8")
        transcript = _write_transcript(tmp_path, "Where is LoginManager defined?")

        result = _run_hook(
            _read_payload(str(tmp_path / "main.rs"), _UNSUPPORTED_SOURCE, transcript_path=str(transcript))
        )

        # No compression path and no relevant-files match -> the whole
        # hook is a true passthrough, exactly as it was before QB-081.
        assert "updatedToolOutput" not in result["hookSpecificOutput"]


class TestEmptyExtraction:
    def test_prose_with_no_identifier_shaped_terms_omits_the_block(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "main.rs").write_text(_UNSUPPORTED_SOURCE, encoding="utf-8")
        _write_entry(tmp_path, "src/auth/login.py", top_symbols=["LoginManager"])
        transcript = _write_transcript(tmp_path, "Can you help me understand how this works?")

        result = _run_hook(
            _read_payload(str(tmp_path / "main.rs"), _UNSUPPORTED_SOURCE, transcript_path=str(transcript))
        )

        assert "updatedToolOutput" not in result["hookSpecificOutput"]

    def test_no_transcript_path_omits_the_block(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "main.rs").write_text(_UNSUPPORTED_SOURCE, encoding="utf-8")
        _write_entry(tmp_path, "src/auth/login.py", top_symbols=["LoginManager"])

        result = _run_hook(_read_payload(str(tmp_path / "main.rs"), _UNSUPPORTED_SOURCE))

        assert "updatedToolOutput" not in result["hookSpecificOutput"]


class TestMaximumResultCap:
    def test_more_matches_than_the_cap_are_truncated(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "main.rs").write_text(_UNSUPPORTED_SOURCE, encoding="utf-8")
        # One quoted query term ("Handler") is enough to prefix-match every
        # one of these 10 files' top symbols — deliberately not 10 distinct
        # query terms, since `query_extract.MAX_QUERY_TERMS` (4) would cap
        # those before `MAX_RELEVANT_FILES` (5) ever came into play. This
        # isolates the *result* cap from the *query-term* cap.
        for i in range(MAX_RELEVANT_FILES + 5):
            _write_entry(tmp_path, f"src/mod_{i}.py", top_symbols=[f"Handler{i}"])
        transcript = _write_transcript(tmp_path, "Check `Handler` implementations")

        result = _run_hook(
            _read_payload(str(tmp_path / "main.rs"), _UNSUPPORTED_SOURCE, transcript_path=str(transcript))
        )
        updated = result["hookSpecificOutput"]["updatedToolOutput"]

        assert updated.count("Symbol prefix:") == MAX_RELEVANT_FILES


class TestDeterministicOrdering:
    def test_stronger_tier_files_are_listed_before_weaker_tier_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "main.rs").write_text(_UNSUPPORTED_SOURCE, encoding="utf-8")
        _write_entry(tmp_path, "src/auth/login.py", top_symbols=["LoginManager"])
        _write_entry(tmp_path, "src/auth/session.py", imported_files=["src/auth/login.py"])
        transcript = _write_transcript(tmp_path, "Check LoginManager and session.py")

        result = _run_hook(
            _read_payload(str(tmp_path / "main.rs"), _UNSUPPORTED_SOURCE, transcript_path=str(transcript))
        )
        updated = result["hookSpecificOutput"]["updatedToolOutput"]

        # exact_symbol (login.py, via LoginManager) outranks exact_filename
        # (session.py, matched directly by name).
        assert updated.index("src/auth/login.py") < updated.index("src/auth/session.py")


class TestExcludesTheFileBeingRead:
    def test_the_file_currently_being_read_is_never_recommended(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "main.rs").write_text(_UNSUPPORTED_SOURCE, encoding="utf-8")
        _write_entry(tmp_path, "main.rs", top_symbols=["Main"])
        transcript = _write_transcript(tmp_path, "Check `Main` please")

        result = _run_hook(
            _read_payload(str(tmp_path / "main.rs"), _UNSUPPORTED_SOURCE, transcript_path=str(transcript))
        )

        assert "updatedToolOutput" not in result["hookSpecificOutput"]
