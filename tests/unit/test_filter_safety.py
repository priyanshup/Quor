"""Error-safety snapshot tests for all built-in filters.

Highest-priority test class: a silently dropped error line is worse than a crash.
For each built-in filter we supply REAL failing-tool output and assert that
error-relevant lines are NEVER marked Decision.COMPRESS in the final ContentMask.

Testing approach:
  - registry.trace() returns the full ContentMask with per-line decisions, without
    honoring abort_unless/abort_if (so we always see stage decisions).
  - registry.apply() produces the rendered output (honors abort_unless/abort_if).
  - We check both: mask decisions (are error lines PROTECT/KEEP, not COMPRESS?)
    and rendered output (do critical strings survive to the AI?).
"""

from __future__ import annotations

from quor.filters.registry import FilterRegistry
from quor.pipeline.engine import PipelineResult
from quor.pipeline.mask import Decision

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _builtin_only() -> FilterRegistry:
    """Registry with only built-in filters — no user or project tier."""
    return FilterRegistry(skip_user=True, skip_project=True)


def _decisions_for(result: PipelineResult, fragment: str) -> set[Decision]:
    """Return the set of Decision values for all lines that contain `fragment`."""
    return {lm.decision for lm in result.mask.lines if fragment in lm.line}


def _trace(command: str, content: str) -> PipelineResult:
    registry = _builtin_only()
    fc = registry.find(command)
    assert fc is not None, f"No built-in filter matched {command!r}"
    return registry.trace(fc, content)


def _apply(command: str, content: str) -> str:
    registry = _builtin_only()
    fc = registry.find(command)
    assert fc is not None, f"No built-in filter matched {command!r}"
    return registry.apply(fc, content)


# ---------------------------------------------------------------------------
# pytest filter
# ---------------------------------------------------------------------------


class TestPytestFilterSafety:
    """Real failing pytest run — two distinct failure types."""

    # Matches pytest verbose mode: "PASSED …" / "FAILED …" prefixes.
    FAILING_OUTPUT = """\
============================= test session starts ==============================
platform linux -- Python 3.11.0, pytest-7.4.0, pluggy-1.0.0
rootdir: /workspace/myapp
plugins: cov-5.0.0
collecting ...
collected 3 items

PASSED tests/test_auth.py::test_login
FAILED tests/test_auth.py::test_logout
FAILED tests/test_payment.py::test_charge

================================= FAILURES ====================================
_____________________________ test_logout _____________________________________

    def test_logout():
>       assert client.logout() is True
E       AssertionError: assert False is True

tests/test_auth.py:25: AssertionError
_____________________________ test_charge _____________________________________

    def test_charge():
>       amount = process_payment(-999)
E       ValueError: negative amounts not allowed

tests/test_payment.py:12: ValueError
=========================== short test summary info ============================
FAILED tests/test_auth.py::test_logout - AssertionError: assert False is True
FAILED tests/test_payment.py::test_charge - ValueError: negative amounts not allowed
2 failed, 1 passed in 0.32s
"""

    def test_failed_lines_are_not_compress(self) -> None:
        result = _trace("pytest", self.FAILING_OUTPUT)
        decisions = _decisions_for(result, "FAILED")
        assert decisions, "Expected lines containing 'FAILED' in the mask"
        assert Decision.COMPRESS not in decisions, (
            "Lines with 'FAILED' must never be COMPRESS — silently dropping a "
            "failure indicator is worse than a crash (ADR-018, ADR-009)"
        )

    def test_assertion_error_lines_are_not_compress(self) -> None:
        result = _trace("pytest", self.FAILING_OUTPUT)
        decisions = _decisions_for(result, "AssertionError")
        assert decisions
        assert Decision.COMPRESS not in decisions

    def test_value_error_lines_are_not_compress(self) -> None:
        # ValueError contains 'Error' — matches preserve_pattern 'Error'
        result = _trace("pytest", self.FAILING_OUTPUT)
        decisions = _decisions_for(result, "ValueError")
        assert decisions
        assert Decision.COMPRESS not in decisions

    def test_session_header_is_compressed(self) -> None:
        result = _trace("pytest", self.FAILING_OUTPUT)
        decisions = _decisions_for(result, "test session starts")
        assert decisions
        assert Decision.COMPRESS in decisions

    def test_collecting_line_is_compressed(self) -> None:
        result = _trace("pytest", self.FAILING_OUTPUT)
        decisions = _decisions_for(result, "collecting")
        assert decisions
        assert Decision.COMPRESS in decisions

    def test_passed_lines_are_compressed(self) -> None:
        # "PASSED tests/test_auth.py::…" starts with PASSED — stripped
        result = _trace("pytest", self.FAILING_OUTPUT)
        decisions = _decisions_for(result, "PASSED tests/")
        assert decisions
        assert Decision.COMPRESS in decisions

    def test_rendered_output_preserves_all_failures(self) -> None:
        rendered = _apply("pytest", self.FAILING_OUTPUT)
        assert "FAILED" in rendered, "FAILED marker must survive filtering"
        assert "AssertionError" in rendered
        assert "ValueError" in rendered
        # Noise must be stripped
        assert "test session starts" not in rendered
        assert "collecting" not in rendered
        assert "PASSED tests/" not in rendered

    def test_error_protect_decisions_are_set(self) -> None:
        """Lines matching preserve_patterns must be Decision.PROTECT, not just KEEP."""
        result = _trace("pytest", self.FAILING_OUTPUT)
        # "FAILED" is explicitly in preserve_patterns → PROTECT
        failed_decisions = _decisions_for(result, "FAILED tests/test_auth")
        assert Decision.PROTECT in failed_decisions


