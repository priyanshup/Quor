"""YAML analyzer for the structured-data summarization framework (QB-040).

Public API: `analyze_yaml(source: str) -> list[CollapseRange]`.

Uses PyYAML (optional dependency, `quor[yaml]`) — `yaml.compose()` rather
than `yaml.safe_load()`, because `compose()` returns a `Node` tree where
every node (including each item of a block/flow sequence) carries its own
`start_mark`/`end_mark` with a `.line` attribute. This is real, well-tested
position tracking from the parser itself, not a hand-rolled scan — the same
reason this module needs no bracket/offset bookkeeping the way `json_fmt.py`
does. Node shape (for the homogeneity check) is read directly from each
node's resolved `tag` (e.g. `tag:yaml.org,2002:int`) and, for mappings, its
key set — never a fully-resolved Python value — so no second parse pass
(`yaml.safe_load()`) is needed at all.

Fail-open contract: mirrors `json_fmt.py`/`quor/pipeline/ast_summarize/
python.py` for a genuine parse failure (`yaml.YAMLError` propagates,
uncaught). A *missing* PyYAML installation is a different, non-exceptional
case — mirrors `quor/pipeline/ast_summarize/go.py`'s identical "optional
dependency absent" contract: caught here, warns, returns `[]` (no
compression for this file), never raises.
"""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

from quor.pipeline.structured_data.collapse import (
    DEFAULT_KEEP_HEAD,
    DEFAULT_MAX_ITEMS,
    CollapseRange,
    is_homogeneous_shapes,
    summary_line,
)

if TYPE_CHECKING:
    from yaml.error import Mark
    from yaml.nodes import Node

_TAG_SHAPES = {
    "tag:yaml.org,2002:bool": "bool",
    "tag:yaml.org,2002:int": "number",
    "tag:yaml.org,2002:float": "number",
    "tag:yaml.org,2002:null": "null",
    "tag:yaml.org,2002:str": "string",
}


def analyze_yaml(source: str) -> list[CollapseRange]:
    """Return collapse ranges for every homogeneous YAML sequence in
    `source` longer than `DEFAULT_MAX_ITEMS` elements, at any nesting depth.

    Returns `[]` (with an actionable warning) if PyYAML is not installed.
    Otherwise raises `yaml.YAMLError` on malformed YAML — not caught here,
    see module docstring "Fail-open contract".
    """
    try:
        import yaml
    except ImportError:
        warnings.warn(
            "[quor] PyYAML is not installed; install quor[yaml] to enable "
            "YAML structural summarization (falling back to no compression "
            "for this file)",
            stacklevel=2,
        )
        return []

    root = yaml.compose(source)
    if root is None:
        return []

    ranges: list[CollapseRange] = []
    lines = source.split("\n")
    _walk_node(root, lines, ranges)
    return ranges


def _start_line(lines: list[str], mark: Mark) -> int:
    """1-indexed line number of a node's `start_mark`.

    No rollback adjustment needed (unlike `_end_line` below): a start_mark
    always denotes the real line a node begins on, even when only
    indentation/a `-` indicator precedes it on that line.
    """
    return min(int(mark.line), len(lines) - 1) + 1


def _end_line(lines: list[str], mark: Mark) -> int:
    """1-indexed *content* line number of a node's `end_mark`.

    PyYAML's `end_mark` for a block construct routinely points at the start
    of whatever comes next — the following sibling's own indentation/`-`
    indicator, or column 0 of the next line entirely — rather than at the
    last character actually consumed (empirically confirmed: a block
    mapping's `end_mark.column` lands right before the next list item's `-`,
    not at end-of-line). Detected structurally, not guessed: whenever
    everything on the mark's own line *before* its column is whitespace,
    the mark is pointing at "the start of the next thing," so the true last
    content line is the nearest preceding non-blank line. A flow scalar's
    end_mark (e.g. right after a quoted string) always has real content
    before it on its own line, so this never fires for those.
    """
    line0 = min(int(mark.line), len(lines) - 1)
    column = int(mark.column)
    if line0 > 0 and lines[line0][:column].strip() == "":
        prev = line0 - 1
        while prev > 0 and not lines[prev].strip():
            prev -= 1
        if lines[prev].strip():
            line0 = prev
    return line0 + 1


def _node_shape(node: Node) -> tuple[str, frozenset[str] | None]:
    """Shape key for a Node — same (type_name, key_set_or_None) contract as
    `collapse._shape_key`, derived from the node itself (tag / mapping
    keys), never a resolved Python value."""
    import yaml

    if isinstance(node, yaml.MappingNode):
        keys = frozenset(
            k.value for k, _ in node.value if isinstance(k, yaml.ScalarNode)
        )
        return ("object", keys)
    if isinstance(node, yaml.SequenceNode):
        return ("array", None)
    return (_TAG_SHAPES.get(node.tag, "other"), None)


def _walk_node(node: Node, lines: list[str], ranges: list[CollapseRange]) -> None:
    import yaml

    if isinstance(node, yaml.SequenceNode):
        _walk_sequence(node, lines, ranges)
    elif isinstance(node, yaml.MappingNode):
        for _key_node, value_node in node.value:
            _walk_node(value_node, lines, ranges)
    # ScalarNode: nothing to recurse into.


def _walk_sequence(node: Node, lines: list[str], ranges: list[CollapseRange]) -> None:
    children = list(node.value)

    # Collect each child's own sub-ranges separately (discarded later if
    # that child ends up omitted by a collapse) — mirrors json_fmt.py's
    # identical per-element sub_ranges bookkeeping.
    per_child_ranges: list[list[CollapseRange]] = []
    for child in children:
        sub: list[CollapseRange] = []
        _walk_node(child, lines, sub)
        per_child_ranges.append(sub)

    if len(children) > DEFAULT_MAX_ITEMS:
        shapes = [_node_shape(c) for c in children]
        if is_homogeneous_shapes(shapes) and _try_collapse(
            node, lines, children, per_child_ranges, ranges
        ):
            return

    for sub in per_child_ranges:
        ranges.extend(sub)


def _try_collapse(
    node: Node,
    lines: list[str],
    children: list[Node],
    per_child_ranges: list[list[CollapseRange]],
    ranges: list[CollapseRange],
) -> bool:
    keep = children[:DEFAULT_KEEP_HEAD]
    omitted = children[DEFAULT_KEEP_HEAD:]

    keep_end_line = _end_line(lines, keep[-1].end_mark)
    omitted_start_line = _start_line(lines, omitted[0].start_mark)
    omitted_end_line = _end_line(lines, omitted[-1].end_mark)
    seq_end_line = _end_line(lines, node.end_mark)

    # Same "when uncertain, keep it" guards as json_fmt.py's _maybe_collapse:
    # never collapse across a line a kept element shares with the omitted
    # run. A flow sequence (`[a, b, c]`) has a closing ']' that can share a
    # line with the last omitted element — same bracket-eating risk as
    # JSON's, guarded the same way (strict inequality). A block sequence
    # (`- item`) has no such trailing token — its own end coincides exactly
    # with the last element's last line, so equality is the expected,
    # unproblematic case there.
    if omitted_start_line <= keep_end_line:
        return False
    if node.flow_style:
        if seq_end_line <= omitted_end_line:
            return False
    elif seq_end_line < omitted_end_line:
        return False

    for sub in per_child_ranges[:DEFAULT_KEEP_HEAD]:
        ranges.extend(sub)

    ranges.append(
        CollapseRange(
            compress_start=omitted_start_line,
            compress_end=omitted_end_line,
            summary=summary_line(len(omitted), len(children), unit="item"),
        )
    )
    return True
