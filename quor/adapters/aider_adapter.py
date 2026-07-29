"""Aider adapter (QB-069) — detection and readiness reporting only.

Researched against Aider's own documentation (aider.chat/docs, specifically
the linting/testing guide) before writing any code, per ADR-036's mandatory
pre-flight compatibility gate (§10.3). Finding: **Aider has no tool-call
hook system at all** — unlike every other tool in QB-068/QB-069, there is
no `PreToolUse`/`beforeShellExecution`/`pre_run_command`-shaped extension
point to even evaluate for a modify capability. Aider's only configurable
touch points are `--lint-cmd`/`--test-cmd` (`.aider.conf.yml`): shell
commands Aider itself invokes as part of its own auto-lint/auto-test
feature after an edit, not a general interception point for arbitrary
commands the AI runs. Third-party summary, corroborated by the absence of
any hook/plugin terminology anywhere in Aider's own docs: "extensibility
comes primarily through shell commands and scripting rather than formal
plugin hooks."

This is a *stronger* absence than Codex CLI (which at least has an
allow/deny-shaped hook with no modify capability) or Cursor/VS
Code/Windsurf (which have modify-shaped gaps in an otherwise real hook
system) — there is simply nothing to hook into. Unlike every other adapter
in this file's family, detection here also cannot key off a single
config-directory convention the way an editor's global settings folder
does — Aider is a plain CLI tool with no fixed home-directory footprint
guaranteed to exist merely from being installed, so detection here checks
multiple independent, deterministic signals (the `aider` executable on
`PATH`, and either a project-local or user-level `.aider.conf.yml`) and
reports whichever it found, or none.

Built on `DetectionOnlyAdapter` (see `quor/adapters/_detection_only.py`'s
module docstring for the shared shape this and five other adapters use).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import ClassVar

from quor.adapters._detection_only import DetectionOnlyAdapter

_CONFIG_FILENAME = ".aider.conf.yml"


class AiderAdapter(DetectionOnlyAdapter):
    """Aider — detection-only. See module docstring for why."""

    agent_id: ClassVar[str] = "aider"
    display_name: ClassVar[str] = "Aider"
    limitation_reason: ClassVar[str] = (
        "Aider has no tool-call hook system at all — its only extensibility "
        "points are --lint-cmd/--test-cmd, which wrap Aider's own auto-lint/"
        "auto-test feature, not a general command-interception mechanism Quor "
        "could hook into."
    )

    def _detect(self) -> tuple[bool, str]:
        on_path = shutil.which("aider") is not None
        project_config = Path.cwd() / _CONFIG_FILENAME
        user_config = Path.home() / _CONFIG_FILENAME

        found: list[str] = []
        if on_path:
            found.append("'aider' executable on PATH")
        if project_config.exists():
            found.append(f"{project_config}")
        if user_config.exists():
            found.append(f"{user_config}")

        if found:
            return True, "detected: " + ", ".join(found)
        return (
            False,
            "not detected (no 'aider' on PATH, no .aider.conf.yml) — "
            "install is unaffected either way",
        )
