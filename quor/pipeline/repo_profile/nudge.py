"""Repository-intelligence onboarding nudge (QB-090).

Two independent problems `quor map`/`quor symbols`/`quor graph` had before
this module: (1) nothing ever told a new user these commands exist, so the
Read-hook features built on top of them (`claude_read.py`'s "Repository
Context" and "Relevant files" blocks, QB-079/QB-081) were silently inert for
anyone who didn't already know to run `quor map` first; (2) once built,
nothing ever told a user the cache had drifted out of date after real
changes to the repo, so those same features could quietly keep working off
stale data indefinitely.

This module is deliberately *not* a second Automatic Repository Intelligence
(`intel.py`) — it never builds, walks, or diffs anything expensively. Its
only two jobs are: decide "should a hint be shown right now" (cheap, O(1)
state-file reads plus at most one rate-limited git subprocess call — see
`_maybe_stale_tip`'s own docstring for why this is safe on the hook path),
and render the hint's plain-text content. Building the actual cache, when
the user agrees, is still entirely `ensure_repo_intelligence()`'s job — this
module only ever suggests running it.

**Two different surfaces, two different message shapes, on purpose.**
`quor init --claude`'s interactive flow (`quor/cli/commands/init.py`) runs
in a real terminal with a real TTY — a normal `walk_repository()` call to
count files for the setup estimate is exactly as cheap here as it already is
for `quor map`/`symbols`/`graph` themselves, so `estimate_build_cost()`
below is only ever called from there. The Read-hook tip
(`quor/adapters/claude_read.py`) fires on every Read call and must never pay
for a repository walk at all — `claude_read.py`'s own module docstring
already establishes this as an absolute rule for every feature that lives
there (`_maybe_prepend_repo_context`/`_maybe_prepend_relevant_files` both
document "never calls `ensure_repo_intelligence()` or `walk_repository()`");
this module's hook-facing functions (`maybe_never_built_tip`/
`maybe_stale_tip`) hold to the same rule and are correspondingly plainer —
no file count, no time estimate, just a one-line pointer to `quor map`.

**Suppression policy (deliberately this simple, not a consent/preference
state machine — see backlog QB-090's own note on why that was scoped out):**

```
Never built  -> shown at most MAX_NEVER_BUILT_SHOWS times, ever
Built        -> silent forever (state_exists() becomes True)
Stale        -> checked at most once per STALE_CHECK_INTERVAL; shown once
                per check that finds real drift
Built again  -> naturally resets: a fresh build advances git_head, so the
                next check finds no drift and stays silent
```

No per-repo accept/decline preference, no "remind me later," no opt-out
tracking. A user who wants the tip gone entirely just runs `quor map` once.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import orjson

from quor.atomic_io import write_json_atomic
from quor.pipeline.repo_profile import intel_store
from quor.pipeline.repo_profile.intel_diff import git_head
from quor.pipeline.repo_profile.walk import walk_repository

MAX_NEVER_BUILT_SHOWS = 5
"""Same throttling philosophy as `pipeline/onboarding.py`'s own
`MAX_ONBOARDING_COMMANDS` — a tip that never stops firing would waste more
tokens nagging about savings than it could ever recover."""

STALE_CHECK_INTERVAL = timedelta(hours=24)
STALE_FILE_THRESHOLD = 20
"""Minimum committed-file change count before staleness is worth mentioning
— a project that's had 2 files touched in a week doesn't need a nudge; one
that's had 80 does. Deliberately a plain, documented constant, not a learned
or inferred heuristic."""

_NUDGE_STATE_FILENAME = "nudge_state.json"
_GIT_TIMEOUT = 10.0
_ESTIMATED_FILES_PER_SECOND = 40
"""Rough calibration only, deliberately conservative and always presented
as an estimate, never a guarantee — derived from real measured repository-
intelligence build throughput, not invented. Repos with a heavier language
mix (more AST-parsed files per file) will run slower than this; the
estimate is intentionally not fine-tuned per-language to avoid implying
more precision than a one-time setup estimate needs."""


@dataclass(frozen=True, slots=True)
class NudgeState:
    never_built_shown_count: int = 0
    last_stale_check_at: str | None = None
    """ISO-8601 UTC timestamp of the last time this module actually paid
    for a git-based staleness check (not merely considered one) — gates
    `_maybe_stale_tip`'s one rate-limited subprocess call to at most once
    per `STALE_CHECK_INTERVAL`, regardless of how many Read calls happen
    in between."""


def _nudge_state_path(root: Path) -> Path:
    return intel_store.cache_dir(root) / _NUDGE_STATE_FILENAME


def _load_nudge_state(root: Path) -> NudgeState:
    path = _nudge_state_path(root)
    if not path.exists():
        return NudgeState()
    try:
        data: dict[str, Any] = orjson.loads(path.read_bytes())
        return NudgeState(
            never_built_shown_count=int(data.get("never_built_shown_count", 0)),
            last_stale_check_at=data.get("last_stale_check_at"),
        )
    except (orjson.JSONDecodeError, KeyError, TypeError, ValueError):
        return NudgeState()


def _save_nudge_state(root: Path, state: NudgeState) -> None:
    write_json_atomic(
        _nudge_state_path(root),
        {
            "never_built_shown_count": state.never_built_shown_count,
            "last_stale_check_at": state.last_stale_check_at,
        },
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Hook-facing tips — no repository walk, ever (see module docstring).
# ---------------------------------------------------------------------------


def compute_hook_nudge(root: Path) -> str | None:
    """Single entry point callers use (`quor/mcp/server.py`'s
    `get_repo_context` tool, formerly `claude_read.py`'s Read hook): one
    fast git subprocess call to confirm `root` is a git repository, plus at
    most one cheap state read, plus (rate-limited to once per
    `STALE_CHECK_INTERVAL`) one or two more fast git subprocess calls —
    never a repository walk. Returns `None` the instant there's nothing
    worth saying; never raises (callers still wrap this in their own
    fail-open guard, but every internal failure here already degrades to
    `None` on its own).

    The git-repo check is not just cost hygiene — it's the feature's own
    stated scope (a bare, non-project directory has nothing sensible to
    index, mirrors `quor init --claude`'s identical gate in
    `_maybe_offer_repo_intelligence_setup`) and, as a direct consequence,
    keeps this feature from ever firing against the countless ad hoc,
    non-git temp directories the rest of this codebase's own test suite
    reads through — a real regression found and fixed while building this:
    without this gate, `_maybe_prepend_repo_intel_nudge()` would inject a
    tip into every "pure passthrough" Read hook test's output, silently
    breaking their own no-op assertions."""
    if not is_git_repo(root):
        return None
    if intel_store.state_exists(root):
        return _maybe_stale_tip(root)
    return _maybe_never_built_tip(root)


def _maybe_never_built_tip(root: Path) -> str | None:
    state = _load_nudge_state(root)
    if state.never_built_shown_count >= MAX_NEVER_BUILT_SHOWS:
        return None
    _save_nudge_state(
        root,
        NudgeState(
            never_built_shown_count=state.never_built_shown_count + 1,
            last_stale_check_at=state.last_stale_check_at,
        ),
    )
    return (
        "[Repository Tip] Repository intelligence hasn't been built yet.\n"
        "Run `quor map` to improve repository-aware compression, search, and "
        "relevant-file suggestions.\n"
        "(This tip stops appearing after a few reminders, or as soon as you run it.)\n\n"
    )


def _maybe_stale_tip(root: Path) -> str | None:
    state = _load_nudge_state(root)
    if state.last_stale_check_at is not None:
        try:
            last_checked = datetime.fromisoformat(state.last_stale_check_at)
        except ValueError:
            last_checked = None
        if last_checked is not None and _utc_now() - last_checked < STALE_CHECK_INTERVAL:
            return None  # checked recently — stay silent regardless of outcome

    # Due for a check. This is the one place this module spends real cost —
    # bounded to two fast git subprocess calls, at most once per interval,
    # never a repository walk (see module docstring).
    _save_nudge_state(
        root,
        NudgeState(
            never_built_shown_count=state.never_built_shown_count,
            last_stale_check_at=_utc_now().isoformat(),
        ),
    )

    intel_state = intel_store.load_state(root)
    if intel_state is None:
        return None  # state.json existed per state_exists() but is unreadable — fail open

    current_head = git_head(root)
    if current_head is None or intel_state.git_head is None or current_head == intel_state.git_head:
        return None  # not a git repo, git unavailable, or genuinely unchanged

    changed = _committed_files_changed(root, intel_state.git_head, current_head)
    if changed is None or changed < STALE_FILE_THRESHOLD:
        return None

    age_days = _age_in_days(intel_state.last_completed_build)
    age_phrase = f"{age_days} day{'s' if age_days != 1 else ''} old" if age_days is not None else "out of date"
    return (
        f"[Repository Tip] Repository intelligence is {age_phrase} and {changed} "
        f"file{'s' if changed != 1 else ''} have changed since.\n"
        "Run `quor map` to refresh it.\n\n"
    )


def _age_in_days(last_completed_build: str) -> int | None:
    try:
        built_at = datetime.fromisoformat(last_completed_build)
    except ValueError:
        return None
    return max(0, (_utc_now() - built_at).days)


_SHORTSTAT_FILES_RE = re.compile(r"(\d+) files? changed")


def _committed_files_changed(root: Path, old_head: str, new_head: str) -> int | None:
    """Count of files touched between two commits, via `git diff --shortstat`
    — a single, fast git-internal diff bounded by the size of the change,
    not the size of the repository, unlike `intel_diff.py`'s fingerprint-
    based walk. Deliberately git-only: this misses uncommitted working-tree
    changes entirely (the same limitation `RepoIntelState.git_head`'s own
    docstring already documents), a fine trade-off for a nudge that can
    afford an occasional false negative in exchange for never touching the
    filesystem-wide fingerprint machinery from the hook path."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "diff", "--shortstat", f"{old_head}..{new_head}"],
            capture_output=True,
            timeout=_GIT_TIMEOUT,
            text=True,
            encoding="utf-8",
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    match = _SHORTSTAT_FILES_RE.search(result.stdout)
    return int(match.group(1)) if match else None


# ---------------------------------------------------------------------------
# CLI-facing (`quor init --claude`) — a real walk is fine here; see module
# docstring for why this is never called from the hook path.
# ---------------------------------------------------------------------------


def estimate_build_cost(root: Path) -> tuple[int, int]:
    """`(file_count, estimated_seconds)` for the one-time interactive setup
    prompt. `file_count` is exact (a real `walk_repository()` call — the
    same one `quor map` itself would make); `estimated_seconds` is a rough,
    clearly-labeled-as-a-estimate calibration, not a promise."""
    file_count = len(walk_repository(root).files)
    estimated_seconds = max(1, round(file_count / _ESTIMATED_FILES_PER_SECOND))
    return file_count, estimated_seconds


def is_git_repo(root: Path) -> bool:
    """Cheap, single-subprocess-call check — used only to decide whether
    `quor init --claude` should offer the repository-intelligence setup
    prompt at all (skipped entirely for e.g. a bare home-directory install,
    per this feature's own scope)."""
    return git_head(root) is not None
