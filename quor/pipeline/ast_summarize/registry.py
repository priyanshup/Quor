"""AST summarization — language-routed analyzer registry (QB-005B/C/D).

A preprocessing helper for AST-aware compression stages
(`quor/pipeline/stages/python_ast_summarize.py`,
`quor/pipeline/stages/code_ast_summarize.py`): given source text and a
language name, returns the set of 1-indexed line numbers eligible for
compression (function/method body lines). This module owns *routing*
only — the actual per-language parsing logic lives in a sibling module per
language (`python.py`, `javascript.py`, `typescript.py` today).

`"javascript"` (QB-005C) and `"typescript"`/`"tsx"` (QB-005D) are
registered **unconditionally** here, exactly like `"python"` — importing
`quor.pipeline.ast_summarize.javascript`/`typescript` never fails or warns
even when the optional `tree-sitter`/`tree-sitter-javascript`/
`tree-sitter-typescript` dependency (`quor[javascript]`) is absent, because
those modules import tree-sitter lazily, inside their own `analyze_*()`
functions, not at module top level (mirrors
`quor/pipeline/extract/docx.py`'s identical lazy-import discipline for
`python-docx`). This keeps this router a plain, uniform dict with no
per-language try/except or availability special-casing — see
`javascript.py`/`typescript.py`'s own module docstrings for the full
missing-dependency fail-open contract.

`"typescript"` and `"tsx"` are two separate registry entries, not one —
`tree-sitter-typescript` exposes two distinct grammars
(`language_typescript()`/`language_tsx()`) that must be selected by file
extension, never inferred from content (empirically confirmed during
QB-005D: JSX syntax genuinely fails to parse under the plain
`language_typescript()` grammar). Mirrors how `"python"`/`"javascript"`
are already two separate entries for two separate file-extension groups,
not a new mechanism.

Mirrors the package shape of `quor/pipeline/extract/registry.py`
(extension-routed, dict-based dispatch, no `Protocol`/ABC for a
single-callable contract — QB-007E1's own "premature abstraction for a
contract this small" judgment applies here too) but is **NOT** a fail-open
API in the same sense `extract()` is, and this distinction is deliberate,
not an oversight:

  - `extract()` NEVER raises — every failure (missing dependency, corrupt
    file, unimplemented handler) is absorbed internally and reported as
    `None`, because DOCX/PDF extraction sits in front of `FilterRegistry`/
    `Pipeline` with no engine-level fail-open safety net of its own.
  - `analyze()` in this module DOES let a genuine parse failure propagate
    (e.g. `analyze_python()` lets `ast.parse()`'s `SyntaxError`/`ValueError`
    through unchanged) because it runs *inside* a `StageHandler.apply()`,
    which already has `Pipeline.execute()`'s per-stage fail-open guarantee
    (ADR-018) sitting above it. Catching the exception here too would be a
    second, redundant safety net — exactly what
    `python_ast_summarize.py`'s own module docstring already argued against
    for the pre-QB-005B, single-language implementation. See
    `quor/pipeline/ast_summarize/python.py`'s own docstring for the same
    point made language-locally.

`get_analyzer()` returning `None` means something different from an
analyzer raising: `None` means "this language has no registered analyzer at
all" (QB-005A Section 4.2's "unsupported language" case — a clean,
non-exceptional skip). A registered analyzer raising means "this specific
input could not be parsed as this specific, supported language" (QB-005A
Section 4's "syntax error" / "parser crash" cases — an engine-level
fail-open, not a registry-level one). Callers must not conflate the two.
"""

from __future__ import annotations

from collections.abc import Callable

from quor.pipeline.ast_summarize.javascript import analyze_javascript
from quor.pipeline.ast_summarize.python import analyze_python
from quor.pipeline.ast_summarize.typescript import analyze_tsx, analyze_typescript

# Language name -> analyzer callable. "python" (QB-005B), "javascript"
# (QB-005C), and "typescript"/"tsx" (QB-005D) are registered. See
# backlog.md for the full rollout history.
_ANALYZERS: dict[str, Callable[[str], set[int]]] = {
    "python": analyze_python,
    "javascript": analyze_javascript,
    "typescript": analyze_typescript,
    "tsx": analyze_tsx,
}


def get_analyzer(language: str) -> Callable[[str], set[int]] | None:
    """Return the analyzer callable registered for `language`, or `None` if
    no analyzer is registered for it.

    `None` is the "unsupported language" signal (QB-005A Section 4.2) — it
    is not raised as an error, since an unregistered language is an
    entirely expected, non-exceptional condition (e.g. a filter
    misconfigured with `language = "cobol"`, or a future language whose
    filter TOML shipped before its analyzer did — mirrors
    `quor/pipeline/extract/registry.py`'s identical "unregistered
    extension" branch).
    """
    return _ANALYZERS.get(language)


def registered_languages() -> frozenset[str]:
    """Return the set of language names with a registered analyzer.

    Exists for `quor doctor`/tests to introspect what's actually wired
    without reaching into `_ANALYZERS` directly.
    """
    return frozenset(_ANALYZERS)
