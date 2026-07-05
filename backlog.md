# Backlog

Proposed changes, process improvements, and known gaps that are not yet scheduled
for implementation. Each entry: ID, Priority, Category, Title, Problem, Desired
outcome, Status. Add new entries at the top (most recent first).

---

## QB-018

**Priority:** High
**Category:** Bug fix

**Title:** `quor gain` project-scoping correctness (case-sensitivity, sibling leakage, GLOB/LIKE injection, degenerate keys)

**Problem:**
Investigation into "`quor gain` stopped increasing" (triggered by a user report) found the plateau
itself was expected behavior (real recent activity was dominated by zero-savings git plumbing
commands), but surfaced a chain of real, separate correctness bugs in `quor/tracking/db.py`'s
project-scoping: (1) `project_path` was matched case-sensitively (`GLOB`/`LIKE` are), so a project
recorded under two different casings (e.g. Windows shells reporting `C:/...` vs `c:/...` for the
identical directory) silently split into two untracked halves; (2) a naive `GLOB "{project}*"`
prefix match had no path-separator boundary, so `/workspace` incorrectly swept in the unrelated
sibling `/workspace-other`; (3) the project key was spliced unescaped into a GLOB pattern, so a real
directory name containing `*`, `?`, `[`, or `]` was silently reinterpreted as a wildcard/character-class,
causing missed or spurious subdirectory matches; (4) a degenerate query key (empty, or a bare drive
letter from querying "/" or "C:/") turned the subdirectory pattern into a match-everything wildcard,
verified to sweep in every unrelated project on a whole drive.

**Desired outcome:**
A single, deterministic, well-tested project-identity model with no duplicated normalization logic
between Python and SQL, no schema migration required, and no behavioral change to real historical
data.

**Resolution:**
- Added `normalize_project_path()` (`quor/tracking/db.py`) as the sole, exclusive definition of
  project identity (lowercase, POSIX-style, trailing-slash-insensitive).
- Added a precomputed `project_key_normalized` column (schema v2, nullable, backward-compatible —
  `ALTER TABLE ADD COLUMN` guarded by `PRAGMA table_info()`, idempotent on every connection),
  populated at write time in `TrackingDB._write_sqlite()`. `InvocationRecord` and `dispatcher.py`'s
  call site are unchanged — the derived column is computed at the single point every record already
  passes through on its way into SQLite.
- Historical rows (written before this column existed) are lazily backfilled by `query_gain()` on
  first read: `normalize_project_path` is registered as a SQL function
  (`conn.create_function(...)`, once per connection) and the backfill `UPDATE` *calls* it directly
  — not a hand-written SQL approximation (`LOWER(RTRIM(...))` was tried and rejected: SQLite's
  `LOWER()` only folds ASCII, and it doesn't collapse repeated separators or normalize backslashes
  the way `Path(...).as_posix()` does, so a SQL-side approximation could silently diverge for edge
  cases the real function handles correctly). This is a single set-based `UPDATE`, one transaction,
  idempotent (its own `WHERE ... IS NULL` guard converges to a no-op once complete).
