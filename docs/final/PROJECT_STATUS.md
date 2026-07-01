# PROJECT STATUS
## Quor — Current State Snapshot

> Last updated: 2026-07-01 (Phase 9 plugin discovery & loading complete)
> Update this document at the start of every implementation session.

---

## Completion Summary

| Area | Status | % Complete | Notes |
|---|---|---|---|
| Research | COMPLETE | 100% | All 5 research documents finalized. Archived. |
| Architecture | COMPLETE | 100% | All decisions made. Documented in DECISIONS.md (25 ADRs). |
| Documentation | COMPLETE | 95% | 10 canonical docs + README.md written. JSON Schema generated (Phase 3). |
| Implementation | IN PROGRESS | 94% | Phases 0–9 complete. Phase 10 (packaging) next. |
| Testing | IN PROGRESS | 94% | 597 tests, ruff+mypy clean. All passing. |
| Packaging | NOT STARTED | 0% | PyPI name available (verified 2026-06-30). Registration pending Phase 10. |

---

## Research Phase (COMPLETE)

All research is done. The research phase produced 5 documents, now archived:

| Document | Location | Status |
|---|---|---|
| zap-analysis.md | docs/archive/research/ | Archived |
| design-review.md | docs/archive/research/ | Archived |
| final-discovery.md | docs/archive/product-discovery/ | Archived |
| competitive-research.md | docs/archive/product-discovery/ | Archived |
| engineering-patterns.md | docs/archive/architecture-exploration/ | Archived |

**The research phase is closed. No additional research is needed before implementation begins.**

The one exception: the three empirical pre-flight checks are observations about the real environment, not research. They must be done, but they are short tasks (minutes), not research sessions.

---

## Architecture Phase (COMPLETE)

All architectural decisions are finalized. The 25 ADRs in DECISIONS.md are the authoritative record.

**Nothing in the architecture is undecided or provisional.** If implementation reveals that a decision was wrong, update DECISIONS.md with the new decision and the reason for the change — do not implement around an ADR without updating it.

**Key finalized decisions:**
- Package name: `quor`
- Core abstraction: ContentMask (KEEP/COMPRESS/PROTECT line-level decisions)
- Config: Pydantic v2, TOML stages-array format
- CLI: exactly 6 commands (`quor` + `qr` entry points)
- Operating modes: AUDIT / OPTIMIZE / SIMULATE
- Persistence: SQLite + JSONL dual tracking
- Plugin system: entry-points (`quor.compression_stage`)
- Trust model: git-tracked project-local filters
- Error model: fail-open at all levels
- Windows-first: PowerShell hook script, `platformdirs`, always `encoding="utf-8"`

---

## Implementation Phase (NOT STARTED)

**No Python code has been written.** The `docs/final/` directory contains planning documents only.

### Pre-implementation blockers

Three things must be done before Phase 0 begins:

**Blocker 1: Python startup time measurement**  
Run on the actual target Windows machine (with corporate AV software active):
```
time python -c "import quor"
```
If >300ms consistently: design the persistent daemon architecture before Phase 2.
**Status: DONE — Python 3.14 startup ~70ms on this machine. No daemon needed.**

**Blocker 2: Claude Code hook mechanism verification on Windows**  
Verify:
- PreToolUse hook fires before command execution (confirm with a test hook script)
- Exact stdin/stdout JSON format on Windows
- Hook timeout budget (empirically measure; documented as 30s but verify)
- Exact `settings.json` location and format on Windows
**Status: DEFERRED to Phase 5 (not required for Phase 0–4)**

**Blocker 3: PyPI name registration**  
Run: `pip index versions quor`
If no result: register immediately at pypi.org.
Do not begin any public work until the name is secured.
**Status: VERIFIED available (2026-06-30). Registration deferred to Phase 9.**

### Implementation phases (IMPLEMENTATION_PLAN.md):

