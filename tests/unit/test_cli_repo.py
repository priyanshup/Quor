"""CLI integration tests for `quor repo` (QB-076, auto-refresh + Repository
Intelligence panel added by QB-077).

QB-077 reverses QB-076's original "never triggers a rebuild" design: `quor
repo` now calls `ensure_repo_intelligence()` itself, exactly like `quor
map`/`quor symbols`/`quor graph` already do, so it can be the *first*
repository-intelligence command a user runs. `TestRepoCommandNoCache` (the
old "read-only, print a message pointing at `quor map`" behavior) is gone —
replaced by `TestRepoCommandAutoOnboard`, which proves the opposite: no
pre-existing cache is required at all. `test_never_walks_the_repository`
(the old proof `quor repo` never touched `walk_repository`) is likewise
gone, replaced by `TestRepoCommandReflectsChanges`, which proves the exact
opposite is now true by design.
"""

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


def _git_add_all(root: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)


def _build_intelligence(repo: Path) -> None:
    """Populate the cache via the existing `quor map`/`quor symbols`/`quor
    graph` commands — used by tests that want to start from an
    already-warm, non-`quor repo`-built cache."""
    runner.invoke(app, ["map", "--path", str(repo)])
    runner.invoke(app, ["symbols", "--path", str(repo)])
    runner.invoke(app, ["graph", "--path", str(repo)])


def _make_repo_with_one_file(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "a.py").write_text("class Foo:\n    pass\n", encoding="utf-8")
    _git_add_all(repo)
    return repo


class TestRepoCommandAutoOnboard:
    def test_builds_intelligence_automatically_with_no_prior_cache(self, tmp_path: Path) -> None:
        """No `quor map`/`quor symbols`/`quor graph` call first — `quor
        repo` alone must be enough."""
        repo = _make_repo_with_one_file(tmp_path)

        result = runner.invoke(app, ["repo", "--path", str(repo)])

        assert result.exit_code == 0
        assert "Quor Repository Dashboard" in result.output
        assert "Python" in result.output
        assert "Repository Intelligence" in result.output
        assert "Rebuilt" in result.output
        assert "Full" in result.output

    def test_onboarding_progress_is_printed_to_stderr(self, tmp_path: Path) -> None:
        repo = _make_repo_with_one_file(tmp_path)

        result = runner.invoke(app, ["repo", "--path", str(repo)])

        assert result.exit_code == 0
        assert "New repository detected" in result.stderr


