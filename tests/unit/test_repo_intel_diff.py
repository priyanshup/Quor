"""Unit tests for quor/pipeline/repo_profile/intel_diff.py (QB-072)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from quor.pipeline.repo_profile.intel_diff import diff_repository, fingerprint_files, git_head


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


class TestGitHead:
    def test_returns_none_outside_git_repo(self, tmp_path: Path) -> None:
        assert git_head(tmp_path) is None

    def test_returns_sha_inside_git_repo(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("pass\n", encoding="utf-8")
        _init_git_repo(tmp_path)

        head = git_head(tmp_path)

        assert head is not None
        assert len(head) == 40  # a full SHA-1 hex digest


class TestFingerprintFiles:
    def test_fingerprints_every_readable_file(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        (tmp_path / "b.py").write_text("y = 2\n", encoding="utf-8")

        fps = fingerprint_files(tmp_path, ["a.py", "b.py"])

        assert set(fps) == {"a.py", "b.py"}
        assert fps["a.py"].content_hash != fps["b.py"].content_hash

    def test_skips_a_vanished_file(self, tmp_path: Path) -> None:
        fps = fingerprint_files(tmp_path, ["missing.py"])
        assert fps == {}


class TestDiffRepository:
    def test_first_scan_reports_every_file_as_added(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")

        diff, fingerprints = diff_repository(tmp_path, ["a.py"], previous={})

        assert diff.added == ["a.py"]
        assert diff.modified == []
        assert diff.deleted == []
        assert diff.renamed == []
        assert "a.py" in fingerprints

    def test_unchanged_file_reports_no_diff(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        _, fingerprints = diff_repository(tmp_path, ["a.py"], previous={})

        diff, new_fingerprints = diff_repository(tmp_path, ["a.py"], previous=fingerprints)

        assert diff.is_empty
        assert new_fingerprints == fingerprints

    def test_unchanged_file_is_not_rehashed(self, tmp_path: Path, monkeypatch) -> None:
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        _, fingerprints = diff_repository(tmp_path, ["a.py"], previous={})

        import quor.pipeline.repo_profile.intel_diff as intel_diff_module

        def _fail_if_called(_path: Path) -> str | None:
            raise AssertionError("an unchanged file must not be re-hashed")

        monkeypatch.setattr(intel_diff_module, "_hash_file", _fail_if_called)

        diff, _ = diff_repository(tmp_path, ["a.py"], previous=fingerprints)
        assert diff.is_empty

    def test_modified_file_detected_via_content_hash(self, tmp_path: Path) -> None:
        path = tmp_path / "a.py"
        path.write_text("x = 1\n", encoding="utf-8")
        _, fingerprints = diff_repository(tmp_path, ["a.py"], previous={})

        path.write_text("x = 2 plus extra padding to force a size change\n", encoding="utf-8")
        diff, new_fingerprints = diff_repository(tmp_path, ["a.py"], previous=fingerprints)

        assert diff.modified == ["a.py"]
        assert diff.added == []
        assert new_fingerprints["a.py"].content_hash != fingerprints["a.py"].content_hash

    def test_deleted_file_detected(self, tmp_path: Path) -> None:
        path = tmp_path / "a.py"
        path.write_text("x = 1\n", encoding="utf-8")
        _, fingerprints = diff_repository(tmp_path, ["a.py"], previous={})

        diff, _ = diff_repository(tmp_path, [], previous=fingerprints)

        assert diff.deleted == ["a.py"]
        assert diff.added == []
        assert diff.renamed == []

    def test_pure_rename_detected_via_matching_content_hash(self, tmp_path: Path) -> None:
        old_path = tmp_path / "old.py"
        old_path.write_text("def foo():\n    pass\n", encoding="utf-8")
        _, fingerprints = diff_repository(tmp_path, ["old.py"], previous={})

        old_path.rename(tmp_path / "new.py")
        diff, _ = diff_repository(tmp_path, ["new.py"], previous=fingerprints)

        assert diff.renamed == [("old.py", "new.py")]
        assert diff.added == []
        assert diff.deleted == []
        assert diff.reextraction_paths == []  # a pure rename never needs re-parsing

    def test_rename_plus_edit_is_reported_as_delete_and_add_not_rename(self, tmp_path: Path) -> None:
        old_path = tmp_path / "old.py"
        old_path.write_text("def foo():\n    pass\n", encoding="utf-8")
        _, fingerprints = diff_repository(tmp_path, ["old.py"], previous={})

        new_path = tmp_path / "new.py"
        old_path.rename(new_path)
        new_path.write_text("def foo():\n    return 1\n", encoding="utf-8")
        diff, _ = diff_repository(tmp_path, ["new.py"], previous=fingerprints)

        assert diff.renamed == []
        assert diff.added == ["new.py"]
        assert diff.deleted == ["old.py"]

    def test_empty_diff_has_no_reextraction_paths(self) -> None:
        from quor.pipeline.repo_profile.intel_model import RepoDiff

        diff = RepoDiff()
        assert diff.is_empty
        assert diff.reextraction_paths == []

    def test_reextraction_paths_includes_added_and_modified_but_not_renamed(self, tmp_path: Path) -> None:
        old_path = tmp_path / "old.py"
        old_path.write_text("def foo():\n    pass\n", encoding="utf-8")
        (tmp_path / "kept.py").write_text("x = 1\n", encoding="utf-8")
        _, fingerprints = diff_repository(tmp_path, ["old.py", "kept.py"], previous={})

        old_path.rename(tmp_path / "renamed.py")
        (tmp_path / "kept.py").write_text("x = 2 with more padding to change size\n", encoding="utf-8")
        (tmp_path / "brand_new.py").write_text("y = 3\n", encoding="utf-8")
        diff, _ = diff_repository(tmp_path, ["renamed.py", "kept.py", "brand_new.py"], previous=fingerprints)

        assert diff.reextraction_paths == ["brand_new.py", "kept.py"]


@pytest.mark.skipif(os.name != "nt", reason="Windows-only: MAX_PATH (QB-110)")
class TestFingerprintFilesLongPaths:
    def test_file_past_max_path_is_fingerprinted_not_skipped(self, tmp_path: Path) -> None:
        """QB-110 regression: fingerprint_files()'s stat()+_hash_file() pair
        must not silently drop a file whose absolute path exceeds MAX_PATH —
        before the fix, both would raise/fail on the unprefixed path and the
        file would be dropped from the fingerprint table entirely (never
        `added`, never diffed against on a later run)."""
        deep = tmp_path
        long_name = "package_segment_" + "x" * 30
        while len(str(deep)) < 250:
            deep = deep / long_name
        target = deep / "sample.py"
        assert len(str(target)) > 260

        from quor.pipeline.repo_profile._longpath import to_long_path

        to_long_path(deep, force=True).mkdir(parents=True, exist_ok=True)
        to_long_path(target).write_text("x = 1\n", encoding="utf-8")

        rel_path = str(target.relative_to(tmp_path).as_posix())
        fingerprints = fingerprint_files(tmp_path, [rel_path])

        assert rel_path in fingerprints
        assert fingerprints[rel_path].content_hash is not None
