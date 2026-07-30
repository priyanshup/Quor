"""Deterministic regression tests for the QB-072 performance follow-up.

`tests/benchmarks/repo_intel_benchmark.py` measures CPU/peak-memory/
elapsed-time/cache-hit-ratio and prints a human-readable report but never
asserts pass/fail (mirrors this repo's existing `report.py`/
`benchmark_runner.py` split). This file turns those measurements into
actual regression protection via *count-based* assertions only (how many
files got re-extracted, what the reported cache hit ratio/action is) —
never a timing comparison between two independently `measure()`d
scenarios, per this repo's own testing rules (docs/final/CLAUDE.md Rule 2
— no known-flaky test tolerated).

**This file used to also assert `cpu_seconds` orderings between
scenarios (e.g. "an incremental build must be faster than a cold
build") — removed after five consecutive real CI failures proved no
fixed tolerance or choice of comparison pair converges:**
- `elapsed_seconds` (wall-clock) flipped from ordinary scheduling jitter
  alone (`one_modified` 0.368s vs. `cold` 0.276s — backwards) — switched
  to `cpu_seconds` (`time.process_time()`) to exclude time blocked on
  I/O wait.
- `cpu_seconds` alone still flipped (`hundred_modified` on `ubuntu-latest`,
  `one_modified` on `macos-latest`, ~1-2% each) — added a 15% tolerance.
- The tolerance still missed by a wide, non-noise margin (`one_modified`
  0.6065s vs. `cold`'s 0.4708s, ~29% slower) — traced to a real
  architectural reason (`intel.py::_refresh_from_cache()` pays fixed
  cache-load/diff costs `cold`'s `_full_rebuild()` never pays, so a small
  diff's marginal re-parse saving can be smaller than that fixed
  overhead) — switched `one_modified`/`ten_modified` to compare against
  `ten_modified`/`hundred_modified` instead of `cold`, since those share
  the identical fixed overhead and should isolate a genuinely monotonic
  relationship.
- That *still* flipped, by an even wider margin (`one_modified` 0.3899s
  vs. `ten_modified`'s 0.2359s — `one_modified` now ~65% *slower* despite
  reextracting fewer files) — this is no longer explainable as either
  noise or a comparison-basis error; `macos-latest` in particular has now
  produced three unrelated timing reversals of escalating, unpredictable
  magnitude (~1%, ~29%, ~65%) for this same style of comparison, which
  means single-`measure()`-sample `cpu_seconds` comparisons are not a
  reliable signal on these shared/virtualized CI runners at all, for any
  pair of scenarios, at any tolerance.

This is the documented, reviewed exception Rule 2 asks for rather than
another silent tolerance bump: every cross-scenario `cpu_seconds`
assertion has been removed. `tests/benchmarks/repo_intel_benchmark.py`
still measures and reports timing for humans reading `quor gain`-style
output; it is simply no longer asserted on in CI. The count-based
assertions below (`files_reextracted`, `cache_hit_ratio`, `action`) are
exact-value, zero-timing-dependency checks and remain this file's real
regression protection, exactly as this docstring's opening paragraph
already promised.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import platformdirs
import pytest

from tests.benchmarks.repo_intel_benchmark import ScenarioResult, build_synthetic_repo, measure

_BENCHMARK_FILE_COUNT = 150
"""Bumped from 60 (QB-072) to 150 by QB-077 so there's room for the new
"one hundred files modified" scenario (indices 12..111) without overlapping
the pre-existing rename (`_BENCHMARK_FILE_COUNT - 1`) / delete
(`_BENCHMARK_FILE_COUNT - 2`) scenarios — matches
`tests/benchmarks/repo_intel_benchmark.py`'s own `DEFAULT_FILE_COUNT`."""


