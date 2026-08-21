"""Unit tests for quor/engine/dispatcher.py and quor/__main__.py routing."""

from __future__ import annotations

import io
import os
import sqlite3
import subprocess
import sys
import warnings
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from quor.engine.dispatcher import CONCISE_INSTRUCTION, run_dispatch
from quor.filters.registry import FilterRegistry
from quor.tracking.db import TrackingDB

# ---------------------------------------------------------------------------
# run_dispatch() tests
# ---------------------------------------------------------------------------


def _make_proc(stdout: str = "", returncode: int = 0) -> MagicMock:
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.stdout = stdout
    proc.returncode = returncode
    return proc


class TestDispatcher:
    def test_known_command_filter_applied(self) -> None:
        # Simulate "quor pytest tests/" with FAILED output (so abort_unless passes)
        proc = _make_proc(
            stdout=(
                "PASSED tests/test_a.py::test_x\n"
                "FAILED tests/test_b.py::test_y\n"
                "    AssertionError: got False\n"
            )
        )
        captured = io.StringIO()
        with (
            patch("subprocess.run", return_value=proc),
            patch("sys.stdout", captured),
        ):
            exit_code = run_dispatch(["pytest", "tests/"])

        output = captured.getvalue()
        assert exit_code == 0
        assert "FAILED" in output
        assert "PASSED" not in output

    def test_unknown_command_passthrough(self) -> None:
        proc = _make_proc(stdout="hello world\n")
        captured = io.StringIO()
        with (
            patch("subprocess.run", return_value=proc),
            patch("sys.stdout", captured),
        ):
            exit_code = run_dispatch(["echo", "hello"])

        assert exit_code == 0
        assert captured.getvalue() == "hello world\n"

    def test_subprocess_exit_code_propagated(self) -> None:
        proc = _make_proc(stdout="", returncode=2)
        captured = io.StringIO()
        with (
            patch("subprocess.run", return_value=proc),
            patch("sys.stdout", captured),
        ):
            exit_code = run_dispatch(["pytest", "--no-such-flag"])

        assert exit_code == 2

    def test_empty_args_returns_zero(self) -> None:
        assert run_dispatch([]) == 0

    def test_subprocess_oserror_returns_127(self) -> None:
        captured = io.StringIO()
        with (
            patch("subprocess.run", side_effect=FileNotFoundError("not found")),
            patch("sys.stdout", captured),
            patch("sys.stderr", io.StringIO()),
        ):
            exit_code = run_dispatch(["nonexistent-command", "--flag"])

        assert exit_code == 127

    @pytest.mark.skipif(
        sys.platform != "win32",
        reason="npm/npx/pnpm/yarn .cmd shim resolution is a Windows-specific concern",
    )
    def test_windows_shell_shim_executable_resolves_and_runs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # npm/npx/pnpm/yarn are .cmd shims on Windows, not native .exe files.
        # subprocess.run(["npm", ...]) without shell=True fails with
        # FileNotFoundError (WinError 2) because CreateProcess does not apply
        # PATHEXT resolution the way a shell does — confirmed by every other
        # test in this class mocking subprocess.run entirely, which is
        # exactly why this gap went undetected. This test spawns a real
        # subprocess (a throwaway .cmd shim) rather than mocking
        # subprocess.run, or it would not exercise the bug at all.
        shim = tmp_path / "fakeshim.cmd"
        shim.write_text("@echo off\r\necho shim output\r\n", encoding="utf-8")
        monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")

        captured = io.StringIO()
        with patch("sys.stdout", captured):
            exit_code = run_dispatch(["fakeshim"])

        assert exit_code == 0
        assert "shim output" in captured.getvalue()

    def test_filter_error_falls_through_to_original(self) -> None:
        proc = _make_proc(
            stdout=(
                "FAILED tests/test_x.py::test_y\n"
                "    AssertionError\n"
            )
        )
        captured = io.StringIO()
        # Force apply() to raise
        with (
            patch("subprocess.run", return_value=proc),
            patch("quor.engine.dispatcher.FilterRegistry") as mock_reg,
            patch("sys.stdout", captured),
        ):
            mock_inst = MagicMock()
            mock_reg.return_value = mock_inst
            mock_inst.find.return_value = MagicMock()  # returns a filter
            mock_inst.apply.side_effect = RuntimeError("boom")
            exit_code = run_dispatch(["pytest", "tests/"])

        # Fail-open: original output returned
        assert exit_code == 0
        assert "FAILED" in captured.getvalue()

    def test_no_filter_outputs_original(self) -> None:
        proc = _make_proc(stdout="some output\n")
        captured = io.StringIO()
        with (
            patch("subprocess.run", return_value=proc),
            patch("quor.engine.dispatcher.FilterRegistry") as mock_reg,
            patch("sys.stdout", captured),
        ):
            mock_inst = MagicMock()
            mock_reg.return_value = mock_inst
            mock_inst.find.return_value = None  # no filter found
            exit_code = run_dispatch(["some-tool", "arg"])

        assert captured.getvalue() == "some output\n"
        assert exit_code == 0

    def test_plugin_execute_failure_is_isolated(self) -> None:
        """A Plugin that raises unexpectedly during execute() must not break the hook.

        End-to-end fail-open verification for Phase 9: registers a real Plugin
        (not a mock) into the actual PluginRegistry that run_dispatch() builds,
        drives it through the real dispatcher path (subprocess mocked only at
        the OS boundary), and confirms the exception is isolated, a warning is
        emitted, the original output is preserved unchanged, and the hook still
        returns valid output with the correct exit code.
        """
        from quor.plugins.base import (
            PluginCategory,
            PluginContext,
            PluginMetadata,
            PluginPayload,
            PluginResult,
        )

        class _ExplodingPlugin:
            api_version = 1

            @property
            def metadata(self) -> PluginMetadata:
                return PluginMetadata(
                    plugin_id="com.test.exploding",
                    display_name="Exploding Plugin",
                    version="1.0.0",
                    category=PluginCategory.PRE_FILTER,
                )

            def initialize(self, ctx: PluginContext) -> None:
                pass

            def execute(self, payload: PluginPayload, ctx: PluginContext) -> PluginResult:
                raise RuntimeError("plugin exploded unexpectedly")

            def shutdown(self) -> None:
                pass

        def _fake_discover_plugins(registry: object, *, use_cache: bool = True, tier: str = "user") -> list:
            registry.register(_ExplodingPlugin(), tier=tier)  # type: ignore[attr-defined]
            return []

        proc = _make_proc(stdout="hello world\n")
        captured = io.StringIO()
        with (
            patch("subprocess.run", return_value=proc),
            patch("sys.stdout", captured),
            patch(
                "quor.pipeline.plugin_loader.discover_plugins",
                side_effect=_fake_discover_plugins,
            ),
            pytest.warns(UserWarning, match="raised during execute"),
        ):
            exit_code = run_dispatch(["echo", "hello"])

        # Execution continued and returned the real subprocess exit code.
        assert exit_code == 0
        # Original output preserved unchanged — the failing plugin's would-be
        # transformation never took effect.
        assert captured.getvalue() == "hello world\n"

    def test_secret_in_filtered_output_warns_but_stdout_unaffected(self) -> None:
        """PA-F07: a secret pattern surviving compression (e.g. inside a
        preserved FAILED/AssertionError line) triggers a stderr warning but
        is never redacted — stdout must still contain the secret verbatim."""
        secret = "ghp_" + "a" * 36
        proc = _make_proc(
            stdout=f"FAILED tests/test_leak.py::test_x\n    AssertionError: token {secret} leaked\n"
        )
        captured_stdout = io.StringIO()
        with (
            patch("subprocess.run", return_value=proc),
            patch("sys.stdout", captured_stdout),
            pytest.warns(UserWarning, match="Possible secret detected"),
        ):
            exit_code = run_dispatch(["pytest", "tests/"])

        assert exit_code == 0
        assert secret in captured_stdout.getvalue()

    def test_no_secret_no_warning(self) -> None:
        proc = _make_proc(stdout="FAILED tests/test_a.py::test_x\n    AssertionError: got False\n")
        captured_stdout = io.StringIO()
        with (
            patch("subprocess.run", return_value=proc),
            patch("sys.stdout", captured_stdout),
            warnings.catch_warnings(record=True) as caught,
        ):
            warnings.simplefilter("always")
            run_dispatch(["pytest", "tests/"])

        assert not any("secret" in str(w.message).lower() for w in caught)

    def test_onboarding_tip_fires_for_first_five_then_silent(self, tmp_path: Path) -> None:
        """PA-F08: the first 5 filtered dispatches print a stderr tip; the
        6th onward is silent. Real dispatcher path, real onboarding state
        file (isolated by the autouse platformdirs fixture)."""
        proc = _make_proc(stdout="FAILED tests/test_a.py::test_x\n    AssertionError: got False\n")

        tip_counts = []
        for _ in range(6):
            captured_stderr = io.StringIO()
            with (
                patch("subprocess.run", return_value=proc),
                patch("sys.stdout", io.StringIO()),
                patch("sys.stderr", captured_stderr),
            ):
                run_dispatch(["pytest", "tests/"])
            tip_counts.append("[quor] Tip (" in captured_stderr.getvalue())

        assert tip_counts == [True, True, True, True, True, False]


