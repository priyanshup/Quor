"""Continue.dev adapter (QB-069) — detection and readiness reporting only.

Researched against Continue's own `config.yaml` reference
(docs.continue.dev/reference) before writing any code, per ADR-036's
mandatory pre-flight compatibility gate (§10.3). Finding: fetching the
official reference page directly and enumerating every top-level key it
documents (`name`, `version`, `schema`, `models`, `context`, `rules`,
`prompts`, `docs`, `mcpServers`, `data`) found **no `hooks` key and no
mention of any lifecycle-hook or pre/post-tool-use interception mechanism
anywhere in the reference** — the same "no hook system at all" conclusion
as Aider, independently arrived at. Continue's only extension points are
MCP servers (`mcpServers` — tools the agent may *optionally* choose to
call, not a mandatory interception point Quor could route a shell command
through) and `prompts` (reusable slash-command text, not a tool-call gate).
Neither can express `COMMAND_INTERCEPT`/`CONTENT_INTERCEPT`.

Built on `DetectionOnlyAdapter` (see `quor/adapters/_detection_only.py`'s
module docstring for the shared shape this and five other adapters use).
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from quor.adapters._detection_only import DetectionOnlyAdapter


def _continue_config_dir() -> Path:
    # Continue CLI ("cn") and the IDE extensions both read
    # ~/.continue/config.yaml, per Continue's own docs.
    return Path.home() / ".continue"


class ContinueAdapter(DetectionOnlyAdapter):
    """Continue.dev — detection-only. See module docstring for why."""

    agent_id: ClassVar[str] = "continue"
    display_name: ClassVar[str] = "Continue.dev"
    limitation_reason: ClassVar[str] = (
        "Continue's config.yaml has no hooks mechanism — its only extension "
        "points are MCP servers (agent-optional tool calls) and slash-command "
        "prompts, neither of which can intercept or rewrite a tool call the "
        "way Quor's compression model requires."
    )

    def _detect(self) -> tuple[bool, str]:
        config_dir = _continue_config_dir()
        if config_dir.exists():
            return True, f"config directory detected at {config_dir}"
        return (
            False,
            "not detected (no ~/.continue directory) — install is unaffected either way",
        )