# ---------------------------------------------------------------------------
# git-status filter (merge conflict scenario)
# ---------------------------------------------------------------------------


class TestGitStatusFilterSafety:
    """Real git status output during a merge conflict."""

    CONFLICT_OUTPUT = """\
On branch feature/user-auth
Your branch is up to date with 'origin/feature/user-auth'.

Unmerged paths:
  (use "git restore --staged <file>..." to unstage)
  (use "git add <file>..." to mark resolution)
\tboth modified:   src/auth/service.py
\tboth modified:   src/models/user.py

CONFLICT (content): Merge conflict in src/auth/service.py
CONFLICT (content): Merge conflict in src/models/user.py
Automatic merge failed; fix conflicts and then commit the result.
"""

    def test_conflict_lines_are_not_compress(self) -> None:
        result = _trace("git status", self.CONFLICT_OUTPUT)
        decisions = _decisions_for(result, "CONFLICT")
        assert decisions
        assert Decision.COMPRESS not in decisions

    def test_conflict_lines_are_protected(self) -> None:
        result = _trace("git status", self.CONFLICT_OUTPUT)
        decisions = _decisions_for(result, "CONFLICT (content)")
        assert Decision.PROTECT in decisions

    def test_modified_lines_are_not_compress(self) -> None:
        result = _trace("git status", self.CONFLICT_OUTPUT)
        decisions = _decisions_for(result, "modified:")
        assert decisions
        assert Decision.COMPRESS not in decisions

    def test_unmerged_header_is_not_compress(self) -> None:
        result = _trace("git status", self.CONFLICT_OUTPUT)
        decisions = _decisions_for(result, "Unmerged")
        assert decisions
        assert Decision.COMPRESS not in decisions

    def test_on_branch_noise_is_compressed(self) -> None:
        result = _trace("git status", self.CONFLICT_OUTPUT)
        decisions = _decisions_for(result, "On branch")
        assert decisions
        assert Decision.COMPRESS in decisions

    def test_hint_lines_are_compressed(self) -> None:
        result = _trace("git status", self.CONFLICT_OUTPUT)
        decisions = _decisions_for(result, 'use "git')
        assert decisions
        assert Decision.COMPRESS in decisions

    def test_rendered_output_preserves_conflicts(self) -> None:
        rendered = _apply("git status", self.CONFLICT_OUTPUT)
        assert "CONFLICT" in rendered
        assert "modified:" in rendered
        assert "On branch" not in rendered


# ---------------------------------------------------------------------------
# git-diff filter (diff content scenario)
# ---------------------------------------------------------------------------


class TestGitDiffFilterSafety:
    """Real git diff with additions, removals, and hunk headers."""

    DIFF_OUTPUT = (
        "diff --git a/src/auth.py b/src/auth.py\n"
        "index a1b2c3d..d4e5f6a 100644\n"
        "--- a/src/auth.py\n"
        "+++ b/src/auth.py\n"
        "@@ -15,6 +15,9 @@ class AuthService:\n"
        " def validate_token(self, token: str) -> bool:\n"
        "-    return self._cache.get(token) is not None\n"
        "+    if token is None:\n"
        "+        raise ValueError(\"token must not be None\")\n"
        "+    return self._cache.get(token) is not None\n"
        " \n"
        "diff --git a/src/models.py b/src/models.py\n"
        "index b2c3d4e..e5f6a7b 100644\n"
        "--- a/src/models.py\n"
        "+++ b/src/models.py\n"
        "@@ -1,3 +1,4 @@\n"
        "+from datetime import datetime\n"
        " class User:\n"
        "-    pass\n"
        "+    created_at: datetime\n"
    )

    def test_added_lines_are_not_compress(self) -> None:
        result = _trace("git diff", self.DIFF_OUTPUT)
        # Lines starting with '+' but not '+++'
        added = [
            lm for lm in result.mask.lines
            if lm.line.startswith("+") and not lm.line.startswith("+++")
        ]
        assert added, "Expected addition lines in the diff mask"
        decisions = {lm.decision for lm in added}
        assert Decision.COMPRESS not in decisions

    def test_removed_lines_are_not_compress(self) -> None:
        result = _trace("git diff", self.DIFF_OUTPUT)
        # Lines starting with '-' but not '---'
        removed = [
            lm for lm in result.mask.lines
            if lm.line.startswith("-") and not lm.line.startswith("---")
        ]
        assert removed, "Expected removal lines in the diff mask"
        decisions = {lm.decision for lm in removed}
        assert Decision.COMPRESS not in decisions

    def test_hunk_headers_are_not_compress(self) -> None:
        result = _trace("git diff", self.DIFF_OUTPUT)
        decisions = _decisions_for(result, "@@")
        assert decisions
        assert Decision.COMPRESS not in decisions

    def test_index_lines_are_compressed(self) -> None:
        result = _trace("git diff", self.DIFF_OUTPUT)
        decisions = _decisions_for(result, "index ")
        assert decisions
        assert Decision.COMPRESS in decisions

    def test_diff_header_lines_are_compressed(self) -> None:
        result = _trace("git diff", self.DIFF_OUTPUT)
        decisions = _decisions_for(result, "diff --git")
        assert decisions
        assert Decision.COMPRESS in decisions

    def test_rendered_output_preserves_diff_content(self) -> None:
        rendered = _apply("git diff", self.DIFF_OUTPUT)
        assert "+    if token is None:" in rendered
        assert "-    return self._cache.get(token) is not None" in rendered
        assert "@@" in rendered
        assert "index a1b2c3d" not in rendered
        assert "diff --git" not in rendered


