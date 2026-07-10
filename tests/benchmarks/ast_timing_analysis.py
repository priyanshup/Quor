"""AST summarization timing/validation analysis (QB-005E).

A second, deliberately SEPARATE measurement pass from `benchmark_runner.py`
— it answers a different question. `benchmark_runner.py`/`manifest.toml`
answer "does compression on realistic corpus samples stay correct and not
regress?" (an automated, `pytest`-gated, baseline-tracked concern). This
module answers "how does the AST summarization machinery itself behave
under the specific operational conditions QB-005E's own validation
checklist names?" — parser-vs-pipeline time contribution, average/worst
runtime, large-file scaling, malformed source, ERROR-node handling, and
"nothing to summarize" cases. These are legitimately different measurement
purposes (regression-tracked correctness vs. one-off operational
characterization), so they get a separate script rather than being folded
into the baseline-tracked manifest — adding synthetic scaling/malformed
snippets to `manifest.toml` would also violate this task's own "do not
generate synthetic repeated code solely to inflate token counts" instruction
for the *corpus*, which this module's synthetic scaling inputs are
deliberately NOT part of.

Isolated from production code by construction, identically to
`benchmark_runner.py`: only ever *calls* Quor's existing, unmodified public
surface — `FilterRegistry`, `ContentMask`, `StageConfig` subclasses,
`CodeAstSummarizeStage`, `PythonAstSummarizeStage`, and the `analyze_*()`
functions directly. Nothing in `quor/` is modified, patched, or
special-cased. This module is itself never imported by anything in
`quor/`, and is NOT wired into `test_benchmarks.py`'s pytest gate — like
`execution_time_ms` throughout the rest of this benchmark suite, timing
numbers here are inherently noisy across machines/CI runners and are
reported for visibility, never as a pass/fail signal.

Run directly:
    python -m tests.benchmarks.ast_timing_analysis
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass

from quor.filters.registry import FilterRegistry
from quor.pipeline.ast_summarize.javascript import analyze_javascript
from quor.pipeline.ast_summarize.python import analyze_python
from quor.pipeline.ast_summarize.typescript import analyze_tsx, analyze_typescript
from quor.pipeline.mask import ContentMask
from quor.pipeline.stages.code_ast_summarize import CodeAstSummarizeConfig, CodeAstSummarizeStage
from quor.pipeline.stages.python_ast_summarize import (
    PythonAstSummarizeConfig,
    PythonAstSummarizeStage,
)
from tests.benchmarks.benchmark_runner import BENCHMARKS_DIR, load_manifest

_AST_CATEGORIES = frozenset({"cat-python", "cat-javascript", "cat-typescript", "cat-tsx"})

_ANALYZER_BY_LANGUAGE = {
    "python": analyze_python,
    "javascript": analyze_javascript,
    "typescript": analyze_typescript,
    "tsx": analyze_tsx,
}

_CATEGORY_LANGUAGE = {
    "cat-python": "python",
    "cat-javascript": "javascript",
    "cat-typescript": "typescript",
    "cat-tsx": "tsx",
}


# ---------------------------------------------------------------------------
# Part 1 — per-corpus-case parser vs. AST-stage vs. full-pipeline timing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TimingBreakdown:
    id: str
    language: str
    lines: int
    parser_only_ms: float
    ast_stage_ms: float
    full_pipeline_ms: float
    ast_stage_pct_of_pipeline: float
    parser_pct_of_ast_stage: float


def _time_ms(fn, repeats: int = 5) -> float:
    """Median of `repeats` runs, in milliseconds — median rather than mean
    to reduce sensitivity to one-off OS scheduling noise, consistent with
    this module's own "timing is for visibility, not a gate" philosophy."""
    samples = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return statistics.median(samples)


