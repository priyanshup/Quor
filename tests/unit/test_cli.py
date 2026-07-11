"""Unit tests for quor/cli/commands/: the six V1 CLI commands."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import orjson
import pytest
from typer.testing import CliRunner

from quor.cli.main import app
from quor.errors import ExitCode

runner = CliRunner()


def _make_proc(stdout: str = "", returncode: int = 0) -> MagicMock:
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.stdout = stdout
    proc.returncode = returncode
    return proc


def _install_real_hooks(tmp_path: Path, settings_path: Path) -> None:
    """Write both hook scripts with real, current-version content and a
    settings.json that actually registers them — the full "healthy install"
    state `doctor`'s registered/up-to-date checks (QB-037) require. Replaces
    the old bare "dummy" script-content fixtures, which predate those checks
    and would now read as "installed but not registered / not up to date".
    """
    from quor.adapters.hook_manifest import HOOK_SPECS, render_hook_script

    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    settings: dict[str, Any] = {"hooks": {}}
    for spec in HOOK_SPECS:
        script_path = hooks_dir / spec.script_name
        script_path.write_text(render_hook_script(spec, python=sys.executable), encoding="utf-8")
        settings["hooks"].setdefault(spec.event, []).append(
            {
                "matcher": spec.matcher,
                "hooks": [
                    {
                        "type": "command",
                        "command": f'powershell -ExecutionPolicy Bypass -File "{script_path}"',
                    }
                ],
            }
        )
    settings_path.write_text(orjson.dumps(settings).decode("utf-8"), encoding="utf-8")


# ---------------------------------------------------------------------------
# quor validate
# ---------------------------------------------------------------------------


class TestValidate:
    def test_all_tiers_valid(self) -> None:
        result = runner.invoke(app, ["validate"])
        assert result.exit_code == 0
        assert "git-status" in result.output

    def test_single_file_valid(self, tmp_path: Path) -> None:
        f = tmp_path / "ok.toml"
        f.write_text(
            '[[filter]]\nname = "ok"\nmatch_command = "^foo$"\nstages = []\n',
            encoding="utf-8",
        )
        result = runner.invoke(app, ["validate", str(f)])
        assert result.exit_code == 0
        assert "ok" in result.output

    def test_single_file_invalid_exits_2(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.toml"
        f.write_text("not valid toml [[[", encoding="utf-8")
        result = runner.invoke(app, ["validate", str(f)])
        assert result.exit_code == ExitCode.CONFIG_ERROR

    def test_single_file_missing_path_exits_2(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["validate", str(tmp_path / "missing.toml")])
        assert result.exit_code == ExitCode.CONFIG_ERROR

    def test_all_tiers_reports_load_warning_as_error(self, tmp_path: Path) -> None:
        with patch("quor.cli.commands.validate.FilterRegistry") as mock_reg:
            import warnings

            def _init(*_a: Any, **_kw: Any) -> MagicMock:
                warnings.warn("[quor] Failed to load user filter bad.toml: boom", stacklevel=2)
                inst = MagicMock()
                inst.all_filters.return_value = []
                return inst

            mock_reg.side_effect = _init
            result = runner.invoke(app, ["validate"])
        assert result.exit_code == ExitCode.CONFIG_ERROR


# ---------------------------------------------------------------------------
# quor explain
# ---------------------------------------------------------------------------


class TestExplain:
    def test_known_command_shows_trace(self) -> None:
        proc = _make_proc(
            stdout="On branch main\nnothing to commit, working tree clean\n"
        )
        with patch("subprocess.run", return_value=proc):
            result = runner.invoke(app, ["explain", "git status"])
        assert result.exit_code == 0
        assert "git-status" in result.output
        assert "Stage Trace" in result.output
        assert "Tokens:" in result.output

    def test_unmatched_command_falls_through_to_generic(self) -> None:
        # The built-in "generic" filter (match_command = '.') matches every
        # non-empty command, so unknown tools fall through to it rather than
        # going unfiltered.
        proc = _make_proc(stdout="some arbitrary output\n")
        with patch("subprocess.run", return_value=proc):
            result = runner.invoke(app, ["explain", "some-totally-unknown-tool --flag"])
        assert result.exit_code == 0
        assert "generic" in result.output

    def test_subprocess_failure_exits_1(self) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError("not found")):
            result = runner.invoke(app, ["explain", "git status"])
        assert result.exit_code == 1
        assert "Could not run command" in result.output


# ---------------------------------------------------------------------------
# quor gain
# ---------------------------------------------------------------------------


class TestGain:
    def test_empty_db(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["gain", "--project", str(tmp_path)])
        assert result.exit_code == 0
        assert "No invocations recorded" in result.output
        assert "Mode: audit" in result.output

    def test_populated_db(self, tmp_path: Path) -> None:
        from quor.tracking.db import InvocationRecord, TrackingDB

        db_path = tmp_path / "data" / "quor.db"
        db = TrackingDB(db_path=db_path)
        db.record(
            InvocationRecord(
                command="git status",
                project_path=tmp_path.as_posix(),
                original_tokens=100,
                final_tokens=20,
                filter_name="git-status",
                was_passthrough=False,
                duration_ms=5.0,
            )
        )
        db.flush()
        db.close()

        with patch("platformdirs.user_data_dir", return_value=str(tmp_path / "data")):
            result = runner.invoke(app, ["gain", "--project", str(tmp_path), "--days", "30"])

        assert result.exit_code == 0
        output = result.output
        assert "Quor Gain (Last 30 days)" in output
        assert "Commands processed" in output
        assert "Filter hit rate" in output
        assert "100%" in output  # single non-passthrough invocation -> 100% hit rate
        assert "Tokens before" in output
        assert "~100" in output
        assert "Tokens after" in output
        assert "~20" in output
        assert "YOU SAVED" in output
        assert "~80 tokens (80%)" in output
        assert "Top savings" in output
        assert "git-status" in output
        assert "estimated via the char/4 approximation" in output
        assert "±20% versus a real tokenizer" in output
        # This scenario has zero Read-hook rows, so the "not
        # represented" notice must appear.
        assert "No Read-hook activity has been recorded" in output
        assert "quor init --claude" in output

    def test_read_activity_included_alongside_bash(self, tmp_path: Path) -> None:
        """QB-007D: a Read-produced row (command="Read: ...") requires no
        special-casing anywhere in `quor gain` — it aggregates into the same
        totals and Top savings table as a Bash row, purely because both are
        ordinary InvocationRecord rows in the same table."""
        from quor.tracking.db import InvocationRecord, TrackingDB

        db_path = tmp_path / "data" / "quor.db"
        db = TrackingDB(db_path=db_path)
        db.record(
            InvocationRecord(
                command="git status",
                project_path=tmp_path.as_posix(),
                original_tokens=100,
                final_tokens=20,
                filter_name="git-status",
                was_passthrough=False,
                duration_ms=5.0,
            )
        )
        db.record(
            InvocationRecord(
                command="Read: docs/design.md",
                project_path=tmp_path.as_posix(),
                original_tokens=3700,
                final_tokens=2600,
                filter_name="markdown",
                was_passthrough=False,
                duration_ms=8.0,
            )
        )
        db.flush()
        db.close()

        with patch("platformdirs.user_data_dir", return_value=str(tmp_path / "data")):
            result = runner.invoke(app, ["gain", "--project", str(tmp_path), "--days", "30"])

        assert result.exit_code == 0
        output = result.output
        assert "Commands processed" in output
        assert "2" in output  # total_invocations, both rows counted
        assert "markdown" in output  # Read's filter shows up in Top savings
        assert "git-status" in output
        assert "YOU SAVED" in output
        # A genuine Read row is present, so the "no Read-hook
        # activity" notice must not appear.
        assert "No Read-hook activity has been recorded" not in output

    def test_docx_read_activity_pools_with_markdown_in_top_savings(self, tmp_path: Path) -> None:
        """QB-007E4: a DOCX/PDF Read is tracked with filter_name="markdown"
        (the same shared filter, by design — no docx.toml/pdf.toml), so it
        pools into the exact same "markdown" Top savings row a direct .md
        Read would — proving no separate reporting category was
        introduced for extracted document types."""
        from quor.tracking.db import InvocationRecord, TrackingDB

        db_path = tmp_path / "data" / "quor.db"
        db = TrackingDB(db_path=db_path)
        db.record(
            InvocationRecord(
                command="Read: docs/design.md",
                project_path=tmp_path.as_posix(),
                original_tokens=1000,
                final_tokens=800,
                filter_name="markdown",
                was_passthrough=False,
                duration_ms=5.0,
            )
        )
        db.record(
            InvocationRecord(
                command="Read: report.docx",
                project_path=tmp_path.as_posix(),
                original_tokens=5000,
                final_tokens=3000,
                filter_name="markdown",
                was_passthrough=False,
                duration_ms=12.0,
            )
        )
        db.flush()
        db.close()

        with patch("platformdirs.user_data_dir", return_value=str(tmp_path / "data")):
            result = runner.invoke(app, ["gain", "--project", str(tmp_path), "--days", "30"])

        assert result.exit_code == 0
        output = result.output
        assert "2" in output  # both rows counted
        assert "markdown" in output
        # Top savings pools both rows' markdown savings into one line:
        # (1000-800) + (5000-3000) = 2200 total saved for "markdown".
        assert "2.2k" in output
        # Both rows are Read-produced, so the notice must not appear.
        assert "No Read-hook activity has been recorded" not in output

    def test_zero_saved_filter_hidden_from_top_savings(self, tmp_path: Path) -> None:
        """A filter that saved nothing must not appear in Top savings."""
        from quor.tracking.db import InvocationRecord, TrackingDB

        db_path = tmp_path / "data" / "quor.db"
        db = TrackingDB(db_path=db_path)
        db.record(
            InvocationRecord(
                command="cat file.txt",
                project_path=tmp_path.as_posix(),
                original_tokens=50,
                final_tokens=50,  # no reduction at all
                filter_name="cat",
                was_passthrough=False,
                duration_ms=1.0,
            )
        )
        db.flush()
        db.close()

        with patch("platformdirs.user_data_dir", return_value=str(tmp_path / "data")):
            result = runner.invoke(app, ["gain", "--project", str(tmp_path), "--days", "30"])

        assert result.exit_code == 0
        assert "Top savings" not in result.output

    def test_negative_net_shown_as_net_not_saved(self, tmp_path: Path) -> None:
        """QB-017: a net-negative invocation (tee footer overhead exceeds
        genuine compression on already-small output) must not be presented
        as "YOU SAVED" in celebratory green — it's not a bug, but it must
        not look like one either."""
        from quor.tracking.db import InvocationRecord, TrackingDB

        db_path = tmp_path / "data" / "quor.db"
        db = TrackingDB(db_path=db_path)
        db.record(
            InvocationRecord(
                command="git rev-parse HEAD",
                project_path=tmp_path.as_posix(),
                original_tokens=21,
                final_tokens=43,  # tee footer pushed this above the original
                filter_name="git-status",
                was_passthrough=False,
                duration_ms=1.0,
            )
        )
        db.flush()
        db.close()

        with patch("platformdirs.user_data_dir", return_value=str(tmp_path / "data")):
            result = runner.invoke(app, ["gain", "--project", str(tmp_path), "--days", "30"])

        assert result.exit_code == 0
        assert "NET TOKENS" in result.output
        assert "YOU SAVED" not in result.output
        assert "does not mean compression failed" in result.output

    def test_all_positive_hides_compression_breakdown(self, tmp_path: Path) -> None:
        """QB-017 gain hardening: when nothing grew, the breakdown section
        (and its explainer paragraph) must not appear at all — only the
        exception gets explained, not the common case."""
        from quor.tracking.db import InvocationRecord, TrackingDB

        db_path = tmp_path / "data" / "quor.db"
        db = TrackingDB(db_path=db_path)
        db.record(
            InvocationRecord(
                command="git status",
                project_path=tmp_path.as_posix(),
                original_tokens=100,
                final_tokens=20,
                filter_name="git-status",
                was_passthrough=False,
                duration_ms=5.0,
            )
        )
        db.flush()
        db.close()

        with patch("platformdirs.user_data_dir", return_value=str(tmp_path / "data")):
            result = runner.invoke(app, ["gain", "--project", str(tmp_path), "--days", "30"])

        assert result.exit_code == 0
        assert "Compression achieved" not in result.output
        assert "Recovery-footer overhead" not in result.output
        assert "had output grow instead of shrink" not in result.output

    def test_mixed_rows_shows_compression_breakdown_with_correct_values(
        self, tmp_path: Path
    ) -> None:
        """A window with both a genuinely-compressed row and a genuinely-grew
        row must show the breakdown, with gross_savings/gross_overhead
        matching the underlying per-row math exactly (not the net figure)."""
        from quor.tracking.db import InvocationRecord, TrackingDB

        db_path = tmp_path / "data" / "quor.db"
        db = TrackingDB(db_path=db_path)
        db.record(
            InvocationRecord(
                command="pytest",
                project_path=tmp_path.as_posix(),
                original_tokens=1000,
                final_tokens=200,  # -800, genuine compression
                filter_name="pytest",
                was_passthrough=False,
                duration_ms=5.0,
            )
        )
        db.record(
            InvocationRecord(
                command="git rev-parse HEAD",
                project_path=tmp_path.as_posix(),
                original_tokens=21,
                final_tokens=43,  # +22, tee overhead
                filter_name="git-status",
                was_passthrough=False,
                duration_ms=1.0,
            )
        )
        db.flush()
        db.close()

        with patch("platformdirs.user_data_dir", return_value=str(tmp_path / "data")):
            result = runner.invoke(app, ["gain", "--project", str(tmp_path), "--days", "30"])

        assert result.exit_code == 0
        assert "Compression achieved" in result.output
        assert "~800 tokens" in result.output   # gross_savings, not net
        assert "Recovery-footer overhead" in result.output
        assert "~22 tokens" in result.output    # gross_overhead
        assert "YOU SAVED" in result.output     # net (800 - 22 = 778) is still positive
        assert "1 of 2 commands (50%) had output grow instead of shrink" in result.output
        # Overall net is still positive -> reassurance, not the tee=false lever.
        assert "doesn't affect the other commands" in result.output
        assert "tee = false" not in result.output

    def test_negative_overall_net_mentions_tee_false_lever(self, tmp_path: Path) -> None:
        """When the *whole window's* net is negative (not just one row), the
        explainer should offer the real, existing per-filter opt-out
        (`tee = false`) rather than just reassuring — there's genuinely
        something the user could do if they cared."""
        from quor.tracking.db import InvocationRecord, TrackingDB

        db_path = tmp_path / "data" / "quor.db"
        db = TrackingDB(db_path=db_path)
        db.record(
            InvocationRecord(
                command="git rev-parse HEAD",
                project_path=tmp_path.as_posix(),
                original_tokens=21,
                final_tokens=43,
                filter_name="git-status",
                was_passthrough=False,
                duration_ms=1.0,
            )
        )
        db.record(
            InvocationRecord(
                command="git rev-parse --short HEAD",
                project_path=tmp_path.as_posix(),
                original_tokens=15,
                final_tokens=40,
                filter_name="git-status",
                was_passthrough=False,
                duration_ms=1.0,
            )
        )
        db.flush()
        db.close()

        with patch("platformdirs.user_data_dir", return_value=str(tmp_path / "data")):
            result = runner.invoke(app, ["gain", "--project", str(tmp_path), "--days", "30"])

        assert result.exit_code == 0
        assert "NET TOKENS" in result.output
        assert "tee = false" in result.output
        assert "doesn't affect the other commands" not in result.output

    def test_top_savings_percentage_uses_gross_not_net(self, tmp_path: Path) -> None:
        """Top savings percentages must be of gross_savings, not the net
        figure — otherwise a filter that genuinely saved 800 tokens would
        show a distorted (or, if net were smaller than any single filter's
        contribution, an impossible >100%) percentage just because an
        unrelated row elsewhere had overhead."""
        from quor.tracking.db import InvocationRecord, TrackingDB

        db_path = tmp_path / "data" / "quor.db"
        db = TrackingDB(db_path=db_path)
        db.record(
            InvocationRecord(
                command="pytest",
                project_path=tmp_path.as_posix(),
                original_tokens=1000,
                final_tokens=200,  # -800
                filter_name="pytest",
                was_passthrough=False,
                duration_ms=5.0,
            )
        )
        db.record(
            InvocationRecord(
                command="git rev-parse HEAD",
                project_path=tmp_path.as_posix(),
                original_tokens=21,
                final_tokens=43,  # +22 overhead; net = 778
                filter_name="git-status",
                was_passthrough=False,
                duration_ms=1.0,
            )
        )
        db.flush()
        db.close()

        with patch("platformdirs.user_data_dir", return_value=str(tmp_path / "data")):
            result = runner.invoke(app, ["gain", "--project", str(tmp_path), "--days", "30"])

        assert result.exit_code == 0
        # pytest is the only row in top_filters with positive savings (800);
        # against gross_savings (800) that's 100%, not 800/778 (~103%, the
        # nonsensical figure the old net-based denominator would produce).
        assert "pytest" in result.output
        assert "(100%)" in result.output

    def test_no_read_hook_activity_notice_shown_for_bash_only_window(
        self, tmp_path: Path
    ) -> None:
        """The dedicated notice, verbatim, when read_hook_invocations
        == 0 for an otherwise-populated report — mentions the specific
        Read-only feature families and the exact fix command."""
        from quor.tracking.db import InvocationRecord, TrackingDB

        db_path = tmp_path / "data" / "quor.db"
        db = TrackingDB(db_path=db_path)
        db.record(
            InvocationRecord(
                command="git status",
                project_path=tmp_path.as_posix(),
                original_tokens=100,
                final_tokens=20,
                filter_name="git-status",
                was_passthrough=False,
                duration_ms=5.0,
            )
        )
        db.flush()
        db.close()

        with patch("platformdirs.user_data_dir", return_value=str(tmp_path / "data")):
            result = runner.invoke(app, ["gain", "--project", str(tmp_path), "--days", "30"])

        assert result.exit_code == 0
        output = result.output
        assert "No Read-hook activity has been recorded in this window" in output
        assert "Markdown/plain-text compression" in output
        assert "DOCX/PDF extraction" in output
        assert "AST summarization" in output
        assert "`quor init --claude`" in output

    def test_no_read_hook_notice_absent_when_read_rows_present(self, tmp_path: Path) -> None:
        """The inverse: as soon as at least one Read row exists, the notice
        must not appear, regardless of how many Bash rows are also present."""
        from quor.tracking.db import InvocationRecord, TrackingDB

        db_path = tmp_path / "data" / "quor.db"
        db = TrackingDB(db_path=db_path)
        db.record(
            InvocationRecord(
                command="git status",
                project_path=tmp_path.as_posix(),
                original_tokens=100,
                final_tokens=20,
                filter_name="git-status",
                was_passthrough=False,
                duration_ms=5.0,
            )
        )
        db.record(
            InvocationRecord(
                command="Read: app.py",
                project_path=tmp_path.as_posix(),
                original_tokens=400,
                final_tokens=250,
                filter_name="cat-python",
                was_passthrough=False,
                duration_ms=3.0,
            )
        )
        db.flush()
        db.close()

        with patch("platformdirs.user_data_dir", return_value=str(tmp_path / "data")):
            result = runner.invoke(app, ["gain", "--project", str(tmp_path), "--days", "30"])

        assert result.exit_code == 0
        assert "No Read-hook activity has been recorded" not in result.output

    def test_mode_optimize_shown_without_clarification(self, tmp_path: Path) -> None:
        """The common/default-expectation mode gets no extra caveat text —
        only non-"optimize" values are annotated."""
        from quor.config.model import QuorUserConfig
        from quor.tracking.db import InvocationRecord, TrackingDB

        db_path = tmp_path / "data" / "quor.db"
        db = TrackingDB(db_path=db_path)
        db.record(
            InvocationRecord(
                command="git status",
                project_path=tmp_path.as_posix(),
                original_tokens=100,
                final_tokens=20,
                filter_name="git-status",
                was_passthrough=False,
                duration_ms=5.0,
            )
        )
        db.flush()
        db.close()

        with (
            patch("platformdirs.user_data_dir", return_value=str(tmp_path / "data")),
            patch(
                "quor.cli.commands.gain.load_user_config",
                return_value=QuorUserConfig(mode="optimize"),
            ),
        ):
            result = runner.invoke(app, ["gain", "--project", str(tmp_path), "--days", "30"])

        assert result.exit_code == 0
        assert "Mode: optimize" in result.output
        assert "affects third-party plugins only" not in result.output

    def test_mode_audit_gets_plugin_only_clarification(self, tmp_path: Path) -> None:
        """The "Mode: audit" line sitting directly above compression stats
        must not read as though it qualifies them — it doesn't, since only
        third-party plugins ever read this value."""
        from quor.config.model import QuorUserConfig
        from quor.tracking.db import InvocationRecord, TrackingDB

        db_path = tmp_path / "data" / "quor.db"
        db = TrackingDB(db_path=db_path)
        db.record(
            InvocationRecord(
                command="git status",
                project_path=tmp_path.as_posix(),
                original_tokens=100,
                final_tokens=20,
                filter_name="git-status",
                was_passthrough=False,
                duration_ms=5.0,
            )
        )
        db.flush()
        db.close()

        with (
            patch("platformdirs.user_data_dir", return_value=str(tmp_path / "data")),
            patch(
                "quor.cli.commands.gain.load_user_config",
                return_value=QuorUserConfig(mode="audit"),
            ),
        ):
            result = runner.invoke(app, ["gain", "--project", str(tmp_path), "--days", "30"])

        assert result.exit_code == 0
        assert "Mode: audit" in result.output
        assert "affects third-party plugins only" in result.output
        assert "always reflect real, applied compression regardless of mode" in result.output

    def test_headline_precedes_stats_table(self, tmp_path: Path) -> None:
        """QB-037 dashboard redesign: the savings headline is the most
        important number and must appear before the secondary stats table,
        not after it — a plain positional check that the reorder actually
        happened, not just that both pieces of text exist somewhere."""
        from quor.tracking.db import InvocationRecord, TrackingDB

        db_path = tmp_path / "data" / "quor.db"
        db = TrackingDB(db_path=db_path)
        db.record(
            InvocationRecord(
                command="git status",
                project_path=tmp_path.as_posix(),
                original_tokens=100,
                final_tokens=20,
                filter_name="git-status",
                was_passthrough=False,
                duration_ms=5.0,
            )
        )
        db.flush()
        db.close()

        with patch("platformdirs.user_data_dir", return_value=str(tmp_path / "data")):
            result = runner.invoke(app, ["gain", "--project", str(tmp_path), "--days", "30"])

        assert result.exit_code == 0
        assert result.output.index("YOU SAVED") < result.output.index("Commands processed")

    def test_notices_grouped_under_one_notice_header_before_stats(self, tmp_path: Path) -> None:
        """When both notice conditions apply (no Read-hook activity, and a
        negative-net window), they must appear together under one NOTICE
        block, and that block must appear before the headline — notices are
        never interleaved with statistics (QB-037)."""
        from quor.tracking.db import InvocationRecord, TrackingDB

        db_path = tmp_path / "data" / "quor.db"
        db = TrackingDB(db_path=db_path)
        db.record(
            InvocationRecord(
                command="git rev-parse HEAD",
                project_path=tmp_path.as_posix(),
                original_tokens=21,
                final_tokens=43,
                filter_name="git-status",
                was_passthrough=False,
                duration_ms=1.0,
            )
        )
        db.flush()
        db.close()

        with patch("platformdirs.user_data_dir", return_value=str(tmp_path / "data")):
            result = runner.invoke(app, ["gain", "--project", str(tmp_path), "--days", "30"])

        assert result.exit_code == 0
        output = result.output
        assert output.count("NOTICE") == 1
        assert "No Read-hook activity has been recorded in this window" in output
        assert "had output grow instead of shrink" in output
        notice_pos = output.index("NOTICE")
        headline_pos = output.index("NET TOKENS")
        assert notice_pos < headline_pos

    def test_no_notice_header_when_nothing_to_report(self, tmp_path: Path) -> None:
        """A window with Read-hook activity and no negative rows has nothing
        for the notices zone to say — the NOTICE header itself must not
        appear just because the zone exists."""
        from quor.tracking.db import InvocationRecord, TrackingDB

        db_path = tmp_path / "data" / "quor.db"
        db = TrackingDB(db_path=db_path)
        db.record(
            InvocationRecord(
                command="Read: app.py",
                project_path=tmp_path.as_posix(),
                original_tokens=400,
                final_tokens=250,
                filter_name="cat-python",
                was_passthrough=False,
                duration_ms=3.0,
            )
        )
        db.flush()
        db.close()

        with patch("platformdirs.user_data_dir", return_value=str(tmp_path / "data")):
            result = runner.invoke(app, ["gain", "--project", str(tmp_path), "--days", "30"])

        assert result.exit_code == 0
        assert "NOTICE" not in result.output


# ---------------------------------------------------------------------------
# quor verify
# ---------------------------------------------------------------------------


class TestVerify:
    def test_all_builtin_filters_pass(self) -> None:
        result = runner.invoke(app, ["verify"])
        assert result.exit_code == 0
        assert "failure(s)" in result.output
        assert "0 failure" in result.output.replace("\n", " ")

    def test_missing_ast_extra_skips_not_fails(self) -> None:
        """Regression test (QB-038): with the optional quor[javascript]
        extra simulated as absent, `quor verify` against the *real* builtin
        filter set must still exit 0 — the cat-javascript/cat-typescript/
        cat-tsx tests that need tree-sitter are skipped, not failed. This is
        the exact scenario a plain `pip install quor` hits, exercised
        end to end through the real CLI command and the real registry, not
        a mock."""
        with patch("quor.filters.registry.is_language_available", return_value=False):
            result = runner.invoke(app, ["verify"])
        assert result.exit_code == 0
        assert "0 failure" in result.output.replace("\n", " ")
        assert "skipped" in result.output
        # Regression guard: Rich interprets "[...]" in an un-escaped string
        # as a markup style tag and silently drops it — this line must show
        # the real, literal, copy-pasteable command, not "quor" with the
        # extras name eaten.
        assert 'pip install "quor[javascript]"' in result.output.replace("\n", " ")

    def test_failure_exits_1(self) -> None:
        from quor.filters.registry import TestRunResult

        with patch("quor.cli.commands.verify.FilterRegistry") as mock_reg:
            inst = MagicMock()
            mock_reg.return_value = inst
            fake_filter = MagicMock()
            fake_filter.name = "broken"
            fake_filter.tests = [MagicMock()]
            inst.all_filters.return_value = [("builtin", fake_filter)]
            inst.run_tests.return_value = TestRunResult(
                failures=["[broken] test 1: 'x' — must_contain 'y' not found"],
                skipped=[],
            )
            result = runner.invoke(app, ["verify"])
        assert result.exit_code == ExitCode.GENERAL_ERROR

    def test_filters_without_tests_warns(self) -> None:
        with patch("quor.cli.commands.verify.FilterRegistry") as mock_reg:
            inst = MagicMock()
            mock_reg.return_value = inst
            fake_filter = MagicMock()
            fake_filter.name = "untested"
            fake_filter.tests = []
            inst.all_filters.return_value = [("builtin", fake_filter)]
            result = runner.invoke(app, ["verify"])
        assert result.exit_code == 0
        assert "no inline tests" in result.output


# ---------------------------------------------------------------------------
# quor doctor
# ---------------------------------------------------------------------------


class TestDoctor:
    def test_clean_install_hook_missing_exits_1(self) -> None:
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == ExitCode.GENERAL_ERROR
        assert "hook script installed" in result.output

    def test_dependency_missing_reported(self) -> None:
        with patch("quor.cli.commands.doctor.importlib.import_module", side_effect=ImportError("nope")):
            result = runner.invoke(app, ["doctor"])
        assert result.exit_code == ExitCode.GENERAL_ERROR

    def test_all_green_after_hook_installed(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        _install_real_hooks(tmp_path, settings_path)
        with patch("platformdirs.user_data_dir", return_value=str(tmp_path)):
            result = runner.invoke(app, ["doctor", "--settings-path", str(settings_path)])
        assert result.exit_code == 0
        assert "✓ Bash hook script installed" in result.output
        assert "✓ Bash hook registered in settings.json" in result.output
        assert "✓ Bash hook up to date" in result.output

    def test_missing_ast_extra_reported_as_skip_not_failure(self, tmp_path: Path) -> None:
        """Regression test (QB-038): quor doctor must report "Built-in
        filter tests pass" as healthy (green) when the only reason any
        inline tests didn't run is a missing *optional* AST dependency —
        the pre-fix behavior treated this as 18 inline test failures and
        marked doctor unhealthy for a completely normal, expected install
        state (a plain `pip install quor`, no extras)."""
        settings_path = tmp_path / "settings.json"
        _install_real_hooks(tmp_path, settings_path)
        with (
            patch("platformdirs.user_data_dir", return_value=str(tmp_path)),
            patch("quor.filters.registry.is_language_available", return_value=False),
        ):
            result = runner.invoke(app, ["doctor", "--settings-path", str(settings_path)])
        assert result.exit_code == 0
        assert "✓ Built-in filter tests pass" in result.output
        assert "skipped" in result.output
        # Regression guard: Rich interprets "[...]" in an un-escaped string
        # as a markup style tag and silently drops it — this detail must
        # show the real extras name, not "(quor)" with "[javascript]" eaten.
        assert "(quor[javascript])" in result.output.replace("\n", " ")

    def test_tee_enabled_by_default(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        _install_real_hooks(tmp_path, settings_path)
        with patch("platformdirs.user_data_dir", return_value=str(tmp_path)):
            result = runner.invoke(app, ["doctor", "--settings-path", str(settings_path)])
        assert result.exit_code == 0
        assert "Tee: enabled" in result.output

    def test_tee_disabled_after_two_consecutive_failures(self, tmp_path: Path) -> None:
        from quor.pipeline.tee import record_tee_failure

        settings_path = tmp_path / "settings.json"
        _install_real_hooks(tmp_path, settings_path)

        with patch("platformdirs.user_data_dir", return_value=str(tmp_path)):
            record_tee_failure("PermissionError: Access is denied")
            record_tee_failure("PermissionError: Access is denied")
            result = runner.invoke(app, ["doctor", "--settings-path", str(settings_path)])

        assert "Tee: disabled (filesystem unavailable)" in result.output
        assert "quor doctor --reset-tee" in result.output
        # tee being adaptively disabled is a real problem -> doctor exits non-zero
        assert result.exit_code == ExitCode.GENERAL_ERROR

    def test_reset_tee_flag_clears_disabled_state(self, tmp_path: Path) -> None:
        from quor.pipeline.tee import get_tee_status, record_tee_failure

        settings_path = tmp_path / "settings.json"
        _install_real_hooks(tmp_path, settings_path)

        with patch("platformdirs.user_data_dir", return_value=str(tmp_path)):
            record_tee_failure("x")
            record_tee_failure("x")
            assert get_tee_status().disabled is True

            result = runner.invoke(
                app, ["doctor", "--settings-path", str(settings_path), "--reset-tee"]
            )

            assert "Tee adaptive-disable state cleared" in result.output
            assert "Tee: enabled" in result.output
            assert get_tee_status().disabled is False

    def test_plugin_diagnostics_include_version(self, tmp_path: Path) -> None:
        """quor doctor lists discovered plugins with their declared version."""
        from quor.pipeline.plugin_loader import PluginInfo, PluginLoadReport

        settings_path = tmp_path / "settings.json"
        _install_real_hooks(tmp_path, settings_path)

        fake_report = PluginLoadReport(
            plugins=[
                PluginInfo(
                    entry_point_name="com.test.versioned",
                    module_path="some.module",
                    class_name="SomePlugin",
                    plugin_id="com.test.versioned",
                    version="2.3.1",
                    api_version=1,
                )
            ],
        )
        with (
            patch("platformdirs.user_data_dir", return_value=str(tmp_path)),
            patch("quor.pipeline.plugin_loader.get_load_report", return_value=fake_report),
        ):
            result = runner.invoke(app, ["doctor", "--settings-path", str(settings_path)])
        assert "com.test.versioned@2.3.1" in result.output


# ---------------------------------------------------------------------------
# quor doctor — PostToolUse/Read hook capability checks (QB-007A)
# ---------------------------------------------------------------------------


class TestReadHookDoctorChecks:
    def _write_both_hook_scripts(self, tmp_path: Path) -> None:
        hook_path = tmp_path / "hooks" / "claude-hook.ps1"
        hook_path.parent.mkdir(parents=True, exist_ok=True)
        hook_path.write_text("dummy", encoding="utf-8")
        read_hook_path = tmp_path / "hooks" / "claude-hook-read.ps1"
        read_hook_path.write_text("dummy", encoding="utf-8")

    def test_read_hook_script_missing_fails(self, tmp_path: Path) -> None:
        """Only the Bash hook script is written — the Read hook script check
        must independently flag itself as missing (not silently pass)."""
        hook_path = tmp_path / "hooks" / "claude-hook.ps1"
        hook_path.parent.mkdir(parents=True, exist_ok=True)
        hook_path.write_text("dummy", encoding="utf-8")
        settings_path = tmp_path / "settings.json"
        with patch("platformdirs.user_data_dir", return_value=str(tmp_path)):
            result = runner.invoke(app, ["doctor", "--settings-path", str(settings_path)])
        assert result.exit_code == ExitCode.GENERAL_ERROR
        assert "✗ Read hook script installed" in result.output
        assert "quor init --claude" in result.output

    def test_read_hook_script_present_passes(self, tmp_path: Path) -> None:
        self._write_both_hook_scripts(tmp_path)
        settings_path = tmp_path / "settings.json"
        with patch("platformdirs.user_data_dir", return_value=str(tmp_path)):
            result = runner.invoke(app, ["doctor", "--settings-path", str(settings_path)])
        assert "✓ Read hook script installed" in result.output

    def test_read_hook_roundtrip_passes_when_compression_actually_fires(
        self, tmp_path: Path
    ) -> None:
        """QB-007C: the roundtrip check exercises real compression (an
        oversized synthetic Markdown document) and must observe
        updatedToolOutput actually being produced and smaller than the
        input — not merely that the hook responds with valid JSON."""
        settings_path = tmp_path / "settings.json"
        _install_real_hooks(tmp_path, settings_path)
        with patch("platformdirs.user_data_dir", return_value=str(tmp_path)):
            result = runner.invoke(app, ["doctor", "--settings-path", str(settings_path)])
        assert result.exit_code == 0
        assert "✓ Read hook responds correctly" in result.output

    def test_read_hook_roundtrip_detects_wrong_hook_event_name(self, tmp_path: Path) -> None:
        """If a future regression changes hookEventName away from
        "PostToolUse", the capability check must catch it, not silently pass."""
        self._write_both_hook_scripts(tmp_path)
        settings_path = tmp_path / "settings.json"
        with (
            patch("platformdirs.user_data_dir", return_value=str(tmp_path)),
            patch(
                "quor.cli.commands.doctor._check_read_hook_roundtrip",
                return_value=("Read hook responds correctly", False, "unexpected hookEventName: 'Bogus'"),
            ),
        ):
            result = runner.invoke(app, ["doctor", "--settings-path", str(settings_path)])
        assert result.exit_code == ExitCode.GENERAL_ERROR
        assert "✗ Read hook responds correctly" in result.output
        assert "unexpected hookEventName" in result.output


# ---------------------------------------------------------------------------
# quor doctor — hook configuration health: manifest-driven registered/
# up-to-date checks (QB-037). "Script exists" (above) is necessary but not
# sufficient — these checks close the gap between "a hook script happens to
# be on disk" and "Claude Code's settings.json actually points at it, and it
# matches what this version of Quor would generate today."
# ---------------------------------------------------------------------------


class TestHookConfigHealth:
    def test_script_exists_but_not_registered_fails(self, tmp_path: Path) -> None:
        """A script can be left over from a previous install (or copied by
        hand) without settings.json referencing it at all — that must not
        read as a healthy install."""
        from quor.adapters.hook_manifest import HOOK_SPECS, render_hook_script

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        for spec in HOOK_SPECS:
            (hooks_dir / spec.script_name).write_text(
                render_hook_script(spec, python=sys.executable), encoding="utf-8"
            )
        settings_path = tmp_path / "settings.json"
        settings_path.write_text("{}", encoding="utf-8")  # exists, but registers nothing

        with patch("platformdirs.user_data_dir", return_value=str(tmp_path)):
            result = runner.invoke(app, ["doctor", "--settings-path", str(settings_path)])
        assert result.exit_code == ExitCode.GENERAL_ERROR
        assert "✗ Bash hook registered in settings.json" in result.output
        assert "✗ Read hook registered in settings.json" in result.output
        assert "quor init --claude" in result.output

    def test_registered_and_up_to_date_when_freshly_installed(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        _install_real_hooks(tmp_path, settings_path)
        with patch("platformdirs.user_data_dir", return_value=str(tmp_path)):
            result = runner.invoke(app, ["doctor", "--settings-path", str(settings_path)])
        assert "✓ Bash hook registered in settings.json" in result.output
        assert "✓ Read hook registered in settings.json" in result.output
        assert "✓ Bash hook up to date" in result.output
        assert "✓ Read hook up to date" in result.output

    def test_outdated_hook_script_fails_up_to_date_check(self, tmp_path: Path) -> None:
        """A script whose embedded schema differs from the hook's current
        `schema_version` (e.g. left over from before a template change)
        must be flagged as outdated, not silently treated as current just
        because it exists and is registered."""
        import re

        settings_path = tmp_path / "settings.json"
        _install_real_hooks(tmp_path, settings_path)
        content = (tmp_path / "hooks" / "claude-hook.ps1").read_text(encoding="utf-8")
        stale_content = re.sub(r"# quor-hook-schema: .+", "# quor-hook-schema: 0", content)
        (tmp_path / "hooks" / "claude-hook.ps1").write_text(stale_content, encoding="utf-8")

        with patch("platformdirs.user_data_dir", return_value=str(tmp_path)):
            result = runner.invoke(app, ["doctor", "--settings-path", str(settings_path)])
        assert result.exit_code == ExitCode.GENERAL_ERROR
        assert "✗ Bash hook up to date" in result.output
        assert "installed hook schema is 0" in result.output
        assert "quor init --claude" in result.output

    def test_up_to_date_check_is_unaffected_by_package_version(self, tmp_path: Path) -> None:
        """QB-037 correction: bumping quor.__version__ alone must never flag
        an installed hook as outdated — only a real schema_version change
        (in hook_manifest.py) should. A freshly-installed hook must still
        read as up to date under a different running package version."""
        settings_path = tmp_path / "settings.json"
        _install_real_hooks(tmp_path, settings_path)

        with (
            patch("platformdirs.user_data_dir", return_value=str(tmp_path)),
            patch("quor.__version__", "999.999.999"),
        ):
            result = runner.invoke(app, ["doctor", "--settings-path", str(settings_path)])
        assert "✓ Bash hook up to date" in result.output
        assert "✓ Read hook up to date" in result.output

    def test_hook_with_no_schema_marker_treated_as_outdated(self, tmp_path: Path) -> None:
        """A hook installed before this check existed has no
        `# quor-hook-schema:` line at all — must read as outdated
        (actionable), not error."""
        settings_path = tmp_path / "settings.json"
        _install_real_hooks(tmp_path, settings_path)
        import re

        content = (tmp_path / "hooks" / "claude-hook.ps1").read_text(encoding="utf-8")
        content = re.sub(r"# quor-hook-schema: .+\n", "", content)
        (tmp_path / "hooks" / "claude-hook.ps1").write_text(content, encoding="utf-8")

        with patch("platformdirs.user_data_dir", return_value=str(tmp_path)):
            result = runner.invoke(app, ["doctor", "--settings-path", str(settings_path)])
        assert result.exit_code == ExitCode.GENERAL_ERROR
        assert "✗ Bash hook up to date" in result.output
        assert "no schema marker" in result.output


# ---------------------------------------------------------------------------
# quor init --claude
# ---------------------------------------------------------------------------


class TestInit:
    @pytest.fixture(autouse=True)
    def _fast_execution_policy_check(self) -> Iterator[None]:
        """Every `init --claude` call runs `_warn_if_execution_policy_restricted()`,
        which spawns a real PowerShell process (~1-1.5s cold start) —
        completely incidental to what these tests actually verify (hook
        collision, atomic writes, dry-run output). QB-030: this was the
        single biggest contributor to the default suite's wall-clock time.
        Mocked here; the real subprocess call has its own dedicated test,
        test_execution_policy_check below, which does not use this fixture."""
        proc = MagicMock(spec=subprocess.CompletedProcess)
        proc.returncode = 0
        proc.stdout = "RemoteSigned"
        with patch("quor.cli.commands.init.subprocess.run", return_value=proc):
            yield

    def test_no_claude_flag_noop(self) -> None:
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert "Nothing to do" in result.output

    def test_dry_run_and_atomic_write(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        result = runner.invoke(
            app, ["init", "--claude", "--yes", "--settings-path", str(settings_path)]
        )
        assert result.exit_code == 0
        assert "Dry run" in result.output
        assert settings_path.exists()

        data = orjson.loads(settings_path.read_bytes())
        commands = [
            h["command"]
            for entry in data["hooks"]["PreToolUse"]
            for h in entry["hooks"]
        ]
        assert any("claude-hook.ps1" in c for c in commands)

    def test_confirmation_declined_aborts(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        result = runner.invoke(
            app, ["init", "--claude", "--settings-path", str(settings_path)], input="n\n"
        )
        assert result.exit_code == ExitCode.GENERAL_ERROR
        assert not settings_path.exists()

    def test_existing_hook_overwritten_not_duplicated(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        runner.invoke(app, ["init", "--claude", "--yes", "--settings-path", str(settings_path)])
        result = runner.invoke(
            app, ["init", "--claude", "--yes", "--settings-path", str(settings_path)]
        )
        assert result.exit_code == 0
        assert "already registered" in result.output

        data = orjson.loads(settings_path.read_bytes())
        pre_tool_use = data["hooks"]["PreToolUse"]
        quor_entries = [
            entry
            for entry in pre_tool_use
            for h in entry["hooks"]
            if "claude-hook.ps1" in h["command"]
        ]
        assert len(quor_entries) == 1

    def test_hook_script_embeds_sys_executable(self, tmp_path: Path) -> None:
        import sys

        settings_path = tmp_path / "settings.json"
        with patch("platformdirs.user_data_dir", return_value=str(tmp_path / "data")):
            result = runner.invoke(
                app, ["init", "--claude", "--yes", "--settings-path", str(settings_path)]
            )
        assert result.exit_code == 0
        hook_path = tmp_path / "data" / "hooks" / "claude-hook.ps1"
        content = hook_path.read_text(encoding="utf-8")
        assert sys.executable in content
        assert "quor hook claude" in content

    def test_does_not_print_reset_tee_message(self, tmp_path: Path) -> None:
        """Regression test (QB-037): `init --claude` runs `doctor` internally
        via `_run_doctor()`, not the Typer-decorated `doctor()` — calling the
        latter directly left `reset_tee` holding the unresolved
        `typer.Option(...)` sentinel (truthy), so this message printed on
        every `init --claude`, even though `--reset-tee` was never passed.
        Fails on the pre-fix code (`init.py` calling `doctor()` directly),
        passes on the fix."""
        settings_path = tmp_path / "settings.json"
        with patch("platformdirs.user_data_dir", return_value=str(tmp_path / "data")):
            result = runner.invoke(
                app, ["init", "--claude", "--yes", "--settings-path", str(settings_path)]
            )
        assert result.exit_code == 0
        assert "Tee adaptive-disable state cleared" not in result.output


# ---------------------------------------------------------------------------
# quor init --claude — PostToolUse/Read hook registration (QB-007A)
# ---------------------------------------------------------------------------


class TestReadHookRegistration:
    @pytest.fixture(autouse=True)
    def _fast_execution_policy_check(self) -> Iterator[None]:
        """See TestInit's identical fixture — same rationale (QB-030)."""
        proc = MagicMock(spec=subprocess.CompletedProcess)
        proc.returncode = 0
        proc.stdout = "RemoteSigned"
        with patch("quor.cli.commands.init.subprocess.run", return_value=proc):
            yield

    def test_read_hook_registered_under_post_tool_use_read_matcher(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        result = runner.invoke(
            app, ["init", "--claude", "--yes", "--settings-path", str(settings_path)]
        )
        assert result.exit_code == 0

        data = orjson.loads(settings_path.read_bytes())
        post_tool_use = data["hooks"]["PostToolUse"]
        matching_entries = [
            entry
            for entry in post_tool_use
            if any("claude-hook-read.ps1" in h["command"] for h in entry["hooks"])
        ]
        assert len(matching_entries) == 1
        assert matching_entries[0]["matcher"] == "Read"

    def test_bash_hook_untouched_by_read_hook_registration(self, tmp_path: Path) -> None:
        """Installing the Read hook must not disturb the existing PreToolUse/
        Bash registration, and must not itself leak into PreToolUse — the
        two are independent, additive writes to separate settings.json keys."""
        settings_path = tmp_path / "settings.json"
        result = runner.invoke(
            app, ["init", "--claude", "--yes", "--settings-path", str(settings_path)]
        )
        assert result.exit_code == 0

        data = orjson.loads(settings_path.read_bytes())
        pre_tool_use_commands = [
            h["command"] for entry in data["hooks"]["PreToolUse"] for h in entry["hooks"]
        ]
        assert any("claude-hook.ps1" in c for c in pre_tool_use_commands)
        assert not any("claude-hook-read.ps1" in c for c in pre_tool_use_commands)

    def test_read_hook_script_embeds_sys_executable(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        with patch("platformdirs.user_data_dir", return_value=str(tmp_path / "data")):
            result = runner.invoke(
                app, ["init", "--claude", "--yes", "--settings-path", str(settings_path)]
            )
        assert result.exit_code == 0
        read_hook_path = tmp_path / "data" / "hooks" / "claude-hook-read.ps1"
        content = read_hook_path.read_text(encoding="utf-8")
        assert sys.executable in content
        assert "quor hook claude-read" in content

    def test_reinstall_does_not_duplicate_read_hook_entry(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        runner.invoke(app, ["init", "--claude", "--yes", "--settings-path", str(settings_path)])
        result = runner.invoke(
            app, ["init", "--claude", "--yes", "--settings-path", str(settings_path)]
        )
        assert result.exit_code == 0
        assert "Read hook is already registered" in result.output

        data = orjson.loads(settings_path.read_bytes())
        post_tool_use = data["hooks"]["PostToolUse"]
        matching = [
            entry
            for entry in post_tool_use
            for h in entry["hooks"]
            if "claude-hook-read.ps1" in h["command"]
        ]
        assert len(matching) == 1

    def test_dry_run_mentions_read_hook(self, tmp_path: Path) -> None:
        settings_path = tmp_path / "settings.json"
        result = runner.invoke(
            app, ["init", "--claude", "--yes", "--settings-path", str(settings_path)]
        )
        assert "Read hook script" in result.output


class TestExecutionPolicyCheck:
    """Unit-level coverage of _warn_if_execution_policy_restricted()'s own
    branching logic, with subprocess.run mocked (fast) — deliberately not
    using TestInit's _fast_execution_policy_check fixture above, since this
    class exists specifically to test what that fixture mocks away. The
    real, unmocked PowerShell subprocess call is already exercised end to
    end by tests/integration/test_cli_commands.py::TestInitAndDoctorIntegration
    (marked @pytest.mark.integration, appropriately excluded from the
    default fast suite — this class does not duplicate that coverage)."""

    def test_restricted_policy_warns(self, capsys: pytest.CaptureFixture[str]) -> None:
        from quor.cli.commands.init import _warn_if_execution_policy_restricted

        proc = MagicMock(spec=subprocess.CompletedProcess)
        proc.returncode = 0
        proc.stdout = "Restricted"
        with patch("quor.cli.commands.init.subprocess.run", return_value=proc):
            _warn_if_execution_policy_restricted()

        assert "Restricted" in capsys.readouterr().out

    def test_remote_signed_policy_silent(self, capsys: pytest.CaptureFixture[str]) -> None:
        from quor.cli.commands.init import _warn_if_execution_policy_restricted

        proc = MagicMock(spec=subprocess.CompletedProcess)
        proc.returncode = 0
        proc.stdout = "RemoteSigned"
        with patch("quor.cli.commands.init.subprocess.run", return_value=proc):
            _warn_if_execution_policy_restricted()

        assert capsys.readouterr().out == ""

    def test_nonzero_returncode_silent(self, capsys: pytest.CaptureFixture[str]) -> None:
        """A failed Get-ExecutionPolicy call (e.g. powershell not on PATH on
        this particular machine) must not be misread as "Restricted"."""
        from quor.cli.commands.init import _warn_if_execution_policy_restricted

        proc = MagicMock(spec=subprocess.CompletedProcess)
        proc.returncode = 1
        proc.stdout = ""
        with patch("quor.cli.commands.init.subprocess.run", return_value=proc):
            _warn_if_execution_policy_restricted()

        assert capsys.readouterr().out == ""

    def test_powershell_missing_fails_open(self, capsys: pytest.CaptureFixture[str]) -> None:
        """No powershell on PATH at all (e.g. a non-Windows dev box) must
        not crash `quor init` — fails open, silently."""
        from quor.cli.commands.init import _warn_if_execution_policy_restricted

        with patch(
            "quor.cli.commands.init.subprocess.run",
            side_effect=OSError("not found"),
        ):
            _warn_if_execution_policy_restricted()  # must not raise

        assert capsys.readouterr().out == ""

    def test_timeout_fails_open(self, capsys: pytest.CaptureFixture[str]) -> None:
        from quor.cli.commands.init import _warn_if_execution_policy_restricted

        with patch(
            "quor.cli.commands.init.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="powershell", timeout=5),
        ):
            _warn_if_execution_policy_restricted()  # must not raise

        assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# Hook collision detection (P0 item 3)
# ---------------------------------------------------------------------------


def _settings_with_third_party_hook(cmd: str = "zap hook bash") -> dict[str, Any]:
    """Build a settings dict that simulates another tool's PreToolUse Bash hook."""
    return {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": cmd}]},
            ]
        }
    }


class TestHookCollisionDetection:
    @pytest.fixture(autouse=True)
    def _fast_execution_policy_check(self) -> Iterator[None]:
        """See TestInit's identical fixture above — same rationale, same
        fix (QB-030): every `init --claude` call in this class also pays
        the real PowerShell spawn incidentally."""
        proc = MagicMock(spec=subprocess.CompletedProcess)
        proc.returncode = 0
        proc.stdout = "RemoteSigned"
        with patch("quor.cli.commands.init.subprocess.run", return_value=proc):
            yield

    def test_third_party_hook_triggers_warning(self, tmp_path: Path) -> None:
        """init --claude warns when another Bash hook is already registered."""
        settings_path = tmp_path / "settings.json"
        settings_path.write_bytes(
            orjson.dumps(_settings_with_third_party_hook("zap hook bash"))
        )
        result = runner.invoke(
            app,
            ["init", "--claude", "--yes", "--settings-path", str(settings_path)],
        )
        # init installs alongside the third-party hook (per --yes); the third-party
        # hook is left in place, so the trailing `quor doctor` run correctly reports
        # the still-unresolved collision as a failure.
        assert result.exit_code == ExitCode.GENERAL_ERROR
        assert "Warning" in result.output or "⚠" in result.output

    def test_third_party_hook_names_known_tool(self, tmp_path: Path) -> None:
        """Zap is named explicitly in the collision warning."""
        settings_path = tmp_path / "settings.json"
        settings_path.write_bytes(
            orjson.dumps(_settings_with_third_party_hook("/usr/local/bin/zap hook"))
        )
        result = runner.invoke(
            app,
            ["init", "--claude", "--yes", "--settings-path", str(settings_path)],
        )
        # See test_third_party_hook_triggers_warning: the trailing doctor run
        # correctly flags the still-unresolved third-party hook.
        assert result.exit_code == ExitCode.GENERAL_ERROR
        assert "Zap" in result.output

    def test_unknown_third_party_hook_warns_generically(self, tmp_path: Path) -> None:
        """An unrecognised command still generates a collision warning."""
        settings_path = tmp_path / "settings.json"
        settings_path.write_bytes(
            orjson.dumps(_settings_with_third_party_hook("some-unknown-tool intercept"))
        )
        result = runner.invoke(
            app,
            ["init", "--claude", "--yes", "--settings-path", str(settings_path)],
        )
        # See test_third_party_hook_triggers_warning: the trailing doctor run
        # correctly flags the still-unresolved third-party hook.
        assert result.exit_code == ExitCode.GENERAL_ERROR
        # Warning present but no named tool
        assert "Warning" in result.output or "⚠" in result.output

    def test_no_conflict_when_only_quor_hook_present(self, tmp_path: Path) -> None:
        """After quor init, re-running should NOT detect a conflict with itself."""
        settings_path = tmp_path / "settings.json"
        # First install
        runner.invoke(
            app, ["init", "--claude", "--yes", "--settings-path", str(settings_path)]
        )
        # Second install — Quor's own hook is present; must not collide with itself
        result = runner.invoke(
            app, ["init", "--claude", "--yes", "--settings-path", str(settings_path)]
        )
        assert result.exit_code == 0
        # "already registered" message but no collision warning
        assert "already registered" in result.output

    def test_doctor_reports_collision(self, tmp_path: Path) -> None:
        """quor doctor reports a conflict when another Bash hook is registered."""
        settings_path = tmp_path / "settings.json"
        settings_path.write_bytes(
            orjson.dumps(_settings_with_third_party_hook("zap hook bash"))
        )
        result = runner.invoke(app, ["doctor", "--settings-path", str(settings_path)])
        assert result.exit_code == ExitCode.GENERAL_ERROR
        assert "✗ No conflicting PreToolUse hooks" in result.output
        assert "1 other Bash hook(s) detected" in result.output

    def test_doctor_no_collision_when_settings_missing(self, tmp_path: Path) -> None:
        """doctor passes the collision check when settings.json doesn't exist."""
        settings_path = tmp_path / "settings.json"  # deliberately never created
        result = runner.invoke(app, ["doctor", "--settings-path", str(settings_path)])
        assert "✓ No conflicting PreToolUse hooks" in result.output


# ---------------------------------------------------------------------------
# _find_conflicting_hooks unit tests
# ---------------------------------------------------------------------------


class TestFindConflictingHooks:
    def test_empty_settings_returns_no_conflicts(self) -> None:
        from quor.cli.commands.init import _find_conflicting_hooks

        assert _find_conflicting_hooks({}, bash_script_name="claude-hook.ps1") == []

    def test_quor_hook_not_reported_as_conflict(self) -> None:
        from quor.cli.commands.init import _find_conflicting_hooks

        settings = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": 'powershell -File "C:\\quor\\claude-hook.ps1"',
                            }
                        ],
                    }
                ]
            }
        }
        assert _find_conflicting_hooks(settings, bash_script_name="claude-hook.ps1") == []

    def test_third_party_bash_hook_returned(self) -> None:
        from quor.cli.commands.init import _find_conflicting_hooks

        cmd = "zap hook bash"
        settings = _settings_with_third_party_hook(cmd)
        conflicts = _find_conflicting_hooks(settings, bash_script_name="claude-hook.ps1")
        assert cmd in conflicts

    def test_non_bash_matcher_ignored(self) -> None:
        from quor.cli.commands.init import _find_conflicting_hooks

        settings = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Python",
                        "hooks": [{"type": "command", "command": "some-tool hook"}],
                    }
                ]
            }
        }
        # Non-Bash matcher cannot intercept Bash commands → not a conflict
        assert _find_conflicting_hooks(settings, bash_script_name="claude-hook.ps1") == []


