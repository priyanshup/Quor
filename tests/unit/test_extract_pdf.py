"""Unit tests for quor/pipeline/extract/pdf.py — QB-007E3.

Covers real PDF-to-Markdown extraction. Fixture documents are built with
reportlab (a write-only PDF library used solely to generate test fixtures —
never imported by quor itself; pdfplumber, quor's own read dependency,
cannot author PDFs) so every test exercises a genuine on-disk PDF, not a
mocked approximation of pdfplumber's object model.
"""

from __future__ import annotations

import sys
import warnings
import zipfile
from pathlib import Path

import pytest
from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.pdfgen import canvas

from quor.pipeline.extract.pdf import extract_pdf

# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _canvas(tmp_path: Path, name: str = "fixture.pdf", pagesize: tuple[float, float] = (500, 400)):
    path = tmp_path / name
    return canvas.Canvas(str(path), pagesize=pagesize), path


# ---------------------------------------------------------------------------
# Headings — inferred from font size
# ---------------------------------------------------------------------------


class TestHeadings:
    def test_larger_font_becomes_higher_heading_level(self, tmp_path: Path) -> None:
        c, path = _canvas(tmp_path)
        c.setFont("Helvetica-Bold", 24)
        c.drawString(72, 350, "Biggest Heading")
        c.setFont("Helvetica-Bold", 18)
        c.drawString(72, 320, "Medium Heading")
        c.setFont("Helvetica-Bold", 14)
        c.drawString(72, 290, "Small Heading")
        c.setFont("Helvetica", 11)
        c.drawString(72, 260, "Ordinary body text.")
        c.save()

        result = extract_pdf(path)
        assert result is not None
        assert "# Biggest Heading" in result
        assert "## Medium Heading" in result
        assert "### Small Heading" in result
        assert "Ordinary body text." in result
        # Heading lines must precede the body paragraph, in document order.
        assert result.index("# Biggest Heading") < result.index("### Small Heading")
        assert result.index("### Small Heading") < result.index("Ordinary body text.")

    def test_more_than_six_size_tiers_clamps_to_level_6(self, tmp_path: Path) -> None:
        c, path = _canvas(tmp_path, pagesize=(500, 600))
        y = 560
        # Body text at 10pt, plus 7 distinct larger sizes (11..17).
        c.setFont("Helvetica", 10)
        c.drawString(72, y, "Body text one.")
        y -= 20
        c.setFont("Helvetica", 10)
        c.drawString(72, y, "Body text two.")
        y -= 20
        for size in range(17, 10, -1):
            c.setFont("Helvetica-Bold", size)
            c.drawString(72, y, f"Heading at size {size}")
            y -= size + 6
        c.save()

        result = extract_pdf(path)
        assert result is not None
        assert "###### Heading at size 11" in result
        # The 7th-largest tier (size 11) still renders as a heading (level 6),
        # not silently downgraded to a plain paragraph.
        assert "Heading at size 11" not in result.split("###### Heading at size 11")[0]

    def test_no_larger_font_means_no_headings(self, tmp_path: Path) -> None:
        """A flat, single-font-size document has nothing larger than its
        own body size — no heading tier exists, nothing is misdetected."""
        c, path = _canvas(tmp_path)
        c.setFont("Helvetica", 11)
        c.drawString(72, 350, "Just one line of body text.")
        c.drawString(72, 330, "And another line of body text.")
        c.save()

        result = extract_pdf(path)
        assert result is not None
        assert "#" not in result

    def test_undecodable_bullet_glyph_does_not_misclassify_its_line_as_a_heading(
        self, tmp_path: Path
    ) -> None:
        """Regression: a real bug found while building the QB-007E3
        benchmark fixtures. `ListFlowable`-style bullets can decode as
        several zero-width `(cid:N)` placeholder characters stacked at one
        position, at the bullet's own (larger) font size — e.g. 9 phantom
        characters at 12pt preceding "queued" (6 real characters at 10pt).
        A character-COUNT-based dominant-size heuristic let the 9 phantom
        chars outvote the 6 real ones, so the line's size came out as
        12pt — landing in a real heading tier established elsewhere in the
        same document — and the bullet line rendered as `### (cid:127)
        queued` instead of falling through to a plain (non-heading)
        paragraph. Fixed by weighting dominant-size-per-line by rendered
        character *width* instead of count — the phantom characters are
        zero-width, so they no longer out-vote real, visible text."""
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import (
            ListFlowable,
            ListItem,
            Paragraph,
            SimpleDocTemplate,
        )

        path = tmp_path / "bullet_heading_regression.pdf"
        doc = SimpleDocTemplate(str(path))
        styles = getSampleStyleSheet()
        doc.build(
            [
                # Several real 10pt body paragraphs are needed to firmly
                # establish body_size=10 by frequency — with too few lines,
                # the buggy 12pt reading for the bullet line could itself
                # win the body-size tie-break instead of being ranked
                # above it, masking the bug (this is what the first,
                # too-minimal version of this test accidentally did).
                Paragraph("A Real Heading", styles["Heading1"]),
                Paragraph("First body paragraph.", styles["Normal"]),
                Paragraph("Second body paragraph.", styles["Normal"]),
                Paragraph("Third body paragraph.", styles["Normal"]),
                ListFlowable(
                    [ListItem(Paragraph("queued", styles["Normal"]))],
                    bulletType="bullet",
                ),
            ]
        )

        result = extract_pdf(path)
        assert result is not None
        assert "# A Real Heading" in result
        assert "#" not in result.split("# A Real Heading", 1)[1]


