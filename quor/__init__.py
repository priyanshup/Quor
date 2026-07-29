"""quor package root.

`__version__` is resolved lazily via module `__getattr__` (PEP 562), not at
import time. `importlib.metadata` transitively imports `email`, `zipfile`,
and `inspect` even before `version()` is called — importing it eagerly here
used to cost every single `quor` invocation ~40-90ms just to populate a
string that only `quor --version` (and the bare-invocation banner) actually
displays. Deferred until something reads `quor.__version__`.
"""

from __future__ import annotations

from typing import Any

_FALLBACK_VERSION = "0.4.1"
"""Kept in sync with pyproject.toml's [project].version by
test_version_matches_pyproject (QB-020) the same way __version__ itself
used to be."""

_version_cache: str | None = None


def __getattr__(name: str) -> Any:
    if name != "__version__":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    global _version_cache
    if _version_cache is None:
        from importlib.metadata import PackageNotFoundError, version

        try:
            _version_cache = version("quor")
        except PackageNotFoundError:
            # Editable/uninstalled case: running from a source checkout with no
            # installed distribution for importlib.metadata to find (e.g. invoked via
            # `python -m quor` from a checkout that was never `pip install`'d at all).
            _version_cache = _FALLBACK_VERSION
    return _version_cache
