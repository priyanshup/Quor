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
- Except for `group_repeated` and `collapse_unchanged_context` (which rewrite
  one placeholder line per collapsed run), stages only ever mark lines
  `COMPRESS`/`PROTECT`/`KEEP` — they don't rewrite content.

"Typical token savings" figures below are drawn from the 60-case benchmark
corpus reported in `backlog.md` (QB-051, 2026-07-14) where available. They are
directional (±20% char/4 token estimate, small hand-curated corpus), not a
guarantee — see `backlog.md`'s own caveats around that table before treating
them as precise.

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

**Typical token savings:** High impact tier — 18.4% of total benchmark tokens
saved, ~17.9% average reduction per fire, fires on 100% of benchmarked cases.

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

**Typical token savings:** Low impact tier — 0.1% of total benchmark tokens
saved, ~0.3% average reduction per fire (100% activation, but the effect size
per fire is small in this corpus).

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

**Typical token savings:** Low impact tier — 0.0% of total benchmark tokens
saved, ~0.2% average reduction per fire — this corpus has little raw
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

**Typical token savings:** High impact tier by total contribution — 32.4% of
all benchmark tokens saved — but that comes from firing on effectively every
case (100% activation) for a small trim each time (~2.2% average per fire),
not from deep, structure-aware compression. `backlog.md` explicitly warns
against reading this as "max_tokens is more valuable than strip_lines";
it's cheap-and-broad, not sophisticated.

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

**Typical token savings:** Not present in the QB-051 benchmark stage table in
`backlog.md` — no measured figure available.

**Languages/filters using it:** Whole-output shortcuts like a clean `git
status` or a successful build summary (QB-010).

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

**Typical token savings:** Not present in the QB-051 benchmark stage table in
`backlog.md` — no measured figure available. Its value is mostly enabling
downstream dedup/grouping stages rather than saving tokens directly itself.

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

**Typical token savings:** Not present in the QB-051 benchmark stage table in
`backlog.md` — no measured figure available.

**Languages/filters using it:** Useful for any filter dealing with long
single-line payloads (long stack traces, JSON blobs, long file paths).

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
merge). This is one of two stages (with `collapse_unchanged_context`) that
rewrites line content rather than only toggling decisions.

**Safety level:** Medium-High. Deterministic and pattern-driven, but the
default shape-based grouping can merge genuinely different lines that share
a pattern — mitigated by the `exact_match` opt-in for filters that need
byte-identical grouping.

**Typical token savings:** Low impact tier — 2.7% of total benchmark tokens
saved, but ~14.1% average reduction per fire (100% activation) — a large
effect when it fires, on a modest share of cases in this corpus.

**Languages/filters using it:** mypy (`build.toml`, shape-based grouping of
the same error recurring at different lines), ESLint (`node.toml`,
`exact_match=True` to avoid merging distinct diagnostics), npm/npx/pnpm/yarn
(deprecation/peer-dependency warning grouping).

**Limitations:** Only collapses *consecutive* matches — a repeated shape
separated by unrelated lines is never caught (the exact gap QB-044 targets
for test-output cross-run summarization). `backlog.md`'s QB-052 also
documents a real-world negative case: mypy's `min_count=3` threshold means
2-of-a-kind repeats never collapse, which combined with other factors
produced measured *negative* compression (-41.2% avg) in real usage —
flagged as an open bug fix, not a defect in this stage's own logic.

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

**Typical token savings:** Highest-total-contribution stage in the current
corpus — High impact tier, 44.1% of all benchmark tokens saved, ~43.1%
average reduction per fire, 100% activation. `backlog.md` calls this "the
single best-performing mechanism Quor has" and the strongest evidence behind
QB-046 (extending it to more languages).

