"""Hook dispatcher: run the real command and apply content filtering.

Called when Claude Code executes the rewritten command `quor git status`.
sys.argv[1:] = ["git", "status"] → subprocess runs git status, output filtered.

Execution order:
  subprocess → PRE_FILTER plugins → ContentMask filter → POST_FILTER plugins
    → tee (ADR-023, if enabled and output changed) → concise-output
    instruction (if enabled) → stdout

Tee is a dispatcher-level concern only — it never touches ContentMask,
Pipeline, or any StageHandler. See quor/pipeline/tee.py.

The concise-output instruction is likewise dispatcher-level only: it is
prepended to the already-assembled output right before the final
`sys.stdout.write`, never fed back into ContentMask/tee/plugins, and only
applied when filtering actually changed the output (`filtered != captured`)
— true passthrough (`filter_config is None`) and a no-op filter match (e.g.
the generic fallback on already-clean output) both stay byte-identical to
`captured`, preserving the existing "original, unfiltered output" fail-open
contract. See CONCISE_INSTRUCTION_ENABLED below.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
import uuid
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from quor.config.loader import load_user_config
from quor.config.model import FilterConfig, QuorUserConfig
from quor.filters.registry import FilterRegistry
from quor.pipeline.content_type import detect
from quor.pipeline.onboarding import MAX_ONBOARDING_COMMANDS, record_filtered_command
from quor.pipeline.secrets import scan_for_secrets
from quor.pipeline.tee import (
    cleanup_tee,
    content_hash,
    get_tee_status,
    record_tee_failure,
    record_tee_success,
    write_tee,
)
from quor.tracking.db import TrackingDB, count_tokens, track_invocation

# ---------------------------------------------------------------------------
# Concise-output instruction — a short, generic nudge prepended to compressed
# output so the assistant favors concise, non-repetitive replies without
# changing what the user asked for. Flip CONCISE_INSTRUCTION_ENABLED or edit
# CONCISE_INSTRUCTION to disable or extend it; neither requires touching
# run_dispatch() itself.
# ---------------------------------------------------------------------------
CONCISE_INSTRUCTION_ENABLED = True

CONCISE_INSTRUCTION = "Respond concisely and avoid repeating information already stated.\n\n"


def _with_concise_instruction(text: str) -> str:
    """Prepend CONCISE_INSTRUCTION to `text` when enabled; no-op otherwise."""
    if not CONCISE_INSTRUCTION_ENABLED:
        return text
    return CONCISE_INSTRUCTION + text


if TYPE_CHECKING:
    # Deferred at runtime (see _setup_plugins/_run_pre_filter_plugins/
    # _run_post_filter_plugins): importing the plugin subsystem eagerly here
    # would make every `quor` invocation that imports this module pay its
    # import cost, not just invocations that actually dispatch a command.
    # This block is invisible to the interpreter (TYPE_CHECKING is always
    # False at runtime) and exists solely so the helpers below can carry
    # real type hints instead of `object`.
    from quor.plugins.base import PluginContext
    from quor.plugins.registry import PluginRegistry


def run_dispatch(args: list[str], tracking: TrackingDB | None = None) -> int:
    """Run `args` as a subprocess, apply filter and plugin pipeline, write to stdout.

    Returns the subprocess exit code. Never raises — any error falls through
    to unfiltered output (fail-open). If `tracking` is provided, records the
    invocation.

    Plugin pipeline (fail-open at each step):
      PRE_FILTER  — before ContentMask; plugins may annotate or modify raw output
      ContentMask — built-in TOML-configured compression stages
      POST_FILTER — after ContentMask; plugins may observe or transform final output
    """
    if not args:
        return 0

    cmd_str = " ".join(args)
    t0 = time.monotonic()

    result = _run_subprocess(args)
    if isinstance(result, int):
        return result
    proc = result
    captured = proc.stdout or ""

    # --- Tee cleanup: once per dispatch, throttled internally (ADR-023) ---
    _cleanup_tee_safe()

    # QuorUserConfig.toml is read+parsed+validated at most once per dispatch:
    # _setup_plugins() (only when plugins are discovered) and _apply_tee()
    # (whenever the non-passthrough path is reached) each previously called
    # load_user_config() independently, so a dispatch with both active read
    # the same on-disk file twice. get_user_config() is a plain memoizing
    # closure local to this one call — nothing is cached across dispatches
    # or processes, so this changes nothing about *what* is read, only how
    # many times.
    cached_user_config: QuorUserConfig | None = None

    def get_user_config() -> QuorUserConfig:
        nonlocal cached_user_config
        if cached_user_config is None:
            cached_user_config = load_user_config()
        return cached_user_config

    filter_config, registry = _lookup_filter(cmd_str)
    plugin_registry, plugin_ctx = _setup_plugins(get_user_config)

    pre_output, raw_content_type = _run_pre_filter_plugins(
        plugin_registry, plugin_ctx, cmd_str=cmd_str, captured=captured
    )

    # --- Passthrough when no filter matches ---
    if filter_config is None or registry is None:
        _teardown_plugins(plugin_registry, plugin_ctx)
        track_invocation(
            tracking,
            command=cmd_str,
            original=captured,
            filtered=pre_output,
            filter_name=None,
            was_passthrough=True,
            t0=t0,
        )
        _scan_secrets_safe(pre_output)
        sys.stdout.write(pre_output)
        sys.stdout.flush()
        return proc.returncode

    # detect() is a pure function of its text argument. When PRE_FILTER
    # plugins left the content byte-identical to `captured` (the common
    # case — most plugins annotate rather than transform), `raw_content_type`
    # (already computed on `captured` inside _run_pre_filter_plugins for the
    # plugin payload) is reused instead of re-scanning the same text a
    # second time. Any content_type-affecting change to `pre_output` still
    # gets a fresh detect() call, exactly as before.
    if raw_content_type is not None and pre_output == captured:
        content_type = raw_content_type
    else:
        content_type = detect(pre_output).value
    filtered = _apply_content_filter(
        registry, filter_config, pre_output, content_type=content_type
    )
    filtered = _run_post_filter_plugins(
        plugin_registry,
        plugin_ctx,
        cmd_str=cmd_str,
        captured=captured,
        filtered=filtered,
        content_type=content_type,
    )

    # Whether filtering actually changed anything, before tee's own footer
    # (which only ever appends on top of a genuine change) can add more —
    # gates the concise-output instruction below so a no-op filter match
    # (e.g. the generic fallback on already-clean output) stays byte-
    # identical to `captured`, exactly like true passthrough.
    content_changed = filtered != captured

    # --- Tee: cache raw output + append recovery footer if it changed (ADR-023) ---
    filtered = _apply_tee(
        filter_config, captured=captured, final_output=filtered, get_user_config=get_user_config
    )

    _teardown_plugins(plugin_registry, plugin_ctx)
    track_invocation(
        tracking,
        command=cmd_str,
        original=captured,
        filtered=filtered,
        filter_name=filter_config.name,
        was_passthrough=False,
        t0=t0,
    )
    _scan_secrets_safe(filtered)
    _maybe_print_onboarding_tip_safe(
        filter_name=filter_config.name,
        original_tokens=count_tokens(captured),
        final_tokens=count_tokens(filtered),
    )

    output = _with_concise_instruction(filtered) if content_changed else filtered
    sys.stdout.write(output)
    sys.stdout.flush()
    return proc.returncode


def _run_subprocess(args: list[str]) -> subprocess.CompletedProcess[str] | int:
    """Resolve and run the real command.

    Returns the `CompletedProcess` on success. On timeout or a missing/
    unrunnable executable, prints the same message to stderr as before and
    returns an int exit code (124/127) — the caller must return this
    immediately, exactly as the inlined try/except used to do.
    """
    # shutil.which() resolves shell-shim executables (npm.CMD, npx.CMD, etc.)
    # that CreateProcess cannot find by bare name without shell=True — see
    # ADR-033. Falls back to the original token unchanged if not found, so
    # the existing FileNotFoundError/OSError handling below still catches a
    # genuinely missing command exactly as before.
    resolved = shutil.which(args[0]) or args[0]
    try:
        return subprocess.run(
            [resolved, *args[1:]],
            stdout=subprocess.PIPE,
            stderr=None,       # inherit: real stderr flows to user directly
            encoding="utf-8",
            errors="replace",  # graceful handling of non-UTF-8 output
            timeout=25,        # 25 s leaves room for filter + plugins within 30 s hook budget
        )
    except subprocess.TimeoutExpired:
        print(f"[quor] command {args[0]!r} timed out after 25 s", file=sys.stderr)
        return 124
    except (OSError, FileNotFoundError) as exc:
        print(f"[quor] cannot run {args[0]!r}: {exc}", file=sys.stderr)
        return 127


def _lookup_filter(cmd_str: str) -> tuple[FilterConfig | None, FilterRegistry | None]:
    """Resolve the FilterConfig (if any) matching `cmd_str`. Fail-open: any
    registry error returns (None, None), which the caller already treats as
    passthrough regardless of which of the two was the actual cause."""
    try:
        registry = FilterRegistry(project_root=Path.cwd())
        return registry.find(cmd_str), registry
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"[quor] filter registry error: {exc}", stacklevel=1)
        return None, None


def _setup_plugins(
    get_user_config: Callable[[], QuorUserConfig] = load_user_config,
) -> tuple[PluginRegistry, PluginContext | None]:
    """Discover and initialize plugins for this invocation (fail-open; empty
    registry = no-op). Returns plugin_ctx=None if there are no plugins to
    run or discovery/initialization raised.

    `get_user_config` defaults to `load_user_config` itself (a fresh read)
    so any direct caller keeps today's exact behavior; `run_dispatch()`
    passes its own memoizing closure so this and `_apply_tee()` share one
    read of config.toml per dispatch instead of each doing their own.
    """
    from quor.plugins.base import ExecutionMode, PluginContext
    from quor.plugins.registry import PluginRegistry

    plugin_registry = PluginRegistry()
    plugin_ctx: PluginContext | None = None

    try:
        from quor.pipeline.plugin_loader import discover_plugins

        discover_plugins(plugin_registry, use_cache=True, tier="user")
        if plugin_registry.all_plugins():
            mode_str = get_user_config().mode
            try:
                mode = ExecutionMode(mode_str)
            except ValueError:
                mode = ExecutionMode.OPTIMIZE

            plugin_ctx = PluginContext(
                project_root=Path.cwd(),
                mode=mode,
                session_id="",
                invocation_id=uuid.uuid4().hex,
            )
            plugin_registry.initialize_all(plugin_ctx)
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"[quor] plugin discovery error: {exc}", stacklevel=1)
        plugin_ctx = None

    return plugin_registry, plugin_ctx


def _run_pre_filter_plugins(
    plugin_registry: PluginRegistry,
    plugin_ctx: PluginContext | None,
    *,
    cmd_str: str,
    captured: str,
) -> tuple[str, str | None]:
    """Run PRE_FILTER plugins against the raw captured output. Fail-open:
    returns `(captured, None)` unchanged if there are no active plugins or a
    plugin raises.

    Also returns the `content_type` detect() computed on `captured` for the
    plugin payload (or None if it was never computed, i.e. no active
    plugins) — the caller reuses this instead of calling detect() a second
    time on the same text when PRE_FILTER left the content unchanged.
    """
    if plugin_ctx is None:
        return captured, None
    try:
        from quor.plugins.base import PluginCategory, PluginPayload

        content_type = detect(captured).value
        pre_payload = PluginPayload(
            command=cmd_str,
            raw_output=captured,
            current_output=captured,
            content_type=content_type,
        )
        pre_payload = plugin_registry.run_category(
            PluginCategory.PRE_FILTER, pre_payload, plugin_ctx
        )
        return pre_payload.current_output, content_type
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"[quor] PRE_FILTER plugin error: {exc}", stacklevel=1)
        return captured, None


def _apply_content_filter(
    registry: FilterRegistry,
    filter_config: FilterConfig,
    pre_output: str,
    *,
    content_type: str,
) -> str:
    """Run the matched ContentMask filter. Fail-open: returns `pre_output`
    unchanged if the filter raises."""
    try:
        return registry.apply(filter_config, pre_output, content_type=content_type)
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"[quor] filter apply error: {exc}", stacklevel=1)
        return pre_output


def _run_post_filter_plugins(
    plugin_registry: PluginRegistry,
    plugin_ctx: PluginContext | None,
    *,
    cmd_str: str,
    captured: str,
    filtered: str,
    content_type: str,
) -> str:
    """Run POST_FILTER plugins against the filtered output. Fail-open:
    returns `filtered` unchanged if there are no active plugins or a plugin
    raises."""
    if plugin_ctx is None:
        return filtered
    try:
        from quor.plugins.base import PluginCategory, PluginPayload

        post_payload = PluginPayload(
            command=cmd_str,
            raw_output=captured,
            current_output=filtered,
            content_type=content_type,
        )
        post_payload = plugin_registry.run_category(
            PluginCategory.POST_FILTER, post_payload, plugin_ctx
        )
        return post_payload.current_output
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"[quor] POST_FILTER plugin error: {exc}", stacklevel=1)
        return filtered


def _cleanup_tee_safe() -> None:
    """Run tee cleanup, fail-open. cleanup_tee() throttles itself internally."""
    try:
        cleanup_tee()
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"[quor] tee cleanup error: {exc}", stacklevel=1)


def _scan_secrets_safe(content: str) -> None:
    """Warn on stderr if a known secret pattern is present in output that's
    about to be written to stdout (PA-F07). Detection only — stdout is never
    modified, regardless of what's found. Fail-open: an error here must
    never affect the hook's real output."""
    try:
        found = scan_for_secrets(content)
        if found:
            warnings.warn(
                f"[quor] Possible secret detected in output ({', '.join(found)}) "
                "— output was not modified or redacted.",
                stacklevel=1,
            )
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"[quor] secret scan error: {exc}", stacklevel=1)


