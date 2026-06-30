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
from quor.errors import ConfigError, ExitCode

console = Console()

_HOOK_SCRIPT_NAME = "claude-hook.ps1"
# Identifies a Quor-generated hook entry in settings.json. The command field
# holds `powershell ... -File "<path>\claude-hook.ps1"`, not the literal
# "quor hook claude" (that string only appears inside the .ps1 file content),
# so the script filename is what's actually present and matchable here.
_HOOK_COMMAND_MARKER = _HOOK_SCRIPT_NAME


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

    existing_settings = _read_settings(settings_file)
    already_installed = _hook_already_installed(existing_settings)

    console.print("[bold]Dry run[/bold]")
    console.print(f"  Will write hook script to: {hook_script_path}")
    console.print(f"  Will update settings file: {settings_file}")
    if already_installed:
        console.print("  [yellow]A Quor hook is already registered — it will be overwritten.[/yellow]")

    if not yes and not typer.confirm("Proceed?", default=True):
        console.print("Aborted.")
        raise typer.Exit(code=ExitCode.GENERAL_ERROR)

    _write_text_atomic(hook_script_path, hook_script_content)
    new_settings = _install_hook_entry(existing_settings, hook_script_path)
    _write_json_atomic(settings_file, new_settings)

    console.print(f"[green]✓ Hook script written to {hook_script_path}[/green]")
    console.print(f"[green]✓ {settings_file} updated[/green]")

    _warn_if_execution_policy_restricted()

    console.print("\nRunning `quor doctor`...\n")
    from quor.cli.commands.doctor import doctor as run_doctor

    run_doctor()


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
