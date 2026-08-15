# Compression Algorithms

This document catalogs every compression stage currently implemented in
`quor/pipeline/stages/`. Each stage is a discrete, composable unit that a
filter's TOML config wires into a pipeline (`[[filter.stages]] type = "..."`);
a filter typically chains several stages together.

All stages share the same invariants (enforced by `quor/pipeline/stages/base.py`
and the pipeline engine):

- A stage never mutates its input `ContentMask`; it returns a new one.
- A `PROTECT` decision, once set, can never be downgraded by a later stage.
- `preserve_patterns` (present on every stage's config) always wins over that
  stage's own compression logic for a matching line.
- User-supplied regex patterns are compiled with the `regex` package and run
  under a per-match timeout (fail-open: a timeout skips that pattern/line with
  a warning rather than hanging or crashing the pipeline).
- Most stages only ever mark lines `COMPRESS`/`PROTECT`/`KEEP` — they don't
  rewrite content. Seven stages are the exception, each in its own
  documented, narrow way (see each stage's own entry below for the exact
  shape): `group_repeated`, `collapse_unchanged_context`,
  `structured_data_summarize`, and `numeric_range_compression` each rewrite
  the first line of a collapsed run to a summary and `COMPRESS` the rest;
  `path_prefix_fold` inserts one new header line and rewrites every line in
  the run to its own suffix; `relative_timestamp_compression` rewrites every
  line but the first to a `+delta` form, `COMPRESS`ing none of them; and
  `column_padding_compression` rewrites a single qualifying line's own
  content in place, independent of any run.

"Typical token savings" figures below are drawn from `docs/BENCHMARKS.md`'s
"Stage contribution" table, itself generated from the current 153-case
benchmark corpus (`tests/benchmarks/manifest.toml`, 2026-08-01). Three
distinct numbers are reported per stage, and they answer different
questions — not interchangeable:

- **Contribution** — this stage's share of *every token saved by any stage,
  corpus-wide*. A high contribution can mean "saves a lot each time" or
  "runs on almost everything for a small trim each time" — the other two
  columns disambiguate which.
- **Activation** — the fraction of times this stage actually ran (as
  opposed to being skipped) *among filters it's wired into* — not a
  percentage of the whole 153-case corpus. Every stage in this document
  currently activates 100% of the time it's reached, because none of them
  skip based on content type; they each fail open internally (no-op) when
  their own input doesn't match what they're looking for, which still
  counts as "ran," not "skipped."
- **Avg saved per fire** — the average token reduction on the cases this
  stage actually changed something on.

**Impact tier** (High/Medium/Low) is Contribution-based: High is ≥15%,
Medium is ≥5%, Low is below that (`quor/analytics/effectiveness.py`'s own
thresholds).

These are **benchmark-corpus measurements only** — a hand-curated set of
realistic sample commands, not a random sample of real usage. They are also
approximate (±20% char/4 token estimate). Separately, QB-054 (shipped)
measures real, per-project usage live via `quor gain --filters`/
`quor doctor` — where a stage's benchmark figures and a specific project's
real-usage figures diverge, that command pair is the authoritative,
project-specific source, not this document; see `docs/BENCHMARKS.md`'s "Real-
world vs. benchmark observations" for a worked example of how far apart the
two can be.

---

## strip_lines

**Purpose:** Drop lines that match a known-noise pattern, while giving
higher-priority patterns a way to force-keep specific lines no matter what.

**Layman explanation:** You give it two lists of patterns — "always delete
lines that look like this" and "never delete lines that look like this."
Deletion patterns lose to keep patterns.

**Technical explanation:** For each `KEEP`-decision line, `preserve_patterns`
is checked first (match → `PROTECT`), then `patterns` (match → `COMPRESS`).
Lines already `PROTECT` or `COMPRESS` from an earlier stage pass through
untouched — this stage never resurrects or downgrades a prior decision.
Patterns are compiled once via `_compile` (LRU-cached) and matched with
`regex`'s timeout-guarded `search`.

**Safety level:** High. Purely pattern-driven, deterministic, no line-count
change, `preserve_patterns` is an explicit escape hatch.

**Typical token savings:** High impact tier — 19.5% contribution, 100%
activation, ~14.5% average reduction per fire.

**Languages/filters using it:** Broadly used across built-in filters (e.g.
git, npm/node, generic command output) as the first-line noise remover before
more specialized stages run.

**Limitations:** Only operates on whole-line matches via regex; can't express
"delete this line unless it's part of a larger structure" — that's what
`code_ast_summarize`/`python_ast_summarize` are for. A misconfigured pattern
that's too broad can silently over-compress since there's no structural
awareness of what a line "means."

---

## deduplicate_consecutive

**Purpose:** Collapse immediately-repeated identical lines (e.g. a progress
bar or spinner re-printing the same line many times).

**Layman explanation:** If the exact same line appears twice in a row, only
the first copy is kept.

**Technical explanation:** Tracks the content of the last kept (`KEEP` or
`PROTECT`) line. If the next `KEEP` line is byte-identical to it, it's marked
`COMPRESS`; otherwise it becomes the new "last kept" reference.
`preserve_patterns` matches are promoted to `PROTECT` before the duplicate
check (and always break/reset the run, since they always survive).

**Safety level:** High. Exact-match only, no fuzzy logic, strictly adjacent
lines, never removes the first occurrence.

**Typical token savings:** Low impact tier — 0.1% contribution, 100%
activation, ~0.2% average reduction per fire — the effect size per fire is
small in this corpus.

**Languages/filters using it:** General-purpose; useful wherever tools emit
repeated status lines.

**Limitations:** Only catches exact, immediately-adjacent duplicates — a
duplicate separated by even one different line is not caught (that's the gap
`group_repeated` and QB-044's proposed cross-run summarization address for
non-adjacent repeats).

---

## remove_ansi

**Purpose:** Strip terminal escape-code-only lines (e.g. leftover cursor
movement/color-reset sequences from a captured terminal session).

**Layman explanation:** Deletes lines that are just invisible terminal
formatting codes and have no real text on them.

**Technical explanation:** Uses a hardcoded `re` pattern (`\x1b\[[0-9;]*[A-Za-z]`,
stdlib `re`, not the timeout-guarded `regex` package, since it's not
user-supplied) to detect ANSI escape sequences. A line is compressed only if
it contains at least one ANSI code *and* nothing else survives after
stripping all such codes and whitespace. Lines with real content alongside
ANSI codes are left untouched (content, not just the codes, is preserved).

**Safety level:** High. Hardcoded, narrow pattern; only ever removes lines
with zero printable content.

**Typical token savings:** Low impact tier — 0.0% contribution, 100%
activation, ~0.1% average reduction per fire — this corpus has little raw
ANSI-laden output; more relevant to filters that capture live terminal
sessions.

**Languages/filters using it:** General-purpose; most useful for any filter
that ingests raw captured terminal/build output.

**Limitations:** Doesn't strip ANSI codes *from* a line with real content
(no de-coloring) — it only ever removes whole lines that are 100% escape
codes. A line with one visible character and heavy ANSI noise around it is
left completely alone.

---

## max_tokens

**Purpose:** Enforce a best-effort token budget on the final output by
compressing `KEEP` lines once a limit is exceeded.

**Layman explanation:** If the output is still too long after everything
else runs, cut it down to roughly the size you asked for — keeping the
start, the end, or both, depending on the strategy you pick. Anything
marked "must keep" is never touched, even if that means going over budget.

**Technical explanation:** Estimates tokens as `ceil(len(line)/4)` per line
(the same char/4 heuristic used throughout Quor, labeled ±20% everywhere).
`preserve_patterns` are applied first (promoted to `PROTECT`). Current token
usage is the sum over all non-`COMPRESS` lines; if under `limit`, the stage
no-ops. Otherwise it computes which line indices to keep under one of three
strategies — `head` (keep the start, compress the tail), `tail` (keep the
end, compress the head — the default), or `both` (keep `limit//2` tokens at
each end, compress the middle) — and marks everything outside that budget
`COMPRESS`. `PROTECT` lines always count toward the total but are never
compressed; if `PROTECT` content alone exceeds `limit`, the output legitimately
exceeds the configured budget (documented as expected, not a bug — ADR-031 /
QB-004).

**Safety level:** Medium-High. Deterministic and `PROTECT`-respecting, but by
design it can be a blunt instrument — see Limitations.

**Typical token savings:** High impact tier — 18.6% contribution, 100%
activation, ~1.4% average reduction per fire — the lowest per-fire average
of any High-tier stage. Its total contribution comes from firing broadly
for a small trim each time, not from deep, structure-aware compression;
don't read a high contribution figure here as "more valuable than
strip_lines" — they're High tier for different reasons (breadth vs. depth).

**Languages/filters using it:** General-purpose backstop; used broadly as a
final safety-net stage across many built-in filters.

**Limitations:** Shallow — it doesn't understand structure, it just cuts by
line position within the budget. Doesn't (and by ADR-031 design won't)
compress `PROTECT` content, so filters with heavy `preserve_patterns` use
(notably git-diff) get little benefit from it — the exact gap QB-041/QB-055/
QB-039 (Balanced/Aggressive modes) target. `both` strategy can produce an
odd result if `limit//2` splits awkwardly against actual content boundaries.

