"""Base types shared by all hook adapters."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

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


class HookOutput(BaseModel):
    """Hook stdout payload (same shape as HookInput, command possibly rewritten)."""

    model_config = ConfigDict(extra="allow", frozen=True)
    tool_name: str = ""
    tool_input: ToolInput


@runtime_checkable
class HookAdapter(Protocol):
    """Protocol for hook adapters. Each adapter handles one AI tool's hook format."""

    def run_hook(self) -> None:
        """Read JSON from sys.stdin, write (possibly modified) JSON to sys.stdout."""
        ...
