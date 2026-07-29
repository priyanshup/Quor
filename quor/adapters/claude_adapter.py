"""ClaudeAdapter — Claude Code's AgentAdapter (QB-035B).

A thin wrapper: `handle_event()` delegates straight to the existing
`quor.adapters.claude.handle_bytes()` / `quor.adapters.claude_read.
handle_bytes()` functions, and `install()`/`doctor_checks()` delegate to the
existing `init.py`/`doctor.py` machinery (`HOOK_SPECS`, `render_hook_script`,
the `_check_*` helpers). No compression/hook logic is reimplemented here —
this class only adapts today's already-correct, already-tested behavior to
the `AgentAdapter` Protocol shape (ADR-036). Byte-for-byte equivalence with
pre-QB-035B behavior is the explicit goal, not a rewrite.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from quor.adapters import claude, claude_read
from quor.adapters.base import (
    QUOR_ADAPTER_API_VERSION,
    AgentEvent,
    DoctorCheck,
    DoctorContext,
    InstallContext,
    InstallResult,
)
from quor.adapters.hook_manifest import HOOK_SPECS

if TYPE_CHECKING:
    from quor.tracking.db import TrackingDB

_SUPPORTED_EVENTS = frozenset({AgentEvent.COMMAND_INTERCEPT, AgentEvent.CONTENT_INTERCEPT})


class ClaudeAdapter:
    """Claude Code — both existing hooks (PreToolUse/Bash, PostToolUse/Read),
    verified and shipped since v0.1/v0.4.x. See module docstring."""

    agent_id: ClassVar[str] = "claude"
    display_name: ClassVar[str] = "Claude Code"
    api_version: ClassVar[int] = QUOR_ADAPTER_API_VERSION

    @property
    def supported_events(self) -> frozenset[AgentEvent]:
        return _SUPPORTED_EVENTS

    def handle_event(
        self, event: AgentEvent, raw_stdin: bytes, tracking: TrackingDB | None
    ) -> bytes | None:
        if event is AgentEvent.COMMAND_INTERCEPT:
            return claude.handle_bytes(raw_stdin)
        if event is AgentEvent.CONTENT_INTERCEPT:
            return claude_read.handle_bytes(raw_stdin, tracking=tracking)
        return None

    def install(self, ctx: InstallContext) -> InstallResult:
        # Delegates to init.py's existing, unchanged Claude-specific install
        # flow (dry-run preview, conflict detection, confirmation, atomic
        # writes) — see quor/cli/commands/init.py's _install_claude().
        from quor.cli.commands.init import _install_claude

        return _install_claude(ctx)

    def doctor_checks(self, ctx: DoctorContext) -> list[DoctorCheck]:
        # Delegates to doctor.py's existing, unchanged per-hook check
        # functions — same names, same order, same detail strings as before
        # QB-035D moved the loop up here.
        from quor.cli.commands.doctor import (
            _check_hook_collision,
            _check_hook_registered,
            _check_hook_script,
            _check_hook_up_to_date,
            _run_roundtrip_check,
        )

        checks: list[DoctorCheck] = []
        for spec in HOOK_SPECS:
            checks.append(_check_hook_script(spec))
            checks.append(_check_hook_registered(spec, ctx.settings_override))
            checks.append(_check_hook_up_to_date(spec))
            roundtrip_result = _run_roundtrip_check(spec.hook_id)
            if roundtrip_result is not None:
                checks.append(roundtrip_result)
        checks.append(_check_hook_collision(ctx.settings_override))
        return checks
