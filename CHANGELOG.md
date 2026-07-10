# Changelog

All notable changes to Quor are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.3.0] — Unreleased

- **Added: `PostToolUse`/`Read` hook plumbing (QB-007A).** A new adapter
  (`quor/adapters/claude_read.py`) and a second, additive hook registration
  (`claude-hook-read.ps1`, `hooks.PostToolUse`/matcher `"Read"`) let `quor init --claude` install
  the mechanism document compression is built on. `quor doctor` gains two checks
  (`Read hook script installed`, `Read hook responds correctly`). Existing `PreToolUse`/`Bash`
  behavior is untouched. Shipped alone as a deliberate no-op (always omitted `updatedToolOutput`);
  see QB-007C below for activation. See ADR-034 and QB-007 in `backlog.md`.
- **Added: Markdown and plain-text document filters (QB-007B).** Two new built-in filters —
  `markdown` (`.md`, `.markdown`) and `document-text` (`.txt`, `.rst`) — route by matching a Read
  tool file path against `FilterRegistry` exactly like a Bash command, using only existing stage
  types (`strip_lines`, `deduplicate_consecutive`, `max_tokens`; no new stage types). Headings,
  lists, fenced code block markers, requirement/decision IDs, and TODO/NOTE/WARNING callouts are
  protected via `preserve_patterns`; `max_tokens` is the only actual compression, engaging only
  once a document exceeds its 2000-token budget. Measured 29.5%/18.8% reduction on realistic
  long-document benchmark samples, 0% on short ones (correct, not a bug — see
  `docs/final/COMMAND_SUPPORT.md` §8). Shipped filter-layer-only, not yet reachable from a real
  Read call; see QB-007C below for activation. See QB-007B in `backlog.md` for full detail,
  including two documented, accepted limitations (fenced-code-block interiors are not
  span-protected; a file path containing a space does not match either filter).
- **Added: the Read hook now actually compresses (QB-007C).** `quor/adapters/claude_read.py`
  wires QB-007A's hook to QB-007B's filters via the existing `FilterRegistry`/`Pipeline` — a
  supported `.md`/`.markdown`/`.txt`/`.rst` Read now genuinely returns compressed content via
  `updatedToolOutput` when compression changes something; every other case (unsupported type,
  no-op compression, or any failure) correctly omits it, preserving fail-open. **Found and fixed a
  real bug during implementation:** the built-in `generic` Bash filter matches any non-empty
  string, so without a guard every unsupported Read file type (`.docx`, `.pdf`, `.py`, ...) would
  have been silently routed through a shell-output filter never designed for document content —
  fixed with an explicit, adapter-local filter-name allowlist that also incidentally neutralizes
  QB-007B's documented `cat.md` routing-collision limitation for real Read calls. No tracking/
  `quor gain`/DOCX/PDF/new-dependency work — exactly as scoped. See QB-007C in `backlog.md` for
  full detail.
- **`quor gain` now explains negative-token rows instead of just softening
  their display.** Confirmed via a new invariant test
  (`TestFilterNeverExpandsOutput`) that no built-in filter stage can itself
  expand content — negative rows come from the tee recovery footer (ADR-023)
  or, in principle, a third-party plugin. `GainReport` gained two
  presentation-only derived fields, `gross_savings` and `gross_overhead`
  (`gross_savings − gross_overhead == tokens_saved`, always), computed at
  query time with no new tracking column or schema migration. `quor gain`
  shows a "Compression achieved" / "Recovery/overhead" breakdown and a
  plain-language explanation, but only when at least one invocation actually
  had a negative net — the common case is unchanged. See QB-017 in
  `backlog.md`.
- **Fixed: `npm`/`npx`/`pnpm`/`yarn` never actually executed through the real
  dispatch path on Windows.** These tools ship as `.CMD` shell shims, not
  native `.exe` binaries; `subprocess.run()` without `shell=True` can't
  resolve them via Windows' `CreateProcess`, so every real invocation failed
  with `WinError 2` before any filtering could happen. `run_dispatch()` now
  resolves the executable via `shutil.which()` first, keeping `shell=False`
  (no new shell-injection surface). See ADR-033 and QB-019 in `backlog.md`.
- **Added: benchmark coverage for every built-in filter.** The compression
  benchmark suite (QB-011) covered only 6 of 14 filter categories; `ruff`,
  `eslint`, `npm`, `npx`, `pnpm`, `yarn`, `cat`, and `cat-python` had none.
  All 14 categories now have committed baseline cases (28 total). See
  ADR-032.
