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

from quor.config.model import QuorUserConfig
from quor.errors import ConfigError

VALID_MODES: tuple[str, ...] = ("audit", "optimize", "simulate")


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