| Phase | Name | Status |
|---|---|---|
| Pre-flight | Empirical checks | COMPLETE (startup: 70ms, name: available) |
| 0 | Repository setup | COMPLETE |
| 1 | ContentMask primitive | COMPLETE |
| 2 | Compression stages | COMPLETE (50 tests, 96% coverage, ruff+mypy clean) |
| 3 | Filter config + registry | COMPLETE (96 tests total, 92% filter coverage, ruff+mypy clean) |
| 4 | Command rewriter | COMPLETE (177 rewrite tests + 323 total, 96% coverage, ruff+mypy clean) |
| 5 | Hook adapter | COMPLETE (34 adapter tests + 357 total, 96% coverage, ruff+mypy clean) |
| 6 | Tracking | COMPLETE (35 tracking tests + 392 total, WAL mode, JSONL+SQLite, ruff+mypy clean) |
| 7 | CLI commands | COMPLETE (21 CLI tests + 413 total, all 6 commands smoke-tested incl. `init --claude` end-to-end, ruff+mypy clean) |
| 7.5 | Pre-Phase 8 hardening | COMPLETE (493 total, see notes below) |
| 8 | Plugin Infrastructure | COMPLETE (560 total, see notes below) |
| 9 | Plugin Discovery & Loading | COMPLETE (597 total, see notes below) |
| 10 | Packaging | NOT STARTED |

---

## Testing Phase (IN PROGRESS)

**597 tests passing** as of Phase 9 complete. All linters clean.

| Module | Tests | Notes |
|---|---|---|
| `quor/pipeline/stages/` | 50 | 96% coverage |
| `quor/filters/` | 96 | 92% coverage |
| `quor/rewrite/` | 177 | lexer, classifier, 100+ fixtures |
| `quor/adapters/` | 34 | hook + dispatcher |
| `quor/tracking/` | 37 | SQLite, JSONL, WAL, 90-day cleanup, GLOB scoping, concurrent writers |
| `quor/cli/commands/` | 35 | init/validate/explain/gain/verify/doctor + collision detection + encoding regression |
| `tests/unit/test_filter_safety.py` | 35 | Error-safety snapshot tests across all 7 built-in filters |
| `tests/unit/test_fail_open.py` | 16 | Chaos tests: corrupt TOML, malformed hook JSON, permission errors, hook timeout, ReDoS |
| Codepage sweep | 6 | cp437/cp1252/utf-8/ascii via TestCodepageSweep |
| `quor/plugins/` | 67 | Plugin Protocol, PluginRegistry (registration, lifecycle, execution, capability queries) |
| `quor/pipeline/plugin_loader.py` | 37 | Entry-point discovery, cache, file:// loader, load report |

**Testing targets from RELEASE_CRITERIA.md:**
- ≥80% coverage on `quor/pipeline/`, `quor/filters/`, `quor/rewrite/` — first two met
- CI on `windows-latest` and `ubuntu-latest` — ✓ configured in `.github/workflows/ci.yml`
- Weekly canary — ✓ configured in `.github/workflows/canary.yml`
- Default test suite completes in <30 seconds — ~16s currently ✓
- 100+ command classifier fixtures — ✓ met in Phase 4

---

## Documentation Phase (90% Complete)

### What exists (as of 2026-06-30):

| Document | Path | Status |
|---|---|---|
| PROJECT_BIBLE.md | docs/final/ | COMPLETE |
| IMPLEMENTATION_PLAN.md | docs/final/ | COMPLETE |
| CLAUDE.md | docs/final/ | COMPLETE |
| CONTRIBUTING.md | docs/final/ | COMPLETE |
| ROADMAP.md | docs/final/ | COMPLETE |
| DECISIONS.md | docs/final/ | COMPLETE |
| ANTI_GOALS.md | docs/final/ | COMPLETE |
| RELEASE_CRITERIA.md | docs/final/ | COMPLETE |
| PROJECT_STATUS.md | docs/final/ | COMPLETE (this file) |
| RESEARCH_COMPLETION.md | docs/final/ | COMPLETE |

### What does not yet exist:

- `README.md` — write in Phase 9 (after Quor actually works)
- `CHANGELOG.md` — write at first release
- JSON Schema — generate in Phase 3 from Pydantic models
- `pyproject.toml` — write in Phase 0
- Built-in filter TOML files — write in Phase 3

---

## Remaining Unknowns

1. **Claude Code hook timeout on Windows.** Documented as 30s. May be shorter in practice. Dispatcher hardened to 25s timeout (returns exit code 124). Canary will detect format changes.
2. **Whether `quor` is still available on PyPI.** Registration deferred to Phase 9. Re-verify before Phase 9 begins.
3. **Plugin API stability under adversarial plugins.** In-memory registry + api_version check implemented and tested. Entry-point scanning (Phase 9) is the remaining risk surface.

