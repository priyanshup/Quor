"""Structured-data (JSON/YAML/TOML) summarization framework (QB-040).

Mirrors the shape of `quor/pipeline/ast_summarize/`: a per-format analyzer
module (`json_fmt.py`, `yaml_fmt.py`, `toml_fmt.py`) plus a routing
`registry.py`, consumed by a single generic `StageHandler`
(`quor/pipeline/stages/structured_data_summarize.py`). See that stage's
module docstring for the full architectural rationale.
"""

from __future__ import annotations
