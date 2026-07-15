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
        "npm",      # QB-006A: generic Node.js package-manager wrapper noise
        "npx",      # same — npx always wraps another tool, but its own
                    # resolution/install preamble is generic, tool-agnostic noise
        "pnpm",     # same
        "yarn",     # same
        "tsc",      # QB-006C: bare TypeScript compiler invocation — also
                    # reachable wrapped (npx tsc, pnpm exec tsc, ...), see the
                    # "tsc" filter block in node.toml
        "jest",     # QB-006C: bare Jest invocation — also reachable wrapped
        "vitest",   # QB-006C: bare Vitest invocation — also reachable wrapped
        "prettier", # QB-006C: bare Prettier invocation — also reachable wrapped
        "next",     # QB-006C: Next.js CLI (build/dev/start/lint) — a base
                    # command in its own right, not a wrapped tool
        "turbo",    # QB-006C: Turborepo task runner — same, wraps arbitrary
                    # per-package scripts but is itself the invoked command
        "gradle",       # QB-045: Gradle wrapper/CLI build noise
        "gradlew",      # same, invoked without the leading "./" (rare but
                        # some shells/PATH setups allow it)
        "./gradlew",    # same, the common Unix wrapper-script invocation
        "gradlew.bat",  # same, Windows wrapper-script invocation
        "mvn",          # QB-045: Maven CLI build noise
        "mvnw",         # same, wrapper invoked without "./"
        "./mvnw",       # same, the common Unix wrapper-script invocation
        "mvnw.cmd",     # same, Windows wrapper-script invocation
        "java",         # QB-056: bare JVM invocation (java -jar app.jar,
                        # java -cp ... Main) — Java stack-trace compression
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
#
# npx/yarn/pnpm are deliberately NOT here (QB-006A): they are now known base
# commands in their own right (see _KNOWN_BASE_COMMANDS above) so their own
# generic wrapper noise gets filtered, regardless of what they wrap underneath.
# bunx stays a transparent prefix — Bun is out of scope for QB-006A.
TRANSPARENT_PREFIXES: tuple[str, ...] = (
    "sudo",
    "doas",
    "bunx",
    "deno",
    "time",
    "env",
    "nice",
)

# Docker/podman style transparent prefixes that consume more tokens
TRANSPARENT_MULTI_WORD_PREFIXES: tuple[tuple[str, str, int], ...] = (
    # (first_token, required_subcommand, total_tokens_consumed_including_container_name)
    #
    # QB-045: this used to match on `first_token` alone, so "docker build -t
    # foo ." was silently swallowed as a fake "docker exec"-shaped transparent
    # prefix (consuming "docker build -t" as the "prefix", leaving "foo ." as
    # a bogus "wrapped command") — docker build could never be routed to its
    # own filter. Gating on the literal "exec" subcommand restricts this to
    # the one shape it was actually designed for.
    ("docker", "exec", 3),     # docker exec <container>
    ("podman", "exec", 3),     # podman exec <container>
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
    if base in ("docker", "docker-compose"):
        # QB-045: docker has many subcommands (run, ps, logs, ...) that are
        # out of scope; `docker exec <container> <cmd>` never reaches here at
        # all (handled earlier as a transparent prefix). Only route the
        # *build* subcommand, in each of its real invocation shapes.
        if not args:
            return False
        if base == "docker-compose":
            return args[0] == "build"
        if args[0] == "build":
            return True
        if args[0] == "buildx" and len(args) >= 2 and args[1] == "build":
            return True
        return args[0] == "compose" and len(args) >= 2 and args[1] == "build"
    if base == "gh":
        # QB-045: only gh's own CI-log-retrieval subcommands are in scope —
        # every other gh subcommand (pr, issue, repo, ...) passes through
        # untouched, same subcommand-gating discipline as python -m above.
        return len(args) >= 2 and args[0] == "run" and args[1] in ("view", "watch")
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

    # Multi-word transparent prefixes (docker exec <container>) — gated on
    # the required subcommand so e.g. "docker build ..." (QB-045) is never
    # mistaken for this shape.
    for keyword, subcommand, consume in TRANSPARENT_MULTI_WORD_PREFIXES:
        if (
            first == keyword
            and len(words) > 1
            and words[1] == subcommand
            and len(words) > consume - 1
        ):
            # e.g. "docker exec mycontainer" → consume first 3 tokens
            prefix_words = words[:consume]
            return (" ".join(prefix_words), words[consume:])

    return None
