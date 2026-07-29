"""Shared `--path` resolution and validation for `quor map`/`quor symbols`/
`quor graph` (QB-074).

Before this, an invalid `--path` (a typo'd directory, or a file passed
where a directory was expected) was never rejected — `walk_repository()`'s
no-git fallback (`os.walk`) silently returns zero files for a path that
doesn't exist or isn't a directory, so the command went on to print a
fully-formed, valid-looking, but empty `RepoProfile`/`RepoSymbolIndex`/
`RepoDependencyGraph` at exit code 0. That's a real diagnosability gap
(nothing tells the user their `--path` was wrong at all, let alone why or
how to fix it) — `resolve_repo_root()` closes it with an explicit check
before any repository-intelligence work begins, printing a message
structured the way every clear CLI error should be: what failed, why, and
what to do about it — then exiting non-zero rather than continuing.
"""

from __future__ import annotations

from pathlib import Path

import typer

from quor.errors import ExitCode


def resolve_repo_root(path: Path | None) -> Path:
    """Resolve `--path` (or the current directory, if omitted) to an
    existing directory, or exit with a clear, actionable error.

    Deliberately checked once, here, rather than left to whatever
    downstream code first happens to touch the filesystem — a single,
    consistent error message and exit code for every command that accepts
    `--path`, instead of three commands each discovering (or not) the
    problem differently.
    """
    root = (path or Path.cwd()).resolve()

    if not root.exists():
        typer.secho(f"Error: repository path does not exist: {root}", fg=typer.colors.RED, err=True)
        typer.echo(
            "Quor could not find that directory. Check the --path value, "
            "or omit --path to use the current directory.",
            err=True,
        )
        raise typer.Exit(code=ExitCode.GENERAL_ERROR)

    if not root.is_dir():
        typer.secho(f"Error: --path must be a directory, not a file: {root}", fg=typer.colors.RED, err=True)
        typer.echo(
            "Point --path at the repository root itself, not a specific file inside it.",
            err=True,
        )
        raise typer.Exit(code=ExitCode.GENERAL_ERROR)

    return root
