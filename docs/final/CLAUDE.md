# CLAUDE.md — Quor Project Instructions
## Read this before every coding session.

> This file is the AI coding assistant's working contract for the Quor project.
> When this file conflicts with anything else, this file wins (except PROJECT_BIBLE.md which has ultimate authority on decisions).

---

## Project Overview

Quor is a Python CLI tool that compresses AI coding assistant command output before it enters the context window. It intercepts Claude Code PreToolUse hooks, rewrites commands to route through Quor, applies a ContentMask pipeline, and returns compressed output.

**Package name:** `quor` (CLI commands: `quor` and `qr`)  
**Python version:** 3.11+ required (stdlib `tomllib`)  
**Primary OS:** Windows 10/11 (corporate, no admin rights, pip only)  
**Status:** Pre-implementation. No Python code exists yet. All design decisions are finalized.

Read PROJECT_BIBLE.md for the full product context. Read DECISIONS.md for the reasoning behind every architectural choice. Do not re-derive decisions already made.

---

## Architecture at a Glance

```
Claude Code PreToolUse hook
    → JSON rewrite: "git status" → "quor git status"
    → Quor subprocess runs real command, captures stdout
    → ContentMask pipeline (stages annotate lines, final render applies)
    → Compressed output returned to AI context
    → SQLite + JSONL tracking (background thread)
```

**Core primitive:** `ContentMask` — array of `LineMask(line, Decision, reason, stage)`.  
Decisions: `KEEP` (default), `COMPRESS` (remove in render), `PROTECT` (cannot be overridden).  
Stages annotate. Final render applies. Stages never mutate line content.

**Three operating modes:** `AUDIT` (default) / `OPTIMIZE` / `SIMULATE`

---

## Folder Responsibilities

| Path | Purpose |
|---|---|
| `quor/__main__.py` | Entry point. Version check (3.11+), then routes to hook or CLI. No logic. |
| `quor/cli/main.py` | Typer app. Registers all 6 commands. No implementation. |
| `quor/cli/commands/` | One file per command: init, validate, explain, gain, verify, doctor. |
| `quor/adapters/` | HookAdapter Protocol + Claude adapter. Platform concerns only. |
| `quor/pipeline/mask.py` | ContentMask, LineMask, Decision. Core primitive. |
| `quor/pipeline/engine.py` | Pipeline executor. Orchestrates stages. Enforces PROTECT immutability. |
| `quor/pipeline/content_type.py` | Heuristic content type detection. |
| `quor/pipeline/stages/` | One file per stage. Each implements StageHandler Protocol. |
| `quor/filters/registry.py` | Three-tier lookup (project > user > built-in). |
| `quor/filters/loader.py` | TOML → FilterConfig (Pydantic v2). |
| `quor/filters/trust.py` | Git-tracked file verification for project-local filters. |
| `quor/filters/builtin/` | Built-in TOML filter files. |
| `quor/rewrite/` | Command classifier, rewrite rules, quote-aware shell lexer. |
| `quor/tracking/` | SQLite + JSONL writer. Background thread. |
| `quor/config/` | Pydantic v2 config models and loader. |
| `quor/errors.py` | Exception hierarchy. |

---

## Coding Conventions

**Types:**
- Pydantic v2 for all config models. `model_config = ConfigDict(frozen=True)` on all models.
- `@dataclass(frozen=True)` for internal data structures that don't need validation.
- Use `typing.Protocol` with `@runtime_checkable` for all plugin interfaces.
- Never use `dict` where a typed model exists.

**Error handling:**
- Never use `assert` for validation. Use explicit `if/raise`.
- All exceptions inherit from `QuorError` or its subclasses.
- Every `except` clause is specific. Never use bare `except:`.
- Hook-level code has one top-level `except Exception` guard. Nowhere else.

**Imports:**
- Stdlib imports first. Third-party imports second. Quor-internal imports third.
- Never import `rich` in `__main__.py` hook path — it would appear in hook stdout.
- `tomllib` is stdlib in 3.11+. No conditional import needed.

**Files and paths:**
- Always use `platformdirs` for config and data paths. Never hardcode `~`, `%APPDATA%`, or `/tmp`.
- Always specify `encoding="utf-8"` on `open()`. Windows default (`cp1252`) is unacceptable.
- Store paths in SQLite as `Path.as_posix()`. Backslashes must never appear in stored paths.
- Temp files via `tempfile.mkdtemp()`, never `/tmp` literal.

