# Investigation: Deterministic Repository Summarization

> Status: **superseded — formalized and implemented as QB-061.** See
> `docs/design/QB-061-repo-context-profile.md` (the numbered backlog recommendation) and
> `docs/final/DECISIONS.md` ADR-037 (the architecture decision + what actually shipped, which
> diverges from this document in a few scoped-down places — e.g. no tree-sitter symbol extraction
> in the shipped v1). Kept here for its original reuse-audit detail, not as the current source of
> truth for what exists.
>
> Original status line (2026-07-28, before QB-061 existed): "investigation only — no backlog item
> assigned, no code written." Produced per CLAUDE.md Rule 4 (competitor-first design): existing
> infrastructure surveyed first, reuse identified, gaps called out, recommendation presented for
> approval before any implementation begins.

---

## 1. Framing: this is a new capability class, not a new filter

Every existing Quor capability — `strip_lines`, `group_repeated`, `code_ast_summarize`,
`structured_data_summarize`, the DOCX/PDF extraction path — shares one shape:

> **Given one already-captured text blob (one command's stdout, or one file's
> content), remove verified-redundant lines from it. Never invent content. Never
> look outside that one blob.**

That shape is enforced structurally: `ContentMask.from_text()` takes a single
string, `StageHandler.apply()` only ever downgrades `KEEP` lines, and Anti-Goal
#18 ("no string→string transform pipeline... a stage that receives a string and
returns a modified string is architecturally wrong") plus Anti-Goal #3 ("never
silently modify content meaning") together rule out a `StageHandler` that reads
one file's lines and writes back a *different, synthesized* document.

Repository summarization is different in kind: it reads **many** files, extracts
**facts** from each, and **synthesizes a new document** that never existed
verbatim anywhere in the repo (a "detected languages" table, an "entry points"
list). That's synthesis, not compression. It does not fit inside `StageHandler`/
`ContentMask` — trying to force it in there (e.g., a stage that "compresses" a
`find .` listing down to a summary) would be answering a different question with
someone else's output, which is a bigger meaning-change risk than anything Quor
does today.

**Conclusion:** this belongs as a new, parallel pipeline that sits *beside*
the ContentMask pipeline, not inside it — reusing ContentMask only at the very
end, optionally, to compress its own generated output if that output is itself
large (see §3.6).

This reframing is the single most important finding — it determines which of
the six "reuse candidates" the user asked about are real reuse (infrastructure
patterns: routing, registries, optional-dependency discipline, config format,
tracking) versus false reuse (the ContentMask/StageHandler contract itself).

---

## 2. Reuse audit, candidate by candidate

