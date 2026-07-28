"""Unit tests for quor/pipeline/repo_profile/statistics.py."""

from __future__ import annotations

import subprocess
from pathlib import Path

from quor.pipeline.repo_profile.model import LanguageStat
from quor.pipeline.repo_profile.statistics import compute_statistics


class TestComputeStatistics:
    def test_total_files_and_directories(self, tmp_path: Path) -> None:
        files = ["a.py", "src/b.py", "src/sub/c.py", "tests/d.py"]

        stats = compute_statistics(tmp_path, files, [])

        assert stats.total_files == 4
        assert stats.total_directories == 3  # src, src/sub, tests

    def test_root_level_files_dont_count_as_directories(self, tmp_path: Path) -> None:
        stats = compute_statistics(tmp_path, ["a.py", "b.py"], [])

        assert stats.total_directories == 0

    def test_primary_language_is_highest_count(self, tmp_path: Path) -> None:
        languages = [
            LanguageStat(language="Python", file_count=10, percentage=90.9),
            LanguageStat(language="Go", file_count=1, percentage=9.1),
        ]

        stats = compute_statistics(tmp_path, [], languages)

        assert stats.primary_language == "Python"

    def test_primary_language_none_when_no_languages(self, tmp_path: Path) -> None:
        stats = compute_statistics(tmp_path, [], [])

        assert stats.primary_language is None

    def test_git_commit_count_none_for_non_git_directory(self, tmp_path: Path) -> None:
        stats = compute_statistics(tmp_path, [], [])

        assert stats.git_commit_count is None

    def test_git_commit_count_reflects_real_history(self, tmp_path: Path) -> None:
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
        (tmp_path / "a.txt").write_text("1\n", encoding="utf-8")
        subprocess.run(["git", "add", "a.txt"], cwd=tmp_path, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "first"], cwd=tmp_path, check=True
        )
        (tmp_path / "a.txt").write_text("2\n", encoding="utf-8")
        subprocess.run(["git", "add", "a.txt"], cwd=tmp_path, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "second"], cwd=tmp_path, check=True
        )

        stats = compute_statistics(tmp_path, [], [])

        assert stats.git_commit_count == 2

    def test_git_repo_with_no_commits_yet_returns_none(self, tmp_path: Path) -> None:
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)

        stats = compute_statistics(tmp_path, [], [])

        assert stats.git_commit_count is None
