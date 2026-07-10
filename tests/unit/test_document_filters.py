"""QB-007B: Markdown/plain-text document filters (markdown.toml, document-text.toml).

Reuses FilterRegistry exactly as the Bash filters do (see
tests/unit/test_node_tool_routing.py for the equivalent Bash-routing
coverage this file mirrors in spirit) — `match_command` is matched against a
bare file path string instead of a shell command string. No new routing
system, no new stage types; these tests exercise the existing
`FilterRegistry.find()`/`.apply()` surface directly.

QB-007B ships the filter layer only. These filters are not yet wired into
the live PostToolUse/Read hook (quor/adapters/claude_read.py, QB-007A, still
always omits updatedToolOutput) — there is deliberately no hook-level test
here; that wiring is a later phase.
"""

from __future__ import annotations

import pytest

from quor.filters.registry import FilterRegistry


def _builtin_only() -> FilterRegistry:
    return FilterRegistry(skip_user=True, skip_project=True)


def _matched_filter_name(path: str) -> str | None:
    fc = _builtin_only().find(path)
    return fc.name if fc else None


def _apply(path: str, content: str) -> str:
    registry = _builtin_only()
    fc = registry.find(path)
    assert fc is not None, f"no filter matched {path!r}"
    return registry.apply(fc, content)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


class TestMarkdownRouting:
    def test_md_extension_routes_to_markdown(self) -> None:
        assert _matched_filter_name("notes.md") == "markdown"

    def test_markdown_extension_routes_to_markdown(self) -> None:
        assert _matched_filter_name("notes.markdown") == "markdown"

    def test_nested_path_routes_to_markdown(self) -> None:
        assert _matched_filter_name("docs/final/PROJECT_BIBLE.md") == "markdown"

    def test_windows_style_path_routes_to_markdown(self) -> None:
        assert _matched_filter_name(r"C:\Users\me\notes.md") == "markdown"

    def test_case_insensitive_extension_routes_to_markdown(self) -> None:
        assert _matched_filter_name("NOTES.MD") == "markdown"
        assert _matched_filter_name("notes.Markdown") == "markdown"


class TestPlainTextRouting:
    def test_txt_extension_routes_to_document_text(self) -> None:
        assert _matched_filter_name("notes.txt") == "document-text"

    def test_rst_extension_routes_to_document_text(self) -> None:
        assert _matched_filter_name("CONTRIBUTING.rst") == "document-text"

    def test_case_insensitive_extension_routes_to_document_text(self) -> None:
        assert _matched_filter_name("NOTES.TXT") == "document-text"


class TestUnmatchedFiles:
    """Files with no dedicated document filter fall through to the built-in
    generic filter — the same established fallback behavior every unmatched
    Bash command already gets (COMMAND_SUPPORT.md §3), not a new mechanism."""

    def test_python_file_falls_through_to_generic(self) -> None:
        assert _matched_filter_name("script.py") == "generic"

    def test_json_file_falls_through_to_generic(self) -> None:
        assert _matched_filter_name("config.json") == "generic"

    def test_no_extension_falls_through_to_generic(self) -> None:
        assert _matched_filter_name("LICENSE") == "generic"

    def test_docx_file_not_matched_by_document_filters(self) -> None:
        """QB-007B is plain-text formats only — DOCX is explicitly out of
        scope (QB-007D) and must not accidentally match either new filter."""
        name = _matched_filter_name("report.docx")
        assert name not in ("markdown", "document-text")
        assert name == "generic"

    def test_pdf_file_not_matched_by_document_filters(self) -> None:
        name = _matched_filter_name("report.pdf")
        assert name not in ("markdown", "document-text")
        assert name == "generic"


class TestPathWithSpacesFallsThrough:
    """Documented trade-off (see markdown.toml's header comment): the
    routing pattern is anchored to a single whitespace-free token so it can
    never accidentally intercept a real shell command. A file path
    containing a space does not match and safely falls through, rather than
    being silently mishandled."""

    def test_path_with_space_does_not_match_markdown(self) -> None:
        assert _matched_filter_name("My Documents/notes.md") == "generic"

    def test_path_with_space_does_not_match_document_text(self) -> None:
        assert _matched_filter_name("My Documents/notes.txt") == "generic"