---

## match_output

**Purpose:** Whole-output short-circuit — if the *entire* rendered output
matches a known "nothing interesting happened" shape (e.g. a clean `git
status`), collapse it to a one-line summary and skip the rest of the
content.

**Layman explanation:** If the whole output looks exactly like "everything's
fine, nothing to report," replace all of it with a single short sentence
saying so.

**Technical explanation:** Renders the mask's current output
(`mask.render()`) and checks a `fullmatch` (not `search`) against
`config.pattern` using the timeout-guarded `_fullmatch`. Refuses to fire at
all if any line is already `PROTECT` — collapsing would break the
index-based PROTECT-restoration mechanism the pipeline engine relies on
(documented explicitly as the highest-risk stage in the pipeline for this
reason). On match, the first line becomes `summary` (`KEEP`), and every
other line is marked `COMPRESS`, preserving line count. Also emits an
explicit `warnings.warn` on every firing so a short-circuit is never silent.

**Safety level:** Medium — explicitly called out in its own module docstring
as the highest-risk stage in the pipeline, mitigated by: opt-in-only TOML
config, refusing to fire over any `PROTECT` content, preserving line count,
and always tracing/warning when it fires.

**Typical token savings:** No measured figure available — not currently
wired into any built-in filter (verified directly against
`quor/filters/builtin/*.toml`), so it never appears in a benchmark run at
all. Implemented and unit-tested (`tests/unit/test_stages.py`), available to
any project-local or plugin filter that wants a whole-output shortcut.

