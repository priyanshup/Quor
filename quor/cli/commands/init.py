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

import sys
from pathlib import Path
from typing import Any

import orjson
import typer
from rich.console import Console

from quor.atomic_io import write_json_atomic as _write_json_atomic
from quor.cli.commands.uninstall_hooks import detect_legacy_hooks, remove_legacy_hooks
from quor.errors import ConfigError
from quor.mcp.launcher import expected_venv_python

console = Console()

_MCP_JSON_FILENAME = ".mcp.json"


def _mcp_server_entry(project_root: Path) -> dict[str, Any]:
    """Built from `sys.executable`, not a bare "python" — same rationale as
    `quor/rewrite/invocation.py`'s `get_quor_invocation()`: an MCP client
    spawns this command as a raw subprocess (no shell, no venv activation,
    no PATH resolution the way an interactive terminal would do it), so a
    bare "python" resolves to whatever the *client's* environment happens
    to put first on PATH, which is almost never the interpreter Quor is
    actually installed into (pipx, conda, a project venv, etc. all break
    this). `sys.executable` is the one interpreter guaranteed to have
    `quor`/`mcp` importable, because it's the interpreter currently running
    this `init` command. Falls back to bare "python" only in the same rare
    case `get_quor_invocation()` falls back to bare "quor" — `sys.executable`
    empty/unset (e.g. some embedded interpreters).

    Exception: if `project_root` already has a `.venv` populated (the
    interpreter `expected_venv_python()` predicts for this OS actually
    exists on disk), scaffold a `${CLAUDE_PROJECT_DIR:-.}`-relative command
    into it instead. This is deliberately narrower than always preferring a
    relative path — a relative `.venv` layout differs by OS
    (`.venv/bin/python` vs `.venv\\Scripts\\python.exe`), so a single
    scaffolded `.mcp.json` still isn't portable to a teammate on a
    different OS if committed to git; each OS should re-run `quor init
    --mcp` locally against its own `.venv` rather than relying on a
    checked-in file scaffolded elsewhere. The upside — matching this
    project's already-committed `.mcp.json` convention, and surviving a
    `.venv` recreation without a re-scaffold — is worth it specifically
    when there's a real local `.venv` to point at.

    Either way, `command` ends up forward-slash-only: the `.venv`-relative
    branch is built from a literal string, and the `sys.executable` branch
    goes through `Path.as_posix()`, since `sys.executable` is
    backslash-separated on Windows. Forward slashes are accepted natively
    by Windows' own `CreateProcess`, so there's no POSIX-only downside, and
    it keeps the scaffolded JSON free of backslash escapes that a
    non-strict-JSON MCP client could mishandle.

    Points at `quor.mcp.launcher`, not `quor.mcp.server` directly — the
    right interpreter at scaffold time doesn't guarantee that interpreter's
    environment is still correct later (a `.venv` can drift out of sync
    with `pyproject.toml`, a dependency can get uninstalled). The launcher
    runs a pre-flight import check and self-repairs before handing off to
    the real server, instead of crashing pre-handshake with no trust
    prompt ever appearing — see `quor/mcp/launcher.py`'s module docstring."""
    venv_python = expected_venv_python(project_root)
    if venv_python.exists():
        suffix = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
        command = f"${{CLAUDE_PROJECT_DIR:-.}}/.venv/{suffix}"
    elif sys.executable:
        command = Path(sys.executable).as_posix()
    else:
        command = "python"
    return {
        "command": command,
        "args": ["-m", "quor.mcp.launcher"],
    }


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
    project_root = Path.cwd()
    mcp_json_path = project_root / _MCP_JSON_FILENAME
    entry = _mcp_server_entry(project_root)

    try:
        existing = _read_json(mcp_json_path)
    except ConfigError as exc:
        console.print(f"[red]✗ Could not read {mcp_json_path}: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    already_registered = existing.get("mcpServers", {}).get("quor") == entry
    new_settings = _with_quor_entry(existing, entry)

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
    console.print(_render_snippet(entry))


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


def _with_quor_entry(existing: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    """Add/overwrite the "quor" entry under "mcpServers" — every other
    server already registered there (a project's own, unrelated MCP
    servers) is left completely untouched, the same non-destructive-merge
    discipline the old hook installer used for settings.json."""
    new_settings = dict(existing)
    servers = dict(new_settings.get("mcpServers", {}))
    servers["quor"] = entry
    new_settings["mcpServers"] = servers
    return new_settings


def _render_snippet(entry: dict[str, Any]) -> str:
    payload = {"mcpServers": {"quor": entry}}
    return orjson.dumps(payload, option=orjson.OPT_INDENT_2).decode()