# ---------------------------------------------------------------------------
# run_dispatch() — files_changed telemetry (QB-093 prep)
# ---------------------------------------------------------------------------


class TestDispatcherFilesChangedTracking:
    """QB-093 telemetry prep: dispatcher.py computes files_changed only for
    git-diff invocations and records it on the InvocationRecord — see
    quor/tracking/db.py's v4 schema column and
    quor/pipeline/git_diff_enrich.py's count_diff_files()."""

    def test_git_diff_records_files_changed(self, tmp_path: Path) -> None:
        diff_text = (
            "diff --git a/a.py b/a.py\n"
            "index 111..222 100644\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-x\n"
            "+y\n"
            "diff --git a/b.py b/b.py\n"
            "index 333..444 100644\n"
            "--- a/b.py\n"
            "+++ b/b.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-x\n"
            "+y\n"
        )
        proc = _make_proc(stdout=diff_text)
        db_path = tmp_path / "quor.db"
        tracking = TrackingDB(db_path)
        try:
            with (
                patch("subprocess.run", return_value=proc),
                patch("sys.stdout", io.StringIO()),
            ):
                run_dispatch(["git", "diff"], tracking=tracking)
        finally:
            tracking.flush()
            tracking.close()

        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT files_changed FROM invocations WHERE filter_name = 'git-diff'"
            ).fetchone()
        assert row == (2,)

    def test_non_diff_command_records_none(self, tmp_path: Path) -> None:
        proc = _make_proc(stdout="FAILED tests/test_a.py::test_x\n    AssertionError: got False\n")
        db_path = tmp_path / "quor.db"
        tracking = TrackingDB(db_path)
        try:
            with (
                patch("subprocess.run", return_value=proc),
                patch("sys.stdout", io.StringIO()),
            ):
                run_dispatch(["pytest", "tests/"], tracking=tracking)
        finally:
            tracking.flush()
            tracking.close()

        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute("SELECT files_changed FROM invocations").fetchone()
        assert row == (None,)


