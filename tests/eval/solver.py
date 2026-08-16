"""QB-048: the pluggable "attempt the task" interface.

This is the one piece of the harness that genuinely cannot be built
deterministically: judging whether compression cost task success requires
something that actually *attempts the task* the way Claude would — a real
model call, given the task description and either the compressed or
uncompressed tool-output context, producing a fix.

No real, model-backed Solver is wired in here. This repo has no `anthropic`
SDK dependency and no API key configured in the environment this harness was
built in — adding one is an infrastructure/cost decision (which model, what
budget, whose credentials) outside this pass's scope, not a technical gap in
the harness itself. `runner.py`/`checker.py` only depend on the `Solver`
Protocol below, so wiring a real implementation later (e.g. an
`AnthropicSolver` calling the Messages API) is a self-contained addition —
nothing else in this package needs to change.

`MockSolver` exists so the rest of the harness (context building via the
real FilterRegistry, scratch-copy application, real subprocess verification)
is exercised by an actual, running, deterministic test suite today instead
of being unverified scaffolding.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from tests.eval.models import Task


@runtime_checkable
class Solver(Protocol):
    """`attempt()` returns the full corrected contents of the one file
    `task.fixture_dir` names as the fix target — deliberately the simplest
    possible answer shape (not a unified diff) so `checker.py` never needs
    a patch-apply step of its own that could itself have bugs. A
    diff-shaped answer is a natural extension once a first Solver exists,
    not part of this first pass."""

    def attempt(self, task: Task, context: str, *, fix_target: Path) -> str: ...


class MockSolver:
    """Deterministic stand-in for a real model call: returns the exact
    contents of a pre-written "known good fix" file, ignoring `context`
    entirely. Exists to exercise the rest of the harness — a real Solver's
    output is genuinely context-dependent (it reads the compressed or
    uncompressed tool output to decide what to fix); this one intentionally
    isn't, so tests using it are testing the harness's plumbing, not an
    AI's reasoning."""

    def __init__(self, fixed_answer: str) -> None:
        self._fixed_answer = fixed_answer

    def attempt(self, task: Task, context: str, *, fix_target: Path) -> str:
        return self._fixed_answer
