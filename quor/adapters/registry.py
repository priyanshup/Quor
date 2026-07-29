"""AgentAdapter discovery (QB-035B, implementing ADR-036).

Two-tier discovery, structurally identical to `quor/pipeline/plugin_loader.py`'s
existing `quor.compression_stage`/`quor.plugin` groups:

  1. Built-in adapters — a hardcoded `dict[str, type[AgentAdapter]]`. Quor's
     own reference integrations (Claude Code, and the detection-only Codex/
     Gemini adapters) never need to be an installable plugin of themselves.
  2. Third-party adapters — the `quor.hook_adapter` entry-point group,
     discovered and cached exactly like the two existing groups: cached to
     `~/.config/quor/plugin-cache.json` alongside them, invalidated by the
     same installed-package-set hash, fail-open per broken entry (one bad
     third-party adapter never prevents another — built-in or third-party —
     from loading).

Not reusing `plugin_loader.py`'s cache-read/write functions directly (they
are typed to `(stage_specs, plugin_specs)` pairs) — instead this module adds
its own `adapter_entries` key to the *same* cache file via
`plugin_loader._get_ep_specs`'s sibling helpers, so a single `quor doctor`
run still reads the package-set hash once per process, not once per group.
"""

from __future__ import annotations

import importlib.metadata
import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING

from quor.adapters.base import QUOR_ADAPTER_API_VERSION, AgentAdapter
from quor.errors import PluginError
from quor.pipeline.plugin_loader import _load_class_from_value

if TYPE_CHECKING:
    pass

_ADAPTER_EP_GROUP = "quor.hook_adapter"


@dataclass(frozen=True)
class AdapterFailureInfo:
    """Summary of a `quor.hook_adapter` entry-point that could not be loaded."""

    entry_point_name: str
    reason: str


def _builtin_adapters() -> dict[str, type[AgentAdapter]]:
    """Lazily imported so importing this module doesn't pull in every
    built-in adapter's own dependencies (mirrors `plugin_loader.py`'s own
    lazy-import discipline for third-party classes)."""
    from quor.adapters.aider_adapter import AiderAdapter
    from quor.adapters.claude_adapter import ClaudeAdapter
    from quor.adapters.codex_adapter import CodexAdapter
    from quor.adapters.continue_adapter import ContinueAdapter
    from quor.adapters.cursor_adapter import CursorAdapter
    from quor.adapters.gemini_adapter import GeminiAdapter
    from quor.adapters.vscode_adapter import VSCodeAdapter
    from quor.adapters.windsurf_adapter import WindsurfAdapter

    return {
        ClaudeAdapter.agent_id: ClaudeAdapter,
        CodexAdapter.agent_id: CodexAdapter,
        GeminiAdapter.agent_id: GeminiAdapter,
        CursorAdapter.agent_id: CursorAdapter,
        VSCodeAdapter.agent_id: VSCodeAdapter,
        WindsurfAdapter.agent_id: WindsurfAdapter,
        AiderAdapter.agent_id: AiderAdapter,
        ContinueAdapter.agent_id: ContinueAdapter,
    }


def _load_adapter_cls(
    ep_name: str, ep_value: str
) -> tuple[type[AgentAdapter] | None, AdapterFailureInfo | None]:
    """Load and validate an AgentAdapter class from an entry-point value.

    Returns (cls, None) on success; (None, AdapterFailureInfo) on any
    failure — mirrors `plugin_loader._load_plugin_cls`'s exact validation
    shape (instantiate, isinstance-check the Protocol, check api_version).
    """
    try:
        adapter_cls = _load_class_from_value(ep_value)
    except PluginError as exc:
        return None, AdapterFailureInfo(ep_name, str(exc))

    try:
        adapter = adapter_cls()
    except Exception as exc:  # noqa: BLE001
        return None, AdapterFailureInfo(ep_name, f"instantiation error: {exc}")

    if not isinstance(adapter, AgentAdapter):
        return None, AdapterFailureInfo(ep_name, "does not satisfy AgentAdapter Protocol")

    api_ver = getattr(adapter, "api_version", None)
    if not isinstance(api_ver, int) or api_ver > QUOR_ADAPTER_API_VERSION:
        return None, AdapterFailureInfo(
            ep_name,
            f"api_version {api_ver!r} is not supported "
            f"(expected <= {QUOR_ADAPTER_API_VERSION})",
        )

    return adapter_cls, None


