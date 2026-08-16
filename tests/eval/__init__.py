"""QB-048: task-success evaluation harness.

Separate from `tests/benchmarks/` (QB-011) on purpose — that suite measures
one thing (how much smaller did the output get) and is a fast, deterministic
CI gate. This suite measures a different, harder thing (did the AI still
succeed at the task using the smaller output) and needs a real Solver
(an AI attempting the task) to mean anything beyond scaffolding — see
`solver.py`'s module docstring for what that requires and why none is wired
in yet.
"""

from __future__ import annotations
