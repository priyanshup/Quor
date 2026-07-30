"""Deterministic regression tests for the QB-072 performance follow-up.

`tests/benchmarks/repo_intel_benchmark.py` measures CPU/peak-memory/
elapsed-time/cache-hit-ratio and prints a human-readable report but never
asserts pass/fail (mirrors this repo's existing `report.py`/
`benchmark_runner.py` split). This file is where those measurements
become actual regression protection — via *count-based* assertions
(how many files got re-extracted, what the reported cache hit ratio is),
never a flaky wall-clock threshold, per this repo's own testing rules
(docs/final/CLAUDE.md Rule 2 — no known-flaky test tolerated).

**`elapsed_seconds` (wall-clock, `time.monotonic()`) is deliberately never
compared between scenarios.** An earlier version of this file's four
`test_is_faster_than_cold_build` tests did exactly that, on the reasoning
that "incremental must be faster than full" needs some timing check as a
smoke guard — but on a shared/noisy CI runner, two near-simultaneous
sub-second `elapsed_seconds` measurements can be flipped by ordinary
scheduling/IO jitter alone (confirmed: a real CI run recorded `one_modified`
elapsed at 0.368s vs. `cold` at 0.276s — backwards — while the same run's
`cpu_seconds` was correctly ordered, 0.183s vs. 0.273s). Every comparative
timing check here uses `cpu_seconds` (`time.process_time()`, user+system
CPU time, excludes time blocked on git subprocess I/O wait — see
`measure()`'s own docstring) instead: a real measurement of how much actual
work each scenario did, immune to being descheduled while another process
on the runner gets a turn, so no tolerance margin is needed to make it
non-flaky.
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

    def test_is_faster_than_cold_build(self, _scenario_results: dict[str, ScenarioResult]) -> None:
        cold = _scenario_results["cold"]
        warm = _scenario_results["warm"]
        # A structural comparison, not a wall-clock threshold: a cache hit
        # skips every parse and every write, so it must consume meaningfully
        # less CPU time than a cold build that does both, on any machine —
        # this is the whole point of the QB-072 cache. `cpu_seconds`, not
        # `elapsed_seconds` — see this module's own docstring for why.
        assert warm.cpu_seconds < cold.cpu_seconds


class TestOneFileModified:
    def test_reextracts_exactly_one_file(self, _scenario_results: dict[str, ScenarioResult]) -> None:
        one_modified = _scenario_results["one_modified"]
        assert one_modified.action == "incremental"
        assert one_modified.files_reextracted == 1
        assert one_modified.cache_hit_ratio == pytest.approx(1.0 - 1 / _BENCHMARK_FILE_COUNT)

    def test_is_faster_than_cold_build(self, _scenario_results: dict[str, ScenarioResult]) -> None:
        cold = _scenario_results["cold"]
        one_modified = _scenario_results["one_modified"]
        assert one_modified.cpu_seconds < cold.cpu_seconds


class TestTenFilesModified:
    def test_reextracts_exactly_ten_files(self, _scenario_results: dict[str, ScenarioResult]) -> None:
        ten_modified = _scenario_results["ten_modified"]
        assert ten_modified.action == "incremental"
        assert ten_modified.files_reextracted == 10
        assert ten_modified.cache_hit_ratio == pytest.approx(1.0 - 10 / _BENCHMARK_FILE_COUNT)

    def test_is_faster_than_cold_build(self, _scenario_results: dict[str, ScenarioResult]) -> None:
        cold = _scenario_results["cold"]
        ten_modified = _scenario_results["ten_modified"]
        assert ten_modified.cpu_seconds < cold.cpu_seconds


class TestHundredFilesModified:
    def test_reextracts_exactly_one_hundred_files(self, _scenario_results: dict[str, ScenarioResult]) -> None:
        hundred_modified = _scenario_results["hundred_modified"]
        assert hundred_modified.action == "incremental"
        assert hundred_modified.files_reextracted == 100
        assert hundred_modified.cache_hit_ratio == pytest.approx(1.0 - 100 / _BENCHMARK_FILE_COUNT)

    def test_is_faster_than_cold_build(self, _scenario_results: dict[str, ScenarioResult]) -> None:
        cold = _scenario_results["cold"]
        hundred_modified = _scenario_results["hundred_modified"]
        assert hundred_modified.cpu_seconds < cold.cpu_seconds


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
