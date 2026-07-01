# PROJECT STATUS
## Quor — Current State Snapshot

> Last updated: 2026-07-02 (v0.1.0 published — see Release Notes below)
> Update this document at the start of every implementation session.

---

## Completion Summary

| Area | Status | % Complete | Notes |
|---|---|---|---|
| Research | COMPLETE | 100% | All 5 research documents finalized. Archived. |
| Architecture | COMPLETE | 100% | All decisions made. Documented in DECISIONS.md (28 ADRs). |
| Documentation | COMPLETE | 100% | 10 canonical docs + README, CHANGELOG, LICENSE, CONTRIBUTING, CODE_OF_CONDUCT, SECURITY all written and reconciled against the released package (see Release Notes below). |
| Implementation | COMPLETE | 100% | All 10 phases complete, including packaging. |
| Testing | COMPLETE | 100% | 605 tests, ruff+mypy clean on `quor/` and `tests/`. All passing, fully machine-isolated. Verified on Python 3.11, 3.13, 3.14. |
| Packaging | COMPLETE | 100% | v0.1.0 published to both TestPyPI and PyPI on 2026-07-01. Installed and verified from a real PyPI/TestPyPI index on three separate machines (Python 3.11 and 3.14). |

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

All architectural decisions are finalized. The 28 ADRs in DECISIONS.md are the authoritative record.

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

## Implementation Phase (COMPLETE — Phases 0-10)

**All 10 phases are implemented, tested, and released as v0.1.0.** The pre-implementation blockers below were resolved before Phase 0 began and are kept here as a historical record.

### Pre-implementation blockers (resolved before Phase 0)

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
| 10 | Packaging | COMPLETE — v0.1.0 published to PyPI 2026-07-01 (see Release Notes below) |

---

## Testing Phase (IN PROGRESS)

**605 tests passing** as of the Phase 9 completion pass (see notes below); all linters clean on `quor/` and `tests/`. The module breakdown below is a snapshot from Phase 9's initial completion and does not sum exactly to the current total — see the Completion Summary at the top of this document for the current total.

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
- ≥80% coverage on `quor/pipeline/`, `quor/filters/`, `quor/rewrite/` — all three met (93% overall)
- CI on `windows-latest` and `ubuntu-latest` — ✓ configured in `.github/workflows/ci.yml`
- Weekly canary — ✓ configured in `.github/workflows/canary.yml`
- Default test suite completes in <30 seconds — ~16s currently ✓
- 100+ command classifier fixtures — ✓ met in Phase 4

---

## Documentation Phase (100% Complete)

### What exists (as of 2026-07-02):

