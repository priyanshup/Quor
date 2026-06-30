# ANTI-GOALS
## What Quor Will Explicitly Never Do

> This document exists so that every proposed feature can be evaluated against a clear boundary.
> "That's an anti-goal" is a complete answer to a feature request.
> When a proposed addition conflicts with this list, the list wins.

---

## Why Anti-Goals Matter

A project without explicit anti-goals accumulates features until it becomes everything to no one. Every item on this list has been rejected because it conflicts with Quor's core identity:

**Quor is a local, deterministic, transparent context optimizer for AI coding assistants.**

Anything that undermines local, deterministic, transparent, or optimizer-scoped is an anti-goal.

---

## Core Absolute Anti-Goals

### 1. Never require administrator privileges

Quor must install and operate with `pip install quor` and no system-level permissions. The target user cannot run `sudo`, cannot install system packages, and has no path to request admin rights from IT in under 2 weeks.

If a feature cannot be implemented without requiring elevated privileges at any step — installation, initialization, runtime, or configuration — it is an anti-goal. No exceptions.

### 2. Never make LLM or network calls in the compression path

The compression pipeline is local, offline, and deterministic. No internet connection required. No API key required. No LLM call for summarization, classification, or decision-making.

LLM-assisted compression is architecturally possible (as an opt-in plugin). It is not, and will never be, in the default path.

**Why:** The hook runs in the request path of an AI session. A second LLM call would double latency, double cost, introduce non-determinism, and require credentials. These are all unacceptable.

### 3. Never silently modify content meaning

Quor removes redundant content. It does not summarize, rephrase, abstract, or otherwise change the meaning of what remains. A line that Quor keeps must be bit-for-bit identical to the original line.

If a compression technique changes the meaning of the output (e.g., "rewrites verbose error messages in simpler English"), it is an anti-goal. `preserve_patterns` and the PROTECT mechanism exist precisely to protect meaningful content from removal.

### 4. Never store, transmit, or log command output content

The SQLite database and JSONL file record: command name, project path, token counts, filter name, duration, mode, timestamp. They do not record the actual content of command outputs.

The tee cache stores raw output content, but locally and under the user's control. It is never transmitted, never indexed, never inspected by Quor's tracking system.

If a feature requires storing command output text centrally, it is an anti-goal.

### 5. Never implement telemetry, analytics, or usage reporting without explicit opt-in

Quor collects no usage data by default. No anonymous crash reports, no filter hit rate aggregation, no "help us improve" popups, no ping-home on install.

If a future maintainer wants to add opt-in telemetry, it must be: explicitly documented, off by default, removable with one config change, and never include command output content.

### 6. Never depend on Rust, Go, or any compiled binary as a core dependency

The core package installs via `pip` with no compilation. Every dependency must provide Windows x64 wheel distributions on PyPI.

Optional extras (`pip install "quor[ml]"`) may have heavier dependencies, but only if they fail gracefully on import error. The core path must work with no extras installed.

### 7. Never build a web UI or web dashboard

Quor is a CLI tool. It will always be a CLI tool. No Flask, no FastAPI, no React dashboard, no browser-based config editor.

`quor gain`, `quor explain`, `quor doctor`, and `quor validate` are the UI. Rich terminal output is the rendering engine. The browser is not in scope.

### 8. Never lock the plugin interface to a single AI assistant

The `quor.compression_stage` entry-point API is AI-assistant-agnostic. A plugin that works with Claude Code must work identically with Cursor or Copilot (when those adapters exist).

A plugin that imports or references Claude Code types directly is a violation of the plugin contract.

### 9. Never optimize for benchmark numbers at the expense of correctness

Quor's compression ratio is a means, not an end. A filter that achieves 80% compression by removing error messages is worse than a filter that achieves 30% compression with correct behavior.

If a proposed optimization improves the compression ratio metric while degrading meaning preservation, it is rejected. The benchmark must measure both.

