# Quor

[![PyPI](https://img.shields.io/pypi/v/quor)](https://pypi.org/project/quor/)
[![Python](https://img.shields.io/pypi/pyversions/quor)](https://pypi.org/project/quor/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/priyanshup/Quor/blob/main/LICENSE)

**Quor cuts the noise out of what your AI coding assistant reads** — deterministic, rule-based compression for command output, source files, and documents, so more of the context window is spent on things that actually matter.

> **Status:** [v0.4.1](https://pypi.org/project/quor/) is the latest release ([CHANGELOG](https://github.com/priyanshup/Quor/blob/main/CHANGELOG.md)) — 1,421 tests passing, `ruff`/`mypy` clean, verified on Python 3.11–3.14 on Windows and Linux.

---

## What is Quor?

Quor sits between your shell (or Claude Code's `Read` tool) and the assistant's context window. It runs your command — or reads your file — exactly as it would happen anyway, then applies a deterministic filtering pipeline that strips low-signal content while preserving everything that indicates success, failure, or change. The result: a smaller, higher-signal prompt, never a different one.

## Why Quor exists

AI coding assistants burn a large share of their context window on things that carry no real information: 500 passing test lines, unchanged `git status` entries, repeated build warnings, a 40-page PDF's page furniture. That's prompt size you pay for on every turn — and it crowds out the one line that actually matters, like the failing assertion or the changed diff hunk. Quor removes the noise, not the signal.

## Key features

| Feature | What it means |
|---|---|
| **Deterministic** | Same input → same output, always. No LLM calls, no ML models, no randomness in the filtering path. |
| **Fail-open** | A broken filter, plugin crash, or timeout falls back to the original, unfiltered output — never blocks your command or hides information. |
| **Transparent** | `quor explain "pytest tests/"` shows exactly what was removed, stage by stage. Filters are plain TOML you can read, edit, and version-control. |
| **Nothing is unrecoverable** | Every compressed output's true raw content is cached locally and linked (`[full output: <path>]`) — the original is always one click away. |
| **Secret-aware** | Warns on stderr if a known credential pattern (GitHub, AWS, Slack, private key) survives compression. Never redacts or removes it silently. |
| **Plugin-extensible** | Third-party stages and lifecycle plugins register via standard Python entry points — no core changes needed. |
| **Non-compiler architecture** | Quor doesn't parse or semantically understand your command — it runs the real tool and applies text-level rules to the real output. |
| **Windows-native, pip-installable** | `pip install quor` — no Rust toolchain, no compilation step. |

> [!NOTE]
> Quor never changes what a command does, what it's allowed to access, or what it returns on your own terminal. It only changes what gets forwarded into the assistant's context window.

## How Quor works

```
git status          →  <your Python interpreter> -m quor git status
                           ↓
              [deterministic filtering pipeline]
                           ↓
        optimized output → AI assistant context
```

1. **The command runs normally** — same process, same exit code, same side effects.
2. **Quor captures the output** before it reaches the assistant.
3. **A rule-based pipeline marks each line** `KEEP`, `COMPRESS`, or `PROTECT` — no ML, fully auditable.
4. **Only `COMPRESS` lines are dropped.** Failures, diffs, tracebacks, and anything matching a `preserve_patterns` rule are never removed.

## Installation

```bash
pip install quor
quor init --claude
```

Requires **Python 3.11+**. This installs `quor` and `qr` as commands and wires up the Claude Code hook.

## Quick start

```bash
# 1. Install
pip install quor

# 2. Wire up the Claude Code hook
quor init --claude

# 3. Confirm everything is healthy
quor doctor

# 4. Preview what Quor would do to a real command — nothing is run for real
quor explain "pytest tests/"

# 5. After using Claude Code for a while, check your savings
quor gain
```

<details>
<summary>Expected <code>quor doctor</code> output on a healthy install</summary>

```
✓ Python ≥ 3.11 — 3.11
✓ Dependency 'typer'
✓ Dependency 'pydantic'
✓ Dependency 'orjson'
✓ Dependency 'platformdirs'
✓ Dependency 'regex'
✓ Dependency 'rich'
✓ Hook script installed — C:\Users\<you>\AppData\Local\quor\quor\hooks\claude-hook.ps1
✓ Hook responds correctly
✓ No conflicting PreToolUse hooks
✓ Tracking DB readable/writable
✓ Built-in filter tests pass
✓ Mode: audit
✓ Plugin discovery — no third-party plugins installed
```

Any `✗` line tells you exactly what to run to fix it (usually `quor init --claude`).
</details>

## Supported AI assistants

| Assistant | Support |
|---|---|
| **Claude Code** | ✅ Full support — `PreToolUse` (Bash) and `PostToolUse` (Read) hooks, installed via `quor init --claude`. |
| Any other assistant | Not supported today. |

## Supported languages & formats

**Structure-aware source reading** (via Claude Code's `Read` tool — function/method bodies are compressed to signature + docstring, everything else preserved):

| Language | Extensions |
|---|---|
| Python | `.py` |
| JavaScript | `.js`, `.jsx`, `.mjs`, `.cjs` |
| TypeScript / TSX | `.ts`, `.tsx` |
| Go | `.go` |
| Rust | `.rs` |
| Java | `.java` |
| C# | `.cs` |

> [!NOTE]
> Go, Rust, Java, and C# analyzers ship as optional extras (`quor[go]`, `quor[rust]`, `quor[java]`, `quor[csharp]`) — install the one you need, e.g. `pip install "quor[go]"`.

**Document reading** (structure — headings, lists, tables — extracted instead of raw text):

| Format | Extensions |
|---|---|
| Markdown | `.md`, `.markdown` |
| Plain text | `.txt`, `.rst` |
| Word | `.docx` |
| PDF | `.pdf` |

**Command-output filters:** `git`, `pytest`, `mypy`/`ruff` (build tooling), and the Node.js/TypeScript toolchain (`npm`, `npx`, `pnpm`, `yarn`, ESLint, `tsc`, Jest, Vitest, Prettier, Next.js, Turbo), plus a generic ANSI-stripping fallback for anything else.

## Benchmarks

Quor ships a committed benchmark suite (`tests/benchmarks/`) — 60 cases across 27 categories — that runs automatically in CI and fails the build on regression. The numbers below are measured results from that suite, not live estimates:

| Filter | Sample | Reduction |
|---|---|---|
| Markdown / plain text | long document | 29.5% / 18.8% |
| Markdown / plain text | short document | 0% |
| DOCX | long design doc | 16.0% |
| DOCX | short README | 0.0% |
| PDF | long design doc | 43.2% |
| PDF | short notes | 0.0% |

> [!NOTE]
> Reduction is entirely content-dependent — there's no fixed "Quor compression rate." A short file or a small diff is already mostly signal and compresses little; that's correct behavior, not underperformance. Run `quor gain` after real usage to see your own project's numbers (a ±20% estimate, using a `char / 4` token approximation).

## Configuration

Filters are plain TOML, resolved in three tiers — **project** overrides **user** overrides **built-in**. You rarely need to touch this to get value from Quor, but every filter is inspectable and editable:

```bash
quor validate path/to/filter.toml   # validate a filter config
quor schema                          # print the filter JSON Schema
```

## Common commands

| Command | Description |
|---|---|
| `quor init --claude` | Install the Claude Code hook |
| `quor explain <cmd>` | Show a stage-by-stage trace of what Quor removed |
| `quor gain` | Show cumulative token savings (±20%) |
| `quor verify` | Run all inline filter tests |
| `quor validate [file]` | Validate a filter config file |
| `quor doctor` | Health check — hook responding? Tests passing? |
| `quor schema` | Output the filter JSON Schema to stdout |

Both `quor` and `qr` are registered entry points for every command above.

## Troubleshooting

| Problem | Fix |
|---|---|
| `'pip'`/`'python'` not recognized | Use `py -m pip install quor` / `py -m quor doctor`, or add Python's install + `Scripts` folder to your **User** PATH. |
| Multiple Python versions, unsure which is used | `py --list`, then `py -3.11 -m pip install quor`. |
| `quor doctor` shows ✗ for "Hook script installed" | Run `quor init --claude` — expected until the hook is wired up. |
| `quor doctor` shows ✗ for "No conflicting PreToolUse hooks" | Another tool already owns Claude Code's `PreToolUse` Bash hook. Two hooks rewriting the same command is unsupported and can silently drop one's changes — disable the other tool. |
| Strange import errors after installing into a venv | Windows' 260-character path limit may be truncating files under a deeply nested path — create the venv somewhere short (e.g. `C:\myvenv`). |
| Checking the installed version | `pip show quor` (`quor --version` doesn't exist yet). |

> [!WARNING]
> **`pip.exe`/`quor.exe` "Access is denied" on a locked-down corporate machine.** Some endpoint-protection policies block the small, unsigned `quor.exe`/`qr.exe` launcher stubs `pip` creates, even though they only re-invoke the interpreter. This never affects Claude Code itself — every command it runs already goes through `<your Python interpreter> -m quor ...` directly, never the launcher. When typing commands yourself, use the same enterprise-safe form:
> ```bash
> py -m pip install quor
> py -m quor doctor
> ```
> (or `python -m ...` if `py` isn't available).

## FAQ

**Does Quor use AI, or send my data anywhere?**
No. Filtering is entirely rule-based (pattern match, dedup, count, budget) and runs locally — no LLM calls, no network calls.

**Does Quor change what my command does?**
No. It runs the real command unmodified, with the same exit code and side effects. It only changes what's forwarded into the assistant's context.

**What if compression removes something I actually needed?**
Nothing is unrecoverable — the true raw output is cached locally and a `[full output: <path>]` link is appended to the compressed result.

**Which OS does Quor support?**
Windows is the primary development and CI target; CI also verifies Ubuntu (Linux) on every change.

**Which AI coding assistant does Quor support today?**
Claude Code only, via its `PreToolUse`/`PostToolUse` hooks.

**What does "Mode: audit" mean in `quor doctor`?**
Quor has a planned AUDIT / OPTIMIZE / SIMULATE mode system. Today only the mode is displayed — it doesn't yet change filtering behavior.

**How do I see how much Quor has actually saved me?**
`quor gain` — reports cumulative token savings, ±20%.

## Roadmap

Quor is under active development. Released features and their history live in [CHANGELOG.md](https://github.com/priyanshup/Quor/blob/main/CHANGELOG.md); what's being considered next is tracked in the project's backlog on GitHub.

## Contributing

```bash
git clone https://github.com/priyanshup/Quor.git
cd Quor
pip install -e ".[dev]"
pip install -e ./tests/fixtures/test_plugin
pytest tests/
```

See [CONTRIBUTING.md](https://github.com/priyanshup/Quor/blob/main/CONTRIBUTING.md) for the full contributor guide, [CODE_OF_CONDUCT.md](https://github.com/priyanshup/Quor/blob/main/CODE_OF_CONDUCT.md) for community standards, and [SECURITY.md](https://github.com/priyanshup/Quor/blob/main/SECURITY.md) to report a vulnerability.

## License

Apache 2.0 — see [LICENSE](https://github.com/priyanshup/Quor/blob/main/LICENSE) for details.
