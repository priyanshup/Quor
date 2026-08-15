# Quor Benchmarks

This document describes how Quor's compression benchmark suite works, what it currently measures,
and — just as importantly — where its numbers should and shouldn't be trusted. It reflects the
benchmark run generated **2026-08-01** (`tests/benchmarks/results/benchmark-report.md`,
`benchmark-results.json`) and the accompanying analytics output
(`tests/benchmarks/results/analytics-report.txt`), cross-referenced against the product backlog
(`backlog.md`) for context on known gaps and planned follow-up work. Regenerated as part of
QB-047 Phase 1 — the prior version of this document (generated 2026-07-15) had drifted to describe
a 60-case corpus the manifest had already outgrown; see
`docs/design/QB-047-real-world-benchmark-corpus-investigation.md` §3/§6 for how that drift was
found.

## Methodology

The benchmark suite (`tests/benchmarks/`) runs a fixed set of **hand-written** sample commands
through Quor's real compression pipeline (`benchmark_runner.py`) with per-stage token tracing
enabled (`Pipeline.execute(track_tokens=True)`). For each case it records:

- **Token counts** before and after compression, and tokens saved. Token counts are an
  **approximation** (character-count / 4, ±20%), not an actual tokenizer — see
  [Known limitations](#known-benchmark-limitations).
- **Compression %** — tokens saved as a percentage of original tokens.
- **Execution time** (ms) for the case.
- **A full per-stage trace** — for every pipeline stage that ran (`strip_lines`,
  `code_ast_summarize`, `max_tokens`, etc.), whether it fired or was skipped, and its own
  tokens-before/after/saved.
- **Correctness signals** — whether the expected filter matched (`filter_correct`), whether
  required content patterns survived compression (`missing_patterns` / `correctness_ok`), whether
  a minimum-reduction threshold was met (`min_reduction_met`), and whether the tee recovery
  mechanism would have fired (`tee_would_fire`).

Results are aggregated into `benchmark-report.md` (per-ecosystem summary, per-filter summary,
per-sample table, best/worst performers, and a baseline comparison showing each case's delta in
percentage points against the previously committed baseline) and the raw `benchmark-results.json`.

A separate analytics pass (`analytics_report.py`, invoked via `run_benchmarks.py --analytics`)
computes:

- **Stage contribution** — each pipeline stage's share of total tokens saved across the corpus,
  classified High (≥15% share), Medium (≥5%), or Low.
- **Language/ecosystem contribution** — same idea, grouped by ecosystem.
- **Top-10 hardest files** — the cases with the lowest compression.

**Correctness, floor, and regression checks are CI-gated on every change** — `test_benchmarks.py`
builds and runs every manifest case at `pytest` import time, and `.github/workflows/ci.yml` runs
`pytest tests/` on every push. **Report generation, the analytics pass, and release-history
tracking are not** — those are `run_benchmarks.py` CLI flags (`--analytics`, `--history`), run
manually. (The prior version of this document said the whole suite was "not wired into CI" without
this distinction — that undersold what's actually automatic.)

**Release-history tracking (QB-047 Phase 1):** `--history` appends one entry per release to
`tests/benchmarks/history.json` (QB-051's format), giving a release-over-release trend view instead
of only "this run vs. the immediately prior baseline." As of this run, `history.json` has just been
created and populated for the first time (v0.5.0) — see
`docs/design/QB-047-real-world-benchmark-corpus-investigation.md` and this document's own
[Known limitations](#known-benchmark-limitations) for what "just started" means in practice: a
trend needs more than one release's worth of entries to say anything yet.

## Current benchmark corpus

**153 hand-authored cases across 16 ecosystems and 58 filter categories** — hand-written and
hand-sanitized-if-necessary, not sampled from real usage (see
[Known limitations](#known-benchmark-limitations)):

| Ecosystem | Cases |
|---|---|
| JavaScript | 35 |
| Git | 22 |
| Python | 14 |
| Config Files | 10 |
| CI/Build | 9 |
| Java | 9 |
| TypeScript | 8 |
| Documents | 8 |
| Python Packaging | 7 |
| C/C++ | 6 |
| Generic | 15 |
| C# | 2 |
| Containers | 2 |
| Files | 2 |
| Go | 2 |
| Rust | 2 |

Most individual filter categories still have only 2-5 samples each — enough to sanity-check a
filter's behavior, not enough to reliably characterize it. This is a hand-curated corpus, not a
sample of real usage; see [Known limitations](#known-benchmark-limitations) below.

## Overall compression

| Metric | Value |
|---|---|
| Cases run | 153 |
| Tokens before | 57,227 |
| Tokens after | 36,678 |
| Tokens saved | 20,549 |
| **Overall compression** | **35.9%** |
| Total execution time | ~190 ms |

## Language breakdown

| Ecosystem | Cases | Tokens before | Tokens after | Saved | Compression % |
|---|---|---|---|---|---|
| Java | 9 | 5,880 | 2,652 | 3,228 | 54.9% |
| Config Files | 10 | 2,873 | 1,326 | 1,547 | 53.9% |
| JavaScript | 35 | 6,904 | 3,506 | 3,398 | 49.2% |
| Python Packaging | 7 | 1,454 | 794 | 660 | 45.4% |
| C# | 2 | 1,025 | 601 | 424 | 41.4% |
| TypeScript | 8 | 4,712 | 2,694 | 2,018 | 42.8% |
| Containers | 2 | 461 | 287 | 174 | 37.7% |
| Rust | 2 | 1,241 | 774 | 467 | 37.6% |
| Python | 14 | 3,398 | 2,109 | 1,289 | 37.9% |
| CI/Build | 9 | 3,059 | 1,952 | 1,107 | 36.2% |
| Go | 2 | 1,178 | 860 | 318 | 27.0% |
| Generic | 15 | 2,204 | 1,628 | 576 | 26.1% |
| Documents | 8 | 12,683 | 9,544 | 3,139 | 24.8% |
| Git | 22 | 8,496 | 6,557 | 1,939 | 22.8% |
| Files | 2 | 262 | 203 | 59 | 22.5% |
| C/C++ | 6 | 1,397 | 1,191 | 206 | 14.8% |

Java, JavaScript, TypeScript, and Python — the languages with AST-aware summarization
(`code_ast_summarize` / `python_ast_summarize`) — lead the corpus, joined by Go/Rust/C# since
QB-046 extended AST-aware summarization to those languages too. Git sits noticeably behind them
despite being an extremely common command category in a coding session, because the git-diff
filter's `preserve_patterns` protects nearly all diff content by design (see
[Real-world vs. benchmark observations](#real-world-vs-benchmark-observations)).

## Filter breakdown

The full 58-category per-filter table (token counts, timings, and per-sample results) is in
`tests/benchmarks/results/benchmark-report.md`, regenerated on every local benchmark run. Notable
current performers, read directly from that report:

- **Strongest:** `cat-tsx` (74.9%), `cat-javascript` (68.3%), `cat-toml` (65.6%), `github-actions`
  (61.3%), `cat-json` (56.6%), `pip` (56.8%), `java` (58.5%).
- **Weakest:** `eslint` (6.6%), `git-branch` (9.0%), `bun` (9.0%), `prettier` (9.4%), `ls-long`
  (11.4%), `ruff` (18.0%), `git-diff` (19.6%).
- **Deliberately near-zero ("already clean") cases** exist across several categories (e.g.
  `jest-all-passing`, `vitest-all-passing`, `mypy-clean-run-no-issues`, `markdown-readme-short`,
  `cat-javascript-vendor-bundle-minified`) — Quor correctly declines to compress further rather
  than a sign of missing coverage; see "Top 10 hardest files" in
  `tests/benchmarks/results/analytics-report.txt`.

The weakest performers are mostly cases that were already terse (repeated/short type errors,
minimal `git branch` output) with little left to cut, not filter defects — the same reading the
prior version of this document gave for its own weakest performers, still true of the current
corpus.

## Stage contribution

Share of total tokens saved across the corpus, attributed to the pipeline stage that produced the
saving:

| Stage | Impact | Contribution | Activation | Avg saved per fire |
|---|---|---|---|---|
| `code_ast_summarize` | High | 30.1% | 100% | 40.7% |
| `strip_lines` | High | 19.5% | 100% | 14.5% |
| `max_tokens` | High | 18.6% | 100% | 1.4% |
| `group_repeated` | Medium | 13.7% | 100% | 9.7% |
| `structured_data_summarize` | Medium | 6.8% | 100% | 54.0% |
| `collapse_unchanged_context` | Low | 3.4% | 100% | 12.7% |
| `python_ast_summarize` | Low | 2.2% | 100% | 48.6% |
| `relative_timestamp_compression` | Low | 2.2% | 100% | 11.9% |
| `column_padding_compression` | Low | 1.8% | 100% | 16.2% |
| `regex_replace` | Low | 1.1% | 100% | 51.2% |
| `path_prefix_fold` | Low | 0.4% | 100% | 4.2% |
| `numeric_range_compression` | Low | 0.1% | 100% | 9.8% |
| `deduplicate_consecutive` | Low | 0.1% | 100% | 0.2% |
| `remove_ansi` | Low | 0.0% | 100% | 0.1% |

Two readings this table invites, that would be **wrong**:

1. *"`max_tokens` is nearly as valuable as `code_ast_summarize`/`strip_lines`."* Its 18.6% share
   comes from firing on almost every case for a small trim each time (1.4% average per fire) —
   breadth, not depth.
2. *"`structured_data_summarize`/`regex_replace` barely matter."* Both have among the **highest
   average savings per fire** (54.0% and 51.2% respectively, above even `code_ast_summarize`'s
   40.7%). Their modest total share is a corpus-composition artifact — this corpus simply has fewer
   config-file/generic cases than Git/JavaScript ones — not a quality signal about the stage
   itself, the same reading this document previously gave for `python_ast_summarize`.

## Real-world vs. benchmark observations

A one-off comparison against this project's own real usage telemetry (`quor gain` and a direct
query against the live tracking database, done as part of a **2026-07-15** product-strategy review
— not a repeatable part of the benchmark suite itself, and not re-run for this regeneration) found
several filters where real-world compression diverged sharply from what the benchmark corpus
showed at that time:

| Filter | Benchmark % (2026-07-15) | Real-world % (2026-07-15) | Direction |
|---|---|---|---|
| `git-log` | 40.8% | 83.8% | Real much higher |
| `git-status` | 52.7% | 6.6% | Real much lower |
| `pytest` | 39.8% | 12.9% | Real much lower |
| `mypy` | 46.1% | **-41.2%** | Real is net *expansion*, not compression |
| `npm` | 43.2% | **-9.1%** | Real is net *expansion*, not compression |

This table is a historical snapshot, kept as-is rather than re-measured here — re-running it
against a different project or time window would not be comparable to the original finding, and
this document's own regeneration should not silently overwrite what was actually observed on that
date.

**This comparison is no longer a one-off.** QB-054 (`quor/analytics/filter_divergence.py`) shipped
this exact real-vs-benchmark comparison as a standing feature — run `quor gain --filters` or
`quor doctor` on any project to see it live, computed the same way, updated every time. QB-047
Phase 1 extends this further with a **"Benchmark coverage nominations"** section
(`find_uncovered_filters()`/`nominate_for_benchmark_coverage()`) that turns a large divergence, or a
filter with no benchmark coverage at all, directly into a benchmark-authorship candidate — see
`tests/benchmarks/README.md`'s "Evidence-directed benchmark curation" section for the workflow.

Separately, at the time of the original 2026-07-15 review, `git-diff` — while its benchmark and
real numbers roughly agreed in direction — was responsible for **45% of every token Quor has ever
saved on this project** (46.5k of 100.7k net tokens saved) despite compressing at only a ~26%
real-world ratio. The benchmark corpus's git-diff coverage has since grown from 2 to 15 cases
(including 3 structural-diff cases from QB-099), but the underlying volume/frequency point stands:
the corpus can say a filter compresses well or poorly, never how often it actually runs in
practice — only real telemetry (`quor gain`) can answer that.

**Takeaway:** the benchmark corpus is a useful regression check and a rough per-filter sanity
signal, but it is not a reliable predictor of real-world compression for every filter, and it says
nothing about real-world volume by itself. Treat benchmark and real-world numbers as two different
instruments — QB-054 now lets you consult both side by side on demand, but that does not make them
the same measurement.

## Known benchmark limitations

- **Small, hand-curated corpus.** 153 cases, hand-written rather than sampled from real usage.
  Several filter categories have only 2-3 samples — not enough for per-category numbers to be more
  than directional. See
  `docs/design/QB-047-real-world-benchmark-corpus-investigation.md` for why a genuinely
  real-content corpus needs new, separately-scoped, opt-in infrastructure rather than an extension
  of anything that exists today.
- **Approximate token counts.** Token counts use a ±20% char/4 estimate, not a real tokenizer —
  adequate for relative comparisons, not a precision instrument.
- **Demonstrated benchmark-vs-real divergence (historical finding, now a standing, checkable
  signal).** The 2026-07-15 review found large gaps between benchmark and real-world compression
  for several filters, in both directions (above). Benchmark percentages for any filter should not
  be assumed to generalize without checking `quor gain --filters`/`quor doctor`'s live divergence
  view for your own project.
- **Trend view exists but is early.** `tests/benchmarks/history.json` (QB-051's format) was
  populated for the first time as part of QB-047 Phase 1 (2026-08-01, this run) and is now wired
  into the Release Readiness Checklist (`docs/final/CLAUDE.md`) — but with one entry recorded so
  far, there is nothing to show a multi-release trend against yet. `detect_regression()` needs at
  least two release entries to compare.
- **Correctness/floor/regression are CI-gated; reporting, analytics, and history are not.**
  `pytest tests/` (CI) runs every manifest case's correctness/floor/baseline-regression checks
  automatically. Generating `benchmark-report.md`, the `--analytics` output, and appending to
  `history.json` remain manual, on-demand `run_benchmarks.py` invocations — the last one has a
  dedicated Release Readiness Checklist line item, but is not automated in CI (see
  `docs/design/QB-047-real-world-benchmark-corpus-investigation.md` §7/§11 for why automating the
  history-commit step in CI was considered and deliberately deferred).
- **Correctness checks are a proxy, not a measure of task success.** `missing_patterns` /
  `must_contain` assertions confirm that specific substrings survived compression; they don't
  measure whether an AI assistant working from the compressed output actually completes a coding
  task correctly at the same rate as from uncompressed output.

## Future benchmark roadmap

Per the current product backlog (`backlog.md`):

1. **QB-047 — Real-world benchmark corpus & continuous tracking.** **Phase 1 implemented
   (2026-08-01):** release-history tracking (`history.json` now populated and wired into the
   release checklist) and evidence-directed hand-curation infrastructure (this document's own
   regeneration, plus the "Benchmark coverage nominations" workflow above). Genuine opt-in
   real-content sample collection remains proposed, not scoped — see the investigation doc for why
   it needs its own dedicated product-and-privacy review before implementation.
2. **QB-054 — Standing telemetry/benchmark divergence detection.** **Implemented** — see
   [Real-world vs. benchmark observations](#real-world-vs-benchmark-observations) above (this
   roadmap item previously read "proposed," which was stale — corrected as part of QB-047 Phase 1).
3. **QB-052 — mypy/npm net-negative real compression.** Resolved 2026-07-31 per `backlog.md`'s own
   entry (a tee-footer/tracking-order fix) — worth re-verifying against a fresh `quor gain
   --filters` run before assuming it fully holds across every project, since real-usage numbers are
   inherently project-specific.
4. **QB-041 / QB-055 — Smarter git-diff compression.** Partially done — `collapse_unchanged_context`
   and a `preserve_patterns` fix have shipped; cross-file repeated-edit deduplication and
   generated-noise summarization remain open. See `backlog.md`'s own QB-041/QB-055 entries for
   current status.
5. **QB-046 — AST-aware summarization for more languages.** **Implemented** — Go, Rust, Java, and
   C# analyzers have shipped and are reflected in this document's own language breakdown above
   (this roadmap item previously listed it as pending; corrected here for the same reason as
   QB-054).
