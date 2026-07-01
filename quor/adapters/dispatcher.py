"""Hook dispatcher: run the real command and apply content filtering.

Called when Claude Code executes the rewritten command `quor git status`.
sys.argv[1:] = ["git", "status"] → subprocess runs git status, output filtered.
"""

from __future__ import annotations

import subprocess
import sys
import time
import warnings
from pathlib import Path

from quor.filters.registry import FilterRegistry
from quor.pipeline.content_type import detect
from quor.tracking.db import InvocationRecord, TrackingDB, count_tokens


def run_dispatch(args: list[str], tracking: TrackingDB | None = None) -> int:
    """Run `args` as a subprocess, apply filter, write to stdout.

    Returns the subprocess exit code. Never raises — any error falls through
    to unfiltered output (fail-open). If `tracking` is provided, records the
    invocation in the background (non-blocking).
    """
    if not args:
        return 0

    cmd_str = " ".join(args)
    t0 = time.monotonic()

    # --- Run the real command ---
    try:
        proc = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=None,       # inherit: real stderr flows to user directly
            encoding="utf-8",
            errors="replace",  # graceful handling of non-UTF-8 output
            timeout=25,        # 25 s leaves room for filter + tracking within 30 s hook budget
        )
    except subprocess.TimeoutExpired:
        print(f"[quor] command {args[0]!r} timed out after 25 s", file=sys.stderr)
        return 124
    except (OSError, FileNotFoundError) as exc:
        print(f"[quor] cannot run {args[0]!r}: {exc}", file=sys.stderr)
        return 127

    captured = proc.stdout or ""

    # --- Lookup filter ---
    filter_config = None
    registry: FilterRegistry | None = None
    try:
        registry = FilterRegistry(project_root=Path.cwd())
        filter_config = registry.find(cmd_str)
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"[quor] filter registry error: {exc}", stacklevel=1)

    if filter_config is None or registry is None:
        _track(
            tracking,
            cmd_str=cmd_str,
            original=captured,
            filtered=captured,
            filter_name=None,
            was_passthrough=True,
            t0=t0,
        )
        sys.stdout.write(captured)
        sys.stdout.flush()
        return proc.returncode

    # --- Apply filter ---
    try:
        content_type = detect(captured).value
        filtered = registry.apply(filter_config, captured, content_type=content_type)
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"[quor] filter apply error: {exc}", stacklevel=1)
        filtered = captured  # fail-open

    _track(
        tracking,
        cmd_str=cmd_str,
        original=captured,
        filtered=filtered,
        filter_name=filter_config.name,
        was_passthrough=False,
        t0=t0,
    )

    sys.stdout.write(filtered)
    sys.stdout.flush()
    return proc.returncode


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
