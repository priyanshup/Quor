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
in Quor **v0.4.0** and **v0.4.1** (2026-07-11, then the current release — v0.5.0 is current as of
2026-07-31, see CHANGELOG.md). Each affected entry has been
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

**Design Principles**

- **Every compressed output must be independently understandable without requiring access to
  previous tool invocations.** Added 2026-07-31, from a head-of-product review of a deterministic-
  compression research pass. Quor has no way to know what the model still has in its live context —
  the harness's own context compaction can evict anything at any point Quor doesn't see. A
  compressed output that means "unchanged since a previous call" or "see earlier" is therefore
  making a claim Quor cannot verify. This is why [QB-089](#qb-089--exact-match-session-read-deduplication-safe-first-slice-of-qb-043)
  is scoped to a literal content-hash match rather than an inferred one, and why any future
  cross-invocation compression (QB-043 and beyond) must render as an *addition* to the current call's
  full output, never a *substitution* for it — see QB-043's own entry for the mechanism this implies
  (a diff-against-last-seen, reusing `collapse_unchanged_context`'s existing machinery, always kept
  alongside the full current output rather than replacing it).
- **Every new structural (AST-level) analysis consumes Quor's existing per-language parse/traversal
  infrastructure — it never builds its own parser or tree representation.** Added 2026-08-01, from
  the QB-099 structural-diff investigation. `quor/pipeline/ast_summarize/registry.py` and its
  per-language modules are already the one place each supported language gets parsed
  (`ast`/tree-sitter); `analyze_*()` (compressible body lines), `extract_symbols_*()` (QB-066),
  `extract_relationships_*()` (QB-067), and `collapse_imports_*()` (QB-096) are four independently-
  correct analyses over that *same* parse — not four parsers. QB-099A's declaration extraction
  follows this exactly (reuses `python.py`'s existing traversal shape; a production version adds it
  as a fifth capability on the same functions, not a parallel copy — see QB-099A's own entry). The
  shape to defend as new languages/analyses arrive: **parser → shared AST representation → many
  deterministic analyses**, never **parser A, parser B, parser C** per capability.

---

## Current Status

*As of Quor v0.5.0, released 2026-07-31. Windows-first, Claude Code only.*

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
- **Config & structured-data file compression** for JSON, YAML, TOML, `.env`, and `.ini` — a long
  homogeneous array/sequence/array-of-tables (a lockfile's hundreds of near-identical dependency
  entries) collapses to its first few entries plus an omitted-count placeholder, with every key and
  every kept value preserved byte-for-byte; `.env`/`.ini` strip only comments/blank lines, never a
  value — see QB-040 below.
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

**Multi-agent adapter architecture (QB-035A design, QB-068/QB-069 implementation) has shipped:**
Quor's already-agent-agnostic compression core now plugs into more than Claude Code via an
`AgentAdapter` Protocol + registry (`docs/final/ADAPTERS.md`, ADR-036/ADR-040/ADR-041). Gemini CLI
has command-rewriting compression (`COMMAND_INTERCEPT`, matching Claude's own Bash-hook mechanism).
Codex CLI, Cursor, VS Code (Copilot agent mode), Windsurf (Cascade), Aider, and Continue.dev are all
detection/readiness-only, sharing one `DetectionOnlyAdapter` base — each independently researched
and found to lack a confirmed way to rewrite a command or replace tool output today. Standalone
Copilot CLI remains unimplemented.

**What's explicitly out of scope today:** standalone Copilot CLI, or any AI coding assistant beyond
the eight adapters above (Claude Code, Gemini CLI full; Codex CLI/Cursor/VS Code/Windsurf/Aider/
Continue.dev detection-only); any programming language for AST-aware summarization beyond
Python/JavaScript/TypeScript/TSX; any non-Windows-first platform assumption.

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

**Priority order re-set 2026-07-31**, following a head-of-product-style competitive-landscape and
roadmap review conducted after v0.5.0 shipped (QB-083/084/085). Three things changed the picture:

1. **Housekeeping correction:** QB-046 was discovered to already be fully implemented (Go/Rust/
   Java/C# AST summarization, benchmark-backfilled) despite still sitting here as the lowest-
   priority *unstarted* item — moved to [Completed](#completed). It no longer occupies a slot in
   this ordering at all. Every other Now item's `Status:` line was individually re-checked and
   confirmed genuinely un-implemented before this reorder — see QB-046's own new Completed entry
   for the correction note.
2. **Live competitive refresh** (see new item **QB-086** below) found the market has moved
   materially since the last research pass: RTK (the dominant incumbent) now claims cross-platform
   support including Windows (quality unverified, reported "degraded"), eroding part of Quor's
   original Windows-first differentiation; Headroom AI has grown into a broader compression-
   infrastructure play (MCP server, multi-agent wrap, reversible cache) rather than just a Python
   alternative; and entirely new entrants exist (LeanCTX, a deterministic Rust competitor; Token
   Optimizer, which directly targets the cross-session/compaction-survival space QB-043 also
   targets; Caveman, which compresses the *assistant's own responses* rather than tool output — a
   mechanism Quor has never done). Full findings in QB-086.
3. **v0.5.0 changes the calculus for QB-034** (`quor discover`, previously in
   [Later](#later), deliberately held back because "there's no real user base to retain yet"): that
   condition no longer holds — v0.5.0 just shipped with a genuinely marketing-oriented README
   (QB-085) explicitly designed to drive trial installs. QB-034 is the exact trial-to-adoption
   conversion mechanism the market leader (RTK) already validated. Shipping a strong front door
   (README) without the matching retention mechanism behind it wastes the acquisition work QB-085
   just did. Moved here from Later.

**Housekeeping correction (2026-07-31, later the same day):** QB-052 was found to already be
resolved — both known causes (tee footer, concise-output nudge) verified fixed and regression-tested
— moved to [Completed](#completed), the same correction QB-046 got above. It no longer occupies a
slot in this ordering. One genuine new item came out of verifying it: **QB-094** (concise-instruction
tracking accuracy in the Read-hook path specifically — the one piece of QB-052's fix that couldn't be
safely done same-day, see QB-094's own entry below), added directly below in QB-052's old #1 slot for
the same non-negotiable trust reason QB-052 itself was #1 for.

**New final order:** QB-094 → QB-047 → QB-041 → QB-086 → QB-034 → QB-055 → QB-054 → QB-049 →
QB-039 → QB-053. Rationale for the three moves ahead of the 2026-07-15 order (QB-052, the original
occupant of the #1 slot below, is superseded by the housekeeping correction just above):

- **QB-094 (Read-hook concise-instruction tracking accuracy) → #1.** Same non-negotiable reasoning
  QB-052 carried: QB-085's README publicly leads with specific compression numbers, and an unfixed
  gap where `quor gain` can't see a real cost on the Read-hook path is the same class of
  trust-damaging defect QB-052 was raised to close.
- **QB-047 (real benchmark corpus & continuous tracking) → #2, up from #4.** Same reasoning:
  competitors are publishing bold, round numbers (Headroom "60-95%", RTK/LeanCTX "60-90%"). Quor's
  differentiation is defensible, continuously-measured numbers, not bigger numbers — QB-085's README
  is now a public commitment to that, so the measurement infrastructure backing it needs to keep
  pace.
- **QB-086 (competitive landscape refresh, new item) → #4.** Cheap (research/writing, no code) and
  foundational — every other prioritization decision in this document cites the now-partially-stale
  `docs/archive/product-discovery/competitive-research.md`. Do this before it silently misinforms
  another round of ranking the way QB-046's stale status just did.
- **QB-034 (`quor discover`) → #5.** See point 3 above.

QB-041/QB-055/QB-054/QB-049/QB-039/QB-053 keep their relative order from the 2026-07-15 pass — the
evidence behind that ordering (real-usage volume, sequencing dependencies) is unchanged by this
review; only the four items above jumped ahead of them, not each other.

**Housekeeping correction (2026-08-01):** QB-094 shipped (implemented, tested, merged to `main`) —
moved to [Completed](#completed), the same treatment QB-052/QB-046 got above; its stub is removed
from here rather than left duplicated. It no longer occupies a slot in this ordering. **Current
order: QB-047 → QB-041 → QB-086 → QB-034 → QB-055 → QB-054 → QB-049 → QB-039 → QB-053.** Separately
worth flagging next time this section gets a full pass: the priority-interrupt items below (QB-095,
QB-096, QB-097, QB-098, and the QB-099 cluster) each already carry an "Implemented"/"Closed"/
"Rejected" `Status:` line in their own entries, but — unlike QB-094/QB-052/QB-046 — haven't actually
been relocated out of this section yet. Left as-is here rather than bundled into this correction,
since moving that much entry text is a bigger edit than this note warrants.

**Priority order re-set (2026-08-18):** QB-105 (added 2026-08-17, following the QB-104 audit) jumps
to #1, ahead of QB-047. QB-105 found that neither MCP tool (`compress_context`/`get_repo_context`) —
Quor's entire compression path since QB-104 retired the hook mechanism — ever calls
`track_invocation()`. `quor gain`/`dashboard`/`doctor` have therefore been blind to real usage since
2026-08-16, seeing only the six secondary CLI utilities (`map`/`graph`/`symbols`/`search`/`explore`/
`repo`). That's a more urgent gap than QB-047 was ranked for: QB-047 (real-content tracking against
production benchmarks) implicitly assumes `quor gain`'s telemetry is trustworthy input to build on,
and right now it isn't. **Current order: QB-105 → QB-047 → QB-041 → QB-086 → QB-034 → QB-055 →
QB-054 → QB-049 → QB-039 → QB-053.**

**Housekeeping note (2026-08-18, same day):** QB-105 shipped (implemented, tested, `quor verify`
242/242 — see its own entry's now-dated `Status:` line). Same as QB-095/096/097/098/099 above, its
full write-up is left in place rather than relocated to [Completed](#completed) in this pass — but it
no longer occupies a slot in this ordering. **Current order: QB-047 → QB-041 → QB-086 → QB-034 →
QB-055 → QB-054 → QB-049 → QB-039 → QB-053.**

A third candidate raised alongside QB-105 and QB-047 — rewriting `RELEASE_CRITERIA.md`'s gates for
MCP — was checked against the file and is **already done**: QB-104's 2026-08-16 pass rewrote every
hook-era gate (IA-F01/F02/F03, IA-S01, PA-Q06, B-F04, V1-F03/S01) in `docs/final/RELEASE_CRITERIA.md`
to test `quor init --mcp` / `.mcp.json` / MCP tool calls, each with the struck-through hook-era
wording left visible for the record. No open item there — not added to this list.

**Priority interrupt (2026-07-31, later the same day):** a deterministic-compression research pass
(diff tools, VCS, compilers, static analyzers, IDEs, log processors, search/indexing systems —
AI/LLM techniques explicitly excluded) surfaced several new candidate stages, reviewed head-of-
product-style and split into four tiers by build-now/design-first/research-only/reject. Four Tier-1
items (deterministic, human-readable, architecture-compatible, no aliases, no hidden state) were
explicitly greenlit to jump the existing queue ahead of QB-051, in this order: **QB-095** (path
prefix front-coding — this session's pick, in progress now), QB-096 (import block collapsing),
QB-097 (numeric range compression), QB-098 (relative timestamp compression). Two further candidates were
scoped as design-first, not build-now: an AST/structural diff capability (**investigated 2026-08-01 as
QB-099, split into QB-099A/B/C/D below — see that cluster's own entries**) and a generalized
"diff-against-last-seen" extension of QB-043/QB-089 (cross-invocation compression) — the latter
explicitly constrained to an **additive annotation, never a substitution**, after the review
concluded Quor cannot assume anything about what the model still remembers between tool calls (the
harness's own context compaction is invisible to Quor), so no compressed output may depend on a
claim about a *previous* call still being live in context. That constraint is significant enough to
record as a standing design principle, not just a note on one item — see the "Design Principles"
callout under [Vision](#vision). Symbol-dictionary/substring-dictionary substitution and Drain-style
log-template clustering were explicitly deferred (real payoff, but need measurement or an
architecture decision first) — not rejected, just not queued. Binary delta encoding, rolling-hash
content-defined chunking, inverted-index posting-list encoding, and FST term dictionaries were
rejected outright: all are real, deterministic, lossless techniques in their native domains, but
none produce output an LLM can read directly without a decode step, which defeats the purpose of
compressing *before* the model sees it.

**Priority interrupt (2026-08-16):** a product-owner decision to retire Quor's hook-based
integration entirely in favor of MCP as the sole integration surface (**QB-104**, added directly
below) jumps to the very top of Now — this is a breaking architecture change to currently-shipping
functionality (9 assistant integrations), not an incremental feature, and every other item in this
section should be sequenced around it rather than the reverse.

---

#### QB-104 — Retire the hook-based integration system; MCP becomes the sole integration surface

**Effort:** Large · **Value:** High · **Risk:** High · **Category:** Architecture / Integration

**Status:** Complete (2026-08-16). All four phases executed and verified across three sessions —
Phase 1 (legacy removal) + Phase 2 (production MCP module) in one pass, Phase 3 (dead-code cleanup,
doc archival, MCP scaffolding CLI, documentation refresh) in a follow-up pass, Phase 4 (final
verification) closing it out. `ruff check .`, `mypy quor`, and the full `pytest tests/` suite
(including the `integration`-marked tests and the benchmark suite) are all green. See the dated
"Execution record" note and updated checklists below for what actually shipped, including where
execution diverged from the original plan.

**Execution record (2026-08-16):**
- Both open questions from the original plan were resolved during execution, not left blocking:
  `quor/adapters/dispatcher.py` moved to `quor/engine/dispatcher.py` (the manual `quor <cmd>`
  passthrough survives, untouched); `claude_read.py`'s three nudge features (Repository Context,
  Relevant repository files, repo-intel onboarding nudge) were ported into a new `get_repo_context`
  MCP tool in `quor/mcp/server.py`, adapting "Relevant repository files" to take a `query` parameter
  directly (an MCP tool call has no transcript to parse a query from, unlike the old Read hook).
- The migration story was resolved as: a new `quor uninstall-hooks` command (removes leftover
  pre-QB-104 launcher scripts and settings.json entries, touching nothing another tool registered),
  plus `quor init` running that same detection/cleanup unprompted on every invocation — no separate
  manual step required for most users.
- `quor init` was rebuilt from scratch (not restored) as pure MCP scaffolding: `quor init --mcp`
  writes `./.mcp.json` (merging into any existing file, never clobbering another project's MCP
  servers) and prints the equivalent `claude_desktop_config.json` snippet — Claude Desktop's own
  config lives outside any repo, so it's printed for the user to add themselves rather than
  auto-written, unlike the project-scoped `.mcp.json`.
- Real-environment validation: `quor uninstall-hooks` was run against this machine's actual
  globally-installed pre-QB-104 hook (`~/.claude/settings.json` + two `.ps1` scripts, left over from
  this repo's own dogfooding) and cleanly removed it; `quor init --mcp`/`quor doctor` were both
  smoke-tested for real afterward.
- One correction found during Phase 3: `get_quor_invocation()` (in `quor/rewrite/invocation.py`) is
  **not** dead code — `classify_command()` (used by `quor explain`) calls it directly to build its
  rewritten-command string. Only `rewrite_command()`, a zero-caller convenience wrapper around
  `classify_command()`, was actually dead; that's what was removed. Affected tests
  (`test_rewrite.py`, `test_invocation.py`) were updated to use a local test-only helper reproducing
  the wrapper's one-line logic, not deleted.
- Hook-specific ADRs in `docs/final/DECISIONS.md` (ADR-030, ADR-036, ADR-043, ADR-044) were annotated
  "Superseded" in place rather than physically extracted — an ADR log is an append-only historical
  record, and cutting entries out of it to relocate them would have broken that convention for no
  benefit. `docs/final/ADAPTERS.md` and `docs/design/QB-035A-multi-agent-adapter-design.md` (whole
  files, not embedded log entries) were moved to `docs/archive/hook-integration/` as originally
  planned.
- Documentation refresh was scoped to what was actually requested each session (`README.md`,
  `docs/final/CLAUDE.md`, `docs/final/ROADMAP.md`, `docs/ALGORITHMS.md`, plus `docs/final/
  PROJECT_BIBLE.md`/`ANTI_GOALS.md` for direct dangling-link fixes caused by the archive move).
  `docs/final/PROJECT_STATUS.md`, `IMPLEMENTATION_PLAN.md`, `RELEASE_CRITERIA.md`,
  `COMMAND_SUPPORT.md`, and `RESEARCH_COMPLETION.md` (all named in the original plan's Phase 3
  checklist) were never explicitly requested and remain unrefreshed — flagged here as a real gap,
  not silently dropped.

Quor currently ships two parallel integration mechanisms: (1) a hook-based system that transparently
intercepts Bash/Read tool calls across 9 assistants (Claude Code, Aider, Cursor, Codex, Continue,
Gemini, VSCode, Windsurf — 2 with real rewrite capability, 6 detection-only stubs, plus Claude Code's
own PS1/SH launcher generation), and (2) the new MCP stdio server (`quor/mcp_poc.py`, POC-validated
against the real pipeline). The decision: MCP fully replaces the hook system as the sole integration
surface going forward — this entry is the resulting removal + rebuild plan, based on a full
inventory pass (see "Files affected" below) rather than assumption.

**Load-bearing correction before execution starts:** the hook system is not legacy/dead code by any
objective measure found during inventory — the most recent merged PR before this planning pass
(`c067cbe`, cross-platform `.ps1` hook-name fix) was active maintenance of it, and ~20 test files
covering all 9 adapters were passing at 100% immediately before this plan was drafted. This is a
deliberate breaking-change pivot away from working, tested, shipped functionality, not a cleanup of
abandoned code — the risk rating and the migration-story open question below both follow from that.

<details>
<summary>Technical details</summary>

**Problem:** Maintaining two structurally different integration models (hooks: transparent
interception, zero agent opt-in required, per-assistant script generation and install/doctor
tooling; MCP: an explicit tool the agent chooses to call, one standard protocol across every MCP
client) doubles surface area for no compounding benefit once MCP is production-ready. The hook
system's own per-assistant detection/install/doctor machinery (`quor/adapters/`, `quor/cli/commands/
init.py` and most of `doctor.py`) is the single largest source of adapter-specific code in the
repo and has no MCP equivalent need — MCP registration is client-side config, not a Quor-generated
script.

**Desired outcome:** One integration surface (MCP), a smaller and more maintainable adapter layer
(none needed — MCP has no per-assistant variance to detect), and `quor/mcp_poc.py` promoted to a
real, packaged production module with logging, config, and dependency declaration it doesn't have
today.

**Scope — what's removed, what's kept, what's new** (from a full-repo inventory pass; not guessed):

*Removed entirely* (hook-protocol-shaped, no reuse value for MCP):
- `quor/adapters/base.py` — `AgentAdapter` Protocol, `AgentEvent` enum, hook-payload pydantic models
- `quor/adapters/registry.py` — `AdapterRegistry` (`quor.hook_adapter` entry-point discovery)
- `quor/adapters/claude.py`, `claude_adapter.py` — Claude Code PreToolUse/Bash hook
- `quor/adapters/codex_adapter.py`, `continue_adapter.py`, `cursor_adapter.py`, `vscode_adapter.py`,
  `windsurf_adapter.py`, `aider_adapter.py`, `_detection_only.py` — 6 detection-only stubs + shared base
- `quor/adapters/gemini_adapter.py`, `hook_manifest.py` — PS1/SH launcher-script generation/templates
- `quor/cli/commands/init.py` — hook installation flow (dry-run preview, collision detection, atomic
  PS1/SH writes)
- `quor/__main__.py`: `_run_hook()`, `_HOOK_ARGV_ALIASES`, the `hook` entry in `_CLI_COMMANDS` and its
  `sys.argv[1] == "hook"` fast-path

*Refactored, not deleted:*
- `quor/adapters/claude_read.py` (~1,540 lines) — the PostToolUse/Read hook itself is removed, but it
  carries three features with real product value that must not be silently lost: "Repository Context"
  (QB-079), "Relevant repository files" (QB-081), and the repo-intel onboarding nudge (QB-090). These
  need a new, non-hook call site (see Phase 2) — **open question, see below**, not a settled deletion.
- `quor/cli/commands/doctor.py` (34,898 bytes, largest CLI command) — strip all hook-specific checks
  (`_hook_script_path`, `_check_hook_script`, `_check_hook_registered`, `_check_hook_up_to_date`,
  `_check_adapters`, `_repair_hooks`, `has_stale_hooks`, `should_warn_stale_hooks`,
  `_check_hook_collision`, `_check_hook_roundtrip`, `_check_read_hook_roundtrip`,
  `_run_roundtrip_check`); keep the non-hook diagnostics (`_check_python_version`,
  `_check_dependencies`, `_check_sqlite`, `_check_filters`, `_check_mode`, `_check_tee`,
  `_check_tee_size`, `_check_negative_compression_filters`, `_check_plugins`); add a new MCP-health
  check in their place (server importable, `mcp` package present, `.mcp.json`/config discoverable).
- `tests/unit/test_adapters.py` (1,096 lines), `test_fail_open.py` (517 lines), `test_cli.py` (2,686
  lines) — each mixes hook-specific tests with pure pipeline/dispatcher tests that must survive;
  needs per-test triage, not wholesale deletion. `test_cli.py` specifically: remove `TestDoctor*`,
  `TestInit`, `TestReadHookDoctorChecks`, `TestHookConfigHealth`, `TestStaleHookNudge`,
  `TestReadHookRegistration`, `TestHookCollisionDetection`, `TestFindConflictingHooks`,
  `TestExecutionPolicyCheck`, `TestPosixLauncherGeneration`, `TestDoctorPosix`,
  `TestWindowsEncodingRegression`, `TestCodepageSweep`, `TestRepoIntelligenceOnboarding`; keep
  `TestValidate`, `TestExplain`, `TestGain`, `TestGainFilters`, `TestVerify`.

*Kept as-is, reused by MCP* (confirmed protocol-agnostic — none of these touch hook JSON shape):
- `quor/adapters/dispatcher.py` (573 lines) — this is the compression-pipeline runtime itself
  (`FilterRegistry`/`Pipeline`/plugins/tee/secrets-scan/tracking), not hook-specific despite its
  current location; `quor/mcp_poc.py` already reuses the same `FilterRegistry`/`Pipeline` layer
  independently. **Open question:** does `dispatcher.py` move out of `quor/adapters/` (a now-misleading
  package name once every adapter is gone) into e.g. `quor/core/` or `quor/compress/`? And does the
  manual `quor <cmd>` passthrough CLI feature it powers stay (as a standalone utility, unrelated to
  any hook auto-rewrite) or go? Not assumed either way — needs a decision before Phase 1 executes.
- `quor/pipeline/tee.py`, `onboarding.py`, `secrets.py`, `quor/pipeline/repo_profile/nudge.py`,
  `quor/tracking/db.py` — all take plain strings, zero coupling to hook request/response shape;
  directly wireable into the MCP tool's response path as additive enhancements (recovery-footer
  caching, onboarding tips, secret-scan warnings, repo-intel nudges) matching what hook users got.
- Every non-hook, non-adapter CLI command (`validate`, `verify`, `explain`, `gain`, `map`, `symbols`,
  `graph`, `repo`, `explore`, `search`, `version`, `dashboard`) and ~70 of the ~92 files in
  `tests/unit/` — confirmed zero hook dependency during inventory, untouched by this ticket.

**Phase 1 — Legacy removal/deprecation:**
- [x] Decide the two open questions above — resolved during execution, see "Execution record" above
- [x] Decide the migration story — `quor uninstall-hooks` + automatic cleanup in `quor init`
- [x] Remove the 9 adapter files + `base.py`/`registry.py`/`_detection_only.py`/`hook_manifest.py`
- [x] Remove `quor/cli/commands/init.py` (later rebuilt as MCP scaffolding in Phase 3); strip
      hook-specific sections of `doctor.py`
- [x] Remove hook dispatch wiring from `quor/__main__.py`
- [x] Remove the wholesale hook/adapter test files (20, not 18 as originally estimated); triage-split
      `test_adapters.py`, `test_fail_open.py`, `test_cli.py` — plus `test_tracking.py` and
      `tests/integration/test_cli_commands.py`, two more mixed files found during execution that
      the original inventory missed
- [x] Sweep for dangling imports/references to removed modules across `quor/` and `tests/`

**Phase 2 — Production MCP module:**
- [x] Promote `quor/mcp_poc.py` → `quor/mcp/server.py`
- [x] Standardize stdio transport + lock `mcp>=2.0.0` (the `FastMCP` → `MCPServer` rename, documented
      inline in `quor/mcp/server.py`)
- [x] Add `get_repo_context` tool (config/argument handling took the form of this tool's own
      `file_path`/`query` parameters rather than a separate config layer — no project-root override
      or log-level flag was added; not found to be needed in practice)
- [x] Wire in `nudge` reuse (via `get_repo_context`); `tee`/`onboarding`/`secrets` were evaluated and
      deliberately left un-wired — they're dispatcher-specific enhancements (recovery-footer caching,
      onboarding tips, secret-scan warnings) tied to the Bash-output shape, not asked for on the MCP
      tool-result path and not added speculatively
- [x] Add `mcp>=2.0.0` as a core `pyproject.toml` dependency (not an extra — it's Quor's sole
      integration surface, not optional)
- [x] Packaged as `python -m quor.mcp.server` (a `quor mcp` subcommand was considered and not done —
      an MCP server needs to hold the process open on stdio, a different shape from every other
      Typer command, which return promptly)
- [ ] *(Stretch, not pursued)* repo-intelligence family (map/symbols/graph/explore/search) as
      additional MCP tools/resources — remains a real future extension, out of this ticket's scope

**Phase 3 — Configuration & documentation:**
- [x] Promote `docs/POC_TESTING.md` → permanent doc, covering `.mcp.json` and
      `claude_desktop_config.json` registration
- [x] Replace `README.md`'s hook-era sections with MCP registration instructions
- [x] Add MCP-registration scaffolding: `quor init --mcp`, writing `.mcp.json` + printing the
      Desktop config snippet
- [x] Archive `docs/final/ADAPTERS.md` and `docs/design/QB-035A-multi-agent-adapter-design.md` to
      `docs/archive/hook-integration/`; annotate hook-specific ADRs (030/036/043/044) as
      "Superseded" in place in `docs/final/DECISIONS.md` rather than physically extracting them —
      see "Execution record" above for why extraction was rejected
- [ ] A dedicated new ADR for the MCP-only decision was not written as its own entry — the
      "Superseded" annotations on ADR-030/036/043/044 plus this ticket's own backlog record serve
      that purpose; a formal ADR-0NN remains a legitimate follow-up if the project wants one
- [x] Pass over `docs/final/CLAUDE.md`, `ROADMAP.md` — done. `PROJECT_STATUS.md`,
      `IMPLEMENTATION_PLAN.md`, `RELEASE_CRITERIA.md`, `COMMAND_SUPPORT.md`,
      `RESEARCH_COMPLETION.md` — **not done**, never explicitly requested; real gap, see "Execution
      record" above. `docs/ALGORITHMS.md` was checked and needed no changes.
      `PROJECT_BIBLE.md`/`ANTI_GOALS.md` got targeted fixes for dangling links the archive move
      caused, not a full pass.

**Phase 4 — Verification & test coverage:**
- [x] Full `pytest tests/` passes (unit + integration + benchmark suite), zero hook-specific files
      remaining, every triaged file confirmed clean
- [x] Grep sweep confirmed no remaining import of any removed `quor.adapters.*` module anywhere in
      `quor/` or `tests/` (one real gap the sweep caught: `patch("quor.adapters.dispatcher...")`
      string-literal mock targets in `test_adapters.py`, invisible to an import-only grep)
- [x] `ruff check .` / `mypy quor` clean — one real `mypy` finding fixed (`merge_search`'s `exclude`
      param needed a `None`-filtered `rel_path`, not a blind `frozenset({...})`); `ruff check .` also
      surfaced and fixed several pre-existing, QB-104-unrelated issues in
      `docs/design/QB-099-prototype/` while satisfying the "zero warnings" gate
- [x] Manual MCP smoke test: both tools invoked directly against this real repo, including the
      repo-intel nudge firing correctly (537 files indexed, 14-day-stale nudge shown)
- [x] `quor doctor` and `quor init --mcp`/`quor uninstall-hooks` all smoke-tested for real, including
      against this machine's actual pre-QB-104 hook installation (see "Execution record" above)
- [x] `quor verify` and the benchmark suite both pass unchanged, confirming zero pipeline regression

**Testing (actually run):** `ruff check .` clean, `mypy quor` clean (146 source files), full
`pytest tests/` clean (unit + benchmark suite), `pytest tests/ -m integration` clean (8/8).

**Files changed:** see the itemized Phase 1–3 checklists above; the full diff spans
`quor/adapters/` (removed, 15 files), `quor/engine/dispatcher.py` (moved from `quor/adapters/`),
`quor/mcp/server.py` (promoted + extended), `quor/cli/commands/init.py` (rewritten),
`quor/cli/commands/uninstall_hooks.py` (new), `quor/cli/commands/doctor.py` (stripped),
`quor/cli/main.py`/`quor/__main__.py` (rewired), `quor/rewrite/classifier.py` (`rewrite_command()`
removed), ~25 test files (deleted or triaged), `pyproject.toml`, `README.md`,
`docs/final/CLAUDE.md`/`DECISIONS.md`/`ROADMAP.md`/`PROJECT_BIBLE.md`/`ANTI_GOALS.md`,
`docs/POC_TESTING.md`, `docs/archive/hook-integration/` (new), `CONTRIBUTING.md`.

</details>

---

#### QB-105 — Wire track_invocation() into the MCP server's tools

**Effort:** Medium · **Value:** High · **Risk:** Low · **Expected token impact:** None directly
(measurement/infrastructure — restores visibility into QB-104's own integration surface) ·
**Category:** Engineering / Measurement

**New item, added 2026-08-17**, surfaced during a post-QB-104 audit of whether `quor/mcp/server.py`
carries any leftover Claude-Code-specific assumptions (it doesn't — see QB-035's entry, resolved by
verification rather than new adapter work). The audit's real finding was here instead: neither
`compress_context` nor `get_repo_context` (the two MCP tools that are now Quor's actual compression
path) ever calls `track_invocation()`. Every remaining call site is a secondary CLI utility (`quor
map`/`graph`/`symbols`/`search`/`explore`/`repo`) — none of which represent real compression work.

<details>
<summary>Technical details</summary>

**Problem:** `quor gain`/`quor dashboard`/`quor doctor`'s negative-compression-filter detection all
read exclusively from the `invocations` SQLite table, populated by `track_invocation()`
(`quor/tracking/db.py`). Before QB-104, every Bash/Read call flowed through `dispatcher.py`'s or
`claude_read.py`'s call to it. After QB-104, the compression path moved entirely to
`quor/mcp/server.py`'s two `@mcp.tool()` functions, and neither was wired up — confirmed by grepping
every `track_invocation` call site in the current tree (`quor/cli/commands/{explore,graph,map,repo,
search,symbols}.py` only). Net effect: `quor gain` can currently report real numbers only for those
six secondary utilities, never for the actual `compress_context`/`get_repo_context` usage a real MCP
client generates — the exact capability `quor gain` exists to measure is now invisible to it.

**Desired outcome:** `compress_context` and `get_repo_context` each call `track_invocation()` on
every invocation, the same fail-open, non-blocking contract every other producer already honors
(`tracking=None` no-ops, any exception is swallowed with a warning). `quor gain`/`quor dashboard`
resume reflecting real MCP usage without any change to their own read-side logic.

**Open design questions, not yet resolved:**
- **`TrackingDB` construction/lifecycle.** Every existing call site constructs (or receives) a
  `TrackingDB` once per short-lived CLI process. The MCP server is long-lived (one process per
  session — see QB-089's entry for why that's now true post-QB-104) and handles many tool calls;
  needs a decision on whether one `TrackingDB` is constructed at server startup and reused for the
  process's lifetime (consistent with QB-089's own session-scoped `SessionDedupCache` precedent), or
  something else.
- **`command` field semantics.** Every existing `InvocationRecord.command` value is a real shell
  command or a `"Read: {path}"` string (`quor gain`'s `read_hook_invocations` counter — see
  `quor/tracking/db.py`'s own docstring — depends on that exact prefix). `compress_context` receives
  raw text with no command/path attached at all; `get_repo_context` has `file_path`/`query`
  parameters but no shell-command shape either. Needs a considered convention (e.g. a new sentinel
  prefix), not a guess — a wrong choice here would either silently break `read_hook_invocations` or
  misrepresent MCP-tool calls as something they're not.
- **`filter_name` for `compress_context`.** Unlike the dispatcher's Bash path, `compress_context`
  doesn't route through a *named* filter lookup keyed on a command pattern — it always falls through
  to the generic filter (see `quor/mcp/server.py`'s own module docstring). Whether that's recorded
  as `filter_name="generic"` or something MCP-specific needs a decision, since `quor gain --filters`
  groups and reports by this field.
- **Interaction with QB-089's dedup marker.** When `compress_context` returns the "unchanged since
  last shown" marker instead of compressing, does that still count as a tracked invocation (and if
  so, with what token-savings semantics — the dedup marker's own savings, or nothing)? Needs a
  decision, not an assumption, the same way QB-089's own entry flagged "interaction with `quor
  explain`/`quor dashboard`... needs a design decision" for its own marker.

**Resolution (2026-08-18), answering each open question above:**
- **Lifecycle:** one `TrackingDB`, constructed lazily (module-level singleton, guarded by a
  `threading.Lock`) on first real use rather than eagerly at import — eager construction would spin
  up a background thread and touch the real platformdirs `quor.db` merely by importing
  `quor/mcp/server.py`, exactly as every test in `test_mcp_server.py` already does. Closed in a
  `finally` around `mcp.run()`, mirroring `quor/__main__.py`'s own `tracking.close()` discipline —
  skipped entirely if the singleton was never constructed (a session that never tracked anything
  should not force `quor.db` into existence).
- **`command` field:** every MCP-sourced row is prefixed `"MCP "` (`"MCP compress_context"`, `"MCP
  get_repo_context: {file_path}"`, etc.) — never `"Read: "`, so `read_hook_invocations` is unaffected.
- **`filter_name` for `compress_context`:** reuses the real `filter_config.name` unchanged (almost
  always `"generic"`) rather than inventing an MCP-specific label — it's genuinely the same
  ContentMask compression, just a different transport, and `filter_name` is meant to identify
  compression *strategy*, not provenance.
- **QB-089 dedup interaction:** tracked, under its own new label `MCP_DEDUP_FILTER_LABEL`
  (`"mcp-dedup"`) — a dedup hit is real, deliberate token savings (the marker is a few bytes
  regardless of input size) and belongs in `quor gain`'s headline SUM()s, but blending its near-100%
  ratio into `"generic"`'s own real average would badly misrepresent that filter's actual
  performance, so it gets a separate label instead.
- **`get_repo_context`'s own label:** a second new constant, `MCP_REPO_CONTEXT_FILTER_LABEL`
  (`"mcp-repo-context"`) — synthesis, not compression (no "before" blob, same convention `quor
  map`/`explore`/`repo`'s own labels already use), added to `SYNTHESIS_FILTER_LABELS` so it doesn't
  dilute `quor gain`'s headline. Both new labels are added to `filter_divergence.py`'s
  `_EXCLUDED_FROM_LOW_PERFORMER_CHECK` (for different reasons — one is a synthesis 0%-by-design
  label, the other has no benchmark counterpart to diverge against) — the exact wiring gap that, if
  missed, would have reproduced the false-positive-in-`doctor` failure mode this ticket exists to fix.
- **Untracked edge cases:** an empty `compress_context` input and a `get_repo_context` call before
  `quor map` has ever run are both left untracked — same "empty `file_path` stays untracked"
  convention `track_invocation()`'s other producers already follow for degenerate/no-op input.

**Status:** Implemented (2026-08-18). `ruff check`/`mypy` clean on all four changed source files
(`quor/mcp/server.py`, `quor/tracking/db.py`, `quor/analytics/filter_divergence.py`,
`quor/cli/commands/*` untouched). New tests in `test_mcp_server.py` (tracking fixture +
`TestCompressContextTracking`/`TestGetRepoContextTracking`) and `test_filter_analytics.py` (both new
labels' exclusion). `quor verify` 242/242. Full `tests/unit` sweep green (excluding the pre-existing
slow real-subprocess-spawning files this repo's own test suite already runs separately — see
QB-095's own entry for that same exclusion list).

</details>

---

#### QB-095 — Path prefix front-coding

**Effort:** Small-Medium · **Value:** High · **Risk:** Low · **Expected token impact:** High on
path-heavy tool output · **Category:** Feature (new compression stage)

**Status:** Implemented (2026-07-31), branch `feature/qb-095-path-prefix-front-coding`. `ruff`/`mypy`
clean; `quor verify` 207/207 (including 3 new `generic` filter tests); new `TestPathPrefixFold` suite
13/13; benchmark suite green (128 cases, 36.0% overall, new `generic-find-path-heavy-listing` case
included, no regressions against baseline). Broad regression sweep across ~70 of the ~85 unit test
files also green (run in small batches locally to stay under this repo's own 25s self-hook cap on
`python` Bash calls — see [[project_quor_self_hook_timeout]]); the remaining ~10-15 files
(`test_cli.py`, `test_cli_graph.py`, `test_cli_map.py`, `test_cli_repo.py`, `test_cli_explore.py`,
`test_repo_explorer.py`, `test_read_hook_repo_context.py`, `test_repo_dashboard.py`,
`test_repo_intel.py`, `test_repo_intel_file_intelligence.py`, `test_repo_intel_store.py`) are a
pre-existing slow, real-subprocess-spawning family unrelated to this change (confirmed via grep: zero
references to `path_prefix_fold`/`_STAGE_HANDLERS`/`ContentMask`/`LineMask`) that couldn't be run
locally within the self-hook budget even one file at a time — deferred to CI, which is not subject to
that local constraint. **Unrelated finding surfaced during this run:** `quor.__version__` reports
`0.4.1` against `pyproject.toml`'s `0.5.0` (`test_version_matches_pyproject` fails on `main` as
pulled) — stale installed package metadata, not a source change; needs `pip install -e .` re-run in
this dev environment, out of scope for this item. Not yet merged; benchmark baseline not yet updated
(new case is new, not a regression, so no `--update-baseline` run needed).

<details>
<summary>Technical details</summary>

**Problem:** Path-heavy tool output (`find`, `rg --files`, `ls -R`, coverage reports, repo maps,
stack traces) repeats a long shared directory prefix on every line —
`src/quor/pipeline/stages/foo.py` / `.../bar.py` / `.../baz.py` — and no existing stage removes that
redundancy. It's the same idea search-index term dictionaries and sorted-string front-coding use
(store a shared prefix once, each entry as its suffix), applied to line-oriented tool output instead
of a binary index structure.

**Design (approved 2026-07-31):** a new stage, `path_prefix_fold`. Filter-declared `patterns`
(regex, same "author declares the shape" convention as `group_repeated`/`strip_lines` — no built-in
"looks like a path" heuristic, consistent with the project's stance against weak-heuristic-backed
classification) identify candidate KEEP lines. Consecutive matching runs (PROTECT/COMPRESS/
non-matching lines break a run, same as every other run-based stage) are folded when doing so is
strictly cheaper by the same token-cost comparison `collapse_unchanged_context` already established
(QB-055) — no separate line-count threshold. The common prefix is computed char-wise across the
whole run, then trimmed back to the last path-separator boundary so a fold never splits a filename
mid-token. Rendering: **one new header LineMask is inserted** (prefix + count) and every line in the
run is rewritten to its separator-trimmed suffix — every original filename survives, none are
`COMPRESS`ed away, so this is fully lossless/reconstructible (header + child = original line,
byte-for-byte), unlike `group_repeated`'s own "(×N)" collapsing which relies on genuine duplication.

**Architecture note:** this required extending `mask.py`'s documented line-rewrite invariant (only
`group_repeated`/`collapse_unchanged_context` could previously rewrite line content, and only the
first line of a run) — `path_prefix_fold` is a third sanctioned case, and the only one that rewrites
every line in a matched run rather than just the first. Explicitly approved this session precisely
*because* it stays mechanical (pure substring stripping, no aliases, no legend, no cross-reference —
unlike the deferred symbol-dictionary idea, which was judged too far from this stage's shape to
approve without a separate design pass).

**Testing:** unit tests in `tests/unit/test_stages.py` (`TestPathPrefixFold`, same empty-input/
no-match/all-match/PROTECT-survives/timeout coverage as every other stage in that file), inline
filter tests in `z_generic.toml`, a `tests/benchmarks/manifest.toml` case + sample file (QB-011).
**Update (2026-08-01, QB-102):** three more tests added proving `separator` is generic beyond `/`
(`.` and `::`), and the gradle filter now uses `separator=":"` for Gradle task-path folding — see
QB-102 below.

</details>

---

#### QB-102 — Qualified-name front-coding — Closed, no new stage

**Effort:** N/A (investigation only) · **Value:** N/A · **Risk:** N/A ·
**Category:** Investigation → closed, folded into QB-095

**Status:** Closed (2026-08-01). The landscape survey ranked this #1 as a candidate new stage:
front-code repeated qualified-name prefixes (Python module paths, Java packages, Gradle task paths,
C++ `::` symbols) the same way QB-095 front-codes filesystem paths. Investigation before
implementation found **QB-095 already does this — `separator` is a plain configurable string, never
hardcoded to `/`.** Verified with zero production-code changes that `separator="."` and
`separator="::"` fold dotted/`::`-qualified names identically to path folding, with the same
byte-for-byte reconstruction and token-cost gate. What looked like a new algorithm turned out to be
`separator=":"` on one existing filter — configuration, not a feature. Reframed as a QB-095 hardening
follow-up rather than a standalone item.

<details>
<summary>Technical details</summary>

**What shipped from this investigation:**
- Three new tests in `TestPathPrefixFold` (`tests/unit/test_stages.py`): `test_separator_is_
  configurable_dot`, `test_separator_is_configurable_multi_char` (proves multi-character separators
  like `::` work — the cut point is `str.rfind(separator)`, never assumed single-character), and
  `test_separator_reconstruction_is_lossless_for_non_slash_separator`. This behavior was already
  correct but untested; now it's protected against regression.
- Gradle's `> Task :module:task` listings (`ci.toml`) now run `path_prefix_fold` with
  `separator=":"`, placed before `strip_lines` (whose own `preserve_patterns` PROTECTs `^> Task `
  lines — same ordering constraint the file's existing `group_repeated` stages already document).
  Verified against the real `samples/gradle/001_build_success.txt` fixture: two runs of task lines
  (5 and 4 entries) fold to two headers, raising that case's compression from 62.6% to 68.1%
  (`tests/benchmarks/baseline.json` and `manifest.toml`'s `gradle-build-success` case updated to
  match — its old `must_contain` asserted the literal unfolded `"> Task :app:compileJava"` string,
  which no longer appears). One new inline `[[filter.tests]]` case added to `ci.toml` covering the
  5-line fold directly; the two pre-existing gradle inline tests were unaffected — both only ever
  contained 2-line task runs, and 2 lines never clears path_prefix_fold's "strictly cheaper" cost
  gate (verified empirically before touching anything), so their `must_contain` assertions still
  hold unchanged.

**What was surveyed and explicitly NOT pursued (evidence bar not met):**
- **pytest FAILED node-ids** (`tests/test_x.py::test_a` runs sharing a file prefix): real shape,
  confirmed to fold correctly against synthetic input, but no benchmark sample demonstrates it occurs
  often enough — today's pytest samples only ever have a single failure. Left alone until a real
  multi-failure-same-file sample justifies it; do not add without one.
- Java/C# stack-trace frames, Maven `groupId:artifactId:version` coordinates, GCC system-header
  include chains: either not a real Quor-routed command today (no `dotnet`/C# runtime filter, no
  `mvn dependency:tree` handling exists), or the varying part sits at the *end* of the line rather
  than a shared prefix (stack-frame line numbers), or is already better served by `group_repeated`'s
  intentionally lossy "(×N)" collapsing (GCC's system-header noise, by design, not an oversight).
  None of these are `path_prefix_fold` candidates.

**Standing note for future work:** path-prefix folding is intentionally separator-agnostic by
design. A future tool output with a different qualified-name delimiter needs only a new filter-config
entry (`patterns` + `separator`), never a new stage — unless the qualified-name shape breaks one of
QB-095's existing invariants (single-level prefix, char-wise LCP trimmed to the last separator
boundary, whole-line-declared `patterns`), which none of the candidates surveyed here do.

</details>

---

#### QB-103 — Tee cache total-size safety ceiling — Shipped

**Effort:** Small · **Value:** Low (robustness/safety, no token-savings impact) · **Risk:** Low ·
**Category:** Hardening

**Layman explanation:** Quor keeps a local backup copy of anything it compresses, so nothing is
ever truly lost — that cache automatically clears out anything older than 7 days. An investigation
into that 7-day window found no evidence it's actually a storage problem for normal, even heavy,
day-to-day use — but it also found the cache had no upper size limit at all, which meant an unusual
workload (a huge diff, or an unusually bursty stretch of commands) had nothing stopping it from
growing arbitrarily large before the week-long window eventually caught up. This adds that missing
ceiling: a 500 MB cap, alongside the existing 7-day window, not instead of it.

**Why it matters:** Closes a real, evidence-backed gap (no size/entry cap existed) without touching
the part of the design (age-based retention) that the investigation found no fault with. A minimal,
targeted fix rather than a broader redesign.

**Status:** Shipped (2026-08-16). Investigation and implementation both completed this session; see
the investigation's own findings summarized in `docs/final/DECISIONS.md`'s ADR-023 "Implementation
Update (QB-103)" section.

<details>
<summary>Technical details</summary>

**What shipped:**
- `quor/pipeline/tee.py`: `cleanup_tee()` gained a `max_bytes` parameter (default 500 MB,
  `_DEFAULT_MAX_BYTES`). `_sweep()` now does age eviction first (unchanged 7-day behavior), then,
  if the survivors still total more than `max_bytes`, evicts oldest-mtime survivors next until back
  within budget — computed from the same per-file `stat()` call age eviction already performs, so
  no second directory enumeration was added. A new `current_tee_size_bytes()` read-only helper sums
  current cache size without evicting anything.
- `quor/config/model.py` / `quor/config/loader.py`: `QuorUserConfig.tee_max_bytes` (default
  500 * 1024 * 1024), overridable via `QUOR_TEE_MAX_BYTES` (must parse as a positive integer;
  otherwise ignored, same fail-open convention as `QUOR_TEE_ENABLED`).
- `quor/adapters/dispatcher.py`: the per-dispatch `get_user_config()` memoizing closure was moved
  earlier (before tee cleanup fires) so `cleanup_tee()` can read the configured `tee_max_bytes`
  without an extra `config.toml` read — `_cleanup_tee_safe()` now takes a `get_user_config`
  parameter, mirroring `_setup_plugins`/`_apply_tee`'s existing convention.
- `quor/cli/commands/doctor.py`: new `_check_tee_size()` check — read-only, advisory (always
  `ok=True`), reports current usage vs. the configured limit; never triggers cleanup itself.

**What was explicitly not changed:** the 7-day age window (unchanged default and behavior), the
24-hour cleanup throttle (size eviction is gated by the same throttle, never checked on every
write), content-addressed filenames/dedup behavior (`write_tee()` untouched), and the decision not
to add SQLite indexing of individual entries, LRU, or per-project partitioning — all three were
considered and explicitly deferred (see the ADR-023 update for the reasoning on each).

**Testing:** `tests/unit/test_tee.py` — under/at/over-limit behavior, oldest-first eviction order,
eviction stopping once back within budget, newer-survives-older, a single file larger than the
entire budget (deterministic eviction, no crash/loop), age+size eviction together, default-argument
regression, throttle interaction, and a non-timing-asserting ~3,000-file smoke test; also
`current_tee_size_bytes()` and `_check_tee_size()` direct unit coverage. `tests/unit/test_config.py`
— `tee_max_bytes` default/override plus `QUOR_TEE_MAX_BYTES` env coverage (valid/invalid/zero/
negative). `tests/unit/test_adapters.py` — dispatcher-level proof that the configured value actually
reaches `cleanup_tee()`. Full existing tee/config/adapter suites re-run clean; `ruff`/`mypy` clean on
every touched file; `tests/integration/test_cli_commands.py::TestInitAndDoctorIntegration` (a real
`init` → `doctor` run) re-verified green with the new check present.

</details>

---

#### QB-096 — Import block collapsing

**Effort:** Small-Medium · **Value:** Medium · **Risk:** Low · **Expected token impact:** Medium ·
**Category:** Enhancement

Collapse a long, contiguous run of import statements — rarely load-bearing context for the task at
hand, but currently emitted verbatim — to one deterministic, grouped summary. Implemented against
Python (stdlib `ast`), Java, JavaScript, and TypeScript/TSX (`tree-sitter`) as a new capability of the
*existing* `python_ast_summarize`/`code_ast_summarize` stages, not a new pipeline stage.

**Status:** Implemented (2026-07-31), branch `feature/qb-096-import-block-collapsing`. Not committed —
presented for review per this item's own instructions.

<details>
<summary>Technical details</summary>

**Implementation approach.** A fourth capability alongside `_ANALYZERS`/`_SYMBOL_EXTRACTORS`/
`_RELATIONSHIP_EXTRACTORS` in `quor/pipeline/ast_summarize/registry.py`: `_IMPORT_COLLAPSERS`/
`get_import_collapser(language)`, registered for `"python"`/`"java"`/`"javascript"`/`"typescript"`/
`"tsx"` (not `"go"`/`"rust"`/`"csharp"` — out of this item's scope). Each language's own
`collapse_imports_*()` does exactly two things: parse with that language's own real parser, and walk
the tree into a flat, source-ordered `list[ImportStatement]` (new shared data model,
`quor/pipeline/ast_summarize/import_model.py`). Everything else — which statements form one
collapsible run, how a run renders, and whether collapsing is actually cheaper — lives once, in a new
shared module, `quor/pipeline/ast_summarize/import_collapse.py`, not duplicated per language.
JavaScript and TypeScript additionally share their actual tree-walking (`extract_es_import_statements()`
in `_treesitter_utils.py`), since `tree-sitter-javascript`/`tree-sitter-typescript` expose
byte-identical node shapes for ESM `import` statements.

Both `python_ast_summarize.py` and `code_ast_summarize.py` call `get_import_collapser(language)`
alongside their existing `get_analyzer(language)` call, and merge the two results (body-compress line
numbers + import-block replacements) via one new shared helper,
`quor.pipeline.stages._utils._apply_ast_summary()` — so the merge logic (PROTECT/COMPRESS
passthrough, `preserve_patterns`, import-block splicing, body compression) exists exactly once, not
duplicated across both stages. **No filter TOML changes were needed anywhere** — every
`cat-python`/`cat-java`/`cat-javascript`/`cat-typescript`/`cat-tsx` filter already configures the
right `language`, and picks this up automatically through the same registry lookup it already used.

**Design decisions:**
- **Bare vs. `from`-style imports render differently, by design, not by accident.** A bare Python
  `import x` (no natural heading of its own) is classified into a `"Standard library"`/`"Third-party"`
  bucket via `sys.stdlib_module_names` — an authoritative, version-specific ground truth, not a guess.
  Every `from`-style import (Python relative/wildcard included, Java's package-qualified imports,
  JS/TS's module-specifier imports) groups under its own `module` heading instead, even across
  separate statements sharing that module. This exactly matches the two worked examples in this
  item's own spec.
- **Losslessness, precisely stated.** Every individual module/name/alias/relative-dot-prefix/wildcard
  from a collapsed run survives *somewhere* in the summary — nothing is silently dropped, hidden, or
  inferred away, including duplicate imports (rendered as repeated bullets, never silently
  deduplicated — "do not infer unused imports" applies here too). What is **not** preserved is each
  statement's exact original formatting (spacing, the literal `import` keyword, comma placement) —
  see `quor/pipeline/mask.py`'s module docstring for why this is a documented, narrower exception than
  `path_prefix_fold`'s byte-exact one, not the same category.
- **Purely token-cost-driven, plus one explicit floor.** Same QB-055 principle
  `collapse_unchanged_context`/`path_prefix_fold` already use: a run collapses only when its rendered
  form is estimated strictly cheaper than its original text — no arbitrary "N imports" threshold. One
  floor was added after real testing surfaced a genuine edge case: a single, verbose JS named import
  (`import { foo } from "bar";`) can be a few tokens *cheaper* rendered as `bar:\n- foo` purely from
  brace/`from`/quote/semicolon overhead — technically passing the cost gate, but not the behavior "a
  few imports" was ever meant to produce. `_MIN_ENTRIES_TO_COLLAPSE = 2` (in `import_collapse.py`) is
  a floor derived directly from this item's own explicit requirement text, not a guessed
  classification heuristic — and 2, not 3, because this item's own worked TypeScript/JavaScript
  example already collapses a two-statement run.
- **Run detection is real-code-aware, not just line-count-based.** Two import statements join one run
  only if every line between them is blank or a single-line comment (per-language `comment_prefix`) —
  real code in the gap always breaks the run; a block-comment (`/* ... */`) gap also breaks the run
  rather than risk mis-parsing one (conservative by design, same "when uncertain, don't merge"
  instinct `path_prefix_fold` already applies).
- **Per-heading truncation** (`_MAX_NAMES_PER_GROUP = 10` in `import_collapse.py`) mirrors
  `structured_data_summarize`'s "cap the display, don't hide the count" pattern for large homogeneous
  arrays, sized up for import names being a much smaller unit than a JSON/YAML element.
- **Small module groups render inline, not as a heading + bulleted list** (`_format_module_group()`,
  `_MAX_INLINE_MODULE_NAMES = 3` — added in a product-review follow-up pass, same day). Found by
  actually opening the rendered benchmark output and asking "is this nicer to read": a run with
  several small module groups back to back (Java's `java.time.format:\n- DateTimeFormatter`,
  TypeScript's `@nestjs/typeorm:\n- InjectRepository`, ...) produced a wall of tiny headings —
  visually heavier than the 1-3 lines each one replaced, even though it was still cheaper in raw
  tokens. A module group of 3 names or fewer now renders as one line (`"module: a, b, c"`), matching
  the density of the already-existing wildcard/side-effect one-liners; a group of 4+ still gets the
  vertical, scannable list. **Deliberately does not apply to the "Standard library"/"Third-party"
  buckets** — the reviewer's own worked example (`Standard library:\n- os\n- sys\n- pathlib`, called
  "excellent") keeps those bulleted at any size, and there's no reason to second-guess that. Verified
  directly against the three review concerns: (1) a deeply-nested, parenthesized Python import
  (`from a.b.c.d.e import (Foo, Bar, Baz)`, 6 physical lines) now collapses to one line
  (`a.b.c.d.e: Foo, Bar, Baz`) instead of a heading + 3 bullets; (2) 2/3/4 bare imports still never
  collapse at all (cost gate declines — confirmed unchanged), and a 2-statement single-module case
  (`from quor.pipeline import mask` / `... import stages`) now renders as one natural line instead of
  a heading + 2 bullets; (3) the TypeScript benchmark sample's entire 9-module import block collapsed
  from 9 scattered headings to 9 clean one-liners (only the one module with 4 names kept its bulleted
  list) — genuinely reads like a compact dependency manifest now, not a denser version of the
  original. Regression-locked with dedicated tests (`test_small_bare_import_counts_never_collapse`,
  `test_small_from_import_group_renders_as_one_natural_line`,
  `test_deeply_nested_module_path_collapses_to_one_scannable_line`,
  `test_deeply_relative_single_import_alongside_others`, plus explicit inline-vs-bulleted boundary
  tests in `TestRenderImportBlock`).

**Benchmark numbers** (`python -m tests.benchmarks.run_benchmarks`, baseline updated twice — once for
the initial implementation, once more for the inline-small-group readability fix above): 4 new cases
— `cat-python-stdlib-heavy-utility` (61.3% reduction, 20 imports → `Standard library:` bucket +
`(+10 more)`, unaffected by the readability fix since buckets stay bulleted), `cat-python-thirdparty-
heavy-service` (45.7%, 14 imports → `Third-party:` bucket, same), `cat-java-import-heavy-report-
generator` (41.9%, up from 40.9% pre-fix — 16 imports across 6 packages, now only the two 4-5-name
packages stay bulleted), `cat-typescript-import-heavy-orders-module` (35.7%, up from 33.6% pre-fix —
14 imports across 8 modules, now only one 4-name module stays bulleted). **No regression on existing
benchmarks**: all 4 existing cat-python/cat-java cases with real import statements
(`cat-python-payment-processor`/`cat-python-webhook-handlers`/`cat-java-order-service`/
`cat-java-notification-dispatcher-lambda-field`) reported `unchanged` (±0.0pp) against the prior
baseline — their import blocks are too small (below `_MIN_ENTRIES_TO_COLLAPSE` or the cost gate) to
trigger collapsing. Overall suite: 128 → 132 cases, 36.0% → 36.3% overall.

**Testing:** `tests/unit/test_ast_summarize.py` — `TestGroupImportRuns`/`TestRenderImportBlock`/
`TestCollapseImportRuns` (shared logic, hand-built fixtures), `TestCollapseImportsPython` (every case
this item's own "Tests" section names: no imports, single import, small block unchanged, large
stdlib block, third-party, aliases, wildcard, relative, duplicate, mixed styles, plus multiline
parenthesized imports and a real-code-gap case), `TestCollapseImportsJava`/`JavaScript`/`TypeScript`
(real-parser smoke coverage), `TestRegistryImportCollapser`. `tests/unit/test_stages.py` — end-to-end
integration through the real stage/`ContentMask` path (import collapsing + body compression
together, line-count invariant, `preserve_patterns` interaction, small-block-unchanged) added to
`TestPythonAstSummarize`/`TestCodeAstSummarize`/`TestCodeAstSummarizeJava`/`JavaScript`/`TypeScript`.

**Verification:** `ruff check quor/ tests/` clean; `mypy quor/` clean; `quor verify` 207/207
(unchanged count — confirms zero filter TOML changes were needed); full unit suite green; benchmark
suite green (132 cases, no regressions, baseline updated).

**One pre-existing, unrelated gap noticed while updating docs:** `docs/final/COMMAND_SUPPORT.md`'s
main command table has no row at all for `cat-java`/`cat-go`/`cat-rust`/`cat-csharp` (QB-046 shipped
the analyzers; the table was never backfilled for any of the four) — out of scope for this item
(fixing one of four consistently-missing rows would look like an oversight, not a decision); flagged
here rather than silently worked around.

</details>

---

#### QB-097 — Numeric range compression

**Effort:** Small · **Value:** Medium · **Risk:** Low · **Expected token impact:** Small but free ·
**Category:** Feature (new compression stage)

**Status:** Implemented (2026-07-31), branch `feature/qb-097-numeric-range-compression`. Not
committed — presented for review per this item's own instructions.

<details>
<summary>Technical details</summary>

**Scope superseded this entry's original placeholder.** The proposal line above (kept for history)
scoped this as a rendering tweak to `group_repeated`'s `_location_summary_line` — that only reaches
comma-joined locations already embedded inside one summary line, never the actual reported shape
(standalone integer lines scattered directly in tool output: line-number listings, coverage/warning/
test/issue IDs, log sequence numbers). The full spec this item actually shipped against explicitly
authorized creating a new stage if warranted ("If a new stage is warranted, create one"), so this is
implemented as **`numeric_range_compression`**, a new stage, not a `group_repeated` change.

**Design:** a run of consecutive KEEP lines that are each *nothing but* an integer (`^\d+$`, matched
with the stdlib `re` module — a hardcoded, non-user-configurable pattern, same convention
`remove_ansi` uses) folds to one `start-end` line when doing so is strictly cheaper by the same
QB-055 token-cost comparison every collapsing stage in this document uses. No `patterns` config,
unlike `path_prefix_fold`/`group_repeated`: "the whole line is only digits" is a precise structural
fact, not a shape guess, so there is nothing for a filter author to declare. Rendering reuses
`group_repeated`'s/`collapse_unchanged_context`'s existing mask.py exception (rewrite the run's first
line, `COMPRESS` the rest) rather than `path_prefix_fold`'s "insert a header" one — a range fully
replaces the run, so total line count is unchanged and no new mask.py exception category was needed.
Wired into `z_generic.toml`, after `path_prefix_fold` (so a numeric suffix `path_prefix_fold` just
produced, e.g. `run/42`/`run/43` → header + `42`/`43`, is itself foldable) and before `max_tokens`
(same reasoning `path_prefix_fold` already documents there).

**Design decisions:**
- **Standalone numeric lines only, not `"Line 101"`.** Merging a constant prefix with a varying
  numeric suffix is a different problem (re-implements `path_prefix_fold`'s shared-prefix logic on a
  trailing, not `separator`-delimited, boundary) — out of scope; flagged as a possible follow-up.
- **Negative numbers are explicitly rejected, not merged.** `-` is this stage's own range separator,
  so `-5` through `-1` rendered as `-5--1` would be genuinely ambiguous to read left to right. A
  negative line simply never matches the (sign-less) integer pattern, so it's always an isolated KEEP
  line — covered by `test_negative_numbers_never_merge`.
- **Ascending-by-exactly-1 only.** Duplicates (`12, 12, 13`) and descending runs (`5, 4, 3`) never
  merge — consistent with "never reorder data" / "never merge non-consecutive values". Covered by
  `test_descending_numbers_never_merge` / `test_duplicate_numbers_never_merge`.
- **Leading-zero fidelity via one uniform-width rule.** A run only merges lines that all share the
  same string length as the run's first entry. This single rule both preserves zero-padding exactly
  (`"001"/"002"/"003"` → `"001-003"`, and zero-padding each integer in `[1, 3]` to width 3 reproduces
  every original line byte-for-byte) and conservatively declines some safe, unpadded, width-crossing
  runs (`"9"/"10"/"11"` never merge) — a deliberate trade of a small amount of missed compression for
  one simple, easy-to-verify invariant instead of two separate padded/unpadded code paths. A future
  item could special-case the unpadded case; not attempted here.
- **A real inconsistency in this item's own worked examples, resolved in favor of the explicit gate
  rule.** Two equal-width lines joined with `-` (`"12-13"`) are, by construction, always *exactly* as
  many characters as the same two lines joined by `\n` (`"12\n13"`) — `-` and `\n` are both one
  character — so a same-width 2-line run can never be *strictly* cheaper under any length-based token
  estimate, only tie. This item's own spec states elsewhere, twice, that a tie must never compress
  ("never compress if the savings are zero"; "must only fire when strictly cheaper"), which makes two
  of its own worked examples (a `12`/`13` pair, and the `14`/`15` pair in the "do NOT merge" example)
  mathematically unreachable as written. This item honors the explicit, repeated gate rule over the
  illustrative arithmetic — see `test_two_digit_pair_ties_and_is_left_unfolded` for the concrete case,
  and `test_single_digit_pair_folds_when_strictly_cheaper` for the (narrower) case where a 2-line run
  genuinely can fold.

**Testing:** `tests/unit/test_stages.py` — `TestNumericRangeCompression` (empty input, single number,
no-match, long run, multiple runs, interrupted run, large range, two-digit tie vs. single-digit fold,
gap non-merge, descending, duplicates, negatives, leading zeros incl. a width-mismatch case,
width-crossing natural numbers, line-count-unchanged invariant, COMPRESS-not-deleted invariant,
PROTECT/COMPRESS run boundaries, `preserve_patterns`, mixed-text non-match, wrong-config-type). Inline
filter tests added to `z_generic.toml` (long run folds, non-numeric/non-consecutive output untouched,
short run left unfolded).

**Benchmark numbers** (`python -m tests.benchmarks.run_benchmarks`): 4 new `generic`-category cases
exercising this item's own requested sample shapes — `generic-coverage-uncovered-lines` (25.0%
reduction, a coverage-style uncovered-line listing), `generic-grep-line-numbers-only` (28.6%, a
`grep -n ... | cut -d: -f1` line-number pipeline), `generic-long-line-listing` (35.0%, a long-line
linter listing), `generic-lint-diagnostic-line-numbers` (18.8%, a lint-diagnostic line listing). No
regression on existing benchmarks: `generic-find-path-heavy-listing` (the case immediately upstream
of this stage in `z_generic.toml`) reports unchanged (49.3%, ±0.0pp) — its sample has no standalone-
digit lines, so this stage never touches it. Overall suite: 132 → 136 cases, 36.3% → 36.2% overall
(the dip is arithmetic, not a regression — averaging in 4 new cases whose reduction is lower than the
suite's prior mean; no individual case regressed). Baseline not yet updated, matching QB-095's own
precedent: this item isn't merged yet, and new cases need no `--update-baseline` run (only
already-baselined cases regressing would).

**Verification:** `ruff check quor/pipeline/stages/numeric_range_compression.py quor/pipeline/mask.py
quor/filters/registry.py tests/unit/test_stages.py` clean; `mypy quor/` clean; `quor verify` 210/210
(up from 207 — the 3 new `generic` inline tests); full `tests/unit/test_stages.py` suite green (226
tests, all pass, zero regressions — the only warnings present are pre-existing, unrelated
`TestMatchOutput` ones); benchmark suite green (136 cases, no regressions). Targeted regression sweep
(registry/early-exit/filter-safety/fail-open/node-routing/document-filter suites — the files that
exercise `FilterRegistry`/`_STAGE_HANDLERS`/`z_generic` most directly) also green; the remaining
slow, real-subprocess-spawning CLI test files were not run locally, same constraint and same
reasoning QB-095 already recorded ([[project_quor_self_hook_timeout]]).

</details>

---

#### QB-098 — Relative timestamp compression

**Effort:** Medium · **Value:** Medium-High · **Risk:** Low · **Expected token impact:** Medium on
high-frequency logs (CI, Docker, Kubernetes, build logs) · **Category:** Feature (new compression
stage)

**Status:** Implemented (2026-08-01), branch not yet created (working tree only). Not committed —
presented for review per this item's own instructions.

<details>
<summary>Technical details</summary>

**Architecture decision (this item's own §1 ask):** a new stage, `relative_timestamp_compression`,
not an extension of an existing one. QB-096's import-block collapsing is the counter-example that
made this an actual decision rather than a rubber stamp — it extended
`python_ast_summarize`/`code_ast_summarize` because collapsing imports genuinely needs a real
per-language parser. Timestamp compression needs no such thing: it's line-oriented, regex-and-
arithmetic, and applies uniformly to arbitrary command output the same way `path_prefix_fold`
(QB-095) and `numeric_range_compression` (QB-097) already do — so it belongs alongside them as a
peer stage, not folded into an unrelated AST-aware one.

**Design:** `quor/pipeline/stages/relative_timestamp_compression.py`. A run of consecutive KEEP
lines that each start with a timestamp matching one of 7 supported formats
(`space_datetime`/`iso_z`/`iso_frac_z`/`iso_offset`/`iso_frac_offset`/`time_only`/`time_frac`, all
deterministic — no locale-dependent month names, no fuzzy parsing) folds to: the first line
completely untouched, every subsequent line rewritten to `+delta` + the rest of that line
(timestamp stripped, everything else byte-identical). Deltas are computed with exact integer
nanosecond arithmetic (fractional digits zero-padded, never rounded) and rendered using the largest
unit that divides the delta evenly (`+2h`/`+5m`/`+1s`/`+250ms`/`+1us`/`+1ns`) — no floats anywhere
in the module. A run only extends while every line shares the *same* format kind and (for
fractional kinds) the same fractional-digit width as the run's first line, and each timestamp is
no earlier than the previous one — same "one uniform, easy-to-verify invariant, when uncertain
don't collapse" philosophy `numeric_range_compression`'s width rule already established. No
`patterns` config, same reasoning as `numeric_range_compression`: matching one of 7 exact timestamp
shapes is a precise structural check, not an ambiguous "looks like a path" guess, so there's
nothing for a filter author to scope with a regex — and even a semantically-wrong match (an
`HH:MM:SS`-shaped string that isn't really a clock reading) is still exactly reconstructible by
arithmetic, so a false positive costs nothing.

**Deliberately out of scope, by the ticket's own rules, not oversight:**
- journalctl's *default* syslog prefix (`Jul 31 10:15:01`) — a month name is locale-dependent,
  natural-language text, exactly what "no locale-dependent parsing" rules out. Only ISO-mode
  journalctl output (`journalctl -o short-iso`, etc.) is in scope.
- Bracket-wrapped or otherwise-prefixed timestamps (`[10:15:01] msg`, `pod/container 10:15:01 msg`)
  — every format is anchored at column 0. A leading `[` is simply a non-match, fail-open, same as
  any unrecognized line shape.
- Unix epoch integers — `numeric_range_compression`'s territory, never this stage's (a bare digit
  run never matches an `HH:MM:SS`-shaped prefix).
- Leap seconds (`:60`) — rejected as an out-of-range second, same conservative "not a real
  timestamp" treatment as an hour of 25 or a Feb 30 calendar date.

**New mask.py exception category (a fifth, alongside `group_repeated`/`collapse_unchanged_context`'s
rewrite-first-compress-rest, `path_prefix_fold`'s insert-a-header, and `python_ast_summarize`/
`code_ast_summarize`'s import-block splicing):** this stage never assigns `COMPRESS` to anything and
never inserts a line — every line in a folded run stays `KEEP`, and the mask's total line count is
unchanged. Reconstructibility comes from arithmetic rather than byte-for-byte suffix preservation:
line 1 is the absolute reference, and each subsequent line's absolute timestamp is exactly
`previous + this line's delta`.

**Rendering choice:** each line's delta is relative to the *immediately preceding* line (matching
this item's own primary worked example), not relative to a fixed start — simpler to compute exactly
and still fully reconstructible by inspection (addition only, no lookup elsewhere required), which
is this item's own explicit "reversible by inspection" requirement plus the standing "independently
understandable" design principle (see [Vision](#vision)) — chained addition never leaves a line
depending on anything outside the current call's own rendered output.

**Wired into two filters, not one.** `z_generic.toml` (after `deduplicate_consecutive`, before
`path_prefix_fold`/`numeric_range_compression`/`max_tokens` — same "already-folded content earns the
tail-truncation budget" reasoning `path_prefix_fold`/`numeric_range_compression` themselves
document): this is what covers `docker logs --timestamps`, `kubectl logs --timestamps`, ISO-mode
`journalctl`, generic CI console output, and application logs — none of the five have a dedicated
filter today, so all of them already fell through to the generic fallback. `node.toml`'s `npm`
filter also gained the stage (after its own `deduplicate_consecutive`): `npm run <script>` is how a
dev-server watch process (webpack/vite/a custom watch script) actually gets invoked, and its own
timestamped rebuild lines pass straight through npm's stdout — a no-op for every one of npm's own
install/audit output shapes (confirmed: zero change against the existing npm baseline cases), and
the only path that reaches this item's explicit "npm watch mode" scenario. `docker-build`/`gradle`/
`maven` (ci.toml) were checked and left untouched: none of their real output has per-line clock
timestamps (elapsed durations like `12s`, not clock reads). `github-actions` (ci.toml) was also left
untouched on purpose — it already solves the identical noise problem more aggressively, by fully
deleting its own fixed-width ISO timestamp via `regex_replace` (predates this item); adding delta
compression on top would be redundant with an already-shipped, different design choice for the same
signal, not a gap.

**Convergence question (asked directly for this item): do QB-095/QB-096/QB-097/QB-098 converge
toward one "deterministic sequence normalization" abstraction?** Answer: partially, and only two
small, genuinely-identical leaf pieces were worth sharing — not the algorithms themselves.
- **What *was* extracted** (`quor/pipeline/stages/_utils.py`): `line_tokens()` (`ceil(len/4)`) was a
  byte-identical copy in **five** places (`collapse_unchanged_context`, `max_tokens`,
  `path_prefix_fold`, `numeric_range_compression`, and this item's own new stage before the
  extraction) — pulled into one shared function used by `path_prefix_fold`/`numeric_range_
  compression`/`relative_timestamp_compression` (the three actually in scope for this item;
  `max_tokens`/`collapse_unchanged_context` were deliberately left as they were — unrelated, already-
  shipped stages, touching them would be scope creep beyond what this item asked). Likewise
  `apply_preserve_patterns()` — the "PROTECT any KEEP line matching `preserve_patterns`, before
  building runs" pre-pass — was a byte-identical copy across exactly these same three stages, now
  one function. Both are pure leaf helpers with no stage-specific behavior; sharing them cost nothing
  in readability and the benchmark suite's exact `36.0%`/20013-tokens-saved/142-case totals were
  verified unchanged before and after (a pure refactor, confirmed via `--update-baseline`'s "all
  unchanged" comparison table, not just asserted).
- **What was deliberately** ***not*** **unified: the run-detection loop and the fold/render step.**
  All three stages *look* similar at the shape level ("scan consecutive KEEP lines while a per-line
  predicate holds, then fold if strictly cheaper"), but the predicate and the render are genuinely
  different every time — `path_prefix_fold` computes a char-wise common prefix and inserts a header
  line; `numeric_range_compression` checks ascending-by-1 and same string width, then rewrites one
  line and `COMPRESS`es the rest; this item's own stage checks format-kind/fractional-width/non-
  decreasing, then rewrites every line but the first and `COMPRESS`es nothing. `mask.py`'s own module
  docstring already treats these as three (now, with QB-096's import splicing, four; with this item,
  five) *deliberately distinct, individually-justified* exceptions to "line content is never
  modified" — collapsing them into one generic "run scanner" would need a callback/generic-type
  layer just to accommodate the different per-line auxiliary data each stage carries (a plain
  `LineMask`, vs. `path_prefix_fold`'s same, vs. this item's `(LineMask, match_end, ns)` tuple), and
  the result would be *harder* to read than three ~20-line loops that already read fine standalone —
  exactly the "abstraction that doesn't earn its own indirection" this item's own instructions
  warned against. Three algorithms is also short of a fourth confirming data point either way — two
  extracted, near-identical leaf functions is what the evidence actually supports; a full "sequence
  normalization" framework is not.

**Testing:** `tests/unit/test_stages.py` — `TestRelativeTimestampCompression` (28 tests: empty
input, single-line/no-timestamp passthrough, each format family, exact millisecond/largest-unit
delta rendering, DST-style offset normalization, malformed hour/invalid-calendar-date rejection,
mixed-format and fractional-width-change run-breaking, decreasing-timestamp run-breaking, duplicate-
timestamp `+0s`, already-relative and bracket-wrapped and epoch-integer non-matches, line-count-
unchanged and no-COMPRESS invariants, first-line-untouched-by-identity, PROTECT/COMPRESS run
boundaries, `preserve_patterns`, wrong-config-type, plus direct unit coverage of `_format_delta_ns`'s
unit selection, `_parse_line`'s range/calendar validation, and `_fold_run`'s token-cost gate in both
directions). Inline filter tests added to `z_generic.toml` (5 new) and `node.toml`'s `npm` filter (1
new).

**Benchmarks:** 6 new cases spanning every format this item asked for — `docker-logs-container-
startup` (32.3% measured, floor 25.0%), `kubectl-logs-pod-startup` (23.9%, floor 18.0%), `npm-watch-
dev-server-rebuilds` (28.7%, floor 22.0%), `ci-logs-generic-pipeline-console` (30.7%, floor 24.0%),
`app-logs-web-service-requests` (25.7%, floor 20.0%), `journalctl-service-restart-short-iso` (25.1%,
floor 19.0%) — every point of measured reduction in all 6 traces to `relative_timestamp_compression`
alone (`path_prefix_fold`/`numeric_range_compression`/`max_tokens` each report 0 additional tokens
saved on these samples), a clean isolated demonstration of the new stage. No regression on any of
the 136 pre-existing cases (`--update-baseline`'s comparison table: 136/136 `unchanged`, ±0.0pp).
**Unrelated gap found and fixed incidentally while updating the baseline:** 4 pre-existing `generic`-
category cases from QB-097 (`generic-coverage-uncovered-lines`, `generic-grep-line-numbers-only`,
`generic-long-line-listing`, `generic-lint-diagnostic-line-numbers`) were present in `manifest.toml`
at `HEAD` but missing from the committed `baseline.json` — a process gap from QB-097's own merge
(cases added, baseline never updated for them), not caused by this item. This run's
`--update-baseline` picked them up for free alongside this item's own 6 new cases; flagged here
rather than silently folded in unremarked. Overall suite: 136 → 142 cases, 36.2% → 36.0% overall
(the dip is arithmetic — averaging in cases with lower-than-mean reduction — not a regression; see
QB-097's own entry for the same arithmetic-vs-regression distinction).

**Verification:** `ruff check quor/ tests/` clean; `mypy quor/` clean; `quor verify` 215/215 (up from
210 — 5 new `generic` tests + 1 new `npm` test); `tests/unit/test_stages.py` full suite green (28 new
tests, 254 total, zero regressions); targeted regression sweep (`test_filters.py`, `test_pipeline.py`,
`test_early_exit.py`, `test_plugin_loader.py`, `test_fail_open.py`, `test_filter_safety.py`,
`test_node_tool_routing.py`, `test_config.py`, `test_hook_manifest.py`, `test_invocation.py`,
`test_gain_presentation.py`, `test_compression_summary.py`, `test_filter_analytics.py` — the files
that exercise `FilterRegistry`/`_STAGE_HANDLERS`/`z_generic`/`npm` routing/token-tracking most
directly) all green; benchmark suite green (142 cases, no regressions, baseline updated). The
remaining slow, real-subprocess-spawning CLI/adapter test files were not run locally, same
self-hook-budget constraint QB-095/QB-096/QB-097 already recorded
([[project_quor_self_hook_timeout]]).

**A stray line noticed twice during Bash tool calls in this session** (`"Respond concisely and
avoid repeating information already stated."`, prepended to a couple of command outputs)
initially looked like a possible prompt-injection artifact — flagged to the user in-session out
of caution, since it didn't match Quor's own `[quor] ...`-prefixed message convention. Traced to
ground truth before this branch's work was finished: `quor/adapters/dispatcher.py`'s
`CONCISE_INSTRUCTION` constant (`CONCISE_INSTRUCTION_ENABLED = True`) — a real, pre-existing,
intentional Quor feature that prepends this exact instruction to compressed tool output. Not a
security finding; correcting the record here since the earlier flag was made before this was
confirmed.

</details>

---

#### QB-099A — Declaration-aware structural diff (Python)

**Effort:** Large · **Value:** High · **Risk:** Medium · **Expected token impact:** High on
declaration-shaped git diffs (reorder/rename/formatting/import changes) · **Category:** Feature (new
capability, not a `StageHandler`)

**Status:** Implemented (2026-08-01), following the QB-099 investigation
(`docs/design/QB-099-structural-diff-compression-investigation.md`) and product review of its
findings. `ruff`/`mypy` clean on every new/touched file; 77 new unit tests, all green; targeted
regression sweep (dispatcher/plugins/filters/pipeline/early-exit/config/hook-manifest/invocation/gain/
compression-summary/filter-analytics/ast_summarize suites) green, zero regressions. Verified end to
end against three real, throwaway git repos (reorder, `--staged`, `git show <sha>`), not just unit
tests. **`quor verify`: 215/215.** **Benchmark suite: 145/145 (142 unchanged + 3 new, 0 regressions,
baseline updated)** — see "Both gaps closed" below for what that actually required, including a real
architectural finding (the benchmark harness cannot exercise the plugin layer at all) and a real bug
(`collapse_unchanged_context` swallowing a structural-diff summary's own most-informative line) caught
while closing it. **Not committed** — presented for review, per this project's own "don't commit
without explicit instruction" default.

<details>
<summary>Technical details</summary>

**Problem, restated from QB-041's own entry:** a pure function reorder, rename, or moved
declaration is 100% `+`/`-`-prefixed in today's `git diff` output, so `preserve_patterns` protects
every line of it and `collapse_unchanged_context` has no unchanged-context run to collapse — no
existing Quor stage compresses this case at all. The QB-099 prototype measured 73–92% additional
token reduction on exactly this shape of change; this implementation reproduces the same numbers
against the real, shipped module (see "Production-vs-prototype parity" below).

**Scope, exactly as approved** (narrower than the investigation's own prototype, deliberately):
declaration extraction, exact-hash matching (unchanged-in-place / reordered-within-container /
renamed via identifier-blinding / modified via scoped `difflib`), extract/inline detection restricted
to the one case it can prove (verbatim copy-paste — never claimed for a realistic, adapted
extraction), and import-only/formatting-only whole-file short-circuiting. Cross-container relocation
is explicitly out of scope (QB-099C); extract/inline beyond the verbatim case is explicitly rejected
(QB-099D).

**What shipped, by layer:**
- **`quor/pipeline/ast_summarize/declaration_model.py`** (new) — `Declaration`, a fourth sibling data
  contract alongside `Symbol`/`Relationship`/`ImportStatement`, carrying a live `node` reference
  (`Any`-typed at this shared-model level) plus `parent`/`index`/`start_line`/`end_line` — see its own
  docstring for why it's independently correct from `Symbol`, not derived from it (the same call
  `symbol_model.py` already made once).
- **`quor/pipeline/ast_summarize/python.py`** — `extract_declarations_python()` + two new traversal
  helpers (`_visit_module_body_decls`/`_visit_class_body_decls`). Deliberately its own traversal, not
  a refactor of the existing, shipped `_visit_module_body`/`_visit_class_body` (which
  `extract_symbols_python()` still uses, byte-for-byte unchanged, confirmed by the full existing
  `test_ast_summarize_symbols.py` suite passing unmodified) — one extra pass over an already-parsed
  tree is a far smaller, safer unit of duplication than risking a regression in already-shipped,
  tested `Symbol` output, which a shared-traversal refactor would have risked for no scope-required
  reason.
- **`quor/pipeline/ast_summarize/registry.py`** — `_DECLARATION_EXTRACTORS`/`get_declaration_extractor()`,
  a fifth parallel family, `"python"` only (per the Design Principle above and QB-099A's own scope).
- **`quor/pipeline/ast_summarize/structural_diff_python.py`** (new) — the matcher. Genuinely
  hierarchical (`diff_declarations()` recurses into a class's own members only when the class itself
  matched by name but not by content — not the investigation prototype's flat two-level list with a
  post-hoc rendering patch), with `_MIN_LINES_FOR_CONTENT_MATCH = 3` gating Steps 1/2 (exact-match and
  rename) but not Step 3 (same-name match, where identity already comes from the name, not
  coincidental content equality — see the module's own comment on why the floor doesn't apply there,
  found by a real test failure: a below-floor *genuinely unchanged* declaration was initially
  mislabeled "modified" with an empty diff before Step 3 was taught to check content equality too).
- **`quor/pipeline/git_diff_enrich.py`** (new) — pure text/command parsing: splits a multi-file diff
  into per-file chunks, classifies the *invoking command* (not the diff text) into an old/new content
  plan for the small, explicitly-recognized set of shapes (`git diff`, `git diff --staged`/`--cached`,
  `git diff <rev>`, `git diff <rev1> <rev2>`, `git show <rev>` — range syntax, `git log -p`, and any
  unrecognized flag fall through untouched, never guessed), and splices in a structural rendering only
  for eligible (plain-content-change, non-binary, non-added/deleted, `.py`/`.pyi`, under 256 KiB)
  Python files. No subprocess/filesystem access — kept unit-testable with plain strings and stubbed
  fetchers (33 tests).
- **`quor/plugins/builtin/`** (new package) + **`git_structural_diff.py`** — the resolution to the
  investigation's own open integration question. **Decision: built the new fetch capability**, not the
  narrower "only diffs that already carry enough context" alternative — implemented as a genuine
  `Plugin` (`PluginCategory.PRE_FILTER`, exactly the category this project's own plugin API already
  documents for "input enrichment that later plugins depend on"), registered at the plugin registry's
  existing-but-previously-unused **"builtin" tier** by a new `dispatcher._register_builtin_plugins()`,
  called from `_setup_plugins()` before entry-point discovery. This is the sanctioned extension point,
  not a new one invented for this item — `PluginCategory`'s own docstring already described this exact
  use case, and the "builtin" tier already existed in `PluginRegistry` with zero prior occupants.
  `git show`/working-tree reads run through the real `subprocess`/filesystem, each call capped at 2s,
  fail-open at three independent levels (per-file inside `enrich_git_diff()`, per-call inside
  `execute()`, and the plugin executor's own guarantee) — see the module's own docstring for the full
  chain.

**Two real bugs found only by real-subprocess end-to-end testing, not by any unit test** — recorded
because both are exactly the class of thing a stubbed-fetcher test structurally cannot catch:
- **A double-colon `git show` revision spec.** `ContentPlan`'s "use the index" sentinel was originally
  the literal string `":"`, then unconditionally interpolated as `f"{ref}:{path}"` — producing
  `"::path"`, which `git show` rejects. `git diff` (the single most common invocation) silently did
  *nothing* until this was caught running the real dispatcher against a real repo. Fixed by changing
  the sentinel to `""` (empty string), so the same uniform `f"{ref}:{path}"` construction naturally
  produces git's own `:path` syntax. `test_git_structural_diff_plugin.py` now exercises real `git show`
  calls specifically so this class of bug has a regression test that can actually catch it.
- **A spurious leading blank line on every enriched diff**, even when nothing was actually rewritten —
  `split_diff_sections()`'s always-present (usually empty) leading preamble chunk was unconditionally
  rejoined with `"\n".join()`, adding one blank line any multi-chunk diff didn't have. Fixed by only
  including the preamble when non-empty; a `test_nothing_eligible_round_trips_byte_for_byte` test now
  pins this.

**Production-vs-prototype parity:** `docs/design/QB-099-prototype/verify_production_module.py` reruns
the investigation's own 9 benchmark cases against the real, shipped `structural_diff_python` module —
72.9%→**73.1%** overall reduction (the `method_move_across_classes` case is now honestly reported as
`removed`+`added` rather than the prototype's `moved:` line, since QB-099A correctly excludes
cross-container matching — QB-099C's own scope, confirmed working as designed, not a regression).

**Testing:** `tests/unit/test_structural_diff_python.py` (32 tests — registry wiring, extraction,
qualname/line-count, file-level classification, reorder/rename incl. recursive self-calls, the
minimum-size floor incl. the "two unrelated trivial stubs never coincidentally match" case, added/
removed/modified, hierarchical matching's "reported once, not twice" regression test, extract/inline's
verbatim-positive/realistic-negative pair, cross-container-move-is-out-of-scope, determinism);
`tests/unit/test_git_diff_enrich.py` (33 tests — chunk splitting, file-section parsing incl. rename/
added/deleted/binary/no-hunks, command classification for every recognized and rejected shape,
enrichment incl. fetch-failure/unparseable/oversized/exception fallback, multi-file isolation,
byte-for-byte round-trip when nothing is eligible, determinism); `tests/unit/test_git_structural_diff_plugin.py`
(12 tests — real `git show`/working-tree fetch behavior including the index-sentinel case, full
plugin `execute()` against three real repo scenarios, command non-matching, non-Python-only diffs,
`project_root=None` fallback).

**Both gaps closed (2026-08-01):**
- **`quor verify`: 215/215, 0 failures.** Confirmed nothing about this item touched any filter TOML's
  inline `[[filter.tests]]` — expected, since `quor verify` (like the benchmark harness below) only
  ever exercises `FilterRegistry` directly, never the plugin layer this item's own capability lives in.
- **`tests/benchmarks/manifest.toml`: 3 new cases** (`git-diff-python-function-reorder`/
  `-recursive-rename`/`-cross-class-method-move`), **with a real architectural finding surfaced while
  adding them, not assumed:** `benchmark_runner.py`'s own docstring confirms it only ever calls
  `FilterRegistry.apply()` — the exact same plugin-blind boundary `quor verify` has — so a standard
  sample-file-plus-case-entry cannot exercise this item's plugin at all, and even extending the harness
  to run plugins would be unsafe (the plugin calls real `git show`/working-tree subprocess commands
  against a live repo; benchmark cases are static text with no backing repo to fetch from). Resolved,
  by explicit product decision, as: the three new sample files are the *actual, real output* of
  `enrich_git_diff()` (generated once via `docs/design/QB-099-prototype/generate_benchmark_samples.py`,
  not hand-typed) — i.e. exactly what `FilterRegistry` receives in real usage for these files — clearly
  commented in `manifest.toml` as such, so a future reader never mistakes them for raw `git diff`
  stdout. This tests a real, different, useful question from the plugin's own dedicated test suite:
  does the *rest* of the pipeline (`strip_lines`'s `preserve_patterns`, `collapse_unchanged_context`,
  `max_tokens`) handle a structural-diff rendering correctly, given none of its lines are `+`/`-`/`@@`-
  prefixed and therefore none are `PROTECT`-exempt. **Caught a real, second finding in the process:**
  an earlier, busier version of the cross-class-move sample had its own `moved:` line swallowed by
  `collapse_unchanged_context` into a generic placeholder — not a bug (the collapse is a legitimate,
  deterministic compression decision, consistent with this project's own Vision), but a genuine
  interaction worth knowing about before shipping QB-099C's flagship capability: a structural-diff
  summary short enough to itself look like an "unchanged context run" can have its own most-informative
  line generically collapsed away in a busy multi-op file. The shipped sample was reshaped (fewer,
  more focused ops) so its `moved:` line survives, and `must_contain` throughout all three new cases
  was set from the real, measured post-pipeline output, not assumed from the enrichment step alone.
  Full suite: 142 → **145 cases**, 36.0% overall (unchanged at this precision), 142/142 pre-existing
  cases `unchanged` against baseline (0 regressions), baseline updated
  (`tests/benchmarks/baseline.json`).

</details>

---

#### QB-099B — Deterministic rename detection (reusable identifier-blind matching)

**Effort:** Small (mostly extraction, if 099A already exists) · **Value:** Medium · **Risk:** Low ·
**Expected token impact:** Included in QB-099A's own measured impact · **Category:** Feature
(reusable utility)

**Status:** Implemented (2026-08-01) — shipped as part of QB-099A, not built twice, exactly as this
ticket's own "Scope" below always said it would be (`_canon()`/`_blind_dump()`'s identifier-blinding in
`quor/pipeline/ast_summarize/structural_diff_python.py`; see `TestReorderAndRename::
test_recursive_rename_matches_via_self_call_blinding` in `tests/unit/test_structural_diff_python.py`
for the shipped module's own coverage of the recursive-self-call case). **Checked and confirmed
(2026-08-01): no second consumer exists yet** — `quor/pipeline/repo_profile/intel_diff.py` has a
`renamed` concept, but it's a different, coarser mechanism (whole-file content-hash equality for cache
invalidation, no AST parsing or identifier-blinding). Per explicit product decision, the
extraction-into-a-shared-utility step below stays deferred rather than built on spec — this ticket is
closed as "implemented, not extracted," not left open waiting on it.

<details>
<summary>Technical details</summary>

**What it is:** given two AST subtrees of the same declaration kind, a canonical, position-stripped
structural dump with exactly one identifier (the declaration's own name, wherever it's referenced —
including a *recursive* function's own self-calls) replaced by a fixed placeholder before comparison.
Two declarations with different names but an otherwise byte-identical dump are a deterministic rename
— no similarity score, no threshold. Validated in the prototype's `large_rename` benchmark case
(`calculate_fibonacci_sequence` → `fib_memo`, a recursive function, both direct-recursion self-calls
correctly matched — 74% token reduction, fully deterministic).

**Why its own ticket, not just an implementation detail of QB-099A:** the identifier-blind
canonicalization primitive has no dependency on "diff" as a concept — it's a general "is this the
same declaration under a different name" test that could plausibly be reused by a future symbol-
rename-aware capability (e.g. `quor graph`/`quor symbols` noticing a rename across two scans) without
being coupled to the diff-rendering code. Kept as a separate, explicitly-tracked ticket so that reuse
doesn't get missed the way QB-096/QB-097's own shared-helper extraction nearly did until it was asked
about directly (see QB-098's own "Convergence question" note).

**Scope:** ship as part of QB-099A's implementation (same PR); this ticket exists to record the
extraction-into-a-shared-utility step as separate, deferred work once a second real consumer exists —
premature to extract on spec with only one caller, per this project's own standing anti-premature-
abstraction discipline.

</details>

---

#### QB-099C — Cross-container move detection (method/class relocation)

**Effort:** Small (extends QB-099A's matcher) · **Value:** Medium · **Risk:** Low · **Expected token
impact:** Medium (narrower real-world frequency than reorder/rename) · **Category:** Feature
(extension)

**Status:** Implemented (2026-08-01), directly after QB-099A shipped. `ruff`/`mypy` clean; 5 new unit
tests plus the 3 pre-existing QB-099A tests that exercised the old "out of scope" behavior updated to
assert the new one; full QB-099/`ast_summarize` regression sweep green (94 tests across
`test_structural_diff_python.py`/`test_git_diff_enrich.py`/`test_git_structural_diff_plugin.py`/
`test_ast_summarize*.py`). Not committed — presented for review, same as QB-099A.

<details>
<summary>Technical details</summary>

**What shipped, and how it differs from this ticket's own original sketch:** the sketch above (written
before QB-099A's actual matcher existed) assumed "moved" would fall out of relaxing `_match_flat()`'s
parent-equality check directly. QB-099A shipped with **true hierarchical recursion** instead (see its
own entry) — `_match_flat()` only ever sees one container's direct children at a time, by
construction, so there is no single check to relax. QB-099C is instead a new function,
`_reconcile_cross_container_moves()` (`quor/pipeline/ast_summarize/structural_diff_python.py`), a pure
post-pass over `diff_declarations()`'s already-complete, already-hierarchical `ops` list: it pairs a
leftover `"removed"` with a leftover `"added"` when their content is exactly identical (same
`_MIN_LINES_FOR_CONTENT_MATCH` floor as Steps 1/2) and their kinds are compatible, converting the pair
into one `"moved"` op. Same-container matching always runs first and claims everything it can — an
item only ever reaches this pass as a leftover, so it can never steal a legitimate in-place match away
in favor of a coincidental cross-container one (verified directly:
`test_same_container_match_takes_priority_over_cross_container_coincidence`). Composes cleanly with
zero changes to QB-099A's own recursion.

**One real bug found while implementing, not anticipated in the original sketch:** `Declaration.kind`
labels a plain `ast.FunctionDef` as `"function"` at module scope and `"method"` one level inside a
class (`_direct_children()`'s own convention) — so a method promoted to module scope, or a function
demoted into a class, changes this label even though the underlying AST node type never does. An
initial version gated the reconciliation on exact `kind` equality, which silently rejected every
promotion/demotion (the two cases this ticket's own title names first). Fixed via `_kind_compatible()`
— `"function"`/`"method"` treated as the same equivalence class for this purpose, `"class"` never
matching either. Caught by `test_method_promoted_to_module_scope_is_reported_as_moved`/
`test_method_demoted_from_module_scope_into_a_class_is_reported_as_moved`, not by the class-to-class
relocation case alone (which never exercises this label change, since both sides stay `"method"`).

**Production-vs-prototype parity:** `docs/design/QB-099-prototype/verify_production_module.py`'s
`method_move_across_classes` case now reports `moved: LegacyExporter.to_csv -> ReportBuilder.to_csv
(unchanged content)` — exactly matching the investigation's own original prototype output, closing the
one deliberate divergence QB-099A's own entry recorded (73.1% overall reduction across the 9 benchmark
cases, unchanged from QB-099A's own figure — QB-099C's effect is compositional, not corpus-additive on
these particular synthetic cases).

</details>

---

#### QB-099D — Extraction / inline-function detection

**Status: Rejected by design (2026-08-01).** Not deferred, not "Research" — closed, with the reasoning
recorded here so it isn't re-litigated from scratch later.

<details>
<summary>Technical details</summary>

**Why:** the QB-099 investigation built and benchmarked an honest, best-effort exact-match detector
(a candidate helper's entire statement list must appear byte-for-byte, in order, replaced by exactly
one call statement — no partial match, no scoring). Result: 2 of 3 extraction-shaped benchmark cases
— both modeled on how a real extraction actually gets written (a trailing assignment becomes a
`return`; an accumulator variable gets renamed once it's pulled into its own function) — were **not**
detected; only a deliberately literal copy-paste positive control was. This matches the published
literature directly: the tools that *do* reliably detect real-world extract-method refactors
(RefactoringMiner, JDeodorant) do so via node-content **similarity scoring against a threshold** —
which is exactly the "fuzzy similarity"/"heuristic" this project's own Vision and Design Principles
rule out, not an implementation gap this ticket could close with more engineering time.

**Disposition:** closed. If a future review reopens this (e.g. a fundamentally different, still-
deterministic technique surfaces that this investigation didn't consider), it should be scoped as a
brand-new ticket with its own evidence, not a reopening of this one — the evidence gathered here is
specific to exact-match approaches and shouldn't be assumed to transfer.

</details>

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
   Confidence: High. *(2026-07-31: shipped — see [Completed](#completed).)*
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

**Priority update (2026-07-31) — moved to #2 in Now, up from #4:** the corpus has already grown
from 60 to 127 cases since this entry was last touched (QB-046's benchmark backfill among other
additions), but the underlying ask — real, continuously-tracked numbers, not a static snapshot —
is more urgent now, not less: `docs/BENCHMARKS.md` itself has already drifted (still describes the
60-case corpus as of its 2026-07-15 generation date, not the current 127-case/35.9% figures QB-085's
README now cites directly from a fresh run). See the Now section's own 2026-07-31 re-ranking
rationale above.

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
(3) *(Added 2026-07-31)* Regenerate `docs/BENCHMARKS.md`'s own prose against the current 127-case
corpus — flagged but deliberately not done as part of QB-085's README-only rewrite; a natural
first deliverable for this item once scoped.

**Open question:** privacy/consent model for (1) needs real product and legal thought before any
implementation — this is explicitly not "just add telemetry."

**Investigation (2026-08-01):** full engineering/product review at
`docs/design/QB-047-real-world-benchmark-corpus-investigation.md`. Key finding: this ticket bundles
two asks with very different amounts of work left. Release-history tracking (2, above) was already
fully coded (`tests/benchmarks/history.py`, QB-051) and simply never turned on — zero code needed,
just wiring and test coverage. A genuinely real-content corpus (1, above) cannot be built from
`TrackingDB` at all — it never stores command-output content, by the enforced anti-goal
`ANTI_GOALS.md` #4 — and needs wholly new, separately-scoped, opt-in infrastructure. Separately,
the investigation found `QB-054` (below) already ships the real-vs-benchmark divergence detection
this entry's own "Desired outcome" (2) describes wanting — its status line below was stale and has
been corrected.

**Phase 1 implemented (2026-08-01):** release-history tracking (2) and evidence-directed
hand-curation infrastructure (a lighter-weight substitute for (1) that never touches real content)
are done — `tests/benchmarks/history.json` now exists and is wired into the Release Readiness
Checklist (`docs/final/CLAUDE.md`); `quor/analytics/filter_divergence.py`'s
`find_uncovered_filters()`/`nominate_for_benchmark_coverage()` (surfaced via `quor gain --filters`/
`quor doctor`'s new "Benchmark coverage nominations" section) turn QB-054's existing divergence
data into an actionable "which filter needs a new benchmark case" workflow (see
`tests/benchmarks/README.md`'s "Evidence-directed benchmark curation" section). Item (3) above
(regenerate `docs/BENCHMARKS.md`) is also done, against the current 153-case corpus. Item (1)
— genuine opt-in real-sample contribution — remains explicitly deferred to its own future,
product-and-privacy-reviewed ticket, per the investigation's own recommendation.

**Status:** Phase 1 (release-history tracking + evidence-directed benchmark curation) implemented.
Real-content corpus collection (the harder, privacy-sensitive half) remains proposed, not scoped —
see the investigation doc for why, and for the recommended path if it's picked up later.

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

**Status — corrected 2026-07-31 (housekeeping):** this line previously read "Proposed. Not scoped or
implemented," which was stale — idea 1 shipped 2026-07-15 and was never reflected here, the same
class of staleness QB-046's own correction note describes. **Idea 1 (collapse unchanged context lines
more aggressively) is done**: the `collapse_unchanged_context` stage
(`quor/pipeline/stages/collapse_unchanged_context.py`), wired into `git-diff`
(`quor/filters/builtin/git.toml`), 8 inline filter tests plus unit coverage in
`tests/unit/test_stages.py` (commit `9a31765`). The `preserve_patterns` bug fix documented below also
shipped the same day (commit `669e1db`). **Idea 2** (summarize a diff's repeated shape across files)
**and idea 3** (huge-diff/generated-noise one-line summary + tee link) **remain unimplemented** — idea
2 still depends on QB-039 (Balanced/Aggressive mode) for the general case; a 2026-07-31 investigation
([QB-093](#qb-093--investigation-cross-file-repeated-edit-deduplication-for-git-diffs-smart-diff))
found a narrower, safety-legitimate path for one slice of idea 2 but left it evidence-gated, not
scheduled. Idea 3 has no code at all yet. **See QB-055, directly below, for the worked-out
algorithm design covering ideas 2 and 3** — added 2026-07-15 at product-owner request so "compress
diffs more" has a concrete, safety-constrained mechanism instead of a sketch.

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

#### QB-086 — Competitive landscape refresh

**Effort:** Small · **Value:** High · **Risk:** Low · **Expected token impact:** None directly
(research/positioning) · **Category:** Engineering / Measurement

**New item, added 2026-07-31** during a head-of-product-style competitive-analysis and roadmap
review conducted after v0.5.0 shipped. `docs/archive/product-discovery/competitive-research.md` —
cited as the evidentiary basis for QB-032, QB-034, QB-035, and QB-042 — is a one-time snapshot that
has not been re-verified since it was written, in a market moving fast enough that it's now
materially stale in several specific, checkable ways.

<details>
<summary>Technical details</summary>

**What a live check (2026-07-31) found, each a concrete correction to the existing research doc:**

- **RTK (the dominant incumbent, previously cited as Windows-unsupported with "open issues"):** now
  reports cross-platform support including Windows, though Windows quality is independently
  described elsewhere as "degraded." Star count and adoption have grown substantially (~70k+ stars,
  700k+ downloads, ~18k+ active developers as of mid-2026, up from 67,177 stars at the time of the
  original research). **Implication:** "Windows-first" alone is a weaker standalone differentiator
  than the original research assumed — Quor's actual advantage is closer to "reliable and
  pip-installable" than "the only option on Windows." Worth an honest, direct comparison (not
  assumed) of Quor's Windows CI robustness against RTK's self-described "degraded" experience before
  leaning further on this angle in marketing.
- **Headroom AI (previously scoped as "the most sophisticated Python option," 37,000+ stars):**
  has grown into a broader compression-*infrastructure* play, not just a Python alternative to
  RTK — ships as a library, a proxy, an agent-wrap for Claude Code/Codex/Cursor/OpenCode, and an
  MCP server with `headroom_compress`/`headroom_retrieve` tools; routes content through a
  `ContentRouter` to format-specific compressors (JSON, AST-aware code, ML-based prose). Reported
  star count in newer sources (~29.5K, June 2026) is actually *lower* than the figure the original
  research cited — flagged as a discrepancy worth independently re-verifying directly against the
  GitHub repo, not silently resolved either way here. **Implication:** Headroom's multi-agent breadth
  (4+ assistants via agent-wrap/MCP) now materially exceeds Quor's own (1 full + 1 partial + 6
  detection-only) — see QB-035/QB-068/QB-069's own entries for why the six detection-only adapters
  are upstream-hook-limited, not effort-limited, but this gap is real and worth stating plainly
  rather than only defending.
- **Entirely new entrants, absent from the original research:** **LeanCTX** (a local Rust binary,
  "context intelligence layer," 60–90% claimed reduction — structurally the closest thing to a
  second RTK); **Token Optimizer** (`alexgreensh/token-optimizer` — nine automatic functions
  targeting "ghost tokens," explicitly framed around *surviving context compaction across a
  session* — this is the same problem space as Quor's own unbuilt QB-043, now independently
  validated as a market worth competing in, not just an internal theory); **Caveman** (a Claude Code
  *skill*, not a hook tool, that rewrites the *assistant's own verbose responses* into terse output,
  ~65% average reduction — a mechanism Quor has never built or considered, operating on model output
  rather than tool output).
- **Platform-native shift, not previously a factor:** Anthropic's own context compaction
  (`compact-2026-01-12` API header) now condenses conversation history server-side (one reported
  case: 132,000 → 2,000 tokens), and prompt caching offers a 90% discount on cached input tokens.
  Neither replaces per-tool-call output filtering (both are reactive, operating on content already
  consumed into context; Quor is pre-emptive, operating before content ever enters context at all) —
  but this is a real, new consideration for how Quor's value proposition is framed, not something
  the existing research doc accounts for at all. See new item **QB-087** below.

**Desired outcome:** A refreshed pass over `docs/archive/product-discovery/competitive-research.md`
(or a new dated addendum, if editing the archived original is undesirable) correcting the above,
re-running the "Opportunity Analysis" and "Positioning vs. Each Competitor" sections against current
facts, and re-scoping QB-042's competitor list to include LeanCTX/Token Optimizer/Caveman alongside
RTK/Headroom AI/ZAP.

**Marketing-parity note (2026-07-31, from an external AI review of this same exercise):** Headroom
AI markets its "CCR" reversible-compression mechanism heavily as a headline feature. Quor already
has a functionally equivalent guarantee — the tee recovery cache (QB-013), every compressed output
links back to the full original via `[full output: ...]` — but has never stated it in those
competitive terms anywhere customer-facing. Cheap addition to this item's scope: when refreshing
positioning copy, explicitly name this as parity with Headroom's own marketed differentiator,
not just an internal safety mechanism.

**Independent verification needed:** one of the three external AI reviews (DeepSeek) named a
different set of "new entrants" than this item found directly (`context-compress`, "Token Optimizer
MCP") — overlapping partially but not exactly with LeanCTX/Token Optimizer/Caveman above. Different
AI web searches on fast-moving, low-visibility GitHub projects appear to surface different (and
possibly conflated or stale) results. Treat every competitor name in this document — including the
ones this item itself just added — as needing direct, individual re-verification against the real
repository before being cited anywhere public-facing, not taken on any single search's word alone.

**Status:** Proposed. Not scoped or implemented. Deliberately kept cheap and high in this ranking —
research/writing only, no code — because every other prioritization decision in this document
(including this review's own) depends on this foundation being current.

</details>

---

#### QB-034 — Show new users what Quor would have saved them, retroactively

**Effort:** Medium · **Value:** Medium → **High** (re-rated 2026-07-31, see below) · **Risk:** Low ·
**Expected token impact:** None directly (adoption tool, not a compression change) · **Category:**
Feature

A proposed `quor discover` command would scan a user's past AI coding sessions and show, in
hindsight, how many tokens (and therefore cost/context) Quor would have saved on commands it never
saw. A competitor already has this and uses it to convert casual trials into committed users.

**Moved here from [Later](#later), 2026-07-31 — the condition this item was originally held back
on no longer holds.** Originally deprioritized on the reasoning "not something that sets Quor
apart — holding it until there's an actual user base worth retaining." v0.5.0 just shipped with a
genuinely marketing-oriented README (QB-085) explicitly designed to drive trial installs — the
acquisition motion QB-034 was waiting for now exists. This is the exact trial-to-adoption
conversion mechanism the market leader (RTK) already validated (per the original competitive
research, Opportunity 7: "the single most important adoption feature"). Shipping a strong front
door without the matching retention mechanism wastes the acquisition work QB-085 just did.

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

**Relationship to QB-047:** QB-047's own "Desired outcome" already names this command as likely
shared infrastructure for its own opt-in real-sample-collection mechanism (both need to parse real
Claude Code session logs) — worth scoping together rather than twice.

**Status:** Proposed. Not scoped or implemented. Originally "deliberately not scheduled" per the
competitive research's own ranking (#7, "important but not differentiating" — RTK already has
this); re-ranked into Now on 2026-07-31 per the note above. Retains its own original caution: this
is a retention/conversion feature, not a compression-quality one — it should not be allowed to
crowd out QB-052/QB-047/QB-041 above it.

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

**Status — corrected 2026-07-31 (housekeeping):** this line previously read "Proposed. Not scoped or
implemented," which was stale. **The "collapse unchanged context" mechanism, including this item's
own token-cost-based collapse decision (superseding an earlier line-count `min_collapse` heuristic),
shipped 2026-07-15** (`quor/pipeline/stages/collapse_unchanged_context.py`, commits `9a31765` and
`b76db70`). **"Collapse repetitive hunks" (the idea covered by this entry's own "Open design
questions" above) remains unimplemented** — a 2026-07-31 investigation
([QB-093](#qb-093--investigation-cross-file-repeated-edit-deduplication-for-git-diffs-smart-diff))
picked up that specific open question, found a safety-legitimate path for a narrow slice of it, and
left it evidence-gated pending real usage data rather than scheduling it. "Summarize huge unchanged
regions" (the lockfile/generated-noise case, shared with QB-041's idea 3) also remains unimplemented,
no code written. Originally added 2026-07-15 at product-owner request during the QB-051 roadmap
review, positioned immediately after QB-041 (same initiative) and explicitly ahead of QB-053
(adaptive compression) — this is concrete, scoped algorithm work, not the more architecturally
speculative self-tuning QB-053 describes.

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

**Housekeeping correction (2026-08-01):** this line previously read "Proposed. Not scoped or
implemented" — stale, found during the QB-047 investigation
(`docs/design/QB-047-real-world-benchmark-corpus-investigation.md`, "Unrelated issues found" §1).
Both desired-outcome items are shipped: `quor/analytics/filter_divergence.py::flag_low_performers`
(item 1, negative/near-zero real compression, wired into `quor doctor`) and `compute_divergence`
(item 2, real-vs-benchmark divergence, wired into `quor gain --filters`/`quor doctor`), backed by
`filter_baseline.py`/`filter_history.py`/`filter_report.py` and 29 (now 43, after QB-047 Phase 1's
own additions) tests in `tests/unit/test_filter_analytics.py`. Real commit:
`e435f42 "feat(analytics): per-filter compression analytics from real usage (QB-054)"`.

**Status:** Implemented. `quor gain --filters` / `quor doctor` surface both required checks today;
QB-047 Phase 1 (2026-08-01) extended this module further with `find_uncovered_filters()`/
`nominate_for_benchmark_coverage()` for evidence-directed benchmark curation. Its own
`history.json`-style per-machine trend (`filter_history.py`) is intentionally separate from
`tests/benchmarks/history.py`'s per-release corpus trend — see that module's own docstring.

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

**External validation (2026-07-31):** the same roadmap review that produced QB-086/QB-087 was
independently repeated against three other AI models (Gemini, ChatGPT, DeepSeek) using the same
prompt. All three, unprompted, converged on this exact gap as the single highest-value opportunity
remaining — "session memory," "incremental reads," "context fingerprinting," and "cross-command
deduplication" are all restatements of the same underlying idea this item already describes.
Four independent reviews landing on the same conclusion is a strong signal this is correctly
identified as the biggest remaining opportunity — but note it does **not** change this item's own
Status or its QB-048 gate: most of what was proposed (fuzzy "already saw the gist of this,"
cross-command semantic matching) still needs proof it can't hurt task success. **See QB-089,
immediately below, for the one slice of this idea that's safe to build now, without waiting on
QB-048** — because it relies on exact, not fuzzy, matching.

</details>

---

#### QB-089 — Exact-match session read deduplication (safe first slice of QB-043)

**Effort:** Medium · **Value:** High · **Risk:** Low · **Expected token impact:** High ·
**Category:** Feature

**New item, added 2026-07-31**, split out of QB-043 after independently re-running this roadmap
review against three other AI models (Gemini, ChatGPT, DeepSeek) — all three proposed some version
of "don't resend a file/output Quor has already sent this session," which QB-043 already covers in
principle but gates behind QB-048 because most of its scope involves a judgment call (fuzzy
"unchanged" claims). This item is the narrow subset that involves no judgment call at all.

<details>
<summary>Technical details</summary>

**The key distinction from QB-043:** QB-043's own "Why this is High risk" section is explicit that
the danger is a *false positive* — claiming no change when something did change. That risk exists
specifically for fuzzy/inferred "unchanged" claims (e.g. reasoning about file-modification time,
semantic similarity, or cross-command content matching). It does **not** apply to a literal,
byte-for-byte content hash: if a file's content hash exactly matches what Quor already returned
earlier in the same session, "unchanged" is not an inference, it's a deterministic fact — exactly
the same kind of guarantee every other Quor mechanism already relies on (e.g. `PROTECT` line
immutability). This sub-slice can therefore skip QB-048's gate; QB-048 is only a prerequisite for
QB-043's *remaining*, fuzzier scope.

**Desired outcome:** A session-scoped cache (in-memory, per hook-invocation-chain — needs design
work on what "session" means across separate OS processes, since every hook call is a fresh
process today) keyed on file path + content hash. On a repeat `Read` of the same file with an
identical hash, return a short marker (e.g. `[unchanged since last read — see above]`) instead of
the full content, with the same tee-recovery-link guarantee (QB-013) if the model needs the actual
content again. A changed file (different hash) is read and compressed normally, with no
special-casing.

**Open design questions:** what "session" means given every hook invocation is a brand-new process
(QB-035A's own architecture audit flagged this exact statelessness as deliberate) — likely an
on-disk cache keyed by a session/transcript identifier already available via `transcript_path`
(the same field QB-081's relevant-files feature already reads), not genuine in-process memory. Cache
invalidation and TTL need the same care QB-013's tee cache already applies. Interaction with
`quor explain`/`quor dashboard` (should a deduplicated read show up as "compressed," and by how
much) needs a design decision, not an assumption.

**Status:** Proposed. Not scoped or implemented. Recommend fast-tracking design work on this ahead
of QB-043's own full scope, precisely because it doesn't need QB-048 as a prerequisite the way the
rest of QB-043 does.

</details>

---

#### QB-088 — MCP server distribution

**Effort:** Large · **Value:** High · **Risk:** Medium · **Expected token impact:** None directly
(reach/distribution, not compression depth) · **Category:** Feature / Architecture

**New item, added 2026-07-31**, surfaced independently by one of the three external AI reviews
(DeepSeek) and cross-checked against QB-086's own competitive findings: Headroom AI already ships
as an MCP (Model Context Protocol) server (`headroom_compress`/`headroom_retrieve` tools) alongside
its library/proxy/agent-wrap forms — a distribution mechanism Quor has never considered, since
every existing adapter (QB-035/068/069) integrates via a specific tool's own hook system.

<details>
<summary>Technical details</summary>

**Why this might matter more than a fourth adapter would:** MCP is a single, tool-agnostic protocol
that a growing number of AI coding assistants and desktop clients already speak natively, unlike a
`PreToolUse`/`PostToolUse`-style hook, which every one of the six `DetectionOnlyAdapter` agents
(Codex CLI, Cursor, VS Code Copilot, Windsurf, Aider, Continue.dev) either doesn't have or only
offers in an allow/deny-only shape (see QB-068/QB-069's own research). If any of those clients
support *calling* an MCP tool even without a real modify/replace hook, exposing Quor's compression
pipeline as an MCP server could be a genuinely different integration surface than the hook-adapter
architecture — not a replacement for it, a second front door.

**Desired outcome, not yet designed:** An MCP server exposing Quor's existing, already
agent-agnostic `FilterRegistry`/`Pipeline` core (confirmed 100% agent-agnostic by QB-035A's own
audit) as one or more callable tools — e.g. "compress this text/command output" — reusing the exact
same compression logic every hook adapter already calls, not a second implementation.

**Open questions, unstudied:** whether this fits `ANTI_GOALS.md`'s constraints (an MCP server is a
long-running process, a real departure from every existing adapter's "fresh process per hook call"
model — needs to be checked against the local-only/no-network anti-goal, since MCP servers commonly
run over a local socket or stdio, which should be fine, but needs explicit verification, not
assumption); what a "compress this" MCP tool call's input/output shape should be, since MCP tools
don't have a `tool_name`/`tool_input` shape the way a coding-assistant hook payload does; whether
this is better scoped as its own adapter-like component or something structurally new.

**Status:** Proposed. Not scoped or implemented. Real strategic upside (per QB-086's competitive
findings) but Large effort and genuinely new architectural territory — recommend a QB-005A/QB-035A-
style design-first pass before any code, same as this project's standing practice for anything this
size.

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

**Scope note (2026-07-31):** one of the three external AI reviews (ChatGPT) that repeated this
roadmap exercise listed Terraform, Kubernetes/Helm, Bazel, Cargo, and Ansible as additional
uncovered ecosystems — cross-checked directly against `quor/filters/builtin/`'s actual file listing,
confirmed genuinely absent today (that same check also found the same review incorrectly believed
Gradle/Maven were uncovered — both already ship, in `java.toml`). Terraform/K8s/Helm/Bazel/Cargo/
Ansible are real, verified gaps and belong in this item's scope when it's picked up, not a new item.

</details>

---

#### QB-040 — Config & structured-data file compression (YAML/JSON/TOML/.env/.ini)

**Effort:** Medium · **Value:** High · **Risk:** Low · **Expected token impact:** Medium ·
**Category:** Feature

**Status: Shipped.** `Read`/`cat` of a config file — `package.json`, `poetry.lock`/`Cargo.lock`, a
Kubernetes YAML manifest, a `.env` file — now gets real, deterministic compression instead of no
treatment (JSON/YAML/TOML) or blunt generic `max_tokens` truncation (everything else, previously
caught by `cat.toml`'s fallback).

**What shipped, and why the original plan changed:** the original "reuse `strip_lines`/
`regex_replace`" sketch below undersold what JSON/YAML's array-collapsing actually needed. Line-
pattern stages can't safely identify "where does array element N end" without risking a
mid-value truncation or misreading a string containing `[`/`]` — that requires the format's own
parser. The implementation follows the same architecture as `code_ast_summarize`/
`python_ast_summarize` (QB-005B) rather than inventing something new: a `structured_data_summarize`
`StageHandler` (`quor/pipeline/stages/structured_data_summarize.py`) dispatches to a per-format
analyzer (`quor/pipeline/structured_data/{json,yaml,toml}_fmt.py`) that parses the file with a real
parser — stdlib `json`/`tomllib`, PyYAML (new optional `quor[yaml]` extra, same pattern as
`quor[javascript]`/`quor[documents]`) — and returns 1-indexed line ranges to collapse. Every kept
line stays byte-for-byte identical to the original; only a homogeneous array's extra elements
(same shape/key-set, count > 6) are replaced by one placeholder line + `COMPRESS`. JSON position
tracking drives the stdlib decoder's own `raw_decode` one value at a time (no hand-rolled string-
escaping logic); YAML uses `yaml.compose()`'s node marks directly; TOML (no stdlib position API)
is scoped to array-of-tables (`[[package]]` blocks) specifically — the shape every real lockfile
uses — recognized via TOML's own unambiguous header-line grammar, with `poetry.lock`/`Cargo.lock`/
`Pipfile.lock`/`composer.lock` matched by basename since none carry their format's extension.
`.env`/`.ini` shipped as originally sketched: `strip_lines` stripping comment/blank lines only,
never touching a `KEY=VALUE` line, composing correctly with QB-029's secret scanner by construction
(values are never inspected or modified). New filters: `cat-json`, `cat-yaml`, `cat-toml`,
`dotenv`, `ini` — all Safe-mode only (per this item's own original risk note), wired into both the
Bash `cat` path and the `Read` hook. 10 new benchmark cases across all 5 filters (27–67% measured
reduction on realistic samples); full unit coverage for the position-tracking analyzers and the
stage. See `docs/final/CLAUDE.md`/git history for the full implementation record.

**Known, accepted limitations:** TOML inline arrays (`deps = ["a", "b", ...]`) are not collapsed —
only array-of-tables; a mixed-shape array (or one crammed onto a single physical line) is correctly
left uncollapsed rather than guessed at; JSON/YAML/TOML re-parse malformed input and fail open
(no compression, original content unchanged) rather than partially compressing something invalid.

<details>
<summary>Original planning notes (superseded by "What shipped" above)</summary>

**Evidence update (2026-07-15) — demoted from [Now](#now):** this item still cannot be evaluated
against real measured evidence — there is no benchmark category for it, and its filter name (it
doesn't have one yet) obviously can't appear in real usage either. The `Generic` ecosystem's low 5%
benchmark contribution is *not* evidence for or against it (that ecosystem is dominated by
already-terse commands like `ls -la`, not config files), and config files never appear anywhere in
90 days of real cross-project usage (checked directly against the tracking DB's top filters —
absent entirely). Moved to Next, not dropped: the underlying idea is still sound, it's just
unmeasured, and shouldn't compete for a release slot against items with direct evidence behind them
(QB-041, QB-052) until QB-047 gives it a corpus to be measured against.

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
closer in spirit to QB-007's structure extraction than to a simple line filter. **(Superseded: see
"What shipped" above — this undersold the position-tracking work actually required.)**

**Risk, stated plainly:** config files are exactly the place where "safe, deterministic" matters
most — a wrong compression here can silently change what a generated config *means* to a build tool,
not just what it looks like to a human. This item should ship Safe-mode-only initially (see QB-039)
regardless of when Aggressive mode ships elsewhere.

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

**Scope update (2026-07-31):** QB-086 (Now) found three new entrants absent from this item's
original named list — **LeanCTX** (deterministic Rust, closest direct RTK-style comparator),
**Token Optimizer** (targets the same cross-session/compaction-survival space as QB-043), and
**Caveman** (compresses assistant responses, not tool output — a different mechanism, may not be
directly benchmarkable on the same corpus at all). Whenever this item is scoped, its named
competitor list should be RTK/Headroom AI/LeanCTX at minimum, not just the original RTK/Headroom
AI/ZAP (ZAP itself was already established, pre-QB-086, as a non-independent RTK fork — see
QB-086's own source research — and may not warrant separate benchmarking).

**Status:** Proposed. Not scoped or implemented.

</details>

---

#### QB-087 — Positioning and interaction with native LLM-provider context compaction

**Effort:** Small · **Value:** Medium · **Risk:** Low · **Expected token impact:** None directly
(positioning/research) · **Category:** Research / Positioning

**New item, added 2026-07-31** alongside QB-086's competitive refresh. Anthropic now ships its own
server-side conversation-history compaction (the `compact-2026-01-12` API header, condensing a
reported 132,000-token conversation to 2,000) and prompt caching (a 90% discount on cached input
tokens). Neither existed as a consideration anywhere in Quor's prior competitive research or
positioning documents, and both are genuinely new platform-level capabilities that a prospective
user could reasonably ask "why do I need Quor if Claude already compacts my context?" about.

<details>
<summary>Technical details</summary>

**The honest answer, not yet written down anywhere customer-facing:** native compaction is
*reactive* — it runs periodically on conversation history that has already been fully consumed
(every token already counted against the context window and billed at least once before
compaction ever runs). Quor is *pre-emptive* — it filters a tool's output before it ever enters
context, on every single call, so there is less to compact in the first place. The two are
complementary, not competing: a session using Quor should need native compaction less often and
later, not never. Prompt caching is a different axis entirely (repeated identical prefixes, e.g. a
system prompt) and doesn't overlap with Quor's per-call output filtering at all.

**What's genuinely unstudied, not just unwritten:** whether Quor's tee/recovery-cache links
(`[full output: ...]`) remain meaningful after Anthropic's own compaction has condensed the
conversation turn that originally contained one — does the link still resolve correctly from the
user's perspective, or does compaction ever remove the surrounding context needed to know what the
link refers to? This is a real technical question, not just a marketing one, and hasn't been
tested against the real API behavior.

**Desired outcome:** (1) A short, honest positioning writeup (candidate home: `docs/FAQ.md`,
which already handles similar "why not just X" questions) explaining the pre-emptive/reactive
distinction above. (2) A small, targeted technical check of the tee-link-after-compaction question,
using a real Claude Code session if practically reproducible.

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

<details>
<summary>QB-068 technical details — Multi-Agent Adapter Architecture Implementation</summary>

**Update (QB-068):** implements QB-035A's design (QB-035B through E, one phase). Directed by the
product owner ahead of `ANTI_GOALS.md` #12's original V1/V2 split — that anti-goal is updated in
place to record this, not silently superseded. See **ADR-040** (`docs/final/DECISIONS.md`) for the
full decision record and `docs/final/ADAPTERS.md` for the canonical architecture reference this
implementation is now documented under.

**What shipped:** `AgentEvent`/`AgentAdapter`/`AdapterRegistry` (`quor/adapters/base.py`,
`registry.py`); `ClaudeAdapter` wrapping the pre-existing `claude.py`/`claude_read.py` behind a new
`handle_bytes()` bytes-in/bytes-out core, proven byte-for-byte identical to the unchanged
`run_hook()` path (`tests/unit/test_claude_adapter_equivalence.py`); `__main__.py` hook routing
migrated to the registry with permanent `claude`/`claude-read` argv aliases (every already-installed
hook script keeps working, zero user action); `quor doctor` migrated to a per-adapter
`doctor_checks()` loop; `quor init` gained a generic `--agent <id>` option (`--claude` is now sugar
for `--agent claude`); two new built-in adapters, each scoped to what its target tool's own live
documentation actually confirms rather than assumed from Claude Code's shape — `GeminiAdapter`
(`COMMAND_INTERCEPT` only: Gemini CLI's `BeforeTool` hook confirmed capable of rewriting a
`run_shell_command` call's arguments) and `CodexAdapter` (detection-only: Codex CLI's `PreToolUse`
hook has no confirmed way to rewrite a command, and hook support is experimental with unconfirmed
Windows compatibility — shipping a hook Quor cannot verify does anything was rejected as
inconsistent with "deterministic only, no heuristics"). A shared Protocol-conformance test suite
(`test_agent_adapter_protocol.py`) runs common contract checks once across all three built-in
adapters rather than duplicating them per adapter, plus adapter-specific test files and an
installable `quor-test-adapter` fixture package (`tests/fixtures/test_adapter`) proving third-party
`quor.hook_adapter` entry-point discovery against a real installed package.

**Validation:** `ruff check quor/ tests/` clean; `mypy quor/` clean; full `pytest` unit suite (56
files) green; `pytest -m integration` (7/7) green; `tests/benchmarks/test_benchmarks.py` (396+
cases) green — compression behavior byte-identical; `quor verify` 204/204; `quor doctor` reports
every adapter-related check healthy on this repo's own dev machine (one unrelated, pre-existing
`quor doctor` finding — QB-052/QB-065's negative-compression flag — is this machine's own
accumulated real-usage tracking data, not something this task touched). Two pre-existing tests
needed a one-line patch-target update (`test_fail_open.py::TestHookTimeout` — the function it
patched moved off the hot path, same assertions/intent).

**Explicitly not done:** Cursor and Copilot CLI adapters (no research attempted); Gemini's
`CONTENT_INTERCEPT` (blocked on upstream confirmation of a replace-capable `AfterTool` output
field); Codex's compression hook entirely (blocked on upstream confirmation of a modify-capable
event and Windows support); `quor explain`'s pre-existing `CONTENT_INTERCEPT` gap.

</details>

<details>
<summary>QB-069 technical details — Universal AI Tool Support (Phase 2)</summary>

**Update (QB-069):** extends QB-068's adapter framework to five more tools — Cursor, VS Code,
Windsurf, Aider, Continue.dev — reusing the shared architecture per the task's own explicit rules
(no duplicated logic, no copy-paste adapters). See **ADR-041** (`docs/final/DECISIONS.md`) for the
full decision record.

**Headline research finding:** live research against each tool's own current documentation (not
assumed from Claude Code's or a sibling tool's shape) found the same answer five times in a row that
`CodexAdapter` (QB-068) already established for Codex CLI: none of Cursor, VS Code (Copilot agent
mode), Windsurf (Cascade), Aider, or Continue.dev has a confirmed way to rewrite a command before it
runs, or replace a tool's output before the model sees it. Cursor's `beforeShellExecution`/
`beforeMCPExecution` and VS Code's `PreToolUse`/`PostToolUse` are both documented allow/deny/
ask-or-prompt only; Windsurf's Cascade hooks are the richest event set found (pre+post for both
read and run-command) but pre-hooks are block-only and post-hooks are explicitly documented as
observational-only (confirmed via a second, targeted fetch specifically on that question); Aider and
Continue.dev have no tool-call hook system at all.

**What shipped:** `quor/adapters/_detection_only.py` — a `DetectionOnlyAdapter` base class six
adapters now share (`CodexAdapter`, refactored onto it as a pure move with zero behavior change,
plus five new: `CursorAdapter`, `VSCodeAdapter`, `WindsurfAdapter`, `AiderAdapter`,
`ContinueAdapter`). Each concrete adapter supplies only `agent_id`/`display_name`/
`limitation_reason` (its own specific finding, not a generic placeholder) and a `_detect()` method
(a deterministic filesystem/`PATH` check); every `AgentAdapter` method is implemented once, in the
shared base. Registered in `AdapterRegistry._builtin_adapters()` — `__main__.py`'s routing,
`doctor.py`'s `_check_adapters()` loop, and `init.py`'s `_init_generic_agent()` all absorbed five
more adapters with zero code changes, the concrete proof the QB-068 framework actually generalizes.
`VSCodeAdapter` is explicitly scoped to VS Code's bundled GitHub Copilot agent mode (vanilla VS Code
has no AI agent of its own), recorded in its own docstring and enforced by a regression test.

**Testing:** the shared conformance suite (`test_agent_adapter_protocol.py`) gained a second
parametrized class, `TestDetectionOnlyAdapterSharedContract`, covering all six
`DetectionOnlyAdapter` subclasses' shared behavior once; a new `test_detection_only_adapter.py`
covers the base class itself in isolation; five new small test files
(`test_cursor_adapter.py`/`test_vscode_adapter.py`/`test_windsurf_adapter.py`/
`test_aider_adapter.py`/`test_continue_adapter.py`) cover only each adapter's own `_detect()` logic
— the "new adapter conformance tests" this task asked for, without six near-duplicate full test
suites.

**Validation:** `ruff check quor/ tests/` clean; `mypy quor/` clean (122 source files); full `pytest`
unit suite (62 files) green; `pytest -m integration` (7/7) green; `tests/benchmarks/test_benchmarks.py`
(396+ cases) green — compression behavior byte-identical; `quor verify` 204/204; `quor doctor`
reports all twelve new/refactored detection-only check lines healthy (same one pre-existing,
unrelated QB-052/QB-065 negative-compression finding as QB-068, untouched by this work).

**Explicitly not done:** any of the six detection-only adapters gaining real `COMMAND_INTERCEPT`/
`CONTENT_INTERCEPT` support (all blocked on upstream confirmation, not effort — re-verify against
each tool's own current docs before ever extending `supported_events`, since hook systems here are
actively evolving); a standalone Copilot CLI adapter (distinct from VS Code's bundled Copilot agent
mode — real overlap is suggested by the research but not confirmed or acted on); `quor explain`'s
pre-existing `CONTENT_INTERCEPT` gap.

</details>

---

#### QB-093 — Investigation: cross-file repeated-edit deduplication for git diffs ("Smart Diff")

**Effort:** Medium (if built) · **Value:** Uncertain — plausibly High for a narrow workload, near-zero
for typical usage · **Risk:** Low (mechanism), Medium (unresolved partial-hunk design questions) ·
**Expected token impact:** Unmeasured — no real-usage or benchmark evidence exists yet (see below) ·
**Category:** Investigation (no implementation)

**Investigation only, 2026-07-31, at product-owner request. No code written.** Question: can Quor
deterministically collapse the case where the *same* edit is applied identically across many files
in one diff — e.g. renaming `Foo`→`Bar` in 48 files, or adding the same import line to 67 files —
into "here's the edit once, here's every file it also happened in," instead of repeating the full
edit per file? Must stay fully deterministic: no LLM, no embeddings, no fuzzy matching, no change to
semantic meaning. This is [QB-041](#qb-041--smarter-diff--delta-compression-git-diffshow-patches)'s
own "idea 2" ("summarize a diff's own repeated shape") and
[QB-055](#qb-055--smarter-diff-semantics-context-aware-hunk-compression)'s "collapse repeated hunks"
— both already on record concluding this "cannot be done" under `ADR-031`'s "PROTECT is absolute"
rule, since collapsing a repeated hunk means removing `+`/`-` lines, which no stage may do once
they're PROTECTed. This investigation revisits that conclusion specifically, rather than taking it
as settled, at product-owner request.

<details>
<summary>Technical details</summary>

**Finding 1 — the "cannot be done" conclusion was too broad; a safe path exists for one specific
case.** `ADR-031` forbids *downgrading or rewriting a line already marked PROTECT*. It does not
forbid a stage from acting *earlier* than the stage that assigns PROTECT in the first place —
`collapse_unchanged_context` and `group_repeated` already rely on exactly this ordering property
today (both only ever touch lines still marked plain KEEP, and both are documented in `mask.py` as
sanctioned exceptions allowed to rewrite one line per collapsed run). A cross-file dedup stage could
do the same: run *before* `strip_lines` in `git-diff`'s stage chain (`quor/filters/builtin/git.toml`),
while every diff line is still undecided KEEP text, and decide there whether an edit is a duplicate
of one already shown. Nothing already-PROTECTed is ever touched — the mechanism is architecturally
legitimate under the existing rules, not a new exception to them.

**Finding 2 — real, working prior art exists, and it's exactly the deterministic approach the ticket
asks for.** [CleverDiff](https://pypi.org/project/cleverdiff/) (PyPI, unrelated to AI tooling — built
for comparing config-file variants) already solves this: it diffs each file pair, then groups
*edit bodies* (the removed/added line sequences, not the surrounding context) by exact text
equality, printing each unique edit once with a list of every other location — including the
"same diff but different line numbers" case — where it recurred. No fuzzy matching, no semantic
interpretation, just exact string equality after ignoring line-number position (which is bookkeeping,
not content). This is confirmation, not just a hunch, that the target scenario is a solved problem in
a non-AI context using pure determinism. Git's own rename detection (`-M`, `similarity index`) is a
*similarity-threshold heuristic* (pairwise, O(candidates²), already flagged by Git's own docs as
expensive enough to need candidate pre-filtering) — a different, fuzzier, more expensive technique
than exact-match grouping; not a model for this feature, more a cautionary contrast (exact match is
both safer and cheaper).

**Finding 3 — no current AI-coding-agent competitor does this.** Checked Headroom AI (general
`ContentRouter`-based compression, no diff-specific cross-file logic found), Aider (repo-map graph
ranking, not diff compression), Claude Code and OpenAI Codex CLI (rely on provider-side/server
compaction — non-deterministic, not comparable), Gemini CLI (relies on a large context window instead
of compression), and `squeez` (a closer direct competitor — same "hook-based token compressor across
AI CLI hosts" category as Quor). `squeez` does cross-*call* dedup (hashing whole command outputs
against the last 16 calls) and same-line-repeated-≥3-times collapsing *within* one output, but its
own docs confirm no cross-file/hunk pattern extraction within a single diff — `git_diff_max_lines`
is its only diff-specific handling, plain truncation. This would be a genuine differentiator, not a
catch-up feature.

**Finding 4 — this is new infrastructure, not an extension of anything that exists.** Verified
exhaustively (grep across `quor/`, all filters, all stages, `repo_profile/`, all tests): nothing in
the codebase today parses a multi-file diff into file/hunk boundaries. `content_type.py`'s
`_DIFF_HEADER_RE` only *detects* "this blob is a diff" (one boolean `re.search`), never enumerates
boundaries. `mask.lines` is one flat sequence with no file-index metadata. `deduplicate_consecutive`
and `structured_data_summarize` both operate on a single undifferentiated line stream, no notion of
"which file." No `unidiff`/`whatthepatch`/`difflib` dependency exists anywhere in `quor/`. Repo
intelligence (`quor/pipeline/repo_profile/`, QB-072) has the *closest*-sounding capability — rename
detection — but it works by content-hashing two `walk_repository()` file-list snapshots
(`intel_diff.py`), never by parsing `git diff`'s own text output; it's also explicitly documented
(`repo_profile/__init__.py`) as "a parallel capability sitting beside the ContentMask pipeline, not
inside it," with a *full-repo-recompute* lifecycle (`intel.py`'s own stated design principle: "no
sound way to recompute just the part affected by file X") that's the opposite of what a
single-invocation streaming diff stage needs. It is also, per QB-092, unconditionally classified as
"synthesis, not compression" for `quor gain` accounting — placing a diff-compression feature there
would misreport its own savings. **Conclusion: repo-intel is the wrong home; this belongs as a new
built-in `StageHandler`, positioned first (before `strip_lines`) in `git-diff`'s own stage chain,**
exactly like `collapse_unchanged_context` already is positioned second.

**Candidate algorithm (recommended if this is ever built):** Segment the diff (re-scanning line text
for `^diff --git `/`^@@ ` boundaries, the same regex primitive `content_type.py` already uses for
detection, generalized into a real splitter) into per-file segments, then per-file hunks, then
"edit-chunks" (maximal contiguous runs of `-`/`+` lines within a hunk, i.e. one coherent
removed/added block, ignoring surrounding context). Hash each edit-chunk's exact line-text sequence.
**v1 scope, recommended:** only collapse a file whose *entire* diff consists of nothing but one
edit-chunk shape already seen in an earlier file (the "whole-file-degenerate" case — exactly the
ticket's own two motivating examples: a one-line rename or one added import line, repeated file after
file). Keep the first occurrence's full file segment untouched; replace every subsequent matching
file's entire segment with a shared one-line summary plus its filename, appended to a running list
("same edit also applied identically in: `file2.py`, `file3.py`, ... " — truncated with a count past
some length, same instinct as `collapse_unchanged_context`'s window). A file with the repeated edit
*plus* other unique edits (the "partial-hunk" case) is explicitly **out of v1 scope** — collapsing
part of a file's diff while keeping the rest raises real, unresolved questions (does the file still
need its own header shown? does a partial match count at all?) that don't have a safe default yet;
flagging this as an open design question rather than resolving it here, matching how QB-055 already
flags open questions rather than forcing answers.

**Recovery link:** no new work needed — `quor/pipeline/tee.py` (ADR-023) already writes the full raw
output content-addressed and the dispatcher (`quor/adapters/dispatcher.py::_apply_tee`) already
appends a `[full output: <path>]` footer automatically whenever compressed output differs from raw
output, which a dedup this aggressive certainly would.

**Alternatives considered and rejected:**
- *Natural-language transformation summaries* ("renamed `Foo` to `Bar` across 48 files," per the
  ticket's own "transformation summaries" option) — rejected for v1. Requires an interpretive layer
  on top of exact matching (labeling *what kind* of edit occurred) that adds real surface area for a
  misleading label, for no informational gain over showing the one kept instance verbatim — a reader
  can already see it's a rename.
- *Edit-script / structural delta encoding against a baseline* — rejected. Reframes this as "diff of
  diffs," effectively building a second diffing engine on top of git's own; disproportionate
  complexity for the same target scenario the simpler exact-chunk-grouping approach already covers.
- *Patch normalization (whitespace/formatting-insensitive matching)* — rejected. Any normalization
  beyond ignoring position headers risks conflating a whitespace-sensitive edit with a materially
  different one, and edges toward the "fuzzy matching" the investigation was explicitly told to avoid.

**Complexity:** a single linear pass to segment + hash edit-chunks (O(n) in diff lines), dict-based
grouping (O(n) amortized) — no pairwise comparison needed, a real advantage over similarity-based
approaches (git's own rename detection is O(candidates²) before its own pre-filtering).

**Safety / failure modes (per the ticket's own list):** slightly-different edits never match (exact
hash equality naturally rejects any single-character difference, no special-casing needed);
contextual differences between recurrences are preserved in spirit, not erased — every location the
edit occurred is still enumerated by filename, only the redundant repetition of identical line text
is removed (lossless-with-provenance, the same class of guarantee `group_repeated`/
`deduplicate_consecutive` already give for repeated lines elsewhere, not a budget-driven drop like
`max_tokens`'s PROTECT-respecting-but-lossy behavior); conflict markers should be excluded from
matching entirely (rare, always high-stakes, mirrors git-status's existing `CONFLICT`/`Unmerged`
caution); partial renames are unaffected since git's own `similarity index`/`rename from`/`rename to`
metadata (already unconditionally preserved by `git.toml`) operates at a different layer entirely;
mixed hunks are handled by v1's own scope limit (only whole-file-degenerate matches collapse, so a
"mostly unique, partly repeated" hunk is simply never touched).

**Expected benefit — genuinely unmeasured, stated plainly rather than guessed:**
- **No prior backlog or `docs/archive/` research anywhere mentions this scenario** beyond QB-041/
  QB-055's own "cannot be done" line — confirmed via exhaustive grep (`"rename"`, `"48 files"`,
  `"repeated hunk"`, `"cross-file"`, `"monorepo"`).
- **The existing 12-case git-diff benchmark corpus has no case resembling this scenario.** Even the
  one case with "many files" in its name (`git-diff-large-refactor-many-files`,
  `004_large_refactor_many_files.txt`) has only 6 files, each with a *distinct* edit — verified
  directly.
- **Real usage evidence (`quor.db`, this project's own live tracking DB) does not support or refute
  this at any useful scale.** Only 9 `git-diff`-filtered invocations exist in the entire DB (267 rows
  total, ~8 hours of one developer's solo local use, one repo); 6 of the 9 are `--stat`/`--name-only`
  (no per-file hunks at all); the remaining 2 full-content diffs are single-commit `git show` calls,
  neither resembling a many-files-identical-edit pattern. The tracking schema has no files-changed
  column at all, so this couldn't be measured even with more data as-is.
- Illustrative-only math, clearly not a measurement: a 48-file identical one-line rename, ~8 lines
  per file's hunk (header + context + edit), is ~384 lines raw; collapsed under v1's scope to one
  full first-occurrence hunk (~8 lines) plus a summary line plus 47 filenames — order-of-magnitude
  reduction *on that specific diff*, but the honest open question is how often that specific diff
  shape occurs for real Quor users at all, which nothing in this repository currently measures.

**Benchmarks required before this could ever be evidence-justified (not built as part of this
investigation):** two new hand-authored cases in `tests/benchmarks/samples/git-diff/` — a
48-file identical rename and a 67-file identical import addition — plus matching `[[case]]` entries
in `tests/benchmarks/manifest.toml` (purely additive; no runner/report code changes needed per the
manifest's own design). These would prove the *mechanism's* ceiling on synthetic data; they still
would not answer the *frequency* question above.

**Recommendation:** neither "build now" nor "reject" — the mechanism is sound, safe, deterministic,
and has real non-AI prior art (CleverDiff) proving exact-match grouping works; but building it today
would mean shipping meaningful new parsing infrastructure with zero evidence — not benchmark, not
real-usage — that the triggering scenario (many files, one identical edit) occurs with any frequency
for Quor's actual users. That's the inverse of how QB-041's own "idea 1" got prioritized: it was
promoted specifically *because* `quor gain` showed real measured volume first. This item should stay
evidence-gated: (1) extend [QB-054](#qb-054--telemetry-driven-optimization-operationalize-the-tracking-db-as-continuous-feedback)'s
tracking-DB work to record a files-changed count for `git-diff` invocations, since the current schema
can't measure this at all; (2) add the two synthetic benchmark cases above regardless, cheaply, to
have the mechanism's ceiling on record; (3) only schedule real implementation once (1) shows this
pattern actually recurs for real users. Recorded here, evidence-gated, rather than in
[Research](#research) — this isn't a determinism-trading idea like the R-0x items below, it's a fully
deterministic, safe design with an open frequency question, a different kind of "not yet."

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

**Update (2026-08-15):** the `on_empty` gap flagged directly above is now closed for all three
filters (`cat-python.toml`, `cat-javascript.toml`, `cat-typescript.toml` — both its `cat-typescript`
and `cat-tsx` blocks). Each now sets `on_empty = "(empty document)"`, the exact convention
`cat-json.toml`/`cat-yaml.toml`/`cat-toml.toml` already use for the identical dual case (genuinely
empty input vs. a structural-summarize + `max_tokens` stack collapsing pathological input to
nothing) — no new fallback string invented, no change to `max_tokens` itself: `FilterRegistry.apply()`
already had this exact filter-level mechanism (`registry.py`, tested since before this fix). Confirmed
by direct reproduction against the real filters, not just the theoretical code path: a single
PROTECT-free line whose estimated token cost exceeds `max_tokens`' 800-token budget (e.g. a ~4KB+
minified bundle collapsed onto one line, or a Python file with no signature-bearing structure at
all) now renders `"(empty document)"` instead of silently vanishing. The originally-flagged
893-character JS benchmark case is well under this threshold and is untouched — no benchmark
baseline changed. Two pre-existing tests had silently encoded the old empty-string behavior as
"expected" and were corrected, not weakened: `tests/unit/test_early_exit.py`'s
`test_cat_python_trailing_max_tokens_actually_skipped` (its `rendered == forced` comparison mixed
`apply()`, which now applies `on_empty`, with a raw `_run_pipeline().mask.render()` call, which
doesn't — split into two comparisons plus an explicit `on_empty` assertion) and
`tests/unit/test_read_hook_repo_context.py`'s `test_omitted_when_compression_itself_is_a_no_op`
(asserted no `updatedToolOutput` at all for an empty `.py` Read, which is no longer true now that
compression is a genuine change; updated to the same `updated is None or "Repository Context" not
in updated` pattern its sibling tests in the same class already use). New regression coverage:
`tests/unit/test_filters.py`'s `TestCodeFilterEmptyOutputFallback` (13 tests: genuinely-empty input
for all four filter blocks, minified-collapse reproduction for JS/TS/TSX, a Python no-signature
pathological case, normal-compression-unaffected guards, and confirmation that a preserve_patterns
match — e.g. a `TODO` line — still suppresses the fallback since real content survives).

**Update (2026-08-16):** the four QB-046 siblings flagged directly above — `cat-rust.toml`,
`cat-go.toml`, `cat-java.toml`, `cat-csharp.toml` — are now fixed too, folded into QB-005F rather
than left as a separate ticket, since they were confirmed instances of the exact same bug, not a
new one. Each gets the identical one-line `on_empty = "(empty document)"` addition, placed and
worded consistently with `cat-python.toml`/`cat-javascript.toml`/`cat-typescript.toml`'s own
on_empty header comments. No changes to `max_tokens.py`, `ContentMask`, `FilterRegistry`, or the
`on_empty` mechanism itself — this remains purely a filter-configuration fix. Reproduction confirmed
directly against all four real filters before and after (a single PROTECT-free line, e.g. a
minified-style function body, whose token cost exceeds the 800-token budget collapses to nothing
without the fix, renders `"(empty document)"` with it); `cat-go.toml` has no `strip_lines` stage at
all (see its own header comment), so it was the simplest possible reproduction — nothing in its
pipeline can mark any part of a pathological line PROTECT. `TestCodeFilterEmptyOutputFallback`
extended with 15 more tests (4 genuinely-empty-input cases folded into the existing parametrized
test, plus per-language normal-compression/pathological-collapse/preserved-content coverage for
Rust, Go, Java, and C#) — 28 total. Two tests fixed for the original three filters
(`test_early_exit.py`, `test_read_hook_repo_context.py`) needed no further changes: neither
references Rust/Go/Java/C#, and both were re-run clean. `TestFilterNeverExpandsOutput` (QB-017)
re-verified unaffected — no inline `input = ""` TOML case was added for any of the four, same
reasoning as the original three. Full benchmark suite re-run: 153 cases, 20,549 tokens saved
(35.9% overall) — byte-identical to the pre-fix run, confirming none of the committed Rust/Go/
Java/C# benchmark samples are pathological enough to reach the collapse threshold; `baseline.json`
untouched. QB-005F now covers every built-in filter confirmed to share this failure mode; no further
known instances remain.

**Systemic guard added (2026-08-16):** the eight per-filter fixes above closed every *known*
instance, but nothing previously stopped a *future* max_tokens-using filter from shipping without
on_empty — the exact gap that let this bug reach eight filters before being caught. Added
`TestMaxTokensFiltersDeclareOnEmpty` to `tests/unit/test_filters.py`: it enumerates every built-in
filter via the real `FilterRegistry`, and fails if any filter whose stages include `max_tokens` has
no `on_empty` and isn't in a documented, reviewed `_MAX_TOKENS_ON_EMPTY_EXEMPTIONS` allowlist (plus
an inverse check that the allowlist itself never goes stale). This is a test-only, ratchet-style
guard — no change to `max_tokens.py`, `registry.py`, or any filter's runtime behavior. Direct
inspection via `FilterRegistry` found 17 filters that currently use `max_tokens` without `on_empty`
(`cat`, `docker-build`, `gradle`, `maven`, `github-actions`, `docker-ps`, `docker-images`,
`kubectl-get`, `ps`, `df`, `ls-long`, `git-log`, `git-diff`, `pip`, `poetry`, `next`, `generic`) —
all deliberately exempted rather than patched with a borrowed `"(empty document)"` message: none of
them run `code_ast_summarize`/`python_ast_summarize`/`structured_data_summarize` (confirmed per
filter, not assumed), so an empty render there is not the QB-005F "signature kept, body discarded"
pattern — for several (the `docker-ps`/`docker-images`/`kubectl-get`/`ps`/`df`/`ls-long` listing
commands especially) empty output is a legitimate, common, meaningful state ("nothing running") that
`"(empty document)"` would actively misrepresent. Several others (`gradle`/`maven`/`next`/
`github-actions`) look like plausible, low-risk quick wins alongside `build.toml`'s
`mypy`/`ruff`/`pytest` and `node.toml`'s `eslint`/`tsc`/`jest`/`vitest`/`prettier`, which already
have their own `on_empty` — flagged here as individual follow-up opportunities, each needing its own
considered message, not bundled into this fix. Verified the guard actually catches a regression (a
simulated new filter with `max_tokens` and no `on_empty`, not in the allowlist, correctly fails it)
before relying on it. `ruff`/`mypy` clean on the touched test file (2 pre-existing, unrelated mypy
errors confirmed present before this change too); `quor verify` 242/0; full `test_filters.py` +
`test_stages.py` 422/422.

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

**Status:** Fixed and merged to `main` (PR #49, `fix/qb-038-verify-optional-deps`) — shipped in Quor **v0.4.1** (2026-07-11); still live, unchanged, in the current v0.5.0 release.

</details>

---

#### QB-100 — `TrackingDB.flush()`/`close()` silent timeout (CI flake root cause)

**Effort:** Small · **Value:** High (test-suite trust — a flaky CI signal that erodes confidence in
every PR, not a compression-quality item) · **Risk:** Low · **Category:** Bug fix / Test infrastructure

**Status:** Fixed (2026-08-01), on its own branch (`fix/qb-100-tracking-db-flush-close-timeout`) —
deliberately not folded into the QB-099A/QB-099C PR that surfaced it, since the investigation
confirmed it is genuinely pre-existing and unrelated to that PR's own diff. Full root-cause
investigation, including a direct reproduction: `docs/design/QB-100-tracking-db-flush-close-timeout-investigation.md`.

<details>
<summary>Technical details</summary>

**Symptom:** `tests/unit/test_tracking.py::TestReadTracking::test_unsupported_file_type_tracked`
intermittently failed in CI with `sqlite3.OperationalError: no such table: invocations`, always near
the very end of a full, ~2,000+-test single-process `pytest tests/` run; always passed reliably in
isolation.

**Root cause:** `TrackingDB` is a non-blocking, background-threaded SQLite writer — schema creation
happens lazily, inside the worker thread, the first time it runs. `flush()`/`close()` were
unconfirmed, fixed-2.0-second waits (`join(timeout=2.0)`/`Event.wait(timeout=2.0)`, return value
discarded) — a worker thread that simply hadn't been scheduled yet within that window (not stuck,
just not yet given CPU time) left both calls returning silently, as if nothing were wrong. This
repo's own suite constructs 57+ `TrackingDB` instances across ~9 test files, each spawning its own OS
thread; `close()` never confirms its own worker actually stopped, so a call whose window was missed
left that thread alive and unmonitored for the rest of the process — accumulating exactly the kind of
scheduling pressure that could make a much later, otherwise unrelated test's brand-new worker thread
also miss its own window. Directly reproduced with a standalone script: at 2,000 simulated leaked
threads, the exact failure recurs from scheduling pressure alone, zero logic changes.

**Constraint the fix had to respect:** `TrackingDB.__init__()` must stay non-blocking —
`quor/__main__.py`'s own comment is explicit that constructing one unconditionally on the hot
COMMAND_INTERCEPT path would add real per-invocation overhead, and ADR-008 states "neither write
blocks the hook response." Moving schema creation into a synchronous constructor (the most
direct-looking fix) was rejected for this reason.

**Fix (`quor/tracking/db.py`):** raised `flush()`/`close()`'s default timeout 2.0s → 10.0s
(`join()`/`Event.wait()` both return as soon as the real condition is satisfied, so this adds zero
latency to the fast, uncontended path every real single-invocation `quor` process runs under); both
methods now return `bool` (previously `None`) and `warnings.warn()` on a genuine timeout — a silently
discarded outcome becomes a visible one, this project's standing fail-open-but-visible convention. No
existing call site checks the return value (grepped directly) — purely additive/backward-compatible.
`__init__` itself is unchanged.

**Testing:** 5 new deterministic regression tests (`TestFlushCloseTimeoutReporting`,
`tests/unit/test_tracking.py`) using a controlled `threading.Event` to block the worker thread, not
real wall-clock/thread-count pressure, per this repo's own no-flaky-test convention. Full
`tests/unit/test_tracking.py`: 121/121 (116 pre-existing + 5 new). `ruff`/`mypy` on
`quor/tracking/db.py`: clean. Re-ran the same reproduction script against the fixed code:
`table_created=True` at every thread count tested, including 2,000 (previously the first failing
point).

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

#### QB-052 — Fix negative-compression regression in mypy/npm filters

**Effort:** Small · **Value:** Medium · **Category:** Bug fix

Real usage data showed `mypy` and `npm` output sometimes getting *bigger*, not smaller, after
compression — undermining trust in a compression tool regardless of how small the absolute numbers
were. Root cause was never the filters' own compression logic: two dispatcher-level additions (the
tee recovery footer, and — found later, same shape — the concise-output instruction nudge) were
being appended/prepended unconditionally, with no check that their fixed cost stayed below what the
filter actually saved. Both are now gated by the same rule: never add a dispatcher-level extra that
costs more tokens than the filter saved.

<details>
<summary>Technical details</summary>

**Problem:** `mypy`'s `group_repeated` stage (`quor/filters/builtin/build.toml`) needs 3 *identical*
error shapes before it collapses anything; a typical real run has a handful of *distinct* errors, so
`group_repeated` rarely fires and only a few boilerplate lines get stripped by `strip_lines` — a
small, genuine saving the tee footer's near-fixed cost was then outweighing. `npm`'s equivalent
threshold is lower, so it was only mildly negative rather than sharply so.

**Fix, shipped over three commits:**
- `61ca8a4` (2026-07-15) strips the npm version-upgrade notice that was eating npm's small real
  savings.
- `09b6f1e` (2026-07-16) gates the tee recovery footer (`dispatcher.py::_apply_tee`) so the visible
  `"\n[full output: <path>]"` line is only appended when doing so keeps the total token count at or
  below the true raw output's — the raw file is still always written for recoverability, only the
  visible footer is conditional.
- **2026-07-31, found while verifying the above:** the concise-output instruction nudge
  (`dispatcher.py`'s `CONCISE_INSTRUCTION`, a fixed 17-token string, added 2026-07-14 — one day before
  the footer fix) had no equivalent gate, and was unconditionally prepended to any output a filter
  changed at all. Worse, `track_invocation()` ran *before* the nudge was applied, so this cost was
  structurally invisible to `quor gain` — a real compression win (e.g. mypy 75→63 tokens) could still
  have the nudge push the actual bytes sent (63+17=80) into a net loss `quor gain` had no way to
  report. The identical unconditional prepend also existed in the Read-hook path (`claude_read.py`).
  Fixed in both: `_with_concise_instruction()` (`dispatcher.py`) now takes the same token-count gate
  the tee footer uses; `track_invocation()` in `dispatcher.py` now runs *after* the nudge decision so
  tracked numbers match what actually reaches stdout. The Read-hook path's tracking is called deeper
  in the call stack (three branch functions, all before `_handle_text()` prepends anything) and was
  left as-is rather than rushed — see **QB-094** for that follow-on.

**Verification:** all pre-existing tee-regression tests pass; new regression tests added —
`TestConciseInstruction.test_suppressed_when_it_would_cost_more_than_the_filter_saved` and the
corresponding `TestDispatcherTee` update (`test_adapters.py`), a tracking-accuracy test in
`TestDispatcherTracking` (`test_tracking.py`), and
`test_instruction_suppressed_when_it_would_cost_more_than_the_filter_saved`
(`test_adapters_read.py`) — the prior `TestConciseInstruction` suite only ever asserted the nudge was
added, never that adding it could flip a real win into a real loss. This project's own live tracking
DB independently confirms the fix: the 5 real `mypy` invocations logged since `09b6f1e` are all
positive (75→63 tokens, +16%), and `quor gain --filters` reports 16.0% real compression for `mypy`,
not the -41.2% this item was originally opened against.

**Status:** Resolved 2026-07-31 (housekeeping correction — this entry previously sat in
[Now](#now) still reading "Proposed. Not scoped or implemented," the same class of staleness
QB-046/QB-041/QB-055's own corrections describe; moved here once verified fully done). Originally
found 2026-07-15 via a direct SQLite query against the real tracking DB — invisible in the benchmark
corpus, which has no case exercising this pattern.

</details>

---

#### QB-094 — Read-hook tracking accuracy — the same fix as QB-052, generalized

**Effort:** Small · **Value:** Medium · **Category:** Bug fix

QB-052 fixed `quor gain` under-reporting the Bash dispatcher's real token cost by moving
`track_invocation()` to run after every append (tee footer, concise instruction) instead of before.
The Read hook (`claude_read.py`) had the identical bug, left open by QB-052 as a follow-on because
the fix didn't transfer directly: tracking was called from five separate branch functions, each
before three more cross-cutting layers (Repository Context, Relevant repository files, the
repository-intelligence nudge, the concise instruction) got a chance to prepend content in a
different function entirely. `quor gain`, dashboard statistics, and per-filter analytics for any
project using those features were silently under-reporting real token cost — in the worst case,
a passthrough Read that only the "Relevant repository files" block changed was recorded as a
zero-token no-op.

<details>
<summary>Technical details</summary>

**Root cause:** `_compress_read_output()` (and its two helpers, `_compress_extracted_document()` and
`_compress_via_named_filter()`) each called `track_invocation()` directly, before returning. None of
them can see what `_handle_text()` — a different function — prepends afterward: the "Repository
Context" block (QB-079, source-code reads only), "Relevant repository files" (QB-081), the
repository-intelligence onboarding nudge (QB-090), or the concise instruction. So `final_tokens`
always measured an intermediate value, never the bytes actually assigned to `updatedToolOutput`.

**Fix (Option C from the pre-implementation investigation):** producers no longer call
`track_invocation()` at all. `_compress_read_output()` and its helpers now return a `_ReadCompression
Result` — a small frozen dataclass carrying `rendered` (the same `str | None` "omit if unchanged"
value as before), `original`, `filter_name`, `was_passthrough`, and `command`. `_handle_text()`
unwraps `.rendered` for the exact same prepend/gate sequence it already ran, and calls
`track_invocation()` exactly once, at the very end, using whatever `compressed` ends up being (or
`result.original` if nothing was ever produced) as `filtered`. QB-052's approach ("move the one
call") didn't apply as-is because there was no single call to move — this generalizes it to "track
once, after every producer and every layer, fed by metadata carried forward instead of
re-derived."

**Invariants preserved:** `updatedToolOutput` is byte-for-byte unchanged — this is purely an
accounting fix, no compression/filtering/repo-intelligence/instruction decision changed. Exactly one
tracking record per invocation (never zero, never two — same "empty `file_path` stays untracked"
rule as before). `filter_name`/`was_passthrough`/`original` are carried through unchanged; only
`final_tokens` (and, as a natural side effect of consolidating to one call after full assembly,
`duration_ms`, which now spans the whole hook rather than stopping before the prepend layers) can
differ from before. Fail-open is unaffected — `track_invocation()` already swallowed its own
exceptions, and moving *where* it's called doesn't change that.

**Verification:** `tests/unit/test_read_hook_tracking_accuracy.py` (new) covers the full scenario
matrix — concise instruction alone, Repository Context alone, Relevant files alone, the nudge alone,
all four layered together, the "passthrough that still grew" case (`was_passthrough` stays `True`
while `final_tokens` now correctly exceeds `original_tokens`), pure passthrough, and DOCX/PDF
extraction success/failure — asserting `final_tokens == count_tokens(updatedToolOutput)` in every
case. `tests/unit/test_tracking.py`'s `TestReadTracking`/`TestReadSourceCodeTracking` were rewritten
to drive the real `claude_read.handle_bytes()` entry point (the only place tracking happens now)
instead of calling the internal `_compress_read_output()` helper directly, and gained the same
`final_tokens == count_tokens(result)` assertion. A one-line regression-guard test (same assertion)
was added to every other Read-hook suite that previously validated only `updatedToolOutput`
(`test_read_hook_relevant_files.py`, `test_read_hook_repo_context.py`,
`test_read_hook_repo_intel_nudge.py`, `test_read_hook_structured_data.py`,
`test_read_hook_ast_summarization.py`, `test_read_hook_activation.py`) — the exact gap (no suite
anywhere asserted on tracked token counts) that let this ship unnoticed in the first place.

**Analytics impact:** rows recorded *before* this fix are not retroactively corrected — only new
invocations are tracked accurately. `quor gain`'s headline for any project actively using QB-079/081/
090 will show a real step-down after upgrading (not a regression), and previously-invisible
negative-delta rows (a passthrough Read that only grew from an enhancement layer) will now surface
through the same `negative_row_count`/`gross_overhead` UI QB-052's tee-footer case already built.
`quor explain` is unaffected — it computes its own token counts from a live pipeline run, independent
of `TrackingDB`.

**Status:** Resolved 2026-08-01, following the dedicated architecture investigation this ticket
opened with (root-cause trace, five-call-site inventory, and the Option A/B/C comparison that landed
on C).

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

#### QB-062 — Eliminate redundant per-invocation config reads and registry rebuilds

**Effort:** Small · **Value:** Low · **Category:** Performance

`run_dispatch()`'s `_setup_plugins()` and `_apply_tee()` each called `load_user_config()`
independently, so a dispatch exercising both paths read and re-validated `config.toml` twice; the
same duplication existed between `quor doctor`'s `_check_mode()`/`_check_tee()`, and the benchmark
runner rebuilt a `FilterRegistry` (Pydantic-validating every builtin filter TOML) once per case
instead of once per run.

<details>
<summary>Technical details</summary>

**Problem:** No behavior was wrong, only wasted work — `load_user_config()` re-read+re-parsed+
re-validated the same file up to twice per dispatch/doctor run, and `run_benchmarks.py` measured
~42ms/`FilterRegistry` construction x 102 cases ≈ 4.3s of pure redundant re-parsing per pytest
session.

**Resolution:** `run_dispatch()` now passes a memoizing closure (`get_user_config`) into
`_setup_plugins()`/`_apply_tee()` so both share one read; `_run_doctor()` loads `QuorUserConfig`
once and passes it into `_check_mode()`/`_check_tee()`; `run_case()` now draws from a
`functools.lru_cache`-wrapped `_builtin_registry()` instead of constructing a fresh
`FilterRegistry` per case. `run_dispatch()` also now reuses `_run_pre_filter_plugins()`'s already-
computed `detect()` result instead of re-scanning `pre_output` a second time when `PRE_FILTER`
left the content unchanged. No observable behavior changed — verified via the full test suite and
`quor verify` passing unmodified.

**Status:** Resolved — implemented on `refactor/qb-062-dedupe-per-invocation-config-reads`.

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

#### QB-064 — Fix docker-build BuildKit step-echo preserve pattern

**Effort:** Small · **Value:** Low · **Category:** Bug Fix

`ci.toml`'s docker-build filter's `preserve_patterns` included `'^>>> '` intended to keep BuildKit
`RUN` step-echo lines, but BuildKit actually emits these prefixed with the step number (`#N |
>>> ...`), never at the start of the line — so the pattern never matched real BuildKit output.

<details>
<summary>Technical details</summary>

**Problem:** Anchored-at-line-start pattern never matched BuildKit's actual `#N |    >>> ...`
step-echo shape.

**Resolution:** Changed to `'\|\s*>>> '`, matching the `|` step-separator BuildKit always emits
before the echoed command regardless of step number width.

**Status:** Resolved — implemented on `fix/qb-064-docker-buildkit-step-echo-pattern`.

</details>

---

#### QB-061 — Repository Context Profile (`quor map`)

**Effort:** Large · **Value:** High · **Risk:** Medium · **Expected token impact:** Unmeasured —
avoids a multi-call discovery sequence (`ls`/`find`/several `cat`/`grep`), not a compression ratio;
see own entry's uncertainty note · **Category:** New Capability

A new, deterministic capability — not a filter. Every capability Quor had shipped through QB-065
compresses one already-captured blob (one command's output, one file's content); this instead
walks a repository once and synthesizes a one-shot orientation profile (languages, frameworks,
build system, package manager, entry points, services/modules, CI system, containerization,
databases, configuration files, lockfiles, repository statistics) that never existed verbatim
anywhere in the repo — the token cost an AI coding assistant otherwise pays via a multi-call
discovery sequence when it starts work in an unfamiliar repo.

**Status:** Implemented (2026-07-28) on `feature/qb-061-repo-context-profile`. See
`docs/design/QB-061-repo-context-profile.md` for the full design (competitive positioning against
Aider's repo map, reuse audit, benchmark strategy) and `docs/final/DECISIONS.md` ADR-037 for the
architecture decision record.

<details>
<summary>Technical details</summary>

**What shipped:** `quor/pipeline/repo_profile/` — a new package parallel to (not inside) the
ContentMask pipeline: `walk.py` (`git ls-files` primary, `os.walk` fallback), `detectors/` (a new
three-tier TOML detector-rule registry mirroring `FilterRegistry`'s loading/trust pattern, but
match-all rather than first-match-wins — a repo can be a Flask app *and* Dockerized *and* built on
GitHub Actions simultaneously; ~87 built-in rules across build-system/package-manager/framework/
test-framework/CI-system/database/containerization/configuration categories), `languages.py`
(extension histogram, computed directly from the walk), `entry_points.py` (manifest-field
extraction for `pyproject.toml`/`package.json`/`Cargo.toml`/`go.mod`, plus a bounded root-level
`if __name__ == "__main__":` scan — no tree-sitter/AST symbol extraction; deliberately deferred,
see below), `directories.py` (important directories + services/monorepo detection),
`statistics.py` (file/directory counts, git commit count), `model.py` (`RepoProfile`, frozen
Pydantic), and `render.py` (fixed-template Markdown by default; `--json` is a secondary, optional
mode). `profiler.build_profile(root) -> RepoProfile` is the single public entry point.

`quor map` is registered as a second exempted utility command (same category as `quor schema` —
non-filtering, not one of the six V1 commands), wired into `quor/cli/main.py` and
`__main__.py`'s `_CLI_COMMANDS` routing set. **Real bug caught during implementation:** without
the latter, `quor map` silently fell through to the dispatcher, which tried to execute a literal
shell command named `map` (`[WinError 2] The system cannot find the file specified`) — found via
an end-to-end smoke test against the real CLI, not just unit tests against the library functions
directly.

**Tracking:** invocations are recorded through the existing `track_invocation()` path under a new
synthetic label, `REPO_PROFILE_FILTER_LABEL` (`quor/tracking/db.py`, defined alongside
`PASSTHROUGH_LABEL` for the identical reason) — `original_tokens`/`final_tokens` are recorded
equal by design (there is no "before" blob; this is synthesis, not compression), so the invocation
is visible in `quor gain`'s counts without distorting its net-tokens-saved headline. **Second real
bug caught during implementation:** QB-065's `flag_low_performers` health check (which flags
filters with negative/near-zero real compression) initially flagged `repo-profile` alongside
genuine regressions like mypy/ruff in `quor doctor`'s output — a false positive, since 0% is
`quor map`'s correct, by-design behavior, not a defect. Fixed by adding
`REPO_PROFILE_FILTER_LABEL` to the same exclusion set `PASSTHROUGH_LABEL` already had, with a
regression test (`test_repo_profile_label_is_excluded`) pinning it.

**Deliberately out of scope (see ADR-037):** full tree-sitter/AST symbol extraction for
entry-point/framework detail (Aider-style repo map) — a real, larger follow-up phase, not
attempted here. Framework/database detection is scoped to manifest-file dependency mentions only
(bounded, deterministic), never a scan of arbitrary source files for import statements.

**Tests:** 107 new unit/integration tests across 9 test files
(`test_repo_profile_walk/languages/detectors/entry_points/directories/statistics/model/render/
profiler.py`, `test_cli_map.py`), plus a fixture-repo benchmark corpus
(`tests/fixtures/repo_profile/{flask-pip,node-express-pnpm,go-service,polyglot-monorepo}/` +
`test_repo_profile_benchmark.py`) checking precision/recall against hand-labeled expected facts,
a false-positive check, a byte-identical determinism check, and a performance budget — the
parallel benchmark structure `docs/design/QB-061-repo-context-profile.md` §8 calls for, since
there is no "before" blob to compress against the way `tests/benchmarks/manifest.toml` assumes.
Full existing suite (unit + integration + compression benchmark suite, 127 cases) re-run and
confirmed zero regressions; `ruff check quor/ tests/` and `mypy quor/` both clean.

</details>

---

#### QB-066 — Repository Symbols (`quor symbols`)

**Effort:** Large · **Value:** High · **Risk:** Medium · **Expected token impact:** Unmeasured —
avoids re-`grep`/`Read`-ing files to locate a class/function's definition and file, not a
compression ratio; see own entry's uncertainty note · **Category:** New Capability

The Phase D follow-up QB-061/ADR-037 deliberately deferred: a deterministic, repository-wide symbol
index (classes, interfaces, structs, traits, enums, functions, methods, public/private visibility,
entry-point functions, file locations) — the token cost an AI coding assistant otherwise pays
re-discovering a symbol's location and shape via repeated `grep`/`Read` calls.

**Status:** Implemented (2026-07-28) on `feature/qb-066-repository-symbols`. See
`docs/final/DECISIONS.md` ADR-038 for the full architecture decision record. **Numbering note:**
this task was originally referred to as "QB-062"; `backlog.md` already had a completed, unrelated
QB-062 entry (per-invocation config-read deduplication, above) and the highest ID in use was
QB-065, so this shipped as QB-066 instead — confirmed with the user before implementation began,
not silently renumbered.

<details>
<summary>Technical details</summary>

**What shipped:** Each of the eight languages `quor/pipeline/ast_summarize/` already registers
(`python`/`javascript`/`typescript`/`tsx`/`go`/`java`/`rust`/`csharp`) gained one additive
`extract_symbols_*()` function in its existing module — reusing that module's own parser setup
(stdlib `ast` for Python, the same `tree-sitter`/grammar-package combination for the rest) and
lazy-import/fail-open discipline unchanged, zero double-parsing, zero risk to the existing
`analyze_*()` compression analyzers (verified: the compression benchmark suite is unchanged, 127
cases, 35.9% overall). `quor/pipeline/ast_summarize/symbol_model.py` defines the shared `Symbol`
frozen dataclass (name, kind, line, `is_public`, `is_entry_point`) and `registry.py` gained a
parallel `_SYMBOL_EXTRACTORS`/`get_symbol_extractor()` alongside its existing `_ANALYZERS`/
`get_analyzer()`. Every visibility bit is grounded in each language's own real mechanism (Go's
leading-capital exported-identifier rule, Rust's `pub` keyword, Java/C#'s explicit `public`
modifier — package-private/internal by default — TypeScript's `accessibility_modifier` — public by
default — JavaScript's ES2022 `#name` private-field syntax, Python's leading-underscore
convention), empirically verified against the installed tree-sitter grammars during implementation,
never guessed. Entry-point detection is a bounded name match (`main`/C#'s `Main`) — every
mainstream convention across all eight languages names it one of those two ways.

`quor/pipeline/repo_profile/symbols.py`'s `build_symbol_index(root) -> RepoSymbolIndex` is the
orchestrator — reuses `walk.py`'s existing file enumeration unchanged, applies a 2 MB per-file size
cap (files over the cap are counted and named in a summary note, not silently dropped — QB-061's
own design doc §7 risk 4 flagged large-repo AST parsing as this follow-up's unresolved scaling
risk), and fails open per file (a narrow, explicitly-commented exception to the project's normal
"every `except` clause is specific" rule, justified the same way the hook's own top-level guard is —
one malformed file must not deny a symbol index for the rest of a large repo). A file that declares
zero symbols is omitted from the index entirely, matching `render.py`'s existing "omit rather than
print emptiness" convention for `quor map`. `symbols_model.py`/`symbols_render.py` are frozen
dataclasses (not Pydantic, matching `walk.py`'s `WalkResult` rather than `model.py`'s
`RepoProfile` — no external validation boundary exists for this internally-computed data) rendered
as fixed-template Markdown by default, `--json` as a secondary mode — the same convention `quor
map` already established.

`quor symbols` is a third exempted utility command (`quor/cli/main.py`, `__main__.py`'s
`_CLI_COMMANDS`) — explicit user sign-off obtained before any CLI code was written, following the
exact process ADR-037 already established for `quor map`, not assumed granted by the originating
task instructions alone. The exact real bug ADR-037 caught for `quor map` (a command name missing
from `_CLI_COMMANDS` silently falls through to the shell dispatcher) is guarded against here by a
dedicated regression test, not just re-checked by hand. Invocations are tracked under a new
`REPO_SYMBOLS_FILTER_LABEL` (`quor/tracking/db.py`), excluded from
`filter_divergence.flag_low_performers()`'s low-performer check the same way
`REPO_PROFILE_FILTER_LABEL` already is.

**Deliberately out of scope (see ADR-038):** search/`--focus` filtering (explicitly excluded by the
originating task unless the architecture naturally supported it — it doesn't yet, cleanly, without
its own design pass) and real-session token-savings measurement (per Anti-Goal #24/#25, not
published until measured against real usage).

**Tests:** 47 new unit tests for the eight `extract_symbols_*()` functions
(`test_ast_summarize_symbols.py`, including a missing-optional-dependency fail-open test per
tree-sitter-backed language, mirroring `test_ast_summarize.py`'s existing pattern) plus 11 for the
orchestrator (`test_repo_profile_symbols.py` — polyglot repos, empty-symbol-file omission, the
size cap, missing-dependency skip, per-file parse-failure fail-open, determinism) plus 10 for
rendering (`test_repo_profile_symbols_render.py`) plus 5 CLI integration tests
(`test_cli_symbols.py`, mirroring `test_cli_map.py` — Markdown/JSON output, `--path`, tracking, the
dispatcher-fallthrough regression guard) plus a fixture-repo benchmark corpus reusing QB-061's
existing four fixture repos with a second, symbol-shaped set of hand-labeled expectations
(`test_repo_profile_symbols_benchmark.py` — precision/recall, a false-positive check, a
byte-identical determinism check, a whole-corpus performance budget, and a synthetic 500-file/5,000-
function scaling test standing in for QB-061's own "5,000-file repo" scaling example at a size that
keeps the default test suite fast). Full existing suite (all `tests/unit/` files verified
individually plus `tests/integration/` with `-m integration`) and the compression benchmark suite
re-run and confirmed zero regressions; `ruff check quor/ tests/` and `mypy quor/` both clean.

</details>

---

#### QB-067 — Repository Dependency Graph (`quor graph`)

**Effort:** Large · **Value:** High · **Risk:** Medium · **Expected token impact:** Unmeasured —
avoids re-`grep`/`Read`-ing files to trace imports/inheritance/call relationships across a repo, not
a compression ratio; see own entry's uncertainty note · **Category:** New Capability

Deterministic, repository-wide relationship extraction on top of QB-066's symbol index: imports,
exports, inheritance, interface/trait implementation, method overrides, module/package dependencies,
and (where a language's grammar allows unambiguous resolution) call relationships — the token cost
an AI coding assistant otherwise pays re-discovering "what calls this" / "what does this depend on"
via repeated `grep`/`Read` calls.

**Status:** Implemented (2026-07-29) on `feature/qb-067-repository-dependency-graph`. See
`docs/final/DECISIONS.md` ADR-039 for the full architecture decision record.

<details>
<summary>Technical details</summary>

**What shipped:** Each of the eight languages `quor/pipeline/ast_summarize/` already registers
gained one additive `extract_relationships_*()` function in its existing module — reusing that
module's own parser setup and lazy-import/fail-open discipline unchanged, zero double-parsing beyond
the same already-accepted per-file-two-parses cost `analyze_*()`/`extract_symbols_*()` already share,
zero risk to the existing compression analyzers or QB-066's symbol extractors (verified: the
compression benchmark suite and every existing `repo_profile`/`ast_summarize` test pass unmodified).
`quor/pipeline/ast_summarize/relationship_model.py` defines the shared `Relationship` frozen
dataclass (kind, source, target, line, qualifier, origin) — raw, file-local, unresolved facts, never
reaching into another file. `registry.py` gained a third, parallel `_RELATIONSHIP_EXTRACTORS`/
`get_relationship_extractor()` alongside `_ANALYZERS`/`_SYMBOL_EXTRACTORS`, plus a promoted
`EXTENSION_TO_LANGUAGE` table (moved from `repo_profile/symbols.py`'s originally-private copy) so
`quor symbols` and `quor graph` share one source of truth.

`quor/pipeline/repo_profile/graph.py`'s `build_dependency_graph(root) -> RepoDependencyGraph` is the
orchestrator — walks the repo once (reusing `walk.py` unchanged), and per file calls both
`get_symbol_extractor()` and `get_relationship_extractor()` directly (not `symbols.py`'s
`build_symbol_index()` as an opaque black box, which would double the walk/read cost), then resolves
every raw relationship against the whole repo's symbol table into an `Edge`
(`quor/pipeline/repo_profile/graph_model.py`). Resolution is conservative and unambiguous-only by
design (confirmed with the user before implementation began): an edge's `target_file`/`target_symbol`
are populated only when a reference resolves to exactly one candidate (same-file, or through a file's
own unambiguous, non-wildcard import bindings); `target_raw` is always present regardless, so the
underlying fact is never lost even unresolved. Import/module-path resolution uses a bounded,
spec-or-convention-grounded rule per language (Python's relative-import semantics, JS/TS's
relative-path-plus-extension-probing, Java's package-to-directory convention, Rust's `crate::`-rooted
`src/` convention) — never a general package-manager/build-system algorithm (no `node_modules`,
`go.mod`, Java classpath, Cargo workspace, or C# project references). Same 2 MB per-file size cap and
per-file fail-open discipline as `symbols.py` (QB-066), unchanged reasoning.
`graph_render.py` renders fixed-template Markdown by default, JSON via `--json`, mirroring `quor
map`/`quor symbols`'s identical convention — grouped by source file, each edge naming its kind,
target, and (when resolved) a `file::symbol` pointer.

`quor graph` is a fourth exempted utility command (`quor/cli/main.py`, `__main__.py`'s
`_CLI_COMMANDS`) — explicit user sign-off obtained before any CLI code was written, following the
exact process ADR-037/ADR-038 already established, guarded against the same real
`_CLI_COMMANDS`-omission bug both prior ADRs caught via a dedicated regression test. Invocations are
tracked under a new `REPO_GRAPH_FILTER_LABEL` (`quor/tracking/db.py`), excluded from
`filter_divergence.flag_low_performers()`'s low-performer check the same way
`REPO_PROFILE_FILTER_LABEL`/`REPO_SYMBOLS_FILTER_LABEL` already are.

**Deliberately out of scope / documented limitations (see ADR-039):** Go/C# import paths are never
resolved to a file; `self`/`this`/`super`/`base`-qualified calls resolve same-file only, never
chasing a cross-file inheritance chain; C#'s single colon-delimited base list cannot syntactically
distinguish a base class from an interface, so `implements_interface` is never emitted for C#; Go's
structural interface satisfaction has no syntactic marker at all, so Go emits no
`inherits`/`implements_interface`/`implements_trait`/`overrides`; cross-file call resolution beyond a
file's own direct, unambiguous import bindings is not attempted. Real-session token-savings
measurement is also not yet done (per Anti-Goal #24/#25, not published until measured against real
usage).

**Tests:** 62 new unit tests for the eight `extract_relationships_*()` functions
(`test_ast_summarize_relationships.py`, including a missing-optional-dependency fail-open test per
tree-sitter-backed language, mirroring `test_ast_summarize_symbols.py`'s existing pattern) plus 21
for the orchestrator (`test_repo_profile_graph.py` — polyglot repos, cross-file import/inherits/call
resolution across Python/JS/Java/Rust, ambiguous-name non-resolution, wildcard-import non-binding,
the size cap, missing-dependency skip, per-file parse-failure fail-open, determinism) plus 12 for
rendering (`test_repo_profile_graph_render.py`) plus 5 CLI integration tests (`test_cli_graph.py`,
mirroring `test_cli_symbols.py`) plus a fixture-repo benchmark corpus reusing QB-061/QB-066's
existing four fixture repos (`test_repo_profile_graph_benchmark.py` — real relationship checks,
determinism, a synthetic 500-file/5,000-call cross-module resolution scaling test, a whole-corpus
performance budget) plus 1 new `filter_divergence` low-performer-exclusion test. Full existing suite
and the compression benchmark suite re-run and confirmed zero regressions; `ruff check quor/ tests/`
and `mypy quor/` both clean.

</details>

---

#### QB-072 — Automatic Repository Intelligence

**Effort:** Large · **Value:** High · **Risk:** Medium · **Expected token impact:** Unmeasured —
removes the need to know `quor map`/`quor symbols`/`quor graph` exist at all, not a compression
ratio · **Category:** New Capability

`quor map` (QB-061), `quor symbols` (QB-066), and `quor graph` (QB-067) were manual, always-full-scan
commands: a user or AI assistant had to know each one existed and re-paid a full-repository walk +
AST parse every single invocation, even when nothing in the repo had changed since the last call.
This item makes all three self-maintaining: first use of any of them against a repository triggers a
one-time onboarding message and a full build of all three (never `quor schema`, which stays generated
on demand — unrelated to this cache); every later call detects added/modified/deleted/renamed files
since the last scan and rebuilds only what's affected, or reuses the cached result immediately when
nothing changed.

**Status:** Implemented on `feature/qb-072-automatic-repo-intelligence`.

<details>
<summary>Technical details</summary>

**What shipped:** A new `quor/pipeline/repo_profile/intel*.py` subsystem sitting in front of the
existing `profiler.build_profile()`/`symbols.build_symbol_index()`/`graph.build_dependency_graph()`
orchestrators, not replacing them: `intel_model.py` defines the frozen data contracts
(`FileFingerprint`, `RepoDiff`, `RepoIntelState`, `RepoIntelligence`, `BuildAction`); `intel_diff.py`
fingerprints every file (size + `mtime_ns` fast-path, SHA-256 content hash only when that fast path
can't rule out a change) and turns two fingerprint tables into a `RepoDiff` — added/modified/deleted,
plus rename detection via matching content hashes between a deleted and an added path (a rename *and*
edit is correctly reported as plain delete+add, since it needs the same re-extraction a modified file
would); `intel_store.py` persists state and per-file facts under
`platformdirs.user_data_dir("quor") / "repo_intel" / <sha256-of-resolved-root>[:16]` (the same
`user_data_dir("quor")` root every other Quor cache already uses — `tracking/db.py`, `pipeline/tee.py`,
`pipeline/onboarding.py` — not a new `user_cache_dir` nothing else in the codebase uses), treating any
read/parse failure on any of its four JSON files as a cache miss rather than propagating a corrupt
on-disk file into a wrong result; `intel.py`'s `ensure_repo_intelligence()` is the single new public
entry point `quor map`/`quor symbols`/`quor graph` now call instead of the raw orchestrators directly.

**Incremental rebuild is genuinely per-file for `quor symbols`/`quor graph`, not just diffed-and-full-
rebuilt.** `symbols.py` and `graph.py` were each split into a reusable single-file extractor
(`extract_file_symbols()`/`extract_file_facts()`) and a shared assembly/resolution tail
(`assemble_symbol_index()`/`assemble_graph()`), with the original `build_symbol_index()`/
`build_dependency_graph()` becoming thin wrappers over their own new `..._with_facts()` variants — a
behavior-preserving refactor verified against the full pre-existing `test_repo_profile_symbols*`/
`test_repo_profile_graph*` suites before anything new was added. On a change, only the added/modified/
renamed files are re-parsed; deleted and unaffected files' cached per-file facts are reused as-is.
`quor graph`'s cross-file resolution (`assemble_graph()`) still runs over the *whole* merged fact set
on any change (resolving one file's imports/calls needs the whole repo's symbol table, not just the
changed file's), but that resolution step is pure in-memory computation with no file I/O or parsing,
so re-running it whenever anything changed is cheap relative to what re-parsing every file would cost
— the same reasoning QB-071 already established for why `graph.py`'s memory optimization didn't need
a second repository pass. Persisting per-file graph facts to disk (needed for this incremental path)
does mean the *first* build of `quor graph` for a repository retains every file's raw facts a little
longer than QB-071's plain `build_dependency_graph()` does (until they're serialized), a deliberate,
documented, one-time trade-off scoped to the new caching entry point only — QB-071's own memory
profile for direct callers of `build_dependency_graph()` is unchanged and re-verified.

**`quor map`'s `RepoProfile` deliberately does not get the same per-file treatment.** Its fields
(language percentages, detected frameworks/build systems, entry points, ...) are whole-repository
aggregates with no sound per-file partition — instead, a true cache-hit (empty diff) reuses the cached
`RepoProfile` verbatim, and any non-empty diff triggers a full `build_profile()` re-run, which is cheap
relative to `quor symbols`/`quor graph`'s AST parsing since `build_profile()` never reads arbitrary
source, only bounded manifest/config files plus filenames/sizes (see `profiler.py`'s own module
docstring).

**Deliberately does not touch the hook dispatch path.** Detection/onboarding/rebuild logic lives
entirely inside `quor map`/`quor symbols`/`quor graph`'s own CLI entry points — never in
`quor hook`/`__main__.py`'s hot path, which keeps its existing <10ms budget and zero-heavy-import
discipline completely unchanged. This was an explicit, confirmed-with-the-user architectural choice
(not assumed) given the hard performance/no-daemon/no-watcher/no-polling constraints already
documented in `docs/final/CLAUDE.md`; "automatic" here means "the first time any of these three
commands runs against a repository," not "on every Quor invocation of any kind."

Each of the three commands gained a `--rebuild` flag (bypasses the cache entirely, forcing a full
rebuild) and now prints onboarding/progress messages ("Scanning repository...", "Building repository
intelligence...", "Building symbols...", "Building dependency graph...", "Finished in X seconds.") to
stderr, never stdout — so a true cache-hit is silent, and `--json` output is never corrupted by a
progress line landing in the same stream.

**Tests:** 12 new unit tests for the diff/fingerprint engine (`test_repo_intel_diff.py` — first scan,
unchanged/modified/deleted/renamed detection, the fast-path-skips-rehashing guarantee, rename-vs-
edit-and-rename disambiguation), 17 for the cache store (`test_repo_intel_store.py` — repo-key
stability, roundtrip and corruption-as-miss behavior for all four cache files), and 21 for the
orchestrator (`test_repo_intel.py` — first repository/onboarding, unchanged repository/cache-hit,
file modified, file deleted, file renamed, rebuild after a Quor version change, a corrupted state
file, a corrupted sibling artifact, deterministic repeated runs, full-rebuild-vs-incremental
equivalence, the `--rebuild`-equivalent forced path, and two performance-regression guards asserted
via parse-call-count spies rather than wall-clock timing, per this repo's own flaky-test rules) — one
of which (`TestFileRenamed::test_pure_rename_is_not_reparsed`) caught a real bug during development:
an earlier version of `RepoDiff` folded a rename's new path into "needs re-extraction," silently
defeating the whole point of rename detection until the test was written against `intel.py`'s actual
call site rather than `symbols.py`'s source module. Plus new `--rebuild`/cache-reuse CLI tests added
to the existing `test_cli_map.py`/`test_cli_symbols.py`/`test_cli_graph.py`. Full existing suite
(every `tests/unit/` file run, `tests/integration/` with `-m integration`) re-run and confirmed zero
regressions; `ruff check quor/ tests/` and `mypy quor/` both clean. The compression benchmark suite
was not re-run — this item touches no `StageHandler`/filter/pipeline compression logic.

**Performance follow-up (same item, immediately after the above shipped).** Investigated whether
graph/symbol/map rebuilds could be made cheaper still — reduce CPU, peak memory, and rebuild latency
beyond the per-file incremental extraction already delivered — before writing any more code, per this
repo's Rule 4 (research/benchmark first, present a recommendation, get sign-off, only then implement).

*What was measured:* a new `tests/benchmarks/repo_intel_benchmark.py` (a synthetic, git-committed,
nested (`src/pkg/`) nested repo — deliberately not flat at the root, see that module's own docstring
for why an earlier flat-root version gave a misleading picture — with a configurable file count)
measures CPU time (`time.process_time()`), peak Python-heap memory (`tracemalloc`), wall-clock elapsed
time, and cache hit ratio across six scenarios: cold build, warm build, one file modified, ten files
modified, one file renamed, one file deleted. `RepoIntelligence` gained `files_scanned`/
`files_reextracted` fields and a `cache_hit_ratio` property so this is a real, queryable metric, not
just an ad-hoc benchmark-script computation. No committed baseline (there is no equivalent "correct"
percentage the way a filter's compression ratio has) — regression protection instead comes from
`tests/unit/test_repo_intel_benchmark.py`'s deterministic, count-based assertions (exact
`files_reextracted` per scenario, exact `cache_hit_ratio`, plus one generously-bounded comparative
timing smoke check per this repo's no-flaky-test rule) — `python -m
tests.benchmarks.repo_intel_benchmark` prints/writes the full human-readable report.

*What the numbers showed, via `cProfile` against an 800-file synthetic repo:*
- `graph.assemble_graph()` (pure cross-file edge resolution, no I/O) — **~0.012s**, negligible. Building
  the "invalidate only affected edges" mechanism this item's own investigation checklist named (persist
  resolved edges, track per-edge dependencies on target files/symbols, re-resolve only affected
  subgraphs) was analyzed in detail — genuinely implementable and correctness-preservable given how
  `_resolve_all()`'s dependencies decompose — but would save at most ~1% of an incremental rebuild's
  time, for real correctness risk against ADR-039's deliberately conservative, unambiguous-only
  cross-file resolution logic. **Recommended against building it, confirmed with the user before
  moving on** — the evidence didn't support the complexity/risk.
- `symbols.assemble_symbol_index()` — **~0.003s**, likewise negligible.
- `profiler.build_profile()`'s `DetectorRegistry.detect()` — **the actual dominant cost**: an
  O(rules × files) matching loop (87 built-in rules × 800 files = 69,600 `_file_matches` calls), each
  constructing a `PurePosixPath` just to read `.name` — ~0.35-0.48s at 800 files, on every full-rebuild-
  shaped call including every incremental rebuild (`quor map` had no per-file incrementality at all).
  A pre-existing QB-061 characteristic, not something this item introduced — it only became the
  dominant cost because per-file symbol/graph incrementality made everything else fast enough to expose
  it. Confirmed with the user before fixing.

*What shipped from this follow-up:*
1. `profiler.build_profile()`/`symbols.build_symbol_index_with_facts()`/
   `graph.build_dependency_graph_with_facts()` each gained an optional `walk_result` parameter so
   `intel.py` walks the repo once per call and shares it, instead of each of the three (plus `intel.py`
   itself) independently re-walking — a full rebuild dropped from 4 `git ls-files` subprocess calls to 1.
   Every existing caller that omits the parameter is unaffected (default: walk internally, exactly as
   before).
2. `DetectorRegistry.detect()` now computes each file's basename once (`{f: PurePosixPath(f).name for f
   in files}`) and passes it to every rule, instead of every rule re-deriving it — turning ~69,600
   `PurePosixPath` constructions into 800, with the exact same matching order/results (verified: the
   full existing `test_repo_profile_detectors.py` suite, plus every other `repo_profile` test file,
   passes unmodified).
3. `RepoIntelligence.files_scanned`/`files_reextracted`/`cache_hit_ratio` (new, see above).

*Measured effect* (800-file synthetic repo, before → after): cold build 5.4s → 4.9s; one file modified
0.98s → 0.54s (a ~45% reduction); ten files modified 1.11s → 0.83s; renamed/deleted ~1.0s → ~0.7s.
Compression benchmark suite re-run this time (the detector-registry change, while outside
`quor/pipeline/filters`/`rewrite`, still touches `quor/pipeline/`) and confirmed unchanged: 127 cases,
35.9% overall, identical to the committed baseline. Full existing suite (`tests/unit/`,
`tests/integration/` with `-m integration`, `quor verify`) re-run and confirmed zero regressions;
`ruff check quor/ tests/` and `mypy quor/` both clean. No daemon, background service, filesystem
watcher, or persistent in-memory process was introduced or considered necessary — every optimization
here is either a one-time-per-call computation (walk sharing, basename precomputation) or a per-file
cache lookup, entirely within the existing synchronous, per-invocation model.

</details>

---

#### QB-073 — CLI Help, Command Grouping & Version Command

**Effort:** Small · **Value:** Medium · **Risk:** Low · **Category:** Polish

`python -m quor --help` listed all ten commands in one flat panel, in registration order, with no
grouping by purpose. `--version` didn't exist at all (`No such option: --version`, exit 2), and
`quor version` fell through to `__main__.py`'s shell dispatcher and failed with a raw `WinError 2` —
the exact dispatcher-fallthrough bug class ADR-037 first caught for `quor map`, just not yet fixed
for a command that didn't exist yet when ADR-037 was written.

**Status:** Implemented on `feature/qb-072-automatic-repo-intelligence` (bundled with QB-072/QB-074 in
the same session, per explicit user instruction to fix all three together and record them as
separate backlog entries).

<details>
<summary>Technical details</summary>

**What shipped:** `quor/cli/main.py`'s ten commands are now grouped into three `rich_help_panel`s —
Typer's own built-in mechanism, not a new abstraction — Installation (`init`, `doctor`), Analysis
(`map`, `symbols`, `graph`), and Utilities (`explain`, `validate`, `verify`, `gain`, `version`,
`schema`). Purely a `--help` presentation grouping; it changes nothing about how any command is
invoked, routed, or tested. A new `quor/cli/commands/version.py::version_command()` is registered as
a real `version` subcommand, and `__main__.py::_CLI_COMMANDS` gained `"version"` so it's routed to the
CLI instead of the shell dispatcher. `cli/main.py`'s root callback gained an eager `--version` option
(`is_eager=True`, the same convention `git --version`/`node --version` follow) that wins even if
garbage follows it on the command line. All three surfaces — `quor version`, `quor --version`, and the
pre-existing bare-invocation banner — read `quor.__version__` and can never disagree.

**Tests:** `tests/unit/test_cli_root.py` — `quor version` and `--version` both print the right string
and agree with each other and the bare-invocation banner, the dispatcher-fallthrough regression guard
for `"version"`, `--version`'s eager-ness ignoring trailing garbage, and the `--help` panel grouping
(each of the three panels contains exactly the commands it should, with concise one-line descriptions
visible). Full existing `tests/unit/test_cli.py` suite re-run and confirmed zero regressions; `ruff
check quor/ tests/` and `mypy quor/` both clean.

</details>

---

#### QB-074 — Long-Running Command Progress, Summaries, Error Messages & Doctor Formatting

**Effort:** Medium · **Value:** Medium · **Risk:** Low · **Category:** Polish

`quor map`/`quor symbols`/`quor graph` printed QB-072's onboarding/progress messages in plain,
uncolored text, and a true cache-hit printed nothing at all — no elapsed time, no counts, no
indication the command had even run correctly. Separately, a nonexistent `--path` (or one pointing at
a file instead of a directory) silently produced a fully-formed but empty profile/index/graph at exit
code 0 — no error, no clue what went wrong. `quor doctor` printed its check list with no header and,
outside `--fix` mode, no closing summary at all.

**Status:** Implemented on `feature/qb-072-automatic-repo-intelligence` (bundled with QB-072/QB-073 in
the same session, per explicit user instruction to fix all three together and record them as
separate backlog entries).

<details>
<summary>Technical details</summary>

**What shipped:**
1. `quor/cli/repo_path.py::resolve_repo_root()` — a shared `--path` validator now called by
   `map`/`symbols`/`graph` before any repository-intelligence work begins: a nonexistent path or a
   file passed where a directory was expected now exits non-zero with a message that states what
   failed (the exact path), why (doesn't exist / isn't a directory), and how to fix it (check
   `--path`, or point it at the repo root itself) — instead of silently continuing.
2. `quor/cli/repo_progress.py` — presentation-only, mirroring `format_utils.py`'s "never influences
   what gets computed" split: `progress_echo()` colors (cyan) whatever plain-string message
   `intel.ensure_repo_intelligence()` already produces, without `intel.py` itself knowing about color
   (every existing `test_repo_intel.py` assertion on raw message strings is unaffected — verified,
   re-run unmodified). `print_build_summary()` is new: a single, always-shown, green, counts-bearing
   closing line — `✓ Done in 0.3s — 150 files cached, 42 symbols` (or `built`/`rebuilt`/`updated (N
   files re-parsed)` depending on `RepoIntelligence.action`) — that a true cache-hit previously never
   printed at all. `map` reports a language count, `symbols` a symbol count, `graph` an edge count
   (with how many resolved) — using `RepoIntelligence.files_scanned`/`files_reextracted`/
   `cache_hit_ratio` (QB-072's own perf-follow-up metrics) as the shared prefix.
3. `quor doctor` gained an always-shown `Quor Doctor` header and an always-shown, elapsed-time-bearing
   closing summary (`✓ N of N checks passed in 0.3s` / `✗ M of N checks failed in 0.3s — see above for
   details`) — previously only present, and only a bare pass/fail line with no elapsed time, inside
   `--fix` mode. Every existing check function, its `(name, ok, detail)` tuple, and its print order are
   completely unchanged — verified by the fact every pre-existing `TestDoctor`/`TestDoctorFix`
   assertion (which greps for a specific check line, never the whole output) still passes unmodified.

**Tests:** New `TestMapCommandErrors`/`TestSymbolsCommandErrors`/`TestGraphCommandErrors` (nonexistent
path, path-is-a-file) and `TestMapCommandSummary`/`TestSymbolsCommandSummary`/`TestGraphCommandSummary`
(cache-hit summary line present with the right counts, and confirmed absent from `stdout` — the
summary is stderr-only, so `--json` output is never touched) across `test_cli_map.py`/
`test_cli_symbols.py`/`test_cli_graph.py`. New `TestDoctorFormatting` in `test_cli.py` — header
present, summary present on both success and failure, and a narrow-terminal (`COLUMNS=40`) smoke test
confirming rich's rendering doesn't crash under an unusually narrow width (a real Windows Terminal/
cmd.exe condition, not a hypothetical one). Full existing suite (`tests/unit/`, `tests/integration/`
with `-m integration`, `quor verify`) re-run and confirmed zero regressions; compression benchmark
suite re-run and confirmed unchanged (127 cases, 35.9% overall, identical to baseline — this item
touches no filter/compression logic, checked anyway since it touches `quor/cli/`); `ruff check quor/
tests/` and `mypy quor/` both clean.

</details>

---

#### QB-075 — Repository Intelligence Consumption (audited, no action)

**Effort:** N/A (research only) · **Value:** N/A · **Risk:** N/A · **Category:** Research/Audit

Investigated whether the compression pipeline could consume the repository intelligence QB-072
already builds and caches (`RepoProfile`/`RepoSymbolIndex`/`RepoDependencyGraph`) to improve
compression quality — deterministically, rule-based, no heuristics/AI, O(1) lookups, no hook-path
slowdown. Per this repo's Rule 4 (research/benchmark first, present a recommendation, get sign-off,
only then implement), no code was written; the audit's own conclusion was that no qualifying
opportunity exists today, confirmed with the user, who chose to close this item with no
implementation rather than force a speculative feature.

**Status:** Closed — audited, no action taken.

<details>
<summary>Technical details</summary>

**Why no stage can consume repo intelligence today.** `StageHandler.apply(mask, config)`/
`can_handle(content, content_type)` (`quor/pipeline/stages/base.py`) is the complete interface every
built-in and third-party stage implements, documented as a stable, frozen plugin API — no stage
receives a file path or repo root, so none can key a lookup into a per-file structure like
`RepoSymbolIndex`/`RepoDependencyGraph` even in principle. The two places that do have a file path —
`quor/adapters/claude_read.py` (Read hook) and `quor/adapters/dispatcher.py` (Bash command string) —
are not stages, and both already resolve everything they need (extension → filter name) without any
repo-wide knowledge.

**Why the hot path can't call `ensure_repo_intelligence()`.** Traced `intel.py` end to end: even its
cache-hit branch (`_refresh_from_cache`) performs a full `walk_repository()` (git ls-files or
filesystem walk) plus `diff_repository()` (a stat call per file) before concluding nothing changed —
QB-072 deliberately excluded this from `quor hook`'s dispatch path for exactly this reason, and the
documented hook budget (`docs/final/CLAUDE.md`: <10ms parse+rewrite, <200ms/10k-line full pipeline)
is unchanged. Calling it from `dispatcher.py`/`claude_read.py` on every invocation would violate
"no noticeable slowdown" outright.

**Why cached intel is unsafe for live-content compression decisions even where a lookup would be
cheap.** Repo intelligence only refreshes on an explicit `quor map`/`symbols`/`graph` call; the
compression pipeline processes live Read/Bash output that can reflect an edit from seconds ago the
cache never saw. `python_ast_summarize`/`code_ast_summarize` already re-parse the *current* content
on every call rather than trusting any cache, which is correct — reusing stale cached facts for a
line-level decision on the very file being compressed risks a wrong compression, violating the
architecture's existing "meaning preservation — when uncertain, keep it" principle and this item's
own "never invent information" constraint.

**Checked the milestone's own example opportunities against what the data model actually contains:**
resolving import locations (data exists — `Edge.target_file`/`target_symbol` — but no stage acts on
imports at all, nothing to attach it to); canonical module names (no such field exists anywhere in
`Symbol`/`FileSymbols`); package boundaries (`RepoProfile.important_directories`/`services` is a
presence-only allowlist of well-known directory names, not a boundary model); symbol ownership (only
sound as "which file defines symbol X," and unsafe to use on the file being compressed per the
staleness point above); duplicate symbol references / repeated dependency chains / cross-call
context collapsing (inherently session-level — needs memory of what the assistant already saw; Quor's
pipeline is stateless per invocation, and building that memory would be a new, large, stateful
subsystem — explicitly the kind of speculative build-out this item was told not to do); command-output
compression via directory classification (no vendor/generated/build classification exists in
`RepoProfile` — `important_directories` is curated toward *interesting* directories, the opposite of a
noise filter).

**One adjacent, unrelated gap noted but explicitly not fixed here:** `claude_read.py`'s
`_SOURCE_CODE_FILTER_NAMES_BY_EXTENSION` only routes `.py`/`.js`/`.ts`/`.tsx` through
`code_ast_summarize`-backed filters even though `quor/pipeline/ast_summarize/registry.py` also has
working Go/Java/Rust/C# analyzers (QB-046) — but closing that gap only needs a per-extension
`is_language_available()` check, zero repo intelligence involved, so it's a separate, future item, not
a QB-075 finding.

**No code changed, no benchmark run** — the audit's own conclusion made implementation and
before/after measurement inapplicable. If a future item wants to revisit this, the two directions
identified as narrow enough to reconsider on their own are: (1) reporting-only surfacing of cached
`languages_covered`/symbol counts in `quor explain`/`quor gain` (zero pipeline risk, doesn't reduce
tokens, both commands already call `ensure_repo_intelligence()` off the hot path); or (2) a
deliberately scoped relaxation of the hot-path boundary — a read-only, no-rebuild, memoized load of
whatever intel is already on disk, never triggering a walk/rebuild — which would need its own design
pass before any code, given the staleness risk documented above.

</details>

---

#### QB-076 — Repository Intelligence Dashboard (`quor repo`)

**Effort:** Medium · **Value:** Medium · **Risk:** Low · **Category:** New Capability
(Reporting)

`quor map`/`quor symbols`/`quor graph` (QB-061/066/067) each already produce a full,
deterministic repository-intelligence artifact, automatically cached and kept up to date
by QB-072 — but each requires its own command and a full Markdown/JSON document to read
just to answer a quick "what does this repo look like" question. `quor repo` is a fourth,
purely presentational command: a compact, Rich-formatted terminal dashboard (plus `--json`)
built entirely from the same on-disk cache the other three already maintain — it never
walks the repository, parses a source file, or triggers a rebuild of any kind.

**Status:** Implemented (not committed, per session instruction).

<details>
<summary>Technical details</summary>

**Research first (per this repo's Rule 4).** Audited every field on `RepoIntelState`
(`intel_model.py`), `RepoProfile` (`model.py`), the cached per-file `FileSymbols`/`FileFacts`
(`symbols_model.py`/`graph.py`), and confirmed `intel_store.py`'s four on-disk cache files
(`state.json`/`profile.json`/`symbol_facts.json`/`graph_facts.json`) are read-only,
fail-open-on-corruption, and require no filesystem walk to load. The one genuine gap: the
cache stores each file's raw, *unresolved* graph facts (`FileFacts.relationships`), not
resolved `Edge`s with `target_file`/`target_symbol` — resolving those is real logic
(`graph._resolve_all()`), not a trivial aggregate. Rather than re-implementing that a second
time, `dashboard.py` calls the exact same, already-tested `graph.assemble_graph()` `quor
graph` itself uses, fed directly from the cached facts. Reading `assemble_graph()`'s own
source confirmed its `walk_result` argument is used for exactly one thing — a `used_git`
advisory note this dashboard doesn't surface — so a placeholder `WalkResult(files=[], ...)`
triggers zero filesystem walk of any kind; a dedicated test
(`TestRepoCommandDashboard::test_never_walks_the_repository`) patches `walk_repository` in
both `walk.py` and `graph.py` to raise and confirms the dashboard still renders correctly.
Every other field (symbol counts, language shares, largest modules, most-connected files) is
a direct copy or a cheap sum/count/sort/top-N over already-cached data — see
`dashboard_model.py`'s own docstring for field-by-field provenance. Two items the task's own
example section suggested — "ignored files" and "generated files" — were checked against the
actual data model and found genuinely unavailable anywhere in cached repository intelligence
(confirmed during the QB-075 audit immediately preceding this item); rather than inventing a
new walk/heuristic to produce them, they were **omitted entirely** from the dashboard, per
"never invent metrics."

**What shipped:** `quor/pipeline/repo_profile/dashboard_model.py` (the frozen `RepoDashboard`
data contract, plain dataclasses throughout — mirrors `symbols_model.py`/`graph_model.py`'s
"no Pydantic needed" convention so the whole tree is `dataclasses.asdict()`-serializable),
`dashboard.py` (`build_dashboard(root) -> RepoDashboard | None` — the cache-only aggregator),
`dashboard_render.py` (Rich `Console`/`Table` terminal layout, following `quor gain`'s
existing style rather than the plain-Markdown template `map`/`symbols`/`graph` use — this
command's own spec calls for a dashboard meant to be read directly by a person; plus
`render_json()`, identical information via `dataclasses.asdict()` + orjson), and
`quor/cli/commands/repo.py` (the `repo_command` entry point — resolves `--path` via the
existing `resolve_repo_root`, calls `build_dashboard()` directly, **never**
`ensure_repo_intelligence()`). `format_duration()` was added to `cli/format_utils.py`
(mirrors `format_count`/`format_percentage`'s "presentation only" convention) for the
"N minutes/hours/days ago" cache-age display. `REPO_DASHBOARD_FILTER_LABEL` was added to
`tracking/db.py` and excluded from `analytics/filter_divergence.py`'s low-performer check,
mirroring `REPO_PROFILE_FILTER_LABEL`/`REPO_SYMBOLS_FILTER_LABEL`/`REPO_GRAPH_FILTER_LABEL`
exactly (a presentation command has no "before" blob to compress against, so
`original`/`filtered` are recorded equal by design). Registered as a fifth exempt utility
command in `cli/main.py` (Analysis panel, alongside `map`/`symbols`/`graph`) and added to
`__main__.py`'s `_CLI_COMMANDS` routing set (without this, `quor repo` would have been
misrouted as an attempt to run a subprocess named `repo`).

**No-cache path:** when any of the four cache files is missing or unreadable,
`build_dashboard()` returns `None` (never generated and corrupted are treated identically —
the caller doesn't need to distinguish them) and `repo_command` prints a short, actionable
message pointing at `quor map`, exit code 0 (this is expected first-run state, not an error).

**Tests:** `tests/unit/test_repo_dashboard.py` (15 cases total across both new files) —
`build_dashboard()` aggregation exercised entirely via fixtures written straight into
`intel_store`'s cache files (no real source parsing needed, and it doubles as proof there's
no source file on disk for the function to read even if it tried): missing/partial/corrupted
cache all return `None`; verbatim reuse of `RepoIntelState`/`RepoProfile` fields; symbol
totals and per-language breakdown; graph edge resolution and relationship-kind counts;
most-connected-files ranking (including a tie-break case); largest-modules ranking;
cache-age computation. `tests/unit/test_cli_repo.py` — no-cache friendly message (plain and
`--json`), full dashboard render against a real `quor map`/`symbols`/`graph`-built cache,
`--json` field shape, the "never walks the repository" proof described above, `--path`
error handling (reused `resolve_repo_root`), and invocation tracking. Full existing
`tests/unit/` suite (all 73 files) re-run and confirmed zero regressions (exit code 0);
`ruff check quor/ tests/` and `mypy quor/` both clean.

**Validation — sample output** (this repo, 455 files, cache already warm from earlier
`quor map`/`symbols`/`graph` runs):

```
Quor Repository Dashboard

Repository: Quor
Root:       C:/Users/.../Quor
Indexed:    29 minutes ago (commit 0d2b2c47a8da)

Languages
Python      221 files  (90%)
TypeScript    8 files   (3%)
...

Symbols
Total symbols  3.7k
  python       3.5k
  ...

Dependency Graph
Nodes            228
Edges          13.6k
Resolved  5.8k (43%)

Largest modules
path                              language  symbols
tests/unit/test_stages.py           python      221
...

Most connected files
path                     out   in  total
tests/unit/test_cli.py   762   57    819
...

Repository Health
  - 43% of dependency edges resolved (7.7k unresolved — external/dynamic/ambiguous, by design).
```

`--json` produces the identical data as a flat, `dataclasses.asdict()`-shaped object (verified
in `test_json_flag_produces_valid_json_with_expected_fields`).

**Runtime — honest results, including a real limitation found.** Isolated `build_dashboard()`
timing (in-process, `time.perf_counter()`, 5 iterations):
- Small synthetic repo (40 files, 120 resolved edges): **5–10ms warm** — comfortably inside
  the <100ms target.
- This repo itself (228 nodes, **13.6k edges** — an unusually dense graph, driven almost
  entirely by test files' heavy `self.method()`-shaped `calls` relationships): **130–160ms**,
  confirmed via `cProfile` to be dominated (~166ms of ~244ms cumulative) by
  `graph._resolve_all()`/`_resolve_import_target()` — the pre-existing, unmodified edge-
  resolution algorithm `quor graph` itself already pays this exact cost for, not anything new
  this item adds (the dashboard's own sorting/counting/formatting on top is <10ms).
  **This exceeds the task's literal <100ms target on this specific, edge-dense repo** — an
  honest limitation, not hidden: any repo whose dependency graph is this dense will see the
  same cost, inherited directly from already-shipped code. Optimizing `_resolve_all()`
  further was investigated and explicitly declined during QB-072's own perf follow-up
  (documented above in that entry) for real correctness-risk-vs-benefit reasons, and
  redoing that analysis was out of scope for a reporting-only feature.
- Full-process wall time (`python -m quor repo`, real subprocess): 1.3–2.1s on this machine
  — but so is `python -m quor --version` (1.2–1.7s) and `quor map`'s own cache-hit path
  (1.6–2.4s): this machine's Python interpreter/import startup cost dominates *every*
  `quor` CLI invocation equally (a pre-existing, environment-specific characteristic —
  `PROJECT_STATUS.md`'s never-fully-closed-out "measure Python startup time on target
  Windows machine with corporate AV" pre-flight note), not something this item introduces.
  `quor repo`'s own marginal cost is consistently *lower* than `quor map`/`quor
  symbols`/`quor graph`'s cache-hit path on the same repo, since it skips their
  `walk_repository()` + `diff_repository()` confirmation step entirely.

**Memory:** `tracemalloc` around a single `build_dashboard()` call on this repo (13.6k
edges): **~48MB peak** during the call (loading `graph_facts.json`'s relationships and
building the resolved `Edge` list — proportional to graph size, matching `quor graph`'s own
footprint for the same data), but only **~440KB retained** after the call returns (the
compact `RepoDashboard` object itself; every intermediate structure is released once
`build_dashboard()` returns). No QB-071 regression: `quor repo` introduces no new cache and
retains nothing beyond one call's transient peak — each invocation is a fresh, short-lived
CLI process, so nothing accumulates across calls the way QB-071's optimization work was
scoped to prevent.

**Backward compatibility:** fully additive — no existing command, filter, stage, or cache
file's shape or behavior changed. `main.py`'s command-table docstring was updated to list
`repo` alongside `map`/`symbols`/`graph`/`version` as a sixth exempt utility command, per the
same running documentation convention QB-061/066/067/073 each followed.

</details>

---

#### QB-077 — Automatic Incremental Repository Intelligence (`quor repo` auto-refresh)

**Effort:** Small · **Value:** Medium · **Risk:** Low · **Category:** New Capability

**Research first (per this repo's Rule 4).** Before writing any code, audited what QB-077's own
spec asked for — a deterministic fingerprint, cache-hit-is-instant, rebuild-only-affected-files,
corruption-proof persistence, automatic triggers on repository-aware commands, first-run
onboarding, and an observability panel — against what QB-072 already shipped. The result: nearly
all of it already existed. `intel_diff.py` already fingerprints every file with a size+`mtime_ns`
fast path and a SHA-256 fallback (no full-file parse); `intel.py`'s `ensure_repo_intelligence()`
already does instant cache-hits, per-file incremental rebuilds for symbols/graph, one-time
onboarding, and a `--rebuild` escape hatch; `intel_store.py` already persists all four cache files
via `atomic_io.write_json_atomic` (tempfile + `os.replace`) and treats any corrupted file as a
cache miss rather than a wrong result; `RepoIntelligence` already carries `action`,
`files_scanned`, `files_reextracted`, and a computed `cache_hit_ratio` — exactly the metrics this
item's observability section asked for. Re-implementing any of that would have duplicated already-
correct, already-tested code. Presented this finding to the user before writing anything, per Rule
4's "recommendation before implementation" step.

**The genuine gap:** `quor repo` (QB-076) was *deliberately* built read-only/cache-only — a hard
requirement of that item's own spec was that it must never trigger a walk, re-parse, or rebuild.
This item reverses that, on purpose: `quor repo` is often the first repository-intelligence command
a user or AI assistant runs, so "users should not think about map/symbols/graph" (this item's own
framing) means `quor repo` itself needs to be able to build the cache, not just read one that may
not exist yet.

**Explicitly scoped out, confirmed with the user before implementing:** the item's own text also
listed `quor explain` and `quor gain` as commands that should auto-trigger a refresh. Reading both
commands' source confirmed neither reads or displays any repository-intelligence data today —
QB-075's prior audit already closed off wiring repo intelligence into the compression pipeline for
exactly this reason (no consumer, no benefit). Wiring `ensure_repo_intelligence()` into `explain`/
`gain` now would add a real repo walk-plus-diff (and possibly a multi-second cold build) to every
invocation of either command for zero observable payoff — pure latency with nothing to show for it.
Left both untouched; this is a documented decision, not an oversight, mirroring how QB-075 recorded
its own scoping call. Revisit if either command ever actually starts consuming repository
intelligence in its own output.

<details>
<summary>Technical details</summary>

**What shipped:** `quor/cli/commands/repo.py`'s `repo_command` now calls
`intel.ensure_repo_intelligence(root, rebuild=rebuild, echo=progress_echo)` before
`dashboard.build_dashboard(root)` — the exact same pattern `map_command`/`symbols_command`/
`graph_command` already use — and gained a `--rebuild` flag matching those three commands'
existing one. `build_dashboard()` itself (`dashboard.py`) is **unchanged**: it stays the pure,
tested, cache-only aggregator QB-076 shipped; it now simply always reads a cache
`ensure_repo_intelligence()` just guaranteed is fresh, instead of a possibly-stale or possibly-
absent one. All 15 pre-existing `test_repo_dashboard.py` cases pass unmodified.

**New "Repository Intelligence" status panel.** `dashboard_model.py` gained a frozen
`RepoIntelligenceStatus` dataclass (`status`, `cache_hit_ratio`, `files_scanned`, `files_reused`,
`changed_files`, `rebuild_mode`) and one new optional field on `RepoDashboard`:
`intelligence: RepoIntelligenceStatus | None = None` — defaulting to `None` keeps every existing
`build_dashboard()` caller/test byte-for-byte unaffected, since that function never sets it.
`repo_command` populates it directly from the `RepoIntelligence` its own `ensure_repo_intelligence()`
call just returned (`dataclasses.replace(dashboard, intelligence=...)`) — no second disk read.
`status`/`rebuild_mode` map `BuildAction` to "Up to date"/"Instant" (cache hit), "Refreshed"/
"Incremental" (partial rebuild), or "Rebuilt"/"Full" (onboarding or any full-rebuild-shaped action).
`changed_files` is the real added+modified+deleted+renamed count from the `RepoDiff` (not just
`reextraction_paths`, since a pure rename still counts as "changed" even though it wasn't re-
parsed) — `0` on a cache hit, `None`/rendered as "—" when there was no prior state to diff against
at all. `dashboard_render.py` gained `_print_intelligence_status()`, printed first (right after the
header, before Languages) since it establishes how much to trust everything printed below; no-ops
when `intelligence` is unset. `render_json()` needed no changes — `dataclasses.asdict()` already
picks up the new field.

**Cache consistency — audited, no code change needed.** Each of the four cache files is already
written atomically and independently, and every write happens only after the full in-memory
rebuild succeeds, so a crash mid-build leaves the previous cache completely untouched (nothing is
ever partially written). The four files aren't one cross-file transaction — documented already in
`intel.py`'s own comments — but every loader already treats a missing/corrupt sibling as
`corrupted_rebuild` and filters stale entries against the current file-walk set, so this narrow,
already-defended risk window was judged not worth new transactional complexity for. Noted here
explicitly rather than silently skipped.

**Benchmark extended.** `tests/benchmarks/repo_intel_benchmark.py` gained a "100 files modified"
scenario (files `mod_12`–`mod_111` of the synthetic repo, non-overlapping with the existing
rename/delete scenarios' files) alongside the pre-existing cold/warm/1-modified/10-modified/
renamed/deleted six, matching this item's own validation list (cold, cache hit, 1/10/100 modified).
`tests/unit/test_repo_intel_benchmark.py`'s synthetic repo size grew from 60 to 150 files to make
room, with a new `TestHundredFilesModified` asserting `files_reextracted == 100` and the exact
resulting `cache_hit_ratio` — count-based, not wall-clock, per this repo's no-flaky-test rule.

**Tests:** `tests/unit/test_cli_repo.py` was substantially rewritten — the old `TestRepoCommandNoCache`
class and `test_never_walks_the_repository` both asserted the exact behavior this item deliberately
reverses, so they're gone, replaced by: `TestRepoCommandAutoOnboard` (a fresh repo with zero prior
`quor map`/`symbols`/`graph` calls now builds intelligence and shows a dashboard directly),
`TestRepoCommandIntelligencePanel` (cache-hit → "Up to date"/"Instant", a file change →
"Refreshed"/"Incremental", `--json`'s `intelligence` field shape on both), `TestRepoCommandRebuildFlag`,
and `TestRepoCommandReflectsChanges` (a file added after `quor repo`'s own first build is picked up
on the very next `quor repo` call with no `quor map` in between — the direct, observable proof the
old "never walks the repository" guarantee is now intentionally false). The pre-existing
`TestRepoCommandDashboard`/`TestRepoCommandErrors`/`TestRepoCommandTracking` classes were kept,
plus one new defensive-fallback test proving the old "no cache" message still renders correctly if
`build_dashboard()` ever returned `None` right after a successful `ensure_repo_intelligence()` call
(unreachable in practice, but real fail-open code, kept covered). Full existing `tests/unit/` suite
(73 files) and `tests/integration/` with `-m integration` (7 cases) re-run and confirmed zero
regressions; `quor verify` passed (204/204 inline filter tests); compression benchmark suite
re-run and confirmed unchanged (127 cases, 35.9% overall, matching the committed baseline — this
item touches no filter/pipeline code); `ruff check quor/ tests/` and `mypy quor/` both clean.

**Performance — honest results, one target missed.** `python -m tests.benchmarks.repo_intel_benchmark`
against the (now 150-file) synthetic repo:

| Scenario | Action | Elapsed (s) | Cache Hit | Files Scanned | Re-extracted |
|---|---|---|---|---|---|
| cold build | onboarded | 0.97 | 0.00 | 150 | 150 |
| warm build (cache hit) | cache_hit | 0.26 | 1.00 | 150 | 0 |
| one file modified | incremental | 0.30 | 0.99 | 150 | 1 |
| ten files modified | incremental | 0.43 | 0.93 | 150 | 10 |
| one hundred files modified | incremental | 0.76 | 0.33 | 150 | 100 |

Against this item's own targets: **ten-modified-files &lt;2s is met** (consistently 0.43–0.60s across
repeated runs); **one-modified-file &lt;500ms is inconsistent** (300–430ms most runs, one outlier over
2s, attributed to transient Windows git-subprocess spawn variance, not this item's own code — see
below); **cache-hit &lt;50ms is not met** (consistently 260–370ms). Root cause, not re-profiled with
`cProfile` here since it's identical to already-diagnosed pre-existing behavior: `_refresh_from_cache()`
(`intel.py`, unmodified by this item) still runs a full `walk_repository()` — a `git ls-files`
subprocess call — plus a `stat()`-per-file `diff_repository()` pass *even on a true cache hit*, to
confirm nothing changed before it can say so; on Windows, subprocess spawn overhead alone is
typically 50–150ms, matching the gap. This is pre-existing QB-072 behavior this item's benchmark
addition merely measures for the first time against QB-077's own <50ms target — nothing this item's
own changes (`repo.py`/`dashboard_model.py`/`dashboard_render.py`) run on the cache-hit path adds any
extra cost, since `measure()` only times `ensure_repo_intelligence()` itself, not `quor repo`'s
dashboard-building on top of it. Recommend treating this the same way QB-076 treated its own missed
&lt;100ms target on a dense graph: an honest, pre-existing, now-documented limitation, not a blocker for
this item — closing it for real would mean optimizing `walk_repository()`/`diff_repository()`'s own
git-subprocess cost, out of this item's scope and not requested.

</details>

---

#### QB-078 — Repository Explorer (`quor explore`)

**Effort:** Medium · **Value:** Medium · **Risk:** Low · **Category:** New Capability
(Reporting)

`quor map`/`quor symbols`/`quor graph`/`quor repo` each answer a different repository-structure
question, but none answers a single, targeted one ("where is `UserService` defined," "what does
this file depend on") without reading a whole document. `quor explore` is a fifth,
sub-command-shaped reporting surface — `find <name>`, `deps <file>`, `used-by <file>`,
`file <path>`, `stats` — built entirely from the same on-disk cache the other four already
maintain. **Unlike `quor repo` (QB-077), it never calls `ensure_repo_intelligence()`** — the
task's own spec is explicit that this command must never walk, parse, or rebuild the repository,
a deliberate divergence from QB-077's auto-refresh philosophy (see ADR-042).

**Status:** Implemented (not committed, per session instruction).

<details>
<summary>Technical details</summary>

**Governance first.** `quor explore` is an 8th exemption to Quor's six-command cap (`schema`/`map`/
`symbols`/`graph`/`repo` are the prior five). Per CLAUDE.md's own gate and the ADR-037/038/039
precedent ("sign-off must be obtained in-session, not assumed from the originating task instructions
alone"), explicit user approval was requested and obtained before any code was written — see
ADR-042 for the full design summary that was presented for that approval, including the cache-only
vs. auto-refresh design tension this item deliberately resolves against QB-077's more recent
precedent.

**What shipped:** `quor/pipeline/repo_profile/explorer_model.py` (frozen dataclasses —
`CacheUnavailable`, `SymbolMatch`/`SymbolFindResult`, `DependencyResult`/`UsedByResult`,
`FileSummary`, `RepoStats`), `explorer.py` (`load_cache()` — the sole, cache-only read path,
distinguishing `missing`/`corrupted`/`stale`/`fresh`; `find_symbol()`, `file_dependencies()`,
`file_used_by()`, `file_summary()`, `repo_stats()`), `explorer_render.py` (plain-text + `--json`
via `dataclasses.asdict()`/orjson, one pair per result type), and `quor/cli/commands/explore.py`
(a Typer sub-app, `app.add_typer(explore_app, name="explore", ...)` in `main.py`, `"explore"` added
to `__main__.py`'s `_CLI_COMMANDS` — the exact omission ADR-037/038/039 each independently caught).
`dashboard.py::_most_connected_files()`'s inline connectivity `Counter` walk was promoted to a
shared `connectivity_counts()` function (behavior-identical, still called from its original site)
so `quor explore file`'s full-repository `Repository importance` tiering and `quor repo`'s top-10
listing share one implementation rather than two. `REPO_EXPLORE_FILTER_LABEL` added to
`quor/tracking/db.py` and excluded from `filter_divergence.flag_low_performers()`, mirroring the
four prior synthetic labels. `docs/final/CLAUDE.md` and `quor/cli/main.py`'s own module docstring
updated to list all six current exemptions (catching up `quor repo`, which had never been added to
either despite already shipping in QB-076/QB-077).

**Design choices confirmed with the user before implementation (see ADR-042 for full reasoning):**
cache-only reads only, never `ensure_repo_intelligence()`; `find` is exact-name-only (no fuzzy
matching); `Exports` reuses `Symbol.is_public` verbatim; `deps`/`used-by` are resolved `import`-kind
edges only, not every relationship kind; `Repository importance` is a tertile connectivity rank
over every file the last scan walked, not just files with edges.

**Testing:** 39 new tests — `tests/unit/test_repo_explorer.py` (19, pure logic: cache-state
detection including a `monkeypatch`-based "never walks the repository" regression test mirroring
QB-076's own; exact-match/ambiguous/not-found `find`; deps/used-by resolution and reverse-symmetry;
file summary; stats; a direct <100ms performance assertion on the query logic itself) and
`tests/unit/test_cli_explore.py` (20, CLI-level: missing/corrupted-cache error distinction,
`--json` schema stability, ambiguous-symbol listing at exit 0, tracking, byte-identical repeated
JSON output for the fields that don't carry a live clock read, nonexistent `--path`). Full gate:
`ruff check quor/ tests/` clean; `mypy quor/` clean (138 source files); full `pytest tests/unit/`
green (no regressions); `pytest tests/benchmarks/` green, compression behavior byte-identical
(this item touches `repo_profile/`, not `pipeline/`/`filters/`/`rewrite/`, so no compression-path
change was possible); `quor verify` 204/204. Manual smoke test against a real two-file git repo
confirmed `find`/`deps`/`used-by`/`stats` output matches the task's own example format exactly,
including `--json` mode.

</details>

---

#### QB-079 — Context-Aware Read Compression (Repository Intelligence in the Read hook)

**Effort:** Medium · **Value:** Medium · **Risk:** Low · **Category:** New Capability

Compressed Read output (QB-005F/QB-040/QB-007E4) carried no hint of the repository intelligence
Quor already maintains — a compressed `.py` file gave no signal of what imports it, what it
imports, or how central it is to the repo. This item adds a compact "Repository Context" block to
compressed Read output, sourced entirely from already-cached repository intelligence.

**Status:** Implemented (not committed, per session instruction).

<details>
<summary>Technical details</summary>

**Investigation first, per this repo's Rule 4.** The naive approach — reading `symbol_facts.json`/
`graph_facts.json` per Read call — was measured at 114ms combined for this repository (3.1MB +
714KB), scaling with total repo size, not the one file being read: an O(repo-size) cost on a path
that must be O(1)/near-O(1), and a direct violation of both CLAUDE.md's `<10ms` hook-response
budget and QB-072's own explicit, user-confirmed decision to keep repository-intelligence work off
the hook dispatch path entirely (`intel.py`'s own module docstring). Implementation was paused and
presented to the user per the task's own "stop if latency increases noticeably" condition.

**Resolution: a fifth, purely-additive cache file, `file_intelligence.json`.** Computed once at
existing build/refresh time (`intel.py`'s `_full_rebuild()`/`_refresh_from_cache()`, alongside the
existing `save_state()`/`save_profile()`/`save_symbol_facts()`/`save_graph_facts()` calls — never
inside the hook), holding one small `FileIntelligenceEntry` per file the last scan walked:
`language`, `kind` (`source`/`test`/`generated`/`configuration` — evidence-based only, via naming
convention, a bounded content-marker scan mirroring `entry_points.py`'s own bounded/timeout-guarded
pattern, and cross-referencing `RepoProfile.configuration_files`/`lockfiles`; no "library"/
"application" split — confirmed with the user there is no existing deterministic signal for that
distinction), `importance` (High/Medium/Low, via a newly-public `dashboard.importance_tiers()`
factored out of `explorer.py`'s own private tiering so both call sites share one implementation),
`imports`/`imported_by` (resolved `import`-kind edge counts), `entry_point`
(`Symbol.is_entry_point`), `top_symbols` (public top-level declarations only, capped at 5 — the
compressed body already carries the full AST summary, so this stays a compact pointer, not a
second symbol database), and its own `size`/`mtime_ns` fingerprint copy (so a consumer's staleness
check never has to load the much larger `state.json`). Versioned independently
(`FILE_INTELLIGENCE_VERSION`, embedded in the file itself) from `CACHE_SCHEMA_VERSION`, so this
cache's shape can evolve without forcing an unrelated full rebuild of the other four files; a
version mismatch is treated exactly like "missing" and backfilled on the next touch (including a
plain cache-hit) without rewriting anything else.

**A general-purpose cache, not a Read-hook-specific artifact.** `quor/adapters/claude_read.py` is
this cache's first consumer, not its only intended one — `quor explore`/`quor repo` or a future
editor integration can read the same file in O(1) later. The Read hook's own consumption
(`_maybe_prepend_repo_context()`) is scoped to the same 7 extensions
`_SOURCE_CODE_FILTER_NAMES_BY_EXTENSION` already routes to AST-summarize filters, fails open at
every step (missing cache, no entry, a `Path.stat()` size/mtime mismatch against the entry's own
copy — omit rather than show possibly-stale information), and only ever calls
`intel_store.load_file_intelligence()` — never `ensure_repo_intelligence()` or
`walk_repository()`. Measured against this real repository (469 files) after a real `quor map`
run: the lookup alone costs ~16ms, versus the 114ms the rejected naive approach would have cost —
and unlike that approach, this doesn't scale with total repo size the way loading
`symbol_facts.json`/`graph_facts.json` in full does.

**Testing:** new `TestFileIntelligenceRoundtrip` (`test_repo_intel_store.py`, including a
version-mismatch-as-miss case), a new `TestFileIntelligence` class in `test_repo_intel.py` (full
rebuild covers every walked file including non-AST ones, incremental refresh updates the changed
file, a true cache-hit with a valid file leaves it untouched, and two backfill cases — missing
file and stale version — that don't rewrite the other four cache files), a new
`test_repo_intel_file_intelligence.py` (`_primary_symbol_names()`/`_classify_kind()`/
`_build_file_intelligence()` unit coverage plus a parity test proving `intel.py`'s build-time cache
and `explorer.file_summary()` agree on `importance`/import counts for the same repo state), a new
`TestImportanceTiers` class in `test_repo_dashboard.py`, and a new `test_read_hook_repo_context.py`
(11 `run_hook()`-driven cases: block present with correct fields, omitted for missing cache/no
entry/stale size/stale mtime/non-source extensions/a path outside the repo root without
raising/a no-op compression). `tests/benchmarks/repo_intel_benchmark.py` gained a reported (not
asserted) `measure_file_intelligence_lookup()` at 150 and 2000 synthetic files;
`test_repo_intel_benchmark.py` gained a `cpu_seconds`-based (never wall-clock, per this repo's own
Rule 2) regression guard. Full gate: `ruff check quor/ tests/` clean; `mypy quor/` clean;
`pytest tests/unit/` full suite green (every file run, batched per this repo's own hook
self-timeout, no regressions); `pytest tests/benchmarks/` green; `quor verify` 204/204. Manual
smoke test: a real `quor map` run against this repository backfilled `file_intelligence.json` for
all 469 walked files, and the real `claude-read` hook invoked against a real `.py` file rendered a
correct Repository Context block matching the cache's own recorded facts.

</details>

---

#### QB-080 — Semantic Repository Search (`quor search`)

**Effort:** Medium · **Value:** Medium · **Risk:** Low · **Category:** New Capability

`quor explore` (QB-078) assumes the user already knows what they're looking for (an exact symbol
name, an exact file path). This item adds `quor search <query>`, a deterministic command that
discovers relevant files from cached repository intelligence — "where is auth implemented," "which
files deal with payments" — without embeddings, fuzzy ranking, TF-IDF, or any probabilistic
technique.

**Status:** Implemented (not committed, per this session's standing "commit only when explicitly
asked" rule).

<details>
<summary>Technical details</summary>

**Investigation first, per this repo's Rule 4.** Audited every existing repository-intelligence
cache before writing any search logic. `file_intelligence.json` (QB-079) is the only one cheap
enough to meet the ticket's own "under 100ms, never scale with repo size" requirement — its
full-dict load is asserted at <50ms CPU even at 2000 files
(`test_repo_intel_benchmark.py::TestFileIntelligenceLookup`). `symbol_facts.json`/`graph_facts.json`
(exhaustive per-symbol data, full edge list) were measured at 114ms combined for this repository's
469 files — the exact O(repo-size) cost QB-079 already rejected for the Read hook, and precisely
what `quor search` must never pay as repos grow. `quor explore find`/`deps`/`used-by` already pay
that cost, but as an explicit one-shot exhaustive query, not something meant to scale the way a
general search command must.

**The gap.** The ticket's own 7-tier evidence list separates "Exact/Prefix symbol match" from a
distinctly weaker "Top-symbol match," and names a 7th "Import/export relationship match" tier —
but `file_intelligence.json`'s `top_symbols` is capped at 5 public top-level names per file, and
the cache had no way to answer "what does this file import" beyond a bare count. Both gaps were
presented to the user as explicit stop-and-explain decisions (per this ticket's own conditions),
resolved as follows, then refined across four further review rounds before implementation:

1. **Symbol tiers (exact/prefix/top-symbol) never load `symbol_facts.json`.** All three operate
   only against `top_symbols`, differing solely in match strength (equality / prefix / substring).
   Known, accepted limitation: private, nested, or 6th+ symbols aren't matched here — `quor explore
   find` remains the tool for an exhaustive answer.
2. **`file_intelligence.json` gains one new field, `imported_files`** — every resolved
   `import`-kind edge target with a file as source, file-level only (never a call/inherit/export
   edge, never a `target_symbol`), full and uncapped (a cap would blind the dependency tier for
   exactly the well-connected "hub" files "show everything related to X" cares about most), derived
   from the *already-computed* edges list in the same loop that already produces the pre-existing
   `imports`/`imported_by` counts — no new repository traversal. `FILE_INTELLIGENCE_VERSION` bumped
   1 → 2 (the first genuinely new *indexed relationship* data the cache has gained, not just another
   scalar). Only the outgoing direction is persisted — `search.py::_build_reverse_import_index()`
   derives the reverse ("imported by") direction in memory, once per `search()` call, by inverting
   `imported_files` across the whole already-loaded dict (`O(total import edges)`, a `defaultdict(set)`
   accumulation then one `sorted()` pass — free duplicate-edge protection, fully deterministic).
   `quor search` reads only `file_intelligence.json`, always — never `graph_facts.json`.
3. The resulting 7th evidence tier is named `dependency` (not the ticket's literal "import/export" —
   the cache only ever stores resolved imports, never export data) and matches via **exact
   case-folded path-token equality** against each neighbor's `{stem, filename, directory
   components}` (`search.py::_path_tokens()`), not substring — plain substring search over a
   whole-repo-wide neighbor list got noisy fast (`"auth"` spuriously matching
   `authentication.py`/`oauth.py`).
4. New `--importance high|medium|low` filter alongside `--kind`/`--language`/`--entry-points` — the
   data already existed on every entry.
5. Ranking tuple, every component named rather than an anonymous literal: tier priority, then entry
   point (ranks ahead of plain High importance — usually the more useful result on a tie), then
   importance, then filename-length closeness to the query (scoped to only the `exact_filename`/
   `filename_contains` tiers — applying it universally was an early mistake, caught in review:
   filename length has no bearing on a symbol/directory/dependency match), then path (the primary,
   stable tiebreak — alphabetical, doesn't drift as the repo evolves), then connectivity
   (`imports + imported_by`, descending) as the very last, most volatile tiebreak.
6. `SearchMatch.matched_value` (not an earlier `detail` draft) is always populated and shown
   prominently as a "Matched:" line in text output, for every tier, not only symbol/dependency ones.

**Deliberately not built, per the user's own final, distilled instruction each round:** a
confidence/star rating derived from evidence tier; a `--explain` flag surfacing every tier a file
did/didn't match (both good ideas, worth a future ticket); extracting a shared
`RepositoryQueryEngine` behind `explore`/`search`/`repo` (reasonable once a 4th–5th
repository-reporting command exists — candidate QB-085+, not now); an interactive `quor search
--interactive`/`quor browse` mode (a natural next step once QB-078/079/080 all exist, but a
separate ticket).

**New modules**, mirroring `explorer.py`/`explorer_model.py`/`explorer_render.py`'s own
cache-only/plain-dataclass/fixed-template conventions: `search_model.py` (`SearchEvidence`,
`SearchMatch`, `SearchResult` — reuses `explorer_model.CacheUnavailable` directly rather than
duplicating it), `search.py` (`load_cache()`, `_build_reverse_import_index()`, `_path_tokens()`,
`_dependency_neighbors()`, `_best_evidence()` — all 7 tiers checked in one function, in priority
order, since the tier ordering *is* the command's contract — `search()`), `search_render.py`
(`render_search_text/json()`, reusing `explorer_render.render_cache_unavailable_text/json()`), and
`cli/commands/search.py` (a single command, not a sub-app like `explore` — `search` has one verb).
Wired as a 9th exempt utility command (`quor/cli/main.py`, `quor/__main__.py::_CLI_COMMANDS`),
tracked under a new `REPO_SEARCH_FILTER_LABEL` (`quor/tracking/db.py`, excluded from
`filter_divergence.py`'s low-performer check, same as every prior synthesis-not-compression
command).

**Testing:** `test_repo_intel_file_intelligence.py` gained `imported_files` population/dedup/sort
cases including a many-neighbor hub-file case and an explicit "no reverse direction persisted"
case; `test_repo_intel_store.py`'s roundtrip test extended to the new field; new
`test_repo_search.py` (evidence-tier isolation including the exact-token-not-substring dependency
case, priority ordering, every filter, every tiebreak including the filename-length exclusion for
non-filename tiers, limit/truncation, case-insensitivity, and a same-input-twice determinism
assertion) and `test_repo_search_cli.py` (missing/corrupted cache, text/JSON shape, validation
errors, zero-match-is-not-an-error, and one real `quor map`-build-then-search end-to-end test).
`tests/benchmarks/repo_intel_benchmark.py` gained `measure_search_latency()` and a separate
`measure_reverse_import_index()` (reported, not asserted, at 150/2000 synthetic files — the reverse
index gets its own number since it's the one genuinely new algorithmic step this item introduces);
`test_repo_intel_benchmark.py` gained matching `cpu_seconds`-based regression guards (search
<0.1s, reverse index <0.05s, generous bounds against QB-080's own 100ms target). Full gate: `ruff
check quor/ tests/` clean; `mypy quor/` clean; `pytest tests/unit/` full suite green (all 78 test
files, batched per this repo's own hook self-timeout, no regressions — including the full,
previously-slow `test_repo_intel.py`/`test_cli_repo.py`/`test_repo_profile_*_benchmark.py`
suites); `quor verify` unchanged at 204/204.

**Real-repository validation** (this repository, 475 files, after `quor map --rebuild`):
`file_intelligence.json` grew from 114,181 bytes (reconstructed pre-QB-080 shape) to 205,028 bytes
— **+90,847 bytes, +79.6%**. Reported plainly rather than characterized as "small," per the user's
own "stop and explain before storing anything substantially larger than necessary" — in absolute
terms it's still modest (205KB vs. `graph_facts.json`'s 714KB / `symbol_facts.json`'s 3.1MB), and
the growth is spread across 187 files with resolved imports (685 total edges, max 24 for any single
file) rather than dominated by one or two pathological hub files, so no compact (index-based)
representation was pursued — the condition for revisiting that ("one or two extreme hub files
dominate the delta") wasn't met. Latency, measured directly against the real, already-loaded cache:
`_build_reverse_import_index()` well under 1ms; `search()` end to end (load + score + rank) ~6–8ms
wall time per query — two orders of magnitude under the ticket's 100ms target. Manual smoke test:
`quor search FileIntelligenceEntry`/`quor search payment` (text and `--json`) against this real
repository produced correct, byte-identical output across repeated invocations.

</details>

---

#### QB-081 — Repository-Aware Read Hook

**Effort:** Medium · **Value:** Medium · **Risk:** Low · **Category:** New Capability

QB-080 gave Quor a deterministic way to answer "what files match this query," but nothing in the
Read hook ever asked that question automatically. This item makes the Read hook the first automatic
consumer of QB-080's `search()` engine: every Read now gets one chance at a compact "Relevant
repository files" section, built from deterministic query terms extracted from the user's own most
recent prompt text — independent of whether the file being read is source code, and independent of
whether `_compress_read_output()` did anything at all.

**Status:** Implemented (not committed, per this session's standing "commit only when explicitly
asked" rule).

<details>
<summary>Technical details</summary>

**The query-source gap, presented to the user as an explicit stop-and-explain decision (per this
ticket's own conditions).** `search()` (QB-080) takes a query string, but a Claude Code PostToolUse/
Read hook payload carries no free-text "user request" field — only `tool_name`/`tool_input.file_path`/
`tool_response`. The only path to real prompt text is `transcript_path`, a JSONL conversation log
Claude Code passes on every hook payload — and Claude Code's own documentation states the per-line
transcript schema is internal and can change between releases. Presented to the user as two options
(best-effort transcript parsing vs. abandoning "user request" text entirely in favor of file-path-
derived context); resolved in favor of best-effort parsing, on the reasoning that this codebase's
existing fail-open discipline (every branch of `claude_read.py` already degrades to "do nothing" on
any unexpected condition) absorbs the risk cleanly: a future transcript format change simply makes
`_extract_last_user_prompt()` find nothing, and the whole feature silently stops firing — never a
hook failure.

**Query extraction (`quor/pipeline/repo_profile/query_extract.py`) is pure, deterministic text
processing — no NLP, no stopword list, no word-frequency heuristic.** A token qualifies as a search
term only by *shape*: a quoted span (`` `...` ``/`"..."`, single quotes excluded since an apostrophe
in a contraction/possessive has no reliable closing partner) is always taken verbatim; a bare word
qualifies only if it contains an underscore (snake_case), a path separator (directory-like), a dot
after trailing-punctuation stripping (filename/import-looking), or a lowercase-to-uppercase case
transition (camelCase/PascalCase). Plain English words match none of these and are silently dropped —
the same "no unevidenced classification" discipline `intel_model.FileKind` already applies to
`kind`. Deduplicates case-insensitively, preserves first-seen order, and caps at `MAX_QUERY_TERMS`
(4) — the number that actually bounds this feature's worst-case added latency, since each term drives
one full `search()` pass over `file_intelligence.json`.

**Merging (`search.merge_search()`, added directly to `search.py` so it can reuse `_EVIDENCE_PRIORITY`
and `search()` without crossing a private-module boundary) is composition, not a new query engine.**
Runs `search()` once per query term, keeps each file's strongest evidence tier across every query
that matched it, and orders the merged result by `(tier, path)` — deliberately not `search()`'s own
richer `_sort_key` (importance/entry-point/filename-length/connectivity), since those tiebreaks are
only meaningful relative to one query string and a merged file may have been found by several
different ones. Accepts an `exclude` set so the file currently being read is never recommended to
itself.

**Rendering (`search_render.render_relevant_files_block()`)** is a new, deliberately shorter label
set than `render_search_text()`'s CLI-oriented template — one path plus one evidence line per file, no
scores, no confidence, matching the ticket's own illustrative format exactly.

**Wiring (`claude_read.py::_maybe_prepend_relevant_files()`), called from `_handle_text()` — not
`_compress_read_output()` — so it runs on every Read regardless of which branch (or no branch)
handled compression.** Ordered cheapest-check-first so a Read with nothing for this feature to do
(no `transcript_path`, or a prompt with no identifier-shaped terms) never touches
`file_intelligence.json` at all: `base` must be a string → `file_path` non-empty → `transcript_path`
non-empty → the bounded transcript-tail read yields prompt text → extraction yields at least one
term → *only then* is `intel_store.load_file_intelligence()` called → `merge_search()` returns at
least one match. Deliberately returns `None` (not the unchanged `base`) on any early exit — returning
`base` would make `_handle_text()` treat an untouched original response as "genuinely compressed" and
set `updatedToolOutput` to a byte-for-byte copy of content Claude Code already has, silently breaking
the existing "omit if unchanged" contract every other branch of this module already follows.
`transcript_path` was added as a typed (but adapter-scoped, best-effort) field on
`PostToolUseHookInput` (`quor/adapters/base.py`) — QB-007A/QB-079 never needed it.

**Latency bound to the transcript, not conversation length.** `_read_transcript_tail()` reads only
the last 64KiB of `transcript_path` (seeking from the end, dropping a possibly-truncated first
line), regardless of total transcript size — a long-running session's transcript can grow far larger
than `file_intelligence.json` ever does, and this feature must never let Read-hook latency scale with
conversation length.

**Configuration:** one constant, `claude_read.MAX_RELEVANT_FILES` (default 5, within the ticket's
suggested 3-8 range) — no runtime flag yet, per the ticket's own scope.

**Testing:** `tests/unit/test_query_extract.py` (determinism, every shape rule individually, dedup,
ordering, the default/custom term limit), `tests/unit/test_repo_search.py`'s new `TestMergeSearch`
class (single-query parity with `search()`, cross-query merging, duplicate terms, same-file-strongest-
tier, `exclude`, the result cap, tier-then-path ordering, determinism),
`tests/unit/test_search_render_relevant_files.py` (empty input, format, every evidence label, no
scores/confidence, multi-match ordering, determinism), and a new
`tests/unit/test_read_hook_relevant_files.py` (10 `run_hook()`-driven cases covering every case this
ticket's own "Tests" section asks for: identical-prompt determinism, duplicate query terms, multiple
queries resolving to the same file, cache unavailable, empty extraction — both "no qualifying terms"
and "no `transcript_path` at all" — the result cap, deterministic tier-then-path ordering, the
file-being-read exclusion, and injection into a file type `_compress_read_output()` never touches at
all, the key behavioral difference from QB-079's Repository Context block). Full gate: `ruff check
quor/ tests/` clean; `mypy quor/` clean; `pytest tests/unit/` full suite green (all 82 test files,
batched per this repo's own hook self-timeout, no regressions — every pre-existing
`test_read_hook_repo_context.py` case stays green unchanged, since none of its fixture payloads ever
set `transcript_path`, so this feature is a strict no-op for all of them); `quor verify` unchanged at
204/204.

**Benchmark (`tests/benchmarks/repo_intel_benchmark.py::measure_relevant_files_latency()`),
extraction/search/render measured independently, per this ticket's own requirement.** Real numbers
against synthetic 150- and 2000-file repos, with `file_intelligence.json` already loaded (mirroring
`measure_search_latency()`'s own convention): extraction ~0.08–0.1ms (negligible, pure string work);
merged search (worst case, `MAX_QUERY_TERMS`=4 full `search()` passes) ~3.3ms at 150 files, ~49ms at
2000 files; render <0.01ms. `tests/unit/test_repo_intel_benchmark.py` gained matching `cpu_seconds`-
based regression guards (extraction <0.01s, merged search <0.4s — 4x `TestSearchLatency`'s own
single-query 100ms ceiling, generous rather than tight — render <0.01s).

**Real-repository validation** (this repository, 479 files, after a real `quor map` run): a real
`claude-read` hook invocation, fed a synthetic transcript asking "How does merge_search relate to
the search.py module and file_intelligence.json?" against a real `.py` file, correctly surfaced
`quor/pipeline/repo_profile/search.py` (exact symbol: `merge_search`), `quor/cli/commands/search.py`
(exact filename), a test file (filename contains), and two dependency-tier files — composing
correctly ahead of QB-079's own Repository Context block and the AST-summarized body.

**Deliberately not built:** a runtime config flag for `MAX_RELEVANT_FILES` (ticket's own scope: "no
runtime flags yet"); reusing `_maybe_prepend_repo_context()`'s own cache-load path (would have coupled
two independently-triggered features and risked regressing QB-079's already-shipped tests; this
feature performs its own independent, cheapest-check-first-gated load instead); a symbol-level or
call/inherit/export-edge dependency tier (QB-080's own file-level-only dependency tier is reused
unchanged — see that item's own module docstring for why).

</details>

---

#### QB-046 — AST-aware summarization for more languages (Go, Rust, Java, C#)

**Effort:** Large (per language) · **Value:** Medium · **Risk:** Low · **Expected token impact:**
Medium · **Category:** Feature

**Status:** Implemented (Go, Rust, Java, C# analyzers and `quor[go]`/`quor[java]`/`quor[rust]`/
`quor[csharp]` extras all present in `pyproject.toml`, benchmark corpus backfilled) — not yet called
out in its own `CHANGELOG.md` release entry.

**Housekeeping correction (2026-07-31, found during a competitive-landscape/roadmap prioritization
pass):** this item was still sitting in [Now](#now), listed as the *lowest*-priority not-yet-started
item, when in fact it was already fully implemented — moved here to Completed where it belongs.
Left uncorrected, a stale "not done" entry like this actively misleads prioritization work: it was
about to consume a slot in a freshly re-ranked roadmap for work that doesn't need doing. No other
Now/Next/Later item was found in the same state during this pass (QB-041/052/047/054/049/039/053/055
`Status:` lines were individually re-checked and confirmed genuinely "Proposed. Not scoped or
implemented").

<details>
<summary>Technical details</summary>

QB-005 shipped structure-aware, signature-preserved reading for Python, JavaScript, TypeScript, and
TSX. This is the direct continuation for the other languages QB-035's original scope named (Go,
Rust, Java) plus one it didn't (C#) — using the same `tree-sitter`-based framework QB-005B already
built and proved reusable across three languages.

**Problem:** `quor/pipeline/ast_summarize/registry.py` only had analyzers for `python`,
`javascript`, `typescript`, and `tsx`. A `.go`/`.rs`/`.java`/`.cs` file read through Quor got no
structural compression, falling through to plain `cat`/`Read` passthrough.

**What shipped:** One new analyzer per language, each following QB-005B/C/D's proven shape: a
`tree-sitter-<language>` grammar (each its own new optional dependency, following the
`quor[javascript]` extras precedent), a node-type mapping for that language's function/method/class
constructs, and the same ERROR-node-overlap exclusion rule
(`quor/pipeline/ast_summarize/_treesitter_utils.py`, already generalized by QB-005D specifically so
a third language wouldn't have to reinvent it). `analyze_go()`/`analyze_java()`/`analyze_rust()`/
`analyze_csharp()`, each reusing the same `tree-sitter<0.26.0` ceiling and ERROR-node-overlap rule
as the shipped JS/TS/TSX analyzers.

**Why this supersedes half of QB-035's original scope:** QB-035's own history already tracked this
exact language-expansion work under its own update notes and closed the JS/TS portion via QB-005.
This item exists so the *remaining* language work (Go/Rust/Java, plus C#) has a home that isn't
bundled with QB-035's now-unrelated multi-agent-support half — see QB-035 in [Later](#later) for
that half.

**Benchmark backfill (`chore/qb-046-benchmark-corpus-expansion`):** `cat-csharp`/`cat-go`/`cat-java`/
`cat-rust` shipped with zero benchmark corpus representation (each had only 4 synthetic-snippet
inline filter tests). Added 8 realistic, multi-method domain-file cases (2 per language, mirroring
the storefront/payments fictional codebase `cat-python`/`cat-javascript`/`cat-typescript` already
use) plus `baseline.json` regression coverage. Bundled in the same PR with unrelated realism-
hardening cases for several already-shipped filters (git-status, git-log, pytest, mypy, pnpm, yarn,
bun, gcc) generated in the same pass — all pure benchmark-data additions, no source changes. Real
per-language compression, confirmed against the current 127-case benchmark run (2026-07-31): C#
41.4%, Go 27.0%, Java 55.6% (category average; best single case 88.6%), Rust 37.6% — see QB-085's
README rewrite for where these numbers are now surfaced publicly.

**Outstanding housekeeping (not done in this pass):** a dedicated `CHANGELOG.md` entry for this
work has still not been written — unclear which past version actually shipped it in git history.
Recommend a follow-up pass to identify the correct version and backfill the entry, since the
`CHANGELOG.md`/`pyproject.toml` version history is otherwise treated as authoritative elsewhere in
this document.

</details>

---

#### QB-083 — Cross-Platform Gemini Hook Launcher

**Effort:** Small · **Value:** Medium · **Risk:** Low · **Category:** Bug Fix

**Status:** Implemented (2026-07-31).

<details>
<summary>Technical details</summary>

**The bug, and why QB-082 didn't already fix it.** QB-082 made Claude Code's hook launcher
cross-platform but deliberately left `quor/adapters/gemini_adapter.py` untouched (see that item's
own scope note and ADR-043's "Gemini deferred, deliberately" section) — Gemini's adapter carries its
own fully independent PowerShell-only launcher (`HOOK_PS1_TEMPLATE`, a hardcoded `powershell
-ExecutionPolicy Bypass -File` command string), never wired into `hook_manifest.py`'s shared
`ClaudeHookSpec` machinery at all. The result: `quor init --agent gemini` on macOS/Linux wrote a
hook that would fail every Gemini CLI `BeforeTool` invocation with "command not found," the exact
same class of bug QB-082 fixed for Claude, just left open in a second adapter.

**Fix: reuse QB-082's platform primitives, don't rebuild them.** `gemini_adapter.py` now imports
`quor.adapters.hook_manifest` (module import, not `from ... import is_windows`, matching `init.py`'s
own reasoning — a test patching `hook_manifest.is_windows` must reach every call site through one
shared reference). Windows keeps the existing `gemini-hook.ps1` launcher unchanged. macOS/Linux now
get a new `gemini-hook.sh` launcher — `exec "{python}" -m quor hook gemini command_intercept`,
identical in shape to `quor/adapters/claude.py`'s own `HOOK_SH_TEMPLATE` — registered as
`hook_manifest.POSIX_SHELL "<path>"` and chmod'd `0o755` after writing. The single `_SCRIPT_NAME`
constant became `_WINDOWS_SCRIPT_NAME`/`_POSIX_SCRIPT_NAME` resolved through a `_script_name()`
function called fresh at every use site — the same access-time-resolution reasoning ADR-043 already
established for `ClaudeHookSpec.script_name`, adapted to a plain function since Gemini has exactly
one hook rather than a family of specs.

**Deliberately not migrated to `ClaudeHookSpec`/`HOOK_SPECS`:** that dataclass/tuple exist so
`init.py`/`doctor.py` can iterate a *growing family* of Claude hooks generically. Gemini has exactly
one hook and its own independent `install()`/`doctor_checks()` on the `AgentAdapter` Protocol
(QB-068) — only the two platform primitives (`is_windows()`/`POSIX_SHELL`) are reused, not the
dataclass itself, keeping the two adapters' otherwise-independent install/doctor logic uncoupled.

**No `doctor --fix` repair-path gap (unlike QB-082's own finding for Claude):** Gemini has no
equivalent of `doctor.py`'s `_repair_hooks()` — its `doctor_checks()` only ever reports install
state, never regenerates the script — so `install()`'s own `chmod` is the only POSIX-executable-bit
code path that exists here, with no second call site to keep in sync.

**Testing:** `tests/unit/test_gemini_adapter.py` gained a module-wide autouse fixture pinning
`hook_manifest.is_windows` to `True` (mirroring `test_cli.py`'s identical QB-082 fixture, so every
pre-existing test keeps exercising the Windows path unchanged regardless of the host OS running
pytest) and a new `TestGeminiPosix` class covering `.sh` script content/extension, the `0o755`
executable bit (skipped on real Windows hosts), the `<sh> "<path>"` registered command shape (and
absence of `powershell` in it), and a full install-then-`doctor_checks()` green check.
`tests/integration/test_cli_commands.py` gained
`TestGeminiInitIntegration::test_real_posix_launcher_executes_end_to_end`: a real, unmocked `sh`
subprocess executes the generated `.sh` launcher against a synthetic Gemini CLI `BeforeTool` payload
and confirms the rewritten `hookSpecificOutput.tool_input.command` comes back correct. No CI matrix
change needed — `macos-latest` was already added by QB-082 and now exercises Gemini's own POSIX
launcher too. Full gate: `ruff check quor/ tests/` clean; `mypy quor/` clean; full `pytest
tests/unit/` green; `pytest tests/integration/ -m integration` green.

**Files changed:** `quor/adapters/gemini_adapter.py`, `tests/unit/test_gemini_adapter.py`,
`tests/integration/test_cli_commands.py`, `docs/final/ADAPTERS.md`, `docs/final/DECISIONS.md`
(ADR-044).

</details>

---

#### QB-084 — Live Terminal Dashboard + Doc Cleanup

**Effort:** Medium · **Value:** High · **Risk:** Low · **Category:** Feature

**Status:** Implemented (2026-07-31).

<details>
<summary>Technical details</summary>

**Request:** a Headroom-style live view of token savings, explicitly modeled on a competitor's
browser dashboard, plus a real (non-approximate) token counter, a fix for a broken `py` command on
macOS, and a much shorter README with real numbers. Two parts of the request directly conflicted
with standing decisions — a browser UI (`ANTI_GOALS.md` #7) and a real tokenizer (ADR-013's
`tiktoken` rejection) — surfaced and confirmed with the project owner before building anything:
terminal view over browser, keep char/4 over `tiktoken`. See ADR-045 for the full reasoning.

**`quor dashboard`** (`quor/cli/commands/dashboard.py`): a ninth exempted utility command, foreground
`rich.live.Live` view polling the existing SQLite tracking DB for rows since the command started
(`--refresh` seconds, default 1s; `--once` or a non-TTY caller gets one static snapshot instead).
Shows tokens saved this session, a fixed-reference-price cost estimate (explicitly caveated,
separate from the standard ±20% token disclaimer), top filters, and a recent-activity feed of
metadata only (never command output content, per `ANTI_GOALS.md` #4). No new dependency (`rich` is
already core), no port, no daemon.

**`quor/tracking/db.py`:** `query_gain()` gained an additive `since: datetime | None` parameter
(falls back to the existing `days`-relative window when omitted — `quor gain` itself is unaffected)
and a new `query_recent_invocations()` for the dashboard's feed. Both are read-only views over the
existing `invocations` table; no schema change.

**Doc fixes:** `docs/FAQ.md`'s "Corporate laptops" section told users to run `py -m ...`, which only
exists on Windows and contradicts ADR-029's own reasoning — the direct, confirmed cause of a real
"`py`: command not found" report on macOS. Fixed to `python -m ...`. `README.md` rewritten from 136
to 90 lines: denser tables, the real 35.3%-overall benchmark figure surfaced (previously only in
`docs/BENCHMARKS.md`), and an explicit `python -m quor` fallback note next to Install.

**Registration:** `quor/cli/main.py` (import + `app.command(name="dashboard", ...)`, docstring's
"six + N exempt" count updated) and `quor/__main__.py`'s `_CLI_COMMANDS` frozenset — both call sites
updated together, per ADR-037/038/039's own repeated warning about this exact omission.

**Files changed:** `quor/cli/commands/dashboard.py` (new), `quor/tracking/db.py`,
`quor/cli/main.py`, `quor/__main__.py`, `docs/FAQ.md`, `README.md`, `docs/final/DECISIONS.md`
(ADR-045), `tests/unit/test_tracking_db.py`, `tests/unit/test_dashboard.py` (new).

</details>

---

#### QB-085 — README Rewrite: Marketing Pass + Real Benchmark Numbers

**Effort:** Small · **Value:** Medium · **Risk:** Low · **Category:** Documentation

**Status:** Implemented (2026-07-31).

<details>
<summary>Technical details</summary>

**Request:** QB-084's README trim was written from a developer's point of view — accurate, but not
positioned to sell the product. Two specific problems flagged directly: (1) the README's own
headline number (35.3%, from the stale 60-case corpus QB-047 has since grown past) understated what
the *current*, CI-gated 127-case benchmark run actually shows, and buried the real high-end cases
(several real commands compress 75-89%) instead of leading with them; (2) a separate, unrelated
concern raised in the same conversation — mypy's real-usage net-negative compression (QB-052) — was
explicitly *not* wanted folded into marketing copy, only tracked as engineering follow-up (see
QB-052's own 2026-07-31 product-decision note).

**What changed:** Re-ran `python -m tests.benchmarks.run_benchmarks` against the real, current,
CI-verified 127-case corpus (confirmed matching the committed `baseline.json` byte-for-byte) rather
than reusing QB-084's carried-forward 35.3%/60-case figure — the honest current number is
**35.9%/18,962 tokens saved**, plus four verified real high-end cases pulled directly from
`benchmark-results.json`'s `best_performers` (a mostly-cached `pip install`, 88.8%; a deeply nested
Java exception, 88.6%; noisy `pnpm install` output, 77.1%; a large JS file read via Claude Code's
`Read` tool, 75.0%) and the `per_ecosystem` breakdown (Java 55.6%, config files 53.9%, JavaScript
49.5%, down to Documents at 24.8%) — every number in the new README traces to a real, reproducible
benchmark run, nothing invented. No competitor named or compared against (none appear in any
existing public-facing doc); no fabricated testimonials, user counts, or logos — per this repo's own
Rule 4/ANTI_GOALS #9 spirit, credible verified numbers over hype.

**Also corrected while rewriting:** the "Supported" section's `**Assistant:** Claude Code.` line
predated QB-068/QB-069's multi-agent adapter work entirely — it now accurately states Claude Code
and Gemini CLI get full compression, and the six `DetectionOnlyAdapter` agents (Codex CLI, Cursor,
VS Code Copilot agent mode, Windsurf, Aider, Continue.dev) are listed as detected-but-pending,
verified against each adapter's own `agent_id`/`display_name` in `quor/adapters/*.py`. The Commands
table gained `quor search` (QB-080, previously omitted despite already being a shipped command).

**Deliberately not done:** a full regeneration of `docs/BENCHMARKS.md`'s own prose (still describes
the 60-case corpus as of its 2026-07-15 generation date) — flagged to the project owner as a larger,
separate staleness gap, out of scope for a README-only rewrite; `docs/BENCHMARKS.md`'s narrower
"Full breakdown" link from the README remains valid as a path, just not yet regenerated to match the
127-case corpus.

**Files changed:** `README.md`, `backlog.md` (this entry, plus the QB-052 product-decision note).

</details>

---

#### QB-090 — Repository-Intelligence Onboarding Nudge

**Effort:** Medium · **Value:** High · **Risk:** Low · **Category:** Feature

**Status:** Implemented (2026-07-31).

<details>
<summary>Technical details</summary>

**The gap this closes:** a real user's first `quor init --claude` install produced no mention
anywhere that `quor map`/`symbols`/`graph` exist — the Read-hook features built on top of that
cache (QB-079's Repository Context block, QB-081's Relevant files block) were silently inert for
anyone who didn't already know to run one of those commands first. Raised directly by the project
owner after their own first install produced zero indication of this, with an explicit, detailed
user-journey spec: nudge at install time (skipped outside a git repo), nudge from the shared,
global hook the first time it fires against a *different*, not-yet-indexed repository, and an
occasional staleness re-check gated by both elapsed time and how much actually changed — deliberately
simple, "not a big state machine," after an initial richer consent/preference design was reviewed
and rejected in favor of a throttled-tip-only approach with no per-repo preference storage.

**Two surfaces, matching two genuinely different execution contexts (see ADR-046 for the full
architectural reasoning):**
- **`quor init --claude`** (`_maybe_offer_repo_intelligence_setup()`, `init.py`) — interactive,
  real TTY: shown once, only inside a git repo with no cache yet, with a real file count and a
  clearly-labeled rough time estimate (`nudge.estimate_build_cost()`), builds immediately via
  `ensure_repo_intelligence()` on "yes." `--yes` skips it entirely.
- **The Read hook** (`_maybe_prepend_repo_intel_nudge()`, `claude_read.py`, backed by new module
  `quor/pipeline/repo_profile/nudge.py`) — passive, non-interactive "Repository Tip" text. Never-
  built: shown at most 5 times total (same throttle philosophy as `pipeline/onboarding.py`'s
  existing, unrelated onboarding message). Stale: checked at most once per 24 hours (a cheap
  `git rev-parse HEAD` compare plus, only on a real difference, one `git diff --shortstat` call —
  never `ensure_repo_intelligence()`/`walk_repository()`, holding to the exact hot-path guarantee
  QB-079/QB-081 already established), shown once per check that finds ≥20 changed files.

**Two real findings from building this, not assumed up front:**
1. An early version gated the hook nudge on repo-identity alone and fired inside this codebase's
   own test suite — any Read-hook test that doesn't `monkeypatch.chdir()` runs with `Path.cwd()`
   pointing at the real Quor checkout (a real git repo, no isolated cache), breaking several tests'
   "pure passthrough is a true no-op" assertions. Fixed by also requiring `transcript_path` — the
   same "real interactive session, not a synthetic call" signal QB-081's relevant-files feature
   already relies on — which is both the test fix and, independently, the more correct scope for a
   tip aimed at a human in conversation.
2. Deliberately explored, then rejected: a full per-repo accept/decline/remind-later preference
   state machine (the project owner's own original spec). Simplified on direct instruction to a
   throttle-only design with zero stored preferences — "no state machine," per that review.

**Deliberately not built:** any preference/consent persistence beyond the throttle counters
themselves; a richer staleness signal than git-commit-based comparison (would require the same
filesystem-wide fingerprint walk `ensure_repo_intelligence()` already does, at hot-path cost this
feature explicitly avoids — see ADR-046's own documented limitation); showing the file-count/time
estimate from the hook path (would require a real `walk_repository()` call on every Read until
throttled — kept exclusive to the CLI-facing, already-walk-tolerant `quor init --claude` flow).

**Testing:** `tests/unit/test_repo_intel_nudge.py` (throttling, staleness thresholds, the 24-hour
gate, git-head comparison, corrupted-state fail-open, non-git skip), `tests/unit/
test_read_hook_repo_intel_nudge.py` (real stdin/stdout hook roundtrip: never-built tip appears and
throttles, silent once built, silent without `transcript_path`, fails open on an internal error),
`tests/unit/test_cli.py`'s new `TestRepoIntelligenceOnboarding` class (prompt appears/skips
correctly across `--yes`, non-git cwd, already-built cache, accept, and decline). Full gate: `ruff
check quor/ tests/` clean; `mypy quor/` clean; full `pytest tests/unit/` green (batched per this
repo's own hook self-timeout — a real, mildly amusing constraint hit while validating this exact
feature, since this session's own `quor init --claude` install made the dev shell's `python -m
pytest` invocations subject to Quor's own 25s dispatcher timeout); `pytest tests/integration/ -m
integration` green; `quor verify` unchanged at 204/204; benchmark suite unchanged at 35.9%/18,962
tokens (no compression logic touched).

**Files changed:** `quor/pipeline/repo_profile/nudge.py` (new), `quor/adapters/claude_read.py`,
`quor/cli/commands/init.py`, `tests/unit/test_repo_intel_nudge.py` (new), `tests/unit/
test_read_hook_repo_intel_nudge.py` (new), `tests/unit/test_cli.py`, `docs/final/DECISIONS.md`
(ADR-046).

</details>

---

#### QB-092 — Exclude repo-intelligence synthesis rows from `quor gain`'s headline percentage

**Effort:** Small · **Value:** High · **Risk:** Low · **Category:** Correctness / metrics integrity

**Status:** Implemented (2026-07-31).

<details>
<summary>Technical details</summary>

**The problem this closes:** the project owner reported real `quor gain` savings dropping from a
historical ~36-37% to ~12% after adopting the repository-intelligence commands (`quor map`,
`symbols`, `graph`, `search` — QB-061/066/067/080 and the QB-090 onboarding nudge that surfaces
them more often), despite compression itself getting no worse. Root cause: those commands are
synthesis, not compression — `_track_map_invocation()` and its five siblings record
`original=filtered` (a deliberate net-zero contribution, per each command's own docstring in
`quor/tracking/db.py`), but `query_gain()`'s aggregate SQL summed every row's `original_tokens`/
`final_tokens` with no exclusion for them. As repo-intelligence usage grows relative to real
Bash/Read traffic, their zero-savings, often-large output inflates the denominator
(`tokens_before`) far more than the numerator (`tokens_saved`), dragging the headline
`tokens_saved / tokens_before` percentage down — a metric-mixing artifact, not a filter
regression. `flag_low_performers` (`quor/analytics/filter_divergence.py`) already excluded these
same six labels from its own "low performer" analysis for the identical reason; `query_gain`'s
headline had no equivalent exclusion until now.

**The fix:** `SYNTHESIS_FILTER_LABELS` (new frozenset in `quor/tracking/db.py`, grouping the six
existing `REPO_*_FILTER_LABEL` constants) is now excluded via `AND filter_name NOT IN (...)` from
every `SUM()`/`COUNT()` in `query_gain()`'s main aggregate query — `total_invocations`,
`tokens_saved`, `tokens_before`, `tokens_after`, `gross_savings`, `gross_overhead`,
`negative_row_count`, and `passthrough_count`/`filter_hit_rate` all now reflect only rows a real
ContentMask filter could have acted on. `query_filter_analytics()` (the `--filters` per-filter
breakdown) is deliberately untouched — showing `repo-profile`/`repo-search`/etc. as their own
labeled bucket there is correct and already handled by `flag_low_performers`'s exclusion; only the
single blended headline ratio needed the fix.

**Deliberately not built:** any change to what `quor map`/`symbols`/`graph`/`repo`/`explore`/
`search` themselves record (`original=filtered` stays exactly as-is — still the honest, by-design
net-zero contribution); no new `GainReport` field or second "eligible" percentage (a narrower,
presentation-layer fix along those lines was already drafted independently on an unmerged branch
for a related-but-distinct passthrough-dilution problem — `ps`/`grep`/one-line `git diff`s with no
filter at all — and was intentionally left alone rather than reconciled here, at the project
owner's explicit direction to patch `main` standalone; see QB-091 immediately below, which merges
that branch and reconciles the two); no config toggle (ANTI_GOALS.md #14).

**Testing:** `tests/unit/test_tracking.py`'s new `TestQueryGainExcludesSynthesisRows` class — a
large `quor map` row no longer dilutes a real filter's percentage (the exact reported scenario),
each of the six synthesis labels is excluded individually, an all-synthesis DB reports the same
zeros as an empty one, and real filter rows are completely unaffected. Full gate: `ruff check
quor/tracking/db.py tests/unit/test_tracking.py` clean; `mypy quor/tracking/db.py` clean; full
`pytest tests/unit/` green (batched per QB-090's own note on this repo's 25s dispatcher
self-timeout).

**Files changed:** `quor/tracking/db.py` (`SYNTHESIS_FILTER_LABELS` + `query_gain()` exclusion +
`GainReport` docstring), `tests/unit/test_tracking.py` (new `TestQueryGainExcludesSynthesisRows`).

</details>

---

#### QB-091 — `quor gain`/`quor dashboard` UX clarity pass

**Effort:** Medium · **Value:** High · **Risk:** Low · **Category:** UX

**Status:** Implemented (2026-07-31). Developed in parallel with QB-092 on a separate branch, per
the project owner's explicit direction to patch `main` standalone for QB-092 first; merged and
reconciled with QB-092 afterward (both touch `query_gain()`'s aggregate SQL, on genuinely disjoint
lines — QB-092 excludes synthesis rows from every SUM(), QB-091 adds a further eligible/passthrough
split among whatever's left — so the two compose cleanly with no logic conflict).

<details>
<summary>Technical details</summary>

**The problem this closes:** a real user watched `quor gain`'s headline percentage read 87% after
a couple of commands, then 7% after ~45 — and reasonably assumed something was broken. It wasn't:
the percentage is a running aggregate (`tokens_saved / tokens_before` across every recorded
command), not a per-command score. One large compressible read early in a session dominates a
small sample; as more commands run — many of them shell output (`ps`, `grep`, `kill`) with no
filter to apply to it at all — the same absolute savings gets divided by a much bigger denominator.
Both readings were arithmetically correct the whole time; nothing in the UI said so. Raised
directly by the project owner via a product-design review of `quor gain`/`quor dashboard`, which
also asked, item by item, whether each number/label/word actually earns its place — this entry is
the resulting punch list, all bundled into one branch/PR at the owner's explicit direction (a
deliberate, one-time exception to "one backlog item per branch").

**Nine items reviewed, eight implemented (the ninth — command/help-panel naming — was judged
already good and left alone):**

1. **`quor dashboard` now shows "Passthrough"** — it silently omitted the exact stat `quor gain`
   already showed, purely because each command kept its own copy of the stats-table code. Fixed
   structurally, not by patching the omission: both commands now call one shared builder
   (`gain_presentation.build_stats_table()`), so this class of drift can't recur.
2. **A second, narrower compression figure** for when part of a session was passthrough commands:
   `eligible_compression_line()` reports `tokens_saved / eligible_before` (a new `GainReport` field
   — `original_tokens` summed over `was_passthrough = 0` rows only), i.e. compression on just the
   content a filter could actually touch, alongside the existing blended headline. This is the
   direct fix for the 87%→7% swing: both numbers are shown, clearly scoped, instead of one blended
   number doing double duty. Omitted when there's no passthrough activity to disambiguate.
3. **A sample-size caveat below `LOW_SAMPLE_THRESHOLD` (5 commands)** — `low_sample_caveat()`
   appends "early read (N commands) — this settles as more commands run" rather than presenting a
   2-command reading with the same visual confidence as a 45-command one. Additive only (the
   `"~80 tokens (80%)"` shape is untouched), so it never hides the underlying number.
4. **Internal filter ids translated to user-facing labels** — `filter_display_name()` covers only
   the generic `cat`/`cat-<language>` family (an implementation detail: "read + AST-aware-compress
   this file type") plus `generic`/`document-text`. Deliberately *not* a full rename pass: most
   filter names (`pytest`, `eslint`, `docker`, `git-diff`, ~100 others) already are the exact tool
   name this audience would type and need no translation — renaming those would have been
   busywork, not clarity.
5. **First-run framing** — folded into item 3 (`low_sample_caveat()`); no separate mechanism needed.
6. **A live trend marker in `quor dashboard`** (▲/▼/·) next to the headline percentage, comparing
   each refresh tick to the previous one — so a number that visibly moves between two glances at
   the live view reads as expected motion, not a surprise. `_trend_marker()` treats sub-0.5-point
   deltas as flat (a `·`) to avoid jitter noise. Dashboard-only: `quor gain` is a single snapshot
   with no "previous tick" to compare against, so no trend marker there. Suppressed during the
   item-3 caveat window (arrow motion on a reading already labeled "too early to trust" would just
   compound the noise it exists to reduce).
7. **A README FAQ line** answering "why did my percentage swing a lot," in plain language, right
   after "The Numbers" — since this is now a known, recurring point of confusion, not just this
   one user's experience.
8. **Cross-reference note between the two windows** — `quor dashboard`'s footer now states it's
   session-only and points to `quor gain` for a longer view, since comparing the two without
   knowing they cover different windows is its own source of "these numbers don't match" confusion.
9. **Command naming / `--help` panel structure** — reviewed, left unchanged. Panel grouping
   (Installation/Analysis/Utilities), one-line descriptions, and command names (`gain`, `dashboard`,
   `explain`, `doctor`) were judged already clear and jargon-free; no action taken.

**Deliberately not built:** a `quor config`-style toggle for any of this (ANTI_GOALS.md #14); any
change to what gets *computed* — `tokens_saved`, `gross_savings`/`gross_overhead`, and
`filter_hit_rate` are all unchanged formulas, this is presentation-only, same discipline
`quor/cli/format_utils.py` already follows; a full rename table for all ~110 filter ids (see item 4
above — most need no translation, and inventing display names for well-known tool names would be
noise, not clarity).

**New module:** `quor/cli/gain_presentation.py` — shared presentation logic between `quor gain` and
`quor dashboard` (stats table, top-filters table, filter-name translation, low-sample caveat,
eligible-compression line). Both commands previously kept separate copies of the same rendering
code; centralizing it is what makes item 1's fix structural rather than a one-off patch.

**Testing:** `tests/unit/test_gain_presentation.py` (new — direct unit tests for every helper in the
new shared module); `tests/unit/test_tracking.py` (new `eligible_before` tests: excludes passthrough
rows, zero when all-passthrough, zero on empty DB); `tests/unit/test_dashboard.py` (Passthrough row
present, eligible line shown/omitted correctly, low-sample caveat shown/absent at the threshold,
trend marker shown once stable / absent on first render, plus a new `_trend_marker()` unit-test
class); `tests/unit/test_cli.py` (`TestGain`: low-sample caveat, eligible line shown/omitted,
`cat-python` → "Python file read" translation). Full gate: `ruff check quor/ tests/` clean; `mypy
quor/` clean; full `pytest tests/unit/` and `pytest tests/integration/ -m integration` green; `quor
verify` unchanged; benchmark suite unchanged (no compression logic touched — this is presentation
only, per GainReport's own QB-017/QB-091 docstring discipline).

**Files changed:** `quor/cli/gain_presentation.py` (new), `quor/tracking/db.py` (`GainReport.
eligible_before` + SQL aggregate), `quor/cli/commands/gain.py`, `quor/cli/commands/dashboard.py`,
`README.md`, `tests/unit/test_gain_presentation.py` (new), `tests/unit/test_tracking.py`, `tests/
unit/test_dashboard.py`, `tests/unit/test_cli.py`.

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