# ---------------------------------------------------------------------------
# Paragraphs — wrapped lines merge, distinct paragraphs stay distinct
# ---------------------------------------------------------------------------


class TestParagraphs:
    def test_wrapped_lines_merge_into_one_paragraph(self, tmp_path: Path) -> None:
        c, path = _canvas(tmp_path)
        c.setFont("Helvetica", 11)
        c.drawString(72, 350, "This is the first visual line of a paragraph")
        c.drawString(72, 336, "that continues immediately below it.")
        c.save()

        result = extract_pdf(path)
        assert result == (
            "This is the first visual line of a paragraph that continues immediately below it."
        )

    def test_large_gap_starts_a_new_paragraph(self, tmp_path: Path) -> None:
        c, path = _canvas(tmp_path)
        c.setFont("Helvetica", 11)
        c.drawString(72, 350, "First paragraph, single line.")
        c.drawString(72, 300, "Second paragraph, separated by a real gap.")
        c.save()

        result = extract_pdf(path)
        assert result == (
            "First paragraph, single line.\n\n"
            "Second paragraph, separated by a real gap."
        )


# ---------------------------------------------------------------------------
# Bullet lists
# ---------------------------------------------------------------------------


class TestBulletLists:
    def test_ascii_bullets_recognized_as_distinct_items(self, tmp_path: Path) -> None:
        c, path = _canvas(tmp_path)
        c.setFont("Helvetica", 11)
        c.drawString(72, 350, "- First item")
        c.drawString(72, 330, "- Second item")
        c.drawString(72, 310, "- Third item")
        c.save()

        result = extract_pdf(path)
        assert result == "- First item\n\n- Second item\n\n- Third item"

    def test_star_bullets_normalized_to_dash(self, tmp_path: Path) -> None:
        c, path = _canvas(tmp_path)
        c.setFont("Helvetica", 11)
        c.drawString(72, 350, "* Star item one")
        c.drawString(72, 330, "* Star item two")
        c.save()

        result = extract_pdf(path)
        assert result == "- Star item one\n\n- Star item two"

    def test_bullet_regex_recognizes_unicode_markers_directly(self) -> None:
        """The recognition contract itself supports common Unicode bullet
        glyphs (•, ◦, ▪, ‣, ●, ○, ·) — verified at the regex level, since
        whether a *given* PDF's font actually round-trips one of these
        glyphs through extraction is a font-encoding property of that PDF,
        not of this module (see TestKnownLimitations below)."""
        from quor.pipeline.extract.pdf import _BULLET_RE

        for marker in ("•", "◦", "▪", "‣", "●", "○", "·"):
            match = _BULLET_RE.match(f"{marker} Item text")
            assert match is not None, f"marker {marker!r} not recognized"
            assert match.group(1) == "Item text"


