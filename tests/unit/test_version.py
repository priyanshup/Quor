"""Smoke tests: package installs and exports the correct version string."""

import subprocess
import sys

from quor import __version__


def test_version_is_string() -> None:
    assert isinstance(__version__, str)
    assert len(__version__) > 0


def test_version_format() -> None:
    # Must start with a digit (PEP 440)
    assert __version__[0].isdigit()


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
