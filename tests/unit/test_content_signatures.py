"""Content-signature matching (extends QB-109 content-type routing).

Coverage targets:
  - quor/pipeline/content_type.py — TOML/pytest/mypy detection added to
    detect(), alongside the pre-existing JSON check
  - quor/filters/registry.py      — FilterRegistry.find() routing raw
    content (no command string) to cat-json/cat-toml/pytest/mypy via
    match_content_types, the same mechanism QB-109 proved for git-diff

MCP's compress_context has no command string, only raw content — these
filters can only ever be selected there through content_type.detect(),
never through match_command. Every check below is a real parse (json/toml)
or an exact structural marker (pytest/mypy banners), never a guess.
"""

from __future__ import annotations

from quor.filters.registry import FilterRegistry
from quor.pipeline.content_type import ContentType, detect

# ---------------------------------------------------------------------------
# detect() — new content types
# ---------------------------------------------------------------------------

_JSON_TEXT = '{"name": "quor", "version": "0.4.1"}'

_TOML_TEXT = """\
[project]
name = "quor"
version = "0.4.1"
"""

_TOML_LOCKFILE_TEXT = """\
[[package]]
name = "a"
version = "1.0"

[[package]]
name = "b"
version = "1.0"
"""

_PYTEST_FAILURES_TEXT = """\
============================= test session starts ==============================
collected 3 items

tests/unit/test_foo.py::test_bar PASSED
tests/unit/test_foo.py::test_baz FAILED

=================================== FAILURES ===================================
_________________________ test_baz _________________________
AssertionError: got False
"""

_PYTEST_SHORT_SUMMARY_TEXT = """\
=========================== short test summary info ============================
FAILED tests/unit/test_foo.py::test_baz - AssertionError: got False
1 failed, 1 passed in 0.12s
"""

_PYTEST_TB_NATIVE_TEXT = """\
=================================== FAILURES ===================================
_________________________ test_baz _________________________
Traceback (most recent call last):
  File "tests/unit/test_foo.py", line 10, in test_baz
    assert False
AssertionError
"""

_MYPY_ERRORS_TEXT = """\
src/foo.py:10: error: Incompatible types
Found 1 error in 1 file (checked 5 source files)
"""

_MYPY_MULTI_ERRORS_TEXT = """\
src/a.py:1: error: Missing return
src/b.py:5: error: Argument type
Found 2 errors in 2 files (checked 5 source files)
"""


class TestDetectJson:
    def test_json_object_detected(self) -> None:
        assert detect(_JSON_TEXT) is ContentType.JSON


class TestDetectToml:
    def test_toml_table_detected(self) -> None:
        assert detect(_TOML_TEXT) is ContentType.TOML

    def test_toml_array_of_tables_detected(self) -> None:
        assert detect(_TOML_LOCKFILE_TEXT) is ContentType.TOML

    def test_malformed_toml_falls_open_to_plain_text(self) -> None:
        # Looks TOML-shaped (starts with "name =") but never closes the
        # string — fails the real tomllib.loads() parse and falls through,
        # same fail-open contract as the existing invalid-JSON test.
        result = detect('name = "unterminated\n')
        assert result is not ContentType.TOML

    def test_non_toml_prose_never_attempts_parse(self) -> None:
        # First non-blank line doesn't look like a table header or
        # key = value assignment — the cheap pre-check rejects it before
        # any parse is attempted.
        assert detect("This is just a sentence about TOML files.") is ContentType.PLAIN_TEXT


