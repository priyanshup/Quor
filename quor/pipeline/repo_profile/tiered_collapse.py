"""R-08 (Graph-Distance AST Tiering) — varies compression depth by
dependency hop-distance from a focal file, instead of collapsing every
file in a multi-file payload the same way.

Tier contract:
  0-hop (focal file):             full, unmodified content.
  1-hop (direct import/importer): function/method bodies collapsed to
                                   signature + docstring — reuses
                                   `ast_summarize.registry.get_analyzer()`,
                                   the exact same body-line-selection
                                   machinery `python_ast_summarize.py`/
                                   `code_ast_summarize.py` already use for
                                   CLI/Bash output, already generic across
                                   all 8 registered languages. Not a
                                   reimplementation.
  2-hop+ (transitive):            collapsed to a bare outline — one line
                                   per top-level class/interface/struct/
                                   trait/enum, name only, no members.

Cross-file coherence (requirement 2, Python only — see
`type_references.py`'s own docstring for why): every 1-hop Python file's
kept signatures are scanned for custom type names via
`type_references.referenced_type_names()`. Any such name that matches a
2-hop+ file's own top-level class/interface name gets that one
declaration *escalated* out of the one-line outline into its full
signature form (class line + bases, each method's signature line, `...`
in place of each body — the PEP 484 stub-file convention for "declared,
not implemented") — everything else in that file stays a bare one-liner.

Fail-open per file (requirement 3): any file whose content can't be
read, or whose AST can't be parsed, falls back to its own full,
unmodified content rather than raising or silently vanishing from the
payload — Quor's existing "invalid syntax fails open" policy (ADR-018),
applied per file instead of per whole-pipeline run, since one malformed
file in a multi-file payload must never take the other files down with
it.

Non-Python languages get the 1-hop signature tier (fully supported, via
`get_analyzer()`) but only a coarse container-name-only outline at 2-hop+
— `declaration_model.py`'s `Declaration` (needed for a full method-level
outline) and `type_references.py` (needed for cross-file coherence) are
both Python-only today, the same scope every declaration-diffing/
type-annotation capability in this codebase already has.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from quor.pipeline.ast_summarize.declaration_model import Declaration
from quor.pipeline.ast_summarize.python import extract_declarations_python
from quor.pipeline.ast_summarize.registry import (
    EXTENSION_TO_LANGUAGE,
    get_analyzer,
    get_symbol_extractor,
    is_language_available,
)
from quor.pipeline.repo_profile.graph_distance import DEFAULT_MAX_HOPS, compute_hop_distances
from quor.pipeline.repo_profile.intel_model import FileIntelligenceEntry
from quor.pipeline.repo_profile.type_references import referenced_type_names
from quor.tracking.db import count_tokens

TIER_FOCUS = "focus"
TIER_SIGNATURES = "signatures"
TIER_OUTLINE = "outline"

_CONTAINER_KINDS = frozenset({"class", "interface", "struct", "trait", "enum"})
"""`SymbolKind`/`Declaration.kind` values treated as a "type definition"
for outline rendering and cross-file coherence — the ticket's own "class,
interface, and exported struct" language, generalized to every
container-shaped kind this codebase's AST layer already recognizes."""


@dataclass(frozen=True)
class TieredFileResult:
    path: str
    hop: int
    tier: str
    content: str
    original_lines: int
    rendered_lines: int
    original_tokens: int
    rendered_tokens: int
    fallback_reason: str | None = None
    """Set (fail-open, requirement 3) when this file's tiered collapse
    could not be performed and `content` is its full, unmodified source
    instead — e.g. a syntax error, or no AST support for its language at
    the tier this file landed on."""


@dataclass(frozen=True)
class TieredContextResult:
    focal_file: str
    files: list[TieredFileResult] = field(default_factory=list)
    preserved_types: list[str] = field(default_factory=list)
    """Type names escalated out of a 2-hop+ file's one-line outline into
    full form because a 1-hop file's kept signature references them
    (requirement 2)."""

    @property
    def original_tokens(self) -> int:
        return sum(f.original_tokens for f in self.files)

    @property
    def rendered_tokens(self) -> int:
        return sum(f.rendered_tokens for f in self.files)


