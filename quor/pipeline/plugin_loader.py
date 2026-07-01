"""Plugin discovery and loading for Quor.

Two entry-point groups are supported:
  quor.compression_stage — StageHandler implementations (ContentMask pipeline)
  quor.plugin            — Plugin implementations (lifecycle middleware)

Discovery results are cached to avoid repeated entry-point scanning. The cache
lives at ~/.config/quor/plugin-cache.json and is invalidated when the installed
package set changes (detected via a SHA-256 hash of name==version pairs).

file:// escape hatch:
  A TOML stage type of "file:///path/to/module.py::ClassName" loads a local
  StageHandler from disk without packaging. Not cached; for development only.

Fail-open contract:
  Every individual discovery failure logs a warning and is skipped. The rest of
  the pipeline continues. A total discovery failure (unreadable cache directory,
  importlib.metadata unavailable) logs a warning and returns an empty result.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import orjson
import platformdirs

from quor.errors import CacheError, PluginError
from quor.pipeline.stages.base import StageConfig, StageHandler
from quor.plugins.base import QUOR_PLUGIN_API_VERSION, Plugin
from quor.plugins.registry import PluginRegistry

_STAGE_EP_GROUP = "quor.compression_stage"
_PLUGIN_EP_GROUP = "quor.plugin"

# ---------------------------------------------------------------------------
# Report types (consumed by quor doctor)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StageInfo:
    """Summary of a successfully loaded StageHandler entry-point."""

    entry_point_name: str
    module_path: str
    class_name: str
    stage_type: str
    api_version: int


@dataclass(frozen=True)
class PluginInfo:
    """Summary of a successfully loaded Plugin entry-point."""

    entry_point_name: str
    module_path: str
    class_name: str
    plugin_id: str
    version: str
    api_version: int


@dataclass(frozen=True)
class FailureInfo:
    """Summary of an entry-point that could not be loaded."""

    entry_point_name: str
    group: str
    reason: str


@dataclass
class PluginLoadReport:
    """Full discovery summary returned by get_load_report() for quor doctor."""

    stages: list[StageInfo] = field(default_factory=list)
    plugins: list[PluginInfo] = field(default_factory=list)
    failures: list[FailureInfo] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.stages and not self.plugins and not self.failures


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def _cache_path() -> Path:
    return Path(platformdirs.user_config_dir("quor")) / "plugin-cache.json"


def _package_set_hash() -> str:
    """Short SHA-256 fingerprint of the installed distribution set."""
    keys: list[str] = []
    for d in importlib.metadata.distributions():
        try:
            keys.append(f"{d.metadata['Name']}=={d.metadata['Version']}")
        except KeyError:
            pass
    return hashlib.sha256("\n".join(sorted(keys)).encode()).hexdigest()[:24]


def _read_cache() -> tuple[str, list[dict[str, str]], list[dict[str, str]]] | None:
    """Return (pkg_hash, stage_specs, plugin_specs) or None on miss/error."""
    path = _cache_path()
    if not path.exists():
        return None
    try:
        data = orjson.loads(path.read_bytes())
        return (
            str(data.get("package_set_hash", "")),
            list(data.get("stage_entries", [])),
            list(data.get("plugin_entries", [])),
        )
    except Exception as exc:  # noqa: BLE001
        warnings.warn(
            f"[quor] plugin cache unreadable, will rescan: {exc}",
            stacklevel=3,
        )
        return None


def _write_cache(
    pkg_hash: str,
    stage_specs: list[dict[str, str]],
    plugin_specs: list[dict[str, str]],
) -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            orjson.dumps(
                {
                    "package_set_hash": pkg_hash,
                    "stage_entries": stage_specs,
                    "plugin_entries": plugin_specs,
                },
                option=orjson.OPT_INDENT_2,
            )
        )
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"[quor] could not write plugin cache: {exc}", stacklevel=3)


def invalidate_cache() -> None:
    """Delete the plugin discovery cache file.

    The next call to discover_stage_handlers() or discover_plugins() will
    perform a full entry-point rescan.

    Raises CacheError if the file exists but cannot be deleted.
    """
    path = _cache_path()
    try:
        if path.exists():
            path.unlink()
    except Exception as exc:
        raise CacheError(f"Could not delete plugin cache at {path}: {exc}") from exc


def _get_ep_specs(
    *, use_cache: bool
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Return (stage_ep_specs, plugin_ep_specs).

    Reads from the on-disk cache when use_cache=True and the package set has
    not changed. Rescans both entry-point groups and writes a fresh cache
    otherwise. Both groups are always scanned and written together to keep
    the cache file consistent.
    """
    if use_cache:
        current_hash = _package_set_hash()
        cached = _read_cache()
        if cached is not None:
            cached_hash, stage_specs, plugin_specs = cached
            if cached_hash == current_hash:
                return stage_specs, plugin_specs
    else:
        current_hash = ""

    # Cache miss or use_cache=False: rescan both groups
    try:
        raw_stage_eps = importlib.metadata.entry_points(group=_STAGE_EP_GROUP)
        raw_plugin_eps = importlib.metadata.entry_points(group=_PLUGIN_EP_GROUP)
    except Exception as exc:  # noqa: BLE001
        warnings.warn(
            f"[quor] entry-point scan failed: {exc}; no third-party plugins loaded",
            stacklevel=3,
        )
        return [], []

    stage_specs = [{"name": ep.name, "value": ep.value} for ep in raw_stage_eps]
    plugin_specs = [{"name": ep.name, "value": ep.value} for ep in raw_plugin_eps]

    if use_cache:
        _write_cache(current_hash, stage_specs, plugin_specs)

    return stage_specs, plugin_specs


