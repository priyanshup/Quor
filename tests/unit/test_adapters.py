"""Unit tests for quor/adapters/: hook adapter and dispatcher."""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import orjson
import pytest
from pydantic import ValidationError

from quor.adapters.base import HookInput, HookOutput, ToolInput
from quor.adapters.claude import HOOK_COMMAND, HOOK_PS1_TEMPLATE, run_hook
from quor.adapters.dispatcher import run_dispatch
from quor.filters.registry import FilterRegistry
from quor.rewrite.invocation import get_quor_invocation

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Rewritten commands are prefixed with the shell-safe Quor invocation
# (sys.executable -m quor), not the bare `quor` launcher — see
# quor/rewrite/invocation.py. Compare against this same helper.
Q = get_quor_invocation()


def _make_hook_payload(command: str, **extra: Any) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}, **extra}


class _FakeStdout:
    """sys.stdout replacement with a writable binary .buffer attribute."""

    def __init__(self) -> None:
        self.buffer: io.BytesIO = io.BytesIO()

    def write(self, s: str) -> int:
        return 0

    def flush(self) -> None:
        pass


def _run_hook_with(payload: dict) -> dict:
    """Call run_hook() with payload as stdin; return parsed stdout JSON."""
    raw = orjson.dumps(payload)
    stdin_text = raw.decode("utf-8")
    fake_stdout = _FakeStdout()

    with (
        patch.object(sys, "stdin", io.StringIO(stdin_text)),
        patch.object(sys, "stdout", fake_stdout),
    ):
        run_hook()

    fake_stdout.buffer.seek(0)
    return orjson.loads(fake_stdout.buffer.read())


# ---------------------------------------------------------------------------
# HookInput / HookOutput model tests
# ---------------------------------------------------------------------------


class TestModels:
    def test_tool_input_parses_command(self) -> None:
        ti = ToolInput(command="git status")
        assert ti.command == "git status"

    def test_tool_input_extra_fields_preserved(self) -> None:
        ti = ToolInput.model_validate({"command": "git status", "description": "check state"})
        assert ti.command == "git status"
        # Extra field stored in __pydantic_extra__
        assert ti.model_extra is not None
        assert ti.model_extra.get("description") == "check state"

    def test_hook_input_defaults(self) -> None:
        hi = HookInput.model_validate({"tool_input": {"command": "pytest"}})
        assert hi.tool_name == ""
        assert hi.tool_input.command == "pytest"

    def test_hook_input_with_tool_name(self) -> None:
        hi = HookInput.model_validate(
            {"tool_name": "Bash", "tool_input": {"command": "git diff"}}
        )
        assert hi.tool_name == "Bash"

    def test_hook_output_shape(self) -> None:
        ho = HookOutput.model_validate(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "updatedInput": {"command": f"{Q} git status"},
                }
            }
        )
        assert ho.hookSpecificOutput.updatedInput is not None
        assert ho.hookSpecificOutput.updatedInput["command"] == f"{Q} git status"

    def test_hook_input_missing_tool_input_raises(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            HookInput.model_validate({"tool_name": "Bash"})

    def test_hook_input_empty_command_allowed(self) -> None:
        hi = HookInput.model_validate({"tool_input": {}})
        assert hi.tool_input.command == ""


# ---------------------------------------------------------------------------
# run_hook() — rewrite behaviour
# ---------------------------------------------------------------------------


def _updated_command(result: dict) -> str | None:
    """Extract the rewritten command from a hookSpecificOutput response, if any."""
    updated_input = result.get("hookSpecificOutput", {}).get("updatedInput")
    return None if updated_input is None else updated_input.get("command")


class TestRunHookRewrite:
    def test_known_command_is_rewritten(self) -> None:
        payload = _make_hook_payload("git status")
        result = _run_hook_with(payload)
        assert _updated_command(result) == f"{Q} git status"

    def test_response_shape(self) -> None:
        """The response is wrapped in hookSpecificOutput — not a raw tool_input echo."""
        payload = _make_hook_payload("git status")
        result = _run_hook_with(payload)
        assert result["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
        assert result["hookSpecificOutput"]["permissionDecision"] == "allow"
        assert "tool_input" not in result

    def test_unknown_command_unchanged(self) -> None:
        """No rewrite → updatedInput is omitted so Claude Code runs the original command."""
        payload = _make_hook_payload("npm install")
        result = _run_hook_with(payload)
        assert "updatedInput" not in result["hookSpecificOutput"]

    def test_compound_command_rewritten(self) -> None:
        payload = _make_hook_payload("git status && git diff")
        result = _run_hook_with(payload)
        assert _updated_command(result) == f"{Q} git status && {Q} git diff"

    def test_excluded_command_unchanged(self) -> None:
        payload = _make_hook_payload("git status --porcelain")
        result = _run_hook_with(payload)
        assert "updatedInput" not in result["hookSpecificOutput"]

    def test_heredoc_command_unchanged(self) -> None:
        payload = _make_hook_payload("git commit -m << EOF")
        result = _run_hook_with(payload)
        assert "updatedInput" not in result["hookSpecificOutput"]

    def test_extra_tool_input_fields_preserved(self) -> None:
        """updatedInput replaces the whole tool_input object — sibling fields must survive."""
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "git log", "description": "show history"},
        }
        result = _run_hook_with(payload)
        updated_input = result["hookSpecificOutput"]["updatedInput"]
        assert updated_input["description"] == "show history"
        assert updated_input["command"] == f"{Q} git log"

    def test_output_is_valid_json(self) -> None:
        payload = _make_hook_payload("git status")
        result = _run_hook_with(payload)
        # _run_hook_with already parsed — reaching here means valid JSON
        assert isinstance(result, dict)

    def test_does_not_regress_to_bare_tool_input_echo(self) -> None:
        """Regression guard: Claude Code only honors hookSpecificOutput.updatedInput.

        A prior version of this hook echoed back the whole mutated input
        payload as a top-level `tool_input` key. That shape round-trips fine
        in-process (these tests would have passed) but is silently ignored by
        the real Claude Code binary, so the rewrite never took effect end to
        end. Guard against reintroducing it.
        """
        payload = _make_hook_payload("git status")
        result = _run_hook_with(payload)
        assert "tool_input" not in result
        assert "tool_name" not in result
        assert set(result.keys()) == {"hookSpecificOutput"}


