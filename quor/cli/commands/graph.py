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
from quor.tracking.db import REPO_GRAPH_FILTER_LABEL, get_tracking_db, track_invocation


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
