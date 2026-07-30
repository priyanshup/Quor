"""Gemini CLI adapter (QB-068) — `COMMAND_INTERCEPT` only.

Researched against Gemini CLI's own documentation (geminicli.com/docs/hooks,
the google-gemini/gemini-cli GitHub repo) before writing any code, per
ADR-036's mandatory pre-flight compatibility gate (§10.3). Findings that
shaped this adapter's scope:

**Confirmed, and what this adapter relies on:**
- Gemini CLI's `BeforeTool` hook fires before a tool call and its stdout
  response supports `hookSpecificOutput.tool_input`, which "merges with
  model arguments" — i.e. a genuine rewrite capability, the Gemini
  equivalent of Claude Code's `hookSpecificOutput.updatedInput`. This is
  what makes `COMMAND_INTERCEPT` achievable here (unlike `CodexAdapter`,
  where no such capability is confirmed).
- The shell-execution tool's name is `run_shell_command` (confirmed via
  Gemini CLI's own hook examples and tool docs) — used as this adapter's
  `matcher`.
- Hooks are registered under a `"hooks"` key in `settings.json`, with the
  same three-tier layering Quor's Claude integration already targets at
  user scope (`~/.gemini/settings.json`, mirroring `~/.claude/settings.json`).
- stdin payload includes `tool_name` and `tool_input` (an object); a
  documented security-hook example reads `input.tool_input.content` for the
  `write_file` tool, confirming `tool_input` fields are named after the
  tool's own parameters.

**Not confirmed, and handled conservatively:**
- The exact parameter name `run_shell_command` uses for the command string
  itself is inferred as `command` from Gemini CLI's general tool schema
  (consistent with every public description of that tool), not from a
  hooks-specific worked example — no hooks doc shows a concrete
  `run_shell_command` BeforeTool payload. If this is wrong, `handle_bytes()`
  fails validation and falls open (original bytes returned), exactly like
  every other unexpected-shape failure in Quor's adapters — it does not
  raise past `__main__._run_hook()`'s outer guard.
- `AfterTool`'s only confirmed output capability is `additionalContext`
  (append) and `tailToolCallRequest` (chain another call) — no confirmed
  full-content-replace field equivalent to Claude Code's
  `updatedToolOutput`. `CONTENT_INTERCEPT` is therefore deliberately NOT
  declared as supported here; adding it later is a non-breaking, additive
  change once/if a replace-capable field is confirmed.
- Windows compatibility is not addressed by Gemini CLI's own docs. This
  adapter reuses Quor's existing Windows-first hook-script pattern (a
  PowerShell script invoked via `powershell -ExecutionPolicy Bypass -File`,
  identical in shape to `quor/adapters/hook_manifest.py`'s Claude templates)
  on the working assumption that Gemini CLI, like Claude Code, invokes a
  hook's `command` string through the OS's normal command execution rather
  than a bundled Unix shell — `quor doctor`'s roundtrip check (below)
  verifies Quor's own response logic is correct, but — exactly like
  `ADR-034`'s equivalent caveat for Claude's Read hook — it cannot prove the
  installed Gemini CLI binary actually honors the rewrite end to end. That
  requires a real Gemini CLI session, tracked as follow-up validation.

QB-083: the PowerShell-only launcher above was Windows-only in exactly the
way QB-082/ADR-043 found (and fixed) for Claude — `powershell`/`pwsh` don't
exist on a default macOS/Linux install, so `quor init --agent gemini` wrote
a permanently broken hook on those platforms. This module now follows the
same fix, reusing `hook_manifest.is_windows()`/`POSIX_SHELL` directly rather
than re-deriving platform detection here: Windows keeps the PS1 launcher
unchanged, macOS/Linux get a POSIX `.sh` launcher registered as
`<sh> "<path>"`, chmod'd `0o755` after writing. See `docs/final/DECISIONS.md`
ADR-044 for the full rationale — this module deliberately does not adopt
`hook_manifest.py`'s `ClaudeHookSpec` dataclass (Gemini has exactly one hook,
not a growing family iterated by a shared install/doctor loop like Claude's
`HOOK_SPECS`), just its two platform primitives.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import orjson
from pydantic import BaseModel, ConfigDict

from quor.adapters import hook_manifest
from quor.adapters.base import (
    QUOR_ADAPTER_API_VERSION,
    AgentEvent,
    DoctorCheck,
    DoctorContext,
    InstallContext,
    InstallResult,
)
from quor.atomic_io import write_json_atomic as _write_json_atomic
from quor.atomic_io import write_text_atomic as _write_text_atomic
from quor.errors import ConfigError
from quor.rewrite.classifier import rewrite_command

if TYPE_CHECKING:
    from quor.tracking.db import TrackingDB

_SUPPORTED_EVENTS: frozenset[AgentEvent] = frozenset({AgentEvent.COMMAND_INTERCEPT})

_UTF8_BOM = "﻿"

# The tool this adapter's BeforeTool hook is registered against — confirmed
# via Gemini CLI's own hook examples (see module docstring).
_SHELL_TOOL_NAME = "run_shell_command"

_WINDOWS_SCRIPT_NAME = "gemini-hook.ps1"
_POSIX_SCRIPT_NAME = "gemini-hook.sh"
_SCHEMA_VERSION = 1

HOOK_COMMAND = "{python} -m quor hook gemini command_intercept"

HOOK_PS1_TEMPLATE = """\
# Quor hook script — generated by `quor init --agent gemini`
# quor-hook-schema: {schema_version}
# Do not edit this file. To update, run `quor init --agent gemini` again.
$ErrorActionPreference = 'Stop'
$json = [Console]::In.ReadToEnd()
$json | & '{python}' -m quor hook gemini command_intercept
"""

# QB-083: POSIX (macOS/Linux) equivalent of HOOK_PS1_TEMPLATE, registered
# instead of it when hook_manifest.is_windows() is False — same `exec`
# shape as quor/adapters/claude.py's HOOK_SH_TEMPLATE (QB-082).
HOOK_SH_TEMPLATE = """\
#!/bin/sh
# Quor hook script — generated by `quor init --agent gemini`
# quor-hook-schema: {schema_version}
# Do not edit this file. To update, run `quor init --agent gemini` again.
exec "{python}" -m quor hook gemini command_intercept
"""


def _script_name() -> str:
    """Platform-resolved generated filename, read fresh on every call — the
    Gemini-adapter equivalent of `ClaudeHookSpec.script_name` (QB-082),
    minus the dataclass since Gemini has exactly one hook, not a family."""
    return _WINDOWS_SCRIPT_NAME if hook_manifest.is_windows() else _POSIX_SCRIPT_NAME


class _GeminiToolInput(BaseModel):
    """The tool_input object inside a Gemini CLI BeforeTool payload for
    run_shell_command. See module docstring: the `command` field name is
    inferred from Gemini CLI's general tool schema, not a hooks-specific
    worked example."""

    model_config = ConfigDict(extra="allow", frozen=True)
    command: str = ""


class _GeminiBeforeToolInput(BaseModel):
    """Full Gemini CLI BeforeTool hook stdin payload (the fields this
    adapter actually uses; extra fields like session_id/cwd/timestamp are
    preserved via extra="allow" but not consumed)."""

    model_config = ConfigDict(extra="allow", frozen=True)
    tool_name: str = ""
    tool_input: _GeminiToolInput


def handle_bytes(raw_stdin: bytes) -> bytes:
    """Bytes-in/bytes-out core, mirroring `quor.adapters.claude.handle_bytes`
    exactly in shape (parse -> rewrite -> serialize, UTF-8-strict decode,
    BOM-tolerant). Raises on parse/validation errors — the caller
    (`GeminiAdapter.handle_event`, then `__main__._run_hook()`) handles
    fail-open."""
    raw = raw_stdin.decode("utf-8").lstrip(_UTF8_BOM)
    data: dict[str, Any] = orjson.loads(raw)

    hook_input = _GeminiBeforeToolInput.model_validate(data)
    original_cmd = hook_input.tool_input.command

    response: dict[str, Any] = {"decision": "allow"}

    rewritten = rewrite_command(original_cmd)
    if rewritten is not None and rewritten != original_cmd:
        updated_input = dict(data.get("tool_input", {}))
        updated_input["command"] = rewritten
        response["hookSpecificOutput"] = {"tool_input": updated_input}

    return orjson.dumps(response)


def _script_path() -> Path:
    import platformdirs

    return Path(platformdirs.user_data_dir("quor")) / "hooks" / _script_name()


def _settings_path(ctx_override: Path | None) -> Path:
    return ctx_override or (Path.home() / ".gemini" / "settings.json")


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


def _hook_registered(settings: dict[str, Any]) -> bool:
    script_name = _script_name()
    entries = settings.get("hooks", {}).get("BeforeTool", [])
    for entry in entries:
        for h in entry.get("hooks", []):
            if script_name in h.get("command", ""):
                return True
    return False


def _install_hook_entry(settings: dict[str, Any], script_path: Path) -> dict[str, Any]:
    script_name = _script_name()
    new_settings = dict(settings)
    hooks = dict(new_settings.get("hooks", {}))
    entries = [
        entry
        for entry in hooks.get("BeforeTool", [])
        if not any(script_name in h.get("command", "") for h in entry.get("hooks", []))
    ]
    if hook_manifest.is_windows():
        command = f'powershell -ExecutionPolicy Bypass -File "{script_path}"'
    else:
        command = f'{hook_manifest.POSIX_SHELL} "{script_path}"'
    entries.append(
        {
            "matcher": _SHELL_TOOL_NAME,
            "hooks": [{"type": "command", "command": command, "name": "quor-compress"}],
        }
    )
    hooks["BeforeTool"] = entries
    new_settings["hooks"] = hooks
    return new_settings




class GeminiAdapter:
    """Gemini CLI — `COMMAND_INTERCEPT` (BeforeTool/run_shell_command) only.
    See module docstring for what's confirmed vs. inferred."""

    agent_id: ClassVar[str] = "gemini"
    display_name: ClassVar[str] = "Gemini CLI"
    api_version: ClassVar[int] = QUOR_ADAPTER_API_VERSION

    @property
    def supported_events(self) -> frozenset[AgentEvent]:
        return _SUPPORTED_EVENTS

    def handle_event(
        self, event: AgentEvent, raw_stdin: bytes, tracking: TrackingDB | None
    ) -> bytes | None:
        if event is AgentEvent.COMMAND_INTERCEPT:
            return handle_bytes(raw_stdin)
        return None

    def install(self, ctx: InstallContext) -> InstallResult:
        settings_file = _settings_path(ctx.settings_override)
        script_path = _script_path()

        try:
            existing_settings = _read_settings(settings_file)
        except ConfigError as exc:
            return InstallResult(installed_paths=(), warnings=(str(exc),))

        template = HOOK_PS1_TEMPLATE if hook_manifest.is_windows() else HOOK_SH_TEMPLATE
        script_content = template.format(python=sys.executable, schema_version=_SCHEMA_VERSION)
        _write_text_atomic(script_path, script_content)
        if not hook_manifest.is_windows():
            # QB-083: match Claude's own POSIX launcher (QB-082) — conventional
            # for a shell script and avoids a surprise if a user later runs
            # the script directly, though settings.json always invokes it via
            # an explicit `<sh> "<path>"` command regardless.
            script_path.chmod(0o755)
        new_settings = _install_hook_entry(existing_settings, script_path)
        _write_json_atomic(settings_file, new_settings)

        return InstallResult(installed_paths=(script_path, settings_file), warnings=())

    def doctor_checks(self, ctx: DoctorContext) -> list[DoctorCheck]:
        settings_file = _settings_path(ctx.settings_override)
        script_path = _script_path()
        script_exists = script_path.exists()

        checks: list[DoctorCheck] = []

        if not script_exists:
            # Never installed — advisory only (ok=True). Gated on the
            # Gemini-specific script path alone (never shared with any
            # other adapter), not on whether `settings_file` happens to
            # exist — that file may already exist purely because *another*
            # agent (e.g. Claude) was installed into the same settings.json
            # (or, under `--settings-path` in tests, the same override path
            # meant for a different adapter's check). Using it here would
            # make Gemini falsely look "partially installed" for a user who
            # never asked for it. This is a new, opt-in integration; its
            # absence must never flip `quor doctor` to unhealthy for a user
            # who only uses Claude Code.
            checks.append(
                (
                    f"{self.display_name} hook installed",
                    True,
                    "not installed — optional; run `quor init --agent gemini` to enable",
                )
            )
        else:
            checks.append(
                (
                    f"{self.display_name} hook script installed",
                    script_exists,
                    str(script_path)
                    if script_exists
                    else f"not found at {script_path} — run `quor init --agent gemini`",
                )
            )
            try:
                settings = _read_settings(settings_file)
                registered = _hook_registered(settings)
            except ConfigError as exc:
                registered = False
                checks.append((f"{self.display_name} settings readable", False, str(exc)))
            checks.append(
                (
                    f"{self.display_name} hook registered in settings.json",
                    registered,
                    ""
                    if registered
                    else f"no BeforeTool entry in {settings_file} references {_script_name()} — "
                    "run `quor init --agent gemini`",
                )
            )

        # Pure-logic roundtrip: proves Quor's own handle_bytes() correctly
        # rewrites a synthetic payload, independent of install state — the
        # Gemini-side equivalent of ClaudeAdapter's _check_hook_roundtrip.
        # This is a genuine pass/fail (not advisory): it verifies Quor's own
        # code, not whether the user opted in.
        checks.append(self._check_roundtrip())
        return checks

    def _check_roundtrip(self) -> DoctorCheck:
        from quor.rewrite.invocation import get_quor_invocation

        payload = orjson.dumps(
            {"tool_name": _SHELL_TOOL_NAME, "tool_input": {"command": "git status"}}
        )
        try:
            result = orjson.loads(handle_bytes(payload))
            rewritten = (
                result.get("hookSpecificOutput", {}).get("tool_input", {}).get("command", "")
            )
            expected = f"{get_quor_invocation()} git status"
            if rewritten == expected:
                return (f"{self.display_name} hook responds correctly", True, "")
            return (
                f"{self.display_name} hook responds correctly",
                False,
                f"unexpected rewrite: {rewritten!r}",
            )
        except Exception as exc:  # noqa: BLE001
            return (f"{self.display_name} hook responds correctly", False, str(exc))