**Pattern matching:**
- User-defined regex patterns use `regex` package (not `re`) with `timeout=1.0`.
- Internal hardcoded patterns may use `re`.
- Compile patterns once at filter load time, not per-line.

**Strings:**
- `orjson` for all JSON serialization/deserialization. Never `json.dumps/loads`.
- `f-strings` for all string formatting. No `.format()` or `%` formatting.

---

## The Six CLI Commands

These are the ONLY commands that exist in V1. Do not add more without explicit approval.

1. `quor init --claude` — install Claude Code hook. Shows dry-run first, writes atomically (tempfile + rename), runs `doctor` automatically.
2. `quor validate [file]` — validate config. Must complete in <1 second. No subprocess execution.
3. `quor explain <command>` — stage-by-stage trace. Shows what each stage did and why.
4. `quor gain` — token savings summary. Reads from SQLite. Always shows ±20% uncertainty.
5. `quor verify` — run all inline filter tests. Exit code 1 if any test fails.
6. `quor doctor` — health check: hook responding? Tests passing? Schema current? Mode set?

Both `quor` and `qr` are CLI entry points.

---

## The ContentMask Pipeline

Every stage must implement:
```python
class StageHandler(Protocol):
    api_version: int  # Current: 1
    stage_type: str
    
    def can_handle(self, content: str, content_type: str) -> bool:
        """Return False to skip this stage cleanly."""
    
    def apply(self, mask: ContentMask, config: StageConfig) -> ContentMask:
        """Return updated mask. Never mutate input. Never change PROTECT decisions."""
```

**Invariants the engine enforces (not individual stages):**
- `PROTECT` decisions are final. No stage can downgrade a PROTECT to COMPRESS.
- Stages receive the current mask and return a new mask. The engine checks PROTECT invariant after each stage.
- Stage failures skip that stage and log a warning. Pipeline continues.

**Built-in stages:**
- `remove_ansi` — COMPRESS lines that are pure ANSI escape codes after stripping
- `strip_lines` — COMPRESS lines matching `patterns`; PROTECT lines matching `preserve_patterns`
- `deduplicate_consecutive` — COMPRESS consecutive duplicate lines (keep first)
- `group_repeated` — COMPRESS repeated pattern matches, replace with first instance + `(×N)`
- `max_tokens` — COMPRESS lines beyond budget (strategy: `head`, `tail`, or `both`)

---

## Filter Configuration Format

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
  preserve_patterns = ['^FAILED', 'AssertionError', 'Error', 'Exception']

  [[filter.stages]]
  type = "max_tokens"
  limit = 500
  strategy = "tail"

  on_empty = "All tests passed."

