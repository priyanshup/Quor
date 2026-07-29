"""VS Code adapter (QB-069) — detection and readiness reporting only.

**Scope note:** vanilla VS Code has no AI agent or hook system of its own —
this adapter targets VS Code's bundled GitHub Copilot agent mode (the
first-party, dominant AI agent inside VS Code), not the editor generically.
A different extension (Continue.dev, Cline, etc.) installed into VS Code is
out of scope here — Continue.dev already has its own adapter
(`continue_adapter.py`).

Researched against `code.visualstudio.com/docs/agent-customization/hooks`
before writing any code, per ADR-036's mandatory pre-flight compatibility
gate (§10.3). Findings:

- VS Code's Copilot agent hooks (registered at `.github/hooks/*.json`
  workspace-level or `~/.copilot/hooks` user-level) fire at eight lifecycle
  points including `PreToolUse`/`PostToolUse`. `PreToolUse`'s stdout
  response is documented as `{"continue": bool, "hookSpecificOutput":
  {"permissionDecision": "allow"|"deny"|"prompt", ...}}` — the docs state
  explicitly: "There is no documented mechanism to rewrite/modify tool
  input before execution."
- `PostToolUse` is documented with "no documented support" for modifying or
  replacing a tool's result — it exists for side effects (formatting,
  logging), not content transformation. Neither hook can do what
  `COMMAND_INTERCEPT`/`CONTENT_INTERCEPT` requires.
- Windows is explicitly supported (hook commands can be OS-specific via
  `"windows"`/`"linux"`/`"osx"` keys) — this is not the blocker here, unlike
  Codex; the blocker is purely the absence of a modify/replace capability.

Built on `DetectionOnlyAdapter` (see `quor/adapters/_detection_only.py`'s
module docstring for the shared shape this and five other adapters use).
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from quor.adapters._detection_only import DetectionOnlyAdapter


def _copilot_hooks_dir() -> Path:
    # User-level Copilot agent hooks directory, per VS Code's own docs —
    # a more specific, relevant signal than any generic "is VS Code
    # installed" check, since plain VS Code presence says nothing about
    # whether the Copilot agent (the thing this adapter actually targets)
    # is even configured.
    return Path.home() / ".copilot"


class VSCodeAdapter(DetectionOnlyAdapter):
    """VS Code (GitHub Copilot agent mode) — detection-only. See module
    docstring for why and for the scope note on what "VS Code" means here."""

    agent_id: ClassVar[str] = "vscode"
    display_name: ClassVar[str] = "VS Code (GitHub Copilot agent mode)"
    limitation_reason: ClassVar[str] = (
        "VS Code's Copilot agent hooks (PreToolUse/PostToolUse) are documented "
        "as allow/deny/prompt only — no documented way to rewrite tool input "
        "or replace a tool's result."
    )

    def _detect(self) -> tuple[bool, str]:
        hooks_dir = _copilot_hooks_dir()
        if hooks_dir.exists():
            return True, f"Copilot config directory detected at {hooks_dir}"
        return (
            False,
            "not detected (no ~/.copilot directory) — install is unaffected either way",
        )