| Candidate | Reusable? | What's actually reusable |
|---|---|---|
| **Routing** (`FilterRegistry` command→filter matching) | No, directly | The *pattern* — three-tier (project > user > builtin) TOML config with git-tracked-file trust (`filters/trust.py::is_git_tracked`) — is an excellent template for a new "detector rule" registry (§3.2), not the command-string regex matcher itself. |
| **StageHandler / ContentMask pipeline** | No | See §1. Reused only as a post-processing step on the *generated summary document*, not on repo source files. |
| **AST summarization (`ast_summarize/registry.py` + per-language modules)** | Partially | The registry *pattern* (language→callable dict, lazy-imported optional deps, `is_language_available()`/`extra_for_language()` introspection, fail-open contract) is directly reusable. The *data* is not: every analyzer today returns `set[int]` (compressible body-line ranges) — no import lists, no function/class names, no decorators, no base classes. None of that exists yet. |
| **Structured data summarization (`structured_data/registry.py`)** | Partially | Same story: format→analyzer registry pattern reusable; but `json_fmt.py`/`yaml_fmt.py`/`toml_fmt.py` return `list[CollapseRange]` (byte offsets for collapsing repeated array shapes), not parsed semantic fields (`dependencies`, `scripts`, `[project.scripts]`). The underlying full-document parse (stdlib `json`/`tomllib`, PyYAML `compose()`) already happens internally, though — real reuse is "don't parse this manifest a third way," not "reuse the CollapseRange output." |
| **Document summarization (`pipeline/extract/registry.py`)** | **Yes, directly, unchanged** | `extract(path) -> str | None` already turns `.docx`/`.pdf` into Markdown-shaped text with zero new code. A repo profiler can call it as-is for `README.docx`, design docs, etc. |
| **Analytics** (`stage_stats`, `compression_summary`, `filter_report`, `filter_effectiveness`) | Partially | These aggregate `StageResult` objects from ContentMask pipeline runs — repo summarization won't produce those. What *is* reusable: `count_tokens()` (tracking/db.py), the `±20%` uncertainty-labeling convention (Anti-Goal #24), and `track_invocation()`/the SQLite+JSONL tracking table, so a repo-summary run shows up in `quor gain` like any other invocation once given a synthetic "command" label. |
| **`quor/filters/trust.py` (`is_git_tracked`)** | **Yes, directly** | `git ls-files` (no args) gives a deterministic, `.gitignore`-respecting file list for free — no need to hand-roll ignore-pattern walking. Falls back to a minimal `os.walk` with a hardcoded ignore set (`.git`, `node_modules`, `__pycache__`, ...) when not inside a git repo. |
| **`_STRUCTURED_DATA_FILTER_NAMES_BY_BASENAME`** (claude_read.py) | **Yes, as a lookup table** | Already encodes "which lockfile basename implies which format/package manager" (`poetry.lock`→poetry/TOML, `Cargo.lock`→cargo/TOML, `Pipfile.lock`→pipenv/JSON, `composer.lock`→composer/JSON). This is *exactly* the kind of fact a package-manager detector needs — extend, don't reinvent. |
| **`quor/filters/builtin/*.toml` filter names** (`cat-python`, `cat-go`, ..., `node.toml`, `poetry.toml`) | Indirectly | Confirms Quor already has an implicit extension→language table (`.py/.js/.jsx/.mjs/.cjs/.ts/.tsx/.go/.java/.rs/.cs`) scattered across `claude_read.py`'s `_SOURCE_CODE_FILTER_NAMES_BY_EXTENSION` and the `cat-*.toml` `match_command` patterns. Worth consolidating into one shared extension→language table both consumers read from, rather than a third copy. |

---

## 3. Proposed architecture

```
quor/pipeline/repo_profile/                 (new package, parallel to pipeline/, not inside it)
  __init__.py
  walk.py            -- deterministic file enumeration (git ls-files, fallback walk)
  detectors/
    registry.py       -- three-tier TOML rule registry, mirrors filters/registry.py's
                          loading pattern (project .quor/detectors/ > user > builtin)
    builtin/*.toml     -- declarative marker-file/content-pattern -> fact rules
  manifests.py         -- package.json / pyproject.toml / Cargo.toml / go.mod / *.lock
                          field extraction, reusing structured_data's existing parse
                          calls (json.loads / tomllib.loads / yaml.compose) rather than
                          a second parser
  symbols.py            -- per-language "what does this file declare" extraction,
                          one parse per file, built as a sibling function next to
                          each ast_summarize/<lang>.py analyzer (same tree, two
                          outputs: compressible-line-ranges AND declared symbols)
  model.py              -- RepoProfile Pydantic model (frozen, like FilterConfig)
  render.py             -- RepoProfile -> deterministic Markdown (fixed template,
                          no invented prose, every line traceable to a source file)
```

### 3.1 Walk (`walk.py`)

- Primary: `git ls-files --cached --others --exclude-standard` (deterministic,
  respects `.gitignore`/`.git/info/exclude` for free, matches `trust.py`'s
  existing subprocess-to-git pattern).
