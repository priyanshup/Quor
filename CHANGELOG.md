# Changelog

All notable changes to Quor are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0] — Internal Alpha

First public-quality release. Quor is a rule-based command-output
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
- Not yet published to PyPI.

[0.1.0]: https://github.com/priyanshup/Quor/releases/tag/v0.1.0
