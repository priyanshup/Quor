"""Unit tests for QB-119 (Tool Payload Projection).

json_projector.project_json_text and log_projector.project_log_text are
the two module-level entry points this covers directly. The stage-level
behavior each composes (remove_ansi's strip_inline field, deduplicate_
consecutive's show_count field) has its own dedicated coverage in
test_stages.py; the tests here focus on the projection layer's own
contract: complex nested cleaning, fail-open on malformed input, and that
error/assertion text always survives verbatim.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from quor.pipeline.projection.json_projector import project_json_text
from quor.pipeline.projection.log_projector import project_log_text

# ---------------------------------------------------------------------------
# json_projector: null/empty stripping + array head/tail truncation
# ---------------------------------------------------------------------------

class TestJsonProjector:
    def test_null_and_empty_string_fields_stripped(self) -> None:
        payload = json.dumps({"name": "quor", "note": None, "tag": "", "version": "0.4.1"})
        result = json.loads(project_json_text(payload))
        assert result == {"name": "quor", "version": "0.4.1"}

    def test_nested_objects_cleaned_recursively(self) -> None:
        payload = json.dumps(
            {"user": {"id": 1, "middle_name": None, "email": "a@b.com"}, "empty": ""}
        )
        result = json.loads(project_json_text(payload))
        assert result == {"user": {"id": 1, "email": "a@b.com"}}

    def test_falsy_but_meaningful_values_survive(self) -> None:
        """0, False, and [] are not null/empty-string — must never be stripped."""
        payload = json.dumps({"count": 0, "enabled": False, "items": []})
        result = json.loads(project_json_text(payload))
        assert result == {"count": 0, "enabled": False, "items": []}

    def test_large_array_truncated_to_head_and_tail_with_placeholder(self) -> None:
        payload = json.dumps({"rows": list(range(50))})
        result = json.loads(project_json_text(payload))
        rows = result["rows"]
        assert rows[:3] == [0, 1, 2]
        assert rows[-2:] == [48, 49]
        assert rows[3] == {"__omitted_items__": "45 items hidden"}
        assert len(rows) == 3 + 1 + 2

    def test_small_array_not_truncated(self) -> None:
        payload = json.dumps({"rows": [1, 2, 3, 4]})
        result = json.loads(project_json_text(payload))
        assert result["rows"] == [1, 2, 3, 4]

    def test_array_nulls_cleaned_inside_kept_elements(self) -> None:
        payload = json.dumps([{"a": 1, "b": None}, {"a": 2, "b": None}])
        result = json.loads(project_json_text(payload))
        assert result == [{"a": 1}, {"a": 2}]

    def test_drop_pairs_strips_exact_key_value_match_only(self) -> None:
        payload = json.dumps({"status": "ok", "detail": "status ok but incomplete"})
        result = json.loads(project_json_text(payload, drop_pairs={"status": "ok"}))
        assert result == {"detail": "status ok but incomplete"}

    def test_drop_pairs_none_leaves_status_field_alone(self) -> None:
        payload = json.dumps({"status": "ok"})
        result = json.loads(project_json_text(payload))
        assert result == {"status": "ok"}

    def test_drop_pairs_does_not_match_different_value(self) -> None:
        payload = json.dumps({"status": "degraded"})
        result = json.loads(project_json_text(payload, drop_pairs={"status": "ok"}))
        assert result == {"status": "degraded"}

    def test_malformed_json_returns_original_unchanged(self) -> None:
        broken = '{"broken": '
        assert project_json_text(broken) == broken

    def test_non_json_text_returns_unchanged(self) -> None:
        text = "just a plain log line, not json at all"
        assert project_json_text(text) == text

    def test_empty_string_returns_unchanged(self) -> None:
        assert project_json_text("") == ""

    def test_json_scalar_top_level_passthrough(self) -> None:
        # Doesn't start with '{' or '[' -> cheap-skip path, unchanged.
        assert project_json_text("42") == "42"


# ---------------------------------------------------------------------------
# log_projector: ANSI stripping + repeat collapsing, composed from stages
# ---------------------------------------------------------------------------

class TestLogProjector:
    def test_ansi_codes_stripped_from_pytest_style_output(self) -> None:
        log = "\x1b[32mPASSED\x1b[0m tests/test_a.py::test_one"
        result = project_log_text(log)
        assert result == "PASSED tests/test_a.py::test_one"

    def test_repeated_npm_progress_ticks_collapsed_with_count(self) -> None:
        log = "\n".join(["extracting..."] * 6 + ["done"])
        result = project_log_text(log)
        assert "extracting... (×6)" in result  # noqa: RUF001
        assert result.count("extracting...") == 1  # only the anchor line, not the count suffix
        assert "done" in result

    def test_repeated_pytest_passed_lines_collapsed(self) -> None:
        log = "\n".join(["PASSED tests/test_a.py"] * 4)
        result = project_log_text(log)
        assert result == "PASSED tests/test_a.py (×4)"  # noqa: RUF001

    def test_stack_trace_and_assertion_preserved_verbatim(self) -> None:
        log = (
            "running tests\n"
            "running tests\n"
            "Traceback (most recent call last):\n"
            '  File "app.py", line 10, in <module>\n'
            "    main()\n"
            "AssertionError: expected 200, got 500\n"
        )
        result = project_log_text(log)
        assert "Traceback (most recent call last):" in result
        assert 'File "app.py", line 10, in <module>' in result
        assert "AssertionError: expected 200, got 500" in result
        assert "running tests (×2)" in result  # noqa: RUF001

    def test_non_duplicate_non_ansi_lines_untouched(self) -> None:
        log = "line one\nline two\nline three"
        assert project_log_text(log) == log

    def test_empty_input(self) -> None:
        assert project_log_text("") == ""

    def test_fail_open_on_unexpected_stage_error(self) -> None:
        with patch(
            "quor.pipeline.projection.log_projector._REMOVE_ANSI.apply",
            side_effect=RuntimeError("boom"),
        ):
            original = "some log text"
            assert project_log_text(original) == original
