"""Repository Intelligence performance benchmark (QB-072 perf follow-up,
extended with a "100 files modified" scenario by QB-077).

Measures CPU time, peak memory, elapsed wall-clock time, and cache hit
ratio across seven scenarios: cold build, warm build (unchanged), one
modified file, ten modified files, one hundred modified files, one renamed
file, and one deleted file — the QB-072 performance follow-up's original
six plus the 100-file scenario QB-077's validation section asks for.

Unlike `tests/benchmarks/run_benchmarks.py` (compression ratio vs. a
committed `baseline.json`), this benchmark has no historical baseline to
regress against — repository-intelligence build time has no equivalent
"correct" percentage the way a filter's compression ratio does. It exists
to make the incremental-rebuild optimization's actual cost/benefit
observable (run `python -m tests.benchmarks.repo_intel_benchmark` for the
full human-readable report) and to back the deterministic, count-based
regression assertions in `tests/unit/test_repo_intel_benchmark.py` — this
module only measures and reports, it never asserts pass/fail itself,
mirroring `report.py`'s "presentation only" split from
`benchmark_runner.py`'s computation.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
import tracemalloc
from dataclasses import dataclass
from pathlib import Path

from quor.pipeline.repo_profile import intel_store, search
from quor.pipeline.repo_profile.intel import ensure_repo_intelligence

DEFAULT_FILE_COUNT = 150


@dataclass(frozen=True)
class ScenarioResult:
    """One scenario's measured cost — every field here is either a direct
    measurement (elapsed/CPU/peak memory) or read straight off the
    `RepoIntelligence` the scenario produced (action/cache_hit_ratio/
    files_scanned/files_reextracted), never derived/estimated."""

    name: str
    action: str
    elapsed_seconds: float
    cpu_seconds: float
    peak_memory_bytes: int
    cache_hit_ratio: float
    files_scanned: int
    files_reextracted: int


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "bench@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Bench"], cwd=root, check=True)


def _git_add_all(root: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)


def build_synthetic_repo(root: Path, file_count: int = DEFAULT_FILE_COUNT) -> None:
    """A deterministic, git-committed synthetic repo of `file_count` Python
    modules under `src/pkg/`, each (after the first) importing its
    predecessor and calling one of its functions — enough real symbols/
    relationships to exercise both `quor symbols`' and `quor graph`'s
    parsing and cross-file resolution paths, not just a pile of empty
    files.

    Deliberately nested under a subdirectory, not flat at the repo root:
    an earlier version of this benchmark put all `file_count` modules
    directly at the repo root, which — for large `file_count` — is not a
    realistic repo shape and pathologically maximized the cost of
    `entry_points.py`'s deliberately root-scoped `__main__`-guard content
    scan (bounded to root-level `.py` files specifically so it never
    becomes a whole-tree scan — see that module's own docstring), making
    `quor map`'s full-rebuild-on-any-change look far more expensive than
    it is for any real, normally-structured repository. Nesting under
    `src/pkg/` keeps root-level file count at effectively zero, matching
    what the vast majority of real repos actually look like.
    """
    package_dir = root / "src" / "pkg"
    package_dir.mkdir(parents=True, exist_ok=True)
    for i in range(file_count):
        if i == 0:
            source = f"def func_{i}():\n    return {i}\n"
        else:
            source = (
                f"from mod_{i - 1} import func_{i - 1}\n\n\n"
                f"def func_{i}():\n    return {i}\n\n\n"
                f"def caller_{i}():\n    return func_{i - 1}()\n"
            )
        (package_dir / f"mod_{i}.py").write_text(source, encoding="utf-8")
    _init_git_repo(root)
    _git_add_all(root)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


def measure(root: Path, name: str, *, rebuild: bool = False) -> ScenarioResult:
    """Run `ensure_repo_intelligence()` once against `root`, measuring its
    cost. `tracemalloc` (stdlib, no extra dependency) gives peak Python
    heap allocation, the aspect of "memory" this pure-Python pipeline
    actually controls; `time.process_time()` gives CPU time (user+system,
    excludes time spent blocked e.g. on git subprocess I/O wait);
    `time.monotonic()` gives wall-clock elapsed time."""
    tracemalloc.start()
    try:
        cpu_start = time.process_time()
        wall_start = time.monotonic()
        result = ensure_repo_intelligence(root, rebuild=rebuild)
        elapsed = time.monotonic() - wall_start
        cpu = time.process_time() - cpu_start
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    return ScenarioResult(
        name=name,
        action=result.action,
        elapsed_seconds=elapsed,
        cpu_seconds=cpu,
        peak_memory_bytes=peak,
        cache_hit_ratio=result.cache_hit_ratio,
        files_scanned=result.files_scanned,
        files_reextracted=result.files_reextracted,
    )


@dataclass(frozen=True)
class FileIntelligenceLookupResult:
    """QB-079: measures the O(1) per-file lookup a consumer (the Read hook
    first) performs against `file_intelligence.json` alone —
    `intel_store.load_file_intelligence()` plus one dict `.get()` — never
    the full `ensure_repo_intelligence()` call, and never
    `symbol_facts.json`/`graph_facts.json`, the two files this item's own
    investigation measured at 114ms combined for this repository. Reported,
    not asserted (see module docstring) — the point is to make "this
    doesn't scale like a full four-file cache load does" empirically
    visible; `tests/unit/test_repo_intel_benchmark.py` is where a bound
    actually gets asserted."""

    file_count: int
    cpu_seconds: float
    elapsed_seconds: float


def measure_file_intelligence_lookup(root: Path, file_count: int) -> FileIntelligenceLookupResult:
    """Requires `file_intelligence.json` to already exist at `root` (call
    `ensure_repo_intelligence(root)` first) — this function only measures
    the read-back lookup, never a build."""
    cpu_start = time.process_time()
    wall_start = time.monotonic()
    entries = intel_store.load_file_intelligence(root)
    if entries is None:
        raise RuntimeError("file_intelligence.json missing — call ensure_repo_intelligence(root) first")
    entries.get("src/pkg/mod_0.py")
    elapsed = time.monotonic() - wall_start
    cpu = time.process_time() - cpu_start
    return FileIntelligenceLookupResult(file_count=file_count, cpu_seconds=cpu, elapsed_seconds=elapsed)


def run_all_scenarios(file_count: int = DEFAULT_FILE_COUNT) -> list[ScenarioResult]:
    """Run all six scenarios in sequence against one synthetic repo (each
    scenario builds on the previous one's on-disk state, the same way a
    real developer's session-over-session usage would) and return their
    measurements in order."""
    tmp_root = Path(tempfile.mkdtemp(prefix="quor_repo_intel_bench_"))
    try:
        repo = tmp_root / "repo"
        build_synthetic_repo(repo, file_count)
        package_dir = repo / "src" / "pkg"

        results = [
            measure(repo, "cold build"),
            measure(repo, "warm build (unchanged)"),
        ]

        (package_dir / "mod_1.py").write_text("def func_1():\n    return 999\n", encoding="utf-8")
        results.append(measure(repo, "one file modified"))

        for i in range(2, 12):
            (package_dir / f"mod_{i}.py").write_text(f"def func_{i}():\n    return {i * 100}\n", encoding="utf-8")
        results.append(measure(repo, "ten files modified"))

        for i in range(12, 112):
            (package_dir / f"mod_{i}.py").write_text(f"def func_{i}():\n    return {i * 1000}\n", encoding="utf-8")
        results.append(measure(repo, "one hundred files modified"))

        (package_dir / f"mod_{file_count - 1}.py").rename(package_dir / "mod_renamed.py")
        _git_add_all(repo)
        results.append(measure(repo, "one file renamed"))

        (package_dir / f"mod_{file_count - 2}.py").unlink()
        _git_add_all(repo)
        results.append(measure(repo, "one file deleted"))

        return results
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def run_file_intelligence_lookup_scenarios() -> list[FileIntelligenceLookupResult]:
    """Builds two independent synthetic repos — `DEFAULT_FILE_COUNT` (150,
    matching every other scenario in this module) and a much larger one
    (2000 files) — and measures the file_intelligence.json lookup alone
    against each, to make its cost-independent-of-repo-size empirically
    visible. Deliberately not part of `run_all_scenarios()`/wired into
    `tests/unit/test_repo_intel_benchmark.py`'s automated suite — building
    a 2000-file repo is real, non-trivial cost this module's docstring
    already scopes to "run manually via `python -m ...`", not something
    the default `pytest tests/unit/` gate should pay on every run."""
    results: list[FileIntelligenceLookupResult] = []
    for file_count in (DEFAULT_FILE_COUNT, 2000):
        tmp_root = Path(tempfile.mkdtemp(prefix="quor_file_intel_lookup_bench_"))
        try:
            repo = tmp_root / "repo"
            build_synthetic_repo(repo, file_count)
            ensure_repo_intelligence(repo)
            results.append(measure_file_intelligence_lookup(repo, file_count))
        finally:
            shutil.rmtree(tmp_root, ignore_errors=True)
    return results


def render_file_intelligence_lookup_report(results: list[FileIntelligenceLookupResult]) -> str:
    lines = [
        "## file_intelligence.json Lookup (QB-079)",
        "",
        "The Read hook's per-file lookup — `intel_store.load_file_intelligence()` "
        "+ one dict `.get()` — measured independently of repo size, in contrast "
        "to the full four-file cache load this item's own investigation measured "
        "at 114ms for this repository's `symbol_facts.json`+`graph_facts.json`.",
        "",
        "| Repo Size (files) | Elapsed (s) | CPU (s) |",
        "|---|---|---|",
    ]
    for r in results:
        lines.append(f"| {r.file_count} | {r.elapsed_seconds:.4f} | {r.cpu_seconds:.4f} |")
    lines.append("")
    return "\n".join(lines)


@dataclass(frozen=True)
class SearchLatencyResult:
    """QB-080: measures `search.search()` end-to-end against an already-
    loaded `file_intelligence.json` — the full evidence-scoring + ranking
    pass, not just the raw file load `measure_file_intelligence_lookup()`
    covers. Reported, not asserted (see module docstring);
    `tests/unit/test_repo_intel_benchmark.py` is where a bound actually
    gets asserted."""

    file_count: int
    cpu_seconds: float
    elapsed_seconds: float


def measure_search_latency(root: Path, file_count: int, query: str = "mod_1") -> SearchLatencyResult:
    """Requires `file_intelligence.json` to already exist at `root`. Loads
    the cache once (mirroring what a real `quor search` CLI invocation
    does) outside the timer, then measures the `search.search()` call
    alone."""
    entries = intel_store.load_file_intelligence(root)
    if entries is None:
        raise RuntimeError("file_intelligence.json missing — call ensure_repo_intelligence(root) first")
    cpu_start = time.process_time()
    wall_start = time.monotonic()
    search.search(entries, query)
    elapsed = time.monotonic() - wall_start
    cpu = time.process_time() - cpu_start
    return SearchLatencyResult(file_count=file_count, cpu_seconds=cpu, elapsed_seconds=elapsed)


@dataclass(frozen=True)
class ReverseImportIndexResult:
    """QB-080: measures `search._build_reverse_import_index()` alone — the
    one genuinely new algorithmic step this item introduces (every other
    part of `search()` is a bounded per-file scan). Reported separately
    from `measure_search_latency()`'s end-to-end number so a future
    accidental regression to something worse than linear is directly
    visible, not folded invisibly into the total."""

    file_count: int
    cpu_seconds: float
    elapsed_seconds: float


def measure_reverse_import_index(root: Path, file_count: int) -> ReverseImportIndexResult:
    entries = intel_store.load_file_intelligence(root)
    if entries is None:
        raise RuntimeError("file_intelligence.json missing — call ensure_repo_intelligence(root) first")
    cpu_start = time.process_time()
    wall_start = time.monotonic()
    search._build_reverse_import_index(entries)
    elapsed = time.monotonic() - wall_start
    cpu = time.process_time() - cpu_start
    return ReverseImportIndexResult(file_count=file_count, cpu_seconds=cpu, elapsed_seconds=elapsed)


def run_search_latency_scenarios() -> tuple[list[SearchLatencyResult], list[ReverseImportIndexResult]]:
    """Same two-repo-size shape as `run_file_intelligence_lookup_scenarios()`
    (150/2000 files) — deliberately not wired into the automated
    `pytest tests/unit/` suite for the same reason (building a 2000-file
    repo is real, non-trivial cost this module's docstring already scopes
    to "run manually via `python -m ...`")."""
    search_results: list[SearchLatencyResult] = []
    reverse_index_results: list[ReverseImportIndexResult] = []
    for file_count in (DEFAULT_FILE_COUNT, 2000):
        tmp_root = Path(tempfile.mkdtemp(prefix="quor_search_latency_bench_"))
        try:
            repo = tmp_root / "repo"
            build_synthetic_repo(repo, file_count)
            ensure_repo_intelligence(repo)
            search_results.append(measure_search_latency(repo, file_count))
            reverse_index_results.append(measure_reverse_import_index(repo, file_count))
        finally:
            shutil.rmtree(tmp_root, ignore_errors=True)
    return search_results, reverse_index_results


def render_search_latency_report(
    search_results: list[SearchLatencyResult], reverse_index_results: list[ReverseImportIndexResult]
) -> str:
    lines = [
        "## quor search Latency (QB-080)",
        "",
        "End-to-end `search.search()` cost against an already-loaded "
        "`file_intelligence.json`, plus `_build_reverse_import_index()` "
        "measured separately as the one genuinely new algorithmic step "
        "this item introduces.",
        "",
        "| Repo Size (files) | Search Elapsed (s) | Search CPU (s) | "
        "Reverse Index Elapsed (s) | Reverse Index CPU (s) |",
        "|---|---|---|---|---|",
    ]
    for s, r in zip(search_results, reverse_index_results, strict=True):
        lines.append(
            f"| {s.file_count} | {s.elapsed_seconds:.4f} | {s.cpu_seconds:.4f} | "
            f"{r.elapsed_seconds:.4f} | {r.cpu_seconds:.4f} |"
        )
    lines.append("")
    return "\n".join(lines)


def render_report(results: list[ScenarioResult]) -> str:
    lines = [
        "# Repository Intelligence Performance Benchmark (QB-072)",
        "",
        "No committed baseline — see this module's own docstring for why. "
        "Regression protection instead comes from the deterministic, "
        "count-based assertions in `tests/unit/test_repo_intel_benchmark.py`.",
        "",
        "| Scenario | Action | Elapsed (s) | CPU (s) | Peak Memory (MB) | "
        "Cache Hit Ratio | Files Scanned | Files Re-extracted |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.name} | {r.action} | {r.elapsed_seconds:.4f} | {r.cpu_seconds:.4f} | "
            f"{r.peak_memory_bytes / 1_000_000:.2f} | {r.cache_hit_ratio:.2f} | "
            f"{r.files_scanned} | {r.files_reextracted} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    results = run_all_scenarios()
    report = render_report(results)

    lookup_results = run_file_intelligence_lookup_scenarios()
    lookup_report = render_file_intelligence_lookup_report(lookup_results)

    search_results, reverse_index_results = run_search_latency_scenarios()
    search_report = render_search_latency_report(search_results, reverse_index_results)

    full_report = report + "\n" + lookup_report + "\n" + search_report
    print(full_report)

    output_path = Path(__file__).parent / "results" / "repo-intel-benchmark-report.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(full_report, encoding="utf-8")


if __name__ == "__main__":
    main()
