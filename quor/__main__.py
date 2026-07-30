"""
Quor entry point.

Version check runs at module level before any Quor import so we fail fast on
old Python without importing anything that might not exist.

Routing:
  quor hook <agent> <event> →  hook path (no rich, stdout must stay valid JSON)
                               resolved through quor.adapters.registry.AdapterRegistry;
                               "claude"/"claude-read" remain permanent argv aliases
                               for pre-QB-035C installed hook scripts (ADR-036).
  quor git status            →  dispatcher (run command, apply filter, print output)
  quor schema / init / …     →  CLI path via typer
"""

import contextlib
import sys

# CLI subcommands defined by Phase 7. Anything NOT in this set and NOT starting
# with "-" is treated as a dispatch target (e.g. "quor git status").
_CLI_COMMANDS: frozenset[str] = frozenset(
    {
        "schema",
        "hook",
        "init",
        "validate",
        "explain",
        "gain",
        "verify",
        "doctor",
        "map",
        "symbols",
        "graph",
        "repo",
        "explore",
        "version",
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


# Back-compat argv aliases (QB-035C): every hook script written to disk by a
# `quor init --claude` from before this release invokes `quor hook claude` /
# `quor hook claude-read` — the two-argv-token shape. These map permanently
# to (agent_id, event) pairs under the new, more general `quor hook
# <agent_id> <event>` shape (ADR-036 §9 step 2, option (a) — the recommended
# default) so an already-installed hook keeps working forever with zero user
# action, never silently breaking on upgrade.
_HOOK_ARGV_ALIASES: dict[str, tuple[str, str]] = {
    "claude": ("claude", "command_intercept"),
    "claude-read": ("claude", "content_intercept"),
}


def _run_hook() -> None:
    # Read stdin immediately so it is available in the except branch.
    original_bytes = sys.stdin.buffer.read()
    try:
        argv_adapter = sys.argv[2] if len(sys.argv) > 2 else ""
        argv_event = sys.argv[3] if len(sys.argv) > 3 else ""

        agent_id, event_value = _HOOK_ARGV_ALIASES.get(argv_adapter, (argv_adapter, argv_event))

        from quor.adapters.base import AgentEvent
        from quor.adapters.registry import AdapterRegistry

        try:
            event: AgentEvent | None = AgentEvent(event_value)
        except ValueError:
            event = None

        registry = AdapterRegistry()
        adapter = registry.find(agent_id) if agent_id else None

        if adapter is None or event is None or event not in adapter.supported_events:
            sys.stdout.buffer.write(original_bytes)
            print(f"[quor] Unknown hook adapter: {argv_adapter!r}", file=sys.stderr)
            return

        # Only CONTENT_INTERCEPT-shaped events (content already produced by
        # the agent, replaced in-process) need a TrackingDB — a
        # COMMAND_INTERCEPT rewrite just edits text; the *rewritten* command
        # is what actually gets tracked, later, when the agent runs it
        # through the dispatcher (`quor <cmd>`). Opening a TrackingDB
        # unconditionally here would add real per-invocation overhead to the
        # hot COMMAND_INTERCEPT path that today's `claude` hook never pays.
        tracking = None
        if event is AgentEvent.CONTENT_INTERCEPT:
            from quor.tracking.db import get_tracking_db

            tracking = get_tracking_db()

        try:
            result = adapter.handle_event(event, original_bytes, tracking)
        finally:
            if tracking is not None:
                tracking.close()

        sys.stdout.buffer.write(result if result is not None else original_bytes)
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
