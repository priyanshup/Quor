"""Base types shared by all hook adapters."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict


class ToolInput(BaseModel):
    """The tool_input object inside a Claude Code PreToolUse hook payload."""

    model_config = ConfigDict(extra="allow", frozen=True)
    command: str = ""


class HookInput(BaseModel):
    """Full Claude Code PreToolUse hook stdin payload."""

    model_config = ConfigDict(extra="allow", frozen=True)
    tool_name: str = ""
    tool_input: ToolInput


class HookSpecificOutput(BaseModel):
    """The `hookSpecificOutput` object Claude Code reads from PreToolUse hook stdout.

    `updatedInput` is the only field Claude Code honors for overriding tool
    arguments — see https://code.claude.com/docs/en/hooks.md. A bare top-level
    `tool_input` (mirroring HookInput's shape) is not part of the protocol and
    is silently ignored.
    """

    model_config = ConfigDict(extra="allow", frozen=True)
    hookEventName: str = "PreToolUse"
    permissionDecision: str = "allow"
    updatedInput: dict[str, Any] | None = None


class HookOutput(BaseModel):
    """Full Claude Code PreToolUse hook stdout payload."""

    model_config = ConfigDict(extra="allow", frozen=True)
    hookSpecificOutput: HookSpecificOutput


@runtime_checkable
class HookAdapter(Protocol):
    """Protocol for hook adapters. Each adapter handles one AI tool's hook format."""

    def run_hook(self) -> None:
        """Read JSON from sys.stdin, write (possibly modified) JSON to sys.stdout."""
        ...


# ---------------------------------------------------------------------------
# PostToolUse / Read (QB-007A) — hook-registration plumbing only.
#
# These models describe the payload shape for quor/adapters/claude_read.py,
# the PostToolUse sibling of the PreToolUse models above. `tool_response`
# carries the file content Read already returned; QB-007A validates the
# shape but never inspects or transforms `tool_response` — that is QB-007B+.
# ---------------------------------------------------------------------------


class ReadToolInput(BaseModel):
    """The tool_input object inside a Claude Code PostToolUse/Read hook payload."""

    model_config = ConfigDict(extra="allow", frozen=True)
    file_path: str = ""


class PostToolUseHookInput(BaseModel):
    """Full Claude Code PostToolUse hook stdin payload for the Read tool."""

    model_config = ConfigDict(extra="allow", frozen=True)
    tool_name: str = ""
    tool_input: ReadToolInput
    tool_response: Any = None


class PostToolUseHookSpecificOutput(BaseModel):
    """The `hookSpecificOutput` object Claude Code reads from PostToolUse hook stdout.

    `updatedToolOutput` is the PostToolUse sibling of `HookSpecificOutput.updatedInput`
    (ADR-030) — the field Claude Code honors for overriding a tool's already-produced
    result. QB-007A never sets it: this phase is intentionally a no-op that always
    passes the original Read output through unchanged, proving the hook-registration/
    roundtrip plumbing before any compression logic exists (QB-007B+).
    """

    model_config = ConfigDict(extra="allow", frozen=True)
    hookEventName: str = "PostToolUse"
    updatedToolOutput: str | None = None


class PostToolUseHookOutput(BaseModel):
    """Full Claude Code PostToolUse hook stdout payload."""

    model_config = ConfigDict(extra="allow", frozen=True)
    hookSpecificOutput: PostToolUseHookSpecificOutput
