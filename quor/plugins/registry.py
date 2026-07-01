"""In-memory plugin registry.

Handles registration, precedence, lifecycle, and fail-open execution of
Plugin instances. Discovery (entry-point scanning and caching) is
intentionally absent — that is Phase 9. This registry is a pure in-memory
store with a stable public interface that Phase 9 will populate.

Three-tier precedence (highest → lowest):
  project  — plugins registered for this specific project
  user     — plugins installed by the user
  builtin  — plugins shipped with Quor

Execution ordering within a category
--------------------------------------
For each category (PRE_FILTER → FILTER → POST_FILTER):
  1. project tier plugins, sorted by ascending priority
  2. user tier plugins, sorted by ascending priority
  3. builtin tier plugins, sorted by ascending priority

Stable tie-breaking: plugins within the same tier and category that share
equal priority values are executed in registration order (first registered
= first executed). This is guaranteed by Python's stable sort (timsort)
combined with dicts preserving insertion order (Python 3.7+). Plugin
authors may rely on this guarantee.

Fail-open contract
------------------
- ``execute()`` exceptions: caught, logged as a warning, payload passed
  through unchanged, remaining plugins continue executing.
- ``PluginResult.abort = True``: chain stops; no warning emitted.
- ``initialize()`` exceptions: plugin permanently disabled for the session,
  removed from the registry, plugin_id returned in failed list.
- ``shutdown()`` exceptions: caught and warned; never propagated.
"""

from __future__ import annotations

import warnings
from typing import Literal

from quor.errors import PluginError
from quor.plugins.base import (
    QUOR_PLUGIN_API_VERSION,
    Plugin,
    PluginCategory,
    PluginContext,
    PluginPayload,
)

_Tier = Literal["project", "user", "builtin"]

# Ordered highest-precedence first; index == rank.
_TIERS: tuple[_Tier, ...] = ("project", "user", "builtin")
_TIER_RANK: dict[_Tier, int] = {t: i for i, t in enumerate(_TIERS)}


