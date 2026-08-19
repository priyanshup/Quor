"""Unit tests for QB-034's quor/discovery/session_scan.py."""

from __future__ import annotations

import os
import time
from pathlib import Path

import orjson
import pytest

from quor.discovery.session_scan import find_session_files, scan_project


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    path.write_text(
        "\n".join(orjson.dumps(line).decode() for line in lines) + "\n", encoding="utf-8"
    )


def _bash_pair(
    tool_id: str, command: str, stdout: str, *, description: str = "run something"
) -> list[dict]:
    """One assistant `tool_use` line plus one user `tool_result` line, the
    same two-line shape real Claude Code transcripts use."""
    return [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_id,
                        "name": "Bash",
                        "input": {"command": command, "description": description},
                    }
                ]
            },
        },
        {
            "type": "user",
            "toolUseResult": {"stdout": stdout, "stderr": "", "interrupted": False},
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": tool_id, "content": stdout}
                ]
            },
        },
    ]


def _cwd_line(cwd: str) -> dict:
    return {"type": "user", "cwd": cwd}


class TestFindSessionFiles:
    def test_no_projects_dir_returns_empty(self, tmp_path: Path) -> None:
        result = find_session_files(tmp_path / "myproject", claude_home=tmp_path / "nope")
        assert result == []

    def test_matches_by_cwd_field_not_directory_name(self, tmp_path: Path) -> None:
        claude_home = tmp_path / ".claude"
        project_dir = tmp_path / "myrepo"
        project_dir.mkdir(parents=True)
        # Directory name deliberately does NOT encode the real path — proves
        # matching goes through the recorded `cwd` field, not name-guessing.
        session_dir = claude_home / "projects" / "totally-unrelated-slug"
        session_dir.mkdir(parents=True)
        session_file = session_dir / "abc.jsonl"
        _write_jsonl(session_file, [_cwd_line(str(project_dir)), *_bash_pair("t1", "ls", "x")])

        result = find_session_files(project_dir, claude_home=claude_home)
        assert result == [session_file]

    def test_non_matching_cwd_excluded(self, tmp_path: Path) -> None:
        claude_home = tmp_path / ".claude"
        project_dir = tmp_path / "myrepo"
        other_dir = tmp_path / "other-repo"
        project_dir.mkdir(parents=True)
        other_dir.mkdir(parents=True)
        session_dir = claude_home / "projects" / "slug"
        session_dir.mkdir(parents=True)
        session_file = session_dir / "abc.jsonl"
        _write_jsonl(session_file, [_cwd_line(str(other_dir))])

        result = find_session_files(project_dir, claude_home=claude_home)
        assert result == []

    @pytest.mark.skipif(
        os.name != "nt",
        reason="Windows-only: _normalize_path uses os.path.normcase, which only "
        "lowercases on win32 (matching real Windows drive-letter-case/NTFS "
        "case-insensitivity) -- a no-op on POSIX, where paths are genuinely "
        "case-sensitive and this scenario doesn't arise the same way.",
    )
    def test_case_insensitive_match(self, tmp_path: Path) -> None:
        claude_home = tmp_path / ".claude"
        project_dir = tmp_path / "MyRepo"
        project_dir.mkdir(parents=True)
        session_dir = claude_home / "projects" / "slug"
        session_dir.mkdir(parents=True)
        session_file = session_dir / "abc.jsonl"
        _write_jsonl(session_file, [_cwd_line(str(project_dir).lower())])

        result = find_session_files(project_dir, claude_home=claude_home)
        assert result == [session_file]

    def test_old_session_excluded_by_days_cutoff(self, tmp_path: Path) -> None:
        claude_home = tmp_path / ".claude"
        project_dir = tmp_path / "myrepo"
        project_dir.mkdir(parents=True)
        session_dir = claude_home / "projects" / "slug"
        session_dir.mkdir(parents=True)
        session_file = session_dir / "abc.jsonl"
        _write_jsonl(session_file, [_cwd_line(str(project_dir))])
        old_time = time.time() - 90 * 86400
        os.utime(session_file, (old_time, old_time))

        result = find_session_files(project_dir, claude_home=claude_home, days=30)
        assert result == []

    def test_malformed_json_line_skipped_not_fatal(self, tmp_path: Path) -> None:
        claude_home = tmp_path / ".claude"
        project_dir = tmp_path / "myrepo"
        project_dir.mkdir(parents=True)
        session_dir = claude_home / "projects" / "slug"
        session_dir.mkdir(parents=True)
        session_file = session_dir / "abc.jsonl"
        session_file.write_text(
            "not json at all\n" + orjson.dumps(_cwd_line(str(project_dir))).decode() + "\n",
            encoding="utf-8",
        )

        result = find_session_files(project_dir, claude_home=claude_home)
        assert result == [session_file]


