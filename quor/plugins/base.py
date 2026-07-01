"""Public plugin API for Quor.

Everything in this module is stable public API. Backward-incompatible changes
require a new api_version and a Quor major version bump.

When to implement Plugin vs StageHandler
-----------------------------------------
Use ``quor.pipeline.stages.base.StageHandler`` when:
  - You want TOML-configurable, line-level ContentMask access.
  - You are writing a compression stage (strip, deduplicate, group, etc.).
  - The stage is stateless and does not need a lifecycle.

Use ``Plugin`` (this module) when:
  - You are doing something beyond content transformation: telemetry,
    policy enforcement, routing, observability, enrichment, summarization.
  - You need one-time initialization (network connections, config loading).
  - You need a shutdown hook for resource cleanup.
  - You want to communicate with other plugins via the annotations bag.

Plugin categories and execution order
--------------------------------------
PRE_FILTER  → FILTER  → POST_FILTER

Within each category, plugins run in ascending priority order (lower int
= earlier). Ties within the same tier are broken by registration order.

Fail-open contract
------------------
Unexpected exceptions from ``execute()`` are caught by the executor, logged
as warnings, and the original payload is passed through unchanged. This is
enforced by the executor, not the plugin — but plugin authors should still
treat ``execute()`` as "must not raise" and use ``try/except`` internally for
expected failure modes.

``initialize()`` MAY raise ``PluginError`` — the executor catches it, logs
a warning, and marks the plugin as permanently disabled for this session.

``shutdown()`` must not raise under any circumstances.

Dataclass construction
----------------------
All public dataclasses in this module are ``kw_only=True``. Always construct
them with keyword arguments:

    PluginMetadata(
        plugin_id="com.example.x",
        display_name="X",
        version="1.0.0",
        category=PluginCategory.FILTER,
    )

This ensures that new optional fields can be added to these types in future
Quor versions without ever changing positional argument order.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# API version
# ---------------------------------------------------------------------------

QUOR_PLUGIN_API_VERSION: int = 1
"""The current plugin API version.

