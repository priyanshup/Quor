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

_TEST_DIR_NAMES = frozenset({"test", "tests", "__tests__", "spec", "specs"})
_FIXTURE_DIR_NAMES = frozenset({"fixtures", "fixture", "data", "testdata", "test-data", "__fixtures__"})


def _is_test_fixture_path(rel_path: str) -> bool:
    """Naming-convention evidence only — same "convention as proof" rigor
    `languages.py`'s `is_vendor_or_build_path()` and `intel.py`'s
    `_is_test_path()` already use, not a content or size guess. True if
    `rel_path` sits under a fixture/mock-data directory nested inside a
    test directory (e.g. `tests/fixtures/...`, `tests/data/...`).

    QB-122: a fixture repo (a mock `package.json`, `go.mod`,
    `requirements.txt`, lockfile, ...) is a real, git-tracked file used to
    exercise this very package's detectors in tests — with no exclusion it
    reads as evidence about the *project itself*, so `quor map` run on
    Quor's own repo reported Flask/Go/pnpm as this project's stack. Scoped
    to a fixture dir specifically nested under a test dir, not any
    directory literally named "data" or "fixtures" anywhere in the repo,
    so real project directories with those names are untouched.
    """
    parts = [p.lower() for p in PurePosixPath(rel_path).parts[:-1]]
    for i, part in enumerate(parts):
        if part in _TEST_DIR_NAMES and any(p in _FIXTURE_DIR_NAMES for p in parts[i + 1 :]):
            return True
    return False


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
    # QB-122: language/framework/build-system/lockfile detection is scoped
    # to `detection_files` (fixture dirs excluded) — see
    # `_is_test_fixture_path()`. Everything else (statistics, entry points,
    # services, important directories) keeps using the full `files` list;
    # only these four signals are the ones a committed test fixture (a mock
    # manifest/lockfile under `tests/fixtures/...`) can silently impersonate.
    detection_files = [f for f in files if not _is_test_fixture_path(f)]

    languages = compute_language_stats(detection_files)
    statistics = compute_statistics(root, files, languages)

    registry = DetectorRegistry(project_root=root)
    detected = registry.detect(detection_files, root)

    lockfiles = sorted(f for f in detection_files if PurePosixPath(f).name in _LOCKFILE_BASENAMES)

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
