"""code_ast_summarize stage: generic, multi-language AST body compression.

QB-005B: the reusable, filter-configurable counterpart to
`python_ast_summarize.py`, introduced by
`docs/design/QB-005A-ast-summarization-design.md` Section 9's parser
framework. Where `python_ast_summarize` is hardcoded to Python,
`code_ast_summarize` reads a `language` field from its `StageConfig`
(TOML: `language = "python"`) and dispatches to whichever analyzer is
registered for that language in
`quor/pipeline/ast_summarize/registry.py::get_analyzer()` — the same
registry `python_ast_summarize.py` itself now delegates to, so there is
only ever one implementation of Python's body-compression logic shared by
both stages, not two.

**Not yet wired into any built-in filter.** QB-005B's own scope is the
framework, proven correct via direct unit tests and via
`python_ast_summarize`'s unchanged behavior — not a new user-visible
filter. `cat-python.toml` continues to use `python_ast_summarize`
unchanged; nothing currently routes a TOML `[[filter.stages]]` entry to
`type = "code_ast_summarize"`. QB-005C/QB-005D (JavaScript/TypeScript) are
expected to be the first real filters to use this stage, once their
analyzers exist.

Fail-open contract — two genuinely different cases, per QB-005A Section 4:
  - **Unsupported language** (no analyzer registered for `config.language`,
    e.g. a filter misconfigured with `language = "cobol"`, or a future
    language's filter shipped before its analyzer): `apply()` returns the
    mask **unchanged**, silently — no exception, no warning. This is a
    deliberate, documented deviation from QB-005A Section 4.2's original
    proposal to implement this check inside `can_handle()`: the
    `StageHandler` Protocol's `can_handle(self, content, content_type)`
    has no access to `StageConfig` (confirmed against
    `quor/pipeline/stages/base.py` during implementation — no stage
    receives its own config in `can_handle()`, and every other stage's
    `can_handle()` also only ever depends on `content`/`content_type`, not
    its own config), so a per-language capability check cannot live there
    without changing the Protocol itself — out of scope for this
    infrastructure-only phase, which must not modify any existing
    interface. Implementing the same fail-open guarantee one call deeper,
    inside `apply()`, is observably identical from the pipeline's
    perspective (`Pipeline.execute()` sees a stage that ran and changed
    nothing) and keeps `can_handle()` — and therefore the `StageHandler`
    Protocol — untouched.
  - **Parse failure for a registered, supported language** (e.g. invalid
    Python syntax): the analyzer's exception is deliberately NOT caught
    here, exactly like `python_ast_summarize.py`. It propagates to
    `Pipeline.execute()`'s existing per-stage fail-open handling.

This stage never regenerates, reformats, or rewrites source text — every
kept line is the original line, byte-for-byte, identical in spirit to
`python_ast_summarize.py`'s own guarantee.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import ConfigDict, Field

from quor.pipeline.ast_summarize.registry import get_analyzer
from quor.pipeline.mask import ContentMask, Decision, LineMask
from quor.pipeline.stages._utils import _compile, matches_any
from quor.pipeline.stages.base import StageConfig


class CodeAstSummarizeConfig(StageConfig):
    model_config = ConfigDict(frozen=True, extra="forbid")

    language: str = Field(
        description=(
            "Language name to look up in quor.pipeline.ast_summarize.registry "
            "(e.g. 'python'). An unregistered language is not a config error — "
            "it fails open (mask unchanged), see module docstring."
        )
    )


class CodeAstSummarizeStage:
    """Compress a supported language's function/method bodies to signature
    + docstring only, dispatching to the analyzer registered for
    `config.language`."""

    api_version: ClassVar[int] = 1
    stage_type: ClassVar[str] = "code_ast_summarize"

    def can_handle(self, content: str, content_type: str) -> bool:
        return True

    def apply(self, mask: ContentMask, config: StageConfig) -> ContentMask:
        if not isinstance(config, CodeAstSummarizeConfig):
            raise TypeError(
                f"code_ast_summarize requires CodeAstSummarizeConfig, "
                f"got {type(config).__name__}"
            )

        analyzer = get_analyzer(config.language)
        if analyzer is None:
            # Unsupported language: fail open, silently, unchanged mask.
            # See module docstring for why this check lives here (apply())
            # rather than in can_handle().
            return mask

        # Parse the full original line sequence (mask.lines), not
        # mask.render()'s already-compressed view — see
        # python_ast_summarize.py's identical comment for why this matters
        # (keeps a 1:1 index<->lineno mapping regardless of what upstream
        # stages already decided). The analyzer call itself may raise on
        # invalid input for its language; deliberately not caught here —
        # see module docstring "Fail-open".
        compress_lines = analyzer("\n".join(lm.line for lm in mask.lines))
        if not compress_lines:
            return mask

        compiled_preserve = [_compile(p) for p in config.preserve_patterns]
        new_lines: list[LineMask] = []
        for idx, lm in enumerate(mask.lines):
            line_number = idx + 1  # analyzers report 1-indexed line numbers

            if lm.decision is Decision.PROTECT:
                new_lines.append(lm)
                continue

            if lm.decision is Decision.COMPRESS:
                new_lines.append(lm)
                continue

            if compiled_preserve and matches_any(lm.line, compiled_preserve):
                new_lines.append(
                    LineMask(lm.line, Decision.PROTECT, "matches preserve_pattern", self.stage_type)
                )
                continue

            if line_number in compress_lines:
                new_lines.append(
                    LineMask(
                        lm.line,
                        Decision.COMPRESS,
                        f"{config.language} function/method body",
                        self.stage_type,
                    )
                )
            else:
                new_lines.append(lm)

        return ContentMask(tuple(new_lines))
