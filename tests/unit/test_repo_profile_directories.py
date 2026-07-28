"""Unit tests for quor/pipeline/repo_profile/directories.py."""

from __future__ import annotations

from quor.pipeline.repo_profile.directories import detect_services, important_directories


class TestImportantDirectories:
    def test_detects_well_known_dirs(self) -> None:
        files = ["src/app.py", "tests/test_app.py", "docs/README.md", "unknown_dir/x.py"]

        result = important_directories(files)

        names = {d.path for d in result}
        assert names == {"src", "tests", "docs"}

    def test_file_counts_are_accurate(self) -> None:
        files = ["tests/a.py", "tests/b.py", "tests/sub/c.py"]

        result = important_directories(files)

        tests_entry = next(d for d in result if d.path == "tests")
        assert tests_entry.file_count == 3

    def test_root_level_files_are_not_directories(self) -> None:
        files = ["README.md", "pyproject.toml"]

        result = important_directories(files)

        assert result == []

    def test_sorted_alphabetically(self) -> None:
        files = ["tests/a.py", "docs/b.md", "src/c.py"]

        result = important_directories(files)

        assert [d.path for d in result] == ["docs", "src", "tests"]

    def test_empty_file_list(self) -> None:
        assert important_directories([]) == []


class TestDetectServices:
    def test_subdirectory_with_manifest_is_a_service(self) -> None:
        files = [
            "backend/pyproject.toml",
            "backend/app.py",
            "frontend/package.json",
            "frontend/index.js",
        ]

        result = detect_services(files)

        by_path = {s.path: s.manifest for s in result}
        assert by_path == {"backend": "pyproject.toml", "frontend": "package.json"}

    def test_root_manifest_is_not_a_service(self) -> None:
        files = ["pyproject.toml", "quor/__init__.py"]

        result = detect_services(files)

        assert result == []

    def test_directory_without_manifest_is_not_a_service(self) -> None:
        files = ["utils/helpers.py"]

        result = detect_services(files)

        assert result == []

    def test_sorted_by_path(self) -> None:
        files = ["zeta/package.json", "alpha/pyproject.toml"]

        result = detect_services(files)

        assert [s.path for s in result] == ["alpha", "zeta"]

    def test_nested_manifest_not_directly_under_top_dir_not_matched(self) -> None:
        """Only a manifest directly one level down qualifies — a manifest
        two levels down belongs to a nested sub-service, not the top-level
        directory itself."""
        files = ["services/backend/pyproject.toml"]

        result = detect_services(files)

        assert result == []