class TestDetectPytest:
    def test_failures_banner_detected(self) -> None:
        assert detect(_PYTEST_FAILURES_TEXT) is ContentType.PYTEST

    def test_short_test_summary_banner_detected(self) -> None:
        assert detect(_PYTEST_SHORT_SUMMARY_TEXT) is ContentType.PYTEST

    def test_pytest_output_wins_over_embedded_traceback(self) -> None:
        # A --tb=native failure embeds a real "Traceback (most recent call
        # last):" line inside the FAILURES section — pytest's own banner
        # must still win so this doesn't get shadowed by the generic
        # traceback content type (pytest.toml already knows how to strip
        # this exact shape, see its own QB-060 tests).
        assert detect(_PYTEST_TB_NATIVE_TEXT) is ContentType.PYTEST

    def test_plain_traceback_without_pytest_banner_stays_traceback(self) -> None:
        content = (
            "Traceback (most recent call last):\n"
            '  File "app.py", line 1, in <module>\n'
            "ValueError: bad\n"
        )
        assert detect(content) is ContentType.TRACEBACK


class TestDetectMypy:
    def test_error_summary_detected(self) -> None:
        assert detect(_MYPY_ERRORS_TEXT) is ContentType.MYPY

    def test_multi_error_summary_detected(self) -> None:
        assert detect(_MYPY_MULTI_ERRORS_TEXT) is ContentType.MYPY

    def test_clean_run_not_classified_as_mypy(self) -> None:
        # No "Found N errors in M files" line at all — no unambiguous
        # marker exists for a clean run, deliberately left unrouted.
        content = "Success: no issues found in 5 source files"
        assert detect(content) is not ContentType.MYPY


# ---------------------------------------------------------------------------
# FilterRegistry.find() — routing raw content with no command string
# ---------------------------------------------------------------------------


class TestRegistryRoutesBySignature:
    def test_json_content_routes_to_cat_json(self) -> None:
        registry = FilterRegistry(skip_user=True, skip_project=True)
        f = registry.find(_JSON_TEXT)
        assert f is not None
        assert f.name == "cat-json"

    def test_toml_content_routes_to_cat_toml(self) -> None:
        registry = FilterRegistry(skip_user=True, skip_project=True)
        f = registry.find(_TOML_TEXT)
        assert f is not None
        assert f.name == "cat-toml"

    def test_pytest_content_routes_to_pytest_filter(self) -> None:
        registry = FilterRegistry(skip_user=True, skip_project=True)
        f = registry.find(_PYTEST_FAILURES_TEXT)
        assert f is not None
        assert f.name == "pytest"

    def test_mypy_content_routes_to_mypy_filter(self) -> None:
        registry = FilterRegistry(skip_user=True, skip_project=True)
        f = registry.find(_MYPY_ERRORS_TEXT)
        assert f is not None
        assert f.name == "mypy"

    def test_signature_routing_beats_generic_catchall(self) -> None:
        # z_generic's match_command='.' matches any non-empty string,
        # content included — content-type routing must be tried before the
        # match_command loop reaches it, or it would never fire at all.
        registry = FilterRegistry(skip_user=True, skip_project=True)
        for content in (
            _JSON_TEXT,
            _TOML_TEXT,
            _PYTEST_FAILURES_TEXT,
            _MYPY_ERRORS_TEXT,
        ):
            f = registry.find(content)
            assert f is not None
            assert f.name != "generic"

    def test_real_commands_still_resolve_by_match_command(self) -> None:
        # A real shell command string must still resolve via match_command —
        # none of these strings satisfy any of the new content-type checks.
        registry = FilterRegistry(skip_user=True, skip_project=True)
        assert registry.find("cat foo.json").name == "cat-json"  # type: ignore[union-attr]
        assert registry.find("cat foo.toml").name == "cat-toml"  # type: ignore[union-attr]
        assert registry.find("pytest tests/").name == "pytest"  # type: ignore[union-attr]
        assert registry.find("mypy src/").name == "mypy"  # type: ignore[union-attr]

    def test_malformed_or_non_matching_content_falls_back_to_generic(self) -> None:
        # Fail-open: content that looks like it might be structured but
        # isn't valid, or plain content with no matching signature at all,
        # both fall through to the generic catch-all rather than erroring.
        registry = FilterRegistry(skip_user=True, skip_project=True)
        for content in (
            '{"broken": ',
            'name = "unterminated\n',
            "just some plain command output\nwith no signature\n",
        ):
            f = registry.find(content)
            assert f is not None
            assert f.name == "generic"