# ---------------------------------------------------------------------------
# mypy filter
# ---------------------------------------------------------------------------


class TestMypyFilterSafety:
    """Real mypy type error output with multiple files and a note."""

    FAILING_OUTPUT = (
        'src/models/user.py:45: error: Incompatible types in assignment '
        '(expression has type "str", variable has type "int")\n'
        'src/api/routes.py:12: error: Argument 1 to "process" has incompatible type '
        '"Optional[str]"; expected "str"  [arg-type]\n'
        "src/utils/helpers.py:89: note: See https://mypy.readthedocs.io for help\n"
        "Found 2 errors in 2 files (checked 8 source files)\n"
    )

    def test_error_lines_are_not_compress(self) -> None:
        result = _trace("mypy .", self.FAILING_OUTPUT)
        decisions = _decisions_for(result, "error:")
        assert decisions
        assert Decision.COMPRESS not in decisions

    def test_error_lines_are_protected(self) -> None:
        result = _trace("mypy .", self.FAILING_OUTPUT)
        decisions = _decisions_for(result, "error:")
        assert Decision.PROTECT in decisions

    def test_note_lines_are_not_compress(self) -> None:
        result = _trace("mypy .", self.FAILING_OUTPUT)
        decisions = _decisions_for(result, "note:")
        assert decisions
        assert Decision.COMPRESS not in decisions

    def test_found_n_errors_summary_is_compressed(self) -> None:
        # "Found 2 errors…" matches strip pattern '^Found \d+ error' → COMPRESS
        # BUT it does NOT match any preserve pattern (no 'error:' colon, no 'Error' capital)
        result = _trace("mypy .", self.FAILING_OUTPUT)
        decisions = _decisions_for(result, "Found 2 errors")
        assert decisions
        assert Decision.COMPRESS in decisions

    def test_rendered_output_preserves_error_locations(self) -> None:
        rendered = _apply("mypy .", self.FAILING_OUTPUT)
        assert "src/models/user.py:45: error:" in rendered
        assert "src/api/routes.py:12: error:" in rendered
        assert "note:" in rendered
        assert "Found 2 errors" not in rendered


# ---------------------------------------------------------------------------
# ruff filter
# ---------------------------------------------------------------------------


class TestRuffFilterSafety:
    """Real ruff check output with multiple violation codes."""

    FAILING_OUTPUT = (
        "src/app.py:1:1: E501 Line too long (110 > 88 characters)\n"
        "src/app.py:15:5: F401 `os.path` imported but unused\n"
        "src/models.py:3:1: E302 Expected 2 blank lines, got 1\n"
        "[*] 2 fixable with the `--fix` option.\n"
        "Found 3 errors.\n"
    )

    def test_violation_lines_are_not_compress(self) -> None:
        result = _trace("ruff check .", self.FAILING_OUTPUT)
        for code in ("E501", "F401", "E302"):
            decisions = _decisions_for(result, code)
            assert decisions, f"Expected lines containing violation code {code!r}"
            assert Decision.COMPRESS not in decisions, (
                f"Violation {code!r} lines must never be COMPRESS"
            )

    def test_fixable_annotation_is_compressed(self) -> None:
        result = _trace("ruff check .", self.FAILING_OUTPUT)
        decisions = _decisions_for(result, "fixable")
        assert decisions
        assert Decision.COMPRESS in decisions

    def test_found_errors_summary_is_compressed(self) -> None:
        result = _trace("ruff check .", self.FAILING_OUTPUT)
        decisions = _decisions_for(result, "Found 3 errors")
        assert decisions
        assert Decision.COMPRESS in decisions

    def test_rendered_output_preserves_all_violation_codes(self) -> None:
        rendered = _apply("ruff check .", self.FAILING_OUTPUT)
        assert "E501" in rendered
        assert "F401" in rendered
        assert "E302" in rendered
        assert "Found 3 errors" not in rendered
        assert "fixable" not in rendered