- **Added: `docs/final/COMMAND_SUPPORT.md`**, the canonical reference for
  every supported command, which filter handles it, command detection
  rules, and filter precedence — consolidates detail previously scattered
  or missing across README/CLAUDE.md/PROJECT_BIBLE.md.
- Strengthened the AI-assisted Git workflow (`docs/final/CLAUDE.md`) with
  pre-PR benchmark/regression requirements, a review checklist, and a
  release-readiness checklist.
- Test count: 983 (was 614), reflecting the above plus accumulated coverage
  from QB-013 (tee), QB-018 (gain project-identity fix), and QB-019.

## [0.2.1] — 2026-07-04

- **PreToolUse hook now emits the response shape Claude Code actually reads.**
  The hook adapter (`quor/adapters/claude.py`) used to rewrite
  `tool_input.command` in place and echo the whole mutated input payload back
  to stdout. Claude Code only honors `hookSpecificOutput.updatedInput` for
  overriding tool arguments — a bare top-level `tool_input` key is silently
  ignored — so the rewrite never reached execution, and `quor gain` never
  recorded real invocations. The hook now emits
  `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision":
  "allow", "updatedInput": {...}}}`, omitting `updatedInput` entirely when no
  rewrite applies. Verified end-to-end against the real Claude Code binary
  (not just in-process unit tests). See ADR-030 in DECISIONS.md.
- Fixed `.github/workflows/canary.yml`, which still asserted the old
  `tool_input`-echo shape and would have falsely reported a Claude Code
  protocol change on its next scheduled run.
- No user-facing action required beyond upgrading the package — the installed
  PowerShell hook script is unchanged; it invokes `python -m quor hook claude`,
  which now returns the corrected response automatically.

[0.2.1]: https://github.com/priyanshup/Quor/releases/tag/v0.2.1

## [0.2.0] — 2026-07-04

- **Rewritten commands no longer depend on the `quor`/`qr` launcher stubs.**
  The PreToolUse hook used to rewrite `git status` to the bare word
  `quor git status`, which Claude Code would then run by resolving `quor` on
  PATH — hitting the pip-generated `quor.exe`/`qr.exe` console-script
  launcher. Some corporate application-control policies block that launcher
  outright while allowing `python.exe` itself, which made every Quor-rewritten
  command fail on those machines even though `python -m quor` worked fine.
  Rewritten commands now invoke the exact interpreter already running Quor
  (`sys.executable -m quor ...`), generated by a single new helper,
  `get_quor_invocation()` (`quor/rewrite/invocation.py`), so the launcher is
  never on the runtime path — it remains installed only as a manual-use
  convenience (`quor doctor`, `quor init --claude`, etc., typed directly by a
  user in an unrestricted shell).

[0.2.0]: https://github.com/priyanshup/Quor/releases/tag/v0.2.0

## [0.1.1] — 2026-07-02

Documentation-only release. No changes to `quor`'s source code or behavior.