# ---------------------------------------------------------------------------
# run_hook() — BOM handling
# ---------------------------------------------------------------------------


_BOM = "﻿"  # U+FEFF, single BOM character


class TestRunHookBom:
    def _run_with_bom_str(self, boms: int, payload: dict) -> dict:
        """Run hook with N BOM characters prepended to the JSON string."""
        json_str = orjson.dumps(payload).decode("utf-8")
        bom_str = _BOM * boms + json_str
        fake_stdout = _FakeStdout()
        with (
            patch.object(sys, "stdin", io.StringIO(bom_str)),
            patch.object(sys, "stdout", fake_stdout),
        ):
            run_hook()
        fake_stdout.buffer.seek(0)
        return orjson.loads(fake_stdout.buffer.read())

    def test_single_bom_stripped(self) -> None:
        result = self._run_with_bom_str(1, _make_hook_payload("git status"))
        assert _updated_command(result) == f"{Q} git status"

    def test_doubled_bom_stripped(self) -> None:
        result = self._run_with_bom_str(2, _make_hook_payload("git diff"))
        assert _updated_command(result) == f"{Q} git diff"

    def test_no_bom_works(self) -> None:
        payload = _make_hook_payload("pytest tests/")
        result = _run_hook_with(payload)
        assert _updated_command(result) == f"{Q} pytest tests/"


# ---------------------------------------------------------------------------
# run_hook() — invalid JSON raises (caught by __main__)
# ---------------------------------------------------------------------------


class TestRunHookInvalidJson:
    def test_invalid_json_raises(self) -> None:
        fake_stdout = _FakeStdout()
        with (
            patch.object(sys, "stdin", io.StringIO("not valid json {")),
            patch.object(sys, "stdout", fake_stdout),
            pytest.raises(orjson.JSONDecodeError),
        ):
            run_hook()

    def test_missing_tool_input_raises(self) -> None:
        fake_stdout = _FakeStdout()
        with (
            patch.object(sys, "stdin", io.StringIO('{"tool_name": "Bash"}')),
            patch.object(sys, "stdout", fake_stdout),
            pytest.raises(ValidationError),
        ):
            run_hook()


# ---------------------------------------------------------------------------
# Hook template constants
# ---------------------------------------------------------------------------


