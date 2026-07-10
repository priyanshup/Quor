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

from quor.adapters.hook_manifest import (
    BASH_HOOK_SPEC,
    HOOK_SPECS,
    ClaudeHookSpec,
    render_hook_script,
)
from quor.errors import ConfigError, ExitCode

console = Console()

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
    hooks_dir = Path(platformdirs.user_data_dir("quor")) / "hooks"
    script_paths: dict[str, Path] = {spec.hook_id: hooks_dir / spec.script_name for spec in HOOK_SPECS}

    existing_settings = _read_settings(settings_file)
    conflicts = _find_conflicting_hooks(existing_settings, bash_script_name=BASH_HOOK_SPEC.script_name)

    console.print("[bold]Dry run[/bold]")
    for spec in HOOK_SPECS:
        console.print(f"  Will write {spec.label} hook script to: {script_paths[spec.hook_id]}")
    console.print(f"  Will update settings file: {settings_file}")
    for spec in HOOK_SPECS:
        if _hook_installed(existing_settings, spec):
            console.print(
                f"  [yellow]⚠  A Quor {spec.label} hook is already registered — "
                "it will be overwritten.[/yellow]"
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

    new_settings = existing_settings
    for spec in HOOK_SPECS:
        script_path = script_paths[spec.hook_id]
        _write_text_atomic(script_path, render_hook_script(spec, python=sys.executable))
        new_settings = _install_hook_entry(new_settings, spec, script_path)
    _write_json_atomic(settings_file, new_settings)

    for spec in HOOK_SPECS:
        console.print(f"[green]✓ {spec.label} hook script written to {script_paths[spec.hook_id]}[/green]")
    console.print(f"[green]✓ {settings_file} updated[/green]")

    _warn_if_execution_policy_restricted()

    console.print("\nRunning `quor doctor`...\n")
    from quor.cli.commands.doctor import _run_doctor

    _run_doctor(settings_path=settings_file)


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


def _hook_installed(settings: dict[str, Any], spec: ClaudeHookSpec) -> bool:
    """Return True if `settings.json` already has a `spec.event` entry whose
    command references `spec.script_name`. Generic across every hook in
    HOOK_SPECS — the command field holds `powershell ... -File "<path>\\
    <script_name>"`, not the literal `quor hook <name>` (that string only
    appears inside the .ps1 file content), so the script filename is what's
    actually present and matchable here."""
    entries = settings.get("hooks", {}).get(spec.event, [])
    for entry in entries:
        for h in entry.get("hooks", []):
            if spec.script_name in h.get("command", ""):
                return True
    return False


def _find_conflicting_hooks(settings: dict[str, Any], *, bash_script_name: str) -> list[str]:
    """Return commands from non-Quor PreToolUse Bash hooks.

    Any PreToolUse entry with matcher "Bash" (or no matcher, which also catches
    Bash commands) whose command does not contain the Bash hook's marker is a
    potential conflict — it could intercept the same commands Quor rewrites.
    Scoped to the Bash hook specifically (not generalized across HOOK_SPECS):
    a PostToolUse/Read hook has no equivalent "silently drops the rewrite"
    failure mode, since Claude Code doesn't rewrite Read's own arguments.
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
            if bash_script_name not in cmd:
                conflicts.append(cmd)
    return conflicts


def _identify_hook_tool(cmd: str) -> str:
    """Return a human-readable tool name if the command matches a known tool, else ''."""
    cmd_lower = cmd.lower()
    for marker, name in _KNOWN_HOOK_TOOLS.items():
        if marker in cmd_lower:
            return name
    return ""


def _install_hook_entry(
    settings: dict[str, Any], spec: ClaudeHookSpec, script_path: Path
) -> dict[str, Any]:
    """Register `spec`'s hook in `settings.json`, replacing any prior entry
    for the same script. Additive to other hooks already registered under
    `spec.event` (and to every other event) — installing/reinstalling one
    hook never disturbs another's registration, generic across HOOK_SPECS."""
    new_settings = dict(settings)
    hooks = dict(new_settings.get("hooks", {}))
    entries = [
        entry
        for entry in hooks.get(spec.event, [])
        if not any(spec.script_name in h.get("command", "") for h in entry.get("hooks", []))
    ]
    command = f'powershell -ExecutionPolicy Bypass -File "{script_path}"'
    entries.append({"matcher": spec.matcher, "hooks": [{"type": "command", "command": command}]})
    hooks[spec.event] = entries
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