@pytest.fixture(scope="module")
def _scenario_results() -> dict[str, ScenarioResult]:
    """Run all seven scenarios once per test module (not once per test) —
    each scenario builds on the previous one's on-disk cache state, so
    they must run in this exact sequence, and re-running the expensive
    cold build per assertion would multiply this file's cost for no
    benefit.

    `tests/conftest.py`'s autouse `_isolate_platformdirs` fixture is
    function-scoped (it depends on the function-scoped `tmp_path`) and
    cannot be requested by this module-scoped fixture — pytest would
    reject the scope mismatch, and even if it didn't, a module-scoped
    fixture's body runs *before* per-function autouse fixtures are set up,
    so relying on it here would risk this fixture writing to the real
    `platformdirs.user_data_dir("quor")` on the machine running the tests
    (exactly what Common Mistake #7 in docs/final/CLAUDE.md warns against).
    A `pytest.MonkeyPatch()` instance, used directly rather than via the
    function-scoped `monkeypatch` fixture, isolates platformdirs here
    regardless of fixture scope/ordering.
    """
    mp = pytest.MonkeyPatch()
    tmp_root = Path(tempfile.mkdtemp(prefix="quor_repo_intel_bench_test_"))
    cache_root = tmp_root / "platformdirs"
    mp.setattr(platformdirs, "user_data_dir", lambda *_a, **_kw: str(cache_root / "data"))
    mp.setattr(platformdirs, "user_config_dir", lambda *_a, **_kw: str(cache_root / "config"))
    try:
        repo = tmp_root / "repo"
        build_synthetic_repo(repo, _BENCHMARK_FILE_COUNT)
        package_dir = repo / "src" / "pkg"

        results = {
            "cold": measure(repo, "cold build"),
            "warm": measure(repo, "warm build"),
        }

        (package_dir / "mod_1.py").write_text("def func_1():\n    return 999\n", encoding="utf-8")
        results["one_modified"] = measure(repo, "one file modified")

        for i in range(2, 12):
            (package_dir / f"mod_{i}.py").write_text(f"def func_{i}():\n    return {i * 100}\n", encoding="utf-8")
        results["ten_modified"] = measure(repo, "ten files modified")

        for i in range(12, 112):
            (package_dir / f"mod_{i}.py").write_text(f"def func_{i}():\n    return {i * 1000}\n", encoding="utf-8")
        results["hundred_modified"] = measure(repo, "one hundred files modified")

        (package_dir / f"mod_{_BENCHMARK_FILE_COUNT - 1}.py").rename(package_dir / "mod_renamed.py")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        results["renamed"] = measure(repo, "one file renamed")

        (package_dir / f"mod_{_BENCHMARK_FILE_COUNT - 2}.py").unlink()
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        results["deleted"] = measure(repo, "one file deleted")

        return results
    finally:
        mp.undo()
        shutil.rmtree(tmp_root, ignore_errors=True)


class TestColdBuild:
    def test_reextracts_every_file(self, _scenario_results: dict[str, ScenarioResult]) -> None:
        cold = _scenario_results["cold"]
        assert cold.action == "onboarded"
        assert cold.files_scanned == _BENCHMARK_FILE_COUNT
        assert cold.files_reextracted == _BENCHMARK_FILE_COUNT
        assert cold.cache_hit_ratio == 0.0


class TestWarmBuild:
    def test_reextracts_nothing(self, _scenario_results: dict[str, ScenarioResult]) -> None:
        warm = _scenario_results["warm"]
        assert warm.action == "cache_hit"
        assert warm.files_reextracted == 0
        assert warm.cache_hit_ratio == 1.0


class TestOneFileModified:
    def test_reextracts_exactly_one_file(self, _scenario_results: dict[str, ScenarioResult]) -> None:
        one_modified = _scenario_results["one_modified"]
        assert one_modified.action == "incremental"
        assert one_modified.files_reextracted == 1
        assert one_modified.cache_hit_ratio == pytest.approx(1.0 - 1 / _BENCHMARK_FILE_COUNT)


