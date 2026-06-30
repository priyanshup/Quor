# CONTRIBUTING
## Quor — Contributor Guide

> Welcome. This guide covers everything you need to contribute to Quor.
> For the full design rationale behind every decision, read DECISIONS.md.
> For AI-assisted development, read CLAUDE.md first.

---

## Getting Started

**Prerequisites:**
- Python 3.11 or higher
- Git
- Windows 10/11, Ubuntu 20.04+, or macOS 12+ (Quor is Windows-first but cross-platform)

**Setup:**
```bash
git clone https://github.com/[org]/quor
cd quor
pip install -e ".[dev]"
```

Dev extras (in `pyproject.toml`):
- `pytest`, `pytest-cov` — testing
- `mypy` — type checking
- `ruff` — linting + formatting
- `build`, `twine` — packaging (only needed for releases)

**Verify setup:**
```bash
quor doctor
pytest tests/unit/
mypy quor/
ruff check quor/
```

All four must pass before you start.

---

## Development Workflow

### Before writing code

1. Check existing issues to see if someone is already working on your idea.
2. For new filters or plugins: open an issue first describing the tool you want to cover, with sample output. Wait for maintainer input before writing.
3. For bug fixes: open an issue with reproduction steps. Confirm the fix approach before a large PR.
4. For anything that touches DECISIONS.md ADRs: those decisions are final. Do not reopen them in a PR. If you believe an ADR should be revisited, open a discussion issue.

### The coding loop

```bash
# Create a branch
git checkout -b feature/your-thing

# Make changes
# Run tests frequently
pytest tests/unit/ -x  # stop at first failure

# Type check
mypy quor/your_changed_module.py

# Lint
ruff check quor/

# Test all inline filter tests
quor verify

# Full suite before PR
pytest
```

### Before submitting a PR

```bash
pytest --cov=quor tests/unit/ --cov-report=term-missing
```

Coverage on changed modules must be ≥80%. The CI will fail if it drops below.

---

## Code Style

**Formatting:** Ruff handles formatting. Run `ruff format quor/` before committing. CI enforces this.

**Type annotations:** All public functions and methods must have type annotations. Private methods (`_name`) should have annotations where it aids clarity. `Any` is an escape hatch, not a style.

**Comments:** Default to no comments. A comment is only warranted when the WHY is non-obvious. Do not comment WHAT the code does — well-named identifiers do that.

**Naming:**
- Classes: `PascalCase`
- Functions and variables: `snake_case`
- Constants: `UPPER_SNAKE_CASE`
- Private members: `_leading_underscore`

**Imports (ruff-isort enforced):**
1. Stdlib
2. Third-party
3. Quor-internal

No wildcard imports. No circular imports (mypy will catch these).

**Strings:** f-strings only. No `.format()` or `%` formatting.

**JSON:** `orjson` for all JSON serialization. Never `json.dumps/loads`.

**Error handling:**
- Never bare `except:` — always catch specific types.
- Never `assert` for validation — `assert` is stripped by `python -O`. Use `if/raise`.
- All quor exceptions inherit from `QuorError` or a subclass.

**Paths:**
- Always `platformdirs` for config and data directories.
- Always `encoding="utf-8"` on `open()`.
- Always `Path.as_posix()` when storing paths as strings.
- Never `/tmp` — use `tempfile.mkdtemp()`.

---

## Testing

### Test structure

```
tests/
├── conftest.py          — autouse isolation fixture
├── unit/
│   ├── test_pipeline.py
│   ├── test_filters.py
│   ├── test_rewrite.py
│   ├── test_tracking.py
│   └── ...
├── integration/         — marked @pytest.mark.integration
│   └── test_hook_e2e.py
└── fixtures/
    ├── commands/        — 100+ command classifier fixtures
    └── outputs/         — sample command outputs for filter tests
```

### Test isolation

