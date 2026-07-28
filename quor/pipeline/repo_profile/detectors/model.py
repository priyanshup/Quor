"""Pydantic v2 config models for Quor detector-rule TOML files.

Hierarchy (mirrors ``quor/config/model.py``'s FilterConfig/QuorConfig shape):
  DetectorFile          top-level TOML document
    └─ DetectorRule     one [[detector]] table

A DetectorRule matches files by basename and/or path-regex, optionally
narrowed further by a content regex. Unlike a filter's ``match_command``
(a single anchor pattern tested against a command string), a detector rule
is tested against every file in a walked repository, so at least one of
``match_basename``/``match_path_regex`` is required — a rule with neither
would have to test its content patterns against every file in the repo,
which is both slow and the kind of unscoped, unverifiable heuristic
CLAUDE.md's "no heuristics that cannot be deterministically verified"
constraint rules out for this feature.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# The fixed set of categories a detector rule may declare. "language" is
# deliberately absent — languages are computed directly from the file
# walk's extension histogram (quor/pipeline/repo_profile/languages.py), not
# from marker-file rules; see docs/design/QB-061-repo-context-profile.md §3.2's
# "language ... no detector rules needed" note.
DetectorCategory = Literal[
    "build_system",
    "package_manager",
    "framework",
    "test_framework",
    "ci_system",
    "database",
    "containerization",
    "configuration",
]


class DetectorRule(BaseModel):
    """One [[detector]] entry from a detector-rule TOML file."""

    model_config = ConfigDict(frozen=True)

    name: str
    category: DetectorCategory
    match_basename: list[str] = Field(default_factory=list)
    """Exact basename matches (e.g. "package.json", "Dockerfile")."""

    match_path_regex: list[str] = Field(default_factory=list)
    """Regex tested against the file's repo-relative POSIX path (e.g.
    ``^\\.github/workflows/.*\\.ya?ml$`` for CI workflow files, or
    ``_test\\.go$`` for Go test files) — for matches that can't be expressed
    as a plain basename."""

    match_content: list[str] = Field(default_factory=list)
    """Regex patterns tested against the *content* of any file that already
    matched ``match_basename``/``match_path_regex``. If empty, the rule
    fires on presence alone (e.g. a Dockerfile existing is itself the
    signal). If non-empty, at least one pattern must match the content of
    at least one candidate file."""

    evidence: str
    """Human-readable description of what this rule detects, shown next to
    the matched file path in the rendered profile — the same per-fact
    transparency `quor explain` already guarantees for compression
    decisions (Anti-Goal #10), applied here to detection decisions."""

    @model_validator(mode="after")
    def _require_a_file_matcher(self) -> DetectorRule:
        if not self.match_basename and not self.match_path_regex:
            raise ValueError(
                f"detector {self.name!r}: at least one of match_basename/"
                "match_path_regex is required — a rule cannot test "
                "match_content against every file in the repo"
            )
        return self


class DetectorFile(BaseModel):
    """Top-level structure of a Quor detector-rule TOML file."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    detector: list[DetectorRule] = Field(default_factory=list)