### 10. Never sacrifice transparency for compression ratio

Every compression decision must be auditable via `quor explain`. A compression technique that cannot be explained (e.g., a black-box ML model that removes lines without a human-interpretable rule) is an anti-goal for the default path.

This does not prohibit ML as an optional plugin. It prohibits ML in the default path where the user cannot inspect the decision.

---

## Scope Anti-Goals

### 11. No watch mode in V1

`quor watch` — a daemon that monitors file changes and proactively compresses — is a V2 feature at earliest. V1 is hook-invocation-only.

### 12. No multi-agent support in V1

Cursor, Copilot CLI, Gemini Code Assist, and other AI assistant adapters are V2. V1 supports only Claude Code's PreToolUse hook.

### 13. No session-level deduplication in V1

The hook does not read the AI's context window to check if content was already seen. This requires parsing the Claude Code session context format, which is an additional dependency and a significant implementation risk. V2.

### 14. No `quor config` command

Configuration is managed directly in TOML. There is no CLI-based config editor. `quor validate` checks the config; it does not set values.

### 15. No filter migration tool in V1

Quor does not convert RTK/Zap filter files to Quor format. The TOML formats are different by design. Users write Quor filters from scratch (the built-in filters are templates). A migration guide in docs is acceptable; a `quor migrate` command is not.

### 16. No credential management

Quor does not store, validate, or manage API keys, tokens, or passwords. It detects credential patterns in output (warning only) but never interacts with them.

### 17. No AI assistant configuration management

`quor init --claude` modifies `settings.json` only to add the hook. It does not manage other Claude Code settings, does not update model preferences, does not configure tool permissions.

---

## Architecture Anti-Goals

### 18. No string→string transform pipeline

The ContentMask abstraction (KEEP/COMPRESS/PROTECT line-level decisions) is the core primitive. A stage that receives a string and returns a modified string is architecturally wrong. Stages annotate masks; only the final render removes lines.

### 19. No mutable ContentMask

Stages receive a ContentMask and return a new ContentMask. They never mutate the input. `LineMask` is a frozen dataclass. The `engine.py` enforcer validates PROTECT immutability after each stage.

### 20. No global state in the pipeline

The pipeline is pure: given the same input and filter config, it always produces the same output. No random seeds, no timestamps, no external service calls, no file reads during pipeline execution (except for `file://` stages, which are loaded once at filter registration time).

### 21. No `assert` for validation

`assert` is stripped by `python -O`. All validation in production code uses explicit `if/raise`. This applies everywhere, including test helper code that may be imported into production paths.

### 22. No compressed pipeline for the hook stdout

The hook writes JSON to stdout. `rich` output must never appear in hook stdout. Only the hook's filtered output (plain text from the pipeline) or the original command output may appear on stdout in hook mode.

---

## Quality Anti-Goals

### 23. No filter without inline tests

A filter with zero `[[filter.tests]]` entries triggers a warning from `quor verify`. A built-in filter with fewer than 3 inline tests is a PR rejection. Tests are not optional.

### 24. No token count without uncertainty label

Every token savings number shown to the user includes "±20%" or equivalent uncertainty labeling. "Saved 1,240 tokens" is not acceptable. "Saved ~1,240 tokens (±20%)" is acceptable.

### 25. No claimed AI quality improvement without evidence

Documentation and README do not claim that Quor improves AI task success rate. The claim is: "We believe filtering improves AI session quality based on 'lost in the middle' research. We cannot currently prove it for coding tasks. `quor explain` lets you verify what filtering did so you can judge for yourself."

---

## The Two Questions Every Feature Proposal Must Answer

1. **Does this undermine local, deterministic, transparent, or optimizer-scoped?**
   If yes: it's an anti-goal. Don't build it.

2. **Does this require the user to trust Quor with something they cannot inspect?**
   If yes: it must be opt-in, auditable, and well-documented. If it cannot be made auditable, it's an anti-goal.
