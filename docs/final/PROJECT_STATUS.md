# PROJECT STATUS
## Quor — Current State Snapshot

> Last updated: 2026-07-01 (Phase 7 complete)
> Update this document at the start of every implementation session.

---

## Completion Summary

| Area | Status | % Complete | Notes |
|---|---|---|---|
| Research | COMPLETE | 100% | All 5 research documents finalized. Archived. |
| Architecture | COMPLETE | 100% | All decisions made. Documented in DECISIONS.md (25 ADRs). |
| Documentation | COMPLETE | 95% | 10 canonical docs + README.md written. JSON Schema generated (Phase 3). |
| Implementation | IN PROGRESS | 78% | Phases 0–7 complete. Phase 8 (plugin system) next. |
| Testing | IN PROGRESS | 78% | 413 tests, ruff+mypy clean. All passing. |
| Packaging | NOT STARTED | 0% | PyPI name available (verified 2026-06-30). Registration pending Phase 9. |

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
| 8 | Plugin system | NOT STARTED |
| 9 | Packaging | NOT STARTED |

---

## Testing Phase (IN PROGRESS)

**413 tests passing** as of Phase 7 completion. All linters clean.

| Module | Tests | Notes |
|---|---|---|
| `quor/pipeline/stages/` | 50 | 96% coverage |
| `quor/filters/` | 96 | 92% coverage |
| `quor/rewrite/` | 177 | lexer, classifier, 100+ fixtures |
| `quor/adapters/` | 34 | hook + dispatcher |
| `quor/tracking/` | 35 | SQLite, JSONL, WAL, 90-day cleanup, GLOB scoping |
| `quor/cli/commands/` | 21 | init/validate/explain/gain/verify/doctor via typer CliRunner |

**Testing targets from RELEASE_CRITERIA.md:**
- ≥80% coverage on `quor/pipeline/`, `quor/filters/`, `quor/rewrite/` — first two met
- CI on `windows-latest` and `ubuntu-latest` — not yet configured
- Default test suite completes in <30 seconds — ~5s currently ✓
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

1. **Actual Python startup time on target Windows machine.** Estimate: will be <300ms. Must verify.
2. **Claude Code hook timeout on Windows.** Documented as 30s. May be shorter in practice.
3. **Claude Code `settings.json` format on Windows.** May differ from documented format.
4. **Whether `quor` is still available on PyPI.** Check now: `pip index versions quor`.
5. **Whether Headroom AI works on the target Windows machine.** If it does and its hook adapter works, the build decision should be revisited (contribute vs. build). This was listed as a pre-flight check but was not performed.

---

## Known Blockers

None beyond the three pre-implementation blockers described above.

---

## Phase 7 Notes (for future sessions)

Two real bugs were found and fixed during Phase 7, not just lint/test issues:

1. **Windows console encoding.** Text-mode `sys.stdout`/`sys.stderr` default to the system codepage (cp1252 on this machine), which cannot encode the ✓/✗ glyphs used throughout the CLI and dispatch output — `quor validate` and `quor verify` crashed with `UnicodeEncodeError` on first run. Fixed in `quor/__main__.py::_ensure_utf8_stdio()`, called once in `main()` before the dispatch/CLI branches (the hook branch is untouched — it writes raw bytes via `sys.stdout.buffer` and never goes through text-mode encoding).
2. **`quor init --claude` duplicate-hook bug.** `_hook_already_installed`/`_install_hook_entry` checked the settings.json `command` field for the literal string `"quor hook claude"`, but that field actually holds `powershell -ExecutionPolicy Bypass -File "...claude-hook.ps1"` — the marker never matched, so every re-run would append a duplicate `PreToolUse` entry instead of overwriting the existing one. Fixed by matching on the hook script filename (`claude-hook.ps1`) instead, which is the string actually present in that field. Covered by `TestInit::test_existing_hook_overwritten_not_duplicated` in `tests/unit/test_cli.py`.

Also added `[tool.ruff.lint.flake8-bugbear] extend-immutable-calls = ["typer.Argument", "typer.Option"]` to `pyproject.toml` — ruff's B008 doesn't know `Path`-typed typer defaults are the idiomatic, required pattern (it only auto-exempts immutable-typed params like `str`), so this is the correct general fix rather than per-file-ignores, and will keep applying cleanly to future CLI commands.

The mode system (ADR-009: AUDIT/OPTIMIZE/SIMULATE) remains **display-only** — `quor doctor` and `quor gain` show the configured mode (read from `~/.config/quor/config.toml`, overridable by `QUOR_MODE` env var, default `"optimize"`), but `quor/adapters/dispatcher.py` does not yet branch on it. Wiring real mode-switching behavior into the dispatcher was explicitly deferred — it wasn't part of the Phase 7 CLI-commands deliverable, and changing dispatcher behavior would have been an unscoped risk to the existing passing test suite.

## Immediate Next Milestone

**Phase 8: Plugin System** (`quor.compression_stage` entry-points)

Deliverables (see IMPLEMENTATION_PLAN.md for full spec):
- Entry-point discovery for third-party `StageHandler` implementations
- Plugin validation: `api_version` check, `StageHandler` Protocol conformance
- Plugin failures log and skip — never raise, never break the pipeline
- Discovery caching (avoid re-scanning entry points on every invocation)

**Internal Alpha (v0.1)** target: after Phase 8 (Plugin system) is complete.

---

## How to Update This Document

At the start of every implementation session:
1. Update the completion percentages in the summary table.
2. Update the phase status in the implementation phase table.
3. Note any new blockers or resolved blockers.
4. Update the "Remaining Unknowns" section.

This document does not need to be comprehensive — that's what IMPLEMENTATION_PLAN.md is for. This document is a quick orientation for anyone (including AI assistants) starting a new session on the project.
