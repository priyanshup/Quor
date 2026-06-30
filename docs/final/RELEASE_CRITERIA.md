# RELEASE CRITERIA
## Quor — Quality Gates by Version

> Every version milestone has mandatory exit criteria.
> No milestone is "done" until every gate in that section is green.
> Partial credit does not exist. A gate is met or it is not.

---

## How to Use This Document

Before declaring any version ready for release:
1. Work through every gate in the version's section in order.
2. Record the result (pass/fail/N/A) and the evidence for each gate.
3. All gates must pass. No exceptions. If a gate is blocking, fix the problem — do not relabel the gate.

---

## Internal Alpha (v0.1)

The bar for Internal Alpha is: "It works on the builder's machine without crashing."

### Functional Gates

- [ ] **IA-F01** `quor init --claude` completes without error on the builder's Windows machine. Hook script is written. `settings.json` is updated atomically.
- [ ] **IA-F02** `quor doctor` shows all green on the builder's Windows machine after `quor init --claude`.
- [ ] **IA-F03** A real Claude Code session with the hook active processes at least these commands without failure: `git status`, `git diff`, `git log`, `pytest tests/`, `cat README.md`.
- [ ] **IA-F04** `quor verify` exits 0 (all inline built-in filter tests pass).
- [ ] **IA-F05** `quor explain "pytest tests/"` shows a stage-by-stage trace with at least 2 stages shown.
- [ ] **IA-F06** `quor gain` shows non-zero token savings after IA-F03 commands.
- [ ] **IA-F07** A deliberate pipeline exception (injected in a test) causes the hook to return original content, not an error message or empty output.
- [ ] **IA-F08** Three operating modes work: set `QUOR_MODE=audit`, verify original content returned. Set `QUOR_MODE=optimize`, verify compressed content returned. Set `QUOR_MODE=simulate`, verify original returned with stats logged.
- [ ] **IA-F09** A filter with `abort_unless` configured returns original content when no matching line exists.
- [ ] **IA-F10** A filter with `preserve_patterns` configured: matching lines survive all subsequent stages.

### Quality Gates

- [ ] **IA-Q01** `mypy quor/` passes with no errors (strict mode or near-strict).
- [ ] **IA-Q02** `ruff check quor/` passes with no errors.
- [ ] **IA-Q03** `pytest tests/unit/` passes (all unit tests).
- [ ] **IA-Q04** Coverage ≥70% on `quor/pipeline/` and `quor/filters/`.
- [ ] **IA-Q05** No hardcoded `~`, `%APPDATA%`, `/tmp`, or `/home` in any source file (grep confirms).
- [ ] **IA-Q06** No bare `except:` in any source file (ruff confirms with E722).
- [ ] **IA-Q07** No `assert` in non-test source files used for validation (grep confirms).
- [ ] **IA-Q08** All `open()` calls specify `encoding="utf-8"` (ruff custom rule or grep confirms).

### Safety Gates

- [ ] **IA-S01** Hook stdout contains only valid JSON or plain text (no rich terminal escape codes) — verified by parsing hook stdout with `json.loads()`.
- [ ] **IA-S02** PROTECT decision: set PROTECT on a line, run through all stages, confirm line appears in rendered output.
- [ ] **IA-S03** 10MB input test: a 10MB string passed to the pipeline does not hang for more than 5 seconds.

---

## Public Alpha (v0.5)

The bar for Public Alpha is: "Safe for other developers to try. Will not break their AI sessions."

**All Internal Alpha gates must pass first.**

### Additional Functional Gates

- [ ] **PA-F01** `pip install --index-url https://test.pypi.org/simple/ quor` succeeds on a fresh Windows 11 VM (no prior Quor dependencies installed).
- [ ] **PA-F02** `pip install quor` (TestPyPI) succeeds on Ubuntu 22.04 with Python 3.11.
- [ ] **PA-F03** Plugin system: a test plugin installed via entry-points loads and its stage runs in the pipeline.
- [ ] **PA-F04** Plugin failure: a test plugin that raises during `apply()` causes that stage to be skipped; pipeline continues; hook returns output.
- [ ] **PA-F05** Tee mechanism: a filter that produces compressed output also writes original to tee dir; `[full output: path]` appears in compressed output.
- [ ] **PA-F06** `quor validate` completes in <1 second on a config with 10 filters (timed).
- [ ] **PA-F07** Secret detection: an output line containing a GitHub token pattern (`ghp_...`) causes a warning to stderr. Hook output (stdout) is unaffected.
- [ ] **PA-F08** Onboarding mode: first 5 filtered commands print brief stats to stderr. Command 6 is silent.
- [ ] **PA-F09** 3 non-builder developers have installed and used Quor without reported hook failures.

### Additional Quality Gates

- [ ] **PA-Q01** GitHub Actions CI passes on `windows-latest` (Python 3.11, 3.12).
- [ ] **PA-Q02** GitHub Actions CI passes on `ubuntu-latest` (Python 3.11, 3.12).
- [ ] **PA-Q03** Coverage ≥80% on `quor/pipeline/`, `quor/filters/`, and `quor/rewrite/`.
- [ ] **PA-Q04** Default test suite (`pytest` with no flags) completes in <30 seconds on both CI platforms.
- [ ] **PA-Q05** JSON Schema generated from Pydantic models matches the schema referenced in built-in filter TOML files (`yaml-language-server` directive points to a valid, current schema).
- [ ] **PA-Q06** `quor doctor --timing` reports hook response latency <50ms on the builder's machine.
- [ ] **PA-Q07** 100+ command classifier fixtures all pass.