- Fallback (no `.git`, or git unavailable): `os.walk` with a small hardcoded
  skip-set (`.git`, `node_modules`, `__pycache__`, `.venv`, `dist`, `build`,
  `target`, `.mypy_cache`, `.pytest_cache`) — same spirit as the tracking-DB
  test isolation fixture's explicit exclusions.
- Output: a flat, sorted list of relative POSIX paths (`Path.as_posix()`,
  already a hard rule elsewhere in this codebase — reuse, don't reinvent).

### 3.2 Detectors (`detectors/`)

A new three-tier TOML registry, structurally identical to `FilterRegistry`
(`_load_builtin`/`_load_user`/`_load_project`, same git-tracked trust check for
project-local rules) but matching **file paths/basenames/content snippets**
instead of command strings, and emitting **facts** instead of stage pipelines:

```toml
schema_version = 1

[[detector]]
name = "poetry"
category = "package_manager"
match_basename = ["pyproject.toml"]
match_content = ['\[tool\.poetry\]']   # regex, evaluated only on matched files
evidence = "pyproject.toml [tool.poetry] table"

[[detector]]
name = "flask"
category = "framework"
match_basename = ["requirements.txt", "pyproject.toml"]
match_content = ['(?m)^\s*(from|import)\s+flask\b']
evidence = "flask import found"
```

This reuses `filters/loader.py`'s TOML→Pydantic pattern and `filters/trust.py`'s
git-tracked check almost verbatim, and gives users the same auditability Quor
already promises elsewhere: every detected fact names the file(s) and pattern
that produced it (mirrors `quor explain`'s "every decision is inspectable"
principle, applied to detection instead of compression).

Built-in detector categories map directly to the goals list: `language`
(extension histogram — no detector rules needed, computed straight from the
walk), `framework`, `build_system`, `package_manager`, `test_framework`,
`infrastructure` (Dockerfile, docker-compose.yml, `.github/workflows/*.yml`,
`*.tf`), `configuration` (`.env.example`, `*.config.*`, well-known config
basenames).

### 3.3 Manifests (`manifests.py`)

For the handful of high-value manifest files (`package.json`, `pyproject.toml`,
`Cargo.toml`, `go.mod`, `*.lock`), extract structured fields (`dependencies`,
`devDependencies`, `scripts`, `[project.scripts]`, `[tool.poetry.scripts]`,
`bin`) by calling the *same* stdlib/PyYAML parse entry points
`structured_data/json_fmt.py`/`toml_fmt.py`/`yaml_fmt.py` already call — not a
second, independently-written parser for the same file. These modules'
`analyze_*` functions aren't reusable as-is (wrong return type), but the
principle "one parse per file format, one place it happens" should hold: either
add a sibling `extract_fields()` next to each `analyze_*()`, or have both call a
shared lower-level parse helper.

### 3.4 Symbols (`symbols.py`)

The genuinely new work. Each `ast_summarize/<lang>.py` module already builds a
full parse tree (`ast.parse()` for Python, `tree_sitter` for JS/TS/Go/Java/
Rust/C#) purely to compute compressible line ranges, then discards the tree.
Extracting entry points/framework signals (`if __name__ == "__main__":`,
`@app.route(...)`, `class Foo(models.Model):`, `func main()`, `public static
void main`) from source requires walking that same tree.

**"Avoid parsing source code twice" is honored by adding a second function per
language module that consumes the *same* parsed tree the existing analyzer
already builds** — not by trying to share a cache with the unrelated Bash/Read
compression path (those run at different times, for different invocations;
Quor has no cross-invocation cache today and Anti-Goal #13 explicitly rules out
session-level content tracking). Concretely, for Python:

```python
# ast_summarize/python.py — additive, existing analyze_python() untouched
def extract_symbols(tree: ast.Module) -> ModuleSymbols: ...
```

and `symbols.py` calls `ast.parse()` **once** per file, passing the resulting
tree to whichever of `_compressible_body_lines()` / `extract_symbols()` it
needs — never two separate `ast.parse()` calls on one file. Same discipline for
the tree-sitter languages (`tree.root_node` reused, not re-parsed).

This is additive only: `analyze_python()`'s public signature and behavior are
unchanged; `extract_symbols()` is a new, optional function the repo profiler
calls, that `code_ast_summarize`/`python_ast_summarize` never touch. Zero risk
to existing filter behavior.

Fails open exactly like today's analyzers: unregistered language →
skip file, degrade gracefully; missing optional tree-sitter extra → skip
with the same `is_language_available()`/`extra_for_language()` pattern already
used to report this to `quor doctor`/`quor verify`. A `pip install quor` with
no extras gets full symbol detail for Python only — this must be stated
explicitly in the generated profile output, not silently degraded.

### 3.5 Model + render (`model.py`, `render.py`)

`RepoProfile` is a frozen Pydantic model (matching `FilterConfig`'s
`ConfigDict(frozen=True)` convention) — languages (with file/line counts),
build systems, package managers, frameworks (each with evidence), test
frameworks, entry points, infrastructure files, notable configuration files,
and a `services`/`modules` list (subdirectories that each carry their own
manifest file — the standard monorepo heuristic Nx/Turborepo/Lerna also use).

`render.py` turns that model into fixed-template Markdown — no invented prose,
every fact traceable to the file(s) that produced it, deterministic given the
same repo state (same promise `quor explain` already makes for compression
decisions, applied to detection).

### 3.6 Optional post-compression

If the rendered profile is itself large (a genuinely huge polyglot monorepo
with dozens of services), the generated Markdown can be run through the
*existing* `markdown` `FilterConfig` exactly the way DOCX/PDF-extracted text
already is (`claude_read.py::_compress_via_named_filter`) — this is the one
place ContentMask legitimately re-enters the picture, compressing Quor's own
generated artifact, not repo source. A homogeneous "47 services, each listing
the same 3 facts" section is a natural `structured_data_summarize`-style
collapse target too, if the profile is rendered as JSON/YAML rather than
prose for that section.

---

## 4. The CLI-surface question (needs explicit sign-off)

CLAUDE.md is explicit and absolute: *"V1 has exactly 6 [CLI commands]... don't
add more without explicit approval"* (`quor schema` is the one documented,
deliberate exception — a non-filtering utility command). A repo-summary
capability needs an explicit trigger, and every option interacts with that
rule:

1. **New CLI command** (e.g. `quor map`), invoked by the user or by the AI via
   a Bash tool call, output goes to stdout like any other command. Cleanest,
   most transparent, most auditable — same category as `quor schema`
   (inspection utility, not a "filtering operation"), which is the precedent
   this would need to cite for the same exception. **Requires the explicit
   approval CLAUDE.md calls for before it can be built.**
2. **Silently reroute an existing exploratory command** (e.g. recognize the
   AI's first `find .`/`git ls-files` and substitute the profile for its real
   output). Rejected: this changes what a real command's output means, a
   larger trust violation than anything Quor does today (Anti-Goal #3 is about
   *removing* verified-redundant content, never about substituting different
   content for what was asked).
3. Do nothing yet; ship the detection/rendering library first, exposed only
   for internal/benchmark use, and decide the trigger mechanism in a follow-up
   once the detection quality itself is validated. Lowest-risk sequencing, but
   defers the actual point of the feature.

**Recommendation:** option 1, framed and justified the same way `quor schema`
was — presented to the user as its own approval checkpoint before any CLI code
is written, independent of approval for the detection engine itself.

---

## 5. Implementation plan (phased, pending approval)

| Phase | Deliverable | New code | Depends on |
|---|---|---|---|
| A | `walk.py` — deterministic file enumeration | `quor/pipeline/repo_profile/walk.py` | `git ls-files` (already used by `trust.py`) |
| B | Detector registry + built-in rules for languages/build systems/package managers/test frameworks/infrastructure | `detectors/registry.py`, `detectors/builtin/*.toml`, extension→language table (consolidating the one currently split across `claude_read.py`/`cat-*.toml`) | Phase A |
| C | Manifest field extraction (`package.json`/`pyproject.toml`/`Cargo.toml`/`go.mod`/lockfiles) | `manifests.py` | Phase B; reuses `structured_data`'s parse calls |
| D | Per-language symbol/entry-point extraction | `extract_symbols()` added to each `ast_summarize/<lang>.py`; `symbols.py` orchestrator | Phase B; additive to `ast_summarize/` |
| E | `RepoProfile` model + deterministic Markdown renderer | `model.py`, `render.py` | Phases B–D |
| F | Optional post-compression of the generated artifact | wiring into existing `markdown`/`structured_data_summarize` filters | Phase E |
| G | CLI/hook exposure (**pending §4 sign-off**) | new CLI command, `track_invocation()` wiring for `quor gain` visibility | Phase E; separate approval gate |
| H | Benchmark harness | see §6 | Phases A–E |

Each phase gets its own feature branch and backlog entry per CLAUDE.md's
"Starting Any Backlog Item" sequence, once approved — this document is the
Rule-4 pre-approval artifact, not a substitute for that process.

---

## 6. Benchmark strategy

The existing `tests/benchmarks/manifest.toml` harness measures *compression
ratio against a captured command output*, with `expected_filter` /
`min_reduction_pct` / `must_contain` correctness checks and a committed
`baseline.json` for regression detection. Repo summarization has no
"before" blob to compress against, so the harness needs a parallel structure,
not a shoehorned reuse of the existing one:

- **Fixture repos**, not fixture *files*: small synthetic directory trees
  checked into `tests/benchmarks/samples/repo-profile/<case-name>/` — e.g. a
  minimal Flask+pip project, a Node/Express project with `pnpm-lock.yaml`, a
  Go service, a deliberately polyglot monorepo with three sub-services.
- **Correctness is precision/recall against hand-labeled expected facts**
  (`expected_languages`, `expected_frameworks`, `expected_build_system`,
  `expected_entry_points`, plus `must_not_detect` for false-positive checks —
  the detection-quality mirror of `must_contain`/`must_not_contain`), not a
  reduction percentage. This is the primary signal, analogous to
  `expected_filter` correctness checks today.
- **Determinism check**: run twice against the identical fixture, assert
  byte-identical output — this is the feature's core promise and costs
  nothing to verify continuously.
- **Token-size ceiling**, secondary: cap expected output tokens per fixture
  size tier (small/medium/large synthetic repo), regression-tracked the same
  way `baseline.json` already tracks compression ratio drift, but as an upper
  bound rather than a floor.
- **Performance budget**: this runs as an explicit, user-invoked command
  (§4), not hook-path code, so it does not inherit the `<10ms` hook budget —
  more like `quor doctor`/`quor gain`'s multi-second tolerance. Still needs an
  explicit target (e.g. `<2s` for a 5,000-file repo) validated against a large
  synthetic fixture, since unbounded per-file AST parsing (Phase D) is the
  obvious scaling risk.

---

## 7. Expected token savings — labeled estimate, not a claim

Per Anti-Goal #24 (no token figure without uncertainty) and #25 (no AI-quality
claim without evidence), this section states a *hypothesis to validate*, not a
number to advertise.