# ---------------------------------------------------------------------------
# Internal class loaders
# ---------------------------------------------------------------------------


def _load_class_from_value(value: str) -> type:
    """Import a class from an entry-point value string like 'module.path:ClassName'."""
    if ":" not in value:
        raise PluginError(f"Entry-point value has no ':': {value!r}")
    module_name, _, attr_name = value.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise PluginError(f"Cannot import {module_name!r}: {exc}") from exc
    try:
        return getattr(module, attr_name)  # type: ignore[no-any-return]
    except AttributeError:
        raise PluginError(f"Attribute {attr_name!r} not found in {module_name!r}") from None


def _load_stage_handler_cls(
    ep_name: str, ep_value: str
) -> tuple[type | None, FailureInfo | None]:
    """Load and validate a StageHandler class from an entry-point value.

    Returns (cls, None) on success; (None, FailureInfo) on any failure.
    """
    try:
        handler_cls = _load_class_from_value(ep_value)
    except PluginError as exc:
        return None, FailureInfo(ep_name, _STAGE_EP_GROUP, str(exc))

    try:
        handler = handler_cls()
    except Exception as exc:  # noqa: BLE001
        return None, FailureInfo(ep_name, _STAGE_EP_GROUP, f"instantiation error: {exc}")

    if not isinstance(handler, StageHandler):
        return None, FailureInfo(
            ep_name, _STAGE_EP_GROUP, "does not satisfy StageHandler Protocol"
        )

    api_ver = getattr(handler, "api_version", None)
    if api_ver != 1:
        return None, FailureInfo(
            ep_name,
            _STAGE_EP_GROUP,
            f"api_version {api_ver!r} is not supported (expected 1)",
        )

    return handler_cls, None


