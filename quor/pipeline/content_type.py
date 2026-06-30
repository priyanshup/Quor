"""Heuristic content-type detection.

Classifies raw command output into a ContentType so stages can use
can_handle(content, content_type) to opt in or out of processing.

Detection is intentionally heuristic and cheap:
  - No ML, no external calls, no heavy imports.
  - False positives (wrong type) degrade compression but never lose data.
  - Checked in specificity order: JSON → diff → traceback → ANSI-heavy → text.

Content types map to filter categories in the built-in filter registry:
  json        →  structured data (never filtered by default)
  diff        →  git diff output
  traceback   →  Python exception output
  ansi        →  heavy terminal output (progress bars, CI runners)
  text        →  default fallback for everything else
"""

from __future__ import annotations

import re
from enum import StrEnum


class ContentType(StrEnum):
    JSON = "json"
    DIFF = "diff"
    TRACEBACK = "traceback"
    ANSI_HEAVY = "ansi"
    PLAIN_TEXT = "text"


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_DIFF_HEADER_RE = re.compile(r"^(diff --git |--- a/|\+\+\+ b/|@@ )", re.MULTILINE)


def detect(content: str) -> ContentType:
    """Return the most specific ContentType for the given raw output string."""
    if not content.strip():
        return ContentType.PLAIN_TEXT

    # JSON: quick prefix check to avoid trying orjson on every string
    stripped = content.strip()
    if stripped and stripped[0] in ("{", "["):
        try:
            import orjson

            orjson.loads(content.encode("utf-8", errors="replace"))
            return ContentType.JSON
        except (orjson.JSONDecodeError, ValueError):
            pass

    # Diff: has standard unified-diff markers
    if _DIFF_HEADER_RE.search(content):
        return ContentType.DIFF

    # Python traceback
    if "Traceback (most recent call last):" in content:
        return ContentType.TRACEBACK

    # ANSI-heavy: more than 20% of non-empty lines contain ANSI escape codes
    lines = content.split("\n")
    non_empty = [ln for ln in lines if ln]
    if non_empty:
        ansi_lines = sum(1 for ln in non_empty if _ANSI_RE.search(ln))
        if ansi_lines / len(non_empty) > 0.20:
            return ContentType.ANSI_HEAVY

    return ContentType.PLAIN_TEXT
