"""Unit tests for quor/pipeline/repo_profile/render.py."""

from __future__ import annotations

import orjson

from quor.pipeline.repo_profile.model import (
    DetectedItem,
    EntryPoint,
    ImportantDirectory,
    LanguageStat,
    RepoProfile,
    RepoStatistics,
    ServiceModule,
)
from quor.pipeline.repo_profile.render import render_json, render_markdown


def _empty_profile(**overrides: object) -> RepoProfile:
    defaults: dict[str, object] = {
        "root": "/repo",
        "statistics": RepoStatistics(
            total_files=0, total_directories=0, primary_language=None, git_commit_count=None
        ),
    }
    defaults.update(overrides)
    return RepoProfile(**defaults)  # type: ignore[arg-type]


class TestRenderMarkdown:
    def test_empty_profile_still_shows_languages_and_statistics(self) -> None:
        output = render_markdown(_empty_profile())

        assert "## Languages" in output
        assert "(no recognized language files)" in output
        assert "## Statistics" in output
        assert "Total files: 0" in output

    def test_empty_sections_are_omitted(self) -> None:
        output = render_markdown(_empty_profile())

        for heading in (
            "## Build System",
            "## Package Managers",
            "## Frameworks",
            "## Test Framework",
            "## CI System",
            "## Containerization",
            "## Databases",
            "## Configuration Files",
            "## Lockfiles",
            "## Entry Points",
            "## Services / Modules",
            "## Important Directories",
            "## Notes",
        ):
            assert heading not in output

    def test_detected_items_show_name_and_evidence(self) -> None:
        profile = _empty_profile(
            frameworks=[DetectedItem(name="flask", category="framework", evidence=["requirements.txt — flask dependency declared"])]
        )

        output = render_markdown(profile)

        assert "## Frameworks" in output
        assert "flask" in output
        assert "requirements.txt" in output

    def test_entry_points_rendered(self) -> None:
        profile = _empty_profile(
            entry_points=[EntryPoint(target="quor = quor.__main__:main", evidence="pyproject.toml [project.scripts]")]
        )

        output = render_markdown(profile)

        assert "## Entry Points" in output
        assert "quor = quor.__main__:main" in output

    def test_services_rendered(self) -> None:
        profile = _empty_profile(
            services=[ServiceModule(path="backend", manifest="pyproject.toml")]
        )

        output = render_markdown(profile)

        assert "## Services / Modules" in output
        assert "backend/" in output

    def test_important_directories_singular_plural(self) -> None:
        profile = _empty_profile(
            important_directories=[
                ImportantDirectory(path="tests", file_count=1),
                ImportantDirectory(path="docs", file_count=5),
            ]
        )

        output = render_markdown(profile)

        assert "tests/ — 1 file" in output
        assert "docs/ — 5 files" in output

    def test_language_singular_plural(self) -> None:
        profile = _empty_profile(
            languages=[LanguageStat(language="SQL", file_count=1, percentage=100.0)]
        )

        output = render_markdown(profile)

        assert "SQL — 1 file (100.0%)" in output

    def test_notes_rendered(self) -> None:
        profile = _empty_profile(notes=["Not a git repository — used filesystem walk."])

        output = render_markdown(profile)

        assert "## Notes" in output
        assert "Not a git repository" in output

    def test_output_ends_with_single_trailing_newline(self) -> None:
        output = render_markdown(_empty_profile())

        assert output.endswith("\n")
        assert not output.endswith("\n\n")

    def test_deterministic_across_repeated_calls(self) -> None:
        profile = _empty_profile(
            languages=[LanguageStat(language="Python", file_count=3, percentage=100.0)]
        )

        assert render_markdown(profile) == render_markdown(profile)


class TestRenderJson:
    def test_produces_valid_json(self) -> None:
        profile = _empty_profile()

        output = render_json(profile)
        parsed = orjson.loads(output)

        assert parsed["root"] == "/repo"
        assert parsed["statistics"]["total_files"] == 0

    def test_json_round_trips_detected_items(self) -> None:
        profile = _empty_profile(
            build_systems=[DetectedItem(name="hatch", category="build_system", evidence=["pyproject.toml — x"])]
        )

        parsed = orjson.loads(render_json(profile))

        assert parsed["build_systems"][0]["name"] == "hatch"