class TestKnownRoutingCollision:
    """Documented, accepted edge case (see markdown.toml's header comment):
    FilterRegistry is shared between Bash command strings and Read file
    paths, and built-in load order is alphabetical, so a markdown file
    literally named to look like an existing command (e.g. "cat.md") can be
    intercepted by that command's filter first. This is inherent to reusing
    match_command/FilterRegistry rather than inventing a parallel routing
    system (explicitly out of scope) — this test documents the actual
    current behavior as a regression guard, not an endorsement."""

    def test_file_named_cat_md_collides_with_cat_filter(self) -> None:
        assert _matched_filter_name("cat.md") == "cat"


class TestBashRoutingUnaffected:
    """Regression guard: adding two new built-in filters must not change
    routing for any existing Bash command — same principle as
    test_node_tool_routing.py's own regression coverage."""

    def test_git_status_still_routes_to_git_status(self) -> None:
        assert _matched_filter_name("git status") == "git-status"

    def test_pytest_still_routes_to_pytest(self) -> None:
        assert _matched_filter_name("pytest tests/") == "pytest"

    def test_pytest_targeting_a_markdown_named_test_file_still_routes_to_pytest(self) -> None:
        """The exact collision this filter's anchoring was designed to
        prevent (see markdown.toml's header comment): a real command string
        that merely references a .md file as an argument must never be
        intercepted by the markdown filter."""
        assert _matched_filter_name("pytest tests/test_readme.md") == "pytest"

    def test_cat_of_a_markdown_file_still_routes_to_cat(self) -> None:
        assert _matched_filter_name("cat notes.md") == "cat"


# ---------------------------------------------------------------------------
# Structure preservation — markdown
# ---------------------------------------------------------------------------


class TestMarkdownStructurePreservation:
    def test_atx_headings_preserved(self) -> None:
        content = "# Title\n\nBody text.\n\n## Subheading\n\nMore body text."
        out = _apply("notes.md", content)
        assert "# Title" in out
        assert "## Subheading" in out

    def test_bullet_list_preserved(self) -> None:
        content = "Intro.\n\n- one\n- two\n- three"
        out = _apply("notes.md", content)
        assert "- one" in out
        assert "- two" in out
        assert "- three" in out

    def test_numbered_list_preserved(self) -> None:
        content = "Intro.\n\n1. first\n2. second\n3. third"
        out = _apply("notes.md", content)
        assert "1. first" in out
        assert "3. third" in out

    def test_requirement_id_preserved(self) -> None:
        content = "Some prose.\n\nREQ-042: must handle the edge case.\n\nMore prose."
        out = _apply("notes.md", content)
        assert "REQ-042: must handle the edge case." in out

    def test_decision_marker_preserved(self) -> None:
        content = "Options considered.\n\n**Decision:** go with approach B.\n\nRationale."
        out = _apply("notes.md", content)
        assert "**Decision:** go with approach B." in out

    def test_todo_preserved(self) -> None:
        content = "Body.\n\nTODO: revisit this once data exists.\n\nMore body."
        out = _apply("notes.md", content)
        assert "TODO: revisit this once data exists." in out

    def test_warning_callout_preserved(self) -> None:
        content = "Body.\n\n**WARNING:** this API is unstable.\n\nMore body."
        out = _apply("notes.md", content)
        assert "**WARNING:** this API is unstable." in out

    def test_fenced_code_block_markers_preserved(self) -> None:
        content = "Before.\n\n```python\ndef f():\n    return 1\n```\n\nAfter."
        out = _apply("notes.md", content)
        assert "```python" in out
        assert out.count("```") == 2

    def test_fenced_code_block_interior_preserved_when_small(self) -> None:
        """Within budget, the interior survives too — the documented
        limitation (see next class) only bites once max_tokens actually
        has to compress something."""
        content = "```python\ndef f():\n    return 1\n```"
        out = _apply("notes.md", content)
        assert "def f():" in out
        assert "    return 1" in out


