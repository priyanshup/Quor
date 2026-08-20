"""quor doctor — health check: dependencies, tracking DB, filters, mode, tee,
plugins, and the MCP server (interpreter isolation, `.mcp.json`
registration, and a live stdio handshake dry-run).

QB-104: every hook-specific check (script installed/registered/up to date,
adapter discovery, hook collision, hook roundtrip) was removed along with
the hook-based integration itself — Quor's MCP server has nothing
file-based to install or go stale, so there is nothing left for `doctor` to
diagnose on that front. Anyone with a leftover pre-QB-104 hook installation
should run `quor uninstall-hooks`, not `quor doctor`.

The MCP checks below are a different category from those removed hook
checks: they audit the *client-side* registration surface the MCP
integration genuinely does depend on — the interpreter it runs under,
`.mcp.json`/`claude_desktop_config.json` registration, and whether
`quor.mcp.launcher` actually completes a live stdio handshake — not a
Quor-installed script that could go stale.
"""

from __future__ import annotations

import os
import platform
import re
import sys
import time
from enum import Enum
from pathlib import Path
from typing import Any

import orjson
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

_MCP_REQUIRED_PACKAGES = ("mcp", "quor")

_HANDSHAKE_TIMEOUT_SECONDS = 15.0


class Status(Enum):
    """A diagnostic check's outcome. WARN is advisory — it's shown but never
    fails the run (matches the old advisory-only checks' `ok=True`
    behavior); only FAIL fails the run (matches the old `ok=False`)."""

    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


_STATUS_STYLE: dict[Status, str] = {
    Status.PASS: "green",
    Status.WARN: "yellow",
    Status.FAIL: "red",
}


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

    checks: list[tuple[str, Status, str]] = []

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
    checks.append(_check_mcp_interpreter_isolation())
    checks.extend(_check_mcp_dependencies())
    checks.extend(_check_mcp_json_files())
    checks.append(_check_mcp_launcher_handshake())

    failed = 0
    warned = 0
    for name, status, detail in checks:
        _print_check_line(name, status, detail)
        if status is Status.FAIL:
            failed += 1
        elif status is Status.WARN:
            warned += 1

    elapsed = time.monotonic() - t0
    total = len(checks)
    warn_suffix = f", {warned} warning(s)" if warned else ""
    if failed:
        console.print(
            f"\n[red]✗ {failed} of {total} check(s) failed[/red]{warn_suffix} "
            f"in {elapsed:.1f}s — see above for details"
        )
    elif warned:
        console.print(
            f"\n[yellow]⚠ {total} of {total} checks passed, {warned} warning(s)[/yellow] "
            f"in {elapsed:.1f}s"
        )
    else:
        console.print(f"\n[green]✓ {total} of {total} checks passed[/green] in {elapsed:.1f}s")

    if failed:
        raise typer.Exit(code=ExitCode.GENERAL_ERROR)


def _print_check_line(name: str, status: Status, detail: str) -> None:
    color = _STATUS_STYLE[status]
    # escape(): `name`/`detail` are dynamic text that can contain literal
    # square brackets (a path segment, or "quor[javascript]" in an extras
    # hint) — Rich's markup parser otherwise reads "[javascript]" as an
    # (unrecognized, silently dropped) style tag, not literal text. Only
    # the hardcoded `[color][...][/color]` here is meant to be parsed as
    # markup.
    suffix = f" — {escape(detail)}" if detail else ""
    # soft_wrap: detail strings can embed long filesystem paths — Rich's
    # default word-wrap would otherwise split it mid-phrase across two
    # lines, which is both harder to read and breaks a clean copy-paste.
    console.print(f"[{color}][{status.value}][/{color}] {escape(name)}{suffix}", soft_wrap=True)


def _check_python_version() -> tuple[str, Status, str]:
    ok = sys.version_info >= (3, 11)
    status = Status.PASS if ok else Status.FAIL
    return ("Python ≥ 3.11", status, f"{sys.version_info.major}.{sys.version_info.minor}")


def _check_dependencies() -> list[tuple[str, Status, str]]:
    import importlib

    results: list[tuple[str, Status, str]] = []
    for pkg in _REQUIRED_PACKAGES:
        try:
            importlib.import_module(pkg)
            results.append((f"Dependency '{pkg}'", Status.PASS, ""))
        except ImportError as exc:
            results.append((f"Dependency '{pkg}'", Status.FAIL, str(exc)))
    return results


def _check_sqlite() -> tuple[str, Status, str]:
    from quor.tracking.db import get_tracking_db

    try:
        db = get_tracking_db()
        db.flush()
        db.close()
        return ("Tracking DB readable/writable", Status.PASS, "")
    except Exception as exc:  # noqa: BLE001
        return ("Tracking DB readable/writable", Status.FAIL, str(exc))