class TestHookTemplate:
    def test_hook_command_has_python_placeholder(self) -> None:
        assert "{python}" in HOOK_COMMAND

    def test_hook_ps1_template_has_python_placeholder(self) -> None:
        assert "{python}" in HOOK_PS1_TEMPLATE

    def test_hook_ps1_template_can_be_formatted(self) -> None:
        rendered = HOOK_PS1_TEMPLATE.format(python=r"C:\Python\python.exe")
        assert r"C:\Python\python.exe" in rendered


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
            patch("quor.adapters.dispatcher.FilterRegistry") as mock_reg,
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
            patch("quor.adapters.dispatcher.FilterRegistry") as mock_reg,
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


# ---------------------------------------------------------------------------
# run_dispatch() — tee mechanism (ADR-023)
# ---------------------------------------------------------------------------


class TestDispatcherTee:
    _CHANGED_OUTPUT = (
        "PASSED tests/test_a.py::test_x\n"
        "FAILED tests/test_b.py::test_y\n"
        "    AssertionError: got False\n"
    )

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

        match = re.search(r"\[full output: (.+)\]", output)
        assert match is not None
        tee_file = Path(match.group(1))
        assert tee_file.exists()
        # The tee file holds the true raw output, including the PASSED line
        # that the filter stripped from what's printed to stdout.
        assert tee_file.read_text(encoding="utf-8") == self._CHANGED_OUTPUT

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
            patch("quor.adapters.dispatcher.FilterRegistry") as mock_reg_cls,
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

    def test_identical_repeated_output_dedupes_to_one_tee_file(self) -> None:
        from quor.pipeline.tee import tee_dir

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

        paths = [re.search(r"\[full output: (.+)\]", f).group(1) for f in footers]  # type: ignore[union-attr]
        assert paths[0] == paths[1]
        assert len(list(tee_dir().glob("*.txt"))) == 1

    def test_tee_failure_does_not_affect_stdout_or_exit_code(self) -> None:
        """A broken tee write must never affect the real command's output/exit code."""
        proc = _make_proc(stdout=self._CHANGED_OUTPUT, returncode=0)
        captured = io.StringIO()
        with (
            patch("subprocess.run", return_value=proc),
            patch("sys.stdout", captured),
            patch("quor.adapters.dispatcher.write_tee", side_effect=OSError("disk full")),
        ):
            exit_code = run_dispatch(["pytest", "tests/"])

        assert exit_code == 0
        assert "FAILED" in captured.getvalue()
        assert "[full output:" not in captured.getvalue()


# ---------------------------------------------------------------------------
# __main__ routing tests
# ---------------------------------------------------------------------------


class TestMainRouting:
    def test_hook_routing(self) -> None:
        """quor hook claude → _run_hook() called."""
        with (
            patch("sys.argv", ["quor", "hook", "claude"]),
            patch("quor.__main__._run_hook") as mock_hook,
            patch("quor.__main__._run_dispatch") as mock_dispatch,
        ):
            from quor.__main__ import main

            main()
            mock_hook.assert_called_once()
            mock_dispatch.assert_not_called()

    def test_dispatch_routing(self) -> None:
        """quor git status → _run_dispatch() called with ['git', 'status']."""
        with (
            patch("sys.argv", ["quor", "git", "status"]),
            patch("quor.__main__._run_hook") as mock_hook,
            patch("quor.__main__._run_dispatch") as mock_dispatch,
            pytest.raises(SystemExit),  # _run_dispatch calls sys.exit
        ):
            mock_dispatch.side_effect = SystemExit(0)
            from quor.__main__ import main

            main()
            mock_dispatch.assert_called_once_with(["git", "status"])
            mock_hook.assert_not_called()

    def test_cli_routing_schema(self) -> None:
        """quor schema → typer CLI (not dispatcher)."""
        with (
            patch("sys.argv", ["quor", "schema"]),
            patch("quor.__main__._run_dispatch") as mock_dispatch,
            patch("quor.__main__._run_hook") as mock_hook,
            patch("quor.cli.main.app") as mock_app,
        ):
            from quor.__main__ import main

            main()
            mock_dispatch.assert_not_called()
            mock_hook.assert_not_called()
            mock_app.assert_called_once()

    def test_flag_goes_to_cli(self) -> None:
        """quor --help → typer CLI (flags not dispatched)."""
        with (
            patch("sys.argv", ["quor", "--help"]),
            patch("quor.__main__._run_dispatch") as mock_dispatch,
            patch("quor.__main__._run_hook") as mock_hook,
            patch("quor.cli.main.app") as mock_app,
        ):
            from quor.__main__ import main

            main()
            mock_dispatch.assert_not_called()
            mock_hook.assert_not_called()
            mock_app.assert_called_once()
