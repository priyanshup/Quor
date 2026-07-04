"""quor doctor — health check: dependencies, hook, tracking DB, filters, mode."""

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
) -> None:
    """Run health checks and print a summary with colored status indicators."""
    checks: list[tuple[str, bool, str]] = []

    checks.append(_check_python_version())
    checks.extend(_check_dependencies())
    checks.append(_check_hook_script())
    checks.append(_check_hook_roundtrip())
    checks.append(_check_hook_collision(settings_path))
    checks.append(_check_sqlite())
    checks.append(_check_filters())
    checks.append(_check_mode())
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
                f"{len(conflicts)} other Bash hook(s) detected — "
                "double-rewriting risk; run `quor init --claude` to review"
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
