"""QB-007C: end-to-end activation of the PostToolUse/Read hook.

QB-007A built the hook plumbing (always a no-op) and QB-007B built the
`markdown`/`document-text` filters (tested only at the FilterRegistry
layer, never reachable from a real Read call). This file exercises the
thing QB-007C actually adds: `quor/adapters/claude_read.py::run_hook()`
genuinely routing supported Read output through those filters and
returning `updatedToolOutput` when compression produces different content
— driven through the real stdin -> stdout JSON contract, not by calling
FilterRegistry directly (see tests/unit/test_document_filters.py for that
layer, and tests/unit/test_adapters_read.py for adapter-level payload/shape
coverage this file assumes already holds).
"""

from __future__ import annotations

import io
import sys
from typing import Any
from unittest.mock import patch

import orjson
import pytest

from quor.adapters.claude_read import run_hook
from quor.filters.registry import FilterRegistry

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


_LARGE_MARKDOWN = (
    "# Design Notes\n\n"
    "REQ-1: must survive compression.\n\n"
    + ("This is an ordinary sentence of filler prose. " * 400)
    + "\n\n# Late Heading\n\nFinal paragraph."
)

_LARGE_TEXT = (
    "REQ-2: must survive compression.\n\n"
    + ("This is an ordinary sentence of filler prose. " * 400)
    + "\n\nFinal paragraph."
)

_SMALL_MARKDOWN = "# Title\n\nJust a short paragraph with nothing special."


# ---------------------------------------------------------------------------
# Supported Read operations return updatedToolOutput
# ---------------------------------------------------------------------------


class TestSupportedTypesCompress:
    @pytest.mark.parametrize(
        "file_path,content",
        [
            ("notes.md", _LARGE_MARKDOWN),
            ("notes.markdown", _LARGE_MARKDOWN),
            ("notes.txt", _LARGE_TEXT),
            ("notes.rst", _LARGE_TEXT),
        ],
    )
    def test_large_supported_document_returns_updated_tool_output(
        self, file_path: str, content: str
    ) -> None:
        result = _run_hook(_read_payload(file_path, content))
        hook_specific = result["hookSpecificOutput"]
        assert hook_specific["hookEventName"] == "PostToolUse"
        updated = hook_specific.get("updatedToolOutput")
        assert isinstance(updated, str)
        assert len(updated) < len(content)

    def test_protected_structure_survives_compression(self) -> None:
        """The whole point of QB-007B's filters: compression shrinks the
        document but never drops a requirement ID or a heading, regardless
        of where it falls in the document."""
        result = _run_hook(_read_payload("notes.md", _LARGE_MARKDOWN))
        updated = result["hookSpecificOutput"]["updatedToolOutput"]
        assert "REQ-1: must survive compression." in updated
        assert "# Late Heading" in updated

    def test_nested_path_still_compresses(self) -> None:
        result = _run_hook(_read_payload("docs/final/DESIGN.md", _LARGE_MARKDOWN))
        assert "updatedToolOutput" in result["hookSpecificOutput"]


# ---------------------------------------------------------------------------
# Unsupported file types pass through unchanged
# ---------------------------------------------------------------------------


class TestUnsupportedTypesPassThrough:
    @pytest.mark.parametrize(
        "file_path",
        ["report.docx", "report.pdf", "script.py", "config.json", "LICENSE", "image.png"],
    )
    def test_unsupported_large_file_never_compresses(self, file_path: str) -> None:
        """Regression guard for the generic-filter scope-leak: FilterRegistry's
        built-in `generic` filter matches *any* non-empty string (including
        every one of these paths), but this adapter must never apply it —
        only quor/filters/builtin/{markdown,document-text}.toml are in
        scope for Read. A large payload is used specifically so that if the
        allowlist regressed, this test would catch real (not just
        theoretical) compression happening."""
        large_content = "line of filler text. " * 10_000
        result = _run_hook(_read_payload(file_path, large_content))
        assert "updatedToolOutput" not in result["hookSpecificOutput"]

    def test_no_extension_passes_through(self) -> None:
        result = _run_hook(_read_payload("Makefile", "line of filler text. " * 10_000))
        assert "updatedToolOutput" not in result["hookSpecificOutput"]


# ---------------------------------------------------------------------------
# Identical content never emits updatedToolOutput
# ---------------------------------------------------------------------------


class TestIdenticalContentOmitsUpdate:
    def test_small_markdown_below_budget_omits_update(self) -> None:
        result = _run_hook(_read_payload("notes.md", _SMALL_MARKDOWN))
        assert "updatedToolOutput" not in result["hookSpecificOutput"]

    def test_small_text_below_budget_omits_update(self) -> None:
        result = _run_hook(_read_payload("notes.txt", "Just a short line of text."))
        assert "updatedToolOutput" not in result["hookSpecificOutput"]

    def test_content_entirely_composed_of_protected_lines_omits_update(self) -> None:
        """Every line matches a preserve_pattern, so nothing is ever
        eligible for max_tokens to compress — rendered output is
        byte-identical even though the filter genuinely ran."""
        content = "REQ-1: first.\nREQ-2: second.\nREQ-3: third.\n"
        result = _run_hook(_read_payload("notes.md", content))
        assert "updatedToolOutput" not in result["hookSpecificOutput"]


# ---------------------------------------------------------------------------
# Fail-open behaviour for pipeline/filter failures
# ---------------------------------------------------------------------------


