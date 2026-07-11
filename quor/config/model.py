"""Pydantic v2 config models for Quor filter files.

Hierarchy:
  QuorConfig         top-level TOML document
    └─ FilterConfig  one [[filter]] table
         ├─ stages   list of raw stage dicts (dispatched in registry)
         └─ tests    list of FilterTest inline tests
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FilterTest(BaseModel):
    """Inline test for a filter — run by `quor verify`."""

    model_config = ConfigDict(frozen=True)

    description: str
    input: str
    must_contain: list[str] = Field(default_factory=list)
    must_not_contain: list[str] = Field(default_factory=list)
    compression_target: float | None = None
    requires_language: str | None = None
    """If set, this test only runs when the named AST-summarization language
    (see `quor.pipeline.ast_summarize.registry.is_language_available`) is
    actually available — e.g. "javascript" for a test whose assertions only
    hold when the optional `quor[javascript]` extra (tree-sitter) is
    installed. Otherwise it's skipped, not failed: `run_tests()` cannot
    verify behavior that provably cannot happen in this environment, and
    treating that as a hard failure would make `quor verify`/`quor doctor`
    report every plain `pip install quor` as unhealthy (QB-038)."""


class FilterConfig(BaseModel):
    """One [[filter]] entry from a TOML filter file."""

    model_config = ConfigDict(frozen=True)

    name: str
    match_command: str
    abort_unless: list[str] = Field(default_factory=list)
    abort_if: list[str] = Field(default_factory=list)
    on_empty: str = ""
    tee: bool = True  # see ADR-023 — per-filter opt-out for the tee mechanism
    stages: list[dict[str, Any]] = Field(default_factory=list)
    tests: list[FilterTest] = Field(default_factory=list)


class QuorConfig(BaseModel):
    """Top-level structure of a Quor TOML filter file."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    filter: list[FilterConfig] = Field(default_factory=list)


class QuorUserConfig(BaseModel):
    """Quor's own user-level settings (~/.config/quor/config.toml).

    Not to be confused with QuorConfig, which is the filter-file schema.
    """

    model_config = ConfigDict(frozen=True)

    mode: str = "audit"  # one of: audit, optimize, simulate — see ADR-009
    tee_enabled: bool = True  # global kill-switch for the tee mechanism — see ADR-023
