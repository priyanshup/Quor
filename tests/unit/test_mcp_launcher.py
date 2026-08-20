"""Tests for `quor/mcp/launcher.py` — the self-healing MCP pre-flight
entrypoint `quor init --mcp` scaffolds instead of `quor.mcp.server`
directly (see that module's docstring and `docs/final/CLAUDE.md`'s Common
Mistake #13 for the drift this closes).

`main()`'s three collaborators (`_check_imports`, `_attempt_self_repair`,
`_handoff`) are monkeypatched at the module level for the `main()`-level
tests below rather than exercised end-to-end — `_handoff()` would otherwise
block forever on the real stdio MCP loop, and forcing a real dependency to
go missing/reappear mid-test-run isn't a real scenario to reproduce safely.
Each collaborator also gets its own direct unit test against real behavior
(real imports, a real subprocess mock) so the seams being monkeypatched are
still verified individually.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from quor.mcp import launcher


class TestMainNormalLaunch:
    def test_handoff_called_and_repair_skipped_when_imports_ok(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(launcher, "_check_imports", lambda: (True, ""))
        repair_calls = []
        monkeypatch.setattr(launcher, "_attempt_self_repair", lambda: repair_calls.append(1) or True)
        handoff_calls = []
        monkeypatch.setattr(launcher, "_handoff", lambda: handoff_calls.append(1))

        launcher.main()

        assert handoff_calls == [1]
        assert repair_calls == []


class TestMainAutoRepair:
    def test_repair_triggered_on_import_failure_then_handoff_on_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            launcher, "_check_imports", lambda: (False, "No module named 'mcp'")
        )
        repair_calls = []
        monkeypatch.setattr(launcher, "_attempt_self_repair", lambda: repair_calls.append(1) or True)
        handoff_calls = []
        monkeypatch.setattr(launcher, "_handoff", lambda: handoff_calls.append(1))

        launcher.main()

        assert repair_calls == [1]
        assert handoff_calls == [1]

    def test_repair_success_is_reported_on_stderr(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(launcher, "_check_imports", lambda: (False, "boom"))
        monkeypatch.setattr(launcher, "_attempt_self_repair", lambda: True)
        monkeypatch.setattr(launcher, "_handoff", lambda: None)

        launcher.main()

        captured = capsys.readouterr()
        assert captured.out == ""
        assert "self-repair succeeded" in captured.err


class TestMainRepairFailure:
    def test_exits_nonzero_and_never_hands_off_when_repair_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            launcher, "_check_imports", lambda: (False, "No module named 'mcp'")
        )
        monkeypatch.setattr(launcher, "_attempt_self_repair", lambda: False)
        handoff_calls = []
        monkeypatch.setattr(launcher, "_handoff", lambda: handoff_calls.append(1))

        with pytest.raises(SystemExit) as exc_info:
            launcher.main()

        assert exc_info.value.code == 1
        assert handoff_calls == []

    def test_troubleshooting_goes_to_stderr_and_stdout_stays_clean(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(
            launcher, "_check_imports", lambda: (False, "No module named 'mcp'")
        )
        monkeypatch.setattr(launcher, "_attempt_self_repair", lambda: False)
        monkeypatch.setattr(launcher, "_handoff", lambda: None)

        with pytest.raises(SystemExit):
            launcher.main()

        captured = capsys.readouterr()
        assert captured.out == ""
        assert "No module named 'mcp'" in captured.err
        assert sys.executable in captured.err


class TestCheckImports:
    def test_real_required_modules_import_cleanly(self) -> None:
        # This test's own environment has mcp/quor installed (it's how the
        # test itself is running), so this exercises the real, unmocked path.
        ok, error = launcher._check_imports()
        assert ok is True
        assert error == ""

    def test_missing_module_is_reported_by_name(self) -> None:
        ok, error = launcher._check_imports(("quor_definitely_not_a_real_module_xyz",))
        assert ok is False
        assert "quor_definitely_not_a_real_module_xyz" in error

    def test_stops_at_first_missing_module(self) -> None:
        ok, error = launcher._check_imports(("mcp", "quor_definitely_not_a_real_module_xyz"))
        assert ok is False
        assert "quor_definitely_not_a_real_module_xyz" in error


class TestFindDevCheckoutRoot:
    def test_finds_this_repo_as_a_dev_checkout(self) -> None:
        root = launcher._find_dev_checkout_root()
        assert root is not None
        assert (root / "pyproject.toml").is_file()

    def test_none_when_no_quor_pyproject_above(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_file = tmp_path / "site-packages" / "quor" / "mcp" / "launcher.py"
        fake_file.parent.mkdir(parents=True)
        fake_file.write_text("", encoding="utf-8")
        monkeypatch.setattr(launcher, "__file__", str(fake_file))

        assert launcher._find_dev_checkout_root() is None

    def test_ignores_a_pyproject_toml_for_a_different_project(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "not-quor"\n', encoding="utf-8"
        )
        fake_file = tmp_path / "quor" / "mcp" / "launcher.py"
        fake_file.parent.mkdir(parents=True)
        fake_file.write_text("", encoding="utf-8")
        monkeypatch.setattr(launcher, "__file__", str(fake_file))

        assert launcher._find_dev_checkout_root() is None


class TestMcpRequirementSpec:
    def test_returns_a_spec_naming_mcp(self) -> None:
        spec = launcher._mcp_requirement_spec()
        assert spec.lower().startswith("mcp")


class TestRunRepairCommand:
    def test_zero_exit_returns_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _FakeResult:
            returncode = 0
            stderr = ""

        monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **kw: _FakeResult())

        assert launcher._run_repair_command(["true"]) is True

    def test_nonzero_exit_returns_false_and_logs_stderr(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        class _FakeResult:
            returncode = 1
            stderr = "pip: no matching distribution"

        monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **kw: _FakeResult())

        assert launcher._run_repair_command(["false"]) is False
        assert "no matching distribution" in capsys.readouterr().err

    def test_timeout_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise_timeout(*_a: object, **_kw: object) -> None:
            raise subprocess.TimeoutExpired(cmd="slow-cmd", timeout=1)

        monkeypatch.setattr(launcher.subprocess, "run", _raise_timeout)

        assert launcher._run_repair_command(["slow-cmd"]) is False

    def test_missing_executable_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise_oserror(*_a: object, **_kw: object) -> None:
            raise OSError("no such file or directory: uv")

        monkeypatch.setattr(launcher.subprocess, "run", _raise_oserror)

        assert launcher._run_repair_command(["uv", "sync"]) is False


class TestAttemptSelfRepair:
    def test_disabled_via_env_var_skips_repair_without_running_anything(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("QUOR_MCP_DISABLE_AUTOREPAIR", "1")

        def _fail_if_called(*_a: object, **_kw: object) -> None:
            raise AssertionError("subprocess.run should not be called when disabled")

        monkeypatch.setattr(launcher.subprocess, "run", _fail_if_called)

        assert launcher._attempt_self_repair() is False

    def test_dev_checkout_tries_pip_install_editable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(launcher, "_find_dev_checkout_root", lambda: tmp_path)
        monkeypatch.setattr(launcher.shutil, "which", lambda _name: None)  # no uv on PATH
        recorded_cmds: list[list[str]] = []

        def _fake_run_repair(cmd: list[str], **_kw: object) -> bool:
            recorded_cmds.append(list(cmd))
            return True

        monkeypatch.setattr(launcher, "_run_repair_command", _fake_run_repair)
        monkeypatch.setattr(launcher, "_check_imports", lambda: (True, ""))

        assert launcher._attempt_self_repair() is True
        assert recorded_cmds == [[sys.executable, "-m", "pip", "install", "-e", str(tmp_path)]]

    def test_dev_checkout_prefers_uv_sync_when_available(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(launcher, "_find_dev_checkout_root", lambda: tmp_path)
        monkeypatch.setattr(launcher.shutil, "which", lambda _name: "/usr/bin/uv")
        recorded_cmds: list[list[str]] = []

        def _fake_run_repair(cmd: list[str], **_kw: object) -> bool:
            recorded_cmds.append(list(cmd))
            return True

        monkeypatch.setattr(launcher, "_run_repair_command", _fake_run_repair)
        monkeypatch.setattr(launcher, "_check_imports", lambda: (True, ""))

        assert launcher._attempt_self_repair() is True
        assert recorded_cmds == [["uv", "sync", "--project", str(tmp_path)]]

    def test_falls_back_to_pip_install_when_uv_sync_fails(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(launcher, "_find_dev_checkout_root", lambda: tmp_path)
        monkeypatch.setattr(launcher.shutil, "which", lambda _name: "/usr/bin/uv")
        recorded_cmds: list[list[str]] = []

        def _fake_run_repair(cmd: list[str], **_kw: object) -> bool:
            recorded_cmds.append(list(cmd))
            return cmd[0] != "uv"

        monkeypatch.setattr(launcher, "_run_repair_command", _fake_run_repair)
        monkeypatch.setattr(launcher, "_check_imports", lambda: (True, ""))

        assert launcher._attempt_self_repair() is True
        assert recorded_cmds == [
            ["uv", "sync", "--project", str(tmp_path)],
            [sys.executable, "-m", "pip", "install", "-e", str(tmp_path)],
        ]

    def test_non_dev_install_reinstalls_declared_mcp_spec(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(launcher, "_find_dev_checkout_root", lambda: None)
        monkeypatch.setattr(launcher, "_mcp_requirement_spec", lambda: "mcp>=2.0.0")
        recorded_cmds: list[list[str]] = []

        def _fake_run_repair(cmd: list[str], **_kw: object) -> bool:
            recorded_cmds.append(list(cmd))
            return True

        monkeypatch.setattr(launcher, "_run_repair_command", _fake_run_repair)
        monkeypatch.setattr(launcher, "_check_imports", lambda: (True, ""))

        assert launcher._attempt_self_repair() is True
        assert recorded_cmds == [[sys.executable, "-m", "pip", "install", "mcp>=2.0.0"]]

    def test_returns_false_when_command_succeeds_but_imports_still_fail(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Repair command exiting 0 doesn't guarantee the import actually
        works afterward (e.g. installed the wrong package) — only a
        post-repair import re-check should be trusted."""
        monkeypatch.setattr(launcher, "_find_dev_checkout_root", lambda: None)
        monkeypatch.setattr(launcher, "_mcp_requirement_spec", lambda: "mcp>=2.0.0")
        monkeypatch.setattr(launcher, "_run_repair_command", lambda *_a, **_kw: True)
        monkeypatch.setattr(launcher, "_check_imports", lambda: (False, "still broken"))

        assert launcher._attempt_self_repair() is False

    def test_returns_false_when_command_itself_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(launcher, "_find_dev_checkout_root", lambda: None)
        monkeypatch.setattr(launcher, "_mcp_requirement_spec", lambda: "mcp>=2.0.0")
        monkeypatch.setattr(launcher, "_run_repair_command", lambda *_a, **_kw: False)

        assert launcher._attempt_self_repair() is False
