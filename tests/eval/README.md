# Task-Success Evaluation Harness (QB-048)

Measures a different axis than `tests/benchmarks/` (QB-011): not "how much
smaller did the output get" but "did the AI still succeed at the task using
the smaller output." Kept as a separate suite on purpose — see
`tests/benchmarks/README.md`'s own "two signals, deliberately kept
separate" framing, extended here to a third signal QB-048 exists to add.

## Status

**Scaffolding, verified end to end; no real evaluation runs exist yet.**
Every piece is real, tested code — real `mypy`/`pytest` subprocess calls,
real `quor.filters.registry.FilterRegistry` compression, real scratch-copy
isolation — except the one piece that genuinely cannot be built
deterministically: `Solver` (`solver.py`), the interface a real model call
would implement to attempt a task given compressed or uncompressed context.
This repo has no `anthropic` SDK dependency and no API key configured in
the environment this was built in, so no real Solver is wired in. Wiring
one (e.g. an `AnthropicSolver` calling the Messages API) is a self-contained
addition — nothing else in this package needs to change, since `runner.py`
and `checker.py` only depend on the `Solver` Protocol.

## Architecture

```
tests/eval/
├── README.md          — this file
├── models.py           — Task, VerifyOutcome, TaskAttemptResult, TaskReport
├── solver.py            — Solver Protocol + MockSolver (no real model call)
├── checker.py            — apply an attempt to a scratch copy, run task.verify_command
├── runner.py              — build_context() (real reproduce_command + real FilterRegistry),
│                            run_task() (both variants, side by side)
├── tasks.py               — the task corpus (one task for this first pass)
├── fixtures/<task_id>/     — pristine starting state, never mutated (copied per attempt)
└── test_runner.py          — pytest @pytest.mark.integration coverage, MockSolver only
```

A `Task` names a `reproduce_command` (a real tool invocation, e.g.
`python -m mypy calc.py`) and a `verify_command` (a real check that proves
whether a fix actually worked, e.g. re-running `mypy` and `pytest`). For
each task, `run_task()`:

1. Runs `reproduce_command` against the pristine fixture to get real noisy
   output, then compresses it through Quor's real, unmodified
   `FilterRegistry` — the same lookup/apply path the real dispatcher uses,
   same pattern `tests/benchmarks/benchmark_runner.py` already established.
2. For each variant (compressed, uncompressed): copies the fixture into an
   isolated scratch directory, asks the `Solver` to attempt a fix given
   that variant's context, writes the attempt, runs `verify_command` there.
3. Returns a `TaskReport` pairing both outcomes.
   `TaskReport.compression_cost_task_success` is `True` exactly when
   compression caused a task to fail that would have succeeded
   uncompressed — the specific regression QB-048 exists to catch.

## Running

```
pytest -m integration tests/eval/test_runner.py    # real mypy/pytest calls, MockSolver
```

Excluded from the default `pytest`/`pytest tests/` sweep (`-m "not
integration"` in `pyproject.toml`'s `addopts`), same convention
`tests/integration/` already uses — these are real-subprocess,
real-filesystem tests, not fast unit tests.

## Adding a task

Per QB-048's own "Desired outcome": start small and specific, not a
comprehensive framework. Add a `Task` to `tasks.py`, a fixture directory
under `fixtures/<id>/` with the buggy starting state, and a
`verify_command` that's genuinely hard to game (see
`fixtures/fix_type_error/verify.py` — mypy alone is satisfiable with a
`# type: ignore`, so this task's verifier requires both a clean type check
*and* a passing behavioral test).

## Not yet built

- A real, model-backed `Solver` — needs an API key/SDK decision, out of
  this pass's scope.
- Aggregate reporting across multiple tasks (`tests/benchmarks/report.py`'s
  equivalent) — premature with one task; add once the corpus grows.
- CLI entrypoint (`tests/benchmarks/run_benchmarks.py`'s equivalent) — same
  reasoning.
