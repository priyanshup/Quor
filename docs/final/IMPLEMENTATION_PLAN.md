# IMPLEMENTATION PLAN
## Quor — Phase-by-Phase Roadmap

> This document describes the exact sequence for building Quor.
> Each phase has clear objectives, deliverables, dependencies, and exit criteria.
> Phases are ordered by dependency. Do not begin a phase before its dependencies are complete.

---

## Pre-Implementation Checklist

Before writing any code, three empirical questions must be answered:

**Pre-flight 1: Python startup time on Windows with corporate AV**
```
time python -c "import quor"
```
Target: <300ms. If consistently >300ms, design a persistent daemon before Phase 2.

**Pre-flight 2: Claude Code hook invocation mechanism on Windows**
- Verify the PreToolUse hook fires before command execution
- Verify stdin/stdout JSON format on Windows (PowerShell, not WSL or cmd)
- Verify hook timeout budget (empirically — the docs say 30s but verify)
- Confirm the exact `settings.json` location and format

**Pre-flight 3: PyPI name registration**
- Run: `pip index versions quor` — if no result, name is available
- Register immediately via `twine` / PyPI account before any public work
- Do not proceed to Phase 1 until the name is secured

---

## Phase 0: Repository Setup

**Objective:** Get a clean, building, testable repository from zero.

**Deliverables:**
- [x] `pyproject.toml` with all 6 core dependencies and correct entry-points
- [x] `quor/__init__.py` with `__version__ = "0.1.0.dev0"`
- [x] `quor/__main__.py` — version check (3.11+), routes to hook or CLI. No logic.
- [x] `quor/errors.py` — complete exception hierarchy
- [x] `conftest.py` — autouse isolation fixture (patches platformdirs)
- [x] `.github/workflows/ci.yml` — Python 3.11, 3.12 on ubuntu-latest and windows-latest
- [x] `pyproject.toml` — ruff, mypy, pytest config
- [x] `CONTRIBUTING.md` placeholders

**Exit criteria:**
- [x] `pip install -e .` succeeds on Windows without compilation
- [x] `quor --help` prints without error
- [x] CI green on windows-latest and ubuntu-latest
- [x] `mypy quor/` passes
- [x] `ruff check quor/` passes

**Estimated complexity:** 0.5 days

**Dependencies:** Pre-flight checklist complete

**Risks:**
- A dependency adds a compiled extension we missed. Resolution: check every package on `pypi.org` for Windows wheel availability before adding.

---

## Phase 1: ContentMask Primitive + Pipeline Engine

**Objective:** Build the core abstraction that everything else rests on.

**Deliverables:**
- [x] `quor/pipeline/mask.py` — `Decision` enum, `LineMask` frozen dataclass, `ContentMask` (list of LineMask + render method)
- [x] `quor/pipeline/engine.py` — `Pipeline.execute()`: runs stages in order, enforces PROTECT immutability, handles stage failures (skip + warn)
- [x] `quor/pipeline/stages/base.py` — `StageHandler` Protocol (runtime_checkable), `StageConfig` (Pydantic v2), `StageResult`
- [x] `quor/pipeline/content_type.py` — heuristic content type detector: JSON, ANSI-heavy, Python traceback, diff, plain text

**Unit tests:**
- [x] ContentMask: render() with all-KEEP, all-COMPRESS, mixed, all-PROTECT
- [x] ContentMask: PROTECT lines survive render regardless of Decision
- [x] Pipeline: stage that raises → stage skipped, subsequent stages run
- [x] Pipeline: PROTECT decision from stage N cannot be changed by stage N+1
- [x] Content type: fixture-based tests for each type (JSON blob, pytest output, git diff, etc.)

**Exit criteria:**
- [x] `pytest tests/unit/test_pipeline.py` passes
- [x] Coverage ≥80% on pipeline/
- [x] mypy passes

**Estimated complexity:** 1 day