def _maybe_print_onboarding_tip_safe(
    *, filter_name: str, original_tokens: int, final_tokens: int
) -> None:
    """Print a brief stats line to stderr for each of the first 5 filtered
    commands; silent from the 6th onward (PA-F08). Fail-open: an error here
    must never affect the hook's real output."""
    try:
        sequence = record_filtered_command()
        if sequence is None:
            return
        saved = original_tokens - final_tokens
        # A small/already-clean output's tee recovery footer can exceed
        # genuine compression savings, producing a net-negative result that
        # isn't a bug (QB-017) — but showing a scary "-62% smaller" in a new
        # user's very first impression of the tool would look exactly like
        # one. Mirror quor gain's own fix for the identical phenomenon:
        # reframe as a neutral net rather than a misleading percentage.
        if saved > 0:
            stats = f"from {original_tokens} to {final_tokens} tokens (~{saved / original_tokens:.0%} smaller)"
        else:
            stats = f"net {original_tokens} to {final_tokens} tokens (already small/clean output)"
        print(
            f"[quor] Tip ({sequence}/{MAX_ONBOARDING_COMMANDS}): compressed "
            f"'{filter_name}' output {stats}. Run `quor gain` anytime to see "
            "your total savings.",
            file=sys.stderr,
        )
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"[quor] onboarding tip error: {exc}", stacklevel=1)


