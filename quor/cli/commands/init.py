"""quor init --claude — install the Claude Code PreToolUse hook.

Writes a PowerShell hook script (with sys.executable embedded) and registers
it in Claude Code's settings.json. Both writes are atomic (tempfile + rename).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import orjson
import platformdirs
import typer
from rich.console import Console

from quor.adapters.claude import HOOK_PS1_TEMPLATE
from quor.adapters.claude_read import HOOK_READ_PS1_TEMPLATE
from quor.errors import ConfigError, ExitCode

console = Console()

_HOOK_SCRIPT_NAME = "claude-hook.ps1"
# Identifies a Quor-generated hook entry in settings.json. The command field
# holds `powershell ... -File "<path>\claude-hook.ps1"`, not the literal
# "quor hook claude" (that string only appears inside the .ps1 file content),
# so the script filename is what's actually present and matchable here.
_HOOK_COMMAND_MARKER = _HOOK_SCRIPT_NAME

# PostToolUse/Read hook (QB-007A) — a separate script/registration from the
# PreToolUse/Bash hook above, installed additively alongside it. See the
# QB-007 design pass in backlog.md for why this is a distinct adapter rather
# than a branch inside the existing Bash hook.
_HOOK_READ_SCRIPT_NAME = "claude-hook-read.ps1"
_HOOK_READ_COMMAND_MARKER = _HOOK_READ_SCRIPT_NAME

# Known tools that register PreToolUse Bash hooks — used to give a named
# warning when Quor detects a conflict.
_KNOWN_HOOK_TOOLS: dict[str, str] = {
    "zap": "Zap (RTK)",
    "rtk": "RTK",
    "headroom": "Headroom AI",
    "comet": "Comet",
}


def init(
    claude: bool = typer.Option(False, "--claude", help="Install the Claude Code PreToolUse hook."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    settings_path: Path | None = typer.Option(
        None, "--settings-path", hidden=True, help="Override the settings.json path (for testing)."
    ),
) -> None:
    """Install Quor's integration with an AI coding assistant."""
    if not claude:
        console.print("[yellow]Nothing to do — pass --claude to install the Claude Code hook.[/yellow]")
        raise typer.Exit()

    settings_file = settings_path or (Path.home() / ".claude" / "settings.json")
    hook_script_path = Path(platformdirs.user_data_dir("quor")) / "hooks" / _HOOK_SCRIPT_NAME
    hook_script_content = HOOK_PS1_TEMPLATE.format(python=sys.executable)
    read_hook_script_path = (
        Path(platformdirs.user_data_dir("quor")) / "hooks" / _HOOK_READ_SCRIPT_NAME
    )
    read_hook_script_content = HOOK_READ_PS1_TEMPLATE.format(python=sys.executable)

    existing_settings = _read_settings(settings_file)
    already_installed = _hook_already_installed(existing_settings)
    read_already_installed = _hook_read_already_installed(existing_settings)
    conflicts = _find_conflicting_hooks(existing_settings)

    console.print("[bold]Dry run[/bold]")
    console.print(f"  Will write hook script to: {hook_script_path}")
    console.print(f"  Will write Read hook script to: {read_hook_script_path}")
    console.print(f"  Will update settings file: {settings_file}")
    if already_installed:
        console.print(
            "  [yellow]⚠  A Quor hook is already registered — it will be overwritten.[/yellow]"
        )
    if read_already_installed:
        console.print(
            "  [yellow]⚠  A Quor Read hook is already registered — it will be overwritten.[/yellow]"
        )

    if conflicts:
        console.print(
            "[yellow]⚠  Warning: another tool's PreToolUse Bash hook is already registered:[/yellow]"
        )
        for cmd in conflicts:
            tool_name = _identify_hook_tool(cmd)
            label = f" ({tool_name})" if tool_name else ""
            console.print(f"  [yellow]• {cmd!r}{label}[/yellow]")
        console.print(
            "[yellow]  Only one PreToolUse Bash hook tool can safely be active at a time — "
            "Claude Code has no supported way to run two, and one tool's rewrite can be "
            "silently dropped with no error. This is not safe to leave as-is: disable the "
            "other tool before relying on Quor, don't run both side by side.[/yellow]"
        )

    # Default confirmation is False when conflicts exist (fail-safe).
    default_confirm = not conflicts
    if not yes and not typer.confirm("Proceed?", default=default_confirm):
        console.print("Aborted.")
        raise typer.Exit(code=ExitCode.GENERAL_ERROR)

    _write_text_atomic(hook_script_path, hook_script_content)
    _write_text_atomic(read_hook_script_path, read_hook_script_content)
    new_settings = _install_hook_entry(existing_settings, hook_script_path)
    new_settings = _install_read_hook_entry(new_settings, read_hook_script_path)
    _write_json_atomic(settings_file, new_settings)

    console.print(f"[green]✓ Hook script written to {hook_script_path}[/green]")
    console.print(f"[green]✓ Read hook script written to {read_hook_script_path}[/green]")
    console.print(f"[green]✓ {settings_file} updated[/green]")

    _warn_if_execution_policy_restricted()

    console.print("\nRunning `quor doctor`...\n")
    from quor.cli.commands.doctor import doctor as run_doctor

    run_doctor(settings_path=settings_file)