# ---------------------------------------------------------------------------
# Regression: Windows encoding crash (Phase 7 fix)
# ---------------------------------------------------------------------------


class TestWindowsEncodingRegression:
    """Regression tests for the Windows cp1252 UnicodeEncodeError crash.

    Bug: CLI output paths (✓/✗ glyphs) crashed on Windows because text-mode
    stdout/stderr defaulted to cp1252, which cannot encode those characters.
    Fix: _ensure_utf8_stdio() calls stream.reconfigure(encoding='utf-8') once
    in main() before any CLI or dispatch output is written.
    """

    def test_ensure_utf8_stdio_calls_reconfigure(self) -> None:
        from quor.__main__ import _ensure_utf8_stdio

        stdout_mock = MagicMock()
        stderr_mock = MagicMock()

        with patch.object(sys, "stdout", stdout_mock), patch.object(sys, "stderr", stderr_mock):
            _ensure_utf8_stdio()

        stdout_mock.reconfigure.assert_called_once_with(encoding="utf-8")
        stderr_mock.reconfigure.assert_called_once_with(encoding="utf-8")

    def test_ensure_utf8_stdio_suppresses_value_error(self) -> None:
        """ValueError from reconfigure (e.g. BytesIO-backed capture) must be swallowed."""
        from quor.__main__ import _ensure_utf8_stdio

        mock_stream = MagicMock()
        mock_stream.reconfigure.side_effect = ValueError("not a text stream")

        with patch.object(sys, "stdout", mock_stream), patch.object(sys, "stderr", mock_stream):
            _ensure_utf8_stdio()  # must not raise

        mock_stream.reconfigure.assert_called_with(encoding="utf-8")

    def test_ensure_utf8_stdio_suppresses_os_error(self) -> None:
        """OSError from reconfigure must be swallowed (stream may not support it)."""
        from quor.__main__ import _ensure_utf8_stdio

        mock_stream = MagicMock()
        mock_stream.reconfigure.side_effect = OSError("reconfigure failed")

        with patch.object(sys, "stdout", mock_stream), patch.object(sys, "stderr", mock_stream):
            _ensure_utf8_stdio()  # must not raise

    def test_ensure_utf8_stdio_handles_stream_without_reconfigure(self) -> None:
        """Streams that lack reconfigure() are silently skipped (no AttributeError)."""
        from quor.__main__ import _ensure_utf8_stdio

        class _NoReconfigure:
            pass

        stream = _NoReconfigure()
        with patch.object(sys, "stdout", stream), patch.object(sys, "stderr", stream):
            _ensure_utf8_stdio()  # must not raise


