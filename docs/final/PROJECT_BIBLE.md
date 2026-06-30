# PROJECT BIBLE
## Quor — Context Compression Middleware for AI Coding Assistants

> This document is the single source of truth for all implementation decisions.
> Every future Claude Code session, every contributor, every reviewer reads this first.
> When this document conflicts with any archived research, this document wins.

---

## Vision

A world where AI coding assistants operate at full effectiveness — never wasting context on test passes, progress bars, ANSI codes, or repeated diagnostics — regardless of which operating system the developer uses or which company they work for.

---

## Mission

Quor compresses AI coding assistant command output before it enters the context window. It intercepts shell commands at the hook level, applies deterministic rule-based compression, and returns clean signal where verbose noise was. It does this transparently, locally, and with full auditability.

---

## Philosophy

1. **Transparency over compression ratio.** A filter that cannot explain what it removed is dangerous. Compression ratio is a means; preserved meaning is the end.
2. **Conservative by default.** When uncertain whether to remove a line, keep it. Aggressive compression is opt-in.
3. **Fail open, always.** Any error in any stage returns the original unmodified content. The AI session must never break because of Quor.
4. **Local and private.** No content is sent to any external service. No telemetry without explicit opt-in. All filtering is deterministic and offline.
5. **One name, everywhere.** The product is called Quor. Config paths, env vars, error messages, and Python package names all use `quor`. No aliases, no internal names.

---

## Product Positioning

**One-sentence value proposition:**
> Quor is the pip-installable context optimizer for AI coding assistants on Windows — transparent, rule-based, and extensible for internal tools.

**Primary differentiators:**
1. **Windows-native**: installs via `pip install quor`, requires no compilation, no Rust, no Homebrew. Works on corporate Windows environments with locked-down package managers.
2. **Transparent**: every compression decision is traceable. `quor explain "pytest tests/"` shows exactly what each stage removed and why.
3. **Python plugin system**: `pip install quor-company-tools` adds compression filters for proprietary internal CLIs. No other tool in the category offers this.
4. **Rule-based and deterministic**: the same input always produces the same output. No LLM calls, no ML models, no non-determinism in the compression path.

**Who uses Quor:**
- Corporate developers on Windows in environments where RTK (the Rust-based incumbent) is unavailable
- Python teams who want to extend compression to internal tools via Python plugins
- Developers who want to audit what the AI is seeing (transparency-first users)
- Any developer using Claude Code who wants lower token costs without changing their workflow

**Who Quor is NOT for:**
- Developers seeking ML-assisted semantic compression (see Headroom AI)
- Developers needing LLM-powered summarization (see samuelfaj/distill)
- Developers content with RTK on macOS/Linux (RTK solves their problem already)

---

## Problem Statement

AI coding assistants inject the full output of every shell command into their context window verbatim. For the 60+ most common development commands, this raw output is 50–90% noise: ANSI escape codes, passing test lines, progress bars, "entering directory" messages, unchanged resource states, repeated diagnostic warnings. This noise:

1. Consumes tokens that should hold meaningful context (code, errors, intent)
2. Inflates cost for paid AI APIs (most Claude Code usage is billed per token)
3. May cause the AI to miss important signals as earlier context is evicted under sliding window pressure

Developers cannot solve this themselves without manually adding `| head`, `| grep`, or `| tail` pipes to every command — which breaks automation, requires expertise, and adds cognitive overhead.

No existing tool solves this for Windows users who cannot install a Rust binary. No existing tool exposes its compression decisions to the developer. No existing tool allows community-contributed filters for proprietary enterprise CLIs.

---

## Target Users

**Primary: Corporate Windows developers using AI coding assistants**
- Environment: Windows 10/11 with IT-managed software restrictions
- Tools: Claude Code, corporate Python installations, internal CLI tools
- Pain point: RTK/snip not available; Homebrew not available; no pip-installable alternative exists

