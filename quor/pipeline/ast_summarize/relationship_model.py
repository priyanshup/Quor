"""`Relationship` — the per-language, per-file relationship-extraction data
contract (QB-067).

A second sibling capability alongside `symbol_model.py`'s `Symbol`, added to
the same `ast_summarize` language modules that already grew
`extract_symbols_*()` in QB-066: `extract_relationships_*()` (one per
language module) answers "what does this file declare a *relationship* to"
— an import, an export, a base class/interface/trait, an override, or a
call — where `extract_symbols_*()` only answers "what does this file
*declare*." Both parse the same source with the same language's own parser;
neither re-derives the other's logic (see `symbol_model.py`'s own docstring
for why `analyze_*()`/`extract_symbols_*()` are already independently
correct siblings rather than one derived from the other — this module
extends that same discipline a second time).

**Deliberately file-local and unresolved.** Every `Relationship` a
`extract_relationships_*()` function returns is a raw, deterministic fact
about ONE file's own source text — it never reaches into another file, never
guesses whether a name refers to a real symbol, and never performs import
resolution (module-path-to-file mapping, alias-chasing, cross-file lookup).
That resolution is a repository-wide question, not a single-file one, and
lives entirely in `quor/pipeline/repo_profile/graph.py`'s orchestrator —
mirroring the same "per-file extractors emit facts, one orchestrator layer
up resolves/aggregates them" split `quor/pipeline/repo_profile/symbols.py`
already uses for `Symbol`. Keeping resolution out of the per-language
modules means the (genuinely complex, per-language-different) module-path/
alias resolution algorithm is written and tested exactly once, not eight
times with eight chances to drift.

`Relationship` is a plain, frozen dataclass — not Pydantic — for the exact
same reason `Symbol` is (see `symbol_model.py`'s docstring): every field is
computed internally from a parse tree, never user input, and `orjson`
serializes it natively.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RelationshipKind = Literal[
    "import",
    "export",
    "inherits",
    "implements_interface",
    "implements_trait",
    "overrides",
    "calls",
]


@dataclass(frozen=True)
class Relationship:
    """One raw, file-local relationship fact, exactly as written in the
    source — never resolved against any other file (see module docstring).

    Field meaning varies deliberately by `kind`, the same way a single
    `Symbol.kind` already carries different implications for `is_public`
    depending on language — each `extract_relationships_*()` function's own
    docstring documents its language's exact mapping. The general shape:

    - `import`: file-level (`source=""`). `target` is the raw module/path
      specifier exactly as written (e.g. `"./utils"`, `"foo.bar"`,
      `"crate::a::b"`). `qualifier` is the local name this import binds in
      the importing file's own namespace (an alias, or the plain imported
      name) — `None` only for import forms that bind nothing addressable
      (e.g. a bare `import "pkg/side_effect"` in Go). `origin` is the
      specific name imported *from* `target` for a "from/named import" form
      (`from target import origin as qualifier`) — `None` when the whole
      module/namespace is bound rather than one name from it.
    - `export`: symbol-level. `source` is the exported symbol's name,
      `target` is unused (empty string).
    - `inherits` / `implements_interface` / `implements_trait`: `source` is
      the subclass/implementing type's name, `target` is the raw base-type/
      interface/trait name as written (never resolved to a file here).
    - `overrides`: `source` is the overriding method's name, `target` is
      that method's own name again (by construction — an override always
      overrides a same-named member), `qualifier` is the enclosing class's
      own raw superclass/supertype name (the type the override is claimed
      against) — resolution of *which* file/class that name lives in
      happens in the orchestrator, exactly like `inherits`.
    - `calls`: `source` is the enclosing function/method name (`""` for a
      module-level call), `target` is the callee's simple name, `qualifier`
      is an optional prefix (`self`, `this`, an import alias/namespace) for
      a qualified call (`qualifier.target(...)`) — `None` for a bare call.
    """

    kind: RelationshipKind
    source: str
    target: str
    line: int
    """1-indexed line the relationship's own syntax appears on (the import
    statement, the base-class reference, the call expression, ...)."""

    qualifier: str | None = None
    origin: str | None = None