class TestScanProject:
    def _setup(self, tmp_path: Path, lines: list[dict]) -> Path:
        claude_home = tmp_path / ".claude"
        project_dir = tmp_path / "myrepo"
        project_dir.mkdir(parents=True)
        session_dir = claude_home / "projects" / "slug"
        session_dir.mkdir(parents=True)
        session_file = session_dir / "abc.jsonl"
        _write_jsonl(session_file, [_cwd_line(str(project_dir)), *lines])
        return project_dir, claude_home

    def test_no_sessions_found(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "myrepo"
        project_dir.mkdir(parents=True)
        report = scan_project(project_dir, claude_home=tmp_path / ".claude")
        assert report.sessions_scanned == 0
        assert report.commands_scanned == 0
        assert report.total_tokens_would_save == 0

    def test_uncompressed_command_scored_against_real_pipeline(self, tmp_path: Path) -> None:
        # Large git status output with plenty of boilerplate the real
        # git-status filter strips — proves scan_project() drives the real
        # FilterRegistry, not a stub.
        big_status = (
            "On branch main\n"
            "  (use \"git add\" to stage)\n" * 20
            + "\tmodified:   src/main.py\n"
        )
        project_dir, claude_home = self._setup(
            tmp_path, _bash_pair("t1", "git status", big_status)
        )
        report = scan_project(project_dir, claude_home=claude_home)
        assert report.sessions_scanned == 1
        assert report.commands_scanned == 1
        assert report.commands_already_covered == 0
        assert report.total_tokens_would_save > 0
        assert "git-status" in report.by_filter
        assert report.by_filter["git-status"].tokens_would_save > 0

    def test_already_compressed_content_excluded_from_totals(self, tmp_path: Path) -> None:
        stdout = "On branch main\n" + "  (use \"git add\")\n" * 20
        compress_call = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t2",
                        "name": "mcp__quor__compress_context",
                        "input": {"raw_text": stdout},
                    }
                ]
            },
        }
        lines = [*_bash_pair("t1", "git status", stdout), compress_call]
        project_dir, claude_home = self._setup(tmp_path, lines)
        report = scan_project(project_dir, claude_home=claude_home)
        assert report.commands_scanned == 1
        assert report.commands_already_covered == 1
        assert report.total_tokens_would_save == 0
        assert report.by_filter == {}

    def test_zero_savings_command_still_counted(self, tmp_path: Path) -> None:
        project_dir, claude_home = self._setup(
            tmp_path, _bash_pair("t1", "echo hi", "hi\n")
        )
        report = scan_project(project_dir, claude_home=claude_home)
        assert report.commands_scanned == 1
        # small/incompressible output — total original tokens still counted
        assert report.total_original_tokens > 0

    def test_top_commands_sorted_by_savings_descending(self, tmp_path: Path) -> None:
        small = _bash_pair("t1", "git status", "On branch main\nnothing to commit")
        big_status = "On branch main\n" + "  (use \"git add\")\n" * 40 + "\tmodified: x.py\n"
        big = _bash_pair("t2", "git status", big_status, description="big one")
        project_dir, claude_home = self._setup(tmp_path, [*small, *big])
        report = scan_project(project_dir, claude_home=claude_home, top_n=5)
        assert len(report.top_commands) == 2
        assert report.top_commands[0].tokens_would_save >= report.top_commands[1].tokens_would_save

    def test_top_n_respected(self, tmp_path: Path) -> None:
        lines: list[dict] = []
        for i in range(5):
            lines.extend(
                _bash_pair(f"t{i}", "git status", "On branch main\n" + "  (use \"git add\")\n" * 10)
            )
        project_dir, claude_home = self._setup(tmp_path, lines)
        report = scan_project(project_dir, claude_home=claude_home, top_n=2)
        assert len(report.top_commands) == 2

    def test_unmatched_tool_result_ignored(self, tmp_path: Path) -> None:
        """A tool_result with no matching pending tool_use id (e.g. a
        truncated/mid-stream transcript) must never raise."""
        orphan_result = {
            "type": "user",
            "toolUseResult": {"stdout": "orphan output"},
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "does-not-exist", "content": "x"}
                ]
            },
        }
        project_dir, claude_home = self._setup(tmp_path, [orphan_result])
        report = scan_project(project_dir, claude_home=claude_home)
        assert report.commands_scanned == 0

    def test_non_bash_tool_use_ignored(self, tmp_path: Path) -> None:
        read_call = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "Read",
                            "input": {"file_path": "foo.py"},
                        }
                    ]
                },
            },
            {
                "type": "user",
                "toolUseResult": {"stdout": "file contents here"},
                "message": {
                    "content": [
                        {"type": "tool_result", "tool_use_id": "t1", "content": "file contents"}
                    ]
                },
            },
        ]
        project_dir, claude_home = self._setup(tmp_path, read_call)
        report = scan_project(project_dir, claude_home=claude_home)
        assert report.commands_scanned == 0
