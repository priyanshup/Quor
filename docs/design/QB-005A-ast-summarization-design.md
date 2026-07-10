# QB-005A — AST-Aware Code Summarization Architecture Design

**Status:** Design proposal (not yet approved, not yet implemented)
**Date:** 2026-07-10
**Author:** Claude Code (design session), branch `feature/qb-005a-ast-design`
**Scope:** Design only. No production code, tests, or benchmarks were modified to produce this
document. This is the artifact CLAUDE.md's Rule 4 ("Competitor-first design... present the
recommendation for approval before implementation") requires before any of the follow-on QB-005B+
items may begin.

---

## 0. Summary

Quor already ships one AST-aware compression feature: **QB-005**, which compresses Python function
and method bodies to their signature + docstring using the stdlib `ast` module
(`quor/pipeline/stages/python_ast_summarize.py`, routed by `quor/filters/builtin/cat-python.toml`).
QB-035 explicitly named "extend AST-aware compression to JS/TS" as deliberately-deferred future
work, pending validation that the Python MVP earns real usage.

This document designs the generalized version: a **multi-language AST summarization framework**
that keeps Python's existing behavior byte-for-byte unchanged, and extends the same compression
philosophy to JavaScript and TypeScript. It answers the nine questions posed by QB-005A and closes
with a phased implementation plan (QB-005B–QB-005F) sized the same way QB-007's sub-items were.

**No conflict with Quor's existing architecture was found that would require stopping.** One real
architectural tension exists — no viable *pure-Python* parser supports current TypeScript syntax —
and it is resolved the same way `quor[documents]` (QB-007E2/E3) already resolved an identical
tension for DOCX/PDF: an optional, wheel-distributed dependency that fails open when absent, never
a core dependency. This is not a workaround invented for this document; it is Quor's own
already-shipped precedent, applied consistently. Section 5 explains this in full rather than
picking a weaker pure-Python option and quietly overstating what it can do.

A second, pre-existing gap was found during this design pass, not created by it: **Read-based `.py`
file access does not get AST summarization today.** `quor/adapters/claude_read.py`'s
`_READ_SUPPORTED_FILTER_NAMES` allowlist only contains `"markdown"` and `"document-text"` — a `.py`
file opened via Claude's native `Read` tool (as opposed to a `cat foo.py` Bash command) currently
passes through completely unfiltered. This is flagged explicitly in Section 9 (QB-005F) rather than
left as a silent inconsistency the new design would otherwise perpetuate for JS/TS too.

---

## 1. Where Parsing Belongs

**Decision: inside a `StageHandler`, exactly where QB-005 already put Python's parser.**

The task frames three options. Evaluating each against Quor's actual architecture:

### Option A — Before Pipeline (rejected)

This is the shape QB-007E1–E4 used for DOCX/PDF: a standalone `extract()` function
(`quor/pipeline/extract/registry.py`) that runs *before* `FilterRegistry`/`Pipeline` even sees the
content, converting a binary document into Markdown-shaped plain text, which is then handed to the
ordinary `markdown` filter.

That shape exists because DOCX/PDF content **is not text** — there is no `ContentMask` to build
until *something* turns bytes into lines. Python/JavaScript/TypeScript source is already
UTF-8 plain text with a 1:1 line structure the moment Quor sees it (whether from `cat`'s stdout or
a `Read` tool response). Forcing source code through a pre-Pipeline extraction step would solve a
problem source code doesn't have, while creating one it doesn't need to have: `extract()`'s
contract is `str | None` with no per-line provenance, so routing source through it would throw away
`ContentMask`'s KEEP/COMPRESS/PROTECT bookkeeping and `quor explain`'s stage-by-stage trace — for a
feature whose entire value is showing exactly what was compressed. Rejected.

### Option B — Inside FilterRegistry (rejected)

`FilterRegistry`'s job (per its own module docstring and ADR-003/ADR-019) is three-tier filter
lookup and pipeline orchestration — it explicitly does not know about content transformation
internals; that is `Pipeline`'s and each `StageHandler`'s job. `Pipeline.execute()`'s own docstring
states "The engine does NOT know about filter configs or content detection" — the inverse
responsibility split would be violated by putting language-aware parsing logic into the registry.
It would also make AST logic untestable in isolation the way every existing stage
(`tests/unit/test_*stage*.py`-style unit tests, independent of any filter TOML) already is. Rejected.

### Option C — Inside a new Stage (chosen)

This is what QB-005 already built for Python, and it satisfies every relevant principle without
adding a new extension point:

- **Engineering Principle #1** (PROJECT_BIBLE.md): "Every stage produces a `ContentMask`... reuse
  existing pipeline where possible." A `StageHandler` is the one and only unit of compression logic
  in Quor's architecture; AST summarization is compression logic.
- **FR06/FR08**: `can_handle()` gives a stage a clean, non-exceptional way to say "not applicable
  here" — exactly what's needed when, e.g., `tree-sitter` isn't installed (Section 4) or a file is
  too large to be worth parsing (Section 4).
