"""PDF structure extraction (QB-007E3).

Converts a PDF file into Markdown-shaped plain text — headings inferred
from font size, merged paragraphs, bullet/numbered lists, GitHub-style
tables, and monospace-font blocks as fenced code — using the optional
`pdfplumber` dependency (`quor[documents]`). Same philosophy as
`quor/pipeline/extract/docx.py` (QB-007E2): simple, deterministic
heuristics, no document-understanding ML, no OCR.

PDF has no structural document model the way DOCX does (no paragraph
styles, no body element tree) — everything here is inferred from geometry:
character position (`top`/`bottom`/`x0`) and font metadata (`size`,
`fontname`), which is all `pdfplumber` exposes. Where DOCX could trust an
author's explicit "Heading 2" style, this module infers headings purely
from *relative* font size (larger than the document's own body-text size),
and infers paragraph boundaries purely from vertical gaps between lines.
These are heuristics, not ground truth — see the module's limitations in
`backlog.md`'s QB-007E3 entry.

`extract_pdf()` is fail-open on its own, exactly like `extract_docx()`:
every failure mode (missing dependency, corrupt file, encrypted file,
unreadable/missing file, unexpected parser exception) returns `None` and
never raises, independent of whatever calls it.

Deliberately excluded by construction: document metadata (`pdf.metadata` —
author, creation date, producer, ...) is never read at all. Images are
never inspected, described, or OCR'd — only text and tables pdfplumber
already extracts as text are used.
"""

from __future__ import annotations

import re
import warnings
from collections import Counter
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from pathlib import Path

    from pdfplumber.pdf import PDF
    from pdfplumber.table import Table

# pdfplumber has no typed model for the dicts extract_text_lines()/chars
# return (they're plain heterogeneous dicts: str/float/list values) — this
# alias documents that shape at every call site below rather than repeating
# `dict[str, Any]` with no explanation.
_Line = dict[str, Any]

# Bullet markers this module recognizes at the start of a line, followed by
# a space and real content. Covers common Unicode bullet glyphs plus the
# plain-ASCII markers ("-", "*", "+") many PDFs (especially ones exported
# from plain-text/code sources) use instead. `-` is placed last in the
# class so it never needs escaping as a range operator.
_BULLET_RE = re.compile(r"^[•◦▪‣●○·*+-]\s+(\S.*)$")

# "1. item" or "1) item" — the *number* is part of the PDF's own rendered
# text (unlike DOCX, where Word's auto-numbering isn't literal paragraph
# text at all), so it's reused verbatim rather than resynthesized. The
# delimiter itself ("." vs ")") is not preserved — like bullets normalizing
# to "-" regardless of the source glyph, output always uses ". " for
# consistency, even when the source used ")".
_NUMBERED_RE = re.compile(r"^(\d+)[.)]\s+(\S.*)$")

# Font-name substring heuristic for monospace/code detection. PDF font
# names are frequently subset-mangled (e.g. "ABCDEF+CourierNewPSMT"), so
# this is a case-insensitive substring match, not an exact one — mirrors
# `quor/pipeline/extract/docx.py`'s `_MONOSPACE_FONTS` heuristic, adapted
# for how PDF font names actually look.
_MONOSPACE_FONT_KEYWORDS = ("courier", "consolas", "menlo", "monaco", "mono", "cascadia")

# A line continues the previous block (same paragraph/list item/code run)
# only if the vertical gap since the previous line is small relative to
# font size — calibrated against real single-spaced-vs-section-break gaps
# (~0.3x size within a paragraph, ~0.9x+ size between sections/headings in
# manual measurement against generated fixtures). 0.6 sits cleanly between
# the two without being tuned to one specific document.
_PARAGRAPH_GAP_FACTOR = 0.6

_MAX_HEADING_LEVEL = 6


def extract_pdf(file_path: Path) -> str | None:
    """Return Markdown-shaped plain text for a PDF file, or `None`.

    `None` covers two distinct cases, both fail-open and both logged with a
    warning rather than raised:
      - `pdfplumber` is not installed — a specific, actionable message
        (install `quor[documents]`), checked first so it's never masked by
        the generic one below.
      - anything else went wrong opening or rendering the file — corrupt
        file, encrypted file (opened without a password), unreadable or
        missing file, or any other unexpected exception from pdfplumber/
        pdfminer. This function never raises.
    """
    try:
        import pdfplumber
    except ImportError:
        warnings.warn(
            "[quor] pdfplumber is not installed; install quor[documents] "
            "to enable PDF extraction",
            stacklevel=2,
        )
        return None

    try:
        with pdfplumber.open(str(file_path)) as pdf:
            return _render(pdf)
    except Exception as exc:  # noqa: BLE001 — fail-open: this handler must never raise on its own
        warnings.warn(f"[quor] document extraction error: {exc}", stacklevel=2)
        return None


