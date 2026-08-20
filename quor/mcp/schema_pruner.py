"""schema_pruner: QB-120 dynamic MCP tool-schema pruning.

Every MCP tool definition (name, description, JSON-Schema parameters) is
sent to the model up front, in `list_tools`, before any tool is actually
selected — so a verbose docstring or per-parameter description costs
prompt tokens on every single turn of a session, whether or not that tool
is ever called. This module condenses that up-front cost without touching
what a tool call itself receives: `prune_tool_schema()` operates on a
plain dict (a JSON-Schema-shaped tool definition — `name`, `description`,
`input_schema`, `output_schema`), not on live callables, so it has no way
to reach the arguments an actual invocation resolves against. Wiring that
distinction into the MCP server itself (pruning only the `list_tools`
response, never the `call_tool` path) is `quor/mcp/server.py`'s job, not
this module's.

Only `description` text is condensed, and only when it's already long
(over `max_*_chars` — a short description is left untouched rather than
being reformatted for no reason). Every structural key — `type`,
`required`, `properties`' own key names, `enum`, `default`, `items` — is
copied through unchanged, so a pruned schema can never lose a required
argument name or a parameter's type.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

DEFAULT_MAX_TOOL_DESCRIPTION_CHARS = 160
DEFAULT_MAX_PARAM_DESCRIPTION_CHARS = 80

_SCHEMA_KEYS = ("input_schema", "output_schema")
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_WHITESPACE_RUN = re.compile(r"\s+")
_SENTENCE_END = re.compile(r"[.!?]\s")


def prune_tool_schema(
    tool: Mapping[str, Any],
    *,
    max_description_chars: int = DEFAULT_MAX_TOOL_DESCRIPTION_CHARS,
    max_param_description_chars: int = DEFAULT_MAX_PARAM_DESCRIPTION_CHARS,
) -> dict[str, Any]:
    """Return a copy of `tool` with its top-level `description` and its
    `input_schema`/`output_schema` parameter descriptions condensed to a
    short summary line. `name`, `required`, and every schema `type` are
    passed through unchanged; unrecognized keys are also passed through
    unchanged, so this never drops information it doesn't understand."""
    pruned = dict(tool)
    description = pruned.get("description")
    if isinstance(description, str):
        pruned["description"] = _condense(description, max_description_chars)
    for key in _SCHEMA_KEYS:
        schema = pruned.get(key)
        if isinstance(schema, Mapping):
            pruned[key] = _prune_schema_node(schema, max_param_description_chars)
    return pruned


def prune_tool_schemas(
    tools: Iterable[Mapping[str, Any]],
    *,
    max_description_chars: int = DEFAULT_MAX_TOOL_DESCRIPTION_CHARS,
    max_param_description_chars: int = DEFAULT_MAX_PARAM_DESCRIPTION_CHARS,
) -> list[dict[str, Any]]:
    """`prune_tool_schema()` mapped over a whole `list_tools` response."""
    return [
        prune_tool_schema(
            tool,
            max_description_chars=max_description_chars,
            max_param_description_chars=max_param_description_chars,
        )
        for tool in tools
    ]


def _prune_schema_node(node: Mapping[str, Any], max_chars: int) -> dict[str, Any]:
    pruned = dict(node)
    description = pruned.get("description")
    if isinstance(description, str):
        pruned["description"] = _condense(description, max_chars)

    properties = pruned.get("properties")
    if isinstance(properties, Mapping):
        pruned["properties"] = {
            name: _prune_schema_node(prop, max_chars) if isinstance(prop, Mapping) else prop
            for name, prop in properties.items()
        }

    items = pruned.get("items")
    if isinstance(items, Mapping):
        pruned["items"] = _prune_schema_node(items, max_chars)

    return pruned


def _condense(text: str, max_chars: int) -> str:
    stripped = text.strip()
    if len(stripped) <= max_chars:
        return stripped  # already short — nothing verbose to trim

    summary = _first_sentence(stripped)
    if len(summary) > max_chars:
        summary = summary[: max_chars - 1].rstrip() + "…"
    return summary


def _first_sentence(text: str) -> str:
    """The first sentence of `text`'s first paragraph, with hand-wrapped
    source newlines collapsed to spaces first — a hard-wrapped docstring's
    first physical line is usually a mid-sentence line break, not a
    paragraph or sentence boundary."""
    first_paragraph = _PARAGRAPH_BREAK.split(text, maxsplit=1)[0]
    collapsed = _WHITESPACE_RUN.sub(" ", first_paragraph).strip()
    match = _SENTENCE_END.search(collapsed)
    return collapsed[: match.end()].strip() if match else collapsed
