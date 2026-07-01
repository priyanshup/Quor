# DECISIONS
## Architecture Decision Records — Quor

> Every major architectural decision is recorded here with context, options considered, and the chosen approach.
> When a future contributor asks "why did we do X?", this document answers.
> When this document conflicts with archived research, this document wins.

---

## ADR-001: Package Name — `quor`

**Status:** Decided  
**Date:** 2026-06-30

**Context:**  
The project was originally named "distill." Two blockers:
1. `distill` is taken on PyPI by a legacy packaging utility.
2. `samuelfaj/distill` (634 stars) is a TypeScript/npm project targeting the same problem with the same name — direct brand conflict.

**Options considered:**
- `distill-ai` — still confusing given the npm conflict
- `distill-ctx` — awkward
- `pare` — clean, available (at time of decision), implies removing excess
- `preen` — clean, available (at time of decision), implies polishing
- `quor` — best fit. Quor is what you do with noisy output. Metaphor is precise and memorable.

**Decision:** `quor`. Package on PyPI, CLI commands, config paths, and error messages all use this name. No aliases.

**Consequences:** Must verify `quor` is still available on PyPI before publishing. Must register early to protect the name.

---

## ADR-002: Language — Python (not Rust)

**Status:** Decided  
**Date:** 2026-06-30

**Context:**  
RTK (67k stars) is written in Rust and distributes platform-native binaries. It is the dominant market leader. A Rust rewrite would produce a more performant tool, but would not solve the actual gap.

**Options considered:**
- Rust: best performance, same distribution problem on corporate Windows (compilation, no `pip install`)
- Go: fast startup, good Windows binaries, but no `pip install` path, no plugin ecosystem
- Python: slower, but pure-Python wheels install with `pip` on any Python 3.11+ environment

**Decision:** Pure Python, no compiled extensions in core. The target user cannot install binaries on corporate Windows. `pip install quor` is the only acceptable installation path.

**Consequences:**
- Python startup time on Windows with corporate AV must be measured. If consistently >300ms, a persistent daemon architecture is needed before V1.
- All dependencies must have wheel distributions for Windows x64. No compilation triggered by `pip install`.
- Performance cap: complex ML compression stages are out of scope for core. ML is a plugin.

---

## ADR-003: Core Abstraction — ContentMask (not string→string transforms)

**Status:** Decided  
**Date:** 2026-06-30

**Context:**  
The early architectural concept (from zap-analysis.md) used 8 sequential string→string transform stages, each receiving the output of the previous stage. This approach has two problems:
1. Stages cannot reason about what previous stages removed — no provenance.
2. A line removed in stage 3 cannot be protected by a `preserve_patterns` in stage 5.

**Options considered:**
- String→string pipeline (Option A): simple but loses provenance
- ContentMask (Option B): stages annotate lines with `Decision` enums; final render applies once

**Decision:** ContentMask. Each stage receives the full `ContentMask` (array of `LineMask` with line content, current decision, reason, and stage name) and returns an updated mask. Only the final render step actually removes lines. `PROTECT` decisions are absolute — no subsequent stage can override them.

**Consequences:**
- `LineMask` is an immutable dataclass. Stages create new `LineMask` objects; they never mutate.
- `PROTECT` propagation is enforced in the `Pipeline.execute()` method, not in individual stages.
- `quor explain` can show the exact stage that set each decision and why.

---

## ADR-004: Configuration Format — TOML with Stages-Array (not Zap-compatible)

**Status:** Decided  
**Date:** 2026-06-30

**Context:**  
Two options for the filter configuration format:
- **Option A (Zap-compatible):** Flat stage fields at the filter level. Matches RTK's format, enabling potential filter migration.
- **Option B (stages-array):** Each stage is an explicit `[[filter.stages]]` entry with a `type` field.

**Options considered:**
- Option A: Migration story, but ordering is implicit, stages are not first-class, limited extensibility.
- Option B: Explicit, ordered, IDE-complete-able, self-documenting, directly represents ContentMask model.

**Decision:** Option B — `[[filter.stages]]` stages-array format. The format is NOT Zap-compatible. This was evaluated as a worthwhile tradeoff: Zap filters rarely need migration (they target different commands), and the explicit format produces significantly better DX.

**Consequences:** Quor TOML files cannot be used in RTK without conversion. A migration guide must document the differences.

**Example of the chosen format:**
```toml
schema_version = 1
# yaml-language-server: $schema=https://quor.dev/filter-schema.json

[[filter]]
name = "pytest"
match_command = '^pytest\b|^python -m pytest\b'
abort_unless = ["FAILED", "ERROR"]

  [[filter.stages]]
  type = "strip_lines"
  patterns = ['^PASSED\b', '^\\.+']
  preserve_patterns = ['^FAILED', 'AssertionError']

  [[filter.stages]]
  type = "max_tokens"
  limit = 500
  strategy = "tail"

  on_empty = "All tests passed."

[[filter.tests]]
description = "Failures preserved"
input = "PASSED test_login\nFAILED test_logout"
must_contain = ["FAILED"]
must_not_contain = ["PASSED"]
```

