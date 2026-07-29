"""`RepoDependencyGraph` — the frozen data contract `quor graph` renders
(QB-067).

Plain, frozen dataclasses, not Pydantic models — matching `symbols_model.py`/
`walk.py`'s convention, not `model.py`'s `RepoProfile`: every field here is
computed internally from a repo walk plus parsed source, never user input,
so there is no external validation boundary to enforce.

`Edge` is this package's own type, not `ast_summarize.relationship_model
.Relationship` reused directly (contrast with `symbols_model.py` reusing
`Symbol` as-is) — a `Relationship` is a raw, single-file, unresolved fact;
an `Edge` is what `graph.py`'s orchestrator produces *after* attempting
resolution against the whole repo's symbol table. They are deliberately
different shapes for a fact that changes meaning at that resolution
boundary, not the same shape reused twice.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from quor.pipeline.ast_summarize.relationship_model import RelationshipKind

__all__ = ["Edge", "RepoDependencyGraph"]


@dataclass(frozen=True)
class Edge:
    """One relationship fact, with as much cross-file resolution as could
    be established deterministically and unambiguously (QB-067) — never
    guessed. `target_file`/`target_symbol` are `None` whenever resolution
    was not attempted (e.g. C#'s `using`, which binds no addressable
    name), not attempted for this `kind` (Go's structural interfaces), or
    attempted but inconclusive (an external/stdlib import, an ambiguous
    same-file name, a dynamic/unqualified call this task's own
    conservative-resolution mandate rules out) — `target_raw` is always
    present regardless, so the fact itself is never lost even when
    unresolved.
    """

    kind: RelationshipKind
    source_file: str
    """Repo-relative POSIX path of the file this relationship was found in."""

    target_raw: str
    """The relationship's raw target text, exactly as `extract_relationships_*()`
    reported it — a module/path specifier, a base-type name, or a callee
    name. Always present, resolved or not."""

    line: int

    source_symbol: str | None = None
    """The enclosing symbol's name (a class for inherits/implements/
    exports, a function/method for overrides/calls) — `None` for a
    file-level `import`."""

    qualifier: str | None = None
    """Raw context the underlying `Relationship` carried — an import's
    local binding name, a call's `self`/`this`/alias prefix, or (for
    `overrides`) the raw superclass name the override is claimed against.
    See `relationship_model.py`'s `Relationship` docstring for the full
    per-kind meaning; carried through unresolved for display."""

    target_file: str | None = None
    """Repo-relative POSIX path of the file `target_raw` was resolved to,
    or `None` if unresolved."""

    target_symbol: str | None = None
    """The specific symbol name within `target_file` this edge resolves
    to, or `None` if unresolved (including when `target_file` itself is
    set but no specific symbol was pinpointed, e.g. an import whose
    module resolved but names no single symbol)."""


@dataclass(frozen=True)
class RepoDependencyGraph:
    """The complete, deterministic repository dependency graph."""

    root: str
    """The scanned root, as a POSIX path — context only, mirrors
    `RepoSymbolIndex.root`'s identical convention."""

    edges: list[Edge] = field(default_factory=list)
    """Every extracted relationship, resolved where possible. Sorted by
    `(source_file, line, kind, target_raw)` for stable, deterministic
    output."""

    languages_covered: list[str] = field(default_factory=list)
    languages_skipped: list[str] = field(default_factory=list)
    total_edges: int = 0
    resolved_edges: int = 0
    """Count of edges with a non-`None` `target_file` — a coarse,
    at-a-glance signal of how much of the graph is cross-file-resolved
    vs. raw-fact-only, never itself a claim of accuracy/completeness."""

    notes: list[str] = field(default_factory=list)
    """Scope/limitation notes about this specific scan — mirrors
    `RepoSymbolIndex.notes`'s identical convention."""
