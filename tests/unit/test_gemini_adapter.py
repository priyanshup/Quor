"""Tests for quor.adapters.gemini_adapter — GeminiAdapter (QB-068).

Covers the confirmed-capable path only (COMMAND_INTERCEPT via BeforeTool/
run_shell_command) — see the module docstring in gemini_adapter.py for what
is confirmed vs. inferred from Gemini CLI's own documentation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import orjson
import pytest

from quor.adapters import hook_manifest
from quor.adapters.base import AgentEvent, DoctorContext, InstallContext
from quor.adapters.gemini_adapter import GeminiAdapter, handle_bytes
from quor.rewrite.invocation import get_quor_invocation

_BOM = "﻿"


@pytest.fixture(autouse=True)
def _pin_windows_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """QB-083: every test in this file predates the cross-platform Gemini
    hook launcher and was written assuming Windows (.ps1 filename,
    `powershell` command string). Pins `hook_manifest.is_windows` itself —
    NOT `os.name`/`sys.platform` — module-wide, exactly matching
    `tests/unit/test_cli.py`'s identical QB-082 fixture and its rationale
    (patching `os.name` directly doesn't change which `pathlib.Path`
    subclass already got baked in at interpreter startup). `TestGeminiPosix`
    below overrides this per-test via its own `monkeypatch.setattr`, which
    `pytest.MonkeyPatch` restores to this default at that test's teardown."""
    monkeypatch.setattr(hook_manifest, "is_windows", lambda: True)


def _payload(command: str) -> bytes:
    return orjson.dumps({"tool_name": "run_shell_command", "tool_input": {"command": command}})