**Languages/filters using it:** None currently, among built-in filters.
Originally motivated by whole-output shortcuts like a clean `git status` or
a successful build summary (QB-010).

**Limitations:** Requires an exact full-output match — any unexpected
extra line (even a warning) prevents it from firing, by design. Cannot
partially collapse output; it's all-or-nothing. Cannot fire once any
`PROTECT` line exists in the mask, even if the match would otherwise be
correct.

---

## regex_replace

**Purpose:** Normalize high-entropy content (UUIDs, timestamps, hashes, file
paths) so that later stages like `deduplicate_consecutive` and
`group_repeated` can recognize lines as "the same" despite noisy details.

**Layman explanation:** Find-and-replace on each line — e.g. turning a
random UUID into a placeholder — so that lines which only differ by that
noise start looking identical to later compression steps.

**Technical explanation:** Applies an ordered list of `(pattern,
replacement)` rules to each `KEEP` line via `regex`'s timeout-guarded `.sub()`
(supports backreferences like `\1`/`\g<name>` natively). `preserve_patterns`
matches are promoted to `PROTECT` and skip substitution entirely. Each rule
is applied in declared order; a per-rule timeout fails open (warns and skips
just that rule, not the whole line).

**Safety level:** Medium. Rewrites line *content* (not just decisions) —
correctness depends entirely on how well-scoped the configured
patterns/replacements are; a careless pattern could alter meaningful text.

**Typical token savings:** Low impact tier — 1.1% contribution, 100%
activation, ~51.2% average reduction per fire — the highest per-fire average
of any stage in the current corpus, on the modest set of cases it directly
rewrites. Its main value is still mostly enabling downstream dedup/grouping
stages rather than saving tokens directly itself; this figure only counts
cases where a rule substitution measurably shortened the line.

**Languages/filters using it:** Any filter needing to normalize noisy
identifiers before deduplication/grouping (documented rationale references
UUIDs, timestamps, hashes, file paths as the primary use case).

**Limitations:** Doesn't compress by itself in the common case — it's an
enabler for other stages. Ordering matters (rules apply sequentially, so
overlapping patterns can interact unexpectedly) and a bad replacement could
alter meaningful content rather than just noise, since there's no semantic
awareness of what's being replaced.

---

## truncate_lines

**Purpose:** Cap the length of individual long lines (stack traces, JSON
payloads, long paths) without changing how many lines exist.

**Layman explanation:** If a single line is extremely long, cut it down to a
maximum length and mark clearly that it was cut, rather than deleting it
entirely.

**Technical explanation:** For each `KEEP` line longer than `max_length`
characters, cuts it to `max_length` total (including an appended `marker`,
default `…[truncated]`) so the cut is visible rather than silent. If
`marker` itself is `>= max_length`, falls back to a hard cut with no marker
rather than exceeding the limit or emitting a bare marker. `preserve_patterns`
matches are promoted to `PROTECT` and left full-length. Line count is always
preserved; only content and length change.

**Safety level:** High. Deterministic, visibly marks its own truncation,
never changes line count, `PROTECT` lines are exempt.

**Typical token savings:** No measured figure available — not currently
wired into any built-in filter (verified directly against
`quor/filters/builtin/*.toml`), so it never appears in a benchmark run at
all. Implemented and unit-tested (`tests/unit/test_stages.py`), available to
any project-local or plugin filter dealing with long single-line payloads.

**Languages/filters using it:** None currently, among built-in filters.

**Limitations:** Truncates blindly by character count with no understanding
of the line's structure — could cut a JSON value or path midway in a way
that loses the specific piece of information that mattered. Only caps
length; doesn't reduce line *count*.

---

## group_repeated

**Purpose:** Collapse a consecutive run of N+ lines that share the same
shape (or, optionally, identical text) into one summary line plus a count.

