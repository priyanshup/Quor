"""Unit tests for quor/pipeline/repo_profile/_longpath.py (QB-110).

Real Windows behavior (string transformation, filesystem round-trip) is
gated `@pytest.mark.skipif(os.name != "nt", ...)`, not simulated via
monkeypatching `os.name` off Windows — `pathlib.Path(...)` resolves to a
fixed `WindowsPath`/`PosixPath` class at interpreter startup regardless of
any later `os.name` patch (see `docs/final/DECISIONS.md`'s ADR on hook
manifest platform testing for the same constraint, found and documented
independently). The non-Windows no-op branch needs no such gating: it's
exercised for real on a POSIX CI runner without any patching at all.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from quor.pipeline.repo_profile._longpath import to_long_path


@pytest.mark.skipif(
    os.name == "nt",
    reason="tests the non-Windows no-op branch for real; on Windows this "
    "behavior is covered by TestToLongPathWindows instead",
)
class TestToLongPathNonWindows:
    def test_short_path_unchanged(self) -> None:
        path = Path("/home/user/project/file.py")
        assert to_long_path(path) == path

    def test_long_path_unchanged(self) -> None:
        path = Path("/" + "x" * 300 + "/file.py")
        assert to_long_path(path) == path

    def test_force_still_a_noop(self) -> None:
        path = Path("/" + "x" * 300 + "/file.py")
        assert to_long_path(path, force=True) == path


@pytest.mark.skipif(os.name != "nt", reason="Windows-only: extended-length path prefixing")
class TestToLongPathWindows:
    def test_short_absolute_path_unchanged(self) -> None:
        path = Path("C:\\Users\\dev\\project\\file.py")
        assert to_long_path(path) == path

    def test_long_absolute_path_gets_prefixed(self) -> None:
        long_component = "x" * 250
        path = Path(f"C:\\Users\\dev\\{long_component}\\file.py")
        result = to_long_path(path)
        assert str(result) == "\\\\?\\" + str(path)

    def test_relative_path_never_prefixed_even_if_long(self) -> None:
        path = Path("x" * 300 + "\\file.py")
        assert not path.is_absolute()
        assert to_long_path(path) == path

    def test_already_prefixed_path_is_idempotent(self) -> None:
        path = Path("\\\\?\\C:\\Users\\dev\\file.py")
        assert to_long_path(path) == path

    def test_unc_path_gets_unc_prefix_form(self) -> None:
        long_component = "x" * 250
        path = Path(f"\\\\server\\share\\{long_component}\\file.py")
        result = to_long_path(path)
        assert str(result) == "\\\\?\\UNC\\server\\share\\" + long_component + "\\file.py"

    def test_force_prefixes_a_short_absolute_path(self) -> None:
        path = Path("C:\\Users\\dev\\file.py")
        result = to_long_path(path, force=True)
        assert str(result) == "\\\\?\\" + str(path)

    def test_force_still_a_noop_on_relative_path(self) -> None:
        path = Path("dev\\file.py")
        assert to_long_path(path, force=True) == path

    def test_real_filesystem_roundtrip_past_max_path(self, tmp_path: Path) -> None:
        """The actual regression proof, not just string manipulation: build
        a real path past Windows' 260-character MAX_PATH, confirm the
        unprefixed form genuinely fails (so this test is meaningful, not
        vacuous), and that to_long_path()'s prefixed form succeeds — the
        same round-trip verified empirically during the QB-110 audit."""
        deep = tmp_path
        long_name = "package_segment_" + "x" * 30
        while len(str(deep)) < 250:
            deep = deep / long_name
        target = deep / "sample.txt"

        prefixed_dir = to_long_path(deep, force=True)
        prefixed_file = to_long_path(target)
        prefixed_dir.mkdir(parents=True, exist_ok=True)
        prefixed_file.write_text("hello", encoding="utf-8")

        assert not target.exists()  # unprefixed access silently misreports
        with pytest.raises(OSError):
            target.read_text(encoding="utf-8")

        assert prefixed_file.read_text(encoding="utf-8") == "hello"
