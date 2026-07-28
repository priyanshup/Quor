"""RepoSymbolIndex -> deterministic output (Markdown default, JSON
optional) — the `quor symbols` counterpart to `render.py`'s
`render_markdown()`/`render_json()` for `quor map`'s `RepoProfile`. Fixed
template only — no invented prose, no content that isn't already a field
on `RepoSymbolIndex`/`Symbol`.
"""

from __future__ import annotations

import dataclasses

import orjson

from quor.pipeline.repo_profile.symbols_model import RepoSymbolIndex

_KIND_LABELS: dict[str, str] = {
    "class": "class",
    "interface": "interface",
    "struct": "struct",
    "trait": "trait",
    "enum": "enum",
    "function": "function",
    "method": "method",
}


def render_markdown(index: RepoSymbolIndex) -> str:
    lines: list[str] = ["# Repository Symbols", "", f"Root: {index.root}", ""]

    if not index.files:
        lines.append("(no symbols found)")
        lines.append("")
    for file_symbols in index.files:
        lines.append(f"## {file_symbols.path} ({file_symbols.language})")
        lines.append("")
        for symbol in file_symbols.symbols:
            kind = _KIND_LABELS.get(symbol.kind, symbol.kind)
            visibility = "public" if symbol.is_public else "private"
            tags = f"[{visibility}]"
            if symbol.is_entry_point:
                tags += " [entry-point]"
            lines.append(f"- {kind} {symbol.name} (line {symbol.line}) {tags}")
        lines.append("")

    lines.append("## Statistics")
    lines.append("")
    lines.append(f"- Files with symbols: {len(index.files)}")
    lines.append(f"- Total symbols: {index.total_symbols}")
    lines.append(f"- Languages covered: {', '.join(index.languages_covered) or 'none'}")
    lines.append("")

    if index.notes:
        lines.append("## Notes")
        lines.append("")
        lines.extend(f"- {note}" for note in index.notes)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_json(index: RepoSymbolIndex) -> str:
    """JSON output mode (`--json`) — a secondary interface, not the primary
    one, mirroring `quor map`'s own `render_json()` convention."""
    return orjson.dumps(dataclasses.asdict(index), option=orjson.OPT_INDENT_2).decode()
