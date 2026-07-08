"""Integration tests for the six V1 CLI commands (TD-006 / V1-Q07).

Unlike tests/unit/test_cli.py — which deliberately mocks `subprocess.run` and
`FilterRegistry` to isolate each command's own logic — these tests avoid
mocking the two boundaries that matter most: real subprocess execution and a
real on-disk SQLite database. QB-019's Windows npm/npx bug was invisible to
the entire test suite specifically because every dispatcher test mocked
`subprocess.run`; these tests exist so that class of gap has a real
(non-mocked) path to be caught in for the CLI surface as well.

Marked `@pytest.mark.integration` per CLAUDE.md's testing conventions. Test
isolation (no writes to the real user config/data directories) still holds:
the autouse `_isolate_platformdirs` fixture in tests/conftest.py redirects
`platformdirs` for every test in this process, including these — only a
genuinely separate OS process would escape it, and none of these tests spawn
`quor` itself as a subprocess (they call the same command functions the real
`quor` entry point calls, via Typer's CliRunner or directly).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import orjson
import platformdirs
import pytest
from typer.testing import CliRunner

from quor.adapters.dispatcher import run_dispatch
from quor.cli.main import app
from quor.tracking.db import get_tracking_db, query_gain

pytestmark = pytest.mark.integration

runner = CliRunner()


@pytest.fixture
def git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A real, empty git repository; cwd for the duration of the test."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# quor gain — real dispatch (real subprocess.run) -> real SQLite -> real read
# ---------------------------------------------------------------------------


class TestGainIntegration:
    def test_real_dispatch_visible_in_gain(self, git_repo: Path) -> None:
        """A `git status` dispatched through the real run_dispatch() path —
        real subprocess.run, real pipeline, real SQLite write via the
        background TrackingDB thread — is visible via `quor gain` afterward."""
        tracking = get_tracking_db()
        exit_code = run_dispatch(["git", "status"], tracking=tracking)
        tracking.flush()
        tracking.close()

        assert exit_code == 0

        # Verify the real on-disk SQLite row directly (query_gain, not string
        # matching on rendered console output) — "git-status" would only
        # appear in `quor gain`'s "Top savings" section if this real
        # invocation happened to save a nonzero number of tokens, which a
        # freshly `git init`'d repo's tiny status output legitimately might
        # not (QB-017), so the durable check is the row itself, not the
        # rendered section that's conditional on savings.
        db_path = Path(platformdirs.user_data_dir("quor")) / "quor.db"
        report = query_gain(db_path, git_repo, days=30)
        assert report.total_invocations == 1
        assert report.passthrough_count == 0  # git-status is a known/filtered command

        result = runner.invoke(app, ["gain", "--project", str(git_repo)])
        assert result.exit_code == 0
        assert "No invocations recorded" not in result.output
        assert "Commands processed" in result.output


# ---------------------------------------------------------------------------
# quor explain — real subprocess.run, not mocked
# ---------------------------------------------------------------------------


class TestExplainIntegration:
    def test_real_command_actually_executes(self, git_repo: Path) -> None:
        """Every existing unit test for `explain` mocks subprocess.run — this
        confirms the real code path actually spawns and captures a real
        process rather than only ever being exercised against a mock."""
        result = runner.invoke(app, ["explain", "git status"])
        assert result.exit_code == 0
        assert "git-status" in result.output
        assert "Stage Trace" in result.output
        assert "Tokens:" in result.output

    def test_real_command_failure_is_reported(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A real command that fails (not a git repo) still runs for real —
        explain shows the actual captured output, not a canned mock result."""
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["explain", "git status"])
        assert result.exit_code == 0
        assert "Stage Trace" in result.output


# ---------------------------------------------------------------------------
# quor verify — real built-in filters, no mocking
# ---------------------------------------------------------------------------


class TestVerifyIntegration:
    def test_real_builtin_filters_all_pass(self) -> None:
        result = runner.invoke(app, ["verify"])
        assert result.exit_code == 0
        assert "0 failure(s)" in result.output.replace("\n", " ")


# ---------------------------------------------------------------------------
# quor validate — real filter file on disk, no mocking
# ---------------------------------------------------------------------------


class TestValidateIntegration:
    def test_real_builtin_tiers_validate_clean(self) -> None:
        result = runner.invoke(app, ["validate"])
        assert result.exit_code == 0
        assert "git-status" in result.output
        assert "pytest" in result.output

    def test_real_project_local_filter_file_on_disk(self, tmp_path: Path) -> None:
        f = tmp_path / "custom.toml"
        f.write_text(
            '[[filter]]\nname = "custom-integration"\nmatch_command = "^custom-tool\\\\b"\n'
            "stages = []\n",
            encoding="utf-8",
        )
        result = runner.invoke(app, ["validate", str(f)])
        assert result.exit_code == 0
        assert "custom-integration" in result.output


# ---------------------------------------------------------------------------
# quor init --claude -> quor doctor — real files, chained end to end
# ---------------------------------------------------------------------------


class TestInitAndDoctorIntegration:
    def test_real_init_then_doctor_all_green(self, tmp_path: Path) -> None:
        """A real `quor init --claude` (real hook script + settings.json
        written to disk, no mocked platformdirs beyond the autouse test
        isolation fixture) followed by a real `quor doctor` — the full
        install-then-healthcheck flow a real user goes through."""
        settings_path = tmp_path / "settings.json"

        init_result = runner.invoke(
            app, ["init", "--claude", "--yes", "--settings-path", str(settings_path)]
        )
        assert init_result.exit_code == 0
        assert settings_path.exists()

        data = orjson.loads(settings_path.read_bytes())
        commands = [
            h["command"]
            for entry in data["hooks"]["PreToolUse"]
            for h in entry["hooks"]
        ]
        assert any("claude-hook.ps1" in c for c in commands)

        # init already runs `doctor` internally (asserted above via exit_code
        # 0), but re-run it standalone too: a real, separate doctor
        # invocation must independently see the hook script init just wrote.
        doctor_result = runner.invoke(app, ["doctor", "--settings-path", str(settings_path)])
        assert doctor_result.exit_code == 0
        assert "✓ Hook script installed" in doctor_result.output
        assert "✓ Tracking DB readable/writable" in doctor_result.output
        assert "✓ Built-in filter tests pass" in doctor_result.output
