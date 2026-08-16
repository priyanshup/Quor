"""QB-048: orchestrate one Task through both context variants.

Reuses `quor.filters.registry.FilterRegistry` the same read-only way
`tests/benchmarks/benchmark_runner.py` (QB-011) already does: real lookup,
real apply, no special-casing for eval purposes. `reproduce_command` is run
for real (a genuine `mypy`/`pytest`/... invocation against the task's
pristine fixture) rather than working from a captured static sample — the
noisy output a solver sees is exactly what the tool produces today, on this
machine, for this fixture.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from quor.filters.registry import FilterRegistry
from quor.tracking.db import count_tokens
from tests.eval.checker import run_attempt
from tests.eval.models import Task, TaskReport
from tests.eval.solver import Solver

_REPRODUCE_TIMEOUT_SECONDS = 30.0


def _builtin_registry() -> FilterRegistry:
    """Same `skip_user=True, skip_project=True` reasoning as
    `benchmark_runner.py`'s own helper: an eval run must reflect Quor's
    shipped, built-in filters — not whatever a contributor's own machine
    happens to have in `~/.config/quor` or a project-local override."""
    return FilterRegistry(skip_user=True, skip_project=True)


def build_context(task: Task, registry: FilterRegistry | None = None) -> tuple[str, str]:
    """Run `task.reproduce_command` against the pristine fixture; return
    `(raw_output, compressed_output)`. `compressed_output` is `raw_output`
    unchanged if no built-in filter matches the command (the same
    passthrough behavior the real dispatcher has)."""
    proc = subprocess.run(  # noqa: S603 — task.reproduce_command is test-authored, not user input
        task.reproduce_command,
        cwd=task.fixture_dir,
        capture_output=True,
        text=True,
        timeout=_REPRODUCE_TIMEOUT_SECONDS,
    )
    raw = proc.stdout + proc.stderr

    registry = registry or _builtin_registry()
    cmd_str = " ".join(task.reproduce_command)
    filter_config = registry.find(cmd_str)
    compressed = registry.apply(filter_config, raw) if filter_config is not None else raw
    return raw, compressed


def run_task(
    task: Task,
    solver: Solver,
    *,
    scratch_root: Path,
    registry: FilterRegistry | None = None,
) -> TaskReport:
    """Run `task` through both context variants against `solver`, returning
    the side-by-side comparison. `scratch_root` must not already contain
    `compressed/`/`uncompressed/` subdirectories."""
    raw, compressed = build_context(task, registry)

    uncompressed_result = run_attempt(
        task,
        variant="uncompressed",
        context=raw,
        context_tokens=count_tokens(raw),
        solver=solver,
        scratch_dir=scratch_root / "uncompressed",
    )
    compressed_result = run_attempt(
        task,
        variant="compressed",
        context=compressed,
        context_tokens=count_tokens(compressed),
        solver=solver,
        scratch_dir=scratch_root / "compressed",
    )

    return TaskReport(
        task_id=task.id,
        compressed=compressed_result,
        uncompressed=uncompressed_result,
    )
