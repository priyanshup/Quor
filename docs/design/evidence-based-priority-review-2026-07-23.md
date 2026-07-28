# Evidence-Based Priority Review — 2026-07-23

> Status: **investigation only — no code written.** Produced by re-running the existing
> benchmark suite and querying the real, live tracking DB (`quor.db`, 3,965 rows,
> 2026-07-04 through 2026-07-23) directly — not by reading `backlog.md`'s stated
> priority order and accepting it. Where this review's evidence disagrees with a
> backlog entry's own effort/value/risk labels, that's called out explicitly.

---

## Methodology

Two live data sources, both real, both queried fresh for this review:

1. **Benchmark corpus** — `python -m tests.benchmarks.run_benchmarks --no-compare
   --analytics`, current `main`, 127 cases (grown from the 60 cited in earlier backlog
   entries — the corpus has expanded since QB-040's structured-data filters shipped).
2. **Real usage telemetry** — direct SQL against `quor.db`
   (`%LOCALAPPDATA%\quor\quor\quor.db`), scoped to this project's own path (3,678 of
   3,965 total rows, ~93% of everything ever recorded on this machine — this project
   dogfoods itself, so this is a genuine real-usage sample, not a synthetic one).
   2,474 real filtered invocations, 1,204 Read passthrough rows, spanning 19 days.

