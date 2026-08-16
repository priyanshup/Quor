"""Unit tests for quor/cli/commands/: the six V1 CLI commands."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from quor.cli.main import app
from quor.errors import ExitCode

runner = CliRunner()


def _make_proc(stdout: str = "", returncode: int = 0) -> MagicMock:
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.stdout = stdout
    proc.returncode = returncode
    return proc


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


class TestExplainCompressionSummary:
    """QB-057: the deterministic per-stage compression breakdown."""

    def test_compression_summary_shown_with_savings(self) -> None:
        # git-status's strip_lines stage strips both lines here ("On branch"
        # and "nothing to commit" both match its patterns) — a real,
        # non-zero saving to assert on.
        proc = _make_proc(
            stdout="On branch main\nnothing to commit, working tree clean\n"
        )
        with patch("subprocess.run", return_value=proc):
            result = runner.invoke(app, ["explain", "git status"])
        assert result.exit_code == 0
        assert "Compression summary" in result.output
        assert "Strip lines:" in result.output
        assert "Final:" in result.output
        assert "Saved:" in result.output

    def test_zero_saving_stage_omitted_from_summary(self) -> None:
        # deduplicate_consecutive has nothing to dedupe here (no repeated
        # lines survive strip_lines), so it must not appear as a bullet.
        proc = _make_proc(
            stdout="On branch main\nnothing to commit, working tree clean\n"
        )
        with patch("subprocess.run", return_value=proc):
            result = runner.invoke(app, ["explain", "git status"])
        assert "Deduplicate consecutive:" not in result.output

    def test_no_compression_summary_when_nothing_saved(self) -> None:
        # Every line here survives git-status's strip_lines/dedupe stages
        # untouched, so total savings is zero and the whole section is
        # omitted rather than shown as an empty header.
        proc = _make_proc(stdout="modified:   src/main.py\n")
        with patch("subprocess.run", return_value=proc):
            result = runner.invoke(app, ["explain", "git status"])
        assert result.exit_code == 0
        assert "Compression summary" not in result.output
        assert "Final:" in result.output
        assert "Saved: 0 tokens (0.0%)" in result.output

    def test_saved_equals_original_minus_final(self) -> None:
        import re

        proc = _make_proc(
            stdout="On branch main\nnothing to commit, working tree clean\n"
        )
        with patch("subprocess.run", return_value=proc):
            result = runner.invoke(app, ["explain", "git status"])

        original = int(re.search(r"Original: ([\d,]+) tokens", result.output).group(1).replace(",", ""))
        final = int(re.search(r"Final: ([\d,]+) tokens", result.output).group(1).replace(",", ""))
        saved = int(re.search(r"Saved: ([\d,]+) tokens", result.output).group(1).replace(",", ""))
        assert saved == original - final

    def test_output_deterministic_across_runs(self) -> None:
        proc = _make_proc(
            stdout="On branch main\nnothing to commit, working tree clean\n"
        )
        with patch("subprocess.run", return_value=proc):
            first = runner.invoke(app, ["explain", "git status"]).output
            second = runner.invoke(app, ["explain", "git status"]).output
        assert first == second


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
        assert "quor init --mcp" in output

    def test_low_sample_caveat_shown_below_threshold(self, tmp_path: Path) -> None:
        """QB-091: a single command's percentage is presented with a caveat
        rather than the same bare confidence a settled reading gets."""
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

        assert "early read" in result.output
        assert "1 command)" in result.output

    def test_eligible_compression_line_shown_with_passthrough_commands(
        self, tmp_path: Path
    ) -> None:
        """QB-091: when some commands were passthrough (no filter could
        apply), a second line scopes the compression rate to just the
        content that was actually eligible."""
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
                command="ps aux",
                project_path=tmp_path.as_posix(),
                original_tokens=500,
                final_tokens=500,
                filter_name=None,
                was_passthrough=True,
                duration_ms=2.0,
            )
        )
        db.flush()
        db.close()

        with patch("platformdirs.user_data_dir", return_value=str(tmp_path / "data")):
            result = runner.invoke(app, ["gain", "--project", str(tmp_path), "--days", "30"])

        assert "a filter could apply to" in result.output

    def test_no_eligible_compression_line_without_passthrough(self, tmp_path: Path) -> None:
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

        assert "a filter could apply to" not in result.output

    def test_filter_display_name_translates_cat_family(self, tmp_path: Path) -> None:
        """QB-091: `cat-python` (an internal filter id) shows a user-facing
        label in Top savings, not the raw registry name."""
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

        assert "Python file read" in result.output
        assert "cat-python" not in result.output

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
        assert "Recovery footer" in result.output
        assert "tee = false" in result.output

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
        assert "Recovery footer" in result.output
        assert "1 command (50%)" in result.output
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
        assert "`quor init --mcp`" in output

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

    def test_notices_separated_by_relevance_not_grouped(self, tmp_path: Path) -> None:
        """When both notice conditions apply (no Read-hook activity, and a
        negative-net window), they are NOT lumped into one banner: the
        Read-hook gap qualifies every number above, so it leads, before the
        headline; the recovery-footer note is a narrow footnote about a
        subset of rows, so it's demoted to after the stats, same tier as
        the token-estimation note — never a bold "NOTICE" banner competing
        with the headline for attention."""
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
        assert "No Read-hook activity has been recorded in this window" in output
        assert "Recovery footer" in output
        read_hook_pos = output.index("No Read-hook activity")
        headline_pos = output.index("NET TOKENS")
        recovery_pos = output.index("Recovery footer")
        assert read_hook_pos < headline_pos < recovery_pos

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


class TestGainFilters:
    """QB-054: `quor gain --filters` — same six-command budget as `quor
    gain` itself (it's a flag, not a new command), so this exercises it as
    a flag on the existing command exactly like `TestGain` above."""

    def _seed(self, tmp_path: Path) -> Path:
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
                command="npm run bad-command",
                project_path=tmp_path.as_posix(),
                original_tokens=100,
                final_tokens=150,  # net expansion -> should be flagged
                filter_name="npm",
                was_passthrough=False,
                duration_ms=5.0,
            )
        )
        db.flush()
        db.close()
        return db_path

    def test_filters_flag_prints_per_filter_report(self, tmp_path: Path) -> None:
        self._seed(tmp_path)
        with patch("platformdirs.user_data_dir", return_value=str(tmp_path / "data")):
            result = runner.invoke(
                app, ["gain", "--project", str(tmp_path), "--days", "30", "--filters"]
            )
        assert result.exit_code == 0
        output = result.output
        # The ordinary gain report still prints (this is additive, not a
        # replacement).
        assert "YOU SAVED" in output
        # Every QB-054-required section is present.
        assert "most-used filters" in output
        assert "Top compression performers" in output
        assert "Worst compression performers" in output
        assert "Negative or near-zero compression" in output
        assert "Real usage vs benchmark divergence" in output
        assert "Filters growing over time" in output
        assert "git-status" in output
        assert "npm" in output  # flagged as a low performer (net expansion)

    def test_filters_flag_appends_one_history_snapshot_per_run(self, tmp_path: Path) -> None:
        from quor.analytics.filter_history import load_history

        self._seed(tmp_path)
        history_path = tmp_path / "data" / "filter_analytics_history.json"

        with patch("platformdirs.user_data_dir", return_value=str(tmp_path / "data")):
            runner.invoke(app, ["gain", "--project", str(tmp_path), "--days", "30", "--filters"])
            runner.invoke(app, ["gain", "--project", str(tmp_path), "--days", "30", "--filters"])

        entries = load_history(history_path)
        assert len(entries) == 2

    def test_without_filters_flag_no_history_written(self, tmp_path: Path) -> None:
        self._seed(tmp_path)
        history_path = tmp_path / "data" / "filter_analytics_history.json"

        with patch("platformdirs.user_data_dir", return_value=str(tmp_path / "data")):
            result = runner.invoke(app, ["gain", "--project", str(tmp_path), "--days", "30"])

        assert result.exit_code == 0
        assert not history_path.exists()

    def test_filters_flag_on_empty_db_still_reports(self, tmp_path: Path) -> None:
        with patch("platformdirs.user_data_dir", return_value=str(tmp_path / "data")):
            result = runner.invoke(
                app, ["gain", "--project", str(tmp_path), "--filters"]
            )
        assert result.exit_code == 0
        assert "No invocations recorded" in result.output
        assert "most-used filters" in result.output


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
