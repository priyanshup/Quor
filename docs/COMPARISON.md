# Quor vs. other token/context-optimization tools

This document compares Quor to other tools that reduce the token footprint of what an AI coding
assistant reads. It is scoped to **facts stated in this repository's own [README.md](../README.md)
and [backlog.md](../backlog.md)** — no other source was consulted while writing it.

**A limitation of that scope, stated up front:** backlog.md's own competitive claims (about RTK,
Headroom AI, and ZAP) are themselves drawn from an internal document,
`docs/archive/product-discovery/competitive-research.md`, which this comparison does not read
directly. What appears below about those three tools is therefore secondhand — Quor's own
characterization of them, not independently verified against each tool's own documentation. Where
backlog.md doesn't state a specific fact about a competitor, this document says so rather than
guessing.

---

## What Quor is, in one line

Quor is a deterministic, rule-based compression layer between a shell command (or Claude Code's
`Read` tool) and the AI assistant's context window. It never uses an LLM or ML model to decide what
to remove — filtering is pattern-match, dedup, count, and budget rules defined in TOML.

## Feature comparison

| Capability | Quor | RTK | Headroom AI | ZAP |
|---|---|---|---|---|
| AI assistant integration | Claude Code only — **Supported** | 14 assistants (per Quor's competitive research) | Not stated in README/backlog | Not stated in README/backlog |
| Multi-agent adapter (e.g. Cursor, Copilot, Gemini) | **Planned** (QB-035A design exists; QB-035F implementation not scheduled) | Already supports 14 assistants | Not stated | Not stated |
| AST-aware source compression | Python, JavaScript, TypeScript, TSX — **Supported** | Not stated | Python, JS, Go, Rust, Java, C++ (per Quor's competitive research) | Not stated |
| AST-aware compression, more languages (Go/Rust/Java/C#) | **Planned** (QB-046) | Not stated | Already covers Go, Rust, Java, C++ | Not stated |
| Document reading (Markdown, plain text, DOCX, PDF) | **Supported** | Not stated | Not stated | Not stated |
| Config/structured-data file compression (YAML/JSON/TOML/.env/.ini) | **Planned** (QB-040, not yet scoped) | Not stated | Not stated | Not stated |
| Git diff/show delta compression | **Supported**, but limited — `preserve_patterns` protects nearly all diff content by design, so real compression on large diffs is low (see [Benchmarks vs. real usage](#benchmarks-vs-real-usage) below) | Not stated | Not stated | Not stated |
| Smarter diff compression (context-aware hunk collapsing) | **Planned** (QB-041 / QB-055, not yet implemented) | Not stated | Not stated | Not stated |
| Stack-trace/traceback compression (pytest, generic) | **Supported** | Not stated to have this (Quor's competitive research states RTK "doesn't have this" for Django/Flask/pytest traceback compression) | Not stated | Not stated |
| Line-length capping | **Supported** (`truncate_lines` stage) | Not stated | Not stated | Has a `truncate_lines_at`-style feature (cited as the design precedent for Quor's own `truncate_lines` stage) |
| Compression modes (Safe/Balanced/Aggressive) | **Planned** (QB-039, open design questions, not implemented) | Not stated | Not stated | Not stated |
| Retroactive "what would this have saved you" scan of past sessions | **Planned** (QB-034, not scheduled) | Has this — a `discover` command that scans past session logs and ranks unfiltered commands by theoretical savings | Not stated | Not stated |
| Continuous/automated competitive benchmarking | **Planned** (QB-042, not implemented; today's competitive comparisons are a one-time internal research document) | N/A | N/A | Referenced once for a manual "efficiency comparison" against Quor; outcome not recorded in README/backlog |
| Secret-leak warning | **Supported** — warns on stderr for known credential patterns; never redacts or blocks | Not stated | Not stated | Not stated |
| Onboarding stats / "security-first mode for corporate use" | **Planned**, and currently unimplemented — PA-F07 (secret-detection warning gate) and PA-F08 (onboarding stats) are tracked backlog gates with, per backlog.md, "zero implementation anywhere in the codebase" | Not stated | Not stated | Per backlog.md, this is "a gap no competitor covers well" (Quor's own characterization) |
| Fail-open behavior (broken filter never blocks or hides output) | **Supported** | Not stated | Not stated | Not stated |
| Full-output recovery (nothing permanently lost) | **Supported** — cached locally, linked via `[full output: <path>]` | Not stated | Not stated | Not stated |
| Plugin extensibility (third-party filter stages) | **Supported** — standard Python entry points | Not stated | Not stated | Not stated |
| Compression telemetry / analytics | **Supported** for the benchmark corpus (QB-051); real-usage analytics against the live tracking DB is **Planned** (QB-054) | Not stated | Not stated | Not stated |
| Platform | Windows-native and pip-installable, primary dev/CI target; Linux (Ubuntu) also verified in CI | Not stated | Not stated | Not stated |
| Data handling | No LLM calls, no network calls — filtering runs entirely locally (per Quor's README FAQ) | Not stated | Not stated | Not stated |

"Not stated" means README.md and backlog.md make no claim either way — it is not evidence of
absence in the other tool.

## Quor's stated differentiators (per its own backlog)

backlog.md's internal competitive research reportedly frames Quor's differentiators as
**Windows-first support, its plugin system, and transparency** (the `quor explain` command) —
explicitly *not* feature parity with RTK's broader assistant/language coverage. Multi-agent and
multi-language expansion (QB-035, QB-046) are deliberately sequenced after these, per a
product decision recorded in backlog.md, on the reasoning that Quor should prove sustained real
usage on its current scope before expanding breadth.

## Strengths (as documented)

- **Deterministic and auditable.** No ML/LLM in the filtering path; every decision is a plain TOML
  rule that can be read, edited, and version-controlled. `quor explain` shows the stage-by-stage
  trace of what was removed.
- **Fail-open by design.** A broken filter, plugin crash, or timeout falls back to unfiltered
  output rather than blocking a command or silently hiding information.
- **Nothing is unrecoverable.** Every compressed output's original content is cached and linked.
- **Secret-aware without silent redaction.** Known credential patterns trigger a stderr warning
  rather than being silently stripped or blocked.
- **Locally run, no network calls.** Stated explicitly in the README FAQ.
- **Committed, CI-gated benchmark suite.** 60 cases across 27 categories run in CI and fail the
  build on regression (per README and backlog.md's QB-011/QB-051).

## Limitations (as documented)

- **Single-assistant support.** Claude Code only, today. Multi-agent support (Cursor, Copilot,
  Gemini) is designed (QB-035A) but not implemented.
- **Narrow language coverage for AST summarization.** Python, JavaScript, TypeScript, and TSX only.
  Go, Rust, Java, and C# are planned (QB-046) but not started.
- **Git diff/show compression is currently weak.** backlog.md documents that `preserve_patterns`
  protects nearly all diff content by design, so a large diff can blow past its token budget with
  little compression applied. Real-usage data cited in backlog.md attributes 45% of all tokens
  Quor has ever saved on its own project to git-diff, at roughly half the compression ratio of its
  sibling git filters — i.e., the single highest-volume filter is also one of the least effective
  today. A fix (QB-041/QB-055) is proposed, not implemented.
- **No config/structured-data file compression.** YAML, JSON, TOML, `.env`, and `.ini` files pass
  through untouched (QB-040, planned, not scoped).
- **Benchmark corpus and real usage disagree, sometimes sharply.** backlog.md reports several
  filters where the 60-case benchmark corpus and this project's own 90-day real-usage telemetry
  diverge significantly — for example mypy measured at 46.1% compression in the benchmark corpus
  but **-41.2%** (net expansion) in real usage, and pytest at 39.75% (benchmark) vs. 12.9% (real).
  Two shipped filters (mypy, npm) were found to expand output on average in real usage rather than
  compress it (QB-052, unfixed as of this writing).
- **Token savings are estimates, not exact counts.** `quor gain` reports savings using a `char / 4`
  approximation, stated in the README as accurate to roughly ±20%.
- **No AI task-success measurement.** backlog.md states explicitly that whether compressed output
  still lets the assistant complete a coding task correctly — as opposed to merely being shorter —
  is "not measured" today (QB-048, planned).
- **Compression is fixed, not adaptive.** Every filter's aggressiveness is a static, human-authored
  TOML rule. A user-facing Safe/Balanced/Aggressive mode toggle (QB-039) and self-tuning
  aggressiveness (QB-053) are both proposed, neither implemented.

## Benchmarks vs. real usage

Quor's own documentation is explicit that **benchmark methodologies differ between tools, and
even within Quor's own numbers, benchmark-corpus results and real-usage results are not the same
measurement and can diverge sharply.** Concretely:

- Quor's published reduction percentages (in README.md) come from a committed, hand-curated
  60-case benchmark suite, not live production telemetry.
- backlog.md documents that this benchmark corpus and Quor's own real-usage tracking database
  (`quor gain`) disagree by large margins for several filters (mypy, git-log, git-status, pytest —
  see [Limitations](#limitations-as-documented) above).
- No data in README.md or backlog.md describes how RTK, Headroom AI, or ZAP measure their own
  compression numbers, so no apples-to-apples benchmark comparison is possible from these two
  documents alone. A continuous, automated competitive benchmark (QB-042) is proposed in backlog.md
  specifically because this comparison doesn't exist today — it is planned, not implemented.

Any compression percentage — Quor's or a competitor's — should be read as specific to the
methodology and corpus that produced it, not as a portable, tool-agnostic number.

---

## When should I choose Quor?

Based only on what's documented here: choose Quor if you use **Claude Code on Windows (or
Linux)**, want compression that is **fully deterministic, local, and auditable** (no LLM/ML
decisions, no network calls, every stage inspectable via `quor explain`), and value that
**nothing is ever silently and permanently lost** (fail-open behavior plus a full-output recovery
link). Its current codebase-compression strengths are Python/JavaScript/TypeScript source files,
Markdown/DOCX/PDF documents, and the mainstream Node.js and Python build/test toolchains.

Choose something else, or wait, if you need support for AI assistants other than Claude Code,
AST-aware compression for languages beyond Python/JS/TS/TSX, compression of config/structured-data
files, or a retroactive "here's what you would have saved" adoption report — all of these are
documented as planned but not yet implemented in Quor as of this writing. If your workflow is
diff-heavy, be aware that Quor's own backlog documents git-diff as its highest-volume but
currently weakest-performing filter.
