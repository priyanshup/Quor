"""Tests for quor.adapters.registry.AdapterRegistry (QB-035B/QB-068/QB-069).

Mirrors tests/unit/test_plugin_loader.py's structural style for the
`quor.compression_stage`/`quor.plugin` entry-point groups, applied to the
new `quor.hook_adapter` group: built-in dict lookup, entry-point discovery,
Protocol validation, api_version rejection, fail-open per broken third-party
adapter, and built-in-shadows-third-party priority.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

import pytest

from quor.adapters.aider_adapter import AiderAdapter
from quor.adapters.base import AgentEvent, DoctorContext, InstallContext, InstallResult
from quor.adapters.claude_adapter import ClaudeAdapter
from quor.adapters.codex_adapter import CodexAdapter
from quor.adapters.continue_adapter import ContinueAdapter
from quor.adapters.cursor_adapter import CursorAdapter
from quor.adapters.gemini_adapter import GeminiAdapter
from quor.adapters.registry import AdapterRegistry, discover_adapter_entry_points
from quor.adapters.vscode_adapter import VSCodeAdapter
from quor.adapters.windsurf_adapter import WindsurfAdapter

_THIS_MODULE = __name__


@dataclass
class _FakeEntryPoint:
    name: str
    value: str


class _GoodAdapter:
    """Minimal valid AgentAdapter for testing."""

    agent_id: ClassVar[str] = "fake"
    display_name: ClassVar[str] = "Fake Agent"
    api_version: ClassVar[int] = 1

    @property
    def supported_events(self) -> frozenset[AgentEvent]:
        return frozenset()

    def handle_event(self, event: AgentEvent, raw_stdin: bytes, tracking: object) -> bytes | None:
        return None

    def install(self, ctx: InstallContext) -> InstallResult:
        return InstallResult()

    def doctor_checks(self, ctx: DoctorContext) -> list[tuple[str, bool, str]]:
        return []


class _BadApiVersionAdapter(_GoodAdapter):
    agent_id: ClassVar[str] = "bad_version"
    api_version: ClassVar[int] = 999


class _MissingAgentIdAdapter:
    """Satisfies AgentAdapter structurally but has no real agent_id value."""

    agent_id: ClassVar[str] = ""
    display_name: ClassVar[str] = "No ID"
    api_version: ClassVar[int] = 1

    @property
    def supported_events(self) -> frozenset[AgentEvent]:
        return frozenset()

    def handle_event(self, event: AgentEvent, raw_stdin: bytes, tracking: object) -> bytes | None:
        return None

    def install(self, ctx: InstallContext) -> InstallResult:
        return InstallResult()

    def doctor_checks(self, ctx: DoctorContext) -> list[tuple[str, bool, str]]:
        return []


class _NotAnAdapter:
    """Does not satisfy the AgentAdapter Protocol at all."""


class _ImposterClaudeAdapter(_GoodAdapter):
    """Declares the same agent_id as the real ClaudeAdapter — used to prove
    built-ins always win over a same-named third-party entry point."""

    agent_id: ClassVar[str] = "claude"


class _ExplodingAdapter:
    agent_id: ClassVar[str] = "explode"
    display_name: ClassVar[str] = "Explode"
    api_version: ClassVar[int] = 1

    def __init__(self) -> None:
        raise RuntimeError("boom")


class TestBuiltinAdapters:
    def test_all_builtins_discoverable_by_id(self) -> None:
        registry = AdapterRegistry()
        assert isinstance(registry.find("claude"), ClaudeAdapter)
        assert isinstance(registry.find("codex"), CodexAdapter)
        assert isinstance(registry.find("gemini"), GeminiAdapter)
        assert isinstance(registry.find("cursor"), CursorAdapter)
        assert isinstance(registry.find("vscode"), VSCodeAdapter)
        assert isinstance(registry.find("windsurf"), WindsurfAdapter)
        assert isinstance(registry.find("aider"), AiderAdapter)
        assert isinstance(registry.find("continue"), ContinueAdapter)

    def test_unknown_agent_returns_none(self) -> None:
        registry = AdapterRegistry()
        assert registry.find("does-not-exist") is None

    def test_all_adapters_includes_every_builtin(self) -> None:
        # .issubset(), not ==: a real third-party quor.hook_adapter package
        # (e.g. this repo's own tests/fixtures/test_adapter, once installed
        # per CONTRIBUTING.md) is legitimately also present in
        # all_adapters() here — this test isn't isolated from real
        # entry-point discovery the way the monkeypatched tests above are.
        registry = AdapterRegistry()
        ids = {a.agent_id for a in registry.all_adapters()}
        assert {
            "claude",
            "codex",
            "gemini",
            "cursor",
            "vscode",
            "windsurf",
            "aider",
            "continue",
        }.issubset(ids)

    def test_no_failures_when_no_third_party_entry_points(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("importlib.metadata.entry_points", lambda group: [])
        registry = AdapterRegistry()
        registry.all_adapters()
        assert registry.failures == []


class TestEntryPointDiscovery:
    def test_returns_empty_when_no_entry_points(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("importlib.metadata.entry_points", lambda group: [])
        adapters, failures = discover_adapter_entry_points()
        assert adapters == {}
        assert failures == []

    def test_discovers_valid_third_party_adapter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ep = _FakeEntryPoint("good", f"{_THIS_MODULE}:_GoodAdapter")
        monkeypatch.setattr("importlib.metadata.entry_points", lambda group: [ep])
        adapters, failures = discover_adapter_entry_points()
        assert not failures
        assert adapters["fake"] is _GoodAdapter

    def test_skips_bad_api_version_with_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ep = _FakeEntryPoint("bad", f"{_THIS_MODULE}:_BadApiVersionAdapter")
        monkeypatch.setattr("importlib.metadata.entry_points", lambda group: [ep])
        with pytest.warns(UserWarning, match="skipped"):
            adapters, failures = discover_adapter_entry_points()
        assert "bad_version" not in adapters
        assert len(failures) == 1
        assert "api_version" in failures[0].reason

    def test_skips_non_protocol_class_with_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ep = _FakeEntryPoint("not_adapter", f"{_THIS_MODULE}:_NotAnAdapter")
        monkeypatch.setattr("importlib.metadata.entry_points", lambda group: [ep])
        with pytest.warns(UserWarning, match="skipped"):
            adapters, failures = discover_adapter_entry_points()
        assert adapters == {}
        assert "does not satisfy AgentAdapter Protocol" in failures[0].reason

    def test_skips_missing_agent_id_with_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ep = _FakeEntryPoint("no_id", f"{_THIS_MODULE}:_MissingAgentIdAdapter")
        monkeypatch.setattr("importlib.metadata.entry_points", lambda group: [ep])
        adapters, failures = discover_adapter_entry_points()
        assert adapters == {}
        assert len(failures) == 1
        assert "agent_id" in failures[0].reason

    def test_skips_bad_import_with_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ep = _FakeEntryPoint("missing", "no.such.module:Cls")
        monkeypatch.setattr("importlib.metadata.entry_points", lambda group: [ep])
        with pytest.warns(UserWarning, match="skipped"):
            adapters, failures = discover_adapter_entry_points()
        assert adapters == {}
        assert failures[0].entry_point_name == "missing"

    def test_instantiation_error_becomes_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ep = _FakeEntryPoint("explode", f"{_THIS_MODULE}:_ExplodingAdapter")
        monkeypatch.setattr("importlib.metadata.entry_points", lambda group: [ep])
        adapters, failures = discover_adapter_entry_points()
        assert adapters == {}
        assert "instantiation error" in failures[0].reason

    def test_entry_point_scan_failure_is_fail_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(group: str) -> None:
            raise RuntimeError("scan exploded")

        monkeypatch.setattr("importlib.metadata.entry_points", _raise)
        with pytest.warns(UserWarning, match="scan failed"):
            adapters, failures = discover_adapter_entry_points()
        assert adapters == {}
        assert failures == []


class TestRegistryPartialFailureIsolation:
    def test_one_broken_third_party_adapter_does_not_break_builtins(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ep = _FakeEntryPoint("bad", f"{_THIS_MODULE}:_BadApiVersionAdapter")
        monkeypatch.setattr("importlib.metadata.entry_points", lambda group: [ep])
        registry = AdapterRegistry()
        with pytest.warns(UserWarning):
            all_adapters = registry.all_adapters()
        ids = {a.agent_id for a in all_adapters}
        assert {"claude", "codex", "gemini"}.issubset(ids)
        assert len(registry.failures) == 1

    def test_builtin_agent_id_is_never_shadowed_by_third_party(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A third-party package declaring agent_id="claude" must not
        replace Quor's own reference ClaudeAdapter — built-ins win."""
        ep = _FakeEntryPoint("imposter", f"{_THIS_MODULE}:_ImposterClaudeAdapter")
        monkeypatch.setattr("importlib.metadata.entry_points", lambda group: [ep])
        registry = AdapterRegistry()
        assert isinstance(registry.find("claude"), ClaudeAdapter)
