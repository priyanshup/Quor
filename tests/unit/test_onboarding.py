"""Unit tests for quor/pipeline/onboarding.py: PA-F08 onboarding mode.

The autouse _isolate_platformdirs fixture (tests/conftest.py) redirects
platformdirs.user_data_dir to a per-test temp directory, so
record_filtered_command()'s state file is already test-isolated with no
extra setup needed here.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from quor.pipeline.onboarding import (
    MAX_ONBOARDING_COMMANDS,
    _state_path,
    _write_count_atomic,
    record_filtered_command,
)


class TestRecordFilteredCommand:
    def test_first_call_returns_one(self) -> None:
        assert record_filtered_command() == 1

    def test_sequence_increments(self) -> None:
        assert record_filtered_command() == 1
        assert record_filtered_command() == 2
        assert record_filtered_command() == 3

    def test_returns_none_after_max(self) -> None:
        for expected in range(1, MAX_ONBOARDING_COMMANDS + 1):
            assert record_filtered_command() == expected
        # The 6th call (one past MAX_ONBOARDING_COMMANDS=5) must be silent.
        assert record_filtered_command() is None

    def test_stays_none_well_past_max(self) -> None:
        for _ in range(MAX_ONBOARDING_COMMANDS):
            record_filtered_command()
        for _ in range(10):
            assert record_filtered_command() is None

    def test_state_persists_across_separate_calls(self) -> None:
        """Each call re-reads the on-disk counter rather than relying on
        any in-process/module-level state — matches the real usage pattern
        (each `quor <command>` invocation is a separate process)."""
        record_filtered_command()
        record_filtered_command()
        assert record_filtered_command() == 3

    def test_corrupted_state_file_treated_as_never_recorded(self) -> None:
        """A state file with foreign/corrupted content (not a bare integer)
        must not crash dispatch — treated as count 0 rather than raising,
        since losing an exact onboarding count is cosmetic."""
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not-an-integer", encoding="utf-8")

        assert record_filtered_command() == 1

    def test_write_failure_cleans_up_tempfile_and_raises(self, tmp_path: Path) -> None:
        """A write failure (e.g. permission denied) must not leave a stray
        .tmp file behind, and must still raise so the caller's fail-open
        wrapper (quor/adapters/dispatcher.py's _maybe_print_onboarding_tip_safe)
        can catch and warn on it, matching tee.py's documented convention."""
        path = tmp_path / "onboarding_count.txt"
        with (
            patch("os.replace", side_effect=OSError("permission denied")),
            pytest.raises(OSError, match="permission denied"),
        ):
            _write_count_atomic(path, 1)

        leftover_tmp_files = list(tmp_path.glob("*.tmp"))
        assert leftover_tmp_files == []