**Dependencies:** Phase 0 complete

---

## Phase 2: Built-In Compression Stages

**Objective:** Implement all five compression stages that the built-in filters will use.

**Deliverables:**
- [x] `quor/pipeline/stages/remove_ansi.py` — COMPRESS ANSI-only lines using `regex`
- [x] `quor/pipeline/stages/strip_lines.py` — COMPRESS lines matching `patterns`; PROTECT lines matching `preserve_patterns`
- [x] `quor/pipeline/stages/deduplicate_consecutive.py` — COMPRESS adjacent duplicate lines
- [x] `quor/pipeline/stages/group_repeated.py` — collapse N repetitions of a pattern to `message (×N)`
- [x] `quor/pipeline/stages/max_tokens.py` — COMPRESS beyond budget (strategies: `head`, `tail`, `both`)
- [x] Pattern compilation at filter load time (not per-line), using `regex` with `timeout=1.0`

**Unit tests (per stage):**
- [x] Empty input → empty output (no crash)
- [x] Input with no matching lines → all KEEP (no change)
- [x] Input with all matching lines → all COMPRESS
- [x] PROTECT lines survive strip_lines even when matching `patterns`
- [x] group_repeated: exactly N occurrences → `(×N)` suffix on first
- [x] max_tokens: `tail` strategy keeps last N lines
- [x] max_tokens: `head` strategy keeps first N lines
- [x] Pattern with catastrophic backtracking potential → timeout after 1s, warn, skip

**Exit criteria:**
- [x] All stage unit tests pass
- [x] Coverage ≥80% on pipeline/stages/
- [x] Each stage passes mypy

**Estimated complexity:** 1 day

**Dependencies:** Phase 1 complete

---

## Phase 3: Filter Configuration and Registry

**Objective:** Load filter configs from TOML, validate with Pydantic v2, implement three-tier lookup.

**Deliverables:**
- [x] `quor/config/model.py` — Pydantic v2 models: `QuorConfig`, `FilterConfig`, `StageConfig`, `FilterTest`
- [x] `quor/filters/loader.py` — load TOML → `FilterConfig`, validate, raise `ConfigError` on invalid
- [x] `quor/filters/trust.py` — `is_git_tracked(path: Path) -> bool` using `git ls-files --error-unmatch`
- [x] `quor/filters/registry.py` — three-tier lookup: project → user → built-in
- [x] JSON Schema generation: `python -m quor schema` outputs schema to stdout
- [x] All five built-in TOML filter files (git, pytest, build, cat, generic) with ≥3 tests each
- [x] `abort_unless` / `abort_if` short-circuit logic in registry/engine
- [x] `on_empty` handling in pipeline render

**Built-in filter files:**

`git.toml`:
- `git status` — strip untracked section header, unchanged files, empty-line compression
- `git log` — strip format decorations, limit to N recent entries
- `git diff` — strip context lines (keep added/removed), preserve hunks with errors
- `git blame` — strip unchanged-author runs

`pytest.toml`:
- `abort_unless = ["FAILED", "ERROR"]`
- Strip PASSED lines, dot-progress lines, timing lines
- Preserve FAILED, AssertionError, Error, Exception, traceback lines
- `on_empty = "All tests passed."`
- `group_repeated` for repeated warning blocks

`build.toml`:
- `mypy` — strip "Success: no issues found", group repeated error patterns
- `ruff` — strip "All checks passed", keep specific violations

`cat.toml`:
- Strip comment lines (configurable prefix patterns)
- Strip blank line runs (max 1 consecutive blank)
- Preserve all non-comment, non-blank content

`generic.toml`:
- `remove_ansi`
- `max_tokens` with configurable limit (default 1000), strategy `tail`

