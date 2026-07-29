"""quor init --agent <agent_id> — install an AI-assistant integration.

`--claude` is permanent sugar for `--agent claude` (QB-035E). The Claude
Code flow below (dry-run preview, hook-collision warning, confirmation,
atomic writes) is unchanged from before QB-068 — it now calls
`_install_claude()` to perform the actual writes, the same function
`ClaudeAdapter.install()` (`quor/adapters/claude_adapter.py`) calls, so
there is exactly one implementation of "write the Claude hook scripts and
register them," not two. Other agents (`--agent codex`, `--agent gemini`,
or any third-party `quor.hook_adapter` entry point) go through a simpler,
generic install flow — see `_init_generic_agent()` — since they don't share
Claude's Bash-hook-collision-detection concept.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import orjson
import platformdirs
import typer
from rich.console import Console

from quor.adapters.base import InstallContext, InstallResult
from quor.adapters.hook_manifest import (
    BASH_HOOK_SPEC,
    HOOK_SPECS,
    ClaudeHookSpec,
    render_hook_script,
)
from quor.atomic_io import write_json_atomic as _write_json_atomic
from quor.atomic_io import write_text_atomic as _write_text_atomic
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
    agent: str | None = typer.Option(
        None, "--agent", help="Agent to install an integration for (e.g. claude, codex, gemini)."
    ),
    claude: bool = typer.Option(
        False, "--claude", help="Install the Claude Code PreToolUse hook. Sugar for --agent claude."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    settings_path: Path | None = typer.Option(
        None, "--settings-path", hidden=True, help="Override the settings.json path (for testing)."
    ),
) -> None:
    """Install Quor's integration with an AI coding assistant."""
    agent_id = agent or ("claude" if claude else None)
    if agent_id is None:
        console.print(
            "[yellow]Nothing to do — pass --agent <name> (e.g. --agent claude) to install an "
            "integration.[/yellow]"
        )
        raise typer.Exit()

    if agent_id == "claude":
        _init_claude(yes=yes, settings_path=settings_path)
        return

    _init_generic_agent(agent_id, yes=yes, settings_path=settings_path)


def _init_claude(*, yes: bool, settings_path: Path | None) -> None:
    """The original `quor init --claude` flow (dry-run preview, Bash-hook
    collision warning, confirmation, atomic writes) — unchanged since before
    QB-068 except that the actual writes are now performed by
    `_install_claude()`, the same function `ClaudeAdapter.install()` calls."""
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

    result = _install_claude(InstallContext(settings_override=settings_path, yes=True))

    for spec in HOOK_SPECS:
        console.print(f"[green]✓ {spec.label} hook script written to {script_paths[spec.hook_id]}[/green]")
    console.print(f"[green]✓ {settings_file} updated[/green]")
    for warning in result.warnings:
        console.print(f"[yellow]⚠  {warning}[/yellow]")

    _warn_if_execution_policy_restricted()

    console.print("\nRunning `quor doctor`...\n")
    from quor.cli.commands.doctor import _run_doctor

    _run_doctor(settings_path=settings_file)


def _install_claude(ctx: InstallContext) -> InstallResult:
    """Perform the actual Claude Code hook writes: one PowerShell script per
    `HOOK_SPECS` entry, plus their `settings.json` registration — both
    atomic. This is the single implementation `_init_claude()` (above, the
    interactive CLI flow) and `ClaudeAdapter.install()` (the `AgentAdapter`
    entry point) both call; neither re-implements it."""
    settings_file = ctx.settings_override or (Path.home() / ".claude" / "settings.json")
    hooks_dir = Path(platformdirs.user_data_dir("quor")) / "hooks"
    script_paths: dict[str, Path] = {spec.hook_id: hooks_dir / spec.script_name for spec in HOOK_SPECS}

    new_settings = _read_settings(settings_file)
    for spec in HOOK_SPECS:
        script_path = script_paths[spec.hook_id]
        _write_text_atomic(script_path, render_hook_script(spec, python=sys.executable))
        new_settings = _install_hook_entry(new_settings, spec, script_path)
    _write_json_atomic(settings_file, new_settings)

    installed = (*script_paths.values(), settings_file)
    return InstallResult(installed_paths=installed, warnings=())


def _init_generic_agent(agent_id: str, *, yes: bool, settings_path: Path | None) -> None:
    """Install flow for any non-Claude agent (`--agent codex`, `--agent
    gemini`, or a third-party `quor.hook_adapter` entry point). Simpler than
    `_init_claude()` by design: those agents don't share Claude's
    Bash-hook-collision-detection concept (QB-035A's design explicitly kept
    that logic Claude-local rather than generalizing it — see
    `docs/design/QB-035A-multi-agent-adapter-design.md` §13). Delegates all
    agent-specific behavior to `adapter.install()`."""
    from quor.adapters.registry import AdapterRegistry

    registry = AdapterRegistry()
    adapter = registry.find(agent_id)
    if adapter is None:
        known = ", ".join(sorted(a.agent_id for a in registry.all_adapters()))
        console.print(f"[red]Unknown agent {agent_id!r}.[/red] Known agents: {known}")
        raise typer.Exit(code=ExitCode.GENERAL_ERROR)

    if not yes and not typer.confirm(f"Install the {adapter.display_name} integration?", default=True):
        console.print("Aborted.")
        raise typer.Exit(code=ExitCode.GENERAL_ERROR)

    result = adapter.install(InstallContext(settings_override=settings_path, yes=yes))

    for path in result.installed_paths:
        console.print(f"[green]✓ {path}[/green]")
    for warning in result.warnings:
        console.print(f"[yellow]⚠  {warning}[/yellow]")
    if not result.installed_paths and not result.warnings:
        console.print(f"[yellow]Nothing installed for {adapter.display_name}.[/yellow]")

    console.print("\nRunning `quor doctor`...\n")
    from quor.cli.commands.doctor import _run_doctor

    _run_doctor(settings_path=settings_path)


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
