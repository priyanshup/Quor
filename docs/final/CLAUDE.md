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
**Status:** Phases 0-10 complete (983 tests passing, ruff+mypy clean). v0.5.0 is the current version (v0.4.1 was the prior PyPI release, published 2026-07-11; first released as v0.1.0 on 2026-07-01). See PROJECT_STATUS.md for the current session snapshot.

Read PROJECT_BIBLE.md for the full product context. Read DECISIONS.md for the reasoning behind every architectural choice. Read COMMAND_SUPPORT.md for the canonical list of every supported command/filter, command detection rules, and filter precedence. Do not re-derive decisions already made.

---

## Architecture at a Glance

```
<agent> hook (Claude Code PreToolUse, Gemini CLI BeforeTool, ...)
    → quor hook <agent_id> <event>  (AdapterRegistry resolves agent_id -> AgentAdapter)
    → AgentAdapter.handle_event(): parse this agent's payload, call the
      agent-agnostic core below, serialize this agent's response shape
      e.g. "git status" → "<python> -m quor git status"
      (interpreter invocation via get_quor_invocation(), not the quor/qr
      launcher — see DECISIONS.md ADR-029)
    → Quor subprocess runs real command, captures stdout
    → ContentMask pipeline (stages annotate lines, final render applies)
    → Compressed output returned to AI context, in that agent's own shape
    → SQLite + JSONL tracking (background thread)
```

