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
