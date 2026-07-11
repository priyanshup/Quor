# Changelog

All notable changes to Quor are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.4.1] — 2026-07-11

- **Fixed: `quor verify`/`quor doctor` falsely reported unhealthy on a plain
  `pip install quor` (QB-038).** `cat-javascript.toml`/`cat-typescript.toml`'s
  inline tests asserted AST-summarization behavior that only holds when the
  optional `quor[javascript]` extra (tree-sitter) is installed — without it,
  the stage correctly fails open (no compression, a clear warning), but the
  tests weren't written to expect that, so they failed instead. Added
  `FilterTest.requires_language`: a tagged test is now skipped, not failed,
  when its language's parser isn't available. `FilterRegistry.run_tests()`
  returns a `TestRunResult(failures, skipped)` instead of a bare list;
  `quor verify`/`quor doctor` both now show skip counts distinctly from
  failures. No compression behavior changed — verified via the benchmark
  suite (byte-identical results) and this repo's own dev/CI environment
  (tree-sitter installed: 88/88 still pass, nothing skipped).
  **Also fixed, found during pre-commit review:** the "install this extra"
  hint text was silently losing its `[javascript]` portion — Rich parses
  unescaped `[...]` in printed text as a style tag, and drops unrecognized
  ones. Confirmed this was a pre-existing class of bug (the `[filter-name]`
  failure-label prefix was equally vulnerable), fixed throughout via
  `rich.markup.escape()` / `markup=False`, with regression tests asserting
  the literal, un-mangled text.
  **`quor verify`'s output redesigned** as a dot-leader-aligned dashboard:
  `✓ name ... x/y` for a pass, a distinct `⊘ name ... skipped (optional
  dependency not installed)` for skip-only (never conflated with a pass),
  `✗ name` with failure detail below for a real failure — plus a footer
  listing the exact `pip install "quor[...]"` command(s) needed, derived
  from which languages were actually skipped.

## [0.4.0] — 2026-07-11

- **Designed: AST-aware code summarization architecture (QB-005A).** A design-only pass
  (`docs/design/QB-005A-ast-summarization-design.md`) for extending QB-005's Python-only AST
  compression to JavaScript/TypeScript, per CLAUDE.md Rule 4. Concludes AST parsing belongs inside
  a `StageHandler` (not before `Pipeline`, not inside `FilterRegistry`) producing `ContentMask`
  decisions exclusively, and recommends `tree-sitter` as a new, optional `quor[javascript]` extra
  for JS/TS — no pure-Python parser supports current TypeScript syntax, so this mirrors the
  already-shipped `quor[documents]` precedent (QB-007E2/E3) rather than a core dependency. No
  architectural conflict found. Flags a pre-existing gap (Read-based `.py` access gets no AST
  summarization today) and folds it into a phased plan (QB-005B–QB-005F). No code changed.
- **Added: AST parser framework, Python proof of concept (QB-005B).** New package
  `quor/pipeline/ast_summarize/` (`registry.py`: `language -> analyzer` routing;
  `python.py`: `analyze_python()`, wrapping QB-005's `_compressible_body_lines()`/
  `_body_line_range()` relocated unmodified). `python_ast_summarize.py` now delegates to this
  registry instead of calling `ast.parse()` directly — its `stage_type`, config shape, and every
  observable behavior are byte-for-byte unchanged (proven via a before/after snapshot diff across
  14 fixtures, plus the full pre-existing `TestPythonAstSummarize` suite passing unmodified).
  New generic stage, `code_ast_summarize` (`language: str` config field), dispatches through the
  same registry — one shared implementation of Python's body-compression logic, not two — but is
  not yet wired into any built-in filter (`cat-python.toml` unchanged). Unsupported-language
  fail-open is implemented in `apply()` rather than `can_handle()`, a documented, deliberate
  deviation from the design doc's original proposal: `can_handle(content, content_type)` has no
  access to `StageConfig` in any existing stage, and changing that Protocol was out of scope for
  this infrastructure-only phase. No new dependencies, no `tree-sitter`, no JS/TS parsing, no
  Read-hook integration, no benchmark changes — exactly QB-005A's own QB-005B scope. See QB-005B
  in `backlog.md` for the full writeup, including an unrelated local-environment note (this
  session's own shell commands were intercepted by a locally-installed Quor hook, whose
  25-second dispatcher subprocess timeout required running validation in batches — not a code
  defect).
- **Added: JavaScript AST summarization (QB-005C).** New analyzer,
  `quor/pipeline/ast_summarize/javascript.py` (`analyze_javascript()`), registered in QB-005B's
  framework alongside Python — `tree-sitter`/`tree-sitter-javascript` (new optional
  `quor[javascript]` extra) parse function/method/arrow-function bodies, imports, exports, class
  `extends` clauses, decorators, and JSDoc are preserved untouched. Implements the design's
  mandatory ERROR-node-overlap exclusion rule: a function whose own span overlaps a tree-sitter
  `ERROR`/`MISSING` node is never compressed, verified against two different real error-recovery
  shapes (a malformed signature that swallows trailing code into one error node vs. a localized
  body-only error that leaves sibling functions cleanly compressible). New filter,
  `cat-javascript.toml` (`.js`/`.jsx`/`.mjs`/`.cjs`), reuses QB-005B's generic
  `code_ast_summarize` stage — no JS-specific stage class. Missing `quor[javascript]`: fails open
  per-call (empty result, actionable warning), core install and Python summarization unaffected
  either way — verified via a before/after snapshot diff proving Python's output is still
  byte-for-byte unchanged.
  **Found and fixed a real, severe bug during implementation:** `tree-sitter==0.26.0` has a
  reproducible native-level memory-corruption bug (`Node.child_by_field_name()` + point-attribute
  access, repeated ~85+ times against one parsed tree, segfaults the process — a crash no
  `try/except` can catch). Root-caused via bisection (not guessed), confirmed absent in `0.25.2`
  at far larger scales, fixed by capping `pyproject.toml`'s `tree-sitter` dependency at `<0.26.0`.
  See QB-005C in `backlog.md` for the full bisection record and node-mapping design notes.
