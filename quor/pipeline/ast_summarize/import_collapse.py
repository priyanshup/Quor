"""Shared, language-agnostic import-block collapsing (QB-096).

Every `collapse_imports_*()` in a per-language module (`python.py`, `java.py`,
`javascript.py`, `typescript.py`) does exactly two things itself: parse the
source with that language's own parser, and walk the tree into a flat,
source-ordered `list[ImportStatement]` (see `import_model.py`). Everything
else — deciding which statements form one collapsible "run," rendering a
run's replacement text, and deciding whether collapsing is actually cheaper —
happens once, here, shared by every language. This mirrors
`_treesitter_utils.py`'s existing "language-agnostic logic lives in one
shared module, language-specific tree-walking stays in its own module" split,
one level up: that module is shared across the JS-family grammars only, this
one is shared across every language QB-096 supports, Python's stdlib `ast`
included.

**Run detection.** Two consecutive `ImportStatement`s belong to the same run
if every line strictly between them is blank or a single-line comment (using
that language's own `comment_prefix`) — real code in the gap always breaks
the run, never merged through. This is a textual check, not a parse-tree one:
it doesn't need to understand a language's comment grammar beyond "starts
with this prefix after stripping whitespace," and deliberately does not try
to recognize block comments (`/* ... */`) — a block-comment gap breaks the
run rather than risk mis-parsing one, the same "when uncertain, don't merge"
conservatism `path_prefix_fold`/`collapse_unchanged_context` already apply
elsewhere in this codebase.

**Collapse decision.** Purely token-cost-driven (QB-055's principle, already
reused by `collapse_unchanged_context`/`path_prefix_fold`): a run collapses
only when its rendered replacement is estimated strictly cheaper than the
run's own original text. No separate "minimum import count" threshold exists
— a short run's replacement (header + heading + bullets) is essentially
never cheaper than 1-2 short `import` lines, so "leave small blocks
unchanged" falls out of the cost math on its own, the same way it does for
the two stages above.

**Rendering.** A bare, headingless import (`module is None` — only Python's
`import x` shape produces this) is classified into one of two fixed buckets
("Standard library" / "Third-party") via the caller-supplied `stdlib_check`;
every other import groups under its own `module` string as a heading,
including relative and wildcard forms, verbatim. See `render_import_block()`
for the exact layout. Both bucket labels are meaningful only when
`stdlib_check` is provided (Python only, today) — a language with no bare-
import concept (Java, JS/TS) never produces a `module is None` statement, so
these buckets are simply never populated for it.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from quor.pipeline.ast_summarize.import_model import (
    ImportBlockReplacement,
    ImportedName,
    ImportStatement,
)

_MAX_NAMES_PER_GROUP = 10
"""Cap on how many names one heading (a bucket or a module group) lists
before switching to "(+N more)". Chosen as a round number generous enough to
give real signal on the common case (a handful to a dozen imports from one
module) while still bounding the pathological case (a single module with
dozens of names, or a stdlib-heavy file with 50+ bare imports) — the same
"cap the display, don't hide the count" idea `structured_data_summarize`
already uses for large homogeneous arrays, just with a larger cap since an
import name is a much smaller unit than a JSON/YAML element."""


def _line_tokens(text: str) -> int:
    """Estimate `text`'s token cost: ceil(len(text) / 4), the same
    char-count approximation `max_tokens`/`collapse_unchanged_context`/
    `path_prefix_fold` already use. `text` may itself span multiple lines
    (contain embedded `\\n`) — the estimate is still a fair whole-block cost,
    since it's just counting characters regardless of where the newlines
    fall."""
    return max(1, math.ceil(len(text) / 4))


def _is_blank_or_comment(line: str, comment_prefix: str) -> bool:
    stripped = line.strip()
    return stripped == "" or stripped.startswith(comment_prefix)


def _gap_is_blank_or_comment(
    source_lines: list[str], gap_start: int, gap_end: int, comment_prefix: str
) -> bool:
    """True if every 1-indexed line in `[gap_start, gap_end]` (inclusive) is
    blank or a single-line comment. An out-of-range line (should not happen
    for a real gap derived from the same source, but checked defensively)
    counts as a run-breaker, never a run-joiner."""
    for line_number in range(gap_start, gap_end + 1):
        idx = line_number - 1
        if idx < 0 or idx >= len(source_lines) or not _is_blank_or_comment(source_lines[idx], comment_prefix):
            return False
    return True


def group_import_runs(
    statements: list[ImportStatement], source_lines: list[str], comment_prefix: str
) -> list[list[ImportStatement]]:
    """Group `statements` (already in source order — every caller's own
    per-language extraction guarantees this) into maximal runs, splitting
    wherever the gap between two consecutive statements contains anything
    other than blank/comment lines."""
    if not statements:
        return []

    runs: list[list[ImportStatement]] = [[statements[0]]]
    for stmt in statements[1:]:
        prev = runs[-1][-1]
        gap_start = prev.end_line + 1
        gap_end = stmt.line - 1
        if gap_start > gap_end or _gap_is_blank_or_comment(source_lines, gap_start, gap_end, comment_prefix):
            runs[-1].append(stmt)
        else:
            runs.append([stmt])
    return runs


def _format_name(name: ImportedName) -> str:
    return f"{name.name} as {name.alias}" if name.alias else name.name


def _format_group(heading: str, names: list[str]) -> str:
    shown = names[:_MAX_NAMES_PER_GROUP]
    lines = [f"{heading}:"]
    lines.extend(f"- {n}" for n in shown)
    remaining = len(names) - len(shown)
    if remaining > 0:
        lines.append(f"(+{remaining} more)")
    return "\n".join(lines)


_MAX_INLINE_MODULE_NAMES = 3
"""A per-module group (never the "Standard library"/"Third-party" buckets,
which always stay bulleted regardless of size — see `_format_module_group()`)
with this many names or fewer renders as one inline `"module: a, b, c"` line
instead of a heading followed by its own bulleted list. A heading plus one to
three bullets is visually heavier than the single source line(s) it
replaces, and a run with several small module groups back to back turns into
a wall of tiny headings — confirmed by inspecting real benchmark output, not
guessed. Inline keeps each such module reference to the one line it deserves
(the same treatment a wildcard/side-effect import already gets). A module
with more names than this still benefits from a vertical, scannable list, so
the heading+bulleted form is kept starting at 4."""


def _format_module_group(module: str, names: list[str]) -> str:
    """Render one `from`-style module's group — inline for a small group
    (see `_MAX_INLINE_MODULE_NAMES`), heading + bulleted list otherwise.
    Never used for the stdlib/third-party buckets (`_format_group()`
    directly, always bulleted) — see this function's own constant docstring
    for why the two are deliberately different."""
    if len(names) <= _MAX_INLINE_MODULE_NAMES:
        return f"{module}: {', '.join(names)}"
    return _format_group(module, names)


def _count_entries(run: list[ImportStatement]) -> int:
    """Total individual bindings a run represents — each bare-import name,
    each `from`-import name, and each wildcard/side-effect statement counts
    as exactly one entry. The single source of truth for the "Imports (N)"
    header count and for `collapse_import_runs()`'s minimum-size floor
    below, so the two can never drift apart."""
    total = 0
    for stmt in run:
        if stmt.module is None:
            total += len(stmt.names)
        elif stmt.is_wildcard or not stmt.names:
            total += 1
        else:
            total += len(stmt.names)
    return total


def render_import_block(run: list[ImportStatement], stdlib_check: Callable[[str], bool] | None = None) -> str:
    """Render one run's replacement text. See module docstring for the
    grouping rules; this function only formats an already-grouped run, it
    makes no cost decision of its own (that's `collapse_import_runs()`)."""
    total = _count_entries(run)
    stdlib_names: list[str] = []
    thirdparty_names: list[str] = []
    module_names: dict[str, list[str]] = {}
    module_order: list[str] = []
    plain_lines: list[str] = []

    for stmt in run:
        if stmt.module is None:
            for name in stmt.names:
                label = _format_name(name)
                top_level = name.name.split(".")[0]
                if stdlib_check is not None and stdlib_check(top_level):
                    stdlib_names.append(label)
                else:
                    thirdparty_names.append(label)
            continue

        if stmt.module not in module_order:
            module_order.append(stmt.module)

        if stmt.is_wildcard:
            module_names.setdefault(stmt.module, []).append("*")
            continue

        if not stmt.names:
            plain_lines.append(stmt.module)
            continue

        bucket = module_names.setdefault(stmt.module, [])
        for name in stmt.names:
            bucket.append(_format_name(name))

    sections: list[str] = []
    if stdlib_names:
        sections.append(_format_group("Standard library", stdlib_names))
    if thirdparty_names:
        sections.append(_format_group("Third-party", thirdparty_names))
    for module in module_order:
        names = module_names.get(module)
        if names == ["*"]:
            sections.append(f"{module}.*")
        elif names:
            sections.append(_format_module_group(module, names))
    sections.extend(plain_lines)

    return "\n\n".join([f"Imports ({total})", *sections])


def _run_source_text(run: list[ImportStatement], source_lines: list[str]) -> str:
    start, end = run[0].line, run[-1].end_line
    return "\n".join(source_lines[start - 1 : end])


_MIN_ENTRIES_TO_COLLAPSE = 2
"""A run representing fewer than this many individual bindings never
collapses, full stop — the token-cost gate below is skipped entirely, not
just usually declined. This is a floor derived directly from the task's own
explicit requirement ("if there are only a few imports, leave them
unchanged"), not a guessed classification threshold: a single import (e.g.
one verbose JS named import, `import { foo } from "bar";`) can occasionally
be a handful of tokens *cheaper* rendered as `bar:\\n- foo` purely because of
`{ }`/`from`/quote/semicolon overhead in the original syntax — technically
passing the cost gate, but "collapsing one import into a differently-shaped
one import" is not the behavior "a few imports" was ever meant to produce.
Two is the floor, not three, because the task's own worked examples already
collapse a two-statement run (`import {A,B,C} from "./foo"; import {D,E}
from "./bar";` — QB-096's own TypeScript/JavaScript example)."""


def collapse_import_runs(
    statements: list[ImportStatement],
    source_lines: list[str],
    comment_prefix: str,
    stdlib_check: Callable[[str], bool] | None = None,
) -> list[ImportBlockReplacement]:
    """Group `statements` into runs and return a replacement for every run
    whose rendered form is estimated strictly cheaper than its original
    text — see module docstring for the run-detection and cost-gate rules.
    A run that doesn't pass the cost gate (or the `_MIN_ENTRIES_TO_COLLAPSE`
    floor above) contributes no replacement, so its original lines are left
    completely untouched by the caller."""
    replacements: list[ImportBlockReplacement] = []
    for run in group_import_runs(statements, source_lines, comment_prefix):
        if _count_entries(run) < _MIN_ENTRIES_TO_COLLAPSE:
            continue
        original_cost = _line_tokens(_run_source_text(run, source_lines))
        replacement_text = render_import_block(run, stdlib_check)
        replacement_cost = _line_tokens(replacement_text)
        if replacement_cost >= original_cost:
            continue
        replacements.append(
            ImportBlockReplacement(start_line=run[0].line, end_line=run[-1].end_line, text=replacement_text)
        )
    return replacements
