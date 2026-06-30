# Engineering Patterns
## Architectural Inspiration Study — Pre-Implementation Reference

> Source material: DSPy, LLMLingua, TextGrad, Promptfoo, SAMMO, EvoPrompt, Headroom AI
> Purpose: Extract the best engineering ideas across the ecosystem before the first line of production code is written.
> This document supersedes and refines the architectural decisions in zap-analysis.md and final-discovery.md where they conflict.

---

# 1. Best Engineering Patterns

## 1.1 Configuration Design

---

### Pattern: Pydantic as the Single Source of Truth

**Source:** Promptfoo (Zod → TypeScript types + JSON Schema); DSPy (Pydantic for all field definitions)

**The pattern:** Define configuration models once in Pydantic. Derive everything else from that definition — TypeScript types, JSON Schema for IDEs, runtime validation errors, CLI help text. Do not maintain a parallel schema.

```python
from pydantic import BaseModel, Field
from typing import Optional, Literal

class StageConfig(BaseModel):
    type: str
    rate: Optional[float] = Field(None, ge=0.0, le=1.0,
        description="Fraction of tokens to keep (0.0–1.0)")
    max_tokens: Optional[int] = Field(None, gt=0,
        description="Hard token ceiling for this stage output")
    preserve_patterns: list[str] = Field(default_factory=list,
        description="Regex patterns; matching lines are never removed")

class FilterConfig(BaseModel):
    match_command: str
    description: str = ""
    stages: list[StageConfig]
    tests: list[TestCase] = Field(default_factory=list)
    schema_version: int = 1
```

Generate the JSON Schema once and publish it to a stable URL. Add the yaml-language-server directive to every generated config file:

```yaml
# yaml-language-server: $schema=https://sieve.dev/config-schema.json
```

This is one comment line that activates autocomplete, hover documentation, and inline validation in VS Code, Zed, and any yaml-language-server-aware editor. It costs nothing and makes the tool feel professional.

**Adopt unchanged.** Use `pydantic` (not `dataclasses`) throughout. Generate JSON Schema with `model_json_schema()`. Publish at a versioned URL.

---

### Pattern: Three Levels of Verbosity in Configuration

**Source:** Promptfoo (provider specification at three verbosity levels)

**The pattern:** Any declarative object in the configuration should accept three formats: a simple string, a structured dict with defaults, and a file reference for arbitrary complexity.

```yaml
# Level 1: built-in by name (simple case)
stages:
  - remove_ansi

# Level 2: built-in with configuration (medium complexity)
stages:
  - type: strip_lines
    patterns: ["^PASSED", "^\\.+"]
    max_tokens: 500

# Level 3: file reference (full custom stage)
stages:
  - type: file://./stages/company_internal_filter.py
```

The parser detects which level is used. Level 1 names are resolved against the built-in stage registry. Level 3 invokes the plugin loading system.

**Adopt unchanged.** The `file://` escape hatch (Pattern 1.5 below) is the right mechanism for Level 3.

---

### Pattern: The Minimal Valid Config Is Genuinely Minimal

**Source:** Promptfoo

**The pattern:** The starter config that `init` generates should have no optional fields. Every field in the generated file must be required for the tool to function. The user should understand the file completely before they need to change it.

```yaml
# yaml-language-server: $schema=https://sieve.dev/config-schema.json

filters:
  - match_command: "^pytest\\b"
    stages:
      - remove_ansi
      - type: strip_lines
        patterns: ["^PASSED", "^\\.+"]
      - type: max_tokens
        limit: 500
    tests:
      - input: "PASSED test_auth.py::test_login\nFAILED test_auth.py::test_logout\nAssertionError"
        must_contain: ["FAILED", "AssertionError"]
        must_not_contain: ["PASSED"]
```

**Adopt unchanged.** The `init` command creates exactly this file and nothing else. No README, no provider file, no example scripts.

---

### Pattern: Dual-Mode Compression Goals

**Source:** LLMLingua (`rate` vs `target_token`)

**The pattern:** Every compression stage should accept two equivalent ways to express its goal: a fractional rate ("keep 30% of tokens") or an absolute token budget ("keep at most 500 tokens"). Both convert internally to an absolute token budget before execution.

```yaml
# Both are valid and equivalent in intent:
stages:
  - type: truncate
    rate: 0.3          # keep 30% of input tokens

  - type: truncate
    max_tokens: 500    # hard ceiling regardless of input size
```

When both are specified, `max_tokens` wins. When neither is specified, the stage uses its default (typically `rate: 1.0`, meaning pass through). The pipeline tracks a running token budget across stages.

**Adopt with improvement:** Add a third mode for the pipeline-level config: `session_budget: 2000` — a session-wide token ceiling that the pipeline enforces across all commands in a session.

---

### Pattern: `validate` as a First-Class CLI Command

**Source:** Promptfoo (`promptfoo validate config`)

**The pattern:** Provide a `validate` command that checks all configuration correctness without executing any compression or making any LLM calls. It should complete in under one second.

What `validate` should check:
1. Config file parses without error against the Pydantic schema
2. All `file://` stage references resolve and import successfully
3. All `match_command` patterns compile as valid regex
4. All inline test inputs and expected outputs are syntactically correct
5. No `strip_lines` and `keep_lines` both specified in the same stage (mutual exclusion)
6. All `preserve_patterns` compile as valid regex

**Adopt unchanged.** This command must be in V1. It is the CI pre-flight check that prevents running expensive benchmarks against broken configs.

---

## 1.2 Plugin and Extensibility Architecture

---

### Pattern: Entry-Points Plugin Discovery

**Source:** Headroom AI; DSPy's named_sub_modules traversal

**The pattern:** Plugins are Python packages installed separately. They register themselves via Python entry points in their `pyproject.toml`:

```toml
# In the plugin package's pyproject.toml:
[project.entry-points."sieve.compression_stage"]
kubernetes = "sieve_kubernetes:KubernetesStageHandler"
helm = "sieve_kubernetes:HelmStageHandler"
```

The core tool discovers plugins at startup:
```python
from importlib.metadata import entry_points

def discover_stages() -> dict[str, type[StageHandler]]:
    discovered = {}
    for ep in entry_points(group="sieve.compression_stage"):
        try:
            handler = ep.load()
            discovered[ep.name] = handler
        except Exception as e:
            logger.warning(f"Failed to load stage '{ep.name}': {e}")
    return discovered
```

**Fail-open:** a plugin that fails to load is logged as a warning and skipped. It never prevents the core pipeline from running.

**Adopt with improvement:** Cache the discovered plugin map to disk after first discovery (invalidate when any package is installed). Entry-point scanning on every invocation adds 20–100ms.

---

### Pattern: Typed Protocol for Plugin Interface

**Source:** DSPy (from the limitation: `**kwargs` in optimizer interface makes contracts invisible); Headroom AI (`StructureHandler` Protocol)

**The pattern:** Define the plugin interface as a `@runtime_checkable Protocol`, not as an abstract base class or duck-typed dict. Validate compliance at registration time, not at runtime.

```python
from typing import Protocol, runtime_checkable
from dataclasses import dataclass

@dataclass
class StageResult:
    content: str
    original_tokens: int
    final_tokens: int
    rule_name: str
    lines_removed: int
    patterns_matched: list[str]
    was_skipped: bool

@runtime_checkable
class StageHandler(Protocol):
    name: str
    api_version: int  # increment when interface changes

    def can_handle(self, content: str, content_type: str) -> bool:
        """Return True if this stage should process this content."""
        ...

    def apply(self, content: str, config: dict) -> StageResult:
        """Apply the stage. Must be deterministic. Never raise."""
        ...
```

At plugin registration: `if not isinstance(handler_instance, StageHandler): raise PluginError(...)`. This fails immediately with a clear message instead of failing obscurely at runtime.

**Adopt unchanged.** Version the interface with `api_version: int`. When the interface changes, bump the version and add a compatibility shim for `api_version == 1`.

---

### Pattern: `file://` Escape Hatch

**Source:** Promptfoo

**The pattern:** Any value in the configuration that could reasonably be a custom function can also be a `file://` reference to a Python module. The module must export a function matching a defined signature.

```yaml
stages:
  - type: file://./stages/internal_log_format.py
    config:
      severity_threshold: WARNING
```

The loader calls `importlib.util.spec_from_file_location()`, loads the module, and expects to find a class or function matching the `StageHandler` Protocol. The `config` dict is passed through unchanged.

**This is the escape hatch that makes the TOML format sufficient for every use case.** No matter how specialized the compression need, there is always a path to Python without abandoning the declarative format.

**Adopt unchanged.**