**Secondary: Python developers who need custom filters**
- Environment: Any OS
- Tools: Claude Code plus internal tooling (custom CI, proprietary CLIs)
- Pain point: No tool allows `pip install` of custom filter packages for proprietary tools

**Tertiary: Transparency-focused developers**
- Environment: Any OS
- Tools: Any AI coding assistant
- Pain point: Cannot verify what the AI is seeing; compression is a black box

---

## User Journey

1. **Discovery**: blog post, Claude Code forum, or GitHub search. README opens with a `quor gain` screenshot and a before/after example. States "Works on Windows. Pure Python. No Rust required." within the first paragraph.

2. **Installation**: `pip install quor`. On first run, `quor doctor` detects no hook and prints one next step.

3. **Hook registration**: `quor init --claude`. Locates `~/.claude/settings.json` (or `%USERPROFILE%\.claude\settings.json` on Windows), shows a dry-run of changes, writes atomically. Embeds the full `sys.executable` path in the hook script — not `python` or `python3`. Runs `quor doctor` automatically to verify.

4. **Onboarding**: First 5 filtered commands print to stderr (not stdout):
   ```
   [quor] git status → quor git status (hook active)
   [quor] Estimated: 847 tokens → 203 tokens (76% reduction ±20%)
   [quor] Run `quor gain` to see cumulative savings.
   ```
   After 5 commands, silent mode.

5. **Daily usage**: Invisible. Developer runs AI coding sessions normally. Quor compresses in the background.

6. **Trust building**: `quor explain "pytest tests/ -x"` shows stage-by-stage trace. `quor gain` shows savings. Both commands convince the user that filtering is working correctly.

7. **Customization**: User adds `transparent_prefixes` for Docker wrappers, or creates `.quor/filters.toml` for internal tools. `quor validate` checks the config.

8. **Contributing**: `quor verify` catches filter regressions. PR process requires minimum 3 inline tests per new filter.

---

## Core Principles

1. **Meaning preservation is non-negotiable.** If the AI needs a piece of information to complete its task correctly, that information must survive filtering. When uncertain, keep the line.
2. **Fail transparent, never silent.** Any error returns the original, logs to stderr, and continues. The AI session must never break.
3. **Measure before you optimize.** Every filter ships with inline tests demonstrating what is retained and what is removed.
4. **Conservative defaults, explicit opt-in for aggression.** `keep_lines_matching` is never a default. `match_output` short-circuit requires the user to configure it explicitly.
5. **Transparency before performance.** Every filter invocation records the trace. `quor explain` is first-class.
6. **Platform is not an afterthought.** Windows is tested in CI from the first commit. `~` is never used when `platformdirs` is available. `/tmp` is never used when `tempfile.mkdtemp()` is available.
7. **Every filter must be verifiable.** `quor verify` runs inline tests. A filter without tests is a bug waiting to happen.
8. **The plugin interface is a contract.** Once v1.0 ships, the `quor.compression_stage` entry-point API is stable. Breaking changes require a major version.
9. **Name things once.** The product is Quor. All paths, env vars, and error messages say `quor`. No internal alias.
10. **Honesty in metrics.** Token savings are labeled as estimates: "1,240 tokens saved (±20% — char/4 approximation)." The core hypothesis — "filtering improves AI task quality" — is stated as a belief, not a proven fact.

---

## Engineering Principles

