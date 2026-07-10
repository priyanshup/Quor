"""QB-005F: end-to-end AST-aware Read-hook compression.

QB-005B-E built the AST summarization pipeline (Python/JavaScript/
TypeScript/TSX analyzers, the `code_ast_summarize` stage, and the
`cat-python`/`cat-javascript`/`cat-typescript`/`cat-tsx` filters) but none of
it was reachable from a real Read call: those filters' `match_command`
patterns all require a literal `cat `-prefixed command string, which a bare
Read `file_path` can never match via `FilterRegistry.find()`. This file
exercises what QB-005F adds — `quor/adapters/claude_read.py` routing
`.py`/`.js`/`.jsx`/`.mjs`/`.cjs`/`.ts`/`.tsx` Read calls to those filters by
name (`_SOURCE_CODE_FILTER_NAMES_BY_EXTENSION`) — driven through the real
stdin -> stdout JSON contract, mirroring
tests/unit/test_read_hook_activation.py's QB-007C pattern exactly. See
tests/unit/test_ast_summarize.py for analyzer-level coverage this file
assumes already holds, and tests/unit/test_tracking.py's `TestReadTracking`
for the tracking-side proof (`filter_name`/`was_passthrough`/token counts).
"""

from __future__ import annotations

import builtins
import io
import sys
import warnings
from typing import Any
from unittest.mock import patch

import orjson
import pytest

from quor.adapters.claude_read import run_hook

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeStdout:
    def __init__(self) -> None:
        self.buffer: io.BytesIO = io.BytesIO()

    def write(self, s: str) -> int:
        return 0

    def flush(self) -> None:
        pass


def _read_payload(file_path: str, tool_response: str) -> dict:
    return {
        "tool_name": "Read",
        "tool_input": {"file_path": file_path},
        "tool_response": tool_response,
    }


def _run_hook(payload: dict) -> dict:
    raw = orjson.dumps(payload).decode("utf-8")
    fake_stdout = _FakeStdout()
    with (
        patch.object(sys, "stdin", io.StringIO(raw)),
        patch.object(sys, "stdout", fake_stdout),
    ):
        run_hook()
    fake_stdout.buffer.seek(0)
    return orjson.loads(fake_stdout.buffer.read())


# Real, representative source samples — each has a module-level
# constant/import (must survive) and at least one function/class whose BODY
# contains a distinctive marker line that must NOT survive, while its
# signature/docstring/JSDoc must.

_PYTHON_SOURCE = '''import os

DEFAULT_TIMEOUT = 30


def fetch_data(url, timeout=DEFAULT_TIMEOUT):
    """Fetch data from a URL."""
    response = make_request(url, timeout=timeout)
    if response.status_code != 200:
        raise RuntimeError("bad response")
    return response.json()


class Client:
    def __init__(self, base_url):
        self.base_url = base_url

    def get(self, path):
        full_url = self.base_url + path
        return fetch_data(full_url)
'''

_JAVASCRIPT_SOURCE = """export const DEFAULT_TIMEOUT = 30;

/**
 * Fetch data from a URL.
 */
function fetchData(url, timeout = DEFAULT_TIMEOUT) {
  const response = makeRequest(url, timeout);
  if (!response.ok) {
    throw new Error("bad response");
  }
  return response.json();
}

class Client {
  constructor(baseUrl) {
    this.baseUrl = baseUrl;
  }

  get(path) {
    const fullUrl = this.baseUrl + path;
    return fetchData(fullUrl);
  }
}
"""

_TYPESCRIPT_SOURCE = """export interface ClientOptions {
  baseUrl: string;
  timeout?: number;
}

export function createClient(options: ClientOptions): Client {
  const timeout = options.timeout ?? DEFAULT_TIMEOUT;
  const client = new Client(options.baseUrl, timeout);
  return client;
}

export class Client {
  private readonly baseUrl: string;

  constructor(baseUrl: string, private readonly timeout: number) {
    this.baseUrl = baseUrl;
  }

  async get(path: string): Promise<unknown> {
    const fullUrl = this.baseUrl + path;
    const response = await fetch(fullUrl);
    return response.json();
  }
}
"""

_TSX_SOURCE = """import React from "react";

interface ButtonProps {
  label: string;
  onClick: () => void;
}

export function Button({ label, onClick }: ButtonProps) {
  const handleClick = () => {
    console.log("button clicked");
    onClick();
  };

  return <button onClick={handleClick}>{label}</button>;
}
"""


