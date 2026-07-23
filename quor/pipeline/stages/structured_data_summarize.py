"""structured_data_summarize stage: collapse long, homogeneous JSON/YAML/TOML
arrays to their first few elements + an omitted-count placeholder (QB-040).

The filter-configurable, `code_ast_summarize`-shaped counterpart to
`quor/pipeline/ast_summarize/`'s per-language body compression: reads a
`format` field from its `StageConfig` (TOML: `format = "json"`) and
dispatches to whichever analyzer is registered for that format in
`quor.pipeline.structured_data.registry.get_analyzer()`.

Why this needs a *new* piece of logic rather than reusing existing stages
(`strip_lines`/`group_repeated`/`max_tokens`): those all decide per-line,
by regex, with no notion of nesting or array-element boundaries — safe for
`.env`/`.ini` (QB-040's other two formats, whose grammar has zero structure
beyond one KEY=VALUE per line, so `strip_lines` alone suffices — see
`quor/filters/builtin/dotenv.toml`/`ini.toml`) but not for JSON/YAML/TOML,
where the goal is "collapse the extra elements of *this specific array*,
keep every key, never touch a value" — a decision that requires knowing
where one array element ends and the next begins, which no line-pattern can
express safely (a naive line-count or regex heuristic risks truncating a
value mid-object or misreading a string that happens to contain `[`/`]`).
`quor.pipeline.structured_data`'s analyzers solve this with the format's own
real parser (stdlib `json`/`tomllib`, PyYAML) plus, for JSON/YAML, genuine
position tracking — see each analyzer's own module docstring.

Byte-preservation contract — identical to `code_ast_summarize`/
`python_ast_summarize`: every KEPT line is the original line, unmodified.
The one exception, shared with `group_repeated`/`collapse_unchanged_context`
(see `quor/pipeline/mask.py`'s module docstring on which stages may rewrite
a line), is the first line of a collapsed run, whose text is replaced with
the omitted-count summary — the same "reuse one line as the placeholder,
COMPRESS the rest" technique, applied to array-element line ranges instead
of repeated-line runs.

Fail-open contract — same two genuinely different cases as
`code_ast_summarize.py` (see that module's docstring for the full
reasoning, which applies here unchanged):
  - **Unsupported format** (no analyzer registered for `config.format`):
    `apply()` returns the mask unchanged, silently.
  - **Parse failure for a registered, supported format** (malformed JSON/
    YAML/TOML): the analyzer's exception is deliberately NOT caught here —
    propagates to `Pipeline.execute()`'s existing per-stage fail-open.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import ConfigDict, Field

from quor.pipeline.mask import ContentMask, Decision, LineMask
from quor.pipeline.stages._utils import _compile, matches_any
from quor.pipeline.stages.base import StageConfig
from quor.pipeline.structured_data.registry import get_analyzer


class StructuredDataSummarizeConfig(StageConfig):
    model_config = ConfigDict(frozen=True, extra="forbid")

    format: str = Field(
        description=(
            "Format name to look up in quor.pipeline.structured_data.registry "
            "(e.g. 'json'). An unregistered format is not a config error — "
            "it fails open (mask unchanged), see module docstring."
        )
    )


class StructuredDataSummarizeStage:
    """Collapse long homogeneous arrays in a supported structured-data
    format, dispatching to the analyzer registered for `config.format`."""

    api_version: ClassVar[int] = 1
    stage_type: ClassVar[str] = "structured_data_summarize"

    def can_handle(self, content: str, content_type: str) -> bool:
        return True

    def apply(self, mask: ContentMask, config: StageConfig) -> ContentMask:
        if not isinstance(config, StructuredDataSummarizeConfig):
            raise TypeError(
                f"structured_data_summarize requires StructuredDataSummarizeConfig, "
                f"got {type(config).__name__}"
            )

        analyzer = get_analyzer(config.format)
        if analyzer is None:
            # Unsupported format: fail open, silently, unchanged mask. See
            # module docstring / code_ast_summarize.py's identical contract.
            return mask

        # Parse the full original line sequence (mask.lines), not
        # mask.render() — keeps a 1:1 index<->lineno mapping regardless of
        # what upstream stages already decided, exactly like
        # code_ast_summarize.py's identical comment explains.
        ranges = analyzer("\n".join(lm.line for lm in mask.lines))
        if not ranges:
            return mask

        lines = list(mask.lines)
        n = len(lines)

        # preserve_patterns (base StageConfig field): PROTECT matching KEEP
        # lines first, exactly like code_ast_summarize's identical pass —
        # lets a filter author shield specific lines from ever being swept
        # into a collapsed run, independent of the structural analysis.
        compiled_preserve = [_compile(p) for p in config.preserve_patterns]
        if compiled_preserve:
            lines = [
                LineMask(lm.line, Decision.PROTECT, "matches preserve_pattern", self.stage_type)
                if lm.decision is Decision.KEEP and matches_any(lm.line, compiled_preserve)
                else lm
                for lm in lines
            ]

        for r in ranges:
            start_idx = r.compress_start - 1  # ranges are 1-indexed
            end_idx = r.compress_end - 1
            if not (0 <= start_idx <= end_idx < n):
                continue  # defensive: an analyzer bug must not corrupt the mask

            # "When uncertain, keep it": if any line in this run is already
            # PROTECT (a user preserve_pattern, or an earlier stage), skip
            # collapsing this run entirely rather than partially collapsing
            # around a protected line — mirrors group_repeated's own
            # "PROTECT lines break a run" rule, but at run granularity here
            # since compress_start's summary text depends on the whole run
            # being collapsible together.
            if any(lines[i].decision is Decision.PROTECT for i in range(start_idx, end_idx + 1)):
                continue

            lines[start_idx] = LineMask(
                line=r.summary,
                decision=Decision.KEEP,
                reason=f"collapsed {config.format} array",
                stage=self.stage_type,
            )
            for i in range(start_idx + 1, end_idx + 1):
                if lines[i].decision is Decision.COMPRESS:
                    continue
                lines[i] = LineMask(
                    line=lines[i].line,
                    decision=Decision.COMPRESS,
                    reason=f"collapsed {config.format} array element",
                    stage=self.stage_type,
                )

        return ContentMask(tuple(lines))