1. Every stage produces a `ContentMask`. Stages never mutate their input. Stages never modify line content — only the `Decision` field (KEEP / COMPRESS / PROTECT).
2. `PROTECT` decisions are absolute. No subsequent stage can override a PROTECT decision.
3. Every stage implements `can_handle(content, content_type) -> bool`. If False, the stage is skipped cleanly.
4. All plugin interfaces are `@runtime_checkable Protocol`. Compliance is validated at registration time.
5. The hook entry point (`__main__.py`) has a top-level exception guard. It always returns valid JSON. It never raises.
6. SQLite writes are non-blocking (background thread). A tracking failure never delays the hook response.
7. All config models are Pydantic v2. JSON Schema is generated from the models. IDE support (yaml-language-server directive) is included in all generated config files.
8. The `regex` package (not `re`) is used for all user-defined pattern matching. It prevents catastrophic backtracking.
9. Cache keys are SHA256 of `orjson.dumps(sorted_dict)`. Credentials and timestamps are stripped before hashing.
10. Tests use an autouse fixture that creates fresh temp directories and isolated SQLite per test. No test reads from or writes to `~/.config/quor/`.
11. Never use `assert` for validation — `assert` is stripped by `python -O`. Use explicit `if/raise`.

---

## Scope (What Is In V1)

**One integration:** Claude Code PreToolUse hook. Only Claude Code at v1.0.

**Five built-in filter categories:**
- `git`: status, diff, log, blame
- `pytest`: test output filtering, failure extraction, repetition counting
- `build`: compiler errors (mypy, ruff, tsc minimal)
- `cat/read`: file contents with comment stripping (minimal mode)
- `generic`: fallback for any command (ANSI stripping, max_lines cap)

**Six CLI commands:**
- `quor init --claude` — install Claude Code hook
- `quor validate [file]` — validate config, <1 second, no execution
- `quor explain <command>` — stage-by-stage trace for a command
- `quor gain` — token savings summary
- `quor verify` — run all inline filter tests
- `quor doctor` — health check (hook responding? Tests passing? Schema current?)

**Two binaries:** `quor` and `qr` (short alias, both registered from day one)

**Three operating modes:** AUDIT (default after install), OPTIMIZE (switch manually), SIMULATE (filter development)

**Plugin system:** `quor.compression_stage` entry-point group, fail-open loading

**Pydantic config with JSON Schema:** yaml-language-server directive in all generated files

**Dual persistence:** SQLite (queryable) + JSONL (streaming, append-only)

**Content-type detection:** Heuristic (JSON, log, code, diff, text) — no ML dependency at V1

**Protected spans:** `preserve_patterns` in every stage config — creates PROTECT decisions no subsequent stage can override

**Tee mechanism:** On truncation, cache original to `~/.local/share/quor/tee/`; append `[full output: path]` to compressed output

---

## Non-Goals (What Is Explicitly Excluded)

See ANTI_GOALS.md for the complete list.

Summary of critical non-goals:
- No LLM calls in the compression path (not even for summarization)
- No ML models as core dependencies
- No multi-agent support at V1 (Cursor, Gemini, Copilot in V2)
- No watch mode at V1
- No web UI ever (CLI only)
- No telemetry without explicit opt-in
- No credentials stored anywhere
- No modification of content meaning (only removal of redundant content)

---

## Functional Requirements