The counterfactual isn't "the raw text this compresses" (there is none) — it's
the token cost of the equivalent manual-discovery tool-call sequence an AI
agent runs today to orient itself in an unfamiliar repo: something like a
directory listing, 3–6 `cat`/Read calls on manifest and config files, a
`grep`/`find` for entry points, maybe a `git log`. That sequence plausibly
costs on the order of low-thousands to low-tens-of-thousands of tokens
depending on repo size and how many files the AI reads before finding what it
needs — highly variable, no rigorous baseline exists yet. A single deterministic
profile targeting roughly 300–1,500 tokens (small/medium repo) would be a
large relative reduction *if* it actually avoids those follow-up calls — but
that "if" is exactly the thing that needs measuring, not assuming: a profile
that's wrong or incomplete just adds calls back (the AI re-verifies via Read
anyway), which would net negative. **This must be measured against real
session traces (the same `quor gain`/tracking infrastructure already reused
here) before any savings number is published anywhere**, exactly as
`quor gain`'s own ±20% labeling discipline already requires for every other
figure.

---

## 8. Limitations

- **Heuristic, not authoritative.** Detectors match markers and patterns —
  a stale `requirements.txt` from a removed dependency, or a `package.json`
  left over from a deleted frontend, will produce a false positive. Every
  fact must carry its evidence (file + pattern), matching `quor explain`'s
  transparency bar, so the AI (and user) can judge confidence rather than
  trust a bare assertion.
