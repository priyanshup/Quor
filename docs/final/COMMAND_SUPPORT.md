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
   git, pytest, mypy, ruff, cat, python, python3, npm, npx, pnpm, yarn
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
- Within `node.toml`, the `eslint` `[[filter]]` block is declared *before* the generic `npm`/
  `npx`/`pnpm`/`yarn` blocks in the same file, because `[[filter]]` blocks preserve TOML
  declaration order. If `eslint` were declared after the generic `npm` block, `npm exec eslint`
  would always match the generic `npm` filter and the eslint-specific routing would never fire.

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
| `npx eslint ...`, `npm exec eslint ...`, `pnpm exec/dlx eslint ...`, `yarn exec eslint ...`, bare `yarn eslint ...` | `eslint` ([node.toml](../../quor/filters/builtin/node.toml)) | Strips ANSI, blank lines; collapses **byte-identical** repeated diagnostic lines (`exact_match=true`) to `(×N)`; caps to 400 tokens (`tail`) | Never strips lines containing `error`/`warning`/`problem`/`fixable` (case-insensitive) or `✖`; short-circuits on a clean run | Same rule violated at 4 different line:column pairs → all 4 lines kept distinctly (different diagnostics, not collapsed) | Routing is pure command-string matching — `npm run lint` (a `package.json` script alias) is **never** routed here even if that script runs eslint under the hood, because resolving the alias would require reading `package.json` (explicitly out of scope, QB-006B) |
| `npm ...` (anything not routed to `eslint` above) | `npm` ([node.toml](../../quor/filters/builtin/node.toml)) | Strips ANSI; collapses repeated `npm warn deprecated` lines; strips funding/audit-nag boilerplate; dedupes | Never strips `npm ERR!` lines, vulnerability/severity lines, advisory URLs, or package-count summary lines | `npm install` with 3 deprecation warnings and a clean audit — warnings collapse to 1 + `(×3)`, install summary kept | No `max_tokens` stage — deliberate, since `npm test`/`npm run build` can wrap an arbitrarily long underlying script whose real output must not be truncated |
| `npx ...` (anything not routed to `eslint` above) | `npx` ([node.toml](../../quor/filters/builtin/node.toml)) | Strips the package-resolution/auto-install preamble (`npm warn exec`, "need to install", "ok to proceed"); collapses repeated deprecation warnings | Never strips the wrapped tool's own error/warning/failure output | `npx cowsay hello` (auto-installed) — resolution preamble removed, `cowsay`'s own output untouched | Same "no `max_tokens`" reasoning as `npm` above |
| `pnpm ...` (anything not routed to `eslint` above) | `pnpm` ([node.toml](../../quor/filters/builtin/node.toml)) | Strips lockfile/progress-bar noise and `+`-only progress lines; collapses repeated `warn` lines | Never strips errors, `ERR_PNPM_*` codes, package-count/`done in`/dependency-tree lines, vulnerability/severity lines | `pnpm install` — progress spam removed, dependency tree and timing kept | Same "no `max_tokens`" reasoning as `npm` above |
| `yarn ...` (anything not routed to `eslint` above) | `yarn` ([node.toml](../../quor/filters/builtin/node.toml)) | Strips classic-yarn step banners (`[1/4] Resolving...`) and Berry's `➤ YN0000:` info lines; collapses repeated `warning` lines | Never strips errors, `success` lines, `done in` timing, vulnerability/severity lines | `yarn install` — 4 step banners removed, `success`/timing summary kept | Same "no `max_tokens`" reasoning as `npm` above |
| `cat <file>.py [-n\|--number\|-b\|--number-nonblank]` | `cat-python` ([cat-python.toml](../../quor/filters/builtin/cat-python.toml)) | Compresses function/method **bodies** to signature + docstring via stdlib `ast` parsing (QB-005); also applies the same comment-stripping/dedup/cap as `cat` below | Never touches imports, module-level constants, class/function signatures, decorators, or docstrings; on invalid syntax, fails open and returns the file completely unmodified | A 200-line file with 10 short functions — bodies collapse to signatures, imports/constants untouched | stdlib `ast` only — no type inference, no cross-file analysis; comments inside a compressed function body are also removed (comments have no AST node, so `python_ast_summarize` can't distinguish them, but this is caught by the plain-text pass anyway since the body line is gone) |
| `cat <file>` (anything not matched by `cat-python` above) | `cat` ([cat.toml](../../quor/filters/builtin/cat.toml)) | Strips `#`/`//`/`--`/`;`-style comment lines; caps to 800 tokens (`both`) | Never strips shebangs, or lines containing `TODO`/`FIXME`/`HACK`/`XXX`/`NOTE`/`WARN` | A config file with inline comments — comments removed, actual settings kept | Comment-pattern matching is generic across languages; a comment style not covered by `#`/`//`/`--`/`;` (e.g. `/* */` block comments) is not stripped |
| Anything else that passed the rewrite check (§1) | `generic` ([z_generic.toml](../../quor/filters/builtin/z_generic.toml)) | Strips ANSI escapes; strips framework-internal traceback frames (same `site-packages`/`dist-packages` pattern as `pytest` above — covers a raw script or dev server crashing outside pytest, e.g. `flask run`); dedupes consecutive lines; caps to 1000 tokens (`tail`) | `Traceback`/`Error`/`Exception` lines are protected; otherwise no command-specific pattern knowledge — nothing else is specially protected beyond what survives dedup/ANSI-strip/budget | A `flask run` crash with an unhandled exception through several Django/Flask internals — the framework `File` lines are removed, the user's own frame and exception survive | This is the widest net and the least intelligent filter — a command that would benefit from a dedicated filter (e.g. `terraform plan`, `tsc`, `docker build`) still only gets generic treatment until someone writes one; see §5 |

**Not currently supported at all** (not in `_KNOWN_BASE_COMMANDS`, so never rewritten, regardless
of filter availability): `cargo`, `docker` (as a direct command — only `docker exec <container>
<known-command>` is peeled as a transparent prefix), `terraform`, `tsc`, `go`, `make`, and any
other build/CLI tool not listed above. These pass through completely untouched.

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
`yarn`, `cat`, `cat-python`, and `generic` — has at least 2 manifest cases in
`tests/benchmarks/manifest.toml` (28 cases across 14 categories total) with a committed baseline
entry. The `ruff`/`eslint`/`npm`/`npx`/`pnpm`/`yarn`/`cat`/`cat-python` cases were added in the
same pass that closed a gap this document originally flagged — see ADR-032 in
`docs/final/DECISIONS.md` for the full history.

---

## Cross-references

- `quor/rewrite/rules.py` — command-detection ground truth.
- `quor/filters/registry.py` — precedence/lookup implementation (`FilterRegistry.find()`).
- `quor/filters/builtin/*.toml` — filter behavior ground truth.
- `CONTRIBUTING.md` — "Writing New Filters" for the full authoring workflow and PR checklist.
- `docs/final/CLAUDE.md` — "The ContentMask Pipeline" for stage semantics, "Filter Configuration
  Format" for the TOML schema.
- `docs/final/ANTI_GOALS.md` — #23 ("No filter without inline tests") and the broader
  transparency/meaning-preservation anti-goals that shape every filter's design.
- `tests/benchmarks/README.md` — benchmark suite usage and interpretation.
- `quor explain "<command>"` — the live, always-current way to check any specific command's
  actual behavior on your installed version.
