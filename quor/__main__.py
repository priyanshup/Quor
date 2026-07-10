"""
Quor entry point.

Version check runs at module level before any Quor import so we fail fast on
old Python without importing anything that might not exist.

Routing:
  quor hook claude       →  hook path (no rich, stdout must stay valid JSON)
  quor git status        →  dispatcher (run command, apply filter, print output)
  quor schema / init / … →  CLI path via typer
"""

import contextlib
import sys

# CLI subcommands defined by Phase 7. Anything NOT in this set and NOT starting
# with "-" is treated as a dispatch target (e.g. "quor git status").
_CLI_COMMANDS: frozenset[str] = frozenset(
    {"schema", "hook", "init", "validate", "explain", "gain", "verify", "doctor"}
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


_HOOK_ADAPTERS: frozenset[str] = frozenset({"claude", "claude-read"})


def _run_hook() -> None:
    # Read stdin immediately so it is available in the except branch.
    original_bytes = sys.stdin.buffer.read()
    try:
        adapter = sys.argv[2] if len(sys.argv) > 2 else ""
        if adapter not in _HOOK_ADAPTERS:
            sys.stdout.buffer.write(original_bytes)
            print(f"[quor] Unknown hook adapter: {adapter!r}", file=sys.stderr)
            return
        import io

        sys.stdin = io.TextIOWrapper(io.BytesIO(original_bytes), encoding="utf-8")

        if adapter == "claude":
            from quor.adapters.claude import run_hook as run_claude_hook

            run_claude_hook()
        else:
            # "claude-read" — PostToolUse/Read (QB-007A/C), tracked (QB-007D)
            # exactly like _run_dispatch() below tracks Bash invocations.
            from quor.adapters.claude_read import run_hook as run_claude_read_hook
            from quor.tracking.db import get_tracking_db

            tracking = get_tracking_db()
            try:
                run_claude_read_hook(tracking=tracking)
            finally:
                tracking.close()
    except Exception as exc:  # noqa: BLE001 — hook must never raise
        sys.stdout.buffer.write(original_bytes)
        print(f"[quor] Hook error — returning original: {exc}", file=sys.stderr)


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
    from quor.adapters.dispatcher import run_dispatch
    from quor.tracking.db import get_tracking_db

    tracking = get_tracking_db()
    try:
        exit_code = run_dispatch(args, tracking=tracking)
    finally:
        tracking.close()
    sys.exit(exit_code)


def main() -> None:
    # Keep hook branch first: must never touch rich or typer.
    if len(sys.argv) >= 2 and sys.argv[1] == "hook":
        _run_hook()
        return

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
