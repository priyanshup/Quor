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

**Superseded in part by ADR-043** — see there for the POSIX (macOS/Linux) launcher added in
QB-082. This ADR's Windows PowerShell decision is unchanged and still current for Windows.

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

## ADR-037: Repository Context Profile — Parallel Package, Not a Stage; `quor map` as a Second Exempted Command (QB-061)

**Status:** Decided and implemented
**Date:** 2026-07-28

**Context:**
A 2026-07-28 product-priority review (`docs/design/QB-061-repo-context-profile.md`, itself
building on `docs/design/repo-summarization-investigation.md`) found that every capability Quor
had shipped through QB-065 compresses **one already-captured blob** — one command's stdout, or
one file's content — and that the largest remaining real-usage issue (mypy/ruff/generic
negative-compression) was a correctness bug already fixed (QB-065), not a missing capability. The
token cost Quor had never addressed was repository orientation: the multi-call discovery sequence
(`ls`/`find`/several `cat`/`grep`/`git log`) an AI coding assistant runs to understand an
unfamiliar repo before it can act. That is a synthesis problem — reading many files and producing
a document that never existed verbatim anywhere in the repo — not a redundancy-removal problem on
a single blob, so it does not fit the existing `ContentMask`/`StageHandler` contract at all (see
Anti-Goal #18: "a stage that receives a string and returns a modified string is architecturally
wrong" — this reads *many* files and returns a *synthesized* one, a different violation entirely,
not a smaller version of the same one).

**Options considered:**
- **Force it into a `StageHandler`** (e.g., a stage that "compresses" a `find .`/`ls` listing down
  to a summary): rejected. This would mean answering a different question with someone else's
  output — a bigger meaning-change risk than anything Quor does today, and a direct violation of
  Anti-Goal #3 ("never silently modify content meaning") and #18.
- **A new package parallel to the ContentMask pipeline** (`quor/pipeline/repo_profile/`), reusing
  existing infrastructure *patterns* (not the `ContentMask`/`StageHandler` contract itself) where
  they generalize: chosen. `ContentMask` re-enters the picture only optionally, at the very end,
  compressing the profile's own rendered Markdown through the existing `markdown` filter if it's
  large — never repo source.
- **A new three-tier detector-rule registry mirroring `FilterRegistry`'s loading pattern, but with
  first-match-wins semantics** (like `FilterRegistry.find()`) **vs. match-all semantics:**
  match-all was chosen. A single command maps to exactly one filter (mutually exclusive by
  construction), but a repository legitimately satisfies many detector rules at once — a repo can
  be a Flask app *and* Dockerized *and* built on GitHub Actions simultaneously. `DetectorRegistry`
  reuses `filters/loader.py`'s TOML→Pydantic pattern and `filters/trust.py::is_git_tracked`'s
  project-tier trust check directly (same three-tier project > user > builtin precedence, same
  git-tracked gate for project-local rules), but its `detect()` method evaluates every loaded rule
  and returns every match grouped by category, deduplicated by (category, name) with
  project > user > builtin precedence on a tie — not `FilterRegistry`'s "first match wins, stop
  looking" contract.
- **Framework/database detection scoped to manifest-file dependency mentions only, vs. scanning
  arbitrary source files for import statements:** manifest-only was chosen. Bounded to a handful of
  small, well-known files per repo (`pyproject.toml`, `package.json`, `requirements.txt`, ...),
  every match a real, verifiable dependency declaration — an unbounded whole-tree import scan would
  be both slow and exactly the kind of unverifiable heuristic the task's "no heuristics that cannot
  be deterministically verified" constraint rules out.
- **Full tree-sitter/AST symbol extraction for entry points (Aider-style repo map) vs. manifest
  fields plus a bounded `if __name__ == "__main__":` content scan:** the lighter mechanism was
  chosen for this phase. Entry points are detected from structured manifest fields
  (`pyproject.toml` `[project.scripts]`/`[tool.poetry.scripts]`, `package.json` `bin`/`main`,
  `Cargo.toml` `[[bin]]`/the `src/main.rs` convention, `go.mod`'s `main.go`/`cmd/*/main.go`
  convention) plus a scan bounded to root-level `.py` files only — never a recursive, unbounded
  content scan. Full symbol-level extraction (reusing the parsed trees `code_ast_summarize`'s
  analyzers already build) remains a real, larger follow-up phase, deliberately not attempted here
  — see `docs/design/QB-061-repo-context-profile.md` §6/§10's own phasing note.
- **A silent reroute of an existing exploratory command** (e.g. substituting the profile for the
  AI's first `find .`) **vs. a new, explicit command:** rejected outright, the same way ADR-030's
  "omit the key rather than emit a wrong value" discipline and Anti-Goal #3 already rule out
  changing what a real command's output means. `quor map` is invoked explicitly, exactly like any
  other Quor CLI command — never substituted transparently for a command the AI actually asked to
  run.
- **A seventh CLI command vs. reusing an existing one:** CLAUDE.md's fixed six-command rule already
  has one precedent exemption, `quor schema` (a non-filtering utility command, JSON Schema dump).
  `quor map` is granted the same category of exemption — a second, explicitly-approved utility
  command, not a filtering operation — rather than overloading `quor explain`/`quor doctor` with an
  unrelated, much larger new output shape.

**Decision:**
`quor/pipeline/repo_profile/` is a new package, structurally sibling to (not inside) the
ContentMask pipeline: `walk.py` (`git ls-files` primary, `os.walk` fallback with a hardcoded
skip-set), `detectors/` (the match-all registry described above, with built-in TOML rule files for
build systems, package managers, frameworks, test frameworks, CI systems, databases,
containerization, and configuration files), `languages.py` (extension histogram, computed directly
from the walk — no detector rules needed), `entry_points.py`, `directories.py` (important
directories + services/monorepo detection), `statistics.py`, `model.py` (`RepoProfile`, a frozen
Pydantic model matching `FilterConfig`'s conventions), and `render.py` (fixed-template Markdown by
default; `--json` is an optional, secondary output mode, not the primary interface).
`profiler.build_profile(root) -> RepoProfile` is the single public entry point. `quor map` is
registered as a second exempted utility command in `quor/cli/main.py`, added to `__main__.py`'s
`_CLI_COMMANDS` routing set (a real bug caught during implementation: without this, `quor map`
silently fell through to the dispatcher, which tried to execute a literal shell command named
`map`). The invocation is tracked through the existing `count_tokens()`/`track_invocation()` path
under a new synthetic label, `REPO_PROFILE_FILTER_LABEL` (`quor/tracking/db.py`, defined alongside
the existing `PASSTHROUGH_LABEL` for the same reason) — `original_tokens`/`final_tokens` are
recorded equal by design (there is no "before" blob to compress against; this is synthesis, not
compression), so the invocation is visible in `quor gain`'s invocation counts without distorting
its net-tokens-saved headline. `quor.analytics.filter_divergence.flag_low_performers` (QB-065's
negative-compression health check) excludes `REPO_PROFILE_FILTER_LABEL` the same way it already
excludes `PASSTHROUGH_LABEL` — both are synthetic, non-ContentMask-filter labels whose 0.0% is
by-design, not evidence of a broken filter; without this exclusion, a real compression regression
(mypy, ruff) would have been reported side-by-side with an expected, by-design zero in `quor
doctor`, diluting the signal (caught and fixed during this same implementation pass).

**Consequences:**
- No changes to `ContentMask`, `Decision`, `StageHandler` Protocol, `Pipeline.execute`, or any
  existing filter/stage — this is entirely additive, new-package work. The compression benchmark
  suite (`python -m tests.benchmarks.run_benchmarks`) shows zero change (127 cases, 35.9% overall,
  same as pre-QB-061), confirming no regression to the existing pipeline.
- `quor map`'s output quality is heuristic in the sense that detector rules match markers and
  patterns (a stale `requirements.txt` from a removed dependency would produce a false positive),
  but never heuristic in the "cannot be deterministically verified" sense the task ruled out —
  every fact is evidence-cited (file + pattern) and re-running the scan against unchanged repo
  state produces byte-identical output (verified by a dedicated determinism test in the fixture-repo
  benchmark corpus, `tests/unit/test_repo_profile_benchmark.py`).
- Symbol-level entry-point/framework detail (Aider-style repo map) remains unbuilt — a real,
  larger follow-up phase, not silently done partially. The current entry-point mechanism is
  manifest-fields-plus-bounded-scan only, explicitly scoped this way per the options above.
- `quor map`'s real-session token savings (the number that would actually validate this feature's
  core hypothesis: does a single profile call measurably reduce the discovery-call sequence an AI
  otherwise runs) is not yet measured — per Anti-Goal #24/#25, no such figure is published anywhere
  in this ADR or the shipped documentation; it must be measured against real usage before any
  claim is made, exactly the discipline already applied to every other Quor savings figure.
