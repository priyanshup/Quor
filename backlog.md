# Backlog

Proposed changes, process improvements, and known gaps for Quor.

**How this file is organized:** [Pending Work](#pending-work) is everything not yet done, at the
top, so it's the first thing anyone sees. [Completed Work](#completed-work) is the historical
record, at the bottom. Within each section, items are grouped **High → Medium → Low priority**.

**Reading an entry:** every item leads with a plain-English summary anyone can follow, no
engineering background required. The technical write-up (root cause, files touched, exact
verification steps) is preserved underneath in a collapsed **Technical details** block — click to
expand it when you need the specifics.

**Effort** is a rough size, not a schedule: **S**mall (hours–a day), **Medium** (a few days),
**Large** (a week or more / multi-part). **Value** is the impact of doing it: **Low / Medium /
High**. Both are judgment calls made while writing this document, not measured numbers.

When adding a new entry: put it in **Pending Work**, under the right priority group, at the top of
that group. When an item is finished, move the whole entry down to the matching priority group
under **Completed Work** (top of that group) and fill in Resolution/Status.

---

## Pending Work

*4 open items.*

### High Priority

#### QB-007 — Smarter reading of documents (PDFs, Word docs, Markdown)

**Effort:** Large · **Value:** High · **Category:** Feature

Right now Quor only shrinks *shell/terminal command* output — it doesn't touch files Claude reads
directly, like a PDF, a Word document, or a long Markdown file. We've confirmed it's technically
possible to hook into that reading step and reduce those documents down to their important
structure (headings, tables, requirements, decisions) instead of sending the whole thing. Nobody
has built it yet — it's a genuinely separate, multi-part project (a new integration point, plus new
handling for each document type), not a quick extension of what exists today.

<details>
<summary>Technical details</summary>

**Problem:** Quor only filters shell command output today. Reading DOCX, PDF, Markdown, or plain
text documents returns raw content with no structure-aware compression.

**Desired outcome:** Token-efficient reading of DOCX, PDF, Markdown, and text documents by
extracting structure — headings, tables, numbered lists, requirements, decisions — instead of
returning raw document text whenever possible.

**Context (Batch 5 design review):** Quor's only integration point today is the Claude Code
`PreToolUse` hook registered for the Bash matcher (`quor/cli/commands/init.py`); most PDF/DOCX
reading inside Claude Code uses native Read/File tools, not Bash, so Quor never receives those
requests under the current architecture.

**Feasibility investigation (2026-07-09, Tier 4): confirmed feasible.** Verified directly against
Claude Code's official hooks reference (`code.claude.com/docs/en/hooks`):
- The `matcher` field for `PostToolUse` (and `PreToolUse`) is a regex against **tool name**, and
  `Read` is a valid, documented match value — same mechanism already used for `Bash`.
- A `PostToolUse` hook receives `tool_name`, `tool_input`, and **`tool_response`** — the file
  content Read just returned — which is what makes compression possible at all.
- A `PostToolUse` hook **can replace that result** before Claude ever sees it, via
  `{"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": "..."}}`. One
  caveat: `updatedToolOutput` being honored for all tools (not only MCP-provided ones) was itself a
  recent Claude Code change, so a minimum version requirement needs pinning down (and a `quor
  doctor` check added for it) before shipping.

**Architectural implication:** a genuinely different integration shape than the existing Bash path.
Today's `PreToolUse` hook rewrites the *command* so Quor's own dispatcher runs the real subprocess
and compresses output before Claude sees it. For Read, Claude Code performs the read itself — no
subprocess for Quor to wrap. The natural shape is a `PostToolUse` hook receiving already-read
content and transforming it via `updatedToolOutput`. Concretely needs: a new hook adapter entry
point alongside `quor/adapters/claude.py`, a new `PostToolUse`/`Read` registration in
`quor init --claude`'s `settings.json` writes, and new content-type-aware stages/filters for
DOCX/PDF/Markdown structure extraction — none of which exists yet.

**Status:** Unblocked — feasibility confirmed. Still not scheduled for implementation; needs its
own scoped design pass (per CLAUDE.md Rule 4) before work begins.

</details>

---

### Low Priority

#### QB-035 — Support more AI coding tools, and more programming languages

**Effort:** Large (multiple multi-week efforts) · **Value:** Medium · **Category:** Feature

Quor currently only works with Claude Code, and its smart Python-summarizing feature (QB-005, done)
only understands Python. Competitors already support more AI assistants (Cursor, GitHub Copilot,
Gemini) and more languages (JS, Go, Rust, Java). Matching that is real long-term value, but each new
assistant and each new language is its own multi-week build — we're deliberately holding off until
Quor has proven it earns real, sustained usage on what it already supports.

<details>
<summary>Technical details</summary>

**Problem:** Quor's only integration today is the Claude Code `PreToolUse` Bash hook, and
`python_ast_summarize` (QB-005) only understands Python. The competitive research
(`docs/archive/product-discovery/competitive-research.md`) identifies both as real capabilities
other tools have — RTK supports 14 AI coding assistants; Headroom AI's `CodeCompressor` handles
Python, JS, Go, Rust, Java, C++ — and lists both explicitly as "v2" in its own feature matrix.

**Desired outcome:** Quor's hook mechanism works with Cursor, GitHub Copilot, and Gemini (or
whichever agents prove relevant), and `cat`'s AST-aware compression extends beyond Python to at
least JS/TS.

**Status:** Deliberately not scheduled — large, multi-week-plus effort each (a new hook adapter per
agent with its own PreToolUse-equivalent mechanism and payload shape; a new parser integration per
additional language). The competitive research's own conclusion governs this: prove the
Windows-first Python MVP earns real usage first (real external testers, multi-hour independent
sessions — see QB-029/PA-F09/PA-S01, none met yet) before investing in market-expansion bets RTK and
Headroom AI already lead on. Revisit only after that validation exists.

</details>

---

#### QB-034 — Show new users what Quor would have saved them, retroactively

**Effort:** Medium · **Value:** Medium · **Category:** Feature

A proposed `quor discover` command would scan a user's past AI coding sessions and show, in
hindsight, how many tokens (and therefore cost/context) Quor would have saved on commands it never
saw. A competitor already has this and uses it to convert casual trials into committed users. Good
adoption value, but not something that sets Quor apart — holding it until there's an actual user
base worth retaining.

<details>
<summary>Technical details</summary>

**Problem:** Per the competitive research (Opportunity 7): RTK's `discover` command scans past
Claude Code session logs (JSONL) to find commands that ran unfiltered/uncompressed, ranks them by
theoretical savings, and uses that to convert casual installs into committed users — described
there as "the single most important adoption feature." Quor has no equivalent; `quor gain` only
reports what *did* get compressed, never what was left on the table.

**Desired outcome:** A command that scans a user's existing Claude Code session logs and surfaces
commands Quor never saw or never matched a filter for, so a new user can see concretely what
switching to (or fully adopting) Quor would have saved them.

**Status:** Deliberately not scheduled. Per the competitive research's own ranking (#7, "important
but not differentiating" — RTK already has this) and Opportunity 1's framing (Quor's actual
differentiators are Windows-first/plugin-system/transparency, not feature parity with RTK), this is
real retention value but not worth pulling forward ahead of genuinely uncontested items. Revisit as
a retention/adoption investment once there's an actual user base to retain.

</details>

---

#### QB-017 — Make the "tokens saved" number always trustworthy

**Effort:** Small–Medium (needs a data-model decision first) · **Value:** Low · **Category:**
Metrics / Observability

The confusing part of this is already fixed: `quor gain` no longer shows a scary "-12 tokens saved"
for tiny commands — it now just says the output was already small/clean. What's still outstanding
is separating, in the underlying data, how much Quor's compression actually saved versus how much a
small bookkeeping footer (the recovery link Quor appends to every output) adds back on top. That's
a data-model decision, not urgent, and low-stakes now that the confusing display is gone.