# ---------------------------------------------------------------------------
# Numbered lists
# ---------------------------------------------------------------------------


class TestNumberedLists:
    def test_numbered_items_kept_as_distinct_lines(self, tmp_path: Path) -> None:
        c, path = _canvas(tmp_path)
        c.setFont("Helvetica", 11)
        c.drawString(72, 350, "1. Step one")
        c.drawString(72, 330, "2. Step two")
        c.drawString(72, 310, "3. Step three")
        c.save()

        result = extract_pdf(path)
        assert result == "1. Step one\n\n2. Step two\n\n3. Step three"

    def test_close_paren_style_numbering_is_also_recognized(self, tmp_path: Path) -> None:
        """"1)" is recognized as a numbered-list marker exactly like "1." —
        but the delimiter itself is normalized to "." in the output, the
        same way bullets normalize to "-" regardless of the source glyph;
        only the *number* is reused verbatim, not the punctuation."""
        c, path = _canvas(tmp_path)
        c.setFont("Helvetica", 11)
        c.drawString(72, 350, "1) First")
        c.drawString(72, 330, "2) Second")
        c.save()

        result = extract_pdf(path)
        assert result == "1. First\n\n2. Second"

    def test_original_pdf_numbers_are_reused_verbatim(self, tmp_path: Path) -> None:
        """Unlike DOCX (where Word's auto-numbering isn't literal paragraph
        text), a PDF's numbers are already part of its rendered text — this
        module reuses them rather than resynthesizing a fresh sequence."""
        c, path = _canvas(tmp_path)
        c.setFont("Helvetica", 11)
        c.drawString(72, 350, "5. Item that starts at five")
        c.drawString(72, 330, "6. Next item")
        c.save()

        result = extract_pdf(path)
        assert "5. Item that starts at five" in result
        assert "6. Next item" in result


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


class TestTables:
    def test_simple_table_renders_as_github_markdown(self, tmp_path: Path) -> None:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

        path = tmp_path / "table.pdf"
        doc = SimpleDocTemplate(str(path), pagesize=letter)
        styles = getSampleStyleSheet()
        data = [["Option", "Verdict"], ["A", "Good"], ["B", "Bad"]]
        t = Table(data)
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]))
        doc.build(
            [
                Paragraph("Before table", styles["Normal"]),
                t,
                Paragraph("After table", styles["Normal"]),
            ]
        )

        result = extract_pdf(path)
        assert result is not None
        assert "| Option | Verdict |" in result
        assert "| --- | --- |" in result
        assert "| A | Good |" in result
        assert "| B | Bad |" in result
        before_idx = result.index("Before table")
        table_idx = result.index("| Option")
        after_idx = result.index("After table")
        assert before_idx < table_idx < after_idx

    def test_table_cell_pipe_is_escaped(self, tmp_path: Path) -> None:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

        path = tmp_path / "table_pipe.pdf"
        doc = SimpleDocTemplate(str(path), pagesize=letter)
        data = [["Header"], ["a | b"]]
        t = Table(data)
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]))
        doc.build([t])

        result = extract_pdf(path)
        assert result is not None
        assert "a \\| b" in result


# ---------------------------------------------------------------------------
# Code blocks — monospace font detection
# ---------------------------------------------------------------------------


