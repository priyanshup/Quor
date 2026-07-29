"""Shared atomic-write helpers: write to a temp file, then `os.replace()` it
onto the real path — a crash, exception, or Ctrl+C mid-write can never leave
a half-written file at `path` itself; readers only ever see the previous
complete file or the new complete file, never something in between.

Consolidates what used to be three independent copies of this exact pattern
(`quor/cli/commands/init.py`, `quor/adapters/gemini_adapter.py`,
`quor/pipeline/onboarding.py`, QB-070) into one implementation.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import orjson


def write_text_atomic(path: Path, content: str) -> None:
    """Write `content` to `path` atomically (tempfile + os.replace).

    On any exception — including KeyboardInterrupt/SystemExit, both
    BaseException rather than Exception, so Ctrl+C mid-write is covered too
    — the partial temp file is removed and the original exception
    re-raised. `path` is only ever replaced by a complete, successfully
    written file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def write_json_atomic(path: Path, data: dict[str, Any], *, indent: bool = True) -> None:
    """Write `data` to `path` as JSON, atomically (tempfile + os.replace).

    `indent` defaults to matching every existing call site's prior
    formatting (`orjson.OPT_INDENT_2`) — pass `indent=False` for a
    compact, single-line file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        option = orjson.OPT_INDENT_2 if indent else 0
        with os.fdopen(fd, "wb") as fh:
            fh.write(orjson.dumps(data, option=option))
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