**Layman explanation:** Instead of showing "WARNING: disk low" five times in
a row, show it once with "(×5)" appended, and drop the other four.

**Technical explanation:** For each configured pattern, runs a separate
collapse pass: walks the line list, and wherever `min_count`-or-more
consecutive `KEEP` lines match the pattern, replaces the run with the first
line's content plus a `(×N)` suffix (`KEEP`, Unicode U+00D7 multiplication
sign) and marks the remaining lines in the run `COMPRESS`. `PROTECT` and
`COMPRESS` lines always break a run. Default `exact_match=False` groups by
*shape* — same pattern match, different text is still grouped (e.g. the same
mypy error message recurring at different line numbers) — because several
shipped filters depend on that default. `exact_match=True` is an opt-in that
additionally requires byte-identical text to continue a run (used by
ESLint's filter, where same-shape-different-rule diagnostics must never
merge). One of the seven stages in this document that rewrites line content
rather than only toggling decisions — see the invariants list above.

**Safety level:** Medium-High. Deterministic and pattern-driven, but the
default shape-based grouping can merge genuinely different lines that share
a pattern — mitigated by the `exact_match` opt-in for filters that need
byte-identical grouping.

**Typical token savings:** Medium impact tier — 13.7% contribution, 100%
activation, ~9.7% average reduction per fire.

**Languages/filters using it:** mypy (`build.toml`, shape-based grouping of
the same error recurring at different lines), ESLint (`node.toml`,
`exact_match=True` to avoid merging distinct diagnostics), npm/npx/pnpm/yarn
(deprecation/peer-dependency warning grouping).

**Limitations:** Only collapses *consecutive* matches — a repeated shape
separated by unrelated lines is never caught (the exact gap QB-044 targets
for test-output cross-run summarization). `backlog.md`'s QB-052 also
documented a real-world negative case: mypy's `min_count=3` threshold means
2-of-a-kind repeats never collapse, which combined with other factors
(unconditional dispatcher-level additions, not a defect in this stage's own
logic) produced measured *negative* compression (-41.2% avg) in real usage.
Resolved 2026-07-31 — see `backlog.md`'s QB-052 entry for the fix.

---

## code_ast_summarize

**Purpose:** Generic, multi-language framework that compresses a source
file's function/method bodies down to signature + docstring, keeping the
API surface and dropping implementation detail.

**Layman explanation:** For a supported programming language, show the
"shape" of each function (its name, parameters, and docstring) but hide the
actual code inside it, the same way an API reference would.

**Technical explanation:** Reads a `language` field from its config and
looks up an analyzer via `quor/pipeline/ast_summarize/registry.py::get_analyzer()`.
Parses the *original* line sequence (`mask.lines`, not the already-compressed
`mask.render()`) to keep a 1:1 index↔line-number mapping regardless of what
upstream stages already decided, then marks every line the analyzer reports
as "body" `COMPRESS` (never rewritten — every kept line is byte-identical to
the source). `preserve_patterns` still applies. Two genuinely different
fail-open paths, by design: an **unsupported language** (no analyzer
registered) makes `apply()` return the mask completely unchanged, silently —
this is deliberately not surfaced as an error since a language-agnostic
filter shouldn't break on unlisted languages. A **parse failure for a
supported language** (e.g. invalid syntax) is *not* caught here — it
propagates to the pipeline engine's own per-stage fail-open handling, which
reverts that stage's effect entirely and logs a warning.

**Safety level:** High for supported/parseable input (never regenerates or
reformats kept text); relies on the pipeline engine's fail-open handling for
unparseable input, which reverts to the unmodified original rather than
producing corrupted output.

