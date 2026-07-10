"""quor doctor — health check: dependencies, hook, tracking DB, filters, mode, tee."""

from __future__ import annotations

import importlib
import io
import sys
from pathlib import Path

import platformdirs
import typer
from rich.console import Console

from quor.config.loader import load_user_config
from quor.errors import ExitCode
from quor.filters.registry import FilterRegistry

console = Console()

_REQUIRED_PACKAGES = ("typer", "pydantic", "orjson", "platformdirs", "regex", "rich")


class _FakeStdout:
    """Stand-in for sys.stdout whose .buffer is writable (Python 3.14 makes the real one read-only)."""

    def __init__(self) -> None:
        self.buffer = io.BytesIO()

    def write(self, s: str) -> int:
        return 0

    def flush(self) -> None:
        pass


def doctor(
    settings_path: Path | None = typer.Option(
        None, "--settings-path", hidden=True, help="Override the Claude settings.json path (for testing)."
    ),
    reset_tee: bool = typer.Option(
        False,
        "--reset-tee",
        help="Clear tee's adaptive-disable state and re-enable it after fixing a filesystem issue.",
    ),
) -> None:
    """Run health checks and print a summary with colored status indicators."""
    if reset_tee:
        from quor.pipeline.tee import reset_tee_state

        try:
            reset_tee_state()
            console.print("[green]Tee adaptive-disable state cleared.[/green]")
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]Could not reset tee state: {exc}[/red]")

    checks: list[tuple[str, bool, str]] = []

    checks.append(_check_python_version())
    checks.extend(_check_dependencies())
    checks.append(_check_hook_script())
    checks.append(_check_hook_roundtrip())
    checks.append(_check_hook_collision(settings_path))
    checks.append(_check_read_hook_script())
    checks.append(_check_read_hook_roundtrip())
    checks.append(_check_sqlite())
    checks.append(_check_filters())
    checks.append(_check_mode())
    checks.append(_check_tee())
    checks.append(_check_plugins())

    all_ok = True
    for name, ok, detail in checks:
        symbol = "[green]✓[/green]" if ok else "[red]✗[/red]"
        suffix = f" — {detail}" if detail else ""
        console.print(f"{symbol} {name}{suffix}")
        all_ok = all_ok and ok

    if not all_ok:
        raise typer.Exit(code=ExitCode.GENERAL_ERROR)


def _check_python_version() -> tuple[str, bool, str]:
    ok = sys.version_info >= (3, 11)
    return ("Python ≥ 3.11", ok, f"{sys.version_info.major}.{sys.version_info.minor}")


def _check_dependencies() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    for pkg in _REQUIRED_PACKAGES:
        try:
            importlib.import_module(pkg)
            results.append((f"Dependency '{pkg}'", True, ""))
        except ImportError as exc:
            results.append((f"Dependency '{pkg}'", False, str(exc)))
    return results


def _check_hook_script() -> tuple[str, bool, str]:
    hook_path = Path(platformdirs.user_data_dir("quor")) / "hooks" / "claude-hook.ps1"
    exists = hook_path.exists()
    detail = str(hook_path) if exists else f"not found at {hook_path} — run `quor init --claude`"
    return ("Hook script installed", exists, detail)


def _check_hook_collision(settings_path: Path | None = None) -> tuple[str, bool, str]:
    """Warn if another tool's PreToolUse Bash hook is registered alongside Quor's."""
    from quor.cli.commands.init import _find_conflicting_hooks, _read_settings
    from quor.errors import ConfigError

    settings_file = settings_path or (Path.home() / ".claude" / "settings.json")
    if not settings_file.exists():
        return ("No conflicting PreToolUse hooks", True, "")
    try:
        settings = _read_settings(settings_file)
        conflicts = _find_conflicting_hooks(settings)
        if conflicts:
            detail = (
                f"{len(conflicts)} other Bash hook(s) detected — only one PreToolUse Bash hook "
                "tool can safely be active at a time (Claude Code can silently drop one hook's "
                "rewrite when two are registered). This means disable the other tool, not "
                "leave both running — run `quor init --claude` to see which one."
            )
            return ("No conflicting PreToolUse hooks", False, detail)
        return ("No conflicting PreToolUse hooks", True, "")
    except ConfigError as exc:
        return ("No conflicting PreToolUse hooks", True, f"(could not check: {exc})")
    except Exception:  # noqa: BLE001 — settings file may not exist or be parseable
        return ("No conflicting PreToolUse hooks", True, "")


