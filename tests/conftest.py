"""Global test fixtures for Quor.

The autouse `_isolate_platformdirs` fixture redirects platformdirs to tmp_path
so no test ever reads from or writes to the real user config/data directories.
"""

from __future__ import annotations

from pathlib import Path

import platformdirs
import pytest


@pytest.fixture(autouse=True)
def _isolate_platformdirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect all platformdirs lookups to per-test temp directories."""
    config_dir = tmp_path / "config" / "quor"
    data_dir = tmp_path / "data" / "quor"
    config_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(platformdirs, "user_config_dir", lambda *_a, **_kw: str(config_dir))
    monkeypatch.setattr(platformdirs, "user_data_dir", lambda *_a, **_kw: str(data_dir))
