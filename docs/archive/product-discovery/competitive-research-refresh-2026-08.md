# Competitive Landscape Refresh — 2026-08-19 (QB-086)

> Scope note: this is a dated addendum, not a rewrite. The original document
> (`competitive-research.md`) was written as pre-implementation due diligence for a project then
> called "Distill," before a line of Quor's code existed. It is preserved unedited for historical
> record. Quor has since shipped (v0.5.0+, MCP-native since QB-104, 12 CLI/utility commands, repo
> intelligence, AST summarization for 7 languages). This addendum re-verifies the competitive
> landscape against **today's actual state** — both the market's and Quor's own — and supersedes
> the original document's Sections 1-11 for any current positioning or roadmap decision. Section 12
> ("Brutally Honest Verdict") and the naming discussion remain historically interesting but moot —
> the project shipped as Quor, not any of the candidate names discussed there.
>
> **Why this exists:** `backlog.md`'s QB-086 entry found the original research materially stale in
> specific, checkable ways, and flagged that different AI-assisted searches on this fast-moving,
> low-visibility part of GitHub had already produced *conflicting* competitor facts (a Headroom AI
> star count anywhere from 29.5K to 37K, depending on source) — with an explicit instruction that
> every figure be re-verified directly, not taken on any single search's word. This addendum does
> that: every number below was pulled live from the GitHub REST API (`api.github.com/repos/...`) on
> 2026-08-19, not scraped from a blog post or aggregator.

---

## 1. Verification Ledger

Every competitor figure below is a direct `GET /repos/{owner}/{repo}` API call made today
(2026-08-19). This resolves the "Independent verification needed" flag the QB-086 backlog entry
itself raised.

| Repo | Stars | Forks | Open issues | License | Language | Created | Last push |
|---|---|---|---|---|---|---|---|
| `rtk-ai/rtk` | 76,641 | 4,821 | 1,975 | Apache-2.0 | Rust | 2026-01-22 | 2026-08-19 |
| `headroomlabs-ai/headroom` | 66,863 | 5,146 | 497 | Apache-2.0 | Python (78.7%) / Rust (16.8%) | 2026-01-07 | 2026-08-19 |
| `yvgude/lean-ctx` | 3,600 | 328 | 8 | Apache-2.0 | Rust | 2026-03-23 | 2026-08-19 |
| `alexgreensh/token-optimizer` | 1,930 | 156 | 2 | PolyForm Noncommercial 1.0 (no OSI license) | Python | 2026-02-26 | 2026-08-19 |
| `JuliusBrussee/caveman` | 99,161 | 5,752 | 352 | NOASSERTION (no LICENSE file) | Go | 2026-04-04 | 2026-08-19 |

**Corrections to the record this resolves:**

- **Headroom AI's star count discrepancy is resolved: 66,863, not 29.5K or 37K.** Both secondary
  figures the prior research cycles picked up (DevShelfHub's 29.5K, the original research's 37K)
  were stale or wrong at the time they were quoted. The project has grown roughly 2.2x past the
  *higher* of those two figures. This matters directly for QB-035/QB-068/QB-069's framing: Headroom
  is not "the sophisticated Python alternative," it is now within 15% of RTK's own star count and
  should be treated as a co-equal dominant incumbent, not a secondary player.
- **RTK grew from 67,177 → 76,641 stars** since the original research — still the single largest
  project in this space by a narrow margin over Headroom, still shipping near-continuously (pushed
  today).
- **All three "new entrant" repositories QB-086 named (LeanCTX, Token Optimizer, Caveman) are real,
  active, and independently confirmed** — none were a hallucinated or conflated search result. The
  backlog's own caution ("a different AI web search named a different set of new entrants —
  `context-compress`, `Token Optimizer MCP`") is worth taking at face value: this addendum did not
  independently find those two alternate names as distinct projects, and does not add them here
  without direct confirmation. Treat them as unresolved, not as additional competitors, until
  someone finds and verifies the actual repositories.
