# QB-061 — Repository Context Profile (`quor map`)

> Status: **Implemented (2026-07-28) on `feature/qb-061-repo-context-profile`.** Both §7 sign-offs
> (CLI-surface exemption, Phase-D-deferred phasing) were granted via the explicit implementation
> instruction that followed this design's delivery. See `docs/final/DECISIONS.md` ADR-037 for the
> architecture decision record and `backlog.md`'s `QB-061` entry for the implementation record —
> a small number of details below (e.g. exact module names) were refined during implementation;
> ADR-037 and the backlog entry are the authoritative record of what actually shipped where they
> diverge from this pre-implementation design. Produced per CLAUDE.md Rule 4 (competitor-first
> design) and per the 2026-07-28 task to identify the next major, non-filter capability. This
> document supersedes and formalizes `docs/design/repo-summarization-investigation.md` (same
> capability, prior working title) as the numbered backlog recommendation, and explicitly ruled
> out the sibling investigation, `docs/design/adaptive-multi-level-compression-investigation.md`,
> as the wrong class of work for this task (see §2).

---

## 1. Problem statement

Every Quor capability shipped to date — `strip_lines`, `group_repeated`, `code_ast_summarize`,
`structured_data_summarize`, the DOCX/PDF extraction path, and the mypy/ruff/generic
negative-compression fix that just shipped as QB-065 — compresses **one already-captured blob**:
one command's stdout, or one file's content. That is the entire addressable surface of the
ContentMask pipeline, and it is now well covered. `docs/design/evidence-based-priority-review-
2026-07-23.md`'s own live-telemetry audit (127-case benchmark corpus + 3,678 real invocations)
found no remaining gap in that surface worth calling "major": the largest live issue (mypy/ruff/
generic net-negative compression, 48% of real filtered traffic) was a correctness bug, not a
missing capability, and it has already been actioned (QB-065, merged).

The token cost Quor has never touched is a different one: **repository orientation.** When an AI
coding assistant starts work in an unfamiliar repo, it burns tool calls discovering the shape of
the codebase before it can act — a directory listing, three to six `cat`/Read calls on manifest
and config files, a `grep`/`find` for entry points, sometimes a `git log`. Every one of those calls
goes through Quor's existing filters today, but filtering an individual `cat pyproject.toml`
call cannot remove the redundancy that exists **across** that whole sequence — the fact that the
AI is reconstructing the same handful of facts (language, framework, build system, entry points)
that a single deterministic scan could produce once, up front. No amount of per-command
compression closes this gap, because it is a synthesis problem, not a redundancy-removal problem
on a single blob — structurally outside what `ContentMask`/`StageHandler` can do (see §2's
Anti-Goal analysis).

## 2. Why this should be built next

**It is the only remaining capability that is "major," not "small filter improvement," while
still satisfying every hard constraint (deterministic, local, no LLM, no cloud, reuse-first).**
Scored against the four candidates this investigation considered:

| Candidate | Verdict | Why |
|---|---|---|
| **Repository Context Profile (this doc)** | **Recommended** | New capability class (synthesis, not compression); directly attacks a real, currently-zero-coverage token cost (repo-orientation tool-call sequences); reuses six existing subsystems almost unchanged (§5); fully deterministic, local, no LLM. |
| Adaptive multi-level compression (`docs/design/adaptive-multi-level-compression-investigation.md`) | Rejected for *this* task | Its own findings show the ceiling is bounded and shallow: it only reaches 4 of ~11 stages, and its own §10 states Aggressive "should not be expected to look dramatically different from Safe on most real sessions." This is precisely the "small filter improvement" category the task explicitly says to stop looking for — it's a real, well-scoped idea, but it tunes existing knobs rather than adding new coverage. Keep on the backlog as QB-039, not as QB-061. |
| QB-052/ruff/mypy/generic negative-compression fix | Already done | This was the evidence-based review's top pick as of 2026-07-23, and it shipped as **QB-065** ("surface negative real-usage compression in quor doctor," already in `main`). Re-proposing it here would duplicate merged work. |
| QB-041/QB-055 (git-diff hunk-level compression) | Not "major" | Real value, but it's an incremental filter improvement to an already-positive filter (same excluded category as above), and its own backlog entry admits the hunk-grouping heuristic isn't fully deterministic yet — a design risk this task's "deterministic" constraint should avoid, not a reason to build it as the flagship item. |

