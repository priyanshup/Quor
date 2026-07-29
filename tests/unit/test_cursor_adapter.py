"""Tests for quor.adapters.cursor_adapter — CursorAdapter (QB-069).

Shared DetectionOnlyAdapter contract (supported_events, handle_event,
install, doctor_checks shape) is covered once, generically, by
test_agent_adapter_protocol.py::TestDetectionOnlyAdapterSharedContract —
this file only covers CursorAdapter's own `_detect()` logic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from quor.adapters.base import DoctorContext
from quor.adapters.cursor_adapter import CursorAdapter


class TestCursorAdapterDetection:
    def test_detected_when_config_dir_present(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_home = tmp_path / "home"
        (fake_home / ".cursor").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        checks = CursorAdapter().doctor_checks(DoctorContext())
        detected_check = next(c for c in checks if "detected" in c[0])
        assert "config directory detected" in detected_check[2]
        assert str(fake_home / ".cursor") in detected_check[2]

    def test_not_detected_when_config_dir_absent(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        checks = CursorAdapter().doctor_checks(DoctorContext())
        detected_check = next(c for c in checks if "detected" in c[0])
        assert "not detected" in detected_check[2]
