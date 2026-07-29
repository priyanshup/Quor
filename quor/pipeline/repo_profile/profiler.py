"""Orchestrator for the Repository Context Profile (QB-061).

`build_profile()` is the single public entry point this whole package
exists to expose: walk the repo once, run every deterministic detection
step against that one file list, and assemble a `RepoProfile`. No LLM call,
no network access, no file content read beyond the small, bounded set each
detection step needs (manifest/config files, never arbitrary source).
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from quor.pipeline.repo_profile.detectors.registry import DetectorRegistry
from quor.pipeline.repo_profile.directories import detect_services, important_directories
from quor.pipeline.repo_profile.entry_points import detect_entry_points
from quor.pipeline.repo_profile.languages import compute_language_stats
from quor.pipeline.repo_profile.model import RepoProfile
from quor.pipeline.repo_profile.statistics import compute_statistics
from quor.pipeline.repo_profile.walk import WalkResult, walk_repository

# Well-known lockfile basenames — surfaced verbatim in RepoProfile.lockfiles
# (paths), independent of (but consistent with) package_managers.toml's
# own lockfile-based rules: this is a plain file-list filter, not a second
# detection mechanism, so it can't disagree with what package_managers
# reports — it just names the actual file(s) found.
_LOCKFILE_BASENAMES = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "bun.lockb",
        "bun.lock",
        "poetry.lock",
        "Pipfile.lock",
        "uv.lock",
        "Cargo.lock",
        "go.sum",
        "composer.lock",
        "Gemfile.lock",
        "packages.lock.json",
    }
)


def build_profile(root: Path, *, walk_result: WalkResult | None = None) -> RepoProfile:
    """Scan `root` and return its deterministic RepoProfile.

    Calling this twice against unchanged repo state returns an identical
    RepoProfile (field-for-field) — the feature's core promise.

    `walk_result` (QB-072 perf follow-up): pass an already-computed
    `WalkResult` to skip a redundant `walk_repository()` call (a `git
    ls-files` subprocess) when the caller already walked the same repo for
    another purpose in the same invocation — `intel.py`'s orchestrator does
    exactly this so a full rebuild walks the repo once, not once per
    artifact. Every existing caller that omits it gets the exact same
    behavior as before (a fresh walk), so this is purely additive.
    """
    walk_result = walk_result if walk_result is not None else walk_repository(root)
    files = walk_result.files

    languages = compute_language_stats(files)
    statistics = compute_statistics(root, files, languages)

    registry = DetectorRegistry(project_root=root)
    detected = registry.detect(files, root)

    lockfiles = sorted(f for f in files if PurePosixPath(f).name in _LOCKFILE_BASENAMES)

    notes: list[str] = []
    if not walk_result.used_git:
        notes.append(
            "Not a git repository (or git is unavailable) — used a filesystem "
            "walk with a hardcoded ignore list instead of `git ls-files`; "
            "node_modules/.venv/build artifacts may be under-filtered compared "
            "to a real git-tracked scan."
        )

    return RepoProfile(
        root=root.as_posix(),
        languages=languages,
        frameworks=detected.get("framework", []),
        build_systems=detected.get("build_system", []),
        package_managers=detected.get("package_manager", []),
        test_frameworks=detected.get("test_framework", []),
        ci_systems=detected.get("ci_system", []),
        databases=detected.get("database", []),
        containerization=detected.get("containerization", []),
        configuration_files=detected.get("configuration", []),
        entry_points=detect_entry_points(root, files),
        services=detect_services(files),
        important_directories=important_directories(files),
        lockfiles=lockfiles,
        statistics=statistics,
        notes=notes,
    )
