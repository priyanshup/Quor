# Pre-Public-Release Tech Debt Audit — 2026-07-06

Snapshot audit performed before Quor's first wide public release. Goal: find edge cases, boundary
cases, and tech debt not yet caught by `backlog.md`'s per-item work, so they can be triaged and
fixed deliberately rather than discovered by early users.

**Methodology:** live re-verification against current `main` (commit `04b0d29`), not a re-read of old
notes. `ruff check`, `mypy`, and the full `pytest` suite (with coverage) were actually re-run today;
several findings below were reproduced live (`quor explain ...`), not inferred from memory or docs.
Cross-referenced against `docs/final/RELEASE_CRITERIA.md`'s gates (which exist but had never been
walked and checked off) and `backlog.md`'s already-open items (not duplicated here — see the
"Carried forward" section).

**This is a point-in-time snapshot**, not a living document. Future audits should add a new dated
file in this folder rather than editing this one, so history isn't lost.

**Verified healthy (so this list isn't taken as "everything is broken"):** `ruff check quor/ tests/`
— clean. `mypy quor/` — clean, 0 errors across 53 files. Full `pytest tests/` — all green (1 skip, 0
failures). Coverage — 93% overall, every module ≥72%, already exceeding the V1.0 release gate's 80%
bar. All 12 broad `except Exception` sites (`noqa: BLE001`) live exclusively in
`plugin_loader.py`/`plugins/registry.py`, consistent with the documented "plugin failures never
fatal" boundary — none found bleeding into core pipeline/dispatch code. Also re-checked a previously
recorded issue (`quor doctor`'s hook-collision check hardcoding `Path.home()`, causing 8 local test
failures) — it's already fixed: `_check_hook_collision()` now takes an injectable `settings_path`
parameter, and `pytest tests/unit/test_cli.py -k "HookCollision or Doctor or Init"` is fully green
locally today. Dropped from this list; a stale note about it has been removed from memory.

---

## Priority: Critical

### TD-001 — Compound-command redirect operators get mangled by the lexer/classifier

**Where:** `quor/rewrite/lexer.py` (tokenizer), surfaces through `quor/rewrite/classifier.py`'s
compound-command rewrite path.

**What:** A command containing a stderr/stdout redirect (`2>&1`) gets a spurious space inserted when
re-serialized, changing `2>&1` into `2 >& 1`. Reproduced live:

```
$ quor explain "cd X && python -m quor gain 2>&1"
Rewritten command: cd X && python -m quor gain 2 >& 1
```

This is not cosmetic. Confirmed the two forms are **not equivalent** in a real shell:

```
$ echo hello 2>&1        # → "hello"
$ echo hello 2 >& 1      # → "hello 2"   (the "2" becomes a literal argument)
```

**Why Critical:** the rewritten string is what Claude Code actually executes (via
`hookSpecificOutput.updatedInput.command`) — this isn't limited to the `quor explain` preview. Any
real compound command a user or the AI runs that redirects stderr silently changes behavior once it
passes through Quor's hook. For a tool whose entire value proposition is "transparently proxy your
commands, only compress what comes back," a bug that changes what the command *does* is the worst
class of defect it can have — worse than a compression-quality bug, because it's silent and the user
has no way to know their command ran differently than intended.

**Status:** Resolved — see QB-023 in `backlog.md`. Fixed on `feature/td-tier1-pre-release-fixes`.

---

## Priority: High

### TD-002 — `assert` used for validation in `quor/tracking/db.py:254`

**Where:** `TrackingDB._write_jsonl()`:
```python
def _write_jsonl(self, rec: InvocationRecord) -> None:
    assert self._jsonl_path is not None
    ...
```

**What:** This is a direct violation of the project's own non-negotiable rule (`CLAUDE.md` Safety
Rule #6, `RELEASE_CRITERIA.md` gate **IA-Q07** — "No `assert` in non-test source files used for
validation, grep confirms"). Currently the only call site (`_flush_loop`) guards it with
`if self._jsonl_path is not None` immediately before calling, so it isn't reachable with a bad value
today — but that's exactly the kind of guarantee `python -O` silently removes, and it's precisely
what IA-Q07 exists to catch by grep. As written, a straightforward `grep -rn "assert " quor/` fails
this gate right now.

**Fix:** replace with an `if self._jsonl_path is None: raise RuntimeError(...)` (or restructure so
the type is non-optional on this code path), matching the pattern used everywhere else in the
codebase.

**Status:** Resolved — see QB-024 in `backlog.md`. Fixed on `feature/td-tier1-pre-release-fixes`.

### TD-003 — `RELEASE_CRITERIA.md` gates have never been walked and checked off

**What:** Every single gate in `docs/final/RELEASE_CRITERIA.md`, across all four milestones, is still
an unchecked `- [ ]` — including gates for Internal Alpha, which the project is functionally well
past (v0.3.0 published, 983+ tests, PyPI releases already shipped). The document's own rules state
"no milestone is done until every gate is green... partial credit does not exist" — but nobody has
actually gone through and recorded pass/fail/evidence for a single gate.

Spot-checking a handful live today: IA-Q01 (mypy) ✅ pass, IA-Q02 (ruff) ✅ pass, IA-Q04 (coverage
≥70% on pipeline/filters) ✅ pass (93% overall), IA-Q07 (no assert) ❌ **fails**, currently (TD-002).
PA-Q01/Q02 (CI on windows-latest + ubuntu-latest, Python 3.11/3.12) ✅ pass. B-Q01 (Python 3.13 in
CI) ❌ not yet done (see TD-004).

**Why High, not Medium:** this document is the project's own self-imposed release gate. Treating a
"public release" as ready without ever having actually run this checklist means the team's own
quality bar was never applied to itself — regardless of how good the code turns out to be on
inspection (and it mostly is — see "Verified healthy" above).

**Fix:** before announcing a public release, walk `RELEASE_CRITERIA.md` for whichever milestone is
being targeted (this audit suggests Public Alpha, v0.5, is the realistic target given the code's
actual maturity) top to bottom, record real pass/fail/evidence per gate, and fix or explicitly defer
(with the user's sign-off) anything that fails — starting with TD-002 and TD-004 below, which this
audit already confirms fail today.

**Status:** Resolved — see QB-028 in `backlog.md`. Fixed on `feature/td-tier2-release-readiness`.
Internal Alpha passes in full; Public Alpha does not yet (real gaps found — secret detection and
onboarding mode are unimplemented, spun out as QB-029; a few other findings spun out as QB-030).

### TD-004 — CI test matrix doesn't cover Python versions the package claims to support

**Where:** `.github/workflows/ci.yml` — matrix is `python-version: ["3.11", "3.12"]` only.
`pyproject.toml` declares `requires-python = ">=3.11"` and lists classifiers for 3.11 through 3.14.

**What:** Python 3.13 and 3.14 are advertised as supported (via classifiers) but have zero CI
coverage — nothing verifies the package actually installs or runs correctly on either. This matters
concretely here: the dev `.venv` on this machine already runs Python 3.14, so a regression specific
to 3.13/3.14 could go unnoticed locally *and* in CI simultaneously.

**Fix:** add 3.13 and 3.14 to the `ci.yml` matrix, or narrow the `classifiers`/`requires-python`
claim to match what's actually tested (3.11–3.12) until CI catches up. `RELEASE_CRITERIA.md`'s
own B-Q01 gate already calls for 3.13 in CI at Beta — doing it now, before public release, avoids
shipping an unverified compatibility claim.

**Status:** Resolved — see QB-025 in `backlog.md`. Fixed on `feature/td-tier1-pre-release-fixes`.

### TD-005 — No dependency / supply-chain security scanning

**What:** No Dependabot config (`.github/dependabot.yml`), no CodeQL workflow, no `pip-audit`/`bandit`
step anywhere in `.github/workflows/`. For a tool whose `SECURITY.md` already discusses trust
boundaries (plugin execution, hook data flow) in detail, and which is about to broaden its exposure
via a public release, this is a standard, low-effort hardening gap — not because Quor's own
dependency list is large (six direct runtime deps), but because it's the kind of thing worth having
in place *before* the audience grows, not after a report comes in.

**Fix:** add a `dependabot.yml` (pip ecosystem, weekly) and a scheduled CodeQL workflow (Python).
Both are one-time, low-maintenance additions.

**Status:** Resolved — see QB-026 in `backlog.md`. Fixed on `feature/td-tier1-pre-release-fixes`.

---

## Priority: Medium

### TD-006 — `tests/integration/` exists but is empty

**Where:** `tests/integration/__init__.py` is the only file in that directory.

**What:** `CLAUDE.md`'s testing conventions and `RELEASE_CRITERIA.md`'s **V1-Q07** ("All six CLI
commands have integration tests in addition to unit tests") both call for real integration tests
(actual subprocess/filesystem/SQLite, not mocked) in this folder, marked `@pytest.mark.integration`.
Today the *only* place that marker is used at all is inside `tests/unit/test_plugin_loader.py` — a
unit test file, not `tests/integration/`. Every CLI command today is covered only by unit tests with
mocked boundaries (`CliRunner`, mocked `subprocess.run`, mocked `platformdirs`). This is consistent
with, and partly explains, real bugs found in this exact gap in the past (QB-019's Windows shell-shim
`FileNotFoundError` was invisible to the entire test suite precisely because every dispatcher test
mocks `subprocess.run`).

**Fix:** add real integration tests for at least the six CLI commands (`init`, `validate`, `explain`,
`gain`, `verify`, `doctor`) that exercise real subprocess dispatch and a real (temp-dir-scoped) SQLite
file, per V1-Q07.

**Status:** Resolved — see QB-027 in `backlog.md`. Fixed on `feature/td-tier2-release-readiness`.

### TD-008 — Version number still duplicated across two files

**What:** `quor/__init__.py::__version__` and `pyproject.toml`'s `[project].version` are two
independently hand-maintained strings. QB-020 (see `backlog.md`) added a test that fails the build if
they ever diverge, but didn't eliminate the duplication — a future release still requires remembering
to bump both.

**Fix (already scoped in QB-020, not done):** derive `__version__` from
`importlib.metadata.version("quor")` at runtime, falling back to a hardcoded string only for the
editable/uninstalled case.

**Status:** Resolved — see QB-020 in `backlog.md`. Fixed on `feature/td-tier5-engineering-hygiene`.

### TD-009 — No verified story for coexisting with another PreToolUse hook tool

**What:** `quor doctor` warns if it detects another PreToolUse hook registered, but there is no
confirmed-working scenario where Quor and a second hook tool (e.g. a competing token-optimizer) both
fire on the same Bash event — this intersects a known, closed-as-not-planned Claude Code limitation
(GitHub issue #15897: `updatedInput` from one PreToolUse hook can be ignored when multiple hooks are
registered for the same matcher). This isn't fixable inside Quor, but it's a real first-run experience
risk for exactly the audience most likely to try Quor first (developers already using a similar tool).

**Fix:** not a code fix — a documentation one. State plainly in `README.md`/`quor doctor`'s warning
text that only one PreToolUse Bash hook tool should be active at a time, and that `quor doctor`'s
warning means "disable the other one," not "safe to ignore."

**Status:** Resolved — see QB-031 in `backlog.md`. Fixed on `feature/td-tier3-trust-credibility`.

### TD-010 — `__main__.py` has the lowest test coverage in the codebase (72%)

**What:** Missing coverage is concentrated in the "unknown hook adapter" branch (lines ~46-48) and
the `_run_dispatch()` CLI-entry wrapper (lines ~73-81) — not the safety-critical top-level
`except Exception` fail-open guard itself (Safety Rule #1), which **is** covered. So this is a minor
gap, not a hidden safety-critical hole, but it's worth closing given `__main__.py` is the single
highest-blast-radius file if it ever does break silently.

**Fix:** add two small tests — one invoking `quor hook <unknown-adapter>`, one invoking the plain CLI
dispatch path (`quor git status`-shaped argv) end-to-end.

**Status:** Resolved — see QB-033 in `backlog.md`. Fixed on `feature/td-tier5-engineering-hygiene`.
Coverage went from 72% to 92%.

---

## Priority: Low / Deferred (carried forward from `backlog.md` — not duplicated in detail here)

These are already tracked; listed here only so this audit gives a complete release-readiness
picture in one place.

- **QB-006B (partial):** `prettier`/`jest`/`tsc` have no dedicated filter yet — falls back to the
  generic npm/npx noise filter. Deliberate, not a regression.
- **QB-007 (blocked):** Document (PDF/DOCX/Markdown) compression is blocked on a feasibility
  investigation into whether Claude Code's native Read/File tool output can be intercepted at all.
- **QB-017 (deferred):** `quor gain` can report a negative token count for already-small, already-clean
  output because the tee mechanism's recovery footer can outweigh genuine compression on tiny inputs.
  Not a correctness bug; a metrics-definition question deferred to a future `quor gain` redesign.
- **QB-020 (partial):** see TD-008 above — same item, listed here for completeness.

---

## Recommendation: should this file (and future ones like it) be committed to git?

**Yes — commit it, tracked, same as `backlog.md` already is.** Reasoning:

1. **Precedent already set by this repo.** `backlog.md` is already public, already committed, and
   already documents things more sensitive than anything in this file — e.g. QB-018's detailed
   writeup of a real GLOB/LIKE-injection-shaped bug in the tracking database, and QB-014/QB-004's
   detailed root-cause writeups of filter logic bugs. Treating a tech-debt audit differently from the
   backlog it feeds would be an inconsistent policy for the same kind of information.
2. **Nothing here is a live, unpatched, exploitable vulnerability disclosed ahead of a fix.**
   `SECURITY.md` already scopes what counts as a security report (PROTECT bypass, code execution via
   filter config, path traversal, data leaking off-device) — none of the items above are in that
   category. The worst-case impact of every item here is "compresses differently than intended" or
   "a test gap," not "an attacker gains something." TD-001 (the redirect bug) is the closest to
   concerning, but it's a correctness bug in Quor's own faithful-proxying behavior, not a way for a
   third party to gain anything they couldn't already do by running the shell directly — and Quor's
   own `SECURITY.md` already documents "fail-open, not fail-closed" as the deliberate design, so a
   world where a rewrite occasionally misbehaves and a user's own command output is what's affected
   is already the documented threat model, not a new exposure.
3. **Standard OSS practice.** Public `KNOWN_ISSUES`/tech-debt/backlog files are the norm for
   pre-1.0 open-source projects (signals engineering maturity, gives contributors something concrete
   to pick up — this project's `CONTRIBUTING.md` is explicitly built around external contribution).
   An attacker gets far more signal from reading `quor/rewrite/lexer.py` directly than from this
   document's prose description of the same bug.
4. **The one caveat:** fix TD-001 (the redirect-mangling bug) *before* or *very shortly after*
   publishing this file — not because disclosing it is unsafe, but because it's cheap to fix and it's
   poor form to publicly flag "this changes what your commands do" and then sit on it. The other
   items are fine to sit in the open as an ordinary backlog.

If a future audit ever finds something that genuinely fits `SECURITY.md`'s scope (a real PROTECT
bypass, real code execution path, real data exfiltration), that item alone should go through private
disclosure first — not into this file until fixed. That's the one case where this recommendation
would flip for that specific item only.
