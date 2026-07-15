# Contributing to Quor

Quor is a rule-based command-output compression layer for AI coding assistants. This guide gets a
new contributor from a clean checkout to an open pull request.

> This is the quick-start guide. For the exhaustive contributor reference (branching model, commit
> message convention, plugin development, release process) see the root
> [`/CONTRIBUTING.md`](../CONTRIBUTING.md). For AI-assisted development rules see
> [`docs/final/CLAUDE.md`](final/CLAUDE.md).

---

## Development Setup

**Prerequisites:**
- Python 3.11 or higher (Quor uses stdlib `tomllib`, which requires 3.11+)
- Git
- Any of Windows 10/11, Ubuntu 20.04+, or macOS 12+ — CI runs both `ubuntu-latest` and
  `windows-latest` across Python 3.11–3.14

**Clone and install:**

```bash
git clone https://github.com/priyanshup/Quor.git
cd Quor
pip install -e ".[dev]"
pip install -e ./tests/fixtures/test_plugin
```

The second command is required, not optional — the plugin-discovery test suite depends on the
`quor-test-stage` fixture package, which is deliberately *not* declared as a `pyproject.toml`
dependency (a relative `file:` URL there would break `pip install quor[dev]` for anyone installing
from PyPI rather than from a source checkout).

The `dev` extra installs everything needed to build, test, and lint Quor: `pytest`/`pytest-cov`,
exact-pinned `mypy`/`ruff` (see `pyproject.toml`'s comments for why these two are pinned rather
than bounded), and the fixture-generation libraries (`python-docx`, `pdfplumber`, `reportlab`,
and the `tree-sitter*` grammars) used by the document- and AST-related test suites.

**Optional extras**, install only if you're working on that area specifically:

```bash
pip install -e ".[documents]"   # DOCX/PDF extraction
pip install -e ".[javascript]"  # JS + TS/TSX AST summarization
pip install -e ".[go]"          # Go AST summarization
pip install -e ".[java]"        # Java AST summarization
pip install -e ".[rust]"        # Rust AST summarization
pip install -e ".[csharp]"      # C# AST summarization
```

(All of these are already included in `dev`, so contributors running the full test suite don't
need to install them separately — the extras above matter mainly for a minimal, single-purpose
install.)

**Verify the setup:**

```bash
python -m quor doctor
pytest tests/
mypy quor/
ruff check quor/ tests/
```

All four should pass on a clean checkout before you start making changes.

> **Invoke the CLI via `python -m quor` / `python -m qr`, not the bare `quor`/`qr` command**, when
> testing from a shell during development. On some locked-down Windows environments, the
> pip-generated launcher `.exe` stubs are silently blocked by endpoint-protection application
> control policy. `python -m` always works and is what the project's own hook path uses
> internally (see `docs/final/DECISIONS.md` ADR-029).

---

## Running Tests

```bash
# Default run — fast, unit + non-integration tests, excludes real subprocess/filesystem tests
pytest tests/

# Same, with coverage (what CI runs)
pytest tests/ --cov=quor --cov-report=term-missing

# Integration tests only (real filesystem/subprocess; requires a real Claude Code install)
pytest tests/ -m integration

# Stop at the first failure while iterating
pytest tests/unit/ -x

# A single test file or test
pytest tests/unit/test_pipeline.py
pytest tests/unit/test_pipeline.py::test_strip_lines_removes_ansi
```

The default `pytest tests/` invocation excludes anything marked `@pytest.mark.integration` — this
keeps the everyday loop under the project's ~30-second target. Integration tests run as a separate
CI step (`pytest tests/ -m integration`).

Test isolation is automatic: an autouse fixture in `tests/conftest.py` patches `platformdirs` so
every test reads/writes to a fresh temp directory. No test should ever touch your real
`~/.config/quor/` or `~/.local/share/quor/`.

Filters also carry their own inline tests (`[[filter.tests]]` blocks in the TOML files under
`quor/filters/builtin/`), run via:

```bash
python -m quor verify
```

This must pass before opening a PR that touches any filter.

---

## Running Benchmarks

Quor ships a compression-regression benchmark suite under `tests/benchmarks/` — it runs
automatically as part of `pytest tests/`, but the standalone runner produces a fuller report:

