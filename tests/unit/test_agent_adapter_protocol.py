"""Shared AgentAdapter conformance tests (QB-068, extended QB-069).

One parametrized suite exercises every built-in adapter against the
*generic* AgentAdapter contract — mirrors `TestStageHandlerProtocol`
(tests/unit/test_pipeline.py) and `test_plugin_loader.py`'s structural
style. Adapter-*specific* behavior (Claude's byte-for-byte equivalence,
Gemini's command-rewrite roundtrip, each detection-only adapter's own
`_detect()` logic) is intentionally NOT re-tested here — see
`test_claude_adapter_equivalence.py`, `test_gemini_adapter.py`, and one
`test_<agent>_adapter.py` per detection-only adapter — this file only
proves every adapter satisfies the same shape and the same fail-open
discipline, once, instead of once per adapter. QB-069 added five more
detection-only adapters (Cursor, VS Code, Windsurf, Aider, Continue.dev) —
their shared `DetectionOnlyAdapter` base means this parametrization is what
actually proves them all conform, not five near-duplicate test files.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from quor.adapters.aider_adapter import AiderAdapter
from quor.adapters.base import (
    AgentAdapter,
    AgentEvent,
    DoctorContext,
    InstallContext,
    InstallResult,
)
from quor.adapters.claude_adapter import ClaudeAdapter
from quor.adapters.codex_adapter import CodexAdapter
from quor.adapters.continue_adapter import ContinueAdapter
from quor.adapters.cursor_adapter import CursorAdapter
from quor.adapters.gemini_adapter import GeminiAdapter
from quor.adapters.vscode_adapter import VSCodeAdapter
from quor.adapters.windsurf_adapter import WindsurfAdapter

ALL_BUILTIN_ADAPTER_CLASSES = (
    ClaudeAdapter,
    CodexAdapter,
    GeminiAdapter,
    CursorAdapter,
    VSCodeAdapter,
    WindsurfAdapter,
    AiderAdapter,
    ContinueAdapter,
)

DETECTION_ONLY_ADAPTER_CLASSES = (
    CodexAdapter,
    CursorAdapter,
    VSCodeAdapter,
    WindsurfAdapter,
    AiderAdapter,
    ContinueAdapter,
)


@pytest.fixture(params=ALL_BUILTIN_ADAPTER_CLASSES, ids=lambda cls: cls.agent_id)
def adapter(request: pytest.FixtureRequest) -> AgentAdapter:
    return request.param()  # type: ignore[no-any-return]


class TestAgentAdapterProtocolConformance:
    def test_satisfies_protocol(self, adapter: AgentAdapter) -> None:
        assert isinstance(adapter, AgentAdapter)

    def test_class_attributes_present_and_typed(self, adapter: AgentAdapter) -> None:
        assert isinstance(adapter.agent_id, str) and adapter.agent_id
        assert isinstance(adapter.display_name, str) and adapter.display_name
        assert isinstance(adapter.api_version, int) and adapter.api_version >= 1

    def test_supported_events_is_a_frozenset_of_agent_event(self, adapter: AgentAdapter) -> None:
        events = adapter.supported_events
        assert isinstance(events, frozenset)
        assert all(isinstance(e, AgentEvent) for e in events)

    def test_handle_event_returns_none_for_unsupported_event(self, adapter: AgentAdapter) -> None:
        unsupported = next(
            (e for e in AgentEvent if e not in adapter.supported_events), None
        )
        if unsupported is None:
            pytest.skip(f"{adapter.agent_id} supports every AgentEvent — nothing unsupported to test")
        assert adapter.handle_event(unsupported, b"{}", None) is None

    def test_doctor_checks_returns_well_shaped_tuples(self, adapter: AgentAdapter) -> None:
        checks = adapter.doctor_checks(DoctorContext(settings_override=None))
        assert isinstance(checks, list)
        for name, ok, detail in checks:  # unpacking asserts arity == 3
            assert isinstance(name, str) and name
            assert isinstance(ok, bool)
            assert isinstance(detail, str)

    def test_doctor_checks_never_raises_with_a_bogus_settings_override(
        self, adapter: AgentAdapter, tmp_path: Path
    ) -> None:
        """A settings file that doesn't exist, or points at a directory, must
        not crash `quor doctor` for any adapter — fail-open per ADR-036 §7."""
        bogus = tmp_path / "does-not-exist" / "settings.json"
        checks = adapter.doctor_checks(DoctorContext(settings_override=bogus))
        assert isinstance(checks, list)

    def test_install_returns_install_result_without_raising(self, adapter: AgentAdapter) -> None:
        with tempfile.TemporaryDirectory() as td:
            settings_path = Path(td) / "settings.json"
            result = adapter.install(InstallContext(settings_override=settings_path, yes=True))
        assert isinstance(result, InstallResult)
        assert isinstance(result.installed_paths, tuple)
        assert isinstance(result.warnings, tuple)

    def test_handle_event_on_garbage_bytes_either_raises_or_returns_bytes_or_none(
        self, adapter: AgentAdapter
    ) -> None:
        """Not a fail-open guarantee at the adapter level (that's
        `__main__._run_hook()`'s job, per ADR-036 §3.3/§7) — just proves no
        adapter returns a nonsense type for malformed input, for every
        event it claims to support."""
        for event in adapter.supported_events:
            try:
                result = adapter.handle_event(event, b"not json at all", None)
            except Exception:  # noqa: BLE001 — allowed; caller's outer guard handles it
                continue
            assert result is None or isinstance(result, bytes)


class TestAdapterIdentityIsUniqueAcrossBuiltins:
    def test_agent_ids_are_unique(self) -> None:
        ids = [cls.agent_id for cls in ALL_BUILTIN_ADAPTER_CLASSES]
        assert len(ids) == len(set(ids))


@pytest.fixture(params=DETECTION_ONLY_ADAPTER_CLASSES, ids=lambda cls: cls.agent_id)
def detection_only_adapter(request: pytest.FixtureRequest) -> AgentAdapter:
    return request.param()  # type: ignore[no-any-return]


class TestDetectionOnlyAdapterSharedContract:
    """Proves the `DetectionOnlyAdapter` base (quor/adapters/_detection_only.py)
    behaves identically across every adapter built on it — this is the
    actual "conformance test" for the QB-069 pattern, not six near-duplicate
    per-adapter test files. Each adapter's own test file only needs to cover
    its `_detect()`/`limitation_reason` specifics."""

    def test_supports_no_events(self, detection_only_adapter: AgentAdapter) -> None:
        assert detection_only_adapter.supported_events == frozenset()

    def test_handle_event_always_returns_none(self, detection_only_adapter: AgentAdapter) -> None:
        for event in AgentEvent:
            assert detection_only_adapter.handle_event(event, b"{}", None) is None

    def test_install_writes_nothing_and_warns_with_own_limitation_reason(
        self, detection_only_adapter: AgentAdapter, tmp_path: Path
    ) -> None:
        result = detection_only_adapter.install(
            InstallContext(settings_override=tmp_path / "x", yes=True)
        )
        assert result.installed_paths == ()
        assert len(result.warnings) == 1
        assert detection_only_adapter.limitation_reason in result.warnings[0]  # type: ignore[attr-defined]

    def test_doctor_checks_are_exactly_two_and_always_advisory(
        self, detection_only_adapter: AgentAdapter
    ) -> None:
        checks = detection_only_adapter.doctor_checks(DoctorContext(settings_override=None))
        assert len(checks) == 2
        assert all(ok is True for _, ok, _ in checks)
        names = {name for name, _, _ in checks}
        assert names == {
            f"{detection_only_adapter.display_name} detected",
            f"{detection_only_adapter.display_name} hook integration",
        }

    def test_hook_integration_detail_includes_own_limitation_reason(
        self, detection_only_adapter: AgentAdapter
    ) -> None:
        checks = detection_only_adapter.doctor_checks(DoctorContext(settings_override=None))
        detail = next(
            detail
            for name, _, detail in checks
            if name == f"{detection_only_adapter.display_name} hook integration"
        )
        assert detection_only_adapter.limitation_reason in detail  # type: ignore[attr-defined]
