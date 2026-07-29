"""CLI integration tests for `quor map` (QB-061)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import orjson
import platformdirs
from typer.testing import CliRunner

from quor.cli.main import app
from quor.tracking.db import REPO_PROFILE_FILTER_LABEL, TrackingDB

runner = CliRunner()


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)


class TestMapCommandMarkdown:
    def test_default_output_is_markdown(self, tmp_path: Path, monkeypatch) -> None:
        _init_git_repo(tmp_path)
        (tmp_path / "app.py").write_text("print(1)\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["map"])

        assert result.exit_code == 0
        assert "# Repository Profile" in result.stdout
        assert "## Languages" in result.stdout
        assert "Python" in result.stdout

    def test_path_option_targets_a_different_directory(self, tmp_path: Path) -> None:
        other = tmp_path / "other_repo"
        other.mkdir()
        _init_git_repo(other)
        (other / "app.go").write_text("package main\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=other, check=True)

        result = runner.invoke(app, ["map", "--path", str(other)])

        assert result.exit_code == 0
        assert "Go" in result.stdout


class TestMapCommandJson:
    def test_json_flag_produces_valid_json(self, tmp_path: Path, monkeypatch) -> None:
        _init_git_repo(tmp_path)
        (tmp_path / "app.py").write_text("print(1)\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["map", "--json"])

        assert result.exit_code == 0
        parsed = orjson.loads(result.stdout)
        assert parsed["languages"][0]["language"] == "Python"
        assert "statistics" in parsed


class TestMapCommandRebuild:
    def test_second_invocation_reuses_the_cache(self, tmp_path: Path, monkeypatch) -> None:
        # A subdirectory of tmp_path, not tmp_path itself: conftest.py's
        # autouse fixture redirects platformdirs.user_data_dir("quor") (this
        # cache's own storage location) to tmp_path/data/quor — a sibling of
        # this repo dir, not a descendant of it. Scanning tmp_path directly
        # would make the cache's own JSON files show up as new untracked
        # files in the *second* invocation's walk, falsely defeating the
        # cache-hit this test means to verify.
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        (repo / "app.py").write_text("print(1)\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        monkeypatch.chdir(repo)

        first = runner.invoke(app, ["map"])
        assert first.exit_code == 0

        second = runner.invoke(app, ["map"])
        assert second.exit_code == 0
        assert second.stdout == first.stdout

    def test_rebuild_flag_forces_a_full_rebuild(self, tmp_path: Path, monkeypatch) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        (repo / "app.py").write_text("print(1)\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        monkeypatch.chdir(repo)

        runner.invoke(app, ["map"])
        result = runner.invoke(app, ["map", "--rebuild"])

        assert result.exit_code == 0
        assert "Python" in result.stdout


class TestMapCommandErrors:
    """QB-074: --path is validated before any work happens — a nonexistent
    path or a file passed where a directory was expected must produce a
    clear, actionable error at a non-zero exit code, not a silently empty
    (but exit-0) profile."""

    def test_nonexistent_path_gives_a_clear_error(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist"

        result = runner.invoke(app, ["map", "--path", str(missing)])

        assert result.exit_code != 0
        assert "does not exist" in result.output
        assert str(missing) in result.output

    def test_path_pointing_at_a_file_gives_a_clear_error(self, tmp_path: Path) -> None:
        a_file = tmp_path / "not_a_directory.txt"
        a_file.write_text("x", encoding="utf-8")

        result = runner.invoke(app, ["map", "--path", str(a_file)])

        assert result.exit_code != 0
        assert "must be a directory" in result.output


class TestMapCommandSummary:
    def test_cache_hit_shows_a_colored_summary_with_counts(self, tmp_path: Path, monkeypatch) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        (repo / "app.py").write_text("print(1)\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        monkeypatch.chdir(repo)

        runner.invoke(app, ["map"])
        result = runner.invoke(app, ["map"])

        assert result.exit_code == 0
        assert "Done in" in result.output
        assert "1 file" in result.output
        assert "cached" in result.output
        assert "language" in result.output
        # The summary is on stderr only — stdout stays clean for --json.
        assert "Done in" not in result.stdout


class TestMapCommandTracking:
    def test_invocation_is_tracked(self, tmp_path: Path, monkeypatch) -> None:
        _init_git_repo(tmp_path)
        (tmp_path / "app.py").write_text("print(1)\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["map"])
        assert result.exit_code == 0

        db_path = Path(platformdirs.user_data_dir("quor")) / "quor.db"
        db = TrackingDB(db_path)
        db.flush()
        db.close()

        import sqlite3

        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT filter_name, was_passthrough, original_tokens, final_tokens "
                "FROM invocations WHERE filter_name = ? ORDER BY id DESC LIMIT 1",
                (REPO_PROFILE_FILTER_LABEL,),
            ).fetchone()

        assert row is not None
        filter_name, was_passthrough, original_tokens, final_tokens = row
        assert filter_name == REPO_PROFILE_FILTER_LABEL
        assert was_passthrough == 0
        assert original_tokens == final_tokens  # net-zero contribution, see map.py docstring
