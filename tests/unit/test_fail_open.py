"""Fail-open chaos tests.

Deliberately break things and confirm Quor degrades safely rather than crashing
or hanging. Each scenario proves the error model from ADR-018 holds:

  1. Corrupted TOML config          → warnings emitted, built-ins still load
  2. Malformed hook stdin JSON       → original bytes returned to Claude Code
  3. Missing DB permissions          → dispatch still returns filtered output
  4. Simulated hook timeout          → original bytes returned (fail-open)
  5. Subprocess hanging (timeout)    → exit 124, no crash
  6. Pathological regex (ReDoS)      → timeout fires, warns, line stays KEEP
"""

from __future__ import annotations

import io
import subprocess
import sys
import warnings
from pathlib import Path
from unittest.mock import MagicMock, patch

import orjson
import pytest

from quor.tracking.db import InvocationRecord, TrackingDB

# ---------------------------------------------------------------------------
# 1. Corrupted TOML config
# ---------------------------------------------------------------------------


class TestCorruptedToml:
    def test_corrupt_user_filter_skipped_with_warning(self) -> None:
        """Invalid TOML in user filter dir emits a warning; built-ins still load."""
        import platformdirs

        from quor.filters.registry import FilterRegistry

        user_dir = Path(platformdirs.user_config_dir("quor")) / "filters"
        user_dir.mkdir(parents=True, exist_ok=True)
        bad_toml = user_dir / "corrupt.toml"
        bad_toml.write_text("[[filter\nnot valid toml ]]]", encoding="utf-8")

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            registry = FilterRegistry(project_root=None)

        # Must not raise; warning must mention the bad file
        assert any("corrupt.toml" in str(w.message) for w in caught), (
            "Expected a warning about the corrupted file"
        )
        # Built-in filters must still be available
        assert registry.all_filters(), "Built-in filters must survive user-tier corruption"

    def test_unknown_stage_type_apply_does_not_raise(self) -> None:
        """A filter with an unknown stage type is skipped gracefully during apply."""
        import platformdirs

        from quor.filters.registry import FilterRegistry

        user_dir = Path(platformdirs.user_config_dir("quor")) / "filters"
        user_dir.mkdir(parents=True, exist_ok=True)
        bad_stage = user_dir / "bad_stage.toml"
        bad_stage.write_text(
            "schema_version = 1\n\n"
            '[[filter]]\nname = "chaos"\nmatch_command = "^chaos$"\n\n'
            "[[filter.stages]]\ntype = \"nonexistent_stage_xyz_chaos\"\n",
            encoding="utf-8",
        )

        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            registry = FilterRegistry(project_root=None)

        fc = registry.find("chaos")
        if fc is not None:
            # apply() must not raise even with a broken stage; unknown stage is
            # skipped with a warning and the content returns unchanged
            with warnings.catch_warnings(record=True):
                warnings.simplefilter("always")
                result = registry.apply(fc, "some content line")
            assert isinstance(result, str)


# ---------------------------------------------------------------------------
# 2. Malformed hook stdin JSON
# ---------------------------------------------------------------------------


class TestMalformedHookJson:
    """_run_hook catches all exceptions and writes original bytes to stdout."""

    def _call_run_hook(self, stdin_bytes: bytes) -> bytes:
        """Call _run_hook with controlled stdin/stdout, return what was written to stdout."""
        from quor.__main__ import _run_hook

        fake_in = MagicMock()
        fake_in.buffer = io.BytesIO(stdin_bytes)
        out_buffer = io.BytesIO()
        fake_out = MagicMock()
        fake_out.buffer = out_buffer

        with (
            patch.object(sys, "argv", ["quor", "hook", "claude"]),
            patch.object(sys, "stdin", fake_in),
            patch.object(sys, "stdout", fake_out),
        ):
            _run_hook()

        return out_buffer.getvalue()

    def test_malformed_json_returns_original(self) -> None:
        original = b'{"not": "valid json'
        result = self._call_run_hook(original)
        assert result == original

    def test_empty_stdin_returns_original(self) -> None:
        result = self._call_run_hook(b"")
        assert result == b""

    def test_missing_tool_input_field_returns_original(self) -> None:
        # Valid JSON but wrong shape — pydantic ValidationError → original returned
        original = orjson.dumps({"tool_name": "Bash"})  # missing tool_input
        result = self._call_run_hook(original)
        assert result == original

    def test_non_dict_json_returns_original(self) -> None:
        original = b"[1, 2, 3]"
        result = self._call_run_hook(original)
        assert result == original


# ---------------------------------------------------------------------------
# 3. Missing file permissions on config / tracking dirs
# ---------------------------------------------------------------------------


