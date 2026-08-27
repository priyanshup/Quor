"""Loads Quor's own user-level config.toml (not filter TOML files).

Mode is documented in ADR-009 (AUDIT / OPTIMIZE / SIMULATE) and is currently
display-only — `quor doctor` and `quor gain` show it, but the dispatcher does
not yet switch behavior based on it. Default is "audit" per ADR-009: new
users should see what filtering would do before opting into OPTIMIZE.

`tee_enabled` is the global kill-switch for the tee mechanism (ADR-023); see
`quor/pipeline/tee.py` and `FilterConfig.tee` for the per-filter override.
`tee_max_bytes` is the tee cache's total-size safety ceiling (ADR-023,
QB-103), overridable via QUOR_TEE_MAX_BYTES — must parse as a positive
integer (bytes); any other value is ignored, same fail-open convention as
the QUOR_MODE/QUOR_TEE_ENABLED overrides below.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

import platformdirs

from quor.config.model import ProjectConfig, QuorUserConfig
from quor.errors import ConfigError

VALID_MODES: tuple[str, ...] = ("audit", "optimize", "simulate")

PROJECT_CONFIG_FILENAME = ".quor.toml"

# Mirrors quor/mcp/launcher.py's _MAX_CHECKOUT_SEARCH_DEPTH — a generous
# bound on how many parent directories find_and_load_project_config() will
# walk up, not a realistic ceiling (Path.parents is already finite,
# terminating at the filesystem root); it exists so a pathological path
# depth degrades to "no project config found" instead of an unbounded walk.
_MAX_PROJECT_CONFIG_SEARCH_DEPTH = 64


def load_user_config() -> QuorUserConfig:
    """Read ~/.config/quor/config.toml, overridable by the QUOR_MODE env var."""
    config_path = Path(platformdirs.user_config_dir("quor")) / "config.toml"

    data: dict[str, object] = {}
    if config_path.exists():
        try:
            with open(config_path, "rb") as fh:
                data = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"Invalid TOML in {config_path}: {exc}") from exc
        except OSError as exc:
            raise ConfigError(f"Cannot read {config_path}: {exc}") from exc

    config = QuorUserConfig.model_validate(data)

    env_mode = os.environ.get("QUOR_MODE", "").lower()
    if env_mode in VALID_MODES:
        config = config.model_copy(update={"mode": env_mode})

    env_tee = os.environ.get("QUOR_TEE_ENABLED", "").strip().lower()
    if env_tee in ("0", "false"):
        config = config.model_copy(update={"tee_enabled": False})
    elif env_tee in ("1", "true"):
        config = config.model_copy(update={"tee_enabled": True})

    env_tee_max_bytes = os.environ.get("QUOR_TEE_MAX_BYTES", "").strip()
    if env_tee_max_bytes:
        try:
            parsed_max_bytes = int(env_tee_max_bytes)
        except ValueError:
            parsed_max_bytes = None
        if parsed_max_bytes is not None and parsed_max_bytes > 0:
            config = config.model_copy(update={"tee_max_bytes": parsed_max_bytes})

    return config


def find_and_load_project_config(target_path: Path) -> ProjectConfig | None:
    """Walk up from `target_path` looking for a `.quor.toml` project-config
    file (QB-130), returning the first one found, or `None` if none exists
    anywhere between `target_path` and the filesystem root.

    `target_path` may be a file or a directory — the search starts at
    `target_path` itself if it's a directory, or its parent if it's a file
    (a `.quor.toml` overrides everything *under* the directory it lives in,
    matching `.gitignore`'s own "nearest ancestor wins" convention, which
    is the closest existing mental model a user already has for this kind
    of file). Returns `None` rather than an empty `ProjectConfig` when
    nothing is found — the two are meaningfully different to
    `resolve_effective_config()`'s caller: "no project config file exists
    at all" versus "one exists and explicitly declares no overrides."

    Raises `ConfigError` on invalid TOML or an unreadable file — matching
    `load_user_config()`'s own fail-loud convention for a config file that
    does exist but can't be parsed, as opposed to one that simply isn't
    there.
    """
    start = target_path if target_path.is_dir() else target_path.parent
    start = start.resolve()

    for parent in (start, *start.parents)[:_MAX_PROJECT_CONFIG_SEARCH_DEPTH]:
        candidate = parent / PROJECT_CONFIG_FILENAME
        if not candidate.is_file():
            continue
        try:
            data = tomllib.loads(candidate.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"Invalid TOML in {candidate}: {exc}") from exc
        except OSError as exc:
            raise ConfigError(f"Cannot read {candidate}: {exc}") from exc
        return ProjectConfig.model_validate(data)

    return None


def resolve_effective_config(
    global_config: QuorUserConfig, project_config: ProjectConfig | None
) -> QuorUserConfig:
    """Merge `project_config`'s overrides onto `global_config`, returning a
    new `QuorUserConfig` (QB-130) — never mutates `global_config` (a
    frozen Pydantic model, so mutation isn't even possible) or any runtime
    singleton; the caller decides what to do with the result.

    Only a field the project's `.quor.toml` actually set (non-`None` in
    `CompressionOverrides`, non-empty for `IgnoreOverrides.exclude_patterns`
    — see `CompressionOverrides`'s own docstring for why `None` and "unset"
    are the same thing here) overrides `global_config`'s value; anything
    left unset falls through unchanged. `exclude_patterns` replaces rather
    than unions with `global_config`'s own list — matches every other
    field here being a plain override, not a merge, and there is no
    existing mechanism that ever populates a *global* `exclude_patterns`
    for a project list to meaningfully union with yet.

    `project_config=None` (no `.quor.toml` found) returns `global_config`
    itself, unchanged.
    """
    if project_config is None:
        return global_config

    updates: dict[str, object] = {}
    compression = project_config.compression
    if compression.min_token_threshold is not None:
        updates["min_token_threshold"] = compression.min_token_threshold
    if compression.ast_pruning_enabled is not None:
        updates["ast_pruning_enabled"] = compression.ast_pruning_enabled
    if compression.aggressiveness is not None:
        updates["aggressiveness"] = compression.aggressiveness
    if project_config.ignore.exclude_patterns:
        updates["exclude_patterns"] = project_config.ignore.exclude_patterns

    if not updates:
        return global_config
    return global_config.model_copy(update=updates)
