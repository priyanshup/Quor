# Compression Benchmark Suite (QB-011)

A repeatable framework that measures Quor's compression effectiveness against
a fixed corpus of representative command output, and fails the build if a
change regresses it. This is the validation gate for future optimization
work on the pipeline.

## Architecture

This suite is isolated from production code by construction — it only ever
*calls* Quor's existing, unmodified public surface:

- `quor.filters.registry.FilterRegistry` — the same lookup/apply path the
  real dispatcher uses.
- `quor.tracking.db.count_tokens` — the same token estimate `quor gain` uses.
- `quor.pipeline.tee.content_hash` — a pure hash utility, used read-only to
  detect whether tee *would* fire (never calling `write_tee()`, so a
  benchmark run never touches your real tee cache).

No compression algorithm, stage, or filter is modified, patched, or
special-cased for benchmark purposes. Nothing in `quor/` imports from or
knows about `tests/benchmarks/`.

**The framework is fully data-driven.** `benchmark_runner.py` and
`report.py` have no hardcoded knowledge of any filter, category, or
ecosystem — `category` and `ecosystem` are arbitrary strings read from
`manifest.toml` and grouped generically (`_group_by()` in
`benchmark_runner.py` groups by whatever value a field returns, with no
list of known values anywhere). Adding a new filter (npm, Docker, PDF,
DOCX, Terraform, ...) is purely a data change: a sample file plus a
`[[case]]` entry. This was verified directly — a throwaway case with
`category = "terraform"` / `ecosystem = "Infrastructure"` appeared
correctly in both the per-category and per-ecosystem reports with zero
changes to any `.py` file, then was removed again.

```
tests/benchmarks/
├── README.md              — this file
├── manifest.toml          — declares every benchmark case
├── samples/<category>/    — one sample file per case, referenced by manifest.toml
├── benchmark_runner.py    — core engine: load manifest, run cases, aggregate, compare
├── report.py              — JSON + Markdown report formatting (presentation only)
├── run_benchmarks.py      — CLI entrypoint (argparse)
├── test_benchmarks.py     — pytest integration; runs automatically with `pytest tests/`
└── baseline.json          — committed reference snapshot for regression detection
```

Two signals are checked, deliberately kept separate rather than blended into
one score:

1. **Correctness** — did the expected filter match, and did every required
   substring (`must_contain`) survive? A violation is always fatal,
   regardless of how much was saved — silently dropping required content is
   worse than a smaller compression ratio.
2. **Compression quality** — checked two ways: a loose per-case
   `min_reduction_pct` floor (catches a catastrophic break even before a
   baseline exists), and comparison against `baseline.json` (the precise
   regression signal — "smaller than last time").

`execution_time_ms` is captured and shown in every report for visibility,
but it is **never** part of the pass/fail gate — wall-clock time is
inherently noisy across machines and CI runners and would make the suite
flaky if used as a hard threshold.

## Running the benchmarks

From the repository root:

```bash
# Run once, compare against the committed baseline, write both reports
python -m tests.benchmarks.run_benchmarks

# Only JSON, to a custom directory
python -m tests.benchmarks.run_benchmarks --output-dir /tmp/bench --format json

# Loosen or tighten the regression threshold (percentage points)
python -m tests.benchmarks.run_benchmarks --regression-threshold 5.0

# Skip baseline comparison entirely (metrics/reports only)
python -m tests.benchmarks.run_benchmarks --no-compare
```

Reports are written to `tests/benchmarks/results/` by default (gitignored —
these are per-run artifacts, not source). Exit code is `0` if every case is
correct, clears its floor, and has no baseline regression; `1` otherwise.

The suite also runs automatically as part of `pytest tests/` (via
`test_benchmarks.py`), so a regression fails CI without anyone needing to
run the standalone script.

## Adding a new benchmark case

1. Capture (or write a realistic, sanitized-if-necessary) sample of the
   command's real output and save it under
   `tests/benchmarks/samples/<category>/<NNN>_<short_description>.txt`.
   Use a new `<category>` subdirectory if the command doesn't fit an
   existing one.
2. Add a `[[case]]` entry to `manifest.toml`:

   ```toml
   [[case]]
   id = "category-short-description"       # stable — never rename an id that
                                            # already has baseline history
   category = "your-category"               # groups the per-filter report
   ecosystem = "Your Ecosystem"             # groups the coarser per-ecosystem
                                            # report (e.g. "Git", "Python") —
                                            # multiple categories can share one
   command = "the real command string"      # matched via FilterRegistry.find(),
                                            # exactly like a real invocation
   sample_file = "samples/your-category/NNN_short_description.txt"
   expected_filter = "the-filter-name-that-should-match"
   min_reduction_pct = 10.0                 # a loose floor — set it comfortably
                                            # below what you actually measure,
                                            # not as a target to hit
   must_contain = ["substrings", "that must survive filtering"]
   ```

