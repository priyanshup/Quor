"""quor doctor — health check: dependencies, tracking DB, filters, mode, tee, plugins.

QB-104: every hook-specific check (script installed/registered/up to date,
adapter discovery, hook collision, hook roundtrip) was removed along with
the hook-based integration itself — Quor's MCP server has nothing
file-based to install or go stale, so there is nothing left for `doctor` to
diagnose on that front. Anyone with a leftover pre-QB-104 hook installation
should run `quor uninstall-hooks`, not `quor doctor`.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import platformdirs
import typer
from rich.console import Console
from rich.markup import escape

from quor.config.loader import load_user_config
from quor.config.model import QuorUserConfig
from quor.errors import ExitCode
from quor.filters.registry import FilterRegistry

console = Console()

_REQUIRED_PACKAGES = ("typer", "pydantic", "orjson", "platformdirs", "regex", "rich", "mcp")


def doctor(
    reset_tee: bool = typer.Option(
        False,
        "--reset-tee",
        help="Clear tee's adaptive-disable state and re-enable it after fixing a filesystem issue.",
    ),
) -> None:
    """Run health checks and print a summary with colored status indicators."""
    _run_doctor(reset_tee=reset_tee)


def _run_doctor(*, reset_tee: bool = False) -> None:
    """The actual health-check logic, callable as plain Python."""
    t0 = time.monotonic()

    if reset_tee:
        from quor.pipeline.tee import reset_tee_state

        try:
            reset_tee_state()
            console.print("[green]Tee adaptive-disable state cleared.[/green]")
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]Could not reset tee state: {exc}[/red]")

    console.print("[bold]Quor Doctor[/bold]\n")

    checks: list[tuple[str, bool, str]] = []

    checks.append(_check_python_version())
    checks.extend(_check_dependencies())
    checks.append(_check_sqlite())
    checks.append(_check_filters())
    # Loaded once and shared: _check_mode()/_check_tee() each need
    # QuorUserConfig, and both always run in the same doctor invocation, so
    # loading config.toml separately for each was a guaranteed duplicate
    # read+parse+validate every single `quor doctor` run.
    user_config = load_user_config()
    checks.append(_check_mode(user_config))
    checks.append(_check_tee(user_config))
    checks.append(_check_tee_size(user_config))
    checks.append(_check_plugins())
    checks.append(_check_negative_compression_filters())

    all_ok = True
    for name, ok, detail in checks:
        _print_check_line(name, ok, detail)
        all_ok = all_ok and ok

    elapsed = time.monotonic() - t0
    total = len(checks)
    if all_ok:
        console.print(f"\n[green]✓ {total} of {total} checks passed[/green] in {elapsed:.1f}s")
    else:
        failed = sum(1 for _, ok, _ in checks if not ok)
        console.print(f"\n[red]✗ {failed} of {total} checks failed[/red] in {elapsed:.1f}s — see above for details")

    if not all_ok:
        raise typer.Exit(code=ExitCode.GENERAL_ERROR)


def _print_check_line(name: str, ok: bool, detail: str) -> None:
    symbol = "[green]✓[/green]" if ok else "[red]✗[/red]"
    # escape(): `name`/`detail` are dynamic text that can contain literal
    # square brackets (a path segment, or "quor[javascript]" in an extras
    # hint) — Rich's markup parser otherwise reads "[javascript]" as an
    # (unrecognized, silently dropped) style tag, not literal text. Only
    # `symbol`'s own hardcoded `[green]...[/green]` is meant to be parsed
    # as markup here.
    suffix = f" — {escape(detail)}" if detail else ""
    # soft_wrap: detail strings can embed long filesystem paths — Rich's
    # default word-wrap would otherwise split it mid-phrase across two
    # lines, which is both harder to read and breaks a clean copy-paste.
    console.print(f"{symbol} {escape(name)}{suffix}", soft_wrap=True)


def _check_python_version() -> tuple[str, bool, str]:
    ok = sys.version_info >= (3, 11)
    return ("Python ≥ 3.11", ok, f"{sys.version_info.major}.{sys.version_info.minor}")


def _check_dependencies() -> list[tuple[str, bool, str]]:
    import importlib

    results: list[tuple[str, bool, str]] = []
    for pkg in _REQUIRED_PACKAGES:
        try:
            importlib.import_module(pkg)
            results.append((f"Dependency '{pkg}'", True, ""))
        except ImportError as exc:
            results.append((f"Dependency '{pkg}'", False, str(exc)))
    return results


def _check_sqlite() -> tuple[str, bool, str]:
    from quor.tracking.db import get_tracking_db

    try:
        db = get_tracking_db()
        db.flush()
        db.close()
        return ("Tracking DB readable/writable", True, "")
    except Exception as exc:  # noqa: BLE001
        return ("Tracking DB readable/writable", False, str(exc))


def _check_filters() -> tuple[str, bool, str]:
    registry = FilterRegistry(project_root=Path.cwd())
    total_failures = 0
    total_skipped = 0
    for _, filter_config in registry.all_filters():
        result = registry.run_tests(filter_config)
        total_failures += len(result.failures)
        total_skipped += len(result.skipped)
    if total_failures:
        return ("Built-in filter tests pass", False, f"{total_failures} inline test failure(s)")
    detail = (
        f"{total_skipped} test(s) skipped — optional AST dependency not installed "
        "(quor[javascript])"
        if total_skipped
        else ""
    )
    return ("Built-in filter tests pass", True, detail)


def _check_mode(user_config: QuorUserConfig) -> tuple[str, bool, str]:
    return (f"Mode: {user_config.mode}", True, "")


def _check_tee(user_config: QuorUserConfig) -> tuple[str, bool, str]:
    """Report tee's status (ADR-023 / QB-013 adaptive fallback).

    Three distinct states, only one of which is flagged as a problem:
      - enabled — normal, healthy state.
      - deliberately disabled by the user (QuorUserConfig.tee_enabled /
        QUOR_TEE_ENABLED) — intentional, not a problem.
      - adaptively disabled after repeated filesystem write failures — a
        real problem worth surfacing (✗), since it means recovery footers
        have silently stopped being written.
    """
    from quor.pipeline.tee import get_tee_status

    try:
        status = get_tee_status()
    except Exception as exc:  # noqa: BLE001
        return ("Tee", True, f"(could not check: {exc})")

    if status.disabled:
        hint = (
            f"auto-disabled after {status.consecutive_failures} consecutive write "
            f"failures ({status.disabled_reason}) — fix the underlying filesystem "
            "issue, then run `quor doctor --reset-tee` to re-enable"
        )
        return ("Tee: disabled (filesystem unavailable)", False, hint)

    if not user_config.tee_enabled:
        return ("Tee: disabled (disabled in config)", True, "")

    return ("Tee: enabled", True, "")


def _check_tee_size(user_config: QuorUserConfig) -> tuple[str, bool, str]:
    """Report the tee cache's current disk usage against its configured
    ceiling (ADR-023, QB-103).

    Read-only — never triggers cleanup itself; eviction only happens the
    next time cleanup_tee() actually runs (dispatcher-triggered, throttled
    to at most once per 24h). Advisory only: being over the limit is
    expected to self-correct on the next scheduled cleanup pass, so it's
    surfaced as information, not a failure — it must never block the rest
    of `quor doctor`'s real diagnostic checks.
    """
    from quor.pipeline.tee import current_tee_size_bytes

    try:
        size_bytes = current_tee_size_bytes()
    except Exception as exc:  # noqa: BLE001
        return ("Tee cache size", True, f"(could not check: {exc})")

    limit_bytes = user_config.tee_max_bytes
    size_mb = size_bytes / (1024 * 1024)
    limit_mb = limit_bytes / (1024 * 1024)
    if size_bytes > limit_bytes:
        return (
            "Tee cache size",
            True,
            f"{size_mb:.1f} MB used, over the {limit_mb:.0f} MB limit — "
            "will be trimmed on the next scheduled cleanup",
        )
    return ("Tee cache size", True, f"{size_mb:.1f} MB used of {limit_mb:.0f} MB limit")


def _check_negative_compression_filters() -> tuple[str, bool, str]:
    """QB-065: surface `flag_low_performers`'s finding directly in `quor
    doctor`, not only in `quor gain --filters` — the QB-052/QB-065 negative-
    compression pattern (a filter's aggregate ratio looks healthy while its
    real per-invocation average is net-negative) was only found by someone
    going looking for it manually; a health check is where a user who never
    thinks to run `gain --filters` would actually see it. Reuses
    `query_filter_analytics`/`flag_low_performers` exactly as `gain --filters`
    does — no new query, no new threshold, same `NEAR_ZERO_COMPRESSION_PCT`
    cutoff from `quor.analytics.filter_divergence`.

    Advisory only, like `_check_plugins`/`_check_tee`'s config-disabled case:
    a bad SQLite read or a degenerate project path (e.g. doctor run from a
    drive root) must not block the rest of doctor's real diagnostic checks,
    so any query failure is reported informationally (True) rather than
    failing the check.
    """
    from quor.analytics.filter_divergence import flag_low_performers
    from quor.tracking.db import query_filter_analytics

    db_path = Path(platformdirs.user_data_dir("quor")) / "quor.db"
    label = "Negative or near-zero compression filters"
    try:
        report = query_filter_analytics(db_path, Path.cwd().resolve())
    except Exception as exc:  # noqa: BLE001 — advisory check, must never block doctor
        return (label, True, f"(could not check: {exc})")

    if report.total_invocations == 0:
        return (label, True, "no tracked invocations yet")

    flagged = flag_low_performers(report.filters)
    if not flagged:
        return (label, True, "")

    names = ", ".join(
        f"{f.filter_name} (net {f.avg_compression_pct:+.1f}%, avg/call {f.per_invocation_avg_pct:+.1f}%)"
        for f in flagged
    )
    return (
        label,
        False,
        f"{len(flagged)} filter(s): {names} — run `quor gain --filters` for details",
    )


def _check_plugins() -> tuple[str, bool, str]:
    """Report discovered third-party stages and plugins; flag any load failures."""
    from quor.pipeline.plugin_loader import get_load_report

    try:
        report = get_load_report(use_cache=False)
    except Exception as exc:  # noqa: BLE001
        return ("Plugin discovery", True, f"(could not check: {exc})")

    if report.is_empty:
        return ("Plugin discovery", True, "no third-party plugins installed")

    if report.failures:
        names = ", ".join(f.entry_point_name for f in report.failures)
        return (
            "Plugin discovery",
            False,
            f"{len(report.failures)} load failure(s): {names}",
        )

    parts: list[str] = []
    if report.stages:
        stage_names = ", ".join(s.stage_type for s in report.stages)
        parts.append(f"{len(report.stages)} stage(s): {stage_names}")
    if report.plugins:
        plugin_names = ", ".join(f"{p.plugin_id}@{p.version}" for p in report.plugins)
        parts.append(f"{len(report.plugins)} plugin(s): {plugin_names}")
    return ("Plugin discovery", True, "; ".join(parts))
