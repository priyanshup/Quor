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
"""

from __future__ import annotations

from dataclasses import dataclass

from quor.adapters.claude import HOOK_PS1_TEMPLATE
from quor.adapters.claude_read import HOOK_READ_PS1_TEMPLATE


@dataclass(frozen=True)
class ClaudeHookSpec:
    """One Claude Code hook Quor installs: a script plus its settings.json registration."""

    hook_id: str          # stable short key, e.g. "bash" — used for internal lookups
    label: str            # display name, e.g. "Bash" — used in doctor/init output
    event: str            # Claude Code hook event, e.g. "PreToolUse" / "PostToolUse"
    matcher: str          # Claude Code tool matcher, e.g. "Bash" / "Read"
    script_name: str      # generated .ps1 filename, also the settings.json command marker
    template: str         # HOOK_*_PS1_TEMPLATE — {python} and {schema_version} placeholders
    schema_version: int   # this hook's own definition version — see module docstring


BASH_HOOK_SPEC = ClaudeHookSpec(
    hook_id="bash",
    label="Bash",
    event="PreToolUse",
    matcher="Bash",
    script_name="claude-hook.ps1",
    template=HOOK_PS1_TEMPLATE,
    schema_version=1,
)

READ_HOOK_SPEC = ClaudeHookSpec(
    hook_id="read",
    label="Read",
    event="PostToolUse",
    matcher="Read",
    script_name="claude-hook-read.ps1",
    template=HOOK_READ_PS1_TEMPLATE,
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