- See `docs/design/QB-061-repo-context-profile.md` for the full design (competitive positioning
  against Aider's repo map, complete reuse audit, benchmark strategy, and the phased implementation
  plan) and `backlog.md`'s `QB-061` entry for the implementation record.

## ADR-038: Repository Symbols — Separate Command/Index from `RepoProfile`, Symbol Extraction as an Additive Function per `ast_summarize` Language Module, `quor symbols` as a Third Exempted Command (QB-066)

**Status:** Decided and implemented
**Date:** 2026-07-28

**Context:**
ADR-037/QB-061 deliberately deferred "full tree-sitter/AST symbol extraction for entry-point/
framework detail (Aider-style repo map)" as a named, larger follow-up phase (its own "Deliberately
out of scope" note, and QB-061's design doc §6/§10 Phase D). This task is that follow-up:
deterministic, repository-wide symbol indexing — classes, interfaces, structs, traits, enums,
functions, methods, public/private visibility, entry-point functions, and file locations — reusing
`quor/pipeline/ast_summarize/`'s existing per-language parsers (the same tree-sitter/`ast`
infrastructure `code_ast_summarize`/`python_ast_summarize` already build) rather than reparsing with
a second parser per language.

**Options considered:**
- **Fold symbol data into `RepoProfile`/`quor map`'s existing output vs. a new, separate command and
  index:** separate was chosen. A symbol index scales with source line count (parse cost per file),
  not repo metadata size the way every existing `RepoProfile` field does (manifest fields, marker
  files, a bounded root-level scan) — folding it in would make every `quor map` call, including the
  common "just tell me what this repo is" case, pay a much larger, language-parse cost it doesn't
  need. Two commands with two focused jobs (orientation vs. symbol lookup) is a smaller trust and
  performance surface than one command whose cost is unpredictable depending on repo size. This
  mirrors QB-061's own §2 competitive positioning note: Aider's repo map answers "what are all the
  symbols in this codebase" continuously; `quor map` answers "what *is* this codebase" once — QB-066
  is the former question, finally built, but kept a distinct command rather than merged into the
  latter's existing shape.
- **A second, wholly independent parsing/registry stack for symbol extraction vs. additive
  `extract_symbols_*()` functions on the existing `ast_summarize` language modules:** additive was
  chosen, per the task's own "reuse existing AST parsers and language registries... do not reparse
  languages if existing infrastructure can be extended" constraint. Each of the eight already-
  registered languages (`python`/`javascript`/`typescript`/`tsx`/`go`/`java`/`rust`/`csharp`) gained
  one new function in its existing module, sharing that module's own lazy-import/fail-open
  discipline for its optional tree-sitter dependency (unchanged) rather than a parallel dependency-
  gating mechanism. `quor/pipeline/ast_summarize/registry.py` gained a second, parallel dict
  (`_SYMBOL_EXTRACTORS`/`get_symbol_extractor()`) alongside its existing `_ANALYZERS`/
  `get_analyzer()` — a separate dict, not a richer per-analyzer return type, because the two
  questions ("which lines compress" vs. "what symbols exist, named and located") are independently
  correct and serve two different consumers (`code_ast_summarize` vs. `quor symbols`) that must
  never be coupled to one shared, larger call.
