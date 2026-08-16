"""
Quor entry point.

Version check runs at module level before any Quor import so we fail fast on
old Python without importing anything that might not exist.

Routing:
  quor git status            →  dispatcher (run command, apply filter, print output)
  quor schema / doctor / …   →  CLI path via typer

QB-104: the hook dispatch path (`quor hook <agent> <event>`) that used to
live here was removed along with the rest of the hook-based integration —
Quor's MCP server (`quor/mcp/server.py`) is the sole integration surface
now, started directly (`python -m quor.mcp.server`), not routed through this
entry point at all.
"""

import contextlib
import sys

# CLI subcommands defined by Phase 7 (plus uninstall-hooks, QB-104). Anything
# NOT in this set and NOT starting with "-" is treated as a dispatch target
# (e.g. "quor git status").
_CLI_COMMANDS: frozenset[str] = frozenset(
    {
        "schema",
        "init",
        "validate",
        "explain",
        "gain",
        "verify",
        "doctor",
        "uninstall-hooks",
        "map",
        "symbols",
        "graph",
        "repo",
        "explore",
        "search",
        "version",
        "dashboard",
    }
)


def _check_python_version() -> None:
    # Runtime guard: sys.executable may differ from the installer Python, so
    # the requires-python constraint (enforced only at install time) is not
    # sufficient. This must run before any other import.
    if sys.version_info < (3, 11):  # noqa: UP036
        print(
            f"Quor requires Python 3.11 or higher. "
            f"You are running {sys.version_info.major}.{sys.version_info.minor}. "
            f"Please upgrade: https://python.org/downloads/",
            file=sys.stderr,
        )
        sys.exit(5)  # ExitCode.DEPENDENCY_MISSING


_check_python_version()


def _ensure_utf8_stdio() -> None:
    # Windows consoles default text-mode stdout/stderr to the system codepage
    # (often cp1252), which cannot encode the ✓/✗ glyphs used throughout the
    # CLI and dispatch output. Reconfigure to UTF-8; harmless if already UTF-8
    # or if the stream (e.g. a test runner's capture buffer) doesn't support it.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):
                reconfigure(encoding="utf-8")


def _run_dispatch(args: list[str]) -> None:
    from quor.engine.dispatcher import run_dispatch
    from quor.tracking.db import get_tracking_db

    tracking = get_tracking_db()
    try:
        exit_code = run_dispatch(args, tracking=tracking)
    finally:
        tracking.close()
    sys.exit(exit_code)


def main() -> None:
    _ensure_utf8_stdio()

    # Dispatch branch: "quor git status" → run git status through filter
    first_arg = sys.argv[1] if len(sys.argv) >= 2 else ""
    if first_arg and first_arg not in _CLI_COMMANDS and not first_arg.startswith("-"):
        _run_dispatch(sys.argv[1:])
        return

    from quor.cli.main import app

    app()


if __name__ == "__main__":
    main()
