# COMMAND SUPPORT
## Quor — Supported Commands, Filters, and Extension Guide

> This is the canonical, single source of truth for **what Quor rewrites, which filter
> processes it, and what that filter does and does not optimize.**
> If any other document (README, CLAUDE.md, PROJECT_BIBLE.md) appears to disagree with this
> file on a specific command's behavior, this file wins — update the other document to link
> here instead of restating the detail.
>
> Ground truth for this document is `quor/rewrite/rules.py` (command detection) and
> `quor/filters/builtin/*.toml` (filter behavior). When in doubt, run
> `quor explain "<your command>"` — it shows the live decision for any command on your
> installed version, which will always be more current than prose.

---

## 1. How command detection works

Quor does not rewrite every command it sees. The Claude Code PreToolUse hook only rewrites a
command if the classifier (`quor/rewrite/classifier.py`, knowledge tables in
`quor/rewrite/rules.py`) recognizes it. Everything else passes through untouched and
unrecorded — this is intentional (see `docs/final/ANTI_GOALS.md` — Quor never silently expands
its own scope), but it means **"the hook is installed" does not mean "every command is
tracked."** Use `quor explain "<command>"` to check any specific command.

Detection runs in this order:

1. **Heredoc check.** Commands containing `<<` are never rewritten (rewriting could corrupt
   heredoc parsing). Passed through unchanged.
2. **Pipe-target check.** If the command pipes into `xargs`, `awk`, `sed`, `perl`, `python`/
   `python3` (as a bare script-stdin consumer), `jq`, or `fx` (`PIPE_INCOMPATIBLE_COMMANDS` in
   `rules.py`), it is not rewritten — Quor's output would corrupt what the downstream tool
   expects to parse.
3. **Structured-output flag check.** If the command includes `--json`, `--format=json`,
   `--format=JSON`, `--output=json`, `--porcelain`, `--porcelain=v1`, or `--porcelain=v2`
   (`STRUCTURED_OUTPUT_FLAGS`), it is not rewritten — these flags mean the caller wants
   machine-readable output, and filtering would break a downstream parser.
4. **Transparent-prefix peeling.** If the command starts with `sudo`, `doas`, `bunx`, `deno`,
   `time`, `env`, or `nice` (`TRANSPARENT_PREFIXES`), or the multi-word forms `docker exec
   <container>` / `podman exec <container>` (`TRANSPARENT_MULTI_WORD_PREFIXES`), Quor inserts
   itself *after* the prefix and re-evaluates the remaining words. `npx`, `yarn`, and `pnpm` are
   deliberately **not** transparent prefixes — as of QB-006A they are known base commands in
   their own right (see below), so their own wrapper noise gets filtered regardless of what
   they wrap underneath.
5. **Known-command check** (`is_known_command()`). The (possibly prefix-peeled) base command
   must be one of `_KNOWN_BASE_COMMANDS`:

   ```
   git, pytest, mypy, ruff, cat, python, python3, npm, npx, pnpm, yarn,
   tsc, jest, vitest, prettier, next, turbo
   ```

   `python` / `python3` are gated further: they only count as known if invoked as
   `python -m <subcommand>` where `<subcommand>` is one of `_KNOWN_PYTHON_SUBCOMMANDS`
   (`pytest`, `mypy`, `ruff`). A bare `python script.py` is never rewritten.

6. **`cat`-specific flag check** (`cat_is_safe_to_rewrite()`). `cat` is only rewritten if every
   flag present is a content-display flag (`-n`, `--number`, `-b`, `--number-nonblank`). Any
   other flag (`-e`, `-v`, `-A`, etc.) leaves the command unrewritten, since those flags change
   what bytes `cat` actually emits and Quor's filters assume plain text.

If a command fails any of these checks, it passes through **completely unmodified** — Quor
never runs, never records it, and `quor gain` will never show it. This is the fallback
behavior at the *rewrite* layer, distinct from the *filter* fallback described in §3.

## 2. Filter precedence

Once a command is rewritten, `quor/filters/registry.py`'s `FilterRegistry.find()` picks which
filter actually processes the output. Precedence has two layers:

**Tier precedence (highest wins):**
1. **Project** — `.quor/filters/*.toml` in the repository root, only if git-tracked
   (see ADR-010). Loaded in filename-sorted order.
2. **User** — `~/.config/quor/filters/*.toml` (via `platformdirs`). Always trusted. Loaded in
   filename-sorted order.
3. **Built-in** — `quor/filters/builtin/*.toml`, bundled with the package. Loaded in
   filename-sorted order (`sorted(_BUILTIN_DIR.glob("*.toml"))`).

**Within a tier, first match wins**, in load order. There is no explicit priority field —
specificity is achieved entirely through **file and block ordering**:

- `cat-python.toml` sorts before `cat.toml` (`-` is `0x2D`, `.` is `0x2E`), so a `.py` file
  matches the AST-aware filter first; anything else falls through to the generic `cat` filter.
