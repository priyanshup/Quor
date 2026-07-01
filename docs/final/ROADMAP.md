# ROADMAP
## Quor — Version Milestones

> This document describes what each version delivers and what "done" means for each milestone.
> Versions are not date-committed. Each exits when its release criteria (see RELEASE_CRITERIA.md) are met.

---

## v0.1 — Internal Alpha: "It Works on My Machine"

**Theme:** The core pipeline works end-to-end on the builder's Windows machine. Not yet safe for general release.

**What ships:**
- ContentMask pipeline with all five built-in stages
- Five built-in filter files: git, pytest, build, cat, generic
- Claude Code PreToolUse hook adapter (Windows PowerShell only)
- Three operating modes: AUDIT / OPTIMIZE / SIMULATE
- Six CLI commands: init, validate, explain, gain, verify, doctor
- Both `quor` and `qr` entry points
- SQLite + JSONL dual tracking
- Inline filter tests (`quor verify`)
- Pydantic v2 config with JSON Schema (yaml-language-server directive)
- Three-tier filter registry (project > user > built-in)
- PROTECT decision propagation
- Fail-open at all levels
- Plugin Infrastructure and Discovery & Loading (Phases 8-9, pulled forward from the original v0.5 target): `quor.compression_stage` / `quor.plugin` entry-points, plugin cache, `api_version` compatibility check, `file://` escape hatch for local custom stages

**What does NOT ship:**
- Windows-generic CI (only builder's machine validated)
- Any other AI assistant adapter

**Exits when:**
- `quor doctor` shows all green on the builder's Windows machine
- A real Claude Code session with `git status`, `pytest`, and `cat` runs without hook failures
- `quor gain` shows accurate savings for 10+ real invocations
- All inline filter tests pass (`quor verify` exit code 0)

> **Update (2026-07-01):** v0.1.0 was published directly to both TestPyPI
> (validation) and the main PyPI registry, ahead of the v0.5 gate below —
> the "Public PyPI release" line originally listed under "What does NOT
> ship" no longer applies. See `PROJECT_STATUS.md`'s "Release Publication
> Notes" for details. Windows-generic CI (multiple real machines, not just
> the builder's) was also validated as part of this release.

---

## v0.5 — Public Alpha: "Installable, Trustworthy, Tested"

**Theme:** Safe to share with other developers. CI-validated on Windows and Linux. (Plugin system shipped in v0.1 — see below.)

**What ships (adds to v0.1):**
- Windows CI on GitHub Actions (`windows-latest`)
- Linux CI on GitHub Actions (`ubuntu-latest`)
- ≥80% test coverage on pipeline/ and filters/
- ~~TestPyPI upload (not main PyPI yet)~~ — done ahead of schedule at v0.1.0 (both TestPyPI and main PyPI)
- `quor doctor --timing` flag for latency profiling
- Tee mechanism: original cached before compression, `[full output: path]` footer
- `on_empty` trigger rate tracked and shown in `quor gain`
- Secret pattern detection (warns to stderr, never blocks)
- README with before/after example and Windows-first callout
- Full `quor explain` with rich output

**What does NOT ship:**
- macOS-specific testing (cross-platform but not CI-validated on Mac)
- Cursor / Copilot / Gemini adapters
- ML content detection
- Session-level deduplication

**Exits when:**
- `pip install --index-url https://test.pypi.org/simple/ quor` works on a fresh Windows VM
- Windows CI and Linux CI both green on main branch
- 3 non-builder developers have installed and used it (internal user testing)
- Zero hook failures reported in 5+ hours of AI coding session use
- All RELEASE_CRITERIA.md Public Alpha gates met

---

## v1.0 — Public Beta → Stable: "Ready for Everyone"

**Theme:** Production-ready. Pip-installable from main PyPI. Recommended for all Python-environment AI coding users.

**What ships (adds to v0.5):**
- Main PyPI release: `pip install quor`
- Plugin API declared stable (semver contract)
- CONTRIBUTING.md: complete guide for external contributors
- At least one community-contributed or ecosystem-contributed plugin (e.g., `quor-docker`)
- macOS validated (manual; CI optional at this milestone)
- GitHub Discussions enabled for community filter requests
- `quor validate` accepts all previous-version filter formats (backwards-compatible)
- `quor doctor` warns if AUDIT mode for >7 days
- Onboarding mode: first 5 filtered commands print brief stats to stderr

**Plugin ecosystem start:**
- `quor-docker`: Docker build and run output filtering (separate package)
- Filter contribution process in CONTRIBUTING.md
- Official `quor-*` namespace guidelines published

**What does NOT ship:**
- Cursor / Copilot adapters (v2)
- ML content detection (v2 optional extra)
- Session-level deduplication (v2)
- Watch mode (v2)
- Web UI (never — see ANTI_GOALS.md)

**Exits when:**
- All RELEASE_CRITERIA.md v1.0 gates met
- Zero P0 bugs open
- Plugin API has not changed since v0.1, when it first shipped (stability proof)
- `pip install quor` works on fresh installs of: Windows 11, Ubuntu 22.04, and Python 3.11, 3.12, 3.13

---

## v2.0 — Multi-Agent + Intelligence Layer

**Theme:** Expands beyond Claude Code. Adds smart session-level context.

**What ships (adds to v1.0):**

**Multi-agent support:**
- Cursor adapter (PreToolUse hook)
- Copilot CLI adapter
- Gemini Code Assist adapter (if hook API available)
- Adapter detection in `quor doctor`

**Session-level intelligence:**
- Content the AI has already seen this session is not re-sent
- Hook reads Claude Code session context JSON to track seen content hashes
- `quor gain` shows session-level deduplication savings separately

**Discovery command:**
- `quor discover` scans past AI session logs for commands with no filter coverage
- Outputs suggested filter TOML that the user can add to their config
- Respects privacy: only reads local session log files, never uploads

**Optional ML extra:**
- `pip install "quor[ml]"` installs Magika (if it has clean Windows wheels by v2)
- Magika improves content type detection for edge cases
- Falls back gracefully to heuristics if Magika unavailable

**Plugin ecosystem maturation:**
- `quor-kubernetes` / `quor-terraform` / `quor-aws` community plugins
- Plugin registry (a GitHub-hosted TOML index of known plugins)
- `quor discover --suggest-plugins` recommends plugins based on your command history

**What does NOT ship:**
- Web UI (never)
- SaaS/cloud features (never — see ANTI_GOALS.md)
- LLM calls in the compression path (never)

---

## Long-Term Vision

**Year 1 (v0.1 → v1.0):**
Quor becomes the standard choice for Python-environment and Windows AI developers. The plugin system has 10+ published filters. A Headroom AI comparison post accurately describes when to use each tool.

**Year 2 (v2.0):**
Multi-agent support. `quor discover` identifies filter gaps. The plugin ecosystem is self-sustaining with community contributions.

**Year 3:**
Session-level intelligence matures. Quor can diff the current session's context against a new command's output and only surface what's new. The token savings compound.

**Year 5:**
The filter registry (a GitHub-hosted TOML index) has 100+ community-maintained filters. The plugin ecosystem is the product; Quor is the runtime. An academic study using Quor's benchmarking infrastructure publishes the first controlled measurement of filtering's effect on AI coding task success rate.

**The enduring design bet:**
Deterministic, rule-based, auditable compression will always be the correct default for enterprise use. LLM-based summarization is complementary (and available via plugin) but will never be the default path — not because it's technically inferior, but because enterprise users cannot trust what they cannot inspect.
