"""Tests for quor.adapters.codex_adapter — CodexAdapter (QB-068).

CodexAdapter is deliberately detection-only (no supported AgentEvent) — see
the module docstring in codex_adapter.py for the research trail. These
tests exist to lock in that scope decision and its "advisory only, never
fails quor doctor" behavior, not to test any compression logic (there is
none here to test).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from quor.adapters.base import AgentEvent, DoctorContext, InstallContext
from quor.adapters.codex_adapter import CodexAdapter


class TestCodexAdapterScope:
    def test_supports_no_events(self) -> None:
        assert CodexAdapter().supported_events == frozenset()

    def test_handle_event_always_returns_none(self) -> None:
        adapter = CodexAdapter()
        for event in AgentEvent:
            assert adapter.handle_event(event, b"{}", None) is None


class TestCodexAdapterInstall:
    def test_install_writes_nothing_and_warns(self, tmp_path: Path) -> None:
        result = CodexAdapter().install(InstallContext(settings_override=tmp_path / "x", yes=True))
        assert result.installed_paths == ()
        assert len(result.warnings) == 1
        assert "not installed" in result.warnings[0]


class TestCodexAdapterDoctor:
    def test_checks_are_always_advisory(self, tmp_path: Path) -> None:
        checks = CodexAdapter().doctor_checks(DoctorContext(settings_override=None))
        assert len(checks) == 2
        assert all(ok is True for _, ok, _ in checks)

    def test_detection_reflects_config_dir_presence(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import quor.adapters.codex_adapter as codex_adapter

        fake_home = tmp_path / "home"
        (fake_home / ".codex").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        detected_check = next(c for c in CodexAdapter().doctor_checks(DoctorContext()) if "detected" in c[0])
        assert "config directory detected" in detected_check[2]
        assert codex_adapter._codex_config_dir() == fake_home / ".codex"
