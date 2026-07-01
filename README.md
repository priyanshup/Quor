# Quor

> A command-output optimization layer for LLM-assisted development — reduces token usage and improves context efficiency for AI coding assistants through deterministic, fail-open filtering.

> **Status:** Internal Alpha — not yet on PyPI. Phases 0-9 complete (605 tests passing, ruff + mypy clean). Under active development toward Phase 10 (Packaging & Release).

---

## What is Quor?

AI coding assistants spend a large share of their context window on raw command output — passing test runs, unchanged git status lines, repeated warnings, verbose build logs. That's prompt size the assistant pays for on every turn, and it crowds out the signal that actually matters: the failure, the diff, the one line that changed.

Quor sits between your shell and the assistant's context window. It runs your command exactly as it would run anyway, then applies a deterministic filtering pipeline that removes low-signal output while preserving everything that indicates success, failure, or change. The result is a smaller, higher-signal prompt — not a different one.

```
git status          →  quor git status
                           ↓
              [deterministic filtering pipeline]
                           ↓
        optimized output → AI assistant context
```

**Key properties:**

- **Deterministic.** Same input always produces the same output. No LLM calls, no ML models, no non-determinism in the filtering path itself.
- **Fail-open.** Every layer degrades to the original, unfiltered output on error rather than risking data loss. A broken filter, a plugin crash, or a timeout never blocks your command or hides information — it just means nothing gets removed that turn.
- **Transparent and configurable.** Every filtering decision is traceable: `quor explain "pytest tests/"` shows exactly what each stage removed and why. Filters are plain TOML you can read, edit, and version-control.
- **Plugin extensible.** Third-party stages and lifecycle plugins register via standard Python entry-points — no core changes required to add support for a new tool or add custom telemetry/policy logic.
- **Non-compiler architecture.** Quor doesn't parse or understand the command it runs — it runs the real tool, captures the real output, and filters text. There's no semantic model of your code, no static analysis, and nothing that could silently change what a command actually does.
- **Windows-native, pip-installable.** `pip install quor` — no Rust toolchain, no Homebrew, no compilation step. Works in locked-down corporate environments where the only allowed install path is `pip`.

Quor does not change what a command does, what it's allowed to access, or what it returns to you on your own terminal — it only changes what gets forwarded into the assistant's context window, and it tells you exactly what and why.

---

## How Quor Works

1. **The command executes normally.** Quor runs the real command (`git status`, `pytest`, etc.) exactly as it would run without Quor — same process, same exit code, same side effects.
2. **The output is captured.** Quor reads the command's stdout before it reaches the AI assistant.
3. **A deterministic filtering pipeline runs.** Rule-based stages (configurable, auditable, no ML) mark each line as keep, remove, or protect.
4. **Important information is preserved.** Failures, diffs, errors, and anything matching a "protect" rule are never removed, no matter what else a filter is configured to strip.
5. **The optimized output is sent to the AI assistant.** A smaller, higher-signal version of the same output — never a summarized or reworded one.

---

## Installation

```bash
pip install quor
quor init --claude
```

*Not yet on PyPI — available after Phase 10 (Packaging & Release).*

---

## Commands (V1)

| Command | Description |
|---|---|
| `quor init --claude` | Install the Claude Code hook |
| `quor explain <cmd>` | Show stage-by-stage trace of what Quor removed |
| `quor gain` | Show cumulative token savings (±20%) |
| `quor verify` | Run all inline filter tests |
| `quor validate [file]` | Validate a filter config file |
| `quor doctor` | Health check — hook responding? Tests passing? |

Both `quor` and `qr` are registered as entry points.

---

## Development Status

| Phase | Description | Status |
|---|---|---|
| 0 | Repository setup | **Complete** |
| 1 | ContentMask primitive + pipeline engine | **Complete** |
| 2 | Built-in compression stages | **Complete** |
| 3 | Filter config + registry | **Complete** |
| 4 | Command rewriter + classifier | **Complete** |
| 5 | Claude Code hook adapter | **Complete** |
| 6 | SQLite + JSONL tracking | **Complete** |
| 7 | CLI commands | **Complete** |
| 8 | Plugin infrastructure | **Complete** |
| 9 | Plugin discovery & loading | **Complete** |
| 10 | Packaging & release | Not started |

605 tests passing, ruff + mypy clean on `quor/` and `tests/`, verified on Python 3.11, 3.13, and 3.14. See [docs/final/PROJECT_STATUS.md](docs/final/PROJECT_STATUS.md) for the current snapshot and [docs/final/IMPLEMENTATION_PLAN.md](docs/final/IMPLEMENTATION_PLAN.md) for the full roadmap.

The operating-mode system (AUDIT / OPTIMIZE / SIMULATE) is intentionally display-only in this phase — `quor doctor` and `quor gain` show the configured mode, but the dispatcher doesn't yet branch on it. This is a scoped, documented roadmap item, not a bug; see `docs/final/PROJECT_STATUS.md` for details.

---

## Development Setup

```bash
git clone https://github.com/priyanshup/Quor.git
cd Quor
pip install -e ".[dev]"
pytest tests/
```

Requires Python 3.11+. Windows is the primary development and CI target. Pure Python — no `uv` or other non-pip tooling required.

---

## License

Apache 2.0 — see [LICENSE](LICENSE) for details.