| Document | Path | Status |
|---|---|---|
| PROJECT_BIBLE.md | docs/final/ | COMPLETE |
| IMPLEMENTATION_PLAN.md | docs/final/ | COMPLETE |
| CLAUDE.md | docs/final/ | COMPLETE |
| CONTRIBUTING.md | repository root (moved from docs/final/ so GitHub's Community Standards checklist detects it; docs/final/CONTRIBUTING.md is now a pointer) | COMPLETE |
| ROADMAP.md | docs/final/ | COMPLETE |
| DECISIONS.md | docs/final/ | COMPLETE |
| ANTI_GOALS.md | docs/final/ | COMPLETE |
| RELEASE_CRITERIA.md | docs/final/ | COMPLETE |
| PROJECT_STATUS.md | docs/final/ | COMPLETE (this file) |
| RESEARCH_COMPLETION.md | docs/final/ | COMPLETE |
| README.md | repository root | COMPLETE |
| CHANGELOG.md | repository root | COMPLETE (v0.1.0 entry) |
| LICENSE | repository root | COMPLETE (Apache-2.0) |
| CODE_OF_CONDUCT.md | repository root | COMPLETE |
| SECURITY.md | repository root | COMPLETE |

### What does not yet exist:

Nothing outstanding. TestPyPI/PyPI publishing and install verification are
both done (see Release Notes below); a first-time-user documentation pass
was completed afterward to reconcile README/CHANGELOG/CONTRIBUTING/SECURITY
against the actual released package and CLI behavior.

---

## Remaining Unknowns

1. **Claude Code hook timeout on Windows.** Documented as 30s. May be shorter in practice. Dispatcher hardened to 25s timeout (returns exit code 124). Canary will detect format changes.
2. **Full end-to-end verification against a live Claude Code session.** Install, `quor init --claude`, and `quor doctor` are all verified against the real PyPI/TestPyPI package on three machines; the hook contract itself is verified against crafted payloads (`.github/workflows/canary.yml`), but not yet against an actual live Claude Code session invoking it.

**Resolved unknowns (no longer open):**
- ~~Whether `quor` is available on PyPI~~ — registered and published; `pip install quor` works (verified 2026-07-01).
- ~~Python startup time~~ — measured at ~70ms on this machine. No daemon needed.
- ~~CI platform coverage~~ — `windows-latest` + `ubuntu-latest` both in `.github/workflows/ci.yml`.
- ~~Fail-open behavior under chaos~~ — tested in `test_fail_open.py`: corrupted TOML, malformed JSON, permission errors, hook timeout, ReDoS all degrade safely.
- ~~Filter safety on real error output~~ — all 7 built-in filters verified in `test_filter_safety.py`.
- ~~Concurrent session safety~~ — two-writer WAL test passing. WAL PRAGMA retry loop added to `TrackingDB._connect()`.
- ~~Plugin API stability under adversarial plugins~~ — in-memory registry + api_version compatibility check implemented and tested (older/current/newer versions); entry-point scanning (Phase 9) is complete, including an end-to-end fail-open integration test through the real dispatcher path (`test_plugin_execute_failure_is_isolated`).

---

## Known Blockers

None. v0.1.0 is published to PyPI and TestPyPI (2026-07-01).

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

**v0.1.0 is released.** `pyproject.toml` entry-points, PyPI registration, README, and the release workflow are all done and published. The next milestone is **v0.5 — Public Alpha** (see ROADMAP.md): Windows + Linux CI already exist from v0.1, so the remaining gap is broader real-world usage (multiple non-builder developers) and the additional Public Alpha gates in RELEASE_CRITERIA.md.

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

## Release Hardening Phase Notes (2026-07-01)

Paused feature work to address technical debt surfaced while investigating a Ruff CI failure (SIM105). No new features, no Plugin API changes, no architecture changes. See ADR-027 in DECISIONS.md for the tooling-policy decisions.

- **Test isolation:** `quor/cli/commands/doctor.py::_check_hook_collision()` hardcoded `Path.home() / ".claude" / "settings.json"`, so 8 tests in `tests/unit/test_cli.py` silently depended on whichever real hooks happened to be installed on the developer's machine. `_check_hook_collision()` and `doctor()` now accept an injectable `settings_path` (hidden `--settings-path` CLI option, mirroring the existing pattern in `init.py`); `init --claude` threads its own resolved settings path into the `doctor` run it triggers at the end. Production behavior is unchanged — only the default (`Path.home()/.claude/settings.json`) is now overridable. 3 of those 8 tests had been asserting `exit_code == 0` for cases with a genuine unresolved third-party hook conflict — a pass that only ever happened because the bug meant `doctor` was silently checking the wrong file. Corrected to expect `ExitCode.GENERAL_ERROR`, matching doctor's actual (and intentional) contract.
- **CI/local tooling parity:** `ruff` and `mypy` are now exact-pinned in `pyproject.toml` dev extras (`ruff==0.15.20`, `mypy==2.1.0`) — these are the tools whose point releases most often add new lint rules or stricter checks, which is what caused the original SIM105 incident. `pytest`/`pytest-cov` use bounded ranges instead, since test-runner releases are comparatively stable.
- **CI test linting:** `ruff check tests/` had never been run in CI (only `quor/` was linted), so 45 lint violations had silently accumulated in `tests/`. Fixed all of them (safe auto-fixes plus manual fixes for ambiguous-unicode assertions, nested `with` blocks, and three blind `except Exception`/`pytest.raises(Exception)` asserts narrowed to the actual exception types raised). One fix (`test_tracking.py::test_tracking_failure_does_not_raise`) had been silently vacuous — it built a record but never called `db.record()` — and now genuinely exercises the fail-open write-error path. `ci.yml` now runs `ruff check quor/ tests/`.
- **Python version policy:** local dev on this machine runs 3.14, while CI only tests 3.11/3.12 (the versions in `pyproject.toml` classifiers). `doctor.py` already carries a `_FakeStdout` workaround specifically for a 3.14 stdout.buffer behavior change — evidence 3.13/3.14 aren't actually vetted yet, just incidentally installable. Recommendation: keep v0.1 scoped to 3.11/3.12 only; CI already reflects this, so no CI change was needed. `pyproject.toml`'s `requires-python` was deliberately left unbounded above rather than capped, since capping it would break `pip install -e ".[dev]"` on this contributor's own 3.14 environment — see ADR-027 for the tradeoff and a follow-up recommendation.

---

## Phase 9 Completion Pass Notes (2026-07-01)

A follow-up implementation audit found that two Phase 9 items didn't actually match the implementation plan or its exit criteria, despite being marked COMPLETE. This pass closed both gaps and reconciled documentation drift found during the same audit:

1. **`api_version` compatibility check was stricter than specified.** The plan calls for accepting `api_version <= QUOR_PLUGIN_API_VERSION` and rejecting only newer versions; the code used `!=`, rejecting older versions too. Fixed at all four check sites: `_load_stage_handler_cls`, `_load_plugin_cls`, `load_from_file_uri` (all in `plugin_loader.py`), and `PluginRegistry.register()`. Each now guards with `isinstance(api_version, int)` before comparing — switching `!=` to `>` would otherwise raise `TypeError` (breaking the fail-open contract) if a malformed plugin declared a non-int `api_version`. 8 new tests cover older/current/newer/non-int `api_version` across all four sites.
2. **`quor doctor` plugin diagnostics didn't show version, and tier was never implemented.** `_check_plugins()` now renders `plugin_id@version` (the version was already captured in `PluginInfo` but never displayed). Tier is *not* implemented: entry-point discovery has no signal from Python packaging metadata that maps to "project/user/builtin" — inventing one would mean designing a new discovery mechanism, which is out of scope for a gap-closing pass. Documented as a deliberate, permanent scope boundary in DECISIONS.md (ADR-026 "Known scope gap" note), not a deferred TODO.
3. **No test exercised the dispatcher's plugin fail-open path end-to-end.** Added `test_plugin_execute_failure_is_isolated` in `test_adapters.py`: registers a real `Plugin` (not a mock) that raises in `execute()`, drives it through the actual `run_dispatch()` (subprocess mocked only at the OS boundary), and asserts the warning is emitted, output is unchanged, and the correct exit code is returned.

**Documentation reconciled:**
- `ROADMAP.md`: "Plugin Discovery & Loading" moved from v0.5's "what ships" / v0.1's "what does NOT ship" to v0.1's "what ships" — `IMPLEMENTATION_PLAN.md` already gated Phase 10 (v0.1) on Phase 9 being complete, so the two documents disagreed about which release the plugin system belongs to. It shipped in v0.1; the roadmap now says so.
- `PROJECT_STATUS.md` (this file): ADR count (25 → 27), test total (597 → 605), removed a stale "No Python code has been written" header left over from before Phase 0, and resolved a stale "Remaining Unknown" that still described Phase 9 as pending.
- `DECISIONS.md`: added the tier scope-boundary note to ADR-026 (see above). No new ADR needed — this is a boundary on an existing decision, not a new one.

**8 new tests** across `test_plugin_loader.py` (+4), `test_plugins.py` (+2), `test_cli.py` (+1), `test_adapters.py` (+1); total 605 passing.

---

## Final Pre-Release Cleanup Notes (2026-07-02)

Closed the small remaining technical-debt items ahead of Phase 10. No new features, no architecture changes, no test count change (605 passing, 2 tests modified in place).

- **Removed `ExitCode.PLUGIN_ERROR` (dead code).** Traced every `PluginError` raise site in the codebase — all are caught internally (`plugin_loader.py`, `PluginRegistry`) and converted to a warning + skip, or re-wrapped into `ConfigError` before reaching a CLI exit boundary. `QuorError.exit_code` is never read anywhere in the codebase for any subclass. `PluginError` now carries the default `GENERAL_ERROR`.
- **Modernized the last two `Path.home()`-patching tests.** `test_doctor_reports_collision` and `test_doctor_no_collision_when_settings_missing` in `test_cli.py` now use `--settings-path` injection like the rest of the file. This also fixed a latent bug: the collision test wrote its fixture to `tmp_path/settings.json` while patching `Path.home()` to `tmp_path`, but `doctor`'s default path is `Path.home()/.claude/settings.json` — the fixture was never actually at the path being checked, so the collision was never really being detected. The assertion (checking for the substring `"conflicting"`) passed anyway, because that word is in the check's label text regardless of pass/fail. Now asserts `exit_code` and the actual failure-detail string.
- **Python version compatibility resolved by real execution, not just static review.** This closes the follow-up ADR-027 explicitly left open ("evidence 3.13/3.14 have not been systematically vetted, only incidentally exercised"). Created actual Python 3.11 and 3.13 virtual environments (via `uv venv --python <version>`) alongside the existing 3.14 development environment, and ran `ruff check quor/ tests/`, `mypy quor/`, and the full pytest suite in each — all three identical: clean lint/types, 605/605 tests passing. `requires-python` remains unbounded above (no incompatibility found to justify capping it). Python 3.12 was not independently re-verified locally (already covered by every GitHub Actions run) but sits directly between two verified points. See ADR-027's "Update" note for the full record.
- **Incidental discovery (not acted on):** installing dev extras on Python 3.11 initially failed with an old bundled pip (24.0) — `InvalidRequirement: Invalid URL given` — because it couldn't parse the dev extra's relative `file:./tests/fixtures/test_plugin` URL. Upgrading to pip 26.1.2 fixed it immediately. Not a Python-version incompatibility; flagged in case a contributor hits it with a stale cached pip.

---

## Release Preparation Notes (2026-07-02)

Closed every remaining item from the release-readiness review ahead of the
v0.1.0 tag. No architecture changes; no new runtime functionality beyond
what the review explicitly called for.

- **Version bumped `0.1.0.dev0` → `0.1.0`** in `pyproject.toml` and `quor/__init__.py`. A `.devN` suffix means `pip install quor` would not resolve to it by default (PEP 440) — publishing without this fix would have made the documented install command fail for ordinary users.
- **`LICENSE` created** (Apache-2.0, matching the `license` field already declared in `pyproject.toml`). `README.md`'s `[LICENSE](LICENSE)` link was previously dead.
- **`[project.urls]` added** to `pyproject.toml`: Homepage, Repository, Issues, Documentation, Changelog — all confirmed present in the built wheel's `METADATA` after the change.
- **Positioning unified** across `README.md`, `pyproject.toml`'s `description`, and the CLI's Typer `help=` text and root-callback docstring (`quor/cli/main.py`) — all now describe Quor as "a rule-based command-output optimization and context-compression layer that reduces unnecessary LLM context while preserving important information," avoiding any wording that implies bypassing corporate/enterprise controls.
- **`quor[dev]` packaging fixed** — see ADR-028. The relative `file://` dev dependency is no longer published; it's installed as a separate step in CI and documented in `CONTRIBUTING.md`.
- **Classifiers updated** to include `Programming Language :: Python :: 3.13` and `3.14` (both verified by real execution in the prior cleanup pass — 3.12 was already listed and is covered by CI).
- **`CHANGELOG.md` created** with a full v0.1.0 entry covering all phases, Release Hardening, Plugin Infrastructure, Plugin Discovery, testing milestones, and known limitations.
- **GitHub community files added at the repository root** (where GitHub's Community Standards checklist looks): `CONTRIBUTING.md` (moved from `docs/final/`), `CODE_OF_CONDUCT.md` (new, Contributor Covenant 2.1), `SECURITY.md` (new, scoped to this project's actual fail-open/no-network design).
- **`.github/workflows/release.yml` added** — tag-triggered (`v*`): builds wheel + sdist, verifies with `twine check`, verifies the tag matches `pyproject.toml`'s version, attaches artifacts to a GitHub Release, and has a `publish-pypi` job gated behind a `pypi` GitHub environment and a `PYPI_API_TOKEN` secret. Without that secret configured, the job fails fast with an explicit error rather than silently publishing or silently skipping.
- **README additions:** a "Performance & Token Reduction" section (mechanism, why reduction varies, what compresses well vs. what's preserved, a benchmark methodology, and a results table explicitly marked "To be measured" — no invented numbers) and a "Roadmap: Observability (Planned)" section clearly marked as not-yet-implemented (compression statistics, before/after preview, dry-run mode, verbose diagnostics).
- **Documentation consistency:** checked off 140 previously-unchecked `[ ]` deliverable/test/exit-criteria boxes in `IMPLEMENTATION_PLAN.md` for the Pre-Implementation Checklist through Phase 7 — all of those phases have been independently confirmed COMPLETE in this same document's phase table for several sessions, but the checkboxes themselves had never been updated to match. ADR count references updated to 28 throughout (see ADR-028). `quor schema`'s existence as a 7th, exempted utility command is now noted in `CLAUDE.md` and `quor/cli/main.py`'s module docstring, and added to the README's command table.

---

## Release Publication Notes (2026-07-01 — 2026-07-02)

Closed out Phase 10 by actually publishing, rather than just preparing to publish.

- **`.github/workflows/publish-testpypi.yml` added.** A manual (`workflow_dispatch`) workflow, independent of the tag-triggered `release.yml`, that builds, verifies (`twine check`, an input-version-vs-`pyproject.toml` check), and publishes to TestPyPI. Lets a release be dry-run validated without pushing the real version tag.
- **TestPyPI publish validated**, then **v0.1.0 tagged and released to real PyPI** via the existing `release.yml` (build → GitHub Release → `publish-pypi` job, gated on the `pypi` environment's `PYPI_API_TOKEN` secret).
- **Installed and verified from the published index on three separate machines:**
  - Personal laptop, Python 3.11 (clean venv) and Python 3.14 — both pass, including `quor doctor`.
  - Corporate/office laptop, Python 3.14 — passes, but required `python -m pip` / `python -m quor` instead of the `pip.exe`/`quor.exe` wrapper scripts directly, because this machine's endpoint-protection policy blocked execution of the wrapper executables even though the interpreter itself was permitted. Documented as a Troubleshooting entry in `README.md`.
  - Incidentally confirmed Windows' 260-character path limit can silently break a venv install if the venv directory is deeply nested — also added to `README.md` Troubleshooting.
- **First-time-user documentation pass** (this pass): reconciled `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`, and this document against the actual released package and real CLI `--help` output (captured directly from the published `0.1.0` wheel). Found and fixed one genuine doc bug: `CONTRIBUTING.md` told bug reporters to run `quor --version`, which doesn't exist as a flag — replaced with `pip show quor` everywhere it was referenced. Added a README Quick Start and Troubleshooting section (PATH issues, `py` launcher, multiple Python versions, corporate AppLocker-style `.exe` blocking, path-length limits). No application code was changed.

---

## How to Update This Document

At the start of every implementation session:
1. Update the completion percentages in the summary table.
2. Update the phase status in the implementation phase table.
3. Note any new blockers or resolved blockers.
4. Update the "Remaining Unknowns" section.

This document does not need to be comprehensive — that's what IMPLEMENTATION_PLAN.md is for. This document is a quick orientation for anyone (including AI assistants) starting a new session on the project.
