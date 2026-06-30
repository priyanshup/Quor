# Quor

> Context compression middleware for AI coding assistants — transparent, rule-based, and pip-installable on Windows.

> **Status:** Pre-alpha — not yet on PyPI. Under active development.

---

## What is Quor?

Quor compresses AI coding assistant command output before it enters the context window. It intercepts shell commands at the hook level, applies deterministic rule-based compression, and returns clean signal where verbose noise was — transparently, locally, with full auditability.

```
git status          →  quor git status
                           ↓
              [ContentMask pipeline]
                           ↓
           compressed output → AI context
```

**Key properties:**

- **Windows-native.** `pip install quor` — no Rust, no Homebrew, no compilation. Works on corporate Windows with locked-down package managers.
- **Transparent.** Every compression decision is traceable. `quor explain "pytest tests/"` shows exactly what each stage removed and why.
- **Extensible.** `pip install quor-company-tools` adds compression filters for proprietary internal CLIs.
- **Rule-based.** Same input always produces the same output. No LLM calls, no ML models, no non-determinism.

---

## Installation

```bash
pip install quor
quor init --claude
```

*Not yet on PyPI — available in a future release.*

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

## How It Works

Quor intercepts Claude Code's `PreToolUse` hook. Before Claude executes `git status`, the hook rewrites the command to `quor git status`. Quor runs the real `git status`, filters the output through the ContentMask pipeline, and returns compressed output to Claude's context window.

The ContentMask pipeline annotates each output line with one of three decisions:

| Decision | Meaning |
|---|---|
| `KEEP` | Line survives to the AI (default) |
| `COMPRESS` | Line is removed in the final render |
| `PROTECT` | Line is kept unconditionally — no stage can override this |

Built-in filters cover: `git`, `pytest`, build tools (`mypy`, `ruff`), `cat`, and a generic ANSI+max-lines fallback.

---

## Development Status

| Phase | Description | Status |
|---|---|---|
| Phase 0 | Repository setup | **Complete** |
| Phase 1 | ContentMask primitive + pipeline engine | **Complete** |
| Phase 2 | Built-in compression stages | **Complete** |
| Phase 3 | Filter config + registry | **Complete** |
| Phase 4 | Command rewriter + classifier | **Complete** |
| Phase 5 | Claude Code hook adapter | **Complete** |
| Phase 6 | SQLite + JSONL tracking | **Complete** |
| Phase 7 | CLI commands | **Complete** |
| Phase 8 | Plugin system | Not Started |
| Phase 9 | Packaging + PyPI | Not Started |

413 tests passing, ruff + mypy clean. See [docs/final/PROJECT_STATUS.md](docs/final/PROJECT_STATUS.md) for the current snapshot and [docs/final/IMPLEMENTATION_PLAN.md](docs/final/IMPLEMENTATION_PLAN.md) for the full roadmap.

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
