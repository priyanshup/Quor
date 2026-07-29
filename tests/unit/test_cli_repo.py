"""CLI integration tests for `quor repo` (QB-076)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import orjson
from typer.testing import CliRunner

from quor.cli.main import app
from quor.tracking.db import REPO_DASHBOARD_FILTER_LABEL, TrackingDB

runner = CliRunner()


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)


def _build_intelligence(repo: Path) -> None:
    """Populate the cache the way a real user would, via the existing
    `quor map`/`quor symbols`/`quor graph` commands — `quor repo` must read
    exactly this cache, never build its own."""
    runner.invoke(app, ["map", "--path", str(repo)])
    runner.invoke(app, ["symbols", "--path", str(repo)])
    runner.invoke(app, ["graph", "--path", str(repo)])


class TestRepoCommandNoCache:
    def test_friendly_message_when_no_cache_exists(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["repo", "--path", str(tmp_path)])

        assert result.exit_code == 0
        assert "quor map" in result.output
        assert "has not been generated yet" in result.output

    def test_json_flag_gives_a_parseable_error_shape(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["repo", "--path", str(tmp_path), "--json"])

        assert result.exit_code == 0
        parsed = orjson.loads(result.stdout)
        assert parsed["error"] == "no_repository_intelligence"


class TestRepoCommandDashboard:
    def test_dashboard_reads_existing_cache(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        (repo / "a.py").write_text("class Foo:\n    pass\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        _build_intelligence(repo)

        result = runner.invoke(app, ["repo", "--path", str(repo)])

        assert result.exit_code == 0
        assert "Quor Repository Dashboard" in result.output
        assert "Languages" in result.output
        assert "Python" in result.output
        assert "Symbols" in result.output
        assert "Dependency Graph" in result.output

    def test_json_flag_produces_valid_json_with_expected_fields(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        (repo / "a.py").write_text("class Foo:\n    pass\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        _build_intelligence(repo)

        result = runner.invoke(app, ["repo", "--path", str(repo), "--json"])

        assert result.exit_code == 0
        parsed = orjson.loads(result.stdout)
        assert parsed["languages"][0]["language"] == "Python"
        assert parsed["total_symbols"] == 1
        assert "graph_edges" in parsed
        assert "largest_modules" in parsed
        assert "most_connected_files" in parsed

    def test_never_walks_the_repository(self, tmp_path: Path, monkeypatch) -> None:
        """`quor repo` must be a pure cache read — patching
        `walk_repository` (the one function any full/incremental rebuild,
        or `assemble_graph()`'s own normal callers, would go through) to
        raise proves the dashboard path never reaches it."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        (repo / "a.py").write_text("class Foo:\n    pass\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        _build_intelligence(repo)

        def _boom(*args, **kwargs):
            raise AssertionError("quor repo must never call walk_repository")

        monkeypatch.setattr("quor.pipeline.repo_profile.walk.walk_repository", _boom)
        monkeypatch.setattr("quor.pipeline.repo_profile.graph.walk_repository", _boom)

        result = runner.invoke(app, ["repo", "--path", str(repo)])

        assert result.exit_code == 0
        assert "Quor Repository Dashboard" in result.output


class TestRepoCommandErrors:
    def test_nonexistent_path_gives_a_clear_error(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist"

        result = runner.invoke(app, ["repo", "--path", str(missing)])

        assert result.exit_code != 0
        assert "does not exist" in result.output


class TestRepoCommandTracking:
    def test_invocation_is_tracked(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        (repo / "a.py").write_text("class Foo:\n    pass\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        _build_intelligence(repo)

        result = runner.invoke(app, ["repo", "--path", str(repo)])
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
                (REPO_DASHBOARD_FILTER_LABEL,),
            ).fetchone()

        assert row is not None
        assert row[0] == REPO_DASHBOARD_FILTER_LABEL
