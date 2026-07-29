"""quor symbols — deterministic Repository Symbols index (QB-066).

A sibling capability to `quor map` (QB-061), not a filter and not a field
folded into `RepoProfile`: it walks the repo once and, for every file whose
extension has a registered `ast_summarize` parser, extracts the classes,
interfaces, structs, traits, enums, functions, and methods it declares —
information a symbol index needs but a one-shot orientation profile
deliberately does not carry by default (see
`quor/pipeline/repo_profile/symbols.py`'s own module docstring for why this
is a separate command, and ADR-038 for the full architecture decision
record). Same exemption category as `quor schema`/`quor map`: a
non-filtering utility command, not one of the six V1 filtering-operation
commands.
"""

from __future__ import annotations

import time
from pathlib import Path

import typer

from quor.pipeline.repo_profile.symbols import build_symbol_index
from quor.pipeline.repo_profile.symbols_render import render_json, render_markdown
from quor.tracking.db import REPO_SYMBOLS_FILTER_LABEL, get_tracking_db, track_invocation


def symbols_command(
    path: Path | None = typer.Option(
        None, "--path", help="Repository root to index (default: current directory)."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Output the symbol index as JSON instead of Markdown."
    ),
) -> None:
    """Generate a deterministic repository-wide symbol index."""
    root = (path or Path.cwd()).resolve()
    t0 = time.monotonic()

    index = build_symbol_index(root)
    output = render_json(index) if json_output else render_markdown(index)

    typer.echo(output)
    _track_symbols_invocation(root, output, t0)


def _track_symbols_invocation(root: Path, output: str, t0: float) -> None:
    """Record this invocation in the same tracking DB every other Quor
    producer uses, so `quor gain` reflects `quor symbols` usage.

    There is no "before" blob to compress against — this is synthesis, not
    compression, exactly like `quor map` — so `original`/`filtered` are
    deliberately passed as the same value (see
    `quor/cli/commands/map.py::_track_map_invocation`'s identical
    reasoning, not repeated here). Fails open like every other tracking
    call site: a tracking-DB error must never affect the index output the
    user already received.
    """
    try:
        db = get_tracking_db()
        track_invocation(
            db,
            command=f"Symbols: {root.as_posix()}",
            original=output,
            filtered=output,
            filter_name=REPO_SYMBOLS_FILTER_LABEL,
            was_passthrough=False,
            t0=t0,
        )
        db.close()
    except Exception:  # noqa: BLE001 — fail-open: tracking must never affect real output
        pass
