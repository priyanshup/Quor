# Backlog

Proposed changes, process improvements, and known gaps that are not yet scheduled
for implementation. Each entry: ID, Priority, Category, Title, Problem, Desired
outcome, Status.

Entries are grouped by **Priority** (High → Medium → Low). Within a group, most
recent first. When adding a new entry, insert it at the top of its priority
group — do not just append to the end of the file.

---

## Priority: High

### QB-032

**Priority:** High
**Category:** Feature

**Title:** Stack trace frame dedup for Python tracebacks (site-packages/dist-packages)

**Problem:**
Per the competitive research (`docs/archive/product-discovery/competitive-research.md`, Opportunity
6, ranked #6): "Django/Flask/pytest stack traces are 90% framework frames. Removing them is safe,
mechanical, and high-value... implementation effort: Low... RTK doesn't have this. Appeals directly
to the Python developer segment Distill/Quor targets." Quor's `pytest` and `generic` filters
previously had no compression at all for traceback frame content — `preserve_patterns` protected the
`Traceback` header and error lines, but individual `File "...", line N, in ...` frames (the bulk of a
real Django/Flask traceback) passed through completely untouched.

**Desired outcome:**
Framework/library traceback frames (Django, Flask, pytest's own internals, any installed package)
compressed out of view, while the user's own project frames and the actual exception always survive.

**Resolution (per Rule 4 — consulted the competitive research first; its own recommended approach,
"pattern matching against site-packages paths," is exactly what was implemented, no new research
needed):**
- Added one new `strip_lines` pattern to both `pytest.toml` and `z_generic.toml`:
  `(?i)^\s*File "[^"]*(?:site-packages|dist-packages)[^"]*", line \d+, in` — matches a traceback
  frame header whose path contains `site-packages` or `dist-packages`, which unambiguously means
  third-party/installed code on every platform (never a user's own project). Verified against real
  Linux, Windows, and venv-style paths before writing the filter config, including negative cases
  (the user's own project path, and a bare stdlib frame) to confirm no false positives.
- **Deliberately scoped down from a fancier "remove the whole frame" version.** A traceback frame is
  two lines (the `File "..."` header plus its indented source-code snippet); `strip_lines` evaluates
  each line independently with no lookback, so only the header line — which unambiguously identifies
  itself via the path — is compressed. The source-code snippet line has no distinguishing marker of
  its own and is left untouched rather than risk dropping real content on a shakier heuristic (Safety
  Rule #3: "when uncertain whether to remove a line, keep it"). Bare stdlib frames (no
  `site-packages`/`dist-packages` in the path) are also deliberately not matched — Windows' stdlib
  path has no equally unambiguous marker (a user folder literally named `lib` would false-positive).
  No new pipeline stage was needed — this is a filter-config-only addition, same pattern as every
  other built-in filter.
- `z_generic.toml` previously had no `strip_lines` stage or `preserve_patterns` at all; added both
  (protecting `Traceback`/`Error`/`Exception`) so the same dedup applies to a non-pytest Python crash
  (a raw script, `flask run`, etc.) — the other half of the "Django/Flask" framing in the research,
  not just pytest.

Regression tests: new inline `[[filter.tests]]` in both `pytest.toml` and `z_generic.toml` (realistic
Django-style traceback, asserting the framework frame is gone while the user frame and exception
survive). New benchmark case `pytest-framework-traceback-frames`
(`tests/benchmarks/samples/pytest/003_framework_traceback_frames.txt` + manifest entry) — 40.9%
compression, correctness verified, baseline updated. `docs/final/COMMAND_SUPPORT.md` updated for both
changed filters per the project's own filter-change convention.

**Status:** Resolved — implemented on `feature/td-tier4-differentiation-roadmap`. Full `pytest
tests/` (993 passed), `pytest tests/ -m integration` (9 passed), `ruff check`, `mypy quor/`, `quor
verify` (44/44), and the compression benchmark suite (29 cases, 0 regressions) all pass.

---

### QB-028

**Priority:** High
**Category:** Release Process

**Title:** Walk `RELEASE_CRITERIA.md`'s gates and record real pass/fail/evidence (TD-003)

**Problem:**
Found during the 2026-07-06 pre-release tech-debt audit (TD-003): every gate in
`docs/final/RELEASE_CRITERIA.md`, across all four milestones, was still an unchecked `- [ ]` despite
the project being functionally well past Internal Alpha (v0.3.0 published, 983+ tests). The document's
own rules state "no milestone is done until every gate is green... partial credit does not exist," but
nobody had actually walked it and recorded evidence for a single gate.

**Desired outcome:**
Walk Internal Alpha and Public Alpha (the realistic target per the tech-debt audit's own assessment)
gate by gate, record real pass/fail/evidence for each, and surface any genuinely new gaps found along
the way as their own backlog items rather than silently noting them.

**Resolution:**
`docs/final/RELEASE_CRITERIA.md` updated in place (per the document's own "record the result and the
evidence for each gate" instruction) with a dated Gate Walk section and per-gate evidence.

- **Internal Alpha: passes in full.** Every gate has direct, live evidence (real `quor doctor`/`quor
  verify`/`quor gain`/`quor explain` runs, real coverage measurements, real grep checks, a live 10MB-input
  timing test) except IA-F03, which used the closest available proxy — a real, unmocked hook-payload
  round trip for all five listed commands (matching `canary.yml`'s own method) rather than a literal
  live interactive Claude Code session, which this environment cannot spawn.
- **Public Alpha: does not pass yet.** Concrete, newly-confirmed gaps (not just unverified) — see
  QB-029 and QB-030 below for the two spun-out findings. Gates requiring genuinely external state (fresh
  VM installs, multiple non-builder testers, multi-hour real sessions, external documentation review)
  are left unchecked with a note on exactly what's needed, rather than assumed.
- **Beta and v1.0 were not walked** — Public Alpha itself doesn't pass yet, and walking a later
  milestone before its prerequisite passes would produce results with no real meaning, per the
  document's own "all gates must pass first" rule.

One concrete fix was made as a direct result of this walk (not a separate backlog item, since it was
found and fixed in the same session as part of investigating PA-Q04): the default `pytest` invocation
was measured at 28–31s locally, right at PA-Q04's <30s bar, because nothing actually excluded
`@pytest.mark.integration`-marked tests from it despite the marker's own docstring and CLAUDE.md
both already claiming they were "excluded from default CI." Added `-m "not integration"` to
`pyproject.toml`'s `addopts` (verified `-m` is a last-one-wins pytest option, so `pytest -m
integration` still works to run them explicitly) and a dedicated CI step so the integration suite
still runs on every push/PR instead of being silently orphaned by the new exclusion.

**Status:** Resolved — implemented on `feature/td-tier2-release-readiness`.

---

### QB-027

**Priority:** High
**Category:** Engineering

**Title:** Add real integration tests for the six CLI commands (TD-006)

**Problem:**
Found during the 2026-07-06 pre-release tech-debt audit (TD-006): `tests/integration/` existed but was
empty, and every CLI command test in `tests/unit/test_cli.py` mocks `subprocess.run` and/or
`FilterRegistry` at the boundaries that matter most. QB-019's Windows npm/npx bug was invisible to the
entire test suite specifically because every dispatcher test mocked `subprocess.run` — the same class
of gap existed for the CLI surface, and was the reason `RELEASE_CRITERIA.md`'s **V1-Q07** ("all six CLI
commands have integration tests") was still open.

**Desired outcome:**
Real integration tests for all six CLI commands (`init`, `validate`, `explain`, `gain`, `verify`,
`doctor`) exercising real subprocess dispatch and a real (temp-dir-scoped) SQLite file, per V1-Q07.

**Resolution:**
Added `tests/integration/test_cli_commands.py`, marked `@pytest.mark.integration`, with no mocking of
`subprocess.run`, `FilterRegistry`, or `platformdirs` beyond the existing autouse test-isolation
fixture: a real `git status` through the real `run_dispatch()` path verified visible via `quor gain`
(both directly via `query_gain()` and the rendered CLI output); `explain` running a genuinely unmocked
real command (every existing unit test for it mocks `subprocess.run`); `verify`/`validate` against the
real built-in filter registry plus a real project-local filter file on disk; and a real `quor init
--claude` chained into a real, separately-invoked `quor doctor`.

Verified empirically (via a throwaway script, before relying on it) that a genuinely separate `quor` OS
subprocess could **not** be safely isolated from the real user data directory on this platform:
`platformdirs`' Windows backend resolves `user_data_dir`/`user_config_dir` via ctypes
(`SHGetKnownFolderPath`), which ignores `LOCALAPPDATA`/`APPDATA` environment variable overrides
entirely. These tests therefore invoke the real command functions in-process (CliRunner /
`run_dispatch()`) under the existing autouse `platformdirs` fixture, which does provide correct
isolation, rather than spawning `quor` itself as a child process.

**Status:** Resolved — implemented on `feature/td-tier2-release-readiness`.

---

### QB-026

**Priority:** High
**Category:** Security

**Title:** No dependency / supply-chain security scanning

**Problem:**
Found during the 2026-07-06 pre-release tech-debt audit (TD-005): no Dependabot config, no CodeQL
workflow, and no `pip-audit`/`bandit` step existed anywhere in `.github/`, despite `SECURITY.md`
already discussing trust boundaries (plugin execution, hook data flow) in detail. Standard,
low-effort hardening worth having in place before a public release grows the audience, not after a
report comes in.

**Desired outcome:**
Automated dependency update PRs and static security analysis running on a schedule.

**Resolution:**
Added `.github/dependabot.yml` (pip ecosystem, weekly) and `.github/workflows/codeql.yml` (scheduled
weekly plus push/PR to `main`, Python analysis via `github/codeql-action`). Both are config-only
additions with no effect on `quor/` or `tests/`; validated YAML syntax locally and confirmed
structure matches the repo's existing workflow conventions (`permissions` block,
`actions/checkout@v4`, unpinned major-version tags).

**Status:** Resolved — implemented on `feature/td-tier1-pre-release-fixes`.

---

### QB-025

**Priority:** High
**Category:** Release Process

**Title:** CI test matrix doesn't cover Python versions the package claims to support

**Problem:**
Found during the 2026-07-06 pre-release tech-debt audit (TD-004): `pyproject.toml` declares
`requires-python = ">=3.11"` and lists classifiers for Python 3.11 through 3.14, but
`.github/workflows/ci.yml`'s matrix only ran `3.11`/`3.12` — Python 3.13 and 3.14 were advertised as
supported with zero CI coverage verifying it. This also intersects `RELEASE_CRITERIA.md`'s own
**B-Q01** gate, which calls for 3.13 in CI at Beta.

**Desired outcome:**
CI matrix coverage matches the versions actually claimed as supported.

**Resolution:**
Added `3.13` and `3.14` to `ci.yml`'s matrix (still crossed with `ubuntu-latest`/`windows-latest`).
Locally re-verified the full suite, `ruff check`, `mypy quor/`, and `quor verify` all pass under this
machine's Python 3.14 interpreter (the only version available to test locally); 3.13 coverage will
be confirmed by CI on the next push.

**Status:** Resolved — implemented on `feature/td-tier1-pre-release-fixes`.

---

### QB-024

**Priority:** High
**Category:** Bug fix

**Title:** `assert` used for validation in `quor/tracking/db.py`

**Problem:**
Found during the 2026-07-06 pre-release tech-debt audit (TD-002): `TrackingDB._write_jsonl()` used
`assert self._jsonl_path is not None` to guard its only precondition — a direct violation of the
project's own non-negotiable rule (`CLAUDE.md` Safety Rule #6, `RELEASE_CRITERIA.md` gate **IA-Q07**,
"no `assert` in non-test source files used for validation, grep confirms"). The only call site
(`_worker()`) already guarded this immediately before calling, so it wasn't reachable with a bad
value today — but `python -O` strips assertions entirely, silently removing exactly the guarantee
IA-Q07 exists to catch by grep.

**Desired outcome:**
The precondition is enforced by a real, non-optimizable check; `grep -rn "assert " quor/` returns
nothing.

**Resolution:**
Replaced with `if self._jsonl_path is None: raise RuntimeError(...)`, matching the pattern used
elsewhere in the codebase. Added `test_write_jsonl_raises_if_called_without_path`
(`tests/unit/test_tracking.py`), which calls `_write_jsonl()` directly (bypassing `_worker()`'s
guard) to confirm the check actually fires as a real error rather than a silently-strippable
assertion.

**Status:** Resolved — implemented on `feature/td-tier1-pre-release-fixes`. `grep -rn "assert "
quor/` confirmed empty (IA-Q07 now passes). Full `pytest tests/`, `ruff check`, `mypy quor/`, and
`quor verify` all pass.

---

### QB-023

**Priority:** High
**Category:** Bug fix

**Title:** File-descriptor-prefixed redirects (`2>&1`) mangled by the lexer/classifier

**Problem:**
Found during the 2026-07-06 pre-release tech-debt audit (TD-001) and reproduced live:
`quor explain "cd X && python -m quor gain 2>&1"` rewrote the redirect into `2 >& 1`, confirmed
against a real shell that `2>&1` and `2 >& 1` are *not* equivalent (`echo hello 2>&1` prints `hello`;
`echo hello 2 >& 1` prints `hello 2` — the space turns the file-descriptor prefix into a literal
argument). Root cause: `quor/rewrite/lexer.py`'s tokenizer split a redirect's leading fd digit (the
`2` in `2>&1`) into a separate `WORD` token from the operator; every downstream reconstruction
(`split_compound`, `classify_command`) then re-joined tokens with a space. Investigating further
surfaced a second, more severe variant of the same bug: for a *known* (rewritten) command,
`parse_args()` only collected `WORD`/quoted/`ENV_ASSIGN` token kinds, silently dropping
`REDIRECT_OTHER` entirely — so `pytest 2>&1` rewrote to `... pytest 2 1`, turning the redirect into
two bare literal arguments rather than merely reformatting it.

**Desired outcome:**
`2>&1` and equivalent fd-prefixed redirects survive rewriting with unchanged shell semantics, for
both passthrough segments and known/rewritten commands, with no change to any other tokenization
behavior.

**Resolution:**
`quor/rewrite/lexer.py::tokenize()` now merges a digit run immediately followed by `>`/`<` (no
intervening space) into a single `REDIRECT_OTHER` token (e.g. `2>&`, `10>&`, `1>>`, `0<`) instead of
emitting the digits as a separate `WORD`. `parse_args()` now includes `REDIRECT_OTHER` in its
collected token kinds so a known command's redirect is preserved rather than dropped. Verified
against a real shell (via `subprocess`, not the Bash tool directly — Quor's own hook was live in this
session and mangled the bug being tested, a fitting demonstration of the defect) that space *after*
the operator (`2>& 1`, `2> file`) is harmless — only space *before* the fd digit changes behavior.
Regression tests added to `tests/unit/test_rewrite.py` (`TestTokenize`, `TestParseArgs`,
`TestClassifySimple`, `TestClassifyCompound`) covering the exact original repro, the known-command
drop case, multi-digit fds, append (`>>`), input redirects (`<`), and confirming plain redirects/bare
digit arguments are unaffected.

**Status:** Resolved — implemented on `feature/td-tier1-pre-release-fixes`. Full `pytest tests/`,
`ruff check`, `mypy quor/`, and `quor verify` (42/42) all pass.

**Correction (2026-07-08):** this entry originally reported `test_discovers_noop_test_stage` as a
"pre-existing, unrelated" failure "confirmed present on unmodified `main`." That confirmation was
itself wrong — the local dev environment used throughout this session never ran
`pip install -e ./tests/fixtures/test_plugin` (the step `ci.yml` always runs before tests), so the
test's entry-point discovery had nothing to find. Once installed locally, the test passes; real CI
runs (which do install the fixture) confirm it always has. There was no real bug — just an
incomplete local setup being mistaken for a product defect across several backlog/release-criteria
entries this session. See TD-003/QB-028's correction note for the fuller account.

---

### QB-021

**Priority:** High
**Category:** Bug fix

**Title:** `release.yml`'s TestPyPI publish collides with the pre-tag manual dry run

**Problem:**
Found while walking through the actual 0.3.0 release: `CONTRIBUTING.md`'s documented Release Process
has the maintainer manually trigger `publish-testpypi.yml` against the target version *before*
tagging, as a dry-run validation step. But `release.yml` (triggered by the tag push) runs its own,
separate `publish-testpypi` job as the first step of the gated production chain — re-uploading the
identical wheel/sdist for a version that's already on TestPyPI from the dry run. Neither workflow's
`pypa/gh-action-pypi-publish` step set `skip-existing`, so TestPyPI's rejection of the duplicate
upload (same filename, already exists) would hard-fail the job, blocking every downstream job
(`validate-testpypi` → `release-approval` → `publish-pypi`) — the exact chain QB-001 built to gate
production publishes. This would have hit every release that follows the documented process, not
just this one.

**Desired outcome:**
The documented dry-run-then-tag workflow no longer fails, without changing what gets published or
weakening the `release-approval` gate.

**Resolution:**
Added `skip-existing: true` to the `publish-testpypi` step in both `release.yml` and
`publish-testpypi.yml`. Re-uploading an already-published version is now a no-op instead of a hard
failure; a genuinely new version still publishes normally. No change to the approval gate or to
`publish-pypi` — this only affects the TestPyPI upload step's handling of a duplicate.

---

### QB-019

**Priority:** High
**Category:** Bug fix

**Title:** `npm`/`npx`/`pnpm`/`yarn` never actually execute through the real dispatch path on Windows

**Problem:**
A production-readiness validation of the tracking/gain pipeline (run against real commands via
`run_dispatch()` directly, not through mocked `subprocess.run`) found that `npm`, `npx`, `pnpm`,
and `yarn` — known base commands since QB-006A — fail unconditionally on Windows with
`FileNotFoundError: [WinError 2] The system cannot find the file specified`. These tools ship as
`.CMD` shell shims, not native `.exe` binaries; `subprocess.run(args)` without `shell=True` uses
Windows' `CreateProcess`, which does not apply `PATHEXT` extension resolution the way a real shell
does. The classifier correctly rewrote the command and the filter registry correctly matched
`npm`/`npx`/`pnpm`/`yarn`/`eslint`, but the actual subprocess spawn failed before any filtering
mattered — the command simply never ran (exit code 127), on Windows specifically, the platform
this project is built for. Every existing dispatcher test mocks `subprocess.run` entirely, which is
exactly why this was invisible to the test suite, `quor verify`, and the QB-011 benchmark suite
(which also never spawns a real subprocess — it applies filters directly to pre-captured sample
files). `quor explain` was unaffected only because it happens to use `shell=True` already.

**Desired outcome:**
`npm`/`npx`/`pnpm`/`yarn` (and any future shell-shim-based known command) actually execute through
`run_dispatch()` on Windows, with no new security surface (no shell-metacharacter injection risk)
introduced for any command, and a regression test that spawns a real subprocess rather than mocking
one, so this class of bug cannot silently reappear.

**Resolution:**
Implemented on `feature/qb-003-command-support-docs` (bundled into the same session as the Batch 7
documentation work and the QB-011 benchmark-coverage follow-up, per explicit instruction to fix any
real bug found during validation). `quor/adapters/dispatcher.py::run_dispatch()` now resolves
`args[0]` via `shutil.which()` before calling `subprocess.run()`, falling back to the original token
unchanged if not found (existing `FileNotFoundError`/`OSError` handling is untouched).
`shell=False` is preserved — no shell is introduced into the execution path. See ADR-033 in
`docs/final/DECISIONS.md` for the full options analysis. Added
`test_windows_shell_shim_executable_resolves_and_runs` (`tests/unit/test_adapters.py`), which
spawns a real throwaway `.cmd` shim (skipped on non-Windows) — confirmed to fail with exit code 127
on the pre-fix code and pass on the fix, per the project's Rule 3 (behavior lock principle). Full
`pytest tests/` and the `tests/benchmarks/` suite re-verified green after the change.

---

### QB-018

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

### QB-006A

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

### QB-004

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

### QB-005

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

### QB-006

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

### QB-007

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

**Context (Batch 5 design review):** Quor's only integration point today is the Claude Code
`PreToolUse` hook registered for the Bash matcher (`quor/cli/commands/init.py`); most PDF/DOCX
reading inside Claude Code uses native Read/File tools, not Bash, so Quor never receives those
requests under the current architecture. The feasibility investigation is a prerequisite for this
item, not separate product work — no backlog ID is tracked for it.

**Feasibility investigation (2026-07-09, Tier 4): confirmed feasible.** Verified directly against
Claude Code's official hooks reference (`code.claude.com/docs/en/hooks`), not inferred from memory:

- The `matcher` field for `PostToolUse` (and `PreToolUse`) is a regex against **tool name**, and
  `Read` is a valid, documented match value — the same mechanism already used for `Bash`. No special
  case is needed to target the Read tool specifically (`"matcher": "Read"`, or `"Read|Edit|Write"` to
  cover more of the file-access surface later).
- A `PostToolUse` hook receives `tool_name`, `tool_input`, and **`tool_response`** — the last of
  which carries the tool's actual result (e.g. the file content Read just returned). This is the
  piece that makes compression possible at all: the hook sees the real content, not just the request.
- Critically, a `PostToolUse` hook **can replace that result** before Claude ever sees it, via:
  ```json
  {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": "..."}}
  ```
  The official docs describe this exact mechanism as being "used for redaction or transformation use
  cases" — which is precisely what this item needs. One important caveat found during the same
  research: `updatedToolOutput` being honored for *all* tools (not only MCP-provided ones) was itself
  a comparatively recent change, so a minimum Claude Code version requirement applies — this needs
  pinning down precisely (and a `quor doctor` check added for it, mirroring the existing dependency
  checks) before this ships, not assumed.

**Architectural implication for whoever implements this next:** this is a genuinely different
integration shape than the existing Bash path, not a small extension of it. Today, Quor's PreToolUse
hook rewrites the *command* so its own dispatcher runs the real subprocess and compresses the output
before Claude sees it (`quor/adapters/dispatcher.py`). For Read, Claude Code performs the read itself
(including whatever internal PDF/DOCX-to-text handling it already does) — there's no subprocess for
Quor to wrap. The natural shape is a `PostToolUse` hook that receives already-read content and
transforms it via `updatedToolOutput`, which is actually a *closer* fit to the existing
`ContentMask`/`FilterRegistry` pipeline than the Bash path is (pipeline stages already just take text
in, return text out — no subprocess model needed here at all). Concretely this means: a new hook
adapter entry point alongside the existing Claude Bash adapter (`quor/adapters/claude.py`), a new
`PostToolUse`/`Read` entry that `quor init --claude` would need to additionally register in
`settings.json`, and new content-type-aware stages/filters for DOCX/PDF/Markdown structure extraction
(the actual feature this item describes) — none of which exists yet. The investigation only answers
"is this possible," not "build it now."

**Status:** Unblocked — feasibility confirmed. Still not scheduled for implementation; this remains a
substantial, multi-part feature (new hook adapter, new settings.json registration, new document-type
parsers/stages) that needs its own scoped design pass (per CLAUDE.md Rule 4) before work begins, not
a quick follow-on to the investigation itself.

---

### QB-001

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

---

## Priority: Medium

### QB-033

**Priority:** Medium
**Category:** Engineering

**Title:** Close `__main__.py`'s test coverage gap (TD-010)

**Problem:**
Found during the 2026-07-06 pre-release tech-debt audit (TD-010): `__main__.py` had the lowest test
coverage in the codebase (72%), concentrated in two branches — the "unknown hook adapter" branch
(`_run_hook()`, then lines ~46-48) and the `_run_dispatch()` CLI-entry wrapper (lines ~73-81). Not the
safety-critical top-level `except Exception` fail-open guard itself (already covered), but worth
closing given `__main__.py` is the single highest-blast-radius file if it ever does break silently.
Root cause: the existing `TestMainRouting` tests (`tests/unit/test_adapters.py`) always mock
`_run_hook`/`_run_dispatch` entirely at the call site to test `main()`'s routing logic in isolation —
so neither function's real body was ever actually exercised by any test.

**Desired outcome:**
Two small tests — one invoking `quor hook <unknown-adapter>`, one invoking the plain CLI dispatch
path (`quor git status`-shaped argv) end-to-end — per the audit's own suggested fix.

**Resolution:**
Added `TestMainRealExecution` to `tests/unit/test_adapters.py`, right after `TestMainRouting`:
- `test_run_hook_unknown_adapter_echoes_original_and_warns`: calls the real `_run_hook()` (not
  mocked) with an unknown adapter name, confirms the original stdin bytes are written back to
  stdout unchanged and the warning appears on stderr — reusing the existing `_FakeStdout` helper.
- `test_run_dispatch_real_execution_exits_with_real_code`: calls the real `_run_dispatch()` (not
  mocked) with a real `git status` invocation, confirms it exits with the real code `run_dispatch()`
  returned — exercising the real tracking-DB-open/dispatch/tracking-close/`sys.exit` body, not a
  mocked stand-in.

**Status:** Resolved — implemented on `feature/td-tier5-engineering-hygiene`. `__main__.py` coverage
went from 72% to 92%; the remaining 4 uncovered lines are the Python-version guard (only reachable by
actually running an unsupported Python version, which can't be tested without a real old interpreter)
and the `if __name__ == "__main__":` idiom itself (only runs via a real script invocation, not via
pytest import) — both appropriately out of scope, matching the audit's own framing that this was "a
minor gap, not a hidden safety-critical hole."

---

### QB-031

**Priority:** Medium
**Category:** Documentation

**Title:** Strengthen PreToolUse hook coexistence warnings (TD-009)

**Problem:**
Found during the 2026-07-06 pre-release tech-debt audit (TD-009): `quor doctor` and `quor init
--claude` both detect another tool's `PreToolUse` Bash hook and warn about it, but the wording only
described a vague "double-rewriting risk" and told the user to "review" the conflict — never stating
plainly that only one such hook tool can safely be active at a time, or that the correct action is to
disable the other one. This intersects a real, unfixable-by-Quor Claude Code limitation
([anthropics/claude-code#15897](https://github.com/anthropics/claude-code/issues/15897), closed as a
known limitation): one hook's `updatedInput` can be silently dropped when two are registered for the
same matcher. This is a first-run experience risk for exactly the audience most likely to try Quor
first — developers already using a competing hook tool (RTK/Zap, Headroom AI, Comet).

**Desired outcome:**
State plainly, in both the CLI warning text and `README.md`, that only one `PreToolUse` Bash hook
tool should be active at a time, that Claude Code has no supported way to run two safely, and that
the warning means "disable the other tool," not "safe to ignore."

**Resolution:**
Not a code-behavior fix — a wording one, in three places:
- `quor/cli/commands/doctor.py::_check_hook_collision()`'s warning detail now explains the actual
  risk (silent rewrite drop) and says explicitly to disable the other tool, not just "review."
- `quor/cli/commands/init.py`'s conflict warning (shown during `quor init --claude`) replaced
  "Proceed only if you understand the risk" (which reads as permission to proceed) with an explicit
  "this is not safe to leave as-is" statement.
- `README.md`'s troubleshooting entry for the same doctor check expanded to name the specific Claude
  Code limitation (linked), name example competing tools, and state the required action plainly.

No test asserted the old exact wording for either CLI warning (checked first), so both were changed
directly; the one existing test that does assert an exact substring
(`test_doctor_reports_collision`'s `"1 other Bash hook(s) detected"`) was preserved as a prefix so it
still passes unmodified.

**Status:** Resolved — implemented on `feature/td-tier3-trust-credibility`.

---

### QB-029

**Priority:** Medium
**Category:** Feature

**Title:** Secret detection (PA-F07) and onboarding mode (PA-F08) are not implemented

**Problem:**
Found while walking `RELEASE_CRITERIA.md`'s gates (QB-028 / TD-003): two Public Alpha functional gates
describe features that don't exist anywhere in the codebase, confirmed by grep returning zero matches
for either:
- **PA-F07 (secret detection):** an output line containing a GitHub token pattern (`ghp_...`) should
  cause a warning to stderr, with hook output (stdout) unaffected.
- **PA-F08 (onboarding mode):** the first 5 filtered commands should print brief stats to stderr, with
  command 6 onward silent.

These aren't just unverified — there's no partial implementation, no stage, no flag, nothing to point
to. The competitive research (`docs/archive/product-discovery/competitive-research.md`) also lists
"security-first mode for corporate use" as one of the gaps no existing competitor covers well, so this
has product value beyond just closing the gate.

**Desired outcome:**
A maintainer decides whether these are still wanted for Public Alpha as originally scoped, and if so,
they get implemented and tested per this project's Rule 1 (test requirement) and Rule 4
(competitor-first design, given the competitive-research cross-reference above) before Public Alpha is
declared complete.

**Resolution:** Both implemented as dispatcher-level, cross-cutting concerns (like `tee.py` — neither
touches `ContentMask`/`Pipeline`/`StageHandler`), per Rule 4's consultation of the competitive
research cited above:

- **PA-F07:** `quor/pipeline/secrets.py::scan_for_secrets()` — a small, deliberately narrow set of
  high-confidence token patterns (GitHub, AWS access key ID, Slack, private key headers), not generic
  entropy-based heuristics, matching the research's own "Medium FP" caution for this category.
  Detection only — never redacts. Called from `quor/adapters/dispatcher.py` right before every
  `sys.stdout.write` (both the passthrough and filtered branches, since a secret can appear in either),
  wrapped in the same fail-open `try/except` pattern as every other dispatcher-level concern.
- **PA-F08:** `quor/pipeline/onboarding.py::record_filtered_command()` — a small atomically-written
  counter file (tempfile + `os.replace`, the same pattern as `init.py`'s settings writes), scoped
  globally per machine rather than per-project (onboarding describes a new user's first experience
  with the tool, not with any one project). Deliberately lighter-weight than tee's SQLite-based state
  file: the stakes of a lost race here are a cosmetic double-print at most, not data corruption, so
  tee's WAL-mode machinery would be disproportionate. Called from the dispatcher's filtered
  (non-passthrough) branch only, matching "first 5 *filtered* commands."

**Found and fixed during implementation, before it shipped:** dogfooding the onboarding tip
immediately surfaced the exact QB-017 phenomenon in a new place — a small/already-clean output's tee
footer overhead produced a tip reading "compressed 'mypy' output from 34 to 55 tokens (~-62%
smaller)," which would look exactly like a broken feature in a new user's very first impression of
the tool. Fixed with the same reframing QB-017 already applied to `quor gain`: a net-negative result
is shown as a neutral "already small/clean output" note instead of a misleading negative percentage.

Tests: `tests/unit/test_secrets.py` (10 tests), `tests/unit/test_onboarding.py` (7 tests, 100%
coverage, including the corrupted-state-file and write-failure boundary cases per Rule 1), plus three
new dispatcher-level tests in `tests/unit/test_adapters.py::TestDispatcher` (a real secret surviving
compression warns but stdout is never redacted; no false-positive warning on clean output; 5
consecutive filtered dispatches each tip, the 6th is silent).

**Status:** Resolved — implemented on `feature/qb-029-secret-detection-onboarding`. Full `pytest
tests/` (1020 passed), `pytest tests/ -m integration` (9 passed), `ruff check`, `mypy quor/`, and
`quor verify` (44/44) all pass.

---

### QB-022

**Priority:** Medium
**Category:** Engineering

**Title:** Decompose `run_dispatch()`'s monolithic orchestration for multi-contributor scalability

**Problem:**
Surfaced during a SOLID-principles review of the codebase (2026-07-06): every genuine *extension
point* Quor has — `StageHandler`, `HookAdapter`, `Plugin` — is already cleanly isolated behind a
`Protocol` (`quor/pipeline/stages/base.py`, `quor/adapters/base.py`, `quor/plugins/base.py`), so a
third-party contributor adding a new compression stage, hook adapter, or lifecycle plugin never needs
to touch core files. That extensibility boundary is the right thing to have in place before opening
the project to outside hobby contributors, and it's already solid.

The one place this breaks down is `quor/adapters/dispatcher.py::run_dispatch()` — a single ~150-line
function that inlines seven sequential concerns (subprocess execution, tee cleanup, filter lookup,
plugin discovery/lifecycle, PRE_FILTER execution, ContentMask filtering, POST_FILTER execution, tee
write, tracking), each wrapped in its own fail-open `try/except`. It is not a correctness problem
today — it's tested and every step is defensively isolated — but it is a single-responsibility
violation, and as more contributors touch the project, it's the one function where unrelated PRs
(e.g. one changing tee behavior, another changing plugin ordering) are likely to collide and produce
avoidable merge conflicts.

**Desired outcome:**
Split `run_dispatch()` into a thin orchestrator that delegates to separately named, independently
testable helper functions (e.g. subprocess execution, plugin-pipeline execution, tee + tracking),
with no change to external behavior, the fail-open contract, or the six-CLI-command surface. This is
a mechanical extraction, not a new abstraction or interface — no `Protocol`, registry, or config
schema change is implied.

**Status:** Open — not yet scheduled. Not urgent: revisit when a new dispatch-level step is actually
being added (e.g. an 8th pipeline concern), rather than preemptively. Estimated effort: roughly half a
day, including re-running `tests/unit/test_adapters.py` and `tests/unit/test_pipeline.py` to confirm
no behavioral change. Low risk.

---

### QB-020

**Priority:** Medium
**Category:** Engineering

**Title:** Single source of truth for version numbers, with a drift-detection test

**Problem:**
The 0.3.0 release audit found that `pyproject.toml`'s `[project].version` (what PyPI/`pip show` see)
and `quor/__init__.py`'s `__version__` (what `quor --help`/`python -m quor` print) are two
independently hand-maintained strings with no automated link between them. `tests/unit/test_version.py`
checks that `__version__` is a well-formed, non-empty string but never cross-checks it against
`pyproject.toml`. They have agreed at every release so far purely because whoever bumped the version
remembered to edit both files — nothing would catch it if a future release only updated one.

**Desired outcome:**
One of the two values becomes the sole source of truth and the other is derived from it (e.g.
`quor/__init__.py` reads its version via `importlib.metadata.version("quor")` at runtime instead of
hardcoding a string — the standard approach for this exact problem — falling back to a hardcoded
string only for the editable/uninstalled case if needed), **and** a test exists that fails the build
if the two ever diverge again, so this can't silently regress the way it could have going into 0.3.0.

**Status:** Resolved (Tier 5 engineering hygiene pass). `tests/unit/test_version.py::test_version_matches_pyproject`
already guarded against divergence (confirmed: fails with a mismatch injected, passes once reverted);
the remaining single-source-of-truth half is now also done. `quor/__init__.py::__version__` is
derived via `importlib.metadata.version("quor")` at import time, falling back to a hardcoded string
only when no distribution is found at all (a source checkout never `pip install`'d). Verified this
resolves correctly against the real editable install (`importlib.metadata.version("quor")` ==
pyproject.toml's version == `python -m quor`'s printed output, all "0.3.0"). Two new tests added:
`test_version_derived_from_installed_metadata` (the happy path) and
`test_version_falls_back_when_package_not_found` (the fallback branch, via `importlib.reload()` with
`importlib.metadata.version` patched to raise — the happy path alone wouldn't exercise it, per Rule 1's
boundary-case requirement).

One accepted trade-off worth knowing: for an editable (`pip install -e .`) install, `importlib.metadata`
reads the version captured in `.dist-info` at install time, not live from `pyproject.toml` — so bumping
`pyproject.toml`'s version now also requires re-running `pip install -e .` (or equivalent) for
`test_version_matches_pyproject` to see the new value locally. This is the standard, universally-accepted
trade-off of this approach (not a bug), and doesn't affect real end users installing a built wheel from
PyPI, where the version is baked in correctly at build time.

---

### QB-006B

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

### QB-012

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

### QB-014

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

### QB-013

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

### QB-008

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

### QB-009

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

### QB-010

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

### QB-011

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

**Resolution:**
Implemented under `tests/benchmarks/` (isolated from `quor/` by construction — it only calls the
existing `FilterRegistry`, `count_tokens`, and `content_hash` public surface; no filter, stage, or
compression algorithm was touched). Design reuses the benchmark structure already documented in
`docs/archive/research/design-review.md`'s "Benchmark Suite Design" section (tuple-style cases with
a command, sample file, expected filter, minimum reduction floor, and must-preserve content — same
`must_contain` convention already used by every built-in filter's own inline TOML tests) rather than
inventing a new format.

- **Dataset**: `manifest.toml` + `samples/<category>/` — 12 realistic, hand-written (sanitized —
  fictional names/repos, no real data) samples across all 6 required categories (git-status,
  git-log, git-diff, pytest, mypy, generic), 2 per category.
- **Runner**: `benchmark_runner.py` (engine) + `run_benchmarks.py` (CLI, `python -m
  tests.benchmarks.run_benchmarks`) — deliberately a standalone script, not a new `quor` subcommand,
  to respect the existing "six CLI commands, no more without approval" rule.
- **Metrics**: original/final tokens, tokens saved, compression %, execution time (reported only,
  never gated — wall-clock is too noisy across machines/CI to use as a pass/fail signal), matched
  filter, and whether tee would fire (via `content_hash`, read-only — never calls `write_tee()`).
- **Reports**: JSON (`benchmark-results.json`) and Markdown (`benchmark-report.md`), including
  per-sample results, per-category summary, overall totals, and best/worst performers.
- **Regression detection**: `baseline.json` (committed) compared via percentage-point delta in
  compression (`--regression-threshold`, default 2.0pp); correctness violations (wrong filter,
  missing required content) and min-reduction floor violations are separate, always-fatal checks
  independent of the baseline diff. `--update-baseline` refuses to run if either check is failing.
- **CI integration**: `test_benchmarks.py` runs automatically with `pytest tests/`, so a regression
  fails the build without a separate manual step.
- **Docs**: `tests/benchmarks/README.md` covers adding cases, running, updating the baseline, and
  interpreting each failure type.

One real bug found and fixed during dataset construction (not a runner bug): a "distinct errors, no
repetition" mypy sample accidentally had exactly 3 consecutive `: error:` lines, triggering mypy's
existing shape-based `group_repeated` collapse (min_count=3) despite the messages differing — correct
filter behavior, but it defeated the sample's intended purpose as a no-collapsing contrast case.
Fixed by reducing to 2 errors, below the threshold.

**Status:** Resolved. Full `pytest` (all green, including the new benchmark tests), the standalone
benchmark suite (0 correctness failures, 0 floor violations, 0 regressions against its own
just-created baseline), `quor verify`, `ruff check`, and `mypy quor/` all pass.

---

### QB-002

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

## Priority: Low

### QB-035

**Priority:** Low
**Category:** Feature

**Title:** Multi-agent hook support (Cursor, Copilot, Gemini) and multi-language AST extraction

**Problem:**
Quor's only integration today is the Claude Code `PreToolUse` Bash hook, and `python_ast_summarize`
(QB-005) only understands Python. The competitive research (`docs/archive/product-discovery/competitive-research.md`)
identifies both as real capabilities other tools have — RTK supports 14 AI coding assistants; Headroom
AI's `CodeCompressor` handles Python, JS, Go, Rust, Java, C++ — and lists both explicitly as "v2" in
its own feature matrix, not v1 scope.

**Desired outcome:**
Quor's hook mechanism works with Cursor, GitHub Copilot, and Gemini (or whichever agents prove
relevant), and `cat`'s AST-aware compression extends beyond Python to at least JS/TS.

**Status:** Deliberately not scheduled — large, multi-week-plus effort each (a new hook adapter per
agent with its own PreToolUse-equivalent mechanism and payload shape; a new parser integration per
additional language), not a same-session task like this backlog's other entries. The competitive
research's own conclusion directly governs this: prove the Windows-first Python MVP earns real usage
first (real external testers, multi-hour independent sessions — see QB-029/PA-F09/PA-S01, none of
which are met yet) before investing in market-expansion bets RTK and Headroom AI already lead on.
Revisit only after that validation exists, not on a fixed timeline.

---

### QB-034

**Priority:** Low
**Category:** Feature

**Title:** `quor discover` — retroactive uncovered-command scanning

**Problem:**
Per the competitive research (`docs/archive/product-discovery/competitive-research.md`, Opportunity
7): RTK's `discover` command scans past Claude Code session logs (JSONL) to find commands that ran
unfiltered/uncompressed, ranks them by theoretical savings, and uses that to convert casual installs
into committed users — described there as "the single most important adoption feature" and "a
retroactive audit + adoption accelerator." Quor has no equivalent; `quor gain` only reports what
*did* get compressed, never what was left on the table.

**Desired outcome:**
A command that scans a user's existing Claude Code session logs and surfaces commands Quor never
saw or never matched a filter for, so a new user can see concretely what switching to (or fully
adopting) Quor would have saved them.

**Status:** Deliberately not scheduled. Per the competitive research's own ranking (#7, "important
but not differentiating" — RTK already has this, so Quor would be catching up, not leading) and
Opportunity 1's framing (Quor's actual differentiators are the Windows-first/plugin-system/
transparency angle, not feature parity with RTK), this is real retention value but not worth pulling
forward ahead of items that are genuinely uncontested. Revisit as a retention/adoption investment
once there's an actual user base to retain, not before.

---

### QB-030

**Priority:** Low
**Category:** Engineering

**Title:** Two small follow-ups from the release-criteria gate walk: pre-existing slow CLI tests, and no permanent 10MB-input regression test

**Problem:**
Two minor findings surfaced while walking `RELEASE_CRITERIA.md` (QB-028 / TD-003), neither blocking but
both worth tracking:

1. **PA-Q04 is borderline, not comfortably passing.** The default `pytest` invocation measured 28–31s
   locally across repeated runs — right at the <30s bar, before accounting for CI runners typically
   being slower than local hardware. Confirmed this is dominated by a handful of pre-existing slow CLI
   subprocess tests (e.g. `test_quor_no_args_prints_version` at ~1.6s, several hook-collision tests in
   `tests/unit/test_cli.py` at ~1.5s each) — unrelated to any Tier 1/2 change. QB-028 already fixed the
   one concrete regression risk (excluding `@pytest.mark.integration` tests from this measurement), but
   the underlying pre-existing slowness itself was not investigated or fixed.
2. **IA-S03 (10MB input must not hang >5s) has no permanent regression test.** Verified live this
   session (0.58s, well within budget) but nothing guards this going forward — a future change to the
   pipeline's line-by-line stage handling could silently regress this without any test catching it.

**Desired outcome:**
(1) Identify why the specific slow CLI tests take ~1.5s each (likely real subprocess/process-spawn
overhead) and see if any can be sped up without losing what they verify, to give PA-Q04 real margin
rather than sitting at the edge. (2) Add a permanent test asserting a large (~10MB) input completes
pipeline processing within a fixed time budget.

**Resolution:**
1. **Root cause found:** every test that calls `quor init --claude` (`tests/unit/test_cli.py`'s
   `TestInit` and `TestHookCollisionDetection` classes — 8 tests total) incidentally spawns a real
   PowerShell subprocess via `init.py`'s `_warn_if_execution_policy_restricted()`, regardless of what
   each individual test actually verifies (hook collision, atomic writes, dry-run output — none of
   which have anything to do with the execution-policy check). This, not `test_quor_no_args_prints_version`
   as originally suspected, was the dominant cost.
   - Added an autouse fixture to both classes mocking just that one subprocess call, cutting each
     affected test from ~1–1.5s to ~0.05–0.2s.
   - Added a new `TestExecutionPolicyCheck` class that unit-tests `_warn_if_execution_policy_restricted()`'s
     own branching logic directly (Restricted → warns, RemoteSigned → silent, missing `powershell` /
     timeout → fails open) — so the behavior the fixture mocks away doesn't lose coverage, it moves to
     a focused, still-fast test. The real, fully unmocked PowerShell call remains covered end-to-end by
     the existing `tests/integration/test_cli_commands.py::TestInitAndDoctorIntegration` (QB-027),
     appropriately in the integration tier, not the fast default suite.
   - Also merged `test_version.py`'s `test_quor_no_args_exits_zero` and `test_quor_no_args_prints_version`
     — two tests independently spawning the *identical* `python -m quor` subprocess just to check
     different assertions on the same output — into one test, one spawn. `test_quor_help_exits_zero`
     (a genuinely different invocation) was left as its own real subprocess test; unlike the
     PowerShell case, this one substantively needs a real interpreter spawn to verify the actual entry
     point, so it wasn't a candidate for removal.
   - Measured repeatedly after the fix: 17–28s (some real run-to-run variance on this machine), down
     from the pre-fix 28–31s the gate was failing on. See QB's own `RELEASE_CRITERIA.md` PA-Q04 entry.
2. **IA-S03 regression test added:** `tests/unit/test_filters.py::TestLargeInputPerformance::test_ten_megabyte_input_completes_within_five_seconds` —
   a real 10MB input through the real `FilterRegistry.apply()`, asserting completion under 5s. This
   was previously only verified manually during the gate walk with no permanent guard.

**Status:** Resolved — implemented on `feature/qb-030-test-speed-and-10mb-regression`.

---

### QB-017

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

**Status:** Partially resolved (Tier 3 trust/credibility pass). The underlying metrics-definition
question — distinguishing genuine pipeline compression from tee-footer overhead in the data model
itself — is still deferred; that still needs the product decision described above and is not
attempted here. What *was* fixed, as a presentation-only "quick win" (`quor/cli/commands/gain.py`,
which is documented as presentation-only and never computes a metric):
- When `tokens_saved` is negative, `quor gain` no longer shows it as a celebratory bold-green "YOU
  SAVED -12 tokens" (which reads as a broken feature to a new user). It now shows "NET TOKENS" in a
  neutral style, with an inline note explaining that a negative net is possible on already-small,
  already-clean output and does not mean compression failed.
- Closed `RELEASE_CRITERIA.md`'s **B-S01** gate literally: the footnote now states the actual ±20%
  uncertainty figure (`count_tokens()`'s own documented accuracy) instead of just saying
  "approximation" without a number — also directly the "honest token metrics" gap identified in the
  competitive research (`docs/archive/product-discovery/competitive-research.md`'s Opportunity 5).

Regression test added: `tests/unit/test_cli.py::TestGain::test_negative_net_shown_as_net_not_saved`.
No changes to `quor/tracking/db.py` or the `GainReport`/`InvocationRecord` schema — purely display.

---

### QB-016

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

**Update (Batch 7 — Workflow Review, `feature/qb-003-command-support-docs`):** Re-reviewed against
the current engineering process after QB-011 (compression benchmark suite), per this batch's
explicit request. The branching/PR-checklist/commit-convention rules this item originally
introduced were verified still accurate and were **not** changed (no inconsistency found). Added,
without altering the existing branching strategy: a "Before Opening a PR — Benchmark & Regression
Requirements" subsection (when to run `tests/benchmarks/`, regression-threshold handling, the
new-filter-must-update-COMMAND_SUPPORT.md rule), a "Review Checklist," and a "Release Readiness
Checklist" (cross-referencing `docs/final/RELEASE_CRITERIA.md` and `CONTRIBUTING.md`'s existing
Release Process rather than duplicating it) — all in `docs/final/CLAUDE.md`'s Git Workflow section.
`CONTRIBUTING.md`'s PR checklist and Filter checklist also gained benchmark-requirement lines (see
QB-003's resolution above for the doc-hygiene batch this was part of).

---

### QB-015

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

### QB-003

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

**Status:** Resolved — implemented on `feature/qb-003-command-support-docs` as part of Batch 7
(Product Clarity / Documentation Hygiene). Created `docs/final/COMMAND_SUPPORT.md` as the single
canonical reference: how command detection works (rewrite-layer gating, transparent prefixes,
pipe/structured-output exclusions), the current command allowlist (`_KNOWN_BASE_COMMANDS`,
`_KNOWN_PYTHON_SUBCOMMANDS`), a full command-by-command filter table (what each filter optimizes /
does not optimize, examples, known limitations, source links), filter precedence (three-tier +
first-match-wins-by-load-order, no priority field), fallback behavior (`z_generic.toml`), how new
commands are added, best practices for new filters, and the QB-011 benchmark-coverage requirement.
`README.md` and `docs/final/CLAUDE.md`/`PROJECT_BIBLE.md` now cross-reference this document instead
of restating command/filter detail, per this item's "eliminate duplicated or conflicting
information elsewhere" requirement. Note: `cargo` (named in this item's own Problem text as an
allowlist example) is **not** actually in `_KNOWN_BASE_COMMANDS` — the Problem text was
illustrative, not literal; `COMMAND_SUPPORT.md` documents the real, verified allowlist.

---