class TestTenFilesModified:
    def test_reextracts_exactly_ten_files(self, _scenario_results: dict[str, ScenarioResult]) -> None:
        ten_modified = _scenario_results["ten_modified"]
        assert ten_modified.action == "incremental"
        assert ten_modified.files_reextracted == 10
        assert ten_modified.cache_hit_ratio == pytest.approx(1.0 - 10 / _BENCHMARK_FILE_COUNT)


class TestHundredFilesModified:
    def test_reextracts_exactly_one_hundred_files(self, _scenario_results: dict[str, ScenarioResult]) -> None:
        hundred_modified = _scenario_results["hundred_modified"]
        assert hundred_modified.action == "incremental"
        assert hundred_modified.files_reextracted == 100
        assert hundred_modified.cache_hit_ratio == pytest.approx(1.0 - 100 / _BENCHMARK_FILE_COUNT)


class TestFileRenamed:
    def test_reextracts_nothing(self, _scenario_results: dict[str, ScenarioResult]) -> None:
        renamed = _scenario_results["renamed"]
        assert renamed.action == "incremental"
        assert renamed.files_reextracted == 0
        assert renamed.cache_hit_ratio == 1.0


class TestFileDeleted:
    def test_reextracts_nothing(self, _scenario_results: dict[str, ScenarioResult]) -> None:
        deleted = _scenario_results["deleted"]
        assert deleted.action == "incremental"
        assert deleted.files_reextracted == 0
        assert deleted.cache_hit_ratio == 1.0
        assert deleted.files_scanned == _BENCHMARK_FILE_COUNT - 1  # one fewer file walked


class TestFileIntelligenceLookup:
    """QB-079: the file_intelligence.json lookup a consumer (the Read hook
    first) performs must cost a small, roughly constant amount of CPU
    time — not scale with total repo size the way loading
    `symbol_facts.json`/`graph_facts.json` in full does (this item's own
    investigation measured that combined load at 114ms for the real Quor
    repository). Independent of the module-scoped `_scenario_results`
    fixture above — a small, fast repo of its own, not the 150-file one —
    since this test only needs the lookup to exist and be cheap, not to
    exercise the full scenario matrix."""

    def test_lookup_cpu_time_stays_well_under_the_hook_budget(self, tmp_path: Path) -> None:
        from quor.pipeline.repo_profile.intel import ensure_repo_intelligence
        from tests.benchmarks.repo_intel_benchmark import (
            build_synthetic_repo,
            measure_file_intelligence_lookup,
        )

        repo = tmp_path / "repo"
        build_synthetic_repo(repo, 30)
        ensure_repo_intelligence(repo)

        result = measure_file_intelligence_lookup(repo, 30)

        # Generous bound, not a tight threshold — CLAUDE.md's own hook
        # budget is <10ms; this leaves real headroom for CI noise while
        # still catching a regression back toward "loads the whole
        # multi-MB symbol_facts.json/graph_facts.json instead" (which this
        # item's own investigation measured at 114ms, over 2 orders of
        # magnitude above this bound).
        assert result.cpu_seconds < 0.05


class TestSearchLatency:
    """QB-080: `quor search`'s own cost must stay well under its 100ms
    target and, like `TestFileIntelligenceLookup` above, must not scale
    with total repo size the way loading `symbol_facts.json`/
    `graph_facts.json` in full does. A small, fast repo of its own —
    this only needs `search()` to exist and be cheap, not to exercise the
    full scenario matrix."""

    def test_search_cpu_time_stays_well_under_the_100ms_target(self, tmp_path: Path) -> None:
        from quor.pipeline.repo_profile.intel import ensure_repo_intelligence
        from tests.benchmarks.repo_intel_benchmark import (
            build_synthetic_repo,
            measure_search_latency,
        )

        repo = tmp_path / "repo"
        build_synthetic_repo(repo, 30)
        ensure_repo_intelligence(repo)

        result = measure_search_latency(repo, 30)

        # Generous bound, not a tight threshold — QB-080's own target is
        # <100ms; this leaves real headroom for CI noise while still
        # catching a regression toward something that scales with repo
        # size (e.g. accidentally loading symbol_facts.json/graph_facts.json).
        assert result.cpu_seconds < 0.1

    def test_reverse_import_index_cpu_time_stays_small(self, tmp_path: Path) -> None:
        from quor.pipeline.repo_profile.intel import ensure_repo_intelligence
        from tests.benchmarks.repo_intel_benchmark import (
            build_synthetic_repo,
            measure_reverse_import_index,
        )

        repo = tmp_path / "repo"
        build_synthetic_repo(repo, 30)
        ensure_repo_intelligence(repo)

        result = measure_reverse_import_index(repo, 30)

        # `_build_reverse_import_index()` is the one genuinely new
        # algorithmic step QB-080 introduces — asserted separately from the
        # end-to-end search bound above so a future regression to something
        # worse than linear is caught precisely, not just folded into the
        # total.
        assert result.cpu_seconds < 0.05