class TestFailOpenOnFilterFailure:
    def test_registry_apply_exception_omits_update_not_raises(self) -> None:
        """A raising FilterRegistry.apply() must not propagate — the
        adapter's own try/except (not just __main__'s outer guard) catches
        it, so a single bad filter can't take down routing entirely."""
        with (
            patch.object(
                FilterRegistry, "apply", side_effect=RuntimeError("synthetic apply failure")
            ),
            pytest.warns(UserWarning, match="Read filter error"),
        ):
            result = _run_hook(_read_payload("notes.md", _LARGE_MARKDOWN))
        assert "updatedToolOutput" not in result["hookSpecificOutput"]

    def test_registry_find_exception_omits_update_not_raises(self) -> None:
        with (
            patch.object(
                FilterRegistry, "find", side_effect=RuntimeError("synthetic find failure")
            ),
            pytest.warns(UserWarning, match="Read filter error"),
        ):
            result = _run_hook(_read_payload("notes.md", _LARGE_MARKDOWN))
        assert "updatedToolOutput" not in result["hookSpecificOutput"]

    def test_registry_construction_exception_omits_update_not_raises(self) -> None:
        with (
            patch(
                "quor.adapters.claude_read.FilterRegistry",
                side_effect=RuntimeError("synthetic construction failure"),
            ),
            pytest.warns(UserWarning, match="Read filter error"),
        ):
            result = _run_hook(_read_payload("notes.md", _LARGE_MARKDOWN))
        assert "updatedToolOutput" not in result["hookSpecificOutput"]

    def test_stage_level_failure_still_yields_best_effort_output(self) -> None:
        """A single stage raising is already fail-open at the Pipeline
        level (unchanged by QB-007C) — the filter still runs, just with
        that stage skipped, so compression can still legitimately fire."""
        import warnings

        from quor.pipeline.stages import strip_lines as strip_lines_module

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("synthetic stage failure")

        with (
            patch.object(strip_lines_module.StripLinesStage, "apply", _boom),
            warnings.catch_warnings(record=True),
        ):
            warnings.simplefilter("always")
            result = _run_hook(_read_payload("notes.md", _LARGE_MARKDOWN))

        # No exception escaped, and the hook still returned a well-formed
        # response either way (with or without updatedToolOutput depending
        # on whether max_tokens alone was enough to change anything).
        assert result["hookSpecificOutput"]["hookEventName"] == "PostToolUse"


# ---------------------------------------------------------------------------
# Routing precedence / filter selection regressions
# ---------------------------------------------------------------------------


class TestRoutingPrecedenceRegressions:
    def test_md_routes_through_markdown_not_document_text(self) -> None:
        """Markdown-specific structure (ATX heading, fenced code) survives
        only if the `markdown` filter (not `document-text`) actually ran."""
        content = "```python\nvalue = 1\n```\n\n" + (
            "This is an ordinary sentence of filler prose. " * 400
        )
        result = _run_hook(_read_payload("notes.md", content))
        updated = result["hookSpecificOutput"]["updatedToolOutput"]
        assert "```python" in updated

    def test_txt_routes_through_document_text_not_markdown(self) -> None:
        result = _run_hook(_read_payload("notes.txt", _LARGE_TEXT))
        assert "updatedToolOutput" in result["hookSpecificOutput"]

    def test_file_named_like_a_bash_command_still_passes_through(self) -> None:
        """Regression guard: FilterRegistry.find("cat.md") still returns the
        `cat` (Bash) filter first, per QB-007B's documented, accepted
        routing-collision limitation — but the adapter's allowlist means
        this Read is never actually processed by it, unlike a Bash `cat
        cat.md` invocation would be. Confirms the allowlist fix in
        practice, not just in isolation."""
        result = _run_hook(_read_payload("cat.md", _LARGE_MARKDOWN))
        assert "updatedToolOutput" not in result["hookSpecificOutput"]

        # The underlying collision is still real at the FilterRegistry
        # layer — only the adapter's allowlist neutralizes it for Read.
        registry = FilterRegistry(skip_user=True, skip_project=True)
        assert registry.find("cat.md").name == "cat"  # type: ignore[union-attr]

    def test_bash_command_routing_is_never_touched_by_the_read_allowlist(self) -> None:
        """The allowlist lives entirely in claude_read.py — FilterRegistry
        itself, and therefore every Bash filter's own routing, is
        unaffected. Confirms the two concerns stay properly separated."""
        registry = FilterRegistry(skip_user=True, skip_project=True)
        assert registry.find("git status").name == "git-status"  # type: ignore[union-attr]
        assert registry.find("pytest tests/").name == "pytest"  # type: ignore[union-attr]

    def test_pytest_targeting_a_markdown_named_test_file_unaffected(self) -> None:
        """Same anchoring regression QB-007B already covered at the
        FilterRegistry layer — re-asserted here because claude_read.py
        never even reaches this command shape (Read paths only), so this
        also documents *why*: the Bash hook (quor/adapters/claude.py) is a
        completely separate code path, untouched by QB-007C."""
        registry = FilterRegistry(skip_user=True, skip_project=True)
        assert registry.find("pytest tests/test_readme.md").name == "pytest"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Extra tool_input fields / non-string tool_response (defensive coverage)
# ---------------------------------------------------------------------------


class TestDefensivePayloadHandling:
    def test_non_string_tool_response_omits_update(self) -> None:
        payload: dict[str, Any] = {
            "tool_name": "Read",
            "tool_input": {"file_path": "notes.md"},
            "tool_response": {"unexpected": "shape"},
        }
        result = _run_hook(payload)
        assert "updatedToolOutput" not in result["hookSpecificOutput"]

    def test_empty_file_path_omits_update(self) -> None:
        result = _run_hook(_read_payload("", _LARGE_MARKDOWN))
        assert "updatedToolOutput" not in result["hookSpecificOutput"]