---

### Pattern: Named Parameter Tree Traversal

**Source:** DSPy (`named_sub_modules()` and `named_parameters()`)

**The pattern:** The pipeline object should support breadth-first traversal that returns `(qualified_name, component)` tuples. A pipeline of stages named `a → b → c` would yield `("stages[0]", a)`, `("stages[1]", b)`, `("stages[2]", c)`. Nested pipelines yield `("inner.stages[0]", ...)`.

```python
def named_stages(pipeline) -> Iterator[tuple[str, StageHandler]]:
    """Breadth-first traversal with cycle detection."""
    seen = set()
    queue = deque([("", pipeline)])
    while queue:
        prefix, obj in queue.popleft()
        if id(obj) in seen:
            continue
        seen.add(id(obj))
        for i, stage in enumerate(obj.stages):
            name = f"{prefix}stages[{i}]" if prefix else f"stages[{i}]"
            yield name, stage
            if hasattr(stage, "stages"):  # nested pipeline
                queue.append((name + ".", stage))
```

**Why this matters:** It enables `sieve explain` to show the exact qualified name of every stage that fired. It enables `sieve doctor` to check every registered stage for compatibility. It enables future optimizers to target specific stages by name.

**Adopt unchanged.**

---

## 1.3 Pipeline Architecture

---

### Pattern: The Mask Abstraction

**Source:** Headroom AI (`StructureMask`, `HandlerResult.mask`)

**The pattern:** The core primitive of a compression stage is not a string transformation — it is a **mask**: a data structure that annotates each line (or token) as KEEP or COMPRESS. The actual text transformation happens once, at the end of the pipeline, when the accumulated mask is rendered.

```python
from enum import Enum
from dataclasses import dataclass, field

class Decision(Enum):
    KEEP = "keep"         # preserve verbatim
    COMPRESS = "compress" # eligible for removal
    PROTECT = "protect"   # never remove (preserve_patterns match)

@dataclass
class LineMask:
    line: str
    decision: Decision
    reason: str           # which rule made this decision
    stage: str            # which stage made this decision

@dataclass
class ContentMask:
    lines: list[LineMask]
    content_type: str
    
    @property
    def kept_content(self) -> str:
        return "\n".join(l.line for l in self.lines if l.decision != Decision.COMPRESS)
    
    @property
    def compression_ratio(self) -> float:
        kept = sum(1 for l in self.lines if l.decision == Decision.KEEP)
        return kept / len(self.lines) if self.lines else 1.0
```

Each stage receives a `ContentMask` and returns a new `ContentMask` with updated decisions. Stages never modify line content — only the `decision` field. The final stage renders the mask to a string.

**Why this is important:**
1. Any stage's decision is auditable: `sieve explain` walks the mask and shows which stage made each decision and why.
2. `PROTECT` decisions from `preserve_patterns` propagate through all subsequent stages — a later aggressive stage cannot override an earlier protection.
3. Stage composition is safe: order matters for `COMPRESS` decisions but `PROTECT` is absolute.
4. The mask is the natural data structure for the AUDIT mode (accumulate mask, report, return original) and SIMULATE mode (accumulate mask, render, report, return original).

