"""CLI integration tests for `quor explore` (QB-078).

Mirrors `test_cli_repo.py`'s structure/fixtures. Unlike `quor repo`,
`quor explore` never calls `ensure_repo_intelligence()` — every test here
either warms the cache explicitly first (`_warm_cache()`, via `quor map`/
`quor symbols`/`quor graph`, exactly like `test_cli_repo.py`'s own
`_build_intelligence()`) or deliberately doesn't, to prove the "missing
cache" error path.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import orjson
from typer.testing import CliRunner

from quor.cli.main import app
from quor.pipeline.repo_profile import intel_store
from quor.tracking.db import REPO_EXPLORE_FILTER_LABEL, TrackingDB

runner = CliRunner()


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)


def _git_add_all(root: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)


def _make_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "service.py").write_text("class UserService:\n    def run(self):\n        pass\n", encoding="utf-8")
    (repo / "consumer.py").write_text(
        "from .service import UserService\n\n\nclass Consumer:\n    def run(self):\n        UserService()\n",
        encoding="utf-8",
    )
    _git_add_all(repo)
    return repo


def _warm_cache(repo: Path) -> None:
    runner.invoke(app, ["map", "--path", str(repo)])
    runner.invoke(app, ["symbols", "--path", str(repo)])
    runner.invoke(app, ["graph", "--path", str(repo)])


class TestExploreMissingCache:
    def test_reports_missing_not_a_generic_error(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)

        result = runner.invoke(app, ["explore", "stats", "--path", str(repo)])

        assert result.exit_code != 0
        assert "No repository intelligence" in result.output

    def test_never_builds_a_cache_itself(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)

        runner.invoke(app, ["explore", "stats", "--path", str(repo)])

        assert not intel_store.state_exists(repo)

    def test_json_error_reports_missing_status(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)

        result = runner.invoke(app, ["explore", "find", "UserService", "--path", str(repo), "--json"])

        assert result.exit_code != 0
        parsed = orjson.loads(result.stdout)
        assert parsed["status"] == "missing"


class TestExploreCorruptedCache:
    def test_reports_corrupted_distinct_from_missing(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _warm_cache(repo)
        (intel_store.cache_dir(repo) / "state.json").write_bytes(b"{ not valid json")

        result = runner.invoke(app, ["explore", "stats", "--path", str(repo)])

        assert result.exit_code != 0
        assert "could not be read" in result.output
        assert "No repository intelligence" not in result.output


class TestExploreFind:
    def test_finds_unique_symbol(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _warm_cache(repo)

        result = runner.invoke(app, ["explore", "find", "UserService", "--path", str(repo)])

        assert result.exit_code == 0
        assert "service.py" in result.output
        assert "Yes" in result.output

    def test_not_found_is_an_error_with_guidance(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _warm_cache(repo)

        result = runner.invoke(app, ["explore", "find", "NoSuchSymbol", "--path", str(repo)])

        assert result.exit_code != 0
        assert "not found" in result.output
        assert "no fuzzy or partial matches" in result.output

    def test_ambiguous_symbol_lists_every_match_and_exits_zero(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        (repo / "mock_service.py").write_text("class UserService:\n    pass\n", encoding="utf-8")
        _git_add_all(repo)
        _warm_cache(repo)

        result = runner.invoke(app, ["explore", "find", "UserService", "--path", str(repo)])

        assert result.exit_code == 0
        assert "2 matches" in result.output
        assert "service.py" in result.output
        assert "mock_service.py" in result.output

    def test_json_output_has_a_stable_schema(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _warm_cache(repo)

        result = runner.invoke(app, ["explore", "find", "UserService", "--path", str(repo), "--json"])

        assert result.exit_code == 0
        parsed = orjson.loads(result.stdout)
        assert parsed["query"] == "UserService"
        assert parsed["matches"][0]["path"] == "service.py"
        assert parsed["matches"][0]["exports"] is True
        assert parsed["matches"][0]["kind"] == "class"


class TestExploreDeps:
    def test_direct_dependency(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _warm_cache(repo)

        result = runner.invoke(app, ["explore", "deps", "consumer.py", "--path", str(repo)])

        assert result.exit_code == 0
        assert "service.py" in result.output
        assert "Total: 1" in result.output

    def test_unknown_file_is_an_error(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _warm_cache(repo)

        result = runner.invoke(app, ["explore", "deps", "does_not_exist.py", "--path", str(repo)])

        assert result.exit_code != 0
        assert "not a file" in result.output


class TestExploreUsedBy:
    def test_reverse_dependency(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _warm_cache(repo)

        result = runner.invoke(app, ["explore", "used-by", "service.py", "--path", str(repo)])

        assert result.exit_code == 0
        assert "consumer.py" in result.output
        assert "Total: 1" in result.output


class TestExploreFile:
    def test_summary_fields(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _warm_cache(repo)

        result = runner.invoke(app, ["explore", "file", "service.py", "--path", str(repo)])

        assert result.exit_code == 0
        assert "python" in result.output
        assert "Repository importance" in result.output

    def test_json_output_has_a_stable_schema(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _warm_cache(repo)

        result = runner.invoke(app, ["explore", "file", "service.py", "--path", str(repo), "--json"])

        assert result.exit_code == 0
        parsed = orjson.loads(result.stdout)
        assert parsed["language"] == "python"
        assert parsed["symbol_count"] == 2  # UserService class + run method
        assert parsed["importance"] in {"High", "Medium", "Low"}


class TestExploreStats:
    def test_basic_fields(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _warm_cache(repo)

        result = runner.invoke(app, ["explore", "stats", "--path", str(repo)])

        assert result.exit_code == 0
        assert "Cache status" in result.output

    def test_json_fields(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _warm_cache(repo)

        result = runner.invoke(app, ["explore", "stats", "--path", str(repo), "--json"])

        assert result.exit_code == 0
        parsed = orjson.loads(result.stdout)
        assert parsed["cache_status"] == "fresh"
        assert parsed["total_files"] == 2


class TestExploreDeterminism:
    def test_stats_stable_aside_from_live_cache_age(self, tmp_path: Path) -> None:
        """`intelligence_age_seconds` is the one field derived from a live
        clock read (mirrors `dashboard.py`'s identical field on
        `RepoDashboard`) — necessarily differs by the real wall-clock gap
        between the two invocations below, so it's excluded from the
        byte-identical comparison; every other field must match exactly."""
        repo = _make_repo(tmp_path)
        _warm_cache(repo)

        first = orjson.loads(runner.invoke(app, ["explore", "stats", "--path", str(repo), "--json"]).stdout)
        second = orjson.loads(runner.invoke(app, ["explore", "stats", "--path", str(repo), "--json"]).stdout)

        assert isinstance(first.pop("intelligence_age_seconds"), float)
        assert isinstance(second.pop("intelligence_age_seconds"), float)
        assert first == second

    def test_find_json_byte_identical_repeated_runs(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _warm_cache(repo)

        first = runner.invoke(app, ["explore", "find", "UserService", "--path", str(repo), "--json"])
        second = runner.invoke(app, ["explore", "find", "UserService", "--path", str(repo), "--json"])

        assert first.stdout == second.stdout


class TestExplorePerformance:
    def test_completes_quickly(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _warm_cache(repo)

        t0 = time.monotonic()
        result = runner.invoke(app, ["explore", "stats", "--path", str(repo)])
        elapsed = time.monotonic() - t0

        assert result.exit_code == 0
        assert elapsed < 2.0


class TestExploreTracking:
    def test_invocation_is_tracked(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _warm_cache(repo)

        result = runner.invoke(app, ["explore", "stats", "--path", str(repo)])
        assert result.exit_code == 0

        import sqlite3

        import platformdirs

        db_path = Path(platformdirs.user_data_dir("quor")) / "quor.db"
        db = TrackingDB(db_path)
        db.flush()
        db.close()

        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT filter_name FROM invocations WHERE filter_name = ? ORDER BY id DESC LIMIT 1",
                (REPO_EXPLORE_FILTER_LABEL,),
            ).fetchone()

        assert row is not None
        assert row[0] == REPO_EXPLORE_FILTER_LABEL


class TestExploreErrors:
    def test_nonexistent_repo_path(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist"

        result = runner.invoke(app, ["explore", "stats", "--path", str(missing)])

        assert result.exit_code != 0
        assert "does not exist" in result.output
