# Investigation: QB-047 — Real-World Benchmark Corpus & Continuous Tracking

> Status: **investigation only, per this ticket's own instructions.** No code changed. Every claim
> below is either a direct citation of existing source (file:line) or explicitly marked as
> inference. Where backlog.md's own prose disagrees with what the code actually does, that
> discrepancy is called out rather than silently resolved in either direction.

---

## Executive summary

QB-047 bundles two asks that turn out to have very different amounts of work left:

1. **Release-over-release trend tracking** for the benchmark corpus is **fully built and
   completely unused.** `tests/benchmarks/history.py` (QB-051) implements the entire format,
   append/dedup logic, and a regression comparator; `run_benchmarks.py --history` wires it to the
   CLI. `history.json` has **never been created** — no such file exists anywhere in the repo, and
   `git log --all` finds zero commits ever touching it. The gap is CI/process wiring, not code.

2. **A "real-world" benchmark corpus** (actual anonymized user command output, not hand-written
   samples) is **not achievable from any infrastructure that exists today.** `TrackingDB`
   (`quor/tracking/db.py`) — the only telemetry Quor has — never stores command output text, by an
   explicit, currently-enforced anti-goal (`docs/final/ANTI_GOALS.md` #4). It stores token *counts*
   only. There is no code path anywhere in the repository that reads real command output content
   after the fact; `quor discover` (QB-034), the one proposed feature that would need to parse real
   session logs, does not exist. Building a real-content corpus requires genuinely new,
   separately-scoped, opt-in infrastructure — not an extension of `TrackingDB`.

Separately, and unprompted by anything in this ticket's own scope list, this investigation found
that **QB-054 ("telemetry-driven optimization") is already fully implemented and shipped**
(`quor/analytics/filter_baseline.py`, `filter_divergence.py`, `filter_history.py`,
`filter_report.py`, wired into `quor gain --filters` and `quor doctor`) — while `backlog.md`'s own
QB-054 entry still reads "Status: Proposed. Not scoped or implemented." This closes much of what
QB-047's "is filter X improving?" and "standing divergence detection" asks want, just scoped to
live, per-machine real usage rather than a committed, cross-release corpus history. See
[§15](#15-final-recommendation) and the [unrelated issues](#unrelated-issues-found) section.

---

## 1. Current benchmark architecture

### Entry point and call graph

```
python -m tests.benchmarks.run_benchmarks   (run_benchmarks.py::main)
  │
  ├─ run_all(manifest_path, benchmarks_dir)             [benchmark_runner.py:313]
  │    ├─ load_manifest(manifest_path)                  [benchmark_runner.py:179]
  │    │    └─ tomllib.load(manifest.toml) → list[BenchmarkCase]
  │    └─ for each case: run_case(case, benchmarks_dir) [benchmark_runner.py:236]
  │         ├─ _builtin_registry()                      [benchmark_runner.py:204]  (lru_cache singleton)
  │         │    └─ FilterRegistry(skip_user=True, skip_project=True)
  │         ├─ (if .docx/.pdf) quor.pipeline.extract.registry.extract()
  │         │    else: sample_path.read_text()
  │         ├─ registry.find(case.command)  → filter_config   (real dispatch lookup)
  │         ├─ registry.apply(filter_config, original) → final  (real pipeline execution)
  │         ├─ registry.trace(filter_config, original, track_tokens=True)  (QB-039, second read-only call)
  │         ├─ quor.tracking.db.count_tokens(original / final)
  │         └─ quor.pipeline.tee.content_hash(original / final)  (read-only, never write_tee())
  │
  ├─ aggregate(results)                                  [benchmark_runner.py:351]
  │    └─ _group_by(results, key=category|ecosystem)     [benchmark_runner.py:325]
  │
  ├─ load_baseline(args.baseline) → dict[id, dict]        [benchmark_runner.py:386]
  ├─ compare_to_baseline(results, baseline, threshold_pp) [benchmark_runner.py:396]
  ├─ (--update-baseline) save_baseline(results, path)     [benchmark_runner.py:438]
  │
  ├─ write_json_report(...) / write_markdown_report(...)  [report.py:23, report.py:78]
  ├─ (--analytics) render_analytics_report(...)           [analytics_report.py:99]
  │    ├─ collect_stage_stats(results) → quor.analytics.stage_stats.StageStatsCollector
  │    └─ quor.analytics.effectiveness.classify(stage_stats)
  └─ (--history) build_entry(...) / append_entry(...) / render_history_table(...) / detect_regression(...)
       [history.py:77, 66, 99, 114]
```

A second, independent entry point runs automatically: `tests/benchmarks/test_benchmarks.py` builds
`_CASES`/`_RESULTS` at **module import time** (lines 32–33: `_CASES = load_manifest()`;
`_RESULTS = {case.id: run_case(case) for case in _CASES}`), then exposes three parametrized
correctness tests per case (`test_matched_expected_filter`, `test_required_content_survives`,
`test_meets_min_reduction_floor`) plus one aggregate `test_no_regression_against_baseline`. This
file runs under plain `pytest tests/`, which `.github/workflows/ci.yml:46` invokes
(`pytest tests/ --cov=quor --cov-report=term-missing`) — so **correctness, floor, and baseline
regression are genuinely CI-gated on every change**, confirmed directly in `ci.yml`.

What is **not** in this call graph and **not** CI-gated: JSON/Markdown report writing, the
`--analytics` report, and the `--history` mechanism. All three are `run_benchmarks.py` CLI-only
paths, run manually. No `.github/workflows/*.yml` file references `benchmark` at all beyond the
plain `pytest tests/` invocation (confirmed by grep across all five workflow files — zero matches).

### Isolation from production code

The runner only ever calls Quor's real public surface — `FilterRegistry.find()`/`.apply()`/
`.trace()`, `count_tokens()`, `content_hash()` — never a modified/patched/special-cased version
(`benchmark_runner.py:1–13`'s own module docstring, corroborated by `tests/benchmarks/README.md`
lines 10–22 and CLAUDE.md's own review-checklist wording). Nothing under `quor/` imports from or
knows about `tests/benchmarks/`.

## 2. Current benchmark data model

| Artifact | Owner / who writes it | Who reads it | Generated or hand-maintained | Git-tracked? |
|---|---|---|---|---|
| `manifest.toml` | Contributors, by hand (`tests/benchmarks/README.md` §"Adding a new benchmark case") | `load_manifest()` (`benchmark_runner.py:179`) | **Hand-maintained** | Yes |
| `samples/<category>/*` | Contributors, by hand or captured-then-sanitized | `run_case()` via `sample_path.read_text()` | **Hand-maintained** | Yes |
| `baseline.json` | `save_baseline()` (`--update-baseline`), reviewed like any diff | `load_baseline()`/`compare_to_baseline()`; also `quor/analytics/filter_baseline.py` (QB-054, reads it directly as ground truth) | **Generated**, committed | Yes |
| `results/benchmark-results.json`, `results/benchmark-report.md`, `results/analytics-report.txt` | `run_benchmarks.py` on every manual run | Humans (review); `docs/BENCHMARKS.md` was hand-written by reading these once | **Generated** | **No** — `.gitignore:32` (`tests/benchmarks/results/`) |
| `history.json` (`tests/benchmarks/history.py`) | `append_entry()` via `--history` | `load_history()`/`detect_regression()`/`render_history_table()` | Generated — **but has never actually been generated once** (see below) | Would be, if it existed |
| `docs/BENCHMARKS.md` | A human, once, reading a specific run's output | Anyone consulting benchmark numbers; cited from `README.md` | **Hand-written prose**, quoting a specific run's numbers verbatim, not regenerated | Yes |
| `README.md`'s benchmark claims (root) | A human (QB-085) | End users, prospective adopters | **Hand-written**, not regenerated | Yes |

**`history.json` does not exist.** Confirmed three ways: (1) `find . -iname history.json` returns
nothing; (2) `git log --all --oneline -- tests/benchmarks/history.json` returns zero commits;
(3) `tests/benchmarks/history.py`'s own module docstring (lines 28–32) states it plainly:
"Explicitly NOT wired into any CI workflow by this task... `detect_regression()` below is the pure
function such a job would call; `run_benchmarks.py --history` is the local, manual equivalent in
the meantime." The format, the CLI flag, and the regression comparator are all real, working code
— `--history` has simply never been run and its output committed, by anyone, ever.

**Manifest/baseline have already drifted from each other.** `manifest.toml` currently has **153**
`[[case]]` entries (`grep -c '^\[\[case\]\]'`); `baseline.json` currently has **145** result entries.
Eight cases exist in the manifest with no baseline entry yet — they will report as `status: "new"`
on the next run, not a failure, but it means someone added benchmark cases without running
`--update-baseline` and committing the result yet. This is exactly the kind of small, silent drift
`docs/BENCHMARKS.md` itself flags happening around it (see next section).

## 3. Current limitations (each backed directly by code)

- **Point-in-time only, no populated trend history.** `history.json` is designed but empty/absent —
  see §2. The only regression signal that exists is "this run vs. the immediately-prior committed
  baseline" (`compare_to_baseline`, `benchmark_runner.py:396`), exactly the gap backlog.md's QB-047
  entry (line 1334) describes.

- **The corpus is entirely hand-written, and says so itself.** `tests/benchmarks/README.md:24-34`
  states the framework is "fully data-driven" with no hardcoded filter knowledge — true, but
  orthogonal to *realism*. The same file's own header comment on `manifest.toml` (lines 1-13)
  describes every case as authored by a human choosing `category`/`ecosystem`/`command` values.
  `docs/BENCHMARKS.md:62-63` states this explicitly: "This is a hand-curated corpus, not a sample of
  real usage."

- **No distinction exists in the data model between synthetic and real-derived samples**, because
  no real-derived sample has ever existed. `BenchmarkCase` (`benchmark_runner.py:64-82`) has no
  provenance field of any kind.

- **Demonstrated, code-confirmed benchmark-vs-real divergence**, not a hypothesis:
  `docs/BENCHMARKS.md:154-160` tabulates five filters where a 2026-07-15 one-off manual query
  against the live tracking DB diverged sharply from the benchmark corpus's own numbers (`git-log`
  40.8% bench vs. 83.8% real; `git-status` 52.7% vs. 6.6%; `pytest` 39.8% vs. 12.9%; `mypy` 46.1%
  vs. **-41.2%**; `npm` 43.2% vs. **-9.1%**). This was a manual SQL query at the time it was
  written — but see [§6](#6-existing-measurement-accuracy) and the unrelated-issues section: this
  exact comparison is now a standing, automated feature (QB-054), just not reflected in this
  document's own "Future benchmark roadmap" section, which still lists it as proposed.

- **The corpus has already gone stale relative to its own describing document.**
  `docs/BENCHMARKS.md` (generated 2026-07-15) describes "60 hand-authored cases across 7 ecosystems
  and 26 filter categories" (line 48). The manifest now has 153 cases (§2). `README.md:45` cites
  "35.9% average token reduction across Quor's own **127**-case benchmark suite" — a third,
  different, also-stale number, from a run some time after the 60-case snapshot and before the
  current 153-case state. None of these three documents' numbers agree with each other or with the
  current repo state.

- **Approximate token counts.** `count_tokens()` (`quor/tracking/db.py:180-182`) is
  `ceil(len(text)/4)`, documented everywhere (including `docs/BENCHMARKS.md:17-18`) as ±20%, not a
  real tokenizer. This affects every benchmark number, real or synthetic, equally — not a QB-047-
  specific gap, but relevant to any claim about corpus "realism" or precision.

- **No CI wiring for anything except correctness/floor/regression.** Confirmed in §1: `--history`
  and `--analytics` are CLI-only. A regression that is individually under the 2.0pp threshold
  (`DEFAULT_REGRESSION_THRESHOLD_PP`, `benchmark_runner.py:60`) but persistent across several
  releases is invisible today — exactly because nothing ever calls `--history` to give
  `detect_regression()` more than zero or one data point to compare.

- **`tests/benchmarks/history.py` has zero test coverage.** Confirmed by grep: no test file in
  `tests/` imports from `tests.benchmarks.history` except `run_benchmarks.py` itself. This is dead,
  untested code paths in a project whose own `ANTI_GOALS.md`/CLAUDE.md hold a hard line on test
  coverage for shipped features (Rule 1, "Mandatory Engineering Rules"). Contrast with QB-054's
  equivalent real-usage modules, which do have `tests/unit/test_filter_analytics.py` (29 test
  methods) — see the unrelated-issues section.

## 4. Real-world corpus investigation

**Does `TrackingDB`/`InvocationRecord` already capture enough to build a real corpus? No — by
design, not by oversight.**

`InvocationRecord` (`quor/tracking/db.py:73-100`) has exactly these fields: `command`,
`project_path`, `original_tokens` (an **int**), `final_tokens` (an **int**), `filter_name`,
`was_passthrough`, `duration_ms`, `recorded_at`, `schema_version`. The SQLite schema
(`quor/tracking/schema.sql:15-27`) mirrors this exactly — ten columns, no text-content column, no
hash column of any kind. `track_invocation()` (`db.py:495-529`), the single shared recorder every
producer (Bash dispatcher, Read hook) calls, builds this record from `count_tokens(original)`/
`count_tokens(filtered)` — the *counts*, never the strings themselves are retained past that one
function call. There is no code anywhere that persists `original`/`filtered` text to disk.

This means: **no amount of querying `TrackingDB` can ever produce a real command-output sample.**
The data literally does not exist there. `query_gain()`, `query_recent_invocations()`, and
`query_filter_analytics()` (`db.py:608, 832, 1042`) can only ever answer "how much" and "how often"
questions, never "what did the output actually look like." `RecentInvocation`
(`db.py:819-830`)'s own docstring says this outright: "Metadata only — the same columns
`InvocationRecord` already writes, never the actual command output content (ANTI_GOALS.md #4)."

**Is there other existing infrastructure that reads real content?** Investigated directly: `quor
discover` (QB-034) is the one proposed feature whose entire premise is parsing real Claude Code
session logs (JSONL transcripts) to find commands Quor never saw. It does not exist —
`find quor -iname "*discover*"` and a repo-wide grep for a `discover` CLI command both return
nothing. backlog.md's own QB-034 entry (lines 1537-1579) confirms: "Status: Proposed. Not scoped or
implemented." Its own text (line 1571) already anticipates QB-047 needing the same session-log-
parsing capability: "both need to parse real Claude Code session logs."

**One architecturally important fact, not previously connected in backlog.md:** Claude Code's own
session transcripts (JSONL files it writes to the user's local disk, independent of Quor) already
contain the real, raw tool outputs Quor would want to sample from — Quor did not write them and
does not need to invent a new capture mechanism to get access to real content; it would need to
*read* something that already exists locally, once, with explicit consent, never automatically or
in the hot path. This is a fundamentally different (and less risky) starting point than "add new
telemetry to `TrackingDB`" — but it is still a wholly new code path (a JSONL transcript parser),
has never been built, and touches unfiltered real content the moment it's read, which is exactly
why backlog.md's own QB-047 entry (line 1348) flags it: "Open question: privacy/consent model...
this is explicitly not 'just add telemetry.'"

**Conclusion:** Building a real-world corpus requires new, purpose-built infrastructure — a local
session-log reader plus an explicit review/consent/redaction step before any sample could be
contributed anywhere — not an extension of `TrackingDB`. `TrackingDB` itself should not change at
all for this purpose; changing it would violate the anti-goal that makes it safe today (§5).

## 5. Privacy investigation

This is the highest-risk part of QB-047, and the codebase is unusually explicit about it.

**`docs/final/ANTI_GOALS.md` #4** (lines 42-48): *"Never store, transmit, or log command output
content. The SQLite database and JSONL file record: command name, project path, token counts,
filter name, duration, mode, timestamp. They do not record the actual content of command outputs...
If a feature requires storing command output text centrally, it is an anti-goal."* (Note: this
text still mentions "the JSONL file" — QB-070 removed dual JSONL persistence entirely, per
`db.py:15-22`'s own historical note. This line of ANTI_GOALS.md is itself stale; see
unrelated-issues.)

**`docs/final/ANTI_GOALS.md` #5** (lines 50-54): *"Never implement telemetry, analytics, or usage
reporting without explicit opt-in. Quor collects no usage data by default... If a future maintainer
wants to add opt-in telemetry, it must be: explicitly documented, off by default, removable with
one config change, and never include command output content."*

Checking each specific question this ticket asks, directly against the schema and code:

| Question | Answer | Evidence |
|---|---|---|
| Does `InvocationRecord` store original text? | **No.** | `db.py:73-100` — no such field |
| Does it store filtered text? | **No.** | same |
| Does it store commands? | **Yes** — the literal command string (e.g. `"git status"`, or for Read-hook rows, `"Read: {file_path}"`, per `RecentInvocation`'s docstring and `db.py:782-783`). | `db.py:77`, `schema.sql:17` |
| Does it store filenames/paths? | **Yes** — `project_path` (full project directory) always; `command` for Read-hook rows embeds the file path. A Bash command's own arguments (e.g. `grep SECRET_KEY .env`) can also embed a path or, in principle, a literal argument value. | `db.py:78, 92` |
| Do hashes exist? | **No** — no hash column anywhere in `schema.sql`. (Note: `content_hash()` exists in `quor/pipeline/tee.py` and is used read-only by the *benchmark runner itself* (§1) to simulate tee — it plays no role in `TrackingDB`.) | `schema.sql:15-27` |
| Does anonymization already exist? | **No** — there is no redaction/anonymization step anywhere in the write path; `command`/`project_path` are written verbatim. | `db.py:460-487` (`_stage_sqlite_insert`) |
| Do compression summaries contain recoverable data? | **No** — `GainReport`/`FilterUsage`/`FilterAnalyticsReport` (all in `db.py`) are pure aggregates over integers (token counts, durations, counts) — no per-row content is exposed by any query function. | `db.py:104-172, 991-1029` |

**What could safely become a benchmark sample today, using only existing infrastructure?**
Nothing — because nothing existing captures content at all (§4). The `command` string and
`project_path` stored in `quor.db` are themselves already a real, if narrow, privacy surface (a
company/project directory name, a full command line that could embed a secret in an argument or a
proprietary path) — but they are *metadata*, not the command-output samples a benchmark corpus
actually needs. Even if one wanted to build a corpus purely from `command` strings (e.g. "these are
the shapes of commands real users actually run"), that is a different, much smaller ask than "real
command *output* samples," and even that would need the same opt-in/consent treatment
ANTI_GOALS.md #5 already requires for any new telemetry.

**What already solves part of the problem?** The tee cache (`quor/pipeline/tee.py`) already stores
raw output content — but explicitly "locally and under the user's control... never transmitted,
never indexed, never inspected by Quor's tracking system" (ANTI_GOALS.md #46). It is the closest
thing in the codebase to "real content is sometimes retained," and it is architected specifically
to never leave the user's machine or feed any aggregate system. Any real-corpus design should study
tee's isolation model (local-only, no aggregation, no transmission) as the precedent for how a
future opt-in sample-review step should also behave, rather than inventing a new trust model from
scratch.

**Bottom line:** nothing today can safely become a benchmark sample without new, explicit,
off-by-default, user-reviewed-before-leaving-the-machine infrastructure. This is fully consistent
with backlog.md's own framing of QB-047 as needing "real product and legal thought" before
implementation (line 1348) — this investigation found nothing that would let a first cut skip that
step.

## 6. Existing measurement accuracy

Every place benchmark/gain percentages are surfaced, checked directly:

- **`README.md:11`** — "Measured across a 127-case real-world benchmark suite, CI-gated on every
  single change." Two separate inaccuracies, confirmed directly:
  - *"real-world"* — the corpus is hand-authored, per the benchmark suite's own README
    (`tests/benchmarks/README.md`) and `docs/BENCHMARKS.md:62-63`'s explicit "not a sample of real
    usage." Calling it real-world is not supported by the code.
  - *"127-case"* — stale. The manifest currently has 153 cases (§2/§3). The *correctness/floor/
    regression* half of "CI-gated on every single change" **is** accurate (confirmed via
    `ci.yml:46` + `test_benchmarks.py`'s module-level execution), but the case count quoted
    alongside it is wrong regardless.
- **`README.md:45`** — "35.9% average token reduction across Quor's own 127-case benchmark suite" —
  same stale count, and the headline percentage is quoted as a bare number without the ±20%
  uncertainty band `quor gain`/`quor dashboard` are required to show live (see below) — the caveat
  exists in prose elsewhere on the same page (line 67: "always shown with an honest ±20%
  uncertainty band") but is not attached to this specific static claim.
- **`docs/BENCHMARKS.md`** — dated/generated 2026-07-15, describes the 60-case corpus, and is
  explicit and honest about its own limitations (§"Known benchmark limitations", lines 182-205) —
  including "Not wired into CI" and "No sustained trend view yet." This document does the most
  correct job of any of the three of *not* overclaiming representativeness — but it is now stale
  against the current 153-case manifest, and its own "Future benchmark roadmap" (line 217-219)
  still lists QB-054 as "proposed but not yet scoped or implemented," which is no longer true (see
  unrelated issues).
- **`quor gain` / `quor dashboard`** — live, real per-project numbers, always rendered with the
  ±20% estimate label (`dashboard.py:64`: "Token counts are estimated via the char/4 approximation,
  ±20%..."). These are the one place in the whole system where "real-world" numbers are actually
  shown, correctly labeled as estimates, and never conflated with the benchmark corpus.
- **`quor gain --filters` / `quor doctor`** — QB-054's divergence report (`filter_report.py`,
  `render_divergence`) explicitly separates "real" from "benchmark" numbers side-by-side per
  filter, with real usage never presented as if it came from the corpus or vice versa. This is the
  one surface in the codebase that already does exactly what §6 of this ticket is checking for —
  correctly.

**Conclusion:** benchmark percentages are being presented as more representative of real-world
performance than they are in exactly two places — `README.md`'s two headline claims — and both are
also independently stale on the underlying case count. `docs/BENCHMARKS.md` itself, and everything
under `quor gain`/`quor doctor`, already draw the real/benchmark distinction correctly.

## 7. Architecture options

### Option A — Minimal extension of the current benchmark system

Wire the already-built-and-tested `--history` mechanism into the release process (a step in
`release.yml`, or a manual step added to CLAUDE.md's existing "Release Readiness Checklist"), and
regenerate `docs/BENCHMARKS.md`/fix `README.md`'s stale claims as a one-time and then per-release
task.

- **Pros:** Reuses 100% existing, already-written code (`history.py`, `run_benchmarks.py
  --history`). Zero new privacy surface — nothing about this touches real user data. Closes a gap
  that is purely "designed but never turned on." Directly answers backlog.md's own "even a simple
  committed CSV/JSON" ask (line 1340).
- **Cons:** Does not address corpus *realism* at all — the corpus stays 100% hand-authored. Doesn't
  by itself answer "is filter X improving in production" — that's a separate, already-solved
  problem (QB-054, see unrelated issues) that lives in a different data store (per-machine, not
  per-release).
- **Migration cost:** Low. A CI/release-process change plus a documentation regeneration pass; no
  new modules.

### Option B — Separate historical benchmark database

Replace `history.json`'s one-row-per-release aggregate with a richer time-series store (e.g. one
row per `(release, case_id)` rather than per-release rollup), enabling true per-case trend queries
instead of only an overall/per-ecosystem percentage.

- **Pros:** Would answer finer-grained questions ("is `git-diff-large-refactor-many-files`
  specifically regressing across 5 releases") that a single aggregate `history.json` entry cannot.
- **Cons:** New infrastructure built from scratch, with no evidence yet that it's needed — there is
  currently *zero* history of any kind (§2), so building a richer store before the simple one has
  ever been populated once is solving a problem that hasn't been observed yet. Raises real
  questions (where does it live — committed binary, external service, SQLite alongside `quor.db`?)
  that the existing git-diffable JSON format avoids entirely.
- **Migration cost:** Medium, and there is no existing data to migrate (`history.json` has never
  been populated — see §2), so "migration" here is really "build in parallel," not "convert."

### Option C — Real-world corpus generated from TrackingDB analytics

As literally named in this ticket. **Not buildable as scoped**, per §4/§5: `TrackingDB` contains no
content, only counts and metadata, by an enforced anti-goal. There is no analytics transform that
can conjure command-output text out of integers. This option, taken literally, requires reframing
before it can even be attempted.

- **If reframed** as "new, opt-in, local-only capture of real content, reviewed by the user before
  any sample leaves their machine, informed by QB-034's session-log-parsing groundwork": this
  becomes buildable, but as a **new, separate subsystem**, not an extension of `TrackingDB` (which
  should not change at all — see §5).
- **Pros (reframed):** The only path that produces genuinely real sample content.
- **Cons (reframed):** Highest effort of every option. Needs new consent/preview/redaction UX (no
  redaction mechanism exists anywhere in the codebase today). Needs curation before any sample
  could ship inside a corpus redistributed to other users — a contributed sample may contain another
  organization's proprietary code, which a receiving Quor maintainer cannot simply merge in without
  review. Introduces sample-selection bias (only contributors' usage patterns represented).
  Backlog.md's own product-owner note (line 1348) already flags this needs "real product and legal
  thought" before implementation — this investigation found nothing that changes that.
- **Privacy implications:** Severe if built casually; addressable if built deliberately (opt-in,
  off by default, explicit per-sample review before transmission, following the tee cache's
  existing "local, never transmitted, never inspected by tracking" precedent — §5).
- **Migration cost:** High.

### Option D — Split the ticket (recommended framing, not a fourth mechanism)

The investigation's own evidence argues against treating "trend tracking" and "corpus realism" as
one architecture decision — they have wildly different cost/risk profiles and one of them
(tracking) is already 95% built. Recommended split:

- **D1 (= Option A):** Ship release-tracking now. Near-zero cost, zero new privacy surface, closes
  an already-built-but-dormant gap.
- **D2 (evidence-directed hand-curation, new but low-risk):** Use QB-054's *already-shipped*
  `quor gain --filters`/`quor doctor` divergence output (real-vs-benchmark comparison, live today —
  see unrelated issues) to decide *which* new hand-written benchmark samples to add next, rather
  than guessing. This is exactly backlog.md's own recommendation ("a first slice... specifically
  targeting git-diff, generic, and config-file samples," line 1316-1318) — it only requires
  following the existing "Adding a new benchmark case" process (`tests/benchmarks/README.md`), aimed
  by real evidence instead of intuition. Zero new privacy surface — no real user content is ever
  touched; a human still hand-writes/sanitizes every sample, same as today.
- **D3 (deferred, separately gated):** Genuine opt-in real-sample contribution (Option C, reframed)
  — pursued only as its own, later, explicitly product+legal-reviewed item, and only if D1+D2 turn
  out not to be sufficient signal. Not part of this recommendation's near-term scope.

## 8. Continuous tracking

**What already exists, confirmed directly:**

- **Per-machine, real-usage trend over time** — `quor/analytics/filter_history.py`'s
  `append_snapshot()`/`growing_filters()`, storing unconditional, append-only snapshots under
  `platformdirs.user_data_dir("quor")/filter_analytics_history.json` (line 41). Wired into
  `quor gain --filters` (`gain.py:249-266`), which appends a new snapshot **every time the flag is
  used**. `growing_filters()` compares the oldest vs. newest snapshot to find filters whose usage
  share is trending up (`filter_history.py:141-177`). This is real, working, tested (`tests/unit/
  test_filter_analytics.py`) continuous tracking — just scoped to one user's machine, not a
  release, and not corpus-related.
- **Live real-vs-benchmark divergence** — `quor/analytics/filter_divergence.py::compute_divergence`
  + `filter_baseline.py::load_benchmark_filter_stats` (reads `baseline.json` directly), surfaced via
  `quor gain --filters`/`quor doctor`. This directly answers "is filter X's real behavior diverging
  from what the benchmark thinks" — today, live, per invocation of the command — not as a
  historical trend, but as a point-in-time comparison against the current committed baseline.
- **Release-scoped corpus trend** — designed (`history.py`), never populated (§2). This is the one
  genuine "what would need to be built" item, and per §1/§2 it needs no new code, only a CI/release
  step that runs `--history` and commits the result.

**What is genuinely missing, not just unwired:** a history *of the divergence itself*. Today,
`compute_divergence()` only ever compares live telemetry against whatever `baseline.json` currently
contains — there is no stored history of "how did real-vs-benchmark divergence change release over
release." Extending `filter_history.py`'s `AnalyticsHistoryEntry` to also snapshot each filter's
`benchmark_compression_pct` (already available via `load_benchmark_filter_stats()`) at snapshot time
would close this with a small, additive change — not a new subsystem, an additional field on an
existing one.

## 9. Benchmark corpus design

The existing `manifest.toml`/`samples/` structure is already fully generic and data-driven —
`category`/`ecosystem` are opaque, manifest-declared strings with no hardcoded list anywhere in
`benchmark_runner.py`/`report.py` (`tests/benchmarks/README.md:24-34`, independently verified there
by the README's own claim of having added and removed a throwaway `terraform` category with zero
`.py` changes). This means **synthetic and any future real-derived samples can live in the same
manifest/samples structure without any structural change** — there is no reason to split them into
separate files or directories.

What *would* be needed, but only once/if a real-derived sample ever exists: an explicit,
author-declared provenance field on `BenchmarkCase` (e.g. `source = "hand-written"` vs.
`source = "real-derived-anonymized"`) so reports can distinguish them. This must be a literal,
declared value per case — never inferred or guessed — consistent with this project's existing
"no heuristic classification field" discipline (every `BenchmarkCase` field today is either
hand-declared in TOML or computed deterministically from real pipeline behavior, never guessed).
Until a real-derived sample exists, adding this field is speculative and should wait.

## 10. Historical metrics

Only metrics already computable from existing code are recommended:

- **Overall `compression_pct` release-over-release** — `history.py`'s existing
  `overall_compression_pct` field. Already built.
- **Per-stage contribution trend** — `history.py`'s `per_stage_contribution_pct`. Already built,
  fed by `analytics_report.py::collect_stage_stats()`.
- **Per-ecosystem compression trend** — `history.py`'s `per_ecosystem_compression_pct`. Already
  built.
- **Real per-filter usage growth** (not corpus-related, but genuinely historical) —
  `filter_history.py::growing_filters()`. Already built, already tracked per-machine.
- **Real-vs-benchmark divergence trend** — not yet built as a *trend* (only live/point-in-time
  today); a small additive extension of `AnalyticsHistoryEntry`, per §8.
- **Corpus size (case count) over time** — already fully answerable from `git log`/`git blame` on
  `manifest.toml`/`baseline.json` (this *is* the existing corpus-growth history — commit-by-commit).
  A dedicated new "corpus growth" metric would duplicate information `git log` already provides for
  free and is **not recommended**.

Not recommended, because nothing in the codebase supports them today and none was requested with
evidence: "percentile improvements" (no per-case distribution infrastructure exists — only
aggregate sums), "benchmark coverage" as a standalone tracked metric (coverage is already fully
visible via `docs/final/COMMAND_SUPPORT.md`'s filter inventory cross-referenced against
`manifest.toml`; a second, parallel "coverage" tracker would be redundant bookkeeping).

## 11. Migration strategy

**Phase 1 (no breaking changes, additive only):**
Wire `python -m tests.benchmarks.run_benchmarks --history` into the release process (either
`release.yml` or, at minimum, a new line item in CLAUDE.md's existing "Release Readiness Checklist,"
which already has a "benchmark suite is green" line to sit alongside). Commit the first-ever
`history.json`. `history.py`'s format is unchanged; this is populating an existing, dormant format,
not changing it.

**Phase 2 (documentation correction, no code):**
Regenerate `docs/BENCHMARKS.md` against the current 153-case corpus (this is already an explicitly
named QB-047 deliverable in backlog.md itself, line 1344-1346) and correct `README.md`'s two stale/
inaccurate claims (§6) to the current case count and accurate "hand-curated" wording. Also correct
backlog.md's own stale QB-054 status line (see unrelated issues) while in the area.

**Phase 3 (evidence-directed corpus expansion, existing process, no new mechanism):**
Use `quor gain --filters`/`quor doctor`'s already-shipped divergence output (QB-054) to target new
hand-written benchmark samples at whichever filters/categories show the largest real-vs-benchmark
gap right now, following the unchanged "Adding a new benchmark case" process
(`tests/benchmarks/README.md`). Backlog.md's own recommendation (git-diff, generic, config-file
samples) is a reasonable starting slice, now checkable directly against live divergence data rather
than the one-off 2026-07-15 manual comparison.

**Phase 4 (explicitly deferred, separately scoped and reviewed):**
Real-sample opt-in contribution (Option C/D3). Not part of this migration. Requires its own product
and legal review before any design work begins, per backlog.md's own existing caution.

No phase requires a schema migration, a breaking change to `manifest.toml`'s format, or a change to
any existing public function signature in `benchmark_runner.py`/`history.py`/`report.py`.

## 12. Test strategy

- **Unit tests (Phase 1):** `tests/benchmarks/history.py` currently has zero test coverage
  anywhere in the repo (§3) — this must be closed before Phase 1 ships, not left as-is just because
  the code already exists. Model it directly on `tests/unit/test_filter_analytics.py`'s existing
  29-test pattern for the structurally identical QB-054 modules (`build_entry`/`append_entry`/
  round-trip, `detect_regression()`'s threshold boundary and "fewer than two entries" cases,
  `render_history_table()`'s output shape).
- **Integration tests:** a `--history` CLI invocation against a real (temp-directory) manifest/
  baseline, asserting the resulting `history.json` round-trips through `load_history()`, following
  the existing `@pytest.mark.integration` convention (CLAUDE.md's own "Test isolation" section) so
  it stays excluded from default CI per this project's <30s default-suite target.
- **Benchmark regression tests:** already fully covered by the existing `test_benchmarks.py`
  mechanism — no changes needed unless Phase 3 adds cases with a genuinely different shape (e.g. a
  provenance field, §9), in which case `BenchmarkCase`'s dataclass and `load_manifest()`'s parsing
  need matching test coverage for the new optional field.
- **Privacy tests:** only relevant once Phase 4 is scoped. At minimum, whatever capture mechanism
  is eventually designed needs a test asserting no content is ever written to `quor.db`/
  `TrackingDB` (i.e., `InvocationRecord`'s shape stays unchanged) — this investigation's own
  strongest recommendation being that `TrackingDB` must remain completely untouched by any Phase 4
  work, so a test pinning that boundary would have real, lasting value.
- **Corpus validation:** none needed beyond what `test_benchmarks.py` already does — every
  manifest case is already validated for filter-correctness and `must_contain` survival on every
  CI run.
- **Trend/release validation:** a test asserting `history.json`'s CI/release step is actually
  idempotent for a re-run against the same version (mirrors `append_entry()`'s existing "re-running
  replaces, doesn't duplicate" contract, `history.py:66-68` docstring) — this is already the
  documented behavior, just needs a test pinning it once Phase 1 wires it somewhere real.

## 13. Files expected to change

**Phase 1 (release tracking):**
- `.github/workflows/release.yml` — add a step running `--history` and committing/uploading
  `tests/benchmarks/history.json` (new file, first commit).
- `docs/final/CLAUDE.md` — add a "history updated" line to the existing Release Readiness
  Checklist (it already has a benchmark-green line to sit next to).
- `tests/unit/test_benchmark_history.py` (new) — closes the zero-coverage gap on `history.py`.

**Phase 2 (documentation correction):**
- `docs/BENCHMARKS.md` — regenerated against the current 153-case corpus.
- `README.md` — correct the "127-case real-world" and "35.9%" claims (§6) to accurate, current, and
  accurately-worded ("hand-curated," not "real-world") figures.
- `backlog.md` — correct QB-054's stale "Proposed. Not scoped or implemented" status line
  (unrelated issue, but adjacent enough to fix in the same pass).

**Phase 3 (evidence-directed corpus expansion):**
- `tests/benchmarks/manifest.toml`, `tests/benchmarks/samples/git-diff/*`,
  `tests/benchmarks/samples/generic/*`, plus a new config-file category directory — new hand-written
  cases, following the existing process.
- `tests/benchmarks/baseline.json` — updated via `--update-baseline` once new cases are reviewed.

**Phase 4 (deferred — not scoped by this investigation):**
- Any file it touches is out of scope until it receives its own dedicated design pass, per §7/§11.
  This investigation explicitly recommends `quor/tracking/db.py` and `quor/tracking/schema.sql`
  **not** be among them.

## 14. Risks

- **Technical:** Low for Phases 1-3 — all reuse existing, already-tested (Phase 1 excepted, see
  below) code paths. Phase 1's own risk is exactly the zero-test-coverage gap on `history.py` (§3,
  §12) — shipping a CI step around untested code is the one concrete near-term risk worth closing
  before, not after, wiring it up.
- **Privacy:** Concentrated entirely in Phase 4 (deferred). Zero incremental privacy risk in
  Phases 1-3 — none of them touch `TrackingDB`, real user content, or any new data collection.
- **Maintenance:** A committed `history.json` growing one entry per release, forever, needs the
  same "does this ever get pruned" thought `quor.db`'s own 90-day cleanup already models
  (`db.py:453-458`) — unlike per-user local data, though, a committed release history has genuine
  long-term historical value and arguably should *not* be pruned; flagging this as a design question
  for whoever wires Phase 1, not resolving it here.
- **Benchmark drift:** Already observed and quantified in this investigation (§2/§3: manifest vs.
  baseline count mismatch, three different stale case-count claims across README.md/
  BENCHMARKS.md/history). Phase 2's documentation-regeneration pass directly addresses the
  observed instances; nothing prevents recurrence except discipline (or, longer-term, generating
  `docs/BENCHMARKS.md` from a script rather than by hand each time — a real option worth a separate,
  future ticket, not attempted here since it's speculative beyond this investigation's evidence).
- **Sample bias:** Only relevant to Phase 4. Real-sample contribution, if ever built, will only ever
  reflect contributors' own usage patterns — flagged in §7 as a structural limitation of Option C/D3,
  not something a clever sampling scheme can fully correct for.
- **Release compatibility:** None — Phases 1-3 are purely additive; no existing format, schema, or
  public function signature changes.
- **Corpus/storage growth:** `manifest.toml`/`samples/` growth is git-tracked, linear, and already
  the existing model (60 → 153 cases with no reported operational issue) — not a new risk Phase 3
  introduces, just continuing an existing, already-working pattern.
- **Performance:** `_builtin_registry()`'s `lru_cache` (`benchmark_runner.py:204-218`) already
  amortizes the dominant cost (filter construction) across however many cases exist; adding more
  cases scales roughly linearly in run time with no structural bottleneck identified.

## 15. Final recommendation

**Ranked:**

1. **Recommended: Option D (split) — ship D1 (Option A) and D2 (evidence-directed hand-curation)
   now; explicitly defer D3 (real-sample capture, Option C reframed) to its own future,
   product+legal-reviewed ticket.**
2. Option A alone — a reasonable fallback if the team wants an even smaller first cut, but leaves
   the "is the corpus representative" half of QB-047's own motivation completely unaddressed.
3. Option B — premature. There is not yet a populated `history.json` to outgrow; building a richer
   store before the simple one has run even once is solving an unobserved problem.
4. Option C as literally scoped ("generated from TrackingDB analytics") — not achievable; would
   need to be re-scoped as D3 before any implementation work could start.

**Why:** This investigation found that QB-047's own bundled asks have already been substantially
addressed by other, already-shipped work (QB-054's live divergence/trend analytics) that backlog.md
itself hasn't caught up to acknowledging. The remaining, genuinely open gap — populating
`history.json` — is nearly free (built, tested format, just never wired to run). The other half —
real corpus content — is precisely the part backlog.md's own product owner already flagged as
needing dedicated legal/product review, and this investigation found nothing to shortcut that;
if anything, the depth of ANTI_GOALS.md #4/#5's existing enforcement (§5) confirms that caution was
well-founded, not overcautious.

**Effort:** D1 — Small (CI wiring + one new test file). D2 — Small/Medium (ordinary benchmark-case
authorship, just evidence-directed). D3 — not estimated here; genuinely out of this investigation's
scope until separately reviewed.

**Risk:** D1/D2 — Low, per §14. D3 — the ticket's own "Risk: Medium" rating undersells it once
ANTI_GOALS.md #4/#5 are read directly; this investigation would rate a naive D3 implementation
High, and a carefully-scoped one (opt-in, local-only, human-reviewed-before-transmission, following
the tee cache's isolation precedent) Medium at best.

**Expected long-term value:** High for D1 (closes a real, currently-invisible gap: an
individually-small-but-persistent regression across releases). Medium-High for D2 (directly
improves corpus realism where it's cheapest to do so — exactly the filters QB-054's own data
already flags). D3's value is real but unquantifiable until its own review happens.

**Compatibility with Quor's deterministic philosophy:** D1/D2 are fully compatible — no heuristics,
no new inferred fields, entirely deterministic aggregation of already-deterministic pipeline
output. D3 would need its own careful design to stay compatible (e.g., no fuzzy/ML-based
redaction as the *only* safeguard — ANTI_GOALS.md #10's transparency requirement would argue for a
human-reviewed, explainable redaction step, not a black-box one) — another reason to scope it
separately rather than fold it into this ticket's implementation.

---

## Unrelated issues found

Per this ticket's own instruction to report separately, matching QB-094/QB-100/QB-102's own
practice:

1. **`backlog.md`'s QB-054 entry is stale and contradicts the shipped code.** Lines 1700-1702 read
   "Status: Proposed. Not scoped or implemented." `quor/analytics/filter_baseline.py`,
   `filter_divergence.py`, `filter_history.py`, and `filter_report.py` all exist, are fully wired
   into `quor gain --filters` (`gain.py:249-266`) and `quor doctor` (`doctor.py:682-698`), and have
   29 passing unit tests (`tests/unit/test_filter_analytics.py`). Git history confirms a real
   commit, `e435f42 "feat(analytics): per-filter compression analytics from real usage (QB-054)"`.
   This should be corrected independent of any QB-047 work.

2. **`docs/BENCHMARKS.md`'s "Future benchmark roadmap" (line 217-219) also still lists QB-054 as
   unimplemented**, for the same reason as #1 above — both documents need the same correction.

3. **`docs/final/ANTI_GOALS.md` #4 (line 44) still describes "the JSONL file"** as part of what
   `TrackingDB` records, but `quor/tracking/db.py:15-22`'s own historical note confirms dual JSONL
   persistence was removed under QB-070 ("SQLite remains the single store"). This anti-goal's
   prose should be updated to match — a minor but genuine documentation/code drift in a file whose
   entire purpose is being an accurate, load-bearing safety contract.

4. **`manifest.toml` (153 cases) and `baseline.json` (145 entries) have already drifted** — 8 cases
   exist with no corresponding baseline entry, meaning someone added benchmark cases without
   running `--update-baseline` and committing the result. Not a failure (they'll report `"new"`,
   not a regression), but worth reconciling independent of QB-047.

5. **`tests/benchmarks/history.py` has zero test coverage** anywhere in the repository, despite
   being fully-implemented, real code with a documented format others (this very ticket) are
   expected to build on. Flagged in §3/§12/§14 as something Phase 1 must close, not carry forward.