def _check_filters() -> tuple[str, Status, str]:
    registry = FilterRegistry(project_root=Path.cwd())
    total_failures = 0
    total_skipped = 0
    for _, filter_config in registry.all_filters():
        result = registry.run_tests(filter_config)
        total_failures += len(result.failures)
        total_skipped += len(result.skipped)
    if total_failures:
        return ("Built-in filter tests pass", Status.FAIL, f"{total_failures} inline test failure(s)")
    if total_skipped:
        return (
            "Built-in filter tests pass",
            Status.WARN,
            f"{total_skipped} test(s) skipped — optional AST dependency not installed "
            "(quor[javascript])",
        )
    return ("Built-in filter tests pass", Status.PASS, "")


def _check_mode(user_config: QuorUserConfig) -> tuple[str, Status, str]:
    return (f"Mode: {user_config.mode}", Status.PASS, "")


def _check_tee(user_config: QuorUserConfig) -> tuple[str, Status, str]:
    """Report tee's status (ADR-023 / QB-013 adaptive fallback).

    Three distinct states, only one of which is flagged as a problem:
      - enabled — normal, healthy state.
      - deliberately disabled by the user (QuorUserConfig.tee_enabled /
        QUOR_TEE_ENABLED) — intentional, not a problem.
      - adaptively disabled after repeated filesystem write failures — a
        real problem worth surfacing (FAIL), since it means recovery
        footers have silently stopped being written.
    """
    from quor.pipeline.tee import get_tee_status

    try:
        status = get_tee_status()
    except Exception as exc:  # noqa: BLE001
        return ("Tee", Status.PASS, f"(could not check: {exc})")

    if status.disabled:
        hint = (
            f"auto-disabled after {status.consecutive_failures} consecutive write "
            f"failures ({status.disabled_reason}) — fix the underlying filesystem "
            "issue, then run `quor doctor --reset-tee` to re-enable"
        )
        return ("Tee: disabled (filesystem unavailable)", Status.FAIL, hint)

    if not user_config.tee_enabled:
        return ("Tee: disabled (disabled in config)", Status.PASS, "")

    return ("Tee: enabled", Status.PASS, "")


def _check_tee_size(user_config: QuorUserConfig) -> tuple[str, Status, str]:
    """Report the tee cache's current disk usage against its configured
    ceiling (ADR-023, QB-103).

    Read-only — never triggers cleanup itself; eviction only happens the
    next time cleanup_tee() actually runs (dispatcher-triggered, throttled
    to at most once per 24h). Advisory only: being over the limit is
    expected to self-correct on the next scheduled cleanup pass, so it's
    surfaced as a warning, not a failure — it must never block the rest
    of `quor doctor`'s real diagnostic checks.
    """
    from quor.pipeline.tee import current_tee_size_bytes

    try:
        size_bytes = current_tee_size_bytes()
    except Exception as exc:  # noqa: BLE001
        return ("Tee cache size", Status.PASS, f"(could not check: {exc})")

    limit_bytes = user_config.tee_max_bytes
    size_mb = size_bytes / (1024 * 1024)
    limit_mb = limit_bytes / (1024 * 1024)
    if size_bytes > limit_bytes:
        return (
            "Tee cache size",
            Status.WARN,
            f"{size_mb:.1f} MB used, over the {limit_mb:.0f} MB limit — "
            "will be trimmed on the next scheduled cleanup",
        )
    return ("Tee cache size", Status.PASS, f"{size_mb:.1f} MB used of {limit_mb:.0f} MB limit")


