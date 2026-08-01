"""Unit tests for QB-099A's `GitStructuralDiffPlugin`
(`quor/plugins/builtin/git_structural_diff.py`) — the real subprocess/
filesystem-backed half of git-diff structural enrichment. Uses a real,
throwaway git repo (`tmp_path`) rather than mocking `subprocess.run`,
specifically because the module's own docstring records a real bug (a
double-colon `git show` revision spec) that every stubbed-fetcher test in
`test_git_diff_enrich.py` was structurally unable to catch — only a real
`git show` call can.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from quor.plugins.base import ExecutionMode, PluginContext, PluginPayload
from quor.plugins.builtin.git_structural_diff import (
    GitStructuralDiffPlugin,
    _git_show,
    _read_working_tree,
)


def _run_git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _run_git(["init", "-q"], tmp_path)
    _run_git(["config", "user.email", "t@example.com"], tmp_path)
    _run_git(["config", "user.name", "t"], tmp_path)
    return tmp_path


def _write(repo: Path, name: str, content: str) -> None:
    (repo / name).write_text(content, encoding="utf-8", newline="\n")


_OLD = "def a():\n    x = 1\n    y = 2\n    return x + y\n\n\ndef b():\n    return 1\n"
_NEW = "def b():\n    return 1\n\n\ndef a():\n    x = 1\n    y = 2\n    return x + y\n"


class TestRealGitShowAndWorkingTreeFetch:
    def test_git_show_index_sentinel_fetches_index_content(self, repo: Path) -> None:
        _write(repo, "foo.py", _OLD)
        _run_git(["add", "foo.py"], repo)
        content = _git_show("", "foo.py", repo)
        assert content == _OLD

    def test_git_show_head_fetches_committed_content(self, repo: Path) -> None:
        _write(repo, "foo.py", _OLD)
        _run_git(["add", "foo.py"], repo)
        _run_git(["commit", "-q", "-m", "init"], repo)
        _write(repo, "foo.py", _NEW)
        content = _git_show("HEAD", "foo.py", repo)
        assert content == _OLD

    def test_git_show_missing_path_returns_none(self, repo: Path) -> None:
        assert _git_show("HEAD", "does_not_exist.py", repo) is None

    def test_read_working_tree_reads_the_real_file(self, repo: Path) -> None:
        _write(repo, "foo.py", _NEW)
        assert _read_working_tree("foo.py", repo) == _NEW

    def test_read_working_tree_missing_file_returns_none(self, repo: Path) -> None:
        assert _read_working_tree("nope.py", repo) is None


class TestGitStructuralDiffPluginEndToEnd:
    def _ctx(self, repo: Path) -> PluginContext:
        return PluginContext(project_root=repo, mode=ExecutionMode.OPTIMIZE, session_id="", invocation_id="x")

    def test_pure_reorder_collapses_to_a_structural_summary(self, repo: Path) -> None:
        _write(repo, "foo.py", _OLD)
        _run_git(["add", "foo.py"], repo)
        _run_git(["commit", "-q", "-m", "init"], repo)
        _write(repo, "foo.py", _NEW)

        raw_diff = subprocess.run(["git", "diff"], cwd=repo, capture_output=True, text=True).stdout
        payload = PluginPayload(command="git diff", raw_output=raw_diff, current_output=raw_diff, content_type="diff")

        plugin = GitStructuralDiffPlugin()
        result = plugin.execute(payload, self._ctx(repo))

        assert result.was_modified
        assert "reordered: a" in result.payload.current_output
        assert "reordered: b" in result.payload.current_output
        assert "+def b():" not in result.payload.current_output

    def test_non_git_diff_command_is_untouched(self, repo: Path) -> None:
        payload = PluginPayload(command="npm test", raw_output="ok", current_output="ok", content_type="text")
        plugin = GitStructuralDiffPlugin()
        result = plugin.execute(payload, self._ctx(repo))
        assert not result.was_modified
        assert result.payload.current_output == "ok"

    def test_git_status_is_not_matched_only_diff_and_show(self, repo: Path) -> None:
        payload = PluginPayload(command="git status", raw_output="clean", current_output="clean", content_type="text")
        plugin = GitStructuralDiffPlugin()
        result = plugin.execute(payload, self._ctx(repo))
        assert not result.was_modified

    def test_non_python_only_diff_is_untouched(self, repo: Path) -> None:
        _write(repo, "README.md", "old\n")
        _run_git(["add", "README.md"], repo)
        _run_git(["commit", "-q", "-m", "init"], repo)
        _write(repo, "README.md", "new\n")

        raw_diff = subprocess.run(["git", "diff"], cwd=repo, capture_output=True, text=True).stdout
        payload = PluginPayload(command="git diff", raw_output=raw_diff, current_output=raw_diff, content_type="diff")
        plugin = GitStructuralDiffPlugin()
        result = plugin.execute(payload, self._ctx(repo))
        assert not result.was_modified
        assert result.payload.current_output == raw_diff

    def test_staged_diff_uses_head_vs_index(self, repo: Path) -> None:
        _write(repo, "foo.py", _OLD)
        _run_git(["add", "foo.py"], repo)
        _run_git(["commit", "-q", "-m", "init"], repo)
        _write(repo, "foo.py", _NEW)
        _run_git(["add", "foo.py"], repo)

        raw_diff = subprocess.run(["git", "diff", "--staged"], cwd=repo, capture_output=True, text=True).stdout
        payload = PluginPayload(
            command="git diff --staged", raw_output=raw_diff, current_output=raw_diff, content_type="diff"
        )
        plugin = GitStructuralDiffPlugin()
        result = plugin.execute(payload, self._ctx(repo))
        assert result.was_modified
        assert "reordered: a" in result.payload.current_output

    def test_git_show_of_a_commit(self, repo: Path) -> None:
        _write(repo, "foo.py", _OLD)
        _run_git(["add", "foo.py"], repo)
        _run_git(["commit", "-q", "-m", "init"], repo)
        _write(repo, "foo.py", _NEW)
        _run_git(["add", "foo.py"], repo)
        _run_git(["commit", "-q", "-m", "reorder"], repo)

        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
        raw_show = subprocess.run(["git", "show", sha], cwd=repo, capture_output=True, text=True).stdout
        payload = PluginPayload(
            command=f"git show {sha}", raw_output=raw_show, current_output=raw_show, content_type="diff"
        )
        plugin = GitStructuralDiffPlugin()
        result = plugin.execute(payload, self._ctx(repo))
        assert result.was_modified
        assert "reordered: a" in result.payload.current_output

    def test_project_root_none_falls_back_to_cwd_without_raising(self, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(repo)
        payload = PluginPayload(command="git diff", raw_output="", current_output="", content_type="diff")
        ctx = PluginContext(project_root=None, mode=ExecutionMode.OPTIMIZE, session_id="", invocation_id="x")
        plugin = GitStructuralDiffPlugin()
        result = plugin.execute(payload, ctx)  # must not raise
        assert result.payload.current_output == ""