def measure_corpus_timing() -> list[TimingBreakdown]:
    """For every AST-routed manifest case, measure:
      - parser_only_ms: the raw analyze_*() call alone.
      - ast_stage_ms: the full code_ast_summarize/python_ast_summarize
        StageHandler.apply() call (parser + this stage's own mask-walking
        loop), via a synthetic, minimal ContentMask/StageConfig — not
        FilterRegistry.apply(), so no other stage's time is included.
      - full_pipeline_ms: FilterRegistry.apply() end to end (parser +
        AST-stage bookkeeping + strip_lines + deduplicate_consecutive +
        max_tokens + render) — the same call `benchmark_runner.py::run_case()`
        already times as `execution_time_ms`, re-measured here so both
        numbers come from the same process/run for a fair ratio.
    """
    registry = FilterRegistry(skip_user=True, skip_project=True)
    breakdowns: list[TimingBreakdown] = []

    for case in load_manifest():
        if case.category not in _AST_CATEGORIES:
            continue
        language = _CATEGORY_LANGUAGE[case.category]
        analyzer = _ANALYZER_BY_LANGUAGE[language]
        source = (BENCHMARKS_DIR / case.sample_file).read_text(encoding="utf-8")
        lines = source.count("\n") + 1

        parser_only_ms = _time_ms(lambda analyzer=analyzer, source=source: analyzer(source))

        mask = ContentMask.from_text(source)
        if language == "python":
            stage = PythonAstSummarizeStage()
            config = PythonAstSummarizeConfig(type="python_ast_summarize")
        else:
            stage = CodeAstSummarizeStage()
            config = CodeAstSummarizeConfig(type="code_ast_summarize", language=language)
        ast_stage_ms = _time_ms(lambda stage=stage, mask=mask, config=config: stage.apply(mask, config))

        filter_config = registry.find(case.command)
        full_pipeline_ms = _time_ms(
            lambda filter_config=filter_config, source=source: registry.apply(filter_config, source)
        )

        breakdowns.append(
            TimingBreakdown(
                id=case.id,
                language=language,
                lines=lines,
                parser_only_ms=parser_only_ms,
                ast_stage_ms=ast_stage_ms,
                full_pipeline_ms=full_pipeline_ms,
                ast_stage_pct_of_pipeline=(
                    (ast_stage_ms / full_pipeline_ms * 100) if full_pipeline_ms else 0.0
                ),
                parser_pct_of_ast_stage=(
                    (parser_only_ms / ast_stage_ms * 100) if ast_stage_ms else 0.0
                ),
            )
        )

    return breakdowns


# ---------------------------------------------------------------------------
# Part 2 — scaling, malformed-source, ERROR-node, and no-op validation
#
# Deliberately synthetic inputs, deliberately NOT part of manifest.toml's
# corpus — this section validates *operational characteristics* (does time
# scale reasonably, does a broken file still return fast and correctly),
# not compression realism. See module docstring.
# ---------------------------------------------------------------------------


def _synthetic_js_functions(n: int) -> str:
    return "".join(f"function func_{i}(x) {{\n  return x + {i};\n}}\n" for i in range(n))


def _synthetic_ts_functions(n: int) -> str:
    return "".join(
        f"function func_{i}(x: number): number {{\n  return x + {i};\n}}\n" for i in range(n)
    )


def _synthetic_nested_ts_classes(n_classes: int, methods_per_class: int) -> str:
    parts = []
    for c in range(n_classes):
        methods = "".join(
            f"  method_{c}_{m}(x: number): number {{\n    return x + {m};\n  }}\n\n"
            for m in range(methods_per_class)
        )
        parts.append(f"class Service_{c} {{\n{methods}}}\n\n")
    return "".join(parts)


@dataclass(frozen=True)
class ScalingPoint:
    n: int
    lines: int
    time_ms: float


def measure_scaling(language: str, sizes: tuple[int, ...] = (10, 50, 100, 500, 1000)) -> list[ScalingPoint]:
    analyzer = analyze_javascript if language == "javascript" else analyze_typescript
    builder = _synthetic_js_functions if language == "javascript" else _synthetic_ts_functions
    points = []
    for n in sizes:
        source = builder(n)
        t = _time_ms(lambda source=source: analyzer(source), repeats=3)
        points.append(ScalingPoint(n=n, lines=source.count("\n") + 1, time_ms=t))
    return points


def measure_nested_declarations(n_classes: int = 30, methods_per_class: int = 10) -> ScalingPoint:
    source = _synthetic_nested_ts_classes(n_classes, methods_per_class)
    t = _time_ms(lambda: analyze_typescript(source), repeats=3)
    return ScalingPoint(n=n_classes * methods_per_class, lines=source.count("\n") + 1, time_ms=t)


@dataclass(frozen=True)
class MalformedCase:
    label: str
    language: str
    time_ms: float
    compressed_line_count: int
    raised: bool


_MALFORMED_LOCALIZED_JS = (
    "function good1(x) {\n  return x + 1;\n}\n\n"
    "function alsoBroken(y) {\n  return y +++ * ;\n}\n\n"
    "function good2(z) {\n  return z + 2;\n}\n"
)
_MALFORMED_SWALLOWING_JS = (
    "function good1(x) {\n  return x + 1;\n}\n\n"
    "function broken(: {\n  return 1;\n}\n\n"
    "function good2(y) {\n  return y + 2;\n}\n"
)
_MALFORMED_LOCALIZED_TS = (
    "function good1(x: number): number {\n  return x + 1;\n}\n\n"
    "function alsoBroken(y: number): number {\n  return y +++ * ;\n}\n\n"
    "function good2(z: number): number {\n  return z + 2;\n}\n"
)
_MALFORMED_PYTHON = "def broken(:\n    pass\n\ndef good():\n    return 1\n"


