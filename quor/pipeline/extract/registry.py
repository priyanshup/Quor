"""Document extraction — binary document to plain text, extension-routed.

A preprocessing step for `quor/adapters/claude_read.py`'s Read hook: turns a
binary document on disk into plain text *before* it would reach the existing
`FilterRegistry`/`Pipeline` (`quor/filters/registry.py`, `quor/pipeline/`) —
extraction is not a `StageHandler`, is never registered with `Pipeline`, and
never touches `ContentMask`. `.md`/`.txt`/`.rst` need no extraction at all —
Read already returns them as plain text, and QB-007B/C's `markdown`/
`document-text` filters already compress them directly — so they are simply
not registered here; an unregistered extension fails open exactly like an
unimplemented one.

QB-007E1 built the routing/fail-open contract with both handlers as stubs
that always raised `NotImplementedError`. QB-007E2 fills in `.docx` for
real (`quor/pipeline/extract/docx.py`, optional `python-docx` dependency,
`quor[documents]`); `.pdf` (QB-007E3) is still a stub. Wiring `extract()`
into `claude_read.py` remains out of scope; see backlog.md's QB-007E1/E2
entries.

Public API: `extract(file_path: Path) -> str | None`. `None` always means
"fail open, proceed exactly as if this layer did not exist." `extract()`
never raises.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable
from pathlib import Path

from quor.pipeline.extract.docx import extract_docx


def _extract_pdf(file_path: Path) -> str | None:
    """PDF structure extraction — not implemented yet (QB-007E3)."""
    raise NotImplementedError("PDF extraction is not implemented yet (QB-007E3)")


# Extension-based routing table. Only extensions that genuinely need
# binary-to-text extraction are registered — `.md`/`.txt`/`.rst` are
# deliberately absent (see module docstring); an unregistered extension and
# a registered-but-unimplemented one (still `.pdf`, QB-007E3) both fail open
# to `None` via extract() below, but only a registered extension is ever
# dispatched to a handler.
_EXTRACTORS: dict[str, Callable[[Path], str | None]] = {
    ".docx": extract_docx,
    ".pdf": _extract_pdf,
}


def extract(file_path: Path) -> str | None:
    """Return extracted plain text for `file_path`, or `None` to fail open.

    `None` covers every case a caller must treat identically — "there is
    nothing more this layer can do; proceed exactly as if extraction did
    not exist":
      - the extension has no registered handler (includes `.md`/`.txt`/
        `.rst`, and anything unknown or unrecognized)
      - the registered handler hasn't been implemented yet — QB-007E1's
        `.docx`/`.pdf` stubs always raise `NotImplementedError`, absorbed
        silently (an expected, known state, not a bug)
      - the handler raised for any other reason (a genuine extraction
        failure, once QB-007E2/E3 land) — absorbed with a warning

    Mirrors the fail-open discipline already used throughout the hook path
    (`quor/adapters/claude_read.py`, `quor/adapters/dispatcher.py`). This
    function must never raise.
    """
    handler = _EXTRACTORS.get(file_path.suffix.lower())
    if handler is None:
        return None
    try:
        return handler(file_path)
    except NotImplementedError:
        return None
    except Exception as exc:  # noqa: BLE001 — fail-open: extraction must never raise
        warnings.warn(f"[quor] document extraction error: {exc}", stacklevel=2)
        return None
