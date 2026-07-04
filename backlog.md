# Backlog

Proposed changes, process improvements, and known gaps that are not yet scheduled
for implementation. Each entry: ID, Priority, Category, Title, Problem, Desired
outcome, Status. Add new entries at the top (most recent first).

---

## QB-006A

**Priority:** High
**Category:** Feature

**Title:** Generic Node.js ecosystem support (npm/npx/pnpm/yarn noise reduction)

**Problem:**
Split from QB-006 following the Batch 5 design review. `npm`, `npx`, `pnpm`, and `yarn` invocations
currently pass through Quor unfiltered and untracked — `npm` is not in `_KNOWN_BASE_COMMANDS` at
all, and `npx`/`pnpm`/`yarn` are only registered as transparent prefixes today, so nothing
underneath them is recognized either. Even without any tool-specific intelligence, the npm/pnpm/yarn
CLI wrapper itself produces a large amount of generic, low-signal noise on every invocation.

**Desired outcome:**
Rewrite rules and a built-in filter for `npm`/`npx`/`pnpm`/`yarn` that strip generic wrapper noise
only: `npm WARN` deprecation spam, progress spinners/ANSI output, audit messages, install/update
summaries ("added N packages in Xs", "up to date, audited N packages"), and other repeated
boilerplate — using only existing stage types (`strip_lines`, `remove_ansi`, `group_repeated`,
`max_tokens`, etc.), the same way every other built-in filter is built. No registry, dispatcher, or
plugin API changes.

**Explicitly out of scope for this item:** any tool-specific intelligence for what runs *underneath*
npm/npx/pnpm/yarn — Jest, ESLint, TypeScript, Vitest, Webpack, Vite, or any other JS/TS toolchain
output shape. That is tracked separately as QB-006B and must not be implemented as part of this item.

**Status:** Approved for implementation — Batch 5, item 2. Not yet implemented.

---

## QB-006B

**Priority:** Medium
**Category:** Feature

**Title:** Node.js ecosystem support — tool-aware filtering (Jest/ESLint/TypeScript/Vitest/Webpack/Vite)

**Problem:**
Split from QB-006 following the Batch 5 design review. `npm test` / `npm run build` / `npx <tool>` /
`yarn build` are opaque wrappers — the actual underlying tool is defined in `package.json` and
invisible to Quor's command-string-based filter matching (`FilterRegistry.find()` only ever matches
on the command string, never on output content). Delivering pytest/mypy/ruff-level compression precision
for JS/TS output requires either extending `quor/pipeline/content_type.py` with new content-shape
heuristics (jest/eslint/tsc/vitest output), or reading `package.json` at filter-registration time to
resolve the wrapped command — both are genuine architectural extensions, not filter-config-only
additions.

**Desired outcome:**
Tool-aware compression for common JS/TS toolchain output (test failures, lint violations, type
errors) with the same PROTECT/`preserve_patterns` precision as `pytest.toml`/`build.toml` today.

**Prerequisite:** a short ADR deciding the content-type-driven stage-branching pattern (raised in
the Batch 5 design review) before implementation begins — this should not be improvised per-filter,
since it's a precedent other future filters would follow.

**Status:** Backlog — deferred, not scheduled. Depends on QB-006A landing first and a design ADR
for content-type-driven filter branching.

---

## QB-016

**Priority:** Low
**Category:** Documentation

**Title:** Strengthen AI Git Workflow

**Problem:**
QB-015's Git workflow documentation didn't specify the exact sequence to follow when starting a new
backlog item (ensuring the prior branch is resolved, pulling latest `main`, verifying a clean working
tree, branching, and verifying the branch before editing), nor what to do if the working tree is
unexpectedly dirty at that point. Without this, there's a real risk of starting new work from a stale
or wrong branch, or of an AI assistant "helpfully" stashing/resetting/discarding a user's uncommitted
work to get to a clean state.

**Desired outcome:**
`docs/final/CLAUDE.md` documents an explicit "Starting Any Backlog Item" sequence (checkout `main`,
pull, verify clean, branch, verify branch, then implement), states that every backlog item gets its
own feature branch (never reused, never branched from another feature branch), documents the
post-merge cleanup sequence before starting the next item, and adds a rule that an unclean working
tree at the start of a backlog item is a stop-and-ask condition — never resolved automatically via
stash/reset/clean/discard.

