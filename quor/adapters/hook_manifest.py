"""Declarative manifest of every Claude Code hook Quor installs.

Single source of truth for `quor init --claude` (installation) and `quor
doctor` (health check): each entry below drives script generation, the
`settings.json` registration shape, and doctor's "installed / registered /
up to date" checks generically. Adding a new hook in a future version means
adding one `ClaudeHookSpec` here — not touching `init.py`'s or `doctor.py`'s
install/check logic (see those modules' loops over `HOOK_SPECS`).

Per-hook *behavioral* (roundtrip) verification is deliberately not part of
this manifest — proving a hook actually compresses requires a hook-specific
synthetic payload (see `doctor.py`'s `_check_hook_roundtrip`/
`_check_read_hook_roundtrip`), which cannot be generalized away. This
manifest only generalizes the parts that genuinely are generic: does the
script exist, is it registered, is it current.

`schema_version` is deliberately independent of `quor.__version__`: it
identifies the *shape of this specific hook's script/registration*, not the
package release. Bump it only when `render_hook_script`'s output for that
hook would actually change in a way an installed copy needs to pick up
(a different template body, a different registration shape) — not on every
Quor release. This means most Quor version bumps never make `doctor` tell
users to reinstall a hook that didn't change.

Scope note: this is intentionally narrower than QB-035A's multi-agent
`AgentAdapter` design (`docs/design/QB-035A-multi-agent-adapter-design.md`),
which proposes a full per-agent Protocol for V2 multi-assistant support.
That reuses the same "declarative hook list drives install/doctor"
conclusion this module reaches independently, but this module stays
Claude-Code-only, matching ANTI_GOALS.md #12 (no multi-agent support in V1).

QB-082: `script_name`/`template` used to be plain fields holding a fixed
PowerShell-shaped value — Quor was Windows-only until this change. They are
now `@property` methods that resolve per-platform via `is_windows()` at
*access time*, backed by four platform-specific fields
(`windows_script_name`/`posix_script_name`/`windows_template`/
`posix_template`). Resolving at access time (not once at spec-construction
time) is what lets a test flip platform detection and see
`spec.script_name`/`spec.template` change immediately, with no module
reload. `is_windows()`/`POSIX_SHELL` are the one shared source of truth for
this — `init.py` imports them rather than re-deriving platform logic itself.
See `docs/final/DECISIONS.md` ADR-043 for the full rationale, including why
Gemini's adapter (`quor/adapters/gemini_adapter.py`) is deliberately not
migrated to this pattern yet.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

from quor.adapters.claude import HOOK_PS1_TEMPLATE, HOOK_SH_TEMPLATE
from quor.adapters.claude_read import HOOK_READ_PS1_TEMPLATE, HOOK_READ_SH_TEMPLATE


def is_windows() -> bool:
    """Single source of truth for "is this a Windows hook install" across
    the whole hook install/render/registration path — `init.py` and this
    module's own `ClaudeHookSpec` properties both call this rather than
    each re-deriving a platform check. `os.name == "nt"` (not
    `sys.platform`'s more specific `"win32"`/`"cygwin"`/... string) is the
    standard-library check that most directly expresses "Windows vs. POSIX
    process semantics", which is the actual axis every call site branches
    on. Read fresh on every call (never cached at import time), so a test
    can patch `os.name` and see it take effect immediately."""
    return os.name == "nt"


POSIX_SHELL = shutil.which("sh") or "/bin/sh"
"""The POSIX shell used both to invoke a generated `.sh` launcher from
settings.json and by tests asserting against that same command string —
resolved once here so neither call site hardcodes `/bin/sh` independently.
`shutil.which("sh")` finds the real interpreter on PATH; the near-universal
`/bin/sh` is the fallback on the rare system where PATH lookup fails."""


@dataclass(frozen=True)
class ClaudeHookSpec:
    """One Claude Code hook Quor installs: a script plus its settings.json registration."""

    hook_id: str          # stable short key, e.g. "bash" — used for internal lookups
    label: str            # display name, e.g. "Bash" — used in doctor/init output
    event: str            # Claude Code hook event, e.g. "PreToolUse" / "PostToolUse"
    matcher: str          # Claude Code tool matcher, e.g. "Bash" / "Read"
    windows_script_name: str   # generated .ps1 filename on Windows
    posix_script_name: str     # generated .sh filename on macOS/Linux
    windows_template: str      # HOOK_*_PS1_TEMPLATE — {python}/{schema_version} placeholders
    posix_template: str        # HOOK_*_SH_TEMPLATE — {python}/{schema_version} placeholders
    schema_version: int   # this hook's own definition version — see module docstring

    @property
    def script_name(self) -> str:
        """Platform-resolved generated filename — also the settings.json
        command marker `doctor.py`/`init.py` match on. See module docstring
        for why this is a property, not a fixed field."""
        return self.windows_script_name if is_windows() else self.posix_script_name

    @property
    def template(self) -> str:
        """Platform-resolved launcher template — {python} and
        {schema_version} placeholders, filled in by `render_hook_script()`.
        See module docstring for why this is a property, not a fixed field."""
        return self.windows_template if is_windows() else self.posix_template


BASH_HOOK_SPEC = ClaudeHookSpec(
    hook_id="bash",
    label="Bash",
    event="PreToolUse",
    matcher="Bash",
    windows_script_name="claude-hook.ps1",
    posix_script_name="claude-hook.sh",
    windows_template=HOOK_PS1_TEMPLATE,
    posix_template=HOOK_SH_TEMPLATE,
    schema_version=1,
)

READ_HOOK_SPEC = ClaudeHookSpec(
    hook_id="read",
    label="Read",
    event="PostToolUse",
    matcher="Read",
    windows_script_name="claude-hook-read.ps1",
    posix_script_name="claude-hook-read.sh",
    windows_template=HOOK_READ_PS1_TEMPLATE,
    posix_template=HOOK_READ_SH_TEMPLATE,
    schema_version=1,
)

# Iterated by `quor init --claude` (install) and `quor doctor` (health check).
HOOK_SPECS: tuple[ClaudeHookSpec, ...] = (BASH_HOOK_SPEC, READ_HOOK_SPEC)


def render_hook_script(spec: ClaudeHookSpec, *, python: str) -> str:
    """Render `spec`'s PowerShell template with the interpreter path and
    `spec`'s own schema version embedded — the `# quor-hook-schema:` line
    `doctor`'s freshness check reads back to detect an outdated install.
    Deliberately not `quor.__version__` — see module docstring."""
    return spec.template.format(python=python, schema_version=spec.schema_version)