**Unit tests:**
- [x] Load valid TOML → FilterConfig (no error)
- [x] Load invalid TOML → ConfigError with useful message
- [x] Lookup: project filter overrides built-in
- [x] Lookup: untrusted project filter → warn + skip
- [x] abort_unless: no match → return original
- [x] abort_if: match → return original
- [x] on_empty: empty render → return on_empty string
- [x] All five built-in filters pass their inline tests

**Exit criteria:**
- [x] `pytest tests/unit/test_filters.py` passes
- [x] All inline tests pass (`quor verify` — even though quor CLI is not yet built, the test runner can be invoked directly)
- [x] Coverage ≥80% on filters/
- [x] JSON Schema generated successfully

**Estimated complexity:** 2 days

**Dependencies:** Phase 2 complete

---

## Phase 4: Command Rewriter and Classifier

**Objective:** Build the command rewriting logic that the hook adapter uses.

**Deliverables:**
- [x] `quor/rewrite/lexer.py` — quote-aware shell tokenizer (handles `"arg with spaces"`, `'single quoted'`, `$VAR`)
- [x] `quor/rewrite/rules.py` — ordered `RULES` list: general-to-specific, covering simple commands, compound (`&&`, `||`, `;`, `&`), env prefixes, transparent prefixes (docker exec, sudo, etc.)
- [x] `quor/rewrite/classifier.py` — `classify_command(cmd: str) -> ClassificationResult` and `rewrite_command(cmd: str) -> str | None`

**Exclusion rules (commands that are NOT rewritten):**
- Commands containing heredocs (`<<`)
- Commands piped through `xargs`, `awk`, `sed`
- `gh --json` and similar structured-output flags
- `cat` with flags other than `-n`
- Unknown commands (passthrough)

**Fixture-based tests:**
- [x] 100+ command strings covering: simple, compound, env-prefixed, transparent-prefixed, heredoc, pipe-incompatible, structured-output
- [x] Each fixture: `input`, `expected_rewrite`, `should_rewrite: bool`
- [x] All fixtures are TOML or JSON files in `tests/fixtures/commands/`

**Exit criteria:**
- [x] All 100+ command fixtures pass
- [x] mypy passes on rewrite/
- [x] No single rule in RULES covers more than 20 fixture cases (specificity check)

**Estimated complexity:** 1.5 days

**Dependencies:** Phase 1 complete (does not require Phase 2 or 3)

---

## Phase 5: Claude Code Hook Adapter

**Objective:** Connect the rewriter and pipeline to Claude Code's PreToolUse hook protocol.

**Deliverables:**
- [x] `quor/adapters/base.py` — `HookAdapter` Protocol, `HookInput` (Pydantic), `HookOutput` (Pydantic)
- [x] `quor/adapters/claude.py` — parse Claude Code JSON, call rewriter, return modified JSON
- [x] Top-level try/except in `__main__.py` hook mode: any exception → return original JSON to stdout
- [x] Cursor doubled-BOM handling: strip `\xEF\xBB\xBF\xEF\xBB\xBF` before JSON parsing
- [x] Hook mode does not import `rich` (validated in CI — test that hook stdout is valid JSON after import)
- [x] PowerShell hook script template (for `quor init --claude` to write)

**Hook invocation flow:**
```
stdin: {"tool_input": {"command": "git status"}}
    → parse HookInput
    → rewrite_command("git status") → "quor git status"
    → return HookOutput with modified command field
stdout: {"tool_input": {"command": "quor git status"}}
```

**Hook dispatcher flow (when Claude Code runs `quor git status`):**
```
args: ["git", "status"]
    → run subprocess: ["git", "status"], capture stdout
    → detect content type
    → lookup filter "git"
    → apply ContentMask pipeline
    → write tracking (background thread)
    → print filtered output to stdout
```

**Unit tests:**
- [x] Hook: valid Claude JSON → rewritten JSON
- [x] Hook: invalid JSON → return original stdout (fail-open test)
- [x] Hook: doubled-BOM JSON → stripped correctly before parse
- [x] Dispatcher: command not in registry → original output
- [x] Dispatcher: pipeline raises → original output (fail-open)
- [x] Dispatcher: subprocess fails (exit code non-zero) → original stderr preserved

