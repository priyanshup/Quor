"""`ImportStatement`/`ImportBlockReplacement` — the shared, language-agnostic
data contract for import-block collapsing (QB-096).

A third sibling capability alongside `symbol_model.py`'s `Symbol` and
`relationship_model.py`'s `Relationship`, added to the same per-language
`ast_summarize` modules: `collapse_imports_*()` (one per language module)
answers "what would a token-cheaper, still-lossless rendering of this file's
import block look like" — a different question from both `extract_symbols_*()`
("what does this file declare") and `extract_relationships_*()` ("what does
this file depend on," deliberately unresolved and, for imports specifically,
deliberately skipping wildcards as "too ambiguous to resolve" per QB-067's own
mandate). Import collapsing has no such ambiguity concern — it never resolves
anything, it only reformats what is already, unambiguously written in the
source — so it captures wildcards, aliases, and relative imports that
`extract_relationships_*()` intentionally does not.

Unlike `Symbol`/`Relationship`, which are purely per-language leaf data,
`ImportStatement` also feeds a genuinely shared, language-agnostic renderer
(`import_collapse.py`) — see that module's own docstring for why grouping and
rendering are centralized there rather than duplicated in each language
module the way `extract_relationships_*()`'s import-walking logic already is.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ImportedName:
    """One name a single import statement binds.

    `name` is the name exactly as written in the source (the module name for
    a bare Python `import os`, the imported symbol for `from x import y`, or
    the literal `"*"` for a JS/TS namespace import `* as ns` — never `"*"`
    for a true wildcard import, which `ImportStatement.is_wildcard` already
    covers and which binds no addressable name at all).
    """

    name: str
    alias: str | None = None
    """The local alias, if the import renamed `name` (`as` in Python/JS/TS).
    `None` when the name is imported under its own name — rendered as
    `f"{name} as {alias}"` when present, `name` alone otherwise."""


@dataclass(frozen=True, slots=True)
class ImportStatement:
    """One import statement, exactly as written — never resolved, never
    reordered, never deduplicated (a source file that imports the same thing
    twice produces two `ImportStatement`s; QB-096 collapsing never silently
    drops one, since neither this module nor `import_collapse.py` may
    "infer" anything about the file beyond its literal text).

    `module`'s meaning is deliberately different for a bare vs. a "from"-style
    import, mirroring the two different render shapes QB-096 specifies:

    - `module is None`: a bare, module-only import with no natural grouping
      heading of its own (Python's `import os` / `import numpy as np`) —
      collapsed by classification (standard library / third-party) instead
      of by heading. `names` always has exactly one entry for this shape.
    - `module is not None`: a "from"-style import with an explicit source to
      group under as a heading (Python's `from pkg import a, b`, Java's
      `import pkg.Class;`, JS/TS's `import {a, b} from "./mod"`) — every
      `ImportStatement` sharing the same `module` string, even across
      separate statements, collapses under one shared heading. Written
      exactly as the source spells it (including any leading relative dots
      for Python, or a leading `./`/`../` for JS/TS) — never normalized.
    """

    line: int
    """1-indexed line the statement starts on."""

    end_line: int
    """1-indexed line the statement ends on (inclusive) — equal to `line`
    for a single-line statement, greater for a parenthesized/multi-line one.
    """

    module: str | None
    names: tuple[ImportedName, ...] = field(default_factory=tuple)
    """Empty for a side-effect-only import that binds no name at all (JS/TS
    `import "./polyfill"`) — rendered as a bare module-path line, no bullets.
    """

    is_wildcard: bool = False
    """True for `from x import *` (Python) / `import pkg.*;` (Java) — always
    implies `module is not None` and `names == ()`. JS/TS has no equivalent
    syntax (its closest construct, `import * as ns from "m"`, binds the real
    name `ns` and is represented as a normal `names` entry, not a wildcard —
    see `ImportedName`'s own docstring)."""


@dataclass(frozen=True, slots=True)
class ImportBlockReplacement:
    """One collapsed import block, ready for a stage to splice into a
    `ContentMask` — never constructed directly by a stage; always the output
    of `import_collapse.collapse_import_runs()`.
    """

    start_line: int
    """1-indexed line of the run's first statement — the line a stage
    rewrites to `text` (see `import_collapse.py`'s module docstring for why
    this mirrors `group_repeated`'s "reuse the first line as the summary"
    technique rather than inserting a new line)."""

    end_line: int
    """1-indexed line of the run's last statement (inclusive) — every line
    from `start_line + 1` through `end_line` is COMPRESSed by the stage."""

    text: str
    """The full replacement block, as a single string containing embedded
    newlines (`"Imports (N)\\n\\nStandard library:\\n- os\\n..."`) — rendered
    once by `import_collapse.render_import_block()`, never touched again."""