- **No semantic understanding.** This extracts structure and metadata, not
  "what the code does." It is explicitly not attempting LLM-shaped
  summarization — that's the point, but it also means it can't answer "why"
  questions, only "what's here."
- **Monorepo/service boundaries are the fuzziest heuristic.** Language/
  build-system/package-manager detection is fairly reliable (marker files are
  unambiguous); "this subdirectory is an independent service" is inherently
  softer and will need an explicit confidence signal rather than a binary
  yes/no.
- **Large-repo scaling.** File enumeration and marker-file detection are
  cheap even at scale; per-file AST symbol extraction is not, and needs an
  explicit cap/sampling strategy (Phase D) plus its own performance budget
  (§6) — this is new scaling territory Quor's existing single-file-at-a-time
  pipeline has never had to reason about.
- **Staleness.** No watch mode exists or is planned for V1 (explicit
  anti-goal). A generated profile is a snapshot; regeneration needs to be
  cheap enough to re-run often, or cache-invalidated on an explicit signal
  (e.g. git HEAD hash) rather than silently going stale — a silently stale
  *synthesized* summary is a worse trust failure than a silently stale
  compression, since there's no original the AI could fall back to noticing
  is missing.
- **Optional-dependency fragmentation.** Full symbol-level detail depends on
  the same optional tree-sitter extras (`quor[javascript]`, `quor[go]`, etc.)
  the AST-summarization framework already gates on. A plain `pip install quor`
  gets full fidelity for Python only; every other language degrades to
  marker-file/manifest-level detection only. Must be stated in the output
  itself, not silently thin.