**Status:** Resolved — implemented on `feature/qb-016-strengthen-git-workflow` (docs/final/CLAUDE.md)

---

## QB-015

**Priority:** Low
**Category:** Documentation

**Title:** Document Git branching, commit and PR workflow

**Problem:**
The project had no documented Git workflow: no branch-naming convention, no commit message
convention, and no PR checklist covering backlog/documentation/release-note follow-through. This
surfaced directly while preparing the QB-014 fix for merge — work was happening ad hoc, and the
QB-014 branch briefly carried unrelated workflow-documentation changes before being split out.

**Desired outcome:**
`CONTRIBUTING.md` documents the standard workflow (branch from `main`, `feature/qb-XXX-short-description`
naming, one backlog item per branch, run `quor verify` + full test suite before commit, conventional
commit messages, push, PR, merge only after review, delete branch after merge) and an expanded PR
checklist (tests pass, no unrelated changes, backlog updated, documentation updated, release notes
required Y/N). `docs/final/CLAUDE.md` documents the corresponding rules for AI-assisted sessions:
never develop on `main`, check branch before changes, never auto-commit or auto-merge, always confirm
before history-changing Git operations.

**Status:** Resolved — implemented on `feature/qb-015-git-workflow` (CONTRIBUTING.md, docs/final/CLAUDE.md)

---

## QB-004

**Priority:** High
**Category:** Bug Investigation

**Title:** Investigate git-diff max_tokens stage not enforcing configured limit

**Problem:**
Measured output from `quor git show`/`git diff` (~5,806 estimated tokens on a real test) greatly
exceeds the `git-diff` filter's configured `max_tokens` limit of 600 in `git.toml`. Root cause is
unknown — the stage may not be executing, the configuration may be wrong, or the token estimation
used for reporting may differ from what the stage actually enforces.

**Desired outcome:**
Root cause identified and either the stage is fixed to actually enforce its configured limit, or the
discrepancy between reported and enforced token counts is understood and documented.

**Resolution:**
Investigated and confirmed `max_tokens` executes correctly and enforces its budget exactly as
documented. The overshoot is caused by `git-diff`'s `preserve_patterns` (`^\+`, `^-`, `^@@`,
`conflict`, `Error`) marking most diff content as `PROTECT`, which `max_tokens` is designed to never
compress — measured at 298 of 515 lines PROTECT, summing to ~5,265 tokens alone, well above the
600 limit, before `max_tokens` even runs. This is expected behavior given current configuration, not
a stage defect. Follow-up product decision tracked in QB-012.

**Status:** Closed — Not a bug

---

## QB-012

**Priority:** Medium
**Category:** Product Decision

**Title:** Define token budget semantics when protected content exceeds max_tokens

**Problem:**
QB-004's investigation confirmed `max_tokens` executes correctly, but when `PROTECT` lines alone
exceed the configured budget, the limit cannot be enforced — `max_tokens` silently becomes a
no-op for that content. There is no documented, decided answer for what should happen in this case.

**Desired outcome:**
A maintainer decides and documents the intended semantics. Options to evaluate:

1. **Best-effort budget (recommended)** — `max_tokens` is a target, not a guarantee. Protected lines
   are never compressed, even if that means exceeding the configured limit.
2. **Hard budget** — `max_tokens` is absolute. Protected lines may be compressed if required to stay
   under the limit, overriding PROTECT when the two conflict.
3. **Priority-based budgeting** — Replace the binary `PROTECT`/`KEEP`/`COMPRESS` model with multiple
   protection levels, so the budget can compress lower-priority protected content before falling back
   to higher-priority content.

Whichever option is chosen should be documented alongside the `max_tokens` stage and reflected in
`git.toml`'s `git-diff` filter (and any other filter combining `preserve_patterns` with `max_tokens`).