- **Zero new abstractions**: no new plugin category, no new registry, no new hook adapter. The
  existing `quor.compression_stage` entry-point contract (ADR-007), the existing `StageConfig` /
  `StageHandler` Protocol (`quor/pipeline/stages/base.py`), and the existing filter-TOML
  `[[filter.stages]]` array all already support exactly this shape.

**Consequence:** language detection continues to happen at the *filter* layer via `match_command`
(command-string routing for Bash `cat`, file-path routing for `Read` — see Section 9), never inside
the stage itself, exactly as QB-005's own design note states: *"Python detection happens at the
filter layer... this stage receives only file content, exactly like every other stage; it is never
told the filename."* The new stage(s) inherit this constraint unchanged.

---

## 2. AST Representation

**Decision: `ContentMask`. Nothing else.**

The task asks whether Quor should produce summarized source, structural Markdown, `ContentMask`, or
another intermediate format. `ContentMask` is not merely *a* reasonable choice among these — it is
the only one consistent with two explicit, load-bearing rules:

- **ANTI_GOALS.md #18** ("No string→string transform pipeline"): *"A stage that receives a string
  and returns a modified string is architecturally wrong."*
- **ANTI_GOALS.md #3** ("Never silently modify content meaning"): *"A line that Quor keeps must be
  bit-for-bit identical to the original line."*

"Structural Markdown" is QB-007's answer to a *different* question — "how do I turn a binary
document with no native line structure into something a text-oriented pipeline can compress?" Source
code has no such problem; it is already the most structured plain text Quor ever sees (every stage
in `cat-python.toml` already operates on it line-by-line). Generating a synthesized Markdown
representation of a Python file would be a lossy, one-way transform of exactly the kind Anti-goal
#3 forbids, and would forfeit the `preserve_patterns`/PROTECT mechanism every other stage gets for
free.

"Summarized source" (a new custom string format, e.g. a hand-rolled "signature-only" text blob) is
rejected for the same reason `python_ast_summarize.py`'s own module docstring already rejects it:
*"`ast` is used for PARSING ONLY. This stage never regenerates, reformats, or rewrites source text
(no `ast.unparse()`...) — every kept line is the original line, byte-for-byte."* A generalized
design must preserve this invariant for JS/TS too — tree-sitter (Section 5) is used exclusively to
compute **line ranges to compress**, never to regenerate source text. `LineMask.line` for every kept
line is always the original line, unchanged, for every supported language.

**What the AST layer actually produces, concretely:** a `set[int]` of 1-indexed line numbers
eligible for compression — the exact same return shape `_compressible_body_lines()` already
computes for Python (`quor/pipeline/stages/python_ast_summarize.py`). The stage then walks
`mask.lines` and marks each line in that set `COMPRESS` (unless it's already `PROTECT`, or matches a
`preserve_patterns` entry, unchanged from QB-005's existing logic). No new mask field, no new
`Decision` value, no schema change to `ContentMask`/`LineMask` (both are frozen dataclasses — adding
fields would be a breaking change to a stable, cross-stage primitive, and nothing in this design
needs one).

---

## 3. Compression Strategy

The **shape** of the strategy is unchanged from QB-005: compress function/method **bodies** to
nothing; preserve everything that describes the file's public surface. What differs per language is
*which AST node types* map to "body" vs. "surface." This section defines that mapping so it can be
implemented directly in QB-005C/QB-005D without further design decisions.

### Always preserved (never entered into the compress-candidate set)

| Category | Python (existing, unchanged) | JavaScript | TypeScript (adds to JS) |
|---|---|---|---|
| Imports | `import`/`from ... import` (top-level, never inside a function body range) | `import` statements | same |
| Exports | N/A (Python has no export keyword) | `export`/`export default`/`export { } from` | same |
| Public functions/methods | Signature line(s) + own docstring | Signature line(s) + own leading JSDoc block | same |
| Classes | Signature line, decorators, own docstring; body walked recursively for nested defs | `class` signature line, `extends`/`implements` clause | same, plus `implements` |
| Interfaces | N/A | N/A | `interface_declaration` — preserved **whole**, no body concept to compress |
| Type aliases | N/A | N/A | `type_alias_declaration` — preserved whole |
| Enums | N/A | N/A | `enum_declaration` — preserved whole |
| Decorators | `decorator_list` (already outside `node.body`, so already preserved — no change) | `@decorator` nodes (Stage 3 proposal / TC39; common via Babel) | same, more common (Angular/NestJS) |
| Docstrings / JSDoc | Leading string-literal expression, excluded from body range | Leading `/** ... */` block immediately preceding a declaration, excluded from body range the same way | same |
| Module-level constants | Any top-level statement outside a function/class body — untouched, same as today | `const`/`let`/`var` at module scope | same |

### Compressed (entered into the compress-candidate set)

- The **body** of a function, method, or arrow function assigned to a name — i.e. everything after
  the signature and any leading docstring/JSDoc, up to (not including) the closing brace's own line,
  mirroring `_body_line_range()`'s existing Python logic exactly.
- Nested/private helper functions are **not special-cased** — a private helper's body compresses via
  the same rule as a public function's body. "Repetitive private helpers" (named explicitly in the
  task) is deliberately **not** handled by the AST stage: collapsing genuinely repeated content is
  already `group_repeated`'s job (existing built-in stage), and `cat-python.toml`'s own pattern
  (`python_ast_summarize` → `strip_lines` → `deduplicate_consecutive` → `max_tokens`) already
  demonstrates that layering existing stages after AST compression is how Quor handles this, not by
  adding logic to the AST stage itself. `cat-javascript.toml`/`cat-typescript.toml` (QB-005C/D) reuse
  this same stage order.