- **Added: TypeScript and TSX AST summarization (QB-005D).** New analyzer,
  `quor/pipeline/ast_summarize/typescript.py` (`analyze_typescript()` for `.ts`, `analyze_tsx()`
  for `.tsx` — two separate grammars, `tree-sitter-typescript`'s `language_typescript()`/
  `language_tsx()`, selected strictly by file extension, never inferred from content; verified
  empirically that JSX genuinely fails to parse under the plain `.ts` grammar). `interface`/`type`
  alias/`enum`/`namespace` declarations, overload signatures, and abstract classes/methods are all
  preserved whole by construction — never entered into the compress-candidate set, no special
  "preserve" code needed. New shared module, `quor/pipeline/ast_summarize/_treesitter_utils.py`,
  extracted from `javascript.py`'s own ERROR-node-overlap/body-range logic so both analyzers reuse
  the identical rule rather than each reimplementing it (`javascript.py`'s own behavior re-verified
  byte-for-byte unchanged after the extraction). New filter, `cat-typescript.toml` (two
  `[[filter]]` blocks — `cat-typescript` for `.ts`, `cat-tsx` for `.tsx`, mirroring `node.toml`'s
  own multi-block-per-file precedent), reuses the same `code_ast_summarize` stage
  `cat-javascript.toml` already uses. `tree-sitter-typescript` added to the same `quor[javascript]`
  extra (a deliberate choice, not a new `quor[typescript]` extra — see `typescript.py`'s own
  docstring). Missing dependency, malformed syntax, and unsupported-grammar cases all verified
  fail-open, same contract as JavaScript.
  **Mandatory pre-flight compatibility check, run before any analyzer code was written:**
  re-verified the QB-005C `tree-sitter==0.26.0` memory-corruption bisection specifically against
  both TypeScript grammars (2000 flat functions, 3000 nested class+method pairs, 200 repeated
  Language/Parser construction calls) — clean at every scale, confirming the bug does not reappear
  and the existing `tree-sitter<0.26.0` ceiling needs no change. Verified via a before/after
  snapshot diff that both Python's and JavaScript's output remain byte-for-byte unchanged. See
  QB-005D in `backlog.md` for the full record.
