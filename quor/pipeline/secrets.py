"""Secret detection (PA-F07): warn if a known secret/token pattern appears
in output Quor is about to write to stdout.

Dispatcher-level only, like tee.py — this module never touches ContentMask,
Pipeline, or any StageHandler. Detection only: it never redacts, modifies, or
removes anything. Hook output (stdout) is always unaffected by what this
module finds; the caller is responsible for turning a detection into a
stderr warning (matching every other dispatcher-level concern's fail-open
convention — see quor/adapters/dispatcher.py's _track()/_apply_tee()).

Patterns are deliberately a small, high-confidence set of well-known token
shapes (GitHub, AWS, Slack, private key headers) rather than generic
entropy-based heuristics: per
docs/archive/product-discovery/competitive-research.md's own caution for
this category ("Medium FP"), a broader heuristic risks false positives on
ordinary high-entropy content (hashes, session IDs) that isn't a secret at
all. These are internal, hardcoded patterns (not user-configurable), so
the stdlib `re` module is used directly, per CLAUDE.md's pattern-matching
convention.
"""

from __future__ import annotations

import re

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("GitHub token", re.compile(r"\bgh[oprsu]_[A-Za-z0-9]{36,}\b")),
    ("AWS access key ID", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    (
        "private key",
        re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
    ),
)


def scan_for_secrets(content: str) -> list[str]:
    """Return the human-readable names of any secret patterns found in
    `content`. Pure and read-only — never modifies `content` and never
    raises on the input itself (only a `_SECRET_PATTERNS` bug could raise,
    same as any other internal hardcoded pattern in this codebase)."""
    return [name for name, pattern in _SECRET_PATTERNS if pattern.search(content)]
