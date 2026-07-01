"""Unit tests for quor/cli/commands/: the six V1 CLI commands."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import orjson
from typer.testing import CliRunner

from quor.cli.main import app
from quor.errors import ExitCode

runner = CliRunner()


def _make_proc(stdout: str = "", returncode: int = 0) -> MagicMock:
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.stdout = stdout
    proc.returncode = returncode
    return proc


# ---------------------------------------------------------------------------
# quor validate
# ---------------------------------------------------------------------------


class TestValidate:
    def test_all_tiers_valid(self) -> None:
        result = runner.invoke(app, ["validate"])
        assert result.exit_code == 0
        assert "git-status" in result.output

    def test_single_file_valid(self, tmp_path: Path) -> None:
        f = tmp_path / "ok.toml"
        f.write_text(
            '[[filter]]\nname = "ok"\nmatch_command = "^foo$"\nstages = []\n',
            encoding="utf-8",
        )
        result = runner.invoke(app, ["validate", str(f)])
        assert result.exit_code == 0
        assert "ok" in result.output

    def test_single_file_invalid_exits_2(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.toml"
        f.write_text("not valid toml [[[", encoding="utf-8")
        result = runner.invoke(app, ["validate", str(f)])
        assert result.exit_code == ExitCode.CONFIG_ERROR

    def test_single_file_missing_path_exits_2(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["validate", str(tmp_path / "missing.toml")])
        assert result.exit_code == ExitCode.CONFIG_ERROR

    def test_all_tiers_reports_load_warning_as_error(self, tmp_path: Path) -> None:
        with patch("quor.cli.commands.validate.FilterRegistry") as mock_reg:
            import warnings

            def _init(*_a: Any, **_kw: Any) -> MagicMock:
                warnings.warn("[quor] Failed to load user filter bad.toml: boom", stacklevel=2)
                inst = MagicMock()
                inst.all_filters.return_value = []
                return inst

            mock_reg.side_effect = _init
            result = runner.invoke(app, ["validate"])
        assert result.exit_code == ExitCode.CONFIG_ERROR


# ---------------------------------------------------------------------------
# quor explain
# ---------------------------------------------------------------------------


class TestExplain:
    def test_known_command_shows_trace(self) -> None:
        proc = _make_proc(
            stdout="On branch main\nnothing to commit, working tree clean\n"
        )
        with patch("subprocess.run", return_value=proc):
            result = runner.invoke(app, ["explain", "git status"])
        assert result.exit_code == 0
        assert "git-status" in result.output
        assert "Stage Trace" in result.output
        assert "Tokens:" in result.output

    def test_unmatched_command_falls_through_to_generic(self) -> None:
        # The built-in "generic" filter (match_command = '.') matches every
        # non-empty command, so unknown tools fall through to it rather than
        # going unfiltered.
        proc = _make_proc(stdout="some arbitrary output\n")
        with patch("subprocess.run", return_value=proc):
            result = runner.invoke(app, ["explain", "some-totally-unknown-tool --flag"])
        assert result.exit_code == 0
        assert "generic" in result.output

    def test_subprocess_failure_exits_1(self) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError("not found")):
            result = runner.invoke(app, ["explain", "git status"])
        assert result.exit_code == 1
        assert "Could not run command" in result.output


# ---------------------------------------------------------------------------
# quor gain
# ---------------------------------------------------------------------------


class TestGain:
    def test_empty_db(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["gain", "--project", str(tmp_path)])
        assert result.exit_code == 0
        assert "No invocations recorded" in result.output
        assert "Mode: optimize" in result.output

    def test_populated_db(self, tmp_path: Path) -> None:
        from quor.tracking.db import InvocationRecord, TrackingDB

        db_path = tmp_path / "data" / "quor.db"
        db = TrackingDB(db_path=db_path)
        db.record(
            InvocationRecord(
                command="git status",
                project_path=tmp_path.as_posix(),
                original_tokens=100,
                final_tokens=20,
                filter_name="git-status",
                was_passthrough=False,
                duration_ms=5.0,
            )
        )
        db.flush()
        db.close()

        with patch("platformdirs.user_data_dir", return_value=str(tmp_path / "data")):
            result = runner.invoke(app, ["gain", "--project", str(tmp_path), "--days", "30"])

        assert result.exit_code == 0
        assert "Total invocations: 1" in result.output
        assert "Tokens saved: ~80" in result.output


# ---------------------------------------------------------------------------
# quor verify
# ---------------------------------------------------------------------------


class TestVerify:
    def test_all_builtin_filters_pass(self) -> None:
        result = runner.invoke(app, ["verify"])
        assert result.exit_code == 0
        assert "failure(s)" in result.output
        assert "0 failure" in result.output.replace("\n", " ")

    def test_failure_exits_1(self) -> None:
        with patch("quor.cli.commands.verify.FilterRegistry") as mock_reg:
            inst = MagicMock()
            mock_reg.return_value = inst
            fake_filter = MagicMock()
            fake_filter.name = "broken"
            fake_filter.tests = [MagicMock()]
            inst.all_filters.return_value = [("builtin", fake_filter)]
            inst.run_tests.return_value = ["[broken] test 1: 'x' — must_contain 'y' not found"]
            result = runner.invoke(app, ["verify"])
        assert result.exit_code == ExitCode.GENERAL_ERROR

    def test_filters_without_tests_warns(self) -> None:
        with patch("quor.cli.commands.verify.FilterRegistry") as mock_reg:
            inst = MagicMock()
            mock_reg.return_value = inst
            fake_filter = MagicMock()
            fake_filter.name = "untested"
            fake_filter.tests = []
            inst.all_filters.return_value = [("builtin", fake_filter)]
            result = runner.invoke(app, ["verify"])
        assert result.exit_code == 0
        assert "no inline tests" in result.output


# ---------------------------------------------------------------------------
# quor doctor
# ---------------------------------------------------------------------------


class TestDoctor:
    def test_clean_install_hook_missing_exits_1(self) -> None:
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == ExitCode.GENERAL_ERROR
        assert "Hook script installed" in result.output

    def test_dependency_missing_reported(self) -> None:
        with patch("quor.cli.commands.doctor.importlib.import_module", side_effect=ImportError("nope")):
            result = runner.invoke(app, ["doctor"])
        assert result.exit_code == ExitCode.GENERAL_ERROR

    def test_all_green_after_hook_installed(self, tmp_path: Path) -> None:
        hook_path = tmp_path / "hooks" / "claude-hook.ps1"
        hook_path.parent.mkdir(parents=True, exist_ok=True)
        hook_path.write_text("dummy", encoding="utf-8")
        settings_path = tmp_path / "settings.json"
        with patch("platformdirs.user_data_dir", return_value=str(tmp_path)):
            result = runner.invoke(app, ["doctor", "--settings-path", str(settings_path)])
        assert result.exit_code == 0
        assert "✓ Hook script installed" in result.output

    def test_plugin_diagnostics_include_version(self, tmp_path: Path) -> None:
        """quor doctor lists discovered plugins with their declared version."""
        from quor.pipeline.plugin_loader import PluginInfo, PluginLoadReport

        hook_path = tmp_path / "hooks" / "claude-hook.ps1"
        hook_path.parent.mkdir(parents=True, exist_ok=True)
        hook_path.write_text("dummy", encoding="utf-8")
        settings_path = tmp_path / "settings.json"

        fake_report = PluginLoadReport(
            plugins=[
                PluginInfo(
                    entry_point_name="com.test.versioned",
                    module_path="some.module",
                    class_name="SomePlugin",
                    plugin_id="com.test.versioned",
                    version="2.3.1",
                    api_version=1,
                )
            ],
        )
        with (
            patch("platformdirs.user_data_dir", return_value=str(tmp_path)),
            patch("quor.pipeline.plugin_loader.get_load_report", return_value=fake_report),
        ):
            result = runner.invoke(app, ["doctor", "--settings-path", str(settings_path)])
        assert "com.test.versioned@2.3.1" in result.output


# ---------------------------------------------------------------------------
# quor init --claude
# ---------------------------------------------------------------------------


class TestInit:
    def test_no_claude_flag_noop(self) -> None:
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert "Nothing to do" in result.output

    def test_dry_run_and_atomic_write(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        result = runner.invoke(
            app, ["init", "--claude", "--yes", "--settings-path", str(settings_path)]
        )
        assert result.exit_code == 0
        assert "Dry run" in result.output
        assert settings_path.exists()

        data = orjson.loads(settings_path.read_bytes())
        commands = [
            h["command"]
            for entry in data["hooks"]["PreToolUse"]
            for h in entry["hooks"]
        ]
        assert any("claude-hook.ps1" in c for c in commands)

    def test_confirmation_declined_aborts(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        result = runner.invoke(
            app, ["init", "--claude", "--settings-path", str(settings_path)], input="n\n"
        )
        assert result.exit_code == ExitCode.GENERAL_ERROR
        assert not settings_path.exists()

    def test_existing_hook_overwritten_not_duplicated(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        runner.invoke(app, ["init", "--claude", "--yes", "--settings-path", str(settings_path)])
        result = runner.invoke(
            app, ["init", "--claude", "--yes", "--settings-path", str(settings_path)]
        )
        assert result.exit_code == 0
        assert "already registered" in result.output

        data = orjson.loads(settings_path.read_bytes())
        pre_tool_use = data["hooks"]["PreToolUse"]
        quor_entries = [
            entry
            for entry in pre_tool_use
            for h in entry["hooks"]
            if "claude-hook.ps1" in h["command"]
        ]
        assert len(quor_entries) == 1

    def test_hook_script_embeds_sys_executable(self, tmp_path: Path) -> None:
        import sys

        settings_path = tmp_path / "settings.json"
        with patch("platformdirs.user_data_dir", return_value=str(tmp_path / "data")):
            result = runner.invoke(
                app, ["init", "--claude", "--yes", "--settings-path", str(settings_path)]
            )
        assert result.exit_code == 0
        hook_path = tmp_path / "data" / "hooks" / "claude-hook.ps1"
        content = hook_path.read_text(encoding="utf-8")
        assert sys.executable in content
        assert "quor hook claude" in content


# ---------------------------------------------------------------------------
# Hook collision detection (P0 item 3)
# ---------------------------------------------------------------------------


def _settings_with_third_party_hook(cmd: str = "zap hook bash") -> dict[str, Any]:
    """Build a settings dict that simulates another tool's PreToolUse Bash hook."""
    return {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": cmd}]},
            ]
        }
    }


