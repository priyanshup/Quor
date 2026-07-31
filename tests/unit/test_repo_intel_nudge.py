"""Unit tests for quor/pipeline/repo_profile/nudge.py (QB-090).

Every test scans a `repo` fixture directory (`tmp_path / "repo"`), never
`tmp_path` itself — mirrors `test_repo_intel.py`'s own documented reason:
`tests/conftest.py`'s autouse fixture redirects `platformdirs.user_data_dir`
to `tmp_path / "data" / "quor"`, a sibling of `repo`, not a descendant of
it. Scanning `tmp_path` directly would make the cache's own JSON files
(including this module's `nudge_state.json`) part of the git repo under
test, corrupting every "how many files changed" assertion below.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from quor.pipeline.repo_profile.intel import ensure_repo_intelligence
from quor.pipeline.repo_profile.nudge import (
    MAX_NEVER_BUILT_SHOWS,
    STALE_FILE_THRESHOLD,
    NudgeState,
    _load_nudge_state,
    _nudge_state_path,
    _save_nudge_state,
    compute_hook_nudge,
    estimate_build_cost,
    is_git_repo,
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    return root


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)


def _commit_all(root: Path, message: str) -> None:
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=root, check=True)


def _force_stale_check_due(root: Path) -> None:
    """Test helper: reopen the 24h rate-limit gate immediately, so a test
    doesn't need to wait a real day to exercise the "due for a check"
    branch."""
    state = _load_nudge_state(root)
    _save_nudge_state(
        root,
        NudgeState(never_built_shown_count=state.never_built_shown_count, last_stale_check_at=None),
    )


class TestIsGitRepo:
    def test_false_outside_git_repo(self, repo: Path) -> None:
        assert is_git_repo(repo) is False

    def test_true_inside_git_repo(self, repo: Path) -> None:
        (repo / "a.py").write_text("pass\n", encoding="utf-8")
        _init_git_repo(repo)
        _commit_all(repo, "init")
        assert is_git_repo(repo) is True


class TestEstimateBuildCost:
    def test_counts_files_and_estimates_positive_seconds(self, repo: Path) -> None:
        (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
        (repo / "b.py").write_text("y = 2\n", encoding="utf-8")

        file_count, estimated_seconds = estimate_build_cost(repo)

        assert file_count == 2
        assert estimated_seconds >= 1  # never zero, even for a tiny repo

    def test_empty_repo_still_returns_a_minimum_estimate(self, repo: Path) -> None:
        file_count, estimated_seconds = estimate_build_cost(repo)
        assert file_count == 0
        assert estimated_seconds == 1


class TestComputeHookNudgeNeverBuilt:
    def test_fires_up_to_the_throttle_cap(self, repo: Path) -> None:
        (repo / "a.py").write_text("pass\n", encoding="utf-8")
        _init_git_repo(repo)
        _commit_all(repo, "init")

        shown = [compute_hook_nudge(repo) for _ in range(MAX_NEVER_BUILT_SHOWS + 2)]

        assert all(tip is not None for tip in shown[:MAX_NEVER_BUILT_SHOWS])
        assert all(tip is None for tip in shown[MAX_NEVER_BUILT_SHOWS:])

    def test_message_mentions_quor_map(self, repo: Path) -> None:
        (repo / "a.py").write_text("pass\n", encoding="utf-8")
        _init_git_repo(repo)
        _commit_all(repo, "init")

        tip = compute_hook_nudge(repo)
        assert tip is not None
        assert "quor map" in tip

    def test_silent_outside_a_git_repo(self, repo: Path) -> None:
        """The hook-facing nudge is git-gated, same as `quor init
        --claude`'s own `_maybe_offer_repo_intelligence_setup` — a bare,
        non-project directory has nothing sensible to index. This also
        happens to be exactly what keeps this feature from polluting the
        rest of this codebase's own test suite, which reads through
        countless non-git temp directories (see nudge.py's own docstring
        for the regression this was found fixing)."""
        assert compute_hook_nudge(repo) is None


class TestComputeHookNudgeBuilt:
    def test_silent_when_nothing_changed(self, repo: Path) -> None:
        (repo / "a.py").write_text("pass\n", encoding="utf-8")
        _init_git_repo(repo)
        _commit_all(repo, "init")

        ensure_repo_intelligence(repo)

        assert compute_hook_nudge(repo) is None

    def test_silent_when_changed_file_count_is_below_threshold(self, repo: Path) -> None:
        (repo / "a.py").write_text("pass\n", encoding="utf-8")
        _init_git_repo(repo)
        _commit_all(repo, "init")
        ensure_repo_intelligence(repo)

        for i in range(STALE_FILE_THRESHOLD - 1):
            (repo / f"f{i}.py").write_text("x = 1\n", encoding="utf-8")
        _commit_all(repo, "small change")
        _force_stale_check_due(repo)

        assert compute_hook_nudge(repo) is None

    def test_fires_when_changed_file_count_meets_threshold(self, repo: Path) -> None:
        (repo / "a.py").write_text("pass\n", encoding="utf-8")
        _init_git_repo(repo)
        _commit_all(repo, "init")
        ensure_repo_intelligence(repo)

        for i in range(STALE_FILE_THRESHOLD + 5):
            (repo / f"f{i}.py").write_text("x = 1\n", encoding="utf-8")
        _commit_all(repo, "bulk change")
        _force_stale_check_due(repo)

        tip = compute_hook_nudge(repo)

        assert tip is not None
        assert "quor map" in tip
        assert "changed" in tip

    def test_does_not_recheck_within_the_rate_limit_window(self, repo: Path) -> None:
        """Regression test: without the 24h gate, every Read call after a
        bulk change would re-run the git diff and re-show the tip — this
        pins that a second call immediately after a real check stays
        silent, even though the repo is still just as stale."""
        (repo / "a.py").write_text("pass\n", encoding="utf-8")
        _init_git_repo(repo)
        _commit_all(repo, "init")
        ensure_repo_intelligence(repo)

        for i in range(STALE_FILE_THRESHOLD + 5):
            (repo / f"f{i}.py").write_text("x = 1\n", encoding="utf-8")
        _commit_all(repo, "bulk change")
        _force_stale_check_due(repo)

        first = compute_hook_nudge(repo)
        second = compute_hook_nudge(repo)

        assert first is not None
        assert second is None

    def test_uncommitted_changes_alone_are_not_detected(self, repo: Path) -> None:
        """Documented limitation (see nudge.py's own docstring): staleness
        detection is git-commit-based only. A working-tree change with
        nothing committed produces no git_head difference, so no nudge
        fires — an accepted false negative, not a bug."""
        (repo / "a.py").write_text("pass\n", encoding="utf-8")
        _init_git_repo(repo)
        _commit_all(repo, "init")
        ensure_repo_intelligence(repo)

        for i in range(STALE_FILE_THRESHOLD + 5):
            (repo / f"f{i}.py").write_text("x = 1\n", encoding="utf-8")
        # Deliberately no _commit_all() call here.
        _force_stale_check_due(repo)

        assert compute_hook_nudge(repo) is None


class TestNudgeStateGate:
    def test_recently_checked_state_suppresses_a_recheck(self, repo: Path) -> None:
        (repo / "a.py").write_text("pass\n", encoding="utf-8")
        _init_git_repo(repo)
        _commit_all(repo, "init")
        ensure_repo_intelligence(repo)

        for i in range(STALE_FILE_THRESHOLD + 5):
            (repo / f"f{i}.py").write_text("x = 1\n", encoding="utf-8")
        _commit_all(repo, "bulk change")

        _save_nudge_state(
            repo,
            NudgeState(never_built_shown_count=0, last_stale_check_at=datetime.now(UTC).isoformat()),
        )

        assert compute_hook_nudge(repo) is None

    def test_corrupted_state_file_fails_open_to_defaults(self, repo: Path) -> None:
        (repo / "a.py").write_text("pass\n", encoding="utf-8")
        _init_git_repo(repo)
        _commit_all(repo, "init")

        state_path = _nudge_state_path(repo)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text("not valid json{{{", encoding="utf-8")

        # Falls back to a fresh NudgeState() rather than raising.
        assert compute_hook_nudge(repo) is not None

    def test_old_last_stale_check_timestamp_allows_a_recheck(self, repo: Path) -> None:
        (repo / "a.py").write_text("pass\n", encoding="utf-8")
        _init_git_repo(repo)
        _commit_all(repo, "init")
        ensure_repo_intelligence(repo)

        for i in range(STALE_FILE_THRESHOLD + 5):
            (repo / f"f{i}.py").write_text("x = 1\n", encoding="utf-8")
        _commit_all(repo, "bulk change")

        eight_days_ago = (datetime.now(UTC) - timedelta(days=8)).isoformat()
        _save_nudge_state(repo, NudgeState(never_built_shown_count=0, last_stale_check_at=eight_days_ago))

        assert compute_hook_nudge(repo) is not None
