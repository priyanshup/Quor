"""Codex CLI adapter (QB-068) — detection and readiness reporting only.

**Why this adapter has no `supported_events`, unlike `ClaudeAdapter`/
`GeminiAdapter`:** researched against OpenAI's own Codex CLI documentation
(developers.openai.com/codex, learn.chatgpt.com/docs/config-file) and
corroborating third-party references before writing any code here, per
ADR-036's own explicit risk (§10.3: "Unverified target agents... must not be
assumed true... mirrors QB-005C's own mandatory pre-flight compatibility
gate"). Two findings from that research materially block a real integration
today:

1. **No confirmed way to rewrite a command.** Codex's `PreToolUse` hook is
   documented as able to "allow or auto-reject" a Bash tool call; nothing in
   OpenAI's own docs confirms a modify-the-input response shape analogous to
   Claude Code's `hookSpecificOutput.updatedInput`. Quor's entire
   `COMMAND_INTERCEPT` model depends on being able to rewrite the command
   Codex is about to run (to route it through `quor <cmd>` for compression)
   — allow/deny alone cannot express that. Shipping a hook that claims to
   support this without confirmation would either silently do nothing or
   emit a response shape Codex ignores; both violate Quor's "deterministic
   only, no heuristics" principle more than shipping no hook at all.
2. **Hooks are experimental with unconfirmed Windows support.** Enabled via
   an opt-in `[features] codex_hooks = true` in `config.toml`; at least one
   third-party reference states hooks are not available on Windows. Quor is
   Windows-first (see `docs/final/CLAUDE.md`) — installing a hook that may
   not run at all on most Quor users' platform is not an acceptable default.

Both are exactly the "unverified target agent" risk ADR-036 flagged as the
single biggest thing a future adapter must independently confirm, not
assume, before implementation. If Codex's hook contract is later confirmed
to support command rewriting on Windows, extending `supported_events` here
is a small, additive, non-breaking change — see `GeminiAdapter` for the
shape it would take.

Built on `DetectionOnlyAdapter` (QB-069) — this module supplies only the
tool-specific parts (identity, the limitation text above, and how to detect
Codex CLI is present); every `AgentAdapter` method is shared, not
reimplemented per adapter. See `quor/adapters/_detection_only.py`'s module
docstring for why five more adapters (QB-069: Cursor, VS Code, Windsurf,
Aider, Continue.dev) independently converged on the same base.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from quor.adapters._detection_only import DetectionOnlyAdapter


def _codex_config_dir() -> Path:
    return Path.home() / ".codex"


class CodexAdapter(DetectionOnlyAdapter):
    """Codex CLI — detection-only. See module docstring for why."""

    agent_id: ClassVar[str] = "codex"
    display_name: ClassVar[str] = "Codex CLI"
    limitation_reason: ClassVar[str] = (
        "Codex's hook system does not yet have a confirmed way to rewrite "
        "commands (allow/deny only) or confirmed Windows support; tracked "
        "pending upstream confirmation."
    )

    def _detect(self) -> tuple[bool, str]:
        config_dir = _codex_config_dir()
        if config_dir.exists():
            return True, f"config directory detected at {config_dir}"
        return False, "not detected (no ~/.codex directory) — install is unaffected either way"
