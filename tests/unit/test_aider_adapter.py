"""Tests for quor.adapters.aider_adapter — AiderAdapter (QB-069).

Shared DetectionOnlyAdapter contract is covered once, generically, by
test_agent_adapter_protocol.py::TestDetectionOnlyAdapterSharedContract —
this file only covers AiderAdapter's own `_detect()` logic, which (unlike
every other QB-069 adapter) checks multiple independent signals rather than
a single config directory, since Aider is a plain CLI tool with no
guaranteed home-directory footprint.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from quor.adapters.aider_adapter import AiderAdapter
from quor.adapters.base import DoctorContext


def _detected_detail(monkeypatch: pytest.MonkeyPatch, fake_home: Path, cwd: Path) -> str:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setattr(Path, "cwd", classmethod(lambda cls: cwd))
    checks = AiderAdapter().doctor_checks(DoctorContext())
    return next(c for c in checks if "detected" in c[0])[2]


class TestAiderAdapterDetection:
    def test_not_detected_when_no_signal_present(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda name: None)
        home = tmp_path / "home"
        home.mkdir()
        cwd = tmp_path / "project"
        cwd.mkdir()

        detail = _detected_detail(monkeypatch, home, cwd)
        assert "not detected" in detail

    def test_detected_via_path_executable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/aider" if name == "aider" else None)
        home = tmp_path / "home"
        home.mkdir()
        cwd = tmp_path / "project"
        cwd.mkdir()

        detail = _detected_detail(monkeypatch, home, cwd)
        assert "detected" in detail
        assert "PATH" in detail

    def test_detected_via_project_config(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda name: None)
        home = tmp_path / "home"
        home.mkdir()
        cwd = tmp_path / "project"
        cwd.mkdir()
        (cwd / ".aider.conf.yml").write_text("auto-lint: true\n", encoding="utf-8")

        detail = _detected_detail(monkeypatch, home, cwd)
        assert "detected" in detail
        assert ".aider.conf.yml" in detail

    def test_detected_via_user_config(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr("shutil.which", lambda name: None)
        home = tmp_path / "home"
        home.mkdir()
        (home / ".aider.conf.yml").write_text("auto-test: true\n", encoding="utf-8")
        cwd = tmp_path / "project"
        cwd.mkdir()

        detail = _detected_detail(monkeypatch, home, cwd)
        assert "detected" in detail
        assert ".aider.conf.yml" in detail