def _check_negative_compression_filters() -> tuple[str, Status, str]:
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
    so any query failure is reported informationally (PASS) rather than
    failing the check. A genuine flagged finding is still FAIL — it's a
    real signal, not a check-couldn't-run situation.
    """
    from quor.analytics.filter_divergence import flag_low_performers
    from quor.tracking.db import query_filter_analytics

    db_path = Path(platformdirs.user_data_dir("quor")) / "quor.db"
    label = "Negative or near-zero compression filters"
    try:
        report = query_filter_analytics(db_path, Path.cwd().resolve())
    except Exception as exc:  # noqa: BLE001 — advisory check, must never block doctor
        return (label, Status.PASS, f"(could not check: {exc})")

    if report.total_invocations == 0:
        return (label, Status.PASS, "no tracked invocations yet")

    flagged = flag_low_performers(report.filters)
    if not flagged:
        return (label, Status.PASS, "")

    names = ", ".join(
        f"{f.filter_name} (net {f.avg_compression_pct:+.1f}%, avg/call {f.per_invocation_avg_pct:+.1f}%)"
        for f in flagged
    )
    return (
        label,
        Status.FAIL,
        f"{len(flagged)} filter(s): {names} — run `quor gain --filters` for details",
    )


def _check_plugins() -> tuple[str, Status, str]:
    """Report discovered third-party stages and plugins; flag any load failures."""
    from quor.pipeline.plugin_loader import get_load_report

    try:
        report = get_load_report(use_cache=False)
    except Exception as exc:  # noqa: BLE001
        return ("Plugin discovery", Status.PASS, f"(could not check: {exc})")

    if report.is_empty:
        return ("Plugin discovery", Status.PASS, "no third-party plugins installed")

    if report.failures:
        names = ", ".join(f.entry_point_name for f in report.failures)
        return (
            "Plugin discovery",
            Status.FAIL,
            f"{len(report.failures)} load failure(s): {names}",
        )

    parts: list[str] = []
    if report.stages:
        stage_names = ", ".join(s.stage_type for s in report.stages)
        parts.append(f"{len(report.stages)} stage(s): {stage_names}")
    if report.plugins:
        plugin_names = ", ".join(f"{p.plugin_id}@{p.version}" for p in report.plugins)
        parts.append(f"{len(report.plugins)} plugin(s): {plugin_names}")
    return ("Plugin discovery", Status.PASS, "; ".join(parts))


def _check_mcp_interpreter_isolation() -> tuple[str, Status, str]:
    """Audit `sys.executable` for virtual environment isolation.

    A global/system interpreter isn't a hard failure — the MCP server can
    still run on it — but it's the same drift risk `quor/mcp/launcher.py`'s
    module docstring already describes for a `.venv` that falls out of sync
    with `pyproject.toml`: shared, unpinned site-packages make a future
    `import mcp` failure more likely, not less.
    """
    name = f"MCP interpreter: {sys.executable}"
    base_prefix = getattr(sys, "base_prefix", sys.prefix)
    in_venv = (
        sys.prefix != base_prefix
        or bool(os.environ.get("VIRTUAL_ENV"))
        or bool(os.environ.get("CONDA_PREFIX"))
    )
    if in_venv:
        return (name, Status.PASS, "running inside an isolated virtual environment")
    return (
        name,
        Status.WARN,
        "running on the global/system interpreter, not an isolated virtual environment "
        "— packages can drift or conflict; consider `python -m venv .venv` then "
        "`quor init --mcp --yes` to scaffold against an isolated interpreter",
    )


def _check_mcp_dependencies() -> list[tuple[str, Status, str]]:
    """Dedicated MCP-focused re-check of `mcp`/`quor` importability under
    this interpreter — distinct from `_check_dependencies()`'s broader
    7-package sweep (which also covers filter/tracking dependencies unused
    by the MCP server), with a repair hint scoped to this exact
    interpreter (`sys.executable`, not a bare `pip`)."""
    import importlib

    results: list[tuple[str, Status, str]] = []
    for pkg in _MCP_REQUIRED_PACKAGES:
        try:
            importlib.import_module(pkg)
            results.append((f"MCP dependency '{pkg}' importable", Status.PASS, ""))
        except ImportError as exc:
            results.append(
                (
                    f"MCP dependency '{pkg}' importable",
                    Status.FAIL,
                    f"{exc} — run `{sys.executable} -m pip install {pkg}`",
                )
            )
    return results


def _global_mcp_config_candidates() -> list[Path]:
    """Locations an MCP client might hold a "quor" server registration
    outside the current project: a bare `~/.mcp.json` (some clients read a
    user-scope config at this path), and Claude Desktop's own
    `claude_desktop_config.json` — the same `mcpServers` JSON shape
    `docs/POC_TESTING.md` documents as the manual "global" equivalent of a
    scaffolded `.mcp.json`, at its OS-specific location."""
    home = Path.home()
    candidates = [home / ".mcp.json"]
    system = platform.system()
    if system == "Darwin":
        candidates.append(
            home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        )
    elif system == "Windows":
        appdata = os.environ.get("APPDATA")
        if appdata:
            candidates.append(Path(appdata) / "Claude" / "claude_desktop_config.json")
    else:
        candidates.append(home / ".config" / "Claude" / "claude_desktop_config.json")
    return candidates


def _check_mcp_json_files() -> list[tuple[str, Status, str]]:
    rows = [_validate_mcp_config_file(Path.cwd() / ".mcp.json", "workspace")]
    rows.extend(
        _validate_mcp_config_file(candidate, "global")
        for candidate in _global_mcp_config_candidates()
    )
    return rows


def _has_unexpanded_placeholder(command: str) -> bool:
    """`True` for shell/env-var-style templating (`${VAR}`, `$VAR`,
    `%VAR%`) an MCP client expands at spawn time, not a literal path this
    process can resolve."""
    return bool(re.search(r"\$\{|\$[A-Za-z_]|%[A-Za-z_][A-Za-z0-9_]*%", command))


def _validate_mcp_config_file(path: Path, scope: str) -> tuple[str, Status, str]:
    """Validate one `.mcp.json`/`claude_desktop_config.json` candidate:
    parses as JSON, has a `mcpServers.quor` entry, and that entry's
    `command` resolves to a real interpreter on disk. Mirrors
    `quor/cli/commands/init.py`'s `_read_json`/`_mcp_server_entry` shape,
    since that's exactly what wrote it."""
    name = f"MCP config ({scope}): {path}"
    if not path.exists():
        return (name, Status.WARN, "not found — run `quor init --mcp` to scaffold it")

    try:
        data: Any = orjson.loads(path.read_bytes())
    except orjson.JSONDecodeError as exc:
        return (name, Status.FAIL, f"invalid JSON: {exc}")

    if not isinstance(data, dict):
        return (name, Status.FAIL, "does not contain a JSON object")

    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or "quor" not in servers:
        return (
            name,
            Status.WARN,
            "no 'quor' entry under mcpServers — run `quor init --mcp` to register it",
        )

    entry = servers["quor"]
    if not isinstance(entry, dict):
        return (name, Status.FAIL, "'quor' entry is not a JSON object")

    command = entry.get("command")
    if not isinstance(command, str) or not command:
        return (name, Status.FAIL, "'quor' entry is missing a valid 'command'")
    if _has_unexpanded_placeholder(command):
        # `${CLAUDE_PROJECT_DIR:-.}/.venv/bin/python`-style shell/env-var
        # templating — the *client* (Claude Code) expands this at spawn
        # time, not something `Path.exists()` can resolve statically here.
        # This repo's own `.mcp.json` uses exactly this form.
        return (
            name,
            Status.WARN,
            f"'command' contains an unexpanded variable ('{command}') — cannot statically "
            "verify the interpreter it resolves to",
        )
    if not Path(command).exists():
        return (
            name,
            Status.FAIL,
            f"interpreter not found at '{command}' — re-run `quor init --mcp --yes`",
        )

    args = entry.get("args")
    if not isinstance(args, list) or "quor.mcp.launcher" not in args:
        return (
            name,
            Status.WARN,
            "'quor' entry does not invoke quor.mcp.launcher — re-run `quor init --mcp --yes`",
        )

    return (name, Status.PASS, "quor registered, interpreter path resolves")