- Matching moved from `GLOB` to `LIKE` (`project_key_normalized = ? OR project_key_normalized LIKE
  ? ESCAPE '\'`), with proper escaping of `%`/`_` (LIKE's only metacharacters) applied only to the
  subdirectory pattern's path portion — never to the equality branch, never to the deliberate `/%`
  wildcard suffix.
- Degenerate query keys (empty, or a bare drive letter) are rejected outright with a clear
  `ValueError` rather than silently matching everything.
- A `project_prefix` column, added during an intermediate design iteration, was found to be written
  but never read by any query — removed entirely (column, write, backfill, index consideration,
  comments) to keep the schema minimal.

Verified against the real production database throughout (via disposable copies for any
migration-touching step): invocation counts and token totals are unchanged for legitimate queries;
the demonstrated overmatching/undermatching bugs are fixed; the live database was independently
confirmed to have already migrated correctly via this session's own ambient dogfooding activity.

**Status:** Resolved. Full `pytest`, `quor verify`, `ruff check`, and `mypy` all pass. Comprehensive
regression tests added to `tests/unit/test_tracking.py` covering case-insensitivity, sibling-leakage
exclusion, subdirectory inclusion, GLOB/LIKE metacharacter escaping, degenerate-key rejection, and
lazy backfill of a hand-built pre-v2 database.

---

## QB-017

**Priority:** Low
**Category:** Metrics / Observability

**Title:** TEE footer affects token metrics clarity

**Problem:**
`quor gain` occasionally reports a negative token contribution for an invocation (confirmed: 7
historical rows, e.g. `git rev-parse HEAD origin/main` recorded as 21→43 tokens). Root-caused via
investigation: the tee recovery mechanism (ADR-023) appends a fixed-size footer
(`\n[full output: {path}]`, ~130 characters / ~33 tokens, dominated by the platformdirs cache path
plus a 64-character SHA256 filename) to the filtered output *before* `original_tokens`/`final_tokens`
are computed in `quor/adapters/dispatcher.py`'s `_track()`. For invocations where the ContentMask
pipeline's real content compression is smaller than the footer's fixed cost — which is common for
already-short, already-clean command output, the exact case where compression has the least to work
with — the footer's overhead can exceed the genuine savings, producing a net "negative" result in
`quor gain` even though the pipeline compressed correctly.

This is **not a correctness bug** in the pipeline, tee mechanism, or tracking/aggregation logic — all
three do exactly what they're designed to do, and the negative numbers were reproduced live and
reconciled exactly against historical data. It is a **metrics-definition problem**: `final_tokens`
(and therefore `tokens_saved`) conflates two different things — "how much the ContentMask pipeline
compressed the content" and "how much dispatcher-level recovery metadata was appended afterward" —
and presents them to the user as one undifferentiated number attributed to the matched filter.

**Desired outcome (for a future metrics redesign, not this item):**
`quor gain` should be able to distinguish genuine compression savings from intentional
dispatcher-level overhead (tee's recovery footer, and any similar future annotation) rather than
netting them silently. Candidate approaches to evaluate at that time: measure `final_tokens` before
`_apply_tee()` runs (changes existing semantics — `final_tokens` would no longer equal exactly what
was written to stdout), or add a distinct field (e.g. `tee_overhead_tokens`) so both numbers can be
shown separately without changing what "final_tokens" has historically meant. Either choice needs a
schema/display decision, not just a code fix, which is why this is deferred rather than bundled into
QB-017 immediately below or fixed ad hoc.

**Status:** Deferred investigation. No immediate fix. Revisit as part of a future `quor gain` /
tracking metrics redesign, not as a standalone bug fix — the current behavior is internally
consistent and arguably defensible (the footer genuinely is extra text sent to the AI's context), so
the right fix depends on a product decision about what "tokens saved" should mean, not just a code
change. Unrelated to, and not fixed by, the separate `project_path` case-sensitivity and GLOB
sibling-leakage fixes to `quor/tracking/db.py` made alongside this entry.

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

**Status:** Implemented (Batch 5, item 2). `quor/filters/builtin/node.toml` adds four `[[filter]]`
blocks (npm, npx, pnpm, yarn), each composed only from existing stage types: `remove_ansi`,
`group_repeated` (collapses deprecation/warning spam to one visible instance + count rather than
deleting it — preserves warnings while cutting repetition), `strip_lines` (targeted noise patterns
with a `preserve_patterns` safety net for errors/vulnerabilities/summaries), and
`deduplicate_consecutive`. No new stage type; no Pipeline/ContentMask/Engine/Dispatcher/StageHandler
change.

Deliberately **no `max_tokens` stage** in any of the four filters, unlike most other built-in
filters — npm/npx/pnpm/yarn can all wrap an arbitrary underlying command (`npm test`, `npx jest`,
`pnpm run build`, `yarn build`), and a token-budget stage risked truncating that wrapped tool's real
output, which conflicts with "preserve actionable output."

**Required, in-scope classifier change:** `npm` was not in `_KNOWN_BASE_COMMANDS` at all, and
`npx`/`pnpm`/`yarn` were only registered as `TRANSPARENT_PREFIXES` (stripped before the classifier
ever looks at them) — so without a change here, none of these commands would ever reach
`FilterRegistry` regardless of how good the TOML filter was. `quor/rewrite/rules.py` now lists
`npm`/`npx`/`pnpm`/`yarn` in `_KNOWN_BASE_COMMANDS` (unconditionally, like `git` — not subcommand-
gated like `python -m X`) and removes `npx`/`pnpm`/`yarn` from `TRANSPARENT_PREFIXES` (`bunx` is
unaffected; Bun is out of scope). This is a `quor/rewrite/` change, not a Pipeline/ContentMask/
Engine/Dispatcher/StageHandler change, so it stays within the item's constraints — but see the
architectural concern below.

**Architectural concern surfaced during implementation:** npm/npx/pnpm/yarn were previously used
throughout the existing classifier test suite as the canonical "definitely unknown, definitely
passes through" example command (compound commands, env-prefix commands, sudo/docker-exec prefix
commands, pipe chains) — a change here has a wide test blast radius even though it's scoped entirely
to the Node ecosystem. Updated 7 test files (`test_rewrite.py`, `test_invocation.py`,
`test_adapters.py`, and the `simple`/`compound`/`env_prefix`/`transparent_prefix` command fixtures):
swapped the generic "unknown command" example to `cargo build` where a test's actual purpose was
unrelated to Node (preserving its original intent), and added explicit new cases/assertions
verifying the new npm/npx/pnpm/yarn behavior where a test was specifically about them. Also: because
`npm`/`pnpm`/`yarn` are not subcommand-gated, `npm test`/`pnpm run build`/`yarn build` are now routed
through Quor too — the wrapped tool's actual output rides through this filter mostly unfiltered
(by design, no tool-aware routing), which is the intended, minimal-risk generic behavior for this
item.

Comprehensive tests added: `tests/unit/test_filter_safety.py` (`TestNpmFilterSafety`,
`TestNpxFilterSafety`, `TestPnpmFilterSafety`, `TestYarnFilterSafety` — realistic install/audit/
failure output per tool, asserting errors/warnings/package counts/audit summaries/success-failure
summaries are never compressed) plus inline filter tests in `node.toml` and new/updated classifier
unit tests and fixture cases. Full `pytest`, `quor verify`, `ruff check`, and `mypy` all pass.

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

**Status:** Implemented, at a deliberately narrower scope than originally framed above. The
prerequisite ADR turned out to be unnecessary: the original problem statement assumed resolving the
wrapped tool required either `package.json` inspection or new content-type heuristics, but the
actual requirement only asked for routing invocation shapes where the real tool name is **already
present in the command string** — `npx eslint`, `npm exec eslint`, `pnpm exec/dlx eslint`, `yarn exec
eslint`, and yarn classic's bare `yarn eslint` shorthand. `npm test` / `npm run build` / `npm run
lint` / any `<wrapper> run <script>` form is explicitly and permanently excluded — the script name is
a `package.json` alias, and resolving it would require reading `package.json`, which stays out of
scope by requirement. This means routing is pure command-string pattern matching in
`FilterRegistry`, with **no new stage, no content-type change, no `package.json` read, and no
Pipeline/ContentMask/Engine/Dispatcher/StageHandler change** — `quor/rewrite/` (the classifier) is
also untouched; QB-006A's classifier change (npm/npx/pnpm/yarn as known base commands) is the only
prerequisite, already satisfied.

**Implementation:** `quor/filters/builtin/node.toml` gained a new `eslint` `[[filter]]` block, placed
*before* the generic npm/npx/pnpm/yarn blocks in the same file (TOML array order is preserved by the
loader, so this is the same specificity-via-ordering idiom as `cat-python.toml`/`cat.toml` in QB-005,
just within one file). `match_command = '^(npx|npm exec|pnpm exec|pnpm dlx|yarn exec|yarn)\s+
(-\S+\s+)*(--\s+)?eslint(?=\s|$)'` — tolerates leading flags (matching the existing
`cat-python.toml` flag-tolerance idiom) and requires whitespace-or-end after the tool name (a bare
`\b` was tried first and incorrectly matched `eslint-plugin-foo`; fixed to a lookahead). Stages:
`remove_ansi`, `group_repeated` (collapses repeated `L:C  error|warning  ...` lines — same shape-only
matching limitation as mypy's existing `group_repeated` config, not a new one), `strip_lines`
(minimal — ESLint's default output has little to strip, unlike npm/mypy/ruff which have a distinct
"success" sentinel to remove), and `max_tokens` (safe here, unlike the generic npm/npx/pnpm/yarn
filters, because once routed to `eslint` the wrapped tool's output shape is actually known).

**Only `eslint` gets a real filter.** `prettier`/`jest`/`tsc` (all explicitly listed as "if one
exists" in the request) do not have filters yet — deliberately not built speculatively — so
`npx prettier`/`npx jest`/`npx tsc` correctly fall through to the generic npm/npx/pnpm/yarn filter
(QB-006A behavior). No fallback code was needed for this: it's an emergent property of
`FilterRegistry.find()`'s existing first-match-wins behavior. Adding `prettier`/`jest`/`tsc`
filters, if wanted later, is now a pure filter-config addition following the exact same pattern.

**Known, accepted trade-off:** routing is based on the literal tool/package name in the command
string, not on what actually executes. A `package.json` script named exactly `"eslint"` that runs
something else entirely, invoked via `npm run eslint`, is *not* routed (explicitly excluded, correct
per requirement); but a global binary or `npx`-resolved package that happens to be named `eslint`
but isn't actually ESLint would be misrouted. This is inherent to "no `package.json` inspection,
no content-based routing" and was called out as a known limitation, not fixed.

Comprehensive tests added: `tests/unit/test_node_tool_routing.py` (new — successful routing across
all six invocation shapes, fallback routing for prettier/jest/tsc/unknown tools, `<wrapper> run
<script>` exclusions including the same-named-script edge case, regression tests proving the
classifier boundary — transparent prefixes, pipe safety, structured-output exclusion, `bunx`
non-involvement — is unaffected by this change since `quor/rewrite/` was not touched, and boundary
cases including the `eslint-plugin-foo` word-boundary bug found and fixed during implementation) plus
`TestEslintFilterSafety` in `test_filter_safety.py` (realistic ESLint stylish-formatter output:
violations/rule names/problem summaries never compressed, repeated-violation collapsing, parse
errors, clean-run passthrough) and 3 new inline filter tests. Full `pytest`, `quor verify`, `ruff
check`, and `mypy` all pass.

**Follow-up refinement (before commit):** the initial `eslint` filter's `group_repeated` config
(shape-only pattern matching, `'^\s*\d+:\d+\s+(error|warning)\s'`) collapsed *any* consecutive
violation-shaped lines together regardless of message — meaning two genuinely different rule
violations sitting on adjacent lines (e.g. a `semi` error followed by a `no-console` error) would
have merged into one collapsed count, silently losing the second rule's identity. This was flagged
before commit and fixed with a minimal, additive, backward-compatible enhancement rather than
changing `group_repeated`'s existing behavior:

- Added an opt-in `exact_match: bool = False` field to `GroupRepeatedConfig`
  (`quor/pipeline/stages/group_repeated.py`). Default `False` preserves the exact behavior every
  existing filter already depends on — mypy's `build.toml` config intentionally collapses the *same
  error message* recurring at *different line numbers* in one file (three different strings, one
  shape), and changing the default would have silently broken that already-tested, desired behavior.
  This is exactly the "cannot force it without affecting mypy" case the requirement anticipated —
  the safe fix was a new opt-in field, not a change to the default matching semantic.
- `exact_match=True` adds one condition to run continuation: the candidate line must be byte-identical
  to the run's first line, in addition to matching the pattern. Only the `eslint` filter sets it
  (`min_count` also lowered from 3 to 2, since exact-match false positives are structurally
  impossible, unlike shape-only matching which needed a higher bar to stay conservative).
- Regression tests added to `tests/unit/test_stages.py::TestGroupRepeated` (default-off behavior
  unchanged; exact-match collapses byte-identical lines; does not collapse same-shape-different-text
  for both a differing line number and a differing rule name; a differing line in the middle of two
  identical pairs correctly splits into two separate collapses) and to
  `TestEslintFilterSafety` in `test_filter_safety.py` (identical repeated messages collapse;
  different rule names, different line numbers, and different file paths — the last via the natural
  file-path-header run-break, unaffected by this change — all correctly stay uncollapsed). Full
  `pytest`, `quor verify` (42/42), `ruff check`, and `mypy` all re-verified clean after this change.

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
