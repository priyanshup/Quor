"""CLI integration tests for `quor search` (QB-080).

Mirrors `test_cli_explore.py`'s structure. Most tests write
`file_intelligence.json` directly via `intel_store.save_file_intelligence()`
(the same shortcut `test_read_hook_repo_context.py` uses for QB-079) rather
than running a full `quor map` — `quor search` only ever reads that one
file, so that's all these tests need to set up. `TestSearchRealPipeline`
is the one exception: a real `quor map` build, proving the whole pipeline
(build -> cache -> search) actually works end to end.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import orjson
from typer.testing import CliRunner

from quor.cli.main import app
from quor.pipeline.repo_profile import intel_store
from quor.pipeline.repo_profile.intel_model import FileIntelligenceEntry

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
    (repo / "placeholder.py").write_text("x = 1\n", encoding="utf-8")
    _git_add_all(repo)
    return repo


def _warm_file_intelligence(repo: Path, entries: dict[str, FileIntelligenceEntry]) -> None:
    intel_store.save_file_intelligence(repo, entries)


class TestSearchMissingCache:
    def test_reports_missing_not_a_generic_error(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)

        result = runner.invoke(app, ["search", "payment", "--path", str(repo)])

        assert result.exit_code != 0
        assert "No repository intelligence" in result.output

    def test_never_builds_a_cache_itself(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)

        runner.invoke(app, ["search", "payment", "--path", str(repo)])

        assert not intel_store.state_exists(repo)

    def test_json_error_reports_missing_status(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)

        result = runner.invoke(app, ["search", "payment", "--path", str(repo), "--json"])

        assert result.exit_code != 0
        parsed = orjson.loads(result.stdout)
        assert parsed["status"] == "missing"


class TestSearchCorruptedCache:
    def test_reports_corrupted_distinct_from_missing(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        intel_store.save_file_intelligence(repo, {})
        (intel_store.cache_dir(repo) / "file_intelligence.json").write_bytes(b"{ not valid json")

        result = runner.invoke(app, ["search", "payment", "--path", str(repo)])

        assert result.exit_code != 0
        assert "could not be read" in result.output


class TestSearchTextOutput:
    def test_reports_matches(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _warm_file_intelligence(
            repo,
            {"src/payment.py": FileIntelligenceEntry(language="python", kind="source", top_symbols=["PaymentService"])},
        )

        result = runner.invoke(app, ["search", "payment", "--path", str(repo)])

        assert result.exit_code == 0
        assert "src/payment.py" in result.output
        assert "Evidence:" in result.output
        assert "Matched:" in result.output

    def test_zero_matches_is_not_an_error(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _warm_file_intelligence(repo, {"a.py": FileIntelligenceEntry(language="python", kind="source")})

        result = runner.invoke(app, ["search", "nonexistent_query_xyz", "--path", str(repo)])

        assert result.exit_code == 0
        assert "No matches found" in result.output


class TestSearchJsonOutput:
    def test_json_shape(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _warm_file_intelligence(
            repo,
            {"src/payment.py": FileIntelligenceEntry(language="python", kind="source", top_symbols=["PaymentService"])},
        )

        result = runner.invoke(app, ["search", "PaymentService", "--path", str(repo), "--json"])

        assert result.exit_code == 0
        parsed = orjson.loads(result.stdout)
        assert parsed["query"] == "PaymentService"
        assert parsed["matches"][0]["path"] == "src/payment.py"
        assert parsed["matches"][0]["evidence"] == "exact_symbol"
        assert "total_candidates" in parsed
        assert "truncated" in parsed


class TestSearchValidation:
    def test_invalid_kind_rejected(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _warm_file_intelligence(repo, {"a.py": FileIntelligenceEntry(language="python", kind="source")})

        result = runner.invoke(app, ["search", "a", "--path", str(repo), "--kind", "not_a_real_kind"])

        assert result.exit_code != 0

    def test_invalid_importance_rejected(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _warm_file_intelligence(repo, {"a.py": FileIntelligenceEntry(language="python", kind="source")})

        result = runner.invoke(app, ["search", "a", "--path", str(repo), "--importance", "not_a_real_tier"])

        assert result.exit_code != 0

    def test_empty_query_rejected(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _warm_file_intelligence(repo, {"a.py": FileIntelligenceEntry(language="python", kind="source")})

        result = runner.invoke(app, ["search", "   ", "--path", str(repo)])

        assert result.exit_code != 0


class TestSearchRealPipeline:
    def test_real_quor_map_build_then_search(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        (repo / "payment_service.py").write_text(
            "class PaymentService:\n    def charge(self):\n        pass\n", encoding="utf-8"
        )
        _git_add_all(repo)

        map_result = runner.invoke(app, ["map", "--path", str(repo)])
        assert map_result.exit_code == 0

        result = runner.invoke(app, ["search", "PaymentService", "--path", str(repo)])

        assert result.exit_code == 0
        assert "payment_service.py" in result.output