def _load_plugin_cls(
    ep_name: str, ep_value: str
) -> tuple[type | None, FailureInfo | None]:
    """Load and validate a Plugin class from an entry-point value.

    Returns (cls, None) on success; (None, FailureInfo) on any failure.
    """
    try:
        plugin_cls = _load_class_from_value(ep_value)
    except PluginError as exc:
        return None, FailureInfo(ep_name, _PLUGIN_EP_GROUP, str(exc))

    try:
        plugin = plugin_cls()
    except Exception as exc:  # noqa: BLE001
        return None, FailureInfo(ep_name, _PLUGIN_EP_GROUP, f"instantiation error: {exc}")

    if not isinstance(plugin, Plugin):
        return None, FailureInfo(
            ep_name, _PLUGIN_EP_GROUP, "does not satisfy Plugin Protocol"
        )

    if plugin.api_version != QUOR_PLUGIN_API_VERSION:
        return None, FailureInfo(
            ep_name,
            _PLUGIN_EP_GROUP,
            f"api_version {plugin.api_version!r} is not supported "
            f"(expected {QUOR_PLUGIN_API_VERSION})",
        )

    return plugin_cls, None


# ---------------------------------------------------------------------------
# Public discovery API
# ---------------------------------------------------------------------------

# Module-level memo for get_extra_stage_handlers() — populated on first call.
# Reset in tests via _reset_extra_handlers_memo().
_extra_handlers_memo: dict[str, tuple[type, type[StageConfig]]] | None = None


def _reset_extra_handlers_memo() -> None:
    """Reset the in-process memo. Call in tests that need fresh discovery."""
    global _extra_handlers_memo
    _extra_handlers_memo = None


def get_extra_stage_handlers() -> dict[str, tuple[type, type[StageConfig]]]:
    """Return third-party StageHandler classes mapped by stage_type.

    Memoised for the process lifetime. Used by FilterRegistry._build_stage_entry()
    to extend the built-in stage map without rescanning on every pipeline run.

    Returns an empty dict when no third-party stages are installed or if
    discovery fails (fail-open).
    """
    global _extra_handlers_memo
    if _extra_handlers_memo is None:
        handlers, _ = discover_stage_handlers()
        _extra_handlers_memo = handlers
    return _extra_handlers_memo


def discover_stage_handlers(
    *, use_cache: bool = True
) -> tuple[dict[str, tuple[type, type[StageConfig]]], list[FailureInfo]]:
    """Discover quor.compression_stage entry-points.

    Returns:
        (extra_handlers, failures) where extra_handlers maps
        stage_type -> (handler_cls, StageConfig) for use in FilterRegistry.

    Failed entry-points are returned in failures and warned; they do not
    prevent other plugins from loading.
    """
    handlers: dict[str, tuple[type, type[StageConfig]]] = {}
    failures: list[FailureInfo] = []

    stage_specs, _ = _get_ep_specs(use_cache=use_cache)

    for spec in stage_specs:
        handler_cls, failure = _load_stage_handler_cls(spec["name"], spec["value"])
        if failure is not None:
            failures.append(failure)
            warnings.warn(
                f"[quor] Stage plugin {spec['name']!r} skipped: {failure.reason}",
                stacklevel=2,
            )
        elif handler_cls is not None:
            try:
                stage_type: str = handler_cls.stage_type  # type: ignore[attr-defined]
                handlers[stage_type] = (handler_cls, StageConfig)
            except AttributeError:
                failures.append(
                    FailureInfo(spec["name"], _STAGE_EP_GROUP, "missing stage_type ClassVar")
                )

    return handlers, failures


def discover_plugins(
    registry: PluginRegistry,
    *,
    use_cache: bool = True,
    tier: str = "user",
) -> list[FailureInfo]:
    """Discover quor.plugin entry-points and register them in the given registry.

    Successfully loaded plugins are registered at the given tier (default 'user').
    Returns a list of FailureInfo for entry-points that could not be loaded.
    """
    failures: list[FailureInfo] = []

    _, plugin_specs = _get_ep_specs(use_cache=use_cache)

    for spec in plugin_specs:
        plugin_cls, failure = _load_plugin_cls(spec["name"], spec["value"])
        if failure is not None:
            failures.append(failure)
            warnings.warn(
                f"[quor] Plugin {spec['name']!r} skipped: {failure.reason}",
                stacklevel=2,
            )
        elif plugin_cls is not None:
            try:
                plugin = plugin_cls()
                registry.register(plugin, tier=tier)  # type: ignore[arg-type]
            except PluginError as exc:
                failures.append(FailureInfo(spec["name"], _PLUGIN_EP_GROUP, str(exc)))
                warnings.warn(
                    f"[quor] Plugin {spec['name']!r} rejected: {exc}",
                    stacklevel=2,
                )

    return failures


