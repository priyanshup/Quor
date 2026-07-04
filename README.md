# Quor

> A rule-based command-output optimization and context-compression layer that reduces unnecessary LLM context while preserving important information.

> **Status:** v0.2.1 is the latest version [available on PyPI](https://pypi.org/project/quor/) (see [CHANGELOG](https://github.com/priyanshup/Quor/blob/main/CHANGELOG.md)). All 10 implementation phases complete (614 tests passing, ruff + mypy clean, verified on Python 3.11 and 3.14 across multiple machines).

---

## What is Quor?

AI coding assistants spend a large share of their context window on raw command output — passing test runs, unchanged git status lines, repeated warnings, verbose build logs. That's prompt size the assistant pays for on every turn, and it crowds out the signal that actually matters: the failure, the diff, the one line that changed.

Quor sits between your shell and the assistant's context window. It runs your command exactly as it would run anyway, then applies a deterministic filtering pipeline that removes low-signal output while preserving everything that indicates success, failure, or change. The result is a smaller, higher-signal prompt — not a different one.

```
git status          →  <your Python interpreter> -m quor git status
                           ↓
              [deterministic filtering pipeline]
                           ↓
        optimized output → AI assistant context
```

**Key properties:**

- **Deterministic preprocessing.** Same input always produces the same output. No LLM calls, no ML models, no non-determinism in the filtering path itself.
- **Token reduction and context optimization.** Removes low-signal output (unchanged lines, repeated noise, verbose logs) so more of the assistant's context budget is spent on content that matters.
- **Fail-open.** Every layer degrades to the original, unfiltered output on error rather than risking data loss. A broken filter, a plugin crash, or a timeout never blocks your command or hides information — it just means nothing gets removed that turn.
- **Transparent, rule-based processing.** Every filtering decision is traceable: `quor explain "pytest tests/"` shows exactly what each stage removed and why. Filters are plain TOML you can read, edit, and version-control.
- **Plugin extensible.** Third-party stages and lifecycle plugins register via standard Python entry-points — no core changes required to add support for a new tool or add custom telemetry/policy logic.
- **Non-compiler architecture.** Quor doesn't parse, execute, or semantically understand the command it runs — it runs the real tool, captures the real output, and applies text-level rules. There's no static analysis and nothing that changes what a command actually does or returns.
- **Windows-native, pip-installable.** `pip install quor` — no Rust toolchain, no Homebrew, no compilation step.

Quor does not change what a command does, what it's allowed to access, or what it returns to you on your own terminal — it only changes what gets forwarded into the assistant's context window, deterministically and transparently.

---

## How Quor Works

1. **The command executes normally.** Quor runs the real command (`git status`, `pytest`, etc.) exactly as it would run without Quor — same process, same exit code, same side effects.
2. **The output is captured.** Quor reads the command's stdout before it reaches the AI assistant.
3. **A deterministic filtering pipeline runs.** Rule-based stages (configurable, auditable, no ML) mark each line as keep, remove, or protect.
4. **Important information is preserved.** Failures, diffs, errors, and anything matching a "protect" rule are never removed, no matter what else a filter is configured to strip.
5. **The optimized output is sent to the AI assistant.** A smaller, higher-signal version of the same output — never a summarized or reworded one.

---

## Performance & Token Reduction

### How Quor reduces context

Quor's pipeline annotates every output line with one of three decisions —
`KEEP`, `COMPRESS`, or `PROTECT` — and the final render drops every
`COMPRESS` line. Reduction comes entirely from removing lines that carry no
new information for the assistant, never from summarizing, rewriting, or
truncating the meaning of the lines that remain.

**Algorithms responsible for compression:**

- `remove_ansi` — strips lines that are pure terminal escape/color codes once stripped of formatting.
- `strip_lines` — removes lines matching configured noise patterns (e.g. `PASSED` lines, dot-progress output), while `preserve_patterns` marks matching lines `PROTECT` unconditionally.
- `deduplicate_consecutive` — collapses runs of identical adjacent lines to the first occurrence.
- `group_repeated` — collapses N repetitions of a matched pattern into the first instance plus a `(×N)` count.
- `max_tokens` — truncates beyond a configured budget using a `head`, `tail`, or `both` strategy, only after the above stages have already removed redundant content. The budget is a **best-effort target, not a guarantee**: `PROTECT` lines (see below) always take precedence and are never truncated to meet it, so rendered output can exceed the configured limit when protected content alone is large (e.g. a `git diff` with many changed lines — see "Why reduction varies by content" below).

### Why reduction varies by content

Compression ratio depends entirely on how repetitive or verbose the actual
output is — there is no fixed "Quor compression rate." A clean `pytest`
run with 500 passing tests and one failure compresses aggressively (500
near-identical `PASSED` lines carry no new information). A `git diff`
against a single changed file compresses very little, because nearly every
line is already signal.

**Compresses well:** long runs of passing test output, unchanged `git
status` entries, repeated build warnings, verbose dependency-resolution
logs, ANSI-heavy terminal formatting.

**Intentionally preserved, never compressed:** failures, tracebacks,
`AssertionError` and similar exception text, diff hunks, anything matching
a filter's `preserve_patterns`, and any line already marked `PROTECT` by an
earlier stage — no later stage can downgrade a `PROTECT` decision.

### Why determinism matters here

Because every stage is a rule (pattern match, dedup, count, or budget) and
never a model call, the same input always produces the same output, and
`quor explain <command>` can show the exact stage-by-stage reasoning behind
every decision. This is what makes the behavior auditable and predictable
in a way a summarization-based approach cannot be: nothing is ever
paraphrased, and nothing is removed without a rule you can inspect (and
override) in a TOML file.

### Benchmark framework

Token savings should be measured as:

```
reduction % = 1 - (output_tokens / input_tokens)
```

using Quor's own `char / 4` token estimator (`quor/tracking/db.py::count_tokens`,
documented as a ±20% approximation — the same figure `quor gain` reports),
against a representative sample of *real* command output for each scenario,
not synthetic or cherry-picked examples. A credible benchmark run should
capture, per scenario: the command, raw output token count, filtered output
token count, which filter/stages fired, and whether the output was a
passing run, a failing run, or a mixed run — since pass/fail mix is the
single biggest driver of reduction percentage for test-runner output.

