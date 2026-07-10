"""Unit tests for quor/adapters/claude_read.py — PostToolUse/Read hook.

Covers payload validation, response shape, BOM handling, and adapter-level
fail-open behaviour (QB-007A), plus the live compression wiring (QB-007C):
a supported, oversized document now genuinely returns `updatedToolOutput`;
small/unsupported/identical-output cases still correctly omit it. See
tests/unit/test_adapters.py for the equivalent PreToolUse/Bash coverage this
file mirrors, and tests/unit/test_read_hook_activation.py for the dedicated
end-to-end QB-007C coverage (routing precedence, fail-open on filter/pipeline
failure, unsupported-type passthrough).
"""

from __future__ import annotations

import io
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import orjson
import pytest
from pydantic import ValidationError

from quor.adapters.base import (
    PostToolUseHookInput,
    PostToolUseHookOutput,
    ReadToolInput,
)
from quor.adapters.claude_read import HOOK_READ_COMMAND, HOOK_READ_PS1_TEMPLATE, run_hook

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_read_payload(file_path: str = "notes.md", **extra: Any) -> dict:
    return {
        "tool_name": "Read",
        "tool_input": {"file_path": file_path},
        "tool_response": "# Heading\n\nBody text.\n",
        **extra,
    }


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
# PostToolUse model tests
# ---------------------------------------------------------------------------


class TestModels:
    def test_read_tool_input_parses_file_path(self) -> None:
        ti = ReadToolInput(file_path="docs/readme.md")
        assert ti.file_path == "docs/readme.md"

    def test_read_tool_input_defaults_to_empty_path(self) -> None:
        ti = ReadToolInput()
        assert ti.file_path == ""

    def test_post_tool_use_hook_input_parses(self) -> None:
        hi = PostToolUseHookInput.model_validate(_make_read_payload("a.md"))
        assert hi.tool_name == "Read"
        assert hi.tool_input.file_path == "a.md"
        assert hi.tool_response == "# Heading\n\nBody text.\n"

    def test_post_tool_use_hook_input_missing_tool_input_raises(self) -> None:
        with pytest.raises(ValidationError):
            PostToolUseHookInput.model_validate({"tool_name": "Read"})

    def test_post_tool_use_hook_input_tool_response_optional(self) -> None:
        hi = PostToolUseHookInput.model_validate(
            {"tool_name": "Read", "tool_input": {"file_path": "a.md"}}
        )
        assert hi.tool_response is None

    def test_post_tool_use_hook_output_shape(self) -> None:
        ho = PostToolUseHookOutput.model_validate(
            {"hookSpecificOutput": {"hookEventName": "PostToolUse"}}
        )
        assert ho.hookSpecificOutput.hookEventName == "PostToolUse"
        assert ho.hookSpecificOutput.updatedToolOutput is None

    def test_post_tool_use_hook_output_accepts_updated_tool_output(self) -> None:
        """Shape used by future QB-007B+ phases — validated now so the
        response model doesn't need to change when compression is added."""
        ho = PostToolUseHookOutput.model_validate(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "updatedToolOutput": "# Heading\n",
                }
            }
        )
        assert ho.hookSpecificOutput.updatedToolOutput == "# Heading\n"


# ---------------------------------------------------------------------------
# run_hook() — response shape and omit-when-unchanged behaviour
# ---------------------------------------------------------------------------


