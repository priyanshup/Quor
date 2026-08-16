"""quor init — scaffold Quor's MCP server registration (QB-104 Phase 3).

Replaces the old hook-installation `init` (removed in QB-104 Phase 1 along
with the rest of the hook-based integration — see backlog.md's QB-104
entry). There is exactly one integration surface now: the MCP server
(`quor/mcp/server.py`), registered via a client's own `.mcp.json`/
`claude_desktop_config.json`, not a Quor-generated launcher script — so
there is no per-agent install flow left to have, only scaffolding.

Every invocation also runs an unprompted legacy-hook cleanup pass
(`quor.cli.commands.uninstall_hooks.detect_legacy_hooks`/
`remove_legacy_hooks`): a leftover pre-QB-104 hook installation is pure
inert clutter now (its target, `quor hook ...`, no longer exists), so
`init` — the command someone reaches for when setting Quor up fresh —
is the natural place to clear it out automatically, without a separate
`quor uninstall-hooks` step. `uninstall-hooks` itself remains for anyone
who wants that cleanup without also scaffolding MCP registration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import orjson
import typer
from rich.console import Console

from quor.atomic_io import write_json_atomic as _write_json_atomic
from quor.cli.commands.uninstall_hooks import detect_legacy_hooks, remove_legacy_hooks
from quor.errors import ConfigError

console = Console()

_MCP_SERVER_ENTRY: dict[str, Any] = {
    "command": "python",
    "args": ["-m", "quor.mcp.server"],
}

_MCP_JSON_FILENAME = ".mcp.json"


def init(
    mcp: bool = typer.Option(
        False, "--mcp", help="Scaffold MCP server registration (writes ./.mcp.json)."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Set up Quor's MCP server registration."""
    _cleanup_legacy_hooks_if_present()

    if not mcp:
        console.print(
            "[yellow]Nothing to do — pass --mcp to scaffold MCP server registration.[/yellow]"
        )
        raise typer.Exit()

    _scaffold_mcp(yes=yes)


def _cleanup_legacy_hooks_if_present() -> None:
    """Unprompted housekeeping pass — see module docstring. Silent when
    there's nothing to clean up; prints exactly what `uninstall_hooks`
    itself would print otherwise, so the two commands stay indistinguishable
    in their reporting."""
    found_scripts, found_entries = detect_legacy_hooks()
    if not found_scripts and not found_entries:
        return

    console.print(
        "[bold]Found a leftover pre-QB-104 hook installation — removing it:[/bold]"
    )
    remove_legacy_hooks(found_scripts, found_entries)
    console.print()


def _scaffold_mcp(*, yes: bool) -> None:
    mcp_json_path = Path.cwd() / _MCP_JSON_FILENAME

    try:
        existing = _read_json(mcp_json_path)
    except ConfigError as exc:
        console.print(f"[red]✗ Could not read {mcp_json_path}: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    already_registered = existing.get("mcpServers", {}).get("quor") == _MCP_SERVER_ENTRY
    new_settings = _with_quor_entry(existing)

    console.print(f"[bold]Will write MCP server registration to:[/bold] {mcp_json_path}")
    if already_registered:
        console.print("[green]  (already up to date)[/green]")
    elif not yes and not typer.confirm("Proceed?", default=True):
        console.print("Aborted.")
        raise typer.Exit(code=1)
    else:
        _write_json_atomic(mcp_json_path, new_settings)
        console.print(f"[green]✓ {mcp_json_path} updated[/green]")

    console.print(
        "\n[bold]For Claude Desktop[/bold] (or any other MCP client using "
        "claude_desktop_config.json), add this under \"mcpServers\" instead:\n"
    )
    console.print(_render_snippet())


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = orjson.loads(path.read_bytes())
    except orjson.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} does not contain a JSON object")
    return data


def _with_quor_entry(existing: dict[str, Any]) -> dict[str, Any]:
    """Add/overwrite the "quor" entry under "mcpServers" — every other
    server already registered there (a project's own, unrelated MCP
    servers) is left completely untouched, the same non-destructive-merge
    discipline the old hook installer used for settings.json."""
    new_settings = dict(existing)
    servers = dict(new_settings.get("mcpServers", {}))
    servers["quor"] = _MCP_SERVER_ENTRY
    new_settings["mcpServers"] = servers
    return new_settings


def _render_snippet() -> str:
    payload = {"mcpServers": {"quor": _MCP_SERVER_ENTRY}}
    return orjson.dumps(payload, option=orjson.OPT_INDENT_2).decode()
