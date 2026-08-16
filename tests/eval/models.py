"""QB-048: data model for one task-success evaluation task.

A `Task` pairs a realistic, reproducible noisy-tool-output scenario (e.g. a
real `mypy` run against a fixture with a genuine type error) with a
deterministic `verify_command` that proves whether a solver's attempt
actually fixed the problem — not a `must_contain` substring check (QB-011's
kind of test), an executable one, so "success" can't be gamed by an answer
that merely looks plausible.

Deliberately narrow, per QB-048's own "Desired outcome": one reproduction
command (the noisy tool invocation), one fix, one verification command.
Multi-file/multi-step tasks are a natural extension once a first one exists
end-to-end, not part of this first pass.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Task:
    """One task-success evaluation task.

    `fixture_dir` holds the starting (buggy) state of every file the task
    touches — copied into a scratch directory per attempt, never mutated in
    place. `reproduce_command` is the real tool invocation (run from the
    scratch copy) whose output is the noisy context a solver sees, both
    compressed (through Quor's real FilterRegistry) and uncompressed.
    `verify_command` is run against the scratch copy *after* the solver's
    attempt is applied; `verify_success` decides pass/fail from its
    `CompletedProcess` (usually just `returncode == 0`, but kept a callable
    since not every tool's "success" is a bare zero exit code).
    """

    id: str
    description: str
    fixture_dir: Path
    fix_target_relpath: str
    reproduce_command: list[str]
    verify_command: list[str]
    verify_success: Callable[[int, str, str], bool] = lambda returncode, stdout, stderr: returncode == 0  # noqa: E731


@dataclass(frozen=True)
class VerifyOutcome:
    """Result of running `Task.verify_command` against a scratch copy."""

    returncode: int
    stdout: str
    stderr: str
    success: bool


@dataclass(frozen=True)
class TaskAttemptResult:
    """One (task, context variant) attempt's outcome — the harness's unit
    of reporting. `variant` is `"compressed"` or `"uncompressed"`."""

    task_id: str
    variant: str
    context_tokens: int
    solver_output: str
    verify: VerifyOutcome

    @property
    def success(self) -> bool:
        return self.verify.success


@dataclass(frozen=True)
class TaskReport:
    """Both variants' outcomes for one task, side by side — the
    comparison QB-048 actually cares about (compressed vs. uncompressed
    task success), not either result alone."""

    task_id: str
    compressed: TaskAttemptResult
    uncompressed: TaskAttemptResult

    @property
    def compression_cost_task_success(self) -> bool:
        """True if compression made this task fail where the uncompressed
        context still succeeded — the specific regression QB-048 exists to
        catch."""
        return self.uncompressed.success and not self.compressed.success
