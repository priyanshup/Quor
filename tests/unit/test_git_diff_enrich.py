"""Unit tests for QB-099A's `quor.pipeline.git_diff_enrich` — the pure
text/command-parsing half of git-diff structural enrichment (no subprocess/
filesystem access; that lives in `quor/plugins/builtin/git_structural_diff.py`
and is covered by `test_git_structural_diff_plugin.py` instead).
"""

from __future__ import annotations

from pathlib import Path

from quor.pipeline.git_diff_enrich import (
    ContentPlan,
    classify_command,
    count_diff_files,
    enrich_git_diff,
    parse_file_section,
    split_diff_sections,
)

_REORDER_DIFF = """diff --git a/foo.py b/foo.py
index 111..222 100644
--- a/foo.py
+++ b/foo.py
@@ -1,8 +1,8 @@
+def b():
+    return 1
+
+
 def a():
     x = 1
     y = 2
     return x + y
-
-
-def b():
-    return 1
"""

_OLD_FOO = "def a():\n    x = 1\n    y = 2\n    return x + y\n\n\ndef b():\n    return 1\n"
_NEW_FOO = "def b():\n    return 1\n\n\ndef a():\n    x = 1\n    y = 2\n    return x + y\n"


class TestSplitDiffSections:
    def test_single_file_diff_is_one_chunk_plus_empty_preamble(self) -> None:
        chunks = split_diff_sections(_REORDER_DIFF)
        assert chunks[0] == ""
        assert chunks[1].startswith("diff --git a/foo.py b/foo.py")

    def test_multi_file_diff_splits_on_each_diff_git_line(self) -> None:
        text = "diff --git a/a.py b/a.py\nx\ndiff --git a/b.py b/b.py\ny\n"
        chunks = split_diff_sections(text)
        assert len(chunks) == 3
        assert chunks[1].startswith("diff --git a/a.py")
        assert chunks[2].startswith("diff --git a/b.py")

    def test_git_show_preamble_before_first_diff_git_line_is_preserved(self) -> None:
        text = "commit abc123\nAuthor: x\n\n    message\n\ndiff --git a/foo.py b/foo.py\nx\n"
        chunks = split_diff_sections(text)
        assert chunks[0].startswith("commit abc123")


class TestCountDiffFiles:
    """QB-093 telemetry prep: how many files a git-diff invocation touches,
    recorded so a future decision on cross-file repeated-edit
    deduplication (QB-093's evidence-gated "idea 2") can be made from real
    usage data — see quor/engine/dispatcher.py's call site."""

    def test_single_file_diff(self) -> None:
        assert count_diff_files(_REORDER_DIFF) == 1

    def test_multi_file_diff(self) -> None:
        text = "diff --git a/a.py b/a.py\nx\ndiff --git a/b.py b/b.py\ny\ndiff --git a/c.py b/c.py\nz\n"
        assert count_diff_files(text) == 3

    def test_no_diff_git_lines_is_zero(self) -> None:
        assert count_diff_files("not a diff at all\njust some text\n") == 0

    def test_git_show_preamble_not_counted_as_a_file(self) -> None:
        text = "commit abc123\nAuthor: x\n\n    message\n\ndiff --git a/foo.py b/foo.py\nx\n"
        assert count_diff_files(text) == 1