The `conftest.py` autouse fixture patches `platformdirs` to return fresh temp directories for every test. **No test reads from or writes to `~/.config/quor/` or `~/.local/share/quor/`.**

Integration tests (marked `@pytest.mark.integration`) are excluded from the default CI run. They require a real Claude Code installation.

### Writing unit tests

```python
def test_strip_lines_removes_ansi():
    # Arrange
    mask = ContentMask.from_str("normal line\n\x1b[32mpassed\x1b[0m\nanother line")
    stage = RemoveAnsiStage()
    config = StageConfig(type="remove_ansi")
    
    # Act
    result = stage.apply(mask, config)
    
    # Assert
    decisions = {lm.line: lm.decision for lm in result.lines}
    assert decisions["normal line"] == Decision.KEEP
    assert decisions["another line"] == Decision.KEEP
    # The ANSI line is pure escape codes when stripped — should be COMPRESS
    # (exact assertion depends on whether the line has non-ANSI content)
```

### Filter inline tests

Every filter must have ≥3 `[[filter.tests]]` entries. These run via `quor verify`. When writing a new filter:
1. Write the tests first.
2. Run `quor verify` — they should fail.
3. Write the filter until they pass.

---

## Pull Requests

### Title format

```
[component] brief imperative description
```

Components: `pipeline`, `filters`, `rewrite`, `tracking`, `cli`, `adapters`, `plugins`, `packaging`, `docs`

Examples:
- `[pipeline] add group_repeated stage`
- `[filters] add terraform built-in filter`
- `[cli] fix quor gain --days flag`

### PR body (required sections)

**What this does:** One paragraph describing the change.

**Why this is needed:** Which requirement from PROJECT_BIBLE.md or IMPLEMENTATION_PLAN.md does this implement? Or which bug does it fix (link to issue)?

**Edge cases considered:** List the edge cases you thought about and how they're handled.

**Tests added:** Which test files were added or modified? Does `quor verify` still pass?

### PR checklist

- [ ] `pytest tests/unit/` passes
- [ ] `mypy quor/` passes
- [ ] `ruff check quor/` passes
- [ ] `quor verify` passes (if filter files changed)
- [ ] Coverage on changed modules ≥80%
- [ ] Windows CI passes (check the Actions tab)
- [ ] No hardcoded paths, no bare `except:`, no `assert` for validation
- [ ] PR title follows `[component] description` format

### What reviewers check

1. **Safety:** Does the hook still fail-open? Is PROTECT still immutable?
2. **Windows:** Any hardcoded paths? Any `encoding=` missing from `open()`?
3. **Tests:** Do the tests actually test the thing? Are edge cases covered?
4. **Anti-goals:** Does this implement an anti-goal (see ANTI_GOALS.md)?
5. **Scope:** Is this more than what was asked for? Simpler is better.

---

## Writing New Filters

Filters are the most impactful contribution. Before writing one:

1. **Open an issue** describing the target command (e.g., `terraform plan`), a sample of its output, and what should be removed vs. preserved.
2. **Collect real output.** Run the command. Save the output to `tests/fixtures/outputs/terraform_plan_output.txt`. This becomes the basis for your tests.
3. **Write the tests first** (in `[[filter.tests]]` blocks).
4. **Write the filter stages** until the tests pass.
5. **Run `quor explain "terraform plan"`** and confirm the trace looks correct.

### Filter checklist

- [ ] At least 3 `[[filter.tests]]` entries
- [ ] `must_contain`: confirms critical lines survive
- [ ] `must_not_contain`: confirms noise is removed
- [ ] `compression_target`: a meaningful target (e.g., 0.5 = 50% reduction)
- [ ] `preserve_patterns`: error and exception patterns are protected
- [ ] `on_empty`: defined if the filter could produce empty output
- [ ] `abort_unless` or `abort_if` if the filter should short-circuit on certain conditions

### Filter placement

