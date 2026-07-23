"""Structured-data format registry (QB-040) — format-name-routed analyzer
lookup, mirroring `quor/pipeline/ast_summarize/registry.py`'s shape exactly
(same reasoning: a preprocessing helper for a compression stage —
`quor/pipeline/stages/structured_data_summarize.py` — that owns *routing*
only, with the actual per-format parsing logic living in a sibling module
per format: `json_fmt.py`, `yaml_fmt.py`, `toml_fmt.py`).

`"json"` and `"toml"` are registered unconditionally (no optional
dependency — stdlib `json`/`tomllib` only). `"yaml"` is also registered
unconditionally — `yaml_fmt.analyze_yaml()` imports PyYAML lazily, inside
the function, not at module top level, exactly like
`quor/pipeline/ast_summarize/go.py` imports tree-sitter — so importing this
module never fails even without `quor[yaml]` installed; a missing PyYAML is
`analyze_yaml()`'s own fail-open concern (returns `[]`, warns), not this
registry's.

`get_analyzer()` returning `None` means "unregistered format" (a filter
misconfigured with `format = "xml"`), a clean, non-exceptional skip — the
`structured_data_summarize` stage fails open (mask unchanged) on this,
exactly like `code_ast_summarize` does for an unregistered `language`. A
registered analyzer *raising* means "this specific input could not be
parsed as this specific format" — propagates through to `Pipeline.execute
()`'s per-stage fail-open. Callers must not conflate the two, same
distinction `quor/pipeline/ast_summarize/registry.py`'s own docstring draws.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module

from quor.pipeline.structured_data.collapse import CollapseRange
from quor.pipeline.structured_data.json_fmt import analyze_json
from quor.pipeline.structured_data.toml_fmt import analyze_toml
from quor.pipeline.structured_data.yaml_fmt import analyze_yaml

_ANALYZERS: dict[str, Callable[[str], list[CollapseRange]]] = {
    "json": analyze_json,
    "yaml": analyze_yaml,
    "toml": analyze_toml,
}

# Optional package each format's analyzer needs at call time (imported
# lazily inside yaml_fmt.py, never at module top level). "json"/"toml" have
# no entry: stdlib `json`/`tomllib`, always available once the >=3.11 floor
# is met.
_REQUIRED_PACKAGES: dict[str, tuple[str, ...]] = {
    "yaml": ("yaml",),
}

_EXTRA_FOR_FORMAT: dict[str, str] = {
    "yaml": "yaml",
}


def get_analyzer(format_name: str) -> Callable[[str], list[CollapseRange]] | None:
    """Return the analyzer callable registered for `format_name`, or `None`
    if no analyzer is registered for it — the "unsupported format" signal,
    not an error (see module docstring)."""
    return _ANALYZERS.get(format_name)


def registered_formats() -> frozenset[str]:
    """Return the set of format names with a registered analyzer."""
    return frozenset(_ANALYZERS)


def is_format_available(format_name: str) -> bool:
    """True if `format_name` is registered *and* its analyzer can actually
    run right now — False if unregistered, or registered but its optional
    dependency isn't installed. Mirrors `quor.pipeline.ast_summarize.
    registry.is_language_available()` exactly, including its use by `quor
    verify`/`quor doctor` to skip (not fail) a check that can only pass with
    an optional extra installed."""
    if format_name not in _ANALYZERS:
        return False
    for module_name in _REQUIRED_PACKAGES.get(format_name, ()):
        try:
            import_module(module_name)
        except ImportError:
            return False
    return True


def extra_for_format(format_name: str) -> str | None:
    """Return the pip extra name (e.g. "yaml" for `pip install
    "quor[yaml]"`) that installs `format_name`'s optional dependency, or
    `None` if it needs no extra (e.g. "json"/"toml") or isn't registered."""
    return _EXTRA_FOR_FORMAT.get(format_name)