def _render(pdf: PDF) -> str:
    """Render every page of `pdf` to Markdown-shaped text, in document
    order. Two passes: first collect every non-table, non-code line's font
    size across the whole document to establish a document-wide "body
    text" baseline and heading-level ranking (so heading levels are
    consistent across pages, not re-derived per page); second, walk each
    page's lines/tables in position order and render them using that
    baseline. Code lines are excluded from the baseline sample for the
    same reason table lines are: a code block's font is frequently a
    *different* size than body prose, and letting it into the body/heading
    size analysis can corrupt heading detection for the rest of the
    document (a code-sized outlier can otherwise get mistaken for "the"
    body size, turning genuinely-larger body text into a false heading).
    """
    pages_items: list[list[tuple[float, str, object]]] = []
    all_sizes: list[float] = []

    for page in pdf.pages:
        tables = page.find_tables()
        table_bboxes = [t.bbox for t in tables]
        lines = [
            line
            for line in page.extract_text_lines(strip=True)
            if line["text"].strip() and not _in_any_bbox(line, table_bboxes)
        ]
        for line in lines:
            if not _is_code_line(line):
                all_sizes.append(_line_size(line))

        items: list[tuple[float, str, object]] = [(line["top"], "line", line) for line in lines]
        items += [(t.bbox[1], "table", t) for t in tables]
        items.sort(key=lambda entry: entry[0])
        pages_items.append(items)

    body_size = _body_size(all_sizes)
    heading_levels = _heading_levels(all_sizes, body_size)

    blocks: list[str] = []
    renderer = _BlockRenderer(blocks)
    for items in pages_items:
        for _top, kind, data in items:
            if kind == "table":
                renderer.flush()
                rendered = _render_table(cast("Table", data))
                if rendered:
                    blocks.append(rendered)
                continue
            renderer.add_line(cast(_Line, data), body_size, heading_levels)
    renderer.flush()

    return "\n\n".join(blocks)


class _BlockRenderer:
    """Accumulates classified lines into paragraph/list/code blocks,
    merging wrapped continuation lines and flushing a finished block only
    when the classification changes or the vertical gap since the last
    line is too large to be the same block. Headings never accumulate —
    each is flushed and emitted immediately, standalone."""

    def __init__(self, blocks: list[str]) -> None:
        self._blocks = blocks
        self._kind: str | None = None  # "paragraph" | "bullet" | "numbered" | "code"
        self._marker = ""
        self._parts: list[str] = []
        self._last_bottom: float | None = None
        self._last_size: float | None = None
        self._code_base_x0: float | None = None

    def add_line(
        self, line: _Line, body_size: float, heading_levels: dict[float, int]
    ) -> None:
        text = line["text"].strip()
        size = _line_size(line)

        level = heading_levels.get(round(size, 1))
        if level is not None:
            self.flush()
            self._blocks.append(f"{'#' * level} {_flatten(text)}")
            self._reset_position()
            return

        gap_ok = self._gap_is_small(line)

        bullet_match = _BULLET_RE.match(text)
        numbered_match = _NUMBERED_RE.match(text)

        if bullet_match:
            self.flush()
            self._start("bullet", "- ", bullet_match.group(1))
        elif numbered_match:
            self.flush()
            self._start("numbered", f"{numbered_match.group(1)}. ", numbered_match.group(2))
        elif _is_code_line(line):
            if self._kind == "code" and gap_ok:
                self._parts.append(_reconstruct_indent(line, self._code_base_x0))
            else:
                self.flush()
                self._code_base_x0 = line["x0"]
                self._start("code", "", _reconstruct_indent(line, self._code_base_x0))
        elif self._kind in ("paragraph", "bullet", "numbered") and gap_ok:
            self._parts.append(text)
        else:
            self.flush()
            self._start("paragraph", "", text)

        self._last_bottom = line["bottom"]
        self._last_size = size

    def flush(self) -> None:
        if self._kind is None or not self._parts:
            self._reset()
            return
        if self._kind == "code":
            self._blocks.append("```\n" + "\n".join(self._parts) + "\n```")
        else:
            self._blocks.append(self._marker + _flatten(" ".join(self._parts)))
        self._reset()

    def _start(self, kind: str, marker: str, first_part: str) -> None:
        self._kind = kind
        self._marker = marker
        self._parts = [first_part]

    def _reset(self) -> None:
        self._kind = None
        self._marker = ""
        self._parts = []
        self._code_base_x0 = None

    def _reset_position(self) -> None:
        self._last_bottom = None
        self._last_size = None

    def _gap_is_small(self, line: _Line) -> bool:
        if self._last_bottom is None or self._last_size is None:
            return False
        top: float = line["top"]
        gap = top - self._last_bottom
        return bool(gap <= self._last_size * _PARAGRAPH_GAP_FACTOR)


def _in_any_bbox(line: _Line, bboxes: list[tuple[float, float, float, float]]) -> bool:
    """True if `line`'s vertical center falls inside any table bbox — used
    to exclude a table's own cell text from also being rendered as stray
    paragraph lines (pdfplumber's word/line extraction and its table
    extraction both see the same underlying characters)."""
    mid = (line["top"] + line["bottom"]) / 2
    return any(bbox[1] <= mid <= bbox[3] for bbox in bboxes)