- Reconciled `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, and the
  `docs/final/` canonical status docs with the actual released v0.1.0
  package — removed stale "not yet on PyPI" / "Internal Alpha" language.
- Added a Quick Start and a Troubleshooting section to `README.md`
  (PATH issues, `py` launcher, multiple Python versions, corporate
  `.exe`-execution blocking, Windows path-length limits), based on real
  multi-machine install verification.
- Fixed a documentation bug: `CONTRIBUTING.md` told bug reporters to run
  `quor --version`, which does not exist as a CLI flag; replaced with
  `pip show quor` throughout.
- Documented the actual, now-automated TestPyPI/PyPI release process in
  `CONTRIBUTING.md`'s Release Process checklist.

[0.1.1]: https://github.com/priyanshup/Quor/releases/tag/v0.1.1

## [0.1.0] — 2026-07-01

First public release, published to [PyPI](https://pypi.org/project/quor/).
Quor is a rule-based command-output
optimization and context-compression layer for AI coding assistants: it
runs your command, captures the output, and applies a deterministic,
fail-open filtering pipeline before the output reaches the assistant's
context window.

### Core pipeline (Phases 0-6)

- **ContentMask pipeline** — the `KEEP` / `COMPRESS` / `PROTECT` line-level
  decision model that every compression stage operates on. `PROTECT` is
  immutable once set: no later stage can downgrade it (ADR-003, "Core
  Abstraction — ContentMask").
- **Five built-in compression stages** — `remove_ansi`, `strip_lines`,
  `deduplicate_consecutive`, `group_repeated`, `max_tokens`.
- **Five built-in filters** — `git`, `pytest`, `build` (mypy/ruff), `cat`,
  and a generic ANSI+truncation fallback, each with inline TOML tests.
- **Three-tier filter registry** — project > user > built-in precedence
  (ADR "Filter Registry — Three-Tier Lookup").
- **Command rewriter and classifier** — quote-aware shell lexer, rule-based
  command classification, 100+ fixture-driven tests.
- **Claude Code hook adapter** — intercepts the `PreToolUse` hook, rewrites
  the command to route through Quor, preserves every extra JSON field
  Claude Code sends.
- **SQLite + JSONL dual tracking** — background-thread writes, WAL mode,
  never blocks the hook path (ADR "Persistence — Dual (SQLite + JSONL)").
- **Six CLI commands** — `init --claude`, `validate`, `explain`, `gain`,
  `verify`, `doctor` — plus the `schema` utility command for the filter
  JSON Schema. Both `quor` and `qr` are registered entry points.

### Plugin Infrastructure (Phase 8)

- Public `Plugin` Protocol (`quor.plugins.base`) — `@runtime_checkable`,
  versioned via `QUOR_PLUGIN_API_VERSION`, lifecycle-managed
  (`initialize` / `execute` / `shutdown`).
- `PluginRegistry` — three-tier registration (project > user > builtin),
  deterministic execution order, fully fail-open execution.
- Deliberately kept separate from the existing `StageHandler` Protocol:
  `StageHandler` is TOML-configurable, line-level, stateless compression;
  `Plugin` is Python-coded, lifecycle-managed middleware for telemetry,
  policy, and routing (ADR "Plugin Architecture — Two-Tier Separation").

### Plugin Discovery & Loading (Phase 9)

- Entry-point discovery for both `quor.compression_stage` and `quor.plugin`
  groups via `importlib.metadata`, with a package-set-hash-invalidated
  local cache.
- `api_version` compatibility check accepts any version `<= QUOR_PLUGIN_API_VERSION`
  and rejects only newer ones — plugins built against an older API keep
  working as the API evolves.
- `file://` escape hatch for loading a local `StageHandler` during
  development without packaging it.
- `quor doctor` plugin diagnostics: lists discovered stages and plugins
  (including each plugin's declared version), and flags load failures.
  Tier is deliberately not reported for entry-point-discovered plugins —
  `importlib.metadata` carries no signal that maps to project/user/builtin,
  so this is a documented scope boundary, not a bug (see DECISIONS.md,
  ADR "Plugin Architecture — Two-Tier Separation").
- End-to-end fail-open verification: a real (non-mock) plugin that raises
  during `execute()` is driven through the actual dispatcher, confirming
  the exception is isolated, a warning is emitted, and the hook still
  returns valid output.

### Release Hardening

A dedicated pass to close reliability gaps before packaging:

- Eliminated the last local-machine dependency in the test suite (CLI
  tests previously read the developer's real `~/.claude/settings.json`);
  all tests now inject an isolated settings path.
- `ruff` and `mypy` are exact-pinned in dev dependencies; `pytest`/`pytest-cov`
  use bounded ranges. CI now lints `tests/` as well as `quor/` (ADR
  "Release Hardening — Dev Tooling Version Policy & CI Lint Scope").
- Removed `ExitCode.PLUGIN_ERROR` as dead code — every `PluginError` is
  caught internally by Quor's fail-open contract and never reaches a
  process exit code.
- Python compatibility verified by actually running the full suite (ruff,
  mypy, pytest) on Python 3.11, 3.13, and 3.14 in isolated virtual
  environments, not just static review.

### Testing

- **605 tests passing**, `ruff` and `mypy` clean on both `quor/` and
  `tests/`.
- ≥80% coverage on `quor/pipeline/`, `quor/filters/`, and `quor/rewrite/`
  (93% overall).
- Dedicated chaos/fail-open suite: corrupted TOML, malformed hook JSON,
  permission errors, hook timeout, pathological regex (ReDoS) — all
  degrade to the original, unfiltered output rather than crashing or
  losing data.
- Error-safety snapshot tests across all 7 built-in filters, confirming
  failure-relevant lines are never removed.
- CI on `windows-latest` and `ubuntu-latest`; a weekly canary workflow
  installs unpinned `@anthropic-ai/claude-code` to catch upstream hook
  format changes before users do.

### Known limitations

- The `AUDIT` / `OPTIMIZE` / `SIMULATE` operating-mode system is
  display-only in this release — `quor doctor` and `quor gain` show the
  configured mode, but the dispatcher does not yet branch on it. This is
  an intentional, scoped roadmap item (see PROJECT_STATUS.md), not a bug.
- No `quor --version` flag yet — check the installed version with
  `pip show quor`.

[0.1.0]: https://github.com/priyanshup/Quor/releases/tag/v0.1.0
