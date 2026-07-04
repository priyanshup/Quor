"""Unit tests for quor/rewrite/invocation.py.

Regression coverage for the switch away from the pip-generated `quor`/`qr`
launcher stubs: rewritten commands must invoke the current interpreter
directly (`sys.executable -m quor`), never the bare `quor` word that a
corporate application-control policy can block on PATH resolution.
"""

from __future__ import annotations

import shlex
import sys

import pytest

from quor.rewrite.classifier import rewrite_command
from quor.rewrite.invocation import get_quor_invocation


class TestGetQuorInvocation:
    def test_uses_sys_executable(self) -> None:
        invocation = get_quor_invocation()
        assert shlex.quote(sys.executable) in invocation

    def test_uses_module_flag(self) -> None:
        assert get_quor_invocation().endswith("-m quor")

    def test_does_not_start_with_bare_quor(self) -> None:
        # The old, PATH-dependent form was the literal word "quor" with
        # nothing before it. The new form always has the interpreter path
        # (or, in the sys.executable-unavailable fallback, is still not
        # followed directly by "-m").
        invocation = get_quor_invocation()
        assert invocation != "quor"
        assert not invocation.startswith("quor ")

    def test_fallback_when_no_sys_executable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "executable", "")
        assert get_quor_invocation() == "quor"


class TestRewriteNoLongerUsesLauncher:
    """Proves the rewrite mechanism no longer depends on quor.exe/qr.exe."""

    def test_rewritten_command_does_not_start_with_bare_quor(self) -> None:
        rewritten = rewrite_command("git status")
        assert rewritten is not None
        assert not rewritten.startswith("quor ")

    def test_rewritten_command_starts_with_current_interpreter(self) -> None:
        rewritten = rewrite_command("git status")
        assert rewritten is not None
        assert rewritten.startswith(shlex.quote(sys.executable))

    def test_rewritten_command_matches_helper(self) -> None:
        rewritten = rewrite_command("cat pyproject.toml")
        assert rewritten == f"{get_quor_invocation()} cat pyproject.toml"

    def test_existing_functionality_unchanged(self) -> None:
        # Same classification/rewrite behavior as before — only the prefix
        # used to reach Quor changed, not which commands get rewritten.
        # (npm is no longer a good "still unknown" example post-QB-006A —
        # see test_rewrite.py for npm/npx/pnpm/yarn classification coverage.)
        assert rewrite_command("cargo build") is None
        assert rewrite_command("git status --porcelain") is None
        rewritten = rewrite_command("git status && git diff")
        assert rewritten is not None
        assert rewritten.count("-m quor") == 2