**Resolved unknowns (no longer open):**
- ~~Python startup time~~ — measured at ~70ms on this machine. No daemon needed.
- ~~CI platform coverage~~ — `windows-latest` + `ubuntu-latest` both in `.github/workflows/ci.yml`.
- ~~Fail-open behavior under chaos~~ — tested in `test_fail_open.py`: corrupted TOML, malformed JSON, permission errors, hook timeout, ReDoS all degrade safely.
- ~~Filter safety on real error output~~ — all 7 built-in filters verified in `test_filter_safety.py`.
- ~~Concurrent session safety~~ — two-writer WAL test passing. WAL PRAGMA retry loop added to `TrackingDB._connect()`.

---

## Known Blockers

None. Phase 9 (Plugin Discovery & Loading) complete. Phase 10 (Packaging) may proceed.

---

## Phase 7 Notes (for future sessions)

Two real bugs were found and fixed during Phase 7, not just lint/test issues:

1. **Windows console encoding.** Text-mode `sys.stdout`/`sys.stderr` default to the system codepage (cp1252 on this machine), which cannot encode the ✓/✗ glyphs used throughout the CLI and dispatch output — `quor validate` and `quor verify` crashed with `UnicodeEncodeError` on first run. Fixed in `quor/__main__.py::_ensure_utf8_stdio()`, called once in `main()` before the dispatch/CLI branches (the hook branch is untouched — it writes raw bytes via `sys.stdout.buffer` and never goes through text-mode encoding).
2. **`quor init --claude` duplicate-hook bug.** `_hook_already_installed`/`_install_hook_entry` checked the settings.json `command` field for the literal string `"quor hook claude"`, but that field actually holds `powershell -ExecutionPolicy Bypass -File "...claude-hook.ps1"` — the marker never matched, so every re-run would append a duplicate `PreToolUse` entry instead of overwriting the existing one. Fixed by matching on the hook script filename (`claude-hook.ps1`) instead, which is the string actually present in that field. Covered by `TestInit::test_existing_hook_overwritten_not_duplicated` in `tests/unit/test_cli.py`.

Also added `[tool.ruff.lint.flake8-bugbear] extend-immutable-calls = ["typer.Argument", "typer.Option"]` to `pyproject.toml` — ruff's B008 doesn't know `Path`-typed typer defaults are the idiomatic, required pattern (it only auto-exempts immutable-typed params like `str`), so this is the correct general fix rather than per-file-ignores, and will keep applying cleanly to future CLI commands.

The mode system (ADR-009: AUDIT/OPTIMIZE/SIMULATE) remains **display-only** — `quor doctor` and `quor gain` show the configured mode (read from `~/.config/quor/config.toml`, overridable by `QUOR_MODE` env var, default `"optimize"`), but `quor/adapters/dispatcher.py` does not yet branch on it. Wiring real mode-switching behavior into the dispatcher was explicitly deferred — it wasn't part of the Phase 7 CLI-commands deliverable, and changing dispatcher behavior would have been an unscoped risk to the existing passing test suite.

## Pre-Phase 8 Hardening Notes (2026-07-01)

**P0 items completed before Phase 8:**

1. **Error-safety snapshot tests** (`tests/unit/test_filter_safety.py`, 35 tests). Real failing output samples for all 7 built-in filters (git-status, git-diff, pytest, mypy, ruff, cat, generic). Assert error-relevant lines are never marked COMPRESS; rendered output preserves all failure content.

2. **Fail-open chaos tests** (`tests/unit/test_fail_open.py`, 16 tests). Confirmed ADR-018 holds under: corrupted TOML, malformed hook JSON, missing permissions, hook timeout (MemoryError/RuntimeError/TimeoutError), hanging subprocess (exit 124), pathological ReDoS regex (timeout → KEEP, warning emitted).

3. **Hook collision detection** (`quor/cli/commands/init.py`, `doctor.py`). `quor init --claude` now scans existing PreToolUse hooks before writing, warns by name (Zap, RTK, Headroom AI, Comet), defaults confirmation to `False` when conflicts exist. `quor doctor` re-runs same check. Covered by `TestHookCollisionDetection` in `test_cli.py`.

4. **CI on windows-latest and ubuntu-latest** — already existed in `.github/workflows/ci.yml`. Verified. No changes needed.

**P1 items completed:**

5. **Weekly canary** (`.github/workflows/canary.yml`). Monday 08:00 UTC cron. Installs unpinned `@anthropic-ai/claude-code`, verifies hook fires, response is valid JSON with correct rewrite, extra fields preserved, `quor init --claude` writes valid settings.json, full test suite passes.