| Scenario | Input Tokens | Output Tokens | Reduction % | Notes |
|---|---|---|---|---|
| `pytest` — large suite, all passing | *To be measured* | *To be measured* | *To be measured* | |
| `pytest` — large suite, one failure | *To be measured* | *To be measured* | *To be measured* | |
| `git status` — many unchanged files | *To be measured* | *To be measured* | *To be measured* | |
| `git diff` — single small file changed | *To be measured* | *To be measured* | *To be measured* | |
| `ruff check` — clean | *To be measured* | *To be measured* | *To be measured* | |
| `ruff check` — several violations | *To be measured* | *To be measured* | *To be measured* | |

No measurements have been recorded yet. `quor gain` will report real,
per-project figures once Quor is in use; this table will be populated from
that data (or a dedicated benchmark script) rather than estimated.

---

## Installation

```bash
pip install quor
quor init --claude
```

That's it — `quor` and `qr` are both installed as commands, and `quor init --claude` wires up the Claude Code hook. Requires Python 3.11+.

### Updating

```bash
pip install --upgrade quor
```

`quor init --claude` does not need to be re-run after an upgrade — the installed hook script is a thin wrapper that always calls the currently-installed `quor` package, so upgrading the package is enough on its own. Check what changed in [CHANGELOG.md](https://github.com/priyanshup/Quor/blob/main/CHANGELOG.md), and confirm the new version with:

```bash
pip show quor
```

---

## Quick Start

```bash
# 1. Install
pip install quor

# 2. Wire up the Claude Code hook
quor init --claude

# 3. Confirm everything is healthy
quor doctor

# 4. See what Quor would do to a real command, without running anything for real
quor explain "pytest tests/"

# 5. After using Claude Code for a while, check your savings
quor gain
```

Expected output for step 3 (`quor doctor`) on a healthy install:

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

If any line shows `✗`, `quor doctor` prints what to run to fix it (usually `quor init --claude`).

---

## Troubleshooting

**`'pip' is not recognized` / `'python' is not recognized`**

The Python installer's "Add python.exe to PATH" option is easy to miss, especially on a per-user install. Two fixes:

- Fastest, no setup needed: use the `py` launcher or full module invocation instead of the bare command:

  ```bash
  py -m pip install quor
  py -m quor doctor
  ```

- Permanent fix: add your Python install's base folder and its `Scripts` subfolder to your **User** PATH (Windows key → "Edit environment variables for your account" → select `Path` → Edit → New). This does not require admin rights. Close and reopen your terminal afterward — existing windows won't see the change.

**Multiple Python versions installed, unsure which one Quor uses**

Use the `py` launcher to be explicit:

```bash
py --list          # show all installed versions
py -3.11 -m pip install quor
py -3.11 -m quor doctor
```

**`pip.exe` / `quor.exe` "Access is denied" on a locked-down corporate machine**

`pip install quor` creates `quor.exe`/`qr.exe` — small, unsigned launcher stubs in your Python install's `Scripts` folder that just re-invoke the interpreter. Some corporate endpoint-protection / application-control policies allow the interpreter itself (`python.exe`, usually a signed, known binary from a trusted install path) but block execution of these newly created, unsigned wrapper scripts, even though they do nothing but call back into that same interpreter.

This only affects commands *you* type directly. Every command Claude Code runs automatically already invokes the interpreter directly — `<your Python interpreter> -m quor ...`, never `quor.exe`/`qr.exe` — so the PreToolUse hook is unaffected by this policy (see [docs/final/DECISIONS.md](https://github.com/priyanshup/Quor/blob/main/docs/final/DECISIONS.md) ADR-029). When typing commands yourself, use the same enterprise-safe form, going through the interpreter instead of the wrapper:

```bash
py -m pip install quor
py -m quor doctor
```

or, if the `py` launcher isn't available (e.g. on Linux/macOS):

```bash
python -m pip install quor
python -m quor doctor
```

**Installing into a virtual environment on Windows and getting strange import errors**

If your venv's path is deeply nested (e.g. under a long temp directory), Windows' classic 260-character path limit can silently truncate files during install. Create the venv somewhere short (e.g. `C:\myvenv`) and reinstall.

**`quor doctor` shows a red `✗` for "Hook script installed"**

Run `quor init --claude` — this is expected on any install where the hook hasn't been wired up yet, not a bug.

**`quor doctor` shows a red `✗` for "No conflicting PreToolUse hooks"**

Another tool already has a `PreToolUse` hook registered in Claude Code's `settings.json`. Run `quor init --claude` again to review and resolve the conflict; Quor will not silently overwrite another tool's hook.

**Checking which version of Quor is installed**

There's no `quor --version` flag yet — use:

```bash
pip show quor
```

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
| `quor schema` | Output the filter file JSON Schema to stdout |

Both `quor` and `qr` are registered as entry points.

---

## Roadmap: Observability (Planned)

The following are **planned, not yet implemented.** They're listed here so
it's clear what to expect next, not as claims about current behavior:

- **Compression statistics** — richer per-run breakdowns than `quor gain`'s
  current cumulative view (per-stage contribution, per-filter history).
- **Estimated token savings inline** — surfacing the ±20% estimate at the
  point of use, not only via a separate `quor gain` invocation.
- **Before/after preview** — a way to see the unfiltered and filtered
  output side by side without needing to run `quor explain` separately.
- **Dry-run mode** — run the filtering pipeline and report what *would* be
  removed without actually altering the output sent to the assistant.
- **Verbose diagnostics** — an opt-in detailed trace mode for debugging
  filter behavior, beyond what `quor explain` already provides.

See [docs/final/ROADMAP.md](https://github.com/priyanshup/Quor/blob/main/docs/final/ROADMAP.md) for the full
version-by-version plan.

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
| 10 | Packaging & release | **Complete** — [v0.2.1 on PyPI](https://pypi.org/project/quor/) (first released as v0.1.0) |

614 tests passing, ruff + mypy clean on `quor/` and `tests/`, verified on Python 3.11, 3.13, and 3.14. See [docs/final/PROJECT_STATUS.md](https://github.com/priyanshup/Quor/blob/main/docs/final/PROJECT_STATUS.md) for the current snapshot, [docs/final/IMPLEMENTATION_PLAN.md](https://github.com/priyanshup/Quor/blob/main/docs/final/IMPLEMENTATION_PLAN.md) for the full roadmap, and [CHANGELOG.md](https://github.com/priyanshup/Quor/blob/main/CHANGELOG.md) for the full release notes.

The operating-mode system (AUDIT / OPTIMIZE / SIMULATE) is intentionally display-only in this release — `quor doctor` and `quor gain` show the configured mode, but the dispatcher doesn't yet branch on it. This is a scoped, documented roadmap item, not a bug; see `docs/final/PROJECT_STATUS.md` for details.

---

## Development Setup

```bash
git clone https://github.com/priyanshup/Quor.git
cd Quor
pip install -e ".[dev]"
pip install -e ./tests/fixtures/test_plugin
pytest tests/
```

The second install step is required for the plugin-discovery tests — see
[CONTRIBUTING.md](https://github.com/priyanshup/Quor/blob/main/CONTRIBUTING.md) for why it's a separate step rather than
a `pyproject.toml` dev dependency.

Requires Python 3.11+. Windows is the primary development and CI target. Pure Python — no `uv` or other non-pip tooling required.

---

## Contributing

See [CONTRIBUTING.md](https://github.com/priyanshup/Quor/blob/main/CONTRIBUTING.md) for the full contributor guide, [CODE_OF_CONDUCT.md](https://github.com/priyanshup/Quor/blob/main/CODE_OF_CONDUCT.md) for community standards, and [SECURITY.md](https://github.com/priyanshup/Quor/blob/main/SECURITY.md) to report a vulnerability.

---

## License

Apache 2.0 — see [LICENSE](https://github.com/priyanshup/Quor/blob/main/LICENSE) for details.
