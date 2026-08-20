"""Unit/integration tests for quor/pipeline/repo_profile/profiler.py."""

from __future__ import annotations

import subprocess
from pathlib import Path

from quor.pipeline.repo_profile.profiler import build_profile


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)


def _build_flask_project(root: Path) -> None:
    _init_git_repo(root)
    (root / "requirements.txt").write_text("flask==3.0\npytest==8.0\n", encoding="utf-8")
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (root / "Dockerfile").write_text("FROM python:3.11\n", encoding="utf-8")
    (root / "app.py").write_text(
        'from flask import Flask\napp = Flask(__name__)\n\nif __name__ == "__main__":\n    app.run()\n',
        encoding="utf-8",
    )
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "ci.yml").write_text("name: CI\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


class TestBuildProfileIntegration:
    def test_flask_project_detected_end_to_end(self, tmp_path: Path) -> None:
        _build_flask_project(tmp_path)

        profile = build_profile(tmp_path)

        assert any(f.name == "flask" for f in profile.frameworks)
        assert any(t.name == "pytest" for t in profile.test_frameworks)
        assert any(c.name == "docker" for c in profile.containerization)
        assert any(c.name == "github-actions" for c in profile.ci_systems)
        assert any(ep.target == "app.py" for ep in profile.entry_points)
        assert profile.statistics.primary_language == "Python"
        assert profile.statistics.git_commit_count == 1
        assert profile.notes == []

    def test_non_git_directory_notes_the_fallback(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x\n", encoding="utf-8")

        profile = build_profile(tmp_path)

        assert any("filesystem walk" in note for note in profile.notes)

    def test_deterministic_across_repeated_calls(self, tmp_path: Path) -> None:
        _build_flask_project(tmp_path)

        first = build_profile(tmp_path)
        second = build_profile(tmp_path)

        assert first.model_dump() == second.model_dump()

    def test_empty_repository_produces_a_valid_profile(self, tmp_path: Path) -> None:
        _init_git_repo(tmp_path)

        profile = build_profile(tmp_path)

        assert profile.languages == []
        assert profile.frameworks == []
        assert profile.statistics.total_files == 0

    def test_lockfiles_surfaced_directly(self, tmp_path: Path) -> None:
        _init_git_repo(tmp_path)
        (tmp_path / "poetry.lock").write_text("", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)

        profile = build_profile(tmp_path)

        assert "poetry.lock" in profile.lockfiles
        assert any(pm.name == "poetry" for pm in profile.package_managers)

    def test_monorepo_services_detected(self, tmp_path: Path) -> None:
        _init_git_repo(tmp_path)
        (tmp_path / "backend").mkdir()
        (tmp_path / "backend" / "pyproject.toml").write_text("", encoding="utf-8")
        (tmp_path / "frontend").mkdir()
        (tmp_path / "frontend" / "package.json").write_text("{}", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)

        profile = build_profile(tmp_path)

        service_paths = {s.path for s in profile.services}
        assert service_paths == {"backend", "frontend"}


class TestFixtureExclusion:
    """QB-122: committed fixture repos under `tests/fixtures/`,
    `tests/data/`, etc. (mock manifests/lockfiles used to exercise this
    package's own detectors in tests) must not read as evidence about the
    profiled project itself."""

    def _build_real_project_with_fixtures(self, root: Path) -> None:
        _init_git_repo(root)
        (root / "app.py").write_text("print(1)\n", encoding="utf-8")
        fixture_dir = root / "tests" / "fixtures" / "flask-mock"
        fixture_dir.mkdir(parents=True)
        (fixture_dir / "requirements.txt").write_text("flask==3.0\n", encoding="utf-8")
        (fixture_dir / "package.json").write_text('{"dependencies": {"express": "*"}}\n', encoding="utf-8")
        (fixture_dir / "pnpm-lock.yaml").write_text("", encoding="utf-8")
        (fixture_dir / "index.js").write_text("console.log(1)\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=root, check=True)

    def test_fixture_manifests_do_not_leak_into_framework_detection(self, tmp_path: Path) -> None:
        self._build_real_project_with_fixtures(tmp_path)

        profile = build_profile(tmp_path)

        assert not any(f.name in ("flask", "express") for f in profile.frameworks)
        assert not any(pm.name == "pip" for pm in profile.package_managers)

    def test_fixture_lockfiles_are_excluded(self, tmp_path: Path) -> None:
        self._build_real_project_with_fixtures(tmp_path)

        profile = build_profile(tmp_path)

        assert profile.lockfiles == []

    def test_fixture_javascript_does_not_leak_into_language_stats(self, tmp_path: Path) -> None:
        self._build_real_project_with_fixtures(tmp_path)

        profile = build_profile(tmp_path)

        assert profile.statistics.primary_language == "Python"
        assert not any(lang.language == "JavaScript" for lang in profile.languages)

    def test_tests_data_directory_is_also_excluded(self, tmp_path: Path) -> None:
        _init_git_repo(tmp_path)
        (tmp_path / "app.py").write_text("print(1)\n", encoding="utf-8")
        data_dir = tmp_path / "tests" / "data" / "sample-repo"
        data_dir.mkdir(parents=True)
        (data_dir / "go.mod").write_text("module example.com/mock\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)

        profile = build_profile(tmp_path)

        assert not any(bs.name == "go-modules" for bs in profile.build_systems)

    def test_plain_test_source_files_still_count_as_project_files(self, tmp_path: Path) -> None:
        # Only fixture/data dirs nested under a test dir are excluded — an
        # ordinary test module (no fixture dir involved) is still real
        # project source and must still count.
        _init_git_repo(tmp_path)
        (tmp_path / "app.py").write_text("print(1)\n", encoding="utf-8")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_app.py").write_text("def test_x(): pass\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)

        profile = build_profile(tmp_path)

        assert profile.statistics.total_files == 2
        python_stat = next(lang for lang in profile.languages if lang.language == "Python")
        assert python_stat.file_count == 2