Every Plugin must declare ``api_version = QUOR_PLUGIN_API_VERSION`` at class
level. Plugins with a mismatched version are rejected at registration time
with a warning and are never executed.
"""


# ---------------------------------------------------------------------------
# Execution mode
# ---------------------------------------------------------------------------


class ExecutionMode(StrEnum):
    """The active operating mode for the Quor pipeline.

    Plugins that want mode-aware behavior should inspect
    ``ctx.mode`` against these values.

    Because ``ExecutionMode`` inherits from ``str``, string comparisons work
    at runtime: ``ctx.mode == "optimize"`` is True when mode is OPTIMIZE.
    However, prefer comparing against ``ExecutionMode`` members for
    correctness and IDE discoverability.

    AUDIT     — pass all output through unchanged; record what would have
                been compressed but do not compress it.
    OPTIMIZE  — default mode; apply compression as configured.
    SIMULATE  — compress in memory and report savings, but write the
                original (uncompressed) output to stdout.

    Third-party plugins should treat any unrecognised mode value as
    ``OPTIMIZE`` for forwards compatibility.
    """

    AUDIT = "audit"
    OPTIMIZE = "optimize"
    SIMULATE = "simulate"


# ---------------------------------------------------------------------------
# Capability constants
# ---------------------------------------------------------------------------

CAPABILITY_CONTENT_TRANSFORM = "quor.content_transform"
"""Plugin modifies ``current_output`` in ``PluginPayload``."""

CAPABILITY_TELEMETRY = "quor.telemetry"
"""Plugin records metrics, logs, or traces to an external system."""

CAPABILITY_POLICY = "quor.policy"
"""Plugin may return ``PluginResult(abort=True)`` to halt the pipeline."""

CAPABILITY_ROUTING = "quor.routing"
"""Plugin inspects the command and annotates the payload for downstream
plugins (e.g. sets ``"quor.route"`` in annotations)."""

CAPABILITY_OBSERVABILITY = "quor.observability"
"""Plugin exposes pipeline data for external monitoring without side-effects
on the output."""

CAPABILITY_READ_ONLY = "quor.read_only"
"""Plugin does not modify ``current_output`` or return ``abort=True``.
May be used by future Quor versions to skip certain plugins in SIMULATE mode."""

# ---------------------------------------------------------------------------
# Plugin category
# ---------------------------------------------------------------------------


class PluginCategory(StrEnum):
    """Declares where in the execution pipeline a plugin runs.

    PRE_FILTER  — before content filtering. Use for routing decisions,
                  command classification, early-exit logic, or input
                  enrichment that later plugins depend on.

    FILTER      — during content transformation. Use for custom compression,
                  normalization, or rewriting. These plugins receive and
                  return ``current_output`` in ``PluginPayload``. If you
                  want line-level ContentMask access, implement
                  ``StageHandler`` instead.

    POST_FILTER — after all content transformation. Use for observability,
                  telemetry, policy enforcement, or post-processing steps
                  that must see the final output.
    """

    PRE_FILTER = "pre_filter"
    FILTER = "filter"
    POST_FILTER = "post_filter"


# ---------------------------------------------------------------------------
# Plugin metadata
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class PluginMetadata:
    """Static descriptive metadata for a plugin.

    Always construct with keyword arguments — this dataclass is ``kw_only``
    so that new optional fields can be added in future Quor versions without
    breaking existing code.

    Required fields: ``plugin_id``, ``display_name``, ``version``,
    ``category``. All other fields have defaults.

    ``plugin_id``
        Globally unique identifier. Use reverse-domain notation to avoid
        collisions: ``"com.example.my-plugin"``. Quor uses this as the
        canonical key in warning messages and in the plugin cache.

    ``display_name``
        Human-readable name shown in ``quor doctor`` and ``quor explain``.

    ``version``
        Semantic version string (``"1.0.0"``). Displayed in ``quor doctor``.
        Not interpreted by Quor — for human inspection only.

    ``category``
        Controls where in the pipeline this plugin runs.

    ``priority``
        Execution order within a category. Lower = earlier. Default 100.
        Plugins with the same priority within the same tier and category run
        in registration order (first registered = first executed). This is
        guaranteed by the registry via Python's stable sort.

    ``min_quor_version``
        The oldest Quor version this plugin is known to work with. Advisory
        only in v1 — Quor does not enforce it at registration time.

    ``capabilities``
        Declared capabilities. Use the ``CAPABILITY_*`` constants in this
        module for well-known capabilities, and namespaced strings for
        vendor-specific ones (``"com.example.my-capability"``).
        Advisory in v1 — Quor does not enforce or filter based on these.
    """

    plugin_id: str
    display_name: str
    version: str
    category: PluginCategory

    author: str = ""
    description: str = ""
    priority: int = 100
    min_quor_version: str = "0.1.0"
    capabilities: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Execution context
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class PluginContext:
    """Read-only runtime context passed to every plugin lifecycle call.

    Intentionally lean. Plugins receive what they need for most use cases
    without being coupled to Quor's internal implementation.

    Always construct with keyword arguments (``kw_only``). New optional
    fields (with defaults) may be added in future Quor versions.

    ``project_root``
        The directory Quor was invoked from (``Path.cwd()`` in the
        dispatcher). ``None`` in environments without a project (e.g. tests,
        CLI-only invocations). Use for reading project-local config files.

    ``mode``
        The active operating mode. Compare against ``ExecutionMode`` members.
        Because ``ExecutionMode`` is a ``StrEnum``, ``ctx.mode == "optimize"``
        also works at runtime.

    ``session_id``
        The AI session identifier extracted from the Claude Code hook payload.
        Empty string when running outside a hook (e.g. ``quor explain``).
        Stable within a single AI session; changes when the user starts a
        new conversation.

    ``invocation_id``
        A unique identifier for this specific pipeline run (uuid4 hex).
        Use for correlating events across plugins in observability systems.
        Empty string in Phase 8 (populated by the dispatcher once plugin
        loading is wired up in Phase 9).
    """

    project_root: Path | None
    mode: ExecutionMode
    session_id: str
    invocation_id: str


# ---------------------------------------------------------------------------
# Pipeline payload
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class PluginPayload:
    """The data envelope that flows through the plugin chain.

    Execution chain: ``raw_output`` → plugin₁ → plugin₂ → … → ``current_output``

    Always construct with keyword arguments (``kw_only``). New optional
    fields (with defaults) may be added in future Quor versions; helper
    methods ``replace_output()`` and ``with_annotation()`` propagate all
    fields, including new ones, transparently.

    ``command``
        The shell command string being processed (e.g. ``"git status"``).

    ``raw_output``
        The original subprocess stdout, captured before any transformation.
        No plugin may change this field — it is the immutable ground truth
        and is always available for comparison or fallback.

    ``current_output``
        The output after transformation by all preceding plugins. Each plugin
        reads this, optionally transforms it, and sets it in the returned
        ``PluginResult.payload``.

    ``content_type``
        The content type detected by ``quor.pipeline.content_type.detect()``.
        Examples: ``"diff"``, ``"json"``, ``"traceback"``, ``"text"``.
        Advisory; a PRE_FILTER plugin may update it via ``annotations`` for
        downstream plugins.

    ``annotations``
        A key-value store for inter-plugin communication. Use namespaced
        keys to avoid collisions: ``"com.example.my-key"``.
        Do not mutate ``annotations`` in place — use ``with_annotation()``
        or ``dataclasses.replace()`` to produce a new payload.

    Immutability note: this is a frozen dataclass, but ``annotations`` is a
    plain ``dict`` (mutable). Plugins must not mutate the dict directly;
    always return a new ``PluginPayload`` via the helper methods.
    """

    command: str
    raw_output: str
    current_output: str
    content_type: str
    annotations: dict[str, Any] = field(default_factory=dict)

    def with_annotation(self, key: str, value: Any) -> PluginPayload:
        """Return a new payload with one additional annotation set.

        Existing annotations are preserved. If the key already exists, the
        new value replaces it.
        """
        return replace(self, annotations={**self.annotations, key: value})

    def replace_output(self, new_output: str) -> PluginPayload:
        """Return a new payload with ``current_output`` replaced.

        A convenience shorthand for the common plugin pattern of modifying
        ``current_output`` while leaving all other fields unchanged.
        """
        return replace(self, current_output=new_output)


# ---------------------------------------------------------------------------
# Plugin result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class PluginResult:
    """What a plugin returns from ``execute()``.

    Always construct with keyword arguments (``kw_only``).

    Only ``payload`` is required. All other fields have defaults so that the
    minimal ``PluginResult(payload=payload)`` is always valid.

    ``payload``
        The (possibly modified) payload to pass to the next plugin in the
        chain. Return the input payload unchanged if this plugin made no
        modifications.

    ``was_modified``
        ``True`` if the plugin changed ``current_output``. Set this
        explicitly rather than relying on string comparison — it signals
        intent and enables the executor to skip unnecessary work.

    ``abort``
        ``True`` to stop the plugin chain after this plugin. Intended for
        policy enforcement: a plugin that decides the command's output
        should not be processed further can return ``abort=True``.
        This is *controlled* early termination, not a failure — the executor
        does not log a warning when ``abort=True``.

    ``note``
        Free-text description of what this plugin did, shown in
        ``quor explain`` output. Use it to explain the *why*, not the *what*.
        Empty string means this plugin's execution is omitted from explain
        output.
    """

    payload: PluginPayload
    was_modified: bool = False
    abort: bool = False
    note: str = ""


# ---------------------------------------------------------------------------
# Plugin Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Plugin(Protocol):
    """Protocol that every Quor plugin must implement.

    Class-level declaration required::

        from quor.plugins import (
            QUOR_PLUGIN_API_VERSION, ExecutionMode,
            Plugin, PluginCategory, PluginContext,
            PluginMetadata, PluginPayload, PluginResult,
        )
        from quor.errors import PluginError
        from typing import ClassVar

        class MyPlugin:
            api_version: ClassVar[int] = QUOR_PLUGIN_API_VERSION

            @property
            def metadata(self) -> PluginMetadata:
                return PluginMetadata(
                    plugin_id="com.example.my-plugin",
                    display_name="My Plugin",
                    version="1.0.0",
                    category=PluginCategory.POST_FILTER,
                )

            def initialize(self, ctx: PluginContext) -> None:
                pass  # or raise PluginError if the plugin cannot start

            def execute(self, payload: PluginPayload, ctx: PluginContext) -> PluginResult:
                return PluginResult(payload=payload)

            def shutdown(self) -> None:
                pass

    Plugins with ``api_version > QUOR_PLUGIN_API_VERSION`` are rejected at
    registration time. Plugins declaring an older ``api_version`` are accepted
    for forwards compatibility.

    Lifecycle
    ---------
    1. ``initialize(ctx)`` — called once when the plugin is registered,
       before the first ``execute()``. Raise ``PluginError`` if the plugin
       cannot operate (missing dependency, invalid config, etc.). The
       executor catches ``PluginError`` and permanently disables the plugin
       for this session, logging a warning.

    2. ``execute(payload, ctx)`` — called once per pipeline invocation.
       The executor guarantees fail-open behavior: unexpected exceptions are
       caught, logged, and the input payload is passed through unchanged.
       Treat this method as "must not raise" and handle expected failure
       modes internally.

    3. ``shutdown()`` — called when the host process exits or the plugin is
       unloaded. Must not raise under any circumstances. Use for cleanup
       only (closing connections, flushing buffers).

    Extension points
    ----------------
    The ``execute()`` signature is stable. New context fields go in
    ``PluginContext``; new payload fields go in ``PluginPayload`` or the
    ``annotations`` bag. Neither change requires modifying ``execute()``.
    """

    api_version: ClassVar[int]

    @property
    def metadata(self) -> PluginMetadata:
        """Return static metadata describing this plugin.

        Called at registration time. The result is cached by the registry.
        """
        ...

    def initialize(self, ctx: PluginContext) -> None:
        """One-time setup called before the first ``execute()``.

        Raise ``PluginError`` if the plugin cannot operate.
        """
        ...

    def execute(self, payload: PluginPayload, ctx: PluginContext) -> PluginResult:
        """Process one pipeline invocation.

        Must not raise. Handle expected failure modes internally and return
        the input payload unchanged on internal error.
        """
        ...

    def shutdown(self) -> None:
        """Resource cleanup. Called at process exit. Must not raise."""
        ...
