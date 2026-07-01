# RESEARCH COMPLETION
## Quor — Close of Research Phase

> This document formally closes the research phase and defines the exact first implementation task.
> After reading this document, there should be no ambiguity about what to do next.

---

## Is the Research Phase Complete?

**Yes. Unconditionally.**

The research phase produced five documents across four categories:
1. **Architecture analysis** (zap-analysis.md): 20-deliverable analysis of the RTK/Zap architecture, proposed Python equivalent.
2. **Critical design review** (design-review.md): 5-role panel review, blocking issues, technical debt, benchmark strategy.
3. **Product discovery** (final-discovery.md): 15 deliverables, user journey, optimization landscape, algorithm catalogue, opportunity matrix, feature selection by version.
4. **Competitive research** (competitive-research.md): ecosystem map, 6 deep-dive competitor analyses, build-vs-contribute verdict, name candidates.
5. **Engineering patterns** (engineering-patterns.md): synthesis of DSPy, LLMLingua, TextGrad, Promptfoo, SAMMO, EvoPrompt, Headroom AI. 24 engineering patterns. V1 build plan. Final dependency list. Final SQLite schema. Final TOML format.

All five documents have been archived to `docs/archive/`. All decisions have been extracted and codified in `docs/final/DECISIONS.md` (25 ADRs at research-phase close; 28 as of the current implementation phase — see `docs/final/PROJECT_STATUS.md` for the current count). The canonical documents in `docs/final/` supersede all archived research.

**No further research is needed.** The architecture is fully specified. The implementation is fully planned. Every open question was answered during research or is answerable via the three empirical pre-flight checks.

---

## Is Any Additional Research Essential Before Implementation?

**No research. Three empirical observations.**

These are not research — they are 30-minute measurement tasks on the real target environment:

**Observation 1: Python startup time**  
Method: `time python -c "import quor"` on the actual Windows machine with corporate AV active.  
Threshold: If <300ms, proceed. If >300ms consistently, design the persistent daemon before Phase 2.  
Time required: 15 minutes.

**Observation 2: Claude Code hook mechanism on Windows**  
Method: Write a minimal Python hook script, configure it in `~/.claude/settings.json`, run a Claude Code session, observe whether the hook fires.  
Confirms: stdin/stdout JSON format, timeout budget, PowerShell execution policy behavior.  
Time required: 30 minutes.

**Observation 3: PyPI name availability**  
Method: `pip index versions quor`. If 404, register immediately at pypi.org.  
Time required: 5 minutes.

These observations affect implementation decisions, not the architecture. They are the first tasks of the pre-flight phase (before Phase 0 in IMPLEMENTATION_PLAN.md).

There is one additional observation that was listed in pre-flight but was not completed during research:

**Observation 4 (optional but important): Headroom AI on Windows**  
Method: `pip install "headroom-ai[all]"` on the target Windows machine. Test whether its hook adapter works with Claude Code.  
Purpose: If Headroom AI works, contributing to it may be more valuable than building Quor. The competitive research concluded: "Build if Headroom AI fails on target Windows. Contribute to Headroom AI if it works."  
Time required: 1 hour.

**If Headroom AI works on Windows:** This changes the direction of the project. Raise this finding before starting Phase 0.  
**If Headroom AI fails or is unavailable:** Proceed. Quor exists.

---

## What the Research Confirmed (Summary)

**The problem is real:**
- AI coding assistants consume full command output verbatim
- 50–90% of typical command output is noise (ANSI codes, passing tests, unchanged states)
- No pip-installable Python solution exists for Windows

**The market gap is real:**
- RTK (dominant, 67k stars) is Rust-only — no `pip install` on Windows
- Headroom AI exists (37k stars) but Windows compatibility is unverified
- snip uses the same hook architecture but is Go-based (not pip-installable)
- No existing tool exposes compression decisions transparently

**The architecture is sound:**
- ContentMask (KEEP/COMPRESS/PROTECT) solves the provenance problem that string→string transforms don't
- TOML stages-array format is more explicit and IDE-friendly than Zap's implicit ordering
- Pydantic v2 + JSON Schema enables IDE autocomplete on filter files
- The plugin system (`quor.compression_stage` entry-points) is the defensible enterprise moat
- Fail-open at every level is the only safe architecture for a hook in an AI session

**The V1 scope is appropriate:**
- 6 CLI commands, 5 filter categories, 1 AI assistant adapter (Claude Code)
- Pure Python, no compiled extensions, pip-installable
- Three operating modes let users build trust before enabling compression