class TestParseFileSection:
    def test_ordinary_modified_file(self) -> None:
        section = parse_file_section(split_diff_sections(_REORDER_DIFF)[1])
        assert section is not None
        assert section.old_path == "foo.py"
        assert section.new_path == "foo.py"
        assert not section.is_binary and not section.is_added and not section.is_deleted
        assert section.has_hunks
        assert section.eligible
        assert section.header_text.endswith("+++ b/foo.py")
        assert section.body_text.startswith("@@ -1,8 +1,8 @@")

    def test_non_diff_git_chunk_returns_none(self) -> None:
        assert parse_file_section("not a diff --git line\nsome text") is None

    def test_added_file_is_not_eligible(self) -> None:
        text = (
            "diff --git a/new.py b/new.py\n"
            "new file mode 100644\n"
            "index 0000000..abc123\n"
            "--- /dev/null\n"
            "+++ b/new.py\n"
            "@@ -0,0 +1 @@\n"
            "+x = 1\n"
        )
        section = parse_file_section(text)
        assert section is not None
        assert section.is_added
        assert not section.eligible

    def test_deleted_file_is_not_eligible(self) -> None:
        text = (
            "diff --git a/old.py b/old.py\n"
            "deleted file mode 100644\n"
            "index abc123..0000000\n"
            "--- a/old.py\n"
            "+++ /dev/null\n"
            "@@ -1 +0,0 @@\n"
            "-x = 1\n"
        )
        section = parse_file_section(text)
        assert section is not None
        assert section.is_deleted
        assert not section.eligible

    def test_binary_file_is_not_eligible(self) -> None:
        text = (
            "diff --git a/img.png b/img.png\n"
            "index abc..def 100644\n"
            "Binary files a/img.png and b/img.png differ\n"
        )
        section = parse_file_section(text)
        assert section is not None
        assert section.is_binary
        assert not section.eligible

    def test_non_python_file_is_not_eligible(self) -> None:
        text = (
            "diff --git a/README.md b/README.md\n"
            "index abc..def 100644\n"
            "--- a/README.md\n"
            "+++ b/README.md\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )
        section = parse_file_section(text)
        assert section is not None
        assert not section.eligible

    def test_renamed_file_picks_up_rename_from_to_paths(self) -> None:
        text = (
            "diff --git a/old_name.py b/new_name.py\n"
            "similarity index 92%\n"
            "rename from old_name.py\n"
            "rename to new_name.py\n"
            "index abc..def 100644\n"
            "--- a/old_name.py\n"
            "+++ b/new_name.py\n"
            "@@ -1 +1 @@\n"
            "-x\n"
            "+x\n"
        )
        section = parse_file_section(text)
        assert section is not None
        assert section.old_path == "old_name.py"
        assert section.new_path == "new_name.py"

    def test_file_with_no_hunks_is_not_eligible(self) -> None:
        # e.g. a pure mode change, or a rename with no content change beyond
        # what `similarity index 100%` already implies.
        text = "diff --git a/foo.py b/foo.py\nold mode 100644\nnew mode 100755\n"
        section = parse_file_section(text)
        assert section is not None
        assert not section.has_hunks
        assert not section.eligible


class TestClassifyCommand:
    def test_bare_git_diff_is_index_vs_working_tree(self) -> None:
        assert classify_command("git diff") == ContentPlan(old_ref="", new_ref=None)

    def test_git_diff_staged_is_head_vs_index(self) -> None:
        assert classify_command("git diff --staged") == ContentPlan(old_ref="HEAD", new_ref="")
        assert classify_command("git diff --cached") == ContentPlan(old_ref="HEAD", new_ref="")

    def test_git_diff_one_revision_is_revision_vs_working_tree(self) -> None:
        assert classify_command("git diff HEAD~1") == ContentPlan(old_ref="HEAD~1", new_ref=None)

    def test_git_diff_two_revisions(self) -> None:
        assert classify_command("git diff abc123 def456") == ContentPlan(old_ref="abc123", new_ref="def456")

    def test_git_show_one_revision_is_parent_vs_revision(self) -> None:
        assert classify_command("git show abc123") == ContentPlan(old_ref="abc123^", new_ref="abc123")

    def test_trailing_path_args_are_ignored(self) -> None:
        assert classify_command("git diff -- foo.py bar.py") == ContentPlan(old_ref="", new_ref=None)

    def test_range_syntax_is_unrecognized(self) -> None:
        assert classify_command("git diff main..feature") is None
        assert classify_command("git diff main...feature") is None

    def test_more_than_two_revisions_is_unrecognized(self) -> None:
        assert classify_command("git diff a b c") is None

    def test_unknown_flag_is_unrecognized(self) -> None:
        assert classify_command("git diff --ignore-all-space") is None

    def test_git_diff_staged_with_a_revision_is_unrecognized(self) -> None:
        assert classify_command("git diff --staged HEAD~1") is None

    def test_non_git_command_is_unrecognized(self) -> None:
        assert classify_command("npm test") is None

    def test_git_log_is_unrecognized(self) -> None:
        assert classify_command("git log -p") is None

    def test_git_show_range_is_unrecognized(self) -> None:
        assert classify_command("git show main..feature") is None


def _stub_fetchers(old_content: str | None, new_content: str | None):
    def git_show(ref: str, path: str, cwd: Path) -> str | None:
        return old_content

    def read_working_tree(path: str, cwd: Path) -> str | None:
        return new_content

    return git_show, read_working_tree


class TestEnrichGitDiff:
    def test_unrecognized_command_returns_diff_text_unchanged(self) -> None:
        git_show, read_wt = _stub_fetchers(_OLD_FOO, _NEW_FOO)
        out = enrich_git_diff("git diff main..feature", _REORDER_DIFF, Path("."), git_show=git_show, read_working_tree=read_wt)
        assert out == _REORDER_DIFF

    def test_eligible_python_file_is_rewritten(self) -> None:
        git_show, read_wt = _stub_fetchers(_OLD_FOO, _NEW_FOO)
        out = enrich_git_diff("git diff", _REORDER_DIFF, Path("."), git_show=git_show, read_working_tree=read_wt)
        assert "reordered: a" in out
        assert "reordered: b" in out
        assert "+def b():" not in out  # the original hunk body is gone

    def test_fetch_failure_falls_back_to_original_chunk(self) -> None:
        git_show, read_wt = _stub_fetchers(None, _NEW_FOO)  # old content unavailable
        out = enrich_git_diff("git diff", _REORDER_DIFF, Path("."), git_show=git_show, read_working_tree=read_wt)
        assert out == _REORDER_DIFF

    def test_unparseable_new_content_falls_back_to_original_chunk(self) -> None:
        git_show, read_wt = _stub_fetchers(_OLD_FOO, "def a(:\n")
        out = enrich_git_diff("git diff", _REORDER_DIFF, Path("."), git_show=git_show, read_working_tree=read_wt)
        assert out == _REORDER_DIFF

    def test_oversized_file_falls_back_to_original_chunk(self) -> None:
        huge = "x = 1\n" * 100_000  # well over the 256 KiB safety cap
        git_show, read_wt = _stub_fetchers(huge, huge)
        out = enrich_git_diff("git diff", _REORDER_DIFF, Path("."), git_show=git_show, read_working_tree=read_wt)
        assert out == _REORDER_DIFF

    def test_git_show_exception_falls_back_to_original_chunk(self) -> None:
        def raising_git_show(ref: str, path: str, cwd: Path) -> str | None:
            raise OSError("boom")

        _, read_wt = _stub_fetchers(_OLD_FOO, _NEW_FOO)
        out = enrich_git_diff("git diff", _REORDER_DIFF, Path("."), git_show=raising_git_show, read_working_tree=read_wt)
        assert out == _REORDER_DIFF

    def test_multi_file_diff_only_rewrites_the_eligible_python_file(self) -> None:
        text = (
            _REORDER_DIFF
            + "diff --git a/README.md b/README.md\n"
            + "index abc..def 100644\n"
            + "--- a/README.md\n"
            + "+++ b/README.md\n"
            + "@@ -1 +1 @@\n"
            + "-old\n"
            + "+new\n"
        )
        git_show, read_wt = _stub_fetchers(_OLD_FOO, _NEW_FOO)
        out = enrich_git_diff("git diff", text, Path("."), git_show=git_show, read_working_tree=read_wt)
        assert "reordered: a" in out
        assert "-old\n+new" in out  # README.md's own hunk is untouched

    def test_nothing_eligible_round_trips_byte_for_byte(self) -> None:
        text = (
            "diff --git a/README.md b/README.md\n"
            "index abc..def 100644\n"
            "--- a/README.md\n"
            "+++ b/README.md\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )
        git_show, read_wt = _stub_fetchers(_OLD_FOO, _NEW_FOO)
        out = enrich_git_diff("git diff", text, Path("."), git_show=git_show, read_working_tree=read_wt)
        assert out == text

    def test_determinism_same_input_produces_identical_output(self) -> None:
        git_show, read_wt = _stub_fetchers(_OLD_FOO, _NEW_FOO)
        first = enrich_git_diff("git diff", _REORDER_DIFF, Path("."), git_show=git_show, read_working_tree=read_wt)
        second = enrich_git_diff("git diff", _REORDER_DIFF, Path("."), git_show=git_show, read_working_tree=read_wt)
        assert first == second
