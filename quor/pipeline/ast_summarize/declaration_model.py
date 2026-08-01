"""`Declaration` — the data contract for declaration-level structural diffing
(QB-099A).

A fourth sibling capability alongside `symbol_model.py`'s `Symbol`,
`relationship_model.py`'s `Relationship`, and `import_model.py`'s
`ImportStatement`, added to the same per-language `ast_summarize` modules:
`extract_declarations_*()` answers "what does this file declare, in enough
structural detail to compare it against another version of itself" — a
different question from `extract_symbols_*()` ("what does this file declare,
as a flat, serializable list for `quor symbols`"). The two overlap in the
facts they carry (name, kind, line) but are deliberately independent, not one
derived from the other: `Symbol` is a plain, externally-serializable leaf
value (`dataclasses.asdict()`, cached across `quor symbols` runs);
`Declaration` carries a live reference to the language's own parse-tree node
(`node`, untyped here — the concrete extractor's own return type narrows it,
e.g. `ast.AST` for `extract_declarations_python()`) plus positional context
(`index`, `end_line`) that only ever matters in-process, for one diff
comparison, and is never meant to be serialized or cached. Forcing one
family to be a view of the other would couple a cacheable, external contract
to an ast.AST reference it has no business holding — see `symbol_model.py`'s
own docstring for why `analyze_*()`/`extract_symbols_*()` already made this
same "independently correct, not derived" call once.

QB-099A registers only `"python"` in `ast_summarize/registry.py`'s
`_DECLARATION_EXTRACTORS` — declaration-level structural diffing is Python-
only for now (see `docs/design/QB-099-structural-diff-compression-
investigation.md`); extending to the tree-sitter languages is real, bounded,
future work, not included here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from quor.pipeline.ast_summarize.symbol_model import SymbolKind


@dataclass(frozen=True, slots=True)
class Declaration:
    """One class/function/method declaration, located and comparable against
    another version of the same file.

    `node`
        The language's own parse-tree node for this declaration (e.g.
        `ast.FunctionDef`/`ast.AsyncFunctionDef`/`ast.ClassDef` for Python).
        Untyped (`Any`) at this shared-model level so a future non-Python
        extractor can return its own node type without widening this field
        into a cross-language union; the structural-diff matcher that
        consumes it is itself language-specific and knows the real type.

    `parent`
        The enclosing class's name for a method or nested class, else
        `None` — same meaning as `Symbol.parent` would carry if `Symbol` had
        one (it doesn't need one; `Declaration` does, since matching across
        containers is exactly what "moved" vs. "reordered" means here).

    `index`
        Source-order rank among sibling declarations sharing this
        `(kind, parent)` — the same sibling-rank concept
        `path_prefix_fold`/`numeric_range_compression` don't need, but a
        declaration-position comparison fundamentally does: this is what a
        one-hunk exact-match tells you it means (unchanged in place, vs.
        reordered, vs. moved).

    `start_line`/`end_line`
        1-indexed, inclusive — this declaration's own full source range
        (signature through last body line), for scoped-diff rendering and
        the minimum-declaration-size floor (see `structural_diff.py`).
    """

    kind: SymbolKind
    name: str
    parent: str | None
    node: Any
    index: int
    start_line: int
    end_line: int

    @property
    def qualname(self) -> str:
        return f"{self.parent}.{self.name}" if self.parent else self.name

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1