6. **Codepage/locale sweep** (`TestCodepageSweep`, `TestWindowsEncodingRegression` in `test_cli.py`). Parametrized for cp437/cp1252/utf-8/ascii — all get `reconfigure(encoding="utf-8")` called. ValueError and OSError from reconfigure are suppressed (contextlib.suppress). Phase 7 encoding crash has dedicated regression tests.

7. **Concurrency test on tracker** (`TestConcurrentWrites` in `test_tracking.py`). Two threads × 30 records = 60 expected, all written without data loss. WAL PRAGMA retry loop added to `TrackingDB._connect()` (up to 5 attempts with 50ms backoff) — real bug found: concurrent open of the same SQLite file caused PRAGMA to fail with "database is locked" and the background thread to die silently.

**Regression audit:**
- Phase 7 duplicate-hook bug: already covered by `test_existing_hook_overwritten_not_duplicated`.
- Phase 7 Windows encoding crash: now covered by `TestWindowsEncodingRegression` (4 dedicated tests).

---

## Phase 8 Notes (2026-07-01)

**Public Plugin API** (`quor/plugins/base.py`, `quor/plugins/__init__.py`):

- `Plugin` Protocol (`@runtime_checkable`) with `api_version: ClassVar[int]`, `metadata`, `initialize`, `execute`, `shutdown`. Lifecycle contract: `initialize` may raise `PluginError`; `execute` is fail-open; `shutdown` must never raise.
- `PluginCategory(StrEnum)`: PRE_FILTER → FILTER → POST_FILTER. StrEnum so string comparisons work at runtime.
- `ExecutionMode(StrEnum)`: AUDIT / OPTIMIZE / SIMULATE. Passed in `PluginContext.mode`.
- `PluginContext(frozen, kw_only)`: project_root, mode, session_id, invocation_id. Lean by design — new optional fields may be added without breaking existing plugins.
- `PluginPayload(frozen, kw_only)`: command, raw_output, current_output, content_type, annotations. Helper methods `replace_output()` and `with_annotation()` return new instances.
- `PluginResult(frozen, kw_only)`: payload + was_modified + abort + note. `abort=True` is controlled early termination (not a failure).
- `PluginMetadata(frozen, kw_only)`: required: plugin_id, display_name, version, category. Optional: author, description, priority (default 100), min_quor_version, capabilities tuple.
- `CAPABILITY_*` string constants: advisory vocabulary (not enforcement) in v1. Third-party plugins may define their own namespaced capability strings.
- `kw_only=True` on all four public dataclasses: allows future fields to be added without positional-order breaking changes.
- `QUOR_PLUGIN_API_VERSION = 1`: gating constant. Plugins with mismatched `api_version` are rejected at registration time.

**In-memory `PluginRegistry`** (`quor/plugins/registry.py`):

- Three tiers: project > user > builtin. Same model as `FilterRegistry`.
- `register(plugin, tier)`: validates Protocol conformance and `api_version`. Warns on same-tier duplicate (replaces) and cross-tier shadow (lower-tier accepted but noted as "will never run").
- `unregister(plugin_id, tier)`: returns `True` if removed.
- `get(plugin_id, tier=None)`: direct id lookup; when `tier=None`, returns highest-precedence-tier instance.
- `plugins_for_category(category)`: project → user → builtin, sorted by ascending priority. Equal priority within the same tier preserves registration order (timsort stable sort + dict insertion order guarantee).
- `all_plugins()`: PRE_FILTER → FILTER → POST_FILTER, with same ordering within each category.
- `capabilities() -> frozenset[str]`: union of all capabilities declared by registered plugins.
- `plugins_with_capability(capability)`: filters `all_plugins()` by declared capability; in execution order.
- `initialize_all(ctx)`: calls `initialize()` on each plugin; `PluginError` or any exception disables the plugin (removes from registry, returns plugin_id in failed list). Disabled plugins are permanently absent from all subsequent lookups and pipeline runs.
- `shutdown_all()`: suppresses all exceptions from `shutdown()`.
- `run_pipeline(payload, ctx)`: runs all plugins across all categories, fail-open.
- `run_category(category, payload, ctx)`: runs only plugins in one category, fail-open. Enables the Phase 9 dispatcher to interleave with the ContentMask pipeline: PRE_FILTER → ContentMask stages → POST_FILTER.
- Internal `_execute_plugins(plugins, payload, ctx)`: shared execution logic. Exceptions from `execute()` → warn + pass payload through; `abort=True` → stop chain with no warning.