class TestCodeBlocks:
    def test_contiguous_monospace_lines_merge_into_one_fence(self, tmp_path: Path) -> None:
        c, path = _canvas(tmp_path)
        c.setFont("Courier", 10)
        c.drawString(72, 350, "def foo():")
        c.drawString(72, 335, "    return 42")
        c.save()

        result = extract_pdf(path)
        assert result == "```\ndef foo():\n    return 42\n```"

    def test_code_block_ends_when_font_changes(self, tmp_path: Path) -> None:
        c, path = _canvas(tmp_path)
        c.setFont("Courier", 10)
        c.drawString(72, 350, "code_line()")
        c.setFont("Helvetica", 11)
        c.drawString(72, 320, "Normal text after code.")
        c.save()

        result = extract_pdf(path)
        assert result == "```\ncode_line()\n```\n\nNormal text after code."

    def test_non_monospace_text_is_not_treated_as_code(self, tmp_path: Path) -> None:
        c, path = _canvas(tmp_path)
        c.setFont("Helvetica", 11)
        c.drawString(72, 350, "Just an ordinary sentence.")
        c.save()

        result = extract_pdf(path)
        assert result == "Just an ordinary sentence."
        assert "```" not in (result or "")


# ---------------------------------------------------------------------------
# Empty / unsupported content
# ---------------------------------------------------------------------------


class TestEmptyOrUnsupportedContent:
    def test_blank_page_returns_empty_string_not_none(self, tmp_path: Path) -> None:
        c, path = _canvas(tmp_path)
        c.save()

        result = extract_pdf(path)
        assert result == ""
        assert result is not None

    def test_image_only_page_with_no_text_returns_empty_string(self, tmp_path: Path) -> None:
        """No OCR, no image inspection — a page with only a drawn shape and
        no text extracts to nothing, exactly as if the page were blank."""
        c, path = _canvas(tmp_path)
        c.rect(50, 50, 100, 100, fill=1)
        c.save()

        result = extract_pdf(path)
        assert result == ""


# ---------------------------------------------------------------------------
# Malformed / corrupt PDF — fail-open
# ---------------------------------------------------------------------------


class TestMalformedPdf:
    def test_not_a_pdf_at_all_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "corrupt.pdf"
        path.write_bytes(b"this is not a pdf file at all, just garbage")
        with pytest.warns(UserWarning, match="document extraction error"):
            result = extract_pdf(path)
        assert result is None

    def test_zip_masquerading_as_pdf_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "fake.pdf"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("hello.txt", "not a pdf")
        with pytest.warns(UserWarning, match="document extraction error"):
            result = extract_pdf(path)
        assert result is None

    def test_truncated_pdf_returns_none(self, tmp_path: Path) -> None:
        c, good_path = _canvas(tmp_path, "good.pdf")
        c.setFont("Helvetica", 11)
        c.drawString(72, 350, "content")
        c.save()
        truncated_bytes = good_path.read_bytes()[: len(good_path.read_bytes()) // 2]
        bad_path = tmp_path / "truncated.pdf"
        bad_path.write_bytes(truncated_bytes)
        with pytest.warns(UserWarning, match="document extraction error"):
            result = extract_pdf(bad_path)
        assert result is None

    def test_nonexistent_file_returns_none(self, tmp_path: Path) -> None:
        with pytest.warns(UserWarning, match="document extraction error"):
            result = extract_pdf(tmp_path / "does_not_exist.pdf")
        assert result is None

    def test_no_exception_escapes_for_any_malformed_input(self, tmp_path: Path) -> None:
        (tmp_path / "empty_file.pdf").write_bytes(b"")
        for path in [tmp_path / "empty_file.pdf", tmp_path / "missing.pdf"]:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                result = extract_pdf(path)  # must never raise
            assert result is None


# ---------------------------------------------------------------------------
# Encrypted PDF — fail-open
# ---------------------------------------------------------------------------


class TestEncryptedPdf:
    def test_password_protected_pdf_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "encrypted.pdf"
        enc = StandardEncryption("userpw", ownerPassword="ownerpw", canPrint=1)
        c = canvas.Canvas(str(path), pagesize=(400, 200), encrypt=enc)
        c.setFont("Helvetica", 12)
        c.drawString(72, 150, "Secret content")
        c.save()

        with pytest.warns(UserWarning, match="document extraction error"):
            result = extract_pdf(path)
        assert result is None


# ---------------------------------------------------------------------------
# Missing dependency — fail-open
# ---------------------------------------------------------------------------


class TestMissingDependency:
    def test_import_error_returns_none_with_actionable_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "pdfplumber", None)
        with pytest.warns(UserWarning, match="quor\\[documents\\]"):
            result = extract_pdf(tmp_path / "anything.pdf")
        assert result is None

    def test_missing_dependency_does_not_attempt_file_access(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "pdfplumber", None)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = extract_pdf(Path("this/path/does/not/exist/at/all.pdf"))
        assert result is None