<details>
<summary>Technical details</summary>

**Problem:** `quor gain` occasionally reports a negative token contribution for an invocation
(confirmed: 7 historical rows). Root cause: the tee recovery mechanism (ADR-023) appends a
fixed-size footer (`\n[full output: {path}]`, ~33 tokens) to the filtered output *before*
`original_tokens`/`final_tokens` are computed. For invocations where genuine compression is smaller
than the footer's fixed cost — common for already-short, already-clean output — the footer's
overhead can exceed the real savings, producing a net "negative" result even though the pipeline
compressed correctly. Not a correctness bug in the pipeline, tee mechanism, or tracking logic — a
**metrics-definition problem**: `final_tokens` conflates "how much the pipeline compressed" with
"how much recovery metadata was appended afterward."

**Desired outcome (future metrics redesign, not yet scoped):** `quor gain` should distinguish
genuine compression savings from intentional dispatcher-level overhead (tee's footer, and any
similar future annotation) rather than netting them silently. Candidate approaches: measure
`final_tokens` before `_apply_tee()` runs (changes existing semantics), or add a distinct field
(e.g. `tee_overhead_tokens`) so both numbers show separately. Either needs a schema/display
decision.

**Status:** Partially resolved (Tier 3 trust/credibility pass). The underlying metrics-definition
question is still deferred. What *was* fixed, as a presentation-only quick win
(`quor/cli/commands/gain.py`): a negative `tokens_saved` no longer shows as a celebratory bold-green
"YOU SAVED -12 tokens" — it now shows "NET TOKENS" in a neutral style with an explanatory note.
Also closed `RELEASE_CRITERIA.md`'s **B-S01** gate by stating the actual ±20% token-count
uncertainty figure instead of just saying "approximation." Regression test:
`tests/unit/test_cli.py::TestGain::test_negative_net_shown_as_net_not_saved`. No schema changes.

</details>

---

## Completed Work

*33 resolved items.*

### High Priority

#### QB-032 — Cleaning up error messages from Python test failures

**Effort:** Small · **Value:** Medium · **Category:** Feature

When a Python test crashes inside library code, the error message included a lot of technical noise
from other people's code, not just yours. Quor now trims that framework noise out automatically
while always keeping your own code's error and location visible.

<details>
<summary>Technical details</summary>

