# Quor Benchmarks

This document describes how Quor's compression benchmark suite works, what it currently measures,
and — just as importantly — where its numbers should and shouldn't be trusted. It reflects the
benchmark run generated **2026-07-15T06:51:28+00:00** (`tests/benchmarks/results/benchmark-report.md`,
`benchmark-results.json`) and the accompanying analytics output
(`tests/benchmarks/results/_review/analytics-report.txt`), cross-referenced against the product
backlog (`backlog.md`) for context on known gaps and planned follow-up work.

## Methodology

The benchmark suite (`tests/benchmarks/`) runs a fixed set of hand-written sample commands through
Quor's real compression pipeline (`benchmark_runner.py`) with per-stage token tracing enabled
(`Pipeline.execute(track_tokens=True)`). For each case it records:

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

A `--history` mode appends run results to an append-only `tests/benchmarks/history.json` with a
`detect_regression()` comparison function, intended to give a release-over-release trend view. As
of this run, that trend view is designed but not yet populated with more than one data point, and
the suite is **not wired into CI** — it runs on demand, not automatically on every change.

## Current benchmark corpus

60 hand-authored cases across 7 ecosystems and 26 filter categories:

| Ecosystem | Cases |
|---|---|
| JavaScript | 26 |
| Python | 9 |
| Documents | 8 |
| TypeScript | 7 |
| Git | 6 |
| Files | 2 |
| Generic | 2 |