```bash
# Run once, compare against the committed baseline, write JSON + Markdown reports
python -m tests.benchmarks.run_benchmarks

# Only JSON, to a custom directory
python -m tests.benchmarks.run_benchmarks --output-dir /tmp/bench --format json

# Skip baseline comparison (metrics/reports only)
python -m tests.benchmarks.run_benchmarks --no-compare

# After an intentional compression change, refresh the committed baseline
python -m tests.benchmarks.run_benchmarks --update-baseline
```

Reports land in `tests/benchmarks/results/` (gitignored — per-run artifacts, not source). Run this
suite standalone whenever you touch `quor/pipeline/`, `quor/filters/`, or `quor/rewrite/` — see
`tests/benchmarks/README.md` for the full explanation of what each signal means and how to
interpret a failure.

---

## Coding Standards

**Formatting & linting:** `ruff format quor/` before committing; `ruff check quor/ tests/` must be
clean. Line length is 100 (E501 is ignored — formatting handles wrapping, not manual line breaks).

**Type checking:** `mypy quor/` runs in `strict` mode and must be clean. All public functions and
methods need type annotations.

**Comments:** Default to none. Only add a comment when the *why* isn't obvious from the code
itself — never restate what well-named identifiers already say.

**Naming:** `PascalCase` for classes, `snake_case` for functions/variables, `UPPER_SNAKE_CASE` for
constants, `_leading_underscore` for private members.

**Imports:** stdlib, then third-party, then Quor-internal (enforced by ruff-isort). No wildcard
imports.

**Strings & serialization:** f-strings only (no `.format()`/`%`); `orjson` for all JSON, never the
stdlib `json` module.

**Error handling:** never a bare `except:` — always catch a specific type. Never `assert` for
validation (`assert` is stripped under `python -O`) — use `if`/`raise`. All Quor-raised exceptions
inherit from `QuorError` or a subclass.

**Paths:** always `platformdirs` for config/data directories, always `encoding="utf-8"` on
`open()`, always `Path.as_posix()` when a path is stored as a string, never a hardcoded `/tmp` —
use `tempfile.mkdtemp()`.

---

## Adding a Language

Quor's `code_ast_summarize` pipeline stage compresses source code by collapsing function/method
bodies while preserving signatures. Language support lives in
`quor/pipeline/ast_summarize/` — one module per language (`python.py`, `javascript.py`,
`typescript.py`, `go.py`, `java.py`, `rust.py`, `csharp.py`), registered in `registry.py`, which
maps a language name to its analyzer and (for tree-sitter-backed languages) the optional extra
that must be installed for it to work.

To add a new language:

1. **Open an issue first** naming the language and a sample of real source code you intend to
   test against.
2. **Add a `tree-sitter-<language>` dependency** as its own `pyproject.toml` extra (e.g.
   `[project.optional-dependencies] kotlin = [...]`), matching the existing per-language extras
   (`go`, `java`, `rust`, `csharp`) rather than folding it into an unrelated one — each extra lets
   a contributor who only needs one language avoid pulling in grammars they don't use. Also add
   the same dependency to the `dev` extra, so the full test suite exercises real fixture coverage
   without a separate install step, and respect the existing `tree-sitter<0.26.0` ceiling shared
   by every language extra (see `pyproject.toml`'s comment on that pin — it works around a
   verified upstream memory-corruption bug, not an arbitrary version freeze).
3. **Write the analyzer module** (`quor/pipeline/ast_summarize/<language>.py`), exposing an
   `analyze_<language>()` entry point that returns the compressible line ranges. It must **fail
   open**: if the grammar package isn't installed, or parsing fails, return an empty result and
   warn — never raise. Reuse shared tree-sitter helpers from `_treesitter_utils.py` rather than
   reimplementing ERROR-node handling per language.
4. **Register it** in `registry.py` alongside the language's required extra, so
   `code_ast_summarize` can dispatch to it by name and `quor validate`/`quor doctor` can report a
   missing extra actionably.
5. **Add a `mypy` override** for the new `tree_sitter_<language>` module in `pyproject.toml`'s
   `[[tool.mypy.overrides]]` list (`ignore_missing_imports = true`) — the dependency is optional at
   runtime, so `mypy quor/` must succeed whether or not the extra is installed.
6. **Add a filter** (see "Adding a Filter" below) that routes the language's file extension(s)
   through `code_ast_summarize` with the right `language` value, plus benchmark coverage.
