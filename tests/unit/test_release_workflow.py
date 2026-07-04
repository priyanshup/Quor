"""Regression guard for QB-001: TestPyPI gate before production PyPI publish.

.github/workflows/release.yml isn't Python, so it can't be unit-tested the
normal way — but its job-dependency chain is exactly the kind of "behavior"
QB-001 fixed and that must not silently regress (e.g. someone "simplifying"
the workflow and accidentally dropping a `needs` entry). No YAML parser
dependency is added for this: the checks below are plain-text structural
assertions against the raw workflow file, which is enough to catch the
specific regression this guards against (the gate chain being weakened or
removed) without taking on a new project dependency for one test file.
"""

from __future__ import annotations

import re
from pathlib import Path

_WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "release.yml"
)


def _read_workflow() -> str:
    return _WORKFLOW_PATH.read_text(encoding="utf-8")


def _job_block(text: str, job_name: str) -> str:
    """Return the raw text of one top-level job block (from its header to
    the next top-level job header or end of file)."""
    pattern = rf"(?m)^  {re.escape(job_name)}:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)"
    match = re.search(pattern, text, flags=re.DOTALL)
    assert match is not None, f"job {job_name!r} not found in release.yml"
    return match.group(1)


def _needs_of(text: str, job_name: str) -> list[str]:
    block = _job_block(text, job_name)
    match = re.search(r"needs:\s*(\[.*?\]|\S+)", block)
    assert match is not None, f"job {job_name!r} has no 'needs:' entry"
    raw = match.group(1)
    if raw.startswith("["):
        return [n.strip() for n in raw.strip("[]").split(",") if n.strip()]
    return [raw.strip()]


class TestReleaseWorkflowExists:
    def test_workflow_file_exists(self) -> None:
        assert _WORKFLOW_PATH.exists()

    def test_no_workflow_dispatch_trigger(self) -> None:
        """No manual-run trigger — the only way to run this workflow is a
        version tag push, so there's no bypass path via manual dispatch."""
        text = _read_workflow()
        # "on:" block should only contain "push: tags:", not workflow_dispatch.
        on_block_match = re.search(r"(?m)^on:\n(.*?)(?=^\S)", text, flags=re.DOTALL)
        assert on_block_match is not None
        assert "workflow_dispatch" not in on_block_match.group(1)


class TestReleaseWorkflowGateChain:
    """Locks in QB-001's fix: publish-pypi is unreachable without the full
    TestPyPI validation chain succeeding first, in this exact run."""

    def setup_method(self) -> None:
        self.text = _read_workflow()

    def test_publish_pypi_needs_release_approval(self) -> None:
        needs = _needs_of(self.text, "publish-pypi")
        assert "release-approval" in needs

    def test_release_approval_needs_validate_testpypi(self) -> None:
        needs = _needs_of(self.text, "release-approval")
        assert "validate-testpypi" in needs

    def test_validate_testpypi_needs_publish_testpypi(self) -> None:
        needs = _needs_of(self.text, "validate-testpypi")
        assert "publish-testpypi" in needs

    def test_publish_testpypi_needs_build(self) -> None:
        needs = _needs_of(self.text, "publish-testpypi")
        assert "build" in needs

    def test_release_approval_uses_a_gated_environment(self) -> None:
        """The approval step must reference a GitHub Environment (the actual
        approval mechanism) — not just be an inert echo step."""
        block = _job_block(self.text, "release-approval")
        assert re.search(r"(?m)^\s*environment:\s*\S+", block)

    def test_publish_pypi_uses_pypi_environment(self) -> None:
        block = _job_block(self.text, "publish-pypi")
        assert re.search(r"(?m)^\s*environment:\s*pypi\s*$", block)

    def test_publish_pypi_checks_token_before_publishing(self) -> None:
        """Missing secret must fail loudly, not silently skip (existing
        behavior predating QB-001, still required)."""
        block = _job_block(self.text, "publish-pypi")
        assert "PYPI_API_TOKEN" in block
        assert "exit 1" in block
