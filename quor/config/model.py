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

    requires_format: str | None = None
    """Same contract as `requires_language`, for the structured-data
    registry (QB-040) instead of the AST-summarization one — e.g. "yaml" for
    a test whose assertions only hold when the optional `quor[yaml]` extra
    (PyYAML) is installed, checked via `quor.pipeline.structured_data.
    registry.is_format_available`. "json"/"toml" need no extra and are
    always available, so no test uses this for them."""


class FilterConfig(BaseModel):
    """One [[filter]] entry from a TOML filter file."""

    model_config = ConfigDict(frozen=True)

    name: str
    match_command: str
    match_content_types: list[str] = Field(default_factory=list)
    """Optional fallback selector, checked only when `match_command` fails to
    match: names of `quor.pipeline.content_type.ContentType` values (e.g.
    "diff") this filter also applies to, judged by the *content itself*
    rather than the command that produced it. Exists because MCP's
    `compress_context` (QB-104) receives raw output text with no originating
    command string at all, so command-shaped patterns like `git-diff`'s
    `^git\\s+(diff|show)\\b` can never match there — see `FilterRegistry.find()`
    for how this is used. Deliberately restricted to `content_type.detect()`
    outcomes that are exact structural checks (diff/json/traceback), never
    "ansi" (a >20%-of-lines heuristic, not a structural fact) — the same
    "author declares the shape, no guessing" convention `patterns`/
    `preserve_patterns` already use."""
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
    tee_max_bytes: int = 500 * 1024 * 1024  # tee cache total-size safety ceiling — ADR-023 (QB-103)
    # repo_intel/ retention (QB-124) — no user-facing kill-switch, matching quor.db's own
    # always-on sweep: this is background cache hygiene, not a pipeline behavior toggle.
    repo_intel_max_age_days: int = 30
    repo_intel_max_bytes: int = 1024 * 1024 * 1024  # 1 GB — a reasoned starting ceiling, not a
    # measured projection like tee_max_bytes' QB-103 figure; revisit once real usage data exists.
    # invocations retention (QB-128) — same "always-on, no kill-switch" reasoning as
    # repo_intel_max_age_days above: this is quor.db's own housekeeping, not something a user
    # needs to opt into. 90 matches the sweep's original hardcoded value (pre-QB-128), now
    # configurable instead of a literal in the DELETE statement.
    telemetry_max_age_days: int = 90

    # QB-130 — global defaults a project's .quor.toml [compression]/[ignore] can override via
    # resolve_effective_config(). min_token_threshold=0 and exclude_patterns=[] mean "no global
    # threshold/exclusions" (today's existing behavior, unchanged, until a project opts in).
    # ast_pruning_enabled=True matches today's existing behavior too — AST summarization stages
    # already run unconditionally wherever a filter configures them.
    min_token_threshold: int = 0
    ast_pruning_enabled: bool = True
    # Parsed and carried through, deliberately NOT wired into apply_filter_pipeline's actual
    # compression strength — see ProjectConfig.compression's own docstring for why (QB-039/
    # ADR-031: a real "aggressive" mode needs its own architecture-first design pass first).
    aggressiveness: str = "balanced"
    exclude_patterns: list[str] = Field(default_factory=list)


class CompressionOverrides(BaseModel):
    """`[compression]` section of a project's `.quor.toml` (QB-130).

    Every field is `Optional`/unset-by-default (`None`), distinct from
    `QuorUserConfig`'s own always-populated fields — `None` here means "this
    project doesn't override this setting," not "off"/"zero", so
    `resolve_effective_config()` can tell "explicitly set to a falsy value"
    apart from "not mentioned in this project's .quor.toml" (e.g.
    `min_token_threshold = 0` legitimately means "no threshold," and must
    still win over a non-zero global default if a project sets it).

    `aggressiveness` is parsed and validated as a plain string (matching
    `QuorUserConfig.mode`'s own existing precedent — no enum/Literal
    validation for the same reason `mode` has none), and carried through
    `resolve_effective_config()` unchanged, but deliberately goes nowhere
    from there: `apply_filter_pipeline()` never reads it. Wiring it into
    real compression strength is exactly backlog.md's QB-039 ("Compression
    Modes: Safe/Balanced/Aggressive") — logged there as "Proposed. Not
    scoped or implemented... needs its own architecture-first design pass,"
    with several open questions (per-filter vs. global, interaction with
    the tee recovery footer, whether `quor gain` needs a mode dimension)
    still unresolved. More concretely, `quor/pipeline/engine.py`'s own
    docstring states a code-enforced invariant — "PROTECT immutability: no
    stage may downgrade a PROTECT decision" — and ADR-031 already
    explicitly considered and rejected a tiered/priority-based budgeting
    scheme that a real "aggressive" mode would need. Accepting the field
    now (so `.quor.toml` round-trips and `quor doctor` can report it)
    without pretending it does anything yet keeps this ticket's scope
    honest about what's actually wired versus merely parsed.
    """

    model_config = ConfigDict(frozen=True)

    min_token_threshold: int | None = None
    ast_pruning_enabled: bool | None = None
    # None means "unset" here, same as the two fields above — NOT the same
    # thing as QuorUserConfig.aggressiveness's own "balanced" *global*
    # default. A default of "balanced" here would mean any .quor.toml that
    # sets *any* [compression] field (even just min_token_threshold) also
    # silently overrides aggressiveness to "balanced" whether the project
    # asked for that or not — resolve_effective_config() only overrides a
    # field the project actually set.
    aggressiveness: str | None = None


class IgnoreOverrides(BaseModel):
    """`[ignore]` section of a project's `.quor.toml` (QB-130)."""

    model_config = ConfigDict(frozen=True)

    exclude_patterns: list[str] = Field(default_factory=list)


class ProjectConfig(BaseModel):
    """Project-level overrides from a `.quor.toml` file (QB-130).

    Distinct from `QuorConfig` (a `[[filter]]` TOML file's own schema) and
    `QuorUserConfig` (`~/.config/quor/config.toml`, user-level, applies to
    every project) — `.quor.toml` lives at a project's root and overrides
    `QuorUserConfig`'s defaults for that project only, discovered by
    `quor.config.loader.find_and_load_project_config()` and merged via
    `quor.config.loader.resolve_effective_config()`.
    """

    model_config = ConfigDict(frozen=True)

    compression: CompressionOverrides = Field(default_factory=CompressionOverrides)
    ignore: IgnoreOverrides = Field(default_factory=IgnoreOverrides)