---

## ADR-005: Configuration Models — Pydantic v2 (not dataclasses)

**Status:** Decided  
**Date:** 2026-06-30

**Context:**  
Config models must: validate TOML input, generate JSON Schema for IDE support, and produce useful error messages. Standard `dataclasses` require a separate validation pass.

**Options considered:**
- `dataclasses`: stdlib, fast, but no built-in validation or JSON Schema generation
- `attrs`: lightweight, good validation, but adds a non-stdlib dependency with no JSON Schema benefit
- `pydantic v2`: validation + JSON Schema generation from one model definition

**Decision:** Pydantic v2 throughout. All config models (FilterConfig, StageConfig, QuorConfig) are Pydantic models. JSON Schema is generated via `model.model_json_schema()` and published to `quor.dev/filter-schema.json`.

**Consequences:**
- Pydantic v2 is a core dependency (not optional).
- The schema generation step must run in CI and the result must be committed. Schema divergence = CI failure.
- Pydantic v2 validation error messages are included in `ConfigError` without modification — they are already user-readable.

---

## ADR-006: CLI Framework — Typer (not argparse or Click)

**Status:** Decided  
**Date:** 2026-06-30

**Context:**  
The CLI has exactly 6 commands with typed arguments. Developer experience matters for `quor explain` (complex invocation).

**Options considered:**
- `argparse`: stdlib, no installation, but verbose and no automatic `--help` formatting
- `click`: mature, good DX, but requires manual type annotations
- `typer`: wraps Click, uses Python type annotations directly, produces excellent `--help` output

**Decision:** Typer. The type annotation approach is consistent with Pydantic v2's model-as-source-of-truth philosophy.

**Consequences:**
- `typer` is a core dependency.
- Each command lives in its own file under `quor/cli/commands/`. The main `cli/main.py` imports commands; it contains no implementation.

---

## ADR-007: Plugin System — Entry-Points (not dynamic import or config-level plugins)

**Status:** Decided  
**Date:** 2026-06-30

**Context:**  
Three options for the plugin architecture were evaluated. The plugin system is the enterprise moat — it must work via `pip install` and fail gracefully.

**Options considered:**
- Config-level `file://` references: works, but requires the user to copy Python files to a known location — not `pip install`-able
- Dynamic import from known paths: fragile, requires Python path manipulation
- Entry-points (`importlib.metadata`): the standard Python packaging mechanism for discoverable plugins

**Decision:** Entry-points via `quor.compression_stage` group. Third-party packages declare stages in their `pyproject.toml`:
```toml
[project.entry-points."quor.compression_stage"]
my_stage = "my_package.stages:MyStage"
```

Quor discovers these at startup using `importlib.metadata.entry_points()`. All registered stages are validated against the `StageHandler` Protocol at registration time. Plugin failures log warnings; they never halt processing.

The file:// escape hatch remains as a developer convenience for local, unreleased stages.

**Consequences:**
- Plugin discovery result is cached to `~/.config/quor/plugin-cache.json`. Cache is invalidated when the installed package set changes.
- Plugin API version (`api_version: int`) must be declared on every handler. Current API is version 1.
- Breaking plugin API changes require a major version bump.

---

## ADR-008: Persistence — Dual (SQLite + JSONL)

**Status:** Decided  
**Date:** 2026-06-30

**Context:**  
Tracking pipeline results enables `quor gain` and future analytics. Two needs: ad-hoc queries (for `quor gain`) and streaming append (for CI artifact export).

**Options considered:**
- SQLite only: great for queries, awkward for streaming CI artifacts
- JSONL only: perfect append, awkward for ad-hoc queries
- Both: redundant writes but serves both use cases correctly

**Decision:** Both. Every pipeline result is written to SQLite (WAL mode, background thread) and appended to a JSONL file. Neither write blocks the hook response.

**SQLite schema (finalized):**
```sql
CREATE TABLE invocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    command TEXT NOT NULL,
    project_path TEXT NOT NULL,
    original_tokens INTEGER NOT NULL DEFAULT 0,
    final_tokens INTEGER NOT NULL DEFAULT 0,
    ratio REAL NOT NULL DEFAULT 1.0,
    stages_applied TEXT NOT NULL DEFAULT '[]',
    content_type TEXT NOT NULL DEFAULT 'unknown',
    mode TEXT NOT NULL DEFAULT 'optimize',
    filter_name TEXT,
    was_passthrough INTEGER NOT NULL DEFAULT 0,
    duration_ms REAL NOT NULL DEFAULT 0,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
    schema_version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX idx_invocations_project ON invocations(project_path, recorded_at);
CREATE INDEX idx_invocations_filter ON invocations(filter_name, recorded_at);

CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT INTO schema_migrations (version) VALUES (1);
```