- `z_generic.toml` is prefixed `z_` specifically so it sorts last among built-ins — its
  `match_command = '.'` matches literally everything, so it must never load before a more
  specific filter has had a chance to match.
- Within `node.toml`, the `eslint`, `tsc`, `jest`, `vitest`, and `prettier` `[[filter]]` blocks are
  all declared *before* the generic `npm`/`npx`/`pnpm`/`yarn` blocks in the same file, because
  `[[filter]]` blocks preserve TOML declaration order. If `eslint` were declared after the generic
  `npm` block, `npm exec eslint` would always match the generic `npm` filter and the
  eslint-specific routing would never fire — same reasoning applies to each of the other
  tool-aware blocks (QB-006C). `next lint` is matched by the `eslint` block itself (added to its
  own `match_command`, not a new block) since it runs ESLint under the hood and produces
  identical output — first-match-wins means it's picked up before the broader `next` block later
  in the file, with no negative lookahead required.

If you add a new filter that should take precedence over an existing built-in for the same
command shape, you must control this through naming/ordering (or a project/user-tier override),
not a priority number — there isn't one.

## 3. Fallback behavior

If no project, user, or built-in filter matches, the command is not rewritten in the first
place (§1) — the fallback described here is specifically the built-in **generic filter**
(`quor/filters/builtin/z_generic.toml`, `match_command = '.'`), which is the last built-in
filter loaded and matches any command that *did* pass the rewrite check but has no
command-specific filter (e.g. a build tool with generic noise that hasn't been given its own
filter yet). It strips ANSI escapes, deduplicates consecutive lines, and caps output at 1000
tokens (`tail` strategy) — no command-specific pattern knowledge at all. See its row in the
table below.

If a command is rewritten but genuinely *no* filter matches at any tier (which should not
happen in practice, since the built-in generic filter matches everything) the pipeline records
`was_passthrough = 1` and returns the original content unmodified.

## 4. Supported commands and filters

Every row below corresponds to one `[[filter]]` block in a built-in TOML file. "Optimizes" and
"does NOT optimize" describe intent, not just the current pattern list — see the linked source
for the exact patterns.