- **New command for an existing category:** Add to the existing built-in TOML file (e.g., a new `git cherry-pick` filter goes in `git.toml`).
- **New category:** Create a new built-in TOML file (e.g., `terraform.toml`). Open an issue to discuss before creating.
- **Tool-specific/company-specific:** Create a plugin package (`pip install quor-yourtool`). See Plugin Development below.

---

## Plugin Development

Plugins add compression stages that aren't appropriate for the built-in distribution.

### Creating a plugin

```bash
mkdir quor-yourtool
cd quor-yourtool
```

`pyproject.toml`:
```toml
[project]
name = "quor-yourtool"
version = "0.1.0"
dependencies = ["quor >= 1.0"]

[project.entry-points."quor.compression_stage"]
yourstage = "quor_yourtool.stages:YourStage"
```

`quor_yourtool/stages.py`:
```python
from quor.pipeline.stages.base import StageHandler, StageConfig
from quor.pipeline.mask import ContentMask, Decision

class YourStage:
    api_version: int = 1
    stage_type: str = "your_stage_type"
    
    def can_handle(self, content: str, content_type: str) -> bool:
        return True  # or content_type in {"log", "text"}
    
    def apply(self, mask: ContentMask, config: StageConfig) -> ContentMask:
        new_lines = []
        for lm in mask.lines:
            if lm.decision.is_protected():
                new_lines.append(lm)  # never override PROTECT
                continue
            # ... your logic here
            new_lines.append(lm)
        return ContentMask(lines=new_lines)
```

### Plugin rules

1. **Never override PROTECT.** Check `lm.decision.is_protected()` before changing any decision.
2. **Never call network APIs.** Plugins run in the hook path.
3. **Never mutate input.** Create new `LineMask` objects; don't modify existing ones.
4. **Implement `can_handle`.** Return `False` rather than raising if the content is unsuitable.
5. **Declare `api_version = 1`.** This is the current stable API version.

### Publishing to PyPI

Follow the standard PyPI publishing process. Use the `quor-*` name prefix for discoverability (e.g., `quor-docker`, `quor-terraform`).

Official `quor-*` namespace guidelines:
- Must include inline filter tests
- Must include Windows CI
- Must declare `api_version = 1`
- Must implement `can_handle()` correctly

---

## Issue Reporting

### Bug reports must include:
1. OS and Python version (`python --version`, `quor doctor`)
2. Quor version (`quor --version`)
3. The command that caused the issue
4. What happened vs. what you expected
5. The output of `quor explain "<your command>"` (if applicable)

### Feature requests must include:
1. The use case (what are you trying to do?)
2. Why the existing behavior doesn't satisfy it
3. Whether any ANTI_GOALS.md items are relevant

Do not open a feature request for anything listed in ANTI_GOALS.md. It will be closed immediately.

---

## Release Process

Releases are managed by maintainers. Contributors do not cut releases.

**Version scheme:** Semantic versioning. `MAJOR.MINOR.PATCH`.
- `MAJOR`: breaking change to plugin API or TOML filter format
- `MINOR`: new feature (backwards-compatible)
- `PATCH`: bug fix

**Release checklist:**
1. All RELEASE_CRITERIA.md gates for the target version pass
2. CHANGELOG.md updated with the new version
3. Version bumped in `pyproject.toml` and `quor/__init__.py`
4. CI green on main branch
5. `python -m build` produces clean wheel and sdist
6. `twine check dist/*` passes
7. TestPyPI upload tested on fresh Windows VM
8. Tag pushed: `git tag v1.0.0 && git push --tags`
9. PyPI upload: `twine upload dist/*`

---

## Community Standards

Quor follows the Contributor Covenant Code of Conduct. Be direct, be constructive, don't be a jerk. Technical disagreements are settled by evidence (benchmark results, test cases) and DECISIONS.md precedent — not by persistence or volume.

Questions that don't belong in issues belong in Discussions (once enabled). The issue tracker is for bugs and concrete feature requests only.
