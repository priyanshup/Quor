"""quor version — show the installed Quor version (QB-073).

Exists as a real subcommand, not only `quor --version`/the bare-invocation
banner `cli/main.py::root()` already prints — a user typing `quor version`
(the common convention across `git version`, `node version`-alikes, etc.)
previously fell through to `__main__.py`'s shell dispatcher (nothing in
`_CLI_COMMANDS` named `"version"`), which tried to run a literal `version`
command and failed with a raw `WinError 2`/`FileNotFoundError` — the exact
dispatcher-fallthrough bug class ADR-037 first caught for `quor map`. This
module, `_CLI_COMMANDS`'s new `"version"` entry, and `cli/main.py`'s
`--version` eager option all report the same `quor.__version__` string, so
`quor version`, `quor --version`, and the bare `quor` banner can never
disagree with each other.
"""

from __future__ import annotations

import typer

from quor import __version__


def version_command() -> None:
    """Show the installed Quor version."""
    typer.echo(f"quor {__version__}")