**Consequences:**
- `was_passthrough` is an explicit boolean INTEGER column (0 or 1). It is NOT a zero-token sentinel. A passthrough invocation records 0 original_tokens and 0 final_tokens alongside `was_passthrough = 1`.
- Project paths stored as `Path.as_posix()` — backslashes never appear in stored paths.
- Schema migrations tracked in `schema_migrations` table. Running the migration is the first thing Quor does on startup.
- SQLite GLOB used for project scoping (not LIKE): `WHERE project_path GLOB '/path/to/project*'`.
- 90-day cleanup runs at session start (weekly, tracked in SQLite).

---

## ADR-009: Three Operating Modes — AUDIT / OPTIMIZE / SIMULATE

**Status:** Decided  
**Date:** 2026-06-30

**Context:**  
Quor runs as a hook in an AI coding session. Trust must be earned before aggressive filtering. New users need confidence before committing to OPTIMIZE mode.

**Options considered:**
- Single mode (optimize always): fast to implement, but risks breaking new users' sessions
- Two modes (audit + optimize): logical, but filter development needs dry-run without affecting AI
- Three modes (audit + optimize + simulate): complete separation of concerns

**Decision:** Three modes:
- **AUDIT** (default after `quor init`): compute and log the ContentMask, but return original unmodified content to the AI. Tracks every invocation. Shows the user what filtering would do. Switch to OPTIMIZE when confident.
- **OPTIMIZE**: apply compression, return filtered content. The production mode.
- **SIMULATE**: apply compression internally, return original content to the AI, log detailed trace. For filter development — see what a new filter does without affecting the AI session.

**Consequences:**
- Mode is set in `~/.config/quor/config.toml` and overridable per-invocation with `QUOR_MODE` env var.
- `quor doctor` shows the current mode prominently. If mode is AUDIT for more than 7 days, `doctor` suggests switching.

---

## ADR-010: Trust System — Git-Tracked Files (not SHA-256 hash files)

**Status:** Decided  
**Date:** 2026-06-30

**Context:**  
Project-local filters (`.quor/filters.toml`) run code from the repository. An untrusted filter (e.g., from a cloned repo) could execute arbitrary Python via `file://` stages. A trust mechanism is required.

**Options considered:**
- SHA-256 hash files: user approves a filter by hashing it; Quor verifies before execution. Works but requires a separate approval flow.
- Git-tracked files: a file that is committed to git was explicitly added by someone with repository access. `git ls-files --error-unmatch .quor/filters.toml` exits 0 iff the file is tracked.
- Allowlist in global config: user maintains a list of approved project paths. Awkward UX.

**Decision:** Git-tracked files. A project-local filter is trusted if and only if `git ls-files --error-unmatch .quor/filters.toml` exits 0. If the file is untracked, Quor warns to stderr and skips it. The user must `git add .quor/filters.toml` to grant trust.

**Consequences:**
- `quor init` runs in the repository root. It checks for `.git` and warns if none found.
- The trust check happens in the filter registry loader, before any stage is instantiated.
- Trust is not inherited — if `.quor/filters.toml` is replaced by a new file (different content, same path), the new file is trusted (it is still git-tracked, even if not yet committed). This is a known limitation. Users should commit filter changes promptly.

---

## ADR-011: Command Rewriting — Hook-Level (not wrapper binary)

**Status:** Decided  
**Date:** 2026-06-30

**Context:**  
Two architectures for intercepting AI commands:
- **Hook-level rewriting**: the PreToolUse hook rewrites `"git status"` to `"quor git status"` before Claude Code executes it. Quor captures the subprocess output.
- **Transparent proxy**: Quor installs shims (`~/.local/bin/git`) that intercept all git invocations. Fragile, breaks outside AI sessions.

**Decision:** Hook-level rewriting. The Claude Code PreToolUse hook modifies the `command` field in the JSON input. The rewritten command is `quor <original>`. Quor runs the original command as a subprocess, captures stdout, applies filtering, prints filtered output.

**The rewrite rules handle:**
- Simple commands: `git status` → `quor git status`
- Compound commands: `git status && git diff` → `quor git status && quor git diff`
- Env prefixes: `FORCE_COLOR=1 git log` → `FORCE_COLOR=1 quor git log`
- Transparent prefix: `docker exec mycontainer git status` → `docker exec mycontainer quor git status`