def _read_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = orjson.loads(path.read_bytes())
    except orjson.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} does not contain a JSON object")
    return data


def _hook_already_installed(settings: dict[str, Any]) -> bool:
    pre_tool_use = settings.get("hooks", {}).get("PreToolUse", [])
    for entry in pre_tool_use:
        for h in entry.get("hooks", []):
            if _HOOK_COMMAND_MARKER in h.get("command", ""):
                return True
    return False


def _find_conflicting_hooks(settings: dict[str, Any]) -> list[str]:
    """Return commands from non-Quor PreToolUse Bash hooks.

    Any PreToolUse entry with matcher "Bash" (or no matcher, which also catches
    Bash commands) whose command does not contain our marker is a potential
    conflict — it could intercept the same commands Quor rewrites.
    """
    conflicts: list[str] = []
    pre_tool_use = settings.get("hooks", {}).get("PreToolUse", [])
    for entry in pre_tool_use:
        # Only Bash-matcher hooks can conflict with Quor's command rewriting
        matcher = entry.get("matcher", "")
        if matcher not in ("Bash", ""):
            continue
        for h in entry.get("hooks", []):
            cmd = h.get("command", "")
            if _HOOK_COMMAND_MARKER not in cmd:
                conflicts.append(cmd)
    return conflicts


def _identify_hook_tool(cmd: str) -> str:
    """Return a human-readable tool name if the command matches a known tool, else ''."""
    cmd_lower = cmd.lower()
    for marker, name in _KNOWN_HOOK_TOOLS.items():
        if marker in cmd_lower:
            return name
    return ""


def _install_hook_entry(settings: dict[str, Any], hook_script_path: Path) -> dict[str, Any]:
    new_settings = dict(settings)
    hooks = dict(new_settings.get("hooks", {}))
    pre_tool_use = [
        entry
        for entry in hooks.get("PreToolUse", [])
        if not any(_HOOK_COMMAND_MARKER in h.get("command", "") for h in entry.get("hooks", []))
    ]
    command = f'powershell -ExecutionPolicy Bypass -File "{hook_script_path}"'
    pre_tool_use.append({"matcher": "Bash", "hooks": [{"type": "command", "command": command}]})
    hooks["PreToolUse"] = pre_tool_use
    new_settings["hooks"] = hooks
    return new_settings


def _hook_read_already_installed(settings: dict[str, Any]) -> bool:
    post_tool_use = settings.get("hooks", {}).get("PostToolUse", [])
    for entry in post_tool_use:
        for h in entry.get("hooks", []):
            if _HOOK_READ_COMMAND_MARKER in h.get("command", ""):
                return True
    return False


def _install_read_hook_entry(settings: dict[str, Any], hook_script_path: Path) -> dict[str, Any]:
    """Register the PostToolUse/Read hook (QB-007A) — additive to any existing
    `hooks.PostToolUse` entries and independent of `hooks.PreToolUse` (see
    `_install_hook_entry` above), so installing/reinstalling this hook never
    disturbs the Bash hook's own registration."""
    new_settings = dict(settings)
    hooks = dict(new_settings.get("hooks", {}))
    post_tool_use = [
        entry
        for entry in hooks.get("PostToolUse", [])
        if not any(
            _HOOK_READ_COMMAND_MARKER in h.get("command", "") for h in entry.get("hooks", [])
        )
    ]
    command = f'powershell -ExecutionPolicy Bypass -File "{hook_script_path}"'
    post_tool_use.append({"matcher": "Read", "hooks": [{"type": "command", "command": command}]})
    hooks["PostToolUse"] = post_tool_use
    new_settings["hooks"] = hooks
    return new_settings


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(orjson.dumps(data, option=orjson.OPT_INDENT_2))
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _warn_if_execution_policy_restricted() -> None:
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-ExecutionPolicy"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    if result.returncode == 0 and result.stdout.strip() == "Restricted":
        console.print(
            "[yellow]Warning: PowerShell execution policy is 'Restricted' — the hook script may "
            "not run. Fix with: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned[/yellow]"
        )
