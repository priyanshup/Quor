"""QB-048 harness tests.

Exercises the real pipeline end to end — real `mypy`/`pytest` subprocess
calls, real `FilterRegistry` compression, real scratch-copy isolation —
with `MockSolver` standing in for the one piece that genuinely needs a real
model call (see `solver.py`'s module docstring). These tests prove the
harness's plumbing is correct; they do not, and cannot yet, prove anything
about whether compression actually costs real task success — that needs a
real Solver.

Marked `@pytest.mark.integration` (real subprocess `mypy`/`pytest` calls,
real filesystem scratch copies) per tests/integration/test_cli_commands.py's
established convention — excluded from the default flag-less `pytest`
sweep, run explicitly with `pytest -m integration`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.eval.checker import run_attempt
from tests.eval.models import TaskAttemptResult, TaskReport, VerifyOutcome
from tests.eval.runner import build_context, run_task
from tests.eval.solver import MockSolver
from tests.eval.tasks import FIX_TYPE_ERROR_TASK

_CORRECT_FIX = (
    "def add_prices(prices: list[int]) -> int:\n"
    "    total = 0\n"
    "    for p in prices:\n"
    "        total += p\n"
    "    return total\n"
)

_UNFIXED = FIX_TYPE_ERROR_TASK.fixture_dir.joinpath("calc.py").read_text(encoding="utf-8")

pytestmark = pytest.mark.integration


class TestBuildContext:
    def test_raw_output_contains_real_mypy_error(self) -> None:
        raw, _compressed = build_context(FIX_TYPE_ERROR_TASK)
        assert "calc.py" in raw
        assert "error" in raw.lower()

    def test_compressed_output_no_larger_than_raw(self) -> None:
        """Net-expansion guardrail, same standard every compression stage
        is held to: compression must never make output larger."""
        raw, compressed = build_context(FIX_TYPE_ERROR_TASK)
        assert len(compressed) <= len(raw)

    def test_pristine_fixture_never_mutated(self) -> None:
        before = FIX_TYPE_ERROR_TASK.fixture_dir.joinpath("calc.py").read_text(encoding="utf-8")
        build_context(FIX_TYPE_ERROR_TASK)
        after = FIX_TYPE_ERROR_TASK.fixture_dir.joinpath("calc.py").read_text(encoding="utf-8")
        assert before == after


class TestRunAttempt:
    def test_correct_fix_passes_verification(self, tmp_path: Path) -> None:
        result = run_attempt(
            FIX_TYPE_ERROR_TASK,
            variant="uncompressed",
            context="irrelevant to MockSolver",
            context_tokens=0,
            solver=MockSolver(_CORRECT_FIX),
            scratch_dir=tmp_path / "scratch",
        )
        assert result.success is True
        assert result.verify.returncode == 0

    def test_unfixed_code_fails_verification(self, tmp_path: Path) -> None:
        result = run_attempt(
            FIX_TYPE_ERROR_TASK,
            variant="uncompressed",
            context="irrelevant to MockSolver",
            context_tokens=0,
            solver=MockSolver(_UNFIXED),
            scratch_dir=tmp_path / "scratch",
        )
        assert result.success is False

    def test_scratch_copy_isolated_from_fixture(self, tmp_path: Path) -> None:
        """The scratch copy, not the pristine fixture, is what gets
        overwritten with the solver's attempt."""
        before = FIX_TYPE_ERROR_TASK.fixture_dir.joinpath("calc.py").read_text(encoding="utf-8")
        run_attempt(
            FIX_TYPE_ERROR_TASK,
            variant="uncompressed",
            context="irrelevant to MockSolver",
            context_tokens=0,
            solver=MockSolver(_CORRECT_FIX),
            scratch_dir=tmp_path / "scratch",
        )
        after = FIX_TYPE_ERROR_TASK.fixture_dir.joinpath("calc.py").read_text(encoding="utf-8")
        assert before == after
        assert before != _CORRECT_FIX


class TestRunTask:
    def test_correct_fix_succeeds_on_both_variants(self, tmp_path: Path) -> None:
        report = run_task(
            FIX_TYPE_ERROR_TASK,
            MockSolver(_CORRECT_FIX),
            scratch_root=tmp_path,
        )
        assert report.compressed.success is True
        assert report.uncompressed.success is True
        assert report.compression_cost_task_success is False

    def test_unfixed_code_fails_on_both_variants(self, tmp_path: Path) -> None:
        report = run_task(
            FIX_TYPE_ERROR_TASK,
            MockSolver(_UNFIXED),
            scratch_root=tmp_path,
        )
        assert report.compressed.success is False
        assert report.uncompressed.success is False
        assert report.compression_cost_task_success is False

    def test_compressed_context_shorter_than_uncompressed(self, tmp_path: Path) -> None:
        report = run_task(
            FIX_TYPE_ERROR_TASK,
            MockSolver(_CORRECT_FIX),
            scratch_root=tmp_path,
        )
        assert report.compressed.context_tokens <= report.uncompressed.context_tokens


class TestTaskReportComparison:
    """Direct unit coverage for compression_cost_task_success — the one
    property QB-048 actually exists to catch — without needing two
    real Solvers whose behavior genuinely differs by context."""

    def _result(self, *, success: bool) -> TaskAttemptResult:
        return TaskAttemptResult(
            task_id="t",
            variant="x",
            context_tokens=10,
            solver_output="output",
            verify=VerifyOutcome(returncode=0 if success else 1, stdout="", stderr="", success=success),
        )

    def test_flags_compression_regression(self) -> None:
        report = TaskReport(
            task_id="t",
            compressed=self._result(success=False),
            uncompressed=self._result(success=True),
        )
        assert report.compression_cost_task_success is True

    def test_no_flag_when_both_succeed(self) -> None:
        report = TaskReport(
            task_id="t",
            compressed=self._result(success=True),
            uncompressed=self._result(success=True),
        )
        assert report.compression_cost_task_success is False

    def test_no_flag_when_both_fail(self) -> None:
        report = TaskReport(
            task_id="t",
            compressed=self._result(success=False),
            uncompressed=self._result(success=False),
        )
        assert report.compression_cost_task_success is False