**Heredoc exclusion:** Commands containing heredocs (`<<`) are NOT rewritten. The lexer detects heredoc syntax and passes through unchanged.

**Pipe-incompatible exclusion:** Commands piped through `xargs`, `awk`, or `sed` are not rewritten (the output would corrupt the pipe).

**Consequences:**
- The rewrite classifier must be fast (<10ms). It is tested independently with 100+ fixture commands.
- `quor explain "command"` runs the classifier and shows the rewrite decision before executing.
- Hook failures (rewrite error) return the original unmodified JSON — the AI still gets the original command.

---

## ADR-012: CLI Scope — Exactly Six Commands

**Status:** Decided  
**Date:** 2026-06-30

**Context:**  
Early design sketches had 10+ commands. The principle of "measure twice, cut once" applies: add commands when users ask for them, not speculatively.

**Options considered:**
- 10+ commands including: `quor config`, `quor list`, `quor show`, `quor test`, `quor run`, `quor migrate`, `quor cache clean`, `quor tee`, `quor watch`
- 6 commands covering the essential use cases at V1

**Decision:** Exactly six commands at V1:
1. `quor init --claude` — install Claude Code hook
2. `quor validate [file]` — validate config (< 1 second, no execution)
3. `quor explain <command>` — stage-by-stage trace
4. `quor gain` — token savings summary
5. `quor verify` — run all inline filter tests
6. `quor doctor` — health check

Both `quor` and `qr` are registered as CLI entry points from day one.

**Consequences:**
- `quor watch` (watch mode) is deferred to V2. Do not add it to V1 even if it seems easy.
- `quor config` is not needed at V1 — config is managed directly in TOML.
- V1 CLI must not grow. Every proposed command beyond the six requires a written justification and explicit user approval.

---

## ADR-013: Token Estimation — char/4 Approximation

**Status:** Decided  
**Date:** 2026-06-30

**Context:**  
Accurate token counting requires the tokenizer of the target model (BPE for Claude). The `tiktoken` library is available for OpenAI models; no public BPE tokenizer exists for Claude. The `anthropic` Python SDK does not expose a tokenizer.

**Options considered:**
- `tiktoken` (cl100k_base): reasonable approximation for Claude but adds a heavyweight dependency (C extension, Windows compilation risk)
- `anthropic.count_tokens()`: accurate but requires a network call — unacceptable for a local hook
- char/4: rough approximation, no dependency, explicit about uncertainty

**Decision:** `ceil(len(text) / 4)` with a documented ±20% uncertainty. Every displayed token count includes the uncertainty label. Never present as exact. This is a known limitation, explicitly documented.

**Consequences:**
- If Anthropic publishes a pure-Python tokenizer with Windows wheels, adopt it immediately.
- The ±20% label must appear in `quor gain`, `quor explain`, and onboarding output. No exceptions.

---

## ADR-014: Content Detection — Heuristics at V1 (ML Optional)

**Status:** Decided  
**Date:** 2026-06-30

**Context:**  
Accurate content type detection (JSON, log, code, diff, text, binary) informs which filter stages are appropriate. ML-based detection (Magika) is more accurate but adds a dependency.

**Options considered:**
- Magika (Google): high accuracy, pip-installable, but TensorFlow dependency — unacceptable for corporate Windows
- Charset-normalizer: good for encoding detection, not for content type
- Heuristics: pattern-based, deterministic, no external dependency, good enough for V1

**Decision:** Heuristics at V1. The `content_type.py` module detects: JSON (starts with `{` or `[`), ANSI-heavy terminal output, Python traceback, diff (starts with `---` / `+++`), and plain text. ML detection (Magika or similar) is designed as a plugin, available as `quor[ml]` extra. V2.

**Consequences:**
- The `can_handle(content, content_type)` guard on each stage uses this classification.
- Misclassification is possible. The PROTECT mechanism prevents misclassification from removing critical lines.
- Future ML detection integrates at the `content_type.py` boundary without changing any stage code.

---

## ADR-015: Pattern Matching — `regex` Package (not `re`)

**Status:** Decided  
**Date:** 2026-06-30

**Context:**  
User-defined patterns in filter config are interpreted as regular expressions. Python's stdlib `re` module is vulnerable to catastrophic backtracking on pathological patterns, which would hang the hook with no timeout.

**Decision:** The `regex` package (PyPI: `regex`) is used for all user-defined pattern matching. It is not used for internal hardcoded patterns (those use `re` for speed). The `regex` package provides timeout support via `regex.compile(pattern, timeout=1.0)`.

**Consequences:**
- `regex` is a core dependency.
- `regex` has Windows wheel distributions — no compilation risk.
- Internal hardcoded patterns (in built-in filter files) may use `re`. User-defined patterns always use `regex`.
- A pattern that times out after 1 second logs a warning and is skipped (fail-open).