### Safety Gates

- [ ] **PA-S01** 5+ hours of real Claude Code session use with no hook failure reported by any tester.
- [ ] **PA-S02** Heredoc exclusion: a command containing `<<EOF` is not rewritten by the classifier.
- [ ] **PA-S03** Pipe-incompatible exclusion: `find . -name "*.py" | xargs cat` is not rewritten.
- [ ] **PA-S04** `gh --json` exclusion: `gh pr list --json number` is not rewritten.
- [ ] **PA-S05** Untrusted project filter: a `.quor/filters.toml` that is NOT git-tracked → warning to stderr, filter not loaded.

### Documentation Gates

- [ ] **PA-D01** README.md contains: one-sentence description, Windows-first callout, `pip install quor`, quick-start (5 commands), before/after example.
- [ ] **PA-D02** CONTRIBUTING.md is complete (setup, workflow, testing, PR process, filter contribution, plugin development).
- [ ] **PA-D03** CLAUDE.md is present (AI assistant instructions).

---

## Beta (v0.9)

The bar for Beta is: "Ready for production use by motivated early adopters."

**All Public Alpha gates must pass first.**

### Additional Functional Gates

- [ ] **B-F01** At least one community-contributed or externally-developed plugin is installable and works.
- [ ] **B-F02** `quor validate` accepts all filter TOML files written for v0.5 (backwards-compatible validation).
- [ ] **B-F03** `quor doctor` warns if mode is AUDIT for >7 days.
- [ ] **B-F04** `quor init --claude` handles existing hook gracefully: shows current state, prompts before overwriting.
- [ ] **B-F05** Cleanup: SQLite records older than 90 days are removed at session start (weekly, tracked).

### Additional Quality Gates

- [ ] **B-Q01** Python 3.13 added to CI matrix. All tests pass.
- [ ] **B-Q02** Zero open P0 bugs.
- [ ] **B-Q03** Zero open P1 bugs with workaround marked "unavailable."
- [ ] **B-Q04** Plugin API has not changed since v0.5 (stability proof for the `api_version = 1` contract).

### Safety Gates

- [ ] **B-S01** `quor gain` output: every token count includes ±20% uncertainty label (visual inspection of 5 different `quor gain` outputs).
- [ ] **B-S02** `quor gain` and documentation do not claim AI quality improvement — only token savings.

---

## v1.0 Stable

The bar for v1.0 is: "Production-ready. Recommended to all Python-environment AI developers."

**All Beta gates must pass first.**

### Functional Gates

- [ ] **V1-F01** `pip install quor` (main PyPI) succeeds on:
  - Fresh Windows 11 VM, Python 3.11
  - Fresh Windows 11 VM, Python 3.12
  - Ubuntu 22.04, Python 3.11
  - macOS 14, Python 3.11 (manual test acceptable)
- [ ] **V1-F02** `quor doctor` shows all green on all platforms in V1-F01.
- [ ] **V1-F03** End-to-end test on Windows: install → init → real Claude Code session (30+ minutes) → gain → verify. No hook failure.
- [ ] **V1-F04** `quor` and `qr` entry points both registered and functional on all platforms.
- [ ] **V1-F05** Filter contribution: a new community-contributed built-in filter merged via PR, following the contribution process in CONTRIBUTING.md.
- [ ] **V1-F06** Plugin API: a `quor-*` namespaced package published to PyPI, installable with `pip install quor-[name]`, functional.

### Quality Gates

- [ ] **V1-Q01** Coverage ≥80% on all core modules.
- [ ] **V1-Q02** CI on windows-latest, ubuntu-latest, and macos-latest.
- [ ] **V1-Q03** Zero open P0 bugs. Zero open P1 bugs.
- [ ] **V1-Q04** All DECISIONS.md ADRs have a corresponding implementation that matches the decision.
- [ ] **V1-Q05** All ANTI_GOALS.md items verified: no anti-goal features present in codebase (grep + code review).
- [ ] **V1-Q06** CHANGELOG.md complete for all versions.
- [ ] **V1-Q07** All six CLI commands have integration tests in addition to unit tests.

### Documentation Gates

- [ ] **V1-D01** README.md reviewed by a developer who has never seen the project. They can install and use Quor in <15 minutes without asking questions.
- [ ] **V1-D02** CONTRIBUTING.md reviewed by an external contributor candidate. They can submit a filter PR without asking questions.
- [ ] **V1-D03** CLAUDE.md tested: a fresh Claude Code session using only CLAUDE.md as context produces correct code on a described task.
- [ ] **V1-D04** JSON Schema published and accessible at the URL referenced in generated config files.
- [ ] **V1-D05** Migration guide: differences between Quor TOML format and RTK format are documented.

### Safety Gates

- [ ] **V1-S01** 20+ hours of real Claude Code session use across at least 3 users with no hook failure.
- [ ] **V1-S02** A deliberate over-aggressive filter test: a filter that removes ALL content → hook returns `on_empty` string, not empty output.
- [ ] **V1-S03** A filter with a catastrophically backtracking regex: pattern times out after 1 second, warning logged, hook returns original content.

---

## Gate Severity Reference

| Code | Meaning |
|---|---|
| F | Functional — the feature must work correctly |
| Q | Quality — the code must meet quality standards |
| S | Safety — the system must fail safely |
| D | Documentation — users must be able to understand and use the feature |

All gates are blocking at their milestone. There is no "informational" gate.