# ---------------------------------------------------------------------------
# cat filter
# ---------------------------------------------------------------------------


class TestCatFilterSafety:
    """File display with shebang, actionable comments, and regular comments."""

    SOURCE_OUTPUT = (
        "#!/usr/bin/env python3\n"
        "# This is a regular comment about module setup\n"
        "# TODO: fix the authentication bypass bug before release\n"
        "# FIXME: this function raises RuntimeError on empty input\n"
        "import os\n"
        "import sys\n"
        "\n"
        "def broken_function(data):\n"
        "    # internal implementation note\n"
        "    if not data:\n"
        "        raise RuntimeError('data must not be empty')\n"
        "    return os.path.join(sys.prefix, data)\n"
    )

    def test_shebang_is_not_compress(self) -> None:
        result = _trace("cat setup.py", self.SOURCE_OUTPUT)
        decisions = _decisions_for(result, "#!/usr/bin/env")
        assert decisions
        assert Decision.COMPRESS not in decisions

    def test_shebang_is_protected(self) -> None:
        result = _trace("cat setup.py", self.SOURCE_OUTPUT)
        decisions = _decisions_for(result, "#!/usr/bin/env")
        assert Decision.PROTECT in decisions

    def test_todo_comment_is_not_compress(self) -> None:
        result = _trace("cat setup.py", self.SOURCE_OUTPUT)
        decisions = _decisions_for(result, "TODO")
        assert decisions
        assert Decision.COMPRESS not in decisions

    def test_fixme_comment_is_not_compress(self) -> None:
        result = _trace("cat setup.py", self.SOURCE_OUTPUT)
        decisions = _decisions_for(result, "FIXME")
        assert decisions
        assert Decision.COMPRESS not in decisions

    def test_regular_comments_are_compressed(self) -> None:
        result = _trace("cat setup.py", self.SOURCE_OUTPUT)
        # "# This is a regular comment about module setup" — no actionable marker
        decisions = _decisions_for(result, "regular comment about module setup")
        assert decisions
        assert Decision.COMPRESS in decisions

    def test_internal_note_is_compressed(self) -> None:
        result = _trace("cat setup.py", self.SOURCE_OUTPUT)
        decisions = _decisions_for(result, "internal implementation note")
        assert decisions
        assert Decision.COMPRESS in decisions

    def test_rendered_output_preserves_actionable_comments(self) -> None:
        rendered = _apply("cat setup.py", self.SOURCE_OUTPUT)
        assert "#!/usr/bin/env python3" in rendered
        assert "TODO" in rendered
        assert "FIXME" in rendered
        assert "import os" in rendered
        # Regular comments stripped
        assert "regular comment about module setup" not in rendered
        assert "internal implementation note" not in rendered


# ---------------------------------------------------------------------------
# generic fallback filter (passthrough safety)
# ---------------------------------------------------------------------------


class TestGenericFilterSafety:
    """Generic catch-all filter: error content must pass through, ANSI noise stripped."""

    _ANSI_ONLY = "\x1b[0m\x1b[1m"

    @property
    def ERROR_OUTPUT(self) -> str:
        a = self._ANSI_ONLY
        return (
            f"{a}\n"
            "Error: Connection timeout after 30s (host=db.prod:5432)\n"
            "Traceback (most recent call last):\n"
            "  File 'app.py', line 42, in connect\n"
            "    cursor = conn.cursor()\n"
            "ConnectionError: database connection refused\n"
            f"{a}\n"
        )

    def test_error_lines_are_not_compress(self) -> None:
        result = _trace("some-tool --run", self.ERROR_OUTPUT)
        for fragment in ("Error: Connection", "Traceback", "ConnectionError"):
            decisions = _decisions_for(result, fragment)
            assert decisions, f"Expected lines with {fragment!r} in mask"
            assert Decision.COMPRESS not in decisions, (
                f"Generic filter must not compress {fragment!r} — no suppress rules defined"
            )

    def test_ansi_only_lines_are_compressed(self) -> None:
        a = self._ANSI_ONLY
        result = _trace("some-tool --run", self.ERROR_OUTPUT)
        ansi_decisions = {
            lm.decision for lm in result.mask.lines if lm.line == a
        }
        assert ansi_decisions, "Expected ANSI-only lines in mask"
        assert Decision.COMPRESS in ansi_decisions

    def test_rendered_output_contains_all_error_content(self) -> None:
        rendered = _apply("some-tool --run", self.ERROR_OUTPUT)
        assert "Error: Connection timeout after 30s" in rendered
        assert "Traceback (most recent call last):" in rendered
        assert "ConnectionError: database connection refused" in rendered