def measure_malformed_source() -> list[MalformedCase]:
    results = []
    for label, language, source in (
        ("js-localized-body-error", "javascript", _MALFORMED_LOCALIZED_JS),
        ("js-signature-error-swallows-tail", "javascript", _MALFORMED_SWALLOWING_JS),
        ("ts-localized-body-error", "typescript", _MALFORMED_LOCALIZED_TS),
        ("python-syntax-error-whole-file", "python", _MALFORMED_PYTHON),
    ):
        analyzer = _ANALYZER_BY_LANGUAGE[language]
        raised = False
        compressed = 0
        t0 = time.perf_counter()
        try:
            compressed = len(analyzer(source))
        except SyntaxError:
            raised = True
        elapsed = (time.perf_counter() - t0) * 1000
        results.append(
            MalformedCase(
                label=label,
                language=language,
                time_ms=elapsed,
                compressed_line_count=compressed,
                raised=raised,
            )
        )
    return results


_NO_FUNCTIONS_TS = (
    'export interface Config {\n  timeout: number;\n  retries: number;\n}\n\n'
    'export const DEFAULT_CONFIG: Config = { timeout: 5000, retries: 3 };\n'
)


def measure_skipped_no_summarization() -> ScalingPoint:
    """A file with zero function-like nodes — the analyzer must still run
    quickly and return an empty set (nothing to compress), the
    "AST found nothing to do" case named explicitly in QB-005E's own
    validation checklist."""
    t = _time_ms(lambda: analyze_typescript(_NO_FUNCTIONS_TS), repeats=5)
    result = analyze_typescript(_NO_FUNCTIONS_TS)
    assert result == set(), "expected zero compressible lines in a functions-free file"
    return ScalingPoint(n=0, lines=_NO_FUNCTIONS_TS.count("\n") + 1, time_ms=t)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _print_report() -> None:
    print("=" * 78)
    print("Part 1 - Parser / AST-stage / full-pipeline time breakdown (per corpus case)")
    print("=" * 78)
    breakdowns = measure_corpus_timing()
    print(
        f"{'id':<48} {'lang':<10} {'lines':>6} {'parser':>8} {'stage':>8} "
        f"{'pipeline':>9} {'stage%':>7} {'parser%':>8}"
    )
    for b in breakdowns:
        print(
            f"{b.id:<48} {b.language:<10} {b.lines:>6} {b.parser_only_ms:>7.3f}ms "
            f"{b.ast_stage_ms:>7.3f}ms {b.full_pipeline_ms:>8.3f}ms "
            f"{b.ast_stage_pct_of_pipeline:>6.1f}% {b.parser_pct_of_ast_stage:>7.1f}%"
        )

    ast_only = [b for b in breakdowns if b.language != "python"]
    py_only = [b for b in breakdowns if b.language == "python"]
    if ast_only:
        print()
        print(
            f"tree-sitter (JS/TS/TSX) mean stage-of-pipeline: "
            f"{statistics.mean(b.ast_stage_pct_of_pipeline for b in ast_only):.1f}%  "
            f"mean parser-of-stage: "
            f"{statistics.mean(b.parser_pct_of_ast_stage for b in ast_only):.1f}%"
        )
        print(f"tree-sitter mean full-pipeline time: {statistics.mean(b.full_pipeline_ms for b in ast_only):.3f}ms")
        print(f"tree-sitter worst-case full-pipeline time: {max(b.full_pipeline_ms for b in ast_only):.3f}ms ({max(ast_only, key=lambda b: b.full_pipeline_ms).id})")
    if py_only:
        print(f"stdlib ast (Python) mean stage-of-pipeline: {statistics.mean(b.ast_stage_pct_of_pipeline for b in py_only):.1f}%")
        print(f"stdlib ast mean full-pipeline time: {statistics.mean(b.full_pipeline_ms for b in py_only):.3f}ms")

    print()
    print("=" * 78)
    print("Part 2 - Large-file scaling (synthetic, NOT part of the benchmark corpus)")
    print("=" * 78)
    for language in ("javascript", "typescript"):
        print(f"-- {language} --")
        for point in measure_scaling(language):
            print(f"  n={point.n:>5} functions, {point.lines:>6} lines: {point.time_ms:>8.3f}ms")

    print()
    nested = measure_nested_declarations()
    print(f"Nested declarations (30 classes x 10 methods = 300 methods, {nested.lines} lines): {nested.time_ms:.3f}ms")

    print()
    print("=" * 78)
    print("Part 3 - Malformed source / ERROR-node handling performance")
    print("=" * 78)
    for m in measure_malformed_source():
        outcome = "raised SyntaxError (Python, expected)" if m.raised else f"{m.compressed_line_count} lines compressed"
        print(f"  {m.label:<38} ({m.language:<10}): {m.time_ms:>7.3f}ms - {outcome}")

    print()
    print("=" * 78)
    print("Part 4 - File with no summarizable content (AST finds nothing to do)")
    print("=" * 78)
    skipped = measure_skipped_no_summarization()
    print(f"  interfaces/consts-only TS file, {skipped.lines} lines: {skipped.time_ms:.3f}ms, 0 lines compressed (correct)")


if __name__ == "__main__":
    _print_report()