**Typical token savings:** High impact tier — 30.1% contribution, 100%
activation, ~40.7% average reduction per fire. Highest total contribution of
any stage in the current corpus by a wide margin (`strip_lines`, the next
highest, is at 19.5%) — `backlog.md` cited this as the strongest evidence
behind QB-046 (extending it to Go/Rust/Java/C#, since shipped). Its
average-per-fire figure is no longer the single highest, though — several
lower-total-contribution stages (`structured_data_summarize`,
`regex_replace`, `python_ast_summarize`) now measure a higher average
reduction per fire; see those stages' own entries.

**Languages/filters using it:** Wired into six built-in filters —
`cat-javascript.toml`, `cat-typescript.toml`, `cat-go.toml`, `cat-java.toml`,
`cat-rust.toml`, and `cat-csharp.toml` — the generic, reusable framework
counterpart to `python_ast_summarize` (which stays Python-specific; see its
own entry below). The registry it dispatches through
(`quor/pipeline/ast_summarize/registry.py`) has analyzers for `python`,
`javascript`, `typescript`, `tsx`, `go`, `java`, `rust`, and `csharp` as of
QB-046 (shipped) — the four-language expansion this document previously
listed under "planned but not yet implemented" below.

**Limitations:** Only compresses whatever an analyzer is registered for —
languages without a registered analyzer pass through completely untouched.
A parse failure on genuinely malformed/non-source input reverts the entire
stage's effect for that file, not just the unparseable part.

---

## python_ast_summarize

**Purpose:** The Python-specific counterpart to `code_ast_summarize` —
compresses Python function/method bodies to signature + docstring using the
same shared analyzer framework.

**Layman explanation:** Same idea as `code_ast_summarize`, but specifically
for Python files, and it's the one actually wired into Quor's shipped
Python filter today.

**Technical explanation:** A thin, Python-specific wrapper: it always calls
`get_analyzer("python")` (never driven by a `language` config field) from
the same registry `code_ast_summarize` uses, so there is exactly one
implementation of Python's body-compression logic shared by both stages,
not two. Same line-sequence-based parsing (`mask.lines`, not `render()`),
same `preserve_patterns` handling, same "kept lines are byte-identical to
source, never regenerated" guarantee. Fail-open behavior mirrors
`code_ast_summarize`'s second case: a parse failure (e.g. `SyntaxError`) is
deliberately not caught locally and propagates to the pipeline engine's
existing per-stage fail-open handling, which reverts to the unmodified
original file. Historically, this stage's parsing logic *was* the
implementation (stdlib `ast`); it has since moved, unmodified, into the
shared `quor/pipeline/ast_summarize/` framework, with this stage now
delegating to it — this stage's own class name, config shape, and observable
behavior are documented as unchanged by that refactor.

**Safety level:** High — identical safety profile to `code_ast_summarize`
for the one language it targets (parsing only, never regenerating source;
fail-open reverts to original on unparseable input).

**Typical token savings:** Low impact tier — 2.2% contribution (low share
only because this corpus has relatively few Python cases relative to
Git/JavaScript/config-file ones — a corpus-composition artifact, not a
quality signal), 100% activation, ~48.6% average reduction per fire — the
second-highest per-fire average of any stage in the current corpus, close
to but no longer identical to `code_ast_summarize`'s 40.7% (both share the
same underlying analyzer, so the gap reflects which files each currently
processes, not a difference in the analyzer itself).

**Languages/filters using it:** Python only — wired into `cat-python.toml`.

**Limitations:** Python-only by design (stdlib `ast`, no `language` config
option) — cannot be repointed at another language; that's exactly what
`code_ast_summarize` exists for. Same parse-failure fail-open caveat as
`code_ast_summarize`: a genuinely invalid `.py` file causes this stage's
effect to be reverted entirely for that file.

---

## collapse_unchanged_context

**Purpose:** Collapse the middle of long runs of unchanged (`KEEP`) lines —
built specifically for git-diff compression, where every `+`/`-`/`@@` line
is already `PROTECT`ed but ordinary unified-diff context lines were
previously left as plain `KEEP` with nothing compressing them on large
diffs.

**Layman explanation:** In a diff, keep a few lines of unchanged context
right before and after every actual change (like `git diff -U<n>` does), but
if there's a long stretch of unchanged lines in the middle with no edits
nearby, replace most of it with a single "N unchanged lines omitted" line.

