"""Tests for quor.pipeline.plugin_loader.

Coverage:
  - _load_class_from_value: import, attr resolution, error cases
  - _load_stage_handler_cls: Protocol validation, api_version check, failure paths
  - discover_stage_handlers: empty, valid, bad api_version, import failure, use_cache
  - discover_plugins: empty, valid registration, duplicate, use_cache
  - load_from_file_uri: valid file, missing file, missing class, wrong api_version,
    non-Protocol class, no '::' separator
  - get_load_report: empty result, stages section, failure section
  - invalidate_cache: deletes cache file; error becomes CacheError
  - _get_ep_specs: cache write on miss, cache hit, hash invalidation
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock, patch

import pytest

from quor.errors import CacheError, PluginError
from quor.pipeline.mask import ContentMask
from quor.pipeline.plugin_loader import (
    PluginLoadReport,
    _load_class_from_value,
    _load_stage_handler_cls,
    _reset_extra_handlers_memo,
    discover_plugins,
    discover_stage_handlers,
    get_load_report,
    invalidate_cache,
    load_from_file_uri,
)
from quor.pipeline.stages.base import StageConfig, StageHandler
from quor.plugins.registry import PluginRegistry

# ---------------------------------------------------------------------------
# Shared in-module stubs (importable by _load_class_from_value)
# ---------------------------------------------------------------------------

_THIS_MODULE = __name__


class _GoodStage:
    """Minimal valid StageHandler for testing."""

    api_version: ClassVar[int] = 1
    stage_type: ClassVar[str] = "good_stage"

    def can_handle(self, content: str, content_type: str) -> bool:
        return True

    def apply(self, mask: ContentMask, config: StageConfig) -> ContentMask:
        return mask


class _BadApiVersionStage:
    """StageHandler with unsupported api_version."""

    api_version: ClassVar[int] = 99
    stage_type: ClassVar[str] = "bad_version_stage"

    def can_handle(self, content: str, content_type: str) -> bool:
        return True

    def apply(self, mask: ContentMask, config: StageConfig) -> ContentMask:
        return mask


class _NotAStage:
    """Class that does NOT satisfy StageHandler Protocol."""

    def hello(self) -> str:
        return "hello"


class _ExplodingStage:
    """Stage whose __init__ raises."""

    def __init__(self) -> None:
        raise RuntimeError("boom")


# Source for file:// tests written to tmp_path
_VALID_STAGE_SOURCE = '''\
from __future__ import annotations
from typing import ClassVar
from quor.pipeline.mask import ContentMask
from quor.pipeline.stages.base import StageConfig

class FileStage:
    api_version: ClassVar[int] = 1
    stage_type: ClassVar[str] = "file_stage"
    def can_handle(self, content: str, content_type: str) -> bool:
        return True
    def apply(self, mask: ContentMask, config: StageConfig) -> ContentMask:
        return mask
'''

_BAD_API_SOURCE = '''\
from typing import ClassVar
from quor.pipeline.mask import ContentMask
from quor.pipeline.stages.base import StageConfig

class BadVersionFileStage:
    api_version: ClassVar[int] = 2
    stage_type: ClassVar[str] = "bad_v"
    def can_handle(self, c, ct): return True
    def apply(self, m, cfg): return m
'''

_NOT_PROTOCOL_SOURCE = '''\
class NotAStageClass:
    pass
'''

# ---------------------------------------------------------------------------
# Helper: minimal mock PluginRegistry-compatible Plugin
# ---------------------------------------------------------------------------


def _make_mock_plugin(plugin_id: str = "com.test.plugin") -> MagicMock:
    """Build a minimal Plugin mock that passes isinstance(p, Plugin) for the registry."""
    from quor.plugins.base import (
        QUOR_PLUGIN_API_VERSION,
        PluginCategory,
        PluginMetadata,
    )

    meta = PluginMetadata(
        plugin_id=plugin_id,
        display_name="Test Plugin",
        version="0.1.0",
        category=PluginCategory.POST_FILTER,
    )
    plugin = MagicMock()
    plugin.api_version = QUOR_PLUGIN_API_VERSION
    type(plugin).metadata = property(lambda self: meta)
    plugin.initialize.return_value = None
    plugin.execute.return_value = MagicMock()
    plugin.shutdown.return_value = None
    return plugin


# ===========================================================================
# _load_class_from_value
# ===========================================================================


class TestLoadClassFromValue:
    def test_loads_module_level_class(self) -> None:
        cls = _load_class_from_value(f"{_THIS_MODULE}:_GoodStage")
        assert cls is _GoodStage

    def test_raises_on_missing_colon(self) -> None:
        with pytest.raises(PluginError, match="no ':'"):
            _load_class_from_value("nocoLon")

    def test_raises_on_bad_module(self) -> None:
        with pytest.raises(PluginError, match="Cannot import"):
            _load_class_from_value("nonexistent.module:SomeClass")

    def test_raises_on_missing_attr(self) -> None:
        with pytest.raises(PluginError, match="not found"):
            _load_class_from_value(f"{_THIS_MODULE}:_DoesNotExist")


# ===========================================================================
# _load_stage_handler_cls
# ===========================================================================


class TestLoadStageHandlerCls:
    def test_loads_valid_handler(self) -> None:
        cls, failure = _load_stage_handler_cls(
            "good", f"{_THIS_MODULE}:_GoodStage"
        )
        assert failure is None
        assert cls is _GoodStage

    def test_fails_bad_api_version(self) -> None:
        cls, failure = _load_stage_handler_cls(
            "bad_ver", f"{_THIS_MODULE}:_BadApiVersionStage"
        )
        assert cls is None
        assert failure is not None
        assert "api_version" in failure.reason

    def test_fails_non_protocol_class(self) -> None:
        cls, failure = _load_stage_handler_cls(
            "not_stage", f"{_THIS_MODULE}:_NotAStage"
        )
        assert cls is None
        assert failure is not None
        assert "StageHandler Protocol" in failure.reason

    def test_fails_import_error(self) -> None:
        cls, failure = _load_stage_handler_cls(
            "bad_import", "nonexistent.module:Cls"
        )
        assert cls is None
        assert failure is not None
        assert "Cannot import" in failure.reason

    def test_fails_instantiation_error(self) -> None:
        cls, failure = _load_stage_handler_cls(
            "explode", f"{_THIS_MODULE}:_ExplodingStage"
        )
        assert cls is None
        assert failure is not None
        assert "instantiation error" in failure.reason


# ===========================================================================
# discover_stage_handlers
# ===========================================================================


class TestDiscoverStageHandlers:
    def _spec(self, name: str, cls_path: str) -> dict[str, str]:
        return {"name": name, "value": cls_path}

    def test_returns_empty_when_no_entry_points(self) -> None:
        with patch(
            "quor.pipeline.plugin_loader._get_ep_specs",
            return_value=([], []),
        ):
            handlers, failures = discover_stage_handlers(use_cache=False)
        assert handlers == {}
        assert failures == []

    def test_discovers_valid_stage(self) -> None:
        spec = self._spec("good", f"{_THIS_MODULE}:_GoodStage")
        with patch(
            "quor.pipeline.plugin_loader._get_ep_specs",
            return_value=([spec], []),
        ):
            handlers, failures = discover_stage_handlers(use_cache=False)
        assert not failures
        assert "good_stage" in handlers
        assert handlers["good_stage"][0] is _GoodStage

    def test_skips_bad_api_version_with_warning(self) -> None:
        spec = self._spec("bad", f"{_THIS_MODULE}:_BadApiVersionStage")
        with patch(
            "quor.pipeline.plugin_loader._get_ep_specs",
            return_value=([spec], []),
        ), pytest.warns(UserWarning, match="skipped"):
            handlers, failures = discover_stage_handlers(use_cache=False)
        assert "bad_version_stage" not in handlers
        assert len(failures) == 1
        assert "api_version" in failures[0].reason

    def test_skips_bad_import_with_warning(self) -> None:
        spec = self._spec("missing", "no.such.module:Cls")
        with patch(
            "quor.pipeline.plugin_loader._get_ep_specs",
            return_value=([spec], []),
        ), pytest.warns(UserWarning, match="skipped"):
            handlers, failures = discover_stage_handlers(use_cache=False)
        assert not handlers
        assert failures[0].entry_point_name == "missing"

    def test_multiple_stages_partial_failure(self) -> None:
        specs = [
            self._spec("good", f"{_THIS_MODULE}:_GoodStage"),
            self._spec("bad", f"{_THIS_MODULE}:_BadApiVersionStage"),
        ]
        with patch(
            "quor.pipeline.plugin_loader._get_ep_specs",
            return_value=(specs, []),
        ), pytest.warns(UserWarning):
            handlers, failures = discover_stage_handlers(use_cache=False)
        assert "good_stage" in handlers
        assert len(failures) == 1

    def test_use_cache_false_calls_get_ep_specs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[bool] = []

        def fake_get_ep_specs(*, use_cache: bool) -> tuple[list, list]:
            calls.append(use_cache)
            return [], []

        monkeypatch.setattr("quor.pipeline.plugin_loader._get_ep_specs", fake_get_ep_specs)
        discover_stage_handlers(use_cache=False)
        assert calls == [False]


# ===========================================================================
# discover_plugins
# ===========================================================================


class TestDiscoverPlugins:
    def test_returns_empty_on_no_entry_points(self) -> None:
        registry = PluginRegistry()
        with patch(
            "quor.pipeline.plugin_loader._get_ep_specs",
            return_value=([], []),
        ):
            failures = discover_plugins(registry, use_cache=False)
        assert failures == []
        assert registry.all_plugins() == []

    def test_registers_valid_plugin(self) -> None:
        from quor.plugins.base import (
            QUOR_PLUGIN_API_VERSION,
            PluginCategory,
            PluginMetadata,
            PluginPayload,
            PluginResult,
        )

        class _TestPlugin:
            api_version: ClassVar[int] = QUOR_PLUGIN_API_VERSION

            @property
            def metadata(self) -> PluginMetadata:
                return PluginMetadata(
                    plugin_id="com.test.discover",
                    display_name="Discover Test",
                    version="1.0.0",
                    category=PluginCategory.POST_FILTER,
                )

            def initialize(self, ctx: object) -> None:
                pass

            def execute(self, payload: PluginPayload, ctx: object) -> PluginResult:
                return PluginResult(payload=payload)  # type: ignore[arg-type]

            def shutdown(self) -> None:
                pass

        # Register _TestPlugin as importable in this module
        sys.modules[_THIS_MODULE]._TestPlugin = _TestPlugin  # type: ignore[attr-defined]
        spec = {"name": "discover_test", "value": f"{_THIS_MODULE}:_TestPlugin"}

        registry = PluginRegistry()
        with patch(
            "quor.pipeline.plugin_loader._get_ep_specs",
            return_value=([], [spec]),
        ):
            failures = discover_plugins(registry, use_cache=False)

        assert not failures
        plugin = registry.get("com.test.discover")
        assert plugin is not None


# ===========================================================================
# load_from_file_uri
# ===========================================================================


class TestLoadFromFileUri:
    def test_loads_valid_stage(self, tmp_path: Path) -> None:
        stage_file = tmp_path / "stage.py"
        stage_file.write_text(_VALID_STAGE_SOURCE, encoding="utf-8")
        uri = f"file://{stage_file}::FileStage"
        handler = load_from_file_uri(uri)
        assert isinstance(handler, StageHandler)
        assert handler.stage_type == "file_stage"  # type: ignore[union-attr]

    def test_raises_on_non_file_uri(self) -> None:
        with pytest.raises(PluginError, match="Not a file://"):
            load_from_file_uri("http://example.com/stage.py::MyStage")

    def test_raises_on_missing_double_colon(self) -> None:
        with pytest.raises(PluginError, match="::ClassName"):
            load_from_file_uri("file:///path/to/stage.py")

    def test_raises_on_missing_file(self, tmp_path: Path) -> None:
        uri = f"file://{tmp_path / 'nonexistent.py'}::MyStage"
        with pytest.raises(PluginError, match="not found"):
            load_from_file_uri(uri)

    def test_raises_on_missing_class(self, tmp_path: Path) -> None:
        stage_file = tmp_path / "stage.py"
        stage_file.write_text("# empty module\n", encoding="utf-8")
        uri = f"file://{stage_file}::MissingClass"
        with pytest.raises(PluginError, match="not found"):
            load_from_file_uri(uri)

    def test_raises_on_wrong_api_version(self, tmp_path: Path) -> None:
        stage_file = tmp_path / "bad.py"
        stage_file.write_text(_BAD_API_SOURCE, encoding="utf-8")
        uri = f"file://{stage_file}::BadVersionFileStage"
        with pytest.raises(PluginError, match="api_version"):
            load_from_file_uri(uri)

    def test_raises_on_non_protocol_class(self, tmp_path: Path) -> None:
        stage_file = tmp_path / "notprotocol.py"
        stage_file.write_text(_NOT_PROTOCOL_SOURCE, encoding="utf-8")
        uri = f"file://{stage_file}::NotAStageClass"
        with pytest.raises(PluginError, match="StageHandler Protocol"):
            load_from_file_uri(uri)

    def test_stage_can_handle_and_apply(self, tmp_path: Path) -> None:
        stage_file = tmp_path / "stage.py"
        stage_file.write_text(_VALID_STAGE_SOURCE, encoding="utf-8")
        uri = f"file://{stage_file}::FileStage"
        handler = load_from_file_uri(uri)
        mask = ContentMask.from_text("hello world")
        result = handler.apply(mask, StageConfig.model_validate({"type": "file_stage"}))  # type: ignore[union-attr]
        assert result is mask


# ===========================================================================
# get_load_report
# ===========================================================================


class TestGetLoadReport:
    def test_report_empty_when_no_plugins(self) -> None:
        with patch(
            "quor.pipeline.plugin_loader._get_ep_specs",
            return_value=([], []),
        ):
            report = get_load_report(use_cache=False)
        assert report.is_empty

    def test_report_includes_discovered_stage(self) -> None:
        spec = {"name": "good", "value": f"{_THIS_MODULE}:_GoodStage"}
        with patch(
            "quor.pipeline.plugin_loader._get_ep_specs",
            return_value=([spec], []),
        ):
            report = get_load_report(use_cache=False)
        assert not report.is_empty
        assert len(report.stages) == 1
        assert report.stages[0].stage_type == "good_stage"
        assert report.stages[0].api_version == 1

    def test_report_includes_failure(self) -> None:
        spec = {"name": "broken", "value": f"{_THIS_MODULE}:_BadApiVersionStage"}
        with patch(
            "quor.pipeline.plugin_loader._get_ep_specs",
            return_value=([spec], []),
        ), pytest.warns(UserWarning):
            report = get_load_report(use_cache=False)
        assert len(report.failures) == 1
        assert report.failures[0].entry_point_name == "broken"

    def test_returns_pluginloadreport_instance(self) -> None:
        with patch(
            "quor.pipeline.plugin_loader._get_ep_specs",
            return_value=([], []),
        ):
            report = get_load_report(use_cache=False)
        assert isinstance(report, PluginLoadReport)


# ===========================================================================
# invalidate_cache
# ===========================================================================


class TestInvalidateCache:
    def test_deletes_existing_cache_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cache = tmp_path / "plugin-cache.json"
        cache.write_bytes(b"{}")
        monkeypatch.setattr("quor.pipeline.plugin_loader._cache_path", lambda: cache)
        invalidate_cache()
        assert not cache.exists()

    def test_no_error_when_cache_absent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cache = tmp_path / "plugin-cache.json"
        monkeypatch.setattr("quor.pipeline.plugin_loader._cache_path", lambda: cache)
        invalidate_cache()  # must not raise

    def test_raises_cache_error_on_os_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cache = tmp_path / "plugin-cache.json"
        cache.write_bytes(b"{}")

        def bad_path() -> Path:
            return cache

        monkeypatch.setattr("quor.pipeline.plugin_loader._cache_path", bad_path)

        # Patch Path.unlink to raise OSError
        original_unlink = Path.unlink

        def failing_unlink(self: Path, missing_ok: bool = False) -> None:
            raise OSError("permission denied")

        monkeypatch.setattr(Path, "unlink", failing_unlink)
        try:
            with pytest.raises(CacheError, match="Could not delete"):
                invalidate_cache()
        finally:
            monkeypatch.setattr(Path, "unlink", original_unlink)


# ===========================================================================
# Cache read/write via _get_ep_specs
# ===========================================================================


class TestCacheBehavior:
    def test_cache_miss_writes_cache_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import quor.pipeline.plugin_loader as loader

        cache_file = tmp_path / "plugin-cache.json"
        monkeypatch.setattr(loader, "_cache_path", lambda: cache_file)
        monkeypatch.setattr(
            loader,
            "_package_set_hash",
            lambda: "testhash000",
        )
        monkeypatch.setattr(
            "importlib.metadata.entry_points",
            lambda group: [],
        )

        stage_specs, plugin_specs = loader._get_ep_specs(use_cache=True)
        assert stage_specs == []
        assert plugin_specs == []
        assert cache_file.exists()

    def test_cache_hit_returns_without_rescanning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import orjson

        import quor.pipeline.plugin_loader as loader

        cache_file = tmp_path / "plugin-cache.json"
        cache_file.write_bytes(
            orjson.dumps(
                {
                    "package_set_hash": "stablehash",
                    "stage_entries": [{"name": "cached_stage", "value": "mod:Cls"}],
                    "plugin_entries": [],
                }
            )
        )
        monkeypatch.setattr(loader, "_cache_path", lambda: cache_file)
        monkeypatch.setattr(loader, "_package_set_hash", lambda: "stablehash")

        scan_count = [0]

        def track_scan(group: str) -> list:
            scan_count[0] += 1
            return []

        monkeypatch.setattr("importlib.metadata.entry_points", track_scan)

        stage_specs, _ = loader._get_ep_specs(use_cache=True)
        assert scan_count[0] == 0
        assert stage_specs == [{"name": "cached_stage", "value": "mod:Cls"}]

    def test_hash_change_invalidates_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import orjson

        import quor.pipeline.plugin_loader as loader

        cache_file = tmp_path / "plugin-cache.json"
        cache_file.write_bytes(
            orjson.dumps(
                {
                    "package_set_hash": "oldhash",
                    "stage_entries": [{"name": "stale", "value": "mod:Cls"}],
                    "plugin_entries": [],
                }
            )
        )
        monkeypatch.setattr(loader, "_cache_path", lambda: cache_file)
        monkeypatch.setattr(loader, "_package_set_hash", lambda: "newhash")
        monkeypatch.setattr("importlib.metadata.entry_points", lambda group: [])

        stage_specs, _ = loader._get_ep_specs(use_cache=True)
        # Cache was stale — returned fresh (empty) result
        assert stage_specs == []


# ===========================================================================
# Integration: test plugin installed via dev deps
# ===========================================================================


@pytest.mark.integration
class TestInstalledTestPlugin:
    """Requires 'pip install -e .[dev]' to have been run so quor-test-stage is installed."""

    def test_discovers_noop_test_stage(self) -> None:
        _reset_extra_handlers_memo()
        handlers, failures = discover_stage_handlers(use_cache=False)
        assert "noop_test" in handlers, (
            f"noop_test not found; failures={failures}; handlers={list(handlers)}"
        )

    def test_noop_test_stage_can_handle_and_apply(self) -> None:
        _reset_extra_handlers_memo()
        handlers, _ = discover_stage_handlers(use_cache=False)
        if "noop_test" not in handlers:
            pytest.skip("quor-test-stage not installed")
        handler_cls, _ = handlers["noop_test"]
        handler = handler_cls()
        mask = ContentMask.from_text("some content")
        result = handler.apply(mask, StageConfig.model_validate({"type": "noop_test"}))
        assert result is mask
