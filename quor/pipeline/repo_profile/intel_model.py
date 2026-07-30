"""Data contracts for Automatic Repository Intelligence (QB-072).

Plain, frozen dataclasses throughout — every field here is computed
internally from a repo walk, a filesystem stat, or a previous cache write,
never user input, mirroring `walk.py`/`symbols_model.py`/`graph_model.py`'s
identical "no Pydantic needed" reasoning (see `model.py`'s docstring for
the one case, `RepoProfile`, where Pydantic is used instead, and why).

`RepoIntelState` is the on-disk state file `intel_store.py` persists per
repository: repository identity (`repo_root`), the last git HEAD and file
count seen, when the repo was last scanned, when a build last completed,
and a per-file fingerprint table `intel_diff.py` uses to detect exactly
which files changed since that scan — the basis for "rebuild only affected
files" instead of a full repo walk-and-parse every time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from quor.pipeline.repo_profile.graph_model import RepoDependencyGraph
    from quor.pipeline.repo_profile.model import RepoProfile
    from quor.pipeline.repo_profile.symbols_model import RepoSymbolIndex

CACHE_SCHEMA_VERSION = 1
"""Bumped whenever `RepoIntelState`'s or the cached artifacts' on-disk shape
changes incompatibly — independent of `quor.__version__`, which already
triggers its own rebuild (`BuildAction.VERSION_REBUILD`) for any version
change at all. This lets a future patch release change the cache's on-disk
format without needing to also bump the package version just to invalidate
old caches."""


@dataclass(frozen=True, slots=True)
class FileFingerprint:
    """One file's cheap identity check, as of the scan that recorded it.

    `size`/`mtime_ns` are the fast-path check `intel_diff.py` uses to skip
    re-hashing a file that plainly hasn't changed (same trick `make`,
    rsync, and watchman all use) — `content_hash` is only (re)computed when
    size or mtime_ns differs from the stored fingerprint, or the file is
    new. A file whose content changes without its size or mtime_ns changing
    (e.g. deliberately backdated) is the one documented edge case this
    fast path can miss — see `intel_diff.py`'s own module docstring.
    """

    size: int
    mtime_ns: int
    content_hash: str
    """SHA-256 hex digest of the file's raw bytes."""


@dataclass(frozen=True, slots=True)
class RepoDiff:
    """The set of files that changed between two scans of the same repo."""

    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    renamed: list[tuple[str, str]] = field(default_factory=list)
    """`(old_path, new_path)` pairs — detected when a deleted path and an
    added path share an identical `content_hash` (see `intel_diff.py`).
    A renamed-and-modified file is reported as a plain `deleted` + `added`
    pair instead (its content hash no longer matches), which is correct:
    it needs the same re-extraction a modified file would, and the old
    path's entries still need removing — `renamed`'s only purpose is to
    let unchanged content skip re-extraction after a pure rename."""

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.modified or self.deleted or self.renamed)

    @property
    def reextraction_paths(self) -> list[str]:
        """Paths that genuinely need re-parsing: added and modified only.

        Deliberately excludes a rename's new path — a pure rename's content
        is byte-identical to what the old path already had cached, so
        `intel.py`'s refresh functions *relocate* that cached entry (keyed
        by path) to the new path instead of re-extracting it. An earlier
        version of this property folded the rename's new path in here,
        which silently defeated that optimization (a rename was quietly
        re-parsed exactly as if it were a fresh file) — a real bug caught
        by `test_repo_intel.py::TestFileRenamed::test_pure_rename_is_not_reparsed`
        monkeypatching the *actual* call site (`intel.py`'s own imported
        name), not the source module's — see that test's own note on why
        patching `symbols.py`/`graph.py` directly does not intercept
        `intel.py`'s calls."""
        return sorted({*self.added, *self.modified})


