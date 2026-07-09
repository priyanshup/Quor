"""Smoke tests: package installs and exports the correct version string."""

import importlib
import subprocess
import sys
import tomllib
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from unittest.mock import patch

import quor
from quor import __version__

_PYPROJECT_PATH = Path(__file__).resolve().parents[2] / "pyproject.toml"


def test_version_is_string() -> None:
    assert isinstance(__version__, str)
    assert len(__version__) > 0


def test_version_format() -> None:
    # Must start with a digit (PEP 440)
    assert __version__[0].isdigit()


def test_version_derived_from_installed_metadata() -> None:
    """QB-020: __version__ is no longer a second hardcoded string -- it's
    derived from importlib.metadata at import time, which is itself derived
    from pyproject.toml at install/build time. This is the actual
    single-source-of-truth half of QB-020 (test_version_matches_pyproject
    only guards against the two ever silently diverging, it doesn't remove
    the duplication)."""
    from importlib.metadata import version

    assert __version__ == version("quor")


def test_version_falls_back_when_package_not_found() -> None:
    """QB-020: if importlib.metadata has no distribution for "quor" at all
    (a source checkout that was never `pip install`'d, editable or
    otherwise), __version__ must still resolve via the hardcoded fallback
    rather than raise ImportError / leave __version__ undefined."""
    with patch("importlib.metadata.version", side_effect=PackageNotFoundError):
        importlib.reload(quor)
        try:
            assert quor.__version__ == "0.3.0"
        finally:
            importlib.reload(quor)  # restore real behavior for later tests


def test_version_matches_pyproject() -> None:
    """Regression guard for QB-020: pyproject.toml's [project].version and
    quor/__init__.py's __version__ are two independently hand-maintained
    strings with no other link between them. They have only ever agreed
    because whoever bumped the version remembered to edit both files --
    this test is what actually enforces it, so a release can no longer ship
    with the two silently out of sync."""
    data = tomllib.loads(_PYPROJECT_PATH.read_text(encoding="utf-8"))
    pyproject_version = data["project"]["version"]
    assert __version__ == pyproject_version, (
        f"quor.__version__ ({__version__!r}) does not match pyproject.toml's "
        f"version ({pyproject_version!r}) -- bump both together."
    )


def test_quor_no_args_exits_zero() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "quor"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0


def test_quor_no_args_prints_version() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "quor"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert "quor" in result.stdout
    assert __version__ in result.stdout


def test_quor_help_exits_zero() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "quor", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0