def get_load_report(*, use_cache: bool = True) -> PluginLoadReport:
    """Run full discovery and return a summary for quor doctor.

    Does not mutate any global state. Stages are discovered but not registered
    anywhere; a temporary PluginRegistry is used for Plugin discovery.
    """
    report = PluginLoadReport()

    extra_handlers, stage_failures = discover_stage_handlers(use_cache=use_cache)
    report.failures.extend(stage_failures)
    for stage_type, (handler_cls, _) in extra_handlers.items():
        try:
            handler = handler_cls()
            report.stages.append(
                StageInfo(
                    entry_point_name=stage_type,
                    module_path=handler_cls.__module__,
                    class_name=handler_cls.__name__,
                    stage_type=stage_type,
                    api_version=int(getattr(handler, "api_version", -1)),
                )
            )
        except Exception as exc:  # noqa: BLE001
            warnings.warn(
                f"[quor] Could not inspect stage {stage_type!r}: {exc}",
                stacklevel=2,
            )

    temp_registry = PluginRegistry()
    plugin_failures = discover_plugins(temp_registry, use_cache=use_cache)
    report.failures.extend(plugin_failures)
    for plugin in temp_registry.all_plugins():
        meta = plugin.metadata
        report.plugins.append(
            PluginInfo(
                entry_point_name=meta.plugin_id,
                module_path=type(plugin).__module__,
                class_name=type(plugin).__name__,
                plugin_id=meta.plugin_id,
                version=meta.version,
                api_version=plugin.api_version,
            )
        )

    return report


# ---------------------------------------------------------------------------
# file:// escape hatch
# ---------------------------------------------------------------------------


def load_from_file_uri(uri: str) -> StageHandler:
    """Load a StageHandler from a file:// URI like 'file:///path/module.py::ClassName'.

    Intended for development and testing use only — not cached.

    Raises PluginError on any failure:
    - Module file does not exist
    - Class not found in module
    - Class does not satisfy StageHandler Protocol
    - api_version != 1
    """
    if not uri.startswith("file://"):
        raise PluginError(f"Not a file:// URI: {uri!r}")

    rest = uri[len("file://"):]
    if "::" not in rest:
        raise PluginError(
            f"file:// URI must contain '::ClassName' — got {uri!r}"
        )

    path_str, _, class_name = rest.rpartition("::")
    module_path = Path(path_str)

    if not module_path.exists():
        raise PluginError(f"file:// stage module not found: {module_path}")

    spec = importlib.util.spec_from_file_location("_quor_file_stage", module_path)
    if spec is None or spec.loader is None:
        raise PluginError(f"Cannot create module spec from {module_path}")

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise PluginError(f"Error executing {module_path}: {exc}") from exc

    if not hasattr(module, class_name):
        raise PluginError(f"Class {class_name!r} not found in {module_path}")

    handler_cls: type = getattr(module, class_name)
    try:
        handler = handler_cls()
    except Exception as exc:
        raise PluginError(
            f"Error instantiating {class_name} from {module_path}: {exc}"
        ) from exc

    if not isinstance(handler, StageHandler):
        raise PluginError(
            f"{class_name} in {module_path} does not satisfy StageHandler Protocol"
        )

    api_ver = getattr(handler, "api_version", None)
    if api_ver != 1:
        raise PluginError(
            f"{class_name}.api_version = {api_ver!r}; only api_version = 1 is supported"
        )

    return handler


# ---------------------------------------------------------------------------
# Convenience type alias for callers
# ---------------------------------------------------------------------------

ExtraStageHandlers = dict[str, tuple[type[Any], type[StageConfig]]]
