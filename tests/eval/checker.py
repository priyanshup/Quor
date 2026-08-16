"""QB-048: apply one Solver attempt to a scratch copy of a task's fixture
and verify it with the task's own real tool command.

Deliberately subprocess-based, not a Python-level assertion on the fix's
text: `Task.verify_command` is the same kind of real tool invocation
(`mypy`, `pytest`, ...) a human would actually run, so "success" means the
tool itself is satisfied — not that the fix merely looks plausible.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from tests.eval.models import Task, TaskAttemptResult, VerifyOutcome
from tests.eval.solver import Solver

_VERIFY_TIMEOUT_SECONDS = 30.0


def run_attempt(
    task: Task,
    *,
    variant: str,
    context: str,
    context_tokens: int,
    solver: Solver,
    scratch_dir: Path,
) -> TaskAttemptResult:
    """Copy `task.fixture_dir` into `scratch_dir`, let `solver` attempt a
    fix for `task.fix_target_relpath` given `context`, write that attempt
    to the scratch copy, then run `task.verify_command` there. `scratch_dir`
    must not already exist — the caller owns its lifecycle (a pytest
    `tmp_path` in tests, a run-scoped temp dir for a real evaluation run)."""
    shutil.copytree(task.fixture_dir, scratch_dir)
    fix_target = scratch_dir / task.fix_target_relpath

    solver_output = solver.attempt(task, context, fix_target=fix_target)
    fix_target.write_text(solver_output, encoding="utf-8")

    verify = _run_verify(task, scratch_dir)
    return TaskAttemptResult(
        task_id=task.id,
        variant=variant,
        context_tokens=context_tokens,
        solver_output=solver_output,
        verify=verify,
    )


def _run_verify(task: Task, scratch_dir: Path) -> VerifyOutcome:
    try:
        proc = subprocess.run(  # noqa: S603 — task.verify_command is test-authored, not user input
            task.verify_command,
            cwd=scratch_dir,
            capture_output=True,
            text=True,
            timeout=_VERIFY_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = (exc.stderr or "") + f"\n[eval] verify_command timed out after {_VERIFY_TIMEOUT_SECONDS}s"
        return VerifyOutcome(returncode=-1, stdout=stdout, stderr=stderr, success=False)

    success = task.verify_success(proc.returncode, proc.stdout, proc.stderr)
    return VerifyOutcome(
        returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr, success=success
    )
