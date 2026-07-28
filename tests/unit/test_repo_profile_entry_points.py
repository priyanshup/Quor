"""Unit tests for quor/pipeline/repo_profile/entry_points.py."""

from __future__ import annotations

from pathlib import Path

from quor.pipeline.repo_profile.entry_points import detect_entry_points


class TestPythonPyprojectEntryPoints:
    def test_project_scripts_detected(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            """
[project.scripts]
quor = "quor.__main__:main"
qr = "quor.__main__:main"
""",
            encoding="utf-8",
        )

        entries = detect_entry_points(tmp_path, ["pyproject.toml"])

        targets = {e.target for e in entries}
        assert "quor = quor.__main__:main" in targets
        assert "qr = quor.__main__:main" in targets
        assert all("[project.scripts]" in e.evidence for e in entries)

    def test_poetry_scripts_detected(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            """
[tool.poetry.scripts]
mytool = "mypackage.cli:app"
""",
            encoding="utf-8",
        )

        entries = detect_entry_points(tmp_path, ["pyproject.toml"])

        assert len(entries) == 1
        assert entries[0].target == "mytool = mypackage.cli:app"
        assert "[tool.poetry.scripts]" in entries[0].evidence

    def test_no_scripts_section_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "x"\n', encoding="utf-8"
        )

        entries = detect_entry_points(tmp_path, ["pyproject.toml"])

        assert entries == []

    def test_malformed_toml_fails_open(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("this is [ not valid", encoding="utf-8")

        entries = detect_entry_points(tmp_path, ["pyproject.toml"])

        assert entries == []


class TestNodePackageJsonEntryPoints:
    def test_bin_string_detected(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(
            '{"name": "x", "bin": "./cli.js"}', encoding="utf-8"
        )

        entries = detect_entry_points(tmp_path, ["package.json"])

        assert any(e.target == "./cli.js" and "bin" in e.evidence for e in entries)

    def test_bin_dict_detected(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(
            '{"name": "x", "bin": {"mytool": "./bin/mytool.js"}}', encoding="utf-8"
        )

        entries = detect_entry_points(tmp_path, ["package.json"])

        assert any(e.target == "mytool = ./bin/mytool.js" for e in entries)

    def test_main_field_detected(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(
            '{"name": "x", "main": "index.js"}', encoding="utf-8"
        )

        entries = detect_entry_points(tmp_path, ["package.json"])

        assert any(e.target == "index.js" and "main" in e.evidence for e in entries)

    def test_malformed_json_fails_open(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text("{not valid json", encoding="utf-8")

        entries = detect_entry_points(tmp_path, ["package.json"])

        assert entries == []


class TestCargoEntryPoints:
    def test_explicit_bin_table_detected(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text(
            """
[[bin]]
name = "mytool"
path = "src/bin/mytool.rs"
""",
            encoding="utf-8",
        )

        entries = detect_entry_points(tmp_path, ["Cargo.toml"])

        assert any(e.target == "mytool" for e in entries)

    def test_default_src_main_convention(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "x"\n', encoding="utf-8")

        entries = detect_entry_points(tmp_path, ["Cargo.toml", "src/main.rs"])

        assert any(e.target == "src/main.rs" for e in entries)

    def test_no_bin_and_no_main_rs_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "x"\n', encoding="utf-8")

        entries = detect_entry_points(tmp_path, ["Cargo.toml", "src/lib.rs"])

        assert entries == []


class TestGoEntryPoints:
    def test_root_main_go_detected(self, tmp_path: Path) -> None:
        entries = detect_entry_points(tmp_path, ["go.mod", "main.go"])

        assert any(e.target == "main.go" for e in entries)

    def test_cmd_convention_detected(self, tmp_path: Path) -> None:
        entries = detect_entry_points(tmp_path, ["go.mod", "cmd/server/main.go"])

        assert any(e.target == "cmd/server/main.go" for e in entries)

    def test_non_cmd_nested_main_go_not_matched(self, tmp_path: Path) -> None:
        entries = detect_entry_points(tmp_path, ["go.mod", "internal/server/main.go"])

        assert entries == []


class TestPythonMainGuardEntryPoints:
    def test_root_level_dunder_main_detected(self, tmp_path: Path) -> None:
        (tmp_path / "run.py").write_text(
            'print("hi")\n\nif __name__ == "__main__":\n    print("main")\n',
            encoding="utf-8",
        )

        entries = detect_entry_points(tmp_path, ["run.py"])

        assert any(e.target == "run.py" for e in entries)

    def test_nested_dunder_main_not_scanned(self, tmp_path: Path) -> None:
        """Bounded to root-level files only — never an unbounded whole-tree scan."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "run.py").write_text(
            'if __name__ == "__main__":\n    pass\n', encoding="utf-8"
        )

        entries = detect_entry_points(tmp_path, ["src/run.py"])

        assert entries == []

    def test_file_without_guard_not_detected(self, tmp_path: Path) -> None:
        (tmp_path / "lib.py").write_text("def foo(): pass\n", encoding="utf-8")

        entries = detect_entry_points(tmp_path, ["lib.py"])

        assert entries == []