class TestRepoCommandIntelligencePanel:
    def test_shows_up_to_date_on_a_true_cache_hit(self, tmp_path: Path) -> None:
        repo = _make_repo_with_one_file(tmp_path)
        first = runner.invoke(app, ["repo", "--path", str(repo)])
        assert first.exit_code == 0

        second = runner.invoke(app, ["repo", "--path", str(repo)])

        assert second.exit_code == 0
        assert "Up to date" in second.output
        assert "Instant" in second.output

    def test_shows_refreshed_after_a_file_changes(self, tmp_path: Path) -> None:
        repo = _make_repo_with_one_file(tmp_path)
        first = runner.invoke(app, ["repo", "--path", str(repo)])
        assert first.exit_code == 0

        (repo / "b.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
        _git_add_all(repo)
        second = runner.invoke(app, ["repo", "--path", str(repo)])

        assert second.exit_code == 0
        assert "Refreshed" in second.output
        assert "Incremental" in second.output

    def test_json_includes_intelligence_field(self, tmp_path: Path) -> None:
        repo = _make_repo_with_one_file(tmp_path)

        result = runner.invoke(app, ["repo", "--path", str(repo), "--json"])

        assert result.exit_code == 0
        parsed = orjson.loads(result.stdout)
        intelligence = parsed["intelligence"]
        assert intelligence["status"] == "Rebuilt"
        assert intelligence["rebuild_mode"] == "Full"
        assert intelligence["files_scanned"] == 1
        assert intelligence["cache_hit_ratio"] == 0.0

    def test_json_cache_hit_ratio_is_a_fraction_on_cache_hit(self, tmp_path: Path) -> None:
        repo = _make_repo_with_one_file(tmp_path)
        first = runner.invoke(app, ["repo", "--path", str(repo)])
        assert first.exit_code == 0

        result = runner.invoke(app, ["repo", "--path", str(repo), "--json"])

        assert result.exit_code == 0
        intelligence = orjson.loads(result.stdout)["intelligence"]
        assert intelligence["status"] == "Up to date"
        assert intelligence["cache_hit_ratio"] == 1.0
        assert intelligence["changed_files"] == 0
        assert intelligence["files_reused"] == 1


class TestRepoCommandRebuildFlag:
    def test_rebuild_flag_forces_a_full_rebuild(self, tmp_path: Path) -> None:
        repo = _make_repo_with_one_file(tmp_path)
        first = runner.invoke(app, ["repo", "--path", str(repo)])
        assert first.exit_code == 0

        result = runner.invoke(app, ["repo", "--path", str(repo), "--rebuild"])

        assert result.exit_code == 0
        assert "Rebuilt" in result.output
        assert "Full" in result.output


class TestRepoCommandReflectsChanges:
    def test_new_file_is_reflected_without_running_map_first(self, tmp_path: Path) -> None:
        """The QB-076 guarantee ("`quor repo` never walks the repository")
        is deliberately no longer true — this proves the opposite: content
        added *after* `quor repo`'s own first build is picked up on the
        very next `quor repo` call, with no `quor map`/`symbols`/`graph`
        call in between."""
        repo = _make_repo_with_one_file(tmp_path)
        first = runner.invoke(app, ["repo", "--path", str(repo)])
        assert first.exit_code == 0
        first_json = orjson.loads(runner.invoke(app, ["repo", "--path", str(repo), "--json"]).stdout)
        assert first_json["total_symbols"] == 1

        (repo / "b.py").write_text("class Bar:\n    pass\nclass Baz:\n    pass\n", encoding="utf-8")
        _git_add_all(repo)
        second = runner.invoke(app, ["repo", "--path", str(repo), "--json"])

        assert second.exit_code == 0
        parsed = orjson.loads(second.stdout)
        assert parsed["total_symbols"] == 3
        assert parsed["intelligence"]["status"] == "Refreshed"


class TestRepoCommandDashboard:
    def test_dashboard_reads_prewarmed_cache(self, tmp_path: Path) -> None:
        repo = _make_repo_with_one_file(tmp_path)
        _build_intelligence(repo)

        result = runner.invoke(app, ["repo", "--path", str(repo)])

        assert result.exit_code == 0
        assert "Quor Repository Dashboard" in result.output
        assert "Languages" in result.output
        assert "Python" in result.output
        assert "Symbols" in result.output
        assert "Dependency Graph" in result.output

    def test_json_flag_produces_valid_json_with_expected_fields(self, tmp_path: Path) -> None:
        repo = _make_repo_with_one_file(tmp_path)
        _build_intelligence(repo)

        result = runner.invoke(app, ["repo", "--path", str(repo), "--json"])

        assert result.exit_code == 0
        parsed = orjson.loads(result.stdout)
        assert parsed["languages"][0]["language"] == "Python"
        assert parsed["total_symbols"] == 1
        assert "graph_edges" in parsed
        assert "largest_modules" in parsed
        assert "most_connected_files" in parsed
        assert "intelligence" in parsed

    def test_defensive_fallback_message_when_dashboard_still_missing(self, tmp_path: Path, monkeypatch) -> None:
        """`ensure_repo_intelligence()` always leaves a usable cache behind
        on success, so this branch is unreachable in normal operation — but
        it's still real, fail-open code, so it stays covered."""
        repo = _make_repo_with_one_file(tmp_path)

        monkeypatch.setattr("quor.cli.commands.repo.build_dashboard", lambda _root: None)

        result = runner.invoke(app, ["repo", "--path", str(repo)])

        assert result.exit_code == 0
        assert "has not been generated yet" in result.output


class TestRepoCommandErrors:
    def test_nonexistent_path_gives_a_clear_error(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist"

        result = runner.invoke(app, ["repo", "--path", str(missing)])

        assert result.exit_code != 0
        assert "does not exist" in result.output


class TestRepoCommandTracking:
    def test_invocation_is_tracked(self, tmp_path: Path) -> None:
        repo = _make_repo_with_one_file(tmp_path)
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
