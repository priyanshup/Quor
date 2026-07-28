"""RepoProfile — the frozen Pydantic model produced by `quor map`.

Every field is deterministic, given the same repo state — this is the data
contract `render.py` turns into Markdown/JSON and `profiler.py` populates.
No field here is ever invented prose; every non-trivial fact carries its
own evidence (a file path, plus what pattern/marker produced it), matching
the transparency bar `quor explain` already sets for compression decisions
(Anti-Goal #10) applied to detection decisions instead.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DetectedItem(BaseModel):
    """One detected fact (a framework, build system, CI system, ...).

    `category` is a plain `str`, not `detectors.model.DetectorCategory`,
    deliberately: this module is the pure data layer for the whole
    `repo_profile` package and must not import the detector-rule subsystem
    (which itself imports this module for its own return type) — keeping
    the dependency one-directional avoids a circular import.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    category: str
    evidence: list[str] = Field(default_factory=list)
    """Each entry is "<path> — <human-readable reason>" — always traceable
    to a real file in the repo, never invented."""


class LanguageStat(BaseModel):
    """One language's share of the repository, by file count."""

    model_config = ConfigDict(frozen=True)

    language: str
    file_count: int
    percentage: float
    """0..100, relative to the total count of files with a recognized
    language extension (not every file in the repo — config/data files
    with no programming-language extension would otherwise dilute every
    language's share for no useful reason)."""


class EntryPoint(BaseModel):
    """One detected program entry point."""

    model_config = ConfigDict(frozen=True)

    target: str
    """The entry point itself — a module:function reference, a script
    path, or a binary name, depending on what produced it."""

    evidence: str
    """Where this entry point came from — always a file path plus a short
    reason (e.g. "pyproject.toml [project.scripts]")."""


class ServiceModule(BaseModel):
    """One subdirectory that carries its own manifest file — the standard
    monorepo heuristic (a subdirectory that could be built/versioned/
    deployed independently, evidenced by owning a recognized manifest)."""

    model_config = ConfigDict(frozen=True)

    path: str
    manifest: str
    """The manifest file basename that qualified this directory (e.g.
    "package.json", "pyproject.toml")."""


class ImportantDirectory(BaseModel):
    """One well-known top-level directory that exists in this repo."""

    model_config = ConfigDict(frozen=True)

    path: str
    file_count: int


class RepoStatistics(BaseModel):
    """Cheap, deterministic aggregate counts about the repo."""

    model_config = ConfigDict(frozen=True)

    total_files: int
    total_directories: int
    primary_language: str | None
    git_commit_count: int | None
    """None when the repo has no `.git` directory, or `git` itself is
    unavailable — never a fabricated 0."""


class RepoProfile(BaseModel):
    """The complete, deterministic repository orientation profile."""

    model_config = ConfigDict(frozen=True)

    root: str
    """The scanned root, as a POSIX path — for context in the rendered
    output, never used for identity/comparison."""

    languages: list[LanguageStat] = Field(default_factory=list)
    frameworks: list[DetectedItem] = Field(default_factory=list)
    build_systems: list[DetectedItem] = Field(default_factory=list)
    package_managers: list[DetectedItem] = Field(default_factory=list)
    test_frameworks: list[DetectedItem] = Field(default_factory=list)
    ci_systems: list[DetectedItem] = Field(default_factory=list)
    databases: list[DetectedItem] = Field(default_factory=list)
    containerization: list[DetectedItem] = Field(default_factory=list)
    configuration_files: list[DetectedItem] = Field(default_factory=list)
    entry_points: list[EntryPoint] = Field(default_factory=list)
    services: list[ServiceModule] = Field(default_factory=list)
    important_directories: list[ImportantDirectory] = Field(default_factory=list)
    lockfiles: list[str] = Field(default_factory=list)
    statistics: RepoStatistics
    notes: list[str] = Field(default_factory=list)
    """Scope/limitation notes about this specific scan (e.g. "not a git
    repository — used filesystem walk fallback") — never a compressed/
    invented summary, just deterministic facts about how the scan itself
    was performed."""