class TestRelevantFilesLatency:
    """QB-081: the Read hook's "Relevant repository files" feature adds
    three phases on top of everything QB-079/QB-080 already cost — query-
    term extraction, a merged multi-query `search()` pass (up to
    `query_extract.MAX_QUERY_TERMS` full `search()` calls), and rendering.
    Each is bounded separately, mirroring `TestSearchLatency`'s own
    per-phase split, so a regression in any one phase is caught precisely."""

    def test_extraction_cpu_time_is_negligible(self, tmp_path: Path) -> None:
        from quor.pipeline.repo_profile.intel import ensure_repo_intelligence
        from tests.benchmarks.repo_intel_benchmark import (
            build_synthetic_repo,
            measure_relevant_files_latency,
        )

        repo = tmp_path / "repo"
        build_synthetic_repo(repo, 30)
        ensure_repo_intelligence(repo)

        result = measure_relevant_files_latency(repo, 30)

        # Pure regex/string work over one bounded prompt string — no
        # repository access at all, so this should stay effectively free
        # regardless of repo size. 0.05s (not a tighter bound like 0.01s),
        # matching this class's other "negligible" checks below — confirmed
        # on real windows-latest CI that `time.process_time()`'s clock
        # granularity there can round a sub-millisecond operation up to a
        # full tick (observed: exactly 0.015625s = 1/64s, the classic
        # ~15.6ms Windows system-timer tick), so anything below ~20ms is not
        # a reliable "negligible work" threshold on that platform specifically.
        assert result.extraction_cpu_seconds < 0.05

    def test_merged_search_cpu_time_stays_bounded_even_at_max_query_terms(self, tmp_path: Path) -> None:
        from quor.pipeline.repo_profile.intel import ensure_repo_intelligence
        from tests.benchmarks.repo_intel_benchmark import (
            build_synthetic_repo,
            measure_relevant_files_latency,
        )

        repo = tmp_path / "repo"
        build_synthetic_repo(repo, 30)
        ensure_repo_intelligence(repo)

        result = measure_relevant_files_latency(repo, 30)

        # Worst case is `query_extract.MAX_QUERY_TERMS` (4) independent
        # `search()` calls — generously bounded at 4x `TestSearchLatency`'s
        # own single-query 100ms ceiling, not a tight threshold.
        assert result.search_cpu_seconds < 0.4

    def test_render_cpu_time_is_negligible(self, tmp_path: Path) -> None:
        from quor.pipeline.repo_profile.intel import ensure_repo_intelligence
        from tests.benchmarks.repo_intel_benchmark import (
            build_synthetic_repo,
            measure_relevant_files_latency,
        )

        repo = tmp_path / "repo"
        build_synthetic_repo(repo, 30)
        ensure_repo_intelligence(repo)

        result = measure_relevant_files_latency(repo, 30)

        # A handful of string-formatted lines (bounded by
        # `claude_read.MAX_RELEVANT_FILES`) — should stay effectively free.
        # 0.05s, not 0.01s — see test_extraction_cpu_time_is_negligible
        # above for why (Windows process-time clock granularity).
        assert result.render_cpu_seconds < 0.05