**Adopt with simplification:** For V1, the mask is line-level (not character-level). Character-level masking (Headroom's approach) is more powerful but adds complexity that is not needed until tree-sitter integration in V2.

---

### Pattern: Three Operating Modes

**Source:** Headroom AI (`HeadroomMode.AUDIT | OPTIMIZE | SIMULATE`)

**The pattern:** The pipeline should operate in one of three modes at all times:

```python
class PipelineMode(Enum):
    AUDIT = "audit"       # Run pipeline, log mask, return ORIGINAL output to AI
    OPTIMIZE = "optimize" # Run pipeline, return COMPRESSED output to AI (default)
    SIMULATE = "simulate" # Run pipeline, log mask and stats, return ORIGINAL (no DB writes)
```

AUDIT mode is the right default for the first week after installation. The AI receives uncompressed output; the user can run `sieve gain --audit` to see what savings are being computed. After reviewing the audit log and trusting the filters, they switch to OPTIMIZE.

SIMULATE is for filter development: "what would happen if I deployed this filter?" It runs the full pipeline, prints the diff, and exits without touching the database or affecting the hook response.

**CLI:**
```
sieve init --claude              # Installs in AUDIT mode by default
sieve mode optimize              # Switch to OPTIMIZE
sieve mode simulate              # One-shot simulation
sieve run --mode simulate        # Ad-hoc simulation for one command
```

**Adopt unchanged.** This is not optional. AUDIT mode is how we build trust in corporate environments where no tool can change AI behavior without prior review.

---

### Pattern: Fail-Open as an Architectural Invariant

**Source:** Headroom AI (extension failure logging); RTK (raw passthrough on error)

**The pattern:** The hook must return a valid response to the AI even if every stage fails. The error path returns the original, unmodified content with a warning logged to stderr (not stdout). The AI never sees an error from the hook.

```python
def apply_pipeline(content: str, pipeline: Pipeline) -> tuple[str, PipelineResult]:
    try:
        mask = ContentMask.from_string(content)
        for stage in pipeline.stages:
            try:
                mask = stage.apply(mask, stage.config)
            except Exception as e:
                logger.warning(f"Stage '{stage.name}' failed: {e}. Skipping.")
                # mask is unchanged — stage is skipped
        return mask.kept_content, PipelineResult.from_mask(mask)
    except Exception as e:
        logger.error(f"Pipeline failed catastrophically: {e}. Returning original.")
        return content, PipelineResult.passthrough(content)
```

Every public function in the pipeline must have a `never-raise` contract enforced by:
1. A top-level exception guard in the hook entry point
2. Per-stage exception guards that skip (not halt) on failure
3. Integration tests that verify the hook returns valid JSON even when every stage raises

**Adopt unchanged.** This is not a nice-to-have. A hook that can crash will crash.

---

### Pattern: Content-Type Detection with Two-Tier Fallback

**Source:** Headroom AI (`MagikaDetector` → `FallbackDetector`)

**The pattern:** Before routing content to a compression engine, detect its content type. Use a rich detector (Google's Magika ML model) if available; fall back to fast heuristics if not. The heuristic fallback must work perfectly on Windows without any optional dependency.

```python
def detect_content_type(content: str) -> ContentType:
    """Two-tier detection. Heuristic fallback is always available."""
    # Tier 1: JSON detection (deterministic)
    stripped = content.strip()
    if stripped.startswith(("{", "[")):
        try:
            json.loads(stripped)
            return ContentType.JSON
        except json.JSONDecodeError:
            pass
    
    # Tier 2: Log output detection
    log_patterns = [r"^\[?(ERROR|WARN|INFO|DEBUG|FATAL)\]?", r"^\d{4}-\d{2}-\d{2}.*ERROR"]
    if any(re.search(p, content, re.MULTILINE) for p in log_patterns):
        return ContentType.LOG
    
    # Tier 3: Code detection
    code_signals = ["def ", "class ", "import ", "function ", "const ", "#include"]
    if sum(1 for s in code_signals if s in content) >= 2:
        return ContentType.CODE
    
    # Tier 4: Diff detection
    if re.search(r"^[+-]{3} ", content, re.MULTILINE):
        return ContentType.DIFF
    
    return ContentType.TEXT
```

The content type determines which built-in stage handlers are offered by default. A `git diff` output goes to the DIFF engine; pytest output goes to the LOG/TEST engine.

**Adopt with the heuristic-only approach for V1.** The Magika ML detector is an optional extra in V2: `pip install sieve[ml]`.

---

### Pattern: Standardized Result Contract

**Source:** LLMLingua (standardized return dict); SAMMO (`EvaluationScore` with mistakes list)

**The pattern:** Every pipeline execution, every engine, every edge case returns the same result object shape. Callers can always inspect the same fields regardless of which path ran.

```python
@dataclass
class PipelineResult:
    original_tokens: int
    final_tokens: int
    ratio: float                     # final / original, e.g. 0.23
    compression_pct: float           # 1 - ratio, e.g. 0.77 = "77% reduction"
    stages_applied: list[str]        # ["remove_ansi", "strip_lines", "max_tokens"]
    stages_skipped: list[str]        # stages where can_handle() returned False
    constraints_violated: list[str]  # e.g. ["max_tokens exceeded despite truncation"]
    content_type: str                # detected content type
    mode: str                        # "audit" | "optimize" | "simulate"
    was_passthrough: bool            # True if no stage modified the content
    duration_ms: float
```

Include `was_passthrough` as an explicit boolean (not a zero-token sentinel — this was a Zap limitation we already identified). Include `stages_applied` for the `sieve explain` command. Include `constraints_violated` for the quality assurance layer.

**Adopt unchanged.**

---

### Pattern: Immutable Transformation Chain

**Source:** DSPy (Signature returns new class on every modification; never mutates in place)

**The pattern:** Pipeline stages receive a `ContentMask` and return a new `ContentMask`. They never mutate the input. The pipeline object itself is immutable after construction.

```python
# Stage does NOT do this:
def apply(self, mask: ContentMask) -> None:
    for line in mask.lines:
        if self.pattern.match(line.line):
            line.decision = Decision.COMPRESS  # WRONG: mutates input

# Stage DOES do this:
def apply(self, mask: ContentMask) -> ContentMask:
    new_lines = []
    for line_mask in mask.lines:
        if line_mask.decision == Decision.PROTECT:
            new_lines.append(line_mask)  # protection is absolute
        elif self.pattern.match(line_mask.line):
            new_lines.append(replace(line_mask, decision=Decision.COMPRESS, reason=self.name))
        else:
            new_lines.append(line_mask)
    return ContentMask(lines=new_lines, content_type=mask.content_type)
```

Using `dataclasses.replace()` creates a new `LineMask` with only the changed fields. The original is preserved for audit logging.

**Adopt unchanged.** This is the single most important architectural decision. It enables undo, parallel stage evaluation, and audit trails at zero marginal cost.

---

### Pattern: `can_handle()` Guard on Every Stage

**Source:** Headroom AI (`StructureHandler.can_handle()`)

**The pattern:** Every stage declares whether it should run on a given content. If `can_handle()` returns False, the stage is skipped cleanly (logged in `stages_skipped`). The mask passes through unchanged.

```python
class RemoveAnsiStage(StageHandler):
    name = "remove_ansi"
    api_version = 1

    def can_handle(self, content: str, content_type: str) -> bool:
        # Only run if ANSI escape codes are present
        return bool(re.search(r'\x1b\[[\d;]*[a-zA-Z]', content))

    def apply(self, mask: ContentMask, config: dict) -> ContentMask:
        ...
```

This avoids wasting time running a diff parser on JSON, or running a log-level filter on a git diff. The routing table (content type → suggested stages) provides defaults, but `can_handle()` is the final arbiter.

**Adopt unchanged.**

---

## 1.4 CLI Design

---

### Pattern: Two Binaries, One Entry Point

**Source:** Promptfoo (`promptfoo` and `pf` both pointing to the same entrypoint)

**The pattern:** Register both a full name and a short alias in `pyproject.toml`:

```toml
[project.scripts]
sieve = "sieve.cli:main"
sv = "sieve.cli:main"
```

The short alias is not an afterthought — it is the command developers type 50 times a day. Document both in the README from day one. Do not add the short alias in a later release; that creates a documentation split.

**Adopt unchanged.** Choose the alias before the package name is finalized: `sv`, `di`, `st`, `px` — whichever is available and natural.

---

### Pattern: Each Command Is Its Own File

**Source:** Promptfoo (one file per command, subcommand groups in subdirectories)

**The pattern:**
```
src/
  sieve/
    cli.py          # imports and registers all commands; no implementation
    commands/
      init.py       # distill init
      validate.py   # distill validate
      explain.py    # distill explain
      gain.py       # distill gain
      doctor.py     # distill doctor
      benchmark/
        run.py      # distill benchmark run
        compare.py  # distill benchmark compare
```

`cli.py` contains only:
```python
from sieve.commands.init import init_cmd
from sieve.commands.validate import validate_cmd
# ...

app = typer.Typer()
app.add_typer(init_cmd)
app.add_typer(validate_cmd)
```

Each command file is independently readable, independently testable, and independently replaceable. A new contributor can understand the `explain` command by reading one file.

**Adopt unchanged.**

---

### Pattern: Differentiated Exit Codes

**Source:** Promptfoo (exit code 1 for validation errors, exit code 2 for deprecation warnings)

**The pattern:** Define exit codes in a constants file from day one:

```python
class ExitCode(IntEnum):
    SUCCESS = 0
    FILTER_TESTS_FAILED = 1   # sieve verify found failures
    CONFIG_ERROR = 2          # config validation failed
    RUNTIME_ERROR = 3         # unexpected error during compression
    HOOK_ERROR = 4            # hook protocol error (bad JSON, timeout)
    DEPENDENCY_MISSING = 5    # required optional dependency not installed
```

CI scripts can distinguish "bad config" (developer needs to fix) from "filter tests failed" (test failure, may be expected in red-green loop) from "runtime error" (investigate).

**Adopt unchanged.** Document these in the README.

---

### Pattern: `postAction` Lifecycle Hook

**Source:** Promptfoo (`main.ts` `postAction` hook that runs after every command)

**The pattern:** Register a cleanup/teardown function that runs after every command, regardless of success or failure:

```python
@app.callback(invoke_without_command=True)
def main_callback(ctx: typer.Context):
    ctx.ensure_object(dict)
    ctx.obj["start_time"] = time.perf_counter()

@app.result_callback()
def after_command(*args, **kwargs):
    """Runs after every command, including on error."""
    elapsed = time.perf_counter() - ctx.obj.get("start_time", time.perf_counter())
    if ctx.obj.get("verbose"):
        typer.echo(f"Completed in {elapsed:.2f}s", err=True)
    flush_telemetry_async()
    check_for_updates_async()
    cleanup_temp_files()
```

Commands never call `flush_telemetry()` or `check_for_updates()`. Those cross-cutting concerns live here.

**Adopt unchanged.**

---

### Pattern: `showHelpAfterError` and `showSuggestionAfterError` as Default Posture

**Source:** Promptfoo (Commander.js defaults)

**The pattern:** When a user runs an invalid subcommand or passes an invalid argument, they automatically receive: (1) the relevant help text for that command and (2) a fuzzy suggestion for what they might have meant.

In Click/Typer, enable this with:
```python
@app.command()
def main_group(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()
```

And configure Typer with `invoke_without_command=True`, `no_args_is_help=True`, and use `typer.Option` with `rich_help_panel` grouping for help organization.

**Adopt from day one.** Two configuration lines. Pays off immediately.

---

### Pattern: The Entrypoint Does One Thing

**Source:** Promptfoo (`entrypoint.ts` is pure pre-flight validation)

**The pattern:** The `__main__.py` entry point does only:
1. Validate Python version (≥ 3.11 for `tomllib`)
2. Check for critical import availability (report clearly which dependency is missing)
3. Import and invoke the CLI

```python
# sieve/__main__.py
import sys

def _check_python_version():
    if sys.version_info < (3, 11):
        print(f"sieve requires Python 3.11+. Found: {sys.version}", file=sys.stderr)
        print("Install guide: https://sieve.dev/docs/installation", file=sys.stderr)
        sys.exit(1)

def main():
    _check_python_version()
    from sieve.cli import app
    app()

if __name__ == "__main__":
    main()
```

The version check happens before any import that might fail. The import of the CLI module is deferred so import errors report cleanly.

**Adopt unchanged.**

---

## 1.5 Testing Philosophy

---

### Pattern: Autouse Isolation Fixture

**Source:** DSPy (`clear_settings` autouse fixture with temp directory)

**The pattern:** Every test runs in complete isolation from every other test. The autouse fixture creates fresh state for every test:

```python
# conftest.py
import pytest
import tempfile
from pathlib import Path

@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    """Every test gets its own SQLite database and config directory."""
    db_path = tmp_path / "sieve.db"
    config_path = tmp_path / "config"
    config_path.mkdir()

    monkeypatch.setenv("SIEVE_DB_PATH", str(db_path))
    monkeypatch.setenv("SIEVE_CONFIG_DIR", str(config_path))

    yield {"db": db_path, "config": config_path, "tmp": tmp_path}
    # No teardown needed — tmp_path is cleaned up by pytest
```

No test should read from or write to `~/.config/sieve/` or the default SQLite path. Tests that do will pass locally and fail in CI.

**Adopt unchanged.** This fixture must be in place before the first test is written.

---

### Pattern: Flag-Gated Test Categories

**Source:** DSPy (`--llm_call`, `--reliability`, `--extra` pytest flags)

**The pattern:** Tests that require external resources are gated behind explicit flags:

```python
# conftest.py
def pytest_addoption(parser):
    parser.addoption("--integration", action="store_true",
        help="Run tests requiring a live Claude Code hook")
    parser.addoption("--slow", action="store_true",
        help="Run benchmarks (may take several minutes)")

def pytest_collection_modifyitems(config, items):
    for item in items:
        if "integration" in item.keywords and not config.getoption("--integration"):
            item.add_marker(pytest.mark.skip(reason="requires --integration"))
        if "slow" in item.keywords and not config.getoption("--slow"):
            item.add_marker(pytest.mark.skip(reason="requires --slow"))
```

The default `pytest` run (no flags) completes in under 30 seconds and requires no external services.

**Adopt unchanged.** CI runs without `--integration`. Local pre-commit runs without `--slow`.

---

### Pattern: Process Isolation for Stateful Tests

**Source:** Promptfoo (Vitest child process forks, not threads)

**The pattern:** Tests that write to disk, spawn subprocesses, or exercise the hook entry point should run in separate processes. This prevents one test's side effects from contaminating another.

```ini
# pytest.ini or pyproject.toml [tool.pytest.ini_options]
[tool.pytest.ini_options]
addopts = "--dist=loadscope --numprocesses=auto"
```

With `pytest-xdist`, tests in the same module run in the same worker process (loadscope); tests in different modules run in parallel across workers. This gives isolation without the overhead of process-per-test.

For hook integration tests specifically, use `subprocess.run()` with a clean environment rather than calling the hook function directly. The hook's behavior depends on stdout buffering, subprocess environment, and signal handling — things that only manifest in a subprocess context.

**Adopt unchanged.**

---

### Pattern: Filter Tests as Inline First-Class Data

**Source:** Zap/RTK (inline `[[tests]]` in TOML filters); extended by this design

**The pattern:** Every built-in filter must include at least three inline tests. The test format captures: input, expected output characteristics (must_contain, must_not_contain, compression_target), and a description.

```toml
[[filter.pytest.tests]]
description = "Passing tests are removed; failures are preserved"
input = """
PASSED test_auth.py::test_login
FAILED test_auth.py::test_logout
    AssertionError: Expected True, got False
"""
must_contain = ["FAILED", "AssertionError"]
must_not_contain = ["PASSED"]
compression_target = 0.5   # must compress by at least 50%

[[filter.pytest.tests]]
description = "Empty output after filtering returns on_empty message"
input = "PASSED test_auth.py::test_login"
expected_output = "All tests passed."
```

`sieve verify` runs all inline tests and reports pass/fail per test per filter. CI blocks on any failure.

**Adopt unchanged.** Minimum three tests per filter. PR review should reject filters with fewer.

---

## 1.6 Caching and Persistence

---

### Pattern: SHA256 Content-Addressable Cache Keys

**Source:** DSPy (SHA256 of `orjson.dumps(sorted_dict)`); TextGrad (SHA256 fingerprinting with diskcache)

**The pattern:** Cache keys are derived from content, not from session identifiers. Strip all session-specific fields (API keys, timestamps, session IDs) before hashing.

```python
import orjson
from hashlib import sha256

def content_cache_key(content: str, stage_name: str, stage_config: dict) -> str:
    """Deterministic key: same content + same stage always hits the same cache entry."""
    payload = {
        "content": content,
        "stage": stage_name,
        "config": stage_config,  # sorted implicitly by orjson
    }
    return sha256(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)).hexdigest()
```

Two-tier cache: in-memory LRU (hot path, within a session) + disk (persistent across sessions, for slow LLM-assisted stages in V2).

**Adopt unchanged.** In V1, the cache is useful primarily for the benchmark runner (where the same test corpus is run repeatedly during filter development). In V2, if we add LLM-assisted stages, the cache prevents redundant API calls.

---

### Pattern: Dual Persistence (Database + Streaming File)

**Source:** Promptfoo (SQLite via Drizzle + JSONL streaming)

**The pattern:** Write compression results to both a queryable store and an append-only flat file simultaneously.

```python
class PipelineTracker:
    def record(self, result: PipelineResult, command: str, project: str) -> None:
        # Write to SQLite (queryable, used by `sieve gain` and `sieve explain`)
        self._db.execute("""
            INSERT INTO invocations
            (command, project, original_tokens, final_tokens, ratio,
             stages_applied, mode, was_passthrough, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """, (...))

        # Write to JSONL (streaming, used by CI, external tools, `sieve export`)
        with open(self._jsonl_path, "a") as f:
            f.write(orjson.dumps(result.to_dict()).decode() + "\n")
```

The JSONL file continues writing even if the SQLite write fails. This means results are never lost due to database errors. CI systems can consume the JSONL directly without needing SQLite.

**Adopt unchanged.**

---

### Pattern: Resume Capability for Long Benchmark Runs

**Source:** Promptfoo (`--resume` flag)

**The pattern:** Benchmark runs should be resumable. The benchmark runner checks which `(filter_name, corpus_entry, run_id)` tuples already have results in the database and skips them.

```python
class BenchmarkRunner:
    def run(self, config: BenchmarkConfig, resume: bool = False) -> BenchmarkResult:
        for entry in config.corpus:
            key = (config.filter_name, entry.hash, config.run_id)
            if resume and self._db.has_result(key):
                continue  # already done in a previous run
            result = self._run_single(entry, config.filter)
            self._db.save_result(key, result)
```

A crash at corpus entry 150 of 300 can be resumed from entry 151 with `sieve benchmark run --resume`.

**Adopt from V1.** The cost is a unique key constraint on the results table. The payoff is not wasting time on re-runs.

---

## 1.7 Error Handling and Safety

---

### Pattern: Typed Exception Hierarchy with `is_transient()` Utility

**Source:** DSPy (7-class exception hierarchy + `is_retryable_lm_error()`)

**The pattern:**

```python
class SieveError(Exception):
    """Base class for all sieve errors. Always safe to catch."""
    pass

class FilterError(SieveError):
    """A compression stage failed."""
    def __init__(self, message: str, stage_name: str, content_preview: str = ""):
        super().__init__(message)
        self.stage_name = stage_name
        self.content_preview = content_preview

class ConfigError(SieveError):
    """Configuration is invalid."""
    pass

class HookError(SieveError):
    """Hook protocol error — Claude Code sent unexpected format."""
    pass

class CacheError(SieveError):
    """Database or cache operation failed."""
    pass

class PluginError(SieveError):
    """Plugin failed to load or violates the protocol."""
    def __init__(self, message: str, plugin_name: str):
        super().__init__(message)
        self.plugin_name = plugin_name

def is_transient_error(exc: Exception) -> bool:
    """Returns True for errors that may resolve on retry."""
    return isinstance(exc, (CacheError,))
```

Callers use `is_transient_error()` for retry decisions. They do not use `isinstance(exc, (CacheError, HookError))` chains.

**Adopt unchanged.** Define this hierarchy before writing any production code.

---

### Pattern: Never Use `assert` for Validation

**Source:** LLMLingua (identified as a flaw — `assert` is stripped by `python -O`)

**The pattern:** Use explicit `if/raise ValueError` for all validation. Never use `assert` for logic the program depends on:

```python
# WRONG — stripped by python -O:
assert rate <= 1.0, "rate must not exceed 1.0"

# CORRECT — always runs:
if rate > 1.0:
    raise ConfigError(f"rate must be ≤ 1.0, got {rate}")
```

**Adopt unchanged.** This is a correctness requirement, not a style preference.

---

### Pattern: Thread-Local Context Manager for Configuration Overrides

**Source:** DSPy (`dspy.context()`)

**The pattern:** Provide a context manager that temporarily overrides configuration for the duration of a `with` block, without touching global state:

```python
from contextlib import contextmanager
from contextvars import ContextVar

_current_config: ContextVar[PipelineConfig] = ContextVar("config")

@contextmanager
def pipeline_context(**overrides):
    """Override pipeline config temporarily. Thread-safe via contextvars."""
    current = _current_config.get(default_config)
    new_config = current.model_copy(update=overrides)
    token = _current_config.set(new_config)
    try:
        yield new_config
    finally:
        _current_config.reset(token)
```

Usage:
```python
# In tests:
with pipeline_context(mode=PipelineMode.SIMULATE, db_path=":memory:"):
    result = pipeline.compress("git status output...")

# In benchmark runner:
with pipeline_context(mode=PipelineMode.SIMULATE):
    for entry in corpus:
        result = pipeline.compress(entry.content)
```

Use `contextvars.ContextVar` (not `threading.local()`) so it works correctly in async contexts and with `asyncio.gather()`.

**Adopt unchanged.**

---

## 1.8 Release Engineering

---

### Pattern: Optional Extras for Heavy Dependencies

**Source:** Headroom AI (`pip install "headroom-ai[all]"`, `[proxy]`, `[ml]`, `[mcp]`)

**The pattern:** Heavy or platform-specific dependencies are optional extras, not hard requirements:

```toml
[project.optional-dependencies]
ml = ["magika>=0.5"]            # Content type ML detection
semantic = ["sentence-transformers>=3.0"]  # Semantic deduplication (V2)
treesitter = ["tree-sitter>=0.24", "tree-sitter-python", "tree-sitter-javascript"]

# Convenience group:
all = ["sieve[ml,semantic,treesitter]"]
```

The core tool installs in under 5 seconds with no optional dependencies. The ML extra requires a model download. The semantic extra requires torch. These are opt-in.

**Adopt unchanged.** The core package must install clean with no compilation on Windows. Every optional extra must gracefully degrade to a pure-Python fallback when not installed.

---

### Pattern: `__version__` as the Single Version Source

**The pattern:** Version is defined in one place — `pyproject.toml` `[project] version`. The package reads it at import time:

```python
# sieve/__init__.py
from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("sieve")
except PackageNotFoundError:
    __version__ = "0.0.0-dev"
```

Never hardcode the version string in Python source. Never maintain a `VERSION` file separately from `pyproject.toml`.

**Adopt unchanged.**

---

# 2. Prompt Engineering Techniques

Categorized by approach. Every technique labelled by evidence quality.

---

## 2.1 Lexical (Character and Token Level)

| Technique | Description | Evidence | V1? |
|---|---|---|---|
| ANSI escape stripping | Regex removal of terminal color codes | Proven | Yes — core |
| Whitespace normalization | Collapse multiple blank lines | Proven | Yes — core |
| Exact duplicate line removal | Set-based deduplication | Proven (log processing) | Yes |
| Adjacent duplicate removal | `uniq`-style consecutive dedup | Proven | Yes |
| Repetition counting (N×) | "warning: deprecated (×200)" | Proven (test runners) | Yes |
| Hash abbreviation | SHA-256 → first 8 chars | Proven (git) | Yes, opt-in |
| Timestamp stripping | ISO 8601, epoch, syslog formats | Industry practice | Yes, opt-in |
| Number precision reduction | 8 decimal → 2 significant figures | Industry practice | V2, narrow use case |

**Trade-offs:** All lexical techniques are deterministic, fast (<1ms), and low-risk. The only risk is timestamp stripping in contexts where timing is the signal (e.g., "the error happened at 14:23:41").

**V1 recommendation:** Include all except number precision. Number precision has too narrow a use case and too high a false-positive risk.

---

## 2.2 Structural (Document and Section Level)

| Technique | Description | Evidence | V1? |
|---|---|---|---|
| Head/tail selection | Keep first N and/or last M lines | Proven | Yes — core |
| Absolute line count cap | Hard upper bound | Proven | Yes — core |
| Empty fallback message | on_empty behavior | Proven | Yes — core |
| Pattern-based line stripping | `strip_lines_matching` equivalent | Proven | Yes — core |
| Pattern-based line keeping | `keep_lines_matching` equivalent | Proven (dangerous) | Yes, with warning |
| Pattern short-circuit | Collapse to summary if pattern matches | Proven (RTK) | Yes |
| Section extraction | Keep headers, discard bodies | Industry practice | V2 |
| Diff context reduction | git diff --unified=1 equivalent | Proven (git) | Yes |

**Trade-offs:** `keep_lines_matching` is the most dangerous structural technique — it inverts the safe default (keep everything, remove known-bad) to (keep only known-good, remove everything else). One missing pattern in the keep-list destroys important content permanently. Require minimum 3 tests per filter that uses it.

---

## 2.3 Syntax-Aware (Format and Language Specific)

| Technique | Description | Evidence | V1? |
|---|---|---|---|
| Code comment stripping | Language-specific regex | Proven (compilers, linters) | Yes — minimal mode |
| Function signature extraction | Regex-based (fragile) | Industry practice | V1 (regex), V2 (tree-sitter) |
| JSON null/empty removal | Parse → filter → re-serialize | Proven (API middleware) | V2 |
| YAML structural compression | Same as JSON | Proven | V2 |
| Stack trace frame dedup | Keep user frames, strip site-packages | Proven (Sentry) | Yes |
| Diff format awareness | Parse unified diff, reduce context | Proven (git format) | Yes |
| Log level filtering | Keep ERROR/WARN only | Proven (all log systems) | Yes |
| Tree-sitter structural extraction | AST-based, accurate | Proven (Aider, VS Code) | V2 |

**Trade-offs:** Regex-based code analysis is fragile but fast. Tree-sitter is accurate but adds a compilation dependency and per-language grammar. For V1, the regex approach is acceptable for the common cases (Python docstring removal, single-line comment removal). Tree-sitter is the V2 path when accuracy matters more than install simplicity.

---

## 2.4 Semantic (Meaning-Level) — Deterministic Approaches

| Technique | Description | Evidence | V1? |
|---|---|---|---|
| Protected spans | Lines/tokens marked immutable to all stages | Proven (LLMLingua force_tokens) | Yes — core |
| Type-aware rate allocation | Different compression ratios by content sub-type | Proven (LLMLingua JSON utils) | V2 |
| Segment labeling (role descriptions) | Mark sections by their function | Proven concept (TextGrad) | V2 |
| Proportional budget allocation | Divide token budget proportionally across sections | Proven (LLMLingua) | V2 |

The protected spans technique is the most important deterministic semantic technique. It allows filters to declare "these patterns, regardless of what other stages do, will never be removed." This is what prevents the `keep_lines_matching` footgun: a `preserve_patterns` list ensures that even if a stage marks a line as COMPRESS, lines matching protection patterns are never actually removed.

---

## 2.5 Semantic — ML-Assisted (Not for V1)

| Technique | Description | Evidence | V1? |
|---|---|---|---|
| Perplexity-guided token pruning | LLMLingua approach | Academic (EMNLP 2023) | Never in hot path |
| Sentence embedding deduplication | Cosine similarity on embeddings | Proven (RAG) | V2 optional |
| LLM-as-judge evaluation | LLM scores filter quality | Proven (Promptfoo) | V2 benchmark only |
| Relevance-based filtering | Context-aware token selection | Academic | Research track |

**Trade-offs for ML approaches:**
- Perplexity-guided: Adds 200ms–5s latency per call. Requires 1GB+ model. Does not work on Windows without GPU. Not suitable for a real-time hook.
- Sentence embeddings: Adds ~50ms per call with a small model. Optional dependency (`sentence-transformers`). Useful for deduplication within large outputs (e.g., 50 similar log lines). V2 candidate.
- LLM-as-judge: Only viable for offline benchmark evaluation. Never in the hook.

---

## 2.6 Runtime Optimization

| Technique | Description | Evidence | V1? |
|---|---|---|---|
| Fail-open with original passthrough | Return original on any stage failure | Proven (RTK) | Yes — mandatory |
| Three operating modes (AUDIT/OPTIMIZE/SIMULATE) | Headroom AI pattern | Proven (Headroom) | Yes |
| Tee + hint mechanism | Cache original, append hint to compressed | Proven (RTK) | Yes |
| Async database writes | Non-blocking SQLite writes via threading | Industry practice | V1 |
| Lazy stage compilation | Compile regex patterns on first use | Industry practice | V1 |

---

## 2.7 Compile-Time Optimization

| Technique | Description | Evidence | V1? |
|---|---|---|---|
| importlib.resources for built-in filters | Embed TOML at install time | Industry practice | Yes |
| Plugin cache invalidation | Cache entry-points scan, invalidate on install | Industry practice | V1 |
| SHA256 content-addressed cache | Skip recomputation for identical content | Proven (DSPy) | V1 |
| Schema compilation | Pydantic model compiled on import | Proven (Pydantic v2) | Yes |

---

## 2.8 Evaluation and Benchmarking

| Technique | Description | Evidence | V1? |
|---|---|---|---|
| Inline filter tests (must_contain, must_not_contain) | Promptfoo-style assertions | Proven | Yes — mandatory |
| Compression ratio targets | Minimum compression per filter | Industry practice | Yes |
| Dual persistence for benchmark results | SQLite + JSONL | Proven (Promptfoo) | Yes |
| Resume capability | Continue interrupted benchmark | Proven (Promptfoo) | Yes |
| LLM-as-judge for quality | Offline only | Proven (Promptfoo) | V2, optional |
| `not-` assertion prefix for inversion | Promptfoo pattern | Proven | Yes |
| Cartesian product evaluation | filters × corpus × metrics | Proven (Promptfoo) | V2 |

---

# 3. White Space Opportunities

Areas that none of the existing projects solve well. Technically realistic only.

---

## Rank 1: Windows-Native, pip-Installable, Zero-ML Hook Middleware

**User value: Critical.** Hundreds of thousands of corporate Windows developers. RTK doesn't serve them. Headroom AI's Windows support is unverified. snip requires Go compilation.

**Difficulty: Low-Medium.** Pure Python + pip. The main engineering challenge is verifying the Claude Code hook invocation on Windows (PowerShell vs cmd.exe vs WSL).

**Long-term impact: High.** If this project establishes the Windows-native position before RTK adds Windows support, it earns a permanent user base in corporate environments where tooling choices are sticky.

**What "winning" looks like:** `pip install sieve` → `sieve init --claude` → verified working hook in 5 minutes on a fresh Windows 11 machine with no admin rights and no Git Bash. No other tool achieves this today.

---

## Rank 2: Enterprise Python Plugin System for Internal CLI Tools

**User value: High for enterprise.** Internal Kubernetes operators, proprietary monitoring CLIs, custom build tools — these produce noisy output that no general-purpose tool will ever filter. The only way to filter them is a custom plugin.

**Difficulty: Low.** Entry-points plugin system is 2 days of implementation.

**Long-term impact: Very High.** This is the moat. RTK cannot add a Python plugin system without abandoning Rust. snip cannot add it without abandoning Go. Only a Python-native tool can offer `pip install company-sieve-filters` as the answer to custom tool coverage.

**What "winning" looks like:** A publicly documented `sieve.compression_stage` entry-point group. Community plugins on PyPI for Kubernetes, Terraform, AWS CLI, Datadog, PagerDuty, internal tooling. The registry becomes the product.

---

## Rank 3: Transparency-First UX (Explain, Preview, Audit Mode)

**User value: High for trust-building.** No existing tool shows users what was removed and why, at the stage level. RTK has no explain mode. Headroom AI is partially opaque (ML engines). The tool that earns corporate trust first earns corporate deployment.

**Difficulty: Low.** The mask abstraction makes this essentially free — `sieve explain` is a rendering of the accumulated mask, already computed as a side effect of compression.

**Long-term impact: High.** Compliance and audit requirements in enterprise are growing. "What did your AI middleware do to our data?" needs a documented answer. No existing tool provides one.

**What "winning" looks like:**
```
$ sieve explain "pytest tests/ -x"
Stage 1: remove_ansi        → 0 lines affected (no ANSI codes present)
Stage 2: strip_lines        → 142 lines removed [PASSED, progress dots]
  Preserved by protect rule: 0 lines
Stage 3: max_tokens         → not triggered (38 lines, limit 100)
─────────────────────────────────────────
Result: 178 lines → 38 lines │ 1,240 tokens → 261 tokens (79% reduction)
```

---

## Rank 4: Repetition Counting with N× Display

**User value: High.** npm audit, mypy, pylint, cargo clippy all produce hundreds of identical diagnostic lines. No existing tool in this category shows "warning: deprecated API (×200)."

**Difficulty: Low.** Group-by with counter. One new pipeline stage: `deduplicate_by_pattern`.

**Long-term impact: Medium.** A genuinely novel compression technique that improves on RTK's approach and earns technical credibility.

---

## Rank 5: Stack Trace Frame Deduplication for Python Developers

**User value: High for the Python-developer target market.** Django/Flask/pytest stack traces are 90% framework frames (`site-packages/django/`, `site-packages/pytest/`). Removing them requires pattern matching against known framework paths — safe, mechanical, high-value.

**Difficulty: Low.** Pattern match against `site-packages` in the frame path. Extract final exception line always.

**Long-term impact: Medium.** A V1 differentiator specifically for the Python developer segment.

---

## Rank 6: Honest Uncertainty in Token Metrics

**User value: Medium.** Trust. "Estimated: 1,240 tokens saved (±20% — char/4 approximation)" is more trustworthy than a precise-looking number. Enterprise procurement values defensible claims over impressive-but-vague ones.

**Difficulty: Trivial.** Label the number differently in the `gain` output.

**Long-term impact: Medium.** Differentiates from RTK's and Headroom AI's metrics on honesty grounds. Builds long-term credibility.

---

## Rank 7: AUDIT Mode as the Default New-User Experience

**User value: High for adoption.** AUDIT mode (compress locally, return original, log what would have been compressed) lets new users evaluate the tool with zero risk. It is the right default for corporate environments where "AI tool changes what the AI sees" requires approval.

**Difficulty: Low.** Three modes already in the design. Making AUDIT the post-install default is a configuration choice.

**Long-term impact: High.** Reduces the adoption friction in corporate settings from "need security approval" to "just enable monitoring mode and see the numbers."

---

# 4. The Ideal Version 1

Starting over with everything learned. Maximum simplicity. Every feature and dependency must justify its existence.

---

## V1 Scope: What Is In

**One integration only: Claude Code PreToolUse hook.**
No Cursor. No Copilot. No Gemini. These add complexity before the core is proven. Claude Code is the user's primary tool. Build one thing correctly.

**Five built-in filter categories:**
1. `git` — status, diff, log, blame (the most frequently used AI commands)
2. `pytest` — test output filtering (failure extraction, repetition counting)
3. `build` — compiler errors from common tools (tsc, mypy, ruff, cargo check)
4. `cat/read` — file contents (comment stripping in minimal mode)
5. `generic` — the fallback for any command (ANSI stripping, max_lines cap)

These five cover 80% of all AI tool calls in a typical coding session.

**Six commands:**
```
sieve init --claude       # Install Claude Code hook (Windows PowerShell + Unix)
sieve validate [file]     # Validate config file, <1 second, no execution
sieve explain <cmd>       # Show stage-by-stage trace for a command
sieve gain                # Token savings summary
sieve verify              # Run all inline filter tests
sieve doctor              # Health check (hook installed? Responding? Tests passing?)
```

No `benchmark`, no `report`, no `watch`, no `share`, no `export`. Those come in V1.1 after users have demonstrated what they actually need.

**Three operating modes:** AUDIT (default after install), OPTIMIZE (switch manually), SIMULATE (for filter development). See Section 1.3.

**SQLite tracking with dual persistence:** Every compression result written to SQLite and to a JSONL file.

**Mask-based pipeline architecture:** Stages produce masks; render is the final step. See Section 1.3.

**Content-type detection (heuristics only):** JSON, log, code, diff, text detection. No ML dependency.

**Protected spans:** `preserve_patterns` TOML config option. Lines matching these patterns are never compressed regardless of other stages.

**Tee mechanism:** On truncation, cache original to `~/.local/share/sieve/tee/`. Append `[full output: ~/path]` to compressed output.

**Entry-points plugin system:** `sieve.compression_stage` group. Zero-config discovery. Fail-open.

**Pydantic config with JSON Schema:** Generate and publish schema. yaml-language-server directive in init-generated file.

**Autouse test isolation:** Every test in a fresh temp dir with isolated SQLite. No tests touch `~/.config/sieve/`.

**Typed exception hierarchy:** `SieveError → FilterError | ConfigError | HookError | CacheError | PluginError`. `is_transient_error()` utility.

---

## V1 Dependencies: Justified or Excluded

| Dependency | Justified? | Reason |
|---|---|---|
| `pydantic >= 2.0` | Yes | Config validation + JSON Schema generation. No alternative. |
| `typer` | Yes | Click-based CLI with good Windows support. Thin dependency. |
| `regex` | Yes | Drop-in `re` replacement, prevents catastrophic backtracking. Essential for hook safety. |
| `platformdirs` | Yes | Cross-platform path resolution (Windows `%APPDATA%`, Linux `~/.config`). One purpose, minimal. |
| `orjson` | Yes | Fast, deterministic JSON serialization for cache keys. 5MB binary wheel. |
| `tomllib` | Built-in | Python 3.11+ stdlib. Zero extra dependency. |
| `sqlite3` | Built-in | SQLite tracking. No ORM needed at this scale. |
| `importlib.resources` | Built-in | Bundle built-in TOML filters. |
| `rich` | Yes | Terminal output formatting, progress bars, tables. No other viable option for Windows terminal color. |
| `watchfiles` | No | Watch mode is V1.1 |
| `sentence-transformers` | No | Semantic dedup is V2 optional |
| `magika` | No | ML content detection is V2 optional |
| `tree-sitter` | No | AST extraction is V2 |
| `httpx` / `requests` | No | No outbound HTTP calls in V1 |
| `diskcache` | No | In-memory LRU sufficient for V1; disk persistence via SQLite already |
| `anthropic` SDK | No | No LLM calls in V1 |

Total V1 dependencies: 6 (pydantic, typer, regex, platformdirs, orjson, rich). All distribute as platform wheels. None require compilation on Windows.

---

## V1 TOML Filter Format

Finalized based on all research. This is Option B (stages-array redesign), confirmed as correct by every project studied.

```toml
schema_version = 1
# yaml-language-server: $schema=https://sieve.dev/filter-schema.json

[[filter]]
name = "pytest"
match_command = '^pytest\b|^python -m pytest\b'
description = "Extract test failures from pytest output"

  [[filter.stages]]
  type = "remove_ansi"
  # No config needed — can_handle() detects ANSI presence automatically

  [[filter.stages]]
  type = "deduplicate_consecutive"
  # Remove consecutive identical lines before pattern matching

  [[filter.stages]]
  type = "strip_lines"
  patterns = ['^PASSED\b', '^\\.+', '^\\s*$', '^collecting ']
  preserve_patterns = ['^FAILED', 'AssertionError', 'Error', 'Exception']
  # preserve_patterns create PROTECT decisions that no subsequent stage can override

  [[filter.stages]]
  type = "group_repeated"
  patterns = ['^(warning|note|hint):']
  # Lines matching these patterns are grouped: "warning: deprecated API (×47)"

  [[filter.stages]]
  type = "max_tokens"
  limit = 500
  strategy = "tail"
  # On overflow, keep the last N tokens (failures are at the end)

  on_empty = "All tests passed."

[[filter.tests]]
description = "Failures are preserved, passes are stripped"
input = "PASSED test_login\nFAILED test_logout\n    AssertionError: got False"
must_contain = ["FAILED", "AssertionError"]
must_not_contain = ["PASSED"]
compression_target = 0.5

[[filter.tests]]
description = "All-pass output triggers on_empty"
input = "PASSED test_login\nPASSED test_signup\n1 passed in 0.3s"
expected_output = "All tests passed."

[[filter.tests]]
description = "Repeated warnings are grouped"
input = "warning: deprecated\nwarning: deprecated\nwarning: deprecated\nFAILED test_x"
must_contain = ["×3", "FAILED"]
must_not_contain = ["warning: deprecated\nwarning: deprecated"]
```

Key design decisions in this format:
- `stages` is an explicit array, not implicit stage-type ordering
- `preserve_patterns` is a per-stage first-class config option
- `group_repeated` is a new stage not in RTK
- `strategy` for max_tokens is explicit (head vs tail vs smart)
- Tests include `compression_target` as a quantitative assertion
- `on_empty` is a filter-level fallback, not a stage

---

## V1 SQLite Schema

Finalized based on all research. Do not change this after the first public release without a migration.

```sql
CREATE TABLE invocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    command TEXT NOT NULL,
    project_path TEXT NOT NULL,    -- normalized with Path.as_posix() for GLOB
    original_tokens INTEGER NOT NULL DEFAULT 0,
    final_tokens INTEGER NOT NULL DEFAULT 0,
    ratio REAL NOT NULL DEFAULT 1.0,
    stages_applied TEXT NOT NULL DEFAULT '[]',  -- JSON array of stage names
    content_type TEXT NOT NULL DEFAULT 'unknown',
    mode TEXT NOT NULL DEFAULT 'optimize',      -- audit | optimize | simulate
    filter_name TEXT,                           -- which filter matched, NULL if passthrough
    was_passthrough INTEGER NOT NULL DEFAULT 0, -- explicit boolean, not zero-token sentinel
    duration_ms REAL NOT NULL DEFAULT 0,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX idx_invocations_project ON invocations(project_path, recorded_at);
CREATE INDEX idx_invocations_filter ON invocations(filter_name, recorded_at);

CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT INTO schema_migrations (version) VALUES (1);
```

Include `stages_applied` as a JSON array from day one — it enables `sieve explain` to show historical traces.
Include `schema_migrations` table from day one — every future schema change gets a row.

---

## V1 Two-Week Build Plan

**Week 1: Core Infrastructure**
- Day 1: Package structure, pyproject.toml, test infrastructure (autouse fixture, flag-gated categories)
- Day 2: Pydantic config models, JSON Schema generation, TOML filter parser, `sieve validate`
- Day 3: ContentMask, ContentType detection, StageHandler Protocol, `can_handle()` dispatch
- Day 4: 6 built-in stage implementations (remove_ansi, strip_lines, group_repeated, deduplicate_consecutive, max_tokens, on_empty)
- Day 5: SQLite schema, dual-persistence tracker, PipelineResult

**Week 2: Integration and Polish**
- Day 6: Claude Code hook (read JSON → rewrite command → return JSON), fail-open error handling
- Day 7: Windows PowerShell hook script, `sieve init --claude` command
- Day 8: 5 built-in filter TOML files (git, pytest, build, cat, generic) with inline tests
- Day 9: `sieve gain`, `sieve verify`, `sieve doctor`
- Day 10: `sieve explain` (mask rendering), README, installation guide for Windows

**Week 3 (stretch): Trust and Quality**
- Tee mechanism
- Three operating modes (AUDIT/OPTIMIZE/SIMULATE)
- Protected spans enforcement
- Benchmark runner (basic)

---

# 5. The Ideal Version 5

Assume the project succeeds. Describe what it has become.

---

## What Version 5 Looks Like

**The defining capability: The Sieve Registry.**

Version 5's defining feature is not its compression algorithm or its pipeline engine — those are table stakes by Version 2. The defining capability is its registry: a curated, versioned, community-maintained library of compression filters for every significant developer tool, accessible via `pip install sieve-[tool]`.

The ecosystem has matured:
- `sieve-kubernetes` — covers kubectl, helm, kustomize, k9s
- `sieve-terraform` — covers terraform plan, apply, state, output
- `sieve-aws` — covers the AWS CLI across all major services
- `sieve-docker` — covers docker, docker-compose, containerd
- `sieve-datadog` — covers Datadog CLI, log streams
- `sieve-company` (internal package on private PyPI) — covers proprietary tooling
- 200+ community packages covering every major cloud CLI, monitoring tool, and build system

The registry has a quality bar: every package in the official `sieve-*` namespace requires minimum 95% test coverage on inline tests, a maintainer SLA for updates when tool output formats change, and a verified Windows CI run.

---

## What Else Has Evolved

**Session-level deduplication (V3):** The hook reads the Claude Code context JSON and tracks what the AI has already seen in this session. Files that were already read are not re-sent in full — only the diff from the last known state. This is the biggest single compression gain available beyond per-command filtering.

**The tee mechanism becomes reversible retrieval (V3):** The `[full output: ~/path]` hint from V1 evolves into a `headroom_retrieve`-style tool injected into the AI's tool list. The AI can explicitly request the uncached original. The retrieval is mediated by Sieve, so it can be filtered and compressed on retrieval too.

**Adaptive compression profiles (V4):** Sieve learns from re-run rates. When the AI immediately runs the same command twice (pattern: `git status` → response → `git status` again), it suggests the filter was too aggressive. Adaptive mode automatically relaxes the compression ratio for that filter.

**Multi-agent expansion (V3):** Cursor, Copilot CLI, Gemini, Continue. Each agent gets a dedicated adapter in the `sieve.hook_adapter` entry-point group.

**The benchmark as a community service (V3):** The benchmark runner publishes anonymized compression statistics to an aggregate dataset. Community members can query: "For the `pytest` filter, what is the average compression ratio across all users?" This provides evidence-based filter optimization and validates the core hypothesis (that filtering improves AI session quality) through aggregate data.

**Version 5's positioning:** Not "a tool you install." A standard. Sieve filters are discussed in AI coding assistant communities the same way `.gitignore` patterns are discussed in Git communities — as community knowledge about what to exclude. The registry is the product; the runtime is the infrastructure.

---

# 6. Final Technical Recommendations

Challenging every current design decision.

---

## What Must Change Before Implementation Begins

### Change 1: Add the Mask Abstraction (Architecture Change)

**Current design:** Pipeline stages transform strings. Each stage receives a string and returns a string.

**Problem:** String-to-string transformation makes stage composition unsafe (ordering matters for correctness, not just preference), makes audit trails expensive (must store string at each stage), and makes the PROTECT decision impossible to enforce across stages.

**Change:** Stages receive and return `ContentMask`. The `PROTECT` decision propagates immutably through all subsequent stages. The final step renders the mask to a string. This is Headroom AI's core architectural insight.

**Cost of changing now:** Low — no production code exists yet.
**Cost of changing later:** High — every stage implementation would need to be rewritten.

**Verdict: Implement the mask abstraction from day one.**

---

### Change 2: Add Three Operating Modes (Feature Addition)

**Current design:** The pipeline always compresses and returns compressed output.

**Problem:** AUDIT mode is essential for corporate adoption. Users in locked-down environments cannot deploy a tool that changes what the AI sees without first observing what it would change. The current design has no way to evaluate filters without affecting AI behavior.

**Change:** Add `PipelineMode.AUDIT | OPTIMIZE | SIMULATE` to the config and hook response. Make AUDIT the default post-install mode. Users switch to OPTIMIZE manually after reviewing audit logs.

**Cost of changing now:** Low — it is a parameter to the hook response formatter.
**Cost of changing later:** Medium — requires adding a mode flag to every call site.

**Verdict: Add three modes before writing the hook entry point.**

---

### Change 3: Use Pydantic Instead of Dataclasses (Technology Choice)

**Current design:** Prior analysis documents reference both dataclasses and Pydantic. The choice was not finalized.

**Change:** Use Pydantic v2 exclusively for all configuration models. Reasons:
1. JSON Schema generation (`model_json_schema()`) for the yaml-language-server directive
2. `model_validate()` for config file loading with clear error messages
3. `model_copy(update=...)` for the thread-local context manager pattern
4. Pydantic v2 is now a standard dependency in the Python ecosystem; the overhead concern is no longer relevant

**Cost:** None. Pydantic v2 is already in common use. The alternative (dataclasses + manual schema maintenance) is strictly worse.

**Verdict: Pydantic v2 throughout. No dataclasses for config models.**

---

### Change 4: Add Protected Spans as a First-Class Stage Config Option

**Current design:** Not explicitly in the stage config format.

**Change:** Every stage that removes lines should accept a `preserve_patterns` list. Lines matching these patterns receive `Decision.PROTECT` and are never touched regardless of what other patterns say. This is the mechanism that prevents `strip_lines` from accidentally removing lines that contain error messages matching a COMPRESS pattern.

**How it works in the mask:**
1. `preserve_patterns` are applied first within the stage, setting `Decision.PROTECT`
2. `drop_patterns` are then applied, setting `Decision.COMPRESS` — but only for lines not already `PROTECT`
3. Later stages skip lines with `Decision.PROTECT`

**Cost of changing now:** Trivial — it is a config field and a check in `apply()`.
**Cost of changing later:** Medium — requires adding to every stage's config model.

**Verdict: Implement protected spans before writing any stage handler.**

---

### Change 5: Simplify the V1 CLI to Six Commands

**Current design (from prior documents):** 10+ commands including `trust`, `history`, `config show/edit/set`, `audit`, `watch`, `discover`, `blame`, `health`, `compatibility check`, `changelog`, `simulate`, `show`, `uninstall`.

**Problem:** Promptfoo has 30+ commands and reports that new users find it overwhelming. Starting with 10+ commands means 10+ things to document, 10+ things to test, and 10+ things to break. The startup cost of implementing 10 commands correctly is 5× higher than implementing 2 commands correctly.

**Change:** V1 CLI is exactly six commands: `init`, `validate`, `explain`, `gain`, `verify`, `doctor`. Everything else waits for demonstrated user demand.

**What to do with the removed commands:**
- `trust` — defer to V1.1 (trust model exists in code, no CLI until needed)
- `config show/edit/set` — replaced by editing the TOML file directly and running `sieve validate`
- `discover` — V1.1 (scan past session logs; valuable but not MVP)
- `watch` — V1.1
- `uninstall` — document as `sieve init --claude --remove` or manual JSON edit

**Verdict: Six commands at V1. Add more based on actual user requests, not anticipated need.**

---

## What Has Been Confirmed by Research

### Confirmed: Entry-Points Plugin Discovery

The `sieve.compression_stage` entry-point group with fail-open loading is the right mechanism. Confirmed by both DSPy and Headroom AI. Cache the discovery result.

### Confirmed: TOML Stages-Array Format (Option B)

The stages-array format is confirmed as correct. Every project studied that has a declarative pipeline format uses an explicit stage list, not an implicit ordering. The Zap/RTK format (implicit stage ordering in TOML) is the antipattern.

### Confirmed: `regex` Package Instead of `re`

Required for catastrophic backtracking prevention. No alternative. In production every day.

### Confirmed: WAL Mode + GLOB Normalization for SQLite

WAL mode for concurrent access. `Path.as_posix()` for Windows path normalization before GLOB queries. Both confirmed by prior analysis.

### Confirmed: Inline Filter Tests as Mandatory

Every built-in filter needs minimum 3 inline tests. CI blocks on failures. `sieve verify` is the command that runs them. Confirmed by both RTK and Promptfoo approaches.

### Confirmed: Tee Mechanism

Cache originals and append `[full output: ~/path]` to compressed output. This makes aggressive compression safe. Confirmed as RTK's most important safety innovation.

---

## What to Simplify

### Simplify: The Trust System

The SHA-256 trust system for project-local TOML filters was inherited from Zap/RTK. It is security theater against sophisticated attackers and operational friction for legitimate users. The threat model (malicious TOML filter in a project directory changes what the AI sees) is real but narrow.

**Simplified approach:** Project-local filters in `.sieve/` are trusted if they are tracked by git (`git ls-files --error-unmatch .sieve/filters.toml` exits 0). If not tracked by git, warn loudly and require `sieve trust .sieve/filters.toml` explicitly. No SHA-256 hash files to manage.

### Simplify: The `on_empty` Mechanism

Currently modeled as a stage in the pipeline. It should be a filter-level property, not a stage. Every filter should have one optional `on_empty` string. If the pipeline's mask results in zero KEEP decisions, the filter returns `on_empty` (or the original, if `on_empty` is not set).

### Simplify: The `match_output` Short-Circuit

The `match_output` + `unless` pattern from Zap/RTK is powerful but complex. For V1, replace it with a cleaner `abort_if` / `abort_unless` property on the filter:

```toml
[[filter]]
match_command = '^terraform apply'
abort_unless = ["Apply complete!", "No changes."]
# If the output doesn't contain any of these, the pipeline is skipped entirely
# (the raw output is passed through — presumably it's an error worth seeing in full)
```

This is cleaner than the two-field `match_output`/`unless` mechanism and achieves the same result.

---

## What to Postpone

### Postpone: `distill discover` / Retroactive Session Analysis

This is RTK's most powerful adoption feature. But it requires parsing Claude Code's JSONL session logs, which involves understanding the session log format (not documented, may change). The risk of breaking on Claude Code updates is high. Implement after the core hook is stable.

### Postpone: Multi-Agent Support (Cursor, Copilot, Gemini)

Each agent has a different hook format. Each requires a separate adapter. Each increases the surface area that breaks on agent updates. V1 is Claude Code only. V1.1 adds the adapter protocol. V2 adds the second agent.

### Postpone: `sieve benchmark` as a Full Framework

The benchmark runner (described in design-review.md) is valuable but complex. V1's benchmark story is: inline filter tests (`sieve verify`) plus manual timing (`sieve explain --timing`). A full benchmark framework with corpus management, cross-run comparison, and Cartesian product evaluation comes in V2.

### Postpone: Session-Level Deduplication

Requires reading the Claude Code context JSON for conversation history. The hook protocol may or may not expose this. Verify empirically before designing around it.

---

## Final Verdict: Is the Research Phase Complete?

**Yes. Implementation can begin with confidence.**

What we know after this study:
- The mask abstraction is the correct core primitive (confirmed by Headroom AI)
- The stages-array TOML format is correct (confirmed by all projects studied)
- The entry-points plugin system is correct (confirmed by DSPy and Headroom AI)
- Pydantic v2 is the correct config validation layer (confirmed by Promptfoo)
- Three operating modes (AUDIT/OPTIMIZE/SIMULATE) are necessary for enterprise adoption (confirmed by Headroom AI)
- Six CLI commands at V1 is the right scope (learned from Promptfoo's scale)
- The tee mechanism is the right safety net (confirmed by RTK)
- Protected spans are essential (confirmed by LLMLingua's force_tokens)
- SHA256 + sorted-key JSON for cache keys is correct (confirmed by DSPy)
- Autouse isolation fixture is mandatory in tests (confirmed by DSPy)
- Fail-open as an architectural invariant is non-negotiable (confirmed by RTK and Headroom AI)

What requires one final empirical test before writing the hook:
- Python startup latency on the target Windows machine with corporate security software
- Claude Code hook invocation mechanism on Windows (PowerShell vs cmd.exe vs WSL)
- `pip install "headroom-ai[all]"` on the target Windows machine (if it works, reconsider contributing instead of building)

**If those three tests confirm the Windows gap exists and Python startup is acceptable, implementation begins immediately. The architecture is sound.**
