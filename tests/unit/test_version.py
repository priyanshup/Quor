"""Smoke tests: package installs and exports the correct version string."""

import subprocess
import sys
import tomllib
from pathlib import Path

from quor import __version__

_PYPROJECT_PATH = Path(__file__).resolve().parents[2] / "pyproject.toml"


def test_version_is_string() -> None:
    assert isinstance(__version__, str)
    assert len(__version__) > 0


def test_version_format() -> None:
    # Must start with a digit (PEP 440)
    assert __version__[0].isdigit()


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