**Design decision preserved from API review:** `StageHandler` (existing, TOML-configurable, ContentMask-typed) and `Plugin` (new, Python-code, lifecycle-managed) are kept as separate Protocol hierarchies. They serve different use cases and should not be merged.

**No breaking changes to existing behavior:** all 493 pre-Phase 8 tests continue to pass. Phase 8 adds 67 new tests; total 560.

---

## Immediate Next Milestone

**Phase 10: Packaging** — `pyproject.toml` entry-points, PyPI registration, README, release workflow.

**Internal Alpha (v0.1)** target: after Phase 10 (Packaging) is complete.

---

## Phase 9 Notes (2026-07-01)

**`quor/pipeline/plugin_loader.py`** — new module; full entry-point discovery and file:// loader:

- `discover_stage_handlers(use_cache)` — scans `quor.compression_stage` entry-points, validates Protocol + `api_version == 1`, returns `{stage_type: (handler_cls, StageConfig)}`. Fail-open: bad plugins warn + skip; rest continue.
- `discover_plugins(registry, use_cache, tier)` — scans `quor.plugin` entry-points, validates Plugin Protocol + `api_version`, registers into `PluginRegistry`. Fail-open: same contract.
- `get_extra_stage_handlers()` — process-lifetime memo that `filters/registry._build_stage_entry()` calls on any unknown stage type; avoids rescanning on every pipeline run.
- `get_load_report(use_cache)` — full discovery summary (`PluginLoadReport`) used by `quor doctor`. Returns `StageInfo`, `PluginInfo`, and `FailureInfo` dataclasses.
- `load_from_file_uri(uri)` — loads a `StageHandler` from `file:///path/module.py::ClassName`. Validates Protocol and `api_version`; raises `PluginError` on any failure. Not cached; for dev/test use only.
- `invalidate_cache()` — deletes `~/.config/quor/plugin-cache.json`.
- Plugin cache: `~/.config/quor/plugin-cache.json`, JSON via orjson. Keyed by SHA-256 hash of `name==version` pairs for all installed distributions. Both entry-point groups are scanned and written atomically to avoid partial caches.

**`quor/filters/registry._build_stage_entry()`** extended:

- `file://` prefix: loads handler via `plugin_loader.load_from_file_uri()`, uses `StageConfig` base class for config.
- Unknown built-in: falls through to `get_extra_stage_handlers()` before raising `ConfigError`. Known list in the error message now includes third-party stages.

**`quor/adapters/dispatcher.py`** extended — full plugin pipeline:

- PRE_FILTER plugins run on raw captured output before ContentMask filter.
- ContentMask filter runs on PRE_FILTER output (unchanged path if no matching filter).
- POST_FILTER plugins run on ContentMask-filtered output.
- All plugin pipeline steps are wrapped in `try/except Exception`; any failure falls back to the ContentMask-filtered output.
- `PluginRegistry` is created per-dispatch, `discover_plugins()` populates it (file cache makes this fast on warm path), `initialize_all()` is called if any plugins exist, `shutdown_all()` is always called at end.

**`quor/cli/commands/doctor.py`** extended:

- `_check_plugins()`: calls `get_load_report(use_cache=False)`, reports discovered stages and plugins; marks check `False` if any load failures exist. Green when no third-party plugins are installed (expected state during v0.1 development).

**Test fixture** (`tests/fixtures/test_plugin/`):

- `quor-test-stage` — installable package with `NoOpTestStage` (`stage_type = "noop_test"`, `api_version = 1`). Registered in `pyproject.toml` dev deps as `file:./tests/fixtures/test_plugin`. Integration test `TestInstalledTestPlugin` discovers it via real entry-point scanning.

**37 new tests** in `tests/unit/test_plugin_loader.py`; total 597 passing.

---

## How to Update This Document

At the start of every implementation session:
1. Update the completion percentages in the summary table.
2. Update the phase status in the implementation phase table.
3. Note any new blockers or resolved blockers.
4. Update the "Remaining Unknowns" section.

This document does not need to be comprehensive — that's what IMPLEMENTATION_PLAN.md is for. This document is a quick orientation for anyone (including AI assistants) starting a new session on the project.