**Resolution:**
Decided: Option 1, best-effort budget. Recorded as ADR-031 in `docs/final/DECISIONS.md`. `max_tokens`
remains a target that only ever compresses KEEP lines; PROTECT always takes precedence and rendered
output may exceed the configured limit when protected content alone is large. This formalizes
existing shipped behavior — no runtime or filter-configuration changes were made. Documentation
updated: `quor/pipeline/stages/max_tokens.py` (docstring + `limit` field description), `README.md`
(`max_tokens` bullet), `docs/final/PROJECT_BIBLE.md` (new note tying `max_tokens` to the existing
"meaning preservation is non-negotiable" principle, plus a status flag on the tee-mechanism claim).
Two follow-ups spun out as their own backlog items: QB-013 (tee mechanism is decided via ADR-023 but
not implemented) and QB-014 (mypy's `group_repeated` stage ordering issue found during this
investigation).

**Status:** Resolved — see ADR-031

---

## QB-014

**Priority:** Medium
**Category:** Bug Investigation

**Title:** Investigate mypy filter stage ordering (group_repeated vs PROTECT interaction)

**Problem:**
Found during the QB-012 investigation: `build.toml`'s `mypy` filter runs `strip_lines`
(with `preserve_patterns` covering `error:`/`warning:`/`note:`) before `group_repeated`
(configured to collapse repeated `^.*: error: ` lines). Since `strip_lines`'s `preserve_patterns`
already marks every matching line `PROTECT`, and `group_repeated` treats `PROTECT` lines as run
breakers (per its own docstring), `group_repeated` never actually collapses anything for the `mypy`
filter as currently ordered — it is effectively a no-op given the current stage sequence.

**Desired outcome:**
Confirm the no-op behavior with a reproduction (e.g. a mypy run with 3+ consecutive identical
error lines), then decide the correct fix: reorder stages so `group_repeated` runs before
`strip_lines` marks those lines PROTECT, narrow `strip_lines`'s `preserve_patterns` so it doesn't
cover the exact lines `group_repeated` is meant to collapse, or confirm current behavior is
acceptable and document why. No fix implemented yet — investigation only.

**Resolution:**
Confirmed and fixed. Merged to `main` via PR #2 (`feature/qb-014`, merge commit `c6107ae`, fix
commit `19ec7a7`).

*Root cause:* `strip_lines` ran before `group_repeated` in `build.toml`'s `mypy` filter.
`preserve_patterns` (`error:`/`warning:`/`note:`/`Error`) marked every matching line `PROTECT`
before `group_repeated` ever saw them, and `group_repeated` treats `PROTECT` lines as run
breakers — so repeated identical errors were never collapsed. A naive reorder alone was
insufficient: `strip_lines`'s preserve-pattern check re-evaluated every line regardless of an
existing `COMPRESS` decision (unlike its own strip-pattern check, which already skipped
already-`COMPRESS` lines), so it resurrected the duplicates `group_repeated` had just compressed.

*Final solution:*
- Reordered the `mypy` filter's pipeline to `group_repeated` → `strip_lines` → `max_tokens`.
- Updated `quor/pipeline/stages/strip_lines.py` so the preserve-pattern check skips lines already
  marked `COMPRESS`, mirroring the guard already used by the strip-pattern check.

*Validation performed:*
- Regression test added to `build.toml`'s `mypy` filter (3+ consecutive identical errors collapse
  to `(×N)`, duplicates do not reappear, a non-adjacent singleton error and warning/note lines
  remain protected).
- Dependency review confirmed `strip_lines` runs first (or is absent) in every other built-in
  filter, so the `strip_lines.py` guard change was dead code everywhere except `mypy` prior to
  this fix.
- Byte-for-byte before/after comparison (via `git stash`) confirmed identical output for
  `git-status`, `git-log`, `git-diff`, `pytest`, `ruff`, `cat`, and `generic`.
- Full test suite: `quor verify` 25/25, `pytest tests/` 612 passed (1 pre-existing, unrelated
  plugin-discovery failure confirmed present independent of this change).

**Status:** Resolved

---

## QB-013

**Priority:** Medium
**Category:** Feature

**Title:** Implement the documented tee mechanism for recoverable aggressive compression