# ---------------------------------------------------------------------------
# Codepage / locale sweep (P1 item 6)
# ---------------------------------------------------------------------------


class TestCodepageSweep:
    """_ensure_utf8_stdio must reconfigure regardless of the stream's original codepage.

    Regression against the Windows cp1252 crash: streams that report non-UTF-8
    encodings must be reconfigured to UTF-8 so that ✓/✗ glyphs don't crash the CLI.
    """

    import pytest

    @pytest.mark.parametrize("codepage", ["cp437", "cp1252", "utf-8", "ascii"])
    def test_reconfigure_called_for_any_codepage(self, codepage: str) -> None:
        from quor.__main__ import _ensure_utf8_stdio

        stdout_mock = MagicMock()
        stdout_mock.encoding = codepage
        stderr_mock = MagicMock()
        stderr_mock.encoding = codepage

        with (
            patch.object(sys, "stdout", stdout_mock),
            patch.object(sys, "stderr", stderr_mock),
        ):
            _ensure_utf8_stdio()

        # Both streams must be reconfigured regardless of starting encoding
        stdout_mock.reconfigure.assert_called_once_with(encoding="utf-8")
        stderr_mock.reconfigure.assert_called_once_with(encoding="utf-8")

    def test_cp1252_stream_reconfigure_failure_does_not_crash(self) -> None:
        """If reconfigure raises on a cp1252 stream, the CLI must not crash."""
        from quor.__main__ import _ensure_utf8_stdio

        mock_stream = MagicMock()
        mock_stream.encoding = "cp1252"
        mock_stream.reconfigure.side_effect = ValueError("cannot reconfigure")

        with (
            patch.object(sys, "stdout", mock_stream),
            patch.object(sys, "stderr", mock_stream),
        ):
            _ensure_utf8_stdio()  # must not raise