**Technical explanation:** Walks the line list, splitting it into runs of
consecutive `KEEP` lines (any `PROTECT`/`COMPRESS` line ends a run and passes
through as a boundary). For each run, if the "middle" — the run minus
`context_lines` kept at each end — has at least `min_collapse` lines, that
middle is replaced with one placeholder `KEEP` line ("... N unchanged lines
omitted ...") and the rest of the middle is marked `COMPRESS`; the
`context_lines`-sized head and tail of the run are always kept verbatim. Runs
below the `min_collapse` threshold are left entirely untouched (avoids
replacing a single leftover line with a placeholder longer than the line
itself). Like `group_repeated`, one of the seven stages in this document
that rewrites line content (the placeholder) rather than only toggling
decisions — see the invariants list above.

**Safety level:** High. Never touches `PROTECT`/`COMPRESS` lines (edits, hunk
headers, conflict markers per ADR-031 are never candidates), only ever
collapses lines already decided `KEEP` by earlier stages, and guards against
degenerate short-run replacement via `min_collapse`.

**Typical token savings:** Low impact tier — 3.4% contribution, 100%
activation, ~12.7% average reduction per fire (benchmark corpus). Separately,
in real usage, `backlog.md`'s QB-041 evidence update found git-diff
converting only ~26% on average — a real-world figure, not a benchmark one,
and the gap this stage (and QB-055's further design) targets.

**Languages/filters using it:** Built for git-diff/git-show compression
(QB-041); not language-specific — applicable to any filter with a mix of
`PROTECT`ed edit lines and long unchanged `KEEP` runs.

**Limitations:** Purely positional (fixed-size context window) — has no
concept of "same shape repeats across multiple hunks" (that's QB-055's
proposed repetitive-hunk collapsing, not yet implemented) or "this whole
file's diff is generated noise" (QB-055's proposed huge-unchanged-region
summarization, also not yet implemented). Only ever collapses runs already
marked `KEEP`; it cannot loosen an existing `PROTECT` decision, so it has no
effect on filters where too much content is already protected — which is
exactly why git-diff's own `preserve_patterns` correctness mattered: an
over-broad pattern silently starves this stage of eligible lines. Fixed
2026-07-15 (see backlog.md's QB-041 "Fix update") — git-diff's
`preserve_patterns` carried bare `'conflict'`/`'Error'` substring matches
that force-protected ordinary context lines merely mentioning an
Error-suffixed identifier, fragmenting runs this stage could otherwise have
collapsed whole.

---

## structured_data_summarize

**Purpose:** Collapse long, homogeneous JSON/YAML/TOML arrays (or TOML
array-of-tables) down to their first few elements plus an omitted-count
placeholder, using each format's real parser rather than line-pattern
guessing.

**Layman explanation:** For a config or lockfile with a long list of
near-identical entries (dozens of dependency records, for example), show
the first few in full and one line saying how many more were omitted —
every key and value that *is* shown stays byte-for-byte accurate.

**Technical explanation:** Reads a `format` field from its config
(`"json"`/`"yaml"`/`"toml"`) and dispatches to a per-format analyzer
(`quor.pipeline.structured_data.registry.get_analyzer()`) that parses the
*original* line sequence with the format's real parser (stdlib `json`,
PyYAML, stdlib `tomllib`) to find array/array-of-tables boundaries a
line-pattern stage can't safely detect (nested brackets, a string
containing `[`/`]`, etc.). For each collapsible range found, the first line
is rewritten to an "N more items omitted (M total)"-style summary and the
rest of the range is marked `COMPRESS` — the same "rewrite the first line of
a run, compress the rest" technique `group_repeated`/
`collapse_unchanged_context`/`numeric_range_compression` use.
`preserve_patterns` still applies; a `PROTECT`ed line anywhere inside a
would-be-collapsed range cancels collapsing that whole range, rather than
partially collapsing around it.

**Safety level:** High for supported/parseable input — every kept line is
byte-identical to source, and collapsing is driven by the format's own real
parser, not a line-count or regex guess. Relies on the pipeline engine's
fail-open handling for a genuinely malformed file, which reverts this
stage's effect entirely rather than producing a corrupted collapse.

**Typical token savings:** Medium impact tier — 6.8% contribution, 100%
activation, ~54.0% average reduction per fire — the highest average-per-fire
saving of any stage in the current corpus, on the modest share of cases
that are JSON/YAML/TOML files.

**Languages/filters using it:** `cat-json.toml` and `cat-yaml.toml` (array
collapsing); `cat-toml.toml` (array-of-tables only — TOML has no stdlib
position-tracking API for general values, so support there is deliberately
narrower). `.env`/`.ini` config files use `strip_lines` instead — their
grammar has no array structure to collapse.

**Limitations:** TOML support only covers array-of-tables (`[[name]]`
blocks), not inline arrays or plain tables. Never touches a dict/mapping
key or a heterogeneous array — only a long, homogeneous run collapses. A
malformed file reverts this stage's entire effect for that file, not just
the unparseable part.

---

## path_prefix_fold

**Purpose:** Front-code a consecutive run of path-like lines that share a
directory (or other separator-delimited) prefix into one header line plus
each line's own shortened suffix.

**Layman explanation:** Like a file-tree view — a list of files that all
live under the same folder is shown as "folder/ (N entries):" followed by
just each file's own name, instead of repeating the full path on every
line.

**Technical explanation:** For a run of consecutive `KEEP` lines matching
the filter-declared `patterns`, computes the longest shared character
prefix across the run, trims it back to the last `separator` occurrence (so
a fold never splits a filename mid-token), and — only if doing so is
estimated to cost strictly fewer tokens than leaving the run alone —
rewrites the run to one new header line (`prefix (N entries):`) followed by
every original line rewritten to its own suffix. Nothing is discarded:
every original line is exactly reconstructible as header-prefix plus its
own suffix. `separator` is configurable, so the same stage handles
filesystem paths (`/`) and other prefix-delimited shapes.

**Safety level:** High. Deterministic prefix computation, a strict
token-cost gate, and full reconstructibility — no information is lost, only
the shared prefix's repetition.

**Typical token savings:** Low impact tier — 0.4% contribution, 100%
activation, ~4.2% average reduction per fire. The token-cost gate means it
declines to fold more often than the activation figure alone would suggest
— a run whose shared prefix is too short to be worth a header line is left
untouched.

**Languages/filters using it:** `z_generic.toml` (the universal fallback
filter, `separator = "/"`, for filesystem-path-shaped output like `find`/
`rg --files` listings) and `ci.toml`'s Gradle handling (`separator = ":"`,
for `> Task :module:name` lines).