@dataclass(frozen=True, slots=True)
class RepoIntelState:
    """The deterministic, persisted state file for one repository's
    Automatic Repository Intelligence cache (QB-072)."""

    schema_version: int
    quor_version: str
    repo_root: str
    """Repository identity — the resolved, absolute POSIX path of the
    repository root this state describes."""

    git_head: str | None
    """`git -C <root> rev-parse HEAD`'s output, or `None` when `root` is
    not a git repository (or `git` itself is unavailable) — informational
    only; file fingerprints, not this field, drive diffing, since HEAD
    alone can't see uncommitted working-tree changes."""

    file_count: int
    last_scan_timestamp: str
    """ISO-8601 UTC timestamp of when this state was last written."""

    last_completed_build: str
    """ISO-8601 UTC timestamp of the last time map/symbols/graph all
    finished rebuilding successfully (a cache-hit run that changed nothing
    does not advance this field — only an actual (re)build does)."""

    fingerprints: dict[str, FileFingerprint] = field(default_factory=dict)


BuildAction = Literal[
    "onboarded",
    "cache_hit",
    "incremental",
    "full_rebuild",
    "version_rebuild",
    "corrupted_rebuild",
    "forced_rebuild",
]
"""What `ensure_repo_intelligence()` actually did this call:
- `onboarded` — no prior state for this repository; first-time build.
- `cache_hit` — state existed, nothing changed; cached artifacts reused as-is.
- `incremental` — state existed, a non-empty `RepoDiff` was rebuilt.
- `full_rebuild` — the walked file list changed so extensively (see
  `intel.py`'s own threshold) that a full rebuild was cheaper/safer than
  reconciling the diff.
- `version_rebuild` — `quor.__version__` (or `CACHE_SCHEMA_VERSION`)
  differs from the cached state's.
- `corrupted_rebuild` — the cache existed but could not be read back.
- `forced_rebuild` — the caller passed `--rebuild`.
"""


@dataclass(frozen=True, slots=True)
class RepoIntelligence:
    """The bundle `ensure_repo_intelligence()` returns: all three
    repository-analysis artifacts together, plus what happened to produce
    them — `quor map`/`quor symbols`/`quor graph` each pull out just their
    own field to render (see each command's own module docstring)."""

    profile: RepoProfile
    symbols: RepoSymbolIndex
    graph: RepoDependencyGraph
    action: BuildAction
    elapsed_seconds: float
    diff: RepoDiff | None
    """The `RepoDiff` that drove this call, or `None` for `cache_hit` (no
    diff was needed to answer "nothing changed") and every full-rebuild-
    shaped action (diffing against no prior state is not a meaningful
    diff)."""

    files_scanned: int = 0
    """Total files in this call's repository walk — the denominator for
    `cache_hit_ratio`."""

    files_reextracted: int = 0
    """How many of `files_scanned` actually had their symbols/relationships
    (re-)parsed this call: 0 for `cache_hit`, every walked file for any
    full-rebuild-shaped action, or exactly `len(diff.reextraction_paths)`
    for `incremental` — a plain, observable proxy for "how much re-parsing
    work this call actually did," independent of how many of those files
    happen to map to an `ast_summarize`-registered language (deliberately
    not narrowed further, so the metric stays simple and stable rather than
    chasing an exact parse count)."""

    @property
    def cache_hit_ratio(self) -> float:
        """Fraction of `files_scanned` that did **not** need re-parsing this
        call — `1.0` for a true cache hit (and for the degenerate empty-repo
        case, nothing to do), `0.0` for any full rebuild, and somewhere in
        between for a partial incremental rebuild."""
        if self.files_scanned == 0:
            return 1.0
        return 1.0 - (self.files_reextracted / self.files_scanned)


FileImportance = Literal["High", "Medium", "Low"]
"""A file's dependency-graph connectivity rank among every file the last
scan walked, tertile-split — the same three values `explorer_model.Importance`
already defines for `quor explore`'s per-file output. Redefined here (not
imported) rather than adding a new cross-package import edge between two
sibling business-logic modules; `dashboard.py::importance_tiers()` is the
one shared implementation both call (QB-079)."""

FileKind = Literal["source", "test", "generated", "configuration"]
"""A file's deterministically classified role (QB-079) — evidence-backed
only, computed by `intel.py::_classify_kind()` from naming conventions, a
bounded content-marker scan, and `RepoProfile.configuration_files`/
`lockfiles`, never guessed. Deliberately excludes a "library"/"application"
split: there is no existing deterministic signal for that distinction in
this codebase today (`RepoProfile.services` covers too few repositories to
be a real classifier) — add it only if repository intelligence gains a
genuine deterministic source for it (explicit package/deployment metadata,
not a path-shape guess)."""