| ID | Requirement |
|---|---|
| FR01 | Hook intercepts Claude Code PreToolUse commands without developer interaction |
| FR02 | Rewrite rules support compound commands (&&, \|\|, ;, &) and env prefixes |
| FR03 | Commands containing heredocs are NOT rewritten |
| FR04 | Transparent prefix recursion: `docker exec mycontainer git status` → `docker exec mycontainer quor git status` |
| FR05 | Hook embeds full sys.executable path (not `python`) to survive venv environments |
| FR06 | ContentMask pipeline: stages produce KEEP/COMPRESS/PROTECT decisions; final render step applies mask |
| FR07 | PROTECT decisions propagate through all stages and cannot be overridden |
| FR08 | `can_handle()` guard on every stage; false → stage skipped cleanly |
| FR09 | Three operating modes: AUDIT (log mask, return original), OPTIMIZE (return compressed), SIMULATE (log stats, return original) |
| FR10 | Fail-open: any error at any level returns original content unmodified |
| FR11 | TOML stages-array filter format with `preserve_patterns`, `group_repeated`, `abort_if/unless`, `on_empty` |
| FR12 | Three-tier filter lookup: `.quor/filters.toml` (git-tracked) > `~/.config/quor/filters.toml` > built-in |
| FR13 | `file://` escape hatch: stages can reference custom Python modules |
| FR14 | Entry-points plugin discovery (`quor.compression_stage` group); fail-open loading; cached after first discovery |
| FR15 | All built-in filters include minimum 3 inline tests; `quor verify` runs all |
| FR16 | SQLite tracking with WAL mode, GLOB project scoping, `Path.as_posix()` normalization, 90-day cleanup |
| FR17 | Dual persistence: every result written to SQLite and JSONL simultaneously |
| FR18 | `quor explain` shows stage-by-stage trace with lines removed, patterns matched, tokens saved |
| FR19 | `quor doctor` runs synthetic hook call to verify hook is responding |
| FR20 | `quor init --claude` shows dry-run before writing, writes atomically (tempfile + rename) |
| FR21 | `quor validate` completes in <1 second, requires no external services |
| FR22 | Secret pattern detection: flag to stderr, never block or silently redact |
| FR23 | Onboarding mode: first 5 filtered commands print brief stats to stderr |
| FR24 | Tee mechanism: original cached to tee dir; `[full output: path]` appended to compressed output |

---

## Non-Functional Requirements

| ID | Requirement | Target |
|---|---|---|
| NFR01 | Hook response latency (parse + rewrite, excluding subprocess) | <50ms p99 |
| NFR02 | Filter processing overhead (full pipeline) | <200ms for outputs up to 10,000 lines |
| NFR03 | Python startup time on Windows with corporate AV software | <300ms (measure and document) |
| NFR04 | Network calls during normal operation | Zero |
| NFR05 | Core package install (no optional extras) | No compilation, all wheel distributions |
| NFR06 | Memory per hook invocation | <50MB |
| NFR07 | SQLite write non-blocking | Background thread, hook never waits for DB |
| NFR08 | Input size cap | 10MB hard limit; above cap: filter first 5MB, append truncation notice |
| NFR09 | Test coverage (classifier + filter engine) | ≥80% line coverage |
| NFR10 | CI test suite duration (no --integration flag) | <30 seconds |
| NFR11 | Plugin API stability | No breaking changes between minor versions |

---

## Architecture Overview

```
[Claude Code AI]
    │ stdin JSON: {"tool_input": {"command": "git status"}}
    ▼
[Quor Hook Adapter — quor/adapters/claude.py]
    │ Parse command from JSON
    │ Call rewrite_command()
    │ Return modified JSON: {"command": "quor git status"}
    ▼
[Claude Code executes "quor git status"]
    │
    ▼
[Quor Dispatcher — quor/__main__.py]
    │ Run real "git status", capture stdout
    │ Detect content type (heuristic)
    │ Look up filter in registry (3-tier)
    │ If found: apply ContentMask pipeline
    │ Print compressed output to stdout
    │ Write to SQLite + JSONL (background thread)
    ▼
[AI Context Window: compressed output]
```

**ContentMask Pipeline Flow:**
```
raw string
    → split to lines
    → ContentMask(lines=[LineMask(line, KEEP, reason="", stage="")])
    → Stage 1: remove_ansi → updates COMPRESS decisions on ANSI-only lines
    → Stage 2: deduplicate_consecutive → COMPRESS repeated adjacent lines
    → Stage 3: strip_lines → COMPRESS lines matching patterns
    → Stage 4: group_repeated → collapse repeated patterns to "text (×N)"
    → Stage 5: max_tokens → COMPRESS lines beyond budget
    → Final render: join KEEP/PROTECT lines
    → if empty and on_empty defined: return on_empty string
    → print to stdout
```

