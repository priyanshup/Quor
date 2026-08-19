"""quor graph — deterministic Repository Dependency Graph (QB-067).

A fourth exempted utility command, following the exact process ADR-037/
ADR-038 already established for `quor map`/`quor symbols`: explicit user
sign-off was obtained before any CLI code was written, not assumed granted
by the originating task instructions alone (see `docs/final/DECISIONS.md`
ADR-039). Reuses `quor symbols`'s (QB-066) `ast_summarize` parsers directly
— `quor/pipeline/repo_profile/graph.py`'s `build_dependency_graph()` is the
single public entry point; see its own module docstring for the full
architecture.

As of QB-072 (Automatic Repository Intelligence), this command no longer
calls `graph.build_dependency_graph()` directly — it goes through
`intel.ensure_repo_intelligence()`, which transparently handles first-time
onboarding, cache reuse, and incremental rebuilds; see that module's own
docstring for the full design. `--rebuild` forces a full rebuild,
bypassing the cache entirely.

As of QB-074, `--path` is validated up front (`repo_path.resolve_repo_root`)
— a nonexistent path or a file passed where a directory was expected now
exits with a clear, actionable message instead of silently producing an
empty graph — and a colorized progress/summary presentation
(`repo_progress.py`) reports elapsed time and an edge count on stderr.
"""

from __future__ import annotations

import time
from pathlib import Path

import typer

from quor.cli.repo_path import resolve_repo_root
from quor.cli.repo_progress import print_build_summary, progress_echo
from quor.pipeline.repo_profile.graph_render import render_json, render_markdown
from quor.pipeline.repo_profile.intel import ensure_repo_intelligence
from quor.tracking.db import REPO_GRAPH_FILTER_LABEL, get_tracking_db, track_invocation_safe


def graph_command(
    path: Path | None = typer.Option(
        None, "--path", help="Repository root to graph (default: current directory)."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Output the dependency graph as JSON instead of Markdown."
    ),
    rebuild: bool = typer.Option(
        False, "--rebuild", help="Force a full rebuild of repository intelligence, bypassing the cache."
    ),
) -> None:
    """Generate a deterministic repository dependency graph."""
    root = resolve_repo_root(path)
    t0 = time.monotonic()

    intel = ensure_repo_intelligence(root, rebuild=rebuild, echo=progress_echo)
    graph = intel.graph
    output = render_json(graph) if json_output else render_markdown(graph)

    detail = f"{graph.total_edges} edge{'s' if graph.total_edges != 1 else ''} ({graph.resolved_edges} resolved)"
    print_build_summary(intel, detail, elapsed_seconds=time.monotonic() - t0)

    typer.echo(output)
    # Synthesis, not compression — see quor/tracking/db.py's
    # track_invocation_safe() docstring for why original/filtered default
    # equal and this call is fail-open (QB-107).
    track_invocation_safe(
        get_tracking_db,
        command=f"Graph: {root.as_posix()}",
        original=output,
        filter_name=REPO_GRAPH_FILTER_LABEL,
        t0=t0,
        close_after=True,
    )
