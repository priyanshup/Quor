"""Unit/integration tests for quor/pipeline/repo_profile/graph.py
(QB-067's `build_dependency_graph()`)."""

from __future__ import annotations

import subprocess
import warnings
from pathlib import Path
from unittest.mock import patch

from quor.pipeline.repo_profile.graph import _MAX_FILE_SIZE_BYTES, build_dependency_graph


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


class TestBuildDependencyGraphBasics:
    def test_python_file_relationships_extracted(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("import os\ndef f():\n    os.getcwd()\n", encoding="utf-8")
        _init_git_repo(tmp_path)

        graph = build_dependency_graph(tmp_path)

        assert graph.root == tmp_path.as_posix()
        kinds = {e.kind for e in graph.edges}
        assert "import" in kinds
        assert "calls" in kinds
        assert graph.languages_covered == ["python"]

    def test_polyglot_repo_covers_multiple_languages(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("def f():\n    pass\n", encoding="utf-8")
        (tmp_path / "app.go").write_text('package main\nimport "fmt"\nfunc F() { fmt.Println() }\n', encoding="utf-8")
        _init_git_repo(tmp_path)

        graph = build_dependency_graph(tmp_path)

        assert graph.languages_covered == ["go", "python"]

    def test_unsupported_extension_produces_no_edges(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# Title\n", encoding="utf-8")
        _init_git_repo(tmp_path)

        graph = build_dependency_graph(tmp_path)

        assert graph.edges == []
        assert graph.languages_covered == []

    def test_edges_sorted_deterministically(self, tmp_path: Path) -> None:
        (tmp_path / "z_file.py").write_text("import os\n", encoding="utf-8")
        (tmp_path / "a_file.py").write_text("import sys\n", encoding="utf-8")
        _init_git_repo(tmp_path)

        graph = build_dependency_graph(tmp_path)

        files = [e.source_file for e in graph.edges]
        assert files == sorted(files)

    def test_deterministic_across_repeated_calls(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text(
            "from .base import Base\nclass Foo(Base):\n    pass\n", encoding="utf-8"
        )
        (tmp_path / "base.py").write_text("class Base:\n    pass\n", encoding="utf-8")
        _init_git_repo(tmp_path)

        first = build_dependency_graph(tmp_path)
        second = build_dependency_graph(tmp_path)

        assert first == second

    def test_empty_repository_produces_a_valid_graph(self, tmp_path: Path) -> None:
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)

        graph = build_dependency_graph(tmp_path)

        assert graph.edges == []
        assert graph.total_edges == 0
        assert graph.resolved_edges == 0

    def test_non_git_directory_notes_the_fallback(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("import os\n", encoding="utf-8")

        graph = build_dependency_graph(tmp_path)

        assert any("filesystem walk" in note for note in graph.notes)

    def test_oversized_file_is_skipped_with_note(self, tmp_path: Path) -> None:
        big_source = "import os\n" + ("# padding\n" * (_MAX_FILE_SIZE_BYTES // 10 + 1))
        (tmp_path / "huge.py").write_text(big_source, encoding="utf-8")
        _init_git_repo(tmp_path)

        graph = build_dependency_graph(tmp_path)

        assert graph.edges == []
        assert any("size cap" in note for note in graph.notes)

    def test_missing_optional_dependency_skips_language_with_actionable_note(self, tmp_path: Path) -> None:
        (tmp_path / "app.go").write_text('package main\nimport "fmt"\n', encoding="utf-8")
        _init_git_repo(tmp_path)

        with patch("quor.pipeline.repo_profile.graph.is_language_available", return_value=False):
            graph = build_dependency_graph(tmp_path)

        assert graph.edges == []
        assert graph.languages_skipped == ["go"]
        assert any("quor[go]" in note for note in graph.notes)

    def test_per_file_parse_failure_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        (tmp_path / "good.py").write_text("import os\n", encoding="utf-8")
        (tmp_path / "bad.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
        _init_git_repo(tmp_path)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            graph = build_dependency_graph(tmp_path)

        assert {e.source_file for e in graph.edges} == {"good.py"}
        assert any("could not be read or parsed" in note for note in graph.notes)


class TestCrossFileResolution:
    def test_relative_import_resolves_to_file(self, tmp_path: Path) -> None:
        (tmp_path / "base.py").write_text("class Base:\n    pass\n", encoding="utf-8")
        (tmp_path / "main.py").write_text("from .base import Base\n", encoding="utf-8")
        _init_git_repo(tmp_path)

        graph = build_dependency_graph(tmp_path)

        import_edge = next(e for e in graph.edges if e.kind == "import")
        assert import_edge.target_file == "base.py"

    def test_inherits_resolves_across_files(self, tmp_path: Path) -> None:
        (tmp_path / "base.py").write_text("class Base:\n    pass\n", encoding="utf-8")
        (tmp_path / "main.py").write_text(
            "from .base import Base\nclass Foo(Base):\n    pass\n", encoding="utf-8"
        )
        _init_git_repo(tmp_path)

        graph = build_dependency_graph(tmp_path)

        inherits_edge = next(e for e in graph.edges if e.kind == "inherits")
        assert inherits_edge.target_file == "base.py"
        assert inherits_edge.target_symbol == "Base"

    def test_same_file_call_resolves(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text(
            "def helper():\n    pass\n\ndef caller():\n    helper()\n", encoding="utf-8"
        )
        _init_git_repo(tmp_path)

        graph = build_dependency_graph(tmp_path)

        call_edge = next(e for e in graph.edges if e.kind == "calls")
        assert call_edge.target_file == "app.py"
        assert call_edge.target_symbol == "helper"

    def test_ambiguous_same_file_call_left_unresolved(self, tmp_path: Path) -> None:
        source = (
            "class A:\n    def run(self):\n        pass\n"
            "class B:\n    def run(self):\n        pass\n"
            "def caller():\n    run()\n"
        )
        (tmp_path / "app.py").write_text(source, encoding="utf-8")
        _init_git_repo(tmp_path)

        graph = build_dependency_graph(tmp_path)

        call_edge = next(e for e in graph.edges if e.kind == "calls" and e.target_raw == "run")
        assert call_edge.target_file is None

    def test_cross_file_import_bound_call_resolves(self, tmp_path: Path) -> None:
        (tmp_path / "helpers.py").write_text("def helper():\n    pass\n", encoding="utf-8")
        (tmp_path / "main.py").write_text(
            "from .helpers import helper\ndef caller():\n    helper()\n", encoding="utf-8"
        )
        _init_git_repo(tmp_path)

        graph = build_dependency_graph(tmp_path)

        call_edge = next(e for e in graph.edges if e.kind == "calls")
        assert call_edge.target_file == "helpers.py"
        assert call_edge.target_symbol == "helper"

    def test_wildcard_import_never_creates_a_binding(self, tmp_path: Path) -> None:
        (tmp_path / "helpers.py").write_text("def helper():\n    pass\n", encoding="utf-8")
        (tmp_path / "main.py").write_text(
            "from .helpers import *\ndef caller():\n    helper()\n", encoding="utf-8"
        )
        _init_git_repo(tmp_path)

        graph = build_dependency_graph(tmp_path)

        call_edge = next(e for e in graph.edges if e.kind == "calls")
        assert call_edge.target_file is None

    def test_external_stdlib_import_left_unresolved(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("import numpy\n", encoding="utf-8")
        _init_git_repo(tmp_path)

        graph = build_dependency_graph(tmp_path)

        import_edge = next(e for e in graph.edges if e.kind == "import")
        assert import_edge.target_file is None
        assert import_edge.target_raw == "numpy"

    def test_javascript_relative_import_resolves_with_extension_probing(self, tmp_path: Path) -> None:
        (tmp_path / "utils.js").write_text("export function helper() {}\n", encoding="utf-8")
        (tmp_path / "main.js").write_text("import { helper } from './utils';\n", encoding="utf-8")
        _init_git_repo(tmp_path)

        graph = build_dependency_graph(tmp_path)

        import_edge = next(e for e in graph.edges if e.kind == "import")
        assert import_edge.target_file == "utils.js"

    def test_java_import_resolves_via_package_directory_convention(self, tmp_path: Path) -> None:
        pkg_dir = tmp_path / "com" / "example"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "Widget.java").write_text(
            "package com.example;\npublic class Widget {}\n", encoding="utf-8"
        )
        (tmp_path / "Main.java").write_text(
            "import com.example.Widget;\nclass Main {}\n", encoding="utf-8"
        )
        _init_git_repo(tmp_path)

        graph = build_dependency_graph(tmp_path)

        import_edge = next(e for e in graph.edges if e.kind == "import")
        assert import_edge.target_file == "com/example/Widget.java"

    def test_rust_crate_import_resolves_via_src_convention(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "lib.rs").write_text("pub mod foo;\n", encoding="utf-8")
        foo_dir = src_dir / "foo"
        foo_dir.mkdir()
        (foo_dir / "mod.rs").write_text("pub struct Bar;\n", encoding="utf-8")
        (src_dir / "main.rs").write_text("use crate::foo::Bar;\nfn main() {}\n", encoding="utf-8")
        _init_git_repo(tmp_path)

        graph = build_dependency_graph(tmp_path)

        bar_import = next(e for e in graph.edges if e.kind == "import" and e.target_raw == "crate::foo::Bar")
        assert bar_import.target_file == "src/foo/mod.rs"

    def test_go_and_csharp_imports_never_resolved(self, tmp_path: Path) -> None:
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "helper.go").write_text("package pkg\nfunc Helper() {}\n", encoding="utf-8")
        (tmp_path / "main.go").write_text('package main\nimport "pkg"\n', encoding="utf-8")
        (tmp_path / "App.cs").write_text("using System;\nclass App {}\n", encoding="utf-8")
        _init_git_repo(tmp_path)

        graph = build_dependency_graph(tmp_path)

        import_edges = [e for e in graph.edges if e.kind == "import"]
        assert all(e.target_file is None for e in import_edges)
