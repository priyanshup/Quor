"""Tests for quor/plugins/base.py and quor/plugins/registry.py.

Tests are organized by contract, not by implementation detail:
  - Protocol conformance (what satisfies Plugin)
  - PluginPayload helpers (with_annotation, replace_output)
  - PluginRegistry registration and precedence
  - PluginRegistry lifecycle (initialize_all, shutdown_all)
  - PluginRegistry execution (run_pipeline, fail-open, abort)
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import ClassVar

import pytest

from quor.plugins import (
    CAPABILITY_CONTENT_TRANSFORM,
    CAPABILITY_POLICY,
    CAPABILITY_READ_ONLY,
    CAPABILITY_TELEMETRY,
    QUOR_PLUGIN_API_VERSION,
    ExecutionMode,
    Plugin,
    PluginCategory,
    PluginContext,
    PluginError,
    PluginMetadata,
    PluginPayload,
    PluginResult,
)
from quor.plugins.registry import PluginRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(**kwargs: object) -> PluginContext:
    defaults: dict[str, object] = {
        "project_root": None,
        "mode": ExecutionMode.OPTIMIZE,
        "session_id": "",
        "invocation_id": "",
    }
    defaults.update(kwargs)
    return PluginContext(**defaults)  # type: ignore[arg-type]


def _make_payload(**kwargs: object) -> PluginPayload:
    defaults: dict[str, object] = {
        "command": "git status",
        "raw_output": "raw",
        "current_output": "current",
        "content_type": "text",
    }
    defaults.update(kwargs)
    return PluginPayload(**defaults)  # type: ignore[arg-type]


def _make_metadata(
    plugin_id: str = "com.test.noop",
    category: PluginCategory = PluginCategory.POST_FILTER,
    priority: int = 100,
    capabilities: tuple[str, ...] = (),
) -> PluginMetadata:
    return PluginMetadata(
        plugin_id=plugin_id,
        display_name="Test Plugin",
        version="1.0.0",
        category=category,
        priority=priority,
        capabilities=capabilities,
    )


class _NoOpPlugin:
    """Minimal valid plugin used throughout the test suite."""

    api_version: ClassVar[int] = QUOR_PLUGIN_API_VERSION

    def __init__(
        self,
        plugin_id: str = "com.test.noop",
        category: PluginCategory = PluginCategory.POST_FILTER,
        priority: int = 100,
        capabilities: tuple[str, ...] = (),
    ) -> None:
        self._meta = _make_metadata(
            plugin_id=plugin_id,
            category=category,
            priority=priority,
            capabilities=capabilities,
        )

    @property
    def metadata(self) -> PluginMetadata:
        return self._meta

    def initialize(self, ctx: PluginContext) -> None:
        pass

    def execute(self, payload: PluginPayload, ctx: PluginContext) -> PluginResult:
        return PluginResult(payload=payload)

    def shutdown(self) -> None:
        pass


# ---------------------------------------------------------------------------
# 1. Protocol conformance
# ---------------------------------------------------------------------------


class TestPluginProtocol:
    def test_noop_plugin_satisfies_protocol(self) -> None:
        assert isinstance(_NoOpPlugin(), Plugin)

    def test_object_missing_execute_does_not_satisfy_protocol(self) -> None:
        class Bad:
            api_version: ClassVar[int] = QUOR_PLUGIN_API_VERSION

            @property
            def metadata(self) -> PluginMetadata:
                return _make_metadata()

            def initialize(self, ctx: PluginContext) -> None:
                pass

            def shutdown(self) -> None:
                pass

        assert not isinstance(Bad(), Plugin)

    def test_object_missing_api_version_does_not_satisfy_protocol(self) -> None:
        class Bad:
            @property
            def metadata(self) -> PluginMetadata:
                return _make_metadata()

            def initialize(self, ctx: PluginContext) -> None:
                pass

            def execute(self, payload: PluginPayload, ctx: PluginContext) -> PluginResult:
                return PluginResult(payload=payload)

            def shutdown(self) -> None:
                pass

        assert not isinstance(Bad(), Plugin)

    def test_api_version_constant_is_1(self) -> None:
        assert QUOR_PLUGIN_API_VERSION == 1


# ---------------------------------------------------------------------------
# 2. PluginPayload helpers
# ---------------------------------------------------------------------------


class TestPluginPayload:
    def test_with_annotation_adds_key(self) -> None:
        p = _make_payload()
        p2 = p.with_annotation("com.test.key", "value")
        assert p2.annotations["com.test.key"] == "value"

    def test_with_annotation_preserves_existing(self) -> None:
        p = _make_payload()
        p = p.with_annotation("a", 1)
        p = p.with_annotation("b", 2)
        assert p.annotations == {"a": 1, "b": 2}

    def test_with_annotation_does_not_mutate_original(self) -> None:
        p = _make_payload()
        p.with_annotation("x", 99)
        assert "x" not in p.annotations

    def test_with_annotation_overwrites_existing_key(self) -> None:
        p = _make_payload()
        p = p.with_annotation("k", "old").with_annotation("k", "new")
        assert p.annotations["k"] == "new"

    def test_replace_output_changes_current_output_only(self) -> None:
        p = _make_payload(raw_output="raw", current_output="old")
        p2 = p.replace_output("new")
        assert p2.current_output == "new"
        assert p2.raw_output == "raw"

    def test_replace_output_does_not_mutate_original(self) -> None:
        p = _make_payload(current_output="original")
        p.replace_output("changed")
        assert p.current_output == "original"


# ---------------------------------------------------------------------------
# 3. PluginRegistry — registration and validation
# ---------------------------------------------------------------------------


class TestRegistryRegistration:
    def test_register_valid_plugin_succeeds(self) -> None:
        reg = PluginRegistry()
        reg.register(_NoOpPlugin())
        assert reg.count()["user"] == 1

    def test_register_to_builtin_tier(self) -> None:
        reg = PluginRegistry()
        reg.register(_NoOpPlugin(), tier="builtin")
        assert reg.count()["builtin"] == 1
        assert reg.count()["user"] == 0

    def test_register_non_plugin_raises(self) -> None:
        reg = PluginRegistry()
        with pytest.raises(PluginError, match="Plugin Protocol"):
            reg.register(object())  # type: ignore[arg-type]

    def test_register_wrong_api_version_raises(self) -> None:
        class WrongVersion:
            api_version: ClassVar[int] = 999

            @property
            def metadata(self) -> PluginMetadata:
                return _make_metadata()

            def initialize(self, ctx: PluginContext) -> None:
                pass

            def execute(self, payload: PluginPayload, ctx: PluginContext) -> PluginResult:
                return PluginResult(payload=payload)

            def shutdown(self) -> None:
                pass

        reg = PluginRegistry()
        with pytest.raises(PluginError, match="api_version"):
            reg.register(WrongVersion())

    def test_duplicate_id_same_tier_warns_and_replaces(self) -> None:
        reg = PluginRegistry()
        reg.register(_NoOpPlugin("com.test.a"))
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            reg.register(_NoOpPlugin("com.test.a"))
        assert any("already registered" in str(x.message) for x in w)
        assert reg.count()["user"] == 1

    def test_same_id_higher_tier_warns_lower(self) -> None:
        reg = PluginRegistry()
        reg.register(_NoOpPlugin("com.test.a"), tier="project")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            reg.register(_NoOpPlugin("com.test.a"), tier="user")
        assert any("higher priority" in str(x.message) for x in w)

    def test_unregister_removes_plugin(self) -> None:
        reg = PluginRegistry()
        reg.register(_NoOpPlugin("com.test.x"))
        removed = reg.unregister("com.test.x")
        assert removed is True
        assert reg.count()["user"] == 0

    def test_unregister_missing_id_returns_false(self) -> None:
        reg = PluginRegistry()
        assert reg.unregister("com.test.nonexistent") is False


# ---------------------------------------------------------------------------
# 4. PluginRegistry — lookup order
# ---------------------------------------------------------------------------


class TestRegistryLookup:
    def test_all_plugins_empty_when_no_registrations(self) -> None:
        assert PluginRegistry().all_plugins() == []

    def test_plugins_for_category_returns_only_matching(self) -> None:
        reg = PluginRegistry()
        reg.register(_NoOpPlugin("com.test.pre", category=PluginCategory.PRE_FILTER))
        reg.register(_NoOpPlugin("com.test.post", category=PluginCategory.POST_FILTER))
        pre = reg.plugins_for_category(PluginCategory.PRE_FILTER)
        assert len(pre) == 1
        assert pre[0].metadata.plugin_id == "com.test.pre"

    def test_execution_order_is_pre_filter_post(self) -> None:
        reg = PluginRegistry()
        reg.register(_NoOpPlugin("com.test.post", category=PluginCategory.POST_FILTER))
        reg.register(_NoOpPlugin("com.test.filter", category=PluginCategory.FILTER))
        reg.register(_NoOpPlugin("com.test.pre", category=PluginCategory.PRE_FILTER))
        order = [p.metadata.plugin_id for p in reg.all_plugins()]
        assert order.index("com.test.pre") < order.index("com.test.filter")
        assert order.index("com.test.filter") < order.index("com.test.post")

    def test_priority_within_category(self) -> None:
        reg = PluginRegistry()
        reg.register(_NoOpPlugin("com.test.low", priority=200))
        reg.register(_NoOpPlugin("com.test.high", priority=10))
        order = [p.metadata.plugin_id for p in reg.all_plugins()]
        assert order.index("com.test.high") < order.index("com.test.low")

    def test_project_tier_runs_before_user(self) -> None:
        reg = PluginRegistry()
        reg.register(_NoOpPlugin("com.test.user-a"), tier="user")
        reg.register(_NoOpPlugin("com.test.proj-a"), tier="project")
        order = [p.metadata.plugin_id for p in reg.all_plugins()]
        assert order.index("com.test.proj-a") < order.index("com.test.user-a")

    def test_all_three_tiers_ordered(self) -> None:
        reg = PluginRegistry()
        reg.register(_NoOpPlugin("com.test.builtin"), tier="builtin")
        reg.register(_NoOpPlugin("com.test.user"), tier="user")
        reg.register(_NoOpPlugin("com.test.project"), tier="project")
        order = [p.metadata.plugin_id for p in reg.all_plugins()]
        assert order.index("com.test.project") < order.index("com.test.user")
        assert order.index("com.test.user") < order.index("com.test.builtin")

    def test_stable_ordering_equal_priorities(self) -> None:
        reg = PluginRegistry()
        ids = [f"com.test.equal-{i}" for i in range(5)]
        for pid in ids:
            reg.register(_NoOpPlugin(pid, priority=50))
        order = [p.metadata.plugin_id for p in reg.all_plugins()]
        for i in range(len(ids) - 1):
            assert order.index(ids[i]) < order.index(ids[i + 1])

    def test_get_returns_plugin_by_id(self) -> None:
        reg = PluginRegistry()
        p = _NoOpPlugin("com.test.x")
        reg.register(p)
        assert reg.get("com.test.x") is p

    def test_get_returns_none_for_missing_id(self) -> None:
        assert PluginRegistry().get("com.test.missing") is None

    def test_get_scoped_to_tier(self) -> None:
        reg = PluginRegistry()
        user_plugin = _NoOpPlugin("com.test.shared")
        builtin_plugin = _NoOpPlugin("com.test.shared")
        reg.register(user_plugin, tier="user")
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            reg.register(builtin_plugin, tier="builtin")
        assert reg.get("com.test.shared", tier="user") is user_plugin
        assert reg.get("com.test.shared", tier="builtin") is builtin_plugin

    def test_get_prefers_higher_tier_when_id_in_multiple_tiers(self) -> None:
        reg = PluginRegistry()
        project_plugin = _NoOpPlugin("com.test.dup")
        user_plugin = _NoOpPlugin("com.test.dup")
        reg.register(project_plugin, tier="project")
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            reg.register(user_plugin, tier="user")
        assert reg.get("com.test.dup") is project_plugin


# ---------------------------------------------------------------------------
# 5. PluginRegistry — lifecycle
# ---------------------------------------------------------------------------


class TestRegistryLifecycle:
    def test_initialize_all_calls_initialize(self) -> None:
        calls: list[str] = []

        class TrackingPlugin(_NoOpPlugin):
            def initialize(self, ctx: PluginContext) -> None:
                calls.append("initialized")

        reg = PluginRegistry()
        reg.register(TrackingPlugin())
        reg.initialize_all(_make_ctx())
        assert calls == ["initialized"]

    def test_initialize_raises_plugin_error_disables_plugin(self) -> None:
        class BadInit(_NoOpPlugin):
            def initialize(self, ctx: PluginContext) -> None:
                raise PluginError("cannot start")

        reg = PluginRegistry()
        reg.register(BadInit())
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            failed = reg.initialize_all(_make_ctx())
        assert "com.test.noop" in failed
        assert reg.count()["user"] == 0
        assert any("disabled" in str(x.message) for x in w)

    def test_initialize_unexpected_exception_disables_plugin(self) -> None:
        class BustedInit(_NoOpPlugin):
            def initialize(self, ctx: PluginContext) -> None:
                raise RuntimeError("surprise")

        reg = PluginRegistry()
        reg.register(BustedInit())
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            failed = reg.initialize_all(_make_ctx())
        assert "com.test.noop" in failed

    def test_shutdown_all_calls_shutdown(self) -> None:
        calls: list[str] = []

        class TrackingPlugin(_NoOpPlugin):
            def shutdown(self) -> None:
                calls.append("shutdown")

        reg = PluginRegistry()
        reg.register(TrackingPlugin())
        reg.shutdown_all()
        assert calls == ["shutdown"]

    def test_shutdown_exception_does_not_propagate(self) -> None:
        class BustedShutdown(_NoOpPlugin):
            def shutdown(self) -> None:
                raise RuntimeError("shutdown failed")

        reg = PluginRegistry()
        reg.register(BustedShutdown())
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            reg.shutdown_all()  # must not raise
        assert any("raised during shutdown" in str(x.message) for x in w)

    def test_disabled_plugin_absent_from_all_plugins(self) -> None:
        class FailInit(_NoOpPlugin):
            def initialize(self, ctx: PluginContext) -> None:
                raise PluginError("cannot start")

        reg = PluginRegistry()
        reg.register(FailInit("com.test.fail"))
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            reg.initialize_all(_make_ctx())
        assert reg.get("com.test.fail") is None
        assert all(p.metadata.plugin_id != "com.test.fail" for p in reg.all_plugins())

    def test_disabled_plugin_does_not_run_in_pipeline(self) -> None:
        executed: list[str] = []

        class FailInit(_NoOpPlugin):
            def initialize(self, ctx: PluginContext) -> None:
                raise PluginError("cannot start")

            def execute(self, payload: PluginPayload, ctx: PluginContext) -> PluginResult:
                executed.append(self.metadata.plugin_id)
                return PluginResult(payload=payload)

        reg = PluginRegistry()
        reg.register(FailInit("com.test.disabled"))
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            reg.initialize_all(_make_ctx())
        reg.run_pipeline(_make_payload(), _make_ctx())
        assert executed == []


# ---------------------------------------------------------------------------
# 6. PluginRegistry — run_pipeline (execution + fail-open + abort)
# ---------------------------------------------------------------------------


class TestRegistryRunPipeline:
    def test_no_plugins_returns_payload_unchanged(self) -> None:
        reg = PluginRegistry()
        payload = _make_payload(current_output="original")
        result = reg.run_pipeline(payload, _make_ctx())
        assert result.current_output == "original"

    def test_plugin_can_modify_output(self) -> None:
        class Transformer(_NoOpPlugin):
            def execute(self, payload: PluginPayload, ctx: PluginContext) -> PluginResult:
                return PluginResult(
                    payload=payload.replace_output("transformed"),
                    was_modified=True,
                )

        reg = PluginRegistry()
        reg.register(Transformer())
        payload = _make_payload(current_output="original")
        result = reg.run_pipeline(payload, _make_ctx())
        assert result.current_output == "transformed"

    def test_plugins_chain_in_order(self) -> None:
        class AppendPlugin(_NoOpPlugin):
            def __init__(self, tag: str, **kwargs: object) -> None:
                super().__init__(**kwargs)  # type: ignore[arg-type]
                self._tag = tag

            def execute(self, payload: PluginPayload, ctx: PluginContext) -> PluginResult:
                new_output = payload.current_output + self._tag
                return PluginResult(payload=payload.replace_output(new_output), was_modified=True)

        reg = PluginRegistry()
        reg.register(AppendPlugin("-A", plugin_id="com.test.a", priority=10))
        reg.register(AppendPlugin("-B", plugin_id="com.test.b", priority=20))
        payload = _make_payload(current_output="X")
        result = reg.run_pipeline(payload, _make_ctx())
        assert result.current_output == "X-A-B"

    def test_execute_exception_is_caught_fail_open(self) -> None:
        class Exploding(_NoOpPlugin):
            def execute(self, payload: PluginPayload, ctx: PluginContext) -> PluginResult:
                raise RuntimeError("kaboom")

        reg = PluginRegistry()
        reg.register(Exploding())
        payload = _make_payload(current_output="safe")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = reg.run_pipeline(payload, _make_ctx())
        assert result.current_output == "safe"
        assert any("fail-open" in str(x.message) for x in w)

    def test_abort_stops_chain(self) -> None:
        executed: list[str] = []

        class PolicyPlugin(_NoOpPlugin):
            def execute(self, payload: PluginPayload, ctx: PluginContext) -> PluginResult:
                executed.append("policy")
                return PluginResult(payload=payload, abort=True, note="blocked")

        class NeverReached(_NoOpPlugin):
            def execute(self, payload: PluginPayload, ctx: PluginContext) -> PluginResult:
                executed.append("never")
                return PluginResult(payload=payload)

        reg = PluginRegistry()
        reg.register(PolicyPlugin("com.test.policy", priority=10))
        reg.register(NeverReached("com.test.never", priority=20))
        reg.run_pipeline(_make_payload(), _make_ctx())
        assert executed == ["policy"]

    def test_abort_does_not_emit_warning(self) -> None:
        class PolicyPlugin(_NoOpPlugin):
            def execute(self, payload: PluginPayload, ctx: PluginContext) -> PluginResult:
                return PluginResult(payload=payload, abort=True)

        reg = PluginRegistry()
        reg.register(PolicyPlugin())
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            reg.run_pipeline(_make_payload(), _make_ctx())
        assert not w

    def test_raw_output_is_never_changed(self) -> None:
        class Transformer(_NoOpPlugin):
            def execute(self, payload: PluginPayload, ctx: PluginContext) -> PluginResult:
                return PluginResult(
                    payload=payload.replace_output("totally different"),
                    was_modified=True,
                )

        reg = PluginRegistry()
        reg.register(Transformer())
        payload = _make_payload(raw_output="original raw", current_output="original")
        result = reg.run_pipeline(payload, _make_ctx())
        assert result.raw_output == "original raw"

    def test_annotations_pass_between_plugins(self) -> None:
        class Writer(_NoOpPlugin):
            def execute(self, payload: PluginPayload, ctx: PluginContext) -> PluginResult:
                return PluginResult(
                    payload=payload.with_annotation("com.test.flag", True),
                )

        class Reader(_NoOpPlugin):
            def __init__(self) -> None:
                super().__init__(plugin_id="com.test.reader", priority=200)
                self.saw_flag = False

            def execute(self, payload: PluginPayload, ctx: PluginContext) -> PluginResult:
                self.saw_flag = payload.annotations.get("com.test.flag", False)
                return PluginResult(payload=payload)

        writer = Writer("com.test.writer", priority=100)
        reader = Reader()
        reg = PluginRegistry()
        reg.register(writer)
        reg.register(reader)
        reg.run_pipeline(_make_payload(), _make_ctx())
        assert reader.saw_flag is True

    def test_memory_error_during_execute_is_caught(self) -> None:
        class OOM(_NoOpPlugin):
            def execute(self, payload: PluginPayload, ctx: PluginContext) -> PluginResult:
                raise MemoryError("out of memory")

        reg = PluginRegistry()
        reg.register(OOM())
        payload = _make_payload(current_output="preserved")
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = reg.run_pipeline(payload, _make_ctx())
        assert result.current_output == "preserved"


# ---------------------------------------------------------------------------
# 7. PluginContext
# ---------------------------------------------------------------------------


class TestPluginContext:
    def test_context_fields_accessible(self) -> None:
        ctx = PluginContext(
            project_root=Path("/some/project"),
            mode=ExecutionMode.AUDIT,
            session_id="abc",
            invocation_id="xyz",
        )
        assert ctx.project_root == Path("/some/project")
        assert ctx.mode == ExecutionMode.AUDIT
        assert ctx.mode == "audit"  # StrEnum is also a str
        assert ctx.session_id == "abc"
        assert ctx.invocation_id == "xyz"

    def test_context_is_immutable(self) -> None:
        ctx = _make_ctx()
        with pytest.raises(AttributeError):
            ctx.mode = ExecutionMode.SIMULATE  # type: ignore[misc]

    def test_kw_only_construction(self) -> None:
        with pytest.raises(TypeError):
            PluginContext(None, ExecutionMode.OPTIMIZE, "", "")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# 8. PluginMetadata
# ---------------------------------------------------------------------------


class TestPluginMetadata:
    def test_required_fields(self) -> None:
        m = PluginMetadata(
            plugin_id="com.example.x",
            display_name="X",
            version="1.0.0",
            category=PluginCategory.FILTER,
        )
        assert m.priority == 100
        assert m.capabilities == ()
        assert m.min_quor_version == "0.1.0"

    def test_metadata_is_immutable(self) -> None:
        m = _make_metadata()
        with pytest.raises(AttributeError):
            m.priority = 1  # type: ignore[misc]

    def test_kw_only_construction(self) -> None:
        with pytest.raises(TypeError):
            PluginMetadata("id", "name", "1.0.0", PluginCategory.FILTER)  # type: ignore[call-arg]

    def test_capability_constants_are_strings(self) -> None:
        assert isinstance(CAPABILITY_CONTENT_TRANSFORM, str)
        assert isinstance(CAPABILITY_TELEMETRY, str)
        assert isinstance(CAPABILITY_POLICY, str)
        assert isinstance(CAPABILITY_READ_ONLY, str)

    def test_capabilities_declared_in_metadata(self) -> None:
        m = PluginMetadata(
            plugin_id="com.example.x",
            display_name="X",
            version="1.0.0",
            category=PluginCategory.POST_FILTER,
            capabilities=(CAPABILITY_TELEMETRY, CAPABILITY_READ_ONLY),
        )
        assert CAPABILITY_TELEMETRY in m.capabilities
        assert CAPABILITY_READ_ONLY in m.capabilities


# ---------------------------------------------------------------------------
# 9. ExecutionMode
# ---------------------------------------------------------------------------


class TestExecutionMode:
    def test_all_values_are_strings(self) -> None:
        for mode in ExecutionMode:
            assert isinstance(mode, str)

    def test_string_comparison_works(self) -> None:
        assert ExecutionMode.OPTIMIZE == "optimize"
        assert ExecutionMode.AUDIT == "audit"
        assert ExecutionMode.SIMULATE == "simulate"

    def test_plugin_error_importable_from_quor_plugins(self) -> None:
        from quor.plugins import PluginError as PE
        assert PE is PluginError

    def test_plugin_receives_execution_mode_in_context(self) -> None:
        seen_modes: list[ExecutionMode] = []

        class ModeCapture(_NoOpPlugin):
            def execute(self, payload: PluginPayload, ctx: PluginContext) -> PluginResult:
                seen_modes.append(ctx.mode)
                return PluginResult(payload=payload)

        reg = PluginRegistry()
        reg.register(ModeCapture())
        reg.run_pipeline(_make_payload(), _make_ctx(mode=ExecutionMode.AUDIT))
        assert seen_modes == [ExecutionMode.AUDIT]
        assert seen_modes[0] == "audit"  # StrEnum is also a str


# ---------------------------------------------------------------------------
# 10. PluginRegistry — capability exposure
# ---------------------------------------------------------------------------


class TestCapabilityExposure:
    def test_capabilities_empty_registry(self) -> None:
        assert PluginRegistry().capabilities() == frozenset()

    def test_capabilities_returns_union_across_all_plugins(self) -> None:
        reg = PluginRegistry()
        reg.register(_NoOpPlugin("com.test.a", capabilities=(CAPABILITY_TELEMETRY,)))
        reg.register(_NoOpPlugin("com.test.b", capabilities=(CAPABILITY_POLICY,)))
        caps = reg.capabilities()
        assert CAPABILITY_TELEMETRY in caps
        assert CAPABILITY_POLICY in caps

    def test_capabilities_excludes_plugins_with_no_declared_capabilities(self) -> None:
        reg = PluginRegistry()
        reg.register(_NoOpPlugin("com.test.silent"))
        reg.register(_NoOpPlugin("com.test.loud", capabilities=(CAPABILITY_READ_ONLY,)))
        assert reg.capabilities() == frozenset({CAPABILITY_READ_ONLY})

    def test_plugins_with_capability_returns_matching_plugin(self) -> None:
        reg = PluginRegistry()
        reg.register(_NoOpPlugin("com.test.transform", capabilities=(CAPABILITY_CONTENT_TRANSFORM,)))
        reg.register(_NoOpPlugin("com.test.noop"))
        matches = reg.plugins_with_capability(CAPABILITY_CONTENT_TRANSFORM)
        assert len(matches) == 1
        assert matches[0].metadata.plugin_id == "com.test.transform"

    def test_plugins_with_capability_returns_empty_when_none_match(self) -> None:
        reg = PluginRegistry()
        reg.register(_NoOpPlugin("com.test.a", capabilities=(CAPABILITY_TELEMETRY,)))
        assert reg.plugins_with_capability(CAPABILITY_POLICY) == []

    def test_plugins_with_capability_in_execution_order(self) -> None:
        reg = PluginRegistry()
        reg.register(
            _NoOpPlugin("com.test.pre", category=PluginCategory.PRE_FILTER, capabilities=(CAPABILITY_READ_ONLY,))
        )
        reg.register(
            _NoOpPlugin("com.test.post", category=PluginCategory.POST_FILTER, capabilities=(CAPABILITY_READ_ONLY,))
        )
        ids = [p.metadata.plugin_id for p in reg.plugins_with_capability(CAPABILITY_READ_ONLY)]
        assert ids.index("com.test.pre") < ids.index("com.test.post")


# ---------------------------------------------------------------------------
# 11. PluginRegistry — run_category
# ---------------------------------------------------------------------------


class TestRegistryRunCategory:
    def test_run_category_only_runs_that_category(self) -> None:
        executed: list[str] = []

        class Tracking(_NoOpPlugin):
            def execute(self, payload: PluginPayload, ctx: PluginContext) -> PluginResult:
                executed.append(self.metadata.plugin_id)
                return PluginResult(payload=payload)

        reg = PluginRegistry()
        reg.register(Tracking("com.test.pre", category=PluginCategory.PRE_FILTER))
        reg.register(Tracking("com.test.filter", category=PluginCategory.FILTER))
        reg.register(Tracking("com.test.post", category=PluginCategory.POST_FILTER))
        reg.run_category(PluginCategory.PRE_FILTER, _make_payload(), _make_ctx())
        assert executed == ["com.test.pre"]

    def test_run_category_no_plugins_returns_payload_unchanged(self) -> None:
        reg = PluginRegistry()
        payload = _make_payload(current_output="intact")
        result = reg.run_category(PluginCategory.FILTER, payload, _make_ctx())
        assert result.current_output == "intact"

    def test_run_category_abort_stops_within_category(self) -> None:
        executed: list[str] = []

        class PolicyPlugin(_NoOpPlugin):
            def execute(self, payload: PluginPayload, ctx: PluginContext) -> PluginResult:
                executed.append("policy")
                return PluginResult(payload=payload, abort=True)

        class NeverPlugin(_NoOpPlugin):
            def execute(self, payload: PluginPayload, ctx: PluginContext) -> PluginResult:
                executed.append("never")
                return PluginResult(payload=payload)

        reg = PluginRegistry()
        reg.register(PolicyPlugin("com.test.policy", category=PluginCategory.PRE_FILTER, priority=10))
        reg.register(NeverPlugin("com.test.never", category=PluginCategory.PRE_FILTER, priority=20))
        reg.run_category(PluginCategory.PRE_FILTER, _make_payload(), _make_ctx())
        assert executed == ["policy"]

    def test_run_category_fail_open(self) -> None:
        class Boom(_NoOpPlugin):
            def execute(self, payload: PluginPayload, ctx: PluginContext) -> PluginResult:
                raise ValueError("boom")

        reg = PluginRegistry()
        reg.register(Boom("com.test.boom", category=PluginCategory.PRE_FILTER))
        payload = _make_payload(current_output="safe")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = reg.run_category(PluginCategory.PRE_FILTER, payload, _make_ctx())
        assert result.current_output == "safe"
        assert any("fail-open" in str(x.message) for x in w)
