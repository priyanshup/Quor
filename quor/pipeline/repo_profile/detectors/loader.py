"""Load and validate Quor detector-rule TOML files.

Byte-for-byte the same load contract as `quor/filters/loader.py`
(``load_filter_file``): parse TOML with stdlib `tomllib`, validate against
the Pydantic model, raise `ConfigError` on either kind of failure.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import ValidationError

from quor.errors import ConfigError
from quor.pipeline.repo_profile._longpath import to_long_path
from quor.pipeline.repo_profile.detectors.model import DetectorFile, DetectorRule


def load_detector_file(path: Path) -> list[DetectorRule]:
    """Parse a TOML detector-rule file and return its DetectorRule list.

    Raises ConfigError on TOML syntax errors or Pydantic validation failures.
    """
    try:
        with open(to_long_path(path), "rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {path.name}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Cannot read {path.name}: {exc}") from exc

    try:
        config = DetectorFile.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"Invalid detector schema in {path.name}: {exc}") from exc

    return list(config.detector)