class TestHandleBytesRewrite:
    def test_known_command_is_rewritten(self) -> None:
        result = orjson.loads(handle_bytes(_payload("git status")))
        rewritten = result["hookSpecificOutput"]["tool_input"]["command"]
        assert rewritten == f"{get_quor_invocation()} git status"

    def test_response_includes_allow_decision(self) -> None:
        result = orjson.loads(handle_bytes(_payload("git status")))
        assert result["decision"] == "allow"

    def test_unknown_command_omits_tool_input_override(self) -> None:
        result = orjson.loads(handle_bytes(_payload("cargo build")))
        assert "hookSpecificOutput" not in result

    def test_sibling_tool_input_fields_preserved(self) -> None:
        raw = orjson.dumps(
            {
                "tool_name": "run_shell_command",
                "tool_input": {"command": "git log", "description": "show history"},
            }
        )
        result = orjson.loads(handle_bytes(raw))
        updated = result["hookSpecificOutput"]["tool_input"]
        assert updated["description"] == "show history"
        assert updated["command"] == f"{get_quor_invocation()} git log"

    @pytest.mark.parametrize("bom_count", [0, 1, 2])
    def test_bom_stripped(self, bom_count: int) -> None:
        raw_text = _BOM * bom_count + _payload("git diff").decode("utf-8")
        result = orjson.loads(handle_bytes(raw_text.encode("utf-8")))
        assert (
            result["hookSpecificOutput"]["tool_input"]["command"]
            == f"{get_quor_invocation()} git diff"
        )

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(orjson.JSONDecodeError):
            handle_bytes(b"not json")

    def test_missing_tool_input_raises(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            handle_bytes(orjson.dumps({"tool_name": "run_shell_command"}))


class TestGeminiAdapterHandleEvent:
    def test_command_intercept_matches_handle_bytes(self) -> None:
        raw = _payload("git status")
        assert GeminiAdapter().handle_event(AgentEvent.COMMAND_INTERCEPT, raw, None) == handle_bytes(raw)

    def test_content_intercept_not_supported(self) -> None:
        assert GeminiAdapter().handle_event(AgentEvent.CONTENT_INTERCEPT, b"{}", None) is None

    def test_supported_events_is_command_intercept_only(self) -> None:
        assert GeminiAdapter().supported_events == frozenset({AgentEvent.COMMAND_INTERCEPT})


class TestGeminiAdapterInstallAndDoctor:
    def test_never_installed_is_advisory(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        checks = GeminiAdapter().doctor_checks(DoctorContext(settings_override=settings_path))
        names_ok = {name: ok for name, ok, _ in checks}
        assert names_ok["Gemini CLI hook installed"] is True
        # Roundtrip check always runs and must pass regardless of install state.
        assert names_ok["Gemini CLI hook responds correctly"] is True

    def test_install_writes_script_and_registers_hook(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        result = GeminiAdapter().install(InstallContext(settings_override=settings_path, yes=True))

        assert not result.warnings
        assert settings_path in result.installed_paths
        assert settings_path.exists()

        settings = orjson.loads(settings_path.read_bytes())
        entries = settings["hooks"]["BeforeTool"]
        assert entries[0]["matcher"] == "run_shell_command"
        assert "gemini-hook.ps1" in entries[0]["hooks"][0]["command"]

        script_path = next(p for p in result.installed_paths if p != settings_path)
        assert script_path.exists()
        assert "quor hook gemini command_intercept" in script_path.read_text(encoding="utf-8")

    def test_doctor_reports_installed_and_registered_after_install(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        GeminiAdapter().install(InstallContext(settings_override=settings_path, yes=True))

        checks = GeminiAdapter().doctor_checks(DoctorContext(settings_override=settings_path))
        names_ok = {name: ok for name, ok, _ in checks}
        assert names_ok["Gemini CLI hook script installed"] is True
        assert names_ok["Gemini CLI hook registered in settings.json"] is True
        assert names_ok["Gemini CLI hook responds correctly"] is True

    def test_reinstall_does_not_duplicate_hook_entry(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        adapter = GeminiAdapter()
        adapter.install(InstallContext(settings_override=settings_path, yes=True))
        adapter.install(InstallContext(settings_override=settings_path, yes=True))

        settings = orjson.loads(settings_path.read_bytes())
        assert len(settings["hooks"]["BeforeTool"]) == 1

    def test_install_does_not_corrupt_other_hooks_in_same_settings_file(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(
            orjson.dumps(
                {"hooks": {"BeforeTool": [{"matcher": "write_file", "hooks": [{"type": "command", "command": "other-tool.sh"}]}]}}
            ).decode("utf-8"),
            encoding="utf-8",
        )

        GeminiAdapter().install(InstallContext(settings_override=settings_path, yes=True))

        settings = orjson.loads(settings_path.read_bytes())
        matchers = {e["matcher"] for e in settings["hooks"]["BeforeTool"]}
        assert matchers == {"write_file", "run_shell_command"}


class TestGeminiPosix:
    """QB-083: the POSIX (.sh) equivalent of the launcher generated above —
    mirrors `tests/unit/test_cli.py`'s `TestPosixLauncherGeneration`/
    `TestDoctorPosix` for Claude's own QB-082 fix. Only the assertions that
    differ by platform are duplicated here (script extension, launcher
    content, executable bit, registered command shape) — collision
    detection and reinstall-dedup are already platform-agnostic substring
    containment and are not re-tested."""

    @pytest.fixture(autouse=True)
    def _posix_platform(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(hook_manifest, "is_windows", lambda: False)

    def test_install_writes_sh_script_with_correct_content(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("platformdirs.user_data_dir", lambda *_a, **_kw: str(tmp_path / "data"))
        settings_path = tmp_path / "settings.json"
        result = GeminiAdapter().install(InstallContext(settings_override=settings_path, yes=True))

        assert not result.warnings
        script_path = next(p for p in result.installed_paths if p != settings_path)
        assert script_path.name == "gemini-hook.sh"
        content = script_path.read_text(encoding="utf-8")
        assert content.startswith("#!/bin/sh")
        assert "exec" in content
        assert "quor hook gemini command_intercept" in content

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason=(
            "Verifies real POSIX permission bits (0o755) — meaningless on a real Windows "
            "host, whose chmod() cannot represent Unix execute bits at all, regardless of "
            "what this class's is_windows() mock says. Not a production gap: chmod(0o755) "
            "only ever runs when is_windows() is genuinely False, i.e. on a real POSIX host."
        ),
    )
    def test_launcher_is_executable(self, tmp_path: Path) -> None:
        """Regression test for QB-083's chmod requirement — fails on the
        pre-fix code (which never set an executable bit), passes once
        `install()` chmods the script on POSIX."""
        settings_path = tmp_path / "settings.json"
        result = GeminiAdapter().install(InstallContext(settings_override=settings_path, yes=True))

        script_path = next(p for p in result.installed_paths if p != settings_path)
        assert script_path.stat().st_mode & 0o777 == 0o755

    def test_settings_json_command_uses_posix_shell(self, tmp_path: Path) -> None:
        """Regression test for QB-083 — fails on the pre-fix code (which
        always wrote `powershell -ExecutionPolicy Bypass -File` regardless
        of platform), passes once `_install_hook_entry()` branches on
        `hook_manifest.is_windows()`."""
        settings_path = tmp_path / "settings.json"
        GeminiAdapter().install(InstallContext(settings_override=settings_path, yes=True))

        settings = orjson.loads(settings_path.read_bytes())
        commands = [h["command"] for e in settings["hooks"]["BeforeTool"] for h in e["hooks"]]
        assert any(c.startswith(f"{hook_manifest.POSIX_SHELL} ") for c in commands)
        assert not any("powershell" in c for c in commands)

    def test_doctor_reports_installed_and_registered_after_posix_install(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        adapter = GeminiAdapter()
        adapter.install(InstallContext(settings_override=settings_path, yes=True))

        checks = adapter.doctor_checks(DoctorContext(settings_override=settings_path))
        names_ok = {name: ok for name, ok, _ in checks}
        assert names_ok["Gemini CLI hook script installed"] is True
        assert names_ok["Gemini CLI hook registered in settings.json"] is True
        assert names_ok["Gemini CLI hook responds correctly"] is True