- **`Symbol` as a Pydantic model (matching `RepoProfile`'s own convention) vs. a plain frozen
  dataclass:** dataclass was chosen, matching `walk.py`'s `WalkResult` rather than `model.py`'s
  `RepoProfile`. Every `Symbol`/`FileSymbols`/`RepoSymbolIndex` field is computed internally from a
  parse tree or a file walk, never user input — no external validation boundary exists for this
  data the way `FilterConfig`'s TOML-sourced fields have one. `orjson.dumps()` serializes dataclass
  instances natively (via `dataclasses.asdict()`), so `--json` needed no extra conversion layer
  either way.
- **Per-language visibility (`is_public`) semantics — one uniform rule vs. each language's own
  mechanism:** each language's own real visibility mechanism was chosen, not a single cross-language
  heuristic (e.g. "no leading underscore"), because a uniform rule would be wrong for at least half
  the languages: Go's exported-identifier capitalization, Rust's `pub` keyword, Java/C#'s explicit
  `public` modifier (package-private/internal by default), and TypeScript's `accessibility_modifier`
  (public by default) are all genuinely different conventions, not stylistic variance on one
  concept. Getting this wrong per-language would be exactly the kind of undocumented,
  non-deterministically-verifiable heuristic the task's "no heuristics that cannot be
  deterministically verified" constraint rules out — every visibility bit here is grounded in a
  real, present-or-absent grammar token, empirically verified against the installed tree-sitter
  grammar during implementation (methodology matches `javascript.py`/`go.py`/etc.'s own "empirically
  verified" precedent), never inferred.
- **Entry-point detection — a bounded name match (`main`/`Main`) vs. a broader heuristic (e.g.
  scanning for `if __name__ == "__main__":`, `public static void main`, framework-specific
  decorators):** the bounded name match was chosen. Every mainstream entry-point convention across
  the eight supported languages names the entry function `main` (Python/JavaScript/TypeScript/Go/
  Java/Rust) or `Main` (C#) — a plain, deterministic name comparison, not a per-language pattern
  library, and the same "small, verifiable rule over broad inference" discipline QB-061's own entry-
  point detection already applies (manifest fields plus a bounded root-level scan, never an
  unbounded content scan).
- **A hard per-file size cap vs. unbounded parsing:** a fixed 2 MB cap was chosen (skipped files are
  counted and named in a summary note, not silently dropped) — QB-061's own design doc (§7 risk 4)
  named large-repo, per-file AST parsing as this future phase's own explicit, unresolved scaling
  risk; a fixed cap is the simplest deterministic answer available (unlike a time-based budget,
  which would make output depend on machine speed, violating the determinism guarantee) and a
  generated/minified/vendored file this size is far more likely to be pathological parse input than
  source worth indexing.
- **Per-file fail-open (catching `Exception` at the orchestrator's file-processing boundary) vs.
  letting a parse failure propagate and abort the whole scan:** per-file fail-open was chosen, and is
  a deliberate, narrow exception to the project's normal "every `except` clause is specific" rule
  (Coding Conventions, above) — justified the same way the hook's own top-level `except Exception`
  guard is: `quor symbols` walks arbitrarily many, only partially-trusted source files in one
  invocation, with no `Pipeline.execute()`-style per-stage fail-open sitting above this loop the way
  a single-file `code_ast_summarize` call has (ADR-018). One malformed file must not deny a symbol
  index for the other 4,999 files in the same repo. The per-language `extract_symbols_*()` functions
  themselves keep the existing, narrower fail-open contract unchanged (a missing optional dependency
  is caught and warns; a genuine parse failure propagates) — the broader catch lives only at
  `symbols.py`'s own repo-wide orchestration layer, one level up, not inside any analyzer.
- **A third exempted CLI command (`quor symbols`) vs. an option on `quor map` (e.g. `quor map
  --symbols`):** a third exempted command was chosen, following the exact process ADR-037 already
  established for `quor map` itself (CLAUDE.md's "V1 has exactly 6 commands... don't add more
  without explicit approval" gate, with `schema` and `map` as the only precedents) — explicit user
  sign-off was obtained before any CLI code was written, not assumed granted by this ADR or the
  originating task instructions alone, mirroring ADR-037's own "not assumed granted by this
  document" discipline. Folding it into `quor map` as a flag was rejected for the same performance-
  surface reason the "separate command/index" option above was chosen: a flag would make `quor map`
  itself carry symbol-scan cost by proximity even when unused by default, whereas a separate command
  keeps `quor map`'s existing performance characteristics completely unchanged (verified: the
  compression benchmark suite and every existing `repo_profile`/`quor map` test pass unmodified).

**Decision:**
`quor/pipeline/repo_profile/symbols.py` (`build_symbol_index(root) -> RepoSymbolIndex`, the single
public entry point, mirroring `profiler.build_profile()`'s own shape) walks the repo once via the
existing `walk.py`, and for each file whose extension maps to a registered `ast_summarize` language
(a small, purpose-built extension table scoped to exactly the eight symbol-capable languages — see
that module's own docstring for why this isn't `languages.py`'s or `claude_read.py`'s existing
extension tables, both scoped to different questions), calls
`ast_summarize.registry.get_symbol_extractor()` and that language's `extract_symbols_*()` function.
`quor/pipeline/repo_profile/symbols_model.py` defines `FileSymbols`/`RepoSymbolIndex` (frozen
dataclasses, reusing `ast_summarize.symbol_model.Symbol` directly rather than redefining it) and
`symbols_render.py` renders fixed-template Markdown by default, JSON via `--json` (mirroring
`render.py`'s identical `quor map` convention). `quor symbols` is registered as a third exempted
utility command in `quor/cli/main.py`/`__main__.py`'s `_CLI_COMMANDS` routing set (the exact real
bug ADR-037 caught for `quor map` — a command name missing from `_CLI_COMMANDS` silently falls
through to the shell dispatcher — is guarded against here by a dedicated regression test,
`test_reachable_without_dispatcher_fallthrough`, rather than only informally re-checked by hand).
Invocations are tracked under a new synthetic label, `REPO_SYMBOLS_FILTER_LABEL` (`quor/tracking/
db.py`, defined alongside `REPO_PROFILE_FILTER_LABEL` for the identical reason — no "before" blob,
`original_tokens`/`final_tokens` recorded equal by design) and excluded from
`flag_low_performers()`'s low-performer check the same way `REPO_PROFILE_FILTER_LABEL` already is.

**Consequences:**
- No changes to `ContentMask`, `Decision`, `StageHandler` Protocol, `Pipeline.execute`, any existing
  filter/stage, or any existing `analyze_*()` compression analyzer's behavior — every
  `extract_symbols_*()` function is purely additive to its language module. The compression
  benchmark suite (`python -m tests.benchmarks.run_benchmarks`) shows zero change (127 cases, 35.9%
  overall, identical to pre-QB-066), confirming no regression to the existing pipeline.
- `quor symbols`'s output is heuristic in the same bounded sense `quor map`'s is: a name match for
  entry points, a per-language grammar-token check for visibility — never heuristic in the "cannot be
  deterministically verified" sense the task ruled out, and re-running the scan against unchanged
  repo state produces byte-identical output (verified by a dedicated determinism test in the
  fixture-repo benchmark corpus, mirroring `test_repo_profile_benchmark.py`'s identical check for
  `quor map`).
- A file that declares zero symbols is omitted from `RepoSymbolIndex.files` entirely (not listed with
  an empty `symbols` list) — the same token-lean, "omit rather than print emptiness" convention
  `render.py` already applies to `RepoProfile`'s own empty sections.
- Search/`--focus` filtering was explicitly out of scope for this task ("do not implement search or
  `--focus` in this QB unless the architecture naturally supports it") and remains unbuilt — a real,
  possible follow-up, not silently done partially.
- `quor symbols`'s real-session token savings are not yet measured — per Anti-Goal #24/#25, no such
  figure is published anywhere in this ADR or the shipped documentation; it must be measured against
  real usage before any claim is made, exactly the discipline ADR-037 already applied to `quor map`.
- See `backlog.md`'s `QB-066` entry for the implementation record (test counts, benchmark corpus,
  files touched).

## ADR-039: Repository Dependency Graph — Raw Facts Per-Language, One Orchestrator Resolves Them, `quor graph` as a Fourth Exempted Command (QB-067)

**Status:** Decided and implemented
**Date:** 2026-07-29

**Context:**
QB-066/ADR-038 built a deterministic, repository-wide *symbol* index — what does each file
declare. This task is the natural follow-up: a deterministic, repository-wide *relationship* graph
— imports, exports, inheritance, interface/trait implementation, method overrides, module/package
dependencies, and (where a language's grammar allows unambiguous resolution) call relationships —
the token cost an AI coding assistant otherwise pays re-discovering "what calls this" / "what does
this depend on" via repeated `grep`/`Read` calls. The task's own instructions required reusing
QB-066 wherever possible and explicitly ruled out inference/heuristics/an LLM.

**Options considered:**
- **A single combined pass per file (symbols + relationships in one parser call) vs. two
  independent additive functions per language, mirroring `analyze_*()`/`extract_symbols_*()`'s
  existing split:** two independent functions was chosen — `extract_relationships_*()` was added to
  each of the eight `ast_summarize` language modules alongside its existing `extract_symbols_*()`
  (QB-066) and `analyze_*()` (QB-005B/C/D/QB-046), reusing that module's own parser setup and
  lazy-import/fail-open discipline unchanged. This mirrors ADR-038's own "two independently correct
  sibling questions, not one derived from the other" reasoning (`_SYMBOL_EXTRACTORS`/
  `_RELATIONSHIP_EXTRACTORS` are two parallel dicts in `registry.py`, not a richer return type on
  one). `quor graph`'s orchestrator calls both extractors per file (two parses), the same
  already-accepted cost the codebase already pays whenever a caller needs both an `analyze_*()` and
  an `extract_symbols_*()` result for one file — see `registry.py`'s own module docstring. A single
  combined-pass function per language was rejected: it would require a third, richer return shape
  invented specifically for this task, coupling two independently-useful questions (what does this
  declare vs. what does it relate to) into one call for every future consumer, exactly what ADR-038
  already ruled out doing for symbols vs. compression.
- **Per-language extractors resolve their own cross-file references vs. a single orchestrator-level
  resolution engine:** orchestrator-level was chosen, unanimously across every language. Each
  `extract_relationships_*()` function is file-local and returns raw, unresolved facts (a raw import
  path, a raw base-class name, a raw callee name) — see `relationship_model.py`'s `Relationship`
  docstring. `quor/pipeline/repo_profile/graph.py`'s `build_dependency_graph()` is the only place
  that resolves a raw fact against the whole repo's symbol table. Writing the (genuinely complex,
  per-language-different) resolution algorithm once, in one file, with one shared test suite, avoids
  eight chances for it to drift — the same reasoning ADR-038 already applied to keeping
  visibility/entry-point logic per-language but simple, extended here to a harder problem
  (cross-file resolution) by centralizing it instead.
- **Resolution scope — best-effort/heuristic name matching vs. conservative, unambiguous-only
  resolution:** conservative was chosen, confirmed with the user before implementation began (this
  task's own "no heuristics" constraint, and the task's own call-graph caveat "where language
  support naturally allows"). An edge's `target_file`/`target_symbol` are populated only when a
  reference resolves to *exactly one* candidate — same-file, or through the file's own unambiguous,
  non-wildcard import bindings — for both type references (inherits/implements/overrides) and calls
  (`graph.py::_resolve_relationship`). A wildcard import (`from x import *`, `use path::*`, Java's
  `import pkg.*`, Go's dot-import) never creates a binding at all, at the *extraction* layer (every
  language's own `extract_relationships_*()` skips it at the source, not filtered out later) — the
  same "ambiguous binding" exclusion applied identically across all eight languages. An ambiguous
  same-file name collision (two classes in one file both declaring a same-named method) also stays
  unresolved rather than guessed. `target_raw` is always present regardless of resolution outcome —
  the underlying deterministic fact is never lost, only the *pointer* to a specific file/symbol is
  sometimes absent.
- **Import/module-path resolution — a general package-manager/build-system resolution algorithm vs.
  a bounded, spec-or-convention-grounded rule per language:** bounded rules were chosen, each scoped
  and documented in `graph.py::_resolve_import_target()`'s per-language helpers: Python's own
  relative-import level semantics plus a bounded absolute-import check against the repo root (no
  `sys.path`/venv/site-packages resolution); JS/TS/TSX's relative-path-plus-extension-probing
  convention (no `node_modules`/`tsconfig` `paths`); Java's package-to-directory convention (a
  fully-qualified name must live at a path ending in that name's slash-joined form, regardless of
  which source root precedes it — no build-tool-specific source-root list); Rust's `crate::`-rooted
  single-crate `src/` convention (no `Cargo.toml`-driven workspace/multi-crate resolution, and never
  `self::`/`super::`, which need the importing file's own position in the `mod` tree — separate,
  out-of-scope information). Go and C# get no import-path resolution rule at all — Go's import paths
  are `go.mod`-module-relative (a real build-system resolution algorithm, explicitly out of scope)
  and C#'s `using` opens a namespace with no required directory convention the way Java's package
  system has; both stay `target_raw`-only, a real, documented, per-language limitation, not an
  oversight. Every rule here is grounded in the language's own spec or a near-universal, real
  convention (the same "real, verifiable rule over broad inference" discipline QB-061's entry-point
  detection and QB-066's Go package-naming-convention default already established) — never a general
  algorithm that would need to model an entire build/package-manager ecosystem.
- **Call relationships — same-file only vs. resolving through a file's own import bindings too:**
  both, via one unified mechanism. A bare call (`helper()`) resolves same-file first (if exactly one
  matching symbol exists in that file), then via the file's own import bindings (an imported name
  called directly). A qualified call (`self.method()`/`this.method()`/`super.method()`/`base.method()`
  — the same-file-scoped sentinel qualifiers each language's own `extract_relationships_*()`
  documents — or `alias.method()`/`pkg.Func()`/`module::func()`) resolves the qualifier against the
  file's own import-alias bindings, then checks the resolved file's own symbol table for the target
  name. `self.method()` resolving to a method declared only on a *different-file* superclass is a
  documented, real limitation (the same-file qualifier sentinels intentionally do not chase an
  inheritance chain across files) — expanding this was considered and rejected as disproportionate
  scope growth for this phase; a real, possible follow-up, not silently done partially.
- **A decorator/attribute expression's own call (Python `@app.route(...)`) — special-cased out vs.
  reported as an ordinary call attributed to the function it decorates:** reported as an ordinary
  call was chosen — `ast.walk()` over a `FunctionDef` node naturally includes its `decorator_list`,
  and excluding it would require new, undocumented special-casing this task's own "no heuristics"
  discipline argues against adding without a specific reason to. It is still a real, deterministic
  fact (that call expression genuinely exists at that source location, evaluated at definition time)
  — documented explicitly in `python.py`'s `extract_relationships_python()` docstring and covered by
  a dedicated regression test once the flask-pip fixture surfaced it during benchmark-corpus testing.
- **A fourth exempted CLI command (`quor graph`) vs. an option on `quor symbols` (e.g. `quor symbols
  --graph`):** a fourth exempted command was chosen, following the exact process ADR-037/ADR-038
  already established (CLAUDE.md's "V1 has exactly 6 commands... don't add more without explicit
  approval" gate, `schema`/`map`/`symbols` as the three precedents) — explicit user sign-off was
  obtained before any CLI code was written, not assumed granted by this ADR or the originating task
  instructions alone, mirroring both prior ADRs' identical discipline. A flag on `quor symbols` was
  rejected for the same performance-surface reason ADR-038 already gave for keeping `quor symbols`
  itself separate from `quor map`: a flag would make `quor symbols` carry relationship-extraction
  cost by proximity even when unused by default, whereas a separate command keeps `quor symbols`'s
  existing performance characteristics completely unchanged (verified: every existing `repo_profile`/
  `quor symbols`/`quor map` test and the compression benchmark suite pass unmodified).
- **Call-graph scope for this phase — ship it now (conservative/unambiguous) vs. defer entirely to a
  named follow-up:** shipping it now (conservative) was chosen, confirmed with the user before
  implementation began — the task's own wording ("where language support naturally allows, also
  extract deterministic call relationships") treats this as an easier bar than the mandatory
  relationship types, and the conservative same-file/unambiguous-import-binding resolution rule
  above keeps it bounded rather than open-ended.

**Decision:**
Each of the eight `ast_summarize` language modules gained one additive `extract_relationships_*()`
function returning `list[Relationship]` (`quor/pipeline/ast_summarize/relationship_model.py`) — raw,
file-local, unresolved facts, exactly as written in the source. `registry.py` gained a third,
parallel dict/getter (`_RELATIONSHIP_EXTRACTORS`/`get_relationship_extractor()`) alongside the
existing `_ANALYZERS`/`_SYMBOL_EXTRACTORS`, plus a promoted, single source of truth for
extension-to-language routing (`EXTENSION_TO_LANGUAGE`, moved here from `repo_profile/symbols.py`'s
originally-private table so `quor symbols` and `quor graph` share it rather than risking two drifting
copies). `quor/pipeline/repo_profile/graph.py`'s `build_dependency_graph(root) -> RepoDependencyGraph`
is the orchestrator — walks the repo once via the existing `walk.py`, and per file calls both
`get_symbol_extractor()` and `get_relationship_extractor()` (not `symbols.py`'s `build_symbol_index()`
as an opaque black box, which would re-walk and re-read every file a second time — see `graph.py`'s
own module docstring), then resolves every raw relationship into an `Edge`
(`quor/pipeline/repo_profile/graph_model.py`) using the conservative, unambiguous-only rules above.
Same 2 MB per-file size cap and same per-file fail-open discipline as `symbols.py` (QB-066),
unchanged reasoning. `graph_render.py` renders fixed-template Markdown by default, JSON via `--json`,
mirroring `quor map`/`quor symbols`'s identical convention — grouped by source file, each edge
rendered as one line naming its kind, target, and (when resolved) `file::symbol` pointer. `quor
graph` is registered as a fourth exempted utility command (`quor/cli/main.py`, `__main__.py`'s
`_CLI_COMMANDS`) — explicit user sign-off obtained before any CLI code was written, following the
exact process ADR-037/ADR-038 already established, guarded against the same real
`_CLI_COMMANDS`-omission bug both prior ADRs caught via a dedicated regression test. Invocations are
tracked under a new `REPO_GRAPH_FILTER_LABEL` (`quor/tracking/db.py`), excluded from
`filter_divergence.flag_low_performers()`'s low-performer check the same way
`REPO_PROFILE_FILTER_LABEL`/`REPO_SYMBOLS_FILTER_LABEL` already are.

**Consequences:**
- No changes to `ContentMask`, `Decision`, `StageHandler` Protocol, `Pipeline.execute`, any existing
  filter/stage, or any existing `analyze_*()`/`extract_symbols_*()` function's behavior — every
  `extract_relationships_*()` function and `graph.py`/`graph_model.py`/`graph_render.py` are purely
  additive. The compression benchmark suite (`python -m tests.benchmarks.run_benchmarks`) and every
  existing `repo_profile`/`ast_summarize` test pass unmodified, confirming no regression.
- `quor graph`'s output is heuristic in the same bounded sense `quor map`/`quor symbols`'s is: a
  spec-or-convention-grounded import-path resolution rule per language, a same-file-or-unambiguous-
  import-binding resolution rule for type references and calls — never heuristic in the "cannot be
  deterministically verified" sense the task ruled out, and re-running the scan against unchanged
  repo state produces byte-identical output (verified by a dedicated determinism test, mirroring
  `quor map`/`quor symbols`'s identical check).
- Real, documented, per-language resolution limitations, not silently-partial work: Go/C# import
  paths are never resolved to a file (no in-scope directory convention); `self`/`this`/`super`/`base`
  qualified calls resolve same-file only, never chasing a cross-file inheritance chain; C#'s
  `inherits` relationship cannot syntactically distinguish a base class from an implemented interface
  (its grammar's single colon-delimited base list has no such marker) so `implements_interface` is
  never emitted for C#; Go's structural interface satisfaction has no syntactic `implements`-style
  marker at all, so Go emits no `inherits`/`implements_interface`/`implements_trait`/`overrides`
  relationships whatsoever; cross-file call resolution beyond a file's own direct, unambiguous import
  bindings (deeper attribute chains, dynamic dispatch, overload-specific resolution) is not
  attempted. Each is documented at its own `extract_relationships_*()`/`_resolve_import_target()`
  docstring, not merely implied by absence.
- `quor graph`'s real-session token savings are not yet measured — per Anti-Goal #24/#25, no such
  figure is published anywhere in this ADR or the shipped documentation; it must be measured against
  real usage before any claim is made, exactly the discipline ADR-037/ADR-038 already applied to
  `quor map`/`quor symbols`.
- See `backlog.md`'s `QB-067` entry for the implementation record (test counts, benchmark corpus,
  files touched).

---

## ADR-040: Multi-Agent Adapter Architecture — Implementation (QB-068)

**Status:** Decided and shipped
**Date:** 2026-07-29

**Context:**
ADR-036 (QB-035A) designed `AgentEvent`/`AgentAdapter`/`AdapterRegistry` as a
design-only phase — no runtime code changed. QB-068 is the implementation:
the product owner directed multi-agent support to begin now, ahead of
`ANTI_GOALS.md` #12's original V1/V2 split (that anti-goal is updated
in-place to record this, not silently ignored — see its own entry). The
task's four hard constraints throughout: preserve Quor's existing
architecture, zero behavior change to existing compression, deterministic
only (no heuristics, no guessing at an unverified agent's hook contract),
and existing users must not notice any regression.

**Decision:**
Implemented ADR-036's design essentially as specified (QB-035B through E,
compressed into one phase rather than the original phased backlog split,
since no intermediate release boundary required staging them separately):

- `quor/adapters/base.py` gained `AgentEvent`, `AgentAdapter`,
  `InstallContext`/`InstallResult`/`DoctorContext`/`DoctorCheck`,
  `QUOR_ADAPTER_API_VERSION`. The dormant, zero-reference `HookAdapter`
  Protocol was removed (ADR-036 flagged this as its intended fate).
- `quor/adapters/claude.py`/`claude_read.py` each gained a `handle_bytes()`
  bytes-in/bytes-out function extracted from `run_hook()`'s body into a
  shared `_handle_text()` core — `run_hook()` itself is unchanged (still
  reads `sys.stdin`, writes `sys.stdout.buffer`, now via the extracted
  core), so every existing direct test of `run_hook()` needed zero changes.
  `tests/unit/test_claude_adapter_equivalence.py` proves
  `ClaudeAdapter.handle_event()` (which calls `handle_bytes()`) produces
  byte-identical output to `run_hook()` across rewrite, no-op, BOM, Read
  compression, and Read no-op cases — the concrete proof ADR-036 section 8.2
  required before considering this migration safe.
- `quor/adapters/registry.py` (`AdapterRegistry`) mirrors
  `plugin_loader.py`'s two-tier discovery exactly: a hardcoded
  `_builtin_adapters()` dict (Claude, Codex, Gemini) plus the
  `quor.hook_adapter` entry-point group, fail-open per broken third-party
  entry, built-in `agent_id` always wins over a same-named third-party one.
- `quor/__main__.py`'s `_run_hook()` now resolves `(agent_id, event)`
  through the registry. Chose ADR-036 section 9 step 2's recommended option
  (a): `"claude"`/`"claude-read"` are permanent argv aliases resolving to
  `("claude", COMMAND_INTERCEPT)`/`("claude", CONTENT_INTERCEPT)` — every
  hook script already installed on a user's disk keeps working with zero
  user action, verified by `tests/unit/test_cli.py`'s existing installed-hook
  round-trip tests passing unmodified.
- `quor/cli/commands/doctor.py`'s hardcoded `HOOK_SPECS`/collision-check
  sequence was replaced by `_check_adapters()`, looping over
  `registry.all_adapters()` and calling each adapter's `doctor_checks()`,
  fail-open per adapter. `ClaudeAdapter.doctor_checks()` reproduces the
  exact prior check sequence (same names, same order, same detail strings)
  by calling the same `doctor.py` helper functions doctor.py always had —
  moved, not rewritten.
- `quor/cli/commands/init.py`'s `init()` gained a generic `--agent <id>`
  option; `--claude` is now permanent sugar for `--agent claude`, routing
  to the unchanged interactive Claude flow (`_init_claude()`), which now
  calls a new `_install_claude()` for the actual writes — the single
  implementation both `_init_claude()` and `ClaudeAdapter.install()` call,
  replacing what was previously logic inlined only in the CLI command. A
  new `_init_generic_agent()` handles any other agent via
  `adapter.install()` directly, without Claude's Bash-hook-collision
  detection (deliberately not generalized — see ADR-036 section 13, that
  logic stays Claude-local).
- Two new built-in adapters, each scoped to what its target tool's own
  documentation actually confirms (researched live before writing any
  code, not assumed from Claude Code's shape — the single biggest risk
  ADR-036 section 10.3 flagged):
  - `GeminiAdapter` — `COMMAND_INTERCEPT` only. Gemini CLI's `BeforeTool`
    hook confirmed to support rewriting a tool's arguments via
    `hookSpecificOutput.tool_input`, matched against the `run_shell_command`
    tool. `CONTENT_INTERCEPT` not implemented: Gemini's `AfterTool` hook's
    only confirmed output capability is `additionalContext` (append), not a
    confirmed full-content-replace field.
  - `CodexAdapter` — no `supported_events` at all. Codex CLI's `PreToolUse`
    hook is documented as allow/deny only, with no confirmed way to rewrite
    a command, and its hook system is experimental with unconfirmed Windows
    support (a material risk for a Windows-first tool). `install()` is a
    documented no-op returning a warning rather than a hook Quor cannot
    verify does anything; `doctor_checks()` is detection/readiness
    reporting only.
- Both new adapters' "not installed" `quor doctor` checks are advisory
  (`ok=True`) — a deliberate departure from `ClaudeAdapter`'s own checks
  (which do fail when Claude's hook isn't installed), made specifically
  because Codex/Gemini are new, opt-in integrations introduced in this same
  release: their absence must never flip `quor doctor`'s overall pass/fail
  for a user who only uses Claude Code. Verified directly against this
  repo's own dev machine (Claude Code installed, Codex/Gemini not) — every
  new adapter check reports healthy, informational.
- `tests/fixtures/test_adapter` (installable `quor-test-adapter` package,
  `quor.hook_adapter` entry point) mirrors `tests/fixtures/test_plugin`'s
  existing convention — proves discovery against a real installed package,
  not only a monkeypatched `importlib.metadata.entry_points()`. Wired into
  `ci.yml`/`canary.yml`/`CONTRIBUTING.md` alongside the existing plugin
  fixture.

**Consequences:**
- Full validation gate run and green: `ruff check quor tests` (0 issues),
  `mypy quor` (0 issues), the entire `tests/unit` suite (56 files, run in
  batches to stay under this dev environment's own dogfooded hook timeout),
  `tests/integration -m integration` (7/7), `tests/benchmarks/test_benchmarks.py`
  (396+ cases, compression behavior byte-identical), `quor verify` (204/204
  filter tests), `quor doctor` (every check this task could affect reports
  healthy; one pre-existing, unrelated failing check — `flag_low_performers`'s
  negative-compression finding, QB-052/QB-065 — reflects this dev machine's
  own accumulated real-usage tracking data, not anything QB-068 touched).
- Two pre-existing tests needed updating, not because their intent changed
  but because the function they patched moved off the hot path:
  `tests/unit/test_fail_open.py::TestHookTimeout` patched
  `quor.adapters.claude.run_hook` to simulate a hook failure; `__main__.
  _run_hook()` no longer calls that function on the `claude`/`claude-read`
  path (it now calls `ClaudeAdapter.handle_event()`, which calls
  `quor.adapters.claude.handle_bytes()`) — updated to patch `handle_bytes`
  instead, same assertions, same intent.
- `ANTI_GOALS.md` #12 is marked superseded in place (not deleted — it
  remains accurate history for why the codebase was single-agent as long
  as it was). `ROADMAP.md`'s v2.0 multi-agent bullets are updated to
  reflect what actually shipped early (Gemini command-rewriting, adapter
  detection in `quor doctor`) versus what's still unstarted (Cursor,
  Copilot CLI) or blocked on upstream confirmation (Codex's compression
  hook).
- `docs/final/ADAPTERS.md` (new) is the canonical reference for this
  architecture going forward — architecture, lifecycle, extension points,
  current adapter capability matrix with its evidence trail, and a
  step-by-step "adding a new adapter" guide. Supersedes nothing;
  `docs/design/QB-035A-multi-agent-adapter-design.md` remains as the
  original design record, not rewritten.
- Not done, and explicitly out of scope: Cursor and Copilot CLI adapters
  (no research performed — ADR-036 section 10.3's gate was not even
  attempted for either, unlike Codex/Gemini); `quor explain`'s missing
  `CONTENT_INTERCEPT` equivalent (pre-existing gap, unchanged); Gemini's
  `CONTENT_INTERCEPT` support (blocked on upstream confirmation of a
  replace-capable `AfterTool` output field); Codex's compression hook
  entirely (blocked on upstream confirmation of a modify-capable event and
  Windows support).

---

## ADR-041: Universal AI Tool Support — `DetectionOnlyAdapter` Shared Base (QB-069)

**Status:** Decided and shipped
**Date:** 2026-07-29

**Context:**
QB-069 directed extending the QB-068 adapter framework to five more tools —
Cursor, VS Code, Windsurf, Aider, Continue.dev — reusing the shared
architecture, with explicit rules against duplicated logic or copy-paste
adapters, and requiring graceful failure plus documented limitations for
any platform lacking hook capability. Per ADR-036/ADR-040's own established
discipline (§10.3 / QB-005C's mandatory pre-flight compatibility gate), each
tool's actual current hook/extension mechanism was researched live against
its own documentation before any adapter code was written.

**What the research found:** the same answer, five times in a row, that
`CodexAdapter` (QB-068) already established for Codex CLI — **none of these
five tools has a confirmed way to rewrite a command before it runs, or
replace a tool's output before the model sees it**:

- **Cursor** (`.cursor/hooks.json`): `beforeShellExecution`/
  `beforeMCPExecution` are documented allow/deny/ask only
  (`{"continue": bool, "permission": "allow"|"deny"|"ask"}`, no rewrite
  field). No post-execution or post-read hook exists in Cursor's event list
  at all.
- **VS Code** (Copilot agent mode; scoped explicitly to that, not the
  editor generically — vanilla VS Code has no AI agent of its own):
  `PreToolUse`/`PostToolUse` are documented allow/deny/prompt only, with
  the docs stating plainly "no documented mechanism to rewrite/modify tool
  input" and "no documented support" for replacing a tool's result. Windows
  is fully supported — the blocker is the missing modify/replace
  capability, not the platform.
- **Windsurf** (Cascade): has the richest event set of any tool researched
  (`pre_run_command`/`post_run_command`, `pre_read_code`/`post_read_code`)
  but pre-hooks are block-only (exit code 2) and post-hooks are the most
  directly confirmed "no" found in this research — a second, targeted fetch
  of the docs confirmed post-hooks are explicitly documented as
  observational-only ("cannot modify command output or file content"; hook
  stdout is only ever shown in the UI).
- **Aider**: no tool-call hook system at all. `--lint-cmd`/`--test-cmd`
  wrap Aider's own auto-lint/auto-test feature, not a general
  command-interception mechanism.
- **Continue.dev**: `config.yaml`'s own reference documents no `hooks` key
  and no lifecycle-hook mechanism of any kind — only MCP servers
  (agent-optional tool calls) and slash-command prompts, neither able to
  intercept a tool call.

**Decision:**
Six adapters now converging on the identical "no confirmed rewrite/replace
capability" shape (Codex plus these five) is exactly the repeated,
*confirmed* pattern that justifies extraction — not a speculative
abstraction guessed at in advance. Introduced `DetectionOnlyAdapter`
(`quor/adapters/_detection_only.py`): a base class implementing every
`AgentAdapter` method (`supported_events` = empty, `handle_event()` always
returns `None`, `install()` writes nothing and returns one warning built
from a `limitation_reason` class attribute, `doctor_checks()` returns
exactly two always-advisory checks built from a `_detect()` method).
`CodexAdapter` (QB-068) was refactored onto this base — a pure move, not a
behavior change (its own existing tests pass unmodified) — retiring the
duplication before a sixth near-identical copy could exist. Each of the
five new adapters (`CursorAdapter`, `VSCodeAdapter`, `WindsurfAdapter`,
`AiderAdapter`, `ContinueAdapter`) supplies only `agent_id`/`display_name`/
`limitation_reason` (that tool's own specific finding, not a generic
placeholder) and a `_detect()` override — a deterministic, local
filesystem/`PATH` check only, no network calls, no heuristics.

Registered in `AdapterRegistry._builtin_adapters()` alongside the existing
three, requiring no change to `__main__.py`'s routing, `doctor.py`'s
`_check_adapters()` loop, or `init.py`'s `_init_generic_agent()` — all
three were already written generically enough in QB-068 to absorb five more
adapters with zero modification, the concrete proof that the QB-035A/
ADR-036 architecture actually generalizes past a second agent.

**Options considered:**
- **Six separate adapter modules, each independently implementing the same
  no-op shape** (what a naive extension of QB-068's `CodexAdapter` would
  have produced): rejected outright — this is precisely the "no duplicated
  logic, no copy-paste adapters" rule QB-069 stated explicitly, and the
  exact failure mode ADR-036 §1.4 originally flagged (duplicated
  boilerplate compounding with each new adapter) recurring one layer up.
- **A generic "capability-limited adapter" abstraction speculated in
  QB-068**, before a second instance of the pattern existed: rejected at
  the time (QB-068 shipped only `CodexAdapter`, standalone) — CLAUDE.md's
  "no speculative abstractions" rule was respected by waiting for a real
  second occurrence (this task) before extracting, exactly the discipline
  ADR-026/ADR-036 already establish for this codebase.
- **Detection signal shape** (a single config-directory check vs. a
  pluggable multiple-signal check): kept `_detect() -> tuple[bool, str]` as
  the only contract point, deliberately not more prescriptive — Aider's
  adapter needed three independent signals (PATH executable, project
  config, user config) where every other adapter needed exactly one
  directory check; a narrower contract (e.g. a single `Path` field) would
  not have fit Aider without a special case.

**Consequences:**
- Full validation gate green: `ruff check quor/ tests/` clean; `mypy quor/`
  clean (122 source files, up from 116 pre-QB-069); full `pytest` unit
  suite (62 files) green; `pytest -m integration` (7/7) green;
  `tests/benchmarks/test_benchmarks.py` (396+ cases) green, compression
  behavior byte-identical; `quor verify` 204/204; `quor doctor` — all
  twelve new/refactored detection-only check lines (two per adapter × six
  adapters) report healthy on this repo's own dev machine, verified
  directly (same one pre-existing, unrelated QB-052/QB-065
  negative-compression finding as QB-068, untouched by this work).
- `docs/final/ADAPTERS.md` gained a capability matrix row and a dedicated
  research-evidence subsection per new adapter, plus a new
  "detection-only adapters — shared base" section documenting the
  `DetectionOnlyAdapter` contract and why six tools converged on it. The
  "Adding a new adapter" walkthrough was updated to route a future
  contributor to whichever path (real adapter vs. `DetectionOnlyAdapter`
  subclass) matches what their own research finds — not to assume either
  in advance.
- Test suite growth mirrors the code: one shared parametrized suite
  (`TestDetectionOnlyAdapterSharedContract`, six-way parametrized) plus one
  small, `_detect()`-only test file per new adapter (`test_cursor_adapter.py`,
  `test_vscode_adapter.py`, `test_windsurf_adapter.py`,
  `test_aider_adapter.py`, `test_continue_adapter.py`) and one isolated test
  of the base class itself (`test_detection_only_adapter.py`) — the "new
  adapter conformance tests" QB-069 asked for, without duplicating the
  six-times-identical contract behavior six times.
- Not done, and explicitly out of scope: any adapter in this set gaining
  real `COMMAND_INTERCEPT`/`CONTENT_INTERCEPT` support — all six remain
  blocked on upstream confirmation of a modify/replace-capable hook, not on
  effort; re-verify against each tool's own current documentation (not this
  ADR) before ever extending one's `supported_events`, since every finding
  here has an explicit "as researched on 2026-07-29" timestamp and hook
  systems described here are explicitly noted as actively evolving
  (Cursor's own bug tracker shows hooks "intermittently non-functional" as
  of this research; Windsurf's hooks moved host (docs.windsurf.com →
  docs.devin.ai) mid-research, reflecting Cognition's acquisition).

---

## ADR-042: Repository Explorer — Cache-Only Reads, Never Triggers a Rebuild; `quor explore` as an 8th Exempted Command (QB-078)

**Status:** Decided and shipped
**Date:** 2026-07-30

**Context:**
QB-078 asked for a deterministic `quor explore` command family (`find`/`deps`/`used-by`/`file`/
`stats`) letting a developer or AI agent answer repository-structure questions "using Quor's
existing cached repository intelligence" — explicit, repeated constraints: "This feature must
never walk or parse the repository," a <100ms target, "No subprocesses. No filesystem walks. No
parsing. No rebuilding," and no duplication of any parsing/graph-traversal logic already living in
`quor/pipeline/repo_profile/`. Per CLAUDE.md's own "V1 has exactly 6 commands... don't add more
without explicit approval" gate and the identical discipline ADR-037/038/039 already established
for `map`/`symbols`/`graph`, explicit user sign-off was obtained in-session before any CLI code was
written — not assumed granted by the ticket text alone, mirroring those three ADRs' own stated
discipline.

**Decision:**
Introduced `quor/pipeline/repo_profile/explorer.py`, whose sole entry point `load_cache()` reads
the same four on-disk cache files `intel_store.py` already maintains (`state.json`/`profile.json`/
`symbol_facts.json`/`graph_facts.json`) — never calling `ensure_repo_intelligence()`,
`walk_repository()`, or any parser, matching `dashboard.py::build_dashboard()`'s existing
"reads cache only" contract for `quor repo`. Unlike `build_dashboard()`, which deliberately
collapses "no cache ever built" and "cache exists but unreadable" into a single `None` (both need
the same "run `quor map`" guidance, per its own docstring), `load_cache()` keeps the states QB-078's
own UX section calls for distinct: `missing` (`intel_store.state_exists()` is `False`), `corrupted`
(`state.json` or any sibling artifact file present but unreadable — the same signal
`intel.py::ensure_repo_intelligence()` treats as `"corrupted_rebuild"`, just never acted on here),
`stale` (readable, but `schema_version`/`quor_version` differs from the current build — the same
condition `intel.py` calls `"version_rebuild"`, again reported rather than rebuilt), and `fresh`.
`find`'s "symbol not found" and "ambiguous symbol" (more than one match — not an error; QB-078's own
example output renders it as a numbered list at exit code 0) round out the states the spec asked
for.

Five subcommands live in one Typer sub-app (`explore_app`, `quor/cli/commands/explore.py`,
registered via `app.add_typer(explore_app, name="explore", ...)` in `main.py`, with `"explore"`
added to `__main__.py`'s `_CLI_COMMANDS` — the exact omission ADR-037/038/039 each independently
caught and guarded with a regression test):
- `find <name>` — exact-name-only lookup (QB-078's own "no fuzzy matching" constraint) across every
  cached `FileSymbols`; `Exports` in its output reuses `Symbol.is_public` verbatim under the label
  QB-078's spec uses, rather than inventing a second, separately computed "is this exported"
  concept — `Symbol.is_public`'s own existing docstring already defines it as *directly* the export
  mechanism for JS/TS and each other supported language's own closest deterministic analogue.
- `deps <file>` / `used-by <file>` — resolved `import`-kind edges only (`Edge.kind == "import"`,
  `target_file is not None`), filtered directly from the already-resolved `RepoDependencyGraph.edges`
  `graph.py` produced — no new resolution or traversal logic, per QB-078's own "do not duplicate
  graph traversal logic" constraint.
- `file <path>` — per-file symbol/relationship counts plus a `Repository importance` tier
  (`High`/`Medium`/`Low`), computed by tertile-ranking every cached file's connectivity degree,
  itself computed by the new `dashboard.py::connectivity_counts()` — extracted from
  `dashboard.py::_most_connected_files()`'s existing `Counter` walk over `edges` so `quor explore`'s
  full-repository ranking and `quor repo`'s top-10 dashboard listing share one implementation, not
  two.
- `stats` — repository-wide aggregates, reusing `dashboard.py::_largest_modules()` for
  `largest_file` and adding two small, single-purpose `Counter`s (most-imported-file,
  most-referenced-symbol) not already computed elsewhere; `Repository intelligence age` reuses the
  exact `now - last_completed_build` formula `dashboard.py::build_dashboard()` already computes
  inline.

Every subcommand supports `--json` via `explorer_render.py`, one `render_*_json()`/`render_*_text()`
pair per result type (`dataclasses.asdict()` + `orjson`, the same idiom `symbols_render.py`/
`graph_render.py`/`dashboard_render.py` already use) rather than a single polymorphic renderer,
since `dataclasses.asdict()` needs a concrete dataclass type to type-check under mypy. Plain,
deterministic text by default (not `quor repo`'s Rich terminal dashboard) — QB-078 frames this
command for two audiences at once ("developers and AI agents"), the same reasoning `quor map`/
`quor symbols`/`quor graph`'s own fixed-template convention already rests on. Invocations are
tracked under a new `REPO_EXPLORE_FILTER_LABEL` (`quor/tracking/db.py`), excluded from
`filter_divergence.flag_low_performers()`'s low-performer check exactly like the four prior
synthetic labels already are — a reporting command has no "before" blob to compress.

**Options considered:**
- **Auto-refresh via `ensure_repo_intelligence()`, mirroring QB-077's `quor repo`** (the
  newest, most-recently-shipped precedent in this same package) vs. **strict cache-only reads**:
  cache-only was chosen — QB-078's own spec is explicit and repeated ("must never walk or parse,"
  "No subprocesses... No rebuilding," target <100ms) in a way QB-077's "users shouldn't have to
  think about map/symbols/graph" philosophy directly conflicts with for this specific command.
  `quor explore` is deliberately not a sixth member of the auto-refreshing family; it is the one
  repository-intelligence command in this package that stays a pure, read-only lens, matching
  `quor repo`'s *original* QB-076 design (before QB-077 reversed it) rather than `quor repo`'s
  current one.
- **Reusing `dashboard.py::build_dashboard()` directly** vs. **a new `load_cache()`**: rejected —
  `build_dashboard()` collapses missing/corrupted into one `None` by design (documented as
  intentional in its own docstring, since `quor repo` gives both the identical "run `quor map`"
  guidance), which cannot serve QB-078's explicit requirement to distinguish the two with different
  actionable messages.
- **A single polymorphic `render_json(value: object)`** vs. **one typed function per result type**:
  the typed-per-type approach was chosen after the polymorphic version failed to type-check cleanly
  under mypy (`dataclasses.asdict()` requires a concrete dataclass type) — matches every existing
  `*_render.py` module's own one-function-per-artifact convention rather than introducing a new
  pattern.
- **Ranking `Importance` over only files with at least one edge** vs. **every file the last scan
  walked** (`state.fingerprints`): the full fingerprint set was chosen, so a genuinely disconnected
  file (a doc, a config) deterministically lands in the bottom tier by construction rather than
  being excluded from ranking as a special case.

**Consequences:**
- No changes to `intel.py`, `intel_store.py`, or `dashboard.py`'s existing behavior beyond one
  refactor (`_most_connected_files()`'s inline `Counter` walk promoted to `connectivity_counts()`,
  called from both the original call site and `explorer.py` — behavior-identical) — every existing
  `repo_profile`/`quor map`/`quor symbols`/`quor graph`/`quor repo` test and the compression
  benchmark suite pass unmodified.
- `quor explore` cannot, by design, tell a user their repository *content* has changed since the
  last `quor map`/`quor repo` run — only that the cache is missing, corrupted, or built by a
  different Quor version. `Repository intelligence age` (`stats`) is the only staleness signal
  offered; closing that gap would require exactly the walk this command must never perform.
- Real, documented scope narrowing, not silent partiality: `deps`/`used-by` report only resolved
  `import`-kind edges — a `calls`/`inherits`/`overrides` relationship is a real, cached fact but is
  not what this command's spec means by "dependency," and remains fully visible via `quor graph`
  instead.
- See `backlog.md`'s `QB-078` entry for the implementation record (test counts, files touched).

---

## ADR-043: Cross-Platform Claude Hook Launcher (QB-082)

**Status:** Decided and shipped
**Date:** 2026-07-30

**Context:**
`quor init --claude` generated only a PowerShell (`.ps1`) launcher script, registered in
`settings.json` as `powershell -ExecutionPolicy Bypass -File "<path>"` — correct for ADR-017's
Windows-first target, but a real, silent-failure bug on macOS/Linux: neither `powershell` nor
`pwsh` exists on a default install of either, so `quor init --claude` on those platforms wrote a
hook that would fail every single Claude Code PreToolUse/PostToolUse invocation with "command not
found." CI (`ubuntu-latest`/`windows-latest`) never caught this — the Linux leg only proved the
Python package itself installs and unit-tests pass, not that the actual hook mechanism works end
to end, since it too was still PowerShell-only. Surfaced when the project owner tried to install
Quor on their own macOS development machine.

**Decision:**
Windows keeps ADR-017's PowerShell launcher unchanged. macOS/Linux now get a POSIX shell (`.sh`)
launcher instead, registered as `<sh> "<path>"` where `<sh>` is resolved via `shutil.which("sh")`
falling back to `/bin/sh`. The generated script is a thin wrapper exactly like the PS1 one — `exec
"{python}" -m quor hook claude` — `exec` replaces the launcher shell process with Python directly,
so stdin/stdout are inherited as-is with no PowerShell-style read-all-then-pipe dance required. The
real hook logic (`quor/adapters/claude.py`/`claude_read.py`'s `run_hook()`/`handle_bytes()`) is
untouched — 100% platform-independent Python already, never forked or duplicated for this change.

Platform detection is centralized in one new function, `hook_manifest.is_windows()` (`os.name ==
"nt"` — the check that most directly expresses "Windows vs. POSIX process semantics," the actual
axis every call site branches on, rather than `sys.platform`'s more specific and less relevant
`"win32"`/`"cygwin"`/... string). `init.py`/`doctor.py` import the `hook_manifest` *module* rather
than pulling `is_windows`/`POSIX_SHELL` in by name — a `from ... import is_windows` binds a
separate reference in the importing module's own namespace at import time, which a test patching
`hook_manifest.is_windows` later could never reach; going through the module keeps exactly one
patchable source of truth. `ClaudeHookSpec.script_name`/`.template` (`hook_manifest.py`) became
`@property` methods resolving `is_windows()` at *access time* rather than fixed fields resolved
once at spec-construction/module-import time — necessary because the specs (`BASH_HOOK_SPEC`/
`READ_HOOK_SPEC`) are frozen module-level constants built once at import; a fixed-field design would
have frozen the platform decision permanently at that first import, long before any test (or, in
principle, any runtime platform check) could matter.

On POSIX, the freshly-written launcher is also `chmod 0o755`'d — conventional for a shell script,
not strictly required by settings.json's own invocation (which always names the shell explicitly),
but avoids a surprise if a user later runs `./claude-hook.sh` directly. `doctor --fix`'s repair path
(`_repair_hooks()`) needed the identical `chmod` added — found only while implementing this, since
it writes a fresh script via the same primitive `_install_claude()` uses, and would otherwise have
left a *repaired* POSIX hook non-executable while a freshly *installed* one was not.

**Why not simulate the other platform via `os.name`/`sys.platform` monkeypatching in tests:**
Tried first, rejected. CPython's `pathlib.WindowsPath`/`PosixPath` each bake an "only instantiable
on the real matching OS" guard in as a class-body conditional (`if os.name == 'nt': def
__new__(...): raise UnsupportedOperation(...)`) evaluated exactly once, the moment `pathlib` is
first imported — which happens at real interpreter/process startup, long before any test fixture
runs. Patching `os.name` afterward doesn't change which concrete class `Path(...)` builds; it just
makes the "wrong" class's `__new__` start raising while every other code path still constructs a
real, correctly-flavored `Path` on the actual host OS. Since nearly every test in `test_cli.py`
constructs real `Path` objects (`tmp_path`, `platformdirs`-derived paths), this would have broken
in confusing, hard-to-diagnose ways. Tests instead monkeypatch `hook_manifest.is_windows` itself (a
plain function returning a bool) — changing only Quor's own platform decision, leaving every real
filesystem path operation completely unaffected. `tests/unit/test_hook_manifest.py`'s own tests are
the one exception, safely patching `os.name` directly, because that module's code path never
constructs a `Path` at all.

**Gemini deferred, deliberately:** `quor/adapters/gemini_adapter.py` has its own fully independent
copy of the pre-QB-082 Windows-only pattern (own `HOOK_PS1_TEMPLATE`, own hardcoded `powershell`
command). The launcher abstraction here (`is_windows()`, `POSIX_SHELL`, the platform-property
pattern on `ClaudeHookSpec`) is intentionally reusable for Gemini, but Gemini's migration is
deferred to a separate ticket to keep QB-082's scope minimal — `quor init --agent gemini` remains
Windows-only until that follow-up ships.

**CI:** added `macos-latest` to the existing `ubuntu-latest`/`windows-latest` matrix, at full
Python-version breadth (not reduced to one version) — under-testing the real launcher on macOS is
exactly the gap that let this bug ship in the first place, so the extra CI minutes are a deliberate
trade the project owner chose to keep paying rather than risk a silent regression here again. A new
integration test (`tests/integration/test_cli_commands.py`) pipes a synthetic PreToolUse payload
straight through the real, unmocked generated `.sh` launcher via a real `sh` subprocess and confirms
valid `hookSpecificOutput` JSON comes back — the strongest available proof the POSIX path actually
works end to end, not just that unit-level mocks agree with each other.

**Consequences:**
- `quor init --claude`'s user-facing command is unchanged on every platform — the fix is entirely
  internal to launcher generation, matching the ticket's own "no separate CLI commands" constraint.
- `doctor.py` needed zero changes to its *checking* logic (`_check_hook_script`/
  `_check_hook_registered`/`_check_hook_up_to_date` already only ever read `spec.script_name`/
  `spec.template`/`spec.schema_version` generically) — only its `--fix` *repair* path gained the
  `chmod` call described above.
- See `backlog.md`'s `QB-082` entry for the implementation record.

**Superseded in part:** this ADR extends, rather than replaces, ADR-017 — see that entry's own
closing note.