7. **Update `docs/final/COMMAND_SUPPORT.md`** so the new language appears in the canonical
   command/filter table.

---

## Adding a Filter

Filters live as TOML files under `quor/filters/builtin/` (one file per command category, e.g.
`git.toml`, `cat-python.toml`) and are the most impactful kind of contribution.

1. **Open an issue** describing the target command, with a real sample of its output and what
   should be kept vs. removed.
2. **Capture real output** and save it to `tests/fixtures/outputs/<command>_output.txt` — this
   becomes the basis for your filter's tests.
3. **Write the filter tests first**, as `[[filter.tests]]` entries (at least 3), then write the
   `[[filter.stages]]` pipeline until they pass. Run `python -m quor verify` to check.
4. **Add benchmark coverage** — a `[[case]]` entry in `tests/benchmarks/manifest.toml` plus a
   realistic sample file under `tests/benchmarks/samples/<category>/`, then commit a baseline via
   `python -m tests.benchmarks.run_benchmarks --update-baseline`. See `tests/benchmarks/README.md`
   for the exact manifest fields. This is required for every new built-in filter, on top of the
   inline `[[filter.tests]]`.
5. **Sanity-check the trace:** `python -m quor explain "<your command>"` should show the stages
   firing as expected.

**Filter checklist:**
- At least 3 `[[filter.tests]]` entries
- `must_contain` — confirms critical lines survive
- `must_not_contain` — confirms noise is removed
- `compression_target` set to a meaningful, honest value
- `preserve_patterns` protecting error/exception patterns
- `on_empty` defined if the filter can produce empty output
- A benchmark `[[case]]` + sample file + committed baseline (QB-011)

**Placement:** a new command in an existing category goes in that category's existing TOML file
(e.g. a new `git cherry-pick` filter goes in `git.toml`). A genuinely new category gets its own
TOML file — open an issue to discuss first. Tool-specific or company-specific filters that aren't
appropriate for the built-in distribution should ship as a plugin package instead (see the root
`CONTRIBUTING.md`'s "Plugin Development" section).

---

## Updating Documentation

Documentation that must stay in sync with a code change is not optional polish — treat it as part
of the PR, the same way you'd treat a broken test:

| If you changed... | Update... |
|---|---|
| A filter (new or modified) | `docs/final/COMMAND_SUPPORT.md`'s command table |
| Pipeline/stage behavior, plugin API, TOML schema | `docs/final/CLAUDE.md` and, for a genuinely new architectural decision, `docs/final/DECISIONS.md` |
| User-facing CLI behavior | `README.md` |
| Anything worth calling out to users on the next release | `CHANGELOG.md` |
| A backlog item's status | `backlog.md` (mark it Resolved/Closed, and spin out any follow-up items you discovered) |

If a change doesn't fit neatly into one of these, ask in the PR description rather than skipping
documentation entirely — reviewers will check for this (see "What reviewers check" in the root
`CONTRIBUTING.md`).

---

## PR Checklist

Before opening a pull request:

- [ ] `pytest tests/ --cov=quor --cov-report=term-missing` passes
- [ ] `pytest tests/ -m integration` passes (if you have a real Claude Code install to test against)
- [ ] `mypy quor/` is clean
- [ ] `ruff check quor/ tests/` is clean (and `ruff format quor/` has been run)
- [ ] `python -m quor verify` passes (required if any filter TOML changed)
- [ ] Coverage on changed modules is ≥80%
- [ ] Benchmark suite has no unexplained regression — `python -m tests.benchmarks.run_benchmarks`
      (required if `quor/pipeline/`, `quor/filters/`, or `quor/rewrite/` changed). If the change
      intentionally alters compression, update the baseline and explain why in the PR body.
- [ ] Windows CI passes, not just your local OS (CI runs `ubuntu-latest` and `windows-latest`)
- [ ] Relevant docs updated (see "Updating Documentation" above)
- [ ] No unrelated changes — the PR touches only what the linked issue/backlog item requires
- [ ] No hardcoded paths, no bare `except:`, no `assert` used for validation
- [ ] PR title follows `[component] brief imperative description` (components: `pipeline`,
      `filters`, `rewrite`, `tracking`, `cli`, `adapters`, `plugins`, `packaging`, `docs`)

See the root [`/CONTRIBUTING.md`](../CONTRIBUTING.md) for the full PR body template, branching
model, and commit message convention.
