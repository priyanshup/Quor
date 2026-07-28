"""Unit tests for quor/pipeline/repo_profile/model.py."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from quor.pipeline.repo_profile.model import (
    DetectedItem,
    RepoProfile,
    RepoStatistics,
)


class TestRepoProfileDefaults:
    def test_minimal_construction_defaults_empty_lists(self) -> None:
        profile = RepoProfile(
            root="/repo",
            statistics=RepoStatistics(
                total_files=0, total_directories=0, primary_language=None, git_commit_count=None
            ),
        )

        assert profile.languages == []
        assert profile.frameworks == []
        assert profile.entry_points == []
        assert profile.notes == []

    def test_is_frozen(self) -> None:
        profile = RepoProfile(
            root="/repo",
            statistics=RepoStatistics(
                total_files=0, total_directories=0, primary_language=None, git_commit_count=None
            ),
        )
        with pytest.raises(ValidationError):
            profile.root = "/other"  # type: ignore[misc]

    def test_detected_item_is_frozen(self) -> None:
        item = DetectedItem(name="flask", category="framework", evidence=["x"])
        with pytest.raises(ValidationError):
            item.name = "django"  # type: ignore[misc]

    def test_model_dump_round_trips(self) -> None:
        profile = RepoProfile(
            root="/repo",
            frameworks=[DetectedItem(name="flask", category="framework", evidence=["x"])],
            statistics=RepoStatistics(
                total_files=1, total_directories=0, primary_language="Python", git_commit_count=5
            ),
        )

        dumped = profile.model_dump()
        rebuilt = RepoProfile.model_validate(dumped)

        assert rebuilt == profile