class PluginRegistry:
    """In-memory store and executor for registered Plugin instances.

    Typical usage::

        registry = PluginRegistry()
        registry.register(MyPlugin(), tier="user")
        ctx = PluginContext(project_root=..., mode=ExecutionMode.OPTIMIZE, ...)
        failed = registry.initialize_all(ctx)

        # Run the full pipeline in one call:
        payload = registry.run_pipeline(payload, ctx)

        # Or run individual phases around the ContentMask pipeline:
        payload = registry.run_category(PluginCategory.PRE_FILTER, payload, ctx)
        output = filter_registry.apply(filter_config, payload.current_output)
        payload = payload.replace_output(output)
        payload = registry.run_category(PluginCategory.POST_FILTER, payload, ctx)

        registry.shutdown_all()
    """

    def __init__(self) -> None:
        # tier → {plugin_id → plugin}, insertion order preserved
        self._tiers: dict[_Tier, dict[str, Plugin]] = {t: {} for t in _TIERS}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, plugin: Plugin, tier: _Tier = "user") -> None:
        """Register a plugin instance at the given tier.

        Validation (raises ``PluginError`` on failure):
        - The object must satisfy the Plugin Protocol structurally.
        - ``plugin.api_version`` must be an ``int`` <= ``QUOR_PLUGIN_API_VERSION``.
          A plugin declaring a newer API version than Quor supports is rejected.

        Warnings (does not raise):
        - Same plugin_id already registered at the same tier → replaces,
          warns "already registered".
        - Same plugin_id registered at a higher-priority tier → accepts,
          warns "will never run" (the higher-tier instance shadows it).
        """
        if not isinstance(plugin, Plugin):
            raise PluginError(
                f"Object {plugin!r} does not satisfy the Plugin Protocol"
            )

        if not isinstance(plugin.api_version, int) or plugin.api_version > QUOR_PLUGIN_API_VERSION:
            raise PluginError(
                f"Plugin api_version {plugin.api_version!r} is not supported "
                f"(expected <= {QUOR_PLUGIN_API_VERSION}). "
                "Check that the plugin is compatible with this version of Quor."
            )

        plugin_id = plugin.metadata.plugin_id
        tier_store = self._tiers[tier]

        if plugin_id in tier_store:
            warnings.warn(
                f"[quor] Plugin {plugin_id!r} already registered at tier {tier!r}; "
                "replacing with new instance.",
                stacklevel=2,
            )

        current_rank = _TIER_RANK[tier]
        for other_tier in _TIERS:
            if _TIER_RANK[other_tier] < current_rank and plugin_id in self._tiers[other_tier]:
                warnings.warn(
                    f"[quor] Plugin {plugin_id!r} is already registered at tier "
                    f"{other_tier!r} (higher priority than {tier!r}). "
                    "The lower-tier registration is accepted but will never run.",
                    stacklevel=2,
                )
                break

        tier_store[plugin_id] = plugin

    def unregister(self, plugin_id: str, tier: _Tier = "user") -> bool:
        """Remove a plugin by id from the given tier.

        Returns ``True`` if the plugin was found and removed, ``False`` if
        no plugin with that id was registered at that tier.
        """
        return self._tiers[tier].pop(plugin_id, None) is not None

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, plugin_id: str, tier: _Tier | None = None) -> Plugin | None:
        """Return the plugin with the given id, or ``None`` if not found.

        If ``tier`` is given, only that tier is searched.
        If ``tier`` is ``None`` (default), all tiers are searched in
        precedence order (project → user → builtin). The first match wins,
        meaning the highest-priority-tier instance is returned when the same
        id exists in multiple tiers.
        """
        if tier is not None:
            return self._tiers[tier].get(plugin_id)
        for t in _TIERS:
            plugin = self._tiers[t].get(plugin_id)
            if plugin is not None:
                return plugin
        return None

    def plugins_for_category(self, category: PluginCategory) -> list[Plugin]:
        """Return all plugins for a category in execution order.

        Order: project → user → builtin; within each tier, ascending
        priority (lower int = earlier). Equal priorities within the same
        tier preserve registration order.
        """
        result: list[Plugin] = []
        for tier in _TIERS:
            tier_plugins = [
                p
                for p in self._tiers[tier].values()
                if p.metadata.category is category
            ]
            tier_plugins.sort(key=lambda p: p.metadata.priority)
            result.extend(tier_plugins)
        return result

    def all_plugins(self) -> list[Plugin]:
        """Return all registered plugins in full execution order.

        Order: PRE_FILTER → FILTER → POST_FILTER; within each category,
        project → user → builtin; within each tier, ascending priority.
        Equal priorities within the same tier and category preserve
        registration order.
        """
        result: list[Plugin] = []
        for category in (
            PluginCategory.PRE_FILTER,
            PluginCategory.FILTER,
            PluginCategory.POST_FILTER,
        ):
            result.extend(self.plugins_for_category(category))
        return result

    def capabilities(self) -> frozenset[str]:
        """Return the union of all capability strings declared by registered plugins.

        Only reflects the current registered set; capabilities change as
        plugins are added or removed. Returns an empty frozenset when no
        plugins are registered or no plugins declare capabilities.
        """
        result: set[str] = set()
        for plugin in self.all_plugins():
            result.update(plugin.metadata.capabilities)
        return frozenset(result)

    def plugins_with_capability(self, capability: str) -> list[Plugin]:
        """Return all registered plugins that declared the given capability.

        Results are in execution order (same as ``all_plugins()``). Plugins
        that did not declare the capability are excluded.

        Capability strings are case-sensitive. Use the ``CAPABILITY_*``
        constants from ``quor.plugins`` for well-known capabilities.
        """
        return [p for p in self.all_plugins() if capability in p.metadata.capabilities]

    def count(self) -> dict[str, int]:
        """Return plugin counts keyed by tier name."""
        return {tier: len(plugins) for tier, plugins in self._tiers.items()}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize_all(self, ctx: PluginContext) -> list[str]:
        """Call ``initialize(ctx)`` on every registered plugin.

        Plugins that raise ``PluginError`` or any unexpected exception are
        permanently disabled for this session: removed from the registry and
        their plugin_id is added to the returned failure list. A warning is
        emitted for every failure.

        Returns a list of plugin_ids that failed initialization.
        """
        failed: list[str] = []
        for tier in _TIERS:
            for plugin_id, plugin in list(self._tiers[tier].items()):
                try:
                    plugin.initialize(ctx)
                except PluginError as exc:
                    warnings.warn(
                        f"[quor] Plugin {plugin_id!r} failed to initialize: {exc}; "
                        "plugin will be disabled for this session.",
                        stacklevel=2,
                    )
                    self._tiers[tier].pop(plugin_id)
                    failed.append(plugin_id)
                except Exception as exc:  # noqa: BLE001
                    warnings.warn(
                        f"[quor] Plugin {plugin_id!r} raised an unexpected error "
                        f"during initialization: {exc}; plugin will be disabled.",
                        stacklevel=2,
                    )
                    self._tiers[tier].pop(plugin_id)
                    failed.append(plugin_id)
        return failed

    def shutdown_all(self) -> None:
        """Call ``shutdown()`` on every registered plugin.

        All exceptions are caught and logged as warnings — shutdown must
        never prevent the process from exiting.
        """
        for tier in _TIERS:
            for plugin_id, plugin in self._tiers[tier].items():
                try:
                    plugin.shutdown()
                except Exception as exc:  # noqa: BLE001
                    warnings.warn(
                        f"[quor] Plugin {plugin_id!r} raised during shutdown: {exc}",
                        stacklevel=2,
                    )

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run_pipeline(
        self,
        payload: PluginPayload,
        ctx: PluginContext,
    ) -> PluginPayload:
        """Run all plugins across all categories in execution order, fail-open.

        Equivalent to calling ``run_category`` for each category in sequence.
        Use this when the plugin pipeline runs as a single unit. Use
        ``run_category`` instead when you need to interleave the plugin
        pipeline with Quor's ContentMask filter pipeline.

        Fail-open contract:
        - Unexpected exceptions from ``execute()`` are caught, logged, and
          the payload is passed through unchanged. Remaining plugins continue.
        - ``PluginResult.abort = True`` stops the chain immediately. No
          warning is emitted — this is intentional controlled flow.
        """
        return self._execute_plugins(self.all_plugins(), payload, ctx)

    def run_category(
        self,
        category: PluginCategory,
        payload: PluginPayload,
        ctx: PluginContext,
    ) -> PluginPayload:
        """Run only plugins in the given category, fail-open.

        Designed for interleaving with the ContentMask filter pipeline::

            payload = registry.run_category(PluginCategory.PRE_FILTER, payload, ctx)
            output  = filter_registry.apply(filter_config, payload.current_output)
            payload = payload.replace_output(output)
            payload = registry.run_category(PluginCategory.POST_FILTER, payload, ctx)

        The same fail-open contract as ``run_pipeline`` applies.
        """
        return self._execute_plugins(self.plugins_for_category(category), payload, ctx)

    def _execute_plugins(
        self,
        plugins: list[Plugin],
        payload: PluginPayload,
        ctx: PluginContext,
    ) -> PluginPayload:
        """Run a specific list of plugins in order, fail-open.

        Shared implementation for ``run_pipeline`` and ``run_category``.
        Not part of the public API — callers should use the public methods.
        """
        current = payload
        for plugin in plugins:
            plugin_id = plugin.metadata.plugin_id
            try:
                result = plugin.execute(current, ctx)
            except Exception as exc:  # noqa: BLE001
                warnings.warn(
                    f"[quor] Plugin {plugin_id!r} raised during execute(): {exc}; "
                    "passing payload through unchanged (fail-open).",
                    stacklevel=2,
                )
                continue
            current = result.payload
            if result.abort:
                break
        return current