**Limitations:** Requires a filter author to declare which lines are
path-like via `patterns` — there's no built-in "looks like a path"
heuristic, since guessing that shape from arbitrary text risks folding
lines that only coincidentally contain the separator. Scope is
single-level: one shared prefix per run, not a nested directory tree.

---

## numeric_range_compression

**Purpose:** Collapse a consecutive run of standalone-integer lines
(nothing else on the line) into one inclusive `start-end` range.

**Layman explanation:** A list of consecutive line numbers or IDs — `101`,
`102`, `103` — becomes one line, `101-105`, instead of five separate lines.

**Technical explanation:** Matches `KEEP` lines that are nothing but digits
(`^\d+$`), and folds a run where each value is exactly one more than the
previous, every line shares the same string width (preserving zero-padding
fidelity — `"001"`/`"002"`/`"003"` folds to `"001-003"`, not `"1-3"`), and
the fold is estimated to cost strictly fewer tokens than the original
lines. Negative numbers are never merged (the range separator `-` would be
ambiguous against a negative sign), and a width change or non-consecutive
value always breaks the run rather than attempting a lossy reformat. No
`patterns` config is needed — "the whole line is only digits" has no
false-positive risk, unlike `path_prefix_fold`'s path-like shape.

**Safety level:** High. A precise structural check (not a shape guess),
strict ascending-by-one and same-width requirements, and the same strict
token-cost gate every run-folding stage in this document uses.

**Typical token savings:** Low impact tier — 0.1% contribution, 100%
activation, ~9.8% average reduction per fire — the smallest measured
contribution of any stage with benchmark evidence. Under the char/4 token
estimate, a same-width run of two-digit-or-longer numbers folding to two
lines is usually a token-count tie rather than a saving (the `-` separator
and the newline it replaces both cost one character), so most 2-line
same-width runs are left unfolded in practice; shorter (single-digit) and
longer runs fold more often.

**Languages/filters using it:** `z_generic.toml`, positioned after
`path_prefix_fold` so a numeric suffix that stage just produced (e.g. a
folded `run/42`/`run/43`) is itself a candidate for further folding.

**Limitations:** Only merges bare, standalone digit lines — `"Line 101"`/
`"Line 102"` (a text prefix plus a number) is out of scope, a different and
harder problem this stage deliberately doesn't attempt.

---

## relative_timestamp_compression

**Purpose:** Rewrite a consecutive run of timestamp-prefixed lines so only
the first line keeps its full timestamp, and every following line shows the
time elapsed since the previous line.

**Layman explanation:** A log with a full date and time on every line —
where what actually matters is how far apart the events were — gets
rewritten so only the first line has the full timestamp, and the rest just
say `+1s`, `+2s`, and so on.

**Technical explanation:** Recognizes seven deterministic timestamp formats
anchored at the start of a line (a plain "space" datetime, several
ISO-8601 variants with or without a fractional second or UTC offset, and
bare time-only forms) — no locale-dependent parsing, no natural-language
dates. A run continues only while every line matches the same format kind,
the same fractional-digit width, and a value no earlier than the previous
line's. Every timestamp is converted to an exact integer nanosecond count
(no floating-point rounding) before computing each delta, rendered using
the largest time unit that divides it evenly. Only folds if the result is
estimated to cost strictly fewer tokens than the original. Unlike every
other folding stage in this document, no line is ever marked `COMPRESS`
and no line is inserted — every line in a folded run stays `KEEP`, with all
but the first rewritten in place to its `+delta` form.

**Safety level:** High. Every supported format is parsed exactly (no
rounding; an explicit UTC offset is normalized to an absolute instant
before diffing, so a run may span a changing offset without ambiguity), a
run breaks on any format/width mismatch or a value going backwards, and the
fold is exactly reconstructible by addition from the first line.

**Typical token savings:** Low impact tier — 2.2% contribution, 100%
activation, ~11.9% average reduction per fire.

**Languages/filters using it:** `node.toml` (an `npm run <script>`
dev-server watch process that timestamps its own rebuild lines) and
`z_generic.toml` (covers `docker logs --timestamps`, `kubectl logs
--timestamps`, ISO-mode `journalctl`, and generic CI/application logs with
no dedicated filter).