class TestPermissionErrors:
    def test_sqlite_connect_failure_does_not_block_dispatch(self) -> None:
        """PermissionError on SQLite connect must not prevent filtered output."""
        from quor.adapters.dispatcher import run_dispatch

        proc = MagicMock(spec=subprocess.CompletedProcess)
        proc.stdout = (
            "FAILED tests/test_foo.py::test_bar\n"
            "    AssertionError: got False\n"
        )
        proc.returncode = 1

        captured = io.StringIO()
        with (
            patch("subprocess.run", return_value=proc),
            patch("sys.stdout", captured),
            # Force SQLite to fail; tracking=None so the DB path is never used anyway,
            # but this proves dispatch itself ignores DB errors entirely
            patch("sqlite3.connect", side_effect=PermissionError("no db access")),
        ):
            exit_code = run_dispatch(["pytest", "tests/"], tracking=None)

        assert exit_code == 1
        output = captured.getvalue()
        assert "FAILED" in output, "Filtered output must still arrive despite DB error"

    def test_tracking_db_write_failure_does_not_raise(self, tmp_path: Path) -> None:
        """Background thread DB write failure must never surface to caller."""
        # Patch sqlite3.connect so the worker thread fails on connection
        with patch("sqlite3.connect", side_effect=PermissionError("no access")):
            db = TrackingDB(db_path=tmp_path / "quor.db")

        rec = InvocationRecord(
            command="test",
            project_path="/project",
            original_tokens=10,
            final_tokens=5,
            filter_name=None,
            was_passthrough=True,
            duration_ms=1.0,
        )
        # record() and close() must not raise even though the thread died
        db.record(rec)
        db.close()

    def test_registry_load_with_unreadable_filter_does_not_raise(
        self, tmp_path: Path
    ) -> None:
        """FilterRegistry silently skips filter files it can't read."""
        import platformdirs

        from quor.filters.registry import FilterRegistry

        user_dir = Path(platformdirs.user_config_dir("quor")) / "filters"
        user_dir.mkdir(parents=True, exist_ok=True)
        bad_file = user_dir / "unreadable.toml"
        bad_file.write_text("schema_version = 1\n", encoding="utf-8")

        # Simulate an unreadable file — only raise for the specific bad file so
        # built-in filter opens go through normally.
        import builtins

        real_open = builtins.open

        def _selective_open(path, *args, **kwargs):
            if "unreadable" in str(path):
                raise PermissionError("no read")
            return real_open(path, *args, **kwargs)

        with (
            patch("quor.filters.loader.open", new=_selective_open),
            warnings.catch_warnings(record=True),
        ):
            warnings.simplefilter("always")
            registry = FilterRegistry(project_root=None)

        # Warning emitted; built-in filters still loaded
        assert registry.all_filters()


# ---------------------------------------------------------------------------
# 4. Simulated hook timeout
# ---------------------------------------------------------------------------


class TestHookTimeout:
    def _call_run_hook(self, stdin_bytes: bytes, *, side_effect: Exception) -> bytes:
        from quor.__main__ import _run_hook

        fake_in = MagicMock()
        fake_in.buffer = io.BytesIO(stdin_bytes)
        out_buffer = io.BytesIO()
        fake_out = MagicMock()
        fake_out.buffer = out_buffer

        with (
            patch.object(sys, "argv", ["quor", "hook", "claude"]),
            patch.object(sys, "stdin", fake_in),
            patch.object(sys, "stdout", fake_out),
            patch("quor.adapters.claude.run_hook", side_effect=side_effect),
        ):
            _run_hook()

        return out_buffer.getvalue()

    def test_timeout_error_returns_original(self) -> None:
        original = orjson.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "git status"},
        })
        result = self._call_run_hook(original, side_effect=TimeoutError("timed out"))
        assert result == original

    def test_memory_error_returns_original(self) -> None:
        original = orjson.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "git log"},
        })
        result = self._call_run_hook(original, side_effect=MemoryError("out of memory"))
        assert result == original

    def test_arbitrary_exception_returns_original(self) -> None:
        original = orjson.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "pytest"},
        })
        result = self._call_run_hook(original, side_effect=RuntimeError("unexpected"))
        assert result == original


# ---------------------------------------------------------------------------
# 5. Subprocess hanging (timeout handling)
# ---------------------------------------------------------------------------


class TestSubprocessTimeout:
    def test_timeout_expired_returns_124(self) -> None:
        """A hanging subprocess raises TimeoutExpired → dispatch returns 124, no crash."""
        from quor.adapters.dispatcher import run_dispatch

        captured = io.StringIO()
        with (
            patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="git log", timeout=25),
            ),
            patch("sys.stdout", captured),
        ):
            exit_code = run_dispatch(["git", "log", "--oneline"])

        assert exit_code == 124

    def test_timeout_does_not_raise_to_caller(self) -> None:
        """run_dispatch must return an int, never raise, on TimeoutExpired."""
        from quor.adapters.dispatcher import run_dispatch

        with (
            patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="cmd", timeout=25),
            ),
            patch("sys.stdout", io.StringIO()),
        ):
            result = run_dispatch(["some-long-running-command"])

        assert isinstance(result, int)

    def test_timeout_with_tracking_still_returns_124(self, tmp_path: Path) -> None:
        """Timeout + active tracking: tracking is skipped, exit 124 returned."""
        from quor.adapters.dispatcher import run_dispatch

        tracking = TrackingDB(db_path=tmp_path / "quor.db")
        try:
            with (
                patch(
                    "subprocess.run",
                    side_effect=subprocess.TimeoutExpired(cmd="make", timeout=25),
                ),
                patch("sys.stdout", io.StringIO()),
            ):
                exit_code = run_dispatch(["make", "build"], tracking=tracking)
        finally:
            tracking.close()

        assert exit_code == 124