- **Caveman's star count (99,161) is the highest of any tool compared, including RTK** — verified,
  not a scraping artifact (`SkillsLLM`'s 98.8K figure was directionally correct). This is a genuine,
  surprising data point: the single most-starred project in this entire competitive set is a
  response-compression joke-branded skill, not a rule-based CLI interceptor. See §4 for why this
  doesn't change Quor's roadmap despite the scale.

---

## 2. What Changed Since the Original Research

### RTK — still dominant, Windows still not solved the way the README implies

RTK's own repository description still reads "60-90% on common dev commands," unchanged in spirit
from the original research. What changed: **Windows hook support is still not native.** A live
search of RTK's own issue tracker (`#1864`, opened 2026-05-13, open as of today) confirms
`rtk init --global` on Windows still falls back to `--claude-md` mode — injecting instructions into
`CLAUDE.md` asking the model to voluntarily run commands through RTK, rather than deterministically
intercepting them via a real PreToolUse hook. This is a materially weaker guarantee than the
original research's blunt "Windows: unsupported" — RTK now *runs* on Windows — but the actual
compression mechanism on Windows is compliance-based, not deterministic. A model that ignores or
forgets the CLAUDE.md instruction gets uncompressed output with no warning to the user. **This is
the single most important correction to make in any Quor-vs-RTK positioning claim**: the honest
comparison isn't "Quor works on Windows, RTK doesn't" (RTK does run), it's "Quor's compression is a
deterministic MCP tool call on every platform; RTK's Windows compression is a hope that the model
reads its instructions file."

### Headroom AI — grown into infrastructure, Windows compilation still blocks pip install

Confirmed via direct search of Headroom's own issue tracker: **no prebuilt Windows wheels exist**
(current release wheels cover Linux `manylinux_2_28` and macOS Apple Silicon only). `pip install
headroom-ai` on Windows falls back to building the `headroom._core` Rust extension from source,
which requires the MSVC linker (`link.exe`) on `PATH` — a live open bug (`#636`) reports this
failing under `uv` with "linker `link.exe` not found," i.e. exactly the corporate-Windows,
no-admin-rights, no-Visual-Studio scenario Quor's own `docs/final/CLAUDE.md` names as its primary
target user. **This directly answers the original research's own Pre-Flight Checklist item #1**
("`pip install "headroom-ai[all]"` on the target Windows machine — does it install without
compilation?") — the answer, as of today, is no, unless a prebuilt wheel happens to exist for the
exact Python/platform combination in use. This closes the open question the original research
explicitly left as a blocker before deciding whether Quor should exist at all.

Architecturally Headroom has grown as the original research anticipated: library, proxy,
`headroom wrap <agent>` for Claude Code/Codex/Cursor/OpenCode, and an MCP server exposing
`headroom_compress`/`headroom_retrieve` tools — the closest thing to a direct MCP-surface
competitor to Quor's own `compress_context`/`get_repo_context`.

### Three new entrants, none present in the original research

**LeanCTX** (`yvgude/lean-ctx`) — a local Rust binary, "context intelligence layer," positioned
almost identically to RTK's original pitch but younger (created 2026-03-23) and smaller (3,600★).
Notable capabilities beyond RTK's scope:
- **10 read modes** (`full`, `map`, `signatures`, `diff`, `lines:N-M`, `density:X`) — tiered,
  on-demand disclosure of file content rather than one fixed compression pass. `density:0.4` targets
  a specific compression ratio directly, rather than a filter author choosing rules and observing
  whatever ratio results.
- **76 MCP tools** across 30+ agents — a much broader MCP surface than Quor's 2 tools or Headroom's
  2 (`headroom_compress`/`headroom_retrieve`).
- **270 passthrough rules** for shell tool output (git, npm, cargo, docker, kubectl, terraform) —
  roughly comparable in spirit to Quor's built-in filter set, though not directly size-comparable
  without running both against the same corpus (see QB-042).
- **Tree-sitter AST support for 27 languages**, against Quor's 7 (Python built-in; JS/TS/Go/Rust/
  Java/C# via extras). This is a real, quantified coverage gap, not just "different scope."
- **Windows install story is genuinely better than RTK's or Headroom's**: a prebuilt binary ships
  via `npm i lean-ctx-bin` (postinstall downloads the correct platform binary, SHA-256 verified), so
  a Windows user isn't forced through Rust compilation the way Headroom's Windows path currently is.
  It is still a compiled-binary dependency in the sense Quor's own anti-goal #6 forbids for Quor
  itself, but from a *user's* install-experience perspective it's a materially smoother Windows path
  than either RTK or Headroom offer today. Worth stating plainly rather than only defending Quor's
  own position.

**Token Optimizer** (`alexgreensh/token-optimizer`) — smallest of the three new entrants (1,930★),
Python, no OSI-recognized license (PolyForm Noncommercial — free for personal/research/education,
commercial use requires a paid license; this alone rules it out as something Quor could ever fork or
meaningfully draw code from). Its framing — "find the ghost tokens, survive compaction, avoid
context quality decay" — targets the *cross-session* problem (repeated re-reads of the same file
across turns, context surviving a compaction event) that Quor has never addressed and already tracks
as its own unbuilt **QB-043**/**QB-089**. This is independent market validation that the problem
space QB-043 targets is real and being built by someone else right now, not just an internal theory
— exactly the framing QB-086's original finding already gave it. Nothing in this refresh changes
QB-043's own scoping; this is a confirmation, not new information about *how* to build it.

**Caveman** (`JuliusBrussee/caveman`) — by far the most-starred project in this entire comparison
(99,161★, ahead of RTK), Go, no OSI-recognized license. Mechanically distinct from every other tool
in this document: it does not filter *tool output* before it reaches the model. It rewrites the
*model's own generated response* into compressed "caveman" prose (dropping articles, filler,
pleasantries — claimed ~65-75% reduction) before that response is displayed/logged, plus a separate
skill that compresses project memory files (`CLAUDE.md`, todos) by ~46%. See §4 for the explicit
recommendation not to pursue this mechanism.

### Platform-native shift — already tracked separately as QB-087

Anthropic's `compact-2026-01-12` beta header (server-side conversation-history summarization,
Opus 4.6/Sonnet 4.6) and prompt caching are real and unchanged from QB-086's original finding. Not
re-litigated here — QB-087 already owns the positioning writeup for this. One addition worth
folding into QB-087 when it's scoped: since Quor moved to MCP (QB-104), "pre-emptive vs reactive"
is an even cleaner distinction to draw than it was under the old hook model — Quor's compression
happens at the moment the assistant chooses to call a tool, strictly before that content is ever
counted against context or billed, where compaction only ever acts on content already fully paid
for once.

---

## 3. Quor's Current Position — Restated Against Verified Facts

The original research (§8, §12) evaluated a *pre-implementation plan*. Quor's actual, shipped
architecture differs from that plan in ways that change the competitive picture:

| Pillar | Quor today | RTK | Headroom AI | LeanCTX | Token Optimizer | Caveman |
|---|---|---|---|---|---|---|
| Zero-loss / correctness contract | **ADR-031**: `PROTECT` is absolute, `max_tokens` best-effort — a written, tested invariant | No published correctness contract; "60-90%" is the only public claim | No published correctness contract | No published correctness contract | No published correctness contract | Explicitly lossy by design (paraphrase, not filter) |
| Compiled-binary dependency | **None** (anti-goal #6) — pip-only, every dependency ships Windows x64 wheels | Rust binary, single-file | Python + Rust core (PyO3); Windows has no prebuilt wheel, requires MSVC | Rust binary; Windows path uses a prebuilt npm-distributed binary (better than RTK/Headroom, still compiled) | Pure Python | Go binary |
| Windows compression path | Deterministic MCP tool call, identical on every OS | Native binary runs, but hook mode falls back to CLAUDE.md compliance-injection (issue #1864, open) | `pip install` currently fails to a from-source Rust build requiring MSVC (issue #636, open) | Prebuilt binary via npm, no compile step | Pure Python hook, no compile step | Native binary; operates on response text, not tool output |
| Integration surface | MCP-native (QB-104) — any MCP client, explicit tool calls | PreToolUse hook injected per-agent (14 agents) | Library / proxy / agent-wrap / MCP server | 76 MCP tools across 30+ agents; also hook-based | Hook scripts across 6 platforms | Claude Code/Codex plugin, Gemini extension, agent rule files |
| Recoverability of compressed content | **tee recovery cache**, `[full output: ...]` link on every compressed output | Tee mechanism, similar in spirit | **CCR** — marketed headline feature, reversible compression with local cache | Not confirmed in available material | Not confirmed in available material | N/A (nothing is "recovered" — the rewritten prose is the only output) |
| External telemetry | **None, ever** (anti-goal #5) — stated absolute in README | Ships its own analytics/quota modeling (SQLite, subscription-tier tracking) | Not confirmed | Not confirmed | Local SQLite session DB, explicitly no network calls (privacy doc confirms) | Not confirmed |
| Repo-wide structural intelligence | `quor map`/`symbols`/`graph`/`explore` — symbol index + dependency graph, 7 languages | None (command-output filtering only) | AST-aware code compression (`CodeCompressor`), not a persisted repo-wide graph | Tree-sitter AST, 27 languages, tiered read modes (`signatures`, `density:X`) — broader language coverage and richer read-mode surface than Quor's own repo intelligence | None | None |
| Written anti-goals / stated non-negotiables | **Yes** — `ANTI_GOALS.md`, 24 explicit commitments, publicly documented | Not published as a distinct document | Not published as a distinct document | Not published as a distinct document | Not published as a distinct document | Not published as a distinct document |

**The headline correction from the original research:** the original "Distill" plan's core
differentiator was "Windows-first, pip-installable, zero-ML." That is still true and still
uncontested — no competitor has actually closed it, verified today, not assumed — but it is no
longer *only* a platform-availability claim. Since QB-104, Quor's MCP-native architecture sidesteps
the entire "does this hook fire reliably on Windows" question that RTK, Headroom, and (to a lesser
extent, given their better binary-distribution story) LeanCTX are all still individually fighting.
An MCP tool call either happens or it doesn't — there's no injected-instruction compliance gap, no
missing linker, no fallback mode. That is a stronger, more precise claim than "works on Windows,"
and it's one the original research couldn't have made because it predates QB-104's architecture
decision entirely.

---

## 4. Opportunity Analysis — Refreshed

**Still uncontested (reconfirmed, not just carried forward):**
1. **Zero-compiled-dependency, pip-only, MCP-native middleware.** Directly reverified today: RTK
   (Rust binary + Windows compliance-injection fallback), Headroom (Rust core, Windows compile
   failure, live bug), LeanCTX (Rust binary, better-but-still-compiled Windows path), Caveman (Go
   binary) all carry a compiled-binary dependency Quor's own anti-goal #6 forbids. Token Optimizer is
   the only pure-Python peer found in this refresh — worth a closer, direct feature comparison next
   time this document is revisited, since it's the one competitor that doesn't fail this test.
2. **Written, testable correctness contract (ADR-031) as a public claim.** No competitor publishes
   an equivalent. This is cheap to state in customer-facing copy and currently true.
3. **CCR-equivalent parity (tee).** Already flagged in QB-086's own marketing-parity note — restate
   here as confirmed still-open, cheap copy work, not yet done.

**New, concrete candidate surfaced by this refresh:**
4. **Tiered/partial-disclosure read modes**, modeled on LeanCTX's `signatures`/`density:X`/`lines:N-M`
   modes. Quor's `get_repo_context` MCP tool currently returns one fixed shape (language, exported
   symbols, import counts, relevant files). LeanCTX's read-mode surface suggests real, validated
   demand for a caller to ask for *less* than the full compressed output on purpose — e.g. "just the
   function signatures in this file" rather than a full AST-summarized body. This is compatible with
   Quor's own constraints (a caller-selected mode is an explicit parameter, not a heuristic guess;
   see `feedback_no_heuristic_fields` — the mode is chosen by the calling assistant, not inferred by
   Quor), doesn't require a new CLI command (an MCP tool parameter, not a 13th exempted command), and
   doesn't touch the ContentMask pipeline's correctness contract. **Spun out as a new backlog
   candidate: QB-111** (see `backlog.md`).

**Reconfirmed, not new (already tracked):**
5. **Cross-session/compaction-survival tracking (QB-043/QB-089).** Token Optimizer is now-live,
   independent market validation this problem space is real. No change to QB-043's own scoping.
6. **Native-compaction positioning (QB-087).** Unchanged; still proposed, not yet written.

**Explicitly not worth pursuing (a new finding, not in the original research):**
7. **Response-side compression (Caveman's mechanism), despite its 99K-star scale.** Caveman rewrites
   the model's *own* generated prose — a lossy paraphrase, not a deterministic keep/drop decision on
   content the model didn't generate. This directly conflicts with three of Quor's own anti-goals:
   #3 (never silently modify content meaning), #9 (never optimize compression ratio at the expense of
   correctness), and #10 (never sacrifice transparency — a paraphrase has no `quor explain`-style
   trace back to "why was this word dropped"). Caveman's star count is a genuine, useful signal that
   raw market appetite for "less text" is larger than the rule-based-filtering category alone
   suggests — but it validates that *token reduction broadly* is a category users want, not that
   *this specific mechanism* is one Quor should adopt. Worth citing as market-size evidence in
   positioning material; not worth building.

---

## 5. Positioning vs. Each Competitor (Refreshed)

| Competitor | Verified 2026-08-19 | Why choose Quor instead |
|---|---|---|
| **RTK** (76,641★) | Windows hook mode still falls back to CLAUDE.md compliance-injection (issue #1864, open) | Quor's MCP tool call is deterministic on every OS — no injected-instruction compliance gap. No compiled-binary dependency. Published correctness contract (ADR-031); RTK publishes a compression-ratio claim with no companion guarantee. |
| **Headroom AI** (66,863★ — corrected from 29.5K-37K secondary-source figures) | `pip install` on Windows currently falls back to a from-source Rust build requiring MSVC (`link.exe` not found, issue #636, open, live today) | Quor installs with zero compilation on any platform, verified against Quor's own no-compiled-dependency anti-goal. Quor's tee recovery cache is functionally equivalent to Headroom's marketed CCR headline feature — parity, stated plainly rather than left undocumented. |
| **LeanCTX** (3,600★, newest major entrant) | Best Windows install story of the three Rust/Go competitors (prebuilt npm binary, SHA-256 verified) — still a compiled dependency | Quor has zero compiled dependency at all, not just a smoother one. LeanCTX's 27-language AST coverage and tiered read modes are real, ahead-of-Quor capabilities worth tracking (§4, QB-111) — not dismissed, but not yet matched. |
| **Token Optimizer** (1,930★, PolyForm Noncommercial) | Pure Python, no compiled dependency — the one competitor that clears Quor's own anti-goal #6 | Targets a different, complementary problem (cross-session/compaction survival, QB-043's space) rather than per-call output filtering — not a like-for-like substitute for Quor's core pipeline. Licensing (commercial use requires payment) is itself a differentiator Quor's Apache-2.0 doesn't share. |
| **Caveman** (99,161★, highest of any tool compared) | Different mechanism entirely — rewrites the model's own response, not tool output | Not a substitute for Quor: operates on the wrong side of the pipeline (post-generation, not pre-context) and is explicitly lossy/unexplainable by construction, which Quor's anti-goals rule out. Cited here only as evidence the broader token-reduction market is larger than the rule-based-filtering category alone. |

---

## 6. What This Refresh Does Not Resolve

Carried forward, unchanged, from the original QB-086 finding — not addressed by this pass:

- **QB-087** (native-compaction positioning writeup) — still proposed, not written.
- **QB-042** (automated head-to-head benchmarking against a competitor's own corpus) — still
  proposed, not scoped. This refresh gives QB-042 a corrected, verified competitor list (RTK,
  Headroom AI, LeanCTX at minimum — see `backlog.md`'s QB-042 entry for the update) but does not
  build the harness itself.
- **The core unproven hypothesis** ("filtering improves AI task quality") from the original
  research's §5 is still unproven — no competitor in this refresh, including the two that grew
  past 60-70K stars, publishes a controlled study. Adoption scale is not quality evidence. This
  remains true and worth restating whenever compression numbers are cited publicly.
