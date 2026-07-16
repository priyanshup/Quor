# Quor — Product Backlog & Roadmap

Quor compresses what AI coding assistants read — command output, files, documents — so the same
work costs fewer tokens, without breaking the assistant's ability to do the work.

**How to read this document:**
- **[Vision](#vision)** — the one-sentence direction everything below serves.
- **[Current Status](#current-status)** — what's actually shipped, today, verified against the
  real release history (not just this document's own prior claims — see the note below).
- **[Strategic Roadmap](#strategic-roadmap)** — in plain English, where Quor is headed over the
  next several releases.
- **[Product Metrics](#product-metrics)** — what "good" means for Quor, and how we'll know.
- **[Now / Next / Later](#now)** — the open backlog, ranked by product value, not by age.
- **[Research](#research)** — promising ideas that are *not* approved for implementation. Kept
  separate on purpose so they never get mistaken for planned work.
- **[Completed](#completed)** — the full historical record. Nothing here is deleted or shortened
  to save space; it's grouped by how much value it delivered instead of by when it happened.

**Reading an entry:** every item leads with a plain-English summary anyone can follow, no
engineering background required. The technical write-up (root cause, files touched, exact
verification steps) is preserved underneath in a collapsed **Technical details** block — click to
expand it when you need the specifics.

**Effort** is a rough size: **S**mall (hours–a day), **Medium** (a few days), **Large** (a week or
more / multi-part). **Value** is the product impact of doing it. **Risk** is what could go wrong or
what's uncertain about the approach. **Expected token impact** is a rough, honest estimate of how
much this moves Quor's actual compression numbers — "high" means it changes the headline savings
number; "low" means it's about trust, coverage, or measurement rather than raw reduction. None of
these are precise measurements — they're judgment calls made while writing this document, revisited
as real data comes in.

**A correction made while writing this document:** several completed items below (QB-007's document
extraction sub-items, QB-005's JavaScript/TypeScript sub-items, QB-036, QB-035A) previously stated
"not committed — awaiting explicit commit instruction." That was true when each was written, but
stale by the time this restructuring happened: `CHANGELOG.md` and `git log` confirm all of it shipped
in Quor **v0.4.0** and **v0.4.1** (2026-07-11, the current release). Each affected entry has been
corrected in place, with the correction called out explicitly rather than silently edited. This
itself is a useful data point: this document had drifted from reality before, which is part of why
it needed restructuring now, and worth remembering the next time "Status" is read as gospel — when
in doubt, check `CHANGELOG.md` and `git log`, not just this file.

---

## Vision

**Old direction:** *"Safe, deterministic compression."* Quor's job was to never risk losing
information — when in doubt, keep it.

**New direction:** ***Maximum practical token reduction.***

That is not a license to be reckless. It means:

- Preserve enough information for an AI to keep working *correctly* — not enough to reconstruct
  the original byte-for-byte.
- Aggressively remove information that has little practical value to a coding assistant, even if a
  human reading the raw output might have found it mildly interesting.
- Optimize for what actually happens inside a real Claude/GPT context window, not for an abstract
  "did we technically avoid deleting anything" bar.
- Measure success as *tokens actually saved in real usage*, not merely *risk avoided*.

Concretely, this changes how we think about a few things that were previously treated as settled:

- **"Protected" content is no longer sacred by default.** ADR-031 decided that content matching a
  filter's `preserve_patterns` is never compressed, even if it blows through the configured token
  budget (see QB-004/QB-012's history below). That was the right call under "safe, deterministic
  compression." Under "maximum practical token reduction," it becomes a *default*, not a *law* —
  see [Compression Modes](#qb-039--compression-modes-safe--balanced--aggressive) below, which reintroduces the old guarantee as "Safe mode" and
  adds modes that trade some of it away deliberately, with the user's knowledge.
- **A filter that compresses 0% because everything it saw was "protected" is now a bug report, not
  a clean result.** Several existing filters (git-diff is the clearest example) protect so much by
  design that they barely compress typical output at all. That was a reasonable, conservative
  choice at the time. It's now a backlog item.
- **The unit of optimization is shifting from "one command's output" to "the whole session."** Quor
  today compresses each Bash/Read call independently. A meaningful share of remaining waste is
  *between* calls — the same file re-read three times, the same error re-shown after every retry.
  That's a genuinely new frontier for Quor, not a bigger version of what it already does — see
  [Cross-call context optimization](#qb-043--cross-call--session-level-context-optimization).

Think of Quor less as "a command-output compressor" and more as **an AI context optimization
engine** — one component of which happens to be command-output compression today.

---

## Current Status

*As of Quor v0.4.1, released 2026-07-11 (today). Windows-first, Claude Code only.*

**What's actually shipped and live on `main` today:**

- **Command-output compression** for git, pytest, mypy/build tooling, and the full everyday
  Node.js/TypeScript toolchain (npm/npx/pnpm/yarn, ESLint, tsc, Jest, Vitest, Prettier, Next.js,
  Turbo) — see QB-006A/B/C, QB-023, QB-032 below.
- **Structure-aware source-code reading** for Python, JavaScript, TypeScript, and TSX — a file read
  through Claude Code's native `Read` tool (not just `cat` via Bash) gets signatures and docstrings
  preserved with function/method bodies summarized, not the full file — see QB-005 (and its A–F
  sub-phases) below.
- **Document reading** for Markdown, plain text, DOCX, and PDF — the same `Read`-hook path extracts
  structure (headings, lists, tables, requirements) instead of returning raw document text — see
  QB-007 below.
- **A safety net that's always on:** every compressed command's full original output is cached and
  recoverable via a `[full output: ...]` link (QB-013); a narrow, high-confidence secret-leak
  scanner warns on anything that looks like a real credential (QB-029); nothing is ever silently
  dropped without a way back.
- **Trustworthy measurement:** `quor gain` reports real, project-scoped token savings with
  known-bug classes closed (QB-018, QB-017); a 60-case benchmark suite with a committed baseline
  catches regressions automatically on every change (QB-011, QB-005E).
- **Release hygiene:** PyPI publishing is gated behind TestPyPI validation and maintainer approval
  (QB-001); CI covers Python 3.11–3.14 on Windows and Linux (QB-025); Dependabot and CodeQL run on
  a schedule (QB-026).

**What's designed but not built:** a multi-agent adapter architecture (QB-035A) that would let
Quor's existing, already-agent-agnostic compression core plug into a second AI coding tool (Cursor
is the best-evidenced candidate) without duplicating logic. No second adapter has been implemented.

**What's explicitly out of scope today:** any AI coding assistant other than Claude Code; any
programming language for AST-aware summarization beyond Python/JavaScript/TypeScript/TSX; any
non-Windows-first platform assumption.

**The honest gap that isn't a backlog item:** QB-028's release-readiness audit found Internal Alpha
fully passes, but Public Alpha does not — not because of missing code, but because several gates
require real external testers running real, multi-hour sessions, which hasn't happened yet. QB-029
and QB-030 closed every *buildable* gap that audit found. What's left is adoption, not engineering —
worth remembering before treating "what should we build next" as the only open question.

---

## Strategic Roadmap

Plain-English version: **prove the new vision on the content types Quor already touches, before
reaching for the harder, higher-payoff architectural change.**

**Phase 1 — Make "maximum practical token reduction" real, not just a slogan.**
Ship compression modes (Safe / Balanced / Aggressive) so the vision shift is something a user can
actually turn on, not just a philosophy in this document. Close the most visible remaining coverage
gaps — config files, and git-diff's own historically low compression rate — since these are the
fastest, lowest-risk ways to raise Quor's real, measured savings. Stand up continuous competitive
benchmarking so "maximum practical" has an external yardstick, not just an internal one.

**Phase 2 — Prove it's actually safe, not just aggressive.**
Before pushing compression further, build the measurement Quor currently lacks: does compressed
output actually let Claude/GPT finish the coding task correctly, not just "does it look shorter."
Extend coverage (test output, build/CI logs, more languages) using the same discipline. This phase
exists specifically so Phase 3 isn't a leap of faith.

**Phase 3 — Go after the bigger lever: the whole session, not one command at a time.**
Cross-call context optimization — noticing that the same file, the same error, the same output has
already been sent to the model once this session — is where the largest remaining token waste
almost certainly lives. It's also architecturally the biggest change Quor has taken on. Phase 2's
quality-measurement work is the prerequisite that makes this safe to attempt.

**Phase 4 — Expand the surface area.**
Multi-agent support (Cursor, Copilot, Gemini) and adoption features (a "here's what Quor would have
saved you" retroactive scan) are real value, but they're market-expansion bets, not product-quality
bets. Per the existing competitive research, they're deliberately sequenced after Quor has proven it
earns sustained real usage on what it already supports — not because they're unimportant, but
because building them first would be optimizing for breadth before the core promise is proven.

**Alongside all four phases, always:** the [Research](#research) track keeps evaluating
higher-risk, higher-payoff approaches (learned/neural compression, semantic extraction) without
committing production code to them until one is proven to justify the trust and complexity cost.

---

## Product Metrics

What Quor should actually be judged against, going forward — not everything below is measured
today; several are new asks that come out of the vision shift itself.

| Metric | What it means | Status |
|---|---|---|
| **Practical token reduction** | Real, measured savings across actual usage (`quor gain`), not just the benchmark corpus. The headline number. | Measured today (QB-018, QB-017), benchmark-only for now |
| **Information retained** | Whether the compressed output still contains what the task genuinely needed. | Not measured — proxy only (`must_contain` assertions in benchmark cases) |
| **AI task success rate** | Does the assistant still complete the coding task correctly when working from compressed context, at the same rate as uncompressed? | Not measured — see QB-048 |
| **Latency** | Wall-clock overhead Quor adds per hook invocation. | Measured ad hoc (QB-005E's timing script, QB-030's 10MB regression test); not tracked over time |
| **Memory / resource footprint** | Peak memory and disk (tee cache) used by the compression pipeline. | Not tracked |
| **User trust** | Proxy signals: recovery-footer (`[full output: ...]`) click-through, opt-outs, secret-scanner false-positive reports. | Not instrumented |
| **Explainability** | Can a user or the AI itself understand *why* a given line was kept, removed, or compressed. | Partially — `quor explain` exists; see QB-049 for what's missing |

None of these are being proposed as pass/fail gates today. They're the dimensions "maximum
practical token reduction" actually has to be judged against — a compression mode that raises the
headline number while quietly tanking task success rate is not a win under the new vision, it's
exactly the kind of recklessness the vision explicitly rules out.

---

## Now

*The highest-value open work — directly implements the vision shift, ready to scope today.*

**Priority order updated 2026-07-15**, following a product-strategy review that added a second
evidence source QB-051 didn't have at the time: real usage telemetry (`quor gain` plus a direct
query against the live tracking DB), not just the 60-case benchmark corpus. The corpus and the real
data agree on the single biggest lever (git-diff) and disagree sharply on several others (mypy,
git-log, git-status, pytest all show large benchmark-vs-real divergence) — both findings changed
what's ordered below. A follow-up product-owner review the same day made two further calls the data
alone couldn't: QB-054 (telemetry-driven optimization) was moved ahead of QB-049/QB-039 as the more
strategically important long-term direction, QB-046 (more AST languages) was moved to last in this
section (still approved work, just no longer competing with items that have proven volume or
measurement value behind them), and one new item, **QB-055** (the concrete diff-compression
algorithm QB-041's own "Desired outcome" only sketched), was added directly alongside QB-041. Every
entry touched by either round carries its own dated **Evidence update** or **Product decision**
paragraph explaining exactly what changed and why; nothing was silently reordered. Final order:
QB-041 → QB-055 → QB-052 → QB-047 → QB-054 → QB-049 → QB-039 → QB-053 → QB-046 (QB-051 itself is
already shipped and sits first as the foundation the rest of this ordering rests on).

---

#### QB-051 — Compression Analytics & Benchmark Dashboard

**Effort:** Medium · **Value:** High · **Risk:** Low · **Expected token impact:** None directly (a
measurement layer) · **Category:** Engineering / Measurement

**Status:** Implemented (2026-07-14). Pure measurement/visibility work — no filter, stage, AST
summarizer, or benchmark corpus was changed; every existing benchmark case's `compression_pct` was
verified byte-identical before and after (60/60 cases, 9602 tokens saved, 35.3% overall, both
runs). Not committed — awaiting explicit commit instruction.

<details>
<summary>Technical details</summary>

**What shipped:** `StageResult` (`quor/pipeline/stages/base.py`) gained optional
`tokens_before`/`tokens_after` fields, populated only when `Pipeline.execute(track_tokens=True)`
opts in (default `False` — zero cost on the real Bash/Read hook path). `quor/analytics/` is a new
production package: `stage_stats.py` (executions/skipped/failed/tokens/avg-min-max % per
`stage_type`, across many runs) and `effectiveness.py` (High/≥15% · Medium/≥5% · Low impact
classification by measured share of total tokens saved). The benchmark suite
(`tests/benchmarks/`) now captures a full per-stage token trace for every case
(`benchmark_runner.py`), and `run_benchmarks.py --analytics` prints stage contribution, language
(ecosystem) contribution, top-10-hardest-files, and the effectiveness table; `--history` appends
to an append-only `tests/benchmarks/history.json` (format designed, comparison function
`detect_regression()` provided) and prints a version-over-version table — deliberately **not**
wired into `.github/workflows/*.yml` (no benchmark CI step exists today; wiring one is a separate
decision, out of scope here per the task's own instruction to design the format rather than build
CI when CI work would be "too large").

**ID note:** the task that requested this used the ticket name "QB-039," but that ID was already
in use in this document (see above, "Compression Modes: Safe/Balanced/Aggressive," proposed,
unimplemented) — this entry uses the next free ID, QB-051, instead of colliding with it.

**What the first real run found** (60-case corpus, `python -m tests.benchmarks.run_benchmarks
--analytics`) — the evidence behind the ranking below:

| Stage | Impact | Contribution | Activation | Avg saved/fire |
|---|---|---|---|---|
| `code_ast_summarize` | High | 44.1% | 100% | 43.1% |
| `max_tokens` | High | 32.4% | 100% | 2.2% |
| `strip_lines` | High | 18.4% | 100% | 17.9% |
| `group_repeated` | Low | 2.7% | 100% | 14.1% |
| `python_ast_summarize` | Low | 2.4% | 100% | 44.3% |
| `deduplicate_consecutive` | Low | 0.1% | 100% | 0.3% |
| `remove_ansi` | Low | 0.0% | 100% | 0.2% |

Language/ecosystem contribution: JavaScript 52%, TypeScript 43%, Python 41%, Git 31%, Documents
25%, Files 23%, Generic 5%. Top-of-the-hardest-files list is dominated by already-clean/passing
test-runner output and short documents (0.0% — nothing left to cut) plus `git`/generic commands.

**Read this data with its limits in view, not as a verdict:** 60 cases is a small, hand-curated
corpus (see QB-047 below) and token counts are the existing ±20% char/4 estimate — this is
directional evidence for re-prioritizing, not a precision instrument. Two specific readings that
would be *wrong*: (1) "`max_tokens` is more valuable than `strip_lines`" — its 32.4% share comes
from firing on nearly every case for a small trim each time (2.2% avg), not from doing
sophisticated work, cheap-and-broad rather than deep; (2) "`python_ast_summarize` barely matters"
— it has the *second-highest* average savings per fire (44.3%, almost identical to
`code_ast_summarize`'s 43.1%) but a low total share only because this corpus has few Python cases
relative to Git/generic ones — that's a corpus-composition artifact, not a quality signal.

**Evidence-based re-ranking of existing backlog items** (Top 10, by measured-ROI signal — not new
invented items; each still needs its own full scoping pass, this only reorders the *why now*):

1. **QB-046 — AST-aware summarization for more languages (Go/Rust/Java/C#).** `code_ast_summarize`
   is the single highest-contributing *and* highest-average-savings-per-fire stage measured
   (44.1% share, 43.1% avg). This is the strongest direct evidence in the whole run: the mechanism
   that already exists is Quor's best lever, and it's currently gated to three languages. Effort:
   Medium (per language, existing tree-sitter pattern). Expected savings: High (mirrors the 43%
   figure above, per newly-covered language). Risk: Low (additive, same fail-open contract).
   Confidence: High.
2. **QB-041 — Smarter diff & delta compression (git diff/show).** Git sits at 31% ecosystem
   contribution — meaningfully behind JavaScript (52%)/TypeScript (43%)/Python (41%) despite git
   commands being extremely common in a coding session. This is fresh, corpus-measured
   confirmation of QB-041's own problem statement (`preserve_patterns` protects most diff content
   by design). Effort: Medium. Expected savings: Medium–High (closing roughly half the gap to the
   language ecosystems would be a large absolute number given git's invocation frequency). Risk:
   Medium (already flagged in QB-041 itself). Confidence: Medium (extrapolating from 31% vs. ~45%
   average, not a controlled comparison).
3. **QB-047 — Real-world benchmark corpus & continuous tracking.** This task's own analytics
   exposed the corpus's limits directly (60 hand-picked cases; several ecosystems have too few
   cases for the per-ecosystem/per-stage numbers above to be more than directional; no config-file
   category exists yet to evaluate QB-040 against real evidence at all). The analytics layer this
   task built is only as good as the corpus feeding it — expanding the corpus is now the highest
   leverage way to *sharpen every other measurement*, not just add one more feature. Effort:
   Medium. Expected savings: None directly (measurement). Risk: Low. Confidence: High (the gap is
   directly observed, not inferred).
4. **QB-040 — Config & structured-data file compression (YAML/JSON/TOML/.env/.ini).** Cannot yet
   be evaluated against real measured evidence — there is no benchmark category for it (see QB-047
   above). The `Generic` ecosystem's low 5% contribution is *not* evidence for or against QB-040
   specifically (it's dominated by already-terse commands like `ls -la`, not config files).
   Recorded here as "worth measuring before investing further," not "the data says do this."
   Effort: Medium. Expected savings: Unmeasured (previously estimated Medium). Risk: Low.
   Confidence: Low (no direct evidence yet — this is the honest read, not a downgrade of the
   original idea).
5. **QB-049 — Explainability upgrades to `quor explain`.** This task's `track_tokens` plumbing
   (`Pipeline.execute()`, `FilterRegistry.trace()`) already exists and is fully wired end to end
   for the benchmark suite; `quor explain` choosing to opt in and add a "tokens before/after" per
   stage-trace-row column (mirroring `analytics_report.py`'s effectiveness table) is now a small,
   low-risk follow-on rather than new plumbing. Effort: Small (now that this task shipped).
   Expected savings: None directly (developer tooling). Risk: Low. Confidence: High.
6. **QB-044 — Deeper test-output compression (cross-run summarization).** `max_tokens`'s
   dominant-but-shallow 32.4%/2.2%-per-fire profile above is a signal that several filters
   (test-runner output among them) are relying on the generic budget clamp rather than
   structure-aware summarization — consistent with QB-044's own premise. Effort: Medium. Expected
   savings: Medium. Risk: Low. Confidence: Medium (inferred from the stage profile, not measured
   directly against test-output cases specifically).
7. **QB-045 — Broader build & CI log compression.** Same reasoning as #6, generalized past test
   runners specifically — the `max_tokens` shallow-clamp pattern likely recurs anywhere Quor has a
   budget stage but no structure-aware one yet. Effort: Medium. Expected savings: Medium. Risk:
   Low. Confidence: Low (no dedicated CI-log cases in the current corpus — see QB-047).
8. **QB-039 (existing entry above) — Compression Modes: Safe/Balanced/Aggressive.** The
   `max_tokens` numbers above are exactly the "budget clamp doing shallow, broad work" case
   Balanced/Aggressive mode is meant to push further into — this task's data makes that case
   concrete rather than hypothetical, without changing QB-039's own open design questions. Effort/
   Risk/Confidence: unchanged from its own entry.
9. **QB-042 — Continuous competitive benchmarking (RTK, Headroom AI, ZAP).** Unaffected in
   priority by this task's numbers directly, but the history format shipped here
   (`tests/benchmarks/history.py`) is a reusable building block for tracking Quor's *own* trend
   line the same way QB-042 wants to track competitors' — worth sequencing after QB-042's own
   scoping, not before. Effort/Risk/Confidence: unchanged from its own entry.
10. **QB-048 — Compression quality & AI task-success evaluation.** Not re-ranked by this task's
    numbers (token-count savings and task-success are explicitly different axes — see QB-048's own
    entry) — listed here only as a reminder that every "High impact" rating above is a *token*
    rating, not a correctness/usefulness one, and QB-048 is what would eventually confirm or
    challenge it.

**Files changed:** `quor/pipeline/stages/base.py`, `quor/pipeline/engine.py`,
`quor/filters/registry.py`, `quor/analytics/__init__.py`, `quor/analytics/stage_stats.py`,
`quor/analytics/effectiveness.py`, `tests/benchmarks/benchmark_runner.py`,
`tests/benchmarks/analytics_report.py` (new), `tests/benchmarks/history.py` (new),
`tests/benchmarks/run_benchmarks.py`.

**Validation:** `ruff check .` clean; `mypy quor/` clean (CI's own invocation); full `tests/unit`
suite green; `tests/integration -m integration` (7/7) green; `tests/benchmarks/test_benchmarks.py`
(185 tests) green; benchmark suite re-run before/after this change produced byte-identical
per-case `compression_pct` for all 60 cases and an identical 35.3%/9602-token overall figure.

**Update (2026-07-15) — product-strategy review revised the ranking above.** The 10-item ranking
directly above was benchmark-corpus-only (this task never queried `quor gain` or the tracking DB
directly). A follow-on product-strategy review added that second evidence source and found it
changes the #1 priority: **QB-041 (git-diff) moves ahead of QB-046 (AST languages).** Real usage
(`python -m quor gain --days 90`, this project) shows git-diff responsible for **45% of every
token this tool has ever saved here**, at roughly half the compression ratio its own git siblings
achieve — a finding no benchmark-only pass could show, since the corpus has only 2 git-diff samples
and no volume/frequency signal at all. The review also found: a large, previously-invisible
benchmark-vs-real divergence for several filters (mypy 46.1% benchmark vs **-41.2% real** — net
*expansion*, not compression; git-log 40.8% benchmark vs 83.8% real; git-status 52.7% benchmark vs
6.6% real; pytest 39.75% benchmark vs 12.9% real), which is now the direct evidence behind QB-047's
promotion to Now and behind two brand-new items, QB-052 (the mypy/npm fix) and QB-054
(telemetry-driven optimization, so this kind of gap is caught automatically instead of by a one-off
manual SQL query). See each item's own **Evidence update (2026-07-15)** note below for specifics.
QB-046, QB-047, and QB-049 have moved up from [Next](#next) into Now; QB-040 and QB-042 have moved
down from Now into [Next](#next) — full reasoning is in each item's own entry, not repeated here.

</details>

---

#### QB-041 — Smarter diff & delta compression (git diff/show, patches)

**Effort:** Medium · **Value:** High · **Risk:** Medium · **Expected token impact:** High ·
**Category:** Enhancement

QB-004's investigation (see Completed, below) found that `git diff`/`git show` barely compress
today — not because of a bug, but because the filter marks almost all diff content "always keep,"
so a genuinely large diff can blow far past its configured token budget with nothing done about it.
That was the right, conservative call under the old "safe, deterministic" direction. Under
"maximum practical token reduction," a diff filter that routinely does ~0% on real, large diffs is
now a gap worth closing, not a settled decision.

**Evidence update (2026-07-15) — this is now Priority 1, promoted from #2:** real usage
(`python -m quor gain --days 90`, this project) shows git-diff responsible for **45% of every
token Quor has ever saved on this project** — 46.5k of 100.7k net tokens saved — at a ~26%
average ratio, versus git-log's 40.8%/git-status's 52.7% *benchmark* ratios (real-world git-log and
git-status swing even further in opposite directions from their benchmark numbers — see QB-047).
Doing the arithmetic directly from the real numbers: moving git-diff's ratio from ~26% to ~40%
(still short of its git siblings) would add roughly +25k tokens on this project's existing 90-day
window alone — a projected **~14% increase in every token Quor has ever saved here**, from one
filter. No other single item in this backlog has that much *already-flowing volume* behind it;
QB-041 is now ranked ahead of QB-046 for exactly this reason (QB-046's per-fire quality is equally
strong, but has zero measured real-world volume in this project — see QB-046's own evidence
update).

<details>
<summary>Technical details</summary>

**Problem:** `git-diff.toml`'s `preserve_patterns` protects the overwhelming majority of real diff
content (QB-004 measured 298 of 515 lines / ~5,265 of ~5,806 tokens protected in its repro case) —
`max_tokens`'s 600-token budget was already a no-op before it ever ran. ADR-031 (QB-012) formally
decided this is correct *behavior* for the budget mechanism; it did not decide that a near-zero
compression rate on large diffs is an acceptable *outcome* — that question was explicitly deferred,
not answered, and is what this item picks up. Confirmed directly by reading the shipped filter
(`quor/filters/builtin/git.toml`): `preserve_patterns = ['^\+', '^-', '^@@', 'conflict', 'Error']`
— literally every added line, every removed line, and every hunk header is protected; only
boilerplate headers (`index `, `diff --git `, `--- a/`, `+++ b/`, blank lines) are ever compressible
today.

**Desired outcome, ideas not yet evaluated against each other:**
- Collapse unchanged context lines more aggressively (git already supports `-U0`/reduced context;
  Quor could default to requesting less context from git itself rather than compressing after the
  fact).
- Summarize a diff's own repeated shape — e.g. "12 files changed, mostly whitespace/import-reorder"
  — the same instinct behind `group_repeated`, applied to whole-hunk shapes instead of lines.
  `match_output` (QB-010) is the closest existing primitive, but wasn't designed for this.
  content — Balanced mode would let a diff-shape-summary genuinely replace hunk bodies instead of
  only ever running when `preserve_patterns` didn't already claim them.
- For a genuinely huge diff (a lockfile regenerated, a vendored dependency bump), recognize the
  "this file's diff is 4,000 lines of generated noise" case and represent it as a one-line summary
  plus the tee recovery link (QB-013) — the file changed, here's proof, here's how to see the rest.

**Status:** Proposed. Not scoped or implemented. Depends conceptually on QB-039 (Balanced/Aggressive
modes) for the second and third ideas above — the first idea (less context from git itself) doesn't,
and could ship independently and sooner. A thin slice of QB-047 (more git-diff benchmark samples,
today only 2) should land alongside this item so it has a real regression baseline before/after,
not just this project's own `quor gain` numbers. **See QB-055, directly below, for the worked-out
algorithm design covering the second and third ideas above** — added 2026-07-15 at product-owner
request so "compress diffs more" has a concrete, safety-constrained mechanism instead of a sketch.

**Fix update (2026-07-15) — over-broad `preserve_patterns` bug found via the 12-case corpus (QB-047
slice already landed):** with QB-055's `collapse_unchanged_context` in place, per-line token tracing
across all 12 git-diff benchmark cases showed the largest *remaining* safe lever wasn't a new stage —
it was a bug in the existing `strip_lines` config. `preserve_patterns` included bare, unanchored
`'conflict'` and `'Error'` substring matches (quoted above). Neither adds real protection: genuine
conflict markers and tool-emitted error/fatal lines in a diff are always `+`/`-`-prefixed and already
covered by `^\+`/`^-`; `'conflict'` never matched anywhere in the corpus, and `'Error'` (capital-E)
doesn't match git's own lowercase `error:`/`fatal:` messages either. What it *did* match: ordinary
unchanged context lines that merely mention an Error-suffixed identifier (`ValueError`,
`DuplicateChargeError`, `NoEligibleWarehouseError`, etc. — 8 lines across 4 of the 12 cases) — forcing
them to `PROTECT`, which permanently excluded them from `collapse_unchanged_context` and fragmented
otherwise-collapsible runs into smaller pieces on both sides of the falsely-protected line. Removed
both patterns from git-diff's `preserve_patterns` (`quor/filters/builtin/git.toml`); `^\+`/`^-`/`^@@`
and the rename-metadata patterns are unchanged and still absolute. Measured corpus impact: git-diff
category 1203→1300 tokens saved (17.1%→18.5%, +97 tokens/+1.4pp), 4 of 12 cases improved, 0
regressions, 0 correctness/floor failures. Added a regression test (`quor/filters/builtin/git.toml`)
pinning that an unchanged context line mentioning an Error-named identifier still collapses. Smaller
than QB-055's own remaining ideas in ceiling, but implementable today with zero risk, since the
corpus shows real diffs are edit-dense enough (most tokens are legitimately-`PROTECT`ed `+`/`-`
content) that a new hunk-shape-collapsing stage would have a much smaller *safe* surface than QB-055's
sketch assumed — see QB-055's own entry for why repetitive-hunk collapsing specifically cannot be done
under this task's "never modify existing PROTECT lines" constraint (collapsing a repeated hunk removes
`+`/`-` lines, which is exactly the mutation ADR-031 forbids).

</details>

---

#### QB-055 — Smarter diff semantics (context-aware hunk compression)

**Effort:** Medium · **Value:** High · **Risk:** Medium · **Expected token impact:** High ·
**Category:** Enhancement

Added 2026-07-15 at product-owner request, as the concrete technical design for QB-041's goal —
QB-041 establishes *why* git-diff needs to compress harder (the evidence) and lists candidate ideas
at a sketch level; this item is the specific algorithm. Not just "compress diffs more" — change
what a diff *shows*: identify genuinely unchanged context, collapse repetitive hunks, always
preserve edited lines and their immediate surrounding context, and summarize large unchanged
regions intelligently instead of either keeping them whole or stripping them blind. Likely the
single biggest practical improvement available, since diffs dominate real coding sessions.

<details>
<summary>Technical details</summary>

**Relationship to QB-041:** not a duplicate. QB-041 is the problem statement plus evidence (why
git-diff underperforms, how much real volume is at stake — see its own "Evidence update") and lists
three candidate approaches at a sketch level. This item is the one of those three ("summarize a
diff's own repeated shape") worked out to an actual algorithm, with an explicit safety constraint
the sketch didn't spell out: *edited lines and their nearby context are never candidates for
summarization — only genuinely unchanged, repetitive, or generated-noise regions are.* Track QB-041
and QB-055 as one initiative, kept as separate entries because evidence-gathering and algorithm
design are different kinds of work with different review needs.

**Desired outcome — the specific mechanism, not yet designed in code:**
- **Identify unchanged context** — lines a diff already marks as context (no `+`/`-` prefix) are
  candidates for compression; lines with a `+`/`-` prefix never are.
- **Preserve edited lines** — every `+`/`-` line stays, unconditionally, exactly as
  `preserve_patterns` already guarantees today (this item doesn't loosen that guarantee, it works
  around it more precisely instead of only ever trimming boilerplate headers, which is all today's
  `strip_lines` patterns touch).
- **Preserve nearby context** — a fixed window of unchanged lines immediately adjacent to an edit
  (both directions) stays too, so the AI still has enough surrounding code to understand the change
  — the same instinct as `git diff -U<n>`, but decided by Quor after the fact rather than requiring
  the user to have requested less context from git in the first place.
- **Collapse repetitive hunks** — when the *same* unchanged-region shape repeats across multiple
  hunks or files (e.g. a mechanical whitespace/import-reorder change touching 40 files identically),
  represent it once with a count — the same instinct as `group_repeated` (existing stage) but
  applied to whole-hunk shapes instead of adjacent lines; `match_output` (QB-010) is the closest
  existing primitive, but wasn't designed for this granularity.
- **Summarize huge unchanged regions** — a genuinely large, low-information unchanged span (a
  regenerated lockfile, a vendored dependency bump) becomes a one-line summary plus the tee recovery
  link (QB-013), never silently dropped.

**Open design questions:**
- Where does "collapse repetitive hunks" sit against ADR-031's `preserve_patterns` guarantee —
  hunks being collapsed here are, by construction, unchanged-context lines, never `+`/`-` lines, so
  this should not need Balanced/Aggressive mode (QB-039) to be safe. Worth confirming explicitly
  during design rather than assuming it.
- What "same shape" means for hunk-level grouping (line-count match? normalized-content match?)
  needs the same deterministic, non-heuristic caution QB-036's design work already applied when it
  rejected a naive "deduplicate visually similar lines" rule for diagnostics.
- Needs its own benchmark cases before/after (ties directly to QB-047's git-diff corpus slice) so
  "smarter" is measured, not assumed.

**Status:** Proposed. Not scoped or implemented. Added 2026-07-15 at product-owner request during
the QB-051 roadmap review, positioned immediately after QB-041 (same initiative) and explicitly
ahead of QB-053 (adaptive compression) — this is concrete, scoped algorithm work, not the more
architecturally speculative self-tuning QB-053 describes.

</details>

---

#### QB-052 — Fix negative-compression regression in mypy/npm filters

**Effort:** Small · **Value:** Medium · **Risk:** Low · **Expected token impact:** Low (small in
absolute tokens, but corrects a trust-damaging defect) · **Category:** Bug fix

Real usage data — not the benchmark corpus, which never surfaced this — shows two shipped filters
making output *bigger*, not smaller, on average: `mypy` (-41.2% avg over 80 real invocations) and
`npm` (-9.1% avg over 26 real invocations). A compression tool that expands output, even
occasionally, undermines trust regardless of how small the absolute numbers are.

<details>
<summary>Technical details</summary>

**Problem, read directly from `quor/filters/builtin/build.toml`:** the `mypy` filter's
`group_repeated` stage requires 3 *identical* error shapes (`min_count = 3`) before it collapses
anything, and `abort_unless = ["error", "Error", "warning", "Warning", "note"]` gates the whole
pipeline. A typical real mypy run reports a handful of *distinct* errors, not 3+ identical ones —
so `group_repeated` rarely fires, `strip_lines` only removes a few boilerplate lines
("Success: no issues found", "Found N error", blank lines), and the tee recovery-footer's
near-fixed overhead is added regardless — net-negative on already-small output. `npm`'s
`group_repeated` (`min_count = 2`, `quor/filters/builtin/node.toml`) hits the same dynamic less
severely, which is why its real average is only mildly negative rather than sharply so.

**Desired outcome:** Either (a) lower `mypy`'s `group_repeated` threshold and broaden `strip_lines`
so genuinely-small mypy runs have more to trim, or (b) suppress/shrink the tee recovery footer when
a filter's own compression didn't save enough to offset it. Option (b) is the general fix — see
QB-053/QB-054 below, which would let *any* filter self-correct this way, not just mypy/npm — and
likely obsoletes needing a filter-specific fix, so it should be scoped first.

**Status:** Proposed. Not scoped or implemented. Found during the 2026-07-15 product-strategy
review via a direct SQLite query against the real tracking DB — invisible in the benchmark corpus,
which has no case that exercises this pattern (mypy's 2 benchmark samples both compress fine).

</details>

---

#### QB-047 — Real-world benchmark corpus & continuous tracking

**Effort:** Medium · **Value:** High · **Risk:** Medium · **Expected token impact:** None
directly · **Category:** Engineering / Measurement

The existing benchmark suite (QB-011, expanded by QB-005E) is 60 realistic but hand-written sample
commands, run on demand, compared against one committed baseline. This item extends it two ways:
sampling real (anonymized, opt-in) commands from actual usage instead of only hand-authored fixtures,
and tracking compression numbers as a trend across releases instead of a single point-in-time
baseline diff.

**Evidence update (2026-07-15) — promoted from [Next](#next), Value raised Medium → High:** the
product-strategy review found large, previously-invisible gaps between the benchmark corpus and
real usage — mypy (46.1% benchmark vs. **-41.2% real**), git-log (40.8% benchmark vs. **83.8%
real**), git-status (52.7% benchmark vs. **6.6% real**), pytest (39.75% benchmark vs. **12.9%
real**) — for four different filters, in both directions. A corpus with 2 samples per category
cannot predict real behavior reliably for the highest-volume filters any more than the lowest. This
is no longer a hypothesis; it's what closing the loop between QB-051's benchmark analytics and
`quor gain`'s real telemetry actually showed. Recommend scoping a first *slice* (more git-diff,
generic, and config-file samples specifically, tied to QB-041 and QB-040) rather than the full
broad corpus rebuild in one pass.

<details>
<summary>Technical details</summary>

**Problem:** Hand-written benchmark fixtures, however realistic, can't tell us whether real usage
looks like the corpus — QB-034's own `discover`-command proposal exists specifically because this
gap is felt keenly ("what would Quor have saved on commands it never saw"). Separately, the
benchmark suite's regression check compares only against the immediately prior baseline — there's no
view of whether compression rates are trending up or down release over release.

**Desired outcome:** (1) An opt-in, privacy-conscious mechanism to contribute real command-output
samples (redacted/anonymized) to the benchmark corpus over time — likely sharing infrastructure with
QB-034's `discover` command, which already needs to read real session logs. (2) A trend view — even
a simple committed CSV/JSON of "compression % per category per release" — so a regression that's
individually below the 2.0pp threshold (QB-011's existing gate) but persistent across several
releases becomes visible. QB-051 already shipped the data format for this (`tests/benchmarks/
history.py`'s `history.json`) — this item is what would actually populate it release over release.

**Open question:** privacy/consent model for (1) needs real product and legal thought before any
implementation — this is explicitly not "just add telemetry."

**Status:** Proposed. Not scoped or implemented.

</details>

---

#### QB-054 — Telemetry-driven optimization (operationalize the tracking DB as continuous feedback)

**Effort:** Medium · **Value:** High · **Risk:** Low · **Expected token impact:** None directly
(measurement/infrastructure — enables QB-053) · **Category:** Engineering / Measurement

QB-051 built the analytics *mechanism* (per-stage stats, effectiveness classification) but only
ever runs it against the benchmark corpus. The 2026-07-15 product-strategy review found its most
important evidence by hand — a one-off SQL query against the real tracking DB (`quor.db`) that
surfaced the mypy/npm regression (QB-052) and the git-diff volume finding behind QB-041's
re-ranking — numbers the benchmark corpus alone could never have shown. This item turns that
one-off manual query into a standing capability.

**Product decision (2026-07-15) — promoted ahead of QB-049 and QB-039, directly behind QB-047:**
the product owner's own read of this review singled this out, alongside QB-053, as "probably the
most important long-term addition" — the direction being: Quor should stop encoding hardcoded
assumptions about what to compress and instead learn from real outcomes across many sessions (e.g.
"this pattern never hurts task success and saves 18% tokens, across 50,000 sessions"). This item is
the prerequisite infrastructure for that direction, so it's sequenced ahead of the smaller,
independent wins (QB-049, QB-039) rather than after them.

<details>
<summary>Technical details</summary>

**Problem:** `quor gain` (QB-017/QB-037) reports net savings and a top-5 filter list, but nothing
flags a filter that's gone net-negative, nothing compares real-usage percentages against the
benchmark corpus's expectations, and nothing runs on a recurring cadence — every insight in the
2026-07-15 review required an ad hoc script against the SQLite DB.

**Desired outcome:** Extend `quor doctor` (or a new `quor gain --analyze`-style view) to
automatically flag: (1) any filter whose rolling real `compression_pct` is negative or near-zero
(directly generalizes the QB-052 finding into an ongoing check, not a one-time fix); (2) any filter
whose real-usage percentage diverges sharply from its benchmark-corpus percentage (generalizes this
review's mypy/git-log/git-status/pytest divergence findings — see QB-047 — into a standing signal
instead of something only found by manually comparing two reports side by side). Reuses QB-051's
`history.json` design pattern (append-only, comparable over time) but sourced from the real
tracking DB instead of the benchmark corpus.

**Relationship to QB-047:** QB-047 improves the *benchmark corpus's* realism; this item improves
visibility into *actual production* behavior. They're complementary, not overlapping — QB-047 makes
the corpus a better proxy for reality, this item stops needing to guess whether the proxy is right
at all.

**Status:** Proposed. Not scoped or implemented. Natural prerequisite for QB-053 (adaptive
compression needs a live signal to adapt from). Came directly out of the 2026-07-15 product-strategy
review, not a prior request.

</details>

---

#### QB-049 — Explainability upgrades to `quor explain`

**Effort:** Medium · **Value:** Medium · **Risk:** Low · **Expected token impact:** None directly ·
**Category:** Enhancement

`quor explain` already shows a stage-by-stage trace of what a filter would do to a given command
(and, per QB-036, deliberately opts out of the early-exit optimization so that trace stays complete).
What it doesn't yet do: explain *why* in plain English ("this line matched the `preserve_patterns`
rule for REQ IDs"), or project the token impact of a hypothetical mode change (QB-039) before a user
turns it on. As compression gets more aggressive under the new vision, explaining it well matters
more, not less — this is the trust half of "aggressive isn't reckless."

**Evidence update (2026-07-15) — promoted from [Next](#next), Effort lowered Medium → Small for the
token-column half:** QB-051 already built and wired the `track_tokens` plumbing
(`Pipeline.execute()`, `FilterRegistry.trace()`) end to end for the benchmark suite. Adding a
"tokens before/after" column to `quor explain`'s existing per-stage trace table is now a small,
low-risk presentation change reusing that plumbing, not new engineering — and would have made this
whole review's manual SQL-querying unnecessary for the per-stage (if not the real-usage) half of
what it found.

<details>
<summary>Technical details</summary>

**Problem:** `quor explain`'s existing per-stage trace shows *what* changed (`Decision` per line,
per stage) but not *why* in language a non-engineer would follow, and has no forward-looking mode —
it explains what already happened, not what a different setting would do.

**Desired outcome:** Three additions, each independently shippable: (1) a "tokens before/after" per
stage-trace-row column, reusing QB-051's existing `track_tokens` plumbing (Small effort, see above);
(2) a plain-English reason string attached to each `Decision` (many stages already carry a `reason`
internally per QB-005B's own fail-open-contract documentation — this may be substantially a
presentation change, not new logic); (3) a `--mode` flag on `quor explain` letting a user preview
what Balanced/Aggressive mode (QB-039) would do differently to the same input, before switching
their default.

**Status:** Proposed. Not scoped or implemented. Part (3) depends on QB-039 existing first; parts
(1) and (2) do not and could ship independently and sooner — (1) especially, given it needs no new
plumbing at all.

</details>

---

#### QB-039 — Compression Modes: Safe / Balanced / Aggressive

**Effort:** Medium · **Value:** High · **Risk:** Medium · **Expected token impact:** High ·
**Category:** Feature

Right now Quor has one behavior: compress conservatively, never touch anything marked "always
keep," even if that means barely compressing at all (see git-diff's history below). This item adds
a user-visible choice — **Safe** (today's behavior, unchanged, still the default), **Balanced**
(compresses further into currently-protected content when a filter has high confidence it's safe —
e.g. collapsing repeated boilerplate inside a protected block), and **Aggressive** (prioritizes
token count hard, accepts a real risk of losing detail a human would have wanted, on the bet that
the AI can ask again if it needs something back). This is the single item that makes the new vision
("maximum practical token reduction") something a user actually experiences, not just a philosophy
in this document.

**Evidence update (2026-07-15) — sequencing confirmed, not re-ranked:** the product-strategy review
found direct, code-level proof of exactly the mechanism this item targets (`git-diff.toml`'s
`preserve_patterns` protects nearly 100% of diff bodies — see QB-041's own evidence update) and of
`max_tokens`'s shallow-but-universal behavior (32.4% of all benchmark savings from a 2.2% average
trim per fire). Both make this item's premise concrete rather than hypothetical. The review's
recommendation stands as originally scoped: let QB-041 ship a narrow, well-understood version of
"compress into currently-protected content" for one filter first, before generalizing to a
project-wide mode switch — this item's own open design questions (below) are unaffected and still
need their own design pass.

<details>
<summary>Technical details</summary>

**Problem:** ADR-031 (see QB-012 below) decided `max_tokens` is a *best-effort* budget — content
matching `preserve_patterns` is never compressed, even when it alone exceeds the configured limit.
That was the correct call for "safe, deterministic compression." It also means several filters
(git-diff especially — see QB-041) have a hard ceiling on how much they can ever save, by design,
regardless of how the vision changes. There is currently no way for a user to say "I understand the
risk, compress harder anyway."

**Desired outcome:** A per-invocation or per-project mode setting (`quor config set mode=aggressive`
or similar) that changes how `max_tokens` and `preserve_patterns` interact, without changing what
any existing filter's `PROTECT`/`KEEP` decisions *mean* — only how strictly `PROTECT` is honored
when the token budget is already blown. Safe mode is ADR-031's existing behavior, unchanged, and
stays the default — this must not silently change behavior for any existing user.

**Open design questions, not yet answered:**
- Does "Balanced" apply per-filter (some filters opt in) or globally? A blanket rule risks exactly
  the kind of undocumented, filter-specific heuristic QB-036's design work explicitly cautioned
  against.
- How does the tee recovery safety net (QB-013) interact with Aggressive mode — should Aggressive
  mode make the `[full output: ...]` recovery link *more* prominent, since more is being risked?
  This seems likely to matter more here than it did at Safe-mode compression levels.
- Does `quor gain` and the benchmark suite need a mode dimension (i.e., track savings separately per
  mode), or is mode an orthogonal setting that doesn't change what's measured? Given [Product
  Metrics](#product-metrics)'s new "AI task success rate" and "information retained" asks, Aggressive
  mode is exactly the case where those metrics matter most and are least proven — QB-048 (quality
  evals) should land before, or alongside, Aggressive mode shipping to anyone by default.
- Interaction with the multi-tier `preserve_patterns` idea ADR-031 explicitly considered and
  rejected (Option 3, "priority-based budgeting") — Balanced mode may effectively be that option,
  revisited under new product priorities rather than rejected outright.

**Status:** Proposed. Not scoped or implemented. The natural next step is a design pass (in the
spirit of QB-005A/QB-035A/QB-036's own architecture-first discipline) before any code, specifically
to answer the open questions above.

</details>

---

#### QB-053 — Adaptive compression (self-tuning aggressiveness per filter)

**Effort:** Large · **Value:** High · **Risk:** Medium · **Expected token impact:** Medium-High ·
**Category:** Feature / Architecture

Every filter's aggressiveness today is a fixed choice a human made once in a `.toml` file. This
item asks whether Quor can *automatically* lean into filters with proven real headroom (git-diff,
once QB-041 gives it room to work with) and automatically back off filters proven to be low- or
negative-value (the mypy/npm finding in QB-052) — without a human hand-tuning each
`preserve_patterns` list or manually flipping QB-039's mode dial. Distinct from QB-039: QB-039 is a
user-selected, static dial; this is the system correcting itself using its own measured evidence.

<details>
<summary>Technical details</summary>

**Problem:** Nothing in `quor/filters/registry.py`/`quor/pipeline/engine.py` today reads back its
own historical performance (the tracking DB, `quor/tracking/db.py`) to change how a filter behaves
on the next call. Every `.toml` config is static until a human edits it.

**Desired outcome, not yet designed:** A feedback path from measured per-filter effectiveness
(QB-054, above, would supply this) back into filter behavior — e.g., a filter whose real
`compression_pct` is consistently near-zero or negative could automatically skip the tee recovery
footer (directly fixing QB-052's finding generally, not per-filter); a filter with consistently high
real volume and a known-conservative mechanism (git-diff, pre-QB-041) could be a candidate for
gradually loosening `preserve_patterns` — this is effectively QB-039's "Balanced mode" logic,
triggered by evidence instead of a user's manual switch.

**Open design questions:**
- Automatic behavior change is a correctness risk in a way a static config isn't — needs the same
  "architecture-first" design pass QB-005A/QB-035A/QB-036 already established as this project's norm
  before any code.
- Depends on QB-054 existing first (an automatic system needs a live, trustworthy telemetry feed to
  adapt from, not a one-off manual review like the one that found QB-052).
- Overlaps conceptually with QB-039 (Balanced mode) — worth designing together rather than as two
  competing mechanisms; QB-039 could be the manual override that always wins over this item's
  automatic behavior.

**Status:** Proposed. Not scoped or implemented. Sequenced after QB-054 and QB-039's own design pass
— this is the most architecturally ambitious item added in the 2026-07-15 update, deliberately not
started before its prerequisites exist.

</details>

---

#### QB-046 — AST-aware summarization for more languages (Go, Rust, Java, C#)

**Effort:** Large (per language) · **Value:** Medium · **Risk:** Low · **Expected token impact:**
Medium · **Category:** Feature

QB-005 shipped structure-aware, signature-preserved reading for Python, JavaScript, TypeScript, and
TSX. This is the direct continuation for the other languages QB-035's original scope named (Go,
Rust, Java) plus one it didn't (C#) — using the same `tree-sitter`-based framework QB-005B already
built and proved reusable across three languages.

**Evidence update (2026-07-15) — kept in [Now](#now) but moved to last, behind all
telemetry-driven work (product decision, overriding this task's own initial ranking):**
`code_ast_summarize` remains the single best-*performing* mechanism Quor has (44.1% of all
benchmark savings, 43.1% avg per fire, consistent with `python_ast_summarize`'s 44.3% avg — same
quality, different language) — the underlying case for this item is not weaker than it was. What
changed is sequencing, not merit: real usage on this project shows **zero** invocations of
`cat-javascript`/`cat-typescript` at all (this is a Python project), so there's no real-world volume
evidence for a new language the way there is for QB-041/QB-055. The product owner's explicit call:
the tree-sitter framework this item would use is already proven and not going anywhere — "there's
no rush until users actually need them" — so this stays behind QB-041/QB-055 (proven volume),
QB-052 (trust fix), QB-047 (fixes the measurement itself), QB-054 (learns what to prioritize from
telemetry instead of guessing), QB-049 (cheap), QB-039, and QB-053 (the two items that would
directly help decide *which* language to add first, using real evidence instead of a guess).

<details>
<summary>Technical details</summary>

**Problem:** `quor/pipeline/ast_summarize/registry.py` only has analyzers for `python`,
`javascript`, `typescript`, and `tsx`. A `.go`/`.rs`/`.java`/`.cs` file read through Quor gets no
structural compression today, falling through to plain `cat`/`Read` passthrough.

**Desired outcome:** One new analyzer per language, each following QB-005B/C/D's now-proven shape:
a `tree-sitter-<language>` grammar (each is its own new optional dependency, following the
`quor[javascript]` extras precedent), a node-type mapping for that language's function/method/class
constructs, and the same ERROR-node-overlap exclusion rule
(`quor/pipeline/ast_summarize/_treesitter_utils.py`, already generalized by QB-005D specifically so
a third language wouldn't have to reinvent it).

**Why this supersedes half of QB-035's original scope:** QB-035's own history already tracked this
exact language-expansion work under its own update notes and closed the JS/TS portion via QB-005.
This item exists so the *remaining* language work (Go/Rust/Java, plus C#) has a home that isn't
bundled with QB-035's now-unrelated multi-agent-support half — see QB-035 in
[Later](#later) for that half.

**Sequencing note:** each language is independently shippable (QB-005C/D's own precedent) — this
doesn't need to be one large effort, and languages can be prioritized by real usage data once it
exists (see QB-047, QB-054).

**Correction (documentation consistency pass):** the "Status" line below was stale.
`pyproject.toml`'s `go`/`java`/`rust`/`csharp` optional extras already declare
`tree-sitter-go`/`tree-sitter-java`/`tree-sitter-rust`/`tree-sitter-c-sharp` and describe
implemented, fail-open analyzers (`analyze_go()`, `analyze_java()`, `analyze_rust()`,
`analyze_csharp()` in `quor/pipeline/ast_summarize/`), each reusing the same
`tree-sitter<0.26.0`-ceiling and ERROR-node-overlap rule as the shipped JS/TS/TSX analyzers. This
work is implemented, not proposed — it has not yet been reflected in a `CHANGELOG.md` release entry
or moved to [Completed](#completed), which should happen in a follow-up pass together with the
verification detail (dates, test counts, benchmark measurements) that a proper Completed write-up
needs and that isn't available from `pyproject.toml` alone.

**Status:** Implemented (Go, Rust, Java, C# analyzers and `quor[go]`/`quor[java]`/`quor[rust]`/
`quor[csharp]` extras all present in `pyproject.toml`) — not yet released/CHANGELOG'd.

</details>

---

## Next

*High-value, not yet started — the natural follow-through once [Now](#now) ships.*

---

#### QB-043 — Cross-call / session-level context optimization

**Effort:** Large · **Value:** High · **Risk:** High · **Expected token impact:** High ·
**Category:** Feature / Architecture

Everything Quor does today compresses one command's or one file's output in isolation. But a real
coding session re-reads the same file, re-runs the same failing test, and re-shows the same error
message many times over. None of that repetition is compressed today — each call is filtered fresh,
with no memory of what the model already saw earlier in the same session. This is very likely the
single largest remaining source of token waste Quor doesn't yet touch, and the most direct
expression of the vision shift from "compress this command's output" to "optimize the AI's actual
context."

<details>
<summary>Technical details</summary>

**Problem:** Every `Bash`/`Read` hook invocation is stateless with respect to prior invocations in
the same session — `FilterRegistry`/`Pipeline` (and `quor gain`'s tracking) all operate per-call.
There is no mechanism today for "this file was already read three tool-calls ago, unchanged" or
"this exact error was already shown after the previous two failed attempts."

**Desired outcome, not yet designed:** some form of session-scoped memory that lets a filter (or a
new pipeline stage) recognize genuine repetition across calls — not just within one output — and
compress accordingly (e.g. "unchanged since last read — see above" instead of resending an entire
file). This is a materially different shape of problem than anything Quor's `ContentMask`/
`StageHandler` architecture was designed for; QB-035A's audit of the codebase found the compression
core is 100% call-scoped and stateless by design (a deliberate, good property for the problem it was
solving) — this item asks whether and how to introduce session state without undermining that.

**Why this is High risk, stated plainly:**
- **Correctness:** "unchanged since last read" is a much stronger claim than anything Quor asserts
  today — a false positive here (claiming no change when something did change) is a materially worse
  failure mode than over-compressing a single call, since the model could act on stale information
  without ever seeing a `[full output: ...]` recovery link for content it never technically lost, it
  just wasn't shown a second time.
  file-modification detection?), whether it survives a Claude Code session restart, and what happens
  if two concurrent sessions touch the same file.
- **Trust:** this is the kind of change that needs QB-048's quality/task-success measurement in
  place *first*, not as a nice-to-have alongside it — the whole point of this item only works if it
  can be proven not to hurt task success, and today Quor has no way to prove that for anything.

**Status:** Proposed, deliberately not scoped in detail here. This needs its own dedicated design
pass (in the spirit of QB-005A/QB-035A) before implementation — likely the largest single design
effort in this document. Sequenced in [Strategic Roadmap](#strategic-roadmap) Phase 3, after quality
measurement (QB-048) exists to make it safe to evaluate.

</details>

---

#### QB-044 — Deeper test-output compression (cross-run summarization)

**Effort:** Medium · **Value:** Medium · **Risk:** Low · **Expected token impact:** Medium ·
**Category:** Enhancement

Quor already strips framework noise from pytest tracebacks (QB-032) and passing-test lines from
Jest/Vitest (QB-006C). What it doesn't do yet: recognize when a *whole test run* is dominated by one
repeated failure pattern (the same assertion failing across 40 parametrized cases) and summarize
that shape instead of showing each occurrence — the same instinct as `group_repeated`, but applied
across a test run's structure rather than adjacent lines.

**Evidence update (2026-07-15) — strengthened:** real usage shows pytest's average compression
(12.9% over 262 real invocations) at roughly a third of its benchmark score (39.75%, 3 cases) — the
largest benchmark-vs-real gap found for any filter with meaningful real volume besides mypy (see
QB-052). This is consistent with, and now better evidenced than, this item's original premise:
`group_repeated` only matches *adjacent* shapes, and real parametrized-test failures are rarely
adjacent. Root cause not yet fully diagnosed (this review inferred it from the stage profile and
the real/benchmark gap, it didn't instrument pytest output directly) — recommend scoping alongside
QB-047's corpus work so the fix has a real regression baseline to check against.

<details>
<summary>Technical details</summary>

**Problem:** `group_repeated` (existing stage) only collapses *adjacent, shape-matching* lines. A
parametrized test suite's failures are rarely adjacent in the raw output — they're separated by
other tests' output — so today's mechanism doesn't catch the highest-value case: "this one bug broke
40 tests" collapsing to a couple of lines instead of 40 near-identical tracebacks.

**Desired outcome:** A test-output-aware pass (pytest/Jest/Vitest, reusing the existing per-tool
filters as the entry point) that recognizes non-adjacent, same-root-cause failures across an entire
run and summarizes them together, while still surfacing genuinely distinct failures individually.

**Open question:** what counts as "same root cause" safely and deterministically (matching
assertion text? matching the first differing line of a diff?) without inventing an unreliable
heuristic — this is the same caution QB-036's design work already applied when it rejected a naive
"deduplicate visually similar lines" rule for diagnostics.

**Status:** Proposed. Not scoped or implemented.

</details>

---

#### QB-045 — Broader build & CI log compression

**Effort:** Medium · **Value:** Medium · **Risk:** Low · **Expected token impact:** Medium ·
**Category:** Feature

`build.toml` covers mypy today; the Node ecosystem work (QB-006A/B/C) covers the JS/TS build
toolchain. Neither covers the other extremely common build/CI surfaces a coding session runs into:
Docker build output, webpack/bundler output outside the Node filters already shipped, and raw CI
runner logs (GitHub Actions, etc.) when a user pastes or `cat`s one in for the assistant to
diagnose.

**Evidence update (2026-07-15) — no clear signal either way:** neither the benchmark corpus nor
real usage has a dedicated CI-log/Docker-build category or filter to measure against — `ruff` (the
closest real analog, build-tooling output) actually performs reasonably in real usage (23.2% avg
over 94 invocations), giving no evidence this class of output is broken the way mypy/pytest are.
Stays in Next, not promoted — recommend waiting for QB-047's corpus work before investing further,
rather than building against zero evidence.

<details>
<summary>Technical details</summary>

**Problem:** No filter exists for `docker build`, generic bundler output beyond what QB-006C
covers, or CI log formats. These are high-noise, high-volume, and structurally similar to output
Quor already handles well (progress spinners, layer-cache noise, timestamped log lines) — closer in
spirit to `node.toml`'s "strip generic wrapper noise" approach (QB-006A) than to anything requiring
new stage types.

**Desired outcome:** New filters following the same pattern already proven for npm/pnpm/yarn:
`remove_ansi`, `group_repeated`, `strip_lines` with a `preserve_patterns` safety net for actual
errors/warnings, no new stage types required.

**Status:** Proposed. Not scoped or implemented. Lowest-risk item in this section — almost entirely
a matter of writing new filter `.toml` files using stages that already exist and are already
well-tested.

</details>

---

#### QB-040 — Config & structured-data file compression (YAML/JSON/TOML/.env/.ini)

**Effort:** Medium · **Value:** High · **Risk:** Low · **Expected token impact:** Medium ·
**Category:** Feature

Quor already compresses documents (Markdown, DOCX, PDF — QB-007) and source code (Python/JS/TS —
QB-005), but a `Read` or `cat` of a config file — `package.json`, a Kubernetes YAML manifest, a
`.env` file, a large `pyproject.toml` — gets no treatment at all today. These files are extremely
common in a real coding session and often large (lockfiles, generated configs), with a lot of
low-value repeated structure (boilerplate keys, generated comments, long dependency lists) that a
coding assistant rarely needs in full.

**Evidence update (2026-07-15) — demoted from [Now](#now):** this item still cannot be evaluated
against real measured evidence — there is no benchmark category for it, and its filter name (it
doesn't have one yet) obviously can't appear in real usage either. The `Generic` ecosystem's low 5%
benchmark contribution is *not* evidence for or against it (that ecosystem is dominated by
already-terse commands like `ls -la`, not config files), and config files never appear anywhere in
90 days of real cross-project usage (checked directly against the tracking DB's top filters —
absent entirely). Moved to Next, not dropped: the underlying idea is still sound, it's just
unmeasured, and shouldn't compete for a release slot against items with direct evidence behind them
(QB-041, QB-052) until QB-047 gives it a corpus to be measured against.

<details>
<summary>Technical details</summary>

**Problem:** No filter or extraction path exists for `.yaml`/`.yml`/`.json`/`.toml`/`.env`/`.ini`
content, whether read via `cat` (Bash) or Claude Code's native `Read` tool. QB-007's document
extraction and QB-005's AST summarization both deliberately scoped this out.

**Desired outcome:** Structure-aware compression for the most common config formats — e.g. for a
large JSON/YAML file, collapse long homogeneous arrays (a lockfile's hundreds of near-identical
dependency entries) while preserving keys/schema shape and any genuinely distinct values; for
`.env`, strip comments/blank lines but never touch a value (these are frequently secrets — this
must compose correctly with QB-029's secret scanner, not fight it).

**Why this is a natural fit for the existing architecture:** `.toml` itself is already a first-class
citizen of Quor's own filter-config format, and `regex_replace`/`strip_lines`/`max_tokens` (QB-008,
QB-009, existing stages) already provide most of the primitives needed for the simpler formats
(`.env`, `.ini`). JSON/YAML's nested-array-collapsing is the one genuinely new piece of logic,
closer in spirit to QB-007's structure extraction than to a simple line filter.

**Risk, stated plainly:** config files are exactly the place where "safe, deterministic" matters
most — a wrong compression here can silently change what a generated config *means* to a build tool,
not just what it looks like to a human. This item should ship Safe-mode-only initially (see QB-039)
regardless of when Aggressive mode ships elsewhere.

**Status:** Proposed. Not scoped or implemented.

</details>

---

#### QB-042 — Continuous competitive benchmarking (RTK, Headroom AI, ZAP)

**Effort:** Medium · **Value:** Medium · **Risk:** Low · **Expected token impact:** None directly ·
**Category:** Engineering / Measurement

Quor's competitive positioning today comes from a one-time research document
(`docs/archive/product-discovery/competitive-research.md`) that's already referenced repeatedly
throughout this backlog (QB-032, QB-034, QB-035) but was written once and never re-run. Under the
new vision — "maximum practical token reduction" — "practical" needs an external yardstick:
proof Quor's real compression numbers hold up against the tools it's actually competing with, on an
ongoing basis, not a one-time snapshot from whenever that research happened.

**Evidence update (2026-07-15) — demoted from [Now](#now):** unaffected in substance by this
review's numbers — it's a positioning/measurement item with no compression payoff of its own, and
every item now ranked ahead of it in Now (QB-041, QB-052, QB-047, QB-046, QB-049, QB-039, QB-054,
QB-053) closes an internal, evidenced gap. Closing what we already know is broken should come
before building an external comparison. `tests/benchmarks/history.py` (QB-051) remains a reusable
building block whenever this item is scoped.

<details>
<summary>Technical details</summary>

**Problem:** No automated way exists to compare Quor's benchmark suite results (QB-011, QB-005E)
against a competitor's on the same corpus. The competitive research that already shaped several
backlog decisions (QB-032's traceback compression, QB-034's `discover` command, QB-035's language/
agent coverage) is a snapshot, not a live signal — it could already be stale in either direction (a
competitor shipping something new, or Quor closing a gap the research flagged).

**Desired outcome:** Extend the existing benchmark harness (`tests/benchmarks/`) to optionally run
the same corpus (or a licensing-safe subset) through a competitor tool where one is installable
(e.g. RTK, if it's a local CLI), and report both results side by side — not to fabricate numbers
for tools that can't be run this way, but to make the comparison real and repeatable wherever it
can be.

**Open question:** several competitors may not be locally runnable (SaaS-only, closed source) — in
that case this item's scope narrows to "keep the competitive research doc itself on a review
cadence" rather than automated head-to-head numbers. Worth scoping which is realistic before
committing effort here.

**Status:** Proposed. Not scoped or implemented.

</details>

---

#### QB-048 — Compression quality & AI task-success evaluation

**Effort:** Large · **Value:** High · **Risk:** Medium · **Expected token impact:** None directly
(this measures, it doesn't compress) · **Category:** Engineering / Measurement

Every benchmark Quor has today measures one thing: how much smaller did the output get. None of
them measure whether the AI could still do its job with the smaller version. Under "safe,
deterministic compression," that gap was arguably acceptable — the whole design philosophy was
"never remove anything risky enough to need this check." Under "maximum practical token reduction,"
especially once [Compression Modes](#qb-039--compression-modes-safe--balanced--aggressive) ships an Aggressive mode, this gap becomes the thing
standing between "aggressive" and "reckless."

**Evidence update (2026-07-15) — role clarified, not deprioritized:** this item isn't "postponed" in
the sense the other demoted items are — nothing in this review measures task success, only token
counts, so nothing here can confirm or challenge this item's own value. What the review *does* add:
two new items depend on this one more directly than before. QB-053 (adaptive compression) and
QB-039 (Aggressive mode) both trade some safety margin for more compression; this item is what would
prove that trade isn't quietly hurting task success. Its sequencing (before any of those three reach
default-on) is unchanged from its original entry.

<details>
<summary>Technical details</summary>

**Problem:** No eval harness exists that runs a real (or realistic) coding task through Claude with
compressed vs. uncompressed context and compares task success. `must_contain` assertions in the
benchmark suite (QB-011) prove specific substrings survive compression; they don't prove the AI can
still *use* what survived to do the task correctly.

**Desired outcome:** A small, initially narrow eval suite — a handful of realistic coding tasks
(fix this bug given this compressed traceback; extend this function given its compressed-body
signature) — run against both the compressed and uncompressed input, scored on task success, not on
output similarity. Starts small and specific rather than attempting a comprehensive eval framework
on the first pass.

**Why this matters more than its own token-impact score suggests:** this is the prerequisite that
makes QB-039 (Aggressive mode), QB-053 (adaptive compression), and QB-043 (cross-call optimization)
safe to ship rather than speculative — see [Product Metrics](#product-metrics)'s "AI task success
rate" row, currently unmeasured, and [Strategic Roadmap](#strategic-roadmap) Phase 2/3 sequencing.

**Status:** Proposed. Not scoped or implemented. Recommend this lands before or alongside QB-039's
Aggressive mode (or QB-053's automatic equivalent) reaching any default-on state, not strictly
before either is scoped/designed.

</details>

---

## Later

*Real, approved product work — but lower urgency, or deliberately sequenced after other items prove
out first. Not the same thing as [Research](#research): everything here is a normal backlog item
that could be scoped and built; it's just not next.*

---

#### QB-050 — A sanctioned track for experimental compression algorithms

**Effort:** Medium · **Value:** Low · **Risk:** Medium · **Expected token impact:** None directly
(this is infrastructure for future items, not compression itself) · **Category:** Engineering

The [Research](#research) section below lists several genuinely interesting algorithmic approaches
(learned token reduction, semantic extraction, hybrid pipelines) that are explicitly not approved
for implementation. This item is the bridge that would let a promising research idea actually be
tried, behind a flag, without it destabilizing Quor's deterministic core — currently there's no
defined path from "Research" to "Next" other than rewriting this whole document.

<details>
<summary>Technical details</summary>

**Problem:** Quor already has a working, precedented extension mechanism —
`quor.compression_stage`/`quor.plugin` entry-point discovery (`quor/pipeline/plugin_loader.py`,
ADR-026), used today for third-party `StageHandler`s. QB-035A's own audit confirmed this mechanism
is cached, fail-open per entry, and already reported on by `quor doctor`. It was designed for
deterministic, synchronous stages — nothing in it assumes or forbids a stage that calls out to a
model or a learned component, but nothing has ever tested that path either.

**Desired outcome:** Confirm (not assume) that an experimental, non-deterministic, or
model-backed compression stage can be built as a normal plugin using the existing mechanism —
flagged clearly as experimental in `quor doctor`/`quor explain` output, opt-in only, never part of
any default filter. If the existing plugin architecture genuinely already supports this, this item
may turn out to be small — a documented pattern and one worked example, not new infrastructure. If
it doesn't (e.g. the fail-open contract assumes synchronous, deterministic execution in a way a
model call would violate), that gap is this item's real finding.

**Status:** Proposed. Not scoped or implemented. Low urgency until a specific Research item is
promising enough to need a real implementation path.

</details>

---

#### QB-035 — Support more AI coding tools (Cursor, Copilot, Gemini)

**Effort:** Large (multiple multi-week efforts) · **Value:** Medium · **Risk:** Medium · **Expected token impact:** None directly (this is reach, not compression depth) · **Category:** Feature

**Note:** this item's original title also covered "more programming languages" — that half is now fully superseded by [QB-046](#qb-046--ast-aware-summarization-for-more-languages-go-rust-java-c) in [Next](#next), which gives language expansion its own value ranking separate from multi-agent support. What remains here is the multi-agent-adapter half only.

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

**Update (QB-005A–QB-005F):** the "language" half of this item's scope is now functionally
complete, measured, *and reachable end-to-end* for the two languages this item named: a design
(`docs/design/QB-005A-ast-summarization-design.md`), a reusable multi-language parser framework
(`quor/pipeline/ast_summarize/`), genuine JavaScript (QB-005C: `.js`/`.jsx`/`.mjs`/`.cjs`) and
TypeScript/TSX (QB-005D: `.ts`/`.tsx`, including interfaces/type aliases/enums/namespaces/overload
signatures/abstract classes) AST summarization, a real benchmark corpus (QB-005E: 12 cases) with
measured compression numbers and a characterized runtime profile (parser time is a small, roughly
linear-scaling fraction of an already-sub-10ms pipeline, even at synthetic 1000-function scale),
and — as of QB-005F — Read-hook integration: `.py`/`.js`/`.jsx`/`.mjs`/`.cjs`/`.ts`/`.tsx` files
opened via Claude Code's native `Read` tool (not just `cat`'d through Bash) now get the same
AST-compressed treatment, closing the pre-existing gap this item's own earlier update flagged
(even Python's Read-based summarization didn't work before QB-005F). The entire QB-005 phased plan
(QB-005A→F) is now complete. Go/Rust/Java (also named in this item's original scope) remain
unstarted — QB-005A through QB-005F only ever targeted "at least JS/TS," per the design's own
Section 9 plan.

**Update (QB-035A):** the "multi-agent" half of this item has now had its first, design-only pass —
see the dedicated `<details>` block below. No agent support shipped; `ANTI_GOALS.md` #12's "no
multi-agent support in V1" and this item's own "revisit only after validation exists" guidance are
both still in force and explicitly not overridden by a design document.

</details>

<details>
<summary>QB-035A technical details — Multi-Agent Adapter Architecture Design</summary>

**Problem:** Before any second agent could be built, QB-035A asked how Quor's architecture should
generalize to support multiple AI coding agents without duplicating compression logic, without
`FilterRegistry`/`Pipeline`/tracking becoming agent-aware, and without branching on agent names
spreading through the codebase. Design/infrastructure only — no new agent implemented, no runtime
behavior changed.

**Headline finding:** reading every named file first (Claude adapter, dispatcher, Read hook,
tracking, `FilterRegistry`, `Pipeline`, CLI, hook installation, doctor checks) found that
`quor/rewrite/`, `quor/filters/registry.py`, all of `quor/pipeline/` (including `extract/`), and
`quor/tracking/db.py` are **already 100% agent-agnostic** — verified by grepping every one of them
for any agent-name reference and finding only docstring mentions of *callers*, never a branch, a
field, or a name check. `InvocationRecord` has no agent column; `Pipeline.execute()` takes a
`ContentMask`; `FilterRegistry.find()` takes a bare string. This means the entire task was scoped to
`quor/adapters/`, `quor/__main__.py`, and two CLI commands (`init`, `doctor`) — nothing else needed
to move, and nothing else is touched by the design.

Three things shaped the design, recorded because they weren't obvious going in:
1. `quor/adapters/base.py` already declares a `HookAdapter` Protocol (`run_hook(self) -> None`) —
   unused anywhere in the codebase, zero references outside its own definition. `PROJECT_BIBLE.md`'s
   original architecture diagram already labels `base.py` as holding a `HookAdapter` Protocol and
   `claude.py` as *one* conforming adapter — the multi-adapter shape was the intent from the
   project's first architecture pass; only the reference implementation was ever built.
2. A working precedent for exactly this kind of extension already ships:
   `quor.compression_stage`/`quor.plugin` entry-point-group discovery (`quor/pipeline/plugin_loader.py`,
   ADR-026) — cached, fail-open per entry, aggregated into a report `quor doctor` consumes. The
   design proposes a third group, `quor.hook_adapter`, discovered the identical way.
3. `claude.py`/`claude_read.py` both strip a *doubled* UTF-8 BOM with an inline comment "Cursor
   sends doubled BOM on Windows," confirmed as a documented, known behavior in `PROJECT_BIBLE.md`
   item 9 — empirical (if informal) evidence that Cursor's hook payload has already been observed
   close enough to Claude Code's own shape that BOM-stripping was the only accommodation needed so
   far. Not proof Cursor's contract matches (explicitly flagged as unverified and a real risk for
   whichever future phase implements a real second adapter), but a concrete data point against
   assuming every future adapter needs an entirely novel payload model.

**Genuine, pre-existing duplication found and explained, not fixed here (per the task's explicit
"stop and explain before changing anything" instruction):** `claude.py` and `claude_read.py`
independently re-implement the same BOM-stripping constant/line, structurally parallel hook-script
templates, and both read `sys.stdin`/write `sys.stdout.buffer` directly inside `run_hook()` — which
is why every existing adapter test has to monkeypatch both streams. Not a bug, not urgent today
(both files are correct and well-tested), but exactly the kind of duplication that compounds with a
third and fourth adapter. The design's proposed `bytes`-in/`bytes`-out `handle_event()` contract
(§3.3 of the design doc) retires this as part of a future migration, not as a change made in this
phase.

**What shipped (documents only):**
- `docs/design/QB-035A-multi-agent-adapter-design.md` — full design: current-state audit (agnostic
  vs. coupled, with evidence), design principles reused from existing precedent (Protocol not ABC,
  two-tier built-in-dict + entry-point discovery, fail-open throughout), the proposed
  `AgentEvent`/`AgentAdapter`/`AdapterRegistry` architecture, adapter lifecycle (and why it
  deliberately does *not* copy `Plugin`'s `initialize()`/`shutdown()` — a hook invocation is a
  brand-new OS process each time, there is no cross-call state to manage), complete interface
  signatures, extension points, failure model, testing strategy, a 6-step migration plan with an
  explicit backward-compatibility recommendation for the `quor hook claude` → `quor hook <agent>
  <event>` argv shape change, 6 named risks, 4 design trade-offs with rejected alternatives, and a
  complete list of every file that would eventually need modification.
- `docs/final/DECISIONS.md` — **ADR-036**: the formal decision record (options considered,
  consequences), mirroring ADR-034/ADR-035's format.
- This `backlog.md` entry and the parent QB-035 update above.

**What did NOT change:** no source file under `quor/` was modified. No new module, class, or
function exists yet. `quor/adapters/base.py` still has the unused `HookAdapter` Protocol exactly as
before (removal is a QB-035E step, once `AgentAdapter` supersedes it). `__main__.py`'s hardcoded
`_HOOK_ADAPTERS` routing, `init.py`'s `--claude`-only flag, and `doctor.py`'s hardcoded Claude-
specific checks are all unchanged — the design describes how they *would* generalize, not a change
to how they work today.

**Remaining work, split into phases (see the design doc's §14 for full detail):**
- **QB-035B** — Implement `AgentEvent`/`AgentAdapter`/`AdapterRegistry` + `ClaudeAdapter` wrapping
  today's `claude.py`/`claude_read.py` with proven byte-for-byte equivalence. No routing/CLI
  changes — safest, fully independent increment.
- **QB-035C** — Migrate `__main__.py` hook routing to the registry, with the `quor hook claude` →
  `quor hook <agent> <event>` back-compat alias decision made explicit and tested against a real,
  already-installed hook script.
- **QB-035D** — Migrate `quor doctor` to a per-adapter `doctor_checks()` loop.
- **QB-035E** — Migrate `quor init` to `--agent`, retire `init.py`'s inline Claude-specific logic
  into `ClaudeAdapter.install()`, remove the now-superseded `HookAdapter` Protocol.
- **QB-035F** (gated on explicit product go-ahead, not automatic — this is the item
  `ANTI_GOALS.md` #12 actually names as V2 work) — verify a real second agent's actual hook
  contract (Cursor is the best-evidenced starting candidate) and implement its `AgentAdapter` as
  the first proof the abstraction holds for more than one agent.
- Unscoped, flagged but not filed as their own items: `quor explain`'s missing equivalent for
  `CONTENT_INTERCEPT`-shaped events; an optional `AdapterError` exception type; whether
  `AgentAdapter` needs a `file://` local-development escape hatch like stages already have.

**Validation:** `ruff check quor/ tests/` clean (no source changed). `mypy quor/` — Success, no
issues (no source changed). Full `pytest` — 0 failures (no source changed; this run exists to
confirm the design phase introduced no regression, not because any test-affecting code moved).
`quor verify` — unchanged pass count (no filter touched). No benchmark run required — no
`quor/pipeline/`, `quor/filters/`, or `quor/rewrite/` file changed, so the "Before Opening a PR"
benchmark-suite trigger in `docs/final/CLAUDE.md` does not apply to this phase.

**Status:** Design complete and merged to `main` — the design document (`docs/design/QB-035A-multi-agent-adapter-design.md`) shipped in Quor **v0.4.0** (2026-07-11). *(Correction: this entry originally read "not committed"; verified against `CHANGELOG.md` and `git log` while restructuring this document.)* No architectural conflict was found that blocked this design; the one real duplication found (adapter BOM-stripping/stdio boilerplate) was surfaced and explained, not fixed, per the task's own instruction. **No implementation code was added** — `AgentEvent`/`AgentAdapter`/`AdapterRegistry` (QB-035B onward) remain unbuilt; see the open QB-035 entry.

</details>

---

#### QB-034 — Show new users what Quor would have saved them, retroactively

**Effort:** Medium · **Value:** Medium · **Risk:** Low · **Expected token impact:** None directly (adoption tool, not a compression change) · **Category:** Feature

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

## Research

**Research only. No implementation approved.** Everything in this section is a promising idea, not
a plan. Nothing here should be built without first going through the same design-first discipline
QB-005A/QB-035A/QB-036 already established for this project — and, per the new [Product
Metrics](#product-metrics), without a way to measure it against task-success, not just compression
percentage. [QB-050](#qb-050--a-sanctioned-track-for-experimental-compression-algorithms) is the (currently also unbuilt) bridge that would let one of these
graduate to an actual backlog item.

Why these are research-stage rather than backlog items, as a general pattern: Quor's trust model
today rests on being deterministic and explainable — the same input always produces the same
output, and `quor explain` can show exactly why. Every idea below trades some amount of that away
in exchange for potentially much higher compression. That trade might be worth it. It hasn't been
evaluated, which is exactly why it's here and not in [Next](#next).

---

**R-01 — Multi-stage / recompression pipelines.** Running a second compression pass over already-
compressed output (or chaining multiple distinct compression strategies) could unlock savings
neither pass would find alone. Unstudied: whether this compounds errors/information loss the same
way lossy image re-encoding does, and whether it's legible to `quor explain` at all once two stages'
worth of decisions are stacked.

**R-02 — LLM-Lingua-style prompt compression.** Microsoft's LLM-Lingua family compresses prompts by
using a small model to score token-level importance and drop low-information tokens, rather than
Quor's current line/pattern-based approach. Genuinely higher potential compression ratios.
Unstudied here: latency (an extra model call per compression), a new runtime dependency, and — most
importantly — that it's fundamentally non-deterministic in a way every existing Quor stage isn't.

**R-03 — TextPress-style approaches.** Referenced in prior discussion as a text-compression
technique worth evaluating; not yet independently verified or scoped by this team. Treat as a
pointer to investigate, not a validated approach — the first real step here is confirming what it
actually does and whether it's applicable to code/command output specifically, not prose.

**R-04 — Neural / model-based compression.** A broader category than R-02 specifically: any approach
where a trained model, rather than deterministic rules, decides what to keep. Highest theoretical
ceiling, highest trust cost — this is the approach most directly in tension with "safe,
deterministic" as a property, even under the new vision's more aggressive risk tolerance.

**R-05 — Learned token reduction (small classifier model).** A narrower, more tractable version of
R-04: a small, fast, locally-run classifier (not a full LLM call) that scores line-level
"keep-worthiness," used to inform — not replace — existing deterministic stages. Interesting because
it could stay fast and local, unlike R-02/R-04. Unstudied: training data source, how it would be
validated to not silently drift, and how `quor explain` would present a probabilistic decision
next to today's deterministic ones.

**R-06 — Semantic extraction (embedding/summarization-based).** Instead of pattern-matching
structure (what QB-007's document extraction and QB-005's AST summarization both do today),
represent content by meaning — an embedding or a generated summary — rather than a structurally
reduced version of the original text. Most relevant to prose/document content, less obviously
applicable to code or command output where structural fidelity (a function's actual signature, not
a paraphrase of it) usually matters more than semantic gist.

**R-07 — Hybrid deterministic + learned approaches.** Rather than replacing Quor's existing filters,
use a learned component (R-05) as a second-opinion check on top of them — e.g. flagging when a
deterministic filter's output looks like it may have removed something important, without the
learned component making compression decisions itself. Interesting as a lower-trust-cost way to get
some of R-04/R-05's benefit without fully giving up determinism. Still unstudied end to end.

---

## Completed

*39 resolved items — nothing shortened or removed, only regrouped. Previously organized by a
"Priority" label assigned when each item was opened (and, as items aged, increasingly inconsistent
with each item's own stated Value); now grouped by **Value** instead, so the highest-impact
completed work is the first thing anyone sees. Ordered by ID within each tier for predictability.*

*The single biggest thing this restructuring found here: several items in this section had drifted
out of date about their own commit status. Each has been corrected in place — see the note at the
top of this document.*

### High Value

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

#### QB-005 — Smarter Python file reading (structure instead of full text)

**Effort:** Large · **Value:** High · **Category:** Feature

When Claude reads a Python file through Quor, it now gets a compressed view — full function
signatures and docstrings, but function bodies summarized — instead of the entire file every time.
This significantly cuts token usage on large Python files while keeping the information Claude
actually needs to work with the code. If anything about a file confuses the summarizer, it safely
falls back to sending the original, unmodified content rather than risk sending something wrong.
QB-035 later asked for the same treatment for JavaScript/TypeScript; QB-005A designed how to
generalize this feature to more languages without touching Python's already-shipped behavior,
QB-005B built that generalized framework (proven correct using Python only, zero new dependencies),
QB-005C shipped real JavaScript support on top of it, and QB-005D has now added TypeScript and TSX
too — `.ts`/`.tsx` files (plus the existing `.js`/`.jsx`/`.mjs`/`.cjs`) read through Quor get the
same signature-preserved, body-compressed treatment, including TypeScript-only constructs
(interfaces, type aliases, enums, namespaces, overload signatures, abstract classes/methods) kept
fully intact, with Python's and JavaScript's own behavior still byte-for-byte unchanged throughout.
QB-005E has since measured all of this against a realistic JS/TS/TSX benchmark corpus (12 new
cases, 60 total, zero regressions on the 48 pre-existing cases) and characterized the AST
machinery's own runtime behavior — parser time is a small, bounded fraction of an already-fast
pipeline, even at synthetic 1000-function scale. QB-005F has now closed the pipeline's own final
gap: this compression previously only ever fired when Claude Code ran `cat some_file.py` through a
Bash tool call — a direct `Read` of the same file (Claude Code's default, and by far the most
common way source files actually reach the model) got none of it. `.py`/`.js`/`.jsx`/`.mjs`/
`.cjs`/`.ts`/`.tsx` Read calls now route through the exact same filters by name, with the same
fail-open guarantees and the same QB-007D tracking, closing out the whole QB-005 phased plan.
See "Sub-items" below.

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

<details>
<summary>QB-005A technical details — AST-aware compression architecture design</summary>

**Problem:** QB-005 shipped Python-only, and QB-035 flagged extending the same idea to JavaScript/
TypeScript as real, deferred future value. Before writing any code, CLAUDE.md's Rule 4
("competitor-first design... present the recommendation for approval before implementation")
required a full design pass: where parsing should live, what representation it should produce, how
compression should behave per-language, what fails open and how, which parser library to use, and a
phased rollout plan.

**What shipped:** a standalone design document,
`docs/design/QB-005A-ast-summarization-design.md` — not code. It answers all nine questions the
task posed (parsing location, AST representation, compression strategy, failure behavior, parser
selection, performance, testing strategy, risks, phased plan) against Quor's actual, already-read
architecture (`ContentMask`/`StageHandler`/`FilterRegistry`/`Pipeline`, the QB-005
`python_ast_summarize` precedent, and the QB-007E1–E4 `extract()` precedent for binary-document
handling). Key conclusions: AST parsing belongs inside a `StageHandler` (not before `Pipeline`, not
inside `FilterRegistry`); the stage must produce `ContentMask` decisions exclusively, never a new
intermediate format; and — the one real trade-off surfaced — no pure-Python parser supports current
TypeScript syntax, so JavaScript/TypeScript support is designed around `tree-sitter` as a new,
optional `quor[javascript]` extra (mirroring the already-shipped `quor[documents]` precedent from
QB-007E2/E3), not a core dependency.

**No architectural conflict was found that required stopping.** The document explicitly states this
and explains the one deliberate deviation (compiled-but-optional `tree-sitter`, not "pure Python")
rather than silently working around it.

**A pre-existing gap was found, not created:** Read-based `.py` file access does not get AST
summarization today — `quor/adapters/claude_read.py`'s filter allowlist only covers
`markdown`/`document-text`. Flagged explicitly in the design's Section 8/9 rather than left
undocumented, and folded into the phased plan as QB-005F.

**Phased plan produced:** QB-005B (parser framework, Python-only proof) → QB-005C (JavaScript) →
QB-005D (TypeScript) → QB-005E (benchmarks) → QB-005F (Read-hook integration, closes the gap above).

**Status:** Design complete and merged to `main` (the design document, `docs/design/QB-005A-ast-summarization-design.md`, was carried into `main` as part of QB-005B's own PR rather than on its own branch, and is present on `main` today).

</details>

<details>
<summary>QB-005B technical details — AST parser framework (Python proof of concept)</summary>

**Problem:** QB-005A's design called for a reusable, multi-language parser framework, proven with
Python before any new dependency (`tree-sitter`, for JS/TS) is introduced. QB-005's original
implementation had the `ast`-parsing logic hardcoded directly inside
`quor/pipeline/stages/python_ast_summarize.py`, with no separation between "how to find compressible
lines in Python source" and "how a `StageHandler` turns that into `ContentMask` decisions" — a
structure that would have forced every future language to either duplicate the stage-bookkeeping
half or fork the whole file.

**What shipped:**
- **New package, `quor/pipeline/ast_summarize/`** (mirrors `quor/pipeline/extract/`'s own shape —
  a routing table, no `Protocol`/ABC for a single-callable contract, same judgment QB-007E1 already
  made for that package): `python.py` holds `analyze_python(source: str) -> set[int]`, wrapping
  `_compressible_body_lines()`/`_body_line_range()` **relocated from `python_ast_summarize.py`
  unmodified** — not rewritten, not reimplemented, byte-for-byte the same functions in a new home.
  `registry.py` holds a `language -> analyzer` dict (`_ANALYZERS`) and `get_analyzer(language) ->
  Callable[[str], set[int]] | None`; only `"python"` is registered in this phase.
- **`quor/pipeline/stages/python_ast_summarize.py` now delegates** to
  `get_analyzer("python")` instead of calling `ast.parse()` directly. Its class name, `stage_type`
  ("python_ast_summarize"), config shape (`PythonAstSummarizeConfig`), and every observable behavior
  are unchanged. `cat-python.toml` required zero changes.
- **New generic stage, `quor/pipeline/stages/code_ast_summarize.py`** — the filter-configurable
  counterpart QB-005A designed: a `language: str` field on its config, dispatching through the same
  registry `python_ast_summarize` now uses, so there is one shared implementation of Python's
  body-compression logic, not two. Registered in `quor/filters/registry.py`'s `_STAGE_HANDLERS` (so
  `type = "code_ast_summarize"` is a legitimate, usable stage type from day one) but **not wired
  into any built-in filter yet** — no filter TOML references it, exactly like QB-007E1's own
  "framework proven directly by unit tests, no real filter wiring yet" precedent.
- **One documented, deliberate deviation from QB-005A's own prose:** Section 4.2 of the design
  imagined the "unsupported language" fail-open check living inside `can_handle()`. Implementation
  found this isn't possible without changing the `StageHandler` Protocol — `can_handle(self,
  content, content_type)` has no access to `StageConfig` (confirmed against every one of the ten
  other built-in stages, none of which receive their own config in `can_handle()` either). Out of
  scope for an infrastructure-only phase that must not modify any existing interface — the check
  was implemented one call deeper, inside `apply()`, which is observably identical from
  `Pipeline.execute()`'s perspective (a stage that ran and changed nothing). Documented in
  `code_ast_summarize.py`'s own module docstring, not silently papered over.

**Fail-open contract, made explicit for the first time:** the new registry's contract deliberately
differs from `quor/pipeline/extract`'s `extract()` — `extract()` never raises (every failure is
absorbed to `None`, since document extraction has no engine-level safety net above it);
`get_analyzer()`'s returned callable **does** raise on a genuine parse failure for a *registered*
language (e.g. `SyntaxError` for invalid Python), because it runs inside a `StageHandler.apply()`
that already has `Pipeline.execute()`'s per-stage fail-open guarantee above it. `None` from
`get_analyzer()` means something narrower and different: "no analyzer is registered for this
language at all." Both modules' docstrings state this distinction explicitly so it can't be
conflated by a future contributor extending the registry.

**Proof of zero behavioral change:** two independent checks, both green.
1. A standalone before/after harness ran `PythonAstSummarizeStage.apply()` across 14 fixtures
   (every case already covered by `TestPythonAstSummarize`, plus a handful more — try/except
   import-fallback defs, `if TYPE_CHECKING:`-gated defs, a `with`-block def) against the pre-refactor
   code and the post-refactor code, dumping every `LineMask`'s `(decision, reason, stage, line)`
   tuple. The two dumps diff byte-for-byte identical, including the two fixtures that raise
   (`SyntaxError` for invalid syntax, `SyntaxError`/`ValueError` for a null byte) — same exception
   type, same message, in both runs.
2. The entire pre-existing `TestPythonAstSummarize` suite (18 tests in
   `tests/unit/test_stages.py`) — including the two tests that assert `apply()` itself raises
   `SyntaxError` on invalid input rather than swallowing it — passes **unmodified** against the
   refactored stage.

**New test coverage added** (nothing pre-existing was changed): `tests/unit/test_ast_summarize.py`
(registry routing, the registry's fail-open-contract distinction from `extract()`, `analyze_python`
correctness in isolation) and a new `TestCodeAstSummarize` class in `tests/unit/test_stages.py`
(empty input, wrong config type, unsupported-language fail-open, syntax-error propagation at both
the stage and pipeline level, `preserve_patterns`, and a parametrized equivalence test proving
`code_ast_summarize(language="python")` and `PythonAstSummarizeStage` produce byte-for-byte
identical decisions on six shared fixtures).

**What was deliberately not touched:** no new dependency, no `tree-sitter`, no JavaScript/TypeScript
analyzer, no Read-hook integration (`quor/adapters/claude_read.py` untouched), no benchmark changes
(`tests/benchmarks/` untouched, all 145 benchmark tests still pass unmodified), no built-in filter
changed (`cat-python.toml` untouched) — exactly QB-005A's own QB-005B scope.

**Environment note (unrelated to correctness, worth recording):** this session's own shell commands
were themselves being intercepted by a locally-installed Quor Claude Code hook (running Quor to
develop Quor) — its dispatcher's hardcoded 25-second subprocess timeout
(`quor/adapters/dispatcher.py::_run_subprocess`) caused a handful of `pytest`/`ruff` invocations
covering many files at once to be killed mid-run with no output, purely a local-environment
artifact of validating this specific PR in this specific way, not a project or code defect. Worked
around by running validation in smaller per-file/per-directory batches; every batch completed with
exit code 0 and zero `F`/`E` markers. Full unit + integration + benchmark suite (1,281 unit tests +
integration tests + 145 benchmark tests) and `quor verify` (77/77 filter tests, including
`cat-python`) all pass.

**Validation:** `ruff check quor/ tests/` clean (one import-order issue auto-fixed in
`quor/filters/registry.py` during implementation). `mypy quor/` — Success, no issues, 64 source
files. Full `pytest` (batched per the note above) — 0 failures across every file. `quor verify` —
77/77 filter tests pass.

**Status:** Implemented and merged to `main` — shipped in Quor **v0.4.0** (2026-07-11). *(Correction: this entry originally read "not committed" — verified against `CHANGELOG.md` and `git log` while restructuring this document; the branch was merged via the `integration/stabilize-ast-and-early-exit` PR and the code is present on `main` today.)*

</details>

<details>
<summary>QB-005C technical details — JavaScript AST analyzer</summary>

**Problem:** QB-005B built the multi-language parser framework but proved it with Python only —
no new dependency, no tree-sitter. QB-005A's design called for JavaScript next, using `tree-sitter`
+ `tree-sitter-javascript` as a new optional `quor[javascript]` extra (Section 5), implementing the
mandatory ERROR-node-overlap exclusion rule (Section 4.1), and reusing the generic
`code_ast_summarize` stage from QB-005B rather than a JS-specific stage class.

**What shipped:**
- **New analyzer, `quor/pipeline/ast_summarize/javascript.py`** — `analyze_javascript(source: str)
  -> set[int]`, same return contract as `analyze_python()`. Registered unconditionally in
  `quor/pipeline/ast_summarize/registry.py`'s `_ANALYZERS` dict (no try/except at import time) —
  possible because `tree_sitter`/`tree_sitter_javascript` are imported **lazily, inside the
  function**, mirroring `quor/pipeline/extract/docx.py`'s identical discipline for `python-docx`.
  Missing dependency: warns with an actionable `quor[javascript]` message and returns an **empty
  set** (not `None` — this module's contract is non-optional `set[int]`) — from
  `code_ast_summarize.py`'s perspective this is indistinguishable from "no functions found," so
  zero changes were needed to that already-shipped file. This is a second, deliberate extension of
  QB-005B's own established deviation (moving a fail-open check from `can_handle()`, which has no
  `StageConfig` access, into the analyzer/`apply()` layer) — not a new, third mechanism.
- **JS-to-Python node mapping** (empirically verified against the installed grammar during
  implementation — see "Real bug found" below for why empirical verification mattered here):
  `function_declaration`/`generator_function_declaration` (named top-level functions),
  `method_definition` (class members — constructor/regular/async/generator/getter/setter all share
  this one node type; recursed into one level via `class_declaration`'s `body` field, methods
  never recursed into further), and `arrow_function`/`function_expression`/`generator_function`
  assigned via a `variable_declarator`'s `value` field (`const`/`let`/`var`). `export_statement`
  (including `export default`) is unwrapped via its `declaration` field and re-dispatched through
  the same top-level logic, so `export function foo() {}` is treated identically to a bare
  `function foo() {}`. Each function-like node's compress range is the lines **strictly between**
  its `statement_block`'s opening `{` and closing `}` lines — unlike Python, JS's opening brace is
  almost always on the signature's own line, so (unlike `_body_line_range()`, which had no brace to
  preserve) both brace lines must be explicitly excluded or the signature would be destroyed.
  JSDoc/decorators need **zero special-casing** — verified structurally, not assumed: both are
  sibling nodes entirely outside the function/class node's own span (unlike Python, where a
  docstring is the function's own first body statement and needs explicit exclusion), so they were
  never going to be touched in the first place.
- **ERROR-node exclusion, implemented and empirically verified as mandatory, not skipped:**
  `_collect_error_ranges()` walks the tree once (only when `root.has_error`) collecting every
  `ERROR`/`MISSING` node's row range; `_add_candidate()` excludes any function-like node whose own
  full span overlaps one. Verified against two genuinely different tree-sitter error-recovery
  shapes, discovered empirically, not assumed: a malformed **signature** (`function broken(: {`)
  can make tree-sitter swallow everything up to EOF into one giant `ERROR` node — a *subsequent*
  function then isn't even a separate top-level node to visit, so it's excluded by construction,
  more conservative than the overlap rule alone requires; a malformed **body expression**
  (`return y +++ * ;`) is a more localized error tree-sitter recovers from cleanly — a function
  before it and a function after it both remain separate, correctly-compressed top-level nodes,
  exactly matching QB-005A Section 7's own stated test expectation. Both behaviors are documented
  in the module's own docstrings/comments, not just discovered and left implicit.
- **New filter, `quor/filters/builtin/cat-javascript.toml`** — routes `.js`/`.jsx`/`.mjs`/`.cjs`,
  reuses the generic `code_ast_summarize` stage (`language = "javascript"`) rather than a
  JS-specific stage class, then the exact same `strip_lines`/`deduplicate_consecutive`/`max_tokens`
  tail `cat-python.toml` already uses, verbatim (per QB-005A Section 9's own instruction) — 4
  inline `[[filter.tests]]`, all passing via `quor verify`.
- **New optional extra, `quor[javascript]`** (`pyproject.toml`), mirroring `quor[documents]`'s
  exact structure: listed in both `javascript` and `dev` (so contributors get real fixture
  coverage without a second install step), plus a `[[tool.mypy.overrides]]` entry for
  `tree_sitter`/`tree_sitter_javascript`.

**A real, severe bug found during implementation — not a hypothetical, and not in Quor's own
code:** `tree-sitter==0.26.0` (the latest release at the time, resolved by the version-range
originally drafted for this task) has a reproducible **native-level memory-corruption bug**.
Calling `Node.child_by_field_name()` and then accessing `.start_point`/`.end_point` on the
returned node, repeated against nodes from the same parsed tree, intermittently segfaults the
entire Python process — a crash no `try/except` can catch, the single worst possible violation of
Quor's fail-open guarantee (ADR-018), discovered by a genuinely large-file unit test
(`test_large_synthetic_file_compresses_every_function_body`, 100 synthetic functions) crashing the
test run outright rather than failing an assertion. Root-caused via systematic bisection, not
guessed: reproduced in a fresh process with a minimal, vanilla-API repro (no quor code involved);
bisected the function count and found a hard, deterministic threshold between 85 (clean) and 87
(crashes); confirmed both `.children`-iteration and `TreeCursor`-based traversal trigger it at a
sufficient node count, ruling out a bug in this module's specific traversal style; confirmed
**absent** in `tree-sitter==0.25.2` at the same and far larger scales (2000+ nodes, no corruption).
Fixed by capping `pyproject.toml`'s `tree-sitter` dependency at `<0.26.0` (both in the `javascript`
extra and the `dev` extra) — mirroring `DECISIONS.md`'s exact-pin rationale for `ruff`/`mypy` (ADR-
027): a new release silently breaking something this load-bearing must be a deliberate, visible
version bump after independent re-verification, never a silent `pip install` side effect. This
finding, and the exact bisection methodology, is documented in both `pyproject.toml`'s own comment
and `javascript.py`'s neighborhood so a future contributor doesn't have to rediscover it from
scratch.

**Proof of zero Python behavioral change:** the same before/after snapshot harness QB-005B
introduced (14 fixtures, `PythonAstSummarizeStage.apply()`, every `LineMask` tuple dumped) was
re-run against the QB-005C codebase and diffed against the original QB-005B baseline —
**byte-for-byte identical**, including the two exception-raising fixtures. The entire pre-existing
`TestPythonAstSummarize` suite (18 tests, unmodified) and QB-005B's own `TestCodeAstSummarize`
Python-equivalence tests still pass unchanged.

**New test coverage added:** `tests/unit/test_ast_summarize.py` gained `TestAnalyzeJavaScript` (22
tests against the *real* parser, not a mock — simple/class/arrow/generator bodies, same-line and
empty bodies left untouched, JSDoc/decorators verified structurally preserved, `export`/`export
default` unwrapping, both ERROR-node-recovery shapes described above, missing-dependency fail-open,
JSX parsing, and the 100-function large-file case that originally surfaced the tree-sitter bug —
now green). `TestRegistry`/`TestRegistryFailOpenContract` updated for `"javascript"` now being
registered (two tests' names/assertions changed to reflect this intentionally, not silently).
`tests/unit/test_stages.py` gained `TestCodeAstSummarizeJavaScript` (function/class/extends
preservation, ERROR-node exclusion at the stage/`ContentMask` level, `preserve_patterns`
interaction, byte-identical-kept-lines regression, and a fail-open-propagation test using a
patched-in fake analyzer, mirroring `TestRegistryFailOpenContract`'s own pattern).

**What was deliberately not touched / explicitly out of scope, per this task's own instructions:**
no TypeScript grammar or `.ts`/`.tsx` routing (QB-005D), no Read-hook integration
(`quor/adapters/claude_read.py` untouched — QB-005F), no benchmark manifest/baseline entries for
`cat-javascript` (QB-005E — flagged explicitly in `docs/final/COMMAND_SUPPORT.md` §7 as a
temporary, documented exception to ADR-032 rather than silently skipped), no CommonJS
(`module.exports = ...`) recognition, no recognition of a function declared inside a conditional
block (`if`/`try` — a Python-specific accommodation in `python.py` deliberately not carried over,
since it isn't documented for JavaScript in the design's Section 3 table), no recognition of a
class *expression* (`const X = class {...}`, only the `class X {...}` declaration form).

**Known, inherited (not new) limitation:** `cat-javascript.toml` reuses `strip_lines`'s
`'^\s*#[^!]'` comment pattern verbatim, per the design's explicit instruction to reuse
`cat-python.toml`'s stage stack as-is — this pattern can misfire on a JS/TS private class field
declaration (`#counter = 0;`), stripping it as a perceived comment. This exact pattern already
ships unchanged in `cat.toml`/`cat-python.toml` today for any `.js` file that doesn't match a
dedicated filter, so this is a pre-existing risk being reused for consistency, not a new one — but
worth tracking for a future, explicitly-scoped fix rather than silently accepting forever.

**Validation:** `ruff check quor/ tests/` clean (one `SIM117` nested-`with` issue fixed during
implementation). `mypy quor/` — Success, no issues, 65 source files. Full `pytest` (unit +
integration + benchmark suite, batched per QB-005B's own environment note — the local Quor-hook-
intercepting-Quor's-own-shell-commands artifact is unchanged from QB-005B) — 0 failures across
every file, including the large-file test that originally crashed the process before the
tree-sitter version fix. `quor verify` — 81/81 filter tests pass (77 from QB-005B's run + 4 new
`cat-javascript` tests).

**Status:** Implemented and merged to `main` — shipped in Quor **v0.4.0** (2026-07-11). *(Correction: this entry originally read "not committed" — verified against `CHANGELOG.md` and `git log` while restructuring this document; the branch was merged via the `integration/stabilize-ast-and-early-exit` PR and the code is present on `main` today.)*

</details>

<details>
<summary>QB-005D technical details — TypeScript AST analyzer</summary>

**Problem:** QB-005C shipped JavaScript; QB-005A's design called for TypeScript next, using
`tree-sitter-typescript`'s two grammars (`language_typescript()`/`language_tsx()`), reusing the
same ERROR-node exclusion rule and `code_ast_summarize` stage, and correctly preserving
TypeScript-only declarations (interface/type alias/enum, plus this task's extended scope:
namespace, decorators, abstract classes/methods, overload signatures).

**Mandatory pre-flight gate, run before writing any analyzer code:** re-verified
`tree-sitter-typescript` compatibility with the `tree-sitter<0.26.0` ceiling QB-005C's own
bisection established. Installed `tree-sitter-typescript==0.23.2` alongside the pinned
`tree-sitter==0.25.2`, then re-ran the exact QB-005C bisection pattern
(`Node.child_by_field_name()` + point-attribute access, repeated) specifically against both TS
grammars: 2000 flat top-level functions (`language_typescript()`) and 3000 nested class+method
pairs — 6000+ field-access calls — (`language_tsx()`), plus 200 repeated separate Language/Parser
construction calls alternating grammars (simulating realistic repeated per-file stage
invocations). **All clean, zero corruption, at every scale tested** — confirming the bug is
specific to `tree-sitter==0.26.0` itself (not a JS-grammar-specific quirk `tree-sitter-typescript`
might have inherited or reintroduced), and that the existing `<0.26.0` ceiling remains sufficient
with **no dependency-version change needed**. Documented in `pyproject.toml`'s own comment rather
than silently assumed safe.

**What shipped:**
- **New shared module, `quor/pipeline/ast_summarize/_treesitter_utils.py`** — the
  ERROR-node-overlap/body-interior-line logic (`statement_block_interior_lines`,
  `collect_error_ranges`, `has_error_overlap`, `add_candidate`) relocated, unmodified, out of
  `javascript.py` — mirrors `quor/pipeline/stages/_utils.py`'s own established
  shared-helpers-module convention, one package level down (shared across *language analyzers*
  instead of across *stages*). This is genuine code reuse, not reimplementation — directly
  satisfies this task's own instruction ("reuse the same ERROR-node exclusion rule implemented
  for JavaScript"). `javascript.py` now imports from this module instead of defining its own
  copies; `analyze_javascript()`'s observable behavior was re-verified byte-for-byte unchanged via
  the same before/after snapshot-diff technique QB-005B/C both used (14 fixtures, all outcomes
  identical including the two exception/warning cases).
- **New analyzer, `quor/pipeline/ast_summarize/typescript.py`** — two public functions,
  `analyze_typescript()` (`.ts`, `language_typescript()`) and `analyze_tsx()` (`.tsx`,
  `language_tsx()`), sharing one internal traversal (`_analyze_with_grammar()`,
  `_visit_top_level()`, `_visit_class_body()`, `_visit_variable_declaration()`). Registered as
  **two separate registry entries**, `"typescript"` and `"tsx"` — not one, and not inferred from
  content: empirically confirmed during implementation that JSX syntax genuinely fails to parse
  under the plain `language_typescript()` grammar (`has_error: True`), and that an angle-bracket
  type assertion (`<number>x`) — genuinely ambiguous with a JSX element — parses cleanly under it
  specifically because it doesn't have to disambiguate against JSX. Exactly the risk QB-005A
  Section 8 predicted, now verified rather than assumed.
- **TypeScript-specific node mapping** (empirically verified against the installed grammar, not
  guessed):
  - `interface_declaration`/`type_alias_declaration`/`enum_declaration` — preserved whole by
    **deliberate omission** from the dispatch table, the same "preserve by not touching it at all"
    mechanism JS's `arrow_function`-vs-expression distinction already relies on. Verified
    structurally that `interface_body` never contains a `statement_block` node, so there is zero
    risk of accidental misidentification even if a future change widened the dispatch table
    carelessly.
  - `namespace X { ... }` — a genuine grammar quirk discovered empirically, not documented
    anywhere in tree-sitter-typescript's own public docs at implementation time: it parses as an
    `expression_statement` wrapping an `internal_module` node, not a dedicated top-level
    declaration type. **Deliberately not recursed into** — this task's own instruction groups
    "namespace" with interface/type/enum as a "preserve" category, and QB-005A's Section 3 table
    documents no recursion rule for it; recursing in anyway would be exactly the kind of
    undocumented, language-specific heuristic this task explicitly warns against. A function
    declared inside a namespace is therefore preserved in full, not compressed — verified directly
    by a dedicated test, not just asserted in a comment.
  - `abstract_class_declaration` — a genuinely distinct top-level node type from `class_declaration`
    (not a modifier flag on it), added to a new `_CLASS_LIKE_TYPES` set alongside it; both expose
    the identical `body` field shape.
  - `function_signature` (overload signatures) and `abstract_method_signature` (abstract methods) —
    both explicitly added to `_FUNCTION_LIKE_TYPES` even though both are always body-less no-ops
    (no `body` field at all) purely for self-documentation ("we considered overloads/abstract
    methods; here's why they're inert"), rather than relying on silent omission the way
    interface/type/enum do.
  - Decorators, `extends`/`implements` clauses, generic type parameters (`<T>`) — all need zero
    special handling, verified the same way JS's decorators did: they live outside whatever
    node's `body` field this module ever touches.
- **New filter, `quor/filters/builtin/cat-typescript.toml`** — **two** `[[filter]]` blocks in one
  file (`cat-typescript` for `.ts`, `cat-tsx` for `.tsx`), not two files — mirrors `node.toml`'s
  own established multi-block-per-file precedent rather than inventing a new one, and avoids
  "duplicating the JavaScript filter unnecessarily" (this task's own instruction) by reusing the
  same `code_ast_summarize` stage and the identical `strip_lines`/`deduplicate_consecutive`/
  `max_tokens` tail `cat-javascript.toml`/`cat-python.toml` already use, verbatim. 7 inline
  `[[filter.tests]]` total (4 for `cat-typescript` — including one specifically asserting
  interface/type/enum survive whole and one for overload/abstract-method preservation — 3 for
  `cat-tsx`), all passing via `quor verify`.
- **New optional-extra decision, made explicitly rather than left open:** `tree-sitter-typescript`
  was added to the **same** `quor[javascript]` extra QB-005C introduced, not a new
  `quor[typescript]` extra — QB-005A Section 9 had explicitly left this as an open question. Chosen
  because the wheel is small (~280 KB), a user wanting either language very likely wants both, and
  a second extra would only add install-matrix permutations for a dependency-weight concern that
  doesn't actually apply at this size. Documented as a deliberate choice (with the reasoning) in
  both `typescript.py`'s own module docstring and `pyproject.toml`'s comment, not silently decided.

**ERROR-node handling — verified against TypeScript specifically, not assumed transferable from
JS:** both recovery shapes QB-005C found for JavaScript were re-confirmed for TypeScript with typed
signatures: a malformed **body** expression (`return y +++ * ;`) is localized — sibling functions
before and after it both still compress correctly; the plain-`.ts`-grammar-on-JSX-content case
(routing mismatch simulation) also confirmed the same safety net holds — the JSX-containing
function's body is excluded via ERROR overlap while an unrelated generic function elsewhere in the
same file still compresses normally.

**Proof of zero Python/JavaScript behavioral change:** the same before/after snapshot harnesses
QB-005B (Python, 14 fixtures) and QB-005C (JavaScript, 19 fixtures) introduced were both re-run
against the final QB-005D codebase and diffed against their respective pre-QB-005D baselines —
**both byte-for-byte identical**, including every exception/warning-raising fixture. The entire
pre-existing `TestPythonAstSummarize` (18 tests) and `TestAnalyzeJavaScript`/
`TestCodeAstSummarizeJavaScript` suites (unmodified) still pass unchanged.

**New test coverage added:** `tests/unit/test_ast_summarize.py` gained `TestAnalyzeTypeScript` (18
tests against the real parser — interface/type/enum/namespace preservation, overload signatures,
abstract classes, decorators, `implements`, generics, JSDoc, export unwrapping, both ERROR-node
recovery shapes, the exact 100-function scale that originally surfaced the tree-sitter==0.26.0 bug
for JS re-run here against the TS grammar specifically, and missing-dependency fail-open) and
`TestAnalyzeTsx` (5 tests — JSX function/arrow-component bodies, generic-vs-JSX disambiguation in
one file, confirmation that the plain `.ts` grammar genuinely fails on JSX content, missing-
dependency fail-open). `TestRegistry` updated for `"typescript"`/`"tsx"` now being registered (two
tests renamed to reflect this intentionally, matching QB-005C's own precedent for evolving these
scope-boundary tests visibly rather than silently). `tests/unit/test_stages.py` gained
`TestCodeAstSummarizeTypeScript` (6 tests) and `TestCodeAstSummarizeTsx` (2 tests, including one
proving `"typescript"` and `"tsx"` configs genuinely reach different analyzer functions by
observing a real behavioral difference, not just asserting they're unequal in the abstract).

**What was deliberately not touched / explicitly out of scope, per this task's own instructions:**
no Read-hook integration (`quor/adapters/claude_read.py` untouched — QB-005F), no benchmark
manifest/baseline entries for `cat-typescript`/`cat-tsx` (QB-005E — flagged explicitly in
`docs/final/COMMAND_SUPPORT.md` §7 as a temporary, documented exception to ADR-032, alongside
`cat-javascript`'s identical existing gap), no recursion into namespace bodies, no CommonJS
recognition, no class-expression recognition (both inherited limitations already documented for
JS, unchanged for TS), no dependency-version change (the pre-flight gate found no compatibility
problem, so none was needed).

**Validation:** `ruff check quor/ tests/` clean. `mypy quor/` — Success, no issues, 67 source
files. Full `pytest` (unit + integration + benchmark suite, batched per the same local
Quor-hook-intercepting-Quor's-own-shell-commands environment artifact QB-005B/C already
documented, unchanged in this phase) — 0 failures across every file. `quor verify` — 88/88 filter
tests pass (81 from QB-005C's run + 4 new `cat-typescript` + 3 new `cat-tsx` tests).

**Status:** Implemented and merged to `main` — shipped in Quor **v0.4.0** (2026-07-11). *(Correction: this entry originally read "not committed" — verified against `CHANGELOG.md` and `git log` while restructuring this document; the branch was merged via the `integration/stabilize-ast-and-early-exit` PR and the code is present on `main` today.)*

</details>

<details>
<summary>QB-005E technical details — AST benchmark suite and empirical evaluation</summary>

**Problem:** QB-005B–QB-005D shipped Python/JavaScript/TypeScript/TSX AST summarization with
correctness proven by unit and inline filter tests, but explicitly deferred *measurement* —
`min_reduction_pct`/`must_contain` alone don't answer "how much does this actually compress
realistic code, and how fast is it?" QB-005A Section 9's own phased plan named this QB-005E's job.

**What shipped:**
- **12 new, realistic (not synthetic-repeated) sample fixtures** under
  `tests/benchmarks/samples/cat-javascript/`, `cat-typescript/`, `cat-tsx/`: 5 JavaScript (a short
  retry-fetch utility, a medium shopping-cart service, a large notification-dispatch service, a
  hand-minified vendor bundle, a heavily-JSDoc/comment-annotated payment-gateway client), 6
  TypeScript (a short currency-formatting utility, a large orders service, an interface-heavy
  domain-model file, a decorator-heavy NestJS controller with class-validator DTOs, a
  generic-heavy repository/result-type utility module, an overload-heavy parsing-helpers module),
  and 1 TSX (a React shopping-cart component with typed props and hooks). All verified to parse
  with zero `ERROR` nodes before being wired into the manifest — not assumed. Continues the same
  storefront/payments/notifications fictional-company narrative the existing `cat-python`/
  `git-diff`/`markdown` samples already use, for a cohesive corpus.
- **12 new `[[case]]` entries in `tests/benchmarks/manifest.toml`** (`cat-javascript` x5,
  `cat-typescript` x6, `cat-tsx` x1), `min_reduction_pct` floors set from real measured values
  (a run with generous placeholder floors first, then tightened a comfortable margin below what
  was actually measured — e.g. `cat-javascript-notification-dispatch-large` measured 75.0%,
  floor set to 50.0 — per the README's own "Adding a new benchmark case" workflow, not guessed).
  **A real must_contain bug found and fixed during this pass, not a hypothetical:** the TSX
  case's first draft asserted a multi-line JSX `return (...)` snippet must survive — but that
  content is inside the component function's own body, which the AST stage correctly compresses
  away; the assertion would have failed the moment the case actually ran. Caught by tracing what
  survives compression before trusting the assertion, not after a red test forced the issue —
  fixed to check the preserved signature/interface/JSDoc text instead. A second, more subtle
  version of the same mistake was caught for the heavily-commented JS case: a `must_contain`
  check against a *plain* `//`-comment (which `strip_lines` genuinely removes, unlike a `/** */`
  JSDoc block, which it never touches) was replaced with a check against the JSDoc block that
  actually survives.
- **`tests/benchmarks/baseline.json` updated** via the framework's own `--update-baseline`
  workflow — diffed old vs. new baseline programmatically (not eyeballed): exactly 12 entries
  added, 0 removed, 0 changed among all 48 pre-existing entries (ignoring `execution_time_ms`,
  which is expected to vary run-to-run and is never part of any gate — see
  `benchmark_runner.py`'s own module docstring). `cat-python-payment-processor` (45.61%) and
  `cat-python-webhook-handlers` (43.35%) — the two cases most directly comparable to the new
  JS/TS cases — confirmed byte-for-byte identical to their pre-QB-005E values.
- **New, deliberately separate script, `tests/benchmarks/ast_timing_analysis.py`** — not wired
  into `test_benchmarks.py`'s pytest gate, not part of the regression-tracked manifest, run
  directly (`python -m tests.benchmarks.ast_timing_analysis`). Answers a genuinely different
  question than the corpus benchmarks: operational characterization (parser-vs-pipeline time
  contribution, scaling, malformed-source/ERROR-node performance, "nothing to summarize" cost)
  rather than regression-tracked compression correctness. Isolated from production code the same
  way `benchmark_runner.py` already is — only calls existing public `quor` APIs
  (`FilterRegistry`, `ContentMask`, `StageConfig` subclasses, `CodeAstSummarizeStage`/
  `PythonAstSummarizeStage`, the `analyze_*()` functions directly); nothing in `quor/` was
  touched. Uses synthetic inputs *specifically and only* for the scaling/malformed measurements
  (a legitimate, standard practice for characterizing scaling behavior, and explicitly not part
  of the corpus this task's own "no synthetic repeated code" instruction governs) — deliberately
  kept out of `manifest.toml` for exactly that reason.

**Measured results (see the script's own output for full detail):**
- **Parser-vs-pipeline contribution:** across the 12 new JS/TS/TSX cases, AST-stage time (parsing
  + this stage's own mask-walking bookkeeping) averages ~35-37% of the full filter-pipeline time;
  the raw parser call itself is typically ~85-105% of the AST stage's own time (the mask-walking
  loop is cheap relative to tree-sitter's C-level parse). The occasional >100% reading is
  measurement noise at sub-millisecond granularity from timing two *separate* runs independently
  (parser-alone vs. full-stage) rather than decomposing one run — reported honestly as a
  measurement-methodology caveat, not smoothed over.
- **Runtime:** tree-sitter (JS/TS/TSX) mean full-pipeline time ~1-3ms per file across the corpus
  (varies by run/machine load, consistent with this suite's own "timing is inherently noisy"
  philosophy); worst case in the corpus (`cat-javascript-notification-dispatch-large`, 210 lines)
  ~2-7ms. stdlib `ast` (Python) mean ~0.4-0.6ms on its own (smaller) samples — a real difference,
  though not a strictly fair apples-to-apples comparison given the Python corpus samples are
  shorter than the JS/TS ones.
- **Large-file scaling (synthetic):** 10 to 1000 flat functions scales roughly linearly for both
  JavaScript (0.16ms to ~13-17ms) and TypeScript (0.15ms to ~17-20ms) — no evidence of quadratic
  blowup at any tested size. 300 methods across 30 nested classes (1291 lines) measured ~5-6ms,
  consistent with the flat-function scaling curve at a comparable total node count.
- **Malformed source / ERROR-node handling:** all four malformed fixtures (JS localized-body-error,
  JS signature-error-swallows-tail, TS localized-body-error, Python whole-file SyntaxError) measured
  well under 0.3ms — the ERROR-node-overlap exclusion rule (QB-005C/D) adds no measurable overhead
  even though it walks the tree an extra time when `has_error` is true.
- **No-summarization-possible case:** a 7-line, functions-free TypeScript file (interface + const
  only) measured ~0.04ms and correctly returned zero compressible lines — the cheapest possible
  outcome, confirming there's no meaningful fixed cost paid on a file the AST stage can't help.

**Regression checks — all explicitly proven, not assumed:**
- Python benchmark numbers unchanged: `cat-python-payment-processor`/`cat-python-webhook-handlers`
  compression percentages identical to pre-QB-005E baseline (see above).
- Existing benchmark cases unchanged unless intentionally expanded: programmatic baseline diff
  confirmed 0 changes among all 48 pre-existing cases.
- Benchmark framework compatibility: `benchmark_runner.py`, `report.py`, `run_benchmarks.py`, and
  `test_benchmarks.py` were **not modified at all** — the framework's own "adding a filter is a
  pure data change" design (stated in its own README) held exactly as advertised; 12 new cases
  required zero code changes to any of those four files.

**What was deliberately not touched, per this task's own instructions:** no changes to
`python.py`/`javascript.py`/`typescript.py`/`_treesitter_utils.py` (the analyzers), no Read-hook
changes, no `FilterRegistry`/`ContentMask`/`StageHandler` changes, no changes to
`benchmark_runner.py`/`report.py`/`run_benchmarks.py`/`test_benchmarks.py` — the framework was
used exactly as designed, not modified. No architectural issue was discovered in the benchmark
framework itself; it needed no changes to support this phase's requirements.

**Validation:** `ruff check quor/ tests/` clean (fixed 3 `B023` loop-variable-binding lambda
issues and one import-order issue in the new timing script during implementation — not silenced,
fixed properly with default-argument binding). `mypy quor/` — Success, no issues, 67 source files
(`quor/` untouched by this phase, as required). Full `pytest` (unit + integration + benchmark
suite, batched per the same local Quor-hook-intercepting-Quor's-own-shell-commands environment
artifact QB-005B–D already documented) — 0 failures across every file. `quor verify` — 88/88
(unchanged from QB-005D — no filter's own inline `[[filter.tests]]` changed, only benchmark
corpus samples, a different mechanism). Full benchmark suite — 60/60 cases correct, 0 floor
violations, 0 regressions.

**Status:** Implemented and merged to `main` — shipped in Quor **v0.4.0** (2026-07-11). *(Correction: this entry originally read "not committed" — verified against `CHANGELOG.md` and `git log` while restructuring this document; the branch was merged via the `integration/stabilize-ast-and-early-exit` PR and the code is present on `main` today.)*

</details>

<details>
<summary>QB-005F technical details — Read-hook AST integration</summary>

**Problem:** QB-005B–E built and benchmarked a complete Python/JavaScript/TypeScript/TSX AST
summarization pipeline, but none of it was reachable from a real Claude Code Read call —
`quor/adapters/claude_read.py`'s routing only ever matched `_READ_SUPPORTED_FILTER_NAMES`
(`markdown`/`document-text`) via `FilterRegistry.find(file_path)`, and `cat-python.toml`/
`cat-javascript.toml`/`cat-typescript.toml`'s `match_command` patterns (`^cat\s+...\.py\b`, etc.)
can never match a bare Read `file_path` string — they require a literal `cat `-prefixed command.
This is the exact, pre-existing gap QB-005A's design doc flagged in its own Section 8/9 and named
QB-005F to close.

**Genuine duplication found and explained before refactoring, per this task's explicit
instruction:** `_compress_extracted_document()` (QB-007E4's DOCX/PDF path) already solved the
identical "a `match_command` pattern can never match this input, so look the filter up **by
name** via `FilterRegistry.all_filters()` instead" problem for extracted document text. Its
post-extraction tail — by-name lookup → `registry.apply()` → `track_invocation()` → return `None`
if unchanged — was byte-for-byte the same sequence the new source-code path needed; the only real
difference is *how* the content to filter is obtained (`extract()` for DOCX/PDF vs. already-plain
`tool_response` for source code). Rather than a third copy-pasted implementation, or a new generic
routing/dispatch abstraction (explicitly ruled out by this task's instructions), that shared tail
was extracted into one new helper, `_compress_via_named_filter(*, content, original, filter_name,
tracking, t0, command)`, called by both paths — `content`/`original` are separate parameters
because they differ for DOCX/PDF (`content` = extracted text, `original` = raw `tool_response`,
so tracking's token-savings numbers stay anchored to what Claude actually received) but are the
same value for source code (no extraction step exists).

**What shipped, all in `quor/adapters/claude_read.py` (only file changed):**
- `_SOURCE_CODE_FILTER_NAMES_BY_EXTENSION: dict[str, str]` — `.py` → `"cat-python"`,
  `.js`/`.jsx`/`.mjs`/`.cjs` → `"cat-javascript"`, `.ts` → `"cat-typescript"`, `.tsx` →
  `"cat-tsx"`. Extension-based routing, mirroring `_EXTRACTION_EXTENSIONS`'s own shape.
- A new dispatch branch in `_compress_read_output()`, checked immediately after the existing
  `.docx`/`.pdf` extraction check: a matched extension is routed straight to
  `_compress_via_named_filter()` with `content=original=tool_response` (no extraction step).
- `_compress_extracted_document()` refactored to call the same new helper for its own tail,
  instead of duplicating it — its own observable behavior (return value, tracking fields,
  fail-open contract) is unchanged; only where the code physically lives changed, verified by the
  full pre-existing `TestReadTracking`/`TestDocxPdfExtraction` suites still passing unmodified.
- `_find_filter_by_name()` (QB-007E4) reused as-is — no changes.
- No changes anywhere in `quor/pipeline/`, `quor/filters/`, or any `.toml` filter — this phase is
  routing-only, exactly as scoped. `code_ast_summarize`/`python_ast_summarize`, the `Pipeline`
  engine, `FilterRegistry`, and all four `cat-*` filters are byte-for-byte unchanged.

**Conceptual flow, exactly as required:**
`Read(source file)` → `claude_read.py`'s extension-based routing (`_SOURCE_CODE_FILTER_NAMES_BY_EXTENSION`)
→ `FilterRegistry` (by name, via `_find_filter_by_name`) → the existing `code_ast_summarize`/
`python_ast_summarize` stage inside that filter's existing `Pipeline` → `updatedToolOutput`. No
parallel implementation, no new stage type, no second AST pipeline.

**Tracking (QB-007D), verified, not assumed:** `_compress_via_named_filter()` calls
`track_invocation()` exactly once per Read, with `command="Read: {file_path}"`,
`filter_name="cat-python"`/`"cat-javascript"`/`"cat-typescript"`/`"cat-tsx"`,
`was_passthrough=False` — no new tracking schema, no Read-specific storage; the same
`query_gain()` aggregation Bash/document rows already use sums Python/JS/TS/TSX Read rows into the
same project totals with zero Read-format-specific code (`TestReadSourceCodeTracking.
test_multiple_source_code_reads_aggregate_with_markdown_reads` proves this directly).

**Fail-open paths verified end to end through the real Read stdin → stdout contract, not just at
the analyzer/stage layer QB-005B–D already covered:**
- Unsupported extension (`.json`/`.rs`/`.toml`/`.css`) → never reaches the new mapping, passes
  through exactly as before QB-005F.
- Invalid Python syntax → `analyze_python()`'s `SyntaxError` propagates to `Pipeline.execute()`'s
  existing per-stage fail-open (ADR-018); the AST stage is skipped, the rest of the filter still
  runs, the hook still returns a well-formed response.
- Malformed JavaScript/TypeScript → tree-sitter's error-recovering parser produces `ERROR`/
  `MISSING` nodes rather than raising; the existing ERROR-node-overlap exclusion rule (QB-005C/D)
  handles it by construction — no exception at any layer.
- Missing `quor[javascript]` dependency (`tree_sitter`/`tree_sitter_javascript` import blocked) →
  `analyze_javascript()`'s existing `ImportError` → warn → empty-set fail-open fires exactly as it
  already does when called via `cat javascript-file.js`; the Read hook returns a well-formed
  response either way.
- `FilterRegistry` construction/`apply()` raising → caught by `_compress_via_named_filter()`'s own
  try/except (same discipline as every other call in this file) — `updatedToolOutput` omitted, no
  exception escapes.
- Non-string `tool_response` → unchanged, still short-circuits before any routing.

**A real, pre-existing stale-test regression found and fixed, not introduced:** several tests
across `tests/unit/test_tracking.py` and `tests/unit/test_read_hook_activation.py` used a `.py`
file path as their canonical "unsupported extension" fixture (predating QB-005F, when `.py` truly
was unsupported for Read). Each was updated to a still-genuinely-unsupported extension
(`.json`/`.rs`), with a comment explaining why, and dedicated QB-005F coverage was added instead
(`tests/unit/test_read_hook_ast_summarization.py`, `TestReadSourceCodeTracking`). One of these
(`test_unsupported_extension_still_passes_through`) used a garbage-filler `.py` fixture large
enough that, once genuinely routed through `cat-python`, its single very-long line collapsed to an
empty string via `max_tokens`' pre-existing (not new) lack of an `on_empty` fallback for source
filters — see "Remaining limitations" below.

**New tests added:** `tests/unit/test_read_hook_ast_summarization.py` (18 tests: real Python/JS/
TS/TSX before/after compression with signature-preserved/body-removed assertions,
`.jsx`/`.mjs`/`.cjs` extension-variant routing, unsupported-extension passthrough, malformed-source
fail-open for all three parser families, missing-dependency fail-open, `FilterRegistry`
construction/`apply()` failure fail-open, non-string `tool_response` handling) plus
`TestReadSourceCodeTracking` in `tests/unit/test_tracking.py` (7 tests: per-language
`filter_name`/`was_passthrough`/token-count tracking, cross-format aggregation).

**Validation:** `ruff check quor/ tests/` clean. `mypy quor/` — Success, no issues, 67 source
files. Full `pytest` (unit + integration, batched across 6 groups per the same local
Quor-hook-intercepting-Quor's-own-shell-commands 25s timeout artifact QB-005B–E already
documented) — 0 failures. `quor verify` — 88/88 (unchanged; no filter's own `[[filter.tests]]`
changed). Full benchmark suite — 60/60 cases correct, 0 floor violations, 0 regressions against
`baseline.json` (unchanged from QB-005E — this phase added no new filter, so no new benchmark
cases were required; Read-hook routing itself is covered by the new unit tests above, not the
benchmark corpus, since `benchmark_runner.py` matches via `FilterRegistry.find(command)` against
command strings, not Read file paths).

**Remaining limitations, explicitly not addressed here (out of this phase's scope):**
- `cat-python.toml`/`cat-javascript.toml`/`cat-typescript.toml` have no `on_empty` fallback
  configured for their `max_tokens` stage — a source file that is (or degenerates to, e.g. a
  minified single-line bundle) one line far exceeding the 800-token budget can compress to an
  empty string. This is pre-existing `cat-*.toml` behavior, identical for the Bash `cat` path
  today; QB-005F does not change it, and changing filter `on_empty` behavior is a filter-content
  decision, not a routing one — out of scope per this phase's "no analyzer/filter behavior changes
  unless a genuine correctness issue is found" constraint. Worth a small, separate follow-up.
- Java (QB-035's original scope) remains unstarted — QB-005A–F only ever covered Python/
  JavaScript/TypeScript per the design doc's explicit Phase 1 scope.

**Status:** Implemented and merged to `main` — shipped in Quor **v0.4.0** (2026-07-11). *(Correction: this entry originally read "not committed" — verified against `CHANGELOG.md` and `git log` while restructuring this document; the branch was merged via the `integration/stabilize-ast-and-early-exit` PR and the code is present on `main` today.)*
This closes out the entire QB-005 phased plan (QB-005A→F).

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

#### QB-006C — Rounding out Node.js/TypeScript toolchain coverage (tsc, jest, vitest, prettier, next, turbo)

**Effort:** Large · **Value:** High · **Category:** Feature

QB-006A/QB-006B gave Quor generic npm/npx/pnpm/yarn noise-stripping plus ESLint-aware routing, but
the rest of the everyday JS/TS toolchain — the TypeScript compiler, the two dominant test runners,
the formatter, and the two most common monorepo/framework CLIs — still passed through untouched.
This closes that gap: each of `tsc`, `jest`, `vitest`, `prettier`, `next`, and `turbo` now gets
either its own dedicated filter or, where the tool's output is genuinely identical to one Quor
already understands, a reuse of the existing filter.

<details>
<summary>Technical details</summary>

**Problem:** `tsc`, `jest`, `vitest`, `prettier`, `next`, and `turbo` were all absent from
`_KNOWN_BASE_COMMANDS` — invoked bare or through a wrapper, none of them were ever rewritten or
filtered, regardless of the npm/npx/pnpm/yarn wrapper-routing QB-006B already added for `eslint`.
`docs/final/COMMAND_SUPPORT.md` explicitly flagged `tsc` (and, implicitly, the others) as
unsupported.

**Desired outcome:** Extend `_KNOWN_BASE_COMMANDS` and `quor/filters/builtin/node.toml` to cover
the highest-value remaining Node ecosystem tools, reusing existing filters/stages wherever the
output shape genuinely matches, and adding a new dedicated filter only where it doesn't — without
any schema, tracking, or fail-open behavior changes.

**Resolution:**
- **Reused, no new filter:** `next lint` runs ESLint under the hood and produces byte-identical
  stylish-formatter output, so it's routed to the existing `eslint` filter block (added to that
  block's own `match_command`, not a new block) — a pure reuse, zero new stage config.
- **New dedicated filters (all in `node.toml`, all reachable bare *and* through
  `npx`/`npm exec`/`pnpm exec`/`pnpm dlx`/`yarn exec`/bare `yarn <tool>`):**
  - `tsc` — strips the `Found N errors...` summary and blank lines; capped at 400 tokens.
    Deliberately **no `group_repeated` stage**: tried first with mypy's shape-based design, but
    benchmark testing caught a real correctness bug — shape-based grouping on the generic
    `error TS\d+:` pattern merges *unrelated* diagnostics that merely share that shape, unlike
    mypy's narrower "same message, different line" case. Dropped it, matching ruff's existing "no
    repetition collapsing" precedent for heterogeneous diagnostics.
  - `jest` / `vitest` — two separate filters, not one shared: real output characteristics
    genuinely differ (ASCII `PASS`/`FAIL` + `Test Suites:`/`Tests:` summary vs. unicode
    `✓`/`×`/`❯`/`→` + `Test Files`/`Tests` summary). Both strip passing-test lines and
    `node_modules`-internal stack frames (mirroring pytest's `site-packages`/`dist-packages`
    treatment), never touch failure detail, and short-circuit on an all-passing run.
  - `prettier` — low-noise by nature; strips only the "Checking formatting..." banner, preserves
    every `[warn]` file line, the summary, and any error text.
  - `next` — strips build/dev step-progress banners ("Creating an optimized production build...",
    etc.), never touches the route-size table, compile success/failure, or type errors. Unlike
    `npm`/`turbo`, it *does* get a `max_tokens` safety net — it's Next's own fixed pipeline
    (bounded shape like `tsc`/`eslint`), not a wrapper around an arbitrary user script.
  - `turbo` — strips only its own `•` preamble bullets; a wrapped task's own output
    (`pkg:task: ...` prefixed) is never pattern-matched. Deliberately **no `max_tokens`** (same
    "wraps arbitrary underlying scripts" reasoning as `npm`) and, after the same benchmark-driven
    discovery as `tsc`, **no `group_repeated`** either — shape-based grouping on
    `cache (miss|hit)` would merge a hit and a miss from two *different* packages, hiding which
    package actually missed cache.
- **Word-boundary hardening:** every new bare-command pattern uses `(?=\s|$)` instead of `\b`
  (e.g. `^tsc(?=\s|$)`, not `^tsc\b`) — a plain `\b` would incorrectly match real, unrelated
  binaries like `tsc-watch` or `jest-environment-jsdom`, since `\b` fires on the word/non-word
  boundary between `c`/`t` and a following `-`. Added regression tests for this specifically.
- **Classifier:** `tsc`, `jest`, `vitest`, `prettier`, `next`, `turbo` added to
  `_KNOWN_BASE_COMMANDS` in `quor/rewrite/rules.py`.
- **Benchmarks:** 12 new manifest cases (2 each) across `tsc`/`jest`/`vitest`/`next`/`turbo`, plus
  a new `prettier` case; the pre-existing `npx-prettier-check-failure` case was reclassified from
  the generic `npx` category to `prettier` now that prettier has its own filter, with an updated
  (lower, but still correct) baseline — the compression drop for that specific sample is expected
  and documented: the prettier filter doesn't strip a wrapping `npx`'s own auto-install preamble,
  the same out-of-scope wrapper-layer gap `eslint`'s filter already has.
- **Docs:** `docs/final/COMMAND_SUPPORT.md` updated (known-command list, filter table, ordering
  rules, benchmark coverage count, removed `tsc` from the "not currently supported" list).

**Status:** Resolved. `pytest tests/` (all green except one pre-existing, unrelated failure —
`test_version_matches_pyproject`, a stale local `importlib.metadata` install artifact predating
this work), `quor verify` (67/67 inline filter tests), `ruff check quor/ tests/`, `mypy quor/`, and
the compression benchmark suite (40 cases, 0 unexplained regressions) all pass.

</details>

---

#### QB-007 — Smarter reading of documents (PDFs, Word docs, Markdown)

**Effort:** Large · **Value:** High · **Category:** Feature

Right now Quor only shrinks *shell/terminal command* output — it doesn't touch files Claude reads
directly, like a PDF, a Word document, or a long Markdown file. We've confirmed it's technically
possible to hook into that reading step and reduce those documents down to their important
structure (headings, tables, requirements, decisions) instead of sending the whole thing. This is a
genuinely separate, multi-part project (a new integration point, plus new handling for each
document type), so it's being built as a sequence of small, independently mergeable pieces:
Markdown and plain-text compression is implemented and, as of QB-007C, actually wired into the live
Read hook — a supported document read by Claude is compressed for real, not just at the filter
layer — and, as of QB-007D, that savings shows up in `quor gain` alongside Bash savings. As of
QB-007E1, the extension-routed preprocessing framework DOCX/PDF extraction will plug into also
exists, and as of QB-007E2/E3, both `.docx` and `.pdf` are genuinely converted to Markdown-shaped
text. As of QB-007E4, both are wired into the live Read hook — a `.docx`/`.pdf` Read is now
extracted and compressed for real, through the same `markdown` filter the `.md` path already uses.
See "Sub-items" below for exactly what's done and what isn't.

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

**Design pass (2026-07-10):** Full architecture and design completed per CLAUDE.md Rule 4 (hook
lifecycle, content routing, filter reuse, per-element compression strategy, dependency evaluation,
failure modes, testing strategy). Recorded as ADR-034 in DECISIONS.md. Split into independently
mergeable sub-items so each can be reviewed/tested/merged on its own:

- **QB-007A — PostToolUse/Read hook plumbing.** Implemented (2026-07-10). Originally a no-op
  (always omitted `updatedToolOutput`); QB-007C (below) is what actually wires compression into it.
  See "QB-007A technical details" below.
- **QB-007B — Markdown/plain-text compression.** Implemented (2026-07-10). Filter-layer only when
  first shipped — `markdown.toml`/`document-text.toml` were fully tested via `FilterRegistry` but
  not yet reachable from a real Read call. See "QB-007B technical details" below.
- **QB-007C — Activate the Read hook.** Implemented (2026-07-10). Wires QB-007A's adapter to
  QB-007B's filters via the existing `FilterRegistry`/`Pipeline` — a supported Read now actually
  returns compressed content via `updatedToolOutput`. See "QB-007C technical details" below.
- **QB-007D — Read tracking integration.** Implemented (2026-07-10). Read invocations now
  participate in the existing tracking pipeline (SQLite, JSONL, `quor gain`) exactly like Bash
  invocations, via a single shared recorder — no schema change, no Read-specific storage. See
  "QB-007D technical details" below.

DOCX/PDF structure extraction (originally a single QB-007E, then QB-007E/F) was further split
(2026-07-10) into four independently mergeable pieces — smaller review surface, easier to isolate a
regression to one piece rather than one large "DOCX+PDF+deps" PR:

- **QB-007E1 — Document extraction framework.** Implemented (2026-07-10). The extension-routed,
  fail-open preprocessing layer DOCX/PDF extraction will plug into — `.docx`/`.pdf` handlers are
  stubs that always raise `NotImplementedError`; no extraction, no optional dependencies yet. See
  "QB-007E1 technical details" below.
- **QB-007E2 — DOCX extraction.** Implemented (2026-07-10). `.docx` files are now genuinely
  converted to Markdown-shaped plain text (headings, paragraphs, bullet/numbered lists,
  GitHub-style tables, contiguous code-style paragraphs as fenced blocks) via `python-docx`, added
  as a new optional dependency group, `quor[documents]`. Still not wired into the live Read hook
  or `FilterRegistry`. See "QB-007E2 technical details" below.
- **QB-007E3 — PDF extraction.** Implemented (2026-07-10). `.pdf` files are now genuinely
  converted to Markdown-shaped plain text via `pdfplumber` (same `quor[documents]` extra) — the
  riskiest sub-item, exactly as anticipated: PDF has no structural document model the way DOCX
  does, so headings/paragraphs/lists are inferred purely from font-size and position heuristics,
  not an authored style. Still not wired into the live Read hook or `FilterRegistry`. See
  "QB-007E3 technical details" below.
- **QB-007E4 — Wire extraction into the live Read hook + benchmark coverage.** Implemented
  (2026-07-10). `quor/adapters/claude_read.py` now calls `extract()` for `.docx`/`.pdf` Reads and
  routes the result through the existing `markdown` `FilterConfig` (looked up by name, no
  `docx.toml`/`pdf.toml`) — a supported DOCX/PDF Read genuinely compresses via `updatedToolOutput`
  now, not just at the extraction/filter layers in isolation. The QB-007E2/E3 benchmark fixtures
  are wired into `manifest.toml`/`baseline.json` (4 new cases: `docx-design-doc-long` 16.0%,
  `docx-readme-short` 0.0%, `pdf-design-doc-long` 43.2%, `pdf-notes-short` 0.0%). See "QB-007E4
  technical details" below, including a genuine architectural finding surfaced (not silently
  worked around) partway through: the benchmark harness itself needed a small extraction branch to
  support binary sample files at all.

**Status:** QB-007A through QB-007E4 implemented **and merged to `main`** — shipped across PRs #40–#46, released in Quor **v0.4.0** (2026-07-11). *(Correction: this entry originally read "none committed/merged to main yet"; verified against `CHANGELOG.md` and `git log` while restructuring this document — every sub-item below is live in the current release.)*

</details>

<details>
<summary>QB-007A technical details</summary>

**What shipped:** `quor/adapters/claude_read.py` (new `PostToolUse`/`Read` hook adapter — always
omits `updatedToolOutput`), `quor/adapters/base.py` (new `ReadToolInput`, `PostToolUseHookInput`,
`PostToolUseHookSpecificOutput`, `PostToolUseHookOutput` models), `quor/__main__.py`
(`_run_hook()` now dispatches on adapter name — `"claude"` or `"claude-read"`),
`quor/cli/commands/init.py` (`quor init --claude` additively registers a second hook script and a
`hooks.PostToolUse`/`Read` entry, independent of the existing `hooks.PreToolUse`/`Bash` entry),
`quor/cli/commands/doctor.py` (two new checks: `Read hook script installed`,
`Read hook responds correctly`).

**No document compression, extraction, Markdown/DOCX/PDF handling, tracking integration, or new
dependencies were added** — those are QB-007B onward, deliberately scoped separately per the
design pass's "small, independently mergeable, minimize risk" rollout principle.

**Limitations (carried forward from the design pass, not resolved by this phase):**
- `quor doctor`'s `Read hook responds correctly` check proves Quor's own response shape is
  well-formed; it cannot prove the installed Claude Code binary actually honors
  `updatedToolOutput` for `Read` — that requires a real Claude Code session, outside this phase's
  automated test coverage.
- The minimum Claude Code version that honors `updatedToolOutput` for non-MCP tools remains
  unconfirmed.
- The real `PostToolUse` hook timeout budget on Windows remains unmeasured — not yet relevant
  while this phase does no real work, but load-bearing before QB-007D/E (DOCX/PDF extraction) can
  be scoped with confidence.

**Verification:** full `pytest tests/`, `quor verify`, `ruff check quor/ tests/`, `mypy quor/` all
green — see ADR-034 for the recorded decision and CHANGELOG.md's Unreleased section.

**Update (QB-007C):** this block is preserved as-is for historical accuracy of what was true when
QB-007A shipped alone. `updatedToolOutput` is no longer always omitted — see "QB-007C technical
details" below for what changed.

</details>

<details>
<summary>QB-007B technical details</summary>

**What shipped:** two new built-in filters — `quor/filters/builtin/markdown.toml` (`.md`,
`.markdown`) and `quor/filters/builtin/document-text.toml` (`.txt`, `.rst`) — routed by matching
`match_command` against a bare file path string instead of a shell command string, reusing
`FilterRegistry` exactly as-is (no new routing system, no schema change). Both filters use only
existing stage types (`strip_lines` for `preserve_patterns`-based structure protection,
`deduplicate_consecutive` for collapsing repeated/blank-line runs, `max_tokens` as the actual
budget-driven compression) — no new stage types were created. `group_repeated` was deliberately
**not** used: collapsing repeated-shape lines is safe for diagnostic tool output (its original use
case) but unsafe for prose/document content, where distinct TODOs, list items, or requirements can
share a superficial shape without being redundant — using it here would risk exactly the kind of
meaning loss PROJECT_BIBLE.md's Core Principle #1 rules out.

**Compression strategy:** `preserve_patterns` only, no strip (COMPRESS) patterns in either filter —
unlike shell-command output, a hand-written document has no reliable "noise" to strip without
risking real content loss. Headings (Markdown ATX only), bullet/numbered lists, fenced code block
*markers*, requirement/decision IDs, decision markers, TODO/FIXME/XXX, and NOTE/WARNING/CAUTION
callouts are all protected via `preserve_patterns`; `max_tokens` (`limit = 2000`, `strategy =
"head"`) is the only actual compression, and only engages once a document exceeds the budget — a
short document renders back byte-identical.

**Known, accepted limitations (not fixed — see below for why):**
- **Fenced code block interiors are not span-protected.** `strip_lines`/`max_tokens`'s
  `preserve_patterns` matches per-line only, with no concept of "protect everything between this
  marker and its matching close." The fence marker lines themselves are protected individually; the
  content between them is not, and `max_tokens`'s best-effort budget can compress through the
  middle of a large code block, leaving a fence marker without its partner (demonstrated, not just
  described, in `tests/unit/test_document_filters.py::TestMarkdownFencedCodeBlockLimitation`).
  Fixing this would require span-aware stage logic that does not exist in any Quor stage today —
  explicitly out of scope per this task's own instruction to "stop and explain the limitation
  rather than inventing new behaviour."
- **RST's setext-style heading convention (title line + punctuation-only underline) is not
  detected**, for the identical per-line-only-matching reason. `document-text.toml` only protects
  RST's single-line `.. code-block::` directive, which is reliably line-matchable.
- **A file path containing a space does not match either filter** (e.g. `My Documents\notes.md`)
  — both patterns are anchored to a single whitespace-free token
  (`^\S+\.(md|markdown)$`/`^\S+\.(txt|rst)$`), specifically so they can never accidentally intercept
  a real shell command string that merely references a `.md`/`.txt` file as an argument (a command
  string always contains a space once it has arguments). The trade-off: a spaced path safely falls
  through to no match rather than being compressed — never a routing corruption, at the cost of
  never compressing a document whose path contains a space.
- **A file literally named to look like an existing Bash command (e.g. `cat.md`) can be
  intercepted by that command's filter first**, since `FilterRegistry` is shared between Bash
  command strings and Read file paths and built-in load order is alphabetical
  (`cat.toml` < `document-text.toml` < `markdown.toml`). Narrow and unlikely in practice; inherent
  to reusing `match_command`/`FilterRegistry` rather than inventing a parallel routing system
  (explicitly out of scope per this task's requirements). Documented and regression-tested
  (`TestKnownRoutingCollision` in `tests/unit/test_document_filters.py`), not silently accepted.
  **Update (QB-007C):** at the live Read hook, this collision is now neutralized in practice — the
  adapter's own filter-name allowlist (see "QB-007C technical details" below) means a Read for
  `cat.md` safely passes through unchanged rather than being run through the `cat` filter, even
  though `FilterRegistry.find("cat.md")` still literally returns `cat` at the routing layer. The
  underlying `FilterRegistry`-level collision described above is unchanged and still applies to
  any *other* caller of `FilterRegistry` that doesn't apply the same allowlist.

**Benchmark coverage:** 4 new manifest cases (`markdown-design-doc-long`,
`markdown-readme-short`, `document-text-project-notes-long`, `document-text-rst-short`) with
committed baselines. Real, measured compression on realistic long-document samples: **29.5%** on a
~3,700-token engineering design doc, **18.8%** on a ~2,700-token plain-text meeting-notes doc.
Short, already-small samples (a README, an RST dev guide) show **0%** — correctly honest, not a
bug: with no strip patterns and `max_tokens` only engaging above budget, a document that never
exceeds the budget is never touched.

**Verification:** full `pytest tests/`, `quor verify`, `ruff check quor/ tests/`, `mypy quor/` —
see the QB-007B implementation session record for exact results.

**Update (QB-007C):** these filters are no longer filter-layer-only — QB-007C (below) wires them
into the live Read hook. All limitations above are unchanged and still apply now that compression
is live.

</details>

<details>
<summary>QB-007C technical details</summary>

**What shipped:** `quor/adapters/claude_read.py::run_hook()` now genuinely routes Read output
through `FilterRegistry`/`Pipeline` instead of always being a no-op. `tool_input.file_path` is
matched via `FilterRegistry.find()` (same three-tier project > user > builtin lookup the Bash
dispatcher uses, `project_root=Path.cwd()`), and if a match is found and applying it produces
content different from `tool_response`, that result is returned via `updatedToolOutput`. In every
other case (no match, no-op compression, or any exception) `updatedToolOutput` is omitted — the
existing `__main__._run_hook()` outer fail-open guard is unchanged, and `_compress_read_output()`
adds a second, more granular try/except around the routing/apply call specifically so one bad
filter can't take down Read compression for every other file in the same process (mirrors
`quor/adapters/dispatcher.py`'s own filter-layer try/except pattern). `quor doctor`'s
`Read hook responds correctly` check (QB-007A) was upgraded from a shape-only check to one that
drives a genuinely oversized document through the real hook and asserts compression actually fired
— a meaningfully stronger capability check than before.

**A real bug found and fixed during implementation — not a hypothetical:** `FilterRegistry` is
shared between the Bash dispatch path and this new Read path, and the built-in `generic` filter
(`z_generic.toml`, `match_command = '.'`) matches *every* non-empty string, including a Read file
path like `report.docx` or `script.py`. Without a guard, every unsupported file type would have
been silently routed through `generic`'s ANSI-strip/dedupe/`max_tokens` pipeline — a shell-output
filter never designed for, or tested against, arbitrary document content, directly violating this
task's own "unsupported file types pass through unchanged" requirement. Fixed with an explicit,
adapter-local allowlist (`_READ_SUPPORTED_FILTER_NAMES = frozenset({"markdown", "document-text"})`)
checked after `FilterRegistry.find()` returns a match — any match outside that set is treated as no
match. This is a caller-side check, not a `FilterRegistry`/schema change, so Bash routing is
completely unaffected (regression-tested in
`tests/unit/test_read_hook_activation.py::TestRoutingPrecedenceRegressions`). As a side effect,
this same allowlist also neutralizes QB-007B's documented `cat.md`-collision limitation for real
Read calls (see the "Update (QB-007C)" note on that limitation above) — not by fixing the
underlying `FilterRegistry` collision, but by refusing to apply a non-document filter regardless of
what `find()` returns.

**What was deliberately not touched:** tracking/SQLite/`quor gain` integration, DOCX/PDF, any
extraction library, new stage types, optional dependencies, and hook *registration* (`quor init
--claude`, `quor doctor`'s script-existence check) — all exactly as scoped. The existing
`PreToolUse`/Bash hook (`quor/adapters/claude.py`, `quor/adapters/dispatcher.py`) was not modified
at all.

**Update (QB-007D):** tracking/SQLite/`quor gain` integration is no longer untouched — see
"QB-007D technical details" below for what changed. Everything else in this list (DOCX/PDF,
extraction libraries, new stage types, optional dependencies, hook registration) remains exactly
as scoped here.

**Verification:** full `pytest tests/`, `quor verify`, `ruff check quor/ tests/`, `mypy quor/` —
see the QB-007C implementation session record for exact results.

**Limitations (carried forward, still unresolved by this phase):**
- The minimum Claude Code version that honors `updatedToolOutput` for `Read` remains unconfirmed —
  this phase makes the mechanism *work correctly when invoked*, it does not change what's known
  about whether/when a real Claude Code binary invokes it.
- The real `PostToolUse` hook timeout budget on Windows remains unmeasured. This is now more
  directly relevant than it was for QB-007A/B, since a large document's compression genuinely runs
  inside the hook's own request path — worth measuring before QB-007E/F (DOCX/PDF, which will be
  slower) are scoped.
- All QB-007B fenced-code-block/RST-heading/whitespace-path limitations are unchanged and now
  affect real, live compression rather than only the filter layer.

</details>

<details>
<summary>QB-007D technical details</summary>

**What shipped:** Read invocations now flow through the exact same tracking pipeline Bash
invocations already use — SQLite (`quor.db`), JSONL fallback (`invocations.jsonl`), and therefore
`quor gain` — with no schema change and no Read-specific storage or aggregation anywhere.

The only structural change: `dispatcher.py`'s previously-private `_track()` helper (build an
`InvocationRecord`, call `TrackingDB.record()`, fail-open on any exception) was promoted to a
public function, `track_invocation()`, in `quor/tracking/db.py` — the module that already owns
`InvocationRecord`/`TrackingDB`/`count_tokens`, and the natural home once a second producer needed
the identical logic. `dispatcher.py` now calls `track_invocation()` instead of its old private
method; behavior for Bash tracking is byte-for-byte unchanged (verified: `TestDispatcherTracking`
in `tests/unit/test_tracking.py` required no changes). `quor/adapters/claude_read.py`'s
`_compress_read_output()` calls the same `track_invocation()` at every exit point that represents
a genuine Read invocation, recording `command="Read: {file_path}"` (an empty `file_path` is the one
case treated as "nothing happened" and left untracked, mirroring `run_dispatch([])`'s early return
before dispatching or tracking anything). `run_hook()` gained an optional `tracking: TrackingDB |
None = None` keyword (default `None` so every pre-existing direct caller/test is unaffected);
`__main__._run_hook()` constructs a `TrackingDB` via `get_tracking_db()` and passes it in for the
`"claude-read"` adapter only, closing it in a `finally` — exactly the same pattern
`_run_dispatch()` already uses for Bash, now visible in both branches of `_run_hook()`.

**Passthrough/filter-name split (mirrors dispatcher.py's `_lookup_filter`/`_apply_content_filter`
split exactly):** no match, or a match outside `_READ_SUPPORTED_FILTER_NAMES` (including a
`FilterRegistry` construction/lookup error) → `filter_name=None, was_passthrough=True`. A supported
filter matched — whether or not applying it changed the content, or even raised (fail-open falls
back to the original response) → `filter_name=<name>, was_passthrough=False`. This means an
*unchanged* compression (small document under budget) is tracked identically to how dispatcher
tracks an unchanged Bash filter application: a filter was genuinely attempted, so it's not counted
as a passthrough, even though `updatedToolOutput` itself is correctly omitted.

**What was deliberately not touched:** `schema.sql`, `_SCHEMA_VERSION`, `InvocationRecord`'s
fields, `query_gain()`, `normalize_project_path()`, the JSONL write format, and `quor gain`'s CLI
rendering — none of these needed, or received, any change. A Read row is aggregated into `quor
gain` purely because it's an ordinary row in the same `invocations` table; no Read-specific
reporting path exists or was added.

**Verification:** full `pytest tests/`, `quor verify`, `ruff check quor/ tests/`, `mypy quor/` all
green. New coverage: `tests/unit/test_tracking.py::TestReadTracking` (compressed/unchanged/
unsupported/no-match/filter-failure tracking, SQLite row shape, JSONL fallback, project-identity
parity with Bash rows, multi-Read aggregation via `query_gain()`) and
`tests/unit/test_cli.py::TestGain::test_read_activity_included_alongside_bash` (a Read row and a
Bash row in the same `quor gain` window, no special-casing).

**Limitations (carried forward, not resolved by this phase):**
- Every Read hook invocation now opens and closes a `TrackingDB` (background thread + SQLite
  connection) exactly once, the same per-invocation cost the Bash dispatch path already pays for
  every command — not a new cost class, but now paid on every Read too. Not measured against the
  unmeasured `PostToolUse` timeout budget noted under QB-007C.
- All QB-007A/B/C limitations (unconfirmed minimum Claude Code version, unmeasured Windows hook
  timeout budget, fenced-code-block/RST-heading/whitespace-path filter limitations) are unchanged.

</details>

<details>
<summary>QB-007E1 technical details</summary>

**What shipped:** a new package, `quor/pipeline/extract/` (`__init__.py` empty, matching every
other package in this codebase — `registry.py` holds the actual logic), whose entire public surface
is one function: `extract(file_path: Path) -> str | None`. `None` always means "fail open, proceed
exactly as if this layer did not exist" — an unregistered extension, a registered-but-unimplemented
handler, or a handler that raised for any other reason are all indistinguishable to the caller.
Routing is a plain `dict[str, Callable[[Path], str | None]]` keyed by lower-cased `Path.suffix`; only
`.docx` and `.pdf` are registered, and both handlers unconditionally `raise NotImplementedError`
(absorbed silently — an expected, known state, not a bug — while any *other* exception a future real
handler raises is absorbed with a warning, so a genuine extraction bug is still visible). `.md`/
`.txt`/`.rst` are deliberately **not** registered: they need no extraction (Read already returns them
as plain text, and QB-007B/C's filters already compress them directly), so they fail open via the
same "no handler" path as any unknown extension — proven directly by test
(`tests/unit/test_extract.py::TestUnknownExtension::test_markdown_extension_returns_none` et al.).

**Architecture — deliberately not integrated yet:** extraction is not a `StageHandler`, is never
registered with `Pipeline`, and never touches `ContentMask` or `FilterRegistry` — none of those three
modules were modified, at all. `quor/adapters/claude_read.py` was also **not** modified in this
phase: `extract()` is not yet called from the Read hook. Wiring it in (so an actual `.docx`/`.pdf`
Read routes through `extract()` before `FilterRegistry`) is deferred to QB-007E2/E3, once there's a
real handler for `extract()` to return something other than `None` for — wiring a permanently-`None`
call in now would be pure indirection with no observable effect, and would make it harder (not
easier) to verify "hook behaviour unchanged" for this phase.

**Design pass (Rule 4 — competitor-first):** consulted the archived competitive/landscape research
(`docs/archive/`) and found no prior conclusions on DOCX/PDF library choice — QB-007F's (now
QB-007E3's) own "riskiest sub-item, no semantic ground truth" note already acknowledged this gap, so
QB-007E1 is new groundwork, not a repeat of existing research. Reused two existing precedents
instead of inventing new ones: the plugin system's import-failure-tolerant pattern (ADR-007 "Plugin
failures log warnings; they never halt processing", `quor/pipeline/plugin_loader.py`'s
`ImportError`-to-`None` handling) for how this layer must degrade, and ADR-014's already-anticipated
(but not yet instantiated) `quor[ml]`-style optional-dependency extra as the template QB-007E2/E3
will follow when `python-docx`/a PDF library are actually added — no extras group was created in this
phase, since no dependency exists yet to gate behind one.

**What was deliberately not touched:** `Pipeline`, `FilterRegistry`, `ContentMask`,
`quor/adapters/claude_read.py`, `pyproject.toml` (no `python-docx`/`pdfplumber`/`pypdf`, no new
`[project.optional-dependencies]` group), and no real extraction logic of any kind — exactly as
scoped. `base.py` was considered and omitted: with only two trivial stub handlers sharing an
already-explicit `Callable[[Path], str | None]` type, a formal `Protocol`/ABC would be premature
abstraction for a contract this small; revisit if QB-007E2/E3 reveal handlers need shared state or a
richer interface than a plain function.

**Verification:** full `pytest tests/`, `quor verify`, `ruff check quor/ tests/`, `mypy quor/` all
green. New coverage: `tests/unit/test_extract.py` (23 tests) — unknown/unregistered extensions,
supported-but-unimplemented extensions (and that `NotImplementedError` doesn't warn while other
exceptions do), fail-open across multiple exception types, extension-based routing (including
case-insensitivity and suffix-only matching, not substring search), registry contents, and a
"never raises regardless of input" sweep.

**Limitations (carried forward, not resolved by this phase):**
- No real extraction exists yet — every `.docx`/`.pdf` Read still behaves exactly as it does today
  (unsupported, passes through unchanged), because `extract()` isn't called from anywhere yet.
- The routing table has no case for files whose real content type doesn't match their extension
  (e.g. a `.docx` that's actually plain text) — not relevant while both handlers are unconditional
  stubs, but worth deciding explicitly once QB-007E2 adds real parsing.

</details>

<details>
<summary>QB-007E2 technical details</summary>

**What shipped:** `.docx`'s stub in `quor/pipeline/extract/registry.py` is replaced with a real
handler, `extract_docx()`, in a new sibling module `quor/pipeline/extract/docx.py` — QB-007E1's own
"revisit module splitting once a handler needs a richer interface" note is exactly what triggered
this split; `registry.py` stays a pure routing table (`{".docx": extract_docx, ".pdf":
_extract_pdf}`) and gained no DOCX-specific logic. `python-docx` (`>=1.1.0,<2.0.0`) is added as a
new optional dependency group, `quor[documents]` — and separately listed in `dev` too, so
contributors running the full test suite get real fixture coverage without a second install step
— following ADR-014's already-anticipated (but until now uninstantiated) `quor[ml]`-style extras
template, exactly as QB-007E1 said this phase would. A `[[tool.mypy.overrides]]` entry
(`ignore_missing_imports` for `docx`/`docx.*`) keeps `mypy quor/` green whether or not the extra is
installed in a given environment — the same pattern already used for `regex` and
`quor_test_stage`.

**Conversion algorithm:** walks `document.element.body` directly (not `document.paragraphs`/
`.tables`, which are separate flat lists that lose the true interleaving of paragraphs and tables)
— this is python-docx's own documented recipe for in-document-order iteration, not a novel
technique. Each block is classified by paragraph style name: `"Heading 1"`.."Heading 6"` → ATX
`#`.."######"`; `"List Bullet"`* → `- `; `"List Number"`* → a sequential counter that increments
within a contiguous run and resets to 1 whenever a different block type interrupts it (valid,
readable raw Markdown for an LLM to read directly — not merely valid for HTML rendering, where a
repeated literal `1.` would also render correctly but read confusingly as plain text); anything
else falls through as a normal paragraph. Tables render as GitHub-style Markdown, first row always
treated as the header (python-docx has no general "is this a header row" signal), `|` escaped in
cell content, multi-paragraph cells joined with `<br>`. Code-style paragraphs are detected two
ways — a style name containing "code" (case-insensitive), or every run in the paragraph having an
explicit monospace font override (Consolas, Courier New, etc.) — and contiguous code paragraphs
merge into a single fenced block, with leading whitespace (indentation) deliberately preserved
even though every other branch strips it (indentation is semantically meaningful in code, not in
prose). Verified empirically, not assumed: `paragraph.text` already includes hyperlink visible text
in the installed python-docx version (confirmed via a hand-built `w:hyperlink` fixture), so no
special-casing was needed there.

**Fail-open, self-contained:** unlike the passthrough-registration in QB-007E1 (where fail-open was
purely `registry.extract()`'s job), `extract_docx()` catches its own exceptions — missing
`python-docx` (a specific, actionable warning naming `quor[documents]`), and everything else
(corrupt file, invalid zip, unreadable/missing file, any other parser exception) via one generic
try/except, matching this task's explicit requirement that `_extract_docx` itself never raise,
independent of whatever calls it. `registry.extract()`'s own wrapper is unchanged and still there
as a second layer (load-bearing for the `.pdf` stub, defense-in-depth for `.docx`).

**Metadata exclusion:** `document.core_properties` (author, revision, timestamps) is never read at
all — not extracted-then-stripped, simply never touched, since only `document.element.body`'s
paragraphs/tables are walked. Comments and headers/footers are excluded the same way: they live in
separate document parts python-docx's body-walk never visits.

**Design pass (Rule 4 — competitor-first):** confirmed via the archived research and QB-007E1's own
audit that no prior conclusion existed on DOCX library choice or python-docx object-model walking —
this is new groundwork. Reused `quor/filters/builtin/markdown.toml`'s exact `preserve_patterns`
regexes (`^#{1,6}\s+\S`, `^\s*[-*+]\s+\S`, `^\s*\d+[.)]\s+\S`) as the target shape for extractor
output, so a supported document's extracted headings/lists are structurally recognizable to the
existing filter once wired in.

**What was deliberately not touched:** `Pipeline`, `FilterRegistry`, `ContentMask`, and
`quor/adapters/claude_read.py` — `extract()` is still not called from anywhere in production;
wiring it into the Read hook remains out of scope (QB-007E3/E4). No `manifest.toml`/`baseline.json`
changes — two representative `.docx` sample fixtures were added
(`tests/benchmarks/samples/docx/001_design_doc_ranking_cache.docx`,
`002_short_client_readme.docx`, mirroring the existing markdown long/short benchmark pair) but not
wired into the benchmark harness, since there is no live compression path to measure yet — that is
QB-007E4's job.

**Verification:** full `pytest tests/`, `quor verify`, `ruff check quor/ tests/`, `mypy quor/` all
green. New coverage: `tests/unit/test_extract_docx.py` (headings 1–6, single-line flattening,
non-Heading styles falling through correctly, plain paragraphs, empty-paragraph handling, hyperlink
text, bullet lists, numbered lists with restart-after-interruption, GitHub-style tables including
pipe-escaping/multi-paragraph-cell/`<br>`/document-order-with-surrounding-paragraphs/empty-table,
contiguous code-block merging with indentation preservation, style-name-only code detection,
unstyled-paragraph-is-not-code, empty document (`""`, not `None`), whitespace-only document,
not-a-zip/wrong-internal-structure/truncated/nonexistent-file fail-open, missing-dependency
fail-open with the actionable message, and the same behavior verified again through
`registry.extract()`'s full dispatch path) — all built from real fixtures generated with
python-docx itself, not mocks. `tests/unit/test_extract.py`'s QB-007E1 "supported but not
implemented" coverage was narrowed to `.pdf` only (still a real stub); its routing/fail-open/
registry tests, which patch `_EXTRACTORS` directly, were unaffected by `.docx` becoming real.
Also verified directly (not just by absence of an import error): `quor` imports cleanly and
`extract()` degrades correctly for every extension, including `.docx`, with `python-docx`
completely absent from `sys.modules`.

**Limitations (carried forward, not resolved by this phase):**
- No nested/multi-level list support — all bullet levels flatten to `- `, all numbered levels to
  one flat, restarting counter; Word's actual `numPr`/`ilvl` numbering XML is not resolved.
- No run-level emphasis (bold/italic) is preserved — "do not invent new formatting" was read as
  scoping this phase to the structural elements the task explicitly listed, not as license to add
  `**`/`*` markers unrequested; revisit if a future phase needs it.
- Monospace-font code detection only catches an explicit *per-run* font override; a document-wide
  theme font or a custom style that merely *implies* monospace without "code" in its name is not
  detected.
- Merged table cells repeat their text across every grid column they visually span (Markdown has no
  colspan syntax to represent a true merge) rather than emitting the value once.
- Images, footnotes/endnotes, and headers/footers are silently absent from output — not OCR'd, not
  extracted, not represented as placeholders.
- Not wired into the Read hook — every limitation already listed under QB-007A/B/C (unconfirmed
  minimum Claude Code version, unmeasured Windows hook timeout budget) remains unmeasured for a
  DOCX-sized document specifically, since none has run through the real hook path yet.

</details>

<details>
<summary>QB-007E3 technical details</summary>

**What shipped:** `.pdf`'s stub in `quor/pipeline/extract/registry.py` is replaced with a real
handler, `extract_pdf()`, in a new sibling module `quor/pipeline/extract/pdf.py` — same module
split QB-007E2 established for DOCX, for the same reason (real per-format logic belongs in its
own module, not in the routing table). `pdfplumber` (`>=0.11.0,<1.0.0`) is added to the existing
`quor[documents]` extra alongside `python-docx`, plus `dev` (for real-fixture test coverage). A
new dev-only dependency, `reportlab` (`>=4.0.0,<6.0.0`), was also added — write-only, never
imported by `quor` itself (pdfplumber cannot author PDFs, unlike python-docx which both reads and
writes DOCX), used solely to generate real `.pdf` test/benchmark fixtures with controlled font
sizes and layout. New `[[tool.mypy.overrides]]` entry for `pdfplumber`/`pdfplumber.*`, matching
the `docx` override's reasoning.

**Why PDF is structurally harder than DOCX, concretely:** DOCX has an explicit document object
model — `paragraph.style.name` literally says `"Heading 2"`. PDF has none; `pdfplumber` exposes
only character geometry (`top`/`bottom`/`x0`/`x1`) and font metadata (`size`, `fontname`) per
glyph. Every structural signal here is inferred, not read:
- **Headings** are inferred from font size alone, exactly as the task specified ("larger font →
  higher heading level," "simple, deterministic heuristics only"): the most common line size
  across the whole document is taken as "body text," every distinct size larger than that is
  ranked into a heading tier (largest → level 1, clamped at level 6), consistently across pages
  (computed once, in a first pass, not re-derived per page).
- **Paragraphs** are reconstructed from `pdfplumber.extract_text_lines()` (which already groups
  characters into visual lines) by merging consecutive lines whose vertical gap is small relative
  to font size (calibrated empirically against generated fixtures: ~0.3× size within a wrapped
  paragraph vs. ~0.9×+ size between genuinely distinct blocks) into one paragraph; a larger gap
  starts a new one. The same gap heuristic also merges wrapped continuation lines into bullet/
  numbered/code blocks, not just plain paragraphs.
- **Bullets/numbers** are recognized by regex against each line's own leading text (`•`/`◦`/`▪`/
  `‣`/`●`/`○`/`·`/ASCII `-`/`*`/`+` for bullets; `\d+[.)]` for numbers) — unlike DOCX, a PDF's
  visible number is *already* part of its rendered text (Word's auto-numbering isn't), so the
  number itself is reused verbatim; the delimiter is still normalized (bullets always render as
  `-`, numbers always as `N.`, matching DOCX's own normalization philosophy).
- **Tables** use `pdfplumber.Page.find_tables()` directly (GitHub-style Markdown output, `|`
  escaped, same as DOCX) — its bounding boxes are also used to exclude a table's own cell text
  from separately appearing as stray paragraph lines, since `extract_text_lines()` and
  `find_tables()` both see the same underlying characters.
- **Code** is detected by font-name substring match (`courier`, `consolas`, `mono`, ... —
  case-insensitive, since PDF font names are frequently subset-mangled, e.g.
  `"ABCDEF+CourierNewPSMT"`) and merges contiguous monospace lines into one fenced block. Leading
  indentation — which `extract_text_lines(strip=True)` strips from the text itself — is
  reconstructed from each line's `x0` relative to the *code block's own first line* (never an
  assumed page margin, which varies per document and would mis-indent every line if guessed
  wrong), divided by the monospace font's own (exact, not approximate) character width.

**A real bug found and fixed during implementation, with a regression test — not a hypothetical:**
building the benchmark fixtures (below) surfaced a genuine defect in the font-size heuristic.
`pdfminer` can fail to decode a bullet glyph to a real Unicode codepoint (no `ToUnicode` CMap —
observed with `reportlab`'s own default `ListFlowable` bullets, and a known real-world PDF
phenomenon, not a fixture artifact) and represents it as *several* zero-width `(cid:N)` placeholder
characters stacked at one position, at the bullet's own (often larger) font size. The original
per-line dominant-size calculation used a raw character-COUNT mode, which let e.g. 9 phantom
zero-width characters at 12pt outvote 6 real, visible characters at 10pt on a short line like
"• queued" — landing that line's inferred size in a real heading tier established elsewhere in the
document, misrendering it as `## (cid:127) queued` instead of a plain paragraph. Fixed by weighting
the dominant-size calculation by each character's rendered *width* (`x1 - x0`) instead of a flat
count — the phantom characters contribute zero width and can no longer out-vote real text, whatever
their string-length happens to be. Regression-tested
(`TestHeadings::test_undecodable_bullet_glyph_does_not_misclassify_its_line_as_a_heading`,
verified to fail against the pre-fix count-based implementation and pass against the fix). A
related, separate finding was folded into the same fix: code-block lines are now also excluded
from the body/heading size sample (previously only table lines were) — a code block's font is
frequently a different size than body prose, and letting it into the size analysis could similarly
corrupt heading detection for the rest of the document.

**Known, accepted, tested-not-hidden limitation:** the *fix above* stops an undecodable bullet
glyph from corrupting heading detection, but such a line still cannot be recognized as a bullet at
all — the glyph genuinely isn't a `-`/`*`/`•`/etc. in the extracted text, so it falls through to a
plain paragraph (regression-tested,
`TestKnownLimitations::test_undecodable_bullet_glyph_falls_through_to_plain_paragraph`). This is a
property of the *source PDF's* own font encoding, not something a text-position/font-size heuristic
can work around.

**What was deliberately not touched:** `Pipeline`, `FilterRegistry`, `ContentMask`, and
`quor/adapters/claude_read.py` — `extract()` is still not called from anywhere in production. No
OCR, no ML, no PyMuPDF/`fitz`, no external services — exactly as scoped. Document metadata
(`pdf.metadata`) is never read; images are never inspected or described. Two representative `.pdf`
sample fixtures were added (`tests/benchmarks/samples/pdf/001_design_doc_export_pipeline.pdf`,
`002_short_client_notes.pdf`, mirroring QB-007E2's DOCX long/short pair) but not wired into
`manifest.toml`/`baseline.json` — QB-007E4's job, once there's a live compression path to measure.

**Verification:** full `pytest tests/`, `quor verify`, `ruff check quor/ tests/`, `mypy quor/` all
green. New coverage: `tests/unit/test_extract_pdf.py` (31 tests, built from real fixtures generated
with `reportlab`) — font-size heading levels (including the >6-tier clamp and the no-larger-font
case), wrapped-paragraph merging vs. genuine paragraph breaks, ASCII/star bullets, numbered lists
(including delimiter normalization and verbatim number reuse), the Unicode-bullet-glyph regex
contract tested directly (independent of whether a given PDF's font can round-trip the glyph),
GitHub-style tables (including pipe escaping and document-order interleaving with surrounding
paragraphs), monospace code-block merging with indentation reconstruction, blank/image-only pages
(`""`, not `None`), corrupt/zip-masquerading/truncated/nonexistent-file fail-open, encrypted-PDF
fail-open, missing-dependency fail-open with the actionable message, multi-page document-order
preservation, the bullet/heading regression above, and the documented undecodable-bullet
limitation. `tests/unit/test_extract.py`'s "supported but not implemented" coverage no longer names
any specific extension (both `.docx` and `.pdf` are real now) — it patches a fake stub handler
directly to prove the `NotImplementedError`-absorption *mechanism* itself still works, independent
of whatever real extensions happen to be registered.

**Limitations (carried forward, not resolved by this phase):**
- Heading/paragraph/list detection is geometry-based inference, not ground truth — a PDF with
  unusual line spacing, a body font that happens to vary in size, or heading text set in the same
  size as body text (bolded instead, say) will not be detected the way a human reader would.
- No nested/multi-level lists, same as DOCX — every bullet/numbered level flattens.
- No run-level emphasis (bold/italic) preserved, same as DOCX.
- The undecodable-bullet-glyph limitation described above.
- Not wired into the Read hook — same unconfirmed-Claude-Code-version and unmeasured-hook-timeout
  limitations already carried from QB-007A/B/C, now also unmeasured for a PDF-sized document.

</details>

<details>
<summary>QB-007E4 technical details</summary>

**What shipped:** `quor/adapters/claude_read.py::_compress_read_output()` gained one new branch,
checked immediately after the existing `tool_response`-is-a-string check: if `Path(file_path).suffix`
is `.docx`/`.pdf` (`_EXTRACTION_EXTENSIONS`, an adapter-local allowlist mirroring
`_READ_SUPPORTED_FILTER_NAMES`'s own pattern), the call is diverted to a new function,
`_compress_extracted_document()`, before falling through to the existing (unmodified)
`.md`/`.txt`/`.rst` code path. That function: (1) calls `extract(Path(file_path))` — QB-007E1/E2/E3's
existing, unmodified public API — (2) on `None`, tracks and returns exactly like "no filter
matched"; (3) on success, looks up the existing `"markdown"` `FilterConfig`
(`quor/filters/builtin/markdown.toml`) *by name* via a new local helper, `_find_filter_by_name()`,
composed from `FilterRegistry.all_filters()` (a real .docx/.pdf command string would never match
`markdown.toml`'s `^\S+\.(md|markdown)$` file-path pattern, so `FilterRegistry.find()` — the
existing routing method — genuinely could not be reused here; by-name lookup is the smallest
addition that still reuses `FilterRegistry.apply()` itself completely unchanged); (4) applies it,
falling back to the unfiltered extracted text on any error, mirroring the non-extraction path's own
`_apply_content_filter`-equivalent fail-open exactly. `FilterRegistry` itself was not modified —
`_find_filter_by_name()` lives in `claude_read.py`, composed entirely from existing public methods.

**original_tokens/final_tokens semantics (as specified):** `original` passed to `track_invocation()`
is always the raw `tool_response` (the pre-extraction Read result) — not the extracted text — so
`original_tokens` reflects what Claude would have received without Quor at all. `final` is whatever
is actually returned as `updatedToolOutput` (the extracted-and-filtered text, or the
extracted-but-unfiltered fallback on a filter error) — the same "track what was actually produced"
principle already used everywhere else `track_invocation()` is called. No `InvocationRecord` field,
schema, or `track_invocation()` call signature changed — QB-007D's tracking is reused byte-for-byte;
these are new *call sites*, not new tracking logic. A practical consequence worth stating plainly:
because extraction alone already transforms the raw `tool_response` into clean Markdown, a
`.docx`/`.pdf` Read returns `updatedToolOutput` far more often than a `.md` Read does — even a short
DOCX/PDF still returns the extracted text (proven by
`TestDocxPdfExtraction::test_small_docx_still_returns_extracted_text`), whereas a short `.md` file
correctly omits it (already unchanged content). The two paths are not symmetric, and are not meant
to be — the "omit if unchanged" comparison is against the true final output either way; document
extraction, by construction, essentially never coincides with the raw `tool_response` it replaces.

**A genuine architectural finding, surfaced rather than silently resolved:** wiring the QB-007E2/E3
benchmark fixtures into `manifest.toml` initially hit a real gap —
`tests/benchmarks/benchmark_runner.py::run_case()` read every `sample_file` as plain UTF-8 text and
had no extraction step anywhere in it, so a `.docx`/`.pdf` sample would either crash
(`UnicodeDecodeError`) or need a pre-extracted `.md` stand-in that would never actually exercise
`extract()`. This was raised to the user rather than resolved unilaterally (options: (a) add a
minimal extraction branch to `run_case()`, (b) benchmark pre-extracted `.md` companions instead
(never exercises `extract()`), (c) skip manifest wiring and document the gap) — (a) was chosen.
`run_case()` now branches on `sample_path.suffix`: for `.docx`/`.pdf`, it calls `extract()` (not
`read_text()`) and looks up the filter by `case.expected_filter` via a small `_find_filter_by_name()`
duplicated in `benchmark_runner.py` itself (not imported from `claude_read.py`'s copy, which is
module-private) — otherwise unchanged. This means `original_tokens` for these 4 benchmark cases is
tokens in the *extracted* text, not a literal raw `tool_response` — what a real `tool_response`
contains for a binary Read remains unconfirmed (an open item since QB-007A), so extracted-text
tokens is the most honest figure available, not a stand-in for that unknown.

**A second, smaller finding while building the benchmark fixtures:** the initial long DOCX/PDF
fixtures (reused as-is from QB-007E2/E3) extracted to text that was already under, or only barely
over, `markdown.toml`'s 2000-token budget — and even once total token count exceeded 2000,
compression still didn't engage. Root cause: `max_tokens`' budget is only charged against
non-PROTECT (KEEP) content — `preserve_patterns`-matched lines (headings, REQ IDs, lists, TODO/
WARNING/NOTE callouts) are free regardless of count. Several padding paragraphs added for length
also *referenced* REQ IDs inline ("...because REQ-101 requires...", mirroring realistic design-doc
prose), which protected them too, leaving genuinely-compressible KEEP content under budget even
though the *document* was well over it. Fixed by expanding both long fixtures with additional prose
that intentionally avoids `preserve_patterns` trigger substrings, verified empirically (not
guessed) against the real filter until genuine compression engaged: `docx-design-doc-long` 16.0%,
`pdf-design-doc-long` 43.2%. The two short fixtures were left untouched in content (0.0% is the
correct, expected result for an under-budget document, matching `markdown-readme-short`'s own
precedent) — their binary bytes still show as changed in git purely from non-deterministic
`docx`/`reportlab` save-time metadata (e.g. embedded timestamps), not content.

**What was deliberately not touched:** `Pipeline`, `ContentMask`, `quor/pipeline/extract/docx.py`,
`quor/pipeline/extract/pdf.py`, and `quor/pipeline/extract/registry.py` — all reused completely
unchanged (confirmed via `git diff --stat`). `FilterRegistry` gained no new method; `dispatcher.py`
was not touched at all. No `docx.toml`/`pdf.toml` was created. No `InvocationRecord`
field/schema/migration changed.

**Verification:** full `pytest tests/`, `quor verify`, `ruff check quor/ tests/`, `mypy quor/`, and
`python -m tests.benchmarks.run_benchmarks` all green; `baseline.json` updated (purely additive —
4 new entries, zero existing entries changed, confirmed via `git diff`). New coverage:
`tests/unit/test_read_hook_activation.py::TestDocxPdfExtraction` (large DOCX/PDF extraction +
compression, protected-structure survival, nonexistent/corrupt-file fail-open, extraction-exception
fail-open, small-document still-returns-extracted-text, tool_response-already-matches omission,
still-unsupported-extension passthrough) — and `TestUnsupportedTypesPassThrough`'s `.docx`/`.pdf`
parametrize entries were removed (they were passing for an increasingly wrong reason once extraction
existed — see that test's own updated docstring). `tests/unit/test_tracking.py::TestReadTracking`
gained DOCX/PDF-specific cases (original_tokens from raw `tool_response`, final_tokens from the
actual compressed output, extraction-failure-as-passthrough, aggregation alongside markdown rows).
`tests/unit/test_cli.py::TestGain` gained a case proving a DOCX Read pools into the same `"markdown"`
Top savings row a `.md` Read would, with no separate reporting category.

**Limitations (carried forward, not resolved by this phase):**
- Every limitation already documented under QB-007E1/E2/E3 (no nested lists, no bold/italic
  emphasis, undecodable bullet glyphs, geometry-based PDF inference, unconfirmed minimum Claude
  Code version, unmeasured Windows hook timeout budget) now applies to live production traffic,
  not just isolated filter-layer testing — none of them were resolved by this phase, they're simply
  now reachable from a real Read call.
- What a real Claude Code `tool_response` contains for a genuine binary DOCX/PDF Read remains
  unconfirmed — `original_tokens` for a real Read (and for the 4 new benchmark cases) is measured
  against the best available proxy (a placeholder string in tests; extracted text in benchmarks),
  not a verified true value.
- The benchmark harness's new extraction branch is minimal and DOCX/PDF-specific
  (`_EXTRACTION_EXTENSIONS`, duplicated from `claude_read.py`'s own constant rather than shared) —
  a third extracted format would need the same small, manual addition in both places.

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

#### QB-038 — `quor verify`/`quor doctor` falsely reported unhealthy on a plain `pip install quor`

**Effort:** Small · **Value:** High · **Category:** Bugfix

`quor verify` and `quor doctor` both reported 18 inline test failures on a completely normal,
expected install — a plain `pip install quor` with no optional extras. Nothing was actually broken;
the tests themselves were wrong.

<details>
<summary>Technical details</summary>

**Problem:** Discovered during the v0.4.0 release's real-PyPI installation validation (fresh venv,
`pip install quor`, no extras). `cat-javascript.toml`/`cat-typescript.toml`'s inline
`[[filter.tests]]` assert AST-summarization behavior (e.g. `must_not_contain` a compressed function
body) that only holds when the optional `quor[javascript]` extra (tree-sitter) is installed. Without
it, `code_ast_summarize` correctly fails open — no compression, a clear warning — but the tests
weren't written to account for that fallback, so they failed instead of passing-with-the-expected-
fallback-behavior. Both `quor verify` and `quor doctor`'s own `_check_filters()` call
`FilterRegistry.run_tests()`, so both reported failure/unhealthy — including automatically, right
after `quor init --claude`'s own final step — for every user who installed quor the primary,
documented way.

**Fix:** Added `FilterTest.requires_language: str | None` (`quor/config/model.py`) — when set, the
test only runs if that AST language is actually available. New
`quor.pipeline.ast_summarize.registry.is_language_available(language)` does the availability check
(stdlib `ast` for "python" is always available; "javascript"/"typescript"/"tsx" probe their
tree-sitter imports without ever emitting the user-facing warning `analyze_*()` would). Tagged the
8 affected tests across `cat-javascript.toml`/`cat-typescript.toml` (`cat-typescript`/`cat-tsx`
blocks) — the "invalid syntax fails open" test in each filter was correctly left untagged, since
that assertion holds regardless of whether tree-sitter is installed.

`FilterRegistry.run_tests()` now returns a `TestRunResult(failures, skipped)` dataclass instead of a
bare `list[str]` — a tagged test whose language isn't available is skipped (not run, not failed) and
recorded with a clear reason. `quor verify`'s output and `quor doctor`'s "Built-in filter tests
pass" detail both now surface skip counts distinctly from failures, so a user on a core-only install
sees *why* those tests didn't run, not a false "unhealthy" verdict.

**Verified:** simulated the missing-dependency case via `sys.modules[name] = None` (the reliable way
to force `ImportError` on a specific module — monkeypatching `builtins.__import__` does not reliably
intercept `importlib.import_module()`, confirmed the hard way during this investigation) — 0
failures, 8 skips, exactly matching the 18 individual failure lines seen on the real PyPI install.
Confirmed no regression when the extra *is* installed (this repo's own dev/CI environment): 88/88
still pass, nothing skipped, byte-identical `quor verify`/benchmark output to before this fix.

**Found during pre-commit review — a second, real bug (not introduced by this fix, but newly
visible because of it):** the "install this extra" hint text — `(quor[javascript])` in doctor's
detail and `pip install "quor[javascript]"` in verify's footer — silently lost its `[javascript]`
portion. Root cause: Rich's `console.print()` parses `[...]` in any un-escaped string as a style
tag; `"javascript"` isn't a recognized style, so Rich dropped it, along with the enclosing brackets
(`(quor)` instead of `(quor[javascript])`). Confirmed this is a pre-existing class of bug, not new:
`run_tests()`'s `[{filter_config.name}]` failure-label prefix has always been vulnerable to the same
issue for *any* filter name — verified directly (`console.print("[cat-javascript] test 1: ...")`
prints without the `[cat-javascript]` prefix at all). Fixed throughout: `doctor.py` now escapes
`name`/`detail` via `rich.markup.escape()` before interpolating them next to real markup
(`[green]✓[/green]`); `verify.py` escapes filter names/failure text the same way, and prints the
`pip install "quor[...]"` hint with `markup=False` (no real styling needed there, so disabling
markup parsing entirely is simpler and more robust than escaping). Two existing regression tests
strengthened to assert the literal, un-mangled text appears — they would have caught this had they
existed before.

**Redesigned `quor verify`'s output** (requested during the same review) from a flat, unaligned
per-filter list to a dot-leader-aligned dashboard: `✓ name ... x/y` for a fully-passing filter,
`⊘ name ... skipped (optional dependency not installed)` for one with only-skips-no-failures (a
distinct symbol from `✓`, not just shared-checkmark-plus-smaller-text, so a skimming reader can't
mistake a skip for a pass), `✗ name` with the existing per-test failure detail below for a real
failure. New footer, shown only when at least one test was skipped: "Install optional language
support:" followed by one `pip install "quor[...]"` line per distinct extra actually needed —
derived from which `requires_language` values were actually skipped
(`ast_summarize/registry.py::extra_for_language()`), not hardcoded, so a future language sharing a
different extra name would produce the correct hint automatically.

**Status:** Fixed and merged to `main` (PR #49, `fix/qb-038-verify-optional-deps`) — shipped in Quor **v0.4.1** (2026-07-11), the current release.

</details>

---

### Medium Value

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

#### QB-036 — Intelligent Early Exit / Whole Output Short-Circuit

**Effort:** Small · **Value:** Medium · **Category:** Optimization

**Note on numbering:** requested and tracked in conversation as "QB-009," but QB-009 was already a
completed, shipped item (`truncate_lines`, "Added a way to cap very long lines" — see that entry
above). Rather than silently overwrite or ambiguously duplicate an existing ID, this work was filed
under QB-036, the next free number (QB-001 through QB-035 are all already in use, with no gaps) —
flagged explicitly, not silently substituted, in the implementation's own final report.

Added an optimization inside the compression pipeline that skips remaining stages once nothing they
could do would change the final output — e.g. once every line has already been fully compressed or
explicitly protected. This never changes what Quor actually produces; it just avoids doing
pointless extra work on the way there.

<details>
<summary>Technical details</summary>

**Problem:** `Pipeline.execute()` always ran every configured stage for every filter, even once a
stage's own scan finds nothing left it could possibly change. Most filters are cheap enough that
this never mattered, but it's wasted work in principle, and the task asked for it to be addressed
as a pipeline-level optimization, not a per-filter one.

**Architecture finding, surfaced before any code was written (per the task's own "stop and explain
if the architecture can't support this cleanly" instruction — this did not rise to a blocking
conflict, but is exactly the kind of thing that instruction wants surfaced):** reading every
built-in stage's `apply()` in full revealed that `Decision.COMPRESS` is *not* engine-enforced
immutable the way `PROTECT` is — only `PROTECT` is restored by `Pipeline._enforce_protect`. Three
built-in stages (`group_repeated`, `max_tokens`, `remove_ansi`) apply their own `preserve_patterns`
pass with a condition of `decision is not PROTECT` rather than excluding `COMPRESS` too, so if one
of them were configured with `preserve_patterns` that happened to match an already-`COMPRESS` line,
that line would be promoted back to `PROTECT` and reappear in `render()`. No built-in filter
actually configures `preserve_patterns` on anything but `strip_lines` today (verified across every
`quor/filters/builtin/*.toml`), so this never fires in practice — but a naive "no KEEP lines left ⇒
safe to skip everything remaining" rule would have been provably unsafe for a hypothetical
project/user filter. `match_output` (whole-render pattern collapse, independent of any per-line
`Decision`) is a second, unrelated reason a blanket rule would be unsafe. See ADR-035 in
`docs/final/DECISIONS.md` for the full design-options writeup, including why a new `StageHandler`
Protocol field was rejected in favor of a hand-audited allowlist.

**Resolution:** `quor/pipeline/engine.py` — `Pipeline.execute()` gains an `early_exit: bool = True`
keyword-only parameter. Before each stage (and after each one runs), if the current mask has zero
`Decision.KEEP` lines remaining *and* every not-yet-run stage is both on a small, hand-audited
`_STAGE_TYPES_INERT_ON_DECIDED_LINES` allowlist (`remove_ansi`, `strip_lines`,
`deduplicate_consecutive`, `group_repeated`, `max_tokens`, `truncate_lines`, `regex_replace`,
`python_ast_summarize`, `code_ast_summarize` — deliberately excluding `match_output`) and configured
with an empty `preserve_patterns`, every remaining stage is marked `was_skipped=True` (with a
distinct `"early exit: ..."` skip_reason) without `can_handle()`/`apply()` ever being called.
`len(stage_results)` always still equals the configured stage count, exactly as it already does for
a `can_handle()`-False or raising stage. Third-party/plugin/`file://` stages are never eligible —
their `stage_type` is never in the allowlist, so the engine never has to vouch for code it hasn't
read. The skip-eligibility check itself is wrapped in `try`/`except`; any exception there falls back
to running the stage normally with a warning logged, satisfying the task's explicit "any
optimization failure must fall back to the existing execution path" requirement literally, not just
by construction.

`quor/filters/registry.py` — `FilterRegistry._run_pipeline()` gains a matching `early_exit`
parameter (default `True`). `apply()` (the real compression path — Bash/Read hooks, benchmarks,
`quor verify`) doesn't pass it, so it stays on. `trace()` (`quor explain`'s diagnostic stage-by-stage
view) explicitly passes `early_exit=False`, since that command's entire purpose is showing what
every configured stage does — an early-exited stage would show "skipped — early exit" instead of
its real per-stage line count, which is exactly the information `quor explain` exists to surface.
This is the one call-site change outside `engine.py`; it is plumbing (threading a boolean through),
not new optimization logic, which itself lives entirely inside `Pipeline.execute()` as required.

No new abstraction, metadata structure, or `StageHandler`/config field was introduced: the allowlist
reuses `StageHandler.stage_type` (already required by the Protocol) and `StageConfig.
preserve_patterns` (already a base-class field every stage config inherits). Zero stage
implementation files were touched. Zero filter `.toml` files were touched.

**Validation:**
- `ruff check quor/ tests/` clean. `mypy quor/` — Success, no issues, 67 source files.
- Full `pytest` (unit + integration, batched across 6 groups per the same local
  Quor-hook-intercepting-Quor's-own-shell-commands 25-second timeout artifact prior QB-005 phases
  already documented) — 0 failures.
- `quor verify` — 88/88 (unchanged; no filter's own `[[filter.tests]]` changed).
- Full benchmark suite (`python -m tests.benchmarks.run_benchmarks`) — 60/60 cases correct, 0 floor
  violations, 0 regressions against the committed baseline (identical token-savings totals to before
  this change: 9602 tokens saved, 35.3% overall).
- **New dedicated test suite, `tests/unit/test_early_exit.py`** (27 tests): the pure
  `_mask_fully_decided`/`_remaining_stages_are_skippable` predicates in isolation; `Pipeline.execute()`
  genuinely never invoking a skipped stage's `can_handle()`/`apply()` (proven via an
  out-of-band call log, not by expecting a raised exception — a raising stub would have its
  exception silently absorbed by the pipeline's own fail-open handling, indistinguishable from a
  real skip); every case early exit must *not* trigger (KEEP lines remaining, `preserve_patterns` on
  a remaining stage, `match_output` present, an unrecognized/plugin `stage_type`); the fail-open
  contract for a broken skip-predicate itself; and the core correctness property — every built-in
  filter's own inline `[[filter.tests]]` input produces byte-for-byte identical `apply()` output
  with `early_exit` forced on vs. off.
- **New, deliberately separate script, `tests/benchmarks/early_exit_analysis.py`** (not wired into
  the pytest gate, mirroring `ast_timing_analysis.py`'s QB-005E precedent): ran every one of the 60
  real benchmark corpus cases with `early_exit` on and forced off, confirmed all 60 produce
  byte-for-byte identical output, and measured wall-clock timing (median of 25 runs per case per
  variant). Results, reported honestly rather than oversold: early exit actually fires (skips ≥1
  stage) in only 2 of 60 cases (`mypy-repeated-type-error`, `mypy-distinct-errors` — both cases where
  `group_repeated` collapses everything before `max_tokens` runs). Aggregate timing delta across the
  full corpus is within measurement noise (roughly ±0.1–0.5%, no consistent direction run to run) —
  the checks the optimization adds are cheap, but so is nearly everything it might skip, given how
  rarely a mask becomes fully decided before the last stage.

**A structural limitation worth recording plainly:** `python_ast_summarize`/`code_ast_summarize` are
always the *first* stage in the filters that use them (`cat-python.toml`, `cat-javascript.toml`,
`cat-typescript.toml`) — and early exit only ever skips stages that haven't run *yet*. The single
most expensive operation in the AST-summarization filters (the actual parse) can therefore never be
skipped by this optimization, by construction, not by oversight. Early exit's real value is limited
to skipping cheap trailing bookkeeping stages (`strip_lines`/`dedup`/`max_tokens`) once the
heavy lifting is already done and happens to have consumed every line — a real but modest win on the
current filter set, not a transformative one.

**Trade-offs:**
- The hand-audited `stage_type` allowlist is a deliberate, narrow coupling of the otherwise
  stage-agnostic engine to specific built-in stage names — the only place in `engine.py` this
  happens. If a future built-in stage is added, or an existing one's `preserve_patterns` handling
  changes to reconsider already-`COMPRESS` lines, `_STAGE_TYPES_INERT_ON_DECIDED_LINES` must be
  reviewed by hand; it is not auto-derived from anything. This is documented prominently in
  `engine.py`'s own module docstring specifically so it isn't missed.
- `quor explain` deliberately does not benefit from this optimization at all (see above) — a
  conscious trade of a small, occasional diagnostic-command speedup for guaranteed, byte-for-byte
  unchanged trace output.

**Status:** Implemented and merged to `main` (branch `feature/qb-009-early-exit`, tracked as QB-036 per the numbering note above) — shipped in Quor **v0.4.0** (2026-07-11). *(Correction: this entry originally read "not committed"; verified against `CHANGELOG.md` and `git log` while restructuring this document.)*

</details>

---

#### QB-037 — Product polish pass: verify warning, init bug, hook health, gain UI

**Effort:** Medium · **Value:** Medium · **Category:** Bugfix / Product UX

A pre-release cleanup pass covering four things found during the AST-work stabilization: a stray
warning that turned out to already be fixed, a real bug in `quor init --claude` printing a message
it shouldn't, a shallow hook health check that only looked for a file on disk, and a `quor gain`
report that was accurate but harder to scan than it needed to be.

<details>
<summary>Technical details</summary>

**1. `quor verify` warning — investigated, not a bug.** Re-traced the exact execution path
(`engine.py`'s per-stage `warnings.warn()` → `FilterRegistry.apply()` → `run_tests()`, the only path
`quor verify` takes) and confirmed the earlier fix (this file's prior entry, "suppress expected
warnings during successful inline filter tests") already covers it. Reproduced `py -m quor verify`
and `python -m quor verify` fresh, in both Git Bash and native PowerShell — clean, 88/88, no
warning, every time. Checked for a shadow install (`pip show quor` → single editable install) and
for project/user-level filter overrides that could bypass the fixed code path — none exist. The
original report almost certainly predated this session's merge of the fix into `main`. No code
change.

**2. `quor init --claude` printing "Tee adaptive-disable state cleared." unconditionally — real
bug, fixed.** Root cause (previously diagnosed, now fixed): `init.py` called the Typer-decorated
`doctor()` function directly as plain Python, so `reset_tee` received the raw
`typer.Option(False, "--reset-tee", ...)` sentinel object (truthy) instead of its resolved default.
Fix: split `doctor()` into a thin Typer wrapper and a plain `_run_doctor(*, settings_path=None,
reset_tee=False)` function with real Python defaults; `init.py` now calls `_run_doctor()` directly.
Regression test added (`TestInit::test_does_not_print_reset_tee_message`) — fails on the pre-fix
code, passes on the fix.

**3. Hook configuration health — was file-existence-only, now version-aware.** New module
`quor/adapters/hook_manifest.py`: a declarative `ClaudeHookSpec` per hook (event, matcher, script
name, template) and a `HOOK_SPECS` tuple both `quor init --claude` and `quor doctor` iterate — one
manifest entry per hook, not two hand-copied function pairs. Reused QB-035A's existing multi-agent
design-doc conclusion (a declarative per-adapter hook list is the right shape) at V1 scope only —
Claude Code, no new adapter Protocol, no multi-agent (ANTI_GOALS.md #12 stays intact). Closed a real
gap: nothing previously verified that `settings.json` *actually references* Quor's hook — a script
could exist on disk from a stale/partial install while Quor was never wired in, and `doctor` would
still print "Hook script installed" ✓. New `_check_hook_registered` check closes this. New
`_check_hook_up_to_date` check compares a `# quor-hook-schema: N` line embedded in each generated
script (via `render_hook_script()`) against `spec.schema_version` — a new field on `ClaudeHookSpec`,
deliberately **not** `quor.__version__`. Corrected after initial review: comparing against the
package version would flag every installed hook as outdated on every Quor release, even releases
that never touch the hook's template — `schema_version` only changes when a hook's own definition
(template body, registration shape) actually does, so most Quor version bumps never prompt a
reinstall. "Exists and is registered" is not the same claim as "matches its current definition,"
which is what this check answers. A future hook needs one `ClaudeHookSpec` entry (with its own
`schema_version`) to get all three generic checks and install support for free; only its behavioral
(roundtrip) check still needs hand-written code, since proving a hook actually compresses inherently
requires a hook-specific synthetic payload — that part was never claimed to generalize.
Found and fixed a real, related UX bug along the way: `doctor`'s check-detail lines could
word-wrap mid-phrase (e.g. splitting `` `quor init --claude` `` across a line break) when a long
temp-directory path pushed the line past the console width — fixed by printing with
`soft_wrap=True`, the same pattern `quor gain` already used for its own long text.

**4. `quor gain` UX — dashboard redesign, no calculation changed.** Considered three layouts
(headline-first only; two-zone notices/statistics dashboard; single-panel scorecard) and chose the
two-zone dashboard: it's the only one that actually satisfies "separate informational notices from
statistics" (the others interleave or cram everything into one box), while keeping every existing
number visible (unlike the scorecard, which risks losing Top savings detail). Notices — Read-hook
coverage gaps, recovery-footer overhead — now print together under one `NOTICE` header before any
statistic. The savings headline (`YOU SAVED`/`NET TOKENS`) now leads the statistics section instead
of appearing after three stacked mini-tables; those three tables (usage, tokens, and the
gross-savings breakdown) collapsed into one compact table. Long explanatory paragraphs (the
char/4-approximation footnote, the negative-row explainer) became one to two short lines instead of
multi-sentence prose. The `±20%` uncertainty label stays directly on the headline number
(ANTI_GOALS.md #24), not only in a footnote. Every existing `quor gain` unit test passes unchanged
against the new layout (same numbers, same required substrings, just rearranged) — three new tests
added specifically for the redesign: headline-before-stats ordering, both notices grouping under one
`NOTICE` header, and no `NOTICE` header printing at all when there's nothing to report.

**Files changed:** `quor/adapters/hook_manifest.py` (new), `quor/adapters/claude.py` +
`quor/adapters/claude_read.py` (version marker added to hook templates), `quor/cli/commands/init.py`
(manifest-driven install, `_run_doctor` fix), `quor/cli/commands/doctor.py` (manifest-driven checks,
`_run_doctor` split, `soft_wrap` fix), `quor/cli/commands/gain.py` (layout redesign), plus test
updates/additions across `tests/unit/test_cli.py`, `tests/unit/test_adapters.py`,
`tests/unit/test_adapters_read.py`, and new `tests/unit/test_hook_manifest.py`.

**Status:** Implemented and merged to `main` (PR #48, `feature/qb-037-product-polish-pass`) — shipped in Quor **v0.4.0** (2026-07-11). *(Correction: this entry originally read "not committed"; verified against `git log` while restructuring this document.)*

</details>

---

### Low Value

*Still real, shipped work — mostly documentation, process, and engineering-hygiene items whose value
is in reducing future risk rather than moving the compression numbers directly.*

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

#### QB-017 — Make the "tokens saved" number always trustworthy ("Gain Hardening")

**Effort:** Small–Medium · **Value:** Low · **Category:** Metrics / Observability

Full close-out of everything left open around `quor gain`, done as one cohesive pass before any
major new feature (QB-007) begins. Covers four things: (1) an audit confirming the project
case-sensitivity/sibling-leakage fix (QB-018) has no remaining gaps, (2) an investigation into every
negative-token row to rule out a second, hidden accounting bug beyond the already-known recovery
footer, (3) a redesign of `quor gain`'s CLI output so it explains *why* a negative net can happen
and whether it matters, and (4) the regression tests locking all of the above in.

<details>
<summary>Technical details</summary>

**1. Case-sensitivity / prefix-matching audit (items 1–2).** QB-018 had already fully implemented
and tested this (`normalize_project_path()`, the precomputed `project_key_normalized` column with
lazy backfill, LIKE-based subdirectory matching with `%`/`_` escaping, degenerate-key rejection).
Audited rather than reimplemented, per the decision to reuse existing work (CLAUDE.md Rule 4) —
found no gap in the algorithm itself. Closed four previously-untested *combinations* of already-correct
behavior: subdirectories 3+ levels deep, case-insensitivity composed with sibling-leakage exclusion,
case-insensitivity composed with subdirectory inclusion, and a trailing-slash query path exercised
end-to-end through `query_gain()` (not just the unit-level `normalize_project_path()` test that
already existed). All four passed against the unmodified implementation — confirms no regression,
adds coverage `backlog.md`'s QB-018 write-up didn't explicitly call out.

**2. Negative-token-row investigation (item 4).** Read every pipeline stage
(`quor/pipeline/stages/*.py`) to check whether anything besides the tee footer (ADR-023) could make
`final_tokens` exceed `original_tokens`. Finding: `truncate_lines`, `max_tokens`,
`strip_lines`/`deduplicate_consecutive`/`remove_ansi`/`python_ast_summarize` can only ever remove or
cap content. `group_repeated` appends a short `" (×N)"` suffix while removing the rest of a run —
theoretically capable of a net increase only if the matched lines are shorter than the suffix
itself, which none of the shipped filter patterns (`npm WARN deprecated`, `L:C  error`, etc.) permit
in practice. `regex_replace` and `match_output` — the two stages whose *configured* replacement text
could in principle be longer than what it replaces — are not wired into any shipped built-in filter
today. This is now locked in by a real regression test, not just reasoning:
`tests/unit/test_filters.py::TestFilterNeverExpandsOutput` runs every built-in filter's own
`[[filter.tests]]` corpus through the real, unmocked pipeline and asserts none of them ever grow.
**Conclusion: no second accounting bug found.** Negative rows are attributable to the tee recovery
footer (dominant, already-documented cause) and, in principle, third-party `PRE_FILTER`/
`POST_FILTER` plugins that add content (no plugin ships by default). Per the original scope
decision, tracking itself (`original_tokens`/`final_tokens`/`tokens_saved`) is unchanged.

**3. `quor gain` CLI redesign (item 3).** `GainReport` (`quor/tracking/db.py`) gained three
presentation-only derived fields, computed by `query_gain()`'s existing SQL aggregation — no new
tracking column, no schema migration, no change to what `_track()` writes per invocation:
- `gross_savings` — sum of `(original − final)` over rows where it's positive
- `gross_overhead` — sum of `(final − original)` over rows where it's positive
- `negative_row_count` — count of rows where `final > original`

`gross_savings − gross_overhead == tokens_saved` always holds exactly (verified by test) — this is
a decomposition of the existing net figure, not a new measurement. `quor gain`
(`quor/cli/commands/gain.py`) now shows a "Compression achieved" / "Recovery/overhead" breakdown
plus a plain-language explanation, but **only when `negative_row_count > 0`** — the common
all-positive case is untouched, so the redesign explains the exception instead of permanently
cluttering the normal report. The explanation's closing sentence adapts to context: reassurance
("doesn't affect the other commands — nothing to fix") when the overall net is still positive, or a
concrete, already-existing lever (`tee = false` in a filter's config, ADR-023) when the window's net
is genuinely negative. "Top savings" percentages now divide by `gross_savings` instead of the net
`tokens_saved` — found and fixed during the redesign: dividing by net could previously produce a
distorted or even >100% figure for a filter that saved a lot while an unrelated row elsewhere had
overhead.

**Found and fixed during implementation:** the first draft of the negative-row explainer pluralized
"command(s)" against `negative_row_count` instead of `total_invocations` ("1 of 2 command" instead
of "1 of 2 commands") — caught by
`test_mixed_rows_shows_compression_breakdown_with_correct_values`, fixed, verified. Also: the
explainer's long paragraph is printed with Rich's `soft_wrap=True` — without it, Rich's default
word-wrap at the terminal width could insert a line break mid-sentence, which both looks worse in a
real terminal and made an existing test's substring assertion fragile against terminal width.

**4. Tests.** New: 4 project-identity combination tests (`tests/unit/test_tracking.py`), 4
gross-savings/overhead decomposition tests (`tests/unit/test_tracking.py`), 1 filter-corpus
never-expands invariant test (`tests/unit/test_filters.py`), 4 new `quor gain` CLI tests covering
the breakdown appearing/not appearing, correct values, the reassurance-vs-lever wording split, and
the gross-vs-net percentage fix (`tests/unit/test_cli.py`). 13 new tests total.

**Desired outcome, restated from the original entry (now met):** `quor gain` distinguishes genuine
compression savings from overhead rather than netting them silently — achieved via display-time
decomposition of existing columns, avoiding the schema/migration cost the original entry flagged as
the blocking "data-model decision."

**Status:** Resolved — implemented on `feature/qb-017-gain-hardening`. Full `pytest tests/`, `quor
verify`, `ruff check quor/ tests/`, and `mypy quor/` all clean. `RELEASE_CRITERIA.md`'s **B-S01**
gate (every `quor gain` output carries the ±20% uncertainty label) remains satisfied — unaffected by
this change, still not formally re-checked since Beta hasn't been walked yet (QB-028).

</details>

---

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

#### QB-063 — Narrow yarn/bun peer-dependency warning grouping (QB-059 follow-up)

**Effort:** Small · **Value:** Low · **Category:** Bug Fix

QB-059 fixed pnpm's `group_repeated` peer-dependency-warning pattern from an over-broad bare
`^\s*warn\b` prefix to the specific "Issues with peer dependencies" shape, but left yarn's
`^warning\b` and bun's `^\s*warn:` blocks with the same bug: either prefix shape-matches *any* two
consecutive warning lines regardless of type, silently merging non-fungible warnings (license
notices, engine-incompatibility, duplicate-dependency notices, ...) into a peer-dependency count.

<details>
<summary>Technical details</summary>

**Problem:** yarn's and bun's `group_repeated` patterns in `node.toml` were bare prefix matches,
the same class of bug QB-059 already fixed for pnpm specifically.

**Resolution:** Narrowed yarn's pattern to `(?i)^warning\b.*has unmet peer dependency` and bun's to
`(?i)^\s*warn:\s*incorrect peer dependency` — mirroring pnpm's QB-059 fix exactly.

**Status:** Resolved — implemented on `fix/qb-063-yarn-bun-peer-dependency-narrowing`.

</details>

---

### Historical (superseded)

*Kept for the record — not resolved work in its own right, but the original request that later,
completed items grew out of.*

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