def _read_source(abs_path: Path) -> str | None:
    try:
        return abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def render_tiered_context(
    root: Path,
    entries: dict[str, FileIntelligenceEntry],
    focal_file: str,
    *,
    max_hops: int = DEFAULT_MAX_HOPS,
) -> TieredContextResult:
    """Render every file within `max_hops` of `focal_file` at its
    hop-appropriate tier. `focal_file` is a repo-relative POSIX path — the
    same shape `compute_hop_distances()` and `FileIntelligenceEntry` keys
    already use.

    Two passes over the hop set, deliberately in this order: pass one
    renders the 0-hop and 1-hop tiers and collects every custom type name
    their kept signatures reference (`referenced_names`); pass two renders
    2-hop+ outlines using that now-complete set, so cross-file coherence
    (requirement 2) never depends on iteration order within a single pass.
    """
    hops = compute_hop_distances(entries, focal_file, max_hops=max_hops)
    ordered = sorted(hops.items(), key=lambda kv: (kv[1], kv[0]))

    near_tier_files: list[TieredFileResult] = []
    referenced_names: set[str] = set()

    for path, hop in ordered:
        if hop >= 2:
            continue
        source = _read_source(root / path)
        if source is None:
            continue
        if hop == 0:
            near_tier_files.append(_focus_tier(path, source))
            continue

        language = EXTENSION_TO_LANGUAGE.get((root / path).suffix.lower())
        result = _signatures_tier(path, source, language)
        near_tier_files.append(result)
        if language == "python" and result.fallback_reason is None:
            referenced_names |= _collect_referenced_type_names(source)

    outline_files: list[TieredFileResult] = []
    preserved: set[str] = set()
    for path, hop in ordered:
        if hop < 2:
            continue
        source = _read_source(root / path)
        if source is None:
            continue
        language = EXTENSION_TO_LANGUAGE.get((root / path).suffix.lower())
        result, preserved_here = _outline_tier(path, hop, source, language, referenced_names)
        outline_files.append(result)
        preserved |= preserved_here

    return TieredContextResult(
        focal_file=focal_file,
        files=[*near_tier_files, *outline_files],
        preserved_types=sorted(preserved),
    )


def _focus_tier(path: str, source: str) -> TieredFileResult:
    lines = len(source.splitlines())
    tokens = count_tokens(source)
    return TieredFileResult(path, 0, TIER_FOCUS, source, lines, lines, tokens, tokens)


def _signatures_tier(path: str, source: str, language: str | None) -> TieredFileResult:
    """1-hop: full body -> signature + docstring, via the existing
    `get_analyzer()` body-line selector. `Decision.COMPRESS`'s own render
    convention (see `quor/pipeline/stages/_utils.py`) is "drop the line
    entirely, keep everything else verbatim" — mirrored here directly
    rather than routed through a full `ContentMask`/`Pipeline`, since
    there's no filter-config/plugin/tee concern at the single-file level;
    those apply once, to the whole assembled multi-file payload (QB-114
    parity), not per file."""
    original_lines = len(source.splitlines())
    original_tokens = count_tokens(source)
    if language is None or not is_language_available(language):
        return TieredFileResult(
            path, 1, TIER_SIGNATURES, source, original_lines, original_lines,
            original_tokens, original_tokens,
            fallback_reason="no AST analyzer for this language",
        )
    analyzer = get_analyzer(language)
    if analyzer is None:
        return TieredFileResult(
            path, 1, TIER_SIGNATURES, source, original_lines, original_lines,
            original_tokens, original_tokens,
            fallback_reason="no AST analyzer for this language",
        )
    try:
        compress_lines = analyzer(source)
    except Exception:  # noqa: BLE001 — fail-open: malformed source keeps its full content
        return TieredFileResult(
            path, 1, TIER_SIGNATURES, source, original_lines, original_lines,
            original_tokens, original_tokens,
            fallback_reason="AST parse failed",
        )
    kept = [line for i, line in enumerate(source.splitlines(), start=1) if i not in compress_lines]
    rendered = "\n".join(kept)
    return TieredFileResult(
        path, 1, TIER_SIGNATURES, rendered, original_lines, len(kept),
        original_tokens, count_tokens(rendered),
    )


def _collect_referenced_type_names(source: str) -> set[str]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            names |= referenced_type_names(node)
    return names