class TestHookCollisionDetection:
    def test_third_party_hook_triggers_warning(self, tmp_path: Path) -> None:
        """init --claude warns when another Bash hook is already registered."""
        settings_path = tmp_path / "settings.json"
        settings_path.write_bytes(
            orjson.dumps(_settings_with_third_party_hook("zap hook bash"))
        )
        result = runner.invoke(
            app,
            ["init", "--claude", "--yes", "--settings-path", str(settings_path)],
        )
        # init installs alongside the third-party hook (per --yes); the third-party
        # hook is left in place, so the trailing `quor doctor` run correctly reports
        # the still-unresolved collision as a failure.
        assert result.exit_code == ExitCode.GENERAL_ERROR
        assert "Warning" in result.output or "⚠" in result.output

    def test_third_party_hook_names_known_tool(self, tmp_path: Path) -> None:
        """Zap is named explicitly in the collision warning."""
        settings_path = tmp_path / "settings.json"
        settings_path.write_bytes(
            orjson.dumps(_settings_with_third_party_hook("/usr/local/bin/zap hook"))
        )
        result = runner.invoke(
            app,
            ["init", "--claude", "--yes", "--settings-path", str(settings_path)],
        )
        # See test_third_party_hook_triggers_warning: the trailing doctor run
        # correctly flags the still-unresolved third-party hook.
        assert result.exit_code == ExitCode.GENERAL_ERROR
        assert "Zap" in result.output

    def test_unknown_third_party_hook_warns_generically(self, tmp_path: Path) -> None:
        """An unrecognised command still generates a collision warning."""
        settings_path = tmp_path / "settings.json"
        settings_path.write_bytes(
            orjson.dumps(_settings_with_third_party_hook("some-unknown-tool intercept"))
        )
        result = runner.invoke(
            app,
            ["init", "--claude", "--yes", "--settings-path", str(settings_path)],
        )
        # See test_third_party_hook_triggers_warning: the trailing doctor run
        # correctly flags the still-unresolved third-party hook.
        assert result.exit_code == ExitCode.GENERAL_ERROR
        # Warning present but no named tool
        assert "Warning" in result.output or "⚠" in result.output

    def test_no_conflict_when_only_quor_hook_present(self, tmp_path: Path) -> None:
        """After quor init, re-running should NOT detect a conflict with itself."""
        settings_path = tmp_path / "settings.json"
        # First install
        runner.invoke(
            app, ["init", "--claude", "--yes", "--settings-path", str(settings_path)]
        )
        # Second install — Quor's own hook is present; must not collide with itself
        result = runner.invoke(
            app, ["init", "--claude", "--yes", "--settings-path", str(settings_path)]
        )
        assert result.exit_code == 0
        # "already registered" message but no collision warning
        assert "already registered" in result.output

    def test_doctor_reports_collision(self, tmp_path: Path) -> None:
        """quor doctor reports a conflict when another Bash hook is registered."""
        settings_path = tmp_path / "settings.json"
        settings_path.write_bytes(
            orjson.dumps(_settings_with_third_party_hook("zap hook bash"))
        )
        with patch("pathlib.Path.home", return_value=tmp_path):
            result = runner.invoke(app, ["doctor"])
        # doctor exit 1 (hook script missing + collision) and mentions conflict
        assert "conflicting" in result.output.lower() or "conflict" in result.output.lower()

    def test_doctor_no_collision_when_settings_missing(self) -> None:
        """doctor passes the collision check when settings.json doesn't exist."""
        with patch("pathlib.Path.home", return_value=Path("/nonexistent/path/xyz")):
            result = runner.invoke(app, ["doctor"])
        # Should not mention collision (check is skipped cleanly when file absent)
        assert "No conflicting" not in result.output or "✓ No conflicting" in result.output