Both sources are reused as-is via Quor's own existing tooling (`run_benchmarks.py
--analytics`, the tracking DB schema) — no new instrumentation was built for this
review, matching the task's "do not write code" constraint.

---

## Evidence, by requested category

### Stage execution frequency & per-stage token savings (benchmark, fresh 127-case run)

| Stage | Contribution | Activation | Avg saved/fire |
|---|---|---|---|
| `code_ast_summarize` | 31.1% | 100% | 40.5% |
| `strip_lines` | 20.7% | 100% | 16.1% |
| `max_tokens` | 20.2% | 100% | 1.8% |
| `group_repeated` | 14.8% | 100% | 9.8% |
| `structured_data_summarize` | 7.3% | 100% | **54.0%** |
| `collapse_unchanged_context` | 3.4% | 100% | 10.3% |
| `python_ast_summarize` | 1.2% | 100% | 44.3% |
| `regex_replace` | 1.2% | 100% | 51.2% |
| `deduplicate_consecutive` / `remove_ansi` | ~0% | 100% | ~0.2% |

(Every stage shows 100% activation because every benchmark case is hand-picked to
exercise its filter — this column is not informative for the benchmark corpus; it's
only meaningful against real traffic, see below.)

### Filter effectiveness — benchmark vs. real, side by side

This is the load-bearing table. Real per-filter numbers come from the tracking DB,
scoped to real invocations where a filter actually ran (`was_passthrough=0`):

| Filter | Real n | Real avg %/invocation | Real net % (aggregate) | Benchmark avg % | Real no-op rate |
|---|---|---|---|---|---|
| **`mypy`** | 100 | **-40.8%** | -35.3% | +20.4% | 5.0% |
| **`ruff`** | 116 | **-12.3%** | +20.8% | +16.6% | 81.9% |
| `generic` (see below) | 975 | -2.8% | +43.8% | +9.3% | 99.0% |
| `git-diff` | 219 | +17.0% | +48.7% | +19.4% | 37.9% |
| `git-status` | 294 | +7.1% | +6.5% | +55.6% | 80.3% |
| `git-log` | 144 | +3.2% | +40.9% | +42.9% | 91.0% |
| `pytest` | 422 | +3.5% | +11.3% | +34.4% | 81.5% |
| `cat` | 175 | +7.1% | +81.1% | +22.2% | 81.7% |
| `cat-python` | 19 | +37.6% | +55.4% | +44.5% | 36.8% |

**`mypy` and `ruff` are the only two filters where the benchmark corpus predicts the
wrong sign.** The benchmark says mypy should average +20.4%; real usage shows -40.8%
— mypy makes its own output *worse* on the median real invocation, not better. This
isn't a one-off: broken down by week, mypy's real average is -38.3% (week of 7/04),
-46.6% (week of 7/08), -25.5% (week of 7/14, the most recent bucket) — negative
throughout the entire 19-day window, not a resolved historical artifact. `ruff` shows
the same pattern, smaller magnitude, also persisting into the most recent week
(-1.4%, still negative).

### No-op rate & the `generic`/`git` finding

`generic` is the single **highest-volume** real filter (975 invocations — more than
`pytest` at 422, more than `git-diff` at 219) and fires with a **99.0% no-op rate**
(965/975 real invocations produced byte-identical output). Tracing the actual
commands: **100% of `generic` matches in real usage are `git` subcommands** — not
arbitrary ad-hoc commands. `git.toml` only has dedicated filters for `status`/`log`/
`diff`; every other real `git` subcommand (`add`, `commit`, `push`, `branch`,
`fetch`, `stash`, `rev-parse`, `ls-files`, etc.) falls through to the generic
catch-all, which has nothing git-shaped to strip. Aggregate net% looks fine (+43.8%,
carried by a handful of large outputs), but the **per-invocation average is -2.8%**
— the same "small output + fixed overhead = net negative" dynamic as mypy/ruff,
just smaller in magnitude, at nearly 10x the volume.

### Missed compression opportunities

Checked for a genuine coverage gap on the Bash side: **zero** — every real Bash
invocation in this dataset matched *some* filter (even if only `generic`); there is
no "unknown command, nothing runs at all" gap today for Bash.

Checked the Read side: 1,204 passthrough rows, concentrated in `.py`/`.md`/`.toml`/
`.txt` — extensions Quor already supports. Traced this to `claude_read.py`'s
`tool_response is not isinstance(str)` branch (tracked with `original=""`,
`filter_name=None`), not to "no filter matched real file content." **This is not
evidence of a real content-coverage gap** — it's most likely Read calls whose
`tool_response` payload wasn't plain text for some other reason (missing file,
non-text response shape). Flagged as an open question, not built into the
recommendation below — an honest negative finding is still a finding.

### Runtime cost

Pipeline cost itself is healthy: the fresh 127-case benchmark run completed in
558.88ms total (≈4.4ms/case), comfortably inside the documented `<200ms/10,000
lines` target — the compression mechanism itself is not the bottleneck anywhere in
this data.

Real `duration_ms` (wall-clock, dominated by the wrapped tool's own execution, not
Quor's overhead — confirmed by reading `dispatcher.py`'s `t0` placement) shows a
secondary, smaller risk worth naming: `pytest` averages 7.39s and peaks at 25.3s;
**23 real invocations exceeded 20s, 7 exceeded 24s** — right at `_run_subprocess`'s
hardcoded `timeout=25` ceiling. Some fraction of real `pytest` runs are plausibly
hitting that timeout and returning exit 124 (`"command ... timed out"`) instead of
real output. Real, measurable, but an order of magnitude smaller in scope than the
finding below (7-23 invocations vs. 216+), and arguably a different kind of problem
(subprocess budget tuning, not compression quality) — noted, not recommended as the
top pick.

### Benchmark effectiveness (broader check)

Consistent with QB-047's own already-documented finding (benchmark vs. real
divergence for git-log/git-status/pytest), refreshed with today's numbers: every
filter in the table above shows real compression well below its benchmark number
*except* mypy/ruff, which invert sign entirely. The corpus systematically
overstates real-world compression — expected, given hand-authored samples trend
toward "content worth compressing" — but the mypy/ruff sign-flip is qualitatively
different from "the benchmark is optimistic." It's "the benchmark is measuring the
opposite of what happens in production."

---

## The single largest remaining opportunity

**Net-negative real-world compression in `mypy` and `ruff`, and the same dynamic at
much larger volume in `generic`/`git`.**

Scored against the four stated priorities:

- **Measurable impact:** 100 (mypy) + 116 (ruff) + 975 (generic) = 1,191 real
  invocations — **48% of all real filtered traffic on this project** — currently
  deliver zero, negative, or trust-eroding compression on the *median* invocation,
  despite the benchmark corpus predicting positive results for all three. mypy alone
  is net token-*negative* in absolute terms today (-1,982 tokens across 100 real
  invocations) — Quor is making its own output larger than the command it ran, on a
  filter it ships by default.
- **Deterministic implementation:** this is a bug-fix investigation into existing,
  already-designed machinery (the tee recovery footer's cost-vs-savings guard,
  `dispatcher.py::_apply_tee`), not new algorithm design. No new heuristics, no new
  stage types, no architecture change.
- **Reuse of existing infrastructure:** 100% — the fix lives entirely inside the
  existing tee/dispatcher/tracking path already shipped for ADR-023.
  Nothing else in the pipeline needs to change.
- **Minimal architectural risk:** the lowest-risk category available — correcting a
  guard that isn't behaving as its own docstring says it should, verified against
  real telemetry, not a speculative new capability.

### Why this beats the closest competing candidate (QB-041/QB-055, git-diff)

`git-diff` is already backlog's #1-ranked item, and the evidence doesn't dispute
that it's valuable — it's the single largest *absolute* real token-saver in this
whole dataset (243,063 tokens across 219 invocations, more than every other filter
combined). But per the criteria this task asks to prioritize by:

- QB-041's own remaining ideas (collapsing repetitive hunks, summarizing huge
  unchanged regions) are explicitly **not** fully deterministic yet — QB-055's own
  entry states "what 'same shape' means for hunk-level grouping needs the same
  deterministic, non-heuristic caution" as an *open, unanswered* design question.
  That's real algorithm-design risk this review's evidence can't discharge; it
  needs its own design pass before it qualifies as "deterministic implementation."
- The mypy/ruff finding, by contrast, is a **correctness regression already
  happening in production**, not a further-optimization opportunity on an already-
  positive filter. Per Anti-Goal #9 ("never optimize for benchmark numbers at the
  expense of correctness") and the "aggressive isn't reckless" trust framing
  already in this project's own vision statement, a filter making output *worse*
  for the median real user is a more urgent class of problem than a well-performing
  filter that could do even better.

### This is an existing backlog item — and the evidence changes its scope

The opportunity identified here **is QB-052** ("Fix negative-compression regression
in mypy/npm filters") — this review does not propose a new QB item. But the fresh
evidence means QB-052's current framing needs correcting before it's re-prioritized:

1. **QB-052's own status line says "Proposed. Not scoped or implemented,"** yet
   `dispatcher.py::_apply_tee`'s current docstring describes a footer-suppression
   guard ("the footer is only appended... when doing so keeps the total token
   count at or below the true raw output's... mypy... were consistently landing
   net-negative... purely because this fixed-cost footer outweighed genuine
   savings") **in the present tense, as already-shipped behavior.** Real telemetry
   proves the negative-compression symptom is still live today (confirmed through
   the most recent measured week), which means one of two things is true and needs
   root-causing before a fix is written: either that guard has a bug that lets
   growth through in a case its own logic should prevent, or the negative average
   is coming from a different mechanism than the tee footer entirely (e.g. the
   filter's own `preserve_patterns`/`on_empty` behavior). This review could not
   determine which without writing code, which is out of scope here — but it *can*
   report, with confidence, that whichever mechanism the current QB-052 backlog
   text assumed is the cause, that assumption needs re-verifying against today's
   code, not re-implemented blind.
2. **Scope should widen from "mypy/npm" to "mypy/ruff," and `generic`/`git` should
   be added as a related, much-larger-volume instance of the same class of bug.**
   `npm`'s real sample is now too small to confirm (n=6, avg exactly 0.0% — no
   longer clearly evidencing the negative pattern QB-052 originally cited it for).
   `ruff` was never named in QB-052 and clearly should be (n=116, -12.3% avg,
   persisting into the most recent week). `generic`/`git` was not previously
   connected to QB-052 at all and is the largest-volume instance of the identical
   dynamic (975 invocations, all real `git` subcommands with no dedicated filter).
3. **QB-052's own "Expected token impact: Low" label undersells it.** That
   estimate was written before this project's real usage had accumulated the
   volume it has now (100/116/975 invocations respectively) and before `ruff`/
   `generic` were known to share the same defect. 48% of real filtered traffic
   showing this pattern is not a low-impact finding, even if the *absolute* token
   numbers per invocation are individually small (a single mypy run doesn't cost
   much; 100 of them, all doing the opposite of their job, is a different kind of
   problem — a trust/correctness one on top of the token one).

### Recommended scope, evidence-derived (not newly designed here)

Root-cause the actual mechanism behind the persisting negative average (starting
with `_apply_tee`'s guard, since that's the code most directly implicated by its
own documentation) → confirm whether `ruff`/`generic` share that exact root cause
or a related-but-distinct one (e.g. `generic`'s dynamic could be less about the tee
footer specifically and more about `max_tokens`'s fixed 1000-token default having
no floor-relative-to-content-size behavior on already-tiny `git` outputs — a
question for the fix's own design, not this review) → fix once, verify against a
regenerated real-usage sample over the following weeks, not just the benchmark
corpus (which, per the evidence above, cannot be trusted alone for this specific
filter pair — it's the one place in this whole dataset where benchmark and reality
disagree on *sign*, not just magnitude).

---

## Secondary findings (real, but not the top pick)

- **`pytest` subprocess timeout risk** (§ Runtime cost) — 7 real invocations over
  24s against a hardcoded 25s ceiling. Real, but an order of magnitude smaller in
  volume than the finding above, and a different kind of problem (timeout budget,
  not compression quality). Worth a follow-up look, not urgent.
- **Read-passthrough non-string `tool_response` rows** (1,204 of them) — traced to
  a specific code branch, not a real content-coverage gap. Recommend leaving this
  uninvestigated further unless a future review has reason to think Read calls are
  failing more than expected — this review found nothing actionable here.
- **QB-046 (more AST languages)** — this review's data reconfirms backlog's own
  2026-07-15 finding: zero real invocations of `cat-javascript`/`cat-typescript`/
  any newer language filter on this project. No new evidence changes that
  item's low current priority.

---

## Summary

Evidence-based ranking, using this review's own data rather than `backlog.md`'s
stated order: **the mypy/ruff/generic net-negative-compression finding (QB-052,
scope-corrected) is the single largest remaining opportunity**, ahead of QB-041/
QB-055's git-diff work, on the basis of larger real-world reach (48% of real
filtered traffic vs. one already-positive filter), lower implementation risk (bug
fix vs. open algorithm-design questions QB-055's own entry admits aren't resolved),
and a correctness framing (Anti-Goal #9) that this project's own stated principles
already rank above further optimization of an already-working filter. This is not a
new QB item — it is hard evidence that an existing one is under-scoped and
under-prioritized relative to what real usage now shows.
