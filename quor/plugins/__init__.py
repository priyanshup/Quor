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
    from quor.plugins.registry import PluginRegistry  # Quor-internal use
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
    "PluginResult",
]
