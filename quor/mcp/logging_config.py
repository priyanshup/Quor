"""Shared file logging sink for the MCP server process
(`quor/mcp/launcher.py` + `quor/mcp/server.py`).

Adds a rotating file handler at `~/.quor/logs/mcp.log` alongside whatever
each module already prints to `sys.stderr` — it never replaces those
prints, and it never touches `sys.stdout`: stdio is the MCP transport once
`quor.mcp.server.main()` takes over, and `sys.stdout` must stay pristine
JSON-RPC framing from the moment the process is spawned (the same rule
`quor/mcp/launcher.py`'s own module docstring already documents for why it
never imports `rich`).

`configure_logging()` is idempotent — `launcher.main()` calls it, then
hands off to `server.main()` in the *same process*, which calls it again.
A module-level guard keeps that from attaching duplicate handlers, which
would otherwise double every log line.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "quor.mcp"

_LOG_DIR = Path.home() / ".quor" / "logs"
_LOG_FILE = _LOG_DIR / "mcp.log"
_MAX_BYTES = 2 * 1024 * 1024
_BACKUP_COUNT = 3

_configured = False


def configure_logging() -> logging.Logger:
    """Return the shared `quor.mcp` logger, attaching its rotating file
    handler on first call. Fails open: if `~/.quor/logs/` can't be created
    or written to (read-only home directory, permissions issue), the
    logger is still returned and usable — it just won't have a file
    handler, and every existing `print(..., file=sys.stderr)` call site
    keeps working exactly as before. A logging sink must never be the
    reason the MCP server fails to start."""
    global _configured

    logger = logging.getLogger(LOGGER_NAME)
    if _configured:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False

    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = RotatingFileHandler(
            _LOG_FILE, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        logger.addHandler(handler)
    except OSError:
        pass

    _configured = True
    return logger
