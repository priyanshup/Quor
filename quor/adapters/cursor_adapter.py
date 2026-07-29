"""Cursor adapter (QB-069) — detection and readiness reporting only.

Researched against Cursor's own hooks documentation (cursor.com/docs/hooks)
and corroborating third-party references (blog.gitbutler.com's hooks deep
dive, the `cursor-hooks` type-definitions project) before writing any code,
per ADR-036's mandatory pre-flight compatibility gate (§10.3). Findings:

- Cursor's hook events (`.cursor/hooks.json`) are `beforeSubmitPrompt`,
  `beforeShellExecution`, `beforeMCPExecution`, `beforeReadFile`,
  `afterFileEdit`, and `stop`. The two relevant to Quor's model —
  `beforeShellExecution` (would map to `COMMAND_INTERCEPT`) and
  `beforeReadFile` (would map to `CONTENT_INTERCEPT`) — are both
  confirmed-or-inferred **allow/deny/ask only**: `beforeShellExecution`'s
  documented stdout response is `{"continue": bool, "permission":
  "allow"|"deny"|"ask"}`, with no field for a rewritten command anywhere in
  that shape. `beforeReadFile` is not independently documented with its own
  response shape, but follows the identical "before"-hook pattern every
  other pre-hook in this system uses — inferred, not confirmed, to be the
  same allow/deny/ask contract; re-verify directly before ever assuming
  otherwise.
- There is no post-execution or post-read hook in Cursor's event list at
  all (`afterFileEdit` fires after the *agent writes* a file, the reverse
  direction from what `CONTENT_INTERCEPT` needs). Unlike Windsurf, which at
  least has `post_run_command`/`post_read_code` (themselves also
  observational-only — see `windsurf_adapter.py`), Cursor has no hook point
  positioned to intercept command output or file-read content whatsoever.

Built on `DetectionOnlyAdapter` (see `quor/adapters/_detection_only.py`'s
module docstring for the shared shape this and five other adapters use).
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from quor.adapters._detection_only import DetectionOnlyAdapter


def _cursor_config_dir() -> Path:
    # Global config directory (hooks.json/mcp.json can also live here
    # alongside the per-project .cursor/ directory) — the same "does the
    # user's home directory show evidence of this tool" signal Codex's own
    # adapter uses for ~/.codex.
    return Path.home() / ".cursor"


class CursorAdapter(DetectionOnlyAdapter):
    """Cursor — detection-only. See module docstring for why."""

    agent_id: ClassVar[str] = "cursor"
    display_name: ClassVar[str] = "Cursor"
    limitation_reason: ClassVar[str] = (
        "Cursor's beforeShellExecution/beforeMCPExecution hooks are documented "
        "allow/deny/ask only (no way to rewrite a command), and Cursor has no "
        "post-execution or post-read hook that could replace content Quor "
        "could otherwise compress."
    )

    def _detect(self) -> tuple[bool, str]:
        config_dir = _cursor_config_dir()
        if config_dir.exists():
            return True, f"config directory detected at {config_dir}"
        return False, "not detected (no ~/.cursor directory) — install is unaffected either way"