# ---------------------------------------------------------------------------
# eslint filter (QB-006B — tool-aware routing through npm/npx/pnpm/yarn)
# ---------------------------------------------------------------------------


class TestEslintFilterSafety:
    """Real ESLint stylish-formatter output, reached via npx/pnpm/yarn
    wrapper routing: violations, rule names, and problem summaries must
    never be compressed."""

    VIOLATIONS_OUTPUT = (
        "/Users/dev/project/src/index.js\n"
        "  12:5   error    'foo' is defined but never used  no-unused-vars\n"
        "  34:10  warning  Missing return type on function  @typescript-eslint/explicit-function-return-type\n"
        "\n"
        "/Users/dev/project/src/utils.js\n"
        "  5:1  error  Unexpected console statement  no-console\n"
        "\n"
        "✖ 3 problems (2 errors, 1 warning)\n"
        "  1 error and 0 warnings potentially fixable with the `--fix` option.\n"
    )

    IDENTICAL_REPEATED_OUTPUT = (
        "/src/a.js\n"
        "  1:1  error  Missing semicolon  semi\n"
        "  1:1  error  Missing semicolon  semi\n"
        "  1:1  error  Missing semicolon  semi\n"
        "\n"
        "✖ 3 problems (3 errors, 0 warnings)\n"
    )

    DIFFERENT_LINE_NUMBERS_OUTPUT = (
        "/src/a.js\n"
        "  1:1  error  Missing semicolon  semi\n"
        "  2:1  error  Missing semicolon  semi\n"
        "  3:1  error  Missing semicolon  semi\n"
        "\n"
        "✖ 3 problems (3 errors, 0 warnings)\n"
    )

    DIFFERENT_RULE_NAMES_OUTPUT = (
        "/src/a.js\n"
        "  1:1  error  Missing semicolon  semi\n"
        "  1:2  error  Unexpected console statement  no-console\n"
        "  1:3  error  'foo' is defined but never used  no-unused-vars\n"
        "\n"
        "✖ 3 problems (3 errors, 0 warnings)\n"
    )

    DIFFERENT_FILE_PATHS_OUTPUT = (
        "/src/a.js\n"
        "  1:1  error  Missing semicolon  semi\n"
        "\n"
        "/src/b.js\n"
        "  1:1  error  Missing semicolon  semi\n"
        "\n"
        "✖ 2 problems (2 errors, 0 warnings)\n"
    )

    PARSE_ERROR_OUTPUT = (
        "/src/broken.js\n"
        "  0:0  error  Parsing error: Unexpected token }\n"
        "\n"
        "✖ 1 problem (1 error, 0 warnings)\n"
    )

    def test_rule_violations_are_not_compress(self) -> None:
        result = _trace("npx eslint .", self.VIOLATIONS_OUTPUT)
        for fragment in ("no-unused-vars", "no-console", "explicit-function-return-type"):
            decisions = _decisions_for(result, fragment)
            assert decisions, f"Expected line with {fragment!r} in mask"
            assert Decision.COMPRESS not in decisions

    def test_problem_summary_is_not_compress(self) -> None:
        result = _trace("npx eslint .", self.VIOLATIONS_OUTPUT)
        decisions = _decisions_for(result, "✖ 3 problems")
        assert decisions
        assert Decision.COMPRESS not in decisions

    def test_fixable_note_is_not_compress(self) -> None:
        result = _trace("npx eslint .", self.VIOLATIONS_OUTPUT)
        decisions = _decisions_for(result, "potentially fixable")
        assert decisions
        assert Decision.COMPRESS not in decisions

    def test_rendered_output_via_pnpm_exec_preserves_all_violations(self) -> None:
        rendered = _apply("pnpm exec eslint .", self.VIOLATIONS_OUTPUT)
        assert "no-unused-vars" in rendered
        assert "no-console" in rendered
        assert "✖ 3 problems (2 errors, 1 warning)" in rendered

    def test_rendered_output_via_yarn_bare_shorthand_preserves_all_violations(self) -> None:
        rendered = _apply("yarn eslint .", self.VIOLATIONS_OUTPUT)
        assert "no-unused-vars" in rendered
        assert "✖ 3 problems (2 errors, 1 warning)" in rendered

    def test_identical_repeated_messages_collapse(self) -> None:
        """QB-006B follow-up: exact_match=true — only byte-identical repeated
        diagnostics collapse."""
        result = _trace("npx eslint .", self.IDENTICAL_REPEATED_OUTPUT)
        decisions = _decisions_for(result, "Missing semicolon")
        # One instance survives (as KEEP or, once strip_lines' preserve_patterns
        # upgrades it for containing "error", PROTECT) and the rest compress.
        assert Decision.COMPRESS in decisions
        assert decisions - {Decision.COMPRESS}, "Expected at least one surviving instance"
        rendered = _apply("npx eslint .", self.IDENTICAL_REPEATED_OUTPUT)
        assert "(×3)" in rendered  # noqa: RUF001
        assert rendered.count("1:1") == 1
        assert "✖ 3 problems (3 errors, 0 warnings)" in rendered

    def test_different_line_numbers_do_not_collapse(self) -> None:
        """Same rule/message, different location — must NOT merge, unlike
        mypy's group_repeated config which intentionally does merge this case."""
        result = _trace("npx eslint .", self.DIFFERENT_LINE_NUMBERS_OUTPUT)
        for fragment in ("1:1", "2:1", "3:1"):
            decisions = _decisions_for(result, fragment)
            assert decisions, f"Expected line {fragment!r} in mask"
            assert Decision.COMPRESS not in decisions
        rendered = _apply("npx eslint .", self.DIFFERENT_LINE_NUMBERS_OUTPUT)
        assert "(×" not in rendered  # noqa: RUF001

    def test_different_rule_names_do_not_collapse(self) -> None:
        result = _trace("npx eslint .", self.DIFFERENT_RULE_NAMES_OUTPUT)
        for fragment in ("semi", "no-console", "no-unused-vars"):
            decisions = _decisions_for(result, fragment)
            assert decisions, f"Expected rule {fragment!r} in mask"
            assert Decision.COMPRESS not in decisions
        rendered = _apply("npx eslint .", self.DIFFERENT_RULE_NAMES_OUTPUT)
        assert "(×" not in rendered  # noqa: RUF001

    def test_different_file_paths_do_not_collapse(self) -> None:
        """Two files with the textually-identical violation on their own
        first line — the file-path header naturally breaks the run, so this
        must never merge across files."""
        result = _trace("npx eslint .", self.DIFFERENT_FILE_PATHS_OUTPUT)
        decisions = _decisions_for(result, "Missing semicolon")
        assert decisions
        assert Decision.COMPRESS not in decisions
        rendered = _apply("npx eslint .", self.DIFFERENT_FILE_PATHS_OUTPUT)
        assert rendered.count("Missing semicolon") == 2
        assert "/src/a.js" in rendered
        assert "/src/b.js" in rendered
        assert "(×" not in rendered  # noqa: RUF001

    def test_parse_error_never_compressed(self) -> None:
        result = _trace("npx eslint .", self.PARSE_ERROR_OUTPUT)
        decisions = _decisions_for(result, "Parsing error: Unexpected token }")
        assert decisions
        assert Decision.COMPRESS not in decisions

    def test_clean_run_with_no_issues_passes_through_unchanged(self) -> None:
        """Real ESLint prints nothing at all on a clean run (unlike mypy's
        'Success: no issues found' sentinel) — abort_unless must no-op."""
        rendered = _apply("npx eslint .", "")
        assert rendered == ""