**Competitive positioning (Rule 4):** this is not chasing an incumbent. RTK/Zap and Headroom AI
(`docs/archive/product-discovery/competitive-research.md` §1, Category A/B) only ever compress
individual command output — neither has anything like a repo-orientation profile. The closest
prior art is **Aider's repo map** (Category G): tree-sitter-derived function/type signatures
across the repo, PageRank-ranked to fit a token budget, kept continuously in context. That is a
different altitude and a different problem: Aider answers "what are all the symbols in this
codebase," continuously, for code-navigation. This design answers "what *is* this codebase"
(languages, frameworks, build system, package manager, entry points, infra) — a one-shot
orientation snapshot, evidence-labeled, not a standing symbol index. The two are complementary,
not overlapping, and Quor's own per-language AST parse trees (built for `code_ast_summarize`)
are exactly the substrate a future, later phase could extend toward Aider-style symbol listing
(§6, Phase D) — without needing to compete with Aider on that specific ground yet, since a
correctness-hardened orientation profile is the higher-value, lower-risk first step for Quor's
declared "deterministic, auditable" identity (ANTI_GOALS.md #10).

**Fit against ANTI_GOALS.md:** no LLM call (#2), no content-meaning invention beyond declared,
evidence-cited facts (#3, addressed head-on in §7's limitations rather than hidden), no telemetry
of output content (#4), no compiled dependency in the core path (#6, everything here is stdlib +
existing optional extras), fully auditable per-fact (#10 — every line traces to a source file and
pattern, mirroring `quor explain`'s existing promise).

## 3. User workflow

```
$ python -m qr map
# Deterministic scan of the current repo (git ls-files, or os.walk fallback).
# Prints a fixed-template Markdown profile to stdout:

## Repository Profile
Languages: Python (94%, 118 files), TOML (4%), Markdown (2%)
Build system: setuptools (pyproject.toml, PEP 621)
Package manager: pip (no lockfile detected)
Test framework: pytest (pytest.ini + 14 test_*.py files)
Frameworks: Typer (evidence: quor/cli/main.py, `import typer`)
Entry points: quor.__main__:main (pyproject.toml [project.scripts])
Infrastructure: GitHub Actions (.github/workflows/ci.yml, canary.yml)
Notable config: pyproject.toml, .github/workflows/*.yml

(Every fact above is evidence-cited: run `qr map --explain` for the file/pattern
that produced each line. Detail is Python-only in this install — javascript/go/
rust/java/csharp AST-level symbol detail needs `pip install "quor[<language>]"`.)
```

- **Invocation:** the AI (or the user) runs `qr map` the same way it runs any other Bash tool
  call today — no new hook wiring, no session-context reading (Anti-Goal #13 stays untouched).
- **When:** naturally the first tool call in an unfamiliar repo, in place of the manual
  discovery sequence described in §1 — Quor does not intercept or auto-substitute this (see
  §7's rejected Option 2); the AI (or a project's own `CLAUDE.md`/onboarding instructions) chooses
  to run it.
- **Cost visibility:** the invocation is tracked through the existing `count_tokens()`/
  `track_invocation()` path exactly like any other filtered command, so it shows up in `quor gain`
  with the same `±20%` uncertainty labeling (Anti-Goal #24) as everything else.
- **Trust:** `qr map --explain` (or a per-line `[source: pyproject.toml, pattern: [tool.poetry]]`
  suffix in the default output) gives the same auditability `quor explain` already guarantees for
  compression decisions, applied here to detection decisions instead.

## 4. Architecture

```
quor/pipeline/repo_profile/          (new package, parallel to pipeline/'s ContentMask path,
                                       not inside it — see §2's "synthesis, not compression"
                                       framing; this is the single most important architectural
                                       finding underlying this whole design)
  __init__.py
  walk.py            -- deterministic file enumeration (git ls-files, os.walk fallback)
  detectors/
    registry.py       -- three-tier TOML rule registry (project > user > builtin),
                          structurally identical to FilterRegistry
    builtin/*.toml     -- declarative marker-file/content-pattern -> fact rules
  manifests.py         -- package.json / pyproject.toml / Cargo.toml / go.mod / *.lock field
                          extraction, reusing structured_data's existing parse calls
  symbols.py            -- per-language entry-point/framework-signal extraction, one parse
                          per file, sharing the same tree code_ast_summarize's analyzers
                          already build (Phase D — see §6, deliberately sequenced last)
  model.py              -- RepoProfile, frozen Pydantic model
  render.py             -- RepoProfile -> deterministic Markdown, every line traceable
```

Why parallel and not inside `ContentMask`: `ContentMask.from_text()` takes one string;
`StageHandler.apply()` only ever downgrades `KEEP` lines; Anti-Goal #18 rules out any stage that
receives one blob and returns a *different, synthesized* document. Repository profiling reads
**many** files and produces a document that never existed verbatim anywhere in the repo — that is
categorically different from everything `StageHandler` is built to do, and forcing it in would be
a bigger meaning-change risk than anything Quor does today. `ContentMask` re-enters the picture
only optionally, at the very end (§4.1), compressing Quor's own generated artifact if it's large —
never repo source.

### 4.1 Optional self-compression

If the rendered profile is itself large (a genuinely huge polyglot monorepo), it can be piped
through the existing `markdown` filter path exactly the way DOCX/PDF-extracted text already is
(`claude_read.py::_compress_via_named_filter`) — the one legitimate re-entry point for
`ContentMask` in this design.

## 5. Existing Quor components that can be reused

| Component | Reuse |
|---|---|
| `filters/registry.py` + `filters/loader.py` + `filters/trust.py` | Pattern (not code) reused directly: three-tier TOML loading, git-tracked trust verification for project-local detector rules — same `is_git_tracked()` call. |
| `ast_summarize/registry.py` + per-language modules | Registry pattern reused directly (lazy-imported optional deps, `is_language_available()`/`extra_for_language()` fail-open introspection). Each language module gains one additive `extract_symbols()` function that reuses the **same parsed tree** the existing compression analyzer already builds — zero risk to `code_ast_summarize`/`python_ast_summarize`, zero double-parsing. |
| `structured_data/json_fmt.py` / `toml_fmt.py` / `yaml_fmt.py` | The underlying stdlib/PyYAML parse calls (`json.loads`, `tomllib.loads`, `yaml.compose`) are reused for manifest field extraction — one parser per format, not a second one. |
| `pipeline/extract/registry.py` | `extract(path) -> str | None` reused **unchanged** for `.docx`/`.pdf` design docs found during the walk. |
| `analytics/` (`count_tokens`, tracking conventions) | `count_tokens()` and the `±20%` uncertainty-labeling convention (Anti-Goal #24) reused for `quor gain` visibility; `track_invocation()` gives a repo-map run a row like any other invocation. |
| `_STRUCTURED_DATA_FILTER_NAMES_BY_BASENAME` (`claude_read.py`) | Already encodes which lockfile basename implies which package manager/format — extended, not reinvented, for package-manager detection. |
| `cat-*.toml` extension patterns / `claude_read.py`'s extension tables | Confirms an implicit extension→language table already exists, scattered across two consumers — this is the trigger to consolidate it into one shared table both the AST filters and the new detector registry read from. |

## 6. New components required

- **`walk.py`** — `git ls-files --cached --others --exclude-standard` primary path (deterministic,
  `.gitignore`-respecting, mirrors `trust.py`'s existing subprocess-to-git pattern); `os.walk` with
  a small hardcoded skip-set as the no-git fallback.
- **Detector registry + built-in rules** (`detectors/registry.py`, `detectors/builtin/*.toml`) —
  new three-tier TOML format matching file paths/basenames/content regex to facts (language,
  framework, build system, package manager, test framework, infrastructure, configuration
  categories).
- **`manifests.py`** — field extraction (`dependencies`, `scripts`, `[project.scripts]`, etc.) for
  the handful of high-value manifest formats, built on the reused parse calls above.
- **`symbols.py` + per-language `extract_symbols()`** — entry-point/framework-signal extraction
  (Phase D, sequenced last — materially larger scope than the rest combined, see §9).
- **`model.py` / `render.py`** — `RepoProfile` (frozen Pydantic model) and its fixed-template,
  no-invented-prose Markdown renderer.
- **CLI command** — `quor map` (needs the sign-off in §7).

## 7. Risks

1. **The CLI-surface question is a hard process gate, not a style choice.** CLAUDE.md is explicit:
   "V1 has exactly 6 [commands]... don't add more without explicit approval." `quor schema` is the
   one documented precedent (a non-filtering utility command). **This design requires the same
   exception for `quor map`, and that requires the user's explicit sign-off before any CLI code is
   written** — this is not assumed granted by this document. Two rejected alternatives are worth
   naming explicitly: silently rerouting an existing exploratory command (e.g. substituting the
   profile for the AI's first `find .`) is rejected outright — it changes what a real command's
   output means, a bigger trust violation than anything Quor does today (Anti-Goal #3 is about
   *removing* verified-redundant content, never substituting different content for what was asked).
2. **Trust asymmetry vs. every other Quor feature.** Compression and extraction never add claims
   about content; this synthesizes new claims about the repo (a "detected framework" is an
   inference, not a copy). Mitigated structurally by requiring every fact to carry its evidence
   (file + pattern) — the same transparency bar `quor explain` already sets — but this is a
   meaningfully bigger ask of user trust than anything shipped today, and should be named as such
   rather than glossed over.
3. **Heuristic, not authoritative.** A stale `requirements.txt` from a removed dependency, or a
   leftover `package.json` from a deleted frontend, produces a false positive. Mitigated the same
   way: every fact is evidence-cited so the AI/user can judge confidence rather than trust a bare
   assertion.
4. **Large-repo scaling.** File enumeration and marker detection are cheap at scale; per-file AST
   symbol extraction (Phase D) is not, and needs an explicit cap/sampling strategy plus its own
   performance budget before it ships — this is new scaling territory Quor's existing
   single-file-at-a-time pipeline has never had to reason about.
5. **Staleness.** No watch mode exists or is planned (Anti-Goal #11). A generated profile is a
   snapshot; a silently stale *synthesized* summary is a worse trust failure than a silently stale
   compression (no original for the AI to notice is missing), so re-generation needs to be cheap
   enough to run often rather than cached indefinitely.
6. **Optional-dependency fragmentation.** Full symbol-level detail (Phase D) depends on the same
   optional tree-sitter extras AST summarization already gates on — a plain `pip install quor` gets
   full fidelity for Python only. Must be stated in the output itself, not silently thin.

## 8. Benchmark strategy

Cannot reuse `tests/benchmarks/manifest.toml` as-is — that harness measures compression ratio
against a captured "before" blob, and there is no "before" here. Parallel structure:

- **Fixture repos, not fixture files** — small synthetic directory trees under
  `tests/benchmarks/samples/repo-profile/<case-name>/` (a minimal Flask+pip project, a Node/Express
  project with a pnpm lockfile, a Go service, a deliberately polyglot monorepo).
- **Correctness = precision/recall against hand-labeled expected facts** (`expected_languages`,
  `expected_frameworks`, `expected_build_system`, `expected_entry_points`, plus `must_not_detect`
  for false-positive checks) — the detection-quality mirror of the existing `must_contain`/
  `must_not_contain` checks, and the primary signal (not a reduction percentage).
- **Determinism check** — run twice against the identical fixture, assert byte-identical output.
  This is the feature's core promise and costs nothing to verify continuously.
- **Token-size ceiling** (secondary) — cap expected output tokens per fixture size tier, tracked
  as an upper bound the same way `baseline.json` tracks compression-ratio drift as a floor.
- **Performance budget** — this is an explicit, user-invoked command, not hook-path code, so it
  does not inherit the `<10ms` hook budget; more like `quor doctor`/`quor gain`'s multi-second
  tolerance. Needs an explicit target (e.g. `<2s` for a 5,000-file repo) validated against a large
  synthetic fixture — unbounded per-file AST parsing (Phase D) is the obvious scaling risk to
  budget against up front.

## 9. Success metrics

Per Anti-Goal #24/#25 (uncertainty labeling; no AI-quality claim without evidence), these are
measurement targets to validate, not numbers to publish before they're measured:

- **Detection precision/recall** on the fixture-repo corpus (§8) — the primary, load-bearing
  metric; a wrong or incomplete profile just adds AI follow-up calls back (net negative), so this
  gates everything else.
- **Determinism** — 100% byte-identical output across repeat runs on the same repo state
  (non-negotiable, cheap to verify).
- **Real-session token comparison** (measured via the existing `quor gain`/tracking infrastructure,
  once shipped) — token cost of one `quor map` call vs. the token cost of the manual-discovery
  tool-call sequence it replaces, on real sessions. This is the number that actually validates the
  hypothesis in §1, and per Anti-Goal #24 must carry the same `±20%` labeling as every other Quor
  savings figure — no number gets published until this is measured against real usage, exactly the
  discipline the 2026-07-23 evidence review already applied to every other filter.
- **Adoption signal** — whether real sessions that run `quor map` show a measurably shorter
  discovery-call sequence afterward (fewer immediate follow-up `cat`/`find`/`grep` calls) — the
  closest available proxy for "the AI didn't need to re-verify," short of a controlled task-success
  study (which Anti-Goal #25 already says Quor cannot claim without real evidence).

## 10. Complete implementation plan

Each phase gets its own feature branch and backlog entry per CLAUDE.md's "Starting Any Backlog
Item" sequence, once the §7 sign-offs are given — this document is the Rule-4 pre-approval
artifact, not a substitute for that process.

| Phase | Deliverable | New code | Depends on |
|---|---|---|---|
| A | `walk.py` — deterministic file enumeration | `quor/pipeline/repo_profile/walk.py` | `git ls-files` (already used by `trust.py`) |
| B | Detector registry + built-in rules (language/build-system/package-manager/test-framework/infra) | `detectors/registry.py`, `detectors/builtin/*.toml`, consolidated extension→language table | Phase A |
| C | Manifest field extraction | `manifests.py` | Phase B; reuses `structured_data`'s parse calls |
| D | Per-language symbol/entry-point extraction (largest single phase — may reasonably ship as its own later follow-up rather than blocking the rest) | `extract_symbols()` per `ast_summarize/<lang>.py`; `symbols.py` orchestrator | Phase B; additive to `ast_summarize/` |
| E | `RepoProfile` model + deterministic Markdown renderer | `model.py`, `render.py` | Phases B–D |
| F | Optional post-compression of the generated artifact | wiring into existing `markdown`/`structured_data_summarize` filters | Phase E |
| G | CLI exposure (**blocked on §7 sign-off #1**) | `quor map` command, `track_invocation()` wiring for `quor gain` | Phase E; separate approval gate |
| H | Benchmark harness | fixture repos + precision/recall harness (§8) | Phases A–E |

**Two explicit decisions needed from the user before Phase A starts implementation** (not before
this design is delivered):
1. **CLI-surface sign-off** — approve `quor map` as a 7th, exempted utility command (same category
   as `quor schema`), per §7 risk 1.
2. **Phasing sign-off** — confirm Phase D (symbol/entry-point extraction) should ship as a later,
   separately-scoped follow-up rather than blocking Phases A–C/E–H's initial release, given it is
   materially larger than every other phase combined.
