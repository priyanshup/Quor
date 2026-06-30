"""Rewrite rules and command knowledge for the Quor classifier.

Rules are checked in order. First match determines the outcome.
Exclusion rules return (should_rewrite=False). Rewrite rules return the new string.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Command knowledge
# ---------------------------------------------------------------------------

# Base commands Quor installs filters for
_KNOWN_BASE_COMMANDS: frozenset[str] = frozenset(
    {
        "git",
        "pytest",
        "mypy",
        "ruff",
        "cat",
        "python",   # gated further: only -m <known> subcommand
        "python3",  # same
    }
)

# python -m <subcommand> patterns we know how to filter
_KNOWN_PYTHON_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "pytest",
        "mypy",
        "ruff",
    }
)

# Commands that always pass through unchanged as transparent prefixes —
# Quor inserts itself AFTER these, before the real command.
TRANSPARENT_PREFIXES: tuple[str, ...] = (
    "sudo",
    "doas",
    "npx",
    "yarn",
    "pnpm",
    "bunx",
    "deno",
    "time",
    "env",
    "nice",
)

# Docker/podman style transparent prefixes that consume more tokens
TRANSPARENT_MULTI_WORD_PREFIXES: tuple[tuple[str, int], ...] = (
    # (first_token, total_tokens_consumed_including_container_name)
    ("docker", 3),     # docker exec <container>
    ("podman", 3),     # podman exec <container>
)

# Pipe targets that make rewriting unsafe (output is consumed by a tool)
PIPE_INCOMPATIBLE_COMMANDS: frozenset[str] = frozenset(
    {
        "xargs",
        "awk",
        "sed",
        "perl",
        "python",   # bare python receiving stdin is a script runner
        "python3",
        "jq",
        "fx",
    }
)

# Flags that request structured machine-readable output — do not rewrite
STRUCTURED_OUTPUT_FLAGS: frozenset[str] = frozenset(
    {
        "--json",
        "--format=json",
        "--format=JSON",
        "--output=json",
        "--porcelain",
        "--porcelain=v1",
        "--porcelain=v2",
    }
)

# cat flags that are safe to rewrite (only content display flags)
CAT_SAFE_FLAGS: frozenset[str] = frozenset({"-n", "--number", "-b", "--number-nonblank"})

# ---------------------------------------------------------------------------
# Predicate helpers (used by classifier)
# ---------------------------------------------------------------------------


def is_known_command(base: str, args: list[str]) -> bool:
    """Return True if this base+args combination has a Quor filter."""
    if base in ("python", "python3"):
        return (
            len(args) >= 2
            and args[0] == "-m"
            and args[1] in _KNOWN_PYTHON_SUBCOMMANDS
        )
    return base in _KNOWN_BASE_COMMANDS


def has_structured_output_flag(args: list[str]) -> bool:
    """Return True if any arg requests structured/machine-readable output."""
    return any(a in STRUCTURED_OUTPUT_FLAGS for a in args)


def cat_is_safe_to_rewrite(args: list[str]) -> bool:
    """Return True if the cat invocation only uses display flags (not -e, -v, etc.)."""
    for arg in args:
        if arg.startswith("-") and arg not in CAT_SAFE_FLAGS and not arg.startswith("--"):
            return False
    return True


def pipe_segment_is_safe(segment: str) -> bool:
    """Return True if the pipe target doesn't consume structured stdin."""
    stripped = segment.strip()
    first_word = stripped.split()[0] if stripped.split() else ""
    return first_word not in PIPE_INCOMPATIBLE_COMMANDS


def is_transparent_prefix(words: list[str]) -> tuple[str, list[str]] | None:
    """Check if words[0] is a transparent prefix.

    Returns (prefix_str, remaining_words) if matched, else None.
    prefix_str includes the prefix command and any consumed arguments.
    """
    if not words:
        return None

    first = words[0]

    # Single-token transparent prefixes (sudo, npx, etc.)
    if first in TRANSPARENT_PREFIXES:
        return (first, words[1:])

    # Multi-word transparent prefixes (docker exec <container>)
    for keyword, consume in TRANSPARENT_MULTI_WORD_PREFIXES:
        if first == keyword and len(words) > consume - 1:
            # e.g. "docker exec mycontainer" → consume first 3 tokens
            prefix_words = words[:consume]
            return (" ".join(prefix_words), words[consume:])

    return None
