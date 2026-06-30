"""Load and validate Quor TOML filter files."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import ValidationError

from quor.config.model import FilterConfig, QuorConfig
from quor.errors import ConfigError


def load_filter_file(path: Path) -> list[FilterConfig]:
    """Parse a TOML filter file and return its FilterConfig list.

    Raises ConfigError on TOML syntax errors or Pydantic validation failures.
    """
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {path.name}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Cannot read {path.name}: {exc}") from exc

    try:
        config = QuorConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"Invalid filter schema in {path.name}: {exc}") from exc

    return list(config.filter)
