"""Three-tier filter registry: project > user > built-in.

Registry lookup returns the first matching FilterConfig for a given command string
(highest priority tier wins). Application runs the ContentMask pipeline and handles
abort_unless / abort_if / on_empty short-circuits.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import platformdirs
import regex
from pydantic import ValidationError

from quor.config.model import FilterConfig
from quor.errors import ConfigError
from quor.filters.loader import load_filter_file
from quor.filters.trust import is_git_tracked
from quor.pipeline.ast_summarize.registry import is_language_available
from quor.pipeline.content_type import detect
from quor.pipeline.engine import Pipeline, PipelineResult, StageEntry
from quor.pipeline.mask import ContentMask
from quor.pipeline.stages.base import StageConfig
from quor.pipeline.stages.code_ast_summarize import (
    CodeAstSummarizeConfig,
    CodeAstSummarizeStage,
)
from quor.pipeline.stages.deduplicate_consecutive import (
    DeduplicateConsecutiveConfig,
    DeduplicateConsecutiveStage,
)
from quor.pipeline.stages.group_repeated import GroupRepeatedConfig, GroupRepeatedStage
from quor.pipeline.stages.match_output import MatchOutputConfig, MatchOutputStage
from quor.pipeline.stages.max_tokens import MaxTokensConfig, MaxTokensStage
from quor.pipeline.stages.python_ast_summarize import (
    PythonAstSummarizeConfig,
    PythonAstSummarizeStage,
)
from quor.pipeline.stages.regex_replace import RegexReplaceConfig, RegexReplaceStage
from quor.pipeline.stages.remove_ansi import RemoveAnsiConfig, RemoveAnsiStage
from quor.pipeline.stages.strip_lines import StripLinesConfig, StripLinesStage
from quor.pipeline.stages.truncate_lines import TruncateLinesConfig, TruncateLinesStage

# Maps stage type string → (handler class, config class)
_STAGE_HANDLERS: dict[str, tuple[type, type[StageConfig]]] = {
    "remove_ansi": (RemoveAnsiStage, RemoveAnsiConfig),
    "strip_lines": (StripLinesStage, StripLinesConfig),
    "deduplicate_consecutive": (DeduplicateConsecutiveStage, DeduplicateConsecutiveConfig),
    "group_repeated": (GroupRepeatedStage, GroupRepeatedConfig),
    "max_tokens": (MaxTokensStage, MaxTokensConfig),
    "truncate_lines": (TruncateLinesStage, TruncateLinesConfig),
    "regex_replace": (RegexReplaceStage, RegexReplaceConfig),
    "match_output": (MatchOutputStage, MatchOutputConfig),
    "python_ast_summarize": (PythonAstSummarizeStage, PythonAstSummarizeConfig),
    "code_ast_summarize": (CodeAstSummarizeStage, CodeAstSummarizeConfig),
}

_BUILTIN_DIR = Path(__file__).parent / "builtin"
_COMMAND_TIMEOUT: float = 0.1  # seconds for command matching regex


def _build_stage_entry(stage_dict: dict[str, Any]) -> StageEntry:
    """Convert a raw stage dict (from TOML) into a validated StageEntry.

    Raises ConfigError on unknown type or validation failure.

    Supports two extension mechanisms beyond the built-in _STAGE_HANDLERS:
    - ``file://`` URI: loads a StageHandler from a local Python file, for
      development use without packaging (see plugin_loader.load_from_file_uri).
    - Third-party entry-points: discovered via quor.compression_stage entry-point
      group and returned by plugin_loader.get_extra_stage_handlers().
    """
    stage_type = stage_dict.get("type", "")

    # file:// escape hatch — load StageHandler from a local Python file
    if stage_type.startswith("file://"):
        from quor.errors import PluginError
        from quor.pipeline.plugin_loader import load_from_file_uri

        try:
            handler = load_from_file_uri(stage_type)
        except PluginError as exc:
            raise ConfigError(f"file:// stage failed to load: {exc}") from exc
        try:
            config = StageConfig.model_validate(stage_dict)
        except ValidationError as exc:
            raise ConfigError(f"Invalid config for file:// stage: {exc}") from exc
        return StageEntry(handler=handler, config=config)

    # Built-in stages (fast path — no extra lookup needed)
    if stage_type in _STAGE_HANDLERS:
        handler_cls, config_cls = _STAGE_HANDLERS[stage_type]
        try:
            config = config_cls.model_validate(stage_dict)
        except ValidationError as exc:
            raise ConfigError(f"Invalid config for stage {stage_type!r}: {exc}") from exc
        return StageEntry(handler=handler_cls(), config=config)

    # Third-party stages discovered via quor.compression_stage entry-points
    from quor.pipeline.plugin_loader import get_extra_stage_handlers

    extra = get_extra_stage_handlers()
    if stage_type in extra:
        handler_cls, config_cls = extra[stage_type]
        try:
            config = config_cls.model_validate(stage_dict)
        except ValidationError as exc:
            raise ConfigError(f"Invalid config for stage {stage_type!r}: {exc}") from exc
        return StageEntry(handler=handler_cls(), config=config)

    all_known = sorted({*_STAGE_HANDLERS, *extra})
    raise ConfigError(
        f"Unknown stage type: {stage_type!r}. Known: {all_known}"
    )


@dataclass(frozen=True)
class TestRunResult:
    """Result of `FilterRegistry.run_tests()` for one filter's inline tests.

    `skipped` (QB-038) is distinct from `failures`: a skipped test's
    assertions were never evaluated at all because they can only hold when
    an optional AST-summarization dependency (`FilterTest.requires_language`)
    is installed — a plain `pip install quor` legitimately doesn't have it.
    Callers (`quor verify`, `quor doctor`) must not count skips as failures.
    """

    failures: list[str]
    skipped: list[str]


class FilterRegistry:
    """Three-tier filter registry: project (highest) > user > built-in (lowest)."""

    def __init__(
        self,
        project_root: Path | None = None,
        *,
        skip_user: bool = False,
        skip_project: bool = False,
    ) -> None:
        self._builtin: list[FilterConfig] = []
        self._user: list[FilterConfig] = []
        self._project: list[FilterConfig] = []

        self._load_builtin()
        if not skip_user:
            self._load_user()
        if not skip_project and project_root is not None:
            self._load_project(project_root)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load_builtin(self) -> None:
        if not _BUILTIN_DIR.exists():
            return
        for toml_path in sorted(_BUILTIN_DIR.glob("*.toml")):
            try:
                self._builtin.extend(load_filter_file(toml_path))
            except ConfigError as exc:
                warnings.warn(
                    f"[quor] Failed to load built-in filter {toml_path.name}: {exc}",
                    stacklevel=2,
                )

    def _load_user(self) -> None:
        user_dir = Path(platformdirs.user_config_dir("quor")) / "filters"
        if not user_dir.exists():
            return
        for toml_path in sorted(user_dir.glob("*.toml")):
            try:
                self._user.extend(load_filter_file(toml_path))
            except ConfigError as exc:
                warnings.warn(
                    f"[quor] Failed to load user filter {toml_path.name}: {exc}",
                    stacklevel=2,
                )

    def _load_project(self, project_root: Path) -> None:
        project_dir = project_root / ".quor" / "filters"
        if not project_dir.exists():
            return
        for toml_path in sorted(project_dir.glob("*.toml")):
            if not is_git_tracked(toml_path):
                warnings.warn(
                    f"[quor] Project filter {toml_path.name} is not git-tracked; skipping",
                    stacklevel=2,
                )
                continue
            try:
                self._project.extend(load_filter_file(toml_path))
            except ConfigError as exc:
                warnings.warn(
                    f"[quor] Failed to load project filter {toml_path.name}: {exc}",
                    stacklevel=2,
                )

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def all_filters(self) -> list[tuple[str, FilterConfig]]:
        """Return all filters as (tier, config) pairs in lookup order."""
        result: list[tuple[str, FilterConfig]] = []
        for f in self._project:
            result.append(("project", f))
        for f in self._user:
            result.append(("user", f))
        for f in self._builtin:
            result.append(("builtin", f))
        return result

    def find(self, command: str) -> FilterConfig | None:
        """Return first matching filter (project > user > built-in priority)."""
        for _, f in self.all_filters():
            try:
                if regex.search(f.match_command, command, timeout=_COMMAND_TIMEOUT):
                    return f
            except (regex.error, TimeoutError):
                pass
        return None

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------

    def apply(self, filter_config: FilterConfig, content: str, content_type: str = "") -> str:
        """Apply filter to content. Returns compressed string or original on abort."""
        if filter_config.abort_unless and not any(
            s in content for s in filter_config.abort_unless
        ):
            return content

        if filter_config.abort_if and any(s in content for s in filter_config.abort_if):
            return content

        result = self._run_pipeline(filter_config, content, content_type)
        rendered = result.mask.render()

        if not rendered.strip() and filter_config.on_empty:
            return filter_config.on_empty

        return rendered

    def trace(
        self, filter_config: FilterConfig, content: str, content_type: str = ""
    ) -> PipelineResult:
        """Run the pipeline and return the full per-stage trace (for `quor explain`).

        Unlike `apply()`, this does not honor abort_unless/abort_if/on_empty —
        it always runs every stage so the trace shows what each stage would
        do. This also means QB-036's early-exit optimization is deliberately
        disabled here (`early_exit=False`): early exit only ever changes
        *which stages actually run*, never the rendered content, but `quor
        explain`'s whole purpose is showing what every configured stage does
        — an early-exited stage would show "skipped — early exit: ..."
        instead of its real per-stage line count, which is exactly the
        diagnostic information this command exists to surface. `apply()`
        below (the real compression path — Bash/Read hooks, benchmarks,
        `quor verify`) keeps the optimization at its default (on).
        """
        return self._run_pipeline(filter_config, content, content_type, early_exit=False)

    def _run_pipeline(
        self,
        filter_config: FilterConfig,
        content: str,
        content_type: str = "",
        *,
        early_exit: bool = True,
    ) -> PipelineResult:
        detected = content_type or detect(content).value

        mask = ContentMask.from_text(content)
        entries: list[StageEntry] = []
        for stage_dict in filter_config.stages:
            try:
                entries.append(_build_stage_entry(stage_dict))
            except ConfigError as exc:
                warnings.warn(f"[quor] Skipping invalid stage: {exc}", stacklevel=2)

        return Pipeline(entries).execute(
            mask, raw_content=content, content_type=detected, early_exit=early_exit
        )

    # ------------------------------------------------------------------
    # Inline test runner
    # ------------------------------------------------------------------

    def run_tests(self, filter_config: FilterConfig) -> TestRunResult:
        """Run all inline FilterTest entries. Returns failure and skip messages.

        Warnings raised while applying a test's input (e.g. a stage's own
        fail-open path firing on a deliberately-invalid fixture, like
        cat-python.toml's "Invalid Python fails open" case) are captured
        per-test rather than left to print unconditionally to stderr. A
        *passing* test proves whatever happened — including any fail-open
        warning along the way — was exactly what the fixture intended, so
        the warning is discarded; a *failing* test keeps its captured
        warnings, appended to that test's own failure messages, since they
        may be useful context for an unexpected failure. This is generic —
        no stage type, exception type, or warning category is
        special-cased; every `warnings.warn()` call from anywhere in the
        pipeline is handled identically. Real compression (`apply()` called
        directly by the dispatcher/Read hook, not through this method) is
        entirely unaffected — warnings there still print normally.

        A test whose `requires_language` names an AST language that isn't
        actually available (QB-038 — e.g. "javascript" without the optional
        `quor[javascript]` extra installed) is skipped entirely, not run and
        not counted as a failure: its assertions describe behavior that
        provably cannot happen in this environment, so evaluating them would
        only ever produce a false failure, not a meaningful signal.
        """
        failures: list[str] = []
        skipped: list[str] = []
        for i, test in enumerate(filter_config.tests):
            label = f"[{filter_config.name}] test {i + 1}: {test.description!r}"

            if test.requires_language is not None and not is_language_available(
                test.requires_language
            ):
                skipped.append(
                    f"{label} — skipped: {test.requires_language} AST parser not "
                    "installed (optional quor[javascript] extra)"
                )
                continue

            test_failures: list[str] = []

            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                try:
                    output: str | None = self.apply(filter_config, test.input)
                except Exception as exc:  # noqa: BLE001
                    test_failures.append(f"{label} — EXCEPTION: {exc}")
                    output = None

            if output is not None:
                for expected in test.must_contain:
                    if expected not in output:
                        test_failures.append(
                            f"{label} — must_contain {expected!r} not found in output"
                        )

                for forbidden in test.must_not_contain:
                    if forbidden in output:
                        test_failures.append(
                            f"{label} — must_not_contain {forbidden!r} found in output"
                        )

                if test.compression_target is not None and test.input:
                    ratio = 1.0 - len(output) / len(test.input)
                    if ratio < test.compression_target:
                        test_failures.append(
                            f"{label} — compression_target {test.compression_target:.0%} "
                            f"not met (got {ratio:.0%})"
                        )

            if test_failures:
                test_failures.extend(
                    f"{label} — warning during test: {w.message}" for w in caught
                )
                failures.extend(test_failures)

        return TestRunResult(failures=failures, skipped=skipped)