def _check_hook_roundtrip() -> tuple[str, bool, str]:
    import orjson

    from quor.adapters.claude import run_hook
    from quor.rewrite.invocation import get_quor_invocation

    payload = orjson.dumps({"tool_name": "Bash", "tool_input": {"command": "git status"}})
    old_stdin, old_stdout = sys.stdin, sys.stdout
    try:
        sys.stdin = io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8")
        fake_stdout = _FakeStdout()
        sys.stdout = fake_stdout
        run_hook()
        result = orjson.loads(fake_stdout.buffer.getvalue())
        rewritten = result.get("hookSpecificOutput", {}).get("updatedInput", {}).get("command", "")
        expected = f"{get_quor_invocation()} git status"
        if rewritten == expected:
            return ("Hook responds correctly", True, "")
        return ("Hook responds correctly", False, f"unexpected rewrite: {rewritten!r}")
    except Exception as exc:  # noqa: BLE001
        return ("Hook responds correctly", False, str(exc))
    finally:
        sys.stdin = old_stdin
        sys.stdout = old_stdout


def _check_read_hook_script() -> tuple[str, bool, str]:
    hook_path = Path(platformdirs.user_data_dir("quor")) / "hooks" / "claude-hook-read.ps1"
    exists = hook_path.exists()
    detail = str(hook_path) if exists else f"not found at {hook_path} — run `quor init --claude`"
    return ("Read hook script installed", exists, detail)


def _check_read_hook_roundtrip() -> tuple[str, bool, str]:
    """Capability check for the PostToolUse/Read hook (QB-007C).

    Proves the live wiring actually compresses, not just that the plumbing
    responds: a synthetic PostToolUse/Read payload for a Markdown document
    large enough to exceed the `markdown` filter's token budget must come
    back with `updatedToolOutput` present and strictly smaller than the
    original. This does NOT prove the installed Claude Code binary actually
    honors `updatedToolOutput` for Read (that requires a real Claude Code
    session; see backlog.md's QB-007 entry) — it only proves Quor's own side
    of the contract is well-formed and that compression is genuinely wired
    in, not merely shape-correct.
    """
    import orjson

    from quor.adapters.claude_read import run_hook

    # Large enough to exceed markdown.toml's 2000-token max_tokens budget,
    # so this check exercises the real compression path, not just the
    # "no filter matched" passthrough shape.
    large_doc = "# Title\n\n" + ("Filler prose to exceed the token budget. " * 400)
    payload = orjson.dumps(
        {
            "tool_name": "Read",
            "tool_input": {"file_path": "example.md"},
            "tool_response": large_doc,
        }
    )
    old_stdin, old_stdout = sys.stdin, sys.stdout
    try:
        sys.stdin = io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8")
        fake_stdout = _FakeStdout()
        sys.stdout = fake_stdout
        run_hook()
        result = orjson.loads(fake_stdout.buffer.getvalue())
        hook_specific = result.get("hookSpecificOutput", {})
        if hook_specific.get("hookEventName") != "PostToolUse":
            return (
                "Read hook responds correctly",
                False,
                f"unexpected hookEventName: {hook_specific.get('hookEventName')!r}",
            )
        updated = hook_specific.get("updatedToolOutput")
        if not isinstance(updated, str):
            return (
                "Read hook responds correctly",
                False,
                "expected updatedToolOutput for an oversized Markdown document, got none",
            )
        if len(updated) >= len(large_doc):
            return (
                "Read hook responds correctly",
                False,
                "updatedToolOutput was not smaller than the original document",
            )
        return ("Read hook responds correctly", True, "")
    except Exception as exc:  # noqa: BLE001
        return ("Read hook responds correctly", False, str(exc))
    finally:
        sys.stdin = old_stdin
        sys.stdout = old_stdout


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
    for _, filter_config in registry.all_filters():
        total_failures += len(registry.run_tests(filter_config))
    if total_failures:
        return ("Built-in filter tests pass", False, f"{total_failures} inline test failure(s)")
    return ("Built-in filter tests pass", True, "")


def _check_mode() -> tuple[str, bool, str]:
    mode = load_user_config().mode
    return (f"Mode: {mode}", True, "")


def _check_tee() -> tuple[str, bool, str]:
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

    if not load_user_config().tee_enabled:
        return ("Tee: disabled (disabled in config)", True, "")

    return ("Tee: enabled", True, "")


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
