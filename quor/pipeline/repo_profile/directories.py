"""Important-directory and services/modules (monorepo) detection (QB-061).

Both are presence-based over the already-walked file list — no additional
filesystem access beyond what `walk.walk_repository()` already did.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from quor.pipeline.repo_profile.model import ImportantDirectory, ServiceModule

# Well-known top-level directory names worth calling out in a profile.
# Presence-only: a directory either exists (has at least one file under it
# in the walked list) or it doesn't — no content inspection.
_WELL_KNOWN_DIRS = (
    "src",
    "lib",
    "app",
    "tests",
    "test",
    "docs",
    "doc",
    "scripts",
    "examples",
    "cmd",
    "pkg",
    "internal",
    "config",
    "migrations",
    ".github",
)

# Manifest basenames that qualify a subdirectory as its own
# independently-built/versioned service or module — the standard monorepo
# heuristic (also used by Nx/Turborepo/Lerna).
_MANIFEST_BASENAMES = (
    "package.json",
    "pyproject.toml",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
)


def important_directories(files: list[str]) -> list[ImportantDirectory]:
    """Return well-known top-level directories that exist in this repo,
    each with the count of files found under it, sorted alphabetically."""
    counts: dict[str, int] = {}
    for f in files:
        parts = PurePosixPath(f).parts
        if len(parts) < 2:
            continue
        top = parts[0]
        if top in _WELL_KNOWN_DIRS:
            counts[top] = counts.get(top, 0) + 1

    return [
        ImportantDirectory(path=name, file_count=count)
        for name, count in sorted(counts.items())
    ]


def detect_services(files: list[str]) -> list[ServiceModule]:
    """Return top-level subdirectories that themselves own a recognized
    manifest file — the monorepo/multi-service heuristic. The repo root
    itself is excluded (a manifest at the root describes the whole repo,
    not a sub-service of it)."""
    services: dict[str, str] = {}
    for f in files:
        parts = PurePosixPath(f).parts
        if len(parts) < 2:
            continue
        top, rest = parts[0], parts[1:]
        if len(rest) == 1 and rest[0] in _MANIFEST_BASENAMES and top not in services:
            services[top] = rest[0]

    return [
        ServiceModule(path=path, manifest=manifest)
        for path, manifest in sorted(services.items())
    ]