# ---------------------------------------------------------------------------
# npm filter (QB-006A)
# ---------------------------------------------------------------------------


class TestNpmFilterSafety:
    """Real npm install/audit output: noise stripped, findings/errors survive."""

    INSTALL_OUTPUT = (
        "npm warn deprecated inflight@1.0.6: This module is not supported, and leaks memory.\n"
        "npm warn deprecated glob@7.2.3: Glob versions prior to v9 are no longer supported\n"
        "npm warn deprecated rimraf@2.7.1: Rimraf versions prior to v4 are no longer supported\n"
        "\n"
        "added 152 packages, and audited 153 packages in 4s\n"
        "\n"
        "23 packages are looking for funding\n"
        "  run `npm fund` for details\n"
        "\n"
        "3 vulnerabilities (1 moderate, 2 high)\n"
        "\n"
        "To address all issues, run:\n"
        "  npm audit fix\n"
        "\n"
        "Run `npm audit` for details.\n"
    )

    FAILURE_OUTPUT = (
        "npm warn deprecated foo@1.0.0: old\n"
        "npm ERR! code ENOENT\n"
        "npm ERR! syscall open\n"
        "npm ERR! path /some/path/package.json\n"
        "npm ERR! errno -4058\n"
        "npm ERR! enoent Could not read package.json\n"
    )

    def test_deprecation_spam_collapses_but_stays_visible(self) -> None:
        result = _trace("npm install", self.INSTALL_OUTPUT)
        decisions = _decisions_for(result, "deprecated inflight")
        assert decisions
        assert Decision.COMPRESS not in decisions

    def test_repeated_deprecation_lines_are_compressed(self) -> None:
        result = _trace("npm install", self.INSTALL_OUTPUT)
        decisions = _decisions_for(result, "glob@7.2.3")
        assert decisions
        assert Decision.COMPRESS in decisions

    def test_package_count_summary_is_not_compress(self) -> None:
        result = _trace("npm install", self.INSTALL_OUTPUT)
        decisions = _decisions_for(result, "added 152 packages")
        assert decisions
        assert Decision.COMPRESS not in decisions

    def test_vulnerability_summary_is_not_compress(self) -> None:
        result = _trace("npm install", self.INSTALL_OUTPUT)
        decisions = _decisions_for(result, "3 vulnerabilities")
        assert decisions
        assert Decision.COMPRESS not in decisions

    def test_funding_nag_is_compressed(self) -> None:
        result = _trace("npm install", self.INSTALL_OUTPUT)
        decisions = _decisions_for(result, "looking for funding")
        assert decisions
        assert Decision.COMPRESS in decisions

    def test_audit_cta_is_compressed(self) -> None:
        result = _trace("npm install", self.INSTALL_OUTPUT)
        decisions = _decisions_for(result, "To address all issues")
        assert decisions
        assert Decision.COMPRESS in decisions

    def test_rendered_output_preserves_actionable_summary(self) -> None:
        rendered = _apply("npm install", self.INSTALL_OUTPUT)
        assert "added 152 packages, and audited 153 packages in 4s" in rendered
        assert "3 vulnerabilities (1 moderate, 2 high)" in rendered
        assert "looking for funding" not in rendered
        assert "To address all issues" not in rendered

    def test_npm_err_lines_never_compressed(self) -> None:
        result = _trace("npm ci", self.FAILURE_OUTPUT)
        for fragment in ("code ENOENT", "syscall open", "errno -4058", "enoent Could not read"):
            decisions = _decisions_for(result, fragment)
            assert decisions, f"Expected 'npm ERR!' line with {fragment!r} in mask"
            assert Decision.COMPRESS not in decisions, f"npm ERR! line {fragment!r} must survive"

    def test_rendered_failure_output_contains_all_npm_err_lines(self) -> None:
        rendered = _apply("npm ci", self.FAILURE_OUTPUT)
        assert "npm ERR! code ENOENT" in rendered
        assert "npm ERR! errno -4058" in rendered