class TestMarkdownFencedCodeBlockLimitation:
    """Demonstrates the documented, accepted limitation (see markdown.toml's
    header comment): strip_lines/max_tokens's preserve_patterns can only
    protect matching *lines*, not a span. A code block's interior line that
    doesn't independently match a preserve_pattern is ordinary KEEP content,
    so max_tokens' best-effort budget (ADR-031) can compress it away even
    though the fence markers around it survive. This is not a bug — it is
    the honest limit of what strip_lines can express today, verified here so
    a future change cannot silently "fix" this in an untested way without
    the test suite noticing the behavior actually changed."""

    def test_tail_strategy_can_strand_a_fence_marker_from_its_interior(self) -> None:
        from quor.pipeline.mask import ContentMask
        from quor.pipeline.stages.max_tokens import MaxTokensConfig, MaxTokensStage

        # A code block whose interior is one large line, followed by a lot of
        # cheap padding. Walking backward (tail strategy) from the end, the
        # budget is consumed almost entirely by the cheap padding lines
        # *before* the walk ever reaches the code block — by the time it
        # gets there, there's enough budget left for the two cheap fence
        # markers (1-3 tokens each) but nowhere near enough for the large
        # interior line. Both fence markers survive; the content between
        # them does not — a code block that *looks* intact but is empty.
        interior = "x" * 5000
        content = (
            f"```python\n{interior}\n```\n"
            + ("Padding line that exists purely to consume token budget.\n" * 200)
        )
        mask = ContentMask.from_text(content)
        config = MaxTokensConfig(type="max_tokens", limit=50, strategy="tail")
        result = MaxTokensStage().apply(mask, config)
        rendered = result.render()

        assert "```python" in rendered
        assert "```" in rendered
        # The interior line is gone (compressed away by the tail budget),
        # even though nothing about it was unsafe to keep — it simply never
        # matched a preserve_pattern in its own right, and cost far more
        # tokens than the budget had left once padding consumed most of it.
        # This is the limitation, made concrete and observable rather than
        # asserted only in prose.
        assert interior not in rendered
        assert ("x" * 50) not in rendered


# ---------------------------------------------------------------------------
# Structure preservation — plain text / RST
# ---------------------------------------------------------------------------


class TestDocumentTextStructurePreservation:
    def test_bullet_list_preserved(self) -> None:
        content = "Intro.\n\n- one\n- two"
        out = _apply("notes.txt", content)
        assert "- one" in out
        assert "- two" in out

    def test_numbered_list_preserved(self) -> None:
        content = "Intro.\n\n1. first\n2. second"
        out = _apply("notes.txt", content)
        assert "1. first" in out

    def test_requirement_id_preserved(self) -> None:
        content = "Prose.\n\nREQ-7: must not regress latency.\n\nMore prose."
        out = _apply("notes.txt", content)
        assert "REQ-7: must not regress latency." in out

    def test_decision_marker_preserved(self) -> None:
        content = "Prose.\n\nDecision: ship behind a flag.\n\nMore prose."
        out = _apply("notes.txt", content)
        assert "Decision: ship behind a flag." in out

    def test_todo_preserved(self) -> None:
        content = "Prose.\n\nFIXME: this edge case is not handled yet.\n\nMore prose."
        out = _apply("notes.txt", content)
        assert "FIXME: this edge case is not handled yet." in out

    def test_note_callout_preserved(self) -> None:
        content = "Prose.\n\nNOTE: requires Python 3.11+.\n\nMore prose."
        out = _apply("notes.txt", content)
        assert "NOTE: requires Python 3.11+." in out

    def test_rst_code_block_directive_preserved(self) -> None:
        content = "Example::\n\n.. code-block:: python\n\n   x = 1"
        out = _apply("doc.rst", content)
        assert ".. code-block:: python" in out

    def test_rst_setext_style_heading_not_specially_protected(self) -> None:
        """Documented limitation (see document-text.toml's header comment):
        RST's title-then-underline heading convention cannot be detected by
        per-line preserve_patterns. The title line survives only because it
        is ordinary KEEP content within budget, not because it was
        recognized as a heading — demonstrated here so the gap stays
        visible rather than silently assumed away."""
        content = "Section Title\n" + "=" * 13 + "\nBody paragraph."
        out = _apply("doc.rst", content)
        # Survives as plain KEEP content (small input, nothing to compress) —
        # but nothing in the filter *specifically* protects it as a heading.
        assert "Section Title" in out


# ---------------------------------------------------------------------------
# Fail-open behaviour
# ---------------------------------------------------------------------------