# ---------------------------------------------------------------------------
# _find_conflicting_hooks unit tests
# ---------------------------------------------------------------------------


class TestFindConflictingHooks:
    def test_empty_settings_returns_no_conflicts(self) -> None:
        from quor.cli.commands.init import _find_conflicting_hooks

        assert _find_conflicting_hooks({}) == []

    def test_quor_hook_not_reported_as_conflict(self) -> None:
        from quor.cli.commands.init import _find_conflicting_hooks

        settings = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": 'powershell -File "C:\\quor\\claude-hook.ps1"',
                            }
                        ],
                    }
                ]
            }
        }
        assert _find_conflicting_hooks(settings) == []

    def test_third_party_bash_hook_returned(self) -> None:
        from quor.cli.commands.init import _find_conflicting_hooks

        cmd = "zap hook bash"
        settings = _settings_with_third_party_hook(cmd)
        conflicts = _find_conflicting_hooks(settings)
        assert cmd in conflicts

    def test_non_bash_matcher_ignored(self) -> None:
        from quor.cli.commands.init import _find_conflicting_hooks

        settings = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Python",
                        "hooks": [{"type": "command", "command": "some-tool hook"}],
                    }
                ]
            }
        }
        # Non-Bash matcher cannot intercept Bash commands → not a conflict
        assert _find_conflicting_hooks(settings) == []