[[filter.tests]]
description = "Failures preserved, passes stripped"
input = "PASSED test_login\nFAILED test_logout\n    AssertionError: got False"
must_contain = ["FAILED", "AssertionError"]
must_not_contain = ["PASSED"]
compression_target = 0.5
```

Every new filter must include at least 3 `[[filter.tests]]` entries.

---

## Performance Requirements

| Operation | Target |
|---|---|
| Hook response (parse + rewrite) | <10ms |
| Full pipeline (10,000 lines) | <200ms |
| `quor validate` | <1 second |
| Python startup on Windows (corporate AV) | <300ms (measure; design daemon if not met) |
| Default CI test suite | <30 seconds |

**Hook-path code must not import heavy dependencies.** `__main__.py` in hook mode runs in the AI's request path. Every `import` must be justified. No `rich`, no heavy schema validation, no plugin discovery in the hot path.

---

## Testing Requirements

**Every PR must:**
- Pass `quor verify` (all inline filter tests pass)
- Maintain ≥80% line coverage on `pipeline/` and `filters/`
- Run on Windows via CI (GitHub Actions `windows-latest`)
- Complete in <30 seconds (no `--integration` flag)

**Test isolation:**
- `conftest.py` autouse fixture patches `platformdirs` to return temp directories.
- No test reads from or writes to `~/.config/quor/` or `~/.local/share/quor/`.
- Integration tests (real config, real SQLite) are `@pytest.mark.integration`. Excluded from default CI.

**Test file naming:**
- `tests/unit/test_<module>.py` — unit tests
- `tests/integration/test_<feature>.py` — integration tests (marked)
- `tests/fixtures/commands/` — fixture command inputs/outputs

**What to test:**
- Stage behavior: given this ContentMask, what does this stage return?
- Pipeline: given this input and filter config, what is the rendered output?
- Classifier: given this command string, is it rewritten and how?
- Trust: given this path, is git-tracked check correct?

---

## Safety Rules — Never Violate These

1. **The hook always returns valid output.** `__main__.py` catches all exceptions and returns original.
2. **PROTECT decisions are immutable.** The pipeline engine enforces this. No stage bypasses it.
3. **Meaning preservation.** When uncertain whether to remove a line, keep it.
4. **No network calls in the hook path.** Zero. Not even DNS lookups.
5. **No `rich` in hook path.** It would corrupt hook stdout.
6. **No `assert` for validation.** Use `if/raise`.
7. **No hardcoded `~`, `/tmp`, or `%APPDATA%`.** Use `platformdirs`.
8. **Always `encoding="utf-8"` on `open()`.** No exceptions.
9. **SQLite writes never block the hook.** Background thread only.
10. **Plugin failures log and skip.** Never raise from plugin failure.

---

## Plugin Conventions

Plugins declare entry points in `pyproject.toml`:
```toml
[project.entry-points."quor.compression_stage"]
my_stage = "my_package.stages:MyStage"
```

Plugin classes must:
- Implement `StageHandler` Protocol
- Declare `api_version: int = 1`
- Implement `can_handle()` — return False rather than raising if content is unsuitable
- Never mutate their input ContentMask
- Never call network APIs
- Never read from the user's home directory directly

Plugin failures (import error, validation error, runtime error) are logged as warnings and skipped. Never fatal.

---

## Common Mistakes to Avoid

1. **Using `re` for user patterns.** Always use `regex` with `timeout=1.0`.
2. **Forgetting `encoding="utf-8"` on file open.** CI should lint for this.
3. **Importing `rich` in hook path.** Rich output to stdout corrupts hook JSON.
4. **Setting `PROTECT` in the wrong place.** PROTECT is set by `preserve_patterns` in `strip_lines`. The engine enforces immutability — individual stages don't need to check.
5. **Hardcoding `/tmp`.** Use `tempfile.mkdtemp()`.
6. **Catching bare `except:`.** Always catch specific exception types.
7. **Writing to real config dirs in tests.** The autouse fixture patches platformdirs.
8. **Adding a 7th CLI command.** Don't. V1 has exactly 6.
9. **Returning empty string from pipeline.** `on_empty` handles this — check it's configured.
10. **Storing backslashes in SQLite paths.** Always `Path.as_posix()`.
11. **Mutable defaults in Pydantic models.** Use `Field(default_factory=list)`.
12. **Presenting token counts without ±20% label.** Always include the uncertainty.

---

## Definition of Done

A task is done when:
- [ ] Code implements the requirement from PROJECT_BIBLE.md or IMPLEMENTATION_PLAN.md
- [ ] Inline filter tests pass (`quor verify`)
- [ ] Unit tests pass with ≥80% coverage on changed modules
- [ ] Windows CI passes
- [ ] `quor validate` produces no errors
- [ ] `quor doctor` shows green for any affected component
- [ ] No hardcoded paths, no bare excepts, no `assert` for validation
- [ ] PR description states which requirement is being implemented

---

## PR Expectations

- Title: `[component] brief description` — e.g., `[pipeline] add group_repeated stage`
- Body: Which IMPLEMENTATION_PLAN.md phase and deliverable? What was changed? What edge cases were considered?
- No speculative abstractions. Implement what's required; nothing more.
- Do not amend published commits. Push a new commit.

---

## Backwards Compatibility Rules

- The `quor.compression_stage` entry-point plugin API is stable after V1.0.
- Breaking changes to the plugin API require a major version bump.
- Breaking changes to the TOML filter format require a migration guide in `CONTRIBUTING.md`.
- The SQLite schema changes via migration scripts in `tracking/migrations/`. Never alter the schema directly.
- `quor validate` must accept filters written for any previous version (backwards-compatible validation).

---

## First Implementation Task

See IMPLEMENTATION_PLAN.md Phase 0 for the exact repository setup steps.  
See RESEARCH_COMPLETION.md for the precise first code task.

Do NOT start implementing until:
1. Python startup time on this Windows machine has been measured
2. Claude Code hook invocation mechanism on Windows has been verified
3. `quor` is available on PyPI (register the name)