**Exit criteria:**
- [x] Synthetic hook test: echo JSON to stdin of `quor hook claude`, verify JSON out
- [x] Hook stdout is valid JSON (no rich output contamination)
- [x] Fail-open test: force a pipeline exception, verify hook still returns original
- [x] Windows-specific: test on windows-latest CI

**Estimated complexity:** 1.5 days

**Dependencies:** Phases 3 and 4 complete

---

## Phase 6: Tracking (SQLite + JSONL)

**Objective:** Persist pipeline results for `quor gain` and CI artifact export.

**Deliverables:**
- [x] `quor/tracking/schema.sql` — finalized schema (see DECISIONS.md ADR-008)
- [x] `quor/tracking/db.py` — `TrackingDB`: background thread writer, WAL mode, GLOB project scoping, 90-day cleanup
- [x] JSONL writer: append-only, one JSON object per line, same fields as SQLite
- [x] `quor/tracking/db.py` — `query_gain(project_path: Path, days: int = 30) -> GainReport`
- [x] Schema migration runner (checks `schema_migrations` table on startup)

**Unit tests:**
- [x] Write record → row in SQLite
- [x] Write record → line in JSONL
- [x] Tracking failure → hook still returns output (non-blocking confirmed)
- [x] 90-day cleanup: records older than 90 days removed
- [x] GLOB scoping: records from other projects not included in project gain
- [x] Path stored as POSIX string (no backslashes even on Windows)

**Exit criteria:**
- [x] `pytest tests/unit/test_tracking.py` passes
- [x] On Windows: path stored correctly (no backslash)
- [x] WAL mode confirmed in test (PRAGMA journal_mode)

**Estimated complexity:** 1 day

**Dependencies:** Phase 5 complete

---

## Phase 7: CLI Commands

**Objective:** Implement all six CLI commands. By the end of this phase, Quor is fully usable.

**Deliverables:**

`quor init --claude`:
- [x] Locate or prompt for Claude Code settings.json path
- [x] Show dry-run: "Will write hook script to X. Will update settings.json at Y."
- [x] Write PowerShell hook script with full `sys.executable` path embedded
- [x] Update settings.json atomically (tempfile + rename)
- [x] Run `quor doctor` automatically
- [x] Handle: settings.json doesn't exist → create it. Existing hook → prompt to overwrite.

`quor validate [file]`:
- [x] If file provided: validate that file. If not: validate all three registry tiers.
- [x] Show: filter names, stage counts, test counts, any validation errors.
- [x] Must complete in <1 second. No subprocess execution.
- [x] Exit code 2 on ConfigError.

`quor explain <command>`:
- [x] Classify command: show rewrite decision and which rule matched
- [x] Look up filter: show which tier supplied it
- [x] Run pipeline in SIMULATE mode: show stage-by-stage trace
- [x] For each stage: lines removed, patterns matched, tokens saved
- [x] Use rich Panel and Table for formatting
- [x] Show final: original tokens → filtered tokens (±20%)

`quor gain`:
- [x] Read from SQLite for current project (platformdirs data dir)
- [x] Show: total invocations, total tokens saved (±20%), filter hit rate, top 5 filters by savings
- [x] Show: `on_empty` trigger rate, passthrough rate, mode (AUDIT/OPTIMIZE/SIMULATE)
- [x] `--days N` flag (default 30)
- [x] `--project /path` flag (default: cwd)

`quor verify`:
- [x] Load all three tiers of filter registry
- [x] For each filter: run all `[[filter.tests]]` entries
- [x] Report: passed/failed per test, per filter
- [x] Exit code 1 if any test fails
- [x] Exit code 0 if all pass (even with no tests — but warn if filter has 0 tests)