- **The CLI-surface question is unresolved** (§4) and is a hard process gate,
  not just a style choice — CLAUDE.md requires explicit approval before a 7th
  command can exist.
- **Trust asymmetry vs. existing Quor features.** Every other Quor capability
  either removes verified-redundant content (compression) or converts a
  binary to text without adding claims (extraction). This synthesizes new
  claims about the repo. That's a meaningfully bigger ask of user trust than
  anything shipped today, and the evidence-per-fact requirement (§3.2, §8
  first bullet) exists specifically to keep it auditable enough to meet
  Quor's own transparency bar (Anti-Goal #10).

---

## 9. Summary recommendation

Build it as a new `quor/pipeline/repo_profile/` package, parallel to (not
inside) the ContentMask pipeline, reusing:
- the three-tier TOML registry + git-trust pattern from `filters/`,
- the language/format registry + optional-dependency fail-open pattern from
  `ast_summarize/`/`structured_data/`,
- `extract()` unchanged for DOCX/PDF docs,
- `count_tokens()`/tracking/the `±20%` convention for any savings figures,
- and the existing `markdown`/`structured_data_summarize` filters only as an
  optional final compression pass on the *generated* artifact.

Two things need explicit user sign-off before any code is written: **(1)** the
CLI-surface mechanism (§4) — this is a 7th-command decision CLAUDE.md
reserves for the user — and **(2)** overall scope/phasing (§5), since Phase D
(per-language symbol extraction) is materially larger than Phases A–C and
could reasonably ship later as its own follow-up.