class TestFailOpen:
    def test_no_filter_match_means_no_route(self) -> None:
        """A path FilterRegistry genuinely can't match falls through to
        None at the routing layer — the same passthrough contract every
        other unmatched lookup already has. (In practice `generic`'s
        catch-all means this rarely happens for a non-empty string; this
        test exercises find() directly, not through generic's net.)"""
        registry = _builtin_only()
        assert registry.find("") is None

    def test_stage_exception_falls_back_to_original_content(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A raising stage must not break document filtering — the existing,
        generic Pipeline.execute() per-stage fail-open (unchanged by
        QB-007B) already covers this for any filter, including the new
        ones. Verified directly here rather than assumed."""
        import warnings

        from quor.pipeline.stages import strip_lines as strip_lines_module

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("synthetic stage failure")

        monkeypatch.setattr(strip_lines_module.StripLinesStage, "apply", _boom)

        content = "# Heading\n\nBody text."
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            out = _apply("notes.md", content)

        # Fail-open: the stage that raised is skipped, remaining stages
        # still ran, and the original content is not corrupted or dropped.
        assert "# Heading" in out
        assert "Body text." in out
        assert any("strip_lines" in str(w.message) for w in caught)

    def test_empty_document_uses_on_empty_sentinel(self) -> None:
        out = _apply("notes.md", "")
        assert out == "(empty document)"

    def test_empty_text_document_uses_on_empty_sentinel(self) -> None:
        out = _apply("notes.txt", "")
        assert out == "(empty document)"


# ---------------------------------------------------------------------------
# max_tokens interaction
# ---------------------------------------------------------------------------


class TestMaxTokensInteraction:
    def test_short_document_passes_through_unchanged(self) -> None:
        """Well under budget: nothing is compressed at all — there is no
        strip (COMPRESS) pattern in either filter, so a small document
        renders back byte-identical."""
        content = "# Title\n\nJust a short paragraph with nothing special."
        out = _apply("notes.md", content)
        assert out == content

    def test_large_document_triggers_head_strategy_compression(self) -> None:
        padding = "This is an ordinary sentence of filler prose. " * 400
        content = f"# Title\n\n{padding}\n\n# Late Heading\n\nFinal paragraph."
        out = _apply("notes.md", content)
        assert len(out) < len(content)

    def test_heading_after_budget_cutoff_still_survives_via_protect(self) -> None:
        """ADR-031: PROTECT overrides position, even under 'head' strategy —
        a heading positioned well past where a pure length-based head cutoff
        would have landed still survives, because strip_lines already marked
        it PROTECT before max_tokens ever ran."""
        padding = "This is an ordinary sentence of filler prose. " * 400
        content = f"# Title\n\n{padding}\n\n# Late Heading\n\nFinal paragraph."
        out = _apply("notes.md", content)
        assert "# Late Heading" in out

    def test_protect_lines_never_compressed_regardless_of_budget(self) -> None:
        """Every REQ/Decision/TODO/heading line in a large document survives
        even when the overall document is compressed."""
        padding = "This is an ordinary sentence of filler prose. " * 400
        content = (
            f"# Title\n\nREQ-1: must survive.\n\n{padding}\n\n"
            "**Decision:** survive too.\n\nTODO: also survive."
        )
        out = _apply("notes.md", content)
        assert "REQ-1: must survive." in out
        assert "**Decision:** survive too." in out
        assert "TODO: also survive." in out

    def test_document_text_large_document_also_compresses(self) -> None:
        padding = "This is an ordinary sentence of filler prose. " * 400
        content = f"REQ-1: important.\n\n{padding}\n\nFinal line."
        out = _apply("notes.txt", content)
        assert len(out) < len(content)
        assert "REQ-1: important." in out


# ---------------------------------------------------------------------------
# deduplicate_consecutive — repeated blank-line runs
# ---------------------------------------------------------------------------


class TestConsecutiveDuplicateCollapsing:
    def test_repeated_blank_lines_collapse_to_one(self) -> None:
        content = "Paragraph one.\n\n\n\n\nParagraph two."
        out = _apply("notes.md", content)
        assert "\n\n\n" not in out
        assert "Paragraph one." in out
        assert "Paragraph two." in out

    def test_repeated_identical_prose_lines_collapse(self) -> None:
        content = "Same line.\nSame line.\nSame line.\nDifferent line."
        out = _apply("notes.txt", content)
        assert out.count("Same line.") == 1
        assert "Different line." in out