# ---------------------------------------------------------------------------
# Source-code Read compression, one language at a time
# ---------------------------------------------------------------------------


class TestSourceCodeCompresses:
    def test_python_read_compresses_function_bodies(self) -> None:
        result = _run_hook(_read_payload("app.py", _PYTHON_SOURCE))
        updated = result["hookSpecificOutput"].get("updatedToolOutput")
        assert isinstance(updated, str)
        assert len(updated) < len(_PYTHON_SOURCE)
        assert "import os" in updated
        assert "DEFAULT_TIMEOUT = 30" in updated
        assert "def fetch_data(url, timeout=DEFAULT_TIMEOUT):" in updated
        assert "Fetch data from a URL." in updated
        assert "class Client:" in updated
        assert "def get(self, path):" in updated
        assert "response = make_request(url, timeout=timeout)" not in updated
        assert "full_url = self.base_url + path" not in updated

    def test_javascript_read_compresses_function_bodies(self) -> None:
        result = _run_hook(_read_payload("app.js", _JAVASCRIPT_SOURCE))
        updated = result["hookSpecificOutput"].get("updatedToolOutput")
        assert isinstance(updated, str)
        assert len(updated) < len(_JAVASCRIPT_SOURCE)
        assert "export const DEFAULT_TIMEOUT = 30;" in updated
        assert "function fetchData(url, timeout = DEFAULT_TIMEOUT) {" in updated
        assert "Fetch data from a URL." in updated
        assert "class Client {" in updated
        assert "const response = makeRequest(url, timeout);" not in updated
        assert "const fullUrl = this.baseUrl + path;" not in updated

    def test_typescript_read_compresses_function_bodies(self) -> None:
        result = _run_hook(_read_payload("app.ts", _TYPESCRIPT_SOURCE))
        updated = result["hookSpecificOutput"].get("updatedToolOutput")
        assert isinstance(updated, str)
        assert len(updated) < len(_TYPESCRIPT_SOURCE)
        assert "export interface ClientOptions {" in updated
        assert "export function createClient(options: ClientOptions): Client {" in updated
        assert "async get(path: string): Promise<unknown> {" in updated
        assert "const client = new Client(options.baseUrl, timeout);" not in updated
        assert "const response = await fetch(fullUrl);" not in updated

    def test_tsx_read_compresses_function_bodies(self) -> None:
        result = _run_hook(_read_payload("Button.tsx", _TSX_SOURCE))
        updated = result["hookSpecificOutput"].get("updatedToolOutput")
        assert isinstance(updated, str)
        assert len(updated) < len(_TSX_SOURCE)
        assert "interface ButtonProps {" in updated
        assert "export function Button({ label, onClick }: ButtonProps) {" in updated
        assert 'console.log("button clicked");' not in updated

    @pytest.mark.parametrize("ext", ["jsx", "mjs", "cjs"])
    def test_javascript_variant_extensions_route_to_cat_javascript(self, ext: str) -> None:
        """.jsx/.mjs/.cjs all share cat-javascript, exactly as that filter's
        own match_command already treats them as one language for the Bash
        (`cat foo.mjs`) path — this proves the Read-hook mapping agrees."""
        result = _run_hook(_read_payload(f"app.{ext}", _JAVASCRIPT_SOURCE))
        updated = result["hookSpecificOutput"].get("updatedToolOutput")
        assert isinstance(updated, str)
        assert "const response = makeRequest(url, timeout);" not in updated


# ---------------------------------------------------------------------------
# Unsupported extensions are unaffected by the new source-code mapping
# ---------------------------------------------------------------------------


class TestUnsupportedSourceExtensionsPassThrough:
    @pytest.mark.parametrize("file_path", ["config.json", "main.rs", "Cargo.toml", "style.css"])
    def test_non_mapped_extension_never_compresses(self, file_path: str) -> None:
        large_content = "line of filler text. " * 10_000
        result = _run_hook(_read_payload(file_path, large_content))
        assert "updatedToolOutput" not in result["hookSpecificOutput"]


# ---------------------------------------------------------------------------
# Fail-open: malformed source never raises past the hook
# ---------------------------------------------------------------------------


