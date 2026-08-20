"""Heuristic content-type detection.

Classifies raw command output into a ContentType so stages can use
can_handle(content, content_type) to opt in or out of processing, and so
FilterRegistry.find() can route MCP's command-less compress_context payloads
straight to a specialized filter (see registry.py's own docstring).

Detection is intentionally cheap and, for every type below, deterministic —
a cheap shape/prefix pre-check (never the actual decision) gates a real
parse or an exact structural marker, the same convention QB-109 established
for `diff`: no guessed heuristic ever drives filter selection.
  - No ML, no external calls, no heavy imports.
  - False positives (wrong type) degrade compression but never lose data.
  - Checked in specificity order: JSON → TOML → diff → pytest → mypy →
    traceback → ANSI-heavy → text. pytest/mypy are checked before traceback
    because a pytest failure section (even under --tb=native) still prints
    a literal "Traceback (most recent call last):" line inside it — pytest's
    own FAILURES/short-test-summary banners must win so the pytest filter
    (which already handles that classic-traceback shape, see pytest.toml's
    QB-060 tests) isn't shadowed by the generic traceback catch-all.

Content types map to filter categories in the built-in filter registry:
  json        →  structured data (cat-json)
  toml        →  structured data (cat-toml)
  diff        →  git diff output
  pytest      →  pytest failure/summary output
  mypy        →  mypy error-summary output
  traceback   →  Python exception output
  ansi        →  heavy terminal output (progress bars, CI runners)
  text        →  default fallback for everything else
"""

from __future__ import annotations

import re
import tomllib
from enum import StrEnum


class ContentType(StrEnum):
    JSON = "json"
    TOML = "toml"
    DIFF = "diff"
    PYTEST = "pytest"
    MYPY = "mypy"
    TRACEBACK = "traceback"
    ANSI_HEAVY = "ansi"
    PLAIN_TEXT = "text"


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_DIFF_HEADER_RE = re.compile(r"^(diff --git |--- a/|\+\+\+ b/|@@ )", re.MULTILINE)
_PYTEST_BANNER_RE = re.compile(
    r"^=+\s*(FAILURES|short test summary info)\s*=+", re.MULTILINE
)
_MYPY_SUMMARY_RE = re.compile(r"^Found \d+ errors? in \d+ files?\b", re.MULTILINE)
# Cheap pre-check only, mirroring JSON's '{'/'[' prefix trick: does the first
# non-blank, non-comment line look like a TOML table header or key = value
# assignment? Skips a wasted tomllib.loads() attempt on obviously-non-TOML
# content (prose, log lines, stack traces) without itself deciding anything —
# the actual TOML/not-TOML determination is always the full parse below.
_TOML_LIKELY_RE = re.compile(r"^[ \t]*(\[|[A-Za-z0-9_-]+\s*=)")


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

    # TOML: real parse via stdlib tomllib, gated by a cheap shape pre-check
    for line in content.splitlines():
        line_stripped = line.strip()
        if not line_stripped or line_stripped.startswith("#"):
            continue
        if _TOML_LIKELY_RE.match(line):
            try:
                tomllib.loads(content)
                return ContentType.TOML
            except (tomllib.TOMLDecodeError, ValueError):
                pass
        break

    # Diff: has standard unified-diff markers
    if _DIFF_HEADER_RE.search(content):
        return ContentType.DIFF

    # pytest: exact FAILURES / short-test-summary-info banners (see pytest.toml)
    if _PYTEST_BANNER_RE.search(content):
        return ContentType.PYTEST

    # mypy: exact "Found N errors in M files" summary line
    if _MYPY_SUMMARY_RE.search(content):
        return ContentType.MYPY

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
