"""RepoDependencyGraph -> deterministic output (Markdown default, JSON
optional) — the `quor graph` counterpart to `symbols_render.py`'s
`render_markdown()`/`render_json()` for `quor symbols`'s `RepoSymbolIndex`.
Fixed template only — no invented prose, no content that isn't already a
field on `RepoDependencyGraph`/`Edge`.
"""

from __future__ import annotations

import dataclasses
from itertools import groupby

import orjson

from quor.pipeline.repo_profile.graph_model import Edge, RepoDependencyGraph

_KIND_VERBS: dict[str, str] = {
    "inherits": "inherits",
    "implements_interface": "implements interface",
    "implements_trait": "implements trait",
}


def render_markdown(graph: RepoDependencyGraph) -> str:
    lines: list[str] = ["# Repository Dependency Graph", "", f"Root: {graph.root}", ""]

    if not graph.edges:
        lines.append("(no relationships found)")
        lines.append("")
    for source_file, file_edges in groupby(graph.edges, key=lambda e: e.source_file):
        lines.append(f"## {source_file}")
        lines.append("")
        for edge in file_edges:
            lines.append(f"- {_format_edge(edge)}")
        lines.append("")

    lines.append("## Statistics")
    lines.append("")
    resolved_pct = round(100 * graph.resolved_edges / graph.total_edges) if graph.total_edges else 0
    lines.append(f"- Files with edges: {len({e.source_file for e in graph.edges})}")
    lines.append(f"- Total edges: {graph.total_edges}")
    lines.append(f"- Resolved edges: {graph.resolved_edges} ({resolved_pct}%)")
    lines.append(f"- Languages covered: {', '.join(graph.languages_covered) or 'none'}")
    lines.append("")

    if graph.notes:
        lines.append("## Notes")
        lines.append("")
        lines.extend(f"- {note}" for note in graph.notes)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _resolved_suffix(edge: Edge) -> str:
    if edge.target_file is None:
        return ""
    if edge.target_symbol is None:
        return f" -> `{edge.target_file}`"
    return f" -> `{edge.target_file}::{edge.target_symbol}`"


def _format_edge(edge: Edge) -> str:
    suffix = _resolved_suffix(edge)

    if edge.kind == "import":
        if edge.qualifier is not None:
            return f"imports `{edge.qualifier}` from `{edge.target_raw}`{suffix} (line {edge.line})"
        return f"imports `{edge.target_raw}`{suffix} (line {edge.line})"

    if edge.kind == "export":
        if edge.target_raw:
            return f"exports `{edge.source_symbol}` (re-exported from `{edge.target_raw}`{suffix}) (line {edge.line})"
        return f"exports `{edge.source_symbol}` (line {edge.line})"

    if edge.kind == "overrides":
        base = f"{edge.qualifier}." if edge.qualifier else ""
        return f"`{edge.source_symbol}` overrides `{base}{edge.target_raw}`{suffix} (line {edge.line})"

    if edge.kind == "calls":
        callee = f"{edge.qualifier}.{edge.target_raw}" if edge.qualifier else edge.target_raw
        return f"`{edge.source_symbol}` calls `{callee}`{suffix} (line {edge.line})"

    verb = _KIND_VERBS.get(edge.kind, edge.kind)
    return f"`{edge.source_symbol}` {verb} `{edge.target_raw}`{suffix} (line {edge.line})"


def render_json(graph: RepoDependencyGraph) -> str:
    """JSON output mode (`--json`) — a secondary interface, not the
    primary one, mirroring `quor map`/`quor symbols`'s own `render_json()`
    convention."""
    return orjson.dumps(dataclasses.asdict(graph), option=orjson.OPT_INDENT_2).decode()