- A same-line body (`def f(): return 1` in Python; `const f = () => x + 1;` in JS/TS with no block)
  is **not compressed** — `ContentMask` is line-based and cannot partially compress a single physical
  line without touching the signature. This is not a new rule; it is `_body_line_range()`'s existing
  behavior (`start <= node.lineno` → `return set()`), generalized to the JS/TS equivalent check
  (does the arrow function's body node span more than one line, i.e. is it a `statement_block` at
  all, vs. a single expression).

### Comments

Python's `ast` module discards comments entirely (no node), which is exactly why `cat-python.toml`
layers `strip_lines` *after* `python_ast_summarize` to do real comment-stripping work
(`^\s*#[^!]`, preserving `TODO`/`FIXME`/`HACK`/`XXX`/`NOTE`/`WARN`). Tree-sitter, by contrast, *does*
expose `//` and `/* */` comments as real nodes — but this design deliberately does **not** use that
capability to add comment-aware logic inside the AST stage itself. Instead, JS/TS filters follow the
identical two-stage layering `cat-python.toml` already proved out:

1. `code_ast_summarize` (Section 8) compresses bodies wholesale — any `//` comment *inside* a
   compressed body vanishes with it, same as Python.
2. A `strip_lines` stage (new pattern: `^\s*//\s`, already present in `cat-python.toml`'s own pattern
   list as a forward-looking entry — `'^\s*//\s'` was added there specifically for this reuse) strips
   ordinary line comments outside compressed regions, preserving the same
   `TODO`/`FIXME`/`HACK`/`XXX`/`NOTE`/`WARN` set.

A leading `/** JSDoc */` block is treated the same way a Python docstring is: excluded from the body
range so it survives as part of the preserved signature, **not** stripped by `strip_lines` (no
existing pattern matches `/* */` block-comment syntax, so this requires no new stripping logic —
only the AST stage's body-range computation needs to know to skip past it, mirroring the existing
`docstring_present` check in `_body_line_range()`).

### Explicitly not attempted

- **Auto-detecting "generated code"** (e.g. a `// Code generated by ...` header, `eslint-disable`
  banners) to skip compression entirely. Considered and rejected: heuristic marker-matching is
  exactly the kind of fragile, false-positive-prone logic Quor avoids elsewhere (see Section 4's
  "Generated code" discussion) — syntactically valid generated code parses and compresses correctly
  like any other file, so there is no correctness reason to special-case it, only a stylistic one
  that `preserve_patterns` already covers if a user wants it.
- **Preserving run-level formatting/emphasis** — not applicable to code (unlike QB-007E2's DOCX
  bold/italic question); code has no equivalent concept.

---

## 4. Failure Behaviour

Every failure mode below must degrade to **the mask unchanged** (fail-open, ADR-018), consistent
with every other stage in the pipeline. Two genuinely different fail-open mechanisms are in play,
and the design must not conflate them:

