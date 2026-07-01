"""Unit tests for quor/adapters/: hook adapter and dispatcher."""

from __future__ import annotations

import io
import subprocess
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import orjson
import pytest
from pydantic import ValidationError

from quor.adapters.base import HookInput, HookOutput, ToolInput
from quor.adapters.claude import HOOK_COMMAND, HOOK_PS1_TEMPLATE, run_hook
from quor.adapters.dispatcher import run_dispatch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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

    def test_hook_output_is_same_shape(self) -> None:
        ho = HookOutput.model_validate(
            {"tool_name": "Bash", "tool_input": {"command": "quor git status"}}
        )
        assert ho.tool_input.command == "quor git status"

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


class TestRunHookRewrite:
    def test_known_command_is_rewritten(self) -> None:
        payload = _make_hook_payload("git status")
        result = _run_hook_with(payload)
        assert result["tool_input"]["command"] == "quor git status"

    def test_unknown_command_unchanged(self) -> None:
        payload = _make_hook_payload("npm install")
        result = _run_hook_with(payload)
        assert result["tool_input"]["command"] == "npm install"

    def test_compound_command_rewritten(self) -> None:
        payload = _make_hook_payload("git status && git diff")
        result = _run_hook_with(payload)
        assert result["tool_input"]["command"] == "quor git status && quor git diff"

    def test_excluded_command_unchanged(self) -> None:
        payload = _make_hook_payload("git status --porcelain")
        result = _run_hook_with(payload)
        assert result["tool_input"]["command"] == "git status --porcelain"

    def test_heredoc_command_unchanged(self) -> None:
        payload = _make_hook_payload("git commit -m << EOF")
        result = _run_hook_with(payload)
        assert result["tool_input"]["command"] == "git commit -m << EOF"

    def test_extra_fields_preserved(self) -> None:
        payload = _make_hook_payload("git status", session_id="abc123", tool_name="Bash")
        result = _run_hook_with(payload)
        assert result["session_id"] == "abc123"
        assert result["tool_name"] == "Bash"

    def test_extra_tool_input_fields_preserved(self) -> None:
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "git log", "description": "show history"},
        }
        result = _run_hook_with(payload)
        assert result["tool_input"]["description"] == "show history"
        assert result["tool_input"]["command"] == "quor git log"

    def test_output_is_valid_json(self) -> None:
        payload = _make_hook_payload("git status")
        result = _run_hook_with(payload)
        # _run_hook_with already parsed — reaching here means valid JSON
        assert isinstance(result, dict)


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
        assert result["tool_input"]["command"] == "quor git status"

    def test_doubled_bom_stripped(self) -> None:
        result = self._run_with_bom_str(2, _make_hook_payload("git diff"))
        assert result["tool_input"]["command"] == "quor git diff"

    def test_no_bom_works(self) -> None:
        payload = _make_hook_payload("pytest tests/")
        result = _run_hook_with(payload)
        assert result["tool_input"]["command"] == "quor pytest tests/"


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