# ---------------------------------------------------------------------------
# run_dispatch() — concise-output instruction
# ---------------------------------------------------------------------------


class TestConciseInstruction:
    # Generously large PASSED padding (all stripped by the pytest filter) so
    # the nudge's fixed 17-token cost comfortably stays under genuine
    # compression savings — mirrors TestDispatcherTee._CHANGED_OUTPUT's own
    # rationale for the same reason (QB-052 gate applies to both the tee
    # footer and this nudge now).
    _CHANGED_OUTPUT = (
        "".join(f"PASSED tests/test_pad_{i}.py::test_ok\n" for i in range(100))
        + "FAILED tests/test_b.py::test_y\n"
        "    AssertionError: got False\n"
    )

    # QB-052 follow-on: only one short PASSED line is stripped — far less
    # than the nudge's fixed cost — so adding it would push the total token
    # count above the true raw output's and must be suppressed.
    _TINY_CHANGED_OUTPUT = "PASSED tests/test_a.py::test_x\nFAILED tests/test_b.py::test_y\n"

    def test_prepended_when_a_filter_compresses_output(self) -> None:
        proc = _make_proc(stdout=self._CHANGED_OUTPUT)
        captured = io.StringIO()
        with (
            patch("subprocess.run", return_value=proc),
            patch("sys.stdout", captured),
        ):
            run_dispatch(["pytest", "tests/"])

        assert captured.getvalue().startswith(CONCISE_INSTRUCTION)

    def test_suppressed_when_it_would_cost_more_than_the_filter_saved(self) -> None:
        """QB-052 follow-on: a small, mostly-non-repetitive real output (the
        exact shape that motivated QB-052's tee-footer gate) must not have
        the fixed-cost nudge silently flip a real compression win into a
        net loss."""
        from quor.tracking.db import count_tokens

        proc = _make_proc(stdout=self._TINY_CHANGED_OUTPUT)
        captured = io.StringIO()
        with (
            patch("subprocess.run", return_value=proc),
            patch("sys.stdout", captured),
        ):
            run_dispatch(["pytest", "tests/"])

        output = captured.getvalue()
        assert not output.startswith(CONCISE_INSTRUCTION)
        assert count_tokens(output) <= count_tokens(self._TINY_CHANGED_OUTPUT)

    def test_absent_on_passthrough(self) -> None:
        """No filter matched: output must stay byte-identical (fail-open
        contract) — the instruction is only prepended on the compressed path."""
        proc = _make_proc(stdout="hello world\n")
        captured = io.StringIO()
        with (
            patch("subprocess.run", return_value=proc),
            patch("sys.stdout", captured),
        ):
            run_dispatch(["echo", "hello"])

        assert captured.getvalue() == "hello world\n"

    def test_disabled_via_module_flag(self) -> None:
        proc = _make_proc(stdout=self._CHANGED_OUTPUT)
        captured = io.StringIO()
        with (
            patch("subprocess.run", return_value=proc),
            patch("sys.stdout", captured),
            patch("quor.engine.dispatcher.CONCISE_INSTRUCTION_ENABLED", False),
        ):
            run_dispatch(["pytest", "tests/"])

        assert not captured.getvalue().startswith(CONCISE_INSTRUCTION)


