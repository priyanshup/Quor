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
**Status:** Phases 0-10 complete (614 tests passing, ruff+mypy clean). v0.2.1 published to PyPI (2026-07-04; first released as v0.1.0 on 2026-07-01). See PROJECT_STATUS.md for the current session snapshot.

Read PROJECT_BIBLE.md for the full product context. Read DECISIONS.md for the reasoning behind every architectural choice. Do not re-derive decisions already made.

---

## Architecture at a Glance

```
Claude Code PreToolUse hook
    → JSON rewrite: "git status" → "<python> -m quor git status"
      (interpreter invocation via get_quor_invocation(), not the quor/qr
      launcher — see DECISIONS.md ADR-029)
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

These are the ONLY filtering-operation commands that exist in V1. Do not add more without explicit approval.

1. `quor init --claude` — install Claude Code hook. Shows dry-run first, writes atomically (tempfile + rename), runs `doctor` automatically.
2. `quor validate [file]` — validate config. Must complete in <1 second. No subprocess execution.
3. `quor explain <command>` — stage-by-stage trace. Shows what each stage did and why.
4. `quor gain` — token savings summary. Reads from SQLite. Always shows ±20% uncertainty.
5. `quor verify` — run all inline filter tests. Exit code 1 if any test fails.
6. `quor doctor` — health check: hook responding? Tests passing? Schema current? Mode set?

`quor schema` also exists as a 7th, exempted utility command (JSON Schema dump for the filter TOML format) — it's not a filtering operation, so it doesn't count against the six.

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
- `truncate_lines` — cap KEEP line length to `max_length`, appending `marker`; PROTECT exempt
- `regex_replace` — apply ordered regex substitution `rules` (capture groups supported) to KEEP lines
- `match_output` — if the whole rendered output fullmatches `pattern`, collapse to `summary`; refuses to fire if any PROTECT line is present
- `python_ast_summarize` — COMPRESS function/method body lines (stdlib `ast` parse only; never rewrites/reformats kept lines); fails open (propagates parse errors to the engine's per-stage fail-open) on any non-Python or invalid-syntax input (QB-005)

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

## Mandatory Engineering Rules

These formalize practice that was already mostly followed, after a retrospective test-coverage
audit (QB test-hardening pass) found real gaps in otherwise-shipped features — including one actual
production bug (tee's `write_tee()` silently corrupting output via Windows text-mode newline
translation) that a boundary test caught. Non-negotiable going forward:

**Rule 1 — Test requirement (non-optional).** Every new feature must include:
- Unit tests covering core logic correctness.
- Regression tests, if the feature introduces or changes behavior — a test that fails without the
  change and passes with it.
- Boundary cases: empty input, minimum/maximum valid values, and at least one large/unusual input
  where a stage or feature's normal test fixtures wouldn't otherwise exercise it.

A feature is not complete without these, regardless of how small it looks. `quor verify` passing is
not a substitute — inline filter tests cover filter *configuration* behavior, not the underlying
stage/module logic in isolation.

**Rule 2 — Pre-PR validation gate.** In addition to the "Every PR must" list above (which already
requires `quor verify` and the coverage/CI bar): `ruff check quor/ tests/` and `mypy quor/` must also
both be clean before any PR, every time — not just on CI's final run. And no exceptions on any of
these gates — a known-flaky test is not an acceptable reason to skip it silently. If a test is
genuinely flaky, that is itself a bug to fix or an explicitly documented, reviewed exception (e.g. a
`pytest.mark.skip(reason=...)` with the reason stated), never a silent re-run-until-green.

**Rule 3 — Behavior lock principle.** Any bug fix, or any change to existing behavior:
- Must add a regression test that fails on the pre-fix code and passes on the fix.
- Must be written so a future, unrelated change cannot silently reintroduce the same bug — prefer
  asserting the *observable* outcome (e.g. rendered output, on-disk bytes) over an internal
  implementation detail that could be refactored around without re-breaking the real behavior.

**Rule 4 — Competitor-first design.** For every non-trivial feature (new architecture, new pipeline
primitive, new ecosystem support, new parser, new compression strategy, new runtime capability, or
any feature requiring design decisions):
1. First consult Quor's existing competitor and landscape analysis documents already in the repo
   (`docs/archive/research/zap-analysis.md`, `docs/archive/product-discovery/competitive-research.md`,
   `docs/archive/product-discovery/final-discovery.md`,
   `docs/archive/architecture-exploration/engineering-patterns.md`, and any other archived research —
   see `docs/final/PROJECT_STATUS.md`/`RESEARCH_COMPLETION.md` for the full index).
2. Reuse existing conclusions wherever possible. Do not repeat research that already exists.
3. If the available notes are insufficient to confidently choose an implementation approach, perform
   additional research before writing code.
4. Compare Quor's current design with established tools and identify: how leading tools solve the
   problem, common industry patterns, trade-offs, and why Quor should or should not adopt each
   approach.
5. Recommend the implementation that best fits Quor's architecture, ADRs, guardrails, simplicity
   goals, portability, maintainability, and long-term roadmap. Do not copy another tool blindly.
6. Present the recommendation for approval before implementation whenever a meaningful architectural
   decision exists.
7. After approval, implement the chosen approach and add tests (Rule 1) before considering the
   feature complete.

This rule applies to all Batch 5 work and every future feature unless explicitly overridden.

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

## Git Workflow — Rules for AI-Assisted Development

See `CONTRIBUTING.md`'s "Branching model" and "Commit message convention" for the full contributor-facing workflow. These are the rules specific to AI-assisted (Claude Code) sessions:

1. **Never develop directly on `main`.** All code changes happen on a `feature/qb-XXX-short-description` branch.
2. **Before making any code changes, check the current branch** (`git branch --show-current` or `git status`). Do this at the start of a session and again before the first edit if time has passed.
3. **If the current branch is `main`:** tell the user and either (a) ask them how they'd like to proceed, or (b) if the user has clearly asked for the change to be made (not just discussed) *and the working tree is clean*, create a feature branch — this is a safe, local, reversible operation and does not require a separate confirmation. If the working tree is **not** clean, rule 8 governs instead: stop and ask, don't branch around uncommitted changes silently. Never leave uncommitted work sitting on `main` past the point where you know it should be branched.
4. **Never commit automatically.** Only create a commit when the user has explicitly asked for one in this conversation. If unclear whether "make the change" also means "commit it," ask.
5. **Never merge automatically.** Merging into `main` happens via a reviewed Pull Request on GitHub, not via a local `git merge` run by the assistant.
6. **Always ask for explicit confirmation before any Git operation that changes history or shared state:** `commit`, `merge`, `rebase`, `push`, `tag`, or anything that triggers a release. Showing the exact commands for the user to review (or to run themselves) is preferred over executing them silently.
7. **Destructive operations** (`git reset --hard`, `git push --force`, `git branch -D`, deleting a remote branch) require explicit, scoped confirmation every time — a prior approval for one push/branch does not carry over to another.
8. **If the working tree is not clean before starting a backlog item, stop and ask the user for guidance.** Never automatically stash, reset, clean, or discard changes to force a clean state — those changes may be in-progress work the user hasn't told you about yet.

### Starting Any Backlog Item

Before making any code or documentation changes for a new backlog item, always follow this sequence — do not skip steps or start implementing first and branch afterward:

1. Ensure any previous feature branch has already been merged or intentionally abandoned. Don't start new work while a prior branch is still open and unresolved.
2. Checkout `main`: `git checkout main`
3. Pull the latest `origin/main`: `git pull origin main`
4. Verify the working tree is clean: `git status`. If it is not, stop — see rule 8 above.
5. Create a new feature branch: `git checkout -b feature/qb-XXX-short-description`
6. Verify the current branch before making any modifications: `git branch --show-current`
7. Only then begin implementation.

**Additional rules:**
- **Every backlog item gets its own feature branch.** One QB item, one branch.
- **Never reuse an old feature branch.** Even a branch you created earlier in the same session, for a different item, must not be repurposed — create a new one following the sequence above.
- **Never begin new work from an existing feature branch.** Always branch from `main`, not from another feature branch. If a new item genuinely depends on an unmerged item's changes, stop and flag the dependency to the user rather than silently stacking branches.
- **After a PR is merged**, before starting the next backlog item:
  1. `git checkout main`
  2. `git pull origin main`
  3. Delete the local feature branch: `git branch -d feature/qb-XXX-short-description`
  4. Delete the remote feature branch: `git push origin --delete feature/qb-XXX-short-description`
  5. Only then start the next backlog item — re-enter this section at step 1.

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

## Current Implementation Task

Phases 0-10 are complete (ContentMask pipeline, filters, rewriter, hook adapter, tracking, CLI, plugin infrastructure and discovery, packaging & release). v0.1.0 is published to PyPI. See ROADMAP.md for the next milestone (v0.5 — Public Alpha) and PROJECT_STATUS.md for the current session snapshot.

The three pre-Phase-0 gates (Python startup time, Claude Code hook mechanism verification, PyPI name availability) were all resolved before Phase 0 began; see PROJECT_STATUS.md's "Pre-implementation blockers" section for the historical record.
