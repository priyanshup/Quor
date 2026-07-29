"""Minimal NoOpTestAdapter for Quor adapter registry integration tests.

Demonstrates the smallest possible third-party `quor.hook_adapter` entry
point — see docs/final/ADAPTERS.md "Adding a new adapter" for the full
walkthrough this fixture exists to prove works end to end, not just under
a monkeypatched `importlib.metadata.entry_points()`.
"""

from __future__ import annotations

from typing import ClassVar

from quor.adapters.base import AgentEvent, DoctorCheck, DoctorContext, InstallContext, InstallResult
from quor.tracking.db import TrackingDB


class NoOpTestAdapter:
    """Declares no supported events and does nothing. For testing adapter
    discovery only — mirrors quor_test_stage.stage.NoOpTestStage."""

    agent_id: ClassVar[str] = "noop_test"
    display_name: ClassVar[str] = "No-Op Test Adapter"
    api_version: ClassVar[int] = 1

    @property
    def supported_events(self) -> frozenset[AgentEvent]:
        return frozenset()

    def handle_event(
        self, event: AgentEvent, raw_stdin: bytes, tracking: TrackingDB | None
    ) -> bytes | None:
        return None

    def install(self, ctx: InstallContext) -> InstallResult:
        return InstallResult()

    def doctor_checks(self, ctx: DoctorContext) -> list[DoctorCheck]:
        return []