class TestRunHookNoOp:
    def test_response_shape(self) -> None:
        result = _run_hook_with(_make_read_payload())
        assert result["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
        assert set(result.keys()) == {"hookSpecificOutput"}

    def test_updated_tool_output_omitted_for_small_unchanged_content(self) -> None:
        """A small document (well under markdown.toml's token budget) is
        rendered back byte-identical, so updatedToolOutput is correctly
        omitted — not because compression doesn't apply (it does, see
        TestRunHookCompression below), but because there's nothing to
        report for this input."""
        result = _run_hook_with(_make_read_payload())
        assert "updatedToolOutput" not in result["hookSpecificOutput"]

    def test_no_op_when_tool_response_missing(self) -> None:
        payload = {"tool_name": "Read", "tool_input": {"file_path": "a.md"}}
        result = _run_hook_with(payload)
        assert result["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
        assert "updatedToolOutput" not in result["hookSpecificOutput"]

    def test_extra_tool_input_fields_do_not_break_parsing(self) -> None:
        payload = _make_read_payload()
        payload["tool_input"]["some_future_field"] = "value"
        result = _run_hook_with(payload)
        assert result["hookSpecificOutput"]["hookEventName"] == "PostToolUse"

    def test_output_is_valid_json(self) -> None:
        result = _run_hook_with(_make_read_payload())
        assert isinstance(result, dict)

    def test_does_not_regress_to_bare_tool_response_echo(self) -> None:
        """Regression guard mirroring ADR-030's Bash-side equivalent: the
        response must only ever contain hookSpecificOutput at the top level,
        never a top-level echo of tool_name/tool_input/tool_response."""
        result = _run_hook_with(_make_read_payload())
        assert "tool_response" not in result
        assert "tool_input" not in result
        assert "tool_name" not in result
        assert set(result.keys()) == {"hookSpecificOutput"}


# ---------------------------------------------------------------------------
# run_hook() — live compression (QB-007C)
# ---------------------------------------------------------------------------


class TestRunHookCompression:
    def test_large_markdown_content_returns_updated_tool_output(self) -> None:
        """A document large enough to exceed markdown.toml's token budget
        genuinely compresses through the full hook path — not just at the
        FilterRegistry layer (see test_document_filters.py for that), but
        through run_hook()'s stdin -> stdout JSON contract end to end."""
        large_payload = _make_read_payload(
            file_path="notes.md", tool_response="line of filler text. " * 10_000
        )
        result = _run_hook_with(large_payload)
        updated = result["hookSpecificOutput"].get("updatedToolOutput")
        assert isinstance(updated, str)
        assert len(updated) < len(large_payload["tool_response"])

    def test_large_plain_text_content_returns_updated_tool_output(self) -> None:
        large_payload = _make_read_payload(
            file_path="notes.txt", tool_response="line of filler text. " * 10_000
        )
        result = _run_hook_with(large_payload)
        updated = result["hookSpecificOutput"].get("updatedToolOutput")
        assert isinstance(updated, str)
        assert len(updated) < len(large_payload["tool_response"])


# ---------------------------------------------------------------------------
# run_hook() — BOM handling
# ---------------------------------------------------------------------------


_BOM = "﻿"  # U+FEFF, single BOM character


class TestRunHookBom:
    def _run_with_bom_str(self, boms: int, payload: dict) -> dict:
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
        result = self._run_with_bom_str(1, _make_read_payload())
        assert result["hookSpecificOutput"]["hookEventName"] == "PostToolUse"

    def test_doubled_bom_stripped(self) -> None:
        result = self._run_with_bom_str(2, _make_read_payload())
        assert result["hookSpecificOutput"]["hookEventName"] == "PostToolUse"

    def test_no_bom_works(self) -> None:
        result = _run_hook_with(_make_read_payload())
        assert result["hookSpecificOutput"]["hookEventName"] == "PostToolUse"


# ---------------------------------------------------------------------------
# run_hook() — invalid input raises (caught by __main__'s fail-open guard)
# ---------------------------------------------------------------------------


class TestRunHookInvalidInput:
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
            patch.object(sys, "stdin", io.StringIO('{"tool_name": "Read"}')),
            patch.object(sys, "stdout", fake_stdout),
            pytest.raises(ValidationError),
        ):
            run_hook()


# ---------------------------------------------------------------------------
# Hook template constants
# ---------------------------------------------------------------------------


class TestHookTemplate:
    def test_hook_read_command_has_python_placeholder(self) -> None:
        assert "{python}" in HOOK_READ_COMMAND

    def test_hook_read_command_targets_claude_read_adapter(self) -> None:
        assert "hook claude-read" in HOOK_READ_COMMAND

    def test_hook_read_ps1_template_has_python_placeholder(self) -> None:
        assert "{python}" in HOOK_READ_PS1_TEMPLATE

    def test_hook_read_ps1_template_targets_claude_read_adapter(self) -> None:
        assert "hook claude-read" in HOOK_READ_PS1_TEMPLATE

    def test_hook_read_ps1_template_can_be_formatted(self) -> None:
        rendered = HOOK_READ_PS1_TEMPLATE.format(python=r"C:\Python\python.exe", schema_version=1)
        assert r"C:\Python\python.exe" in rendered

    def test_hook_read_ps1_template_has_schema_version_placeholder(self) -> None:
        assert "{schema_version}" in HOOK_READ_PS1_TEMPLATE


# ---------------------------------------------------------------------------
# __main__ routing — end-to-end fail-open through the real dispatch path
# ---------------------------------------------------------------------------


class TestMainRoutingReadAdapter:
    def test_hook_claude_read_routes_to_new_adapter(self) -> None:
        """quor hook claude-read → the real _run_hook() body dispatches to
        quor.adapters.claude_read.run_hook(), not the Bash adapter."""
        from quor.__main__ import _run_hook

        payload = orjson.dumps(_make_read_payload())
        fake_stdin = MagicMock()
        fake_stdin.buffer.read.return_value = payload
        fake_stdout = _FakeStdout()

        with (
            patch("sys.argv", ["quor", "hook", "claude-read"]),
            patch.object(sys, "stdin", fake_stdin),
            patch.object(sys, "stdout", fake_stdout),
        ):
            _run_hook()

        fake_stdout.buffer.seek(0)
        result = orjson.loads(fake_stdout.buffer.read())
        assert result["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
        assert "updatedToolOutput" not in result["hookSpecificOutput"]

    def test_malformed_read_payload_fails_open_to_original_bytes(self) -> None:
        """A payload that fails validation must not raise past __main__ —
        the original stdin bytes are echoed back unchanged, and a warning is
        printed to stderr, exactly like the Bash adapter's fail-open path."""
        from quor.__main__ import _run_hook

        payload = b'{"tool_name": "Read"}'  # missing tool_input -> ValidationError
        fake_stdin = MagicMock()
        fake_stdin.buffer.read.return_value = payload
        fake_stdout = _FakeStdout()
        captured_stderr = io.StringIO()

        with (
            patch("sys.argv", ["quor", "hook", "claude-read"]),
            patch.object(sys, "stdin", fake_stdin),
            patch.object(sys, "stdout", fake_stdout),
            patch.object(sys, "stderr", captured_stderr),
        ):
            _run_hook()

        fake_stdout.buffer.seek(0)
        assert fake_stdout.buffer.read() == payload
        assert "Hook error — returning original" in captured_stderr.getvalue()

    def test_invalid_json_read_payload_fails_open_to_original_bytes(self) -> None:
        from quor.__main__ import _run_hook

        payload = b"not valid json {"
        fake_stdin = MagicMock()
        fake_stdin.buffer.read.return_value = payload
        fake_stdout = _FakeStdout()
        captured_stderr = io.StringIO()

        with (
            patch("sys.argv", ["quor", "hook", "claude-read"]),
            patch.object(sys, "stdin", fake_stdin),
            patch.object(sys, "stdout", fake_stdout),
            patch.object(sys, "stderr", captured_stderr),
        ):
            _run_hook()

        fake_stdout.buffer.seek(0)
        assert fake_stdout.buffer.read() == payload
        assert "Hook error — returning original" in captured_stderr.getvalue()

    def test_unknown_adapter_still_rejected(self) -> None:
        """Adding "claude-read" must not accidentally widen the adapter
        allowlist to arbitrary values."""
        from quor.__main__ import _run_hook

        payload = b'{"tool_name": "Read", "tool_input": {"file_path": "a.md"}}'
        fake_stdin = MagicMock()
        fake_stdin.buffer.read.return_value = payload
        fake_stdout = _FakeStdout()
        captured_stderr = io.StringIO()

        with (
            patch("sys.argv", ["quor", "hook", "claude-reader"]),
            patch.object(sys, "stdin", fake_stdin),
            patch.object(sys, "stdout", fake_stdout),
            patch.object(sys, "stderr", captured_stderr),
        ):
            _run_hook()

        fake_stdout.buffer.seek(0)
        assert fake_stdout.buffer.read() == payload
        assert "Unknown hook adapter: 'claude-reader'" in captured_stderr.getvalue()