def _check_mcp_launcher_handshake() -> tuple[str, Status, str]:
    """Live dry-run: spawn `quor.mcp.launcher` as a real subprocess under
    this interpreter and complete an actual MCP stdio `initialize`
    handshake against it, the same handshake any MCP client performs on
    startup — not a static check, an end-to-end proof the server actually
    comes up. `QUOR_MCP_DISABLE_AUTOREPAIR=1` keeps this a true dry-run:
    doctor must never trigger a network install as a side effect of a
    health check (see `quor/mcp/launcher.py`'s own module docstring for
    what that env var does). The client session is closed and the
    subprocess torn down as soon as the handshake completes."""
    name = "MCP launcher stdio handshake (live dry-run)"
    try:
        import anyio
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError as exc:
        return (name, Status.FAIL, f"mcp client library unavailable: {exc}")

    async def _handshake() -> str:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "quor.mcp.launcher"],
            cwd=str(Path.cwd()),
            env={"QUOR_MCP_DISABLE_AUTOREPAIR": "1"},
        )
        with anyio.fail_after(_HANDSHAKE_TIMEOUT_SECONDS):
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    result = await session.initialize()
                    return result.server_info.name

    try:
        server_name = anyio.run(_handshake)
    except TimeoutError:
        return (
            name,
            Status.FAIL,
            f"no response within {_HANDSHAKE_TIMEOUT_SECONDS:.0f}s — run "
            f"`{sys.executable} -m quor.mcp.launcher` manually to see the raw error on stderr",
        )
    except Exception as exc:  # noqa: BLE001 — surface any handshake failure as a check result
        return (
            name,
            Status.FAIL,
            f"handshake failed: {exc} — run `{sys.executable} -m quor.mcp.launcher` "
            "manually to see the raw error on stderr",
        )
    return (name, Status.PASS, f"server responded: {server_name!r}")
