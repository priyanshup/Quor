"""quor uninstall-hooks — remove a pre-QB-104 hook-based Quor installation.

QB-104 replaced Quor's hook-based integration (Claude Code's PreToolUse/Bash
and PostToolUse/Read hooks, plus Gemini CLI's BeforeTool hook) with the MCP
server (`quor/mcp/server.py`) as the sole integration surface. Anyone who ran
`quor init --claude` or `quor init --agent gemini` under an older release
still has launcher scripts on disk and entries in `~/.claude/settings.json`/
`~/.gemini/settings.json` that now do nothing useful (their target,
`quor hook ...`, no longer exists) but are otherwise harmless clutter.

`detect_legacy_hooks()`/`remove_legacy_hooks()` are the shared detect/remove
primitives: this module's own `uninstall_hooks` command uses them behind an
explicit confirmation prompt, and `quor/cli/commands/init.py`'s `init()`
uses them unprompted (QB-104 Phase 3) as an automatic housekeeping step —
one detection/removal implementation, two call sites with different UX.

Never touches a settings.json entry that doesn't reference one of Quor's own
known script names, so another tool's hook registered alongside Quor's is
left completely alone.

The known script names and settings.json shapes are hardcoded here (not
imported from the now-removed `quor.adapters.hook_manifest`/
`gemini_adapter` modules) — this command outlives the code that used to
generate them, and only needs to recognize their fixed, historical names.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import orjson
import platformdirs
import typer
from rich.console import Console

from quor.atomic_io import write_json_atomic as _write_json_atomic
from quor.errors import ConfigError, ExitCode

console = Console()

# Every launcher-script filename any pre-QB-104 release ever generated,
# across both hook families (Claude's HOOK_SPECS, Gemini's single hook) and
# both platform variants (QB-082/083's Windows .ps1 / POSIX .sh split) —
# checked regardless of the current platform, since a script left over from
# an OS migration or a dual-boot machine is still worth cleaning up.
_LEGACY_HOOK_SCRIPT_NAMES: tuple[str, ...] = (
    "claude-hook.ps1",
    "claude-hook.sh",
    "claude-hook-read.ps1",
    "claude-hook-read.sh",
    "gemini-hook.ps1",
    "gemini-hook.sh",
)

# (display label, settings.json path, hook-event keys Quor ever registered
# under). Claude Code and Gemini CLI each keep their own settings.json, per
# quor.adapters.claude_adapter's/gemini_adapter's own historical install
# targets.
_LEGACY_SETTINGS_TARGETS: tuple[tuple[str, Path, tuple[str, ...]], ...] = (
    ("Claude Code", Path.home() / ".claude" / "settings.json", ("PreToolUse", "PostToolUse")),
    ("Gemini CLI", Path.home() / ".gemini" / "settings.json", ("BeforeTool",)),
)

LegacyEntry = tuple[str, Path, list[str]]


def detect_legacy_hooks() -> tuple[list[Path], list[LegacyEntry]]:
    """Read-only scan for a pre-QB-104 hook installation. Returns
    `(found_scripts, found_entries)` — both empty if nothing found. Never
    raises: an unreadable settings.json is reported to the console and
    skipped, not treated as fatal (mirrors every other fail-open scan in
    this codebase)."""
    hooks_dir = Path(platformdirs.user_data_dir("quor")) / "hooks"
    found_scripts = [
        hooks_dir / name for name in _LEGACY_HOOK_SCRIPT_NAMES if (hooks_dir / name).exists()
    ]

    found_entries: list[LegacyEntry] = []
    for label, settings_file, event_keys in _LEGACY_SETTINGS_TARGETS:
        if not settings_file.exists():
            continue
        try:
            settings = _read_settings(settings_file)
        except ConfigError as exc:
            console.print(f"[yellow]⚠  Could not read {settings_file}: {exc}[/yellow]")
            continue
        matched = [key for key in event_keys if _has_quor_entry(settings, key)]
        if matched:
            found_entries.append((label, settings_file, matched))

    return found_scripts, found_entries


def remove_legacy_hooks(found_scripts: list[Path], found_entries: list[LegacyEntry]) -> None:
    """Perform the actual removal for a `detect_legacy_hooks()` result,
    printing one status line per item. Each removal is independent and
    fail-open — one script or settings file failing to update is reported
    and does not block the others."""
    for path in found_scripts:
        try:
            path.unlink()
            console.print(f"[green]✓ Removed {path}[/green]")
        except OSError as exc:
            console.print(f"[red]✗ Could not remove {path}: {exc}[/red]")

    for label, settings_file, events in found_entries:
        try:
            settings = _read_settings(settings_file)
            cleaned = _strip_quor_entries(settings, events)
            _write_json_atomic(settings_file, cleaned)
            console.print(f"[green]✓ Removed Quor entries from {settings_file}[/green]")
        except Exception as exc:  # noqa: BLE001 — one target's failure must not stop the other
            console.print(f"[red]✗ Could not update {settings_file} ({label}): {exc}[/red]")


def uninstall_hooks(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Remove a pre-QB-104 hook installation."""
    found_scripts, found_entries = detect_legacy_hooks()

    if not found_scripts and not found_entries:
        console.print("[green]No legacy Quor hook installation found — nothing to do.[/green]")
        return

    console.print("[bold]The following will be removed:[/bold]")
    for path in found_scripts:
        console.print(f"  • {path}")
    for label, settings_file, events in found_entries:
        console.print(f"  • Quor entries under {', '.join(events)} in {settings_file} ({label})")

    if not yes and not typer.confirm("Proceed?", default=True):
        console.print("Aborted.")
        raise typer.Exit(code=ExitCode.GENERAL_ERROR)

    remove_legacy_hooks(found_scripts, found_entries)

    console.print(
        "\n[bold]Done.[/bold] Quor's old hook-based integration has been removed. "
        "Run `quor init --mcp` for MCP server setup."
    )


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


def _command_is_quors(command: str) -> bool:
    return any(name in command for name in _LEGACY_HOOK_SCRIPT_NAMES)


def _has_quor_entry(settings: dict[str, Any], event_key: str) -> bool:
    entries = settings.get("hooks", {}).get(event_key, [])
    return any(
        _command_is_quors(h.get("command", ""))
        for entry in entries
        for h in entry.get("hooks", [])
    )


def _strip_quor_entries(settings: dict[str, Any], event_keys: list[str]) -> dict[str, Any]:
    """Return `settings` with every hook Quor ever registered removed from
    each key in `event_keys` — leaves any other tool's hook under the same
    key completely untouched. Drops an event key entirely once it has no
    entries left, and drops the whole `"hooks"` object if every event key
    under it ends up empty, rather than leaving empty-list clutter behind."""
    new_settings = dict(settings)
    hooks = dict(new_settings.get("hooks", {}))

    for event_key in event_keys:
        entries = hooks.get(event_key, [])
        kept_entries = []
        for entry in entries:
            kept_hooks = [h for h in entry.get("hooks", []) if not _command_is_quors(h.get("command", ""))]
            if kept_hooks:
                kept_entries.append({**entry, "hooks": kept_hooks})
        if kept_entries:
            hooks[event_key] = kept_entries
        else:
            hooks.pop(event_key, None)

    if hooks:
        new_settings["hooks"] = hooks
    else:
        new_settings.pop("hooks", None)

    return new_settings
