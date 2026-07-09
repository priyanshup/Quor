"""Hook dispatcher: run the real command and apply content filtering.

Called when Claude Code executes the rewritten command `quor git status`.
sys.argv[1:] = ["git", "status"] → subprocess runs git status, output filtered.

Execution order:
  subprocess → PRE_FILTER plugins → ContentMask filter → POST_FILTER plugins
    → tee (ADR-023, if enabled and output changed) → stdout

Tee is a dispatcher-level concern only — it never touches ContentMask,
Pipeline, or any StageHandler. See quor/pipeline/tee.py.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
import uuid
import warnings
from pathlib import Path

from quor.config.model import FilterConfig
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
from quor.tracking.db import InvocationRecord, TrackingDB, count_tokens


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

    # --- Run the real command ---
    # shutil.which() resolves shell-shim executables (npm.CMD, npx.CMD, etc.)
    # that CreateProcess cannot find by bare name without shell=True — see
    # ADR-033. Falls back to the original token unchanged if not found, so
    # the existing FileNotFoundError/OSError handling below still catches a
    # genuinely missing command exactly as before.
    resolved = shutil.which(args[0]) or args[0]
    try:
        proc = subprocess.run(
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

    captured = proc.stdout or ""

    # --- Tee cleanup: once per dispatch, throttled internally (ADR-023) ---
    _cleanup_tee_safe()

    # --- Lookup filter ---
    filter_config = None
    registry: FilterRegistry | None = None
    try:
        registry = FilterRegistry(project_root=Path.cwd())
        filter_config = registry.find(cmd_str)
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"[quor] filter registry error: {exc}", stacklevel=1)

    # --- Setup plugin pipeline (fail-open; empty registry = no-op) ---
    from quor.plugins.base import ExecutionMode, PluginCategory, PluginContext, PluginPayload
    from quor.plugins.registry import PluginRegistry

    plugin_registry = PluginRegistry()
    plugin_ctx: PluginContext | None = None

    try:
        from quor.config.loader import load_user_config
        from quor.pipeline.plugin_loader import discover_plugins

        discover_plugins(plugin_registry, use_cache=True, tier="user")
        if plugin_registry.all_plugins():
            mode_str = load_user_config().mode
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

    # --- PRE_FILTER plugins ---
    pre_output = captured
    if plugin_ctx is not None:
        try:
            pre_payload = PluginPayload(
                command=cmd_str,
                raw_output=captured,
                current_output=captured,
                content_type=detect(captured).value,
            )
            pre_payload = plugin_registry.run_category(
                PluginCategory.PRE_FILTER, pre_payload, plugin_ctx
            )
            pre_output = pre_payload.current_output
        except Exception as exc:  # noqa: BLE001
            warnings.warn(f"[quor] PRE_FILTER plugin error: {exc}", stacklevel=1)

    # --- Passthrough when no filter matches ---
    if filter_config is None or registry is None:
        _teardown_plugins(plugin_registry, plugin_ctx)
        _track(
            tracking,
            cmd_str=cmd_str,
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

    # --- Apply ContentMask filter ---
    content_type = detect(pre_output).value
    try:
        filtered = registry.apply(filter_config, pre_output, content_type=content_type)
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"[quor] filter apply error: {exc}", stacklevel=1)
        filtered = pre_output

    # --- POST_FILTER plugins ---
    if plugin_ctx is not None:
        try:
            post_payload = PluginPayload(
                command=cmd_str,
                raw_output=captured,
                current_output=filtered,
                content_type=content_type,
            )
            post_payload = plugin_registry.run_category(
                PluginCategory.POST_FILTER, post_payload, plugin_ctx
            )
            filtered = post_payload.current_output
        except Exception as exc:  # noqa: BLE001
            warnings.warn(f"[quor] POST_FILTER plugin error: {exc}", stacklevel=1)

    # --- Tee: cache raw output + append recovery footer if it changed (ADR-023) ---
    filtered = _apply_tee(filter_config, captured=captured, final_output=filtered)

    _teardown_plugins(plugin_registry, plugin_ctx)
    _track(
        tracking,
        cmd_str=cmd_str,
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

    sys.stdout.write(filtered)
    sys.stdout.flush()
    return proc.returncode


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


def _apply_tee(filter_config: FilterConfig, *, captured: str, final_output: str) -> str:
    """Tee the raw output and append a recovery footer, if warranted. Fail-open.

    Tee fires only when all of these hold:
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

    On any error, returns `final_output` unchanged — tee must never affect
    stdout or the exit code (ADR-018 fail-open).
    """
    try:
        from quor.config.loader import load_user_config

        user_config = load_user_config()
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
        return f"{final_output}\n[full output: {path}]"
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


def _track(
    tracking: TrackingDB | None,
    *,
    cmd_str: str,
    original: str,
    filtered: str,
    filter_name: str | None,
    was_passthrough: bool,
    t0: float,
) -> None:
    if tracking is None:
        return
    try:
        rec = InvocationRecord(
            command=cmd_str,
            project_path=Path.cwd().as_posix(),
            original_tokens=count_tokens(original),
            final_tokens=count_tokens(filtered),
            filter_name=filter_name,
            was_passthrough=was_passthrough,
            duration_ms=(time.monotonic() - t0) * 1000,
        )
        tracking.record(rec)
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"[quor] tracking record error: {exc}", stacklevel=1)
