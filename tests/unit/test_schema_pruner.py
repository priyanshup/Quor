"""Unit tests for quor/mcp/schema_pruner.py (QB-120)."""

from __future__ import annotations

from quor.mcp.schema_pruner import (
    DEFAULT_MAX_PARAM_DESCRIPTION_CHARS,
    DEFAULT_MAX_TOOL_DESCRIPTION_CHARS,
    prune_tool_schema,
    prune_tool_schemas,
)

_LONG_TOOL_DOCSTRING = (
    "Use this tool whenever reading large command outputs, log streams, git\n"
    "history, or long files (exceeding 30 lines). It compresses the input\n"
    "deterministically to conserve token context window space.\n\n"
    "focal_file: repo-relative path to a file to anchor graph-distance AST\n"
    "tiering on instead of compressing raw_text — when given, raw_text is\n"
    "ignored entirely and a much longer tail of explanation follows here.\n"
)

_LONG_PARAM_DESCRIPTION = (
    "repo-relative path to a file to anchor graph-distance AST tiering on "
    "instead of compressing raw_text, with a great deal of extra explanatory "
    "detail that a model does not need before it has even picked this tool."
)


def _tool(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "compress_context",
        "description": _LONG_TOOL_DOCSTRING,
        "input_schema": {
            "type": "object",
            "title": "compress_contextArguments",
            "properties": {
                "raw_text": {"type": "string", "title": "Raw Text", "default": ""},
                "focal_file": {
                    "type": "string",
                    "title": "Focal File",
                    "default": "",
                    "description": _LONG_PARAM_DESCRIPTION,
                },
            },
            "required": ["raw_text"],
        },
    }
    base.update(overrides)
    return base


def test_long_description_is_condensed() -> None:
    pruned = prune_tool_schema(_tool())
    description = pruned["description"]
    assert isinstance(description, str)
    assert len(description) < len(_LONG_TOOL_DOCSTRING)
    assert len(description) <= DEFAULT_MAX_TOOL_DESCRIPTION_CHARS
    # First-sentence content survives condensing, not just a hard cutoff.
    assert "Use this tool whenever reading large command outputs" in description


def test_short_description_is_left_untouched() -> None:
    tool = _tool(description="Compress text.")
    pruned = prune_tool_schema(tool)
    assert pruned["description"] == "Compress text."


def test_required_param_names_survive_pruning() -> None:
    pruned = prune_tool_schema(_tool())
    schema = pruned["input_schema"]
    assert schema["required"] == ["raw_text"]
    assert set(schema["properties"]) == {"raw_text", "focal_file"}


def test_param_types_are_preserved() -> None:
    pruned = prune_tool_schema(_tool())
    props = pruned["input_schema"]["properties"]
    assert props["raw_text"]["type"] == "string"
    assert props["focal_file"]["type"] == "string"


def test_long_param_description_is_condensed() -> None:
    pruned = prune_tool_schema(_tool())
    focal_desc = pruned["input_schema"]["properties"]["focal_file"]["description"]
    assert len(focal_desc) < len(_LONG_PARAM_DESCRIPTION)
    assert len(focal_desc) <= DEFAULT_MAX_PARAM_DESCRIPTION_CHARS


def test_non_description_metadata_is_preserved() -> None:
    pruned = prune_tool_schema(_tool())
    props = pruned["input_schema"]["properties"]
    assert props["raw_text"]["default"] == ""
    assert props["raw_text"]["title"] == "Raw Text"
    assert pruned["input_schema"]["title"] == "compress_contextArguments"
    assert pruned["name"] == "compress_context"


def test_missing_description_is_not_added() -> None:
    tool = _tool()
    del tool["description"]
    pruned = prune_tool_schema(tool)
    assert "description" not in pruned


def test_output_schema_is_pruned_like_input_schema() -> None:
    tool = _tool(
        output_schema={
            "type": "object",
            "properties": {"result": {"type": "string", "description": _LONG_PARAM_DESCRIPTION}},
        }
    )
    pruned = prune_tool_schema(tool)
    out_desc = pruned["output_schema"]["properties"]["result"]["description"]
    assert len(out_desc) <= DEFAULT_MAX_PARAM_DESCRIPTION_CHARS


def test_nested_array_items_are_pruned() -> None:
    tool = _tool(
        input_schema={
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "description": _LONG_PARAM_DESCRIPTION,
                        "properties": {"name": {"type": "string"}},
                    },
                }
            },
            "required": ["items"],
        }
    )
    pruned = prune_tool_schema(tool)
    item_schema = pruned["input_schema"]["properties"]["items"]["items"]
    assert item_schema["type"] == "object"
    assert len(item_schema["description"]) <= DEFAULT_MAX_PARAM_DESCRIPTION_CHARS
    assert item_schema["properties"]["name"]["type"] == "string"


def test_prune_tool_schemas_maps_over_list() -> None:
    pruned = prune_tool_schemas([_tool(), _tool(name="get_repo_context")])
    assert [t["name"] for t in pruned] == ["compress_context", "get_repo_context"]
    assert all(len(t["description"]) <= DEFAULT_MAX_TOOL_DESCRIPTION_CHARS for t in pruned)


def test_prune_is_non_mutating() -> None:
    tool = _tool()
    original_description = tool["description"]
    prune_tool_schema(tool)
    assert tool["description"] == original_description


def test_custom_char_limits_are_respected() -> None:
    pruned = prune_tool_schema(_tool(), max_description_chars=40, max_param_description_chars=20)
    assert len(pruned["description"]) <= 40
    focal_desc = pruned["input_schema"]["properties"]["focal_file"]["description"]
    assert len(focal_desc) <= 20
