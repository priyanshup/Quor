"""Unit/integration tests for quor/pipeline/repo_profile/symbols.py
(QB-066's `build_symbol_index()`)."""

from __future__ import annotations

import subprocess
import warnings
from pathlib import Path
from unittest.mock import patch

from quor.pipeline.repo_profile.symbols import _MAX_FILE_SIZE_BYTES, build_symbol_index


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


class TestBuildSymbolIndex:
    def test_python_file_symbols_extracted(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text(
            "class Widget:\n    def render(self):\n        pass\n\ndef main():\n    pass\n",
            encoding="utf-8",
        )
        _init_git_repo(tmp_path)

        index = build_symbol_index(tmp_path)

        assert index.root == tmp_path.as_posix()
        assert len(index.files) == 1
        file_symbols = index.files[0]
        assert file_symbols.path == "app.py"
        assert file_symbols.language == "python"
        names = {s.name for s in file_symbols.symbols}
        assert names == {"Widget", "render", "main"}
        assert index.total_symbols == 3
        assert index.languages_covered == ["python"]

    def test_polyglot_repo_covers_multiple_languages(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("def f():\n    pass\n", encoding="utf-8")
        (tmp_path / "app.go").write_text("package main\n\nfunc Exported() {}\n", encoding="utf-8")
        _init_git_repo(tmp_path)

        index = build_symbol_index(tmp_path)

        assert index.languages_covered == ["go", "python"]
        assert {f.language for f in index.files} == {"go", "python"}

    def test_file_with_no_symbols_is_omitted(self, tmp_path: Path) -> None:
        (tmp_path / "empty.py").write_text("x = 1\n", encoding="utf-8")
        _init_git_repo(tmp_path)

        index = build_symbol_index(tmp_path)

        assert index.files == []
        assert index.total_symbols == 0

    def test_unsupported_extension_is_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# Title\n", encoding="utf-8")
        _init_git_repo(tmp_path)

        index = build_symbol_index(tmp_path)

        assert index.files == []
        assert index.languages_covered == []

    def test_files_sorted_and_symbols_sorted_by_line(self, tmp_path: Path) -> None:
        (tmp_path / "z_file.py").write_text("def z():\n    pass\n", encoding="utf-8")
        (tmp_path / "a_file.py").write_text(
            "def second():\n    pass\n\ndef first_by_position_only():\n    pass\n", encoding="utf-8"
        )
        _init_git_repo(tmp_path)

        index = build_symbol_index(tmp_path)

        assert [f.path for f in index.files] == ["a_file.py", "z_file.py"]
        a_file = index.files[0]
        assert [s.line for s in a_file.symbols] == sorted(s.line for s in a_file.symbols)

    def test_non_git_directory_notes_the_fallback(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")

        index = build_symbol_index(tmp_path)

        assert any("filesystem walk" in note for note in index.notes)

    def test_deterministic_across_repeated_calls(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text(
            "class A:\n    def m(self):\n        pass\n\ndef f():\n    pass\n", encoding="utf-8"
        )
        _init_git_repo(tmp_path)

        first = build_symbol_index(tmp_path)
        second = build_symbol_index(tmp_path)

        assert first == second

    def test_empty_repository_produces_a_valid_index(self, tmp_path: Path) -> None:
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)

        index = build_symbol_index(tmp_path)

        assert index.files == []
        assert index.total_symbols == 0
        assert index.notes == []

    def test_oversized_file_is_skipped_with_note(self, tmp_path: Path) -> None:
        big_source = "def f():\n    pass\n" + ("# padding\n" * (_MAX_FILE_SIZE_BYTES // 10 + 1))
        (tmp_path / "huge.py").write_text(big_source, encoding="utf-8")
        _init_git_repo(tmp_path)

        index = build_symbol_index(tmp_path)

        assert index.files == []
        assert any("size cap" in note for note in index.notes)

    def test_missing_optional_dependency_skips_language_with_actionable_note(self, tmp_path: Path) -> None:
        (tmp_path / "app.go").write_text("package main\n\nfunc F() {}\n", encoding="utf-8")
        _init_git_repo(tmp_path)

        with patch(
            "quor.pipeline.repo_profile.symbols.is_language_available", return_value=False
        ):
            index = build_symbol_index(tmp_path)

        assert index.files == []
        assert index.languages_skipped == ["go"]
        assert any('quor[go]' in note for note in index.notes)

    def test_per_file_parse_failure_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        (tmp_path / "good.py").write_text("def ok():\n    pass\n", encoding="utf-8")
        (tmp_path / "bad.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
        _init_git_repo(tmp_path)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            index = build_symbol_index(tmp_path)

        assert [f.path for f in index.files] == ["good.py"]
        assert any("could not be read or parsed" in note for note in index.notes)
