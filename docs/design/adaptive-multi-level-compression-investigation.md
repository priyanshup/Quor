# Investigation: Adaptive Multi-Level Compression

> Status: **investigation only — no backlog item assigned, no code written.**
> Produced per CLAUDE.md Rule 4 (competitor-first design). Before any new analysis, this
> reuses two pieces of prior art already in this repo: **QB-039** ("Compression Modes:
> Safe/Balanced/Aggressive", `backlog.md`, Proposed/unimplemented) and **ADR-031**
> (`docs/final/DECISIONS.md`, "Token Budget Semantics"), which already rejected a
> closely related mechanism. This document does not re-derive either — it picks up
> QB-039's open design questions and answers them, and treats ADR-031's rejection as a
> hard constraint the recommended design must respect, not a question to reopen.

---

## 0. Naming clash to flag immediately

QB-039's own sketch proposes `quor config set mode=aggressive`. That collides with an
**existing, shipped, differently-scoped field**: `QuorUserConfig.mode` (ADR-009) is
already `"audit" | "optimize" | "simulate"` — a config field that, per `gain.py`'s own
inline comment, today "affects third-party plugins only... compression [runs]
regardless of mode." Reusing the word `mode` for compression aggressiveness would
silently overload a field that already means something else and that users/plugins
already read. **This design uses a distinct field name, `compression_level`,
everywhere** — this is a real landmine QB-039's own sketch would walk into if
implemented literally, worth catching before any code exists.

Also confirmed while checking this: `QuorUserConfig` has no project-level tier today
(unlike `FilterRegistry`, which is project > user > builtin). QB-039's own open
question ("per-invocation or per-project?") is therefore not just a design choice —
project-level scope for *any* `QuorUserConfig` field is infrastructure that doesn't
exist yet. Flagged in §6, deferred rather than silently assumed.

---

## 1. Determine: can existing stages expose configurable aggressiveness?

Read every built-in stage (`quor/pipeline/stages/*.py`). They split cleanly into two
groups:

| Group | Stages | Existing numeric knob | Scalable? |
|---|---|---|---|
| **Threshold-based** | `max_tokens` (`limit`), `truncate_lines` (`max_length`), `group_repeated` (`min_count`), `collapse_unchanged_context` (`context_lines`) | Yes — each already takes a plain integer that directly controls how much it compresses | **Yes, today, with zero new stage code** |
| **Binary / structural** | `strip_lines`, `remove_ansi`, `deduplicate_consecutive`, `match_output`, `code_ast_summarize`, `python_ast_summarize`, `structured_data_summarize` | None — each either fires completely or not at all (a function body is compressed entirely or not; a line matches `patterns` or it doesn't) | **No, not without new design work** |

This split is the single most important finding for scoping the mechanism. It also
lines up badly with where the value is: QB-051's own measured effectiveness table
(`backlog.md`, already-shipped analytics) shows `code_ast_summarize` (44.1%) and
`python_ast_summarize` (2.4%, small sample) — both **binary** — contribute roughly
46% of all benchmark savings, `strip_lines` (18.4%, effectively binary per-pattern)
another 18%, while the four **scalable** stages contribute the remaining ~35%
(`max_tokens` 32.4% at a *shallow* 2.2% average trim per fire — i.e., barely
engaged at today's limits; `group_repeated` 2.7%; `deduplicate_consecutive`/
`remove_ansi` negligible; `collapse_unchanged_context` not yet in that table,
shipped after it).

**Conclusion:** a level mechanism built only on existing numeric knobs is real,
deterministic, and has genuine (if bounded) headroom — `max_tokens` alone is already
proven to be under-tightened relative to its own measured contribution. But it
cannot, by itself, deliver the kind of "compress function bodies harder" or
"strip more patterns" aggressiveness the QB-039 backlog entry's prose gestures at
("collapsing repeated boilerplate inside a protected block") — that would require
new, per-stage design work (e.g., an AST stage that keeps the first N body lines
instead of none) that is explicitly out of scope for a "reuse the existing pipeline,
no heuristics" first version. Flagged as a bounded limitation, not hidden.

---

## 2. Recommended mechanism: a deterministic config-scaling function, not a new primitive

**No changes to `ContentMask`, `Decision`, `StageHandler` Protocol, `Pipeline.execute`,
or `_enforce_protect`.** The entire mechanism lives in one new, small, pure function
that runs where `FilterRegistry._build_stage_entry` already converts a raw TOML stage
dict into a `StageEntry`, before the stage is instantiated:

```
scale_stage_dict(stage_dict: dict, level: CompressionLevel) -> dict
```

Driven by two small, static, hand-authored tables (no learning, no telemetry, no
per-content branching — see §7 for why this satisfies "no heuristics"):

```python
class CompressionLevel(StrEnum):
    SAFE = "safe"            # today's behavior, unchanged — the default
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"

_LEVEL_FACTOR: dict[CompressionLevel, float] = {
    CompressionLevel.SAFE: 1.0,       # no-op multiplier — see §5's equivalence proof
    CompressionLevel.BALANCED: 0.65,
    CompressionLevel.AGGRESSIVE: 0.40,
}

# (stage_type, field_name) -> (direction, floor)
_SCALABLE_FIELDS: dict[tuple[str, str], ScaleRule] = {
    ("max_tokens", "limit"):                    ScaleRule(shrink, floor=50),
    ("truncate_lines", "max_length"):            ScaleRule(shrink, floor=40),
    ("group_repeated", "min_count"):             ScaleRule(shrink, floor=2),   # stage's own contract
    ("collapse_unchanged_context", "context_lines"): ScaleRule(shrink, floor=0),
}
```

`scale_stage_dict` looks up `(stage_dict["type"], field)` for every field present in
`_SCALABLE_FIELDS`, multiplies by `_LEVEL_FACTOR[level]`, rounds, and clamps to the
rule's floor — the same floor values each stage's own `Field(gt=0)`/`Field(ge=0)`
constraint already requires, so an out-of-range value is structurally impossible, not
just unlikely. Every field not in the table (patterns, regex rules, `format`,
`language`, ...) passes through completely untouched.

**Why this is genuinely the simplest option with the most headroom**, against the two
alternatives considered:

| Option | Filter TOML changes needed | New maintenance surface | Verdict |
|---|---|---|---|
| **A — generic scaling table (recommended)** | None. Every existing filter TOML gains 3 levels automatically. | One small static table, reviewed once. | Simplest; fully backward compatible by construction. |
| **B — per-filter level overrides in TOML** (`[[filter.stages.level_overrides.aggressive]]`) | Every filter author must hand-author overrides for every level they want to support. | ADR-032 already made benchmark coverage mandatory per filter; this would make it mandatory per filter *per level*, tripling authoring/test burden for no mechanical gain over Option A in the common case. | More control, rarely needed; not the default. |
| **C — fully separate `[[filter]]` blocks per level** | Duplicate the entire stage list per level, 3x. | Three near-identical stage lists per filter drifting out of sync over time; each needs its own ≥3 inline tests (Anti-Goal #23). | Rejected — worst maintenance-to-value ratio. |

Recommendation: **Option A as the default and only mechanism most filters ever need**,
with one explicit, rare escape hatch — an optional `level_scaling: bool = true` field
on `StageConfig` (inherits cleanly, since `StageConfig` already has `extra="allow"`
at the base and every concrete config uses `extra="forbid"` — this would be one new,
explicitly-declared field on the base class, not a schema-breaking change) a filter
author can set `false` on a specific stage instance if scaling it is wrong for that
filter (e.g. a filter that already hand-tuned `max_tokens.limit` to an unusually low,
deliberate value). This is Option B's flexibility, opt-in and rare, without its
default-path cost.

---

## 3. `preserve_patterns` / `PROTECT` across levels: unchanged, absolute, at every level

This is the design's most important constraint, and it is **not a new decision** —
it is ADR-031's decision, carried forward unmodified.

ADR-031 already evaluated and explicitly rejected "priority-based budgeting" —
"replace the binary `PROTECT` with multiple protection levels, so lower-priority
protected content can be compressed before higher-priority content" — as
disproportionate: it would require breaking changes to `Decision`, `_enforce_protect`,
and four stage modules, and would break the plugin API's "stable after V1.0"
guarantee, "with no evidence yet that it would meaningfully outperform best-effort in
practice." QB-039's own text acknowledges this rejection and notes Balanced mode
"may effectively be that option, revisited under new product priorities" — but that
revisit has not happened, and the user's explicit constraints for *this* task
(preserve correctness, no heuristics) give no new evidence to revisit it now.

**This design does not touch `PROTECT` at any level.** `preserve_patterns` produces
`Decision.PROTECT` identically regardless of `compression_level`; `_enforce_protect`'s
existing pipeline-wide invariant is untouched and still the only thing that can
restore a PROTECT decision a stage tried to downgrade. What moves across levels is
strictly limited to the four numeric fields in §2's table — never *which* lines are
protected, never the patterns themselves. `strip_lines`' `patterns` (what gets
COMPRESSed outright) are also untouched — they are pattern lists, not scalable
numeric fields, and QB-039's more ambitious "compress into currently-protected
content" idea is explicitly **out of scope for this design**, left as a distinct,
separately-decided future item exactly as QB-039 itself already frames it (its own
open question, not resolved here). This scope line is deliberate: it's what makes
"preserve correctness" and "no heuristics" true by construction rather than by
argument.

One consequence worth stating plainly: because PROTECT is never touched, a filter
like `git-diff` — whose `preserve_patterns` protects nearly 100% of real diff bodies
(QB-041's own measured finding) — will show **little to no difference between Safe,
Balanced, and Aggressive** under this design. That's correct, expected behavior, not
a bug: closing that specific gap is QB-041/QB-055's independent, already-scoped
initiative (context-aware hunk compression, deliberately designed to work entirely
within unchanged-context KEEP lines so it *doesn't* need PROTECT-loosening either —
see QB-055's own "should not need Balanced/Aggressive mode to be safe" note). The two
initiatives are complementary, not overlapping.

---

## 4. Named levels vs. numeric budgets — recommendation

The user's framing offers two axes: named levels (`lossless`/`balanced`/`aggressive`)
or numeric budgets (`100%`/`75%`/`50%`/`25%`). Recommendation: **named levels as the
only user-facing interface; a numeric multiplier as the internal implementation
detail that backs them** — not a competing second axis.

Reasons, in order of weight:

1. **Rule 4 (reuse existing conclusions).** QB-039 already scoped this exact feature
   under the names Safe/Balanced/Aggressive, with product-owner sign-off on the
   framing (it's the top-priority item once its prerequisites land). Inventing a
   parallel, unreconciled "budget %" naming scheme now would fragment product
   terminology for no benefit — this design keeps QB-039's names (lower-cased to
   `safe`/`balanced`/`aggressive` for the enum).
2. **A raw percentage overstates precision these stages don't have.** `max_tokens`
   is explicitly documented (ADR-031, the stage's own docstring) as a *best-effort*
   target, not a guarantee — "PROTECT lines are never compressed to meet it... the
   rendered output may exceed `limit`." Exposing "75%" as a user-facing dial implies
   "output will be 75% of the original," which this mechanism cannot promise for any
   filter whose PROTECT coverage is high. That's the same false-precision problem
   Anti-Goal #24 already legislates against for token counts generally ("no token
   count without uncertainty label") — a bare percentage dial would reintroduce it
   at the *input* side instead of the output side.
3. **A small, closed set is easier to give correctness guarantees for.** Three named
   levels means three fixed points to benchmark, test, and reason about exhaustively
   (§6). An open percentage range invites "what does 43% mean" questions with no
   good answer and multiplies benchmark/test surface for no real user benefit — most
   users want "compress harder," not a specific fraction.

The internal multiplier table (§2) keeps the door open without any of that cost: a
future `compression_level = "custom"` plus a raw `compression_budget: float` field
would slot into the exact same `scale_stage_dict` plumbing later, if a real use case
ever asks for it. Nothing in this design forecloses that; it just isn't the
recommended default interface.

---

## 5. Backward compatibility

- **FilterConfig/StageConfig TOML schema: unchanged.** Zero migration for any of the
  ~20 built-in filters or any project/user filter already in the wild. The one new
  optional field (`level_scaling`, §2) defaults to `true` and is additive, per
  Pydantic's existing default-field pattern already used throughout `config/model.py`.
- **`FilterRegistry.apply()`/`.trace()`: new parameter is optional and defaults to
  `CompressionLevel.SAFE`.** Every existing call site (dispatcher, `claude_read.py`,
  `quor explain`, the benchmark runner, every unit test) that doesn't pass `level=`
  keeps its current behavior, unchanged, by construction — `_LEVEL_FACTOR[SAFE] ==
  1.0` makes `scale_stage_dict` a mathematical no-op at the default level, not just an
  intended no-op. This should be the one behavior actually asserted by a dedicated
  regression test (§6): running the full benchmark corpus at `level=safe` must be
  **byte-identical** to today's baseline with no level argument at all.
- **`QuorUserConfig`: new field (`compression_level: str = "safe"`) with a default
  matching current behavior.** Old `config.toml` files without the field parse
  exactly as before (Pydantic default fills it in) — same pattern already proven by
  `mode`/`tee_enabled`.
- **Plugin API untouched.** `StageHandler`'s `api_version=1` contract, `apply()`
  signature, and `can_handle()` are not modified — scaling happens one layer up, in
  `FilterRegistry`, before a stage is ever instantiated. This respects
  `docs/final/CLAUDE.md`'s "Backwards Compatibility Rules" (`quor.compression_stage`
  entry-point API stable after V1.0) without needing an exception. Explicit
  limitation, stated rather than hidden: a **third-party plugin stage's own config
  fields are not auto-scaled** unless they happen to share a `(stage_type,
  field_name)` key already in `_SCALABLE_FIELDS` (they won't, since that table is
  keyed to built-in stage types only) — a plugin author gets no free multi-level
  support today. Extending the table to be plugin-declarable is a reasonable follow-up,
  explicitly out of scope here.
- **`tests/benchmarks/baseline.json`: additive field only** (§6) — mirrors the
  existing precedent already set by `stage_results`/`stages` (QB-051's own addition,
  "existing baseline.json entries... simply don't have a 'stages' key, and nothing
  reads one back out of them").

---

## 6. Benchmark strategy

Extends the existing suite (`tests/benchmarks/manifest.toml` + `benchmark_runner.py`
+ `baseline.json`) rather than building a parallel one — this is a parameter added
to an existing correctness/regression harness, not a new kind of test.

- **Run every case at every supported level**, not just once. `BenchmarkResult` gains
  a `level` field; `baseline.json` keys results by `(case_id, level)` instead of
  `case_id` alone. Old baseline entries (no `level` key) are treated as `"safe"` —
  additive, matching the file's own established precedent for prior additive fields.
- **SAFE level is the hard backward-compatibility gate**, not just another data
  point: its `compression_pct` must match the existing baseline within today's
  already-established `--regression-threshold` (2.0pp, ADR-032/QB-011) exactly as
  now. A SAFE-level regression is treated exactly as seriously as any regression is
  today.
- **BALANCED/AGGRESSIVE get their own floors**, informed by the fact that they can
  only ever compress via the four scalable stages (§1) — set conservatively low
  initially (e.g. `min_reduction_pct` no stricter than SAFE's) and tightened once
  real numbers exist, mirroring how `min_reduction_pct` values were originally
  chosen for the existing corpus.
- **New, level-specific correctness check: monotonicity.** For every case,
  `tokens_saved(aggressive) >= tokens_saved(balanced) >= tokens_saved(safe)` should
  hold by construction (the scaling table only ever shrinks thresholds as the level
  gets more aggressive). This is a cheap, deterministic, high-value invariant that a
  single-level benchmark suite never needed and this one gets almost for free —
  a violation would indicate a scaling-table bug, not a content-specific edge case.
- **`must_contain` applies unchanged at every level.** Since PROTECT/`preserve_patterns`
  never move (§3), whatever a filter's inline tests and benchmark cases already
  assert must survive must keep surviving at Balanced and Aggressive exactly as at
  Safe — a `must_contain` violation at any level is exactly as fatal as one at Safe
  is today. This is the concrete, mechanical proof that "preserve correctness" holds
  across levels, not just an assertion about it.
- **Inline `[[filter.tests]]` (`quor verify`) do not need per-level duplication.**
  They already run against `apply()` at whatever level is passed; since the default
  test harness runs at SAFE (today's behavior) unless a future test explicitly opts a
  filter's test into level coverage, no existing filter's 3+ required inline tests
  need to change. A new, optional `test_at_levels: list[str]` field could let a
  filter author assert specific must-survive behavior at Balanced/Aggressive for
  filters where that matters most (e.g. asserting `max_tokens`-scaled output for a
  filter with an unusually tight `limit`) — additive, opt-in, not required.

---

## 7. Why this satisfies "no heuristics" specifically

Worth stating explicitly since the user's constraint list singles it out and QB-053
(a genuinely different, adjacent backlog item — see §8) could be mistaken for
satisfying the same request. A **heuristic**, in the sense this project already uses
the word (`content_type.py`'s own docstring: "heuristic... False positives... degrade
compression but never lose data"), is a rule that infers something uncertain about
*this specific content* and acts on that inference. `_LEVEL_FACTOR` and
`_SCALABLE_FIELDS` are neither: they are fixed, hand-authored, content-independent
numbers, identical for every invocation at a given level, chosen once by a human and
reviewed like any other constant (mirroring how `min_count`, `context_lines`, and
`max_length`'s existing defaults were themselves chosen once, by a human, in each
filter's `.toml`). Given the same `(stage_dict, level)` input, `scale_stage_dict`
always returns the same output — this is a **policy**, not an inference, and the
whole pipeline downstream of it stays exactly as deterministic as it is today (Anti-
Goal #20: "No global state in the pipeline... given the same input and filter config,
it always produces the same output" — `level` simply becomes part of "filter config"
for this purpose).

---

## 8. QB-053 is a different, adjacent idea — explicitly not part of this design

`backlog.md`'s QB-053 ("Adaptive compression: self-tuning aggressiveness per filter")
uses the word "adaptive" too, and it would be easy to conflate with this task. It is
a different mechanism: QB-053 proposes the system *automatically* changing a filter's
behavior based on its own measured historical performance from the tracking DB (e.g.
"a filter with consistently high real volume... could be a candidate for gradually
loosening `preserve_patterns`") — explicitly sequenced after QB-054 (telemetry
infrastructure) and explicitly acknowledged in its own backlog entry as needing
"the same architecture-first design pass" precisely because automatic, evidence-driven
behavior change is a correctness risk a static config isn't.

That mechanism is, by this project's own definition above, a heuristic (or at least a
learned/statistical policy) — it would make output for the same input vary over time
as the tracking DB accumulates more data, which conflicts with this task's explicit
"preserve determinism" and "no heuristics" constraints. **This investigation
interprets "adaptive" in the user's request as "adaptive to a caller-declared
level/budget input," matching QB-039's static, user-selected dial — not QB-053's
self-tuning, evidence-driven one.** The two are complementary future directions
(QB-053 could, later, choose *which* `compression_level` to apply per filter based on
telemetry — using this design's mechanism as its output, rather than replacing it)
but that combination is explicitly out of scope here and should stay its own,
separately-justified decision, exactly as QB-053's own entry already recommends
("QB-039 could be the manual override that always wins over this item's automatic
behavior").

---

## 9. CLI / config surface (no new command needed)

Respects `docs/final/CLAUDE.md`'s hard "exactly six commands" rule by construction —
nothing here needs a seventh:

- **`quor explain <command> --level <safe|balanced|aggressive>`** — directly
  implements the idea already sketched in QB-049's backlog entry ("a `--mode` flag on
  `quor explain` letting a user preview what Balanced/Aggressive mode would do...
  depends on QB-039 existing first"). Reuses `registry.trace()` and
  `build_compression_summary()` completely unchanged; `--level` just selects which
  `CompressionLevel` gets passed to `_run_pipeline()`.
- **`QuorUserConfig.compression_level`** (new field, `~/.config/quor/config.toml`),
  read via the existing `load_user_config()` path, overridable by a new
  `QUOR_COMPRESSION_LEVEL` env var — mirrors `mode`'s/`tee_enabled`'s existing
  env-override pattern in `config/loader.py` exactly.
- **`quor gain`**: extend the existing conditional mode-annotation
  (`gain.py`'s `if mode == "optimize": ...`) with the equivalent for
  `compression_level != "safe"` — same pattern, same file, same author intent
  ("only annotated for the non-default... case").
- **`quor doctor`**: one new one-line health check, `_check_compression_level`,
  mirroring `_check_mode`'s existing `return (f"Mode: {user_config.mode}", True,
  "")` pattern exactly.
- **Project-level override is a deferred question**, same as QB-039 left it: no
  project-tier exists for `QuorUserConfig` today (§0). Adding one is new,
  independently-scopable infrastructure (a `.quor/config.toml` merged ahead of the
  user-level file, presumably git-trust-checked like `FilterRegistry`'s project
  filters) — not assumed or silently built as part of this mechanism.

---

## 10. Expected token savings — labeled estimate, not a claim

Per Anti-Goal #24/#25 (uuncertainty labeling; no AI-quality claim without evidence),
this is a hypothesis grounded in QB-051's already-measured data, not a new number to
publish anywhere yet.

The four scalable stages (§1) account for roughly a third of today's measured
benchmark savings, and one of them — `max_tokens`, the largest of the four at 32.4%
contribution — is already known to be firing shallow (2.2% average trim), meaning
its current default `limit` values are, on the existing corpus, rarely the binding
constraint. Tightening it and its three siblings at Balanced/Aggressive plausibly
adds a **low-to-mid single-digit percentage-point** improvement to the blended
average compression ratio in the best case (filters with low PROTECT coverage,
where these stages actually get to act on real content) — and closer to **zero** for
filters like `git-diff` where PROTECT already claims nearly everything (§3). This
must be measured against the real benchmark corpus (§6) before any number is
published, exactly as this project's own discipline already requires for every other
compression claim — and the ceiling on this specific mechanism is real and bounded
(§1): it does not touch the two largest contributors (`code_ast_summarize`/
`strip_lines`, ~62% combined), so "Aggressive" under this design should not be
expected to look dramatically different from "Safe" on most real sessions until a
follow-up phase gives the binary stages their own aggressiveness knobs.

---

## 11. Summary recommendation

Build `compression_level` as a small, closed, named enum (`safe`/`balanced`/
`aggressive`, matching QB-039's already-agreed naming) that maps, via one static
multiplier table, onto the numeric fields four existing stages already expose
(`max_tokens.limit`, `truncate_lines.max_length`, `group_repeated.min_count`,
`collapse_unchanged_context.context_lines`). No new `Decision` state, no
`StageHandler`/`ContentMask`/plugin-API changes, no per-filter TOML rewrites,
`PROTECT`/`preserve_patterns` left completely untouched at every level (honoring
ADR-031's existing rejection of tiered protection rather than reopening it). SAFE is
mathematically a no-op and must be proven byte-identical to today's baseline — that
equivalence is the backbone of the backward-compatibility story. Surfaced through the
existing `quor explain --level` flag (already sketched in the backlog as dependent on
this work existing) and a new `QuorUserConfig` field — no seventh CLI command.
Explicitly scoped away from two adjacent, larger ideas that are easy to conflate with
this one: QB-039's own more ambitious "compress into PROTECT content" ambition, and
QB-053's self-tuning/telemetry-driven "adaptive" mechanism — both remain distinct,
separately-justified future decisions this design does not foreclose but also does
not attempt.