def _apply_tee(
    filter_config: FilterConfig,
    *,
    captured: str,
    final_output: str,
    get_user_config: Callable[[], QuorUserConfig] = load_user_config,
) -> str:
    """Tee the raw output, and append a recovery footer only when doing so
    doesn't cost more tokens than the filter actually saved. Fail-open.

    Tee fires (i.e. `write_tee()` runs and the failure counter is updated)
    whenever all of these hold:
      - the global kill-switch (QuorUserConfig.tee_enabled) is on
      - this filter has not opted out (FilterConfig.tee is not False)
      - the final output actually differs from the true raw subprocess output
        (nothing to recover otherwise — e.g. abort_unless/abort_if short-circuits)
      - tee has not adaptively disabled itself after repeated filesystem
        write failures (see quor/pipeline/tee.py's "Adaptive fallback")

    A write_tee() failure caused by the filesystem (OSError — permission
    denied, corporate policy, disk full, etc.) is recorded; after
    MAX_CONSECUTIVE_TEE_FAILURES (quor.pipeline.tee) in a row, tee persists
    itself as disabled and stops attempting writes entirely until
    `quor doctor --reset-tee` — no automatic retry.

    QB-052: the raw file is always written on a successful tee (recoverability
    is preserved unconditionally), but the visible `"\n[full output: <path>]"`
    footer is only appended to stdout when doing so keeps the total token
    count at or below the true raw output's — i.e. when the filter's own
    compression saved at least as many tokens as the footer costs. `mypy` and
    other filters with small, mostly-non-repetitive real-world output were
    consistently landing net-negative (QB-052's real-usage finding) purely
    because this fixed-cost footer outweighed genuine, real savings; the
    footer itself was the cause, not the filter's compression logic. Uses
    `count_tokens()` (the same estimator every other token figure in Quor is
    built from) — a direct comparison, not a new heuristic or threshold.

    On any error, returns `final_output` unchanged — tee must never affect
    stdout or the exit code (ADR-018 fail-open).

    `get_user_config` defaults to `load_user_config` itself (a fresh read),
    same rationale as `_setup_plugins`' own parameter of the same name.
    """
    try:
        user_config = get_user_config()
        if not (user_config.tee_enabled and filter_config.tee):
            return final_output

        if content_hash(final_output) == content_hash(captured):
            return final_output

        if get_tee_status().disabled:
            return final_output

        try:
            path = write_tee(captured)
        except OSError as exc:
            record_tee_failure(str(exc))
            return final_output

        record_tee_success()

        with_footer = f"{final_output}\n[full output: {path}]"
        if count_tokens(with_footer) > count_tokens(captured):
            return final_output
        return with_footer
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"[quor] tee error: {exc}", stacklevel=1)
        return final_output


def _teardown_plugins(
    plugin_registry: object, plugin_ctx: object | None
) -> None:
    """Shutdown all plugins if they were initialized. Fail-open."""
    if plugin_ctx is None:
        return
    try:
        from quor.plugins.registry import PluginRegistry as _PR

        if isinstance(plugin_registry, _PR):
            plugin_registry.shutdown_all()
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"[quor] plugin shutdown error: {exc}", stacklevel=1)
