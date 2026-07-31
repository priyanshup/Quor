# Quor

**Your AI coding assistant is burning tokens on noise. Quor cuts it before it ever reaches the model.**
Runs entirely on your machine. No LLM, no cloud, no network call — just a deterministic rule pipeline that strips the boilerplate out of everything your assistant reads.

[![PyPI](https://img.shields.io/pypi/v/quor)](https://pypi.org/project/quor/)
[![Python](https://img.shields.io/pypi/pyversions/quor)](https://pypi.org/project/quor/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/priyanshup/Quor/blob/main/LICENSE)

> ### 35.9% smaller, on average. Up to 89% smaller on the worst offenders.
> Measured across a 127-case real-world benchmark suite, CI-gated on every single change — not a one-time demo number. See [the numbers](#the-numbers) below, or run `quor gain` / `quor dashboard` to see your own project's real savings.

| | | |
|---|---|---|
| **Local-only** | **No LLM** | **No cloud** |
| **No telemetry** | **No API keys** | **No file uploads** |
| **Deterministic** | **Fail-open** | **Enterprise-safe** |

## Install — 30 seconds

```bash
pip install quor
quor init --claude
quor doctor
```

Requires Python 3.11+. If `quor`/`qr` isn't found on your `PATH` after install, run every command below as `python -m quor ...` instead — that's exactly what Claude Code itself already uses under the hood, so it always works.

To upgrade: `pip install --upgrade quor && quor init --claude && quor doctor` — hook files live outside the package, so re-running `init` keeps them in sync.

## Why this matters

Every command your AI assistant runs — `git status`, a `pytest` run, a file read — pours straight into its context window. Most of it is boilerplate: passing tests it doesn't need to see, unchanged diff context, repeated warnings, PDF page furniture, dependency-install spam. That's tokens you're paying for, latency you're waiting on, and context-window budget your assistant isn't spending on your actual code.

The obvious fix — have the AI summarize its own output — is the wrong one: it doubles latency, doubles cost, and can silently drop the one line that mattered. Quor takes the other path: a local, rule-based pipeline that runs in milliseconds, makes the same keep/drop decision every time given the same input, and never touches the network.

```
command runs → Quor captures the output → rules mark each line KEEP / COMPRESS / PROTECT → noise drops → the assistant reads fewer tokens
```

Same command, same exit code, same side effects — only what reaches the context window changes. Failures, diffs, and tracebacks are never touched, and every compressed output links back to the full original — nothing is ever lost, just deferred until you ask for it.

## The Numbers

**35.9% average token reduction** across Quor's own 127-case benchmark suite (CI-gated — a regression here fails the build, not just a dashboard). That average includes plenty of already-terse output with nothing left to cut; on the cases that actually have noise to remove, it goes much further:

| Real command | Compression |
|---|---|
| `pip install -r requirements.txt` (mostly-cached dependencies) | **88.8%** smaller |
| A deeply nested Java exception stack trace | **88.6%** smaller |
| `pnpm install` progress noise | **77.1%** smaller |
| A large JavaScript file read through Claude Code's `Read` tool | **75.0%** smaller |

By ecosystem:

| Content | Compression |
|---|---|
| Java | 55.6% |
| Config files (JSON/TOML/YAML/lockfiles) | 53.9% |
| JavaScript | 49.5% |
| Python packaging (pip/poetry) | 47.4% |
| TypeScript | 42.5% |
| Python | 35.6% |
| CI/build logs | 35.7% |
| Documents (PDF, DOCX, Markdown) | 24.8% |

Short, already-dense output compresses little — that's correct behavior, not underperformance; Quor never trims a line just to move the number (see [ANTI_GOALS.md](docs/final/ANTI_GOALS.md#9-never-optimize-for-benchmark-numbers-at-the-expense-of-correctness)). Full breakdown in [docs/BENCHMARKS.md](docs/BENCHMARKS.md), or run `quor gain` / `quor dashboard` for your own project's real, live numbers — always shown with an honest ±20% uncertainty band, never a bare number dressed up as exact.

**Why did my `quor gain`/`quor dashboard` percentage swing a lot between two runs?** It's a running average across every command Quor has seen in the window, not a per-command score — one big compressible file read can dominate a small sample (86% after 2 commands), and the ratio settles as more commands, including plenty that are already small or have nothing Quor can filter (`ps`, `grep`, a one-line `git diff`), run afterward (7% after 45). Both numbers are correct the whole time. `quor gain`'s "On the N% of commands a filter could apply to" line, and the Passthrough count both commands show, isolate the compression rate on just the content that was actually eligible — usually the steadier number to watch.

## Commands

| | |
|---|---|
| `quor init --claude` | Install the Claude Code hook |
| `quor doctor` | Health check |
| `quor gain` | Cumulative token savings summary |
| `quor dashboard` | Live terminal view of savings for this session |
| `quor explain <cmd>` | Show what would be removed, stage by stage |
| `quor search <query>` | Semantic search across your repository |
| `quor map` / `quor symbols` / `quor graph` | Repository profile, symbol index, and dependency graph |
| `quor validate [file]` | Validate a filter config |

Full reference: `quor --help`.

## Supported

**Full compression:** Claude Code (Bash + Read hooks), Gemini CLI (command rewriting). **Detected, integration pending upstream hook support:** Codex CLI, Cursor, VS Code (GitHub Copilot agent mode), Windsurf (Cascade), Aider, Continue.dev — `quor doctor` tells you exactly which state you're in.

**Commands:** git, pytest, mypy/ruff, pip/poetry, the full Node/TypeScript toolchain (npm, pnpm, yarn, ESLint, tsc, Jest, Vitest, Prettier, Next.js, Turbo), and a generic fallback for everything else. **Source code:** Python built in; JavaScript/TypeScript, Go, Rust, Java, C# via `pip install "quor[<language>]"`. **Documents:** Markdown, TXT, DOCX, PDF via `quor[documents]`. **Config:** JSON/TOML/.env/.ini built in, YAML via `quor[yaml]`.

## Trust

Compression tooling sits in the middle of every command you run — it has to earn the right to be there.

- **Local execution only** — no network calls, no cloud, no telemetry, no API keys, ever
- **Rule-based, not AI** — pattern match, dedup, count, budget; zero ML in the filter path, zero hallucination risk
- **Fail-open** — a filter bug never blocks a command or hides output, it just returns the original untouched
- **Secret-aware** — warns (never silently strips) if a credential pattern survives compression
- **Meaning-preserving by contract** — a line Quor keeps is bit-for-bit identical to the original; nothing is rephrased or summarized (see [ANTI_GOALS.md](docs/final/ANTI_GOALS.md#3-never-silently-modify-content-meaning))
- **App-control friendly** — every invocation runs through `python -m quor` directly, never an unsigned launcher `.exe`, so corporate AppLocker/Defender policies don't get in the way

## Contributing

```bash
git clone https://github.com/priyanshup/Quor.git && cd Quor
pip install -e ".[dev]"
pytest tests/
```

[CONTRIBUTING.md](https://github.com/priyanshup/Quor/blob/main/CONTRIBUTING.md) · [SECURITY.md](https://github.com/priyanshup/Quor/blob/main/SECURITY.md) · [CHANGELOG.md](https://github.com/priyanshup/Quor/blob/main/CHANGELOG.md)

## License

Apache 2.0 — see [LICENSE](https://github.com/priyanshup/Quor/blob/main/LICENSE)