def _outline_tier(
    path: str,
    hop: int,
    source: str,
    language: str | None,
    referenced_names: set[str],
) -> tuple[TieredFileResult, set[str]]:
    original_lines = len(source.splitlines())
    original_tokens = count_tokens(source)

    if language == "python":
        try:
            decls = extract_declarations_python(source)
        except (SyntaxError, ValueError):
            return (
                TieredFileResult(
                    path, hop, TIER_OUTLINE, source, original_lines, original_lines,
                    original_tokens, original_tokens, fallback_reason="AST parse failed",
                ),
                set(),
            )
        rendered, preserved = _render_python_outline(decls, referenced_names)
        return (
            TieredFileResult(
                path, hop, TIER_OUTLINE, rendered, original_lines, len(rendered.splitlines()),
                original_tokens, count_tokens(rendered),
            ),
            preserved,
        )

    if language is not None and is_language_available(language):
        extractor = get_symbol_extractor(language)
        if extractor is not None:
            try:
                symbols = extractor(source)
            except Exception:  # noqa: BLE001 — fail-open: fall through to the full-content case below
                symbols = None
            if symbols is not None:
                containers = [s for s in symbols if s.kind in _CONTAINER_KINDS]
                if containers:
                    rendered = "\n".join(f"{s.kind} {s.name}" for s in containers)
                    return (
                        TieredFileResult(
                            path, hop, TIER_OUTLINE, rendered, original_lines,
                            len(containers), original_tokens, count_tokens(rendered),
                            fallback_reason="cross-file type preservation is Python-only",
                        ),
                        set(),
                    )

    return (
        TieredFileResult(
            path, hop, TIER_OUTLINE, source, original_lines, original_lines,
            original_tokens, original_tokens, fallback_reason="no AST support for this language",
        ),
        set(),
    )


def _render_python_outline(
    decls: list[Declaration], referenced_names: set[str]
) -> tuple[str, set[str]]:
    """One line per top-level class/interface-shaped declaration by
    default (bare kind + name, no members) — except a declaration whose
    name is in `referenced_names` (requirement 2), which is escalated to
    its full signature form instead: its own declaration line (with
    bases), then each of its methods' signature lines with `...` bodies."""
    top_level = [d for d in decls if d.parent is None and d.kind in _CONTAINER_KINDS]
    by_parent: dict[str, list[Declaration]] = {}
    for d in decls:
        if d.parent is not None:
            by_parent.setdefault(d.parent, []).append(d)

    lines: list[str] = []
    preserved: set[str] = set()
    for decl in top_level:
        if decl.name not in referenced_names:
            lines.append(f"{decl.kind} {decl.name}")
            continue
        preserved.add(decl.name)
        lines.append(_python_class_signature(decl.node))
        for member in by_parent.get(decl.name, []):
            if isinstance(member.node, ast.FunctionDef | ast.AsyncFunctionDef):
                lines.append(f"    {_python_def_signature(member.node)}")
                lines.append("        ...")
            elif member.kind in _CONTAINER_KINDS and isinstance(member.node, ast.ClassDef):
                lines.append(f"    {_python_class_signature(member.node)}")
                lines.append("        ...")
    return "\n".join(lines), preserved


def _python_class_signature(node: ast.ClassDef) -> str:
    bases = [ast.unparse(b) for b in node.bases]
    base_str = f"({', '.join(bases)})" if bases else ""
    return f"class {node.name}{base_str}:"


def _python_def_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    ret = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
    return f"{prefix} {node.name}({ast.unparse(node.args)}){ret}:"


_TIER_HEADER_LABEL: dict[str, str] = {
    TIER_FOCUS: "focus, 0-hop, full",
    TIER_SIGNATURES: "1-hop, signatures",
    TIER_OUTLINE: "outline",
}


def render_tiered_payload(result: TieredContextResult) -> str:
    """Assemble a `TieredContextResult` into one text payload: one
    `### path (tier label)` section per file, in the same 0/1/2-hop order
    `render_tiered_context()` produced them, plus a trailing note listing
    any cross-file-preserved types (requirement 2's visible proof it
    actually happened)."""
    sections: list[str] = []
    for f in result.files:
        label = _TIER_HEADER_LABEL[f.tier]
        if f.tier == TIER_OUTLINE:
            label = f"{f.hop}-hop, {label}"
        if f.fallback_reason:
            label = f"{label}, fallback: {f.fallback_reason}"
        sections.append(f"### {f.path} ({label})\n{f.content}")

    payload = "\n\n".join(sections)
    if result.preserved_types:
        payload += (
            "\n\n# Cross-file types preserved (referenced by a 1-hop signature): "
            + ", ".join(result.preserved_types)
        )
    return payload