The core pipeline (`quor/rewrite/`, `quor/filters/`, `quor/pipeline/`,
`quor/tracking/`) is 100% agent-agnostic — it takes/returns plain strings
and has never had, and must never gain, a branch on which agent called it.
Everything agent-specific lives in `quor/adapters/` behind the
`AgentAdapter` Protocol (ADR-036) — see `docs/final/ADAPTERS.md` for the
full architecture, lifecycle, and how to add a new adapter.

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
| `quor/adapters/` | `AgentAdapter` Protocol + `AdapterRegistry` (ADR-036) + one adapter per AI coding tool (`claude_adapter.py`, `gemini_adapter.py` — real hook capability; `codex_adapter.py`, `cursor_adapter.py`, `vscode_adapter.py`, `windsurf_adapter.py`, `aider_adapter.py`, `continue_adapter.py` — share the `_detection_only.py` base, no confirmed rewrite/replace capability). Platform/agent-payload concerns only — see `docs/final/ADAPTERS.md`. |
| `quor/pipeline/mask.py` | ContentMask, LineMask, Decision. Core primitive. |
| `quor/pipeline/engine.py` | Pipeline executor. Orchestrates stages. Enforces PROTECT immutability. |
| `quor/pipeline/content_type.py` | Heuristic content type detection. |
| `quor/pipeline/stages/` | One file per stage. Each implements StageHandler Protocol. |
| `quor/pipeline/repo_profile/` | QB-061's Repository Context Profile (`quor map`) — a new package *parallel to* the ContentMask pipeline, not a stage within it (see its own `__init__.py` docstring for why). Walks the repo once (`walk.py`), runs a three-tier detector-rule registry (`detectors/`, mirrors `filters/registry.py`'s loading pattern) plus language/entry-point/directory/statistics modules, and renders a deterministic `RepoProfile` (`model.py`/`render.py`) via `profiler.build_profile()`. Also hosts QB-066's Repository Symbols index (`quor symbols`) — `symbols.py`'s `build_symbol_index()` reuses the same `walk.py` file enumeration and, for every file whose extension has a registered `ast_summarize` parser, calls that language's additive `extract_symbols_*()` function (see `quor/pipeline/ast_summarize/symbol_model.py`) to collect classes/interfaces/structs/traits/enums/functions/methods into a `RepoSymbolIndex` (`symbols_model.py`/`symbols_render.py`) — a separate command/index from `RepoProfile`, not a new field on it (see ADR-038). Also hosts QB-067's Repository Dependency Graph (`quor graph`) — `graph.py`'s `build_dependency_graph()` walks the repo once and, per file, calls both `get_symbol_extractor()` (QB-066) and the new `get_relationship_extractor()` (QB-067) from `quor/pipeline/ast_summarize/registry.py`, then resolves each file's raw, unresolved `Relationship` facts (imports, exports, inheritance, interface/trait implementation, overrides, calls — see `quor/pipeline/ast_summarize/relationship_model.py`) against the whole repo's symbol table into a `RepoDependencyGraph` of `Edge`s (`graph_model.py`/`graph_render.py`) — conservative, unambiguous-only resolution (see ADR-039), never inferred. `intel.py`/`intel_store.py` (QB-072) persist all three artifacts to an on-disk cache (`state.json`/`profile.json`/`symbol_facts.json`/`graph_facts.json`) and `ensure_repo_intelligence()` auto-onboards/refreshes it for `quor map`/`quor symbols`/`quor graph`/`quor repo` (QB-076/QB-077's `dashboard.py`). Also hosts QB-078's Repository Explorer (`quor explore`) — `explorer.py`'s `load_cache()` reads that same on-disk cache **read-only**, distinguishing "missing" from "corrupted" from "stale" (unlike `dashboard.py`, which collapses missing/corrupted together), and never calls `ensure_repo_intelligence()` — a deliberate cache-only contract (see ADR-042 and `quor/cli/commands/explore.py`'s own module docstring) so `find`/`deps`/`used-by`/`file`/`stats` (`explorer_model.py`/`explorer_render.py`) never walk, parse, or rebuild anything. |
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

`quor schema` also exists as a 7th, exempted utility command (JSON Schema dump for the filter TOML format) — it's not a filtering operation, so it doesn't count against the six. `quor map` (QB-061, ADR-037), `quor symbols` (QB-066, ADR-038), `quor graph` (QB-067, ADR-039), `quor repo` (QB-076/QB-077, Repository Intelligence Dashboard with auto-refresh), and `quor explore` (QB-078, ADR-042, Repository Explorer — cache-only, never calls `ensure_repo_intelligence()`, unlike `quor repo`) are an 8th, 9th, 10th, 11th, and 12th exemption in the same category — deterministic, non-filtering repository-analysis commands, each explicitly approved as its own exemption, not an open door for further ones (see Common Mistake #8 below).

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

**Early exit (QB-036, optimization only — never changes observable output):** `Pipeline.execute()` skips remaining stages once the mask is "fully decided" (no `KEEP` lines left) *and* every not-yet-run stage is on a small, hand-audited `stage_type` allowlist in `engine.py` with an empty `preserve_patterns` — both conditions are required because three built-in stages (`group_repeated`, `max_tokens`, `remove_ansi`) can, if configured with `preserve_patterns`, promote an already-`COMPRESS` line back to `PROTECT` (see ADR-035), and `match_output`'s effect can never be predicted from `Decision` state alone so it is never treated as skippable. Skipped stages still get a `StageResult` (`was_skipped=True`), so `len(stage_results)` is unchanged. The skip check itself fails open (an exception there falls back to running the stage for real). `FilterRegistry.apply()` (the real compression path) has this on by default; `FilterRegistry.trace()` (`quor explain`) explicitly disables it so its diagnostic trace always reflects every stage's real behavior. No `StageHandler` was modified to support this — see `quor/pipeline/engine.py`'s own module docstring for the full correctness argument.

**Built-in stages:**
- `remove_ansi` — COMPRESS lines that are pure ANSI escape codes after stripping
- `strip_lines` — COMPRESS lines matching `patterns`; PROTECT lines matching `preserve_patterns`
- `deduplicate_consecutive` — COMPRESS consecutive duplicate lines (keep first)
- `group_repeated` — COMPRESS repeated pattern matches, replace with first instance + `(×N)`; default matches by *shape* (same pattern, e.g. mypy's same-message-different-line-number collapsing) — opt-in `exact_match: true` requires the full line to be byte-identical before it joins a run (QB-006B, used by the `eslint` filter so different rule violations never merge)
- `max_tokens` — COMPRESS lines beyond budget (strategy: `head`, `tail`, or `both`)
- `truncate_lines` — cap KEEP line length to `max_length`, appending `marker`; PROTECT exempt
- `regex_replace` — apply ordered regex substitution `rules` (capture groups supported) to KEEP lines
- `match_output` — if the whole rendered output fullmatches `pattern`, collapse to `summary`; refuses to fire if any PROTECT line is present
- `python_ast_summarize` — COMPRESS function/method body lines (stdlib `ast` parse only; never rewrites/reformats kept body lines); fails open (propagates parse errors to the engine's per-stage fail-open) on any non-Python or invalid-syntax input (QB-005). As of QB-005B, delegates its actual line-range computation to `quor/pipeline/ast_summarize/registry.py::get_analyzer("python")` — a reusable, multi-language analyzer framework — rather than calling `ast.parse()` directly; this stage's own behavior, config shape, and `cat-python.toml` are unchanged. As of QB-096, also calls `get_import_collapser("python")` and merges its result in via `quor.pipeline.stages._utils._apply_ast_summary()` — see that bullet's own QB-096 note below for what this adds.
- `code_ast_summarize` — the generic, filter-configurable counterpart to `python_ast_summarize` (QB-005B). Reads a `language` field from its config and dispatches to whichever analyzer `quor/pipeline/ast_summarize/registry.py` has registered for that name; an unregistered language fails open (mask unchanged, silent — see the stage's own module docstring for why this lives in `apply()` rather than `can_handle()`, which has no access to `StageConfig`). `"python"` (QB-005B), `"javascript"` (QB-005C), and `"typescript"`/`"tsx"` (QB-005D — two separate registry entries, since `tree-sitter-typescript` exposes two distinct grammars that must be selected by file extension, never by content) are all registered, backed by `tree-sitter`/`tree-sitter-javascript`/`tree-sitter-typescript` (optional `quor[javascript]` extra — one extra covers both languages, a deliberate QB-005D choice; see `typescript.py`'s own module docstring for the rationale). `cat-javascript.toml` routes `.js`/`.jsx`/`.mjs`/`.cjs` through this stage with `language = "javascript"`; `cat-typescript.toml` routes `.ts`/`.tsx` with `language = "typescript"`/`"tsx"` respectively (two `[[filter]]` blocks in one file, mirroring `node.toml`'s own multi-block precedent). `cat-python.toml` still uses the separate `python_ast_summarize` stage, not this one (a deliberate, documented difference, not an oversight — see `backlog.md`'s QB-005B/QB-005C entries). A missing `quor[javascript]` install fails open per-call (empty compress set, actionable warning), not at import time — see `javascript.py`/`typescript.py`'s own module docstrings. `javascript.py` and `typescript.py` share their ERROR-node-overlap-exclusion and body-interior-line logic via `quor/pipeline/ast_summarize/_treesitter_utils.py` (extracted from `javascript.py` in QB-005D, behavior re-verified byte-for-byte unchanged) rather than each reimplementing it. **A verified, real bug in `tree-sitter==0.26.0`** (native-level memory corruption under repeated `Node.child_by_field_name()` + point-attribute access on trees above roughly 85 relevant nodes — reproduced via bisection, absent in `0.25.2`) means `pyproject.toml` caps `tree-sitter` at `<0.26.0`; QB-005D re-ran the same bisection methodology specifically against the TypeScript/TSX grammars (clean at 2000-3000+ nodes) before adding `tree-sitter-typescript`, confirming the existing ceiling remains sufficient. Do not raise that ceiling without independently re-verifying a newer release no longer reproduces it. As of QB-005F, all four `cat-python`/`cat-javascript`/`cat-typescript`/`cat-tsx` filters are also reachable from Claude Code's native `Read` tool, not just `cat`'d through Bash — see `quor/adapters/claude_read.py`'s module docstring and `_SOURCE_CODE_FILTER_NAMES_BY_EXTENSION` for the by-name routing (a bare Read `file_path` can never match these filters' `cat `-prefixed `match_command` patterns, so they're looked up by name instead, the same mechanism QB-007E4 already uses for extracted DOCX/PDF text). **QB-096 (import block collapsing):** both stages above also call `get_import_collapser(language)` — a fourth registry family alongside `_ANALYZERS`/`_SYMBOL_EXTRACTORS`/`_RELATIONSHIP_EXTRACTORS`, registered for `"python"`/`"java"`/`"javascript"`/`"typescript"`/`"tsx"` (not `"go"`/`"rust"`/`"csharp"` — out of this item's scope) — that replaces a whole run of consecutive, top-level import statements with one synthesized summary block (`quor/pipeline/ast_summarize/import_collapse.py`'s shared, language-agnostic `group_import_runs()`/`render_import_block()`/`collapse_import_runs()`, fed by each language's own `collapse_imports_*()` extraction). A bare Python `import x` groups into a "Standard library"/"Third-party" bucket (`sys.stdlib_module_names` — deterministic, not guessed); every `from`-style import (Python relative/wildcard included, Java's package-qualified imports, JS/TS's module-specifier imports) groups under its own `module` heading instead — except a module group of 3 names or fewer, which renders as one inline `"module: a, b, c"` line rather than a heading + bulleted list (`_format_module_group()`, `_MAX_INLINE_MODULE_NAMES` — a product-review finding: a heading followed by 1-3 bullets is visually heavier than the line(s) it replaces; the stdlib/third-party buckets are deliberately exempt from this and always stay bulleted). Gated by the same QB-055 token-cost comparison every collapsing stage in this document already uses, plus a `_MIN_ENTRIES_TO_COLLAPSE = 2` floor (never collapse a run representing fewer than 2 individual bindings, regardless of cost math — a literal requirement, not a guessed heuristic; see `import_collapse.py`'s own docstring for the one edge case that required it). The merge with body-compression lives in one shared helper, `quor.pipeline.stages._utils._apply_ast_summary()`, so `python_ast_summarize.py`/`code_ast_summarize.py` each call it once rather than duplicating the merge logic — see `quor/pipeline/mask.py`'s module docstring for the exact, narrow line-rewrite exception this represents (the fourth, after `group_repeated`/`collapse_unchanged_context`/`path_prefix_fold`) and why it's justified on different grounds than `path_prefix_fold`'s byte-exact one. No filter TOML changes were needed — every `cat-python`/`cat-java`/`cat-javascript`/`cat-typescript`/`cat-tsx` filter gets this automatically through the same `language` config it already had.
- `path_prefix_fold` (QB-095) — front-codes a consecutive run of path-like KEEP lines (filter-declared `patterns`, same "author declares the shape" convention as `group_repeated`/`strip_lines`) sharing a directory prefix: the shared prefix, computed char-wise and trimmed back to the last `separator` boundary so a fold never splits a filename mid-token, becomes one new header line, and every line in the run is rewritten to its suffix — no line is `COMPRESS`ed away, so a fold is fully reconstructible (header + child = original line, byte-for-byte). Folds only when strictly cheaper by token estimate, the same QB-055 principle `collapse_unchanged_context` already uses — no separate line-count threshold. The one stage besides `group_repeated` allowed to change the mask's total line count (inserts exactly one header per fold), and the only stage allowed to rewrite every line in a matched run rather than just the first — see `quor/pipeline/mask.py`'s module docstring for the exact, narrow invariant this required extending. Wired into `z_generic.toml` (the fallback filter every command eventually reaches) with an anchored `'^\S+/\S*$'` pattern.
- `structured_data_summarize` (QB-040) — collapses a long, homogeneous JSON/YAML/TOML array/sequence/array-of-tables (same shape or, for objects, same key set; count > 6) to its first 3 elements plus one placeholder line, byte-preserving every kept line exactly like `code_ast_summarize` does for function bodies — the one line that *is* rewritten (the placeholder) mirrors `group_repeated`'s "reuse one line as the summary" technique, never inserting a new line. Reads a `format` field (`"json"`/`"yaml"`/`"toml"`) and dispatches via `quor/pipeline/structured_data/registry.py::get_analyzer()`, the structured-data mirror of `ast_summarize/registry.py`. JSON position tracking drives the stdlib `json` decoder's own `raw_decode` one value at a time (no hand-rolled string-escaping); YAML uses `yaml.compose()`'s node marks (optional `quor[yaml]`/PyYAML extra — missing dependency fails open exactly like `quor[javascript]` does for `code_ast_summarize`); TOML has no stdlib position API at all, so it's scoped specifically to array-of-tables (`[[name]]` blocks — the shape every real lockfile uses), recognized via TOML's own unambiguous header-line grammar. `cat-json.toml`/`cat-yaml.toml`/`cat-toml.toml` route `.json`/`.yaml`/`.yml`/`.toml` (plus the well-known lockfile basenames `poetry.lock`/`Cargo.lock`/`Pipfile.lock`/`composer.lock`, which don't carry their format in the extension) through this stage, on both the Bash `cat` path and Claude Code's `Read` tool (by-name routing via `_STRUCTURED_DATA_FILTER_NAMES_BY_EXTENSION`/`_STRUCTURED_DATA_FILTER_NAMES_BY_BASENAME` in `claude_read.py`, same mechanism as the AST-summarization filters). `.env`/`.ini` (also QB-040) need no new stage at all — `cat-dotenv.toml`/`cat-ini.toml` (filter names `dotenv`/`ini`; filed as `cat-*.toml` purely for built-in load-order priority over `cat.toml` — see those files' own header comments) just use `strip_lines` to remove comment/blank lines, never touching a `KEY=VALUE` line, composing correctly with the secret scanner (PA-F07) by construction since no value is ever inspected or modified.

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

Every new filter must include at least 3 `[[filter.tests]]` entries, plus a benchmark case
(`tests/benchmarks/manifest.toml` + sample file, QB-011) — see COMMAND_SUPPORT.md §7 and
CONTRIBUTING.md's Filter checklist. See COMMAND_SUPPORT.md for the full command/filter inventory,
how command detection works, and filter precedence rules — this file documents the pipeline and
TOML schema; COMMAND_SUPPORT.md documents which commands actually use them.

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

### Before Opening a PR — Benchmark & Regression Requirements (QB-011)

In addition to `quor verify`, `ruff`, and `mypy` (Rule 2 above) and whatever backlog item this
branch implements:

1. **If the change touches `quor/pipeline/`, `quor/filters/`, or `quor/rewrite/`**, run the
   compression benchmark suite locally: `python -m tests.benchmarks.run_benchmarks`. It also runs
   automatically inside `pytest tests/` (`tests/benchmarks/test_benchmarks.py`), so CI will catch
   a regression either way — but running it standalone produces the full Markdown report
   (`tests/benchmarks/results/benchmark-report.md`), which is far easier to read than a pytest
   failure when deciding whether a compression change was intentional.
2. **A new built-in filter is not complete without benchmark coverage.** See
   `docs/final/COMMAND_SUPPORT.md` §7 and `CONTRIBUTING.md`'s Filter checklist — a
   `[[case]]` entry, a sample file, and a committed baseline are required alongside the ≥3 inline
   `[[filter.tests]]` entries.
3. **Regression checking:** if `python -m tests.benchmarks.run_benchmarks` reports a
   `"regression"` status for any case (compression dropped by more than
   `--regression-threshold` percentage points, default 2.0, against `tests/benchmarks/baseline.json`),
   treat it the same as a failing test — bisect and fix, don't silence it. Only run
   `--update-baseline` when the compression change is intentional, and explain why in the PR body
   (see `tests/benchmarks/README.md`'s "Interpreting a failure" section).
4. **New or changed filter → update `docs/final/COMMAND_SUPPORT.md`.** It is the canonical
   command/filter reference (QB-003); a filter change that isn't reflected there is an incomplete
   PR, the same way an undocumented public API change would be.

### Review Checklist

Beyond `CONTRIBUTING.md`'s "What reviewers check" (safety/fail-open, Windows correctness, test
quality, anti-goals, scope), a reviewer of AI-assisted work should also confirm:

- [ ] The branch followed "Starting Any Backlog Item" above — one backlog item, branched from
      current `main`, not stacked on another unmerged feature branch.
- [ ] `quor verify`, full `pytest`, `ruff check quor/ tests/`, and `mypy quor/` are all clean
      (Rule 2) — not just "should pass," actually run and shown green.
- [ ] If `quor/pipeline/`, `quor/filters/`, or `quor/rewrite/` changed: the benchmark suite was run
      and any regression is either fixed or explicitly justified with an updated baseline.
- [ ] If a filter was added or changed: `docs/final/COMMAND_SUPPORT.md` reflects it, and benchmark
      coverage (§7 there) was added.
- [ ] `backlog.md`'s entry for this item has its Status updated, and any follow-up gaps discovered
      during the work are spun out as new entries rather than silently dropped.
- [ ] No unrelated changes bundled into the PR.

### Release Readiness Checklist

Cutting a release is a separate, higher-stakes activity than merging an individual PR. Before
tagging a version:

- [ ] Every gate for the target version in `docs/final/RELEASE_CRITERIA.md` passes — no partial
      credit, per that document's own rules.
- [ ] The full `CONTRIBUTING.md` "Release Process" checklist (versioning, `CHANGELOG.md`,
      `pyproject.toml`/`quor/__init__.py` version bump, CI green, `python -m build` +
      `twine check`, TestPyPI validation, tag push, `release.yml`) is followed in order — it is
      not repeated here to avoid two copies drifting apart.
- [ ] The compression benchmark suite is green against its committed baseline — a release should
      never ship a silent compression regression relative to the last tagged version.
- [ ] `docs/final/PROJECT_STATUS.md` reflects the actual current state (test count, phase status,
      known gaps) as of this release, not a stale snapshot from an earlier session.
- [ ] `docs/final/COMMAND_SUPPORT.md` and `README.md`'s command/filter-facing sections are current
      with whatever filters shipped in this release.

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
8. **Adding another filtering-operation CLI command.** Don't. V1 has exactly 6. `schema`, `map` (QB-061), `symbols` (QB-066), `graph` (QB-067), `repo` (QB-076/QB-077), and `explore` (QB-078) are the only six exemptions, all non-filtering utility commands with their own explicit approval — see "The Six CLI Commands" above. Don't treat that as an open door for a seventh.
9. **Returning empty string from pipeline.** `on_empty` handles this — check it's configured.
10. **Storing backslashes in SQLite paths.** Always `Path.as_posix()`.
11. **Mutable defaults in Pydantic models.** Use `Field(default_factory=list)`.
12. **Presenting token counts without ±20% label.** Always include the uncertainty.
13. **Invoking `quor`/`qr` as a bare command when manually testing the CLI** (e.g. `quor explain "git status"`, `quor doctor`). On a locked-down corporate Windows machine, the pip-generated `quor.exe`/`qr.exe` launcher stubs get silently blocked by Defender/AppLocker application-control policy (see README.md's "corporate-launcher troubleshooting" section and ADR-029 in DECISIONS.md — this is the exact reason the PreToolUse rewrite path invokes `sys.executable -m quor`, never the bare launcher). Manual/dev-workflow invocation is **not** covered by that ADR, so always use `python -m quor ...` / `python -m qr ...` yourself too — never the bare `quor`/`qr` form — when testing from a shell during development.

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

Phases 0-10 are complete (ContentMask pipeline, filters, rewriter, hook adapter, tracking, CLI, plugin infrastructure and discovery, packaging & release). v0.5.0 is published to PyPI (v0.1.0 was the first release, 2026-07-01). See ROADMAP.md for the next milestone (v0.5 — Public Alpha — a named milestone gate, not the same thing as the v0.5.0 package version) and PROJECT_STATUS.md for the current session snapshot.

The three pre-Phase-0 gates (Python startup time, Claude Code hook mechanism verification, PyPI name availability) were all resolved before Phase 0 began; see PROJECT_STATUS.md's "Pre-implementation blockers" section for the historical record.