---

## ADR-016: Package Structure — Flat `quor/` (not `src/quor/`)

**Status:** Decided  
**Date:** 2026-06-30

**Context:**  
Python packaging has two common layouts: flat (`quor/` at repo root) and src-layout (`src/quor/`). Both work. The choice affects import behavior during development.

**Options considered:**
- `src/quor/`: prevents accidental import of the uninstalled package during development; PEP 517-recommended for libraries
- `quor/`: simpler, common in CLIs, one less directory level

**Decision:** `quor/` at repo root (flat layout). Quor is a CLI tool, not a library. The src-layout benefit (preventing uninstalled imports) is less relevant for CLI tools. This matches the majority of CLI tooling in the Python ecosystem (pip, black, ruff).

**Consequences:** `pyproject.toml` uses `[tool.hatch.build.targets.wheel] packages = ["quor"]`. Development installation: `pip install -e .`.

---

## ADR-017: Hook Script Format — PowerShell (Windows-first)

**Status:** Decided  
**Date:** 2026-06-30

**Context:**  
The Claude Code PreToolUse hook is a script that reads JSON from stdin and writes JSON to stdout. On Windows, shell script options are: cmd.exe batch, PowerShell, or WSL bash.

**Options considered:**
- cmd.exe batch: available, but `$input` piping is awkward. Hard to handle stdin/stdout correctly.
- WSL bash: requires WSL installed. Not available in all corporate environments.
- PowerShell: universally available on Windows 10/11. Good stdin/stdout handling.

**Decision:** PowerShell (`.ps1`). The hook script contains:
```powershell
$input | & "C:\full\path\to\python.exe" -m quor hook claude
```

`quor init --claude` writes this script to a location that Claude Code can discover, configured in `~/.claude/settings.json`.

**Consequences:**
- The Python executable path is embedded as `sys.executable` at `quor init` time — not as `python` or `python3`. This is critical for venv support on Windows.
- PowerShell execution policy: `quor init` checks and warns if `Get-ExecutionPolicy` returns `Restricted`. It does NOT attempt to change the policy.
- The hook script must handle the cursor doubled-BOM edge case: strip `\xEF\xBB\xBF\xEF\xBB\xBF` before JSON parsing.

---

## ADR-018: Error Handling — Fail-Open at Every Level

**Status:** Decided  
**Date:** 2026-06-30

**Context:**  
Quor operates inside an AI coding session. If Quor fails, the AI session must continue working. A Quor error must never prevent the AI from seeing command output.

**Decision:** Every level of Quor is fail-open:
- **Hook level**: `__main__.py` has a top-level `try/except Exception`. Any exception → return original JSON unmodified. Log error to stderr.
- **Pipeline level**: Stage exceptions → skip that stage, log warning, continue pipeline.
- **Plugin level**: Plugin load failure → skip that plugin, log warning, continue.
- **DB level**: SQLite write failure → log warning, continue. Never delay hook response for DB.
- **Filter level**: Test failures (inline filter tests) → do not block the pipeline. Only fail when `quor verify` is run explicitly.

**Exception hierarchy:**
```python
class QuorError(Exception): pass
class FilterError(QuorError):
    def __init__(self, message: str, stage_name: str, content_preview: str = ""): ...
class ConfigError(QuorError): pass
class HookError(QuorError): pass
class CacheError(QuorError): pass
class PluginError(QuorError):
    def __init__(self, message: str, plugin_name: str): ...
def is_transient_error(exc: Exception) -> bool: ...
```

**Exit codes:**
```python
class ExitCode(IntEnum):
    SUCCESS = 0
    FILTER_TESTS_FAILED = 1
    CONFIG_ERROR = 2
    RUNTIME_ERROR = 3
    HOOK_ERROR = 4
    DEPENDENCY_MISSING = 5
```

**Consequences:**
- Never use `assert` for validation in production code — `assert` is stripped by `python -O`.
- All validation uses explicit `if/raise`.
- `is_transient_error()` distinguishes retriable errors (e.g., SQLite locked) from permanent ones (config syntax error).

---

## ADR-019: Filter Registry — Three-Tier Lookup

**Status:** Decided  
**Date:** 2026-06-30

**Context:**  
Filters come from three sources with different trust levels and override semantics.

**Decision:**
1. **Project-local** (highest priority): `.quor/filters.toml` in the git repository root. Trusted only if git-tracked. Overrides user and built-in filters.
2. **User-global**: `~/.config/quor/filters.toml` (platformdirs). Always trusted. Overrides built-in filters.
3. **Built-in** (lowest priority): bundled with the package in `quor/filters/builtin/`. Cannot be modified without reinstalling.

