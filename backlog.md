# Backlog

Proposed changes, process improvements, and known gaps that are not yet scheduled
for implementation. Each entry: ID, Priority, Category, Title, Problem, Desired
outcome, Status. Add new entries at the top (most recent first).

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