| Failure mode | Mechanism | Where |
|---|---|---|
| Syntax error (Python) | `ast.parse()` raises `SyntaxError` — **not caught locally**, propagates to `Pipeline.execute()`'s existing per-stage `try/except`, which already logs a warning and continues with the mask unchanged. | Unchanged from QB-005. |
| Syntax error (JS/TS) | Tree-sitter does **not** raise on malformed input — it is an *error-recovering* parser that returns a partial tree containing `ERROR`/`MISSING` nodes. This is a materially different failure shape than Python's and needs an explicit rule (below). | New — Section 4.1. |
| Unsupported language | The stage's `can_handle()` returns `False` (FR08) when the requested `language` config value has no registered analyzer, or when the analyzer's optional dependency isn't installed. Clean skip, not an exception. | New — Section 4.2. |
| Parser crash (non-syntax-error exception inside the analyzer, e.g. a `tree-sitter` internal error) | Not caught locally, by design — propagates to `Pipeline.execute()`'s existing per-stage guard, identical to how a `SyntaxError` already does. No second, redundant try/except is added, mirroring `python_ast_summarize.py`'s own stated rationale ("only per-line, expected failure modes... are caught locally; a whole-stage failure relies on the engine's existing, already-tested fail-open guarantee"). | Reused, not new. |
| Huge files | A stage-level `max_lines`/`max_bytes` guard in `can_handle()` (default generous, e.g. 20,000 lines / 2 MB — tunable per filter config) returns `False` above the threshold, so the file is never handed to the parser at all. This is a new, explicit safety valve this design adds — Python's stage has none today because stdlib `ast.parse()` is fast enough on typical Python file sizes that it was never observed as a problem; the same is not guaranteed for tree-sitter parsing arbitrary minified bundles (see below), so JS/TS gets the guard from day one and Python may optionally adopt the same guard for consistency (non-breaking — a generous default cannot regress any file that currently compresses successfully). | New — Section 4.3. |
| Generated code | Not specially detected (Section 3). Syntactically valid generated code compresses exactly like hand-written code; no failure path is needed because there is no failure. | N/A by design. |
| Minified code | Two independent protections, neither new: (1) `ContentMask` is line-based — a minified file is typically one or a handful of extremely long lines, and a body sharing its line with its own signature is already excluded from compression by the same-line-body rule (Section 3); (2) the huge-files byte-size guard above catches large single-line files that line-count alone would miss (a minified bundle can have very few *lines* but a very large *byte* count — the guard checks `len(content)`, not just line count, for exactly this reason). | Reuses Section 3 + 4.3. |

### 4.1 — ERROR nodes and partial parses (JS/TS-specific)

Because tree-sitter recovers from syntax errors instead of raising, a body-compression candidate
must not be trusted if it overlaps a node tree-sitter itself marked `ERROR` or `MISSING`. The
analyzer computes the compress-candidate line-range set the same way Python's does, but with one
extra check: **any function/method whose own signature-to-closing-brace span contains an `ERROR` or
`MISSING` node anywhere in the tree is excluded from the compress set entirely** — not because that
one function is unsafe to touch (it might be fine), but because a truncated or malformed nearby
construct can shift what tree-sitter believes that function's boundaries are, and Quor's own
"meaning preservation is non-negotiable" principle means the conservative default wins on any doubt.
This is a genuinely new rule Python's stage has never needed (a `SyntaxError` there fails the whole
file, never partially) — it must be implemented, not assumed away, in QB-005C.

### 4.2 — Unsupported language / missing optional dependency

`can_handle()` performs a cached "is this language's analyzer available" check once per process (not
once per line — matching the performance requirement in Section 6) and returns `False` if:
- the `language` value in `StageConfig` names a language with no registered analyzer at all (e.g. a
  future Go/Rust filter shipped before its analyzer exists — mirrors `quor/pipeline/extract/registry.py`'s
  own "unregistered extension" branch), or
- the analyzer *is* registered but its optional import (`tree_sitter_javascript` /
  `tree_sitter_typescript`) fails — mirroring `extract_docx()`/`extract_pdf()`'s existing
  `ImportError` → actionable-warning → fail-open pattern exactly, including naming the specific
  extra (`quor[javascript]`) in the warning text.

Both cases produce the same observable outcome: the stage is skipped (`StageResult.was_skipped=True`,
`skip_reason="can_handle returned False"`), and the rest of the filter's stage list (e.g.
`strip_lines`, `max_tokens`) still runs normally on the unmodified file — the user gets the same
`cat.toml`-equivalent generic compression they'd get for any file type Quor doesn't specially
understand, never an error, never a crash.

### 4.3 — Huge files

Implemented as an early-return guard inside the stage's own `can_handle()`, evaluated against
`raw_content` (already passed to every stage's `can_handle()` per the existing `StageHandler`
Protocol — no interface change needed): if `len(raw_content) > max_bytes` or the file's line count
exceeds `max_lines`, return `False`. Values are `StageConfig` fields with generous defaults, so no
currently-passing file is expected to regress; a filter author can lower them for known-large
monorepo files if desired. This directly serves NFR02 (<200ms/10,000 lines) — the guard exists
specifically so a pathological outlier can never blow the pipeline's latency budget, rather than
relying on parse speed alone to stay fast in every case.

---

## 5. Parser Selection

### Python — no change

`ast` (stdlib). Already shipped (QB-005). Zero new dependency, zero new risk. Not re-evaluated here.

### JavaScript / TypeScript — candidates evaluated

