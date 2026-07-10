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

**Implementation Update (QB-013):**
This ADR was `Decided` but unimplemented for some time (see ADR-031's original Consequences
section, and `backlog.md`'s QB-013, for the historical gap — no `tee.py` module existed and no
filter read a `tee` field). QB-013 has since implemented it: `quor/pipeline/tee.py`, dispatcher-level
only (`quor/adapters/dispatcher.py` calls it; no `ContentMask`/`Pipeline`/`StageHandler` change).
SHA256 content-addressed storage under `platformdirs.user_data_dir("quor") / "tee"`, with dedup and
mtime refresh on a cache hit. 7-day TTL cleanup, throttled via a separate `tee_state.db` (WAL mode).
Global kill-switch (`tee_enabled` / `QUOR_TEE_ENABLED`) and per-filter (`FilterConfig.tee`) opt-out,
both backward-compatible defaults (tee on unless explicitly disabled). An adaptive fallback disables
tee automatically after repeated `OSError` write failures (e.g. a locked-down corporate filesystem)
rather than retrying forever; reset via `quor doctor --reset-tee`. `docs/final/PROJECT_BIBLE.md`'s
"nothing is irrecoverably lost" claim is now accurate current behavior, not aspirational design.

**Reporting Update (QB-017 gain hardening):**
The `[full output: path]` footer is appended *after* `original_tokens`/`final_tokens` are computed
for tracking, which means its cost is counted as part of `final_tokens` — for an already-small,
already-clean command, the footer can cost more tokens than compression saved, producing a
negative `tokens_saved` for that invocation. This is expected, not a bug (see QB-017 in
`backlog.md`). `quor gain` now decomposes its net figure into `gross_savings` (sum of genuinely
compressed invocations) and `gross_overhead` (sum of invocations whose output grew) — a
presentation-only split of the existing `original_tokens`/`final_tokens` columns computed at query
time in `quor/tracking/db.py::query_gain()`, with no new tracking column and no change to what
`_track()` writes per invocation. Investigated during QB-017 and confirmed by a regression test
(`tests/unit/test_filters.py::TestFilterNeverExpandsOutput`): no built-in filter stage can itself
expand content, so a negative-net row is attributable to this footer (or, in principle, a
third-party `PRE_FILTER`/`POST_FILTER` plugin adding content) — not a hidden accounting bug in the
tracking formula.

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

**Update (2026-07-02 — Final Pre-Release Cleanup):** The Python version follow-up above is now resolved by real execution rather than static review. Created actual Python 3.11 and 3.13 virtual environments (via `uv venv --python <version>`) alongside the existing 3.14 development environment, and ran `ruff check quor/ tests/`, `mypy quor/`, and the full pytest suite in each — all three identical: clean lint/types, 605/605 tests passing. No 3.13/3.14-specific incompatibility was found beyond the `_FakeStdout` workaround already in place. Decision: `requires-python` stays unbounded above (no incompatibility to justify capping it); CI's matrix stays at 3.11/3.12 (expanding CI is a separate cost/benefit decision from verifying local compatibility, and every commit is already covered on those two versions). Python 3.12 itself was not independently re-verified in this pass — it sits directly between two verified points and is already exercised by every CI run.

---

## ADR-028: Release Packaging — Dev Fixture Excluded From Published Metadata

**Status:** Decided
**Date:** 2026-07-02

