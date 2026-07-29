"""Deterministic file-change detection for Automatic Repository
Intelligence (QB-072).

`diff_repository()` is the single public entry point: given the current
walk of a repository and the `FileFingerprint` table recorded during the
previous scan, it returns exactly which files were added, modified,
deleted, or renamed since — the basis for `intel.py`'s "rebuild only
affected files" promise, instead of a full repository walk-and-parse on
every call.

**Fast path.** Every currently-walked file is `stat()`-ed once (size,
`mtime_ns`); a file whose size and `mtime_ns` both match its previous
fingerprint is assumed unchanged and its content is never re-read or
re-hashed — the same size+mtime fast-reject strategy `make`, `rsync`, and
watchman all use to make "did anything change" cheap on an otherwise
untouched tree. Content is only actually hashed (SHA-256 of the raw bytes)
for a file that is new, or whose size/mtime_ns differ from what was
recorded last time.

**Documented limitation.** A file whose content changes without its size
or `mtime_ns` changing at all (e.g. a deliberately backdated write that
lands on the exact same byte count) will not be detected as modified by
the fast path — the same accepted trade-off every tool using this
strategy makes. This is a real, if pathological, gap; `--rebuild` (or a
version bump, or cache corruption) always forces a full re-hash regardless.

**Rename detection.** A path that disappeared (`deleted`) and a path that
appeared (`added`) are reclassified as one `renamed` pair when they share
an identical `content_hash` — ambiguous many-to-many matches (several
files deleted and several added, all with the same content) are paired
off deterministically in sorted order, which is a reasonable, documented
simplification rather than an attempt to guess "true" provenance no
signal here can actually establish. A file that was both renamed and
edited is *not* detected as a rename (its content hash no longer matches)
— it is correctly reported as a plain delete+add instead, since it needs
the same re-extraction a modified file would.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from quor.pipeline.repo_profile.intel_model import FileFingerprint, RepoDiff

_GIT_TIMEOUT = 10.0
_HASH_CHUNK_SIZE = 1_048_576


def git_head(root: Path) -> str | None:
    """Return `root`'s current git HEAD commit SHA, or `None` when `root`
    is not a git repository (or `git` itself is unavailable) — mirrors
    `walk.py::_git_ls_files`'s identical subprocess-to-git, fail-open
    pattern."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            timeout=_GIT_TIMEOUT,
            text=True,
            encoding="utf-8",
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    head = result.stdout.strip()
    return head or None


def _hash_file(abs_path: Path) -> str | None:
    """SHA-256 hex digest of `abs_path`'s raw bytes, streamed in fixed-size
    chunks so a single large file never needs to be held in memory whole —
    or `None` if the file cannot be read (vanished, permission error, ...),
    fail-open like every other per-file read in this package."""
    try:
        digest = hashlib.sha256()
        with abs_path.open("rb") as fh:
            while chunk := fh.read(_HASH_CHUNK_SIZE):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def fingerprint_files(root: Path, files: list[str]) -> dict[str, FileFingerprint]:
    """Fingerprint every file in `files` unconditionally (no previous state
    to compare against) — used for a first-time or forced full build, where
    every file's fingerprint must be recorded from scratch so the *next*
    call has something to diff against."""
    fingerprints: dict[str, FileFingerprint] = {}
    for rel_path in files:
        abs_path = root / rel_path
        try:
            st = abs_path.stat()
        except OSError:
            continue
        content_hash = _hash_file(abs_path)
        if content_hash is None:
            continue
        fingerprints[rel_path] = FileFingerprint(size=st.st_size, mtime_ns=st.st_mtime_ns, content_hash=content_hash)
    return fingerprints


def diff_repository(
    root: Path, files: list[str], previous: dict[str, FileFingerprint]
) -> tuple[RepoDiff, dict[str, FileFingerprint]]:
    """Compare the current walk (`files`) against `previous` fingerprints.

    Returns `(diff, new_fingerprints)` — `new_fingerprints` is the complete,
    up-to-date fingerprint table for every currently-present, readable file
    (unchanged entries are carried over as-is, not re-hashed), ready to be
    persisted as the new `RepoIntelState.fingerprints` regardless of what
    the caller does with `diff`. Passing `previous={}` (no prior state)
    correctly reports every file as `added` — the same code path a
    first-time/forced-full build uses, so there is exactly one diffing
    algorithm, not a separate "first scan" special case.
    """
    previous_paths = set(previous)
    current_paths = set(files)
    deleted_candidates = previous_paths - current_paths

    new_fingerprints: dict[str, FileFingerprint] = {}
    added: list[str] = []
    modified: list[str] = []

    for rel_path in files:
        abs_path = root / rel_path
        try:
            st = abs_path.stat()
        except OSError:
            continue

        prev_fp = previous.get(rel_path)
        if prev_fp is not None and prev_fp.size == st.st_size and prev_fp.mtime_ns == st.st_mtime_ns:
            new_fingerprints[rel_path] = prev_fp
            continue

        content_hash = _hash_file(abs_path)
        if content_hash is None:
            continue
        fp = FileFingerprint(size=st.st_size, mtime_ns=st.st_mtime_ns, content_hash=content_hash)
        new_fingerprints[rel_path] = fp

        if prev_fp is None:
            added.append(rel_path)
        elif prev_fp.content_hash != content_hash:
            modified.append(rel_path)
        # else: mtime/size changed but content is identical (e.g. a clean
        # checkout or `touch`) — not a real change, nothing to report.

    deleted_by_hash: dict[str, list[str]] = {}
    for d in sorted(deleted_candidates):
        deleted_by_hash.setdefault(previous[d].content_hash, []).append(d)

    renamed: list[tuple[str, str]] = []
    still_added: list[str] = []
    consumed_deleted: set[str] = set()
    for a in sorted(added):
        candidates = deleted_by_hash.get(new_fingerprints[a].content_hash)
        if candidates:
            old_path = candidates.pop(0)
            renamed.append((old_path, a))
            consumed_deleted.add(old_path)
        else:
            still_added.append(a)

    final_deleted = sorted(deleted_candidates - consumed_deleted)

    diff = RepoDiff(
        added=sorted(still_added),
        modified=sorted(modified),
        deleted=final_deleted,
        renamed=sorted(renamed),
    )
    return diff, new_fingerprints