**Problem:** Per the competitive research (Opportunity 6, ranked #6): "Django/Flask/pytest stack
traces are 90% framework frames. Removing them is safe, mechanical, and high-value... RTK doesn't
have this." Quor's `pytest` and `generic` filters previously had no compression for traceback frame
content — individual `File "...", line N, in ...` frames passed through completely untouched.

**Desired outcome:** Framework/library traceback frames compressed out of view, while the user's own
project frames and the actual exception always survive.

**Resolution:** Added one new `strip_lines` pattern to both `pytest.toml` and `z_generic.toml`:
`(?i)^\s*File "[^"]*(?:site-packages|dist-packages)[^"]*", line \d+, in` — matches a frame header
whose path unambiguously means third-party/installed code, verified against real Linux/Windows/venv
paths including negative cases. Deliberately scoped down from removing the whole frame (the header
line alone is compressed; the indented source snippet has no distinguishing marker of its own and is
left untouched — Safety Rule #3: "when uncertain whether to remove a line, keep it"). Bare stdlib
frames are also deliberately not matched (no unambiguous marker on Windows). `z_generic.toml`
previously had no `strip_lines`/`preserve_patterns` at all; added both.

Regression tests in both filters (realistic Django-style traceback). New benchmark case
`pytest-framework-traceback-frames` — 40.9% compression, correctness verified, baseline updated.
`docs/final/COMMAND_SUPPORT.md` updated.

**Status:** Resolved — implemented on `feature/td-tier4-differentiation-roadmap`. Full `pytest
tests/` (993 passed), integration tests (9 passed), `ruff check`, `mypy quor/`, `quor verify`
(44/44), and the compression benchmark suite (29 cases, 0 regressions) all pass.

</details>

---

#### QB-028 — Checked our own release checklist against reality

**Effort:** Medium · **Value:** High · **Category:** Release Process

We had a formal release checklist that nobody had actually gone through and verified — it just sat
there unchecked. We walked every item, confirmed what's really ready for an early "Alpha" release
and what isn't, and turned the gaps we found into their own to-do items (QB-029, QB-030).

<details>
<summary>Technical details</summary>

**Problem:** Found during the 2026-07-06 pre-release tech-debt audit (TD-003): every gate in
`docs/final/RELEASE_CRITERIA.md`, across all four milestones, was still an unchecked `- [ ]` despite
the project being functionally well past Internal Alpha (v0.3.0 published, 983+ tests).

**Desired outcome:** Walk Internal Alpha and Public Alpha gate by gate, record real pass/fail/
evidence for each, and surface any genuinely new gaps found as their own backlog items.

**Resolution:** `RELEASE_CRITERIA.md` updated in place with a dated Gate Walk section and per-gate
evidence.
- **Internal Alpha: passes in full.** Every gate has direct, live evidence except IA-F03, which used
  the closest available proxy (a real, unmocked hook-payload round trip for all five listed
  commands) rather than a literal live interactive Claude Code session.
- **Public Alpha: does not pass yet.** Concrete gaps spun out as QB-029 and QB-030. Gates requiring
  genuinely external state (fresh VM installs, multiple non-builder testers, multi-hour real
  sessions) left unchecked with a note on what's needed.
- **Beta and v1.0 were not walked** — Public Alpha itself doesn't pass yet.

One concrete fix made as a direct result of this walk: the default `pytest` invocation was measured
at 28–31s locally, right at PA-Q04's <30s bar, because nothing actually excluded
`@pytest.mark.integration`-marked tests from it despite docs already claiming they were excluded.
Added `-m "not integration"` to `pyproject.toml`'s `addopts` and a dedicated CI step so the
integration suite still runs on every push/PR.

**Status:** Resolved — implemented on `feature/td-tier2-release-readiness`.

</details>

---

#### QB-027 — Added real tests for all six commands

**Effort:** Medium · **Value:** High · **Category:** Engineering

Our automated tests were checking the six main Quor commands in a "fake" (mocked) way that could
miss real bugs — this is exactly how the Windows npm bug (QB-019) slipped through. We added tests
that actually run the real commands end-to-end, so this class of bug gets caught automatically going
forward.

<details>
<summary>Technical details</summary>

**Problem:** Found during TD-006: `tests/integration/` existed but was empty, and every CLI command
test in `tests/unit/test_cli.py` mocks `subprocess.run` and/or `FilterRegistry` at the boundaries
that matter most. QB-019's Windows npm/npx bug was invisible to the entire test suite specifically
because every dispatcher test mocked `subprocess.run` — the same gap existed for the CLI surface,
the reason `RELEASE_CRITERIA.md`'s **V1-Q07** was still open.

**Desired outcome:** Real integration tests for all six CLI commands (`init`, `validate`, `explain`,
`gain`, `verify`, `doctor`) exercising real subprocess dispatch and a real temp-dir-scoped SQLite
file, per V1-Q07.

**Resolution:** Added `tests/integration/test_cli_commands.py`, marked `@pytest.mark.integration`,
with no mocking of `subprocess.run`, `FilterRegistry`, or `platformdirs` beyond the existing autouse
test-isolation fixture. Verified empirically (via a throwaway script) that a genuinely separate
`quor` OS subprocess could **not** be safely isolated from the real user data directory on this
platform: `platformdirs`' Windows backend resolves paths via ctypes, which ignores
`LOCALAPPDATA`/`APPDATA` overrides entirely. These tests therefore invoke the real command functions
in-process under the existing autouse `platformdirs` fixture, rather than spawning `quor` itself as a
child process.

**Status:** Resolved — implemented on `feature/td-tier2-release-readiness`.

</details>

---

#### QB-026 — Turned on automatic security scanning

**Effort:** Small · **Value:** High · **Category:** Security

Before a public release, we want automatic alerts for outdated/vulnerable dependencies and known
code security issues. Added free, standard GitHub tooling that now runs on a weekly schedule and on
every change.

<details>
<summary>Technical details</summary>

**Problem:** Found during TD-005: no Dependabot config, no CodeQL workflow, and no `pip-audit`/
`bandit` step existed anywhere in `.github/`, despite `SECURITY.md` already discussing trust
boundaries in detail.

**Desired outcome:** Automated dependency update PRs and static security analysis running on a
schedule.

**Resolution:** Added `.github/dependabot.yml` (pip ecosystem, weekly) and
`.github/workflows/codeql.yml` (scheduled weekly plus push/PR to `main`, Python analysis via
`github/codeql-action`). Config-only additions with no effect on `quor/` or `tests/`.

**Status:** Resolved — implemented on `feature/td-tier1-pre-release-fixes`.

</details>

---

#### QB-025 — Test on the Python versions we claim to support

**Effort:** Small · **Value:** Medium · **Category:** Release Process

Quor said it supported Python 3.11 through 3.14, but our automated tests only actually ran on
3.11/3.12 — so 3.13/3.14 support was just a promise, unverified. Added both newer versions to the
automated test matrix.

<details>
<summary>Technical details</summary>

**Problem:** Found during TD-004: `pyproject.toml` declares `requires-python = ">=3.11"` and lists
classifiers for 3.11 through 3.14, but `.github/workflows/ci.yml`'s matrix only ran `3.11`/`3.12`.
Also intersects `RELEASE_CRITERIA.md`'s **B-Q01** gate, which calls for 3.13 in CI at Beta.

**Desired outcome:** CI matrix coverage matches the versions actually claimed as supported.

**Resolution:** Added `3.13` and `3.14` to `ci.yml`'s matrix (crossed with `ubuntu-latest`/
`windows-latest`). Locally re-verified the full suite, `ruff check`, `mypy quor/`, and `quor verify`
all pass under Python 3.14; 3.13 coverage confirmed by CI on the next push.

**Status:** Resolved — implemented on `feature/td-tier1-pre-release-fixes`.

</details>

---

#### QB-024 — Replaced a check that could silently disappear

**Effort:** Small · **Value:** Medium · **Category:** Bug fix

One safety check in the tracking code used a coding shortcut (`assert`) that Python can be told to
skip entirely in some run modes — meaning the safety check could vanish without warning. Replaced it
with a real, unskippable check.

<details>
<summary>Technical details</summary>

**Problem:** Found during TD-002: `TrackingDB._write_jsonl()` used `assert self._jsonl_path is not
None` to guard its only precondition — a direct violation of the project's own rule (CLAUDE.md
Safety Rule #6, `RELEASE_CRITERIA.md` gate **IA-Q07**, "no `assert` in non-test source files used for
validation, grep confirms"). `python -O` strips assertions entirely, silently removing exactly the
guarantee IA-Q07 exists to catch.

**Desired outcome:** The precondition is enforced by a real, non-optimizable check; `grep -rn
"assert " quor/` returns nothing.

**Resolution:** Replaced with `if self._jsonl_path is None: raise RuntimeError(...)`. Added
`test_write_jsonl_raises_if_called_without_path`, which calls `_write_jsonl()` directly (bypassing
the caller's guard) to confirm the check fires as a real error.

**Status:** Resolved — implemented on `feature/td-tier1-pre-release-fixes`. `grep -rn "assert "
quor/` confirmed empty (IA-Q07 now passes). Full test suite green.

</details>

---

#### QB-023 — Fixed a bug that quietly broke redirect commands (e.g. `2>&1`)

**Effort:** Medium · **Value:** High · **Category:** Bug fix

A common shell trick used to redirect error output (`2>&1`) was being mis-rewritten by Quor into
something that meant something different — not just displayed differently, actually changed what
the command did. This was a real, silent correctness bug. It's fixed and now has tests guarding
against it recurring.

<details>
<summary>Technical details</summary>

**Problem:** Found during TD-001 and reproduced live: `quor explain "cd X && python -m quor gain
2>&1"` rewrote the redirect into `2 >& 1`, confirmed against a real shell that `2>&1` and `2 >& 1`
are *not* equivalent. Root cause: the tokenizer split a redirect's leading fd digit into a separate
`WORD` token from the operator; downstream reconstruction re-joined tokens with a space. A second,
more severe variant: for a known (rewritten) command, `parse_args()` only collected
`WORD`/quoted/`ENV_ASSIGN` token kinds, silently dropping the redirect entirely — `pytest 2>&1`
rewrote to `... pytest 2 1`.

**Desired outcome:** `2>&1` and equivalent fd-prefixed redirects survive rewriting with unchanged
shell semantics.

**Resolution:** `quor/rewrite/lexer.py::tokenize()` now merges a digit run immediately followed by
`>`/`<` into a single `REDIRECT_OTHER` token. `parse_args()` now includes `REDIRECT_OTHER` in its
collected token kinds. Verified against a real shell that space *after* the operator is harmless —
only space *before* the fd digit changes behavior. Regression tests added covering the exact repro,
the known-command drop case, multi-digit fds, append (`>>`), and input redirects (`<`).

**Status:** Resolved — implemented on `feature/td-tier1-pre-release-fixes`. Full test suite,
`ruff check`, `mypy quor/`, and `quor verify` (42/42) all pass.

</details>

---

#### QB-021 — Fixed a release-process conflict that would have blocked publishing

**Effort:** Small · **Value:** Medium · **Category:** Bug fix

Our documented release steps and our automated release pipeline both tried to upload the same test
package to the same place, and the second upload would fail outright — which would have blocked
every future release that followed the documented process. Fixed so a repeat upload is simply
ignored instead of failing.

<details>
<summary>Technical details</summary>

**Problem:** Found while walking through the actual 0.3.0 release: `CONTRIBUTING.md`'s documented
Release Process has the maintainer manually trigger `publish-testpypi.yml` before tagging, as a
dry-run. But `release.yml` (triggered by the tag push) runs its own, separate `publish-testpypi` job
as the first step of the gated production chain — re-uploading the identical wheel/sdist for a
version already on TestPyPI. Neither workflow set `skip-existing`, so TestPyPI's rejection of the
duplicate upload would hard-fail the job, blocking every downstream job (the exact chain QB-001
built to gate production publishes).

**Desired outcome:** The documented dry-run-then-tag workflow no longer fails, without changing what
gets published or weakening the `release-approval` gate.

**Resolution:** Added `skip-existing: true` to the `publish-testpypi` step in both `release.yml` and
`publish-testpypi.yml`. Re-uploading an already-published version is now a no-op; a genuinely new
version still publishes normally.

</details>

---

#### QB-019 — Fixed npm/yarn tools not running at all on Windows

**Effort:** Medium · **Value:** High · **Category:** Bug fix

On Windows specifically — Quor's primary platform — commands using npm, npx, pnpm, or yarn silently
failed to run at all through Quor, meaning JavaScript/TypeScript developers got nothing. Root cause
was a Windows-specific quirk in how Quor launched programs. Fixed, with a new test that actually
spawns a real process so this can't silently break again.

<details>
<summary>Technical details</summary>

**Problem:** A production-readiness validation (run against real commands via `run_dispatch()`
directly, not mocked) found that `npm`, `npx`, `pnpm`, and `yarn` fail unconditionally on Windows
with `FileNotFoundError: [WinError 2]`. These tools ship as `.CMD` shell shims, not native `.exe`
binaries; `subprocess.run(args)` without `shell=True` uses Windows' `CreateProcess`, which doesn't
apply `PATHEXT` extension resolution the way a real shell does. Every existing dispatcher test mocks
`subprocess.run` entirely, which is exactly why this was invisible to the test suite, `quor verify`,
and the benchmark suite.

**Desired outcome:** `npm`/`npx`/`pnpm`/`yarn` actually execute through `run_dispatch()` on Windows,
with no new security surface, and a regression test that spawns a real subprocess.

**Resolution:** `quor/adapters/dispatcher.py::run_dispatch()` now resolves `args[0]` via
`shutil.which()` before calling `subprocess.run()`, falling back to the original token unchanged if
not found. `shell=False` is preserved. See ADR-033 in `docs/final/DECISIONS.md`. Added
`test_windows_shell_shim_executable_resolves_and_runs`, which spawns a real throwaway `.cmd` shim
(skipped on non-Windows) — confirmed to fail with exit code 127 on the pre-fix code and pass on the
fix.

**Status:** Resolved — implemented on `feature/qb-003-command-support-docs`.

</details>

---

#### QB-018 — Fixed several bugs in usage-tracking accuracy

**Effort:** Large · **Value:** High · **Category:** Bug fix

Investigating a report that "quor gain" (the savings dashboard) had stalled uncovered four separate,
real bugs in how Quor identifies "which project" a command belongs to — including two different
project folders sometimes getting merged together, and one case where a bad folder name could
accidentally sweep in data from an entire unrelated drive. All fixed, with tests, and verified
against real historical data.

<details>
<summary>Technical details</summary>

**Problem:** Investigation into "`quor gain` stopped increasing" found the plateau itself was
expected (real recent activity dominated by zero-savings git plumbing commands), but surfaced a
chain of real, separate correctness bugs in `quor/tracking/db.py`'s project-scoping: (1)
`project_path` was matched case-sensitively, so a project recorded under two different casings
silently split into two untracked halves; (2) a naive `GLOB "{project}*"` prefix match had no
path-separator boundary, so `/workspace` incorrectly swept in the unrelated sibling
`/workspace-other`; (3) the project key was spliced unescaped into a GLOB pattern, so a directory
name containing `*`/`?`/`[`/`]` was silently reinterpreted as a wildcard; (4) a degenerate query key
turned the subdirectory pattern into a match-everything wildcard, sweeping in every unrelated project
on a whole drive.

**Desired outcome:** A single, deterministic, well-tested project-identity model with no duplicated
normalization logic, no schema migration required, and no behavioral change to real historical data.

**Resolution:** Added `normalize_project_path()` as the sole definition of project identity. Added a
precomputed `project_key_normalized` column (schema v2, nullable, backward-compatible), populated at
write time. Historical rows lazily backfilled by `query_gain()` on first read via a registered SQL
function (a hand-written SQL approximation was tried and rejected — SQLite's `LOWER()` only folds
ASCII and doesn't normalize separators the way the real function does). Matching moved from `GLOB` to
`LIKE` with proper escaping. Degenerate query keys rejected outright with a clear `ValueError`. An
unused `project_prefix` column (written but never read) removed entirely.

**Status:** Resolved. Full test suite, `quor verify`, `ruff check`, and `mypy` all pass. Comprehensive
regression tests covering case-insensitivity, sibling-leakage exclusion, subdirectory inclusion,
GLOB/LIKE metacharacter escaping, degenerate-key rejection, and lazy backfill.

</details>

---

#### QB-006A — Basic support for the Node.js/JavaScript toolchain

**Effort:** Medium · **Value:** High · **Category:** Feature

Quor previously did nothing for npm/npx/pnpm/yarn commands — a big gap for JavaScript/TypeScript
developers. Added filtering that strips out the generic noise these tools produce (progress
spinners, deprecation spam, install summaries) while leaving the actual test/build/lint output
intact.

<details>
<summary>Technical details</summary>

**Problem:** Split from QB-006. `npm`, `npx`, `pnpm`, and `yarn` invocations passed through Quor
unfiltered and untracked — `npm` wasn't in `_KNOWN_BASE_COMMANDS` at all, and `npx`/`pnpm`/`yarn`
were only registered as transparent prefixes. Even without tool-specific intelligence, the CLI
wrapper itself produces a large amount of generic, low-signal noise.

**Desired outcome:** Rewrite rules and a built-in filter stripping generic wrapper noise only —
`npm WARN` spam, progress/ANSI output, audit messages, install summaries — using only existing stage
types. Tool-specific intelligence for what runs underneath (Jest, ESLint, TypeScript, etc.) is
explicitly out of scope, tracked separately as QB-006B.

**Resolution:** `quor/filters/builtin/node.toml` adds four `[[filter]]` blocks (npm, npx, pnpm,
yarn), composed from `remove_ansi`, `group_repeated`, `strip_lines` (with a `preserve_patterns`
safety net for errors/vulnerabilities/summaries), and `deduplicate_consecutive`. Deliberately no
`max_tokens` stage — these commands can wrap an arbitrary underlying command, and a token budget
risked truncating that wrapped tool's real output. Required classifier change: `npm` added to
`_KNOWN_BASE_COMMANDS`; `npx`/`pnpm`/`yarn` removed from `TRANSPARENT_PREFIXES`. This had a wide test
blast radius since these commands were previously used throughout the test suite as the canonical
"unknown command" example — 7 test files updated.

**Status:** Implemented (Batch 5, item 2). Comprehensive tests in `test_filter_safety.py` plus
inline filter tests and updated classifier tests. Full test suite, `quor verify`, `ruff check`, and
`mypy` all pass.

</details>

---

#### QB-004 — Investigated why a git-diff size limit wasn't being respected

**Effort:** Small · **Value:** Low · **Category:** Bug Investigation

A configured "keep this under 600 tokens" limit for `git diff` output wasn't being honored.
Investigation found this was working as designed — the limit deliberately never touches lines marked
"always keep" (the actual diff content), so a big diff can still exceed the target. Not a bug; led to
a follow-up product decision (QB-012, resolved below).

<details>
<summary>Technical details</summary>

**Problem:** Measured output from `quor git show`/`git diff` (~5,806 estimated tokens) greatly
exceeds the `git-diff` filter's configured `max_tokens` limit of 600. Root cause unknown at the time.

**Desired outcome:** Root cause identified and either the stage fixed to enforce its limit, or the
discrepancy documented.

**Resolution:** Confirmed `max_tokens` executes correctly and enforces its budget exactly as
documented. The overshoot is caused by `git-diff`'s `preserve_patterns` marking most diff content as
protected, which `max_tokens` is designed to never compress — measured at 298 of 515 lines protected,
summing to ~5,265 tokens alone, above the 600 limit before `max_tokens` even runs. Expected behavior
given current configuration, not a stage defect.

**Status:** Closed — Not a bug.

</details>

---

#### QB-005 — Smarter Python file reading (structure instead of full text)

**Effort:** Large · **Value:** High · **Category:** Feature

When Claude reads a Python file through Quor, it now gets a compressed view — full function
signatures and docstrings, but function bodies summarized — instead of the entire file every time.
This significantly cuts token usage on large Python files while keeping the information Claude
actually needs to work with the code. If anything about a file confuses the summarizer, it safely
falls back to sending the original, unmodified content rather than risk sending something wrong.

<details>
<summary>Technical details</summary>

**Problem:** Quor's `cat` filter only stripped comments and blank lines; it always returned full
source content otherwise. For large files this left significant token cost on the table.

**Desired outcome:** An AST-aware or parser-assisted code summarization mode prioritizing imports,
public types, function/method signatures, docstrings, constants, and file structure over full
function bodies.

**Approved architecture (Batch 5 design review):** Python only in V1, using only the standard
library `ast` module (no new dependency). `StageHandler`'s interface not modified — stages continue
to receive only content, never a filename. Python detection happens at the filter layer via command
matching; a new `cat-python.toml` filter routes `.py` reads to the new stage. No new registry
tie-break algorithm — correctness comes entirely from built-in filter load order (`cat-python.toml`
before `cat.toml`). Fail-open on any parsing failure — falls back to full, unmodified content, never
a crash or partial output.

**Resolution:** `quor/pipeline/stages/python_ast_summarize.py` compresses function/method bodies to
signature + docstring using stdlib `ast` only, with fail-open delegated to the engine's existing
per-stage exception handling. `cat-python.toml` routes `.py` reads through it, then reuses
`cat.toml`'s existing strip_lines/deduplicate_consecutive/max_tokens stack so comment-stripping and
blank-line dedup aren't lost for Python files. Comprehensive unit tests
(`TestPythonAstSummarize`): valid file, syntax error at both stage and pipeline fail-open level,
empty file, null-byte input, decorators, nested classes/functions, async functions, a 300-function
synthetic large file, non-ASCII identifiers/docstrings, single-line and docstring-only bodies, and
byte-identical-kept-line regression tests.

**Status:** Implemented (Batch 5, item 1). Full `pytest`, `quor verify`, `ruff check`, and `mypy` all
pass. Committed (`95328a3`).

</details>

---

#### QB-006 — *(superseded)* Original "Node.js support" request

**Effort:** N/A · **Value:** N/A · **Category:** Feature

This was the original, broad "support Node.js" request. It was later split into two more precisely
scoped items — [QB-006A](#qb-006a--basic-support-for-the-nodejsjavascript-toolchain) and
[QB-006B](#qb-006b--smarter-handling-for-one-specific-js-tool-eslint), both done — so this entry is
kept only for historical record.

<details>
<summary>Technical details</summary>

**Problem:** Quor had no rewrite/filter coverage for `npm`, `npx`, or `pnpm` — a significant
ecosystem gap relative to competitors.

**Desired outcome:** Rewrite rules and filters for `npm`/`npx`/`pnpm` invocations, prioritized by
workflow: build, test, lint, and type-check first.

**Status:** Split following the Batch 5 design review — see QB-006A (generic Node ecosystem noise
removal) and QB-006B (tool-aware Node ecosystem filtering). This entry is kept for historical
context; new work is tracked under QB-006A/QB-006B.

</details>

---

#### QB-001 — Require a safety check before publishing new releases

**Effort:** Small · **Value:** High · **Category:** Release Process

Previously, tagging a new release published it straight to the public package registry (PyPI) with
no verification step. Added a required gate: a release must first be test-published and verified,
then explicitly approved by a maintainer, before it can go out for real.

<details>
<summary>Technical details</summary>

**Problem:** `release.yml` published directly to PyPI after tagging, bypassing manual TestPyPI
verification.

**Desired outcome:** Production publication must require successful TestPyPI validation and explicit
approval.

**Status:** Resolved — implemented on `feature/qb-001-testpypi-release-gate`
(`.github/workflows/release.yml`). `publish-pypi` now needs a `release-approval` environment job,
which needs `validate-testpypi` (installs the tagged version from TestPyPI and smoke-tests it),
which needs `publish-testpypi`. A maintainer must still create the `release-approval` environment
with required reviewers under Settings > Environments for the approval gate to be enforced.

</details>

---

### Medium Priority

#### QB-022 — Simplify the code that runs every command

**Effort:** Small (~half a day) · **Value:** Low · **Category:** Engineering

One internal function had grown to handle seven different jobs at once (running the command,
cleanup, filtering, tracking, and more). It worked correctly, but as more people contribute code,
unrelated changes were likely to collide in this one spot. Split into smaller, named pieces so
future changes are safer to review — purely internal code health, no visible change for users.

<details>
<summary>Technical details</summary>

**Problem:** Surfaced during a SOLID-principles review (2026-07-06): every genuine *extension
point* Quor has — `StageHandler`, `HookAdapter`, `Plugin` — is already cleanly isolated behind a
`Protocol`, so third-party contributors never need to touch core files for those. The one place
this broke down was `quor/adapters/dispatcher.py::run_dispatch()` — a single ~150-line function
inlining seven sequential concerns (subprocess execution, tee cleanup, filter lookup, plugin
discovery/lifecycle, PRE_FILTER execution, ContentMask filtering, POST_FILTER execution, tee write,
tracking), each wrapped in its own fail-open `try/except`.

**Desired outcome:** Split `run_dispatch()` into a thin orchestrator delegating to separately named,
independently testable helper functions, with no change to external behavior, the fail-open
contract, or the six-CLI-command surface. A mechanical extraction, not a new abstraction.

**Resolution:** `run_dispatch()` cut from ~165 to ~55 executable lines. Six new private helpers
added — `_run_subprocess`, `_lookup_filter`, `_setup_plugins`, `_run_pre_filter_plugins`,
`_apply_content_filter`, `_run_post_filter_plugins` — joining the six that already existed
(`_cleanup_tee_safe`, `_apply_tee`, `_teardown_plugins`, `_track`, `_scan_secrets_safe`,
`_maybe_print_onboarding_tip_safe`). Purely mechanical: execution order, fail-open semantics, and
every existing log/warning message preserved exactly. Plugin-subsystem imports stayed local/lazy
inside the new helpers rather than being hoisted to module level, so per-invocation import cost is
unchanged; a `TYPE_CHECKING`-guarded import (zero runtime cost) was added so the new helpers could
carry real `PluginRegistry`/`PluginContext` type hints instead of `object`.

**Status:** Resolved — implemented on `feature/qb-022-simplify-dispatcher` (PR #38). Full `pytest
tests/`, `quor verify` (44/44), `ruff check`, and `mypy quor/` all pass. The one test-suite failure
present (`test_version_matches_pyproject`) was confirmed pre-existing and unrelated via a
stash-comparison against the unmodified tree.

</details>

---

#### QB-033 — Closed a test-coverage gap in the most critical file

**Effort:** Small · **Value:** Low · **Category:** Engineering

The file that decides how every single command gets routed had the weakest test coverage in the
whole project. Added two tests that exercise its real logic directly — not a simulated version — so
a break here can't slip through silently.

<details>
<summary>Technical details</summary>

**Problem:** Found during TD-010: `__main__.py` had the lowest test coverage in the codebase (72%),
concentrated in the "unknown hook adapter" branch and the `_run_dispatch()` CLI-entry wrapper —
not the safety-critical top-level fail-open guard (already covered). Root cause: existing tests
always mocked `_run_hook`/`_run_dispatch` entirely, so neither function's real body was ever
exercised.

**Desired outcome:** Two small tests — one invoking `quor hook <unknown-adapter>`, one invoking the
plain CLI dispatch path end-to-end.

**Resolution:** Added `TestMainRealExecution`: `test_run_hook_unknown_adapter_echoes_original_and_warns`
(calls the real `_run_hook()`, confirms original stdin bytes are echoed back and a warning appears
on stderr) and `test_run_dispatch_real_execution_exits_with_real_code` (calls the real
`_run_dispatch()` with a real `git status` invocation).

**Status:** Resolved — implemented on `feature/td-tier5-engineering-hygiene`. `__main__.py` coverage
went from 72% to 92%; the remaining 4 uncovered lines (Python-version guard, `__main__` idiom) are
appropriately out of scope.

</details>

---

#### QB-031 — Made the "you have two hook tools installed" warning clearer

**Effort:** Small · **Value:** Medium · **Category:** Documentation

If a user already has a competing tool installed, Quor detects the conflict but the old wording just
said "review this" — vague enough to read as safe to ignore. It isn't: only one such tool can safely
run at a time. Reworded the warning, in the app and the docs, to say plainly that the other tool
needs to be disabled.

<details>
<summary>Technical details</summary>

**Problem:** Found during TD-009: `quor doctor` and `quor init --claude` both detect another tool's
`PreToolUse` Bash hook and warn about it, but the wording only described a vague "double-rewriting
risk" and told the user to "review" — never stating plainly that only one such hook tool can safely
be active, or that the fix is to disable the other one. Intersects a real, unfixable-by-Quor Claude
Code limitation (anthropics/claude-code#15897, closed as a known limitation): one hook's
`updatedInput` can be silently dropped when two are registered for the same matcher.

**Desired outcome:** State plainly, in both CLI warning text and README, that only one `PreToolUse`
Bash hook tool should be active at a time, and that the warning means "disable the other tool," not
"safe to ignore."

**Resolution:** A wording fix in three places: `doctor.py`'s warning now explains the actual risk
(silent rewrite drop) and says explicitly to disable the other tool; `init.py`'s conflict warning
replaced "Proceed only if you understand the risk" with an explicit "this is not safe to leave
as-is" statement; `README.md`'s troubleshooting entry names the specific Claude Code limitation
(linked) and states the required action plainly.

**Status:** Resolved — implemented on `feature/td-tier3-trust-credibility`.

</details>

---

#### QB-029 — Added secret-leak detection and a friendlier first-run experience

**Effort:** Large · **Value:** High · **Category:** Feature

Two promised features didn't exist yet: (1) warning the user if a command's output contains
something that looks like a real API key/token, and (2) showing a brief "here's what just got
compressed" tip for a new user's first few commands, then going quiet. Both are now built and
tested.

<details>
<summary>Technical details</summary>

**Problem:** Found while walking `RELEASE_CRITERIA.md`'s gates (QB-028): two Public Alpha functional
gates describe features with zero implementation anywhere in the codebase — **PA-F07** (secret
detection: a GitHub-token-shaped output line should warn to stderr, hook stdout unaffected) and
**PA-F08** (onboarding mode: the first 5 filtered commands print brief stats to stderr, command 6
onward silent). The competitive research also lists "security-first mode for corporate use" as a
gap no competitor covers well.

**Desired outcome:** A maintainer decides whether these are still wanted for Public Alpha, and if
so, implements and tests them.

**Resolution:** Both implemented as dispatcher-level, cross-cutting concerns (like `tee.py`):
- **PA-F07:** `quor/pipeline/secrets.py::scan_for_secrets()` — a deliberately narrow set of
  high-confidence token patterns (GitHub, AWS access key ID, Slack, private key headers), not
  generic entropy heuristics. Detection only — never redacts. Called right before every stdout
  write, wrapped in the same fail-open pattern as every other dispatcher-level concern.
- **PA-F08:** `quor/pipeline/onboarding.py::record_filtered_command()` — a small atomically-written
  counter file, scoped globally per machine. Called from the dispatcher's filtered branch only.

**Found and fixed during implementation:** dogfooding the onboarding tip surfaced the same QB-017
phenomenon in a new place — a small/already-clean output's tee footer overhead produced a
misleading negative-looking tip. Fixed with the same reframing QB-017 applied to `quor gain`.

**Status:** Resolved — implemented on `feature/qb-029-secret-detection-onboarding`. Tests:
`test_secrets.py` (10 tests), `test_onboarding.py` (7 tests, 100% coverage), plus 3 new
dispatcher-level tests. Full test suite (1020 passed), integration tests (9 passed), `ruff check`,
`mypy quor/`, and `quor verify` (44/44) all pass.

</details>

---

#### QB-020 — Made the version number impossible to get out of sync

**Effort:** Small · **Value:** Medium · **Category:** Engineering

Quor's version number was manually typed in two separate places, with nothing checking they
matched — a future release could easily ship with mismatched numbers. Now one place is the single
source of truth and the other reads from it automatically, with a test that fails the build if they
ever disagree.

<details>
<summary>Technical details</summary>

**Problem:** The 0.3.0 release audit found `pyproject.toml`'s `[project].version` and
`quor/__init__.py`'s `__version__` are two independently hand-maintained strings with no automated
link. They'd agreed at every release so far purely because whoever bumped the version remembered to
edit both files.

**Desired outcome:** One value becomes the sole source of truth and the other is derived from it,
and a test exists that fails the build if they ever diverge.

**Resolution:** `tests/unit/test_version.py::test_version_matches_pyproject` already guarded against
divergence; the remaining single-source-of-truth half is now done too.
`quor/__init__.py::__version__` is derived via `importlib.metadata.version("quor")` at import time,
falling back to a hardcoded string only when no distribution is found. Two new tests:
`test_version_derived_from_installed_metadata` and `test_version_falls_back_when_package_not_found`.

One accepted trade-off: for an editable install, `importlib.metadata` reads the version captured at
install time, not live from `pyproject.toml` — so bumping the version now also requires re-running
`pip install -e .` locally. Standard, universally-accepted trade-off; doesn't affect real end users
installing a built wheel from PyPI.

**Status:** Resolved (Tier 5 engineering hygiene pass).

</details>

---

#### QB-006B — Smarter handling for one specific JS tool (ESLint)

**Effort:** Medium · **Value:** Medium · **Category:** Feature

Building on QB-006A, added dedicated, precise compression for ESLint (a common JavaScript
code-quality tool) when run through npm/npx/yarn/pnpm — matching the same quality bar as Quor's
Python-test and type-checking support. Other tools (Prettier, Jest, TypeScript) weren't built yet
since nobody's asked for them; they safely fall back to the generic handling from QB-006A.

<details>
<summary>Technical details</summary>

**Problem:** Split from QB-006. `npm test` / `npm run build` / `npx <tool>` / `yarn build` are
opaque wrappers — the actual underlying tool is defined in `package.json` and invisible to Quor's
command-string-based filter matching.

**Desired outcome:** Tool-aware compression for common JS/TS toolchain output with the same
PROTECT/`preserve_patterns` precision as `pytest.toml`/`build.toml` today.

**Resolution:** Implemented at a deliberately narrower scope than originally framed: routing only
covers invocation shapes where the real tool name is **already present in the command string** —
`npx eslint`, `npm exec eslint`, `pnpm exec/dlx eslint`, `yarn exec eslint`, and yarn classic's bare
`yarn eslint`. `npm test` / `npm run build` / any `<wrapper> run <script>` form is explicitly and
permanently excluded — the script name is a `package.json` alias, and resolving it would require
reading `package.json`, which stays out of scope by requirement. Pure command-string pattern
matching in `FilterRegistry`, no new stage or content-type change.

`quor/filters/builtin/node.toml` gained a new `eslint` `[[filter]]` block, placed before the generic
npm/npx/pnpm/yarn blocks (specificity-via-ordering, same idiom as `cat-python.toml`/`cat.toml`).
Only `eslint` gets a real filter — `prettier`/`jest`/`tsc` fall through to the generic filter
(QB-006A behavior), not built speculatively.

**Follow-up refinement (before commit):** the initial `group_repeated` config collapsed any
consecutive violation-shaped lines together regardless of message, meaning two genuinely different
rule violations on adjacent lines would merge into one collapsed count. Fixed with an opt-in
`exact_match: bool = False` field on `GroupRepeatedConfig` (default `False` preserves mypy's
existing same-message-different-line-number collapsing) — only the `eslint` filter sets it to
`True`.

**Status:** Implemented. Tests: `test_node_tool_routing.py` (new), `TestEslintFilterSafety`, plus
regression tests for the `group_repeated` refinement. Full test suite, `quor verify`, `ruff check`,
and `mypy` all pass.

</details>

---

#### QB-012 — Decided what happens when "always keep" content is bigger than the size budget

**Effort:** Small · **Value:** Medium · **Category:** Product Decision

A product decision was needed for a specific edge case: what should happen when content that's
flagged "never compress this" is already bigger than the configured token limit? Decided: the limit
is a target, not an absolute cap — protected content is never sacrificed to hit the number.
Documented as an official decision (ADR-031); no behavior changed, since this matched what the
product already did.

<details>
<summary>Technical details</summary>

**Problem:** QB-004's investigation confirmed `max_tokens` executes correctly, but when `PROTECT`
lines alone exceed the configured budget, the limit cannot be enforced — it silently becomes a
no-op for that content. No documented, decided answer existed for what should happen.

**Desired outcome:** A maintainer decides and documents the intended semantics among: (1) best-effort
budget (protected lines never compressed, even over limit), (2) hard budget (protected lines may be
compressed to stay under limit), or (3) priority-based budgeting (multiple protection levels).

**Resolution:** Decided: Option 1, best-effort budget. Recorded as ADR-031. `max_tokens` remains a
target that only ever compresses KEEP lines; PROTECT always takes precedence. Formalizes existing
shipped behavior — no runtime or filter-configuration changes. Two follow-ups spun out: QB-013 (tee
mechanism decided but not implemented) and QB-014 (mypy `group_repeated` ordering issue).

**Status:** Resolved — see ADR-031.

</details>

---

#### QB-014 — Fixed duplicate error messages not being collapsed for one tool

**Effort:** Small · **Value:** Medium · **Category:** Bug Investigation

When running `mypy` (a Python type-checker), repeated identical error lines weren't being collapsed
into "(×3)" the way they were supposed to — a bug in the order two internal steps ran in. Fixed the
ordering and a related edge case, with before/after comparisons confirming nothing else changed.

<details>
<summary>Technical details</summary>

**Problem:** Found during the QB-012 investigation: `build.toml`'s `mypy` filter ran `strip_lines`
(marking error/warning/note lines PROTECT) before `group_repeated` (meant to collapse repeated
identical error lines). Since `group_repeated` treats PROTECT lines as run breakers, it never
actually collapsed anything for `mypy` as ordered — effectively a no-op.

**Desired outcome:** Confirm the no-op with a reproduction, then decide the fix: reorder stages,
narrow `preserve_patterns`, or confirm current behavior is acceptable.

**Resolution:** Confirmed and fixed (PR #2). A naive reorder alone was insufficient — `strip_lines`'s
preserve-pattern check re-evaluated every line regardless of an existing `COMPRESS` decision, so it
resurrected duplicates `group_repeated` had just compressed. Final solution: reordered the `mypy`
pipeline to `group_repeated` → `strip_lines` → `max_tokens`, and updated `strip_lines.py` so the
preserve-pattern check skips lines already marked `COMPRESS`. Byte-for-byte before/after comparison
confirmed identical output for every other filter (dependency review found this guard change was
dead code everywhere except `mypy`).

**Status:** Resolved. `quor verify` 25/25, `pytest tests/` 612 passed.

</details>

---

#### QB-013 — Built the promised "nothing is ever truly lost" safety net

**Effort:** Large · **Value:** High · **Category:** Feature

Quor's design docs promised that whenever it compresses output, it also saves a full, uncompressed
copy somewhere recoverable, with a pointer/link left behind — but that safety net had only ever been
decided on paper, not built. It's now implemented: every command's original output is cached, a
"[full output: ...]" link is added, old cached copies clean up automatically after a week, and it
can be turned off per-command or globally if unwanted.

<details>
<summary>Technical details</summary>

**Problem:** ADR-023 and `PROJECT_BIBLE.md` both document a tee mechanism — cache the original
output before compression and append a `[full output: path]` pointer, so aggressive compression is
safe because "nothing is irrecoverably lost." ADR-023 is marked `Decided`, but no implementation
existed. This became directly relevant while resolving QB-012 (best-effort `max_tokens` budgets rely
on the tee mechanism as the safety net).

**Desired outcome:** Implement the tee mechanism per ADR-023: cache original output, append the
footer, support per-filter opt-out, and clean up tee files older than 7 days.

**Resolution:** Implemented on `feature/qb-013-tee-mechanism` (PR #8, hardening fix PR #9).
Dispatcher-level only, no pipeline/stage changes. SHA256 content-addressed storage under
`~/.local/share/quor/tee/`, with dedup + mtime refresh on cache hit. Footer appended post-pipeline
(not subject to `max_tokens`). 7-day TTL cleanup, throttled via a separate WAL-mode state DB
(hardened against concurrent-open lock contention). Global and per-filter opt-out, both
backward-compatible defaults.

</details>

---

#### QB-008 — Added a general find-and-replace tool for output

**Effort:** Small · **Value:** Medium · **Category:** Enhancement

Added a reusable building block that lets any filter normalize noisy text (long file paths,
timestamps, random IDs) using find-and-replace patterns — useful for any future filter, not just one
specific tool.

<details>
<summary>Technical details</summary>

**Problem:** Quor's pipeline had no general-purpose regex substitution stage. Repeated high-entropy
content (paths, timestamps, UUIDs, hashes) in command output couldn't be normalized.

**Desired outcome:** A configurable regex replacement stage with backreference support, chainable
like existing stages.

**Resolution:** Implemented as the `regex_replace` stage. Ordered list of `{pattern, replacement}`
rules per filter, applied via `regex.sub()`. PROTECT lines and `preserve_patterns` matches are never
modified, matching every other stage's invariant.

</details>

---

#### QB-009 — Added a way to cap very long lines

**Effort:** Small · **Value:** Medium · **Category:** Enhancement

Added a reusable building block that trims individual lines that run unusually long (huge JSON
blobs, giant stack traces) — since a handful of long lines can bloat token usage even when
everything else is under control.

<details>
<summary>Technical details</summary>

**Problem:** Quor had no stage to cap individual line length.

**Desired outcome:** A configurable max-line-length stage, similar to ZAP's `truncate_lines_at`.

**Resolution:** Implemented as the `truncate_lines` stage. Caps KEEP line length to `max_length`,
appending a configurable `marker`. Line count never changes. PROTECT lines and `preserve_patterns`
matches are exempt.

</details>

---

#### QB-010 — Added a "recognize this whole pattern instantly" shortcut

**Effort:** Small · **Value:** Medium · **Category:** Enhancement

Added a reusable building block that lets a filter recognize a full, predictable output (like "clean
git status") and swap in an instant short summary — skipping unnecessary processing and guaranteeing
consistent results for known-good cases.

<details>
<summary>Technical details</summary>

**Problem:** Quor's only whole-output shortcuts were the narrower `abort_unless`/`on_empty`
filter-level options — no general stage could match the entire output against a pattern and
immediately substitute a short summary.

**Desired outcome:** A pipeline stage that short-circuits to an immediate compressed result when the
complete output matches a predefined pattern.

**Resolution:** Implemented as the `match_output` stage. Explicit opt-in per filter; fullmatches the
current rendered output. Refuses to fire if any PROTECT line is already present, avoiding a class of
index-collision bugs. Emits an explicit warning on every fire, in addition to the normal `quor
explain` stage trace.

</details>

---

#### QB-011 — Built a way to measure whether Quor is actually working well

**Effort:** Large · **Value:** High · **Category:** Engineering

Quor had no repeatable way to prove how much it actually saves, or to catch it if a future change
accidentally made compression worse. Built a benchmark suite — a fixed set of realistic sample
commands that gets run automatically, measuring token savings and flagging any regression before it
ships.

<details>
<summary>Technical details</summary>

**Problem:** Quor had no repeatable way to measure token reduction, latency, or compression quality
across a fixed corpus, and no way to track whether a pipeline change is an improvement or
regression. Surfaced during a ZAP efficiency comparison, where neither tool had proven, benchmarked
numbers to point to.

**Desired outcome:** A repeatable benchmark framework running a fixed corpus of representative
commands through Quor's pipeline, measuring token reduction, latency, and compression quality,
trackable over time.

**Resolution:** Implemented under `tests/benchmarks/` (isolated from `quor/` by construction). 12
realistic, hand-written samples across 6 categories (git-status, git-log, git-diff, pytest, mypy,
generic). `benchmark_runner.py` + `run_benchmarks.py` (standalone script, not a new `quor`
subcommand). Metrics: tokens, compression %, execution time (reported only, never gated). Reports in
JSON and Markdown. Regression detection via a committed `baseline.json`, percentage-point delta
(default 2.0pp threshold); correctness and min-reduction-floor violations are separate, always-fatal
checks. Runs automatically with `pytest tests/`.

One real bug found and fixed during dataset construction: a "distinct errors, no repetition" mypy
sample accidentally had exactly 3 consecutive `: error:` lines, triggering the existing
`group_repeated` collapse despite differing messages — defeated the sample's intended purpose. Fixed
by reducing to 2 errors, below threshold.

**Status:** Resolved. Full test suite (including new benchmark tests), the standalone benchmark suite
(0 correctness failures, 0 floor violations, 0 regressions against its own baseline), `quor verify`,
`ruff check`, and `mypy quor/` all pass.

</details>

---

#### QB-002 — Fixed the default mode not matching what the docs promised

**Effort:** Small · **Value:** Medium · **Category:** Product Decision

The documentation said Quor's default behavior is the cautious "Audit" mode, but the actual code
defaulted to the more aggressive "Optimize" mode — a real mismatch between what was promised and
what shipped. Fixed the code to match the documented, intended default.

<details>
<summary>Technical details</summary>

**Problem:** ADR-009 and three docs (CLAUDE.md, PROJECT_BIBLE.md, ROADMAP.md) state the default
operating mode is `AUDIT`. `quor/config/model.py` actually defaulted to `"optimize"`, and `quor
doctor` printed `Mode: optimize` on a fresh install. Unclear whether this was an implementation bug
or an intentional, undocumented change.

**Desired outcome:** A maintainer decides which side is correct, and the two are reconciled.

**Resolution:** Code default changed to `audit` to match ADR-009/PROJECT_BIBLE.md/CLAUDE.md/
ROADMAP.md, README example output and tests updated to match. ADR-009 was not touched — it was
already correct.

**Status:** Resolved — implemented on `feature/qb-002-default-mode-audit`.

</details>

---

### Low Priority

#### QB-030 — Sped up the test suite and locked in a large-file safety test

**Effort:** Small · **Value:** Low · **Category:** Engineering

Two small housekeeping items: our automated test suite was creeping close to its target speed limit
(traced to tests that were unnecessarily spawning a real PowerShell process each time), and there
was no permanent, automatic test confirming Quor stays fast on a large (10MB) file. Both fixed.

<details>
<summary>Technical details</summary>

**Problem:** Two minor findings from the QB-028 gate walk: (1) the default `pytest` invocation
measured 28–31s locally — right at the <30s PA-Q04 bar; (2) IA-S03 (10MB input must not hang >5s)
had no permanent regression test, only a one-off manual verification.

**Desired outcome:** Identify why specific slow CLI tests take ~1.5s each and speed them up without
losing coverage; add a permanent large-input timing test.

**Resolution:**
1. Root cause: every test calling `quor init --claude` incidentally spawned a real PowerShell
   subprocess via an execution-policy check, regardless of what the test actually verified. Added an
   autouse fixture mocking just that call (cutting affected tests from ~1–1.5s to ~0.05–0.2s), and a
   new `TestExecutionPolicyCheck` class unit-testing the check's own branching logic directly so
   coverage isn't lost, just relocated to a focused fast test. Also merged two tests that
   independently spawned the identical `python -m quor` subprocess into one. Measured 17–28s after,
   down from 28–31s.
2. Added `test_ten_megabyte_input_completes_without_hanging` — a real 10MB input through the real
   `FilterRegistry.apply()`. Found and fixed on the open PR before merge: first shipped with a hard
   5.0s ceiling, which CI failed at 5.16s on `ubuntu-latest` (real CI hardware variance, not a bug —
   local machine measures 0.5–1.2s). Loosened to 20s, giving ~15–40x margin while still catching a
   genuine algorithmic regression.

**Status:** Resolved — implemented on `feature/qb-030-test-speed-and-10mb-regression`.

</details>

---

#### QB-016 — Documented the exact steps for starting new work

**Effort:** Small · **Value:** Low · **Category:** Documentation

Added a clear, step-by-step checklist (in the project's internal instructions) for how to safely
start any new piece of work — including an explicit rule that if things look messy, stop and ask
rather than automatically discarding anyone's in-progress changes.

<details>
<summary>Technical details</summary>

**Problem:** QB-015's Git workflow documentation didn't specify the exact sequence for starting a new
backlog item, nor what to do if the working tree is unexpectedly dirty — risking work starting from
a stale/wrong branch, or an AI assistant "helpfully" discarding uncommitted work.

**Desired outcome:** `docs/final/CLAUDE.md` documents an explicit "Starting Any Backlog Item"
sequence, states every backlog item gets its own feature branch, and adds a rule that an unclean
working tree is a stop-and-ask condition — never resolved automatically via stash/reset/clean.

**Resolution:** Implemented on `feature/qb-016-strengthen-git-workflow`.

**Update (Batch 7):** Re-reviewed after QB-011; branching/PR-checklist/commit rules verified still
accurate (unchanged). Added a "Before Opening a PR — Benchmark & Regression Requirements"
subsection, a Review Checklist, and a Release Readiness Checklist.

**Status:** Resolved.

</details>

---

#### QB-015 — Documented how we use Git (branches, commits, PRs)

**Effort:** Small · **Value:** Low · **Category:** Documentation

Wrote down the project's branching/commit/pull-request conventions for the first time, so
contributors (human or AI) follow one consistent process instead of improvising each time.

<details>
<summary>Technical details</summary>

**Problem:** The project had no documented Git workflow: no branch-naming convention, no commit
message convention, no PR checklist. Surfaced while preparing the QB-014 fix for merge — work was
happening ad hoc.

**Desired outcome:** `CONTRIBUTING.md` documents the standard workflow (branch from `main`,
`feature/qb-XXX-short-description` naming, one backlog item per branch, tests before commit,
conventional commit messages) and an expanded PR checklist. `docs/final/CLAUDE.md` documents the
corresponding rules for AI-assisted sessions.

**Status:** Resolved — implemented on `feature/qb-015-git-workflow`.

</details>

---

#### QB-003 — Documented which commands Quor actually understands

**Effort:** Small · **Value:** Low · **Category:** Documentation

Users might assume "Quor is installed" means "every command gets optimized" — it doesn't; only a
known list of commands (git, pytest, etc.) get special treatment. Added clear documentation of
exactly what's covered today and how to check any specific command.

<details>
<summary>Technical details</summary>

**Problem:** Nothing in the docs stated explicitly that Quor only rewrites commands matching a known
rule set — inviting confusion like the investigation that preceded this backlog item (hook verified
installed and firing, yet `quor gain` reported zero invocations because tested commands were outside
the allowlist).

**Desired outcome:** Documentation states Quor only rewrites known commands, links to `quor explain
<command>` to check coverage, and lists the current allowlist.

**Resolution:** Created `docs/final/COMMAND_SUPPORT.md` as the single canonical reference: how
command detection works, the current command allowlist, a full command-by-command filter table,
filter precedence, fallback behavior, and how new commands are added. `README.md` and
`docs/final/CLAUDE.md`/`PROJECT_BIBLE.md` now cross-reference this document instead of restating
detail.

**Status:** Resolved — implemented on `feature/qb-003-command-support-docs`.

</details>

---
