"""On-disk persistence for Automatic Repository Intelligence (QB-072).

Cache location: `platformdirs.user_data_dir("quor") / "repo_intel" / <repo_key>`
— the same `user_data_dir("quor")` root every other cache-like Quor
subsystem already uses (`tracking/db.py`'s SQLite file, `pipeline/tee.py`,
`pipeline/onboarding.py`), not a new `user_cache_dir` root nothing else in
the codebase uses. `<repo_key>` is a short SHA-256 fingerprint of the
repository's resolved absolute path, so two different repositories never
collide and the same repository always maps back to the same directory
regardless of which command (`quor map`/`quor symbols`/`quor graph`)
touches it first.

Four files live under that directory:
- `state.json` — `RepoIntelState` (identity, versions, git HEAD, file
  count, timestamps, per-file fingerprints).
- `profile.json` — the cached `RepoProfile` (`quor map`'s own output),
  cached verbatim because reassembling it cheaply from partial per-file
  facts isn't possible (its fields are repo-wide aggregates, not a
  per-file merge — see `intel.py`'s own module docstring) — a genuine
  cache-hit returns this file's contents completely unchanged.
- `symbol_facts.json` — every file's `FileSymbols` plus which paths failed
  to parse; `RepoSymbolIndex` itself is always cheaply *reassembled* from
  this (via `symbols.assemble_symbol_index()`), never cached as a second,
  potentially-drifting copy.
- `graph_facts.json` — every file's raw `FileFacts` plus which paths
  failed to parse; `RepoDependencyGraph` itself is always cheaply
  *reassembled* from this (via `graph.assemble_graph()`), for the same
  reason.

**Fail-open, corruption-as-missing.** Every read function returns `None`
on any parse/shape error (mirrors `plugin_loader.py::_read_cache()`'s
identical "unreadable cache, will rescan" contract) rather than raising —
`intel.py` treats a `None` from any of these exactly like "no cache at
all," forcing a full rebuild rather than propagating a corrupt on-disk
file into a wrong in-memory result.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

import orjson
import platformdirs

from quor.atomic_io import write_json_atomic
from quor.pipeline.ast_summarize.relationship_model import Relationship
from quor.pipeline.ast_summarize.symbol_model import Symbol
from quor.pipeline.repo_profile.graph import FileFacts
from quor.pipeline.repo_profile.intel_model import FileFingerprint, RepoIntelState
from quor.pipeline.repo_profile.model import RepoProfile
from quor.pipeline.repo_profile.symbols_model import FileSymbols

_CACHE_ROOT_SUBDIR = "repo_intel"
_STATE_FILENAME = "state.json"
_PROFILE_FILENAME = "profile.json"
_SYMBOL_FACTS_FILENAME = "symbol_facts.json"
_GRAPH_FACTS_FILENAME = "graph_facts.json"


def repo_key(root: Path) -> str:
    """Short, stable, filesystem-safe identifier for `root` — a SHA-256
    fingerprint of its resolved absolute POSIX path, so the same repository
    always maps to the same cache directory across processes/sessions
    regardless of cwd or path casing quirks Windows may introduce."""
    return hashlib.sha256(root.resolve().as_posix().encode("utf-8")).hexdigest()[:16]


def cache_dir(root: Path) -> Path:
    return Path(platformdirs.user_data_dir("quor")) / _CACHE_ROOT_SUBDIR / repo_key(root)


# ---------------------------------------------------------------------------
# state.json
# ---------------------------------------------------------------------------


def state_exists(root: Path) -> bool:
    """Whether a state file exists at all for `root` — distinct from
    `load_state()` returning `None`, which also happens when the file
    exists but is corrupted. `intel.py` uses this distinction to choose
    between the one-time onboarding banner (no file at all) and the
    "cache was corrupted" banner (file present, unreadable)."""
    return (cache_dir(root) / _STATE_FILENAME).exists()


def load_state(root: Path) -> RepoIntelState | None:
    path = cache_dir(root) / _STATE_FILENAME
    if not path.exists():
        return None
    try:
        data = orjson.loads(path.read_bytes())
        fingerprints = {
            p: FileFingerprint(size=f["size"], mtime_ns=f["mtime_ns"], content_hash=f["content_hash"])
            for p, f in data["fingerprints"].items()
        }
        return RepoIntelState(
            schema_version=data["schema_version"],
            quor_version=data["quor_version"],
            repo_root=data["repo_root"],
            git_head=data["git_head"],
            file_count=data["file_count"],
            last_scan_timestamp=data["last_scan_timestamp"],
            last_completed_build=data["last_completed_build"],
            fingerprints=fingerprints,
        )
    except (orjson.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def save_state(root: Path, state: RepoIntelState) -> None:
    data: dict[str, Any] = {
        "schema_version": state.schema_version,
        "quor_version": state.quor_version,
        "repo_root": state.repo_root,
        "git_head": state.git_head,
        "file_count": state.file_count,
        "last_scan_timestamp": state.last_scan_timestamp,
        "last_completed_build": state.last_completed_build,
        "fingerprints": {p: asdict(f) for p, f in state.fingerprints.items()},
    }
    write_json_atomic(cache_dir(root) / _STATE_FILENAME, data)


# ---------------------------------------------------------------------------
# profile.json
# ---------------------------------------------------------------------------


def load_profile(root: Path) -> RepoProfile | None:
    path = cache_dir(root) / _PROFILE_FILENAME
    if not path.exists():
        return None
    try:
        return RepoProfile.model_validate(orjson.loads(path.read_bytes()))
    except (orjson.JSONDecodeError, ValueError):
        return None


def save_profile(root: Path, profile: RepoProfile) -> None:
    write_json_atomic(cache_dir(root) / _PROFILE_FILENAME, profile.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# symbol_facts.json
# ---------------------------------------------------------------------------


def load_symbol_facts(root: Path) -> tuple[dict[str, FileSymbols], set[str]] | None:
    """Return `(path -> FileSymbols, parse_failure_paths)`, or `None` on
    any miss/corruption."""
    path = cache_dir(root) / _SYMBOL_FACTS_FILENAME
    if not path.exists():
        return None
    try:
        data = orjson.loads(path.read_bytes())
        files = {
            f["path"]: FileSymbols(
                path=f["path"],
                language=f["language"],
                symbols=[Symbol(**s) for s in f["symbols"]],
            )
            for f in data["files"]
        }
        parse_failures = set(data["parse_failures"])
        return files, parse_failures
    except (orjson.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def save_symbol_facts(root: Path, files: dict[str, FileSymbols], parse_failures: set[str]) -> None:
    data = {
        "files": [asdict(f) for f in files.values()],
        "parse_failures": sorted(parse_failures),
    }
    write_json_atomic(cache_dir(root) / _SYMBOL_FACTS_FILENAME, data)


# ---------------------------------------------------------------------------
# graph_facts.json
# ---------------------------------------------------------------------------


def load_graph_facts(root: Path) -> tuple[dict[str, FileFacts], set[str]] | None:
    """Return `(path -> FileFacts, parse_failure_paths)`, or `None` on any
    miss/corruption."""
    path = cache_dir(root) / _GRAPH_FACTS_FILENAME
    if not path.exists():
        return None
    try:
        data = orjson.loads(path.read_bytes())
        facts = {
            p: FileFacts(
                language=f["language"],
                symbol_counts=Counter(f["symbol_counts"]),
                relationships=[Relationship(**r) for r in f["relationships"]],
            )
            for p, f in data["facts"].items()
        }
        parse_failures = set(data["parse_failures"])
        return facts, parse_failures
    except (orjson.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def save_graph_facts(root: Path, facts: dict[str, FileFacts], parse_failures: set[str]) -> None:
    data = {
        "facts": {
            p: {
                "language": f.language,
                "symbol_counts": dict(f.symbol_counts),
                "relationships": [asdict(r) for r in f.relationships],
            }
            for p, f in facts.items()
        },
        "parse_failures": sorted(parse_failures),
    }
    write_json_atomic(cache_dir(root) / _GRAPH_FACTS_FILENAME, data)