**Limitations:** Deliberately excludes journalctl's default syslog-style
prefix (a month name is locale-dependent/natural-language, out of scope by
design) and any timestamp not anchored at the very start of the line (a
bracket-wrapped or otherwise-prefixed timestamp simply isn't recognized). A
run breaks on a timestamp that decreases, since that could be legitimate
clock skew or a day rollover with no date to disambiguate on a time-only
line.

---

## column_padding_compression

**Purpose:** Collapse multi-space column-alignment padding in
machine-generated tabular output (`docker ps`, `kubectl get pods`, `ps
aux`, `df -h`, and similar) down to a single separating space, since an LLM
needs field values and their order, not visual column alignment.

**Layman explanation:** A table whose columns are padded with extra spaces
to line up visually gets each run of alignment spaces collapsed to one
space; every value and its position in the row survive, only the visual
padding is removed.

**Technical explanation:** For each `KEEP` line matching the filter-declared
`patterns`, replaces every run of 2+ literal spaces that has a
non-whitespace character immediately on both sides with a single space (a
tab, or a run touching the start/end of a line, never matches, so
indentation and trailing whitespace are untouched). Processes each
qualifying line independently — unlike the three run-folding stages above,
it needs no multi-line scanning, since collapsing padding inside one line
doesn't depend on its neighbors. Applies the same strict token-cost gate
every folding stage in this document uses. An optional `max_gaps` config
limits how many space-runs on a line are collapsed, for tables with a
trailing free-text column (a commit subject, a process command line) that
can legitimately contain multiple real spaces the stage must not touch.

**Safety level:** Medium-High. Every token's exact spelling and
left-to-right order is preserved — only inter-column gap width changes —
but, unlike `path_prefix_fold`, the original padding width isn't
reconstructible, and a filter that wires this stage into content that
merely looks tabular (e.g. prose with an accidental double space) would
silently collapse that spacing too. The safety boundary is the filter
author's `patterns` opt-in, not something the stage detects on its own.

**Typical token savings:** Low impact tier — 1.8% contribution, 100%
activation, ~16.2% average reduction per fire.

**Languages/filters using it:** `docker.toml`, `git.toml` (`git branch
-vv`), `kubectl.toml`, `pip.toml`, `poetry.toml`, and `unix.toml` (`ps
aux`, `df -h`, `ls -l`). Never wired into `z_generic.toml` — the universal
fallback doesn't know what command produced its input, so it can't safely
declare "these are table rows" without reintroducing the shape-guessing
this stage is built to avoid.

**Limitations:** Requires a filter author to declare which lines are table
rows via `patterns` — a prose sentence with an accidental double space is
indistinguishable from a table row by shape alone. Without `max_gaps` set,
a table whose trailing column can legitimately contain multiple real
spaces is unsafe to wire this stage into.

---

## Algorithms planned but not yet implemented (from backlog.md)

The following are proposed compression mechanisms tracked in `backlog.md`
that do not yet exist as stages (or, for QB-039, as a cross-cutting mode) in
`quor/pipeline/stages/`. (QB-046 — AST-aware summarization for more
languages — and QB-040 — config/structured-data file compression — were
previously listed here; both have since shipped and are removed from this
list. QB-046 is documented under `code_ast_summarize` above; QB-040 is now
documented under `structured_data_summarize` above.)

- **QB-055 — Context-aware hunk compression (diff semantics).** The
  worked-out algorithm for git-diff's next compression step: collapse
  *repetitive* hunk shapes across multiple files/hunks (the `group_repeated`
  instinct applied to whole hunks instead of lines), and summarize genuinely
  huge unchanged regions (e.g. a regenerated lockfile) as a one-line summary
  with a recovery link — while `+`/`-` lines and their immediate context
  remain unconditionally preserved. Builds directly on
  `collapse_unchanged_context`, which only handles the "fixed context
  window" half of this design. Status: proposed, not scoped or implemented.

- **QB-044 — Deeper test-output compression (cross-run summarization).**
  `group_repeated` only collapses *adjacent* matching lines; this item
  proposes recognizing a whole test run dominated by one repeated,
  non-adjacent failure pattern (e.g. the same assertion failing across 40
  parametrized cases) and summarizing it as one shape instead of showing
  every occurrence. Status: proposed, not scoped or implemented.

- **QB-045 — Broader build & CI log compression.** Proposes new *filters*
  (not new stage types — composed from `remove_ansi`, `group_repeated`,
  `strip_lines` with a `preserve_patterns` safety net) for Docker build
  output, generic bundler output, and CI runner logs, which have no
  dedicated filter today. Status: proposed, not scoped or implemented; no
  direct evidence yet either way.

- **QB-039 — Compression Modes: Safe / Balanced / Aggressive.** Not a new
  stage but a proposed cross-cutting mode setting that would change how
  strictly `PROTECT`/`preserve_patterns` is honored once a token budget is
  already exceeded (e.g. letting `max_tokens` compress into currently-
  protected content when a filter has high confidence it's safe). Safe mode
  (today's only behavior, described throughout this document) would remain
  the default. Status: proposed, not scoped or implemented.

- **QB-053 — Adaptive compression (self-tuning aggressiveness per
  filter).** Proposes a feedback loop where a filter's real, measured
  effectiveness (via the tracking DB) automatically adjusts its own
  aggressiveness over time — e.g. loosening `preserve_patterns` for a
  filter with consistently high real volume and a known-conservative
  mechanism — rather than a human hand-tuning each filter's TOML config.
  Explicitly distinct from QB-039 (a static, user-selected dial); this
  would be the system correcting itself from its own evidence. Status:
  proposed, not scoped or implemented — sequenced after QB-054 (telemetry)
  and QB-039's own design pass.