# ---------------------------------------------------------------------------
# Multi-page documents preserve order
# ---------------------------------------------------------------------------


class TestMultiPage:
    def test_document_order_preserved_across_pages(self, tmp_path: Path) -> None:
        c, path = _canvas(tmp_path)
        c.setFont("Helvetica-Bold", 20)
        c.drawString(72, 350, "Page One Heading")
        c.setFont("Helvetica", 11)
        c.drawString(72, 320, "Page one body text.")
        c.showPage()
        c.setFont("Helvetica-Bold", 20)
        c.drawString(72, 350, "Page Two Heading")
        c.setFont("Helvetica", 11)
        c.drawString(72, 320, "Page two body text.")
        c.save()

        result = extract_pdf(path)
        assert result is not None
        for marker in [
            "# Page One Heading",
            "Page one body text.",
            "# Page Two Heading",
            "Page two body text.",
        ]:
            assert marker in result
        indices = [result.index(m) for m in [
            "# Page One Heading", "Page one body text.", "# Page Two Heading", "Page two body text."
        ]]
        assert indices == sorted(indices)


# ---------------------------------------------------------------------------
# Registry integration — the full fail-open path
# ---------------------------------------------------------------------------


class TestRegistryIntegration:
    def test_real_extraction_flows_through_registry_extract(self, tmp_path: Path) -> None:
        from quor.pipeline.extract.registry import extract

        c, path = _canvas(tmp_path)
        c.setFont("Helvetica-Bold", 18)
        c.drawString(72, 350, "Report")
        c.setFont("Helvetica", 11)
        c.drawString(72, 320, "Body text.")
        c.save()

        result = extract(path)
        assert result == "# Report\n\nBody text."

    def test_corrupt_pdf_fails_open_through_registry_extract(self, tmp_path: Path) -> None:
        from quor.pipeline.extract.registry import extract

        path = tmp_path / "corrupt.pdf"
        path.write_bytes(b"garbage")
        with pytest.warns(UserWarning, match="document extraction error"):
            result = extract(path)
        assert result is None


# ---------------------------------------------------------------------------
# Known limitation, documented and regression-tested (not silently accepted)
# ---------------------------------------------------------------------------


class TestKnownLimitations:
    def test_undecodable_bullet_glyph_falls_through_to_plain_paragraph(
        self, tmp_path: Path
    ) -> None:
        """Some PDFs render bullet glyphs (commonly via a Symbol/Wingdings/
        ZapfDingbats-style font, or — as reproduced here — reportlab's own
        default bullet character) without a ToUnicode CMap, so pdfminer
        cannot recover the actual bullet character; it extracts as a
        `(cid:N)` placeholder instead of a real bullet glyph. This is a
        genuine limitation of the source PDF's own encoding, not something
        this module's regex can work around — such a line is not
        recognized as a bullet and falls through to plain-paragraph
        handling instead of being silently mis-rendered as a list."""
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate

        path = tmp_path / "undecodable_bullet.pdf"
        doc = SimpleDocTemplate(str(path))
        styles = getSampleStyleSheet()
        doc.build(
            [
                ListFlowable(
                    [ListItem(Paragraph("First bullet", styles["Normal"]))],
                    bulletType="bullet",
                )
            ]
        )

        result = extract_pdf(path)
        assert result is not None
        assert "- First bullet" not in result
        assert "First bullet" in result  # content itself is not lost