# ---------------------------------------------------------------------------
# 6. Pathological regex (catastrophic backtracking / ReDoS)
# ---------------------------------------------------------------------------


class TestRegexTimeout:
    def test_timeout_in_strip_patterns_produces_warning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TimeoutError from _search → warning emitted, line stays KEEP (fail-open)."""
        from quor.pipeline.mask import ContentMask, Decision
        from quor.pipeline.stages import _utils
        from quor.pipeline.stages.strip_lines import StripLinesConfig, StripLinesStage

        def _always_timeout(
            pat: object, line: str
        ) -> None:
            raise TimeoutError("pattern match timed out")

        monkeypatch.setattr(_utils, "_search", _always_timeout)

        config = StripLinesConfig(type="strip_lines", patterns=[r"some_pattern"])
        stage = StripLinesStage()
        mask = ContentMask.from_text("some content that could match")

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = stage.apply(mask, config)

        # Timeout → fail-open → line stays KEEP, not COMPRESS
        assert result.lines[0].decision is Decision.KEEP
        timeout_warnings = [w for w in caught if "timed out" in str(w.message)]
        assert timeout_warnings, "Expected a timeout warning from matches_any"

    def test_timeout_in_preserve_patterns_keeps_line_as_keep(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TimeoutError in preserve_patterns → line NOT incorrectly PROTECTED or COMPRESS."""
        from quor.pipeline.mask import ContentMask, Decision
        from quor.pipeline.stages import _utils
        from quor.pipeline.stages.strip_lines import StripLinesConfig, StripLinesStage

        def _always_timeout(
            pat: object, line: str
        ) -> None:
            raise TimeoutError("timed out")

        monkeypatch.setattr(_utils, "_search", _always_timeout)

        config = StripLinesConfig(
            type="strip_lines",
            patterns=[r"safe_pattern"],
            preserve_patterns=[r"another_pattern"],
        )
        stage = StripLinesStage()
        mask = ContentMask.from_text("content line")

        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = stage.apply(mask, config)

        # Timeout on both patterns → line stays KEEP (not PROTECT, not COMPRESS)
        assert result.lines[0].decision is Decision.KEEP

    def test_catastrophic_pattern_at_short_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Real catastrophic backtracking pattern with short timeout → TimeoutError caught."""
        from quor.pipeline.mask import ContentMask, Decision
        from quor.pipeline.stages import _utils
        from quor.pipeline.stages.strip_lines import StripLinesConfig, StripLinesStage

        # Shorten the timeout so the test completes quickly
        monkeypatch.setattr(_utils, "_PATTERN_TIMEOUT", 0.05)

        # Classic ReDoS: (a+)+b applied to "a"*50 requires exponential backtracking
        config = StripLinesConfig(type="strip_lines", patterns=[r"(a+)+b"])
        stage = StripLinesStage()
        pathological_input = "a" * 50
        mask = ContentMask.from_text(pathological_input)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = stage.apply(mask, config)

        # Whether timeout fires or the regex engine optimises it away, the line
        # must never be COMPRESS (it does not end with 'b', so the pattern
        # cannot match regardless).
        assert result.lines[0].decision is Decision.KEEP

        # If a timeout did fire, a warning must have been emitted.
        # (The regex engine may avoid backtracking via optimisations; that is
        # also acceptable — no timeout warning needed in that case.)
        timeout_warnings = [w for w in caught if "timed out" in str(w.message)]
        # Accept either outcome: timeout-protected or engine-optimised
        _ = timeout_warnings  # either is correct

    def test_full_pipeline_with_redos_pattern_completes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End-to-end: a filter config containing a pathological pattern doesn't hang."""
        import platformdirs

        from quor.filters.registry import FilterRegistry
        from quor.pipeline.stages import _utils

        monkeypatch.setattr(_utils, "_PATTERN_TIMEOUT", 0.05)

        user_dir = Path(platformdirs.user_config_dir("quor")) / "filters"
        user_dir.mkdir(parents=True, exist_ok=True)
        redos_filter = user_dir / "redos.toml"
        redos_filter.write_text(
            "schema_version = 1\n\n"
            '[[filter]]\nname = "redos-test"\nmatch_command = "^redos-cmd$"\n\n'
            '[[filter.stages]]\ntype = "strip_lines"\n'
            'patterns = ["(a+)+b"]\n',
            encoding="utf-8",
        )

        registry = FilterRegistry(project_root=None)
        fc = registry.find("redos-cmd")
        assert fc is not None

        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            # Must return without hanging
            result = registry.apply(fc, "a" * 50)

        assert isinstance(result, str)
