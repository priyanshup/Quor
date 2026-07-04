"""Single source of truth for how Quor invokes itself in a rewritten command.

Runtime must never depend on the pip-generated `quor`/`qr` console-script
launchers — some corporate application-control policies block them outright
(they're just PATH-resolved executables, indistinguishable from arbitrary
third-party binaries), while `python -m quor` is allowed. Re-entering the
exact interpreter that is already running Quor (`sys.executable`) also sidesteps
any ambiguity across venvs, pipx, Poetry, uv, and conda environments, since it
is guaranteed to be the interpreter that already has Quor importable.
"""

from __future__ import annotations

import shlex
import sys


def get_quor_invocation() -> str:
    """Return the shell-safe command prefix used to invoke Quor.

    If Quor is ever shipped as a frozen binary (PyInstaller/Nuitka),
    `sys.executable` would be that binary and `-m quor` would no longer
    apply — not the case for any currently published build.
    """
    if sys.executable:
        return f"{shlex.quote(sys.executable)} -m quor"
    return "quor"