FILE_INTELLIGENCE_VERSION = 2
"""Independent of `CACHE_SCHEMA_VERSION` above — `file_intelligence.json`'s
on-disk shape can change without forcing a rebuild of the state/profile/
symbol/graph cache files, and vice versa. Embedded as a top-level `version`
key in `file_intelligence.json` itself; `intel_store.load_file_intelligence()`
treats a mismatch exactly like "file missing", so `intel.py`'s existing
backfill-on-next-touch logic handles a version bump the same way as a
first-time build — no coordination with `CACHE_SCHEMA_VERSION` needed.

Bumped 1 -> 2 by QB-080: `FileIntelligenceEntry` gained `imported_files`, its
first indexed *relationship* data (every prior field was a scalar or a
capped name list) — a genuinely new kind of content, not just another
scalar, so existing caches need regenerating rather than silently reading
back with a missing field."""


@dataclass(frozen=True, slots=True)
class FileIntelligenceEntry:
    """One file's build-time-computed facts (QB-079) — a general-purpose
    per-file intelligence cache, not a Read-hook-specific artifact. The
    Read hook (`quor/adapters/claude_read.py`) is this cache's first
    consumer, not its only intended one; `quor explore`/`quor repo` and
    future consumers (an editor extension, `quor explain`) can read the
    same file in O(1) instead of recomputing these facts. Every field is
    either copied from an already-computed artifact the same build pass
    produces, or a cheap deterministic classification with real evidence
    — never a guess, and never derived by parsing a file this module
    would not already have parsed for `quor symbols`/`quor graph`.

    Kept deliberately small: the compressed Read output already carries
    the full AST summary, so this is a compact pointer *to* the repo, not
    a second symbol database — `top_symbols` stays capped at 5, with no
    per-symbol detail (signatures, decorators, inheritance) stored here.
    """

    language: str
    """`ast_summarize` language key (e.g. `"python"`) for an AST-covered
    file, `languages.py`'s display name for a recognized-but-unparsed
    file, or `"unknown"` if the extension isn't recognized at all."""

    kind: FileKind

    importance: FileImportance = "Low"
    """Meaningful for every walked file (computed over the whole repo's
    connectivity, not just AST-covered files)."""

    imports: int = 0
    """Resolved `import`-kind edges with this file as source. Always `0`
    for a file with no `graph_facts` entry (not AST-covered, or failed to
    parse) — never estimated."""

    imported_by: int = 0
    """Resolved `import`-kind edges with this file as target."""

    entry_point: bool = False
    """`Symbol.is_entry_point` for any symbol in this file — `False` for
    any file with no `symbol_files` entry."""

    top_symbols: list[str] = field(default_factory=list)
    """Public, top-level declaration names only (methods excluded) —
    capped at 5, declaration-line order, de-duplicated. Empty for any file
    with no `symbol_files` entry."""

    imported_files: list[str] = field(default_factory=list)
    """Repo-relative POSIX paths of every file this file has a resolved
    `import`-kind edge targeting (QB-080) — deduplicated, sorted for
    determinism, **not capped** (a hard cap would silently blind `quor
    search`'s dependency-evidence tier for exactly the well-connected "hub"
    files that tier is most useful for). File-level only: never a
    call/inherit/export edge, never a `target_symbol` — this must stay a
    plain list of *files*, not grow into anything requiring
    `symbol_facts.json`/`graph_facts.json` at read time, or it defeats
    QB-080's own latency goal (search must never scale with repo size the
    way loading those two files does).

    Deliberately one direction only — the reverse ("imported by") is never
    persisted a second time here; `imported_by` above already gives the
    scalar count, and `quor search`'s own `search.py` derives the full
    reverse path list in memory, once per invocation, by inverting this
    field across the whole loaded cache."""

    size: int = 0
    mtime_ns: int = 0
    """This file's own fingerprint copy — a consumer needing only a
    staleness check never has to load the (much larger) `state.json` to
    get it."""