Most individual filter categories (e.g. `git-diff`, `git-log`, `git-status`, `mypy`, `pnpm`,
`generic`) have only **2 samples each** — enough to sanity-check a filter's behavior, not enough
to reliably characterize it. This is a hand-curated corpus, not a sample of real usage; see
[Known limitations](#known-benchmark-limitations) below.

## Overall compression

| Metric | Value |
|---|---|
| Cases run | 60 |
| Tokens before | 27,229 |
| Tokens after | 17,627 |
| Tokens saved | 9,602 |
| **Overall compression** | **35.3%** |
| Total execution time | 91.05 ms |

## Language breakdown

| Ecosystem | Cases | Tokens before | Tokens after | Saved | Compression % |
|---|---|---|---|---|---|
| JavaScript | 26 | 6,142 | 2,944 | 3,198 | 52.1% |
| TypeScript | 7 | 4,426 | 2,544 | 1,882 | 42.5% |
| Python | 9 | 2,150 | 1,276 | 874 | 40.6% |
| Git | 6 | 1,415 | 973 | 442 | 31.2% |
| Documents | 8 | 12,683 | 9,544 | 3,139 | 24.8% |
| Files | 2 | 262 | 203 | 59 | 22.5% |
| Generic | 2 | 151 | 143 | 8 | 5.3% |

JavaScript, TypeScript, and Python — the three languages with AST-aware summarization
(`code_ast_summarize` / `python_ast_summarize`) — lead the corpus. Git sits noticeably behind them
despite being an extremely common command category in a coding session, because the git-diff
filter's `preserve_patterns` protects nearly all diff content by design (see
[Real-world vs. benchmark observations](#real-world-vs-benchmark-observations)).

## Filter breakdown

| Category | Cases | Compression % | Category | Cases | Compression % |
|---|---|---|---|---|---|
| cat-tsx | 1 | 74.4% | git-log | 2 | 40.8% |
| cat-javascript | 5 | 67.8% | pytest | 3 | 39.8% |
| pnpm | 2 | 60.6% | markdown | 2 | 26.6% |
| yarn | 2 | 59.6% | pdf | 2 | 41.5% |
| git-status | 2 | 52.7% | npm | 2 | 43.2% |
| mypy | 2 | 46.1% | jest | 2 | 25.8% |
| cat-python | 2 | 44.6% | next | 2 | 25.8% |
| cat-typescript | 6 | 34.7% | cat | 2 | 22.5% |
| npx | 1 | 33.3% | document-text | 2 | 16.9% |
| vitest | 2 | 27.0% | docx | 2 | 15.4% |
| ruff | 2 | 18.0% | git-diff | 2 | 16.1% |
| generic | 2 | 5.3% | turbo | 2 | 15.7% |
| eslint | 2 | 6.6% | tsc | 2 | 6.2% |
| prettier | 2 | 9.4% | | | |

(Full per-filter table with token counts and timings is in
`tests/benchmarks/results/benchmark-report.md`.) The weakest performers — `tsc` (6.2%), `eslint`
(6.6%), `generic` (5.3%) — are mostly cases that were already terse (repeated/short type errors,
`ls -la` output) with little left to cut, not filter defects. Several 0.0%-compression samples
(`jest-all-passing`, `vitest-all-passing`, `markdown-readme-short`, `document-text-rst-short`,
`docx-readme-short`, `pdf-notes-short`, `cat-javascript-vendor-bundle-minified`, `generic-ls-la`)
are deliberately included "already clean" baseline cases — Quor correctly declines to compress
them further rather than a sign of missing coverage.

## Stage contribution

Share of total tokens saved across the corpus, attributed to the pipeline stage that produced the
saving:

| Stage | Impact | Contribution | Activation | Avg saved per fire |
|---|---|---|---|---|
| `code_ast_summarize` | High | 44.1% | 100% | 43.1% |
| `max_tokens` | High | 32.4% | 100% | 2.2% |
| `strip_lines` | High | 18.4% | 100% | 17.9% |
| `group_repeated` | Low | 2.7% | 100% | 14.1% |
| `python_ast_summarize` | Low | 2.4% | 100% | 44.3% |
| `deduplicate_consecutive` | Low | 0.1% | 100% | 0.3% |
| `remove_ansi` | Low | 0.0% | 100% | 0.2% |

Two readings this table invites, that would be **wrong**:

1. *"`max_tokens` is nearly as valuable as `code_ast_summarize`."* Its 32.4% share comes from
   firing on almost every case for a small trim each time (2.2% average per fire) — breadth, not
   depth.
2. *"`python_ast_summarize` barely matters."* It has the **second-highest average saving per
   fire** (44.3%, essentially tied with `code_ast_summarize`'s 43.1%). Its low total share is a
   corpus-composition artifact — this corpus simply has few Python cases relative to
   Git/JavaScript/generic ones — not a quality signal about the stage itself.

## Real-world vs. benchmark observations

A one-off comparison against this project's own real usage telemetry (`quor gain` and a direct
query against the live tracking database, done as part of a 2026-07-15 product-strategy review —
not a repeatable part of the benchmark suite itself) found several filters where real-world
compression diverges sharply from what the benchmark corpus shows:

| Filter | Benchmark % | Real-world % | Direction |
|---|---|---|---|
| `git-log` | 40.8% | 83.8% | Real much higher |
| `git-status` | 52.7% | 6.6% | Real much lower |
| `pytest` | 39.8% | 12.9% | Real much lower |
| `mypy` | 46.1% | **-41.2%** | Real is net *expansion*, not compression |
| `npm` | 43.2% | **-9.1%** | Real is net *expansion*, not compression |

The `mypy` and `npm` cases are the most concerning: real invocations of these filters make output
*bigger* on average, not smaller. The likely cause (read directly from
`quor/filters/builtin/build.toml`): `mypy`'s `group_repeated` stage requires 3 identical error
shapes before it collapses anything, but a typical real mypy run reports a handful of *distinct*
errors — so little gets trimmed, while the tee recovery footer's near-fixed overhead is still
added, producing a net increase on already-small output. This pattern is invisible in the
benchmark corpus because both of `mypy`'s 2 benchmark samples happen to compress fine.

Separately, `git-diff` — while its benchmark and real numbers roughly agree in direction — is
responsible for **45% of every token Quor has ever saved on this project** (46.5k of 100.7k net
tokens saved) despite compressing at only a ~26% real-world ratio, well below its git siblings.
The benchmark corpus has only 2 git-diff samples, so it cannot show this volume/frequency
dimension at all — the corpus can say a filter compresses well or poorly, but not how often it
actually runs in practice.

**Takeaway:** the benchmark corpus is a useful regression check and a rough per-filter sanity
signal, but it is not a reliable predictor of real-world compression for every filter, and it says
nothing about real-world volume. Treat benchmark and real-world numbers as two different
instruments, not interchangeable ones.

## Known benchmark limitations

- **Small, hand-curated corpus.** 60 cases, hand-written rather than sampled from real usage.
  Several filter categories have only 2 samples — not enough for per-category numbers to be more
  than directional.
- **No config-file category.** There is currently no benchmark coverage for YAML/JSON/TOML/.env/
  .ini files, so proposed work in that area (backlog item QB-040) has no benchmark evidence to be
  evaluated against yet.
- **Approximate token counts.** Token counts use a ±20% char/4 estimate, not a real tokenizer —
  adequate for relative comparisons, not a precision instrument.
- **Demonstrated benchmark-vs-real divergence.** As shown above, at least five filters
  (`git-log`, `git-status`, `pytest`, `mypy`, `npm`) show large gaps between benchmark and
  real-world compression, in both directions. Benchmark percentages for other filters should not
  be assumed to generalize without similar verification.
- **No sustained trend view yet.** The regression gate compares only against the immediately
  prior baseline; the `history.json` trend format exists but has not yet been populated
  release-over-release, so a regression that's individually small but persistent across several
  releases would currently go unnoticed.
- **Not wired into CI.** A benchmark run today is manual (`run_benchmarks.py`), not a required
  check on every change.
- **Correctness checks are a proxy, not a measure of task success.** `missing_patterns` /
  `must_contain` assertions confirm that specific substrings survived compression; they don't
  measure whether an AI assistant working from the compressed output actually completes a coding
  task correctly at the same rate as from uncompressed output.

## Future benchmark roadmap

Per the current product backlog (`backlog.md`), the following benchmark-related work is proposed
but not yet scoped or implemented, in priority order:

1. **QB-047 — Real-world benchmark corpus & continuous tracking.** Extend the corpus with
   opt-in, anonymized real-usage samples (not just hand-authored fixtures) and start populating
   the `history.json` trend view release-over-release. A first slice would specifically target
   `git-diff`, `generic`, and config-file samples, tied to the `git-diff` and config-file work
   below.
2. **QB-052 — Fix the `mypy`/`npm` net-negative regression** surfaced by the real-world
   comparison above.
3. **QB-054 — Standing telemetry/benchmark divergence detection**, generalizing the one-off
   real-vs-benchmark comparison above into an ongoing, automated check instead of a manual query.
4. **QB-041 / QB-055 — Smarter git-diff compression.** `git-diff.toml`'s `preserve_patterns`
   protects nearly all diff content by design today; given git-diff's outsized real-world volume
   (above), improving its compression ratio is now the highest-leverage single filter change
   identified. This would also need its own expanded git-diff benchmark cases (tied to QB-047) to
   have a real regression baseline.
5. **QB-046 — Extend AST-aware summarization to more languages** (Go/Rust/Java/C#).
   `code_ast_summarize` is the corpus's single highest-contributing and highest-average-saving
   stage, currently gated to three languages.

Separately, at the time this document was written, several new git-diff sample files were present
in the working tree (`tests/benchmarks/samples/git-diff/003`–`012`, plus a modified
`tests/benchmarks/manifest.toml`) but had not yet been run through the benchmark suite or reflected
in the results above — consistent with a corpus-expansion effort along the lines of QB-047's
"git-diff slice" already being in progress. This document was written without running benchmarks,
per its own instructions, so those files' effect on the numbers above is not yet known.
