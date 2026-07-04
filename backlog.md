# Backlog

Proposed changes, process improvements, and known gaps that are not yet scheduled
for implementation. Each entry: ID, Priority, Category, Title, Problem, Desired
outcome, Status. Add new entries at the top (most recent first).

---

## QB-002

**Priority:** Medium
**Category:** Product Decision

**Title:** Default operating mode contradicts ADR-009

**Problem:**
ADR-009 (DECISIONS.md) and three docs (CLAUDE.md, PROJECT_BIBLE.md, ROADMAP.md) state the default
operating mode is `AUDIT`. `quor/config/model.py` actually defaults to `"optimize"`, and `quor doctor`
prints `Mode: optimize` on a fresh install. Unclear whether this is an implementation bug against a
finalized ADR, or an intentional change that was never written back into the docs/ADR.

**Desired outcome:**
A maintainer decides which side is correct — either fix the code default to match ADR-009, or update
ADR-009 and the docs to reflect `optimize` as the intended default — and the two are reconciled.

**Status:** Backlog

---

## QB-001

**Priority:** High
**Category:** Release Process

**Title:** Require successful TestPyPI validation before production release

**Problem:**
`release.yml` publishes directly to PyPI after tagging, bypassing manual TestPyPI verification.

**Desired outcome:**
Production publication must require successful TestPyPI validation and explicit approval.

**Status:** Backlog
