"""Unit tests for `quor/pipeline/repo_profile/explorer.py` (QB-078).

Exercises the cache-only lookup/aggregate logic directly (not through the
CLI — see `tests/unit/test_cli_explore.py` for that), warming the cache via
`ensure_repo_intelligence()` the same way `test_cli_repo.py` warms it via
`quor map`/`quor symbols`/`quor graph`.
"""

from __future__ import annotations

import dataclasses
import time
from pathlib import Path

from quor.pipeline.repo_profile import intel_store
from quor.pipeline.repo_profile.explorer import (
    CacheUnavailable,
    ExplorerCache,
    file_dependencies,
    file_summary,
    file_used_by,
    find_symbol,
    load_cache,
    repo_stats,
)
from quor.pipeline.repo_profile.intel import ensure_repo_intelligence


def _make_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    (repo / "service.py").write_text("class UserService:\n    def run(self):\n        pass\n", encoding="utf-8")
    (repo / "mock_service.py").write_text("class UserService:\n    pass\n", encoding="utf-8")
    (repo / "consumer.py").write_text(
        "from .service import UserService\n\n\nclass Consumer:\n    def run(self):\n        UserService()\n",
        encoding="utf-8",
    )
    return repo


def _warm(repo: Path) -> None:
    ensure_repo_intelligence(repo)


def _load(repo: Path) -> ExplorerCache:
    cache = load_cache(repo)
    assert isinstance(cache, ExplorerCache)
    return cache


class TestLoadCache:
    def test_missing_reports_missing_status(self, tmp_path: Path) -> None:
        repo = tmp_path / "empty"
        repo.mkdir()

        result = load_cache(repo)

        assert isinstance(result, CacheUnavailable)
        assert result.status == "missing"

    def test_corrupted_state_reports_corrupted_status(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _warm(repo)
        (intel_store.cache_dir(repo) / "state.json").write_bytes(b"{ not json")

        result = load_cache(repo)

        assert isinstance(result, CacheUnavailable)
        assert result.status == "corrupted"

    def test_corrupted_sibling_artifact_reports_corrupted_status(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _warm(repo)
        (intel_store.cache_dir(repo) / "symbol_facts.json").write_bytes(b"{ not json")

        result = load_cache(repo)

        assert isinstance(result, CacheUnavailable)
        assert result.status == "corrupted"

    def test_fresh_cache_loads(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _warm(repo)

        result = load_cache(repo)

        assert isinstance(result, ExplorerCache)
        assert result.cache_status == "fresh"

    def test_stale_on_quor_version_mismatch(self, tmp_path: Path, monkeypatch) -> None:
        repo = _make_repo(tmp_path)
        _warm(repo)
        monkeypatch.setattr("quor.pipeline.repo_profile.explorer.quor.__version__", "0.0.0-test")

        result = load_cache(repo)

        assert isinstance(result, ExplorerCache)
        assert result.cache_status == "stale"

    def test_never_walks_the_repository(self, tmp_path: Path, monkeypatch) -> None:
        """`load_cache()` must reach zero code path that walks the repo —
        mirrors `test_cli_repo.py`'s QB-076-era
        `test_never_walks_the_repository`, patching both modules that could
        plausibly call it."""
        repo = _make_repo(tmp_path)
        _warm(repo)

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("quor explore must never walk the repository")

        monkeypatch.setattr("quor.pipeline.repo_profile.walk.walk_repository", _boom)
        monkeypatch.setattr("quor.pipeline.repo_profile.graph.walk_repository", _boom)

        cache = _load(repo)
        find_symbol(cache, "UserService")
        repo_stats(cache)


class TestFindSymbol:
    def test_exact_match_found(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _warm(repo)
        cache = _load(repo)

        result = find_symbol(cache, "Consumer")

        assert len(result.matches) == 1
        match = result.matches[0]
        assert match.path == "consumer.py"
        assert match.kind == "class"
        assert match.exports is True

    def test_ambiguous_reports_every_match(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _warm(repo)
        cache = _load(repo)

        result = find_symbol(cache, "UserService")

        assert result.is_ambiguous
        assert {m.path for m in result.matches} == {"service.py", "mock_service.py"}

    def test_no_fuzzy_matching(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _warm(repo)
        cache = _load(repo)

        result = find_symbol(cache, "userservice")

        assert result.matches == []

    def test_not_found_returns_empty_not_none(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _warm(repo)
        cache = _load(repo)

        result = find_symbol(cache, "DoesNotExist")

        assert result.query == "DoesNotExist"
        assert result.matches == []
        assert not result.is_ambiguous


class TestDependencies:
    def test_direct_dependency_resolved(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _warm(repo)
        cache = _load(repo)

        result = file_dependencies(cache, "consumer.py")

        assert result is not None
        assert result.dependencies == ["service.py"]
        assert result.total == 1

    def test_used_by_is_the_exact_reverse(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _warm(repo)
        cache = _load(repo)

        result = file_used_by(cache, "service.py")

        assert result is not None
        assert result.used_by == ["consumer.py"]
        assert result.total == 1

    def test_unknown_file_returns_none(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _warm(repo)
        cache = _load(repo)

        assert file_dependencies(cache, "does_not_exist.py") is None
        assert file_used_by(cache, "does_not_exist.py") is None

    def test_known_file_with_zero_dependencies_is_empty_not_none(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _warm(repo)
        cache = _load(repo)

        result = file_dependencies(cache, "service.py")

        assert result is not None
        assert result.dependencies == []
        assert result.total == 0


class TestFileSummary:
    def test_known_file(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _warm(repo)
        cache = _load(repo)

        result = file_summary(cache, "service.py")

        assert result is not None
        assert result.language == "python"
        assert result.symbol_count == 2  # UserService class + run method
        assert result.importance in {"High", "Medium", "Low"}

    def test_unknown_file_returns_none(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _warm(repo)
        cache = _load(repo)

        assert file_summary(cache, "does_not_exist.py") is None


class TestRepoStats:
    def test_basic_fields(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _warm(repo)
        cache = _load(repo)

        stats = repo_stats(cache)

        assert stats.total_files == 3
        # service.py: UserService + run; mock_service.py: UserService;
        # consumer.py: Consumer + run — 5 declared symbols total.
        assert stats.total_symbols == 5
        assert stats.cache_status == "fresh"
        assert stats.most_imported_file == "service.py"

    def test_deterministic_repeated_calls(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _warm(repo)
        cache = _load(repo)

        first = repo_stats(cache)
        second = repo_stats(cache)

        # `intelligence_age_seconds` is the one field derived from a live
        # clock read (mirrors `dashboard.py`'s identical field) — every
        # other field must be byte-identical across repeated calls.
        assert dataclasses.replace(first, intelligence_age_seconds=0.0) == dataclasses.replace(
            second, intelligence_age_seconds=0.0
        )


class TestPerformance:
    def test_queries_complete_in_well_under_100ms(self, tmp_path: Path) -> None:
        """QB-078's own <100ms target, measured against the query logic
        itself (excluding Python/CLI process startup, a fixed cost this
        feature doesn't control)."""
        repo = _make_repo(tmp_path)
        _warm(repo)
        cache = _load(repo)

        t0 = time.monotonic()
        find_symbol(cache, "UserService")
        file_dependencies(cache, "consumer.py")
        file_used_by(cache, "service.py")
        file_summary(cache, "service.py")
        repo_stats(cache)
        elapsed = time.monotonic() - t0

        assert elapsed < 0.1
