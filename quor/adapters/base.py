"""Base types shared by all hook adapters."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from quor.tracking.db import TrackingDB


QUOR_ADAPTER_API_VERSION: int = 1
"""Current `AgentAdapter` API version — mirrors
`quor.plugins.base.QUOR_PLUGIN_API_VERSION`'s contract exactly. An adapter
declaring a newer major version than this is rejected at discovery time."""


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


# ---------------------------------------------------------------------------
# Multi-agent adapter architecture (QB-035B, implementing ADR-036 / the
# design in docs/design/QB-035A-multi-agent-adapter-design.md).
#
# Supersedes the HookAdapter Protocol that used to live here
# (`run_hook(self) -> None`, direct sys.stdin/sys.stdout I/O) — that Protocol
# had zero implementers and zero references anywhere else in the codebase;
# see ADR-036 for why bytes-in/bytes-out replaces it rather than extending
# it: it makes every adapter trivially unit-testable, and moves stream I/O
# to exactly one place (`__main__._run_hook()`), which already owns it today.
# ---------------------------------------------------------------------------


class AgentEvent(StrEnum):
    """An abstract hook-event kind, decoupled from any one agent's own event
    names. Deliberately a closed, minimal set of two — see ADR-036 for why
    an open/free-text event system was rejected."""

    COMMAND_INTERCEPT = "command_intercept"
    """Before a shell command runs, the adapter may rewrite it.
    Maps to Claude Code's PreToolUse/Bash today."""

    CONTENT_INTERCEPT = "content_intercept"
    """After a tool already produced content (e.g. a file read), the
    adapter may replace it before the agent's model sees it.
    Maps to Claude Code's PostToolUse/Read today."""


DoctorCheck = tuple[str, bool, str]
"""(name, ok, detail) — the exact shape `quor/cli/commands/doctor.py`'s
`_check_*` functions already return today, named for reuse at the new
`AgentAdapter.doctor_checks()` call sites."""


@dataclass(frozen=True, kw_only=True)
class InstallContext:
    """Context passed to `AgentAdapter.install()`. Mirrors `PluginContext`'s
    documented rationale: intentionally lean, not coupled to Quor's internal
    implementation. Always construct with keyword arguments."""

    settings_override: Path | None = None
    """Override the agent's own config-file path — a test hook, mirrors
    `init.py`'s existing `--settings-path`."""

    yes: bool = False
    """Skip any interactive confirmation prompt — mirrors `init.py`'s
    existing `--yes`/`-y`."""


@dataclass(frozen=True, kw_only=True)
class InstallResult:
    """What `AgentAdapter.install()` returns."""

    installed_paths: tuple[Path, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class DoctorContext:
    """Context passed to `AgentAdapter.doctor_checks()`."""

    settings_override: Path | None = None
    """Override the agent's own config-file path — mirrors `doctor.py`'s
    existing `--settings-path`."""


@runtime_checkable
class AgentAdapter(Protocol):
    """One AI coding agent's integration with Quor. Implemented once per
    agent (Claude Code, Codex CLI, Gemini CLI, ...).

    Lifecycle — three distinct timeframes (see the design doc's §4 for the
    full rationale):
      1. Install-time (`quor init --agent X`) — one-shot, interactive,
         side-effecting.
      2. Event-time (`quor hook X <event>`) — the hot path. Every real
         invocation is a brand-new OS process spawned by the agent itself,
         so `handle_event()` must be a pure, stateless, single-shot call.
      3. Diagnostic-time (`quor doctor`) — synchronous, read-only, in the
         CLI's own process.

    Every method must not raise for *expected* failure modes (malformed
    payload, missing optional dependency, an unsupported event, ...) — an
    adapter's own fail-open responsibility. An unexpected exception from
    `handle_event()` is still caught by `__main__._run_hook()`'s existing
    outer guard, which writes back the original bytes unchanged.
    """

    agent_id: ClassVar[str]
    """Stable identifier: "claude", "codex", "gemini", ... Used in
    `quor hook <agent_id> <event>` routing and `quor init --agent
    <agent_id>`."""

    display_name: ClassVar[str]
    """Human-readable name shown in `quor doctor`/`quor init` output."""

    api_version: ClassVar[int]
    """Mirrors `QUOR_PLUGIN_API_VERSION`'s contract: adapters declaring a
    newer major version than Quor's own are rejected at discovery time."""

    @property
    def supported_events(self) -> frozenset[AgentEvent]:
        """Which `AgentEvent` kinds this agent's hook model actually,
        verifiably exposes. Drives both `quor init` (which hooks to install)
        and `quor doctor` (which roundtrip checks are meaningful to run).
        An adapter must not claim an event it cannot verifiably honor —
        see each built-in adapter's own module docstring for what's
        verified and what's deliberately left out pending confirmation."""
        ...

    def handle_event(
        self, event: AgentEvent, raw_stdin: bytes, tracking: TrackingDB | None
    ) -> bytes | None:
        """Process one hook invocation for `event`. `raw_stdin` is the
        untouched original payload bytes (BOM and all — stripping is each
        adapter's own concern). Returns the raw response bytes to write to
        stdout, or `None` if `event` is not in `supported_events` (treated
        as "not handled")."""
        ...

    def install(self, ctx: InstallContext) -> InstallResult:
        """Write this agent's hook script(s) and register them with the
        agent's own config mechanism."""
        ...

    def doctor_checks(self, ctx: DoctorContext) -> list[DoctorCheck]:
        """Return this adapter's own health checks."""
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