# ---------------------------------------------------------------------------
# run_dispatch() — tee mechanism (ADR-023)
# ---------------------------------------------------------------------------


class TestDispatcherTee:
    # A generously large amount of PASSED noise (all stripped by the pytest
    # filter) so genuine compression savings comfortably exceed the tee
    # recovery footer's fixed path-length cost regardless of how deep the
    # test's own tmp_path happens to be on a given machine (QB-052) — this
    # fixture is meant to stay net-positive; see _TINY_CHANGED_OUTPUT below
    # for the deliberately-small counterpart.
    _CHANGED_OUTPUT = (
        "".join(f"PASSED tests/test_pad_{i}.py::test_ok\n" for i in range(100))
        + "FAILED tests/test_b.py::test_y\n"
        "    AssertionError: got False\n"
    )

    # QB-052: only one short PASSED line is stripped (a handful of tokens) —
    # far less than the tee footer's fixed cost — so the visible footer must
    # be suppressed here. Same shape as the real-world mypy/npm finding that
    # motivated QB-052: small output, little to strip, footer dominates.
    _TINY_CHANGED_OUTPUT = "PASSED tests/test_a.py::test_x\nFAILED tests/test_b.py::test_y\n"

    def test_footer_appended_when_output_changes(self) -> None:
        proc = _make_proc(stdout=self._CHANGED_OUTPUT)
        captured = io.StringIO()
        with (
            patch("subprocess.run", return_value=proc),
            patch("sys.stdout", captured),
        ):
            run_dispatch(["pytest", "tests/"])

        output = captured.getvalue()
        assert "[full output:" in output

        import re

        from quor.pipeline.tee import read_tee

        match = re.search(r"--show-tee (\S+)\]", output)
        assert match is not None
        # The recovered entry holds the true raw output, including the
        # PASSED line that the filter stripped from what's printed to stdout.
        assert read_tee(match.group(1)) == self._CHANGED_OUTPUT

    def test_no_footer_when_output_unchanged(self) -> None:
        # abort_unless (no FAILED/ERROR/error substring) short-circuits the
        # pytest filter, so output is returned byte-for-byte unchanged —
        # nothing to recover, so tee must not fire.
        proc = _make_proc(stdout="2 passed in 0.1s\n")
        captured = io.StringIO()
        with (
            patch("subprocess.run", return_value=proc),
            patch("sys.stdout", captured),
        ):
            run_dispatch(["pytest", "tests/"])

        assert "[full output:" not in captured.getvalue()

    def test_filter_level_opt_out_disables_footer(self) -> None:
        proc = _make_proc(stdout=self._CHANGED_OUTPUT)
        captured = io.StringIO()

        real_registry = FilterRegistry(project_root=Path.cwd())
        real_filter = real_registry.find("pytest tests/")
        assert real_filter is not None
        disabled_filter = real_filter.model_copy(update={"tee": False})

        with (
            patch("subprocess.run", return_value=proc),
            patch("sys.stdout", captured),
            patch("quor.engine.dispatcher.FilterRegistry") as mock_reg_cls,
        ):
            mock_inst = MagicMock()
            mock_reg_cls.return_value = mock_inst
            mock_inst.find.return_value = disabled_filter
            mock_inst.apply.return_value = "FAILED tests/test_b.py::test_y\n"
            run_dispatch(["pytest", "tests/"])

        assert "[full output:" not in captured.getvalue()

    def test_global_env_opt_out_disables_footer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QUOR_TEE_ENABLED", "0")
        proc = _make_proc(stdout=self._CHANGED_OUTPUT)
        captured = io.StringIO()
        with (
            patch("subprocess.run", return_value=proc),
            patch("sys.stdout", captured),
        ):
            run_dispatch(["pytest", "tests/"])

        assert "[full output:" not in captured.getvalue()

    def test_configured_tee_max_bytes_reaches_cleanup_tee(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """QB-103: QuorUserConfig.tee_max_bytes (here via QUOR_TEE_MAX_BYTES)
        must actually reach cleanup_tee()'s max_bytes parameter — proves the
        dispatcher wiring, not just cleanup_tee()'s own default-argument
        behavior (already covered in tests/unit/test_tee.py)."""
        monkeypatch.setenv("QUOR_TEE_MAX_BYTES", "12345")
        proc = _make_proc(stdout=self._CHANGED_OUTPUT)
        with (
            patch("subprocess.run", return_value=proc),
            patch("sys.stdout", io.StringIO()),
            patch("quor.engine.dispatcher.cleanup_tee") as mock_cleanup,
        ):
            run_dispatch(["pytest", "tests/"])

        mock_cleanup.assert_called_once_with(max_bytes=12345)

    def test_identical_repeated_output_dedupes_to_one_tee_row(self) -> None:
        from quor.pipeline.tee import content_hash, read_tee

        proc = _make_proc(stdout=self._CHANGED_OUTPUT)
        footers = []
        for _ in range(2):
            captured = io.StringIO()
            with (
                patch("subprocess.run", return_value=proc),
                patch("sys.stdout", captured),
            ):
                run_dispatch(["pytest", "tests/"])
            footers.append(captured.getvalue())

        import re

        hashes = [re.search(r"--show-tee (\S+)\]", f).group(1) for f in footers]  # type: ignore[union-attr]
        assert hashes[0] == hashes[1]
        assert hashes[0] == content_hash(self._CHANGED_OUTPUT)
        assert read_tee(hashes[0]) == self._CHANGED_OUTPUT

    def test_tee_failure_does_not_affect_stdout_or_exit_code(self) -> None:
        """A broken tee write must never affect the real command's output/exit code."""
        proc = _make_proc(stdout=self._CHANGED_OUTPUT, returncode=0)
        captured = io.StringIO()
        with (
            patch("subprocess.run", return_value=proc),
            patch("sys.stdout", captured),
            patch("quor.engine.dispatcher.write_tee", side_effect=OSError("disk full")),
        ):
            exit_code = run_dispatch(["pytest", "tests/"])

        assert exit_code == 0
        assert "FAILED" in captured.getvalue()
        assert "[full output:" not in captured.getvalue()

    def test_first_dispatcher_failure_leaves_tee_enabled(self) -> None:
        """QB-013 adaptive fallback: one filesystem failure alone must not
        disable tee — only MAX_CONSECUTIVE_TEE_FAILURES consecutive ones."""
        from quor.pipeline.tee import MAX_CONSECUTIVE_TEE_FAILURES, get_tee_status

        proc = _make_proc(stdout=self._CHANGED_OUTPUT)
        with (
            patch("subprocess.run", return_value=proc),
            patch("sys.stdout", io.StringIO()),
            patch("quor.engine.dispatcher.write_tee", side_effect=OSError("Access is denied")),
        ):
            run_dispatch(["pytest", "tests/"])

        status = get_tee_status()
        assert MAX_CONSECUTIVE_TEE_FAILURES > 1, "test assumes threshold isn't 1"
        assert status.disabled is False
        assert status.consecutive_failures == 1

    def test_reaching_threshold_disables_tee(self) -> None:
        from quor.pipeline.tee import MAX_CONSECUTIVE_TEE_FAILURES, get_tee_status

        proc = _make_proc(stdout=self._CHANGED_OUTPUT)
        with patch(
            "quor.engine.dispatcher.write_tee", side_effect=OSError("Access is denied")
        ):
            for _ in range(MAX_CONSECUTIVE_TEE_FAILURES):
                with (
                    patch("subprocess.run", return_value=proc),
                    patch("sys.stdout", io.StringIO()),
                ):
                    run_dispatch(["pytest", "tests/"])

        status = get_tee_status()
        assert status.disabled is True
        assert status.consecutive_failures == MAX_CONSECUTIVE_TEE_FAILURES

    def test_disabled_tee_makes_no_further_write_attempts(self) -> None:
        """After adaptive-disable, one more invocation (with content that
        would otherwise clearly trigger tee) must not even call write_tee —
        no automatic retry, ever."""
        from quor.pipeline.tee import MAX_CONSECUTIVE_TEE_FAILURES, get_tee_status

        proc = _make_proc(stdout=self._CHANGED_OUTPUT)
        with patch(
            "quor.engine.dispatcher.write_tee", side_effect=OSError("Access is denied")
        ) as mock_write:
            for _ in range(MAX_CONSECUTIVE_TEE_FAILURES):
                with (
                    patch("subprocess.run", return_value=proc),
                    patch("sys.stdout", io.StringIO()),
                ):
                    run_dispatch(["pytest", "tests/"])
            assert mock_write.call_count == MAX_CONSECUTIVE_TEE_FAILURES

        assert get_tee_status().disabled is True

        captured = io.StringIO()
        with (
            patch("subprocess.run", return_value=proc),
            patch("sys.stdout", captured),
            patch("quor.engine.dispatcher.write_tee") as mock_write_after_disable,
        ):
            exit_code = run_dispatch(["pytest", "tests/"])
            mock_write_after_disable.assert_not_called()

        assert exit_code == 0
        assert "FAILED" in captured.getvalue()
        assert "[full output:" not in captured.getvalue()

    def test_successful_write_resets_failure_counter(self) -> None:
        """A single failure followed by a real, successful write must reset
        the consecutive-failure counter back to zero."""
        from quor.pipeline.tee import get_tee_status

        proc = _make_proc(stdout=self._CHANGED_OUTPUT)
        with (
            patch("subprocess.run", return_value=proc),
            patch("sys.stdout", io.StringIO()),
            patch("quor.engine.dispatcher.write_tee", side_effect=OSError("Access is denied")),
        ):
            run_dispatch(["pytest", "tests/"])
        assert get_tee_status().consecutive_failures == 1

        with (
            patch("subprocess.run", return_value=proc),
            patch("sys.stdout", io.StringIO()),
        ):
            run_dispatch(["pytest", "tests/"])  # real write_tee — succeeds

        status = get_tee_status()
        assert status.consecutive_failures == 0
        assert status.disabled is False

    def test_footer_bypasses_the_filters_max_tokens_budget(self) -> None:
        """End-to-end proof of the ADR-023 design claim: the footer is
        appended after the full pipeline (including max_tokens) has already
        run, so it is never itself subject to — or truncated by — the
        filter's configured token limit.

        Compares the dispatcher's real output against what the filter alone
        (registry.apply(), no tee) produces for the identical input: the
        footer must be appended verbatim on top, pushing the total past what
        max_tokens would have permitted on its own — proof the budget was
        enforced on the body only, before the footer ever entered the
        picture.
        """
        from quor.filters.registry import FilterRegistry
        from quor.tracking.db import count_tokens

        # PASSED lines get stripped (guarantees tee actually triggers), while
        # the 60 FAILED lines are protected by preserve_patterns and provide
        # enough weight to matter for the max_tokens=500 budget comparison.
        # 200 lines (not just a handful) so the stripped savings comfortably
        # exceed the tee footer's fixed path-length cost (QB-052) regardless
        # of how deep the test's own tmp_path happens to be.
        passed_noise = "".join(f"PASSED tests/test_{i}.py::test_ok\n" for i in range(200))
        many_failures = "".join(f"FAILED tests/test_{i}.py::test_case\n" for i in range(60))
        raw_output = passed_noise + many_failures
        proc = _make_proc(stdout=raw_output)

        registry = FilterRegistry(project_root=Path.cwd())
        filter_config = registry.find("pytest tests/")
        assert filter_config is not None
        filter_only_output = registry.apply(filter_config, raw_output)

        captured = io.StringIO()
        with (
            patch("subprocess.run", return_value=proc),
            patch("sys.stdout", captured),
        ):
            run_dispatch(["pytest", "tests/"])
        dispatcher_output = captured.getvalue()

        # Dispatcher output is exactly the concise-output instruction +
        # "filter's own output" + footer — nothing about the body itself
        # changed because tee ran afterward, and the instruction is prepended
        # only as the very last step before stdout (see CONCISE_INSTRUCTION).
        assert dispatcher_output.startswith(CONCISE_INSTRUCTION + filter_only_output)
        footer = dispatcher_output[len(CONCISE_INSTRUCTION + filter_only_output) :]
        assert footer.startswith("\n[full output:")

        # The filter alone already respects its own max_tokens=500 budget
        # (±20% char/4 estimate, so allow slack); adding the footer pushes
        # the dispatcher's total past that budget, proving the footer was
        # never subject to it.
        assert count_tokens(filter_only_output) <= 500 * 1.25
        assert count_tokens(dispatcher_output) > count_tokens(filter_only_output)

    # -----------------------------------------------------------------
    # QB-052: suppress the recovery footer when it would make the
    # invocation net-negative (the footer costs more tokens than the
    # filter actually saved).
    # -----------------------------------------------------------------

    def test_qb052_footer_retained_when_compression_stays_net_positive(self) -> None:
        """When the filter's own savings comfortably exceed the tee
        footer's fixed cost, behavior is unchanged from before QB-052: the
        footer is appended and the raw output is recoverable from it."""
        from quor.pipeline.tee import content_hash, read_tee

        proc = _make_proc(stdout=self._CHANGED_OUTPUT)
        captured = io.StringIO()
        with (
            patch("subprocess.run", return_value=proc),
            patch("sys.stdout", captured),
        ):
            run_dispatch(["pytest", "tests/"])

        output = captured.getvalue()
        expected_digest = content_hash(self._CHANGED_OUTPUT)
        assert f"[full output: quor doctor --show-tee {expected_digest}]" in output
        assert read_tee(expected_digest) == self._CHANGED_OUTPUT

    def test_qb052_footer_suppressed_when_it_would_cause_net_expansion(self) -> None:
        """mypy/npm-shaped case: the filter only strips a handful of tokens,
        far less than the footer's fixed cost. The visible footer must be
        omitted — the printed output must never cost more tokens than the
        true raw output would have — but the tee entry is still written
        unconditionally, so the raw output remains recoverable even though
        stdout doesn't advertise it.

        The concise-output nudge (QB-052 follow-on, see TestConciseInstruction)
        is gated by the same rule and is suppressed here too: this is the
        exact tiny-savings shape where the fixed nudge cost would also
        outweigh what the filter saved."""
        from quor.pipeline.tee import content_hash, read_tee

        proc = _make_proc(stdout=self._TINY_CHANGED_OUTPUT)
        captured = io.StringIO()
        with (
            patch("subprocess.run", return_value=proc),
            patch("sys.stdout", captured),
        ):
            run_dispatch(["pytest", "tests/"])

        output = captured.getvalue()
        assert "[full output:" not in output
        assert not output.startswith(CONCISE_INSTRUCTION)
        assert output == "FAILED tests/test_b.py::test_y\n"

        # The tee entry was still written for this exact raw content —
        # "always generate the tee entry" holds even when the footer is
        # suppressed.
        expected_digest = content_hash(self._TINY_CHANGED_OUTPUT)
        assert read_tee(expected_digest) == self._TINY_CHANGED_OUTPUT

    def test_qb052_error_during_footer_token_check_fails_open(self) -> None:
        """If the new token-count comparison itself raises, _apply_tee's
        existing outer fail-open handler still wins — same ADR-018 contract
        as any other tee-internal error, unchanged by QB-052."""
        from quor.engine.dispatcher import _apply_tee
        from quor.filters.registry import FilterRegistry

        registry = FilterRegistry(project_root=Path.cwd())
        filter_config = registry.find("pytest tests/")
        assert filter_config is not None
        final_output = "FAILED tests/test_b.py::test_y\n    AssertionError: got False\n"

        with patch(
            "quor.engine.dispatcher.count_tokens", side_effect=RuntimeError("boom")
        ):
            result = _apply_tee(
                filter_config,
                captured=self._CHANGED_OUTPUT,
                final_output=final_output,
            )

        assert result == final_output
        assert "[full output:" not in result


# ---------------------------------------------------------------------------
# __main__ routing tests
# ---------------------------------------------------------------------------


class TestMainRouting:
    def test_dispatch_routing(self) -> None:
        """quor git status → _run_dispatch() called with ['git', 'status']."""
        with (
            patch("sys.argv", ["quor", "git", "status"]),
            patch("quor.__main__._run_dispatch") as mock_dispatch,
            pytest.raises(SystemExit),  # _run_dispatch calls sys.exit
        ):
            mock_dispatch.side_effect = SystemExit(0)
            from quor.__main__ import main

            main()
            mock_dispatch.assert_called_once_with(["git", "status"])

    def test_cli_routing_schema(self) -> None:
        """quor schema → typer CLI (not dispatcher)."""
        with (
            patch("sys.argv", ["quor", "schema"]),
            patch("quor.__main__._run_dispatch") as mock_dispatch,
            patch("quor.cli.main.app") as mock_app,
        ):
            from quor.__main__ import main

            main()
            mock_dispatch.assert_not_called()
            mock_app.assert_called_once()

    def test_flag_goes_to_cli(self) -> None:
        """quor --help → typer CLI (flags not dispatched)."""
        with (
            patch("sys.argv", ["quor", "--help"]),
            patch("quor.__main__._run_dispatch") as mock_dispatch,
            patch("quor.cli.main.app") as mock_app,
        ):
            from quor.__main__ import main

            main()
            mock_dispatch.assert_not_called()
            mock_app.assert_called_once()


class TestMainRealExecution:
    """TD-010: __main__.py had the lowest coverage in the codebase because
    TestMainRouting above always mocks _run_dispatch entirely at the call
    site — the real body of the dispatch wrapper was never actually
    exercised by any test."""

    def test_run_dispatch_real_execution_exits_with_real_code(self) -> None:
        """quor git status: the real _run_dispatch() body (not mocked) opens
        a real tracking DB, runs the real dispatcher, and exits with the
        exit code run_dispatch() actually returned."""
        from quor.__main__ import _run_dispatch

        captured_stdout = io.StringIO()
        with (
            patch.object(sys, "stdout", captured_stdout),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_dispatch(["git", "status"])

        assert exc_info.value.code == 0
