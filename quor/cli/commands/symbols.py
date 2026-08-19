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

As of QB-072 (Automatic Repository Intelligence), this command no longer
calls `symbols.build_symbol_index()` directly — it goes through
`intel.ensure_repo_intelligence()`, which transparently handles first-time
onboarding, cache reuse, and incremental rebuilds; see that module's own
docstring for the full design. `--rebuild` forces a full rebuild,
bypassing the cache entirely.

As of QB-074, `--path` is validated up front (`repo_path.resolve_repo_root`)
— a nonexistent path or a file passed where a directory was expected now
exits with a clear, actionable message instead of silently producing an
empty index — and a colorized progress/summary presentation
(`repo_progress.py`) reports elapsed time and a symbol count on stderr.
"""

from __future__ import annotations

import time
from pathlib import Path

import typer

from quor.cli.repo_path import resolve_repo_root
from quor.cli.repo_progress import print_build_summary, progress_echo
from quor.pipeline.repo_profile.intel import ensure_repo_intelligence
from quor.pipeline.repo_profile.symbols_render import render_json, render_markdown
from quor.tracking.db import REPO_SYMBOLS_FILTER_LABEL, get_tracking_db, track_invocation_safe


def symbols_command(
    path: Path | None = typer.Option(
        None, "--path", help="Repository root to index (default: current directory)."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Output the symbol index as JSON instead of Markdown."
    ),
    rebuild: bool = typer.Option(
        False, "--rebuild", help="Force a full rebuild of repository intelligence, bypassing the cache."
    ),
) -> None:
    """Generate a deterministic repository-wide symbol index."""
    root = resolve_repo_root(path)
    t0 = time.monotonic()

    intel = ensure_repo_intelligence(root, rebuild=rebuild, echo=progress_echo)
    index = intel.symbols
    output = render_json(index) if json_output else render_markdown(index)

    detail = f"{index.total_symbols} symbol{'s' if index.total_symbols != 1 else ''}"
    print_build_summary(intel, detail, elapsed_seconds=time.monotonic() - t0)

    typer.echo(output)
    # Synthesis, not compression — see quor/tracking/db.py's
    # track_invocation_safe() docstring for why original/filtered default
    # equal and this call is fail-open (QB-107).
    track_invocation_safe(
        get_tracking_db,
        command=f"Symbols: {root.as_posix()}",
        original=output,
        filter_name=REPO_SYMBOLS_FILTER_LABEL,
        t0=t0,
        close_after=True,
    )
