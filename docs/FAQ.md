# FAQ

## What is Quor?

Quor sits between your shell (or Claude Code's `Read` tool) and the assistant's context window. It runs your command — or reads your file — exactly as it would happen anyway, then applies a deterministic filtering pipeline that strips low-signal content while preserving anything that indicates success, failure, or change. Same command, same result, smaller prompt.

## Does it use AI?

No. Filtering is entirely rule-based — pattern matching, deduplication, counting, budget limits. No LLM calls, no ML models, no randomness anywhere in the filtering path. Same input always produces the same output.

## Is my code uploaded?

No. Quor runs locally and makes no network calls. Your command output and file contents never leave your machine through Quor.

## Does it work offline?

Yes. There's nothing to connect to — the entire filtering pipeline is local, deterministic logic.

## Windows support

Windows is Quor's primary development and CI target — `pip install quor`, no Rust toolchain, no compilation step. CI also verifies Ubuntu (Linux) on every change.

## Corporate laptops

Some locked-down endpoint-protection policies block the small, unsigned `quor.exe`/`qr.exe` launcher stubs that `pip` creates, even though they only re-invoke the interpreter. This never affects Claude Code itself — every command it runs already goes through `<your Python interpreter> -m quor ...` directly, not the launcher. When typing commands yourself on such a machine, use the same form:

```bash
py -m pip install quor
py -m quor doctor
```

## Optional language support

Structure-aware source reading (function/method bodies compressed to signature + docstring) is built in for Python. JavaScript/TypeScript support (`.js`, `.jsx`, `.mjs`, `.cjs`, `.ts`, `.tsx`) requires the optional `quor[javascript]` extra, since it depends on `tree-sitter` grammars rather than a pure-Python parser. If that extra isn't installed, Quor warns and simply skips AST-based compression for those files — it never fails or blocks.

## Why are filters skipped?

Two different reasons, both intentional:

- **Fail-open:** if a filter crashes, times out, or hits a plugin error, Quor falls back to the original, unfiltered output rather than risk hiding or corrupting it.
- **Protected content:** content matching a filter's `preserve_patterns` (diff hunks, tracebacks, failures) is never compressed, even if it means the configured token budget is exceeded. This is a deliberate guarantee (ADR-031), not a bug.

## Why isn't every file compressed?

A file that's already mostly signal has little to remove. A short README or a small diff will often compress 0% — that's correct behavior, not underperformance. Compression only helps where there's actual noise (repeated boilerplate, unchanged context, page furniture) to strip out.

## How much token savings should I expect?

It depends entirely on the content — there's no fixed rate. Run `quor gain` after real usage to see your own project's cumulative savings (a ±20% estimate, using a `char / 4` token approximation).

## Why doesn't Quor always achieve 70%?

There's no fixed "Quor compression rate" to hit — reduction is entirely content-dependent. Quor's own committed benchmark suite shows results ranging from 0% (short documents, already dense) up to ~43% (a long PDF design doc), depending on how much genuine noise a given sample contains. Content that's marked "always keep" — real diff lines, failures, tracebacks — is protected by design and won't be compressed regardless of how far over budget it runs, because losing that content would defeat the point of showing it at all.

## Is Quor safe?

Yes, by design:
- **Fail-open** — any filter failure falls back to the original, unfiltered output.
- **Nothing is unrecoverable** — every compressed output's true raw content is cached locally, with a `[full output: <path>]` link back to it.
- **Secret-aware** — warns on stderr if a known credential pattern (GitHub, AWS, Slack, private key) survives compression; never redacts or removes it silently.
- **Deterministic** — no ML, no LLM calls, so behavior is fully predictable and auditable.
