"""CLI integration tests for `quor graph` (QB-067)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import orjson
import platformdirs
from typer.testing import CliRunner

from quor.cli.main import app
from quor.tracking.db import REPO_GRAPH_FILTER_LABEL, TrackingDB

runner = CliRunner()


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)


class TestGraphCommandMarkdown:
    def test_default_output_is_markdown(self, tmp_path: Path, monkeypatch) -> None:
        _init_git_repo(tmp_path)
        (tmp_path / "app.py").write_text("import os\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["graph"])

        assert result.exit_code == 0
        assert "# Repository Dependency Graph" in result.stdout
        assert "app.py" in result.stdout

    def test_path_option_targets_a_different_directory(self, tmp_path: Path) -> None:
        other = tmp_path / "other_repo"
        other.mkdir()
        _init_git_repo(other)
        (other / "app.py").write_text("import sys\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=other, check=True)

        result = runner.invoke(app, ["graph", "--path", str(other)])

        assert result.exit_code == 0
        assert "sys" in result.stdout

    def test_reachable_without_dispatcher_fallthrough(self) -> None:
        """Regression guard for the exact real bug ADR-037 caught for
        `quor map`: `graph` must be routed to the CLI, never treated as a
        literal shell command name by the dispatcher."""
        import quor.__main__ as main_module

        assert "graph" in main_module._CLI_COMMANDS


class TestGraphCommandJson:
    def test_json_flag_produces_valid_json(self, tmp_path: Path, monkeypatch) -> None:
        _init_git_repo(tmp_path)
        (tmp_path / "app.py").write_text("import os\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["graph", "--json"])

        assert result.exit_code == 0
        parsed = orjson.loads(result.stdout)
        assert parsed["edges"][0]["kind"] == "import"
        assert "total_edges" in parsed


class TestGraphCommandTracking:
    def test_invocation_is_tracked(self, tmp_path: Path, monkeypatch) -> None:
        _init_git_repo(tmp_path)
        (tmp_path / "app.py").write_text("import os\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["graph"])
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
                (REPO_GRAPH_FILTER_LABEL,),
            ).fetchone()

        assert row is not None
        filter_name, was_passthrough, original_tokens, final_tokens = row
        assert filter_name == REPO_GRAPH_FILTER_LABEL
        assert was_passthrough == 0
        assert original_tokens == final_tokens  # net-zero contribution, see graph.py docstring