Lookup for command `git status`:
1. Check project-local registry. If filter found and file trusted → use it.
2. Check user-global registry. If filter found → use it.
3. Check built-in registry. If filter found → use it.
4. If no filter found → passthrough (return original). Log to tracking as `was_passthrough = 1`.

**Consequences:**
- Filter names must be unique within each tier. Duplicate filter names in the same file are a `ConfigError`.
- `quor explain <command>` shows which tier supplied the filter.
- `quor validate` validates all three tiers and reports which filters are active.

---

## ADR-020: `rich` — Core Dependency (not optional)

**Status:** Decided  
**Date:** 2026-06-30

**Context:**  
`rich` provides formatted terminal output for `quor explain`, `quor gain`, `quor doctor`, and onboarding. It was initially proposed as optional.

**Decision:** `rich` is a core dependency. The user-facing CLI quality depends on formatted output. Saving one dependency by making `rich` optional produces a significantly worse user experience. `rich` has Windows wheel distributions and zero compilation requirements.

**Consequences:**
- `rich` imports must not appear in the hook path (`__main__.py` hook mode). The hook returns JSON to stdout; `rich` output would corrupt it. `rich` is imported only in CLI commands.
- `rich.console.Console(stderr=True)` is used for all diagnostic output — never `print()` in CLI code.

---

## ADR-021: `abort_unless` / `abort_if` — Filter-Level Short-Circuit

**Status:** Decided  
**Date:** 2026-06-30

**Context:**  
Some filters should skip all compression if a specific signal is absent. For pytest: if there are no failures, the output might be "all passed" and aggressive stripping would produce an empty result, confusing the AI.

**Decision:** `abort_unless` and `abort_if` are filter-level (not stage-level) fields that run before the ContentMask pipeline:
- `abort_unless = ["FAILED", "ERROR"]`: if none of the patterns match any line, return original immediately.
- `abort_if = ["No such file"]`: if any pattern matches any line, return original immediately (danger signal).

These replace Zap's `match_output/unless` pair.

**Consequences:**
- `abort_unless` and `abort_if` are evaluated on the raw input, before the ContentMask pipeline runs.
- Short-circuit invocations are recorded in SQLite with `was_passthrough = 0` (they ran the filter, just not all stages) and a `stages_applied` value of `[]`.

---

## ADR-022: `on_empty` — Empty Output Handling

**Status:** Decided  
**Date:** 2026-06-30

**Context:**  
After aggressive compression, the filtered output can be empty. An empty string returned to the AI looks like command failure (exit code 0 but no output). The AI may then retry the command or make incorrect assumptions.

**Decision:** `on_empty` is a filter-level string field. If the ContentMask renders to an empty string, `on_empty` is returned instead.

**Example:**
```toml
on_empty = "All tests passed."
```

**Consequences:**
- `on_empty` is appended to the rendered output only if the rendered output is empty AND `on_empty` is defined.
- `on_empty` strings must not exceed 200 characters. Longer values raise `ConfigError`.
- `on_empty` trigger rate is tracked in SQLite and visible in `quor gain`. High trigger rate indicates over-aggressive compression.

---

## ADR-023: Tee Mechanism — Cache Original Before Compression

**Status:** Decided  
**Date:** 2026-06-30

**Context:**  
Aggressive compression can remove context the AI needs. If that context is gone, the developer cannot recover it without re-running the command. An audit trail is needed.

**Decision:** Before applying compression, write the original output to `~/.local/share/quor/tee/{hash}.txt`. Append `[full output: ~/.local/share/quor/tee/{hash}.txt]` to the end of the compressed output.

The hash is SHA256 of the original content. Tee files older than 7 days are cleaned up at session start (weekly cleanup, tracked in SQLite).

**Consequences:**
- The tee directory path uses `platformdirs.user_data_dir("quor")`.
- Tee files contain the raw subprocess output with no modification.
- The `[full output: path]` footer is not subject to `max_tokens` limits — it is appended after the pipeline completes.
- Tee can be disabled per-filter with `tee = false` in the filter config.

---

## ADR-024: Windows Path Encoding — UTF-8 Everywhere

**Status:** Decided  
**Date:** 2026-06-30

**Context:**  
Windows defaults to `cp1252` encoding for file I/O. Corporate Windows environments may have mixed encoding configurations. Python's `open()` uses the system locale default on Windows.

**Decision:** All file operations specify `encoding="utf-8"` explicitly. No file open without explicit encoding. JSONL files written with `\n` line endings, not platform-default.

**Consequences:**
- Pre-commit hook or linter rule: `open(` without `encoding=` is a CI failure.
- SQLite stores all text in UTF-8 (SQLite default).
- Filter TOML files are read with `open(path, "rb")` + `tomllib.load()` — `tomllib` handles encoding.

