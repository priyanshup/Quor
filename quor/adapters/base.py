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