**The name conflict is resolved:**
- `quor` is the correct name (available on PyPI at time of research, best metaphor)
- `samuelfaj/distill` (TypeScript, npm) makes the original name `distill` unacceptable

---

## What the Research Did NOT Decide

These items were intentionally deferred — they don't need to be answered before implementation:

1. **Exact GitHub org/repo name.** Decide at first public push.
2. **License.** Apache 2.0 is the conventional choice for tools in this space (RTK, Headroom AI are both Apache 2.0). Choose at v0.5.
3. **Plugin ecosystem governance.** Who approves the `quor-*` namespace? Write the policy at v1.0.
4. **macOS CI.** Manual verification at v1.0. macOS CI at v2.0.
5. **The session-level deduplication architecture.** V2. Do not design this for V1.
6. **LLM-assisted compression as a plugin.** Architecturally feasible. Build only when users ask.

---

## The Exact First Implementation Task

**Task:** Create the repository with a working `pyproject.toml` and a `quor/__main__.py` that performs the Python version check.

**Before this task:**
1. Complete Observations 1–3 above (startup time, hook mechanism, PyPI name). If Observation 4 (Headroom AI) changes the project direction, raise it before this task.
2. Create the GitHub repository.
3. Clone it locally.

**The task is complete when:**
- [ ] `pip install -e .` succeeds on Windows without compilation
- [ ] `python -m quor --help` prints without error
- [ ] `python -m quor` (no args) prints a version string and exits 0
- [ ] Running on Python 3.10 prints: "Quor requires Python 3.11 or higher. You are running 3.10.x. Please upgrade." and exits with code 5.
- [ ] `quor/errors.py` exists with the complete exception hierarchy
- [ ] `conftest.py` exists with the autouse isolation fixture
- [ ] `.github/workflows/ci.yml` exists and CI runs on `windows-latest` and `ubuntu-latest`

**Specific files to create (Phase 0 from IMPLEMENTATION_PLAN.md):**

```
pyproject.toml                     — dependencies, entry-points, tool config
quor/__init__.py                  — __version__ = "0.1.0.dev0"
quor/__main__.py                  — version check (3.11+), route to hook or CLI
quor/errors.py                    — QuorError, FilterError, ConfigError, HookError, CacheError, PluginError, ExitCode
conftest.py                        — autouse isolation fixture
.github/workflows/ci.yml           — matrix: Python 3.11+3.12 × ubuntu+windows
.gitignore                         — standard Python gitignore
```

**The `pyproject.toml` entry-points block:**
```toml
[project.scripts]
quor = "quor.__main__:main"
qr = "quor.__main__:main"
```

**The version check in `__main__.py`:**
```python
import sys

def _check_python_version() -> None:
    if sys.version_info < (3, 11):
        print(
            f"Quor requires Python 3.11 or higher. "
            f"You are running {sys.version_info.major}.{sys.version_info.minor}. "
            f"Please upgrade: https://python.org/downloads/",
            file=sys.stderr,
        )
        sys.exit(5)  # ExitCode.DEPENDENCY_MISSING

_check_python_version()
```

**Do not implement anything else in this task.** The CLI commands, the pipeline, the filters — those are Phase 1 through Phase 9. Phase 0 is repository scaffolding only.

---

## Handing Off to Implementation

The research phase produced this definitive answer to every architectural question that could be answered without running code. What remains is building and validating.

**The canonical documents in `docs/final/` are the only source of truth for implementation:**

| Document | When to use it |
|---|---|
| PROJECT_BIBLE.md | Understand the full product vision and requirements |
| IMPLEMENTATION_PLAN.md | Understand what to build in what order |
| CLAUDE.md | Read before every AI-assisted coding session |
| DECISIONS.md | Understand why every architectural choice was made |
| ANTI_GOALS.md | Evaluate any proposed feature against scope |
| RELEASE_CRITERIA.md | Know what "done" means for each milestone |
| ROADMAP.md | Understand what each version delivers |
| CONTRIBUTING.md | Guide for any contributor (human or AI) |
| PROJECT_STATUS.md | Current state at a glance — update each session |
| RESEARCH_COMPLETION.md | This document — close of research, first task |

**The archived research in `docs/archive/` is historical record only.** It should not be consulted during implementation except to understand the history of a decision. If a question arises during implementation that is not answered by `docs/final/`, the answer is either:
1. In a DECISIONS.md ADR (check there first), or
2. A new decision that should be made and added to DECISIONS.md.

Do not revisit archived research to relitigate decided questions. The research phase is over. Build.
