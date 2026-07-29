"""Windsurf (Cascade) adapter (QB-069) — detection and readiness reporting only.

Researched against Windsurf's own Cascade Hooks documentation
(docs.windsurf.com/windsurf/cascade/hooks, which as of this research
redirects to docs.devin.ai/desktop/cascade/hooks — Windsurf is now under
Cognition/Devin) before writing any code, per ADR-036's mandatory
pre-flight compatibility gate (§10.3). Findings:

- Cascade has twelve hook events, including `pre_run_command`/
  `post_run_command` and `pre_read_code`/`post_read_code` — structurally
  the closest of any tool researched in QB-068/QB-069 to Claude Code's own
  PreToolUse/Bash + PostToolUse/Read pair.
- **Pre-hooks are block-only.** The docs state plainly: "For pre-hooks
  (executed before an action), your script can block the action by exiting
  with exit code 2." There is no structured stdout response for
  modification — no rewritten-command field anywhere in the schema.
- **Post-hooks are observational only.** Directly confirmed by fetching the
  hooks doc a second time, focused specifically on `post_run_command`/
  `post_read_code`: "Post-hooks cannot block since the action has already
  occurred," their documented purpose is "log command results" / "log
  successful reads, track file access patterns," and the only mention of
  hook output is that stdout/stderr may optionally be shown in the
  user-facing UI — never fed back into what Cascade's model sees. This is
  the most directly-confirmed "no" of any tool in QB-068/QB-069 (not
  inferred from a sibling hook's shape, as with Cursor's `beforeReadFile`).
- Windows is fully, explicitly supported (a dedicated `powershell` field in
  each hook entry, with documented PowerShell fallback behavior, and a
  `C:\\ProgramData\\Windsurf\\hooks.json` system-level path) — not the
  blocker here, unlike Codex.

Built on `DetectionOnlyAdapter` (see `quor/adapters/_detection_only.py`'s
module docstring for the shared shape this and five other adapters use).
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from quor.adapters._detection_only import DetectionOnlyAdapter


def _windsurf_config_dir() -> Path:
    # User-level Cascade hooks.json location, per Windsurf's own docs
    # (~/.codeium/windsurf/hooks.json on every OS, including Windows).
    return Path.home() / ".codeium" / "windsurf"


class WindsurfAdapter(DetectionOnlyAdapter):
    """Windsurf (Cascade) — detection-only. See module docstring for why."""

    agent_id: ClassVar[str] = "windsurf"
    display_name: ClassVar[str] = "Windsurf (Cascade)"
    limitation_reason: ClassVar[str] = (
        "Cascade's pre_run_command/pre_read_code hooks can only block (exit "
        "code 2), and its post_run_command/post_read_code hooks are documented "
        "as observational only — neither can rewrite a command or replace "
        "content Quor could compress."
    )

    def _detect(self) -> tuple[bool, str]:
        config_dir = _windsurf_config_dir()
        if config_dir.exists():
            return True, f"config directory detected at {config_dir}"
        return (
            False,
            "not detected (no ~/.codeium/windsurf directory) — install is unaffected either way",
        )