**Languages/filters using it:** Per its own module docstring, **not yet
wired into any built-in filter** as of this writing — it's the reusable
framework counterpart to `python_ast_summarize`, proven via direct unit
tests and via `python_ast_summarize`'s own unchanged behavior. The registry
it dispatches through (`quor/pipeline/ast_summarize/registry.py`) currently
has analyzers for `python`, `javascript`, `typescript`, and `tsx` (per
`backlog.md`'s QB-046 entry); `code_ast_summarize` is the intended future
entry point for JS/TS/TSX filters once they're wired to use it explicitly.

**Limitations:** Only compresses whatever an analyzer is registered for —
languages without a registered analyzer (e.g. Go, Rust, Java, C#) pass
through completely untouched (documented as QB-046, "planned but not
implemented" below). A parse failure on genuinely malformed/non-source input
reverts the entire stage's effect for that file, not just the unparseable
part.

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

**Typical token savings:** Low total-contribution but very high per-fire
value — 2.4% of total benchmark tokens saved (low share only because this
corpus has few Python cases relative to Git/JS/TS ones — a corpus-composition
artifact per `backlog.md`, not a quality signal), but ~44.3% average
reduction per fire, 100% activation — essentially identical per-fire quality
to `code_ast_summarize`'s 43.1%, as expected since they share the same
underlying analyzer.

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
itself). Like `group_repeated`, this is one of the two stages that rewrites
line content (the placeholder) rather than only toggling decisions.

**Safety level:** High. Never touches `PROTECT`/`COMPRESS` lines (edits, hunk
headers, conflict markers per ADR-031 are never candidates), only ever
collapses lines already decided `KEEP` by earlier stages, and guards against
degenerate short-run replacement via `min_collapse`.

**Typical token savings:** Not present in the QB-051 benchmark stage table in
`backlog.md` (introduced for QB-041, git-diff compression) — no measured
figure available yet; `backlog.md`'s QB-041 evidence update notes git-diff
currently converts only ~26% on average in real usage, which is the gap this
stage (and QB-055's further design) targets.

**Languages/filters using it:** Built for git-diff/git-show compression
(QB-041); not language-specific — applicable to any filter with a mix of
`PROTECT`ed edit lines and long unchanged `KEEP` runs.

**Limitations:** Purely positional (fixed-size context window) — has no
concept of "same shape repeats across multiple hunks" (that's QB-055's
proposed repetitive-hunk collapsing, not yet implemented) or "this whole
file's diff is generated noise" (QB-055's proposed huge-unchanged-region
summarization, also not yet implemented). Only ever collapses runs already
marked `KEEP`; it cannot loosen an existing `PROTECT` decision, so it has no
effect on filters where too much content is already protected.

---

## Algorithms planned but not yet implemented (from backlog.md)

The following are proposed compression mechanisms tracked in `backlog.md`
that do not yet exist as stages (or, for QB-039, as a cross-cutting mode) in
`quor/pipeline/stages/`:

- **QB-055 — Context-aware hunk compression (diff semantics).** The
  worked-out algorithm for git-diff's next compression step: collapse
  *repetitive* hunk shapes across multiple files/hunks (the `group_repeated`
  instinct applied to whole hunks instead of lines), and summarize genuinely
  huge unchanged regions (e.g. a regenerated lockfile) as a one-line summary
  with a recovery link — while `+`/`-` lines and their immediate context
  remain unconditionally preserved. Builds directly on
  `collapse_unchanged_context`, which only handles the "fixed context
  window" half of this design. Status: proposed, not scoped or implemented.

- **QB-046 — AST-aware summarization for more languages (Go, Rust, Java,
  C#).** Extends the `tree-sitter`-based analyzer framework behind
  `code_ast_summarize`/`python_ast_summarize` (currently registered for
  Python, JavaScript, TypeScript, TSX) to four more languages, following the
  same signature-preserved, body-compressed pattern. Status: proposed, no
  language chosen as "first" yet — sequenced last in `backlog.md`'s current
  priority order pending real usage evidence.

- **QB-040 — Config & structured-data file compression (YAML/JSON/TOML/
  .env/.ini).** No filter or stage exists yet for structure-aware
  compression of config files — e.g. collapsing long homogeneous arrays in a
  lockfile while preserving schema/key shape, or stripping comments/blank
  lines from `.env` files without ever touching a value (must compose with
  the existing secret scanner, QB-029). Status: proposed, demoted to `Next`
  pending a benchmark category to measure it against.

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
