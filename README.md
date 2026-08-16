# Quor

**Your AI coding assistant is burning tokens on noise. Quor cuts it before it ever reaches the model.**
An MCP-native, zero-heuristic context-compression server. Runs entirely on your machine. No LLM, no cloud, no network call — just a deterministic rule pipeline that strips the boilerplate out of everything your assistant reads, exposed as standard MCP tools any MCP-compatible client can call.

[![PyPI](https://img.shields.io/pypi/v/quor)](https://pypi.org/project/quor/)
[![Python](https://img.shields.io/pypi/pyversions/quor)](https://pypi.org/project/quor/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/priyanshup/Quor/blob/main/LICENSE)

> ### 35.9% smaller, on average. Up to 89% smaller on the worst offenders.
> Measured across a 153-case hand-curated benchmark suite, CI-gated on every single change — not a one-time demo number. Benchmark cases are realistic, hand-authored samples, not a random draw of real usage — run `quor gain` / `quor dashboard` on your own project for what Quor actually saves you, or see [the numbers](#the-numbers) below for the full benchmark breakdown.

| | | |
|---|---|---|
| **Local-only** | **No LLM** | **No cloud** |
| **No telemetry** | **No API keys** | **No file uploads** |
| **Deterministic** | **Fail-open** | **Enterprise-safe** |

## Install — 30 seconds

```bash
pip install quor
quor init --mcp
```

This writes `./.mcp.json`, registering Quor as an MCP server for the current project, and prints the equivalent `claude_desktop_config.json` snippet for Claude Desktop or any other MCP client. Requires Python 3.11+.

To upgrade: `pip install --upgrade quor` — nothing else to re-run; there's no launcher script to go stale.

If you have a pre-0.6 install with `quor init --claude`'s old hook files still on disk, `quor init` cleans them up automatically the next time you run it (or run `quor uninstall-hooks` directly).

## Why this matters

Every command your AI assistant runs — `git status`, a `pytest` run, a file read — pours straight into its context window. Most of it is boilerplate: passing tests it doesn't need to see, unchanged diff context, repeated warnings, PDF page furniture, dependency-install spam. That's tokens you're paying for, latency you're waiting on, and context-window budget your assistant isn't spending on your actual code.

The obvious fix — have the AI summarize its own output — is the wrong one: it doubles latency, doubles cost, and can silently drop the one line that mattered. Quor takes the other path: a local, rule-based pipeline that runs in milliseconds, makes the same keep/drop decision every time given the same input, and never touches the network.

```
assistant reads a command's output → calls Quor's compress_context MCP tool on it → rules mark each line KEEP / COMPRESS / PROTECT → noise drops → the assistant gets fewer tokens back
```

Same output, same meaning — only what reaches the context window changes. Failures, diffs, and tracebacks are never touched, and every compressed output links back to the full original — nothing is ever lost, just deferred until you ask for it.

## MCP Tools

Quor runs as a standard MCP server (`quor/mcp/server.py`, stdio transport) exposing two tools:

| Tool | What it does |
|---|---|
| `compress_context(raw_text)` | Runs `raw_text` through the same deterministic filter pipeline as the CLI, returns the compressed result with a `[Quor Compressed: XX% saved]` header |
| `get_repo_context(file_path, query)` | Deterministic repository intelligence for a file (language, exported symbols, import counts) and/or files relevant to a search query — requires `quor map` to have been run first |

The calling assistant decides when to invoke `compress_context` — on a large command output, a big file read, anything it judges worth compressing before it lands in context. This is the one real trade-off of moving to MCP: compression is opt-in per call, not a transparent interception of every command the way the pre-0.6 hook-based integration was. In exchange, Quor works with any MCP-compatible client instead of one hook implementation per assistant.

## The Numbers

**35.9% average token reduction** across Quor's own 153-case, hand-curated benchmark suite (CI-gated — a regression here fails the build, not just a dashboard). That average includes plenty of already-terse output with nothing left to cut; on the cases that actually have noise to remove, it goes much further:

| Real command | Compression |
|---|---|
| `pip install -r requirements.txt` (mostly-cached dependencies) | **88.8%** smaller |
| A deeply nested Java exception stack trace | **88.6%** smaller |
| `pnpm install` progress noise | **77.1%** smaller |
| A large JavaScript file read through Claude Code's `Read` tool | **75.0%** smaller |

By ecosystem:

| Content | Compression |
|---|---|
| Java | 54.9% |
| Config files (JSON/TOML/YAML/lockfiles) | 53.9% |
| JavaScript | 49.2% |
| Python packaging (pip/poetry) | 45.4% |
| TypeScript | 42.8% |
| Python | 37.9% |
| CI/build logs | 36.2% |
| Documents (PDF, DOCX, Markdown) | 24.8% |

Short, already-dense output compresses little — that's correct behavior, not underperformance; Quor never trims a line just to move the number (see [ANTI_GOALS.md](docs/final/ANTI_GOALS.md#9-never-optimize-for-benchmark-numbers-at-the-expense-of-correctness)). Full breakdown in [docs/BENCHMARKS.md](docs/BENCHMARKS.md), or run `quor gain` / `quor dashboard` for your own project's real, live numbers — always shown with an honest ±20% uncertainty band, never a bare number dressed up as exact.

**Why did my `quor gain`/`quor dashboard` percentage swing a lot between two runs?** It's a running average across every command Quor has seen in the window, not a per-command score — one big compressible file read can dominate a small sample (86% after 2 commands), and the ratio settles as more commands, including plenty that are already small or have nothing Quor can filter (`ps`, `grep`, a one-line `git diff`), run afterward (7% after 45). Both numbers are correct the whole time. `quor gain`'s "On the N% of commands a filter could apply to" line, and the Passthrough count both commands show, isolate the compression rate on just the content that was actually eligible — usually the steadier number to watch.

## Commands

| | |
|---|---|
| `quor init --mcp` | Scaffold MCP server registration (`.mcp.json` + printed Desktop config) |
| `quor doctor` | Health check |
| `quor gain` | Cumulative token savings summary |
| `quor dashboard` | Live terminal view of savings for this session |
| `quor explain <cmd>` | Show what would be removed, stage by stage |
| `quor search <query>` | Semantic search across your repository |
| `quor map` / `quor symbols` / `quor graph` | Repository profile, symbol index, and dependency graph |
| `quor validate [file]` | Validate a filter config |
| `quor uninstall-hooks` | Remove a pre-0.6 hook-based install, if you have one |

Full reference: `quor --help`.

## Supported

**Any MCP-compatible client** can register and call Quor's tools — Claude Code, Claude Desktop, and every other client speaking the Model Context Protocol, with no per-assistant integration work required. `quor init --mcp` scaffolds registration for Claude Code (`.mcp.json`) and prints the equivalent `claude_desktop_config.json` snippet; any other MCP client follows the same `mcpServers` shape.

**Commands the filter pipeline understands:** git, pytest, mypy/ruff, pip/poetry, the full Node/TypeScript toolchain (npm, pnpm, yarn, ESLint, tsc, Jest, Vitest, Prettier, Next.js, Turbo), and a generic fallback for everything else. **Source code:** Python built in; JavaScript/TypeScript, Go, Rust, Java, C# via `pip install "quor[<language>]"`. **Documents:** Markdown, TXT, DOCX, PDF via `quor[documents]`. **Config:** JSON/TOML/.env/.ini built in, YAML via `quor[yaml]`.

## Trust

An MCP server sees whatever text an assistant hands it for compression — it has to earn the right to be there.

- **Local execution only** — no network calls, no cloud, no telemetry, no API keys, ever
- **Rule-based, not AI** — pattern match, dedup, count, budget; zero ML in the filter path, zero hallucination risk
- **Fail-open** — a filter bug never blocks a command or hides output, it just returns the original untouched
- **Secret-aware** — warns (never silently strips) if a credential pattern survives compression
- **Meaning-preserving by contract** — a line Quor keeps is bit-for-bit identical to the original; nothing is rephrased or summarized (see [ANTI_GOALS.md](docs/final/ANTI_GOALS.md#3-never-silently-modify-content-meaning))
- **App-control friendly** — the MCP server runs via `python -m quor.mcp.server` directly, never an unsigned launcher `.exe`, so corporate AppLocker/Defender policies don't get in the way

## Contributing

```bash
git clone https://github.com/priyanshup/Quor.git && cd Quor
pip install -e ".[dev]"
pytest tests/
```

[CONTRIBUTING.md](https://github.com/priyanshup/Quor/blob/main/CONTRIBUTING.md) · [SECURITY.md](https://github.com/priyanshup/Quor/blob/main/SECURITY.md) · [CHANGELOG.md](https://github.com/priyanshup/Quor/blob/main/CHANGELOG.md)

## License

Apache 2.0 — see [LICENSE](https://github.com/priyanshup/Quor/blob/main/LICENSE)
