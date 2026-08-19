"""Unit tests for quor/pipeline/repo_profile/walk.py."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from quor.pipeline.repo_profile.walk import walk_repository


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)


class TestWalkRepositoryGit:
    def test_git_ls_files_returns_tracked_and_untracked(self, tmp_path: Path) -> None:
        _init_git_repo(tmp_path)
        (tmp_path / "a.py").write_text("print(1)\n", encoding="utf-8")
        (tmp_path / "b.py").write_text("print(2)\n", encoding="utf-8")
        subprocess.run(["git", "add", "a.py"], cwd=tmp_path, check=True)

        result = walk_repository(tmp_path)

        assert result.used_git is True
        assert result.files == ["a.py", "b.py"]

    def test_gitignored_files_are_excluded(self, tmp_path: Path) -> None:
        _init_git_repo(tmp_path)
        (tmp_path / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
        (tmp_path / "kept.txt").write_text("x\n", encoding="utf-8")
        (tmp_path / "ignored.txt").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "add", ".gitignore", "kept.txt"], cwd=tmp_path, check=True)

        result = walk_repository(tmp_path)

        assert "ignored.txt" not in result.files
        assert "kept.txt" in result.files

    def test_output_is_sorted(self, tmp_path: Path) -> None:
        _init_git_repo(tmp_path)
        for name in ("zeta.py", "alpha.py", "mid.py"):
            (tmp_path / name).write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)

        result = walk_repository(tmp_path)

        assert result.files == sorted(result.files)

    def test_deterministic_across_repeated_calls(self, tmp_path: Path) -> None:
        _init_git_repo(tmp_path)
        (tmp_path / "one.py").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)

        first = walk_repository(tmp_path)
        second = walk_repository(tmp_path)

        assert first.files == second.files
        assert first.used_git == second.used_git is True

    def test_nested_paths_use_posix_separators(self, tmp_path: Path) -> None:
        _init_git_repo(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)

        result = walk_repository(tmp_path)

        assert "src/app.py" in result.files
        assert "\\" not in "".join(result.files)


class TestWalkRepositoryFallback:
    def test_non_git_directory_uses_fallback(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("x\n", encoding="utf-8")
        (tmp_path / "b.txt").write_text("x\n", encoding="utf-8")

        result = walk_repository(tmp_path)

        assert result.used_git is False
        assert result.files == ["a.txt", "b.txt"]

    def test_fallback_skips_hardcoded_ignore_dirs(self, tmp_path: Path) -> None:
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "pkg.js").write_text("x\n", encoding="utf-8")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "mod.pyc").write_text("x\n", encoding="utf-8")
        (tmp_path / "real.py").write_text("x\n", encoding="utf-8")

        result = walk_repository(tmp_path)

        assert result.files == ["real.py"]

    def test_empty_directory_returns_empty_list(self, tmp_path: Path) -> None:
        result = walk_repository(tmp_path)

        assert result.files == []
        assert result.used_git is False


@pytest.mark.skipif(os.name != "nt", reason="Windows-only: MAX_PATH (QB-110)")
class TestWalkRepositoryFallbackLongPaths:
    def test_deep_tree_past_max_path_is_still_walked(self, tmp_path: Path) -> None:
        """QB-110 regression: the fallback's os.walk root must be extended-
        length-prefixed unconditionally (not threshold-gated on its own,
        typically-short, starting length) — os.walk builds every deeper
        dirpath by string concatenation off the root it's given, so a
        short, unprefixed root still hits MAX_PATH once recursion goes deep
        enough. Confirms the file is actually found, not silently dropped."""
        deep = tmp_path
        long_name = "package_segment_" + "x" * 30
        while len(str(deep)) < 250:
            deep = deep / long_name
        target = deep / "sample.py"
        assert len(str(target)) > 260

        from quor.pipeline.repo_profile._longpath import to_long_path

        to_long_path(deep, force=True).mkdir(parents=True, exist_ok=True)
        to_long_path(target).write_text("x\n", encoding="utf-8")

        result = walk_repository(tmp_path)

        assert result.used_git is False
        assert len(result.files) == 1
        assert result.files[0].endswith("sample.py")
        assert "\\" not in result.files[0]  # still POSIX-normalized, prefix never leaked
