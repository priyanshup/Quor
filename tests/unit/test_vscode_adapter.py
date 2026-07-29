"""Tests for quor.adapters.vscode_adapter — VSCodeAdapter (QB-069).

Shared DetectionOnlyAdapter contract is covered once, generically, by
test_agent_adapter_protocol.py::TestDetectionOnlyAdapterSharedContract —
this file only covers VSCodeAdapter's own `_detect()` logic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from quor.adapters.base import DoctorContext
from quor.adapters.vscode_adapter import VSCodeAdapter


class TestVSCodeAdapterDetection:
    def test_detected_when_copilot_dir_present(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_home = tmp_path / "home"
        (fake_home / ".copilot").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        checks = VSCodeAdapter().doctor_checks(DoctorContext())
        detected_check = next(c for c in checks if "detected" in c[0])
        assert "Copilot config directory detected" in detected_check[2]

    def test_not_detected_when_copilot_dir_absent(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        checks = VSCodeAdapter().doctor_checks(DoctorContext())
        detected_check = next(c for c in checks if "detected" in c[0])
        assert "not detected" in detected_check[2]

    def test_display_name_documents_copilot_agent_mode_scope(self) -> None:
        # Regression guard for the module docstring's scope note: this
        # adapter targets VS Code's bundled Copilot agent mode specifically,
        # not the editor generically — the display name must say so, since
        # it's the only thing a `quor doctor`/`quor init` user actually sees.
        assert "Copilot" in VSCodeAdapter.display_name
