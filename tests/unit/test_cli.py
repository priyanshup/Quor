"""Unit tests for quor/cli/commands/: the six V1 CLI commands."""

from __future__ import annotations

import subprocess
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
        with patch("platformdirs.user_data_dir", return_value=str(tmp_path)):
            result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert "✓ Hook script installed" in result.output


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