- **Added: JavaScript/TypeScript/TSX AST benchmark corpus and empirical evaluation (QB-005E).**
  12 new, realistic (not synthetic-repeated) sample fixtures under `tests/benchmarks/samples/`
  (5 JavaScript, 6 TypeScript, 1 TSX — short/medium/large files, a minified bundle, a
  heavily-commented file, interface-heavy, decorator-heavy NestJS-style, generic-heavy, and
  overload-heavy code) and matching `[[case]]` entries in `manifest.toml`, closing the temporary
  benchmark-coverage gap QB-005C/QB-005D's own scope had deferred. `min_reduction_pct` floors set
  from real measured values, not guessed. **Found and fixed two real `must_contain` bugs during
  implementation:** one case asserted JSX body content that the AST stage correctly compresses
  away (fixed to check the preserved signature instead), and one asserted a plain `//` comment
  that `strip_lines` genuinely removes, unlike a JSDoc block (fixed to check JSDoc instead) — both
  caught by tracing what actually survives compression, not by a failing test after the fact.
  `baseline.json` updated via the framework's own `--update-baseline` workflow; a programmatic
  diff confirmed exactly 12 entries added, 0 removed, 0 changed among the 48 pre-existing entries
  (`cat-python`'s own two cases confirmed byte-for-byte identical). New, deliberately separate
  script, `tests/benchmarks/ast_timing_analysis.py` (not wired into the pytest gate, not part of
  the regression-tracked manifest), measures parser-vs-pipeline time contribution, large-file
  scaling (roughly linear to 1000 synthetic functions), malformed-source/ERROR-node handling
  performance (no measurable overhead), and "nothing to summarize" cost (near-zero) — using
  synthetic inputs only for those specific operational measurements, deliberately kept separate
  from the realistic, regression-tracked corpus. `benchmark_runner.py`/`report.py`/
  `run_benchmarks.py`/`test_benchmarks.py` were not modified at all — the framework's own
  "adding a filter is a pure data change" design held exactly as advertised. See QB-005E in
  `backlog.md` for the full measured results.
- **Added: Read-hook AST integration, closing the QB-005 phased plan (QB-005F).** Reading a
  `.py`/`.js`/`.jsx`/`.mjs`/`.cjs`/`.ts`/`.tsx` file via Claude Code's native `Read` tool (not just
  `cat`'d through Bash) now genuinely compresses through the same `cat-python`/`cat-javascript`/
  `cat-typescript`/`cat-tsx` filters QB-005B–D already shipped and QB-005E already benchmarked —
  closing the pre-existing gap QB-005A's design doc flagged in its own Section 8/9. Only
  `quor/adapters/claude_read.py` changed: a new extension → filter-name mapping
  (`_SOURCE_CODE_FILTER_NAMES_BY_EXTENSION`) routes a matched Read `file_path` to the right filter
  **by name** (the same by-name lookup QB-007E4 already uses for extracted DOCX/PDF text, since a
  bare file path can never match any of these filters' `cat `-prefixed `match_command` patterns) —
  no new stage type, no new pipeline, no analyzer/filter behavior changes. A genuine code
  duplication was found between this new path and QB-007E4's own post-extraction tail (by-name
  lookup → apply → track → omit-if-unchanged) and extracted into one shared helper,
  `_compress_via_named_filter()`, used by both — not a new routing layer, and not a third
  copy-pasted implementation. Reuses `FilterRegistry`, the existing `code_ast_summarize`/
  `python_ast_summarize` stages, and QB-007D's tracking exactly as-is; Python/JS/TS/TSX Read
  invocations now aggregate into `quor gain` alongside Bash and document rows with zero
  Read-format-specific code. Every fail-open path (unsupported extension, invalid Python syntax,
  malformed JS/TS, missing `quor[javascript]`, a raising `FilterRegistry`) verified end to end
  through the real Read stdin → stdout contract, not just at the analyzer layer QB-005B–D already
  covered. A few pre-existing tests that used a `.py` path as their "this extension is
  unsupported" fixture (predating QB-005F) were updated to a still-genuinely-unsupported extension,
  with dedicated QB-005F coverage added instead. See QB-005F in `backlog.md` for the full record,
  including a pre-existing, out-of-scope limitation noted (not introduced by this change): source
  filters have no `max_tokens` `on_empty` fallback, so a pathological single-very-long-line file
  can compress to an empty string, identical to existing Bash `cat` behavior today.
- **Added: `PostToolUse`/`Read` hook plumbing (QB-007A).** A new adapter
  (`quor/adapters/claude_read.py`) and a second, additive hook registration
  (`claude-hook-read.ps1`, `hooks.PostToolUse`/matcher `"Read"`) let `quor init --claude` install
  the mechanism document compression is built on. `quor doctor` gains two checks
  (`Read hook script installed`, `Read hook responds correctly`). Existing `PreToolUse`/`Bash`
  behavior is untouched. Shipped alone as a deliberate no-op (always omitted `updatedToolOutput`);
  see QB-007C below for activation. See ADR-034 and QB-007 in `backlog.md`.
- **Added: Markdown and plain-text document filters (QB-007B).** Two new built-in filters —
  `markdown` (`.md`, `.markdown`) and `document-text` (`.txt`, `.rst`) — route by matching a Read
  tool file path against `FilterRegistry` exactly like a Bash command, using only existing stage
  types (`strip_lines`, `deduplicate_consecutive`, `max_tokens`; no new stage types). Headings,
  lists, fenced code block markers, requirement/decision IDs, and TODO/NOTE/WARNING callouts are
  protected via `preserve_patterns`; `max_tokens` is the only actual compression, engaging only
  once a document exceeds its 2000-token budget. Measured 29.5%/18.8% reduction on realistic
  long-document benchmark samples, 0% on short ones (correct, not a bug — see
  `docs/final/COMMAND_SUPPORT.md` §8). Shipped filter-layer-only, not yet reachable from a real
  Read call; see QB-007C below for activation. See QB-007B in `backlog.md` for full detail,
  including two documented, accepted limitations (fenced-code-block interiors are not
  span-protected; a file path containing a space does not match either filter).
- **Added: the Read hook now actually compresses (QB-007C).** `quor/adapters/claude_read.py`
  wires QB-007A's hook to QB-007B's filters via the existing `FilterRegistry`/`Pipeline` — a
  supported `.md`/`.markdown`/`.txt`/`.rst` Read now genuinely returns compressed content via
  `updatedToolOutput` when compression changes something; every other case (unsupported type,
  no-op compression, or any failure) correctly omits it, preserving fail-open. **Found and fixed a
  real bug during implementation:** the built-in `generic` Bash filter matches any non-empty
  string, so without a guard every unsupported Read file type (`.docx`, `.pdf`, `.py`, ...) would
  have been silently routed through a shell-output filter never designed for document content —
  fixed with an explicit, adapter-local filter-name allowlist that also incidentally neutralizes
  QB-007B's documented `cat.md` routing-collision limitation for real Read calls. No tracking/
  `quor gain`/DOCX/PDF/new-dependency work — exactly as scoped. See QB-007C in `backlog.md` for
  full detail.
- **Added: Read invocations now participate in tracking and `quor gain` (QB-007D).** Every Read
  call reaching `quor/adapters/claude_read.py` is now recorded as an `InvocationRecord` — same
  `command` column (`"Read: {file_path}"`), same SQLite/JSONL dual-write, same token accounting,
  same fail-open guarantee — via a single new shared helper, `track_invocation()`
  (`quor/tracking/db.py`), promoted out of `dispatcher.py`'s previously-private `_track()` so both
  the Bash dispatcher and the Read hook call the exact same recording logic instead of duplicating
  it. No schema change, no new table, no Read-specific storage or aggregation: `quor gain` picks up
  Read rows automatically because they're ordinary rows in the same `invocations` table. See QB-007D
  in `backlog.md` for full detail.
- **Added: document extraction framework, no extraction yet (QB-007E1).** New package
  `quor/pipeline/extract/` with a single public function, `extract(file_path: Path) -> str | None`
  — the extension-routed, fail-open preprocessing layer DOCX/PDF extraction will plug into. `.docx`/
  `.pdf` are registered but their handlers always raise `NotImplementedError` (absorbed silently, no
  warning — an expected state, not a bug); any other extension, including `.md`/`.txt`/`.rst` (which
  need no extraction at all), fails open the same way an unregistered extension does. Not a
  `StageHandler`; `Pipeline`, `FilterRegistry`, `ContentMask`, and `quor/adapters/claude_read.py` are
  all untouched — `extract()` isn't called from anywhere yet. No new dependencies. See QB-007E1 in
  `backlog.md` for full detail, including why DOCX/PDF extraction was split into four smaller,
  independently mergeable pieces (QB-007E1–E4) instead of one large PR.
- **Added: real DOCX-to-Markdown extraction (QB-007E2).** `quor/pipeline/extract/docx.py`'s
  `extract_docx()` fills in QB-007E1's `.docx` stub for real, using a new optional dependency,
  `python-docx` (`quor[documents]`; core install keeps working without it). Converts headings
  (`Heading 1`–`6` → `#`–`######`), paragraphs, bullet/numbered lists, GitHub-style tables
  (`|`-escaped, multi-paragraph cells joined with `<br>`), and contiguous code-style paragraphs
  (style name or explicit monospace font) into fenced blocks with indentation preserved — walking
  `document.element.body` directly so paragraph/table order is preserved, not python-docx's
  separate `document.paragraphs`/`.tables` flat lists. Document properties, comments, and
  headers/footers are excluded by construction (never read), not filtered after the fact. Fully
  fail-open on its own — missing `python-docx`, a corrupt file, an invalid zip, or any other parser
  exception all return `None` with a warning, and `extract_docx()` never raises regardless of what
  calls it. Still not wired into the Read hook, `FilterRegistry`, or `Pipeline` — all three, plus
  `quor/adapters/claude_read.py`, remain untouched. See QB-007E2 in `backlog.md` for the full
  algorithm, design trade-offs, and stated limitations (no nested lists, no bold/italic emphasis,
  merged table cells repeat rather than span, images/footnotes/headers/footers silently absent).
- **Added: real PDF-to-Markdown extraction (QB-007E3).** `quor/pipeline/extract/pdf.py`'s
  `extract_pdf()` fills in QB-007E1's `.pdf` stub for real, using a new optional dependency,
  `pdfplumber` (same `quor[documents]` extra as `python-docx`). Unlike DOCX, PDF has no document
  object model to read structure from — headings are inferred purely from font size (larger than
  the document's own body-text size, ranked into levels, clamped at `######`), paragraphs are
  reconstructed by merging lines whose vertical gap is small enough to be a wrapped continuation,
  bullets/numbers are recognized by regex against each line's own text (the PDF's visible number
  is reused verbatim; only the delimiter is normalized), tables use `pdfplumber`'s own table
  detection, and monospace-font lines merge into fenced code blocks with indentation reconstructed
  from character position. Fully fail-open on its own — missing `pdfplumber`, a corrupt file, an
  encrypted file, or any other parser exception all return `None` with a warning, never raise.
  **Found and fixed a real bug while building the benchmark fixtures:** an undecoded bullet glyph
  (no `ToUnicode` CMap) can decode as several zero-width placeholder characters at the bullet's own
  font size, which a naive character-count size heuristic let outvote real, visible body text on a
  short line — misrendering a bullet item as a heading. Fixed by weighting dominant line size by
  rendered character width instead of count; regression-tested against the pre-fix behavior. Still
  not wired into the Read hook, `FilterRegistry`, or `Pipeline`. See QB-007E3 in `backlog.md` for
  the full algorithm, the bug writeup, and stated limitations (geometry-based inference only, same
  no-nested-lists/no-emphasis limitations as DOCX, undecodable bullet glyphs fall through to plain
  paragraphs rather than being lost).
- **Added: DOCX/PDF Reads now genuinely compress end to end (QB-007E4).**
  `quor/adapters/claude_read.py` now calls `extract()` for `.docx`/`.pdf` Reads and routes the
  result through the existing `markdown` `FilterConfig` — looked up by name via
  `FilterRegistry.all_filters()`, since a `.docx`/`.pdf` command string would never match
  `markdown.toml`'s file-path pattern — no `docx.toml`/`pdf.toml`, no new stage, no new routing
  system. `original_tokens` is measured from the raw Read `tool_response`; `final_tokens` from
  whatever is actually returned, reusing `track_invocation()`'s existing call signature unchanged.
  **Found and fixed a real gap while wiring the benchmark manifest, surfaced to the user rather
  than silently resolved:** the benchmark harness had no extraction step at all, so it could not
  genuinely benchmark a binary `.docx`/`.pdf` sample file — fixed with a minimal extraction branch
  in `run_case()`. The QB-007E2/E3 benchmark fixtures are now wired into `manifest.toml`/
  `baseline.json` (4 new cases, purely additive — `docx-design-doc-long` 16.0%, `docx-readme-short`
  0.0%, `pdf-design-doc-long` 43.2%, `pdf-notes-short` 0.0%; the two long fixtures needed more
  content to demonstrate real compression, since `max_tokens`' budget is only charged against
  non-protected content). `Pipeline`, `ContentMask`, `FilterRegistry`, and the extraction framework
  itself (`quor/pipeline/extract/`) are all reused completely unchanged. See QB-007E4 in
  `backlog.md` for the full architectural writeup.
- **`quor gain` now explains negative-token rows instead of just softening
  their display.** Confirmed via a new invariant test
  (`TestFilterNeverExpandsOutput`) that no built-in filter stage can itself
  expand content — negative rows come from the tee recovery footer (ADR-023)
  or, in principle, a third-party plugin. `GainReport` gained two
  presentation-only derived fields, `gross_savings` and `gross_overhead`
  (`gross_savings − gross_overhead == tokens_saved`, always), computed at
  query time with no new tracking column or schema migration. `quor gain`
  shows a "Compression achieved" / "Recovery/overhead" breakdown and a
  plain-language explanation, but only when at least one invocation actually
  had a negative net — the common case is unchanged. See QB-017 in
  `backlog.md`.

## [0.3.0] — 2026-07-05

- **Fixed: `npm`/`npx`/`pnpm`/`yarn` never actually executed through the real
  dispatch path on Windows.** These tools ship as `.CMD` shell shims, not
  native `.exe` binaries; `subprocess.run()` without `shell=True` can't
  resolve them via Windows' `CreateProcess`, so every real invocation failed
  with `WinError 2` before any filtering could happen. `run_dispatch()` now
  resolves the executable via `shutil.which()` first, keeping `shell=False`
  (no new shell-injection surface). See ADR-033 and QB-019 in `backlog.md`.
- **Added: benchmark coverage for every built-in filter.** The compression
  benchmark suite (QB-011) covered only 6 of 14 filter categories; `ruff`,
  `eslint`, `npm`, `npx`, `pnpm`, `yarn`, `cat`, and `cat-python` had none.
  All 14 categories now have committed baseline cases (28 total). See
  ADR-032.
- **Added: `docs/final/COMMAND_SUPPORT.md`**, the canonical reference for
  every supported command, which filter handles it, command detection
  rules, and filter precedence — consolidates detail previously scattered
  or missing across README/CLAUDE.md/PROJECT_BIBLE.md.
- Strengthened the AI-assisted Git workflow (`docs/final/CLAUDE.md`) with
  pre-PR benchmark/regression requirements, a review checklist, and a
  release-readiness checklist.
- Test count: 983 (was 614), reflecting the above plus accumulated coverage
  from QB-013 (tee), QB-018 (gain project-identity fix), and QB-019.
- **Added: pipeline-level early exit, an optimization only (QB-036 — requested and tracked in
  conversation as "QB-009," refiled since that ID was already a completed, unrelated item; see
  QB-036 in `backlog.md` for the full numbering note).** `Pipeline.execute()` now skips any suffix
  of remaining stages once the `ContentMask` is "fully decided" (no `KEEP` lines left) and every
  one of those remaining stages is on a small, hand-audited allowlist
  (`_STAGE_TYPES_INERT_ON_DECIDED_LINES` in `quor/pipeline/engine.py`) with an empty
  `preserve_patterns` — never changes rendered output, only whether a stage is actually invoked.
  **Found, and designed around, a real pre-existing subtlety while auditing every built-in stage's
  `apply()` (not a bug fix — nothing was changed about it):** `Decision.COMPRESS` is not
  engine-enforced immutable the way `PROTECT` is; `group_repeated`/`max_tokens`/`remove_ansi` can
  each, if configured with `preserve_patterns`, promote an already-`COMPRESS` line back to
  `PROTECT`, and `match_output`'s whole-render collapse can't be predicted from `Decision` state at
  all — both are why the allowlist requires an empty `preserve_patterns` and excludes
  `match_output` unconditionally, rather than relying on a blanket "no KEEP lines left" rule. See
  ADR-035. `FilterRegistry.apply()` (Bash/Read hooks, benchmarks, `quor verify`) has the
  optimization on by default; `FilterRegistry.trace()` (`quor explain`) explicitly disables it so
  its diagnostic stage-by-stage view is completely unaffected. The skip-eligibility check itself
  fails open (falls back to running the stage for real on any exception). Zero `StageHandler`
  implementations, zero filter `.toml` files, and zero existing pipeline stage configs were
  changed — the allowlist reuses `StageHandler.stage_type` and the already-existing
  `StageConfig.preserve_patterns` field; no new abstraction was introduced. Verified byte-for-byte
  identical output across every built-in filter's own inline tests and all 60 benchmark corpus
  cases with the optimization forced on vs. off (`tests/unit/test_early_exit.py`,
  `tests/benchmarks/early_exit_analysis.py` — the latter a deliberately separate script, not wired
  into the pytest gate, mirroring `ast_timing_analysis.py`'s QB-005E precedent). Measured
  real-world impact, reported honestly: early exit fires in 2 of 60 benchmark corpus cases (both
  `mypy`), with an aggregate timing delta within measurement noise — `python_ast_summarize`/
  `code_ast_summarize` are always the first stage in the filters that use them, so this
  optimization can never skip the expensive AST parse itself, only cheap trailing stages, by
  construction.
- **Designed: multi-agent adapter architecture (QB-035A).** A design-only pass
  (`docs/design/QB-035A-multi-agent-adapter-design.md`, ADR-036) for supporting AI coding agents
  beyond Claude Code without duplicating compression logic or branching on agent names — no new
  agent implemented, no runtime behavior changed, consistent with `ANTI_GOALS.md` #12 ("no
  multi-agent support in V1"). **Headline finding:** `quor/rewrite/`, `FilterRegistry`, `Pipeline`
  (all stages, `extract/`), and `quor/tracking/db.py` are already 100% agent-agnostic — verified by
  grepping every one of them for any agent-name reference and finding none. All agent-name coupling
  is concentrated in exactly four places: `__main__.py`'s hardcoded `_HOOK_ADAPTERS` set/if-else,
  `init.py`'s Claude-settings.json-specific logic behind a single `--claude` flag, `doctor.py`'s
  hardcoded Claude-specific check functions, and `quor/adapters/base.py`'s already-declared but
  entirely unused `HookAdapter` Protocol — which `PROJECT_BIBLE.md`'s original architecture diagram
  shows was intended as a multi-adapter extension point from the project's first design pass,
  never implemented past the reference adapter. Proposes `AgentEvent` (a closed, two-value event
  abstraction: `COMMAND_INTERCEPT`/`CONTENT_INTERCEPT`, mapped from Claude Code's
  `PreToolUse`/`PostToolUse` today), the `AgentAdapter` Protocol (`bytes`-in/`bytes`-out
  `handle_event()`, plus `install()`/`doctor_checks()` for the CLI), and `AdapterRegistry` — a
  built-in dict plus a new `quor.hook_adapter` entry-point group, mirroring the existing
  `quor.compression_stage`/`quor.plugin` discovery mechanism (ADR-026) exactly. **Genuine, real
  duplication found between `claude.py` and `claude_read.py` (independently re-implemented BOM-
  stripping and stdio boilerplate) was surfaced and explained, not fixed** — per this task's
  explicit "stop and explain before changing anything" instruction; the proposed `bytes`-in/
  `bytes`-out contract retires it as part of a future migration, not this phase. Also documents an
  empirical, informally-observed data point worth recording: both existing adapters already strip a
  doubled UTF-8 BOM specifically because Cursor is known to send one (`PROJECT_BIBLE.md` item 9) —
  suggestive, not confirmatory, that a first additional adapter may need less novel payload-parsing
  work than assumed; explicitly flagged as unverified and a real risk for whichever future phase
  implements it. Full 6-step migration plan, 6 named risks, 4 design trade-offs with rejected
  alternatives, and a complete file-by-file list of what would eventually need to change are in the
  design doc; remaining work is split into QB-035B–F (see `backlog.md`).
- **Fixed: `python_ast_summarize`'s expected fail-open warning printing during
  `quor verify`.** `FilterRegistry.run_tests()` now captures warnings raised
  while applying a test's input and discards them if the test passes — a
  passing test (including one whose fixture deliberately triggers a stage's
  own fail-open path, like `cat-python.toml`'s "Invalid Python fails open"
  case) proves the warning was exactly what the fixture intended. A failing
  test keeps its captured warnings appended to that test's own failure
  message. Generic: no stage type, exception type, or warning category is
  special-cased. Real compression (`apply()` called directly by the
  dispatcher/Read hook, not through `run_tests()`) is unaffected — warnings
  there still print normally.
- **Improved: `quor gain` clarity (calculation unchanged).** Added a notice
  when zero Read-hook invocations have ever been recorded for the selected
  window, explaining that Read-only features (Markdown/DOCX/PDF/AST-via-Read)
  aren't represented and that `quor init --claude` is required. Reworded the
  char/4 token-estimation footnote so it's clear the ±20% applies to the
  token *counts*, not that the savings percentage itself is separately
  uncertain. Renamed "Recovery/overhead" to "Recovery-footer overhead" for
  clarity. The "Mode: audit" line no longer reads as though it qualifies the
  compression statistics directly beneath it — annotated only for non-default
  mode values, since `mode` never reaches the compression path itself.
- **Investigated: `quor verify` warning — confirmed already fixed, no
  further change.** Re-traced the exact execution path and reproduced
  `quor verify` fresh in two shells; clean, 0 warnings, every time. The
  earlier fix (above) already covers this; the original report predated
  this session's merge of that fix into `main`.
- **Fixed: `quor init --claude` printing "Tee adaptive-disable state
  cleared." unconditionally.** Root cause: `init.py` called the
  Typer-decorated `doctor()` directly as plain Python, so `reset_tee`
  received the unresolved `typer.Option(...)` sentinel (truthy) instead of
  its real default. Split `doctor()` into a thin Typer wrapper and a plain
  `_run_doctor(*, settings_path=None, reset_tee=False)` function with real
  Python defaults; `init.py` now calls `_run_doctor()` directly. Regression
  test added.
- **Added: schema-aware hook configuration health checks.** New
  `quor/adapters/hook_manifest.py` — a declarative `ClaudeHookSpec` per hook
  (event, matcher, script name, template, own `schema_version`), iterated by
  both `quor init --claude` (install) and `quor doctor` (health check)
  instead of two hand-copied function pairs. Closes a real gap: `doctor`
  previously only checked that a hook *script file* existed, never that
  `settings.json` actually registered it — a stale/partial install could
  show "Hook script installed" ✓ while Quor was never wired into Claude
  Code. New "registered in settings.json" and "up to date" checks close
  this; the latter compares a `# quor-hook-schema: N` line embedded in each
  generated script against that hook's own `spec.schema_version` —
  deliberately **not** `quor.__version__`, so a Quor release that doesn't
  change a hook's definition never tells users to reinstall it; only a real
  change to a hook's template/registration shape bumps its
  `schema_version`. A future hook needs one manifest entry to get install
  support and all three generic checks for free — only its behavioral
  (roundtrip) check still needs hand-written code, since that inherently
  needs a hook-specific synthetic payload. Reuses QB-035A's "declarative
  hook list" design conclusion at V1/Claude-only scope (no multi-agent
  Protocol, per ANTI_GOALS.md #12). Also fixed a related UX bug found along
  the way: `doctor`'s check-detail lines could word-wrap mid-phrase
  (splitting `` `quor init --claude` `` across a line break) when a long
  path pushed the line past console width — fixed with `soft_wrap=True`.
- **Redesigned: `quor gain` output as a dashboard (presentation only, no
  calculation changed).** Notices (Read-hook coverage gaps, recovery-footer
  overhead) now print together under one `NOTICE` header before any
  statistic, instead of interleaved as inline paragraphs. The savings
  headline (`YOU SAVED`/`NET TOKENS`) now leads the statistics section
  instead of trailing three stacked mini-tables; those tables collapsed into
  one compact table. Long explanatory paragraphs shortened to one or two
  lines. The `±20%` uncertainty label stays directly on the headline number
  (ANTI_GOALS.md #24). See QB-037 in `backlog.md` for the three alternative
  layouts considered and why this one was chosen.

## [0.2.1] — 2026-07-04

- **PreToolUse hook now emits the response shape Claude Code actually reads.**
  The hook adapter (`quor/adapters/claude.py`) used to rewrite
  `tool_input.command` in place and echo the whole mutated input payload back
  to stdout. Claude Code only honors `hookSpecificOutput.updatedInput` for
  overriding tool arguments — a bare top-level `tool_input` key is silently
  ignored — so the rewrite never reached execution, and `quor gain` never
  recorded real invocations. The hook now emits
  `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision":
  "allow", "updatedInput": {...}}}`, omitting `updatedInput` entirely when no
  rewrite applies. Verified end-to-end against the real Claude Code binary
  (not just in-process unit tests). See ADR-030 in DECISIONS.md.
- Fixed `.github/workflows/canary.yml`, which still asserted the old
  `tool_input`-echo shape and would have falsely reported a Claude Code
  protocol change on its next scheduled run.
- No user-facing action required beyond upgrading the package — the installed
  PowerShell hook script is unchanged; it invokes `python -m quor hook claude`,
  which now returns the corrected response automatically.

[0.2.1]: https://github.com/priyanshup/Quor/releases/tag/v0.2.1

## [0.2.0] — 2026-07-04

- **Rewritten commands no longer depend on the `quor`/`qr` launcher stubs.**
  The PreToolUse hook used to rewrite `git status` to the bare word
  `quor git status`, which Claude Code would then run by resolving `quor` on
  PATH — hitting the pip-generated `quor.exe`/`qr.exe` console-script
  launcher. Some corporate application-control policies block that launcher
  outright while allowing `python.exe` itself, which made every Quor-rewritten
  command fail on those machines even though `python -m quor` worked fine.
  Rewritten commands now invoke the exact interpreter already running Quor
  (`sys.executable -m quor ...`), generated by a single new helper,
  `get_quor_invocation()` (`quor/rewrite/invocation.py`), so the launcher is
  never on the runtime path — it remains installed only as a manual-use
  convenience (`quor doctor`, `quor init --claude`, etc., typed directly by a
  user in an unrestricted shell).

[0.2.0]: https://github.com/priyanshup/Quor/releases/tag/v0.2.0

## [0.1.1] — 2026-07-02

Documentation-only release. No changes to `quor`'s source code or behavior.

- Reconciled `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, and the
  `docs/final/` canonical status docs with the actual released v0.1.0
  package — removed stale "not yet on PyPI" / "Internal Alpha" language.
- Added a Quick Start and a Troubleshooting section to `README.md`
  (PATH issues, `py` launcher, multiple Python versions, corporate
  `.exe`-execution blocking, Windows path-length limits), based on real
  multi-machine install verification.
- Fixed a documentation bug: `CONTRIBUTING.md` told bug reporters to run
  `quor --version`, which does not exist as a CLI flag; replaced with
  `pip show quor` throughout.
- Documented the actual, now-automated TestPyPI/PyPI release process in
  `CONTRIBUTING.md`'s Release Process checklist.

[0.1.1]: https://github.com/priyanshup/Quor/releases/tag/v0.1.1

## [0.1.0] — 2026-07-01

First public release, published to [PyPI](https://pypi.org/project/quor/).
Quor is a rule-based command-output
optimization and context-compression layer for AI coding assistants: it
runs your command, captures the output, and applies a deterministic,
fail-open filtering pipeline before the output reaches the assistant's
context window.

### Core pipeline (Phases 0-6)

- **ContentMask pipeline** — the `KEEP` / `COMPRESS` / `PROTECT` line-level
  decision model that every compression stage operates on. `PROTECT` is
  immutable once set: no later stage can downgrade it (ADR-003, "Core
  Abstraction — ContentMask").
- **Five built-in compression stages** — `remove_ansi`, `strip_lines`,
  `deduplicate_consecutive`, `group_repeated`, `max_tokens`.
- **Five built-in filters** — `git`, `pytest`, `build` (mypy/ruff), `cat`,
  and a generic ANSI+truncation fallback, each with inline TOML tests.
- **Three-tier filter registry** — project > user > built-in precedence
  (ADR "Filter Registry — Three-Tier Lookup").
- **Command rewriter and classifier** — quote-aware shell lexer, rule-based
  command classification, 100+ fixture-driven tests.
- **Claude Code hook adapter** — intercepts the `PreToolUse` hook, rewrites
  the command to route through Quor, preserves every extra JSON field
  Claude Code sends.
- **SQLite + JSONL dual tracking** — background-thread writes, WAL mode,
  never blocks the hook path (ADR "Persistence — Dual (SQLite + JSONL)").
- **Six CLI commands** — `init --claude`, `validate`, `explain`, `gain`,
  `verify`, `doctor` — plus the `schema` utility command for the filter
  JSON Schema. Both `quor` and `qr` are registered entry points.

### Plugin Infrastructure (Phase 8)

- Public `Plugin` Protocol (`quor.plugins.base`) — `@runtime_checkable`,
  versioned via `QUOR_PLUGIN_API_VERSION`, lifecycle-managed
  (`initialize` / `execute` / `shutdown`).
- `PluginRegistry` — three-tier registration (project > user > builtin),
  deterministic execution order, fully fail-open execution.
- Deliberately kept separate from the existing `StageHandler` Protocol:
  `StageHandler` is TOML-configurable, line-level, stateless compression;
  `Plugin` is Python-coded, lifecycle-managed middleware for telemetry,
  policy, and routing (ADR "Plugin Architecture — Two-Tier Separation").

### Plugin Discovery & Loading (Phase 9)

- Entry-point discovery for both `quor.compression_stage` and `quor.plugin`
  groups via `importlib.metadata`, with a package-set-hash-invalidated
  local cache.
- `api_version` compatibility check accepts any version `<= QUOR_PLUGIN_API_VERSION`
  and rejects only newer ones — plugins built against an older API keep
  working as the API evolves.
- `file://` escape hatch for loading a local `StageHandler` during
  development without packaging it.
- `quor doctor` plugin diagnostics: lists discovered stages and plugins
  (including each plugin's declared version), and flags load failures.
  Tier is deliberately not reported for entry-point-discovered plugins —
  `importlib.metadata` carries no signal that maps to project/user/builtin,
  so this is a documented scope boundary, not a bug (see DECISIONS.md,
  ADR "Plugin Architecture — Two-Tier Separation").
- End-to-end fail-open verification: a real (non-mock) plugin that raises
  during `execute()` is driven through the actual dispatcher, confirming
  the exception is isolated, a warning is emitted, and the hook still
  returns valid output.

### Release Hardening

A dedicated pass to close reliability gaps before packaging:

- Eliminated the last local-machine dependency in the test suite (CLI
  tests previously read the developer's real `~/.claude/settings.json`);
  all tests now inject an isolated settings path.
- `ruff` and `mypy` are exact-pinned in dev dependencies; `pytest`/`pytest-cov`
  use bounded ranges. CI now lints `tests/` as well as `quor/` (ADR
  "Release Hardening — Dev Tooling Version Policy & CI Lint Scope").
- Removed `ExitCode.PLUGIN_ERROR` as dead code — every `PluginError` is
  caught internally by Quor's fail-open contract and never reaches a
  process exit code.
- Python compatibility verified by actually running the full suite (ruff,
  mypy, pytest) on Python 3.11, 3.13, and 3.14 in isolated virtual
  environments, not just static review.

### Testing

- **605 tests passing**, `ruff` and `mypy` clean on both `quor/` and
  `tests/`.
- ≥80% coverage on `quor/pipeline/`, `quor/filters/`, and `quor/rewrite/`
  (93% overall).
- Dedicated chaos/fail-open suite: corrupted TOML, malformed hook JSON,
  permission errors, hook timeout, pathological regex (ReDoS) — all
  degrade to the original, unfiltered output rather than crashing or
  losing data.
- Error-safety snapshot tests across all 7 built-in filters, confirming
  failure-relevant lines are never removed.
- CI on `windows-latest` and `ubuntu-latest`; a weekly canary workflow
  installs unpinned `@anthropic-ai/claude-code` to catch upstream hook
  format changes before users do.

### Known limitations

- The `AUDIT` / `OPTIMIZE` / `SIMULATE` operating-mode system is
  display-only in this release — `quor doctor` and `quor gain` show the
  configured mode, but the dispatcher does not yet branch on it. This is
  an intentional, scoped roadmap item (see PROJECT_STATUS.md), not a bug.
- No `quor --version` flag yet — check the installed version with
  `pip show quor`.

[0.1.0]: https://github.com/priyanshup/Quor/releases/tag/v0.1.0
