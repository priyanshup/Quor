"""quor graph — deterministic Repository Dependency Graph (QB-067).

A fourth exempted utility command, following the exact process ADR-037/
ADR-038 already established for `quor map`/`quor symbols`: explicit user
sign-off was obtained before any CLI code was written, not assumed granted
by the originating task instructions alone (see `docs/final/DECISIONS.md`
ADR-039). Reuses `quor symbols`'s (QB-066) `ast_summarize` parsers directly
— `quor/pipeline/repo_profile/graph.py`'s `build_dependency_graph()` is the
single public entry point; see its own module docstring for the full
architecture.
"""

from __future__ import annotations

import time
from pathlib import Path

import typer

from quor.pipeline.repo_profile.graph import build_dependency_graph
from quor.pipeline.repo_profile.graph_render import render_json, render_markdown
from quor.tracking.db import REPO_GRAPH_FILTER_LABEL, get_tracking_db, track_invocation


def graph_command(
    path: Path | None = typer.Option(
        None, "--path", help="Repository root to graph (default: current directory)."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Output the dependency graph as JSON instead of Markdown."
    ),
) -> None:
    """Generate a deterministic repository dependency graph."""
    root = (path or Path.cwd()).resolve()
    t0 = time.monotonic()

    graph = build_dependency_graph(root)
    output = render_json(graph) if json_output else render_markdown(graph)

    typer.echo(output)
    _track_graph_invocation(root, output, t0)


def _track_graph_invocation(root: Path, output: str, t0: float) -> None:
    """Record this invocation in the same tracking DB every other Quor
    producer uses, so `quor gain` reflects `quor graph` usage.

    There is no "before" blob to compress against — this is synthesis, not
    compression, exactly like `quor map`/`quor symbols` — see
    `quor/cli/commands/map.py::_track_map_invocation`'s identical
    reasoning, not repeated here. Fails open like every other tracking
    call site: a tracking-DB error must never affect the graph output the
    user already received.
    """
    try:
        db = get_tracking_db()
        track_invocation(
            db,
            command=f"Graph: {root.as_posix()}",
            original=output,
            filtered=output,
            filter_name=REPO_GRAPH_FILTER_LABEL,
            was_passthrough=False,
            t0=t0,
        )
        db.close()
    except Exception:  # noqa: BLE001 — fail-open: tracking must never affect real output
        pass