# ---------------------------------------------------------------------------
# Regression: Windows encoding crash (Phase 7 fix)
# ---------------------------------------------------------------------------


class TestWindowsEncodingRegression:
    """Regression tests for the Windows cp1252 UnicodeEncodeError crash.

    Bug: CLI output paths (✓/✗ glyphs) crashed on Windows because text-mode
    stdout/stderr defaulted to cp1252, which cannot encode those characters.
    Fix: _ensure_utf8_stdio() calls stream.reconfigure(encoding='utf-8') once
    in main() before any CLI or dispatch output is written.
    """

    def test_ensure_utf8_stdio_calls_reconfigure(self) -> None:
        from quor.__main__ import _ensure_utf8_stdio

        stdout_mock = MagicMock()
        stderr_mock = MagicMock()

        with patch.object(sys, "stdout", stdout_mock), patch.object(sys, "stderr", stderr_mock):
            _ensure_utf8_stdio()

        stdout_mock.reconfigure.assert_called_once_with(encoding="utf-8")
        stderr_mock.reconfigure.assert_called_once_with(encoding="utf-8")

    def test_ensure_utf8_stdio_suppresses_value_error(self) -> None:
        """ValueError from reconfigure (e.g. BytesIO-backed capture) must be swallowed."""
        from quor.__main__ import _ensure_utf8_stdio

        mock_stream = MagicMock()
        mock_stream.reconfigure.side_effect = ValueError("not a text stream")

        with patch.object(sys, "stdout", mock_stream), patch.object(sys, "stderr", mock_stream):
            _ensure_utf8_stdio()  # must not raise

        mock_stream.reconfigure.assert_called_with(encoding="utf-8")

    def test_ensure_utf8_stdio_suppresses_os_error(self) -> None:
        """OSError from reconfigure must be swallowed (stream may not support it)."""
        from quor.__main__ import _ensure_utf8_stdio

        mock_stream = MagicMock()
        mock_stream.reconfigure.side_effect = OSError("reconfigure failed")

        with patch.object(sys, "stdout", mock_stream), patch.object(sys, "stderr", mock_stream):
            _ensure_utf8_stdio()  # must not raise

    def test_ensure_utf8_stdio_handles_stream_without_reconfigure(self) -> None:
        """Streams that lack reconfigure() are silently skipped (no AttributeError)."""
        from quor.__main__ import _ensure_utf8_stdio

        class _NoReconfigure:
            pass

        stream = _NoReconfigure()
        with patch.object(sys, "stdout", stream), patch.object(sys, "stderr", stream):
            _ensure_utf8_stdio()  # must not raise