def _line_size(line: _Line) -> float:
    """The line's dominant character size, weighted by each character's
    rendered width rather than a raw per-character count.

    Verified against a real generated PDF, not a hypothetical: a bullet
    dingbat pdfminer can't map to a real Unicode codepoint (no ToUnicode
    CMap for that glyph — common for `ListFlowable`-style bullets) can
    decode as *several* zero-width `(cid:N)` placeholder characters
    stacked at the same position, all at the bullet's own (often larger)
    font size. A raw character-count mode would let that phantom run
    outweigh the line's real, visible text on a short line (e.g. "•
    queued") and misclassify it — as a heading, if the bullet's size
    happens to fall into a heading tier. Weighting by rendered width
    (`x1 - x0`) instead means a zero-width run contributes nothing, so the
    line's real text — the only part actually occupying space — decides
    its size, regardless of how many placeholder characters an undecoded
    glyph expands to."""
    chars: list[_Line] = line["chars"]
    if not chars:
        return 0.0
    # A plain dict, not Counter[float]: Counter's *values* are always int
    # in its type stub (it counts hashable keys), which can't hold a
    # fractional accumulated width.
    weights: dict[float, float] = {}
    for c in chars:
        size: float = round(c["size"], 1)
        width: float = max(c["x1"] - c["x0"], 0.0)
        weights[size] = weights.get(size, 0.0) + width
    if not weights or sum(weights.values()) == 0:
        # Every character on the line is zero-width (the whole line is an
        # undecodable glyph run) — fall back to a plain count so a size is
        # still returned, rather than silently dropping this line's tier.
        return float(Counter(round(c["size"], 1) for c in chars).most_common(1)[0][0])
    return min(weights, key=lambda s: (-weights[s], s))


def _body_size(all_sizes: list[float]) -> float:
    """The document's ordinary paragraph-text size: the most common line
    size across the whole document. Ties broken toward the smaller value
    — conservative, so a genuinely large body font doesn't accidentally
    suppress real headings by claiming their size band."""
    if not all_sizes:
        return 0.0
    counts = Counter(round(s, 1) for s in all_sizes)
    top_count = max(counts.values())
    return min(size for size, count in counts.items() if count == top_count)


def _heading_levels(all_sizes: list[float], body_size: float) -> dict[float, int]:
    """Map each distinct font size larger than `body_size` to a heading
    level — the largest distinct size becomes level 1, the next level 2,
    and so on, clamped at level 6 (ATX Markdown's own limit) for any size
    ranked beyond the sixth tier rather than losing the structure
    entirely."""
    larger = sorted({round(s, 1) for s in all_sizes if s > body_size}, reverse=True)
    return {size: min(rank, _MAX_HEADING_LEVEL) for rank, size in enumerate(larger, start=1)}


def _is_code_line(line: _Line) -> bool:
    fonts = {c["fontname"].lower() for c in line["chars"]}
    if not fonts:
        return False
    return all(any(keyword in font for keyword in _MONOSPACE_FONT_KEYWORDS) for font in fonts)


def _reconstruct_indent(line: _Line, base_x0: float | None) -> str:
    """Recover leading-space indentation `extract_text_lines(strip=True)`
    already stripped, using each character's own advance width — exact
    (not approximate) for a genuinely monospace font, which is the only
    case this is ever called for. `base_x0` is the current code block's
    *own* first line's `x0` (its self-established left margin), never an
    assumed page margin — a document's real margin varies per document
    and guessing it wrong would over- or under-indent every line in the
    block. `base_x0=None` means this line is establishing that baseline
    itself, so its own indentation is zero by definition."""
    stripped: str = line["text"].strip()
    chars = line["chars"]
    if not chars or base_x0 is None:
        return stripped
    char_width: float = chars[0]["x1"] - chars[0]["x0"]
    if char_width <= 0:
        return stripped
    x0: float = line["x0"]
    indent_chars = max(round((x0 - base_x0) / char_width), 0)
    return (" " * indent_chars) + stripped


def _render_table(table: Table) -> str:
    """Render a pdfplumber `Table` as a GitHub-style Markdown table — same
    approach as `quor/pipeline/extract/docx.py::_render_table`: first row
    always treated as the header, `|` escaped, `None` cells (pdfplumber's
    representation of a genuinely empty cell) rendered as empty strings."""
    raw_rows = table.extract()
    rows: list[list[str]] = [[_escape_cell(cell) for cell in row] for row in raw_rows if row]
    if not rows:
        return ""

    col_count = len(rows[0])
    if col_count == 0:
        return ""

    lines = [
        "| " + " | ".join(rows[0]) + " |",
        "| " + " | ".join(["---"] * col_count) + " |",
    ]
    for row in rows[1:]:
        cells = (row + [""] * col_count)[:col_count]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _escape_cell(cell: str | None) -> str:
    text = (cell or "").strip()
    text = text.replace("|", "\\|")
    return text.replace("\n", "<br>")


def _flatten(text: str) -> str:
    """Collapse internal whitespace to single spaces — headings and list
    items must stay on one line to match the existing `markdown` filter's
    single-line `preserve_patterns`."""
    return " ".join(text.split())
