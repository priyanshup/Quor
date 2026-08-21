"""Global test fixtures for Quor.

The autouse `_isolate_platformdirs` fixture redirects platformdirs to tmp_path
so no test ever reads from or writes to the real user config/data directories.

The autouse `_isolate_tempdir` fixture redirects tempfile.gettempdir() the
same way (QB-123): the CLI's root callback runs a startup orphan-temp-dir
sweep (quor/pipeline/orphan_sweep.py) that globs and deletes stale
`quor_*`-prefixed directories under the OS temp dir. Without this isolation,
every test that invokes the Typer CLI app touches the real, shared,
machine-wide %TEMP% — observed in practice to collide with legitimate
`quor_*`-prefixed directories other tests create there (e.g.
tests/unit/test_repo_intel_benchmark.py's own tempfile.mkdtemp() fixtures),
causing unrelated test failures.
"""

from __future__ import annotations

import tempfile
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


@pytest.fixture(autouse=True)
def _isolate_tempdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect tempfile.gettempdir() to a per-test temp directory — see
    module docstring for why. A test that wants to assert against a
    specific fake OS temp dir (e.g. tests/unit/test_zero_file_tee.py's
    TestOrphanSweeper) still overrides this locally with its own
    monkeypatch, which simply takes precedence."""
    fake_system_temp = tmp_path / "systemp"
    fake_system_temp.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(fake_system_temp))
