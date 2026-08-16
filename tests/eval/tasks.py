"""QB-048: the task corpus.

Deliberately one task for this first pass, per QB-048's own "Desired
outcome": "Starts small and specific rather than attempting a comprehensive
eval framework on the first pass." Add new `Task` entries here as the
harness proves itself, the same way `tests/benchmarks/manifest.toml` grew
one `[[case]]` at a time.
"""

from __future__ import annotations

import sys
from pathlib import Path

from tests.eval.models import Task

_FIXTURES_DIR = Path(__file__).parent / "fixtures"

FIX_TYPE_ERROR_TASK = Task(
    id="fix_type_error",
    description=(
        "calc.py's add_prices() is annotated to return int but returns "
        "str(total). Given mypy's output pointing at the bug, fix the "
        "function so it returns the actual int sum."
    ),
    fixture_dir=_FIXTURES_DIR / "fix_type_error",
    fix_target_relpath="calc.py",
    reproduce_command=[sys.executable, "-m", "mypy", "calc.py"],
    verify_command=[sys.executable, "verify.py"],
)

ALL_TASKS = [FIX_TYPE_ERROR_TASK]