**Package Structure:**
```
quor/
├── __init__.py
├── __main__.py              — version check, then imports cli.main()
├── cli/
│   ├── main.py              — typer app, registers all commands
│   └── commands/
│       ├── init.py          — quor init
│       ├── validate.py      — quor validate
│       ├── explain.py       — quor explain
│       ├── gain.py          — quor gain
│       ├── verify.py        — quor verify
│       └── doctor.py        — quor doctor
├── adapters/
│   ├── base.py              — HookAdapter Protocol, HookInput, HookOutput
│   └── claude.py            — Claude Code adapter
├── pipeline/
│   ├── mask.py              — ContentMask, LineMask, Decision enum
│   ├── engine.py            — Pipeline executor
│   ├── content_type.py      — Two-tier content detection
│   └── stages/
│       ├── base.py          — StageHandler Protocol, StageResult
│       ├── remove_ansi.py
│       ├── strip_lines.py
│       ├── group_repeated.py
│       ├── deduplicate_consecutive.py
│       ├── max_tokens.py
│       └── file_stage.py    — file:// escape hatch loader
├── filters/
│   ├── registry.py          — Three-tier lookup (project > user > built-in)
│   ├── loader.py            — TOML parser → FilterConfig
│   ├── trust.py             — Git-tracked file verification
│   └── builtin/             — Built-in TOML filter files
│       ├── git.toml
│       ├── pytest.toml
│       ├── build.toml
│       ├── cat.toml
│       └── generic.toml
├── rewrite/
│   ├── classifier.py        — classify_command(), rewrite_command()
│   ├── rules.py             — RULES list (general-to-specific ordering)
│   └── lexer.py             — Quote-aware shell tokenizer
├── tracking/
│   ├── db.py                — SQLite + JSONL writer (background thread)
│   └── schema.sql           — Schema with migrations table
├── config/
│   ├── model.py             — Pydantic v2 config models
│   └── loader.py            — TOML load + env var overrides
└── errors.py                — Exception hierarchy
```

---

## Compression Philosophy

**What Quor removes:**
- ANSI/terminal escape codes
- Lines matching configurable strip patterns (e.g., `^PASSED`, `^\\.+`)
- Consecutive duplicate lines
- Lines beyond head/tail/max_tokens budget
- Lines not matching keep patterns (dangerous — requires minimum 3 tests)
- Repetitions collapsed to "message (×N)"

**What Quor never removes:**
- Lines matching `preserve_patterns` (PROTECT decision — absolute)
- Error messages, assertion failures, exception lines (should be in preserve_patterns)
- Any content where the removal could cause the AI to make a wrong decision

**The tee mechanism makes aggressive compression safe:**
Originals are cached to `~/.local/share/quor/tee/{hash}.txt`. The compressed output includes `[full output: ~/.local/share/quor/tee/abc123.txt]`. The AI can request the full output if the compressed version lacks necessary detail. This means Quor can be aggressive — nothing is irrecoverably lost.

**Token estimation:**
`ceil(len(text) / 4)` — char/4 approximation. Always labeled as an estimate with ±20% uncertainty. Never presented as exact. This is a known limitation and must be documented, not hidden.

**The unproven hypothesis:**
Filtering improves AI task quality. This is plausible (supported by "lost in the middle" research on attention dilution) but has not been measured in a controlled study on coding tasks. Quor documentation states: "We believe filtering improves AI session quality. We cannot currently prove it. `quor explain` lets you verify what filtering did so you can judge for yourself."

---

## Safety Philosophy

**Output safety:** Meaning preservation is non-negotiable. `on_empty` prevents AI from misinterpreting empty filtered output as command failure. `preserve_patterns` prevents the pipeline from removing lines the user has declared critical. Default patterns are conservative — strip only what is definitively noise.

**Command safety:** Heredoc detection prevents rewriting commands with heredocs. Pipe-incompatible commands (`find | xargs`) are not rewritten. `gh --json` commands are not rewritten (structured output would be corrupted). `cat` with flags other than `-n` is not rewritten.

