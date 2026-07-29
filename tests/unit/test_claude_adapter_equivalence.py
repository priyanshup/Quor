"""Byte-for-byte equivalence: ClaudeAdapter vs. the pre-QB-035B hooks.

QB-035B's explicit requirement (docs/design/QB-035A-multi-agent-adapter-
design.md §8.2): `ClaudeAdapter.handle_event()` must produce output
byte-identical to what `quor.adapters.claude.run_hook()` /
`quor.adapters.claude_read.run_hook()` already produced, across every case
that matters (rewrite, no-op, BOM handling, Read compression, Read no-op).
This is not a re-test of rewrite/compression *logic* — tests/unit/
test_adapters.py and test_adapters_read.py already own that — it exists
solely to prove the QB-068 refactor changed nothing observable.
"""

from __future__ import annotations

import io
import sys
from typing import Any
from unittest.mock import patch

import orjson
import pytest

from quor.adapters.base import AgentEvent
from quor.adapters.claude_adapter import ClaudeAdapter

_BOM = "﻿"  # U+FEFF


class _FakeStdout:
    def __init__(self) -> None:
        self.buffer = io.BytesIO()

    def write(self, s: str) -> int:
        return 0

    def flush(self) -> None:
        pass


def _run_legacy_bash_hook(raw_text: str) -> bytes:
    from quor.adapters.claude import run_hook

    fake_stdout = _FakeStdout()
    with (
        patch.object(sys, "stdin", io.StringIO(raw_text)),
        patch.object(sys, "stdout", fake_stdout),
    ):
        run_hook()
    fake_stdout.buffer.seek(0)
    return fake_stdout.buffer.read()


def _run_legacy_read_hook(raw_text: str) -> bytes:
    from quor.adapters.claude_read import run_hook

    fake_stdout = _FakeStdout()
    with (
        patch.object(sys, "stdin", io.StringIO(raw_text)),
        patch.object(sys, "stdout", fake_stdout),
    ):
        run_hook(tracking=None)
    fake_stdout.buffer.seek(0)
    return fake_stdout.buffer.read()


def _bash_payload(command: str, **extra: Any) -> dict[str, Any]:
    return {"tool_name": "Bash", "tool_input": {"command": command, **extra}}


def _read_payload(file_path: str, tool_response: str) -> dict[str, Any]:
    return {
        "tool_name": "Read",
        "tool_input": {"file_path": file_path},
        "tool_response": tool_response,
    }


class TestBashCommandInterceptEquivalence:
    @pytest.mark.parametrize(
        "command",
        [
            "git status",
            "cargo build",  # unknown -> no rewrite
            "git status && git diff",  # compound
            "git status --porcelain",  # excluded
            "git commit -m << EOF",  # heredoc -> unchanged
        ],
    )
    def test_matches_legacy_run_hook(self, command: str) -> None:
        payload = _bash_payload(command)
        raw_text = orjson.dumps(payload).decode("utf-8")

        legacy = _run_legacy_bash_hook(raw_text)
        new = ClaudeAdapter().handle_event(
            AgentEvent.COMMAND_INTERCEPT, raw_text.encode("utf-8"), None
        )

        assert new == legacy

    def test_extra_tool_input_fields_preserved_equivalently(self) -> None:
        payload = _bash_payload("git log", description="show history")
        raw_text = orjson.dumps(payload).decode("utf-8")

        legacy = _run_legacy_bash_hook(raw_text)
        new = ClaudeAdapter().handle_event(
            AgentEvent.COMMAND_INTERCEPT, raw_text.encode("utf-8"), None
        )
        assert new == legacy

    @pytest.mark.parametrize("bom_count", [0, 1, 2])
    def test_bom_handling_equivalent(self, bom_count: int) -> None:
        payload = _bash_payload("git diff")
        raw_text = _BOM * bom_count + orjson.dumps(payload).decode("utf-8")

        legacy = _run_legacy_bash_hook(raw_text)
        new = ClaudeAdapter().handle_event(
            AgentEvent.COMMAND_INTERCEPT, raw_text.encode("utf-8"), None
        )
        assert new == legacy


class TestReadContentInterceptEquivalence:
    def test_small_document_no_op_equivalent(self) -> None:
        payload = _read_payload("notes.md", "# Heading\n\nBody text.\n")
        raw_text = orjson.dumps(payload).decode("utf-8")

        legacy = _run_legacy_read_hook(raw_text)
        new = ClaudeAdapter().handle_event(AgentEvent.CONTENT_INTERCEPT, raw_text.encode("utf-8"), None)

        assert new == legacy
        assert b"updatedToolOutput" not in legacy

    def test_oversized_document_compression_equivalent(self) -> None:
        large_doc = "# Title\n\n" + ("Filler prose to exceed the token budget. " * 400)
        payload = _read_payload("example.md", large_doc)
        raw_text = orjson.dumps(payload).decode("utf-8")

        legacy = _run_legacy_read_hook(raw_text)
        new = ClaudeAdapter().handle_event(AgentEvent.CONTENT_INTERCEPT, raw_text.encode("utf-8"), None)

        assert new == legacy
        assert b"updatedToolOutput" in legacy

    def test_unsupported_extension_no_op_equivalent(self) -> None:
        payload = _read_payload("image.png", "binary-ish content")
        raw_text = orjson.dumps(payload).decode("utf-8")

        legacy = _run_legacy_read_hook(raw_text)
        new = ClaudeAdapter().handle_event(AgentEvent.CONTENT_INTERCEPT, raw_text.encode("utf-8"), None)

        assert new == legacy


class TestSupportedEventsCoverBothLegacyHooks:
    def test_command_and_content_intercept_both_supported(self) -> None:
        events = ClaudeAdapter().supported_events
        assert AgentEvent.COMMAND_INTERCEPT in events
        assert AgentEvent.CONTENT_INTERCEPT in events