| Library | Pure Python? | TypeScript support | Maintenance | Windows wheels | Verdict |
|---|---|---|---|---|---|
| **tree-sitter** + `tree-sitter-javascript` + `tree-sitter-typescript` | No — C extension, prebuilt wheels | Yes — dedicated TS and TSX grammars | Active; used in production by GitHub, Neovim, Zed, and many code-intelligence tools | Yes, per PyPI-published wheel tags for the core `tree-sitter` bindings and both grammar packages (to be **re-confirmed as a pre-flight gate at the start of QB-005C**, the same way Quor already re-verifies environment assumptions before committing to an implementation — see PROJECT_STATUS.md's historical "Pre-implementation blockers" pattern) | **Recommended** |
| `esprima-python` | Yes | **None** — ES5/early-ES6 (~ES2017) only, no TS syntax parsing at all | Effectively unmaintained (no meaningful updates in years); no coverage of optional chaining (`?.`), nullish coalescing (`??`), private class fields (`#x`), or any TS syntax | Trivially (pure Python) | Rejected — hard blocker: cannot parse the Phase 1-required language (TypeScript) at all, and would silently fail-open on a large fraction of *modern* JavaScript too |
| `pyjsparser` | Yes | None | Similarly unmaintained, similarly ES5-era | Trivially | Rejected — same TS blocker, thinner community signal than esprima |
| Shell out to Node.js (`@babel/parser`, or the TypeScript compiler's own AST) | N/A (external process) | Full, always current | N/A | N/A | Rejected outright — reintroduces the exact "install another toolchain" problem Quor's entire positioning exists to solve (PROJECT_BIBLE.md's headline promise is "no Rust required"; requiring Node.js on a corporate Windows machine with no admin rights is the identical class of blocker, just a different runtime). Also reintroduces per-file subprocess spawn cost and drops Quor's zero-network/fully-local guarantee if any dependency were ever fetched at runtime. |
| Hand-rolled regex/brace-matching "parser" | Yes | Whatever is implemented | N/A | Trivially | Rejected — this is not actually AST-aware (contradicts the task's own framing), and is unreliable in the presence of template literals, regex literals, and strings containing brace characters. A wrong brace match compresses the wrong span, which is a direct, silent violation of Anti-goal #3 ("never silently modify content meaning") — unacceptable risk for a feature whose value proposition is *safety*, not just compression ratio. |

**Recommendation: `tree-sitter` + `tree-sitter-javascript` + `tree-sitter-typescript`, shipped as a
new optional extra, `quor[javascript]`.**

This is an honest deviation from the task's "prefer mature pure-Python parsers" instruction, stated
openly rather than quietly picked around: **no pure-Python parser satisfies the Phase 1 requirement
of parsing current TypeScript.** Choosing `esprima-python` to satisfy the letter of "pure Python"
would mean shipping a feature that silently underperforms on the majority of real-world modern JS
and *cannot function at all* on the TypeScript half of this task's own scope — which would itself
violate the transparency principle (PROJECT_BIBLE.md Philosophy #1: "A filter that cannot explain
what it removed is dangerous") by giving a false impression of TypeScript support that doesn't
exist.

**Why this does not violate ANTI_GOALS #6:** that rule's actual text is *"Never depend on Rust, Go,
or any compiled binary as a **core** dependency... Optional extras... may have heavier dependencies,
but only if they fail gracefully on import error."* `tree-sitter` is C, not Rust/Go (the rule's named
examples), and — more importantly — this design does not propose it as a core dependency at all. It
is proposed exactly as `quor[documents]` (`python-docx`, `pdfplumber`) already precedents: an
optional extra, absent by default, `quor` core installs and runs with zero compilation either way,
and every code path that touches it fails open on `ImportError` (Section 4.2) — the identical
contract `extract_docx()`/`extract_pdf()` already ship today. `pdfplumber` itself already pulls in
Pillow, a C-extension imaging library, as a transitive dependency of an existing optional extra —
this is not a new category of dependency for Quor, only a new instance of an already-approved one.

**Why tree-sitter specifically (beyond "the only real option"):** it is genuinely well-suited to
this problem beyond being the last one standing —
- **Error-tolerant by design.** Unlike Python's `ast`, a syntax error doesn't kill the whole parse —
  it produces a partial tree (Section 4.1 designs around this deliberately, both as a strength to use
  and a risk to guard).
- **One grammar API, many languages.** Every tree-sitter grammar (JS, TS, and, if ever added, Go,
  Rust, Java, C++) exposes the same node-tree/query API. This directly satisfies "Future languages
  should be extensible" — a future language addition is "write one new analyzer module using the
  same pattern," not "evaluate an entirely different parsing library from scratch," which is exactly
  the economy of scale QB-035's own competitive research flagged Quor as currently lacking relative
  to Headroom AI's multi-language `CodeCompressor`.
- **Fully deterministic** — same bytes in, same tree out, no randomness, no state — satisfies
  ANTI_GOALS #20.
- **No AI/ML/LLM involvement whatsoever** — it is a classical incremental parser (originally built for
  editor syntax highlighting), satisfying ANTI_GOALS #2 as unambiguously as stdlib `ast` does.

---

## 6. Performance

| Concern | Expectation | Basis |
|---|---|---|
| CPU (parse cost) | Tree-sitter parsing is C-speed; for files under a few thousand lines, parse time is expected to be low single-digit milliseconds — comparable to or faster than Python's own `ast.parse()` on an equivalently sized file. Not independently benchmarked in this design pass (design-only scope); **must be measured empirically as part of QB-005E** (benchmark corpus, Section 8) before this expectation is treated as validated, per NFR02's existing <200ms/10,000-line budget. | Design-time estimate only — flagged, not asserted as fact. |
| Memory | A tree-sitter parse tree for a typical source file is compact (bounded by source size); freed when the stage returns. Expected well within NFR06's 50MB/invocation budget for realistic file sizes — the huge-files guard (Section 4.3) exists specifically so an unbounded outlier can't be the exception that breaks this. | Consistent with existing NFR06 budget; not independently measured here. |
| Latency | The `max_lines`/`max_bytes` guard (Section 4.3) is the primary latency-budget defense — it ensures the stage either finishes fast or doesn't run at all, rather than relying purely on best-case parser speed. | New design element, not a measurement. |
| Scalability (across languages) | Because every tree-sitter grammar shares one API, adding a fourth/fifth language (Go, Rust, ...) is expected to be a bounded, per-language cost (one grammar package + one analyzer module + one filter TOML + one benchmark entry) rather than a new parsing-library integration each time — this is the direct payoff of Section 5's "one grammar API, many languages" reasoning. | Structural argument, to be validated the first time a language is actually added beyond JS/TS. |
| Plugin discovery cost | Unaffected — this design adds built-in stages (`_STAGE_HANDLERS`, Section 8), not new entry-point plugin discovery, so ADR-007's existing plugin-cache/discovery-cost numbers (Performance Targets table, PROJECT_BIBLE.md) are untouched. | No new discovery path introduced. |

**Explicit non-goal for this design pass:** producing final, committed performance numbers. That is
what `tests/benchmarks/` (ADR-032) and QB-005E exist for — this section states expectations and the
mechanism by which they must be verified, consistent with how QB-007E3's PDF work was itself
benchmarked only once real extraction code existed, not during its own design phase.

---

## 7. Testing Strategy

Mirrors the testing shape every prior QB item in this codebase already used (QB-005, QB-007B/C/D,
QB-007E1–E4) — no new testing philosophy is introduced, only new instances of the existing one.

**Unit tests** (per new analyzer module, `tests/unit/test_ast_summarize_<language>.py`):
- Valid file with functions, classes, decorators, nested scopes — compression happens where expected.
- Syntax error / malformed input — fail-open verified at both the analyzer level (returns `None` or
  equivalent "cannot analyze" signal) and the full-pipeline level (mirrors QB-005's own dual-level
  fail-open tests).
- Empty file, whitespace-only file.
- Single-line body (`=> x + 1`-style) — not compressed (Section 3).
- JSDoc-leading declarations — JSDoc preserved, body compressed.
- Decorator-heavy input (Angular/NestJS-style class) — decorators preserved.
- TypeScript-specific: `interface`, `type` alias, `enum` — preserved whole, never entered into the
  compress-candidate set.
- JSX/TSX-specific: a `.tsx` file using JSX syntax — correct grammar variant selected
  (`language_tsx()` vs `language_typescript()`), no `<Foo>` vs. type-cast ambiguity misparse.
- A file containing a real syntax error alongside otherwise-valid functions — Section 4.1's
  ERROR-node-overlap exclusion rule verified directly (a function overlapping the error is *not*
  compressed; a function far from it, in a large file, still is).
- Missing optional dependency (`tree_sitter_javascript`/`tree_sitter_typescript` absent from
  `sys.modules`) — `can_handle()` returns `False`, verified the same way
  `tests/unit/test_extract_docx.py`'s "missing-dependency fail-open" case already verifies this
  pattern for `python-docx`.
- A large synthetic file (mirrors QB-005's "300-function synthetic large file" case) — correctness at
  scale, plus a rough sanity check against the huge-files guard's default threshold.
- Byte-identical-kept-line regression test — every line the stage decides to keep must be
  `==` the corresponding original line, verified directly (mirrors QB-005's own regression test of
  the same property).

**Integration tests** (filter-level, `[[filter.tests]]` in each new TOML — minimum 3 per
ANTI_GOALS.md #23, matching `cat-python.toml`'s own 4):
- Function body compressed; signature and docstring/JSDoc preserved.
- Imports/exports/top-level constants never touched.
- Invalid syntax fails open — original content returned unchanged.
- (TS filter only) Interface/type/enum declarations preserved whole.

**Regression tests:**
- A dedicated test proving `cat-python.toml`/`python_ast_summarize.py` are **byte-for-byte unchanged**
  by this work — QB-005B introduces the generalized framework without touching the existing Python
  stage or filter at all (Section 9), and this must be provable, not assumed, exactly as QB-007C's
  own regression test proved the Bash dispatch path was untouched by the new Read hook.
- `TestKnownRoutingCollision`-style test (mirrors QB-007B's own documented, tested limitation) for
  the equivalent JS/TS routing edge case: a file literally named to collide with an existing filter
  pattern, or a path containing whitespace (same anchoring rationale `markdown.toml`'s header comment
  already documents — `cat-javascript.toml`/`cat-typescript.toml` should use the same
  whitespace-anchored `match_command` pattern for the same reason).

**Benchmark corpus** (`tests/benchmarks/`, mandatory per ADR-032 — "a filter PR without a benchmark
case is incomplete"):
- One realistic, moderate-size `.js` file and one `.ts` file (mirrors the existing long/short pairing
  convention — e.g. `markdown-design-doc-long`/`markdown-readme-short`).
- One decorator-heavy TypeScript file (Angular/NestJS-shaped) — decorators are common enough in real
  TS codebases to warrant their own corpus entry, not just a unit-test fixture.
- One minified `.js` file — must show near-zero (not negative, not crashing) compression, proving
  Section 4's minified-code protections hold on a realistic minified bundle, not just a synthetic
  one-liner.
- One `.tsx` file exercising JSX — proving grammar-variant selection (Section 5) is correct on
  realistic content, not just a unit-test fixture.

**What is explicitly out of scope for this design pass:** actually writing any of the above. Rule 1
of CLAUDE.md ("Test requirement — non-optional") applies to QB-005B onward, not to this document.

---

## 8. Risks

### Architectural risks

- **Two established AST/structural-compression integration shapes already coexist** in this codebase
  — Stage-based (QB-005, Section 1's chosen option) and extract-then-filter-by-name (QB-007E4, Section
  1's rejected Option A). This design deliberately keeps AST summarization in the Stage-based camp for
  source code and does not attempt to unify the two shapes into one — they solve genuinely different
  problems (binary→text vs. text→more-compressed-text) and forcing a false unification would be its
  own architectural risk. Flagged so a future reader doesn't mistake the coexistence of both shapes
  for an oversight.
- **The pre-existing Read-hook gap** (Summary, Section 9/QB-005F): if QB-005F is deprioritized or
  dropped from the phased plan, this design would ship JS/TS AST summarization for `cat` (Bash) only,
  while the *more commonly used* `Read` tool path remains completely unaware of any AST compression
  for any language, Python included. This is a real product-value risk, not just a completeness nit —
  worth the user's explicit attention when prioritizing QB-005B–F, not something to silently accept.

### Correctness risks

- **Tree-sitter's error tolerance is a double-edged property.** It is the reason JS/TS gets graceful
  partial-file handling Python's `ast` cannot offer — and simultaneously the reason a naive
  implementation could silently compress the wrong span near a syntax error, where Python's stage
  would simply fail the whole file loudly instead. Section 4.1's ERROR-node-overlap exclusion rule is
  the mitigation; it must be implemented and tested (Section 7), not assumed to fall out "for free"
  from using tree-sitter.
- **Grammar-variant selection ambiguity** (`.ts` vs `.tsx`, and to a lesser extent `.js` vs `.jsx`) is
  a new routing dimension Python never needed (one grammar, one extension). Getting this wrong
  produces either spurious parse errors (safe, fail-open, but reduces the feature's real-world hit
  rate) or, worse, a misparse of JSX-as-type-cast syntax that could feed a wrong line range into the
  compress set. Must be resolved by file extension at the filter-routing layer (mirrors how
  `cat-python.toml`'s `match_command` already resolves "is this Python" purely from the `.py`
  extension in the command string) — not inferred from file content.
- **`quor validate`'s <1-second, no-subprocess-execution requirement (FR21)** is unaffected in the
  common case (validating TOML shape doesn't require actually parsing a source file), but worth an
  explicit note: nothing in this design proposes validating filter *content* by test-parsing sample
  source at `quor validate` time — that would risk violating FR21 if a pathological sample were ever
  involved. `quor validate` stays a config-shape check only, exactly as it is today.

### Maintenance risks

- **Grammar version drift.** `tree-sitter-javascript`/`tree-sitter-typescript` release independently
  of Quor. A grammar update could rename or restructure node types an analyzer's queries depend on,
  silently changing (not crashing) what gets compressed. ADR-032's mandatory benchmark-regression
  coverage is the existing mechanism that would catch this — called out explicitly here so it isn't
  rediscovered as a surprise later the way the `mypy`/`group_repeated` ordering issue was (ADR-031's
  QB-014 follow-up).
- **Install footprint.** `quor[javascript]`'s wheels add real megabytes to an opt-in install. Zero
  cost to any user who doesn't request the extra (same as `quor[documents]` today) — flagged only for
  completeness, not treated as a blocking concern given the existing precedent already accepted this
  trade-off for DOCX/PDF.
- **A third optional-extra dependency family (`documents`, and now `javascript`) increases the
  permutation space `mypy`/CI must stay green across** (installed vs. not, per extra) — the existing
  `[[tool.mypy.overrides]]` pattern (`ignore_missing_imports` for `docx`/`pdfplumber`) is the proven
  mechanism to extend for `tree_sitter`/`tree_sitter_javascript`/`tree_sitter_typescript`; no new
  mechanism needs inventing, only more entries in the same table.

---

## 9. Phased Implementation Plan

Sized and sequenced the same way QB-007 was split — each item independently reviewable, testable,
and mergeable, smallest-risk-first, with the "framework before real behavior" ordering QB-007E1
already validated as the right shape for this kind of work.

- **QB-005A — Design** *(this document)*. No code.

- **QB-005B — Parser framework.** Introduce `quor/pipeline/ast_summarize/` (mirrors
  `quor/pipeline/extract/`'s own package shape): a routing table (language → analyzer callable,
  `Callable[[str], set[int] | None]` — no `Protocol`/ABC, matching QB-007E1's own explicit
  "premature abstraction for a contract this small" judgment) and a new generic `StageHandler`,
  `code_ast_summarize` (`quor/pipeline/stages/code_ast_summarize.py`), with a `language: str` config
  field. Python gets a first analyzer module that **wraps the existing, unmodified**
  `_compressible_body_lines()`/`_body_line_range()` logic from `python_ast_summarize.py` — proving the
  new framework end-to-end using the one language that needs no new dependency, before any new
  dependency is introduced. `python_ast_summarize.py` and `cat-python.toml` are **not modified** in
  this phase (zero regression risk on already-shipped behavior — a regression test proves this, per
  Section 7). Deliverable is the framework and its Python-backed proof, not a user-visible feature
  change.

- **QB-005C — JavaScript summarization.** Add `tree-sitter` + `tree-sitter-javascript` as the new
  `quor[javascript]` optional extra (`pyproject.toml`, mirroring `quor[documents]`'s exact structure
  including a `[[tool.mypy.overrides]]` entry). New `javascript.py` analyzer implementing Section 3's
  mapping and Section 4.1's ERROR-node-exclusion rule. New `cat-javascript.toml` filter (routes
  `.js`/`.jsx`/`.mjs`/`.cjs`, same `strip_lines`/`deduplicate_consecutive`/`max_tokens` tail
  `cat-python.toml` already uses). Fail-open verified when the extra is absent.

- **QB-005D — TypeScript summarization.** Add `tree-sitter-typescript` to the same
  `quor[javascript]` extra (or a dedicated `quor[typescript]` extra if dependency-weight separation
  from plain JS is judged worthwhile at implementation time — a genuine open question, not
  pre-decided here). New `typescript.py` analyzer, handling both the `language_typescript()` and
  `language_tsx()` grammar variants (Section 5/8). New `cat-typescript.toml` (routes
  `.ts`/`.tsx`), preserving `interface`/`type`/`enum` declarations whole per Section 3.

- **QB-005E — Benchmarks.** Manifest/baseline coverage (ADR-032-mandatory) for both new filters —
  the long/short pair plus the decorator-heavy and minified/JSX corpus entries specified in Section 7.
  Not merged until real, measured compression numbers exist (no fabricated placeholder percentages,
  matching every prior QB item's own discipline around honest benchmark reporting).

- **QB-005F — Read-hook integration, and closing the pre-existing Python gap.** Wires AST
  summarization into `quor/adapters/claude_read.py` for `.py`/`.js`/`.jsx`/`.mjs`/`.cjs`/`.ts`/`.tsx`
  Read calls — extending `_READ_SUPPORTED_FILTER_NAMES`-equivalent routing, using the **by-name**
  filter lookup pattern QB-007E4 already established for exactly this kind of mismatch (a Read file
  path like `"module.py"` will never match `cat-python.toml`'s `^cat\s+...`-shaped `match_command`
  pattern, the identical problem QB-007E4 solved for extracted-DOCX-text by looking up `"markdown"`
  by name via `FilterRegistry.all_filters()` instead of by pattern match). This phase is what actually
  closes the gap identified in the Summary and Section 8 — **Python's existing `python_ast_summarize`
  stage benefits from this phase too**, since it is the first point at which Read-based `.py` access
  gets AST compression at all, not something QB-005B–E provide on their own.

Each phase follows CLAUDE.md's existing "Starting Any Backlog Item" git workflow (own feature branch
off `main`, not stacked on another unmerged item) and Definition of Done checklist unchanged — this
design does not propose any process change, only the technical content each phase should contain.

---

## Appendix: Explicit Answers to the Nine Questions (index)

1. Where parsing belongs → Section 1 → inside a `StageHandler`.
2. AST representation → Section 2 → `ContentMask`, exclusively.
3. Compression strategy → Section 3.
4. Failure behaviour → Section 4 (4.1–4.3 for the genuinely new JS/TS-specific rules).
5. Parser selection → Section 5 → `tree-sitter` family, optional extra.
6. Performance → Section 6 (expectations + the mechanism to validate them, not final numbers).
7. Testing strategy → Section 7.
8. Risks → Section 8.
9. Phased implementation plan → Section 9 (QB-005B–QB-005F).