| Command(s) matched | Filter (source file) | Optimizes | Does NOT optimize | Example | Known limitations |
|---|---|---|---|---|---|
| `git status` | `git-status` ([git.toml](../../quor/filters/builtin/git.toml)) | Strips "how to stage/unstage" help hints and clean-tree boilerplate; dedupes consecutive lines | Never strips `modified:`/`deleted:`/`renamed:`/`new file:`/conflict/`Unmerged` lines (all `PROTECT`) | `git status` on a repo with 40 unchanged tracked files but 2 modified — output shrinks to the 2 modified lines | Only recognizes English git output; a non-English locale's status messages won't match the strip patterns (fails open — nothing removed, not a crash) |
| `git log` | `git-log` ([git.toml](../../quor/filters/builtin/git.toml)) | Strips `Author:`/`Date:`/`Merge:` lines; caps to 400 tokens (`head` strategy) | Never strips the commit line, or lines containing `fix`, `feat`, or `BREAKING` | `git log` on a long history — keeps commit subjects, drops author/date noise | `head` strategy means very long history is truncated from the *bottom* (older commits) first; if you need older history, use `git log <range>` directly or read the tee'd original (ADR-023, not yet implemented — QB-013) |
| `git diff`, `git show` | `git-diff` ([git.toml](../../quor/filters/builtin/git.toml)) | Strips diff index headers (`index `, `diff --git`, `--- a/`, `+++ b/`) and blank context lines; caps to 600 tokens (`both` strategy) | Never strips `+`/`-`/`@@` hunk lines, or lines containing `conflict`/`Error` (all `PROTECT`) | Small single-file diff — nearly everything survives, since diff content is already signal | A diff with many real hunks can exceed the 600-token budget — this is intentional (ADR-031: `max_tokens` is best-effort, `PROTECT` is absolute, never the reverse) |
| `mypy`, `python -m mypy` | `mypy` ([build.toml](../../quor/filters/builtin/build.toml)) | Collapses the *same* error message repeated ≥3 times at different line numbers to one instance + `(×N)`; strips the success/found-N-errors summary lines when errors are present | Never strips any `error:`/`warning:`/`note:` line, or `Error` (all `PROTECT`); short-circuits entirely (`abort_unless`) on a clean run | 4 identical `Argument 1 to "save"` errors at different lines → 1 line + `(×3)`, plus any distinct errors kept individually | `group_repeated` here defaults to shape-based matching (not `exact_match`) — this is deliberate for mypy's "same message, different line number" pattern; do not set `exact_match=true` on this filter, it would stop the collapsing entirely |
| `ruff check`, `python -m ruff check` | `ruff` ([build.toml](../../quor/filters/builtin/build.toml)) | Strips the "all checks passed" / "found N errors" / "N fixable" summary lines when violations are present | Never strips any violation line (`error:`, `Error`, `warning:`); short-circuits on a clean run | 3 lint violations across 2 files — all 3 survive, only the trailing summary is removed | No repetition collapsing (no `group_repeated` stage) — a lint rule violated 50 times produces 50 lines, unlike mypy |
| `pytest`, `python -m pytest` | `pytest` ([pytest.toml](../../quor/filters/builtin/pytest.toml)) | Strips per-test `PASSED`/dot-progress lines, session banners, collection lines; strips framework-internal traceback frames (`File "...", line N, in ...` where the path contains `site-packages`/`dist-packages` — Django/Flask/library internals); dedupes; caps to 500 tokens (`tail`) | Never strips `FAILED`, `ERROR`, `Exception`, `assert`/`AssertionError`, or `Traceback` lines; the user's own project frames (any `File` line without `site-packages`/`dist-packages` in the path) always survive | 500-test suite, 1 failure through 4 Django internal frames — the 4 framework `File` lines are removed, the user's own frame and the exception message survive | Pattern-based `PASSED`/dot detection assumes pytest's default output format; a heavily customized reporter plugin may not match the strip patterns (fails open). Only the frame *header* line is stripped for a framework frame — its indented source-code line has no distinguishing marker of its own and is deliberately left in place rather than risk dropping real content. Bare stdlib frames (no `site-packages`/`dist-packages` in the path) are not stripped — no cross-platform-safe way to distinguish them from a user's own code (a Windows stdlib path has no marker as unambiguous as `site-packages`) |
| `npx eslint ...`, `npm exec eslint ...`, `pnpm exec/dlx eslint ...`, `yarn exec eslint ...`, bare `yarn eslint ...`, `next lint ...` | `eslint` ([node.toml](../../quor/filters/builtin/node.toml)) | Strips ANSI, blank lines; collapses **byte-identical** repeated diagnostic lines (`exact_match=true`) to `(×N)`; caps to 400 tokens (`tail`) | Never strips lines containing `error`/`warning`/`problem`/`fixable` (case-insensitive) or `✖`; short-circuits on a clean run | Same rule violated at 4 different line:column pairs → all 4 lines kept distinctly (different diagnostics, not collapsed) | Routing is pure command-string matching — `npm run lint` (a `package.json` script alias) is **never** routed here even if that script runs eslint under the hood, because resolving the alias would require reading `package.json` (explicitly out of scope, QB-006B). `next lint` is routed here (QB-006C) since it runs ESLint under the hood |
| Bare `tsc ...`, `npx tsc ...`, `npm exec tsc ...`, `pnpm exec/dlx tsc ...`, `yarn exec tsc ...`, bare `yarn tsc ...` | `tsc` ([node.toml](../../quor/filters/builtin/node.toml)) | Strips the `Found N errors...` summary line and blank lines; caps to 400 tokens (`tail`) | Never strips any `error TSxxxx:` diagnostic line; short-circuits on a clean run (real `tsc` prints nothing at all on success, unlike mypy's success sentinel) | 3 distinct diagnostics across 3 files — all 3 survive individually, only the trailing summary is removed | **Deliberately no `group_repeated` stage** (QB-006C) — tried first, then rejected: shape-based grouping on the generic `error TS\d+:` pattern would merge genuinely different, unrelated diagnostics that happen to share that shape (unlike mypy's narrower "same message, different line" scenario), silently losing distinct error text. Same conclusion `ruff` already reached for lint violations |
| Bare `jest`, `npx jest`, `npm exec jest`, `pnpm exec/dlx jest`, `yarn exec jest`, bare `yarn jest` | `jest` ([node.toml](../../quor/filters/builtin/node.toml)) | Strips `PASS` suite lines and the "Ran all test suites" banner; strips framework-internal stack frames through `node_modules` (mirrors pytest's `site-packages`/`dist-packages` distinction); caps to 500 tokens (`tail`) | Never strips `FAIL` lines, `●` failure-detail bullets, `Expected:`/`Received:`, `Test Suites:`/`Tests:` summary lines; short-circuits on an all-passing run | A failing suite through a `node_modules/jest-runtime` internal frame — the library frame is removed, the user's own frame (`src/bar.test.js:10:20`) and the assertion detail survive | Pattern-based `PASS`/`FAIL` detection assumes jest's default reporter; a heavily customized custom reporter may not match (fails open) |
| Bare `vitest`, `npx vitest`, `npm exec vitest`, `pnpm exec/dlx vitest`, `yarn exec vitest`, bare `yarn vitest` | `vitest` ([node.toml](../../quor/filters/builtin/node.toml)) | Strips passing `✓` checkmark lines and `node_modules` stack frames; caps to 500 tokens (`tail`) | Never strips `×`/`❯`/`→` failure markers, `error`/`expected` text, or the `Test Files`/`Tests` summary lines; short-circuits on an all-passing run | Same shape as `jest` above but a genuinely distinct output format (unicode symbols, not `PASS`/`FAIL` text) — kept as its own filter rather than reusing jest's, since the strip/preserve patterns don't overlap | Same custom-reporter caveat as `jest` above |
| Bare `prettier`, `npx prettier`, `npm exec prettier`, `pnpm exec/dlx prettier`, `yarn exec prettier`, bare `yarn prettier` | `prettier` ([node.toml](../../quor/filters/builtin/node.toml)) | Strips the "Checking formatting..." banner; caps to 400 tokens (`tail`) | Never strips `[warn]` file lines, the "Code style issues found" summary, or any `error`/`Error` text; short-circuits on a clean run | 2 files needing formatting — banner removed, both `[warn]` lines and the summary survive | Low-noise tool by nature — modest compression. Doesn't strip a wrapping `npx`'s own auto-install preamble (e.g. `npm warn exec ...`); that's a separate wrapper-layer concern intentionally out of scope for the tool-specific filter, same as `eslint` above |
| `npm ...` (anything not routed to `eslint`/`tsc`/`jest`/`vitest`/`prettier` above) | `npm` ([node.toml](../../quor/filters/builtin/node.toml)) | Strips ANSI; collapses repeated `npm warn deprecated` lines; strips funding/audit-nag boilerplate; dedupes | Never strips `npm ERR!` lines, vulnerability/severity lines, advisory URLs, or package-count summary lines | `npm install` with 3 deprecation warnings and a clean audit — warnings collapse to 1 + `(×3)`, install summary kept | No `max_tokens` stage — deliberate, since `npm test`/`npm run build` can wrap an arbitrarily long underlying script whose real output must not be truncated |
| `npx ...` (anything not routed above) | `npx` ([node.toml](../../quor/filters/builtin/node.toml)) | Strips the package-resolution/auto-install preamble (`npm warn exec`, "need to install", "ok to proceed"); collapses repeated deprecation warnings | Never strips the wrapped tool's own error/warning/failure output | `npx cowsay hello` (auto-installed) — resolution preamble removed, `cowsay`'s own output untouched | Same "no `max_tokens`" reasoning as `npm` above |
| `pnpm ...` (anything not routed above) | `pnpm` ([node.toml](../../quor/filters/builtin/node.toml)) | Strips lockfile/progress-bar noise and `+`-only progress lines; collapses repeated `warn` lines | Never strips errors, `ERR_PNPM_*` codes, package-count/`done in`/dependency-tree lines, vulnerability/severity lines | `pnpm install` — progress spam removed, dependency tree and timing kept | Same "no `max_tokens`" reasoning as `npm` above |
| `yarn ...` (anything not routed above) | `yarn` ([node.toml](../../quor/filters/builtin/node.toml)) | Strips classic-yarn step banners (`[1/4] Resolving...`) and Berry's `➤ YN0000:` info lines; collapses repeated `warning` lines | Never strips errors, `success` lines, `done in` timing, vulnerability/severity lines | `yarn install` — 4 step banners removed, `success`/timing summary kept | Same "no `max_tokens`" reasoning as `npm` above |
| `next build`/`next dev`/`next start` (anything not routed to `eslint` via `next lint`) | `next` ([node.toml](../../quor/filters/builtin/node.toml)) | Strips version banner and step-progress lines ("Creating an optimized production build...", "Collecting page data...", etc.); dedupes; caps to 500 tokens (`tail`) | Never strips `error`/`warning`/`Failed to compile`/`Route (`/`First Load JS`/`✓ Compiled` lines | A successful build — step banners removed, the route size table and `✓ Compiled successfully` survive intact | Unlike `npm`/`turbo`, `next` gets a `max_tokens` safety net — it's Next.js's own fixed build/dev pipeline (bounded shape like `tsc`/`eslint`), not a wrapper around an arbitrary user script |
| `turbo ...` | `turbo` ([node.toml](../../quor/filters/builtin/node.toml)) | Strips the `• Packages in scope`/`• Running ... in N packages`/`• Remote caching` preamble bullets; dedupes | Never strips `error`/`failed`/`FAIL`, or the `Tasks:`/`Cached:`/`Time:` summary lines; per-package `<pkg>:<task>: ...` lines (including cache hit/miss status) are never pattern-matched, so a wrapped task's own output always survives | 3-package build — preamble bullets removed, every per-package line and the task summary kept | No `max_tokens` stage (like `npm`) — `turbo` wraps arbitrary per-package scripts. **Deliberately no `group_repeated`** either (QB-006C) — shape-based grouping on `cache (miss|hit)` would merge a hit and a miss from two *different* packages together, hiding which package actually missed cache |
| `cat <file>.py [-n\|--number\|-b\|--number-nonblank]` | `cat-python` ([cat-python.toml](../../quor/filters/builtin/cat-python.toml)) | Compresses function/method **bodies** to signature + docstring via stdlib `ast` parsing (QB-005); also applies the same comment-stripping/dedup/cap as `cat` below | Never touches imports, module-level constants, class/function signatures, decorators, or docstrings; on invalid syntax, fails open and returns the file completely unmodified | A 200-line file with 10 short functions — bodies collapse to signatures, imports/constants untouched | stdlib `ast` only — no type inference, no cross-file analysis; comments inside a compressed function body are also removed (comments have no AST node, so `python_ast_summarize` can't distinguish them, but this is caught by the plain-text pass anyway since the body line is gone) |
| `cat <file>` (anything not matched by `cat-python` above) | `cat` ([cat.toml](../../quor/filters/builtin/cat.toml)) | Strips `#`/`//`/`--`/`;`-style comment lines; caps to 800 tokens (`both`) | Never strips shebangs, or lines containing `TODO`/`FIXME`/`HACK`/`XXX`/`NOTE`/`WARN` | A config file with inline comments — comments removed, actual settings kept | Comment-pattern matching is generic across languages; a comment style not covered by `#`/`//`/`--`/`;` (e.g. `/* */` block comments) is not stripped |
| Anything else that passed the rewrite check (§1) | `generic` ([z_generic.toml](../../quor/filters/builtin/z_generic.toml)) | Strips ANSI escapes; strips framework-internal traceback frames (same `site-packages`/`dist-packages` pattern as `pytest` above — covers a raw script or dev server crashing outside pytest, e.g. `flask run`); dedupes consecutive lines; caps to 1000 tokens (`tail`) | `Traceback`/`Error`/`Exception` lines are protected; otherwise no command-specific pattern knowledge — nothing else is specially protected beyond what survives dedup/ANSI-strip/budget | A `flask run` crash with an unhandled exception through several Django/Flask internals — the framework `File` lines are removed, the user's own frame and exception survive | This is the widest net and the least intelligent filter — a command that would benefit from a dedicated filter (e.g. `terraform plan`, `tsc`, `docker build`) still only gets generic treatment until someone writes one; see §5 |

**Not currently supported at all** (not in `_KNOWN_BASE_COMMANDS`, so never rewritten, regardless
of filter availability): `cargo`, `docker` (as a direct command — only `docker exec <container>
<known-command>` is peeled as a transparent prefix), `terraform`, `go`, `make`, `bun`/`bunx`
(deliberately out of scope, QB-006A), and any other build/CLI tool not listed above. These pass
through completely untouched. (`tsc`, `jest`, `vitest`, `prettier`, `next`, and `turbo` were in
this list before QB-006C — see §4 above for their current filters.)

## 5. How new commands are added

Adding support for a new command is a two-part change (rewrite layer + filter layer), and both
are required — adding one without the other does nothing observable:

1. **Rewrite layer** (`quor/rewrite/rules.py`): add the base command to `_KNOWN_BASE_COMMANDS`
   (or, for a `python -m <tool>` style invocation, add `<tool>` to
   `_KNOWN_PYTHON_SUBCOMMANDS`). If the tool is typically invoked through a wrapper that should
   pass through untouched (e.g. a new container runtime), consider `TRANSPARENT_PREFIXES` or
   `TRANSPARENT_MULTI_WORD_PREFIXES` instead — see §1 for the distinction between "known base
   command" (Quor filters its own output) and "transparent prefix" (Quor skips past it to
   whatever command follows).
2. **Filter layer**: add a new `[[filter]]` block to an existing built-in TOML file (if the
   command belongs in an existing category) or create a new TOML file (for a new category) — see
   `CONTRIBUTING.md`'s "Writing New Filters" section for the full authoring process, and
   §6 below for placement/ordering rules specific to precedence.
3. **This document**: add a row to the table in §4, and update §1 if the rewrite-layer change
   affects detection logic in a way not already covered.
4. **Benchmark coverage**: add a `[[case]]` entry (plus sample file) to
   `tests/benchmarks/manifest.toml` — see §7. This is required, not optional, for every new
   built-in filter.

## 6. Best practices for adding new filters

- **Specificity via ordering, not priority.** If your new filter should win over an existing
  broader one for the same command, either (a) put it in a file that sorts earlier
  alphabetically among built-ins, or (b) if it's in the *same* file as a broader filter,
  declare it *before* the broader `[[filter]]` block — see §2's `eslint`/`cat-python` examples.
- **Never let a new filter shadow `generic`'s role.** `z_generic.toml` must remain the last
  built-in filter loaded. Don't name a new file starting with `z` unless it is intentionally
  meant to be a catch-all.
- **`preserve_patterns` first, `strip_lines`/`group_repeated` patterns second.** Decide what
  must never be removed before deciding what to remove — see PROJECT_BIBLE.md's "Core
  Principles" #1 (meaning preservation is non-negotiable).
- **`abort_unless`/`abort_if` for short-circuiting**, not a stage-level pattern, if the entire
  filter should no-op when a signal is absent (e.g. `pytest`'s `abort_unless = ["FAILED",
  "ERROR", "error"]` — an all-pass run returns unchanged rather than risking an empty/confusing
  result).
- **`on_empty` if aggressive stripping could ever produce empty output** — an empty string
  looks like command failure to the AI, not success.
- **Real captured output, not hand-invented text**, as the basis for both inline
  `[[filter.tests]]` and benchmark samples (§7) — synthetic examples miss real-world noise
  patterns (extra whitespace, locale differences, version-specific banner text).
- **≥3 inline `[[filter.tests]]` entries** covering: noise removed, critical content preserved,
  and any short-circuit condition — see `CONTRIBUTING.md`'s Filter checklist for the full list.

## 7. Benchmark coverage requirement (QB-011)

**Every new built-in filter must include benchmark coverage, in addition to its inline
`[[filter.tests]]`.** Inline tests validate correctness (does this specific crafted input keep
what it should and drop what it shouldn't); the benchmark suite validates *and tracks over
time* the filter's real-world compression quality against a committed baseline, so a future
change to a shared stage (e.g. `strip_lines`, `group_repeated`) that silently regresses this
filter's compression is caught automatically in CI.

Concretely, adding a new filter means also adding to `tests/benchmarks/`:

1. A realistic (captured or carefully hand-written, sanitized if needed) sample of the command's
   output under `tests/benchmarks/samples/<category>/<NNN>_<description>.txt`.
2. A `[[case]]` entry in `tests/benchmarks/manifest.toml` referencing that sample, the expected
   filter name, a `min_reduction_pct` floor, and `must_contain` substrings.
3. A baseline entry, via `python -m tests.benchmarks.run_benchmarks --update-baseline` (committed
   alongside the filter change, not as a follow-up).

See `tests/benchmarks/README.md` for the full walkthrough and `tests/benchmarks/manifest.toml`'s
header comment for field-by-field documentation. This runs automatically as part of
`pytest tests/` (via `tests/benchmarks/test_benchmarks.py`), so a missing or regressed case fails
CI, not just a manual review step.

**Current benchmark coverage (ADR-032):** every currently-implemented built-in filter —
`git-status`, `git-log`, `git-diff`, `pytest`, `mypy`, `ruff`, `eslint`, `npm`, `npx`, `pnpm`,
`yarn`, `tsc`, `jest`, `vitest`, `prettier`, `next`, `turbo`, `cat`, `cat-python`, and `generic` —
has at least 2 manifest cases in `tests/benchmarks/manifest.toml` (40 cases across 20 categories
total) with a committed baseline entry. The `ruff`/`eslint`/`npm`/`npx`/`pnpm`/`yarn`/`cat`/
`cat-python` cases were added in the same pass that closed a gap this document originally
flagged — see ADR-032 in `docs/final/DECISIONS.md` for the full history. The `tsc`/`jest`/
`vitest`/`prettier`/`next`/`turbo` cases were added for QB-006C; that pass also reclassified the
existing `npx-prettier-check-failure` case from the generic `npx` category to `prettier` once
prettier got its own filter, with an updated (lower) baseline reflecting the new filter's own
compression on that sample. `markdown`/`document-text` (4 cases, 2 categories) were added for
QB-007B — see §8 below for why these are a structurally different kind of case (file-path routing,
not command routing).

---

## 8. Read/file-path routing — Markdown and plain-text documents (QB-007B/C/D)

Everything in §1–§7 above describes routing a **Bash command string** through the classifier and
then `FilterRegistry`. QB-007B introduces a second, structurally different use of the same
`FilterRegistry`: routing a **Read tool file path** directly, with no classifier/rewrite layer
involved at all (there is no command to rewrite — see `backlog.md`'s QB-007 entry for why Read
needs an entirely different hook shape than Bash). QB-007C wires this into the live
`PostToolUse`/Read hook — as of QB-007C, a supported document read by Claude Code is genuinely
compressed via `updatedToolOutput`, not just at the filter-testing layer. QB-007D wires the same
Read path into tracking: every Read invocation is recorded with `command="Read: {file_path}"` in
the same `invocations` table Bash commands use, so it shows up in `quor gain` with no special
handling — see `backlog.md`'s QB-007D entry for the full detail.

**Adapter-side allowlist (QB-007C), important for understanding what actually runs:**
`quor/adapters/claude_read.py` only ever applies a filter whose name is `markdown` or
`document-text` (`_READ_SUPPORTED_FILTER_NAMES`) — *not* whatever `FilterRegistry.find()` happens
to return. This matters because the built-in `generic` filter (§3) matches literally any non-empty
string, including any unsupported Read file path (`report.docx`, `script.py`, ...); without this
allowlist, every unsupported file type would be silently routed through a shell-output filter never
designed for document content. The allowlist is adapter-local, not a `FilterRegistry`/schema
change, so it has no effect on Bash routing at all.

**How routing works:** `match_command` is matched against the Read tool's bare file path string
instead of a command string, via the exact same `FilterRegistry.find()` call described in §2 — no
new routing system, no schema change. Both new filters anchor their pattern to the entire routed
string being a single whitespace-free token (`^\S+\.(md|markdown)$` / `^\S+\.(txt|rst)$`)
specifically so they can never accidentally intercept a real Bash command string that merely
references a `.md`/`.txt` file as an argument — a command string always contains a space once it
has arguments (e.g. `pytest tests/notes.md`), so only a bare path can match. See
`quor/filters/builtin/markdown.toml`'s header comment for the full rationale, and
`tests/unit/test_document_filters.py::TestBashRoutingUnaffected` for the regression coverage
proving this.

**Known routing collision (accepted, documented):** because `FilterRegistry` is shared between
Bash commands and Read paths, and built-in load order is alphabetical, `FilterRegistry.find()`
still literally returns the `cat` filter for a path like `cat.md` (`cat.toml` sorts before
`markdown.toml`). This is inherent to reusing `match_command`/`FilterRegistry` rather than building
a parallel routing system — narrow and unlikely in practice, and regression-tested
(`TestKnownRoutingCollision` in `tests/unit/test_document_filters.py`) so it can't silently change
without being noticed. **As of QB-007C, this no longer affects real Read calls:** the adapter-side
allowlist above means only `markdown`/`document-text` are ever actually applied, so a Read for
`cat.md` safely passes through unchanged rather than being run through `cat`'s stripping logic —
regression-tested end to end in
`tests/unit/test_read_hook_activation.py::TestRoutingPrecedenceRegressions`.

| File path(s) matched | Filter (source file) | Optimizes | Does NOT optimize | Known limitations |
|---|---|---|---|---|
| `*.md`, `*.markdown` | `markdown` ([markdown.toml](../../quor/filters/builtin/markdown.toml)) | Nothing is stripped — `preserve_patterns` protects ATX headings (`#`–`######`), fenced code block *marker* lines, bullet/numbered lists, requirement/decision IDs, decision markers, TODO/FIXME/XXX, and NOTE/WARNING/CAUTION/IMPORTANT callouts; `deduplicate_consecutive` collapses repeated/blank-line runs; `max_tokens` (2000, `head`) is the only real compression, engaging only once a document exceeds budget | Setext-style headings (`Title\n=====`) are not detected — only ATX (`#`); a fenced code block's *interior* is not span-protected, only its marker lines individually | A large code block can be truncated through the middle by `max_tokens`, stranding one fence marker from its content (demonstrated in `TestMarkdownFencedCodeBlockLimitation`) — not a bug, a documented limit of per-line-only `preserve_patterns` |
| `*.txt`, `*.rst` | `document-text` ([document-text.toml](../../quor/filters/builtin/document-text.toml)) | Same philosophy as `markdown` minus ATX/fence patterns, plus RST's single-line `.. code-block::` directive | RST's setext-style section headings (title + punctuation underline) are not detected at all — no markdown-equivalent heading pattern exists for `.txt`/generic `.rst` | Same fenced/directive-interior caveat as `markdown` for `.. code-block::` bodies; plain `.txt` has no structural markup at all beyond the shared list/ID/TODO/NOTE patterns |

**Why no `strip` (COMPRESS) patterns exist in either filter:** unlike shell-command output, a
hand-written document has no reliable "noise" pattern to remove without risking real content loss
(PROJECT_BIBLE.md Core Principle #1: meaning preservation is non-negotiable). Both filters rely
entirely on `preserve_patterns` (protect) plus `max_tokens` (best-effort budget cap) — there is
nothing analogous to `pytest`'s `PASSED` lines or `npm`'s deprecation-warning spam in ordinary
prose. This means a document under the 2000-token budget renders back byte-identical; real
compression only appears on documents that exceed it. Measured on realistic samples in
`tests/benchmarks/`: 29.5% on a ~3,700-token engineering design doc, 18.8% on a ~2,700-token
plain-text notes doc, 0% on short samples (a README, an RST dev guide) — the last one is correct,
not a regression.

`group_repeated` was deliberately not used by either filter: collapsing repeated-shape lines is
safe for diagnostic tool output (its original use case — see `mypy`'s row in §4) but unsafe for
prose, where distinct TODOs or list items can share a superficial shape without being redundant.

---

## 9. Document extraction — DOCX and PDF (QB-007E1/E2/E3)

Structurally different from everything in §1–§8: this is not a `FilterRegistry` entry and is not
reachable from a real Read call today. `quor/pipeline/extract/` is a separate preprocessing layer
— extension-routed, fail-open, and explicitly **not** a `StageHandler` — that will eventually sit
*before* `FilterRegistry`/`Pipeline` for binary document types the Read hook can't compress
directly (unlike `.md`/`.txt`/`.rst`, which are already plain text and need no extraction at all;
see §8). As of QB-007E3, both `.docx` and `.pdf` have real handlers; wiring `extract()` into
`quor/adapters/claude_read.py` remains out of scope (deferred to QB-007E4 — see `backlog.md`'s
QB-007E2/E3 entries).

**Public API:** `quor.pipeline.extract.registry.extract(file_path: Path) -> str | None`. `None`
always means "fail open, proceed as if this layer didn't exist" — an unregistered extension, a
registered-but-unimplemented handler, or a handler that raised for any reason.

| Extension | Handler | Converts to | Does NOT convert | Known limitations |
|---|---|---|---|---|
| `.docx` | `extract_docx()` ([docx.py](../../quor/pipeline/extract/docx.py)) | Headings (`Heading 1`–`6` → ATX `#`–`######`), paragraphs, bullet lists (`- `), numbered lists (sequential, restarting per contiguous run), GitHub-style tables (`\|`-escaped, multi-paragraph cells joined with `<br>`), contiguous code-style paragraphs (style name or explicit monospace font) as fenced blocks with indentation preserved | Document properties/comments/headers/footers (excluded by construction — never read, not filtered), images, footnotes/endnotes, run-level emphasis (bold/italic) | No nested/multi-level lists (Word's `numPr`/`ilvl` numbering XML is not resolved — every level flattens); merged table cells repeat their text across every spanned column (Markdown has no colspan syntax); monospace detection only catches an explicit per-run font override, not a theme-level or style-implied convention |
| `.pdf` | `extract_pdf()` ([pdf.py](../../quor/pipeline/extract/pdf.py)) | Headings inferred from font size (larger than the document's own body-text size, ranked into levels, clamped at `######` — PDF has no "Heading 2" style to read, unlike DOCX), paragraphs reconstructed by merging lines with a small vertical gap, bullet lists (`•`/`◦`/`▪`/`‣`/`●`/`○`/`·`/ASCII `-`/`*`/`+` all normalize to `- `), numbered lists (the PDF's own visible number reused verbatim; delimiter normalized to `N.`), GitHub-style tables via `pdfplumber`'s own table detection, contiguous monospace-font lines as fenced code blocks with indentation reconstructed from character position | Document metadata (`pdf.metadata` — never read), images (no OCR), footnotes | Geometry-based inference only, not ground truth — unusual line spacing or a body font that varies in size can mislead it; same no-nested-lists/no-emphasis limitations as DOCX; a bullet glyph a PDF's own font can't map to a real Unicode codepoint (no `ToUnicode` CMap — a real, observed PDF phenomenon, not hypothetical) falls through to a plain paragraph rather than being recognized as a bullet |

**Fail-open, tested at every layer:** missing `python-docx`/`pdfplumber` (`quor[documents]`,
optional — core install works without either), a corrupt file, an invalid zip, an encrypted PDF, a
truncated or nonexistent file, or any other unexpected parser exception all return `None` with a
warning; both `extract_docx()` and `extract_pdf()` never raise, independent of whatever calls them
(`registry.extract()` wraps every handler in the same guarantee too, defense-in-depth for both).

**Benchmark fixtures, not yet wired in:** two representative samples exist for each format —
`tests/benchmarks/samples/docx/001_design_doc_ranking_cache.docx`/`002_short_client_readme.docx`
and `tests/benchmarks/samples/pdf/001_design_doc_export_pipeline.pdf`/`002_short_client_notes.pdf`
(mirroring §7's markdown long/short pair) — but neither pair is yet referenced from
`manifest.toml`/`baseline.json`; there is no live compression path to benchmark until `extract()`
is wired into the Read hook. Adding that manifest coverage is QB-007E4's job.

---

## Cross-references

- `quor/rewrite/rules.py` — command-detection ground truth.
- `quor/filters/registry.py` — precedence/lookup implementation (`FilterRegistry.find()`).
- `quor/filters/builtin/*.toml` — filter behavior ground truth.
- `quor/pipeline/extract/registry.py` — document extraction routing/fail-open contract (§9).
- `quor/pipeline/extract/docx.py` — DOCX-to-Markdown conversion algorithm (§9).
- `quor/pipeline/extract/pdf.py` — PDF-to-Markdown conversion algorithm (§9).
- `CONTRIBUTING.md` — "Writing New Filters" for the full authoring workflow and PR checklist.
- `docs/final/CLAUDE.md` — "The ContentMask Pipeline" for stage semantics, "Filter Configuration
  Format" for the TOML schema.
- `docs/final/ANTI_GOALS.md` — #23 ("No filter without inline tests") and the broader
  transparency/meaning-preservation anti-goals that shape every filter's design.
- `tests/benchmarks/README.md` — benchmark suite usage and interpretation.
- `quor explain "<command>"` — the live, always-current way to check any specific command's
  actual behavior on your installed version.
