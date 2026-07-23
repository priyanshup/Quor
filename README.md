# Quor

**Deterministic context compression for AI coding assistants.**
Runs locally. No LLM, no cloud, nothing leaves your machine — just fewer tokens, more signal.

[![PyPI](https://img.shields.io/pypi/v/quor)](https://pypi.org/project/quor/)
[![Python](https://img.shields.io/pypi/pyversions/quor)](https://pypi.org/project/quor/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/priyanshup/Quor/blob/main/LICENSE)

```bash
pip install quor && quor init --claude
```

| | | |
|---|---|---|
| **Local-only** | **No LLM** | **No cloud** |
| **No telemetry** | **No API keys** | **No file uploads** |
| **Deterministic** | **Fail-open** | **Enterprise-safe** |

---

## Why

AI coding assistants burn context on noise — 500 passing test lines, unchanged diff context, repeated warnings, PDF page furniture. That's tokens you pay for on every turn, crowding out the one line that actually matters.

Quor removes the noise before it reaches the model — not with another LLM call, but with a deterministic rule pipeline that runs in milliseconds, entirely on your machine.

- **Automatic** — hooks into Claude Code directly; no workflow change
- **Safe** — never touches failures, diffs, tracebacks, or anything matching a preserve rule
- **Recoverable** — the original output is always cached and linked, never discarded
- **Auditable** — filters are plain TOML; `quor explain` shows exactly what was cut

## How it works

```
command runs → Quor captures the output → rules mark each line KEEP / COMPRESS / PROTECT → noise is dropped → Claude reads fewer tokens
```

Same command, same exit code, same side effects — only what reaches the assistant's context changes.

## Install

```bash
pip install quor
quor init --claude
quor doctor
```

Requires Python 3.11+. Installs `quor` and `qr`, wires the Claude Code hook, and verifies the install is healthy.

## Upgrading Quor

```bash
pip install --upgrade quor
quor init --claude
quor doctor
```

The hook scripts `quor init --claude` writes (and their registration in `~/.claude/settings.json`) live outside the installed Python package, so `pip install --upgrade quor` never touches them — only the package code updates automatically. Re-run `quor init --claude` after upgrading to refresh them, then `quor doctor` to confirm. If you skip this, `quor` will print a one-line reminder the next time you run it, whenever your installed hooks are actually out of date.

## Features

| | |
|---|---|
| **Deterministic** | Pattern match, dedup, budget — no ML, no randomness, same input always gives the same output |
| **Fail-open** | A broken filter or timeout returns the original, unfiltered output — never blocks, never hides |
| **Transparent** | `quor explain "pytest tests/"` shows the exact stage-by-stage trace |
| **Recoverable** | Every compressed output links back to its full, cached original |
| **Secret-aware** | Warns on stderr if a credential pattern survives compression |
| **Plugin-extensible** | Third-party filters register via standard Python entry points |
| **Cross-language** | Python, JS/TS/TSX, Go, Rust, Java, C# source; Markdown, TXT, DOCX, PDF documents |
| **Cross-platform** | `pip install quor` — no compiler, no Rust toolchain. Windows & Linux |

## Benchmarks

Measured by Quor's own committed benchmark suite, gated in CI on every change:

| Content | Reduction |
|---|---|
| PDF, long document | 43.2% |
| Markdown, long document | 29.5% |
| DOCX, long document | 16.0% |

Short files compress little — that's correct, not underperformance. Run `quor gain` after real usage to see your own project's numbers.

## Trust

Quor is built to be safe on machines where nothing else is.

- **Local execution only** — no network calls, no cloud dependency
- **No AI models** — rule-based logic only: pattern match, dedup, count, budget
- **No data collection, no telemetry, no file uploads**
- **No API keys required**
- **Deterministic and auditable** — every filter is a readable, version-controllable TOML file
- **Built on established Python libraries** — typer, pydantic, rich, orjson
- **Fail-open by design** — a bug in a filter never blocks a command or hides output
- **Enterprise-friendly** — every Claude Code invocation runs through `python -m quor` directly, never a launcher `.exe`, so app-control policies that block unsigned stubs don't affect it

## Supported

**Assistant:** Claude Code (`PreToolUse` + `PostToolUse` hooks). Others not yet supported.

**Commands:** `git`, `pytest`, `mypy`/`ruff`, `pip`/`poetry`, the Node.js/TypeScript toolchain (`npm`, `pnpm`, `yarn`, ESLint, `tsc`, Jest, Vitest, Prettier, Next.js, Turbo), and a generic fallback for everything else.

**Source reading:** Python (built in), JavaScript/JSX, TypeScript/TSX, Go, Rust, Java, C# — install what you need: `pip install "quor[go]"` (also `rust`, `java`, `csharp`; JS/TS bundled in `quor[javascript]`).

**Documents:** Markdown, plain text, DOCX, PDF — via `pip install "quor[documents]"`.

**Config files:** JSON, TOML, `.env`, `.ini` (built in); YAML via `pip install "quor[yaml]"`. Long, homogeneous arrays/sequences/array-of-tables — a lockfile's hundreds of near-identical dependency entries — collapse to their first few entries, keys and kept values untouched; `.env`/`.ini` only ever strip comments/blank lines, never a value.

## Commands

| | |
|---|---|
| `quor init --claude` | Install the Claude Code hook |
| `quor explain <cmd>` | Show what would be removed, stage by stage |
| `quor gain` | Show cumulative token savings |
| `quor doctor` | Health check |
| `quor validate [file]` | Validate a filter config |

## Contributing

```bash
git clone https://github.com/priyanshup/Quor.git && cd Quor
pip install -e ".[dev]"
pytest tests/
```

[CONTRIBUTING.md](https://github.com/priyanshup/Quor/blob/main/CONTRIBUTING.md) · [SECURITY.md](https://github.com/priyanshup/Quor/blob/main/SECURITY.md) · [CHANGELOG.md](https://github.com/priyanshup/Quor/blob/main/CHANGELOG.md)

## License

Apache 2.0 — see [LICENSE](https://github.com/priyanshup/Quor/blob/main/LICENSE)