**Context:**
A release-readiness review found that `pyproject.toml`'s `dev` extra declared `quor-test-stage @ file:./tests/fixtures/test_plugin` — a relative `file://` URL pointing at the in-repo plugin-discovery test fixture. `pip`/`uv` bake this verbatim into the built wheel's `METADATA` (confirmed by inspecting the actual built wheel). That path only exists inside a source checkout of this repository; anyone who runs `pip install quor[dev]` against a copy installed from PyPI gets an unresolvable path and, with some pip versions, an outright `InvalidRequirement: Invalid URL given` parse error (reproduced with pip 24.0 during Python-version verification in the prior hardening pass; pip 26.1.2 resolves it but still can't find the path once installed from PyPI).

**Options considered:**
- Leave it as-is: the fixture keeps working for source-checkout contributors, but every published release ships a `dev` extra that fails for anyone installing it from PyPI — a real, reproducible break for a normal-looking command.
- Publish `quor-test-stage` to PyPI as its own tiny package and depend on it normally: works, but creates a second package to version, maintain, and keep in sync for a fixture that exists purely to test entry-point discovery — disproportionate maintenance cost for what it's for.
- Remove the fixture from `pyproject.toml`'s `dev` extra entirely; install it as a separate, explicit step in CI and in contributor setup docs.

**Decision:** Remove `quor-test-stage` from `[project.optional-dependencies].dev`. It is installed via a separate `pip install -e ./tests/fixtures/test_plugin` step in `.github/workflows/ci.yml`, `.github/workflows/canary.yml`, and documented as a required second step in `CONTRIBUTING.md` and the README's Development Setup. Production behavior is unchanged — this only affects what a contributor or CI runner does to get the *test suite* fully working; end users installing plain `quor` were never affected either way.

**Consequences:**
- The published wheel's `dev` extra now only lists real PyPI packages (`pytest`, `pytest-cov`, `mypy`, `ruff`) — `pip install quor[dev]` from PyPI will resolve cleanly (though a PyPI user still won't have the `tests/` directory to point the fixture at; that extra was always intended for contributors working from a source checkout, not end users).
- Contributors must remember the second install step — documented in three places (CI workflows, `CONTRIBUTING.md`, README) specifically to reduce the chance of it being missed or drifting out of sync.
- If a genuinely reusable, general-purpose plugin-testing fixture is ever needed by third parties (not just this repo's own test suite), publishing it as a real PyPI package should be reconsidered — this decision is scoped to "make the current release clean," not a permanent rejection of that option.

---

## ADR-029: Rewritten Commands Invoke `sys.executable -m quor` (not the `quor`/`qr` launcher)

**Status:** Decided
**Date:** 2026-07-04

**Context:**
ADR-011 decided the rewritten command Quor hands back to Claude Code is `quor <original>` — the bare word `quor`, resolved via PATH by whatever shell Claude Code's Bash tool runs. On a corporate Windows laptop this bare word resolves to the pip-generated `quor.exe` console-script launcher stub (declared in `[project.scripts]`), which some application-control policies block outright, even though the exact same Python interpreter running `python -m quor` is allowed. Investigation traced the full path from the PreToolUse hook (`quor/adapters/claude.py::run_hook`) through `rewrite_command` (`quor/rewrite/classifier.py`) and confirmed the rewritten string is not merely metadata: Claude Code executes it verbatim, so the literal prefix chosen here fully determines which executable actually runs. `python -m quor doctor` working while `quor doctor` is blocked on the same machine was the direct evidence.

**Options considered:**
- `py -m quor ...` (Windows Python Launcher): rejected — doesn't exist on Linux/macOS at all, and even on Windows it isn't guaranteed to resolve to the interpreter Quor is currently running under (venv/pipx/poetry/uv/conda environments aren't reliably reachable through `py`), which could invoke a Python without Quor installed.
- Bare `quor`/`qr` (status quo): rejected — this is the bug; it depends on the PATH-resolved launcher stub, which is exactly what gets blocked.
- `sys.executable -m quor ...`: the interpreter already running Quor, by definition has Quor importable, and is unambiguous across every packaging/environment tool. Chosen.

**Decision:** Rewritten commands are prefixed with `shlex.quote(sys.executable) + " -m quor"`, produced by a single helper, `get_quor_invocation()` in the new `quor/rewrite/invocation.py`. `quor/rewrite/classifier.py::_classify_simple` is the only call site that constructs this prefix (compound/piped/env-prefixed/transparent-prefix rewrites all recurse through it, so there is no second place to keep in sync). `shlex.quote` produces POSIX-safe quoting, matching the Git-Bash-style shell Claude Code's Bash tool actually parses on every OS, so interpreter paths containing spaces (common on Windows) are handled correctly.

**Consequences:**
- The `quor`/`qr` console-script entry points in `pyproject.toml` are unchanged and still installed by `pip install quor` — they are now purely a convenience for commands a user types by hand (`quor doctor`, `quor init --claude`), not a runtime dependency of the PreToolUse rewrite path.
- `quor/cli/commands/doctor.py::_check_hook_roundtrip` and every rewrite-format test (`tests/unit/test_rewrite.py`, `tests/unit/test_adapters.py`, `tests/fixtures/commands/*.toml` via the loader) now compare against `get_quor_invocation()` instead of a hardcoded `"quor "` literal, so they remain valid on any machine/interpreter.
- If Quor is ever distributed as a frozen binary (PyInstaller/Nuitka), `sys.executable` would be that binary and `-m quor` would no longer apply — not the case for any currently published build; noted as a limitation in `get_quor_invocation()`'s docstring for future maintainers.
- Manual invocation (a user typing `quor doctor` directly) is unaffected by this ADR and still goes through the launcher stub — the corporate-launcher troubleshooting entry in `README.md` is retained for that case, with a clarification that automatic Claude-Code-driven commands are no longer subject to it.

---

## ADR-030: PreToolUse Hook Response — `hookSpecificOutput.updatedInput` (not a bare `tool_input` echo)

**Status:** Decided
**Date:** 2026-07-04

**Context:**
Since Phase 5, `quor/adapters/claude.py::run_hook` rewrote `data["tool_input"]["command"]` in place on the parsed input dict and wrote the *entire* input payload back to stdout unchanged in shape — i.e. `{"tool_name": ..., "tool_input": {"command": "<rewritten>"}}`. This looked correct in isolation: every unit test in `tests/unit/test_adapters.py` called `run_hook()` directly and asserted against `result["tool_input"]["command"]`, so the suite was internally consistent and green. But no test ever drove the output through the actual Claude Code binary. Investigation (prompted by a user report that rewritten commands never executed, while a sibling tool, Zap, rewrote commands successfully using the same hook mechanism) confirmed via the official docs (`https://code.claude.com/docs/en/hooks.md`) that Claude Code's PreToolUse consumer only reads `hookSpecificOutput.updatedInput` to override tool arguments. A top-level `tool_input` key mirroring the input shape is not part of the protocol and is silently dropped — Claude Code always executed the *original*, unmodified command. This is the same class of failure as ADR-029 (a self-consistent unit-test suite validating the wrong external contract), but here the output was never correct, not merely superseded.

**Options considered:**
- Keep echoing the mutated full payload, add an e2e test against a real `claude` binary to catch drift: doesn't fix the actual bug — the shape is wrong regardless of test coverage.
- Emit `hookSpecificOutput.updatedInput` always, including when no rewrite applies (echoing the unchanged command): rejected — makes "no rewrite" and "rewrite to the same string" indistinguishable from "rewrite happened", and adds a field for no operational benefit.
- Emit `hookSpecificOutput` with `permissionDecision: "allow"` on every call, but only include `updatedInput` when a rewrite actually changed the command: chosen. Keeps stdout always non-empty/valid JSON (required by the "hook must always return valid output" invariant) while being unambiguous about whether a rewrite occurred.

**Decision:** `run_hook()` builds `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow", "updatedInput": {...}}}`. `updatedInput` is the original `tool_input` object with only `command` replaced (sibling fields such as `description` are preserved, since `updatedInput` replaces the whole `tool_input` object, not just one field) and is omitted entirely when `rewrite_command()` returns `None` or the same string — Claude Code then runs the original command unmodified. `quor/adapters/base.py::HookOutput` is updated to model this shape (`HookOutput.hookSpecificOutput: HookSpecificOutput`), replacing the old "same shape as `HookInput`" model that was never actually correct.

**Consequences:**
- `quor/cli/commands/doctor.py::_check_hook_roundtrip` now reads `result["hookSpecificOutput"]["updatedInput"]["command"]` instead of `result["tool_input"]["command"]`.
- `tests/unit/test_adapters.py` assertions were rewritten against the new shape; a regression test (`test_does_not_regress_to_bare_tool_input_echo`) asserts the response's only top-level key is `hookSpecificOutput`, so a future accidental revert back to echoing `tool_input`/`tool_name` fails loudly in-process instead of only failing silently against the real Claude Code binary.
- No backward-compatibility path is needed: the old shape never worked end-to-end against real Claude Code, so there is no working behavior to preserve.
- This does not add an actual end-to-end test against the real `claude` CLI/binary — that remains a gap (the same gap that let this bug ship in the first place). `quor doctor`'s `_check_hook_roundtrip` check is still an in-process call to `run_hook()`, not a subprocess invocation of the installed PowerShell hook script through Claude Code itself.
- This ADR's original text (as first written) did not update `.github/workflows/canary.yml` — the weekly canary's "Verify hook responds to current Claude Code PreToolUse format" and "Verify hook preserves extra JSON fields" steps still asserted against the pre-fix `result["tool_input"]["command"]` / top-level `session_id` echo. A subsequent release audit (2026-07-04, same day) caught this by reproducing the canary's exact check logic locally and confirming it failed against the fixed adapter — i.e. the canary would have reported a false "Claude Code changed its hook format" failure on its next scheduled run, for a reason that was actually Quor's own stale assertion. Both canary steps were corrected to assert against `hookSpecificOutput.updatedInput` and to explicitly assert the top-level `tool_input`/`tool_name` echo is absent (a direct regression guard for this ADR), and the second step was repurposed to check sibling-field preservation *inside* `updatedInput` (e.g. `description`) rather than top-level fields, since top-level fields were never part of the real protocol.

---

## ADR-031: Token Budget Semantics — `max_tokens` is Best-Effort, `PROTECT` is Absolute

**Status:** Decided
**Date:** 2026-07-04

**Context:**
QB-004 investigated why `git-diff`'s `max_tokens` stage (`limit = 600`) rendered ~5,806 estimated tokens for a real `git show` — roughly 9.7x over budget. Root-cause tracing found this was not a stage-execution bug: `strip_lines`'s `preserve_patterns` (`^\+`, `^-`, `^@@`, `conflict`, `Error`) marked 298 of 515 lines `PROTECT`, summing to ~5,265 tokens alone — already over the 600 limit before `max_tokens` ran. `max_tokens` is implemented to never compress `PROTECT` lines, per ADR-003's invariant that `PROTECT` decisions are absolute. This pattern is not isolated to `git-diff`: 6 of Quor's 8 built-in filters (`git-log`, `git-diff`, `pytest`, `mypy`, `ruff`, `cat`) combine `preserve_patterns` with a `max_tokens` stage, so any of them can exceed its configured limit whenever matched content is voluminous — precisely when that content (failing tests, real lint violations, diff hunks) matters most. QB-012 was opened to decide, once and for all, what `max_tokens` is supposed to guarantee when this happens.

**Options considered:**
- **Best-effort budget:** `max_tokens` is a target; `PROTECT` lines are never compressed to meet it, even if the limit is exceeded as a result.
- **Hard budget:** `max_tokens` is absolute; `PROTECT` lines may be compressed if required to stay under the limit.
- **Priority-based budgeting:** replace the binary `PROTECT` with multiple protection levels, so lower-priority protected content can be compressed before higher-priority content.

**Decision:** Best-effort budget. `max_tokens.limit` is a target the stage tries to hit by compressing `KEEP` lines; `PROTECT` lines are never compressed to meet it, and rendered output may exceed `limit` when protected content alone is large. This formalizes the behavior the pipeline already had — it is consistent with ADR-003 (`PROTECT` decisions are absolute, enforced pipeline-wide by `Pipeline.execute`'s `_enforce_protect`) and with the existing, ratified principle in `PROJECT_BIBLE.md` that "meaning preservation is non-negotiable." The "hard budget" option was rejected because it would let `max_tokens` silently discard exactly the content (failing assertions, real errors, diff hunks) that filter authors deliberately marked as unconditionally important, and specifically in the scenario — large volumes of that content — where losing it is most likely to cause the AI to make a wrong decision. It would also require carving a stage-specific exception into `_enforce_protect`'s pipeline-wide "no stage may downgrade PROTECT" invariant, and would silently break the guarantee several existing inline filter tests already assert (e.g. pytest.toml's "FAILED lines preserved"). "Priority-based budgeting" was rejected for QB-012 as disproportionate: it requires a breaking change to the `Decision` enum, `_enforce_protect`, and all four stage modules that check `Decision.PROTECT` (`strip_lines`, `deduplicate_consecutive`, `group_repeated`, `max_tokens`), and breaks the "stable after V1.0" plugin contract in `quor/pipeline/stages/base.py` — a large engineering and compatibility cost with no evidence yet that it would meaningfully outperform best-effort in practice.

**Consequences:**
- No runtime behavior changes — this ADR formalizes shipped behavior, it does not alter it.
- `quor/pipeline/stages/max_tokens.py`'s module docstring and `MaxTokensConfig.limit` field description now state explicitly that the limit is a best-effort target, not a guarantee.
- `README.md`'s `max_tokens` description now states that `PROTECT` lines take precedence and the budget can be exceeded.
- The existing tee mechanism (ADR-023) remains the correct complementary safety net for cases where best-effort compression still leaves large output — but ADR-023 is `Decided` and not yet implemented (no `tee.py` module, no `tee` field read by any built-in filter). Tracked as QB-013. **Update:** QB-013 has since been implemented (see ADR-023's "Implementation Update"); this line is preserved as originally written for historical accuracy of what was true when this ADR was decided.
- A related but separate finding from the QB-012 investigation: `build.toml`'s `mypy` filter runs `group_repeated` after `strip_lines` has already marked every `error:`/`warning:`/`note:` line `PROTECT` via `preserve_patterns` — since `group_repeated` treats `PROTECT` as a run-breaker, it is currently a no-op for `mypy`. This is a stage-ordering question, not a budget-semantics question, and is out of scope for this ADR. Tracked as QB-014.

**Implementation Update (QB-014):**
This ADR's Consequences section above describes the state of the `mypy` filter as observed at the time this ADR was written. It is preserved as-is for historical accuracy. QB-014 has since been implemented:
- The `mypy` pipeline now executes `group_repeated` → `strip_lines` → `max_tokens` (reordered from the sequence described above).
- `strip_lines` now skips its `preserve_patterns` check for lines already marked `COMPRESS`, so the reorder doesn't resurrect duplicates `group_repeated` already collapsed.
- The fix was validated with a new regression test and full project verification (`quor verify`, full `pytest` suite, dependency review across all built-in filters, byte-for-byte before/after comparison). See `backlog.md`'s `QB-014` entry for full details.

---

## ADR-032: Benchmark Coverage — Every Built-in Filter Requires a Manifest Case (QB-011 follow-up)

**Status:** Decided
**Date:** 2026-07-05

**Context:**
QB-011 shipped the compression benchmark suite (`tests/benchmarks/`) covering only 6 of the built-in
filter categories that existed at the time — `git-status`, `git-log`, `git-diff`, `pytest`, `mypy`,
`generic` — explicitly naming `eslint`/`npm`/`npx`/`pnpm`/`yarn` as a known follow-up gap in its own
README's "Future benchmark expansion" section. `ruff` (shipped alongside `mypy` in `build.toml`) and
`cat`/`cat-python` (QB-005) were never covered either, since none of the three were part of the
original 6-category corpus. Batch 7's documentation audit (QB-003, `docs/final/COMMAND_SUPPORT.md`)
surfaced the `eslint`/`npm`/`npx`/`pnpm`/`yarn`/`cat`/`cat-python` gap concretely while writing the
canonical command/filter reference; auditing the resulting coverage claim against the manifest then
surfaced `ruff` as an eighth, previously-unnoticed gap of the same kind. It matters because the
benchmark suite is the only mechanism that catches a *quiet* compression regression in a shared stage
(e.g. `strip_lines`, `group_repeated`) over time — inline `[[filter.tests]]` catch correctness
violations on that filter's own crafted fixtures, but carry no baseline to regress against. A filter
with no benchmark case has no regression protection beyond whatever its own inline tests happen to
assert.

**Options considered:**
- Leave the gap as a "nice to have": simplest, but leaves 8 of 14 built-in filter blocks with zero
  compression-regression tracking indefinitely, and a documentation audit that finds a gap without
  closing it invites the same gap resurfacing at the next audit.
- Require benchmark coverage only for *future* new filters: partially closes the process gap but
  leaves the already-shipped `ruff`/`eslint`/`npm`/`npx`/`pnpm`/`yarn`/`cat`/`cat-python` filters
  permanently uncovered unless someone separately revisits them later.
- Close the existing gap immediately (benchmark cases for every currently-implemented filter) and
  formalize the requirement going forward: fully closes the gap now and prevents recurrence.

**Decision:** Close the gap immediately, and make benchmark coverage mandatory for every filter from
this point forward, not merely a recommendation. Added 16 new `[[case]]` entries (2 per filter:
`ruff`, `eslint`, `npm`, `npx`, `pnpm`, `yarn`, `cat`, `cat-python`) to `tests/benchmarks/manifest.toml`,
with realistic sample files under `tests/benchmarks/samples/<category>/`. Verified via
`python -m tests.benchmarks.run_benchmarks --no-compare` — all 28 cases (12 original + 16 new) pass
correctness and `min_reduction_pct` floor checks — then committed to `tests/benchmarks/baseline.json`
via `--update-baseline`. Every currently-implemented built-in filter now has measurable,
regression-tracked benchmark coverage. `docs/final/COMMAND_SUPPORT.md` §7, `CONTRIBUTING.md`'s Filter
checklist, and `docs/final/CLAUDE.md`'s Git Workflow section all state this as a hard requirement — a
filter PR without a benchmark case is incomplete, the same way a filter PR without inline tests
already was (`docs/final/ANTI_GOALS.md` #23).

**Consequences:**
- `tests/benchmarks/manifest.toml` now has 28 cases across 14 categories (was 12 across 6).
  `tests/benchmarks/baseline.json` was regenerated to include all of them.
- `tests/benchmarks/README.md`'s "Future benchmark expansion" note, which named part of this exact
  gap, is now stale and was updated to reflect the closed state.
- No production code changed — `tests/benchmarks/` remains isolated from `quor/` by construction (per
  QB-011's original design), calling only `FilterRegistry`, `count_tokens`, and `content_hash`.
- A filter added after this ADR without benchmark coverage should be treated as an incomplete PR at
  review time (see `docs/final/CLAUDE.md`'s Review Checklist).

---

## ADR-033: Subprocess Execution — Resolve via `shutil.which()`, Never `shell=True`

**Status:** Decided
**Date:** 2026-07-05

**Context:**
A production-readiness validation of the tracking/gain pipeline ran real commands end-to-end
through `quor/adapters/dispatcher.py::run_dispatch()` rather than through mocked
`subprocess.run` calls (every existing dispatcher test mocks `subprocess.run`, which is exactly
why this went undetected). `npm`, `npx`, `pnpm`, and `yarn` — known base commands since QB-006A,
specifically so their wrapper noise gets filtered — failed unconditionally on Windows with
`FileNotFoundError: [WinError 2] The system cannot find the file specified`, because these tools
ship as `.CMD` shell shims, not native `.exe` binaries. Windows' `CreateProcess` (what
`subprocess.run(args)` calls without `shell=True`) does not apply `PATHEXT`-based extension
resolution the way `cmd.exe` or `quor explain`'s `subprocess.run(command, shell=True)` does.
The result in the real dispatch path: the classifier correctly recognized and rewrote the
command, the filter registry correctly had an `npm`/`npx`/`pnpm`/`yarn` entry, but the actual
subprocess spawn failed before any of that mattered — the command simply never ran, printing
`[quor] cannot run 'npm': ...` to stderr and returning exit code 127, on the exact platform
(Windows) this project is built for.

**Options considered:**
- **`shell=True` with a joined string:** works, but re-joining an argv list into a single string
  and re-parsing it through `cmd.exe` reopens shell-metacharacter injection risk (`&`, `|`, `^`,
  `%VAR%` expansion) for command content that already passed through the classifier as a safe,
  pre-split argv list. Manually re-escaping for `cmd.exe` specifically is exactly the kind of
  hand-rolled quoting logic ADR-015 already rejected for regex (unbounded edge cases).
- **`shell=True` with the args list:** Python's `list2cmdline` quotes each argument before handing
  the joined string to `cmd.exe`, so this is safer than the string form — but still routes every
  invocation through a shell, an unnecessary and permanently larger security surface for the 95%+
  of commands (`git`, `pytest`, `mypy`, `ruff`, `cat`) that are native executables needing no shell
  at all.
- **`shutil.which(args[0])` resolution, keep `shell=False`:** resolves the shim's real path
  (`...\npm.CMD`) using Python's own stdlib `PATHEXT`-aware search — the same mechanism a real
  shell uses — then hands that fully-resolved path straight to `CreateProcess` with no shell
  involved at all. No new metacharacter-interpretation surface is introduced for any command,
  known or not.

**Decision:** `shutil.which(args[0]) or args[0]` before the `subprocess.run(...)` call in
`run_dispatch()`, falling back to the original token unchanged if not found so the existing
`FileNotFoundError`/`OSError` handling still catches a genuinely missing command exactly as
before. `shell=False` is preserved. This is the minimal change that fixes the root cause without
adding a shell to the execution path.

**Consequences:**
- `git`, `pytest`, `mypy`, `ruff`, `cat`, `python` are unaffected — `shutil.which()` resolves their
  real `.exe`/script path exactly as `CreateProcess` would have found it anyway; behavior for
  every previously-working command is unchanged.
- `npm`, `npx`, `pnpm`, `yarn` (and any future shell-shim-based tool added as a known base command)
  now actually execute on Windows through the real dispatch path, not just in `quor explain` (which
  happened to use `shell=True` already) or in the benchmark suite (which never spawns a real
  subprocess at all — it calls `FilterRegistry.apply()` directly on pre-captured sample files).
- A new regression test (`tests/unit/test_adapters.py::TestDispatcher::test_windows_shell_shim_executable_resolves_and_runs`)
  spawns a real throwaway `.cmd` shim rather than mocking `subprocess.run`, specifically because
  mocking is what let the original bug ship undetected. Skipped on non-Windows platforms, since
  `.cmd`/`.bat` shim resolution is a Windows-specific concern.
- See `backlog.md`'s `QB-019` for the full investigation record.

---

## ADR-034: `PostToolUse`/`Read` Hook — a Separate Adapter, `updatedToolOutput` Omitted Until Compression Exists (QB-007A)

**Status:** Decided
**Date:** 2026-07-10

**Context:**
QB-007's feasibility investigation (2026-07-09, recorded in `backlog.md`) confirmed that
document compression requires a fundamentally different integration shape than the existing
`PreToolUse`/`Bash` hook: Claude Code performs the `Read` itself, so there is no subprocess for
Quor to wrap, and the only point where Quor can intercept is `PostToolUse`, using
`hookSpecificOutput.updatedToolOutput` — the `PostToolUse` sibling of `updatedInput` (ADR-030) —
to substitute compressed content for the real `tool_response` before Claude sees it. A full
design pass (2026-07-10) worked out the complete architecture (content routing, filter reuse,
per-format extraction, dependency choices, failure modes) and deliberately split it into small,
independently mergeable sub-items so each carries its own review/test/merge cycle rather than
landing as one large, high-risk change. This ADR records the decisions made for the first of
those — QB-007A, hook-registration plumbing only, no compression logic.

**Options considered (adapter placement):**
- **Branch inside `quor/adapters/claude.py`** on a `hook_event_name` field, reusing the existing
  Bash adapter module for both `PreToolUse` and `PostToolUse`: rejected — it would add untested
  new code paths inside the one module every existing Bash-hook test and every merged PR since
  Phase 5 already depends on, for a payload shape and failure mode that share nothing structural
  with the Bash path (no subprocess, no command rewrite, no `tool_input.command`).
- **A separate adapter module, `quor/adapters/claude_read.py`**, with its own hook script and its
  own `settings.json` registration under `hooks.PostToolUse`/matcher `"Read"`: chosen. Zero
  regression surface on the Bash path; the two hook registrations are additive and independent
  (`_install_hook_entry()` and the new `_install_read_hook_entry()` each only touch their own key
  under `hooks`).

**Options considered (QB-007A scope):**
- **Ship hook plumbing and a first compression filter together:** rejected — conflates two
  genuinely separate risks (does the mechanism work at all vs. does the compression logic behave
  correctly) into one PR, and repeats the exact mistake ADR-030 documents: a bug in the
  plumbing/response-shape layer is easy to miss when it's reviewed alongside unrelated filter
  logic.
- **Ship hook plumbing alone, always omitting `updatedToolOutput`:** chosen. This phase is
  deliberately a no-op — `quor/adapters/claude_read.py::run_hook()` parses and validates the
  `PostToolUseHookInput` payload (catching malformed input via the same fail-open path
  `__main__._run_hook()` already provides for the Bash adapter) but never reads or transforms
  `tool_response`, and never sets `updatedToolOutput`. This isolates and de-risks the two
  load-bearing unknowns flagged by the design pass — the minimum Claude Code version that honors
  `updatedToolOutput` for `Read`, and the real `PostToolUse` hook timeout budget — before any
  extraction-library or filter-authoring work is committed to.

**Decision:**
`quor/__main__.py::_run_hook()` now dispatches on the hook adapter name passed as `sys.argv[2]`:
`"claude"` → the existing Bash adapter, `"claude-read"` → the new
`quor.adapters.claude_read.run_hook()`. `quor init --claude` writes a second PowerShell hook
script (`claude-hook-read.ps1`, invoking `quor hook claude-read`) and registers it under
`hooks.PostToolUse` with `matcher: "Read"`, additively alongside the existing
`hooks.PreToolUse`/`Bash` entry — installing or reinstalling one never disturbs the other.
`quor doctor` gains two checks: `Read hook script installed` (file existence, mirroring
`_check_hook_script`) and `Read hook responds correctly` (an in-process roundtrip, mirroring
`_check_hook_roundtrip`, that additionally asserts `updatedToolOutput` is never present — the
QB-007A no-op contract). New Pydantic models (`ReadToolInput`, `PostToolUseHookInput`,
`PostToolUseHookSpecificOutput`, `PostToolUseHookOutput`) in `quor/adapters/base.py` mirror the
existing `ToolInput`/`HookInput`/`HookSpecificOutput`/`HookOutput` models, including
`PostToolUseHookOutput.hookSpecificOutput.updatedToolOutput` — modeled now so QB-007B+ can set it
without a schema change, even though QB-007A never does.

**Consequences:**
- No changes to `quor/adapters/claude.py`, `quor/adapters/dispatcher.py`, `quor/pipeline/`, or
  `quor/filters/` — this ADR is additive-only with respect to the existing Bash path.
- `quor doctor`'s new capability check can only prove Quor's own response shape is well-formed —
  it cannot prove the installed Claude Code binary actually honors `updatedToolOutput` for `Read`.
  Learning directly from ADR-030's own history (an in-process-only test suite let a
  response-shape bug ship once already), a real end-to-end verification against an actual
  installed Claude Code binary remains a manual, non-automated gate for this phase and is not
  claimed as covered by the automated test suite.
- The minimum Claude Code version requirement and the real `PostToolUse` hook timeout budget
  remain open questions, unresolved by this ADR — QB-007D/E (DOCX/PDF extraction) should not be
  scoped in detail until they are.
- See `backlog.md`'s `QB-007` entry for the full sub-item breakdown (QB-007A–E) and the design
  pass this ADR formalizes.

## ADR-035: Pipeline Early Exit — Conservative, Hand-Audited `stage_type` Allowlist (QB-036)

**Status:** Decided
**Date:** 2026-07-10

**Context:**
QB-036 asked for an optimization layer inside `Pipeline.execute()` that skips remaining stages
once further processing cannot change `ContentMask.render()`'s output — with the hard constraint
that observable output must remain byte-for-byte identical for every existing test and the entire
benchmark corpus. Reading every built-in stage's `apply()` in full (required before writing any
code) surfaced a fact not previously written down anywhere: `Decision.COMPRESS` is *not*
engine-enforced immutable the way `PROTECT` is — `_enforce_protect` only restores `PROTECT`. Three
built-in stages (`group_repeated`, `max_tokens`, `remove_ansi`) apply their own `preserve_patterns`
pass with a condition of `decision is not PROTECT` rather than excluding `COMPRESS` too, so *if*
one of them is configured with `preserve_patterns` that happens to match an already-`COMPRESS`
line, that line is promoted back to `PROTECT` and reappears in `render()`. Separately,
`match_output` collapses the entire rendered text based on whether it matches a regex, independent
of any per-line `Decision` at all. Neither is a bug introduced by this task, and neither is fixed
by it (out of scope — "avoid changing stage implementations unless absolutely necessary"); both
had to be designed *around* to keep the optimization provably safe.

**Options considered:**
- **A blanket rule** ("once every line has decision != KEEP, skip everything remaining"),
  applied uniformly regardless of stage type: rejected — provably unsafe given the
  `group_repeated`/`max_tokens`/`remove_ansi`/`match_output` behavior above. No built-in filter
  shipped today actually configures `preserve_patterns` on anything but `strip_lines` (verified
  across every `quor/filters/builtin/*.toml`), so this would happen to work today, but the engine
  cannot assume that stays true for a project/user filter it has never seen.
- **A new `StageHandler` Protocol field** (e.g. `inert_on_decided_lines: ClassVar[bool]`) that
  every stage class declares: rejected — requires editing every one of the nine built-in
  `StageHandler` classes, directly contradicting this task's explicit "avoid changing every
  StageHandler unless absolutely necessary" scope constraint, for information the engine can
  already determine by reading the stages once, by hand, itself.
- **A hand-audited, conservative allowlist of `stage_type` strings inside `quor/pipeline/
  engine.py`, gated additionally by each stage instance's own (already-existing)
  `StageConfig.preserve_patterns` field being empty:** chosen. Zero stage files changed. The
  allowlist excludes `match_output` unconditionally (its behavior can never be predicted from
  `Decision` state alone) and treats a non-empty `preserve_patterns` on *any* remaining stage as
  disqualifying, regardless of whether that specific stage type's own bug is exploitable by that
  pattern — correct by construction rather than by trusting today's specific quirk inventory.
  Third-party/plugin/`file://` stages are never eligible (their `stage_type` is never in the
  allowlist) — the engine cannot vouch for code it has never read.

**Decision:**
`Pipeline.execute()` gains an `early_exit: bool = True` keyword-only parameter. After each stage
(and before the first), if the current mask has zero `Decision.KEEP` lines remaining and every
not-yet-run stage is both a known-safe `stage_type` and configured with an empty
`preserve_patterns`, every remaining stage is marked `was_skipped=True` (skip_reason describing
"early exit") without `can_handle()`/`apply()` ever being invoked — `len(stage_results)` still
equals the configured stage count, exactly as it already does for a `can_handle()`-False or
raising stage. The skip-eligibility check itself is wrapped in a `try`/`except`; any exception
there falls back to running the stage normally (a warning is logged), so a bug in the optimization
can degrade performance but never correctness. `FilterRegistry.apply()` (the real compression path
— Bash/Read hooks, benchmarks, `quor verify`) keeps the default (on); `FilterRegistry.trace()`
(`quor explain`'s diagnostic stage-by-stage view) explicitly passes `early_exit=False`, since that
command's entire purpose is showing what every configured stage does — an early-exited stage would
show "skipped — early exit" instead of its real per-stage line count, which is exactly the
information `quor explain` exists to surface. No new abstraction was introduced beyond this one
boolean parameter: the allowlist reuses `StageHandler.stage_type` (already required) and
`StageConfig.preserve_patterns` (already a base-class field every stage config inherits).

**Consequences:**
- Verified byte-for-byte identical `render()` output with `early_exit` on vs. forced off across
  every one of the 60 cases in `tests/benchmarks/manifest.toml`, plus every built-in filter's own
  inline `[[filter.tests]]` input (`tests/unit/test_early_exit.py`,
  `tests/benchmarks/early_exit_analysis.py`).
- Early exit fires narrowly in practice: 2 of 60 real benchmark corpus cases actually skip a
  stage (both `mypy` cases, where `group_repeated` collapses everything before `max_tokens` runs).
  Measured aggregate timing impact across the corpus is within measurement noise (sub-millisecond,
  no consistent net direction) — this task's own honest performance finding, not oversold.
- A structural limitation worth recording: `python_ast_summarize`/`code_ast_summarize` are always
  the *first* stage in the filters that use them (`cat-python.toml`, `cat-javascript.toml`,
  `cat-typescript.toml`), so early exit — which only ever skips stages that haven't run yet — can
  never skip the expensive AST parse itself, only the cheap bookkeeping stages after it. The
  highest-cost operation in the AST-summarization filters is therefore unaffected by this
  optimization by construction, not by oversight.
- If a future built-in stage is added, or an existing one's `preserve_patterns` handling changes to
  reconsider already-`COMPRESS` lines, `_STAGE_TYPES_INERT_ON_DECIDED_LINES` in `engine.py` must be
  reviewed — it is a deliberately hand-maintained, not auto-derived, list. This is documented
  prominently in `engine.py`'s own module docstring, not just here.
- See `backlog.md`'s `QB-036` entry for the full validation record.

## ADR-036: Multi-Agent Adapter Architecture — `AgentAdapter` Protocol + Registry (QB-035A)

**Status:** Decided (design only — no code implements this yet)
**Date:** 2026-07-10

**Context:**
QB-035 (Support more AI coding tools, and more programming languages) named Cursor, GitHub Copilot
Agent, and Gemini CLI as future targets but was deliberately left unscheduled pending real,
sustained usage validation of the Claude-Code-only v1 — a decision `ANTI_GOALS.md` #12 formalizes
("No multi-agent support in V1... Cursor, Copilot CLI, Gemini Code Assist... are V2") and
`ROADMAP.md` v2.0 names explicitly ("Cursor adapter", "Copilot CLI adapter", "Adapter detection in
`quor doctor`"). QB-035A asked, as a design-only phase with no runtime changes, how Quor's
architecture should generalize to support more than one agent without duplicating compression
logic or branching on agent names throughout the codebase.

Reading every relevant module before designing anything (`quor/rewrite/`, `quor/filters/registry.py`,
`quor/pipeline/` in full, `quor/tracking/db.py`, both existing adapters, `__main__.py`, `init.py`,
`doctor.py`) found that Quor's core is **already fully agent-agnostic** — zero references to
"claude" or any agent concept anywhere in the rewrite classifier, `FilterRegistry`, `Pipeline`,
any `StageHandler`, `extract()`, or `InvocationRecord`. All agent-name coupling was found
concentrated in exactly four places: `__main__.py`'s hardcoded `_HOOK_ADAPTERS` set and if/else,
`init.py`'s Claude-Code-settings.json-specific logic behind a single `--claude` flag, `doctor.py`'s
hardcoded Claude-specific check functions, and `quor/adapters/base.py`'s Claude-Code-shaped Pydantic
models sitting alongside an already-declared but entirely unused `HookAdapter` Protocol.
`PROJECT_BIBLE.md`'s original architecture diagram already labels that Protocol as intentional
("HookAdapter Protocol, HookInput, HookOutput") — this generalization was planned from the project's
first architecture pass, never implemented past the reference adapter.

**Options considered:**
- **Leave `run_hook() -> None` (direct `sys.stdin`/`sys.stdout` I/O) as the adapter contract, add a
  registry around it:** rejected — every existing adapter test already has to monkeypatch both
  streams to exercise it; a registry alone would not retire the duplicated BOM-stripping logic
  independently re-implemented in both `claude.py` and `claude_read.py` today, and every future
  adapter would keep re-copying that same boilerplate.
- **A `bytes`-in/`bytes`-out `handle_event(event, raw_stdin: bytes, tracking) -> bytes | None`
  contract, with exactly one place (`__main__._run_hook()`) owning stream I/O:** chosen. Makes every
  adapter a pure, directly unit-testable function; retires the duplicated stdin-handling boilerplate
  as part of the QB-035B migration; requires no change to the existing outer fail-open guard in
  `__main__.py`, which already holds `original_bytes` and already falls back to writing them
  unchanged on any exception.
- **An open, string-keyed event system** (arbitrary event names per agent) **vs. a small, closed
  `AgentEvent` enum with two values** (`COMMAND_INTERCEPT`, `CONTENT_INTERCEPT`) **mapped from each
  agent's own event names:** the closed enum was chosen — both values already exist today under
  Claude Code's own names (`PreToolUse`/Bash, `PostToolUse`/Read); an open system would be exactly
  the speculative abstraction CLAUDE.md's Rule 4 and this project's repeated "no speculative
  abstractions" discipline warn against. A third event kind remains a non-breaking additive enum
  member later, not a redesign.
- **A single shared "generic hook payload" Pydantic model vs. keeping `HookInput`/`ToolInput`/etc.
  fully adapter-local:** adapter-local was chosen. A shared generic payload model would either have
  to lowest-common-denominator every future agent's fields or grow an unbounded `extra="allow"`
  grab-bag; keeping each adapter's payload models next to that adapter, with only the `bytes`
  boundary shared, applies the same "don't branch on agent identity" principle to data shape, not
  just control flow.
- **Two discovery mechanisms (a hardcoded built-in dict plus a `quor.hook_adapter` entry-point
  group) vs. entry-points only:** the dual mechanism was chosen, mirroring
  `_STAGE_HANDLERS`/`quor.compression_stage` (ADR-026) and `PluginRegistry`/`quor.plugin` exactly —
  Quor's own built-in Claude Code integration should not need to be an installable plugin of itself,
  while third-party agent adapters get the same fail-open, cached discovery every other extension
  point already provides.

**Decision:**
`quor/adapters/base.py` gains `AgentEvent` (a two-value `StrEnum`), the `AgentAdapter` Protocol
(`agent_id`/`display_name`/`api_version` class attributes; `supported_events` property;
`handle_event()`, `install()`, `doctor_checks()` methods), and thin `InstallContext`/
`InstallResult`/`DoctorContext`/`DoctorCheck` dataclasses/type-alias — mirroring `Plugin`'s existing
`kw_only`, frozen-dataclass conventions (ADR-026) exactly. A new `quor/adapters/registry.py`
provides `AdapterRegistry`, structurally identical to `plugin_loader.py`'s existing discovery
(cached, fail-open per entry, built-in dict + `quor.hook_adapter` entry-point group). `__main__.py`,
`quor doctor`, and `quor init` are redesigned (not yet implemented) to resolve through this registry
instead of hardcoding Claude Code, with `init`/`doctor` remaining the only two CLI commands touched
— no seventh command is introduced, respecting CLAUDE.md's fixed six-command rule. The existing,
unused `HookAdapter` Protocol is superseded and slated for removal once `AgentAdapter` lands.
`ClaudeAdapter` is designed as a thin wrapper around today's `claude.py`/`claude_read.py`, required
to produce byte-for-byte identical output to today's behavior, proven via the same before/after
equivalence discipline QB-005B established for the AST parser framework refactor — not a rewrite of
either file's actual logic.

**Consequences:**
- No runtime code changes in this phase (QB-035A) — this ADR records a design decision for
  QB-035B–F to implement, not a shipped change. `ANTI_GOALS.md` #12 is not violated: no agent
  support is added; only an internal extension point is designed.
- The hook argv shape (`quor hook claude` → `quor hook <agent_id> <event>`) is a real backward-
  compatibility risk for already-installed hook scripts once QB-035C implements the `__main__.py`
  migration — the design document recommends permanent argv aliases (`"claude"`/`"claude-read"` →
  resolved agent/event pairs) as the default resolution, but this is not decided as final until
  QB-035C actually implements it against a real pre-existing hook script.
- Whether Cursor, Copilot Agent, or Gemini CLI actually expose anything resembling
  `COMMAND_INTERCEPT`/`CONTENT_INTERCEPT` is unverified by this design — an empirical observation
  (Cursor sending a doubled UTF-8 BOM, already handled in both existing adapters and documented in
  `PROJECT_BIBLE.md` item 9) is suggestive, not confirmatory, and QB-035F must independently verify
  a real target agent's hook contract before implementing it, mirroring QB-005C's own mandatory
  pre-flight compatibility gate applied to a parser library.
- `quor explain` has no equivalent for `CONTENT_INTERCEPT`-shaped events (e.g. "explain how a Read
  of this file would compress") — an existing, pre-dating-this-ADR gap, explicitly out of scope for
  QB-035A–E and not resolved by this decision.
- See `docs/design/QB-035A-multi-agent-adapter-design.md` for the full design (event model
  rationale, lifecycle model, complete interface signatures, every file eventually needing
  modification, and the phased QB-035B–F backlog breakdown) and `backlog.md`'s `QB-035A` entry for
  the validation record.