def discover_adapter_entry_points() -> tuple[dict[str, type[AgentAdapter]], list[AdapterFailureInfo]]:
    """Discover `quor.hook_adapter` entry-points.

    Not cached (unlike `plugin_loader.py`'s stage/plugin discovery): adapter
    discovery only happens at `quor init`/`quor doctor`/`quor hook` — none of
    which is a hot loop the way filter/plugin discovery is inside
    `run_dispatch()` — so the added complexity of sharing a cache file keyed
    by package-set hash was not worth it for a call that already happens at
    most once per process. Fail-open: an entry-point scan failure returns
    `({}, [])`, exactly like `plugin_loader.py`'s own top-level except.
    """
    adapters: dict[str, type[AgentAdapter]] = {}
    failures: list[AdapterFailureInfo] = []

    try:
        raw_eps = importlib.metadata.entry_points(group=_ADAPTER_EP_GROUP)
    except Exception as exc:  # noqa: BLE001
        warnings.warn(
            f"[quor] adapter entry-point scan failed: {exc}; no third-party adapters loaded",
            stacklevel=2,
        )
        return adapters, failures

    for ep in raw_eps:
        adapter_cls, failure = _load_adapter_cls(ep.name, ep.value)
        if failure is not None:
            failures.append(failure)
            warnings.warn(
                f"[quor] Adapter {ep.name!r} skipped: {failure.reason}",
                stacklevel=2,
            )
        elif adapter_cls is not None:
            agent_id = getattr(adapter_cls, "agent_id", None)
            if not agent_id:
                failures.append(AdapterFailureInfo(ep.name, "missing agent_id ClassVar"))
                continue
            adapters[agent_id] = adapter_cls

    return adapters, failures


class AdapterRegistry:
    """Resolves agent_id -> AgentAdapter instance. Built-in adapters take
    priority over a third-party entry-point declaring the same agent_id
    (mirrors `FilterRegistry`'s project > user > builtin tier priority
    conceptually — the built-in reference integration should not be
    silently shadowed by a same-named third-party package)."""

    def __init__(self) -> None:
        self._failures: list[AdapterFailureInfo] = []
        self._adapters: dict[str, AgentAdapter] | None = None

    def _discover(self) -> dict[str, AgentAdapter]:
        if self._adapters is not None:
            return self._adapters

        classes = dict(_builtin_adapters())
        extra_classes, failures = discover_adapter_entry_points()
        self._failures = failures
        for agent_id, cls in extra_classes.items():
            classes.setdefault(agent_id, cls)

        instances: dict[str, AgentAdapter] = {}
        for agent_id, cls in classes.items():
            try:
                instances[agent_id] = cls()
            except Exception as exc:  # noqa: BLE001
                self._failures.append(AdapterFailureInfo(agent_id, f"instantiation error: {exc}"))
                warnings.warn(f"[quor] Adapter {agent_id!r} could not be instantiated: {exc}", stacklevel=2)

        self._adapters = instances
        return instances

    def all_adapters(self) -> list[AgentAdapter]:
        """Every successfully discovered adapter, built-in first, in a
        stable order (dict insertion order — built-ins are inserted before
        any third-party entry point)."""
        return list(self._discover().values())

    def find(self, agent_id: str) -> AgentAdapter | None:
        return self._discover().get(agent_id)

    @property
    def failures(self) -> list[AdapterFailureInfo]:
        """Populated only after `all_adapters()`/`find()` has run at least
        once (discovery is lazy) — mirrors `PluginLoadReport.failures`."""
        self._discover()
        return self._failures
