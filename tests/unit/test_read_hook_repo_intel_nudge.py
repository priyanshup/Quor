"""QB-090: `claude-read` hook coverage for the repository-intelligence
onboarding nudge — `quor/adapters/claude_read.py::_maybe_prepend_repo_intel_nudge()`.

Driven through the real stdin -> stdout JSON contract, mirroring
`test_read_hook_repo_context.py`'s harness exactly. `monkeypatch.chdir()`s
into an isolated `tmp_path`, matching `Path.cwd()` being exactly what this
feature treats as the repository root.
"""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import orjson
import pytest

from quor.adapters.claude_read import run_hook
from quor.pipeline.repo_profile.nudge import MAX_NEVER_BUILT_SHOWS
from quor.tracking.db import InvocationRecord, count_tokens


class _FakeStdout:
    def __init__(self) -> None:
        self.buffer: io.BytesIO = io.BytesIO()

    def write(self, s: str) -> int:
        return 0

    def flush(self) -> None:
        pass


def _read_payload(file_path: str, tool_response: str, *, transcript_path: str | None = "transcript.jsonl") -> dict:
    payload: dict = {
        "tool_name": "Read",
        "tool_input": {"file_path": file_path},
        "tool_response": tool_response,
    }
    if transcript_path is not None:
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


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A real git repo, chdir'd into — separate from `tmp_path` itself so
    the isolated `platformdirs.user_data_dir` cache (a sibling under
    `tmp_path/data/quor`, per `tests/conftest.py`) never lands inside the
    git repo under test (same care `test_repo_intel_nudge.py` takes)."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "unrelated.xyz").write_text("nothing quor filters\n", encoding="utf-8")
    _init_git_repo(root)
    monkeypatch.chdir(root)
    return root


class _FakeTracking:
    def __init__(self) -> None:
        self.records: list[InvocationRecord] = []

    def record(self, rec: InvocationRecord) -> None:
        self.records.append(rec)


class TestTrackingAccuracy:
    """QB-094 regression guard: the nudge is prepended in `_handle_text()`,
    after the producer has already returned — before this fix, a pure
    passthrough Read the nudge alone turned into real output was tracked
    as a zero-token no-op. See
    tests/unit/test_read_hook_tracking_accuracy.py for the full scenario
    matrix."""

    def test_tracked_final_tokens_include_the_nudge(self, repo: Path) -> None:
        payload = _read_payload(str(repo / "unrelated.xyz"), "nothing quor filters\n")
        raw = orjson.dumps(payload).decode("utf-8")
        fake_stdout = _FakeStdout()
        tracking = _FakeTracking()
        with (
            patch.object(sys, "stdin", io.StringIO(raw)),
            patch.object(sys, "stdout", fake_stdout),
        ):
            run_hook(tracking=tracking)
        fake_stdout.buffer.seek(0)
        result = orjson.loads(fake_stdout.buffer.read())
        updated = result["hookSpecificOutput"]["updatedToolOutput"]

        assert len(tracking.records) == 1
        rec = tracking.records[0]
        assert rec.final_tokens == count_tokens(updated)
        assert rec.was_passthrough is True
        assert rec.final_tokens > rec.original_tokens


class TestNeverBuiltNudgeInReadHook:
    def test_nudge_appears_on_an_otherwise_pure_passthrough_read(self, repo: Path) -> None:
        """No filter matches `.xyz`, and no repo-intelligence cache exists —
        before this feature, `_handle_text()` would have returned no
        `updatedToolOutput` at all (pure passthrough). This pins that the
        nudge alone is now enough to produce one."""
        response = _run_hook(_read_payload(str(repo / "unrelated.xyz"), "nothing quor filters\n"))

        output = response["hookSpecificOutput"].get("updatedToolOutput", "")
        assert "Repository Tip" in output
        assert "quor map" in output
        assert "nothing quor filters" in output  # original content still present

    def test_throttled_after_max_shows(self, repo: Path) -> None:
        outputs = [
            _run_hook(_read_payload(str(repo / "unrelated.xyz"), "content\n"))["hookSpecificOutput"]
            for _ in range(MAX_NEVER_BUILT_SHOWS + 2)
        ]
        tip_shown = ["Repository Tip" in o.get("updatedToolOutput", "") for o in outputs]

        assert tip_shown[:MAX_NEVER_BUILT_SHOWS] == [True] * MAX_NEVER_BUILT_SHOWS
        assert tip_shown[MAX_NEVER_BUILT_SHOWS:] == [False, False]

    def test_silent_once_repository_intelligence_is_built(self, repo: Path) -> None:
        from quor.pipeline.repo_profile.intel import ensure_repo_intelligence

        ensure_repo_intelligence(repo)

        response = _run_hook(_read_payload(str(repo / "unrelated.xyz"), "content\n"))

        assert "updatedToolOutput" not in response["hookSpecificOutput"]


class TestRepoIntelNudgeRequiresTranscriptPath:
    def test_silent_without_a_transcript_path(self, repo: Path) -> None:
        """Regression test: a synthetic tool call with no `transcript_path`
        (exactly what this codebase's own other Read-hook unit tests
        construct) must never trigger this feature — see
        `_maybe_prepend_repo_intel_nudge()`'s own docstring for the real
        test-suite breakage this gate was added to fix."""
        payload = _read_payload(str(repo / "unrelated.xyz"), "content\n", transcript_path=None)

        response = _run_hook(payload)

        assert "updatedToolOutput" not in response["hookSpecificOutput"]

    def test_fires_once_a_transcript_path_is_present(self, repo: Path) -> None:
        payload = _read_payload(str(repo / "unrelated.xyz"), "content\n", transcript_path="transcript.jsonl")

        response = _run_hook(payload)

        assert "Repository Tip" in response["hookSpecificOutput"].get("updatedToolOutput", "")


class TestRepoIntelNudgeFailsOpen:
    def test_an_internal_error_never_breaks_the_hook(self, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "quor.adapters.claude_read.compute_hook_nudge",
            lambda _root: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        response = _run_hook(_read_payload(str(repo / "unrelated.xyz"), "content\n"))

        # Fails open to plain passthrough — no nudge, no crash, no updatedToolOutput.
        assert "updatedToolOutput" not in response["hookSpecificOutput"]
        assert response["hookSpecificOutput"]["hookEventName"] == "PostToolUse"

    def test_missing_tool_response_is_a_no_op(self) -> None:
        """Unlike `_maybe_prepend_relevant_files` (which genuinely needs
        `file_path` to exclude the file being read from its own results),
        this feature has nothing file-specific to say — it's about the
        repo, not the file — so a missing `file_path` alone doesn't
        suppress it (see the sibling test above). The one thing that does
        suppress it is `base` being `None` at all, e.g. a non-string
        `tool_response` (`_compress_read_output`'s own early exit)."""
        response = _run_hook({"tool_name": "Read", "tool_input": {}, "tool_response": {"not": "a string"}})
        assert "updatedToolOutput" not in response["hookSpecificOutput"]