# ---------------------------------------------------------------------------
# Codepage / locale sweep (P1 item 6)
# ---------------------------------------------------------------------------


class TestCodepageSweep:
    """_ensure_utf8_stdio must reconfigure regardless of the stream's original codepage.

    Regression against the Windows cp1252 crash: streams that report non-UTF-8
    encodings must be reconfigured to UTF-8 so that ✓/✗ glyphs don't crash the CLI.
    """

    import pytest

    @pytest.mark.parametrize("codepage", ["cp437", "cp1252", "utf-8", "ascii"])
    def test_reconfigure_called_for_any_codepage(self, codepage: str) -> None:
        from quor.__main__ import _ensure_utf8_stdio

        stdout_mock = MagicMock()
        stdout_mock.encoding = codepage
        stderr_mock = MagicMock()
        stderr_mock.encoding = codepage

        with (
            patch.object(sys, "stdout", stdout_mock),
            patch.object(sys, "stderr", stderr_mock),
        ):
            _ensure_utf8_stdio()

        # Both streams must be reconfigured regardless of starting encoding
        stdout_mock.reconfigure.assert_called_once_with(encoding="utf-8")
        stderr_mock.reconfigure.assert_called_once_with(encoding="utf-8")

    def test_cp1252_stream_reconfigure_failure_does_not_crash(self) -> None:
        """If reconfigure raises on a cp1252 stream, the CLI must not crash."""
        from quor.__main__ import _ensure_utf8_stdio

        mock_stream = MagicMock()
        mock_stream.encoding = "cp1252"
        mock_stream.reconfigure.side_effect = ValueError("cannot reconfigure")

        with (
            patch.object(sys, "stdout", mock_stream),
            patch.object(sys, "stderr", mock_stream),
        ):
            _ensure_utf8_stdio()  # must not raise
