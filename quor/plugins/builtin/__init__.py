"""First-party plugins Quor ships with itself, registered at the "builtin"
tier (`quor.plugins.registry.PluginRegistry`'s lowest-precedence tier —
project/user plugins can still override or coexist). Distinct from the
third-party, entry-point-discovered plugins `quor.pipeline.plugin_loader`
finds: these are always present, never require installation, and are
instantiated directly by `quor.adapters.dispatcher._setup_plugins()` rather
than discovered via `importlib.metadata`.
"""

from __future__ import annotations