3. Run `python -m tests.benchmarks.run_benchmarks --no-compare` and read the
   generated Markdown report to see the actual measured compression % for
   your new case. Set `min_reduction_pct` a few points below that measured
   value — it exists to catch a catastrophic break (a stage silently
   no-op'ing), not to enforce a specific target.
4. Run `python -m tests.benchmarks.run_benchmarks --update-baseline` to add
   your new case to `baseline.json`, then commit both files together.

New cases always show up as `"new"` on their first comparison (there's
nothing to regress against yet) — this is expected, not a failure.

## Updating the baseline

Whenever a pipeline change *intentionally* changes a sample's compression
(a new stage, a better pattern, a stricter filter):

```bash
python -m tests.benchmarks.run_benchmarks --update-baseline
```

This refuses to run if any correctness check or floor check is currently
failing — you cannot bless a broken baseline. Review the diff of
`baseline.json` before committing it, the same way you'd review any other
change: every `compression_pct` that moved should have an explanation you
can point to (which filter/stage change caused it).

## Interpreting a failure

**Correctness failure** (`filter_correct` is false, or `missing_patterns` is
non-empty): something is actively broken — either routing sent a sample to
the wrong filter, or a stage is now dropping content it must never drop.
This is never something to "fix" by loosening the manifest; find and fix the
underlying pipeline regression, or fix the sample if it was genuinely wrong.

**Min-reduction floor violation**: compression on a specific sample dropped
below its configured floor. Check the Markdown report's per-sample row —
compare `compression_pct` against what the floor was set from. If the drop
is intentional (e.g. a filter got more conservative for good reason), lower
the floor deliberately and explain why in the commit message; don't lower it
just to silence the suite.

**Baseline regression** (`status: "regression"` in the comparison, or a
non-empty regression list from the CLI): a sample compresses meaningfully
worse (by more than `--regression-threshold` percentage points, default
`2.0`) than the last committed baseline. Read the Markdown report's
"Baseline comparison" table for the exact before/after percentages. If the
regression is unintentional, that's a real bug in a recent pipeline change —
bisect it the same way you would any other regression. If it's an accepted
trade-off, update the baseline (see above) and say why in the PR.

## Filter coverage

As of ADR-032 (`docs/final/DECISIONS.md`), every currently-implemented built-in filter has at
least 2 manifest cases. This closes the gap this section used to describe (`eslint`/`npm`/`npx`/
`pnpm`/`yarn` were the original omissions from QB-011; `ruff` and `cat`/`cat-python` were found
missing during the same follow-up pass). `cat-javascript`/`cat-typescript`/`cat-tsx` (QB-005C/D)
shipped without benchmark coverage as a temporary, explicitly-documented exception — QB-005E has
since closed that gap too, adding 12 cases across those 3 categories (60 cases across 27
categories total). Every filter added after this point must include its own benchmark case
before merge — see `docs/final/COMMAND_SUPPORT.md` §7.

## AST summarization timing analysis (QB-005E)

`ast_timing_analysis.py` is a second, deliberately separate script from the rest of this suite —
run it directly (`python -m tests.benchmarks.ast_timing_analysis`), it is not wired into
`test_benchmarks.py`'s pytest gate. It breaks down, for every Python/JavaScript/TypeScript/TSX
benchmark case, how much time is spent in raw parsing (`analyze_*()`) versus the AST
`StageHandler`'s own bookkeeping versus the rest of the filter pipeline (`strip_lines`/
`deduplicate_consecutive`/`max_tokens`/render), and separately measures large-file scaling,
malformed-source/ERROR-node handling performance, and files with nothing to summarize — using
deliberately synthetic inputs for the scaling/malformed cases, since those measure operational
characteristics, not compression realism, and are intentionally kept out of the regression-tracked
corpus in `manifest.toml`. See `backlog.md`'s QB-005E entry for the measured results.

## Future benchmark expansion

A "mixed session" category chaining multiple commands, and wiring `--regression-threshold`
into CI as an explicit, reviewable config value rather than a hardcoded default, remain open
ideas — see the implementation summary for detail. Filter coverage itself (the original content
of this section) is now complete; see "Filter coverage" above.