---

## ADR-025: Testing Isolation — Autouse Fixture (no global state)

**Status:** Decided  
**Date:** 2026-06-30

**Context:**  
Quor reads and writes to `~/.config/quor/`, `~/.local/share/quor/`, and SQLite databases. Tests that write to these locations pollute the developer's actual Quor installation.

**Decision:** An autouse pytest fixture creates fresh temp directories and an isolated SQLite database for every test. No test reads from or writes to the real Quor config or data directories. The fixture patches `platformdirs.user_config_dir` and `platformdirs.user_data_dir` to return temp directories.

**Consequences:**
- The fixture is in `conftest.py` at the repo root. It is `autouse=True` — no test can forget to use it.
- Integration tests that deliberately test the real config path are explicitly marked with `@pytest.mark.integration` and excluded from the default CI run.
- The default CI run (no flags) must complete in <30 seconds with all tests isolated.

---

## ADR-026: Plugin Architecture — Two-Tier Separation (Plugin Protocol vs StageHandler)

**Status:** Decided  
**Date:** 2026-07-01

**Context:**  
ADR-007 established `quor.compression_stage` entry-points for third-party `StageHandler` implementations — stateless, TOML-configurable, ContentMask-typed compression stages. During Phase 8 implementation it became clear that a second category of extension was needed: lifecycle-managed, Python-coded middleware for telemetry, policy enforcement, routing, enrichment, and observability. Attempting to fit these into `StageHandler` would have required adding lifecycle methods (`initialize`, `shutdown`) and a payload envelope to an interface that is deliberately minimal and TOML-configurable.

**Options considered:**

- **Extend `StageHandler`** with optional lifecycle methods: backwards-compatible but would conflate two different responsibilities. Every existing stage author would see lifecycle methods that are irrelevant to their use case. TOML-driven stage config and Python-driven plugin config share no meaningful overlap.
- **Single unified `Plugin` protocol** replacing `StageHandler`: would require migrating all five existing built-in compression stages to the new interface. High disruption, no benefit.
- **Two separate Protocols** (`StageHandler` and `Plugin`), each with its own registry: clean separation of concerns. ContentMask pipeline stays TOML-driven and stage-based. `PluginRegistry` handles lifecycle-managed middleware. Phase 9 wires entry-point discovery for both.

**Decision:** Two separate Protocol hierarchies, each with its own registry and execution model:

| | `StageHandler` | `Plugin` |
|---|---|---|
| Purpose | Content compression | Middleware |
| Configured via | TOML `[[filter.stages]]` | Python code |
| Entry-point group | `quor.compression_stage` | (same, different subgroup TBD in Phase 9) |
| Lifecycle | None | `initialize` / `execute` / `shutdown` |
| Fail-open | Stage skip + warn | Plugin disable or payload passthrough |
| Categories | N/A (order = TOML declaration) | PRE_FILTER → FILTER → POST_FILTER |

**Consequences:**
- `Plugin` Protocol lives in `quor/plugins/base.py`. `StageHandler` Protocol lives in `quor/pipeline/stages/base.py`. Neither imports the other.
- Phase 9 must wire entry-point discovery for both independently.
- Plugin authors who need line-level `ContentMask` access should implement `StageHandler`. Plugin authors who need lifecycle management, annotations, or cross-plugin communication should implement `Plugin`.
- The `ExecutionMode` enum (AUDIT/OPTIMIZE/SIMULATE) is available to `Plugin.execute()` via `PluginContext.mode`. `StageHandler` stages are mode-unaware in v1.
- `kw_only=True` on all four public `Plugin`-side dataclasses ensures new optional fields can be added without positional-order breaking changes after v1.

**Implementation Evolution:**  
During implementation, Quor adopted an interface-first approach for the plugin architecture. Rather than implementing plugin discovery immediately, the project first stabilized the public Plugin API, lifecycle, registry, and execution model. This separated Plugin Infrastructure from Plugin Discovery & Loading, allowing third-party plugins to target a stable public API before runtime discovery mechanisms were introduced. As a result, the original single "Plugin System" phase was split into two phases: Phase 8 (Plugin Infrastructure) and Phase 9 (Plugin Discovery & Loading). This was an implementation refinement, not a change in product vision or architecture.