`quor doctor`:
- [x] Check: Python version ≥ 3.11
- [x] Check: All dependencies importable at their required versions
- [x] Check: Hook script exists and is writable
- [x] Check: Run synthetic hook invocation (echo test JSON, verify JSON response)
- [x] Check: SQLite is readable and writable
- [x] Check: All built-in filter tests pass
- [x] Check: Current mode (warn if AUDIT for >7 days)
- [x] Show: summary with colored status indicators

**Exit criteria:**
- [x] All six commands execute without error
- [x] `quor init --claude` writes correct hook on Windows CI
- [x] `quor doctor` shows all green on clean install
- [x] `quor verify` passes on all built-in filters
- [x] End-to-end test: init → run a command → doctor → gain (on windows-latest)

**Estimated complexity:** 2.5 days

**Dependencies:** Phase 6 complete

---

## Phase 8: Plugin Infrastructure

**Objective:** Build the public Plugin API and in-memory registry that Phase 9 discovery and third-party plugin authors will consume. No entry-point scanning, no dynamic importing, no file loading.

**Deliverables:**
- [x] `quor/plugins/base.py` — `Plugin` Protocol (`@runtime_checkable`, `ClassVar[int] api_version`), `PluginContext` (frozen, kw_only), `PluginMetadata` (frozen, kw_only), `PluginPayload` (frozen, kw_only, with `replace_output()` and `with_annotation()` helpers), `PluginResult` (frozen, kw_only), `PluginCategory(StrEnum)`, `ExecutionMode(StrEnum)`, `CAPABILITY_*` advisory string constants, `QUOR_PLUGIN_API_VERSION = 1`
- [x] `quor/plugins/registry.py` — `PluginRegistry`: `register`, `unregister`, `get`, `plugins_for_category`, `all_plugins`, `capabilities`, `plugins_with_capability`, `count`, `initialize_all`, `shutdown_all`, `run_pipeline`, `run_category`; `_execute_plugins` shared fail-open execution core
- [x] `quor/plugins/__init__.py` — re-exports all public types; `PluginError` re-exported from `quor.errors` so plugin authors never import from internals
- [x] Three-tier registration precedence: project > user > builtin (same model as `FilterRegistry`)
- [x] Deterministic execution order: tier → category (PRE_FILTER → FILTER → POST_FILTER) → ascending priority → registration order for ties (guaranteed by Python's stable timsort + dict insertion order)
- [x] Fail-open contract: `execute()` exceptions caught + warned, payload passes through unchanged; `initialize()` `PluginError` permanently disables the plugin (removes from registry); `shutdown()` exceptions suppressed

**Architecture note:** `Plugin` Protocol and `PluginRegistry` are intentionally a separate abstraction from `StageHandler`. `StageHandler` is TOML-configurable, ContentMask-typed, stateless compression. `Plugin` is Python-coded, lifecycle-managed, higher-level middleware (telemetry, policy, routing, enrichment). Phase 9 wires entry-point discovery for both abstractions. See ADR-026.

**Unit tests:**
- [x] Protocol conformance (satisfies / does not satisfy `isinstance` checks)
- [x] `PluginPayload` helpers: `with_annotation`, `replace_output`, immutability
- [x] Registration: valid, Protocol violation, wrong `api_version`, duplicate (same tier), cross-tier shadow
- [x] Lookup: `get()` by id and by tier; stable ordering (equal priorities); all-three-tiers precedence
- [x] Lifecycle: `initialize` called; `PluginError` disables; unexpected exception disables; disabled plugin absent from `all_plugins()` and `run_pipeline()`; `shutdown` called; `shutdown` exception suppressed
- [x] `run_pipeline`: no plugins, transform, chain order, fail-open, abort, `raw_output` preserved, annotation chaining, `MemoryError` caught
- [x] Capability queries: `capabilities()`, `plugins_with_capability()`, empty registry, execution order preserved
- [x] `run_category`: category isolation from others, no plugins, abort stops within category, fail-open

**Exit criteria:**
- [x] `pytest tests/unit/test_plugins.py` passes (67 tests)
- [x] `mypy quor/plugins/` passes with no errors
- [x] `ruff check quor/plugins/` passes with no errors
- [x] No regressions in the pre-Phase 8 test suite (560 total tests passing)

**Estimated complexity:** 1.5 days

**Dependencies:** Phase 5 complete (does not require Phase 7)

**Status:** COMPLETE (2026-07-01)

---

## Phase 9: Plugin Discovery & Loading

**Objective:** Wire entry-point discovery into `PluginRegistry` and the ContentMask pipeline. Phase 8 built the stable interface; this phase implements the deployment mechanism.

**Deliverables:**
- [x] `quor/pipeline/plugin_loader.py` — discover `quor.compression_stage` entry-points via `importlib.metadata`, validate against `StageHandler` Protocol, register results into the pipeline; register `Plugin`-implementing entry-points into `PluginRegistry`
- [x] Plugin cache: `~/.config/quor/plugin-cache.json`, invalidated when installed package set changes (compare `importlib.metadata` distribution set hash)
- [x] `api_version` compatibility check: warn and skip if plugin `api_version > QUOR_PLUGIN_API_VERSION`
- [x] Plugin failure isolation: any exception during load, import, or validation → log warning, skip plugin; pipeline continues
- [x] `quor doctor` plugin diagnostics: list loaded plugins with their version; report any load failures. Tier is deliberately not reported — entry-point discovery has no signal that maps to project/user/builtin tier without a new discovery mechanism; documented as a permanent scope boundary in DECISIONS.md ADR-026.
- [x] `file://` escape hatch: stages can reference `file:///path/to/module.py::ClassName` (developer convenience only)

**Unit tests:**
- [x] Plugin with correct `api_version` loads successfully
- [x] Plugin with older `api_version` (<= current) loads successfully
- [x] Plugin with `api_version > 1` warns and skips
- [x] Plugin that raises during `apply()` → stage skipped, pipeline continues
- [x] Plugin that fails to import → warning, graceful skip
- [x] Cache: second call uses cached result, does not re-scan entry-points
- [x] `file://` stage: loads module from path, instantiates class, validates Protocol

**Exit criteria:**
- [x] A minimal test plugin (`tests/fixtures/test_plugin/`) loads via entry-points in CI
- [x] Plugin failure test confirms hook still returns valid output — `test_plugin_execute_failure_is_isolated` in `test_adapters.py` registers a real (non-mock) failing `Plugin` and drives it through the actual `run_dispatch()`
- [x] `quor doctor` lists a discovered plugin with its correct version — `test_plugin_diagnostics_include_version` in `test_cli.py` (the installed test fixture is a `StageHandler`, which has no version concept, so this is verified with a `Plugin`-shaped report instead)

**Estimated complexity:** 0.5 days

**Dependencies:** Phase 8 complete

---

## Phase 10: Packaging and Distribution

**Objective:** Get Quor to a pip-installable state that works on a fresh corporate Windows machine.

**Deliverables:**
- [x] `pyproject.toml` complete: all metadata, entry-points, classifiers, Python 3.11+ constraint, `[project.urls]`
- [x] Built-in filter TOML files included in the built wheel (confirmed by inspecting the actual built wheel's contents — hatchling's default `packages = ["quor"]` behavior already includes non-Python files under the package tree; no explicit `include` directive was needed)
- [x] Version bumped to `0.1.0`
- [x] `CHANGELOG.md` with initial release notes
- [x] `README.md` with: one-sentence description, Windows-first callout, installation (`pip install quor`), quick start (5 commands), Troubleshooting section — done; a `quor gain` screenshot was not produced (no graphical capture available in this environment), a text-based expected-output example was used instead for the Quick Start's `quor doctor` step
- [x] TestPyPI upload — via `.github/workflows/publish-testpypi.yml` (manual `workflow_dispatch`), 2026-07-01
- [x] Fresh Windows VM install test — not a VM, but verified via `pip install --index-url https://test.pypi.org/simple/ ...` on 3 separate real machines (personal laptop ×2 Python versions, corporate/office laptop), 2026-07-01
- [x] PyPI upload — tag `v0.1.0` pushed, `.github/workflows/release.yml` published to PyPI automatically, 2026-07-01

**Exit criteria:**
- [x] `pip install quor` on fresh Windows 11 (no prior Quor install) → works — verified on 3 machines against the real PyPI index, 2026-07-01
- [x] `quor doctor` shows all green on fresh install (after `quor init --claude`) — verified on real PyPI installs; a fresh install correctly shows one red item ("Hook script installed") until `quor init --claude` is run, which is expected behavior, not a defect
- [x] `quor init --claude` succeeds on Windows without admin rights — exercised repeatedly throughout development on this non-admin Windows dev environment
- [ ] The full end-to-end flow (install → init → real Claude Code session → gain) works on Windows — the hook contract is verified against crafted payloads (`.github/workflows/canary.yml`) but not against a literal live Claude Code session; still open

**Estimated complexity:** 1 day

**Dependencies:** Phase 7 + 9 complete

---

## Total Estimated Timeline

| Phase | Name | Complexity | Cumulative |
|---|---|---|---|
| Pre-flight | Empirical checks + name registration | 0.5 days | 0.5 days |
| 0 | Repository setup | 0.5 days | 1 day |
| 1 | ContentMask primitive | 1 day | 2 days |
| 2 | Compression stages | 1 day | 3 days |
| 3 | Filter config + registry | 2 days | 5 days |
| 4 | Command rewriter | 1.5 days | 6.5 days |
| 5 | Hook adapter | 1.5 days | 8 days |
| 6 | Tracking | 1 day | 9 days |
| 7 | CLI commands | 2.5 days | 11.5 days |
| 8 | Plugin Infrastructure | 1.5 days | 13 days |
| 9 | Plugin Discovery & Loading | 0.5 days | 13.5 days |
| 10 | Packaging | 1 day | 14.5 days |

**Realistic timeline: 3–4 weeks** (accounting for debugging, Windows-specific issues, and iteration on filter quality).

---

## Implementation Order Notes

**Phases 1–4 can be partially parallelized:**
- Phase 4 (command rewriter) only requires Phase 1 and can start immediately after.
- Phases 2 and 3 are sequential.
- Phase 5 requires both Phase 3 and Phase 4.

**The critical path is:** Pre-flight → 0 → 1 → 2 → 3 → 5 → 6 → 7 → 10

**Phase 4 can start in parallel with Phase 2.**  
**Phase 8 (Plugin Infrastructure) can start in parallel with Phase 7 (after Phase 5).**  
**Phase 9 (Plugin Discovery & Loading) depends on Phase 8.**

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Python startup >300ms on Windows with AV | Medium | High | Measure in pre-flight. Design daemon if needed before Phase 5. |
| `quor` name taken on PyPI | Low | High | Register in pre-flight before writing any code. |
| Hook timeout on Windows | Medium | Medium | Measure empirically. Profile Phase 5 hook path aggressively. |
| Corporate network blocks PyPI in CI | Low | Medium | Use a cached requirements approach. Test on `windows-latest` not self-hosted. |
| Built-in filter over-aggressive (removes needed content) | High | Medium | Conservative defaults. PROTECT patterns for all error-class content. |
| Filter under-aggressive (no meaningful compression) | Medium | Low | Track compression ratios in Phase 6. Tune in built-in filter TOML files. |
| `regex` package missing Windows wheels | Very Low | High | Verify on PyPI before committing to dependency. Has had Windows wheels since 2021. |
