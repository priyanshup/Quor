# Quor

**Deterministic context compression for AI coding assistants.**
Runs locally. No LLM, no cloud, nothing leaves your machine — just fewer tokens, more signal.

[![PyPI](https://img.shields.io/pypi/v/quor)](https://pypi.org/project/quor/)
[![Python](https://img.shields.io/pypi/pyversions/quor)](https://pypi.org/project/quor/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/priyanshup/Quor/blob/main/LICENSE)

| | | |
|---|---|---|
| **Local-only** | **No LLM** | **No cloud** |
| **No telemetry** | **No API keys** | **No file uploads** |
| **Deterministic** | **Fail-open** | **Enterprise-safe** |

## Install

```bash
pip install quor
quor init --claude
quor doctor
```

Requires Python 3.11+. If `quor`/`qr` isn't found on your `PATH` after install, run every command below as `python -m quor ...` instead — that's exactly what Claude Code itself already uses under the hood, so it always works.

To upgrade: `pip install --upgrade quor && quor init --claude && quor doctor` — hook files live outside the package, so re-running `init` keeps them in sync.

## Why

AI coding assistants burn context on noise: passing test output, unchanged diff context, repeated warnings, PDF page furniture. Quor strips that before it reaches the model — no LLM call, just a deterministic rule pipeline that runs in milliseconds and never touches the network.

```
command runs → Quor captures the output → rules mark each line KEEP / COMPRESS / PROTECT → noise drops → the assistant reads fewer tokens
```

Same command, same exit code, same side effects — only what reaches the context window changes. Failures, diffs, and tracebacks are never touched; every compressed output links back to the full original.

## Results

**35.3% average token reduction**, measured across Quor's own 60-case benchmark suite (CI-gated on every change):

| Content | Compression |
|---|---|
| JavaScript | 52.1% |
| TypeScript | 42.5% |
| Python | 40.6% |
| PDF, long document | 43.2% |
| Markdown, long document | 29.5% |

Short, already-dense files compress little — that's correct, not underperformance. See [docs/BENCHMARKS.md](docs/BENCHMARKS.md) for the full breakdown, or run `quor gain` / `quor dashboard` for your own project's real numbers.

## Commands

| | |
|---|---|
| `quor init --claude` | Install the Claude Code hook |
| `quor doctor` | Health check |
| `quor gain` | Cumulative token savings summary |
| `quor dashboard` | Live terminal view of savings for this session |
| `quor explain <cmd>` | Show what would be removed, stage by stage |
| `quor validate [file]` | Validate a filter config |
| `quor map` | Repository profile — languages, frameworks, entry points |
| `quor symbols` | Repository-wide symbol index |
| `quor graph` | Repository dependency graph |

## Supported

**Assistant:** Claude Code. **Commands:** git, pytest, mypy/ruff, pip/poetry, the Node/TypeScript toolchain (npm, pnpm, yarn, ESLint, tsc, Jest, Vitest, Prettier, Next.js, Turbo), and a generic fallback for everything else. **Source:** Python built in; JS/TS, Go, Rust, Java, C# via `pip install "quor[<language>]"`. **Documents:** Markdown, TXT, DOCX, PDF via `quor[documents]`. **Config:** JSON/TOML/.env/.ini built in, YAML via `quor[yaml]`.

## Trust

- Local execution only — no network calls, no cloud, no telemetry, no API keys
- Rule-based, not AI — pattern match, dedup, count, budget; no ML anywhere in the filter path
- Fail-open — a filter bug never blocks a command or hides output, it just returns the original
- Secret-aware — warns (never silently strips) if a credential pattern survives compression
- Every Claude Code invocation runs through `python -m quor` directly, never an unsigned launcher `.exe`, so app-control policies don't affect it

## Contributing

```bash
git clone https://github.com/priyanshup/Quor.git && cd Quor
pip install -e ".[dev]"
pytest tests/
```

[CONTRIBUTING.md](https://github.com/priyanshup/Quor/blob/main/CONTRIBUTING.md) · [SECURITY.md](https://github.com/priyanshup/Quor/blob/main/SECURITY.md) · [CHANGELOG.md](https://github.com/priyanshup/Quor/blob/main/CHANGELOG.md)

## License

Apache 2.0 — see [LICENSE](https://github.com/priyanshup/Quor/blob/main/LICENSE)