**Known scope gap — entry-point discovery does not report tier:**  
`PluginRegistry`'s three-tier precedence (project > user > builtin) is a property of *manual* registration (`registry.register(plugin, tier=...)`) — the caller decides the tier. Phase 9's entry-point discovery (`discover_plugins()`) has no equivalent concept: `importlib.metadata.entry_points()` reports which installed *distribution* provides an entry point, not whether that distribution is "project-local," "user-installed," or "builtin" — there is no signal in Python packaging metadata that maps to Quor's tier concept. `discover_plugins()` therefore registers every entry-point-discovered plugin at a single tier per call (default `"user"`), and `get_load_report()` (consumed by `quor doctor`) does not include a tier field in `StageInfo`/`PluginInfo` — there is nothing meaningful to report. Representing per-plugin tier for entry-point-discovered plugins would require inventing a new mechanism (e.g., separate entry-point groups per tier, or a project-local plugin allowlist file) — that is new-feature/architecture work, deliberately out of scope for Phase 9. `quor doctor` does report plugin `version` (from `PluginMetadata.version`, already captured in `PluginInfo`) alongside `plugin_id`.

---

## ADR-027: Release Hardening — Dev Tooling Version Policy & CI Lint Scope

**Status:** Decided
**Date:** 2026-07-01

**Context:**
A Ruff SIM105 failure reached CI despite a prior commit claiming "Ruff + mypy clean." Investigation found the violation reproduced identically with the locally installed Ruff — not a version mismatch, just a check that was never actually run before that commit. The investigation surfaced three related, previously-undiscovered gaps: `ruff`/`mypy`/`pytest` are unpinned in `pyproject.toml` (silent drift is possible over time even though local and CI happened to match on the day of the incident); CI only ever linted `quor/`, never `tests/` (45 violations had silently accumulated there); and local development on this machine runs Python 3.14, one version ahead of CI's tested 3.11/3.12 matrix.

**Options considered (versioning):**
- Full lock file (`uv.lock` / `pip-compile`): maximum reproducibility, but CI's install step is plain `pip install -e ".[dev]"` — adopting a lock file would mean also changing CI to a lock-aware install command, a bigger footprint than this hardening pass justifies, and `uv` was not confirmed available in the current dev sandbox.
- Exact-pin every dev dependency: fully reproducible, but adds update-PR churn for tools (`pytest`, `pytest-cov`) that rarely cause silent breakage.
- Exact-pin only the tools that generate new, breaking-by-default checks on point releases (`ruff`, `mypy`); bounded compatible ranges for the rest (`pytest`, `pytest-cov`).

**Decision (versioning):** Exact-pin `ruff` and `mypy` (`ruff==0.15.20`, `mypy==2.1.0` — both the versions verified clean during this pass). Bound `pytest`/`pytest-cov` to `<10.0.0` / `<8.0.0` respectively rather than pinning exactly, since a pytest major bump (8→9 already happened silently under the old unbounded range) is far less likely to introduce a *false* CI failure than a new Ruff rule or a new mypy strictness default.

**Options considered (CI lint scope):**
- Leave `tests/` unlinted: matches historical behavior, but the exact silent-drift failure mode that caused this hardening pass would recur for test code specifically.
- Lint `tests/` with a separate, looser Ruff config: more setup, more to maintain, and no strong reason test code needs different rules than `quor/`.
- Lint `tests/` with the same `[tool.ruff]` config already in `pyproject.toml`: simplest, and 45 accumulated violations were fixed in this same pass so it starts clean.

**Decision (CI lint scope):** `ci.yml`'s lint step now runs `ruff check quor/ tests/`. No separate config.

**Options considered (Python version support):**
- Support only 3.11/3.12 for v0.1 (matches current CI matrix and `pyproject.toml` classifiers).
- Add 3.13 to CI: a stable, non-bleeding-edge release; reasonable middle ground.
- Add 3.14 to CI: matches this contributor's local machine, but `doctor.py` already carries a `_FakeStdout` workaround for a 3.14-specific `stdout.buffer` behavior change — evidence 3.13/3.14 have not been systematically vetted, only incidentally exercised.

**Decision (Python version support):** Officially scope v0.1 to Python 3.11/3.12 only. CI already reflects this — no workflow change needed. `pyproject.toml`'s `requires-python = ">=3.11"` was deliberately left unbounded above rather than capped to `<3.13`: capping it would make `pip install -e ".[dev]"` fail on this contributor's own 3.14 environment as a side effect of an unrelated hardening pass, which is a bigger, more disruptive change than this ADR is scoped to make unilaterally.

**Consequences:**
- Bumping `ruff`/`mypy` going forward is a deliberate, visible `pyproject.toml` diff — not a silent `pip install` side effect. Expect periodic small PRs to bump these pins as new versions are adopted.
- `tests/` is now part of the CI-enforced lint surface; new test code must pass `ruff check tests/` before merge.
- Follow-up (not done in this ADR): decide whether to (a) cap `requires-python` to `<3.13` and require contributors to develop on 3.11/3.12, or (b) add 3.13/3.14 to the CI matrix and verify the existing 3.14-specific workarounds are sufficient. Either is a bigger, separate decision than this hardening pass.
