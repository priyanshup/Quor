"""Quor plugin public API.

Third-party plugins import from ``quor.plugins`` (this module) or directly
from ``quor.plugins.base``. Both are stable public API.

Quick reference::

    from quor.plugins import (
        QUOR_PLUGIN_API_VERSION,
        ExecutionMode,
        Plugin, PluginCategory, PluginContext,
        PluginMetadata, PluginPayload, PluginResult,
        PluginError,
        CAPABILITY_CONTENT_TRANSFORM,
        CAPABILITY_TELEMETRY,
        CAPABILITY_POLICY,
        CAPABILITY_ROUTING,
        CAPABILITY_OBSERVABILITY,
        CAPABILITY_READ_ONLY,
    )
    # Internal use only — not part of the third-party plugin API:
    from quor.plugins import PluginRegistry
"""

from quor.errors import PluginError
from quor.plugins.base import (
    CAPABILITY_CONTENT_TRANSFORM,
    CAPABILITY_OBSERVABILITY,
    CAPABILITY_POLICY,
    CAPABILITY_READ_ONLY,
    CAPABILITY_ROUTING,
    CAPABILITY_TELEMETRY,
    QUOR_PLUGIN_API_VERSION,
    ExecutionMode,
    Plugin,
    PluginCategory,
    PluginContext,
    PluginMetadata,
    PluginPayload,
    PluginResult,
)
from quor.plugins.registry import PluginRegistry

__all__ = [
    "CAPABILITY_CONTENT_TRANSFORM",
    "CAPABILITY_OBSERVABILITY",
    "CAPABILITY_POLICY",
    "CAPABILITY_READ_ONLY",
    "CAPABILITY_ROUTING",
    "CAPABILITY_TELEMETRY",
    "QUOR_PLUGIN_API_VERSION",
    "ExecutionMode",
    "Plugin",
    "PluginCategory",
    "PluginContext",
    "PluginError",
    "PluginMetadata",
    "PluginPayload",
    "PluginRegistry",
    "PluginResult",
]