# ---------------------------------------------------------------------------
# npx filter (QB-006A)
# ---------------------------------------------------------------------------


class TestNpxFilterSafety:
    """npx's own resolution preamble is generic noise; the wrapped tool's
    output (whatever it is) must pass through untouched — no tool-aware
    routing for what runs underneath npx."""

    AUTO_INSTALL_OUTPUT = (
        "npm warn exec The following package was not found and will be installed: cowsay@1.6.0\n"
        " ______\n"
        "< moo >\n"
        " ------\n"
    )

    WRAPPED_FAILURE_OUTPUT = (
        "npm warn exec The following package was not found and will be installed: foo@1.0.0\n"
        "Error: something broke\n"
        "    at Object.<anonymous> (/tmp/foo/index.js:3:7)\n"
    )

    def test_resolution_preamble_is_compressed(self) -> None:
        result = _trace("npx cowsay hello", self.AUTO_INSTALL_OUTPUT)
        decisions = _decisions_for(result, "npm warn exec")
        assert decisions
        assert Decision.COMPRESS in decisions

    def test_wrapped_tool_output_is_not_compress(self) -> None:
        result = _trace("npx cowsay hello", self.AUTO_INSTALL_OUTPUT)
        decisions = _decisions_for(result, "moo")
        assert decisions
        assert Decision.COMPRESS not in decisions

    def test_wrapped_tool_error_is_not_compress(self) -> None:
        result = _trace("npx foo", self.WRAPPED_FAILURE_OUTPUT)
        for fragment in ("Error: something broke", "Object.<anonymous>"):
            decisions = _decisions_for(result, fragment)
            assert decisions, f"Expected line with {fragment!r} in mask"
            assert Decision.COMPRESS not in decisions

    def test_rendered_output_drops_only_resolution_preamble(self) -> None:
        rendered = _apply("npx foo", self.WRAPPED_FAILURE_OUTPUT)
        assert "Error: something broke" in rendered
        assert "Object.<anonymous>" in rendered
        assert "npm warn exec" not in rendered


# ---------------------------------------------------------------------------
# pnpm filter (QB-006A)
# ---------------------------------------------------------------------------