**Plugin safety:** Plugin failures log a warning and skip that stage — they never halt the pipeline. A project-local filter in `.quor/` is trusted only if git-tracked (`git ls-files --error-unmatch` exits 0). Untrusted project filters warn to stderr and are skipped.

**Secret safety:** Pattern-based detection of known token formats (GitHub, AWS, Anthropic, etc.). Warnings go to stderr only — never block or silently redact. User can suppress with `QUOR_IGNORE_SECRETS=1`.

**Input safety:** 10MB hard cap on subprocess output. Above cap: filter first 5MB, append `[output truncated at 5MB]`.

---

## Plugin Philosophy

The entry-points plugin system (`quor.compression_stage` group) is the enterprise moat. No competitor offers `pip install company-quor-filters` as an answer to custom tool coverage.

**Plugin design rules:**
1. Plugin failures must be logged as warnings, never as errors that halt processing
2. The discovery result is cached to `~/.config/quor/plugin-cache.json` (invalidated when package set changes)
3. All plugins must declare `api_version: int`. The current API is version 1.
4. When the plugin API changes, increment `API_VERSION` and provide a shim for `api_version == 1` plugins
5. Plugins are `@runtime_checkable Protocol` — compliance is validated at registration, not at runtime
6. Official `quor-*` namespace plugins (published to PyPI under that prefix) must have: ≥95% inline test coverage, Windows CI run, maintainer SLA for output format updates

---

## Performance Targets

| Operation | Target | Measured How |
|---|---|---|
| Hook JSON parse + rewrite | <10ms | `quor doctor --timing` |
| Full pipeline (10,000 lines) | <200ms | Benchmark suite |
| `quor validate` | <1 second | Measured in `quor doctor` |
| Python startup on Windows (corporate AV) | <300ms (document actual) | `time python -c "import quor"` |
| Plugin discovery (first run) | <100ms | Benchmark suite |
| Plugin discovery (cache hit) | <5ms | Benchmark suite |
| SQLite write (background) | Never blocks hook | `was_blocking` flag in PipelineResult |

**If Python startup consistently exceeds 300ms on Windows:** design the persistent daemon architecture before cutting V1. The hook would connect to a running daemon via a local socket, eliminating per-command startup cost.

---

## Windows-First Strategy

Every decision that affects Windows compatibility must be verified before merge:

1. All installed packages must provide Windows x64 wheel distributions. No `pip install` that triggers MSVC compilation is acceptable.
2. All paths use `platformdirs` — never hardcode `~`, `/tmp`, `~/.config`, or `/home`.
3. Path storage in SQLite uses `Path.as_posix()` — backslashes must never appear in stored paths.
4. The hook script is a PowerShell `.ps1` file containing: `$input | python -m quor hook claude`
5. The embedded Python path in the hook script is `sys.executable` (full path), not `python` or `python3`.
6. Windows CI runs on every PR (GitHub Actions `windows-latest`).
7. File encoding: always specify `encoding="utf-8"` when opening text files. Windows default is `cp1252`.
8. Line endings: always use `\n` in generated files. Do not rely on platform default.
9. Cursor doubled-BOM (`\xEF\xBB\xBF\xEF\xBB\xBF`) handling: strip before JSON parsing (documented Windows-specific behavior).

---

## Cross-Platform Strategy

Quor is Windows-first but must not break on macOS or Linux. All features ship cross-platform. Windows-specific workarounds are in adapter code, not in the core pipeline.

**Platform-specific paths via `platformdirs`:**
- Config: `platformdirs.user_config_dir("quor")` → `%APPDATA%\quor` (Win), `~/.config/quor` (Linux), `~/Library/Application Support/quor` (Mac)
- Data: `platformdirs.user_data_dir("quor")` → `%LOCALAPPDATA%\quor` (Win), `~/.local/share/quor` (Linux), `~/Library/Application Support/quor` (Mac)