class TestMalformedSourceFailsOpen:
    def test_invalid_python_syntax_does_not_raise(self) -> None:
        """Mirrors cat-python.toml's own inline filter test: analyze_python()
        lets SyntaxError propagate to Pipeline.execute()'s per-stage
        fail-open (ADR-018) — the AST stage is skipped, the rest of the
        filter (strip_lines/dedup/max_tokens) still runs, and the hook
        still returns a well-formed response either way."""
        broken = "def broken(:\n    pass\n\n" + ("x = 1\n" * 50)
        result = _run_hook(_read_payload("broken.py", broken))
        assert result["hookSpecificOutput"]["hookEventName"] == "PostToolUse"

    def test_malformed_javascript_does_not_raise(self) -> None:
        """tree-sitter is error-recovering (unlike Python's ast.parse) —
        malformed JS produces ERROR/MISSING nodes rather than raising, and
        any function overlapping one is excluded from compression by
        construction (QB-005C's ERROR-node rule), not by exception."""
        broken = "function broken( {\n  return 1;\n}\n\nfunction ok() {\n  return 2;\n}\n"
        result = _run_hook(_read_payload("broken.js", broken))
        assert result["hookSpecificOutput"]["hookEventName"] == "PostToolUse"

    def test_malformed_typescript_does_not_raise(self) -> None:
        broken = "function broken(: number {\n  return 1;\n}\n"
        result = _run_hook(_read_payload("broken.ts", broken))
        assert result["hookSpecificOutput"]["hookEventName"] == "PostToolUse"


# ---------------------------------------------------------------------------
# Fail-open: missing optional tree-sitter dependency never raises past the
# hook — mirrors test_ast_summarize.py's TestAnalyzeJavaScript's own
# missing-dependency test, but proven end to end through the real Read
# stdin -> stdout contract instead of calling analyze_javascript() directly.
# ---------------------------------------------------------------------------


class TestMissingDependencyFailsOpen:
    def test_missing_tree_sitter_javascript_falls_back_to_unchanged(self) -> None:
        real_import = builtins.__import__

        def _blocked(name: str, *args: object, **kwargs: object) -> object:
            if name in ("tree_sitter", "tree_sitter_javascript"):
                raise ImportError(f"simulated missing dependency: {name}")
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        with (
            patch("builtins.__import__", side_effect=_blocked),
            warnings.catch_warnings(),
        ):
            warnings.simplefilter("ignore")
            result = _run_hook(_read_payload("app.js", _JAVASCRIPT_SOURCE))

        # No exception escaped, and the response is still well-formed. The
        # AST stage contributes nothing (empty compress set), but strip_lines
        # may still legitimately change something — this test only asserts
        # the fail-open contract, not a specific compression outcome.
        assert result["hookSpecificOutput"]["hookEventName"] == "PostToolUse"


# ---------------------------------------------------------------------------
# Fail-open: a raising FilterRegistry layer still never propagates past the
# source-code path — mirrors TestFailOpenOnFilterFailure in
# test_read_hook_activation.py for the markdown/document-text path.
# ---------------------------------------------------------------------------


class TestSourceCodeRegistryFailureFailsOpen:
    def test_registry_apply_exception_omits_update_not_raises(self) -> None:
        from quor.filters.registry import FilterRegistry

        with (
            patch.object(
                FilterRegistry, "apply", side_effect=RuntimeError("synthetic apply failure")
            ),
            pytest.warns(UserWarning, match="Read filter error"),
        ):
            result = _run_hook(_read_payload("app.py", _PYTHON_SOURCE))
        assert "updatedToolOutput" not in result["hookSpecificOutput"]

    def test_registry_construction_exception_omits_update_not_raises(self) -> None:
        with (
            patch(
                "quor.adapters.claude_read.FilterRegistry",
                side_effect=RuntimeError("synthetic construction failure"),
            ),
            pytest.warns(UserWarning, match="Read filter error"),
        ):
            result = _run_hook(_read_payload("app.py", _PYTHON_SOURCE))
        assert "updatedToolOutput" not in result["hookSpecificOutput"]


# ---------------------------------------------------------------------------
# Defensive payload handling, mirroring TestDefensivePayloadHandling in
# test_adapters_read.py
# ---------------------------------------------------------------------------


class TestSourceCodeDefensivePayloadHandling:
    def test_non_string_tool_response_omits_update(self) -> None:
        payload: dict[str, Any] = {
            "tool_name": "Read",
            "tool_input": {"file_path": "app.py"},
            "tool_response": {"unexpected": "shape"},
        }
        result = _run_hook(payload)
        assert "updatedToolOutput" not in result["hookSpecificOutput"]