class TestPnpmFilterSafety:
    """Real pnpm install output: progress ticks stripped, package delta and
    structured fetch errors survive."""

    INSTALL_OUTPUT = (
        "Lockfile is up to date, resolution step is skipped\n"
        "Progress: resolved 1, reused 0, downloaded 0, added 0\n"
        "Progress: resolved 80, reused 40, downloaded 5, added 80\n"
        "Progress: resolved 152, reused 140, downloaded 12, added 152, done\n"
        "Packages: +152\n"
        "++++++++++++++++++++++++++++++++++++++++++++++++++++\n"
        "\n"
        "dependencies:\n"
        "+ express 4.18.2\n"
        "\n"
        "Done in 3.2s\n"
    )

    FETCH_FAILURE_OUTPUT = (
        "Progress: resolved 10, reused 0, downloaded 0\n"
        "ERR_PNPM_FETCH_404  GET https://registry.npmjs.org/nonexistent-pkg: Not Found - 404\n"
        "This error happened while installing a direct dependency of the project\n"
    )

    def test_progress_ticks_are_compressed(self) -> None:
        result = _trace("pnpm install", self.INSTALL_OUTPUT)
        decisions = _decisions_for(result, "Progress: resolved")
        assert decisions
        assert Decision.COMPRESS in decisions

    def test_progress_bar_line_is_compressed(self) -> None:
        result = _trace("pnpm install", self.INSTALL_OUTPUT)
        plus_lines = [lm for lm in result.mask.lines if lm.line.strip("+") == "" and lm.line.strip()]
        assert plus_lines, "Expected a plus-sign progress bar line in mask"
        assert all(lm.decision is Decision.COMPRESS for lm in plus_lines)

    def test_package_delta_summary_is_not_compress(self) -> None:
        result = _trace("pnpm install", self.INSTALL_OUTPUT)
        decisions = _decisions_for(result, "Packages: +152")
        assert decisions
        assert Decision.COMPRESS not in decisions

    def test_done_summary_is_not_compress(self) -> None:
        result = _trace("pnpm install", self.INSTALL_OUTPUT)
        decisions = _decisions_for(result, "Done in 3.2s")
        assert decisions
        assert Decision.COMPRESS not in decisions

    def test_rendered_output_preserves_actionable_summary(self) -> None:
        rendered = _apply("pnpm install", self.INSTALL_OUTPUT)
        assert "Packages: +152" in rendered
        assert "+ express 4.18.2" in rendered
        assert "Done in 3.2s" in rendered
        assert "Lockfile is up to date" not in rendered
        assert "Progress: resolved" not in rendered

    def test_structured_fetch_error_never_compressed(self) -> None:
        result = _trace("pnpm add nonexistent-pkg", self.FETCH_FAILURE_OUTPUT)
        for fragment in ("ERR_PNPM_FETCH_404", "direct dependency of the project"):
            decisions = _decisions_for(result, fragment)
            assert decisions, f"Expected line with {fragment!r} in mask"
            assert Decision.COMPRESS not in decisions


# ---------------------------------------------------------------------------
# yarn filter (QB-006A)
# ---------------------------------------------------------------------------


class TestYarnFilterSafety:
    """Real yarn classic install output: step banners stripped, success/
    timing summary and command failures survive."""

    INSTALL_OUTPUT = (
        "yarn install v1.22.19\n"
        "[1/4] Resolving packages...\n"
        "[2/4] Fetching packages...\n"
        "[3/4] Linking dependencies...\n"
        "[4/4] Building fresh packages...\n"
        "success Saved lockfile.\n"
        "Done in 12.34s.\n"
    )

    PEER_WARNING_OUTPUT = (
        'warning "eslint > file-entry-cache@6.0.1" has unmet peer dependency "flat-cache@^3.0.4".\n'
        'warning "eslint > table@6.8.1" has unmet peer dependency "ajv@^8.0.1".\n'
        'warning "jest > jest-cli@29.0.0" has unmet peer dependency "node-notifier@^10.0.0".\n'
        "error Command failed with exit code 1.\n"
    )

    def test_step_banners_are_compressed(self) -> None:
        result = _trace("yarn install", self.INSTALL_OUTPUT)
        for fragment in ("Resolving packages", "Fetching packages", "Linking dependencies"):
            decisions = _decisions_for(result, fragment)
            assert decisions
            assert Decision.COMPRESS in decisions

    def test_success_confirmation_is_not_compress(self) -> None:
        result = _trace("yarn install", self.INSTALL_OUTPUT)
        decisions = _decisions_for(result, "success Saved lockfile")
        assert decisions
        assert Decision.COMPRESS not in decisions

    def test_done_summary_is_not_compress(self) -> None:
        result = _trace("yarn install", self.INSTALL_OUTPUT)
        decisions = _decisions_for(result, "Done in 12.34s")
        assert decisions
        assert Decision.COMPRESS not in decisions

    def test_repeated_peer_warnings_collapse_but_first_stays_visible(self) -> None:
        result = _trace("yarn install", self.PEER_WARNING_OUTPUT)
        decisions = _decisions_for(result, "file-entry-cache")
        assert decisions
        assert Decision.COMPRESS not in decisions

    def test_command_failure_never_compressed(self) -> None:
        result = _trace("yarn build", self.PEER_WARNING_OUTPUT)
        decisions = _decisions_for(result, "error Command failed with exit code 1")
        assert decisions
        assert Decision.COMPRESS not in decisions

    def test_rendered_output_preserves_success_and_failure_summaries(self) -> None:
        rendered = _apply("yarn install", self.INSTALL_OUTPUT)
        assert "success Saved lockfile." in rendered
        assert "Done in 12.34s." in rendered
        assert "Resolving packages" not in rendered