**Python version:** 3.11+ required (for stdlib `tomllib`). The version check is in `__main__.py`, runs before any import, and prints a human-readable error message with a link to the installation guide.

---

## Privacy Principles

1. No content from command outputs is ever sent to any external service.
2. No usage statistics, error reports, or analytics are collected without explicit opt-in.
3. The SQLite database contains command names and token counts only — never command output content.
4. The JSONL file contains the same fields as SQLite — never content.
5. The tee cache contains the raw command output. Users control the tee directory location. The tee directory is never uploaded anywhere.
6. Secret pattern detection results are logged to stderr only — never stored.

---

## Security Principles

1. Project-local filters are trusted only if git-tracked. Untrusted filters warn and skip.
2. Secret pattern detection warns on stdout (the AI's input) — the user decides how to act.
3. The `file://` escape hatch loads Python code from user-specified paths — this is intentional power-user functionality, documented clearly as executing Python code.
4. No `eval()` anywhere in the codebase. Use explicit `if/raise` validation.
5. No `assert` for security-critical checks. Use explicit `if/raise` (assert is stripped by `python -O`).
6. Plugin code from the `quor-*` namespace runs with the same process permissions as Quor. Users must review plugin code before installing.
7. Input sanitization for audit logs: strip newlines and pipe characters before writing to pipe-delimited logs.

---

## Statistics Philosophy

**What Quor reports:**
- Tokens saved (estimated, labeled ±20%, based on char/4)
- Filter hit rate (% of commands filtered)
- Stage utilization (which stages actually ran)
- `on_empty` trigger rate (proxy for over-aggression)
- Top uncovered commands (passthrough frequency)

**What Quor does not report:**
- AI task success rate (cannot be measured from hook position)
- "Faster AI responses" (too many confounders)
- Precise token counts (tokenizer is approximate)
- Aggregate statistics across users (no telemetry without opt-in)

**Honesty rule:** Every savings number displayed by `quor gain` includes the ±20% uncertainty label. No precise-looking numbers for imprecise measurements.

---

## Benchmark Philosophy

**Purpose:** Catch regressions, validate new filters, compare versions. NOT to prove the core hypothesis.

**Benchmark categories:**
- **Baseline** (fast, in CI): Run inline tests for every filter. Must pass on every commit. <30 seconds.
- **Regression** (on release): Compare filter behavior between versions. Flag any degradation >5%.
- **Performance** (on release): Latency benchmarks at 1KB, 10KB, 100KB, 1MB inputs.
- **Quality** (monthly, manual): LLM-as-judge evaluation of meaning preservation. Costs money. Not automated.

**`must_preserve` and `must_not_preserve` assertions:**
Every filter test should include:
- `must_contain`: patterns that must appear in filtered output (correctness)
- `must_not_contain`: patterns that must NOT appear in filtered output (noise removal)
- `compression_target`: minimum % reduction (performance)

**Honest benchmark reporting:** Results include the input, the filter applied, the output, and whether each assertion passed. Not just a single percentage.

---

## Long-Term Vision

Year 1: Quor becomes the standard choice for Python-environment AI developers and corporate Windows users. The plugin system has 10+ community-contributed filters.

Year 2: Multi-agent support (Cursor, Copilot CLI, Gemini). The `discover` command scans past Claude Code session logs for coverage gaps.

Year 3: Session-level intelligence — content the AI has already seen in the current session is not re-sent. The hook reads enough context JSON to know what was already provided.

Year 5: The defining feature is not the compression algorithm — it's the plugin ecosystem. Hundreds of `pip install quor-[tool]` packages exist, covering every major cloud CLI, build system, and monitoring tool. The filter registry is the product; Quor is the runtime.

The 5-year academic validation: a published study using Quor's built-in benchmarking infrastructure measures AI coding task success rate with and without filtering. The 12% reduction in re-run rate validates the core hypothesis that was anecdotally supported in Year 1.