**Problem:**
`docs/final/DECISIONS.md` ADR-023 ("Tee Mechanism — Cache Original Before Compression") and
`docs/final/PROJECT_BIBLE.md` both document a tee mechanism — cache the original output to
`~/.local/share/quor/tee/{hash}.txt` and append a `[full output: path]` pointer to compressed output
— specifically so that aggressive compression is safe because "nothing is irrecoverably lost." ADR-023
is marked `Decided`, but no implementation exists: there is no `tee.py` module, and no built-in filter
reads a `tee` field. This gap became directly relevant while resolving QB-012 (best-effort `max_tokens`
budgets rely on the tee mechanism as the safety net for cases where protected content pushes output
well over the configured target).

**Desired outcome:**
Implement the tee mechanism as specified in ADR-023: cache original subprocess output before
compression, append the `[full output: path]` footer (not subject to `max_tokens`), support
per-filter `tee = false` opt-out, and clean up tee files older than 7 days at session start. Once
implemented, `PROJECT_BIBLE.md`'s "nothing is irrecoverably lost" claim becomes accurate rather than
aspirational.

**Status:** Resolved — implemented on `feature/qb-013-tee-mechanism` (merged via PR #8, hardening
fix via PR #9). Dispatcher-level only (`quor/adapters/dispatcher.py`), no pipeline/stage changes.
SHA256 content-addressed storage under `~/.local/share/quor/tee/`, with dedup + mtime refresh on
cache hit. `[full output: <path>]` footer appended post-pipeline (not subject to `max_tokens`).
7-day TTL cleanup, throttled via a separate `tee_state.db` (WAL mode, hardened against concurrent-
open lock contention). Global (`tee_enabled`, `QUOR_TEE_ENABLED`) and per-filter (`FilterConfig.tee`)
opt-out, both backward-compatible defaults.

---

## QB-005

**Priority:** High
**Category:** Feature

**Title:** Structural code extraction for file reads

**Problem:**
Quor's `cat` filter only strips comments and blank lines; it always returns full source content
otherwise. For large files this leaves significant token cost on the table compared to returning
just the API surface a developer or AI actually needs.

**Desired outcome:**
An AST-aware or parser-assisted code summarization mode that prioritizes imports, public types,
function/method signatures, docstrings, constants, and file structure over full function bodies —
reducing tokens while preserving developer/AI context. Primary objective is token reduction without
losing the structural understanding needed to work with the file.

**Approved architecture (Batch 5 design review):**
- **Python only in V1.** No multi-language parsing, no third-party parser — Python standard library
  `ast` only. No new dependency.
- **`StageHandler`'s interface is not modified.** Stages continue to receive only `ContentMask` +
  config, never a filename or command string — the same contract every existing stage has.
- **Python detection happens at the filter layer**, via command matching (e.g. `cat *.py`), not by
  threading filenames into stages. A new `cat-python.toml` filter routes `.py` file reads to the new
  AST-aware stage; the stage itself receives only file content, exactly like every existing stage.
- **No new registry tie-break algorithm.** `FilterRegistry` keeps its existing "first matching filter
  wins" behavior unchanged. Correctness comes entirely from **built-in filter load order**:
  `cat-python.toml` must load (and therefore match) before the generic `cat.toml`. Document this
  ordering rule so future extension-specific filters (e.g. a hypothetical `cat-javascript.toml`)
  follow the same pattern rather than each inventing their own precedence mechanism.
- **Fail-open on any parsing failure** (`SyntaxError` or otherwise) — falls back to full, unmodified
  content, never a crash or partial/corrupt output.

**Status:** Implemented (Batch 5, item 1). `quor/pipeline/stages/python_ast_summarize.py` compresses
function/method bodies to signature + docstring using stdlib `ast` only, with fail-open behavior
delegated entirely to the engine's existing per-stage exception handling (no local try/except).
`cat-python.toml` routes `.py` file reads through it, then reuses `cat.toml`'s existing
strip_lines/deduplicate_consecutive/max_tokens stack so comment-stripping and blank-line dedup
(which `ast` cannot see, since comments have no AST node) are not lost for Python files — this
combination is covered by a dedicated inline filter test. Comprehensive unit tests added to
`tests/unit/test_stages.py::TestPythonAstSummarize` (valid file, syntax error at both the stage and
pipeline fail-open level, empty file, null-byte input, decorators, nested classes/functions, async
functions, a 300-function synthetic large file, non-ASCII identifiers/docstrings, single-line and
docstring-only function bodies, and byte-identical-kept-line regression tests). Full `pytest`,
`quor verify`, `ruff check`, and `mypy` all pass. Not yet committed — awaiting instruction.

---

## QB-006

**Priority:** High
**Category:** Feature

**Title:** Node.js ecosystem support

**Problem:**
Quor has no rewrite/filter coverage for `npm`, `npx`, or `pnpm` — a significant ecosystem gap
relative to competitors. Build, test, lint, and type-check output for JS/TS projects currently
passes through unfiltered and untracked.

**Desired outcome:**
Rewrite rules and filters for `npm`/`npx`/`pnpm` invocations, prioritized by workflow: build, test,
lint, and type-check first.

**Status:** Split following the Batch 5 design review — see QB-006A (generic Node ecosystem noise
removal, approved for implementation next) and QB-006B (tool-aware Node ecosystem filtering,
deferred to future backlog). This entry is kept for historical context; new work is tracked under
QB-006A/QB-006B.

---

## QB-007

**Priority:** High
**Category:** Feature

**Title:** Intelligent document compression

**Problem:**
Quor only filters shell command output today. Reading DOCX, PDF, Markdown, or plain text documents
returns raw content with no structure-aware compression.

**Desired outcome:**
Token-efficient reading of DOCX, PDF, Markdown, and text documents by extracting structure —
headings, tables, numbered lists, requirements, decisions — instead of returning raw document text
whenever possible.

**Status:** Blocked pending feasibility investigation into whether Claude Code can intercept native
Read/File tool output. Implementation will begin only after this investigation is complete.

**Context (Batch 5 design review):** Quor's only integration point today is the Claude Code
`PreToolUse` hook registered for the Bash matcher (`quor/cli/commands/init.py`); most PDF/DOCX
reading inside Claude Code uses native Read/File tools, not Bash, so Quor never receives those
requests under the current architecture. The feasibility investigation is a prerequisite for this
item, not separate product work — no backlog ID is tracked for it.

---

## QB-008

**Priority:** Medium
**Category:** Enhancement

**Title:** Regex replacement pipeline stage

**Problem:**
Quor's pipeline has no general-purpose regex substitution stage. Repeated high-entropy content
(paths, timestamps, UUIDs, hashes) in command output can't be normalized the way ZAP's `replace`
stage does.

**Desired outcome:**
A configurable regex replacement stage (with backreference support, chainable like existing stages)
usable by any filter to normalize this kind of content.

**Status:** Resolved — implemented as the `regex_replace` stage (`quor/pipeline/stages/regex_replace.py`).
Ordered list of `{pattern, replacement}` rules per filter, applied via `regex.sub()` (native
backreference support, no extra handling needed). PROTECT lines and `preserve_patterns` matches are
never modified, matching every other stage's invariant. Registered in `quor/filters/registry.py`.

---

## QB-009

**Priority:** Medium
**Category:** Enhancement

**Title:** Per-line truncation stage

**Problem:**
Quor has no stage to cap individual line length. Long lines (stack traces, JSON payloads, long
paths) can dominate token cost even when the number of lines is otherwise under control.

**Desired outcome:**
A configurable max-line-length stage, similar to ZAP's `truncate_lines_at`, usable by any filter to
reduce excessively long lines while preserving useful context.

**Status:** Resolved — implemented as the `truncate_lines` stage (`quor/pipeline/stages/truncate_lines.py`).
Caps KEEP line length to `max_length`, appending a configurable `marker` so the cut is visible.
Line count is never changed. PROTECT lines and `preserve_patterns` matches are exempt from truncation,
matching `max_tokens`'s precedent that protected content is never reduced by any stage.

---

## QB-010

**Priority:** Medium
**Category:** Enhancement

**Title:** Whole-output pattern short-circuit

**Problem:**
Quor's only whole-output shortcuts today are the narrower `abort_unless`/`on_empty` filter-level
options. There's no general stage that can match the entire output against a pattern and immediately
substitute a short summary, avoiding unnecessary downstream stage processing — the equivalent of
ZAP's `match_output` + `unless` guard.

**Desired outcome:**
A pipeline stage that can short-circuit to an immediate compressed result when the complete output
matches a predefined pattern (e.g. clean git status, successful build summary), configurable per
filter.

**Status:** Resolved — implemented as the `match_output` stage (`quor/pipeline/stages/match_output.py`).
Explicit opt-in per filter (`pattern` + `summary`); fullmatches the current rendered output. Refuses
to fire at all if any PROTECT line is already present, avoiding a class of index-collision bugs where
a naive collapse could lose a protected line's original content — this was judged safer than relying
on `Pipeline._enforce_protect` to paper over that case. Keeps the same LineMask count as input (no
engine/dispatcher changes needed). Emits an explicit warning on every fire, in addition to the normal
`quor explain` stage trace, so a short-circuit is never a silent event.

---

## QB-011

**Priority:** Medium
**Category:** Engineering

**Title:** Compression benchmark suite

**Problem:**
Quor has no repeatable way to measure token reduction, latency, or compression quality across a
fixed corpus, and no way to track whether a pipeline change is an improvement or a regression over
time. This gap was surfaced directly during a ZAP efficiency comparison, where neither tool had
proven, benchmarked efficiency numbers to point to.

**Desired outcome:**
A repeatable benchmark framework that runs a fixed corpus of representative commands and documents
through Quor's pipeline, measuring token reduction, latency, and compression quality, with results
trackable over time to catch regressions and validate improvements objectively.

**Status:** Backlog

---

## QB-003

**Priority:** Low
**Category:** Documentation

**Title:** Document command allowlist behavior

**Problem:**
Users naturally expect "Bash hook installed" to mean "every Bash command is tracked." In reality,
Quor intentionally rewrites only a small allowlist of known commands (git, cargo, pytest, etc.);
anything else passes through untouched and unrecorded. Nothing in the docs states this explicitly,
which invites confusion like the investigation that preceded this backlog item (hook verified
installed and firing correctly, yet `quor gain` reported zero invocations because the tested
commands were outside the allowlist / hit an unrelated protocol bug).

**Desired outcome:**
Documentation (README and/or CLAUDE.md) explicitly states that Quor only rewrites commands matching
its known rule set, links to `quor explain <command>` as the way to check whether a given command is
covered, and lists (or links to) the current allowlist so users don't assume blanket coverage.

**Status:** Backlog

---

## QB-002

**Priority:** Medium
**Category:** Product Decision

**Title:** Default operating mode contradicts ADR-009

**Problem:**
ADR-009 (DECISIONS.md) and three docs (CLAUDE.md, PROJECT_BIBLE.md, ROADMAP.md) state the default
operating mode is `AUDIT`. `quor/config/model.py` actually defaults to `"optimize"`, and `quor doctor`
prints `Mode: optimize` on a fresh install. Unclear whether this is an implementation bug against a
finalized ADR, or an intentional change that was never written back into the docs/ADR.

**Desired outcome:**
A maintainer decides which side is correct — either fix the code default to match ADR-009, or update
ADR-009 and the docs to reflect `optimize` as the intended default — and the two are reconciled.

**Status:** Resolved — implemented on `feature/qb-002-default-mode-audit`. Code default changed to
`audit` to match ADR-009/PROJECT_BIBLE.md/CLAUDE.md/ROADMAP.md (`quor/config/model.py`,
`quor/config/loader.py`), README example output and `tests/unit/test_cli.py` updated to match. ADR-009
was not touched — it was already correct.

---

## QB-001

**Priority:** High
**Category:** Release Process

**Title:** Require successful TestPyPI validation before production release

**Problem:**
`release.yml` publishes directly to PyPI after tagging, bypassing manual TestPyPI verification.

**Desired outcome:**
Production publication must require successful TestPyPI validation and explicit approval.

**Status:** Resolved — implemented on `feature/qb-001-testpypi-release-gate` (`.github/workflows/release.yml`).
`publish-pypi` now needs a `release-approval` environment job, which needs `validate-testpypi` (installs
the tagged version from TestPyPI and smoke-tests it), which needs `publish-testpypi`. A maintainer must
still create the `release-approval` environment with required reviewers under Settings > Environments
for the approval gate to be enforced.
