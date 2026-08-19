"""Windows extended-length path support (`\\\\?\\` prefix), QB-110.

Windows' classic MAX_PATH (260-character) limit applies to ordinary
(non-prefixed) path strings even when the `LongPathsEnabled` registry key
is off — which requires admin rights to change, something Quor's own
target user (corporate Windows, no admin rights, per `docs/final/CLAUDE.md`)
cannot do. The `\\\\?\\` extended-length prefix bypasses MAX_PATH entirely
for Win32 file APIs, with no privilege requirement at all. Verified
empirically during the QB-110 audit: on a real machine with
`LongPathsEnabled=0`, `Path.read_text()`/`.exists()`/`os.listdir()` all
fail (or, for `.exists()`, silently report `False` on a file that's
genuinely there) on a real >260-character path without the prefix, and all
succeed with it.

Real trigger for Quor specifically: Git for Windows commonly has its own
long-path support enabled internally, so `walk.py`'s primary path
(`git ls-files`) can legitimately report tracked files whose absolute path
exceeds 260 characters — files git itself can see, but that an unprefixed
`Path.read_text()` call can't open. Every per-file read in this package
already fails open (`except (OSError, UnicodeDecodeError)`, marking the
file `parse_failure`/skipped rather than crashing), so this was never a
crash risk — just silently reduced repo-intelligence coverage on exactly
the deep-corporate-package-structure, OneDrive-synced-desktop scenario
Quor is built for. This module exists to avoid the failure happening at
all, not just to fail open once it does.

`to_long_path()`'s result is for immediate use in one file-system call
only — never store it or do further path arithmetic (`.relative_to()`
against an unprefixed path, `.as_posix()`, etc.) on the result outside the
one call site that needs it. Repo-relative path bookkeeping elsewhere in
this codebase stays `Path.as_posix()`-normalized, unprefixed, per the
project's existing convention (see `quor/tracking/db.py`'s
`normalize_project_path()`).
"""

from __future__ import annotations

import os
from pathlib import Path

_EXTENDED_PREFIX = "\\\\?\\"
_UNC_PREFIX = "\\\\?\\UNC\\"

# Conservative margin below the real 260-character MAX_PATH — leaves room
# for a filename plus null terminator without computing the exact
# remaining budget at each call site.
_LONG_PATH_THRESHOLD = 240


def to_long_path(path: Path, *, force: bool = False) -> Path:
    """Return `path`, extended-length-prefixed if it's a Windows path long
    enough to risk hitting MAX_PATH. A no-op everywhere else: non-Windows,
    relative paths (the prefix requires an absolute path), paths already
    under the threshold, and paths already prefixed.

    `force=True` skips the length threshold (still a no-op on non-Windows,
    relative, or already-prefixed paths) — for a directory that's about to
    be recursively walked, not just opened once. `os.walk()` builds every
    deeper `dirpath` via plain string concatenation off the root it's
    given, so an unprefixed root that's currently short can still recurse
    into a descendant that exceeds MAX_PATH; there is no way to know the
    eventual depth in advance, so the root must be prefixed unconditionally
    rather than gated on its own current length. See `walk.py`'s
    `_walk_fallback` for the one caller that needs this.
    """
    if os.name != "nt":
        return path
    raw = str(path)
    if raw.startswith(_EXTENDED_PREFIX):
        return path
    if not path.is_absolute() or (not force and len(raw) < _LONG_PATH_THRESHOLD):
        return path
    if raw.startswith("\\\\"):
        # UNC path (\\server\share\...) needs its own prefix form —
        # \\?\UNC\server\share\..., not \\?\\\server\share\....
        return Path(_UNC_PREFIX + raw[2:])
    return Path(_EXTENDED_PREFIX + raw)
