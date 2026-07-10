"""Unit tests for quor/pipeline/extract/registry.py — QB-007E1/E2/E3.

Covers the document extraction framework's routing and fail-open contract —
extension routing, the registry table, and the guarantee that extract()
never raises. Most tests here patch `_EXTRACTORS` directly with a fake
handler, so they exercise the *architecture* in isolation, independent of
whichever real handler happens to be registered. `.docx` and `.pdf` are
both real as of QB-007E2/E3 — see `tests/unit/test_extract_docx.py` and
`tests/unit/test_extract_pdf.py` for their dedicated coverage (real
fixtures, real conversion output).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from quor.pipeline.extract.registry import _EXTRACTORS, extract

# ---------------------------------------------------------------------------
# Unknown / unregistered extensions
# ---------------------------------------------------------------------------


class TestUnknownExtension:
    def test_unknown_extension_returns_none(self) -> None:
        assert extract(Path("archive.zip")) is None

    def test_no_extension_returns_none(self) -> None:
        assert extract(Path("README")) is None

    def test_code_file_extension_returns_none(self) -> None:
        assert extract(Path("script.py")) is None

    def test_markdown_extension_returns_none(self) -> None:
        """`.md` needs no extraction at all — it's already plain text and is
        handled entirely by the existing FilterRegistry (QB-007B/C). It must
        be absent from the routing table, not merely unimplemented."""
        assert extract(Path("notes.md")) is None
        assert ".md" not in _EXTRACTORS

    def test_text_extension_returns_none(self) -> None:
        assert extract(Path("notes.txt")) is None
        assert ".txt" not in _EXTRACTORS

    def test_rst_extension_returns_none(self) -> None:
        assert extract(Path("guide.rst")) is None
        assert ".rst" not in _EXTRACTORS


# ---------------------------------------------------------------------------
# Supported extension, not yet implemented — the NotImplementedError
# contract itself. No currently-registered extension is a stub any more
# (`.docx`/`.pdf` are both real as of QB-007E2/E3), but the mechanism a
# *future* format could rely on (QB-007E1's original design) must keep
# working — proven here the same way the rest of this file proves routing/
# fail-open behavior: patch in a fake stub handler, independent of whatever
# real extensions happen to be registered.
# ---------------------------------------------------------------------------


class TestSupportedButNotImplemented:
    @staticmethod
    def _raises_not_implemented(_: Path) -> str | None:
        raise NotImplementedError("not implemented yet")

    def test_unimplemented_stub_returns_none(self) -> None:
        with patch.dict(_EXTRACTORS, {".docx": self._raises_not_implemented}):
            assert extract(Path("report.docx")) is None

    def test_not_implemented_does_not_warn(self, recwarn: pytest.WarningsRecorder) -> None:
        """NotImplementedError is an expected, known state — not a bug — so
        it must be absorbed silently, unlike a genuine extraction failure."""
        with patch.dict(_EXTRACTORS, {".docx": self._raises_not_implemented}):
            extract(Path("report.docx"))
        assert len(recwarn) == 0


# ---------------------------------------------------------------------------
# Fail-open behaviour — a handler that raises something other than
# NotImplementedError
# ---------------------------------------------------------------------------


class TestFailOpen:
    @staticmethod
    def _raises_runtime_error(_: Path) -> str | None:
        raise RuntimeError("boom")

    def test_generic_exception_returns_none(self) -> None:
        with patch.dict(_EXTRACTORS, {".docx": self._raises_runtime_error}):
            assert extract(Path("report.docx")) is None

    def test_generic_exception_warns(self) -> None:
        with (
            patch.dict(_EXTRACTORS, {".docx": self._raises_runtime_error}),
            pytest.warns(UserWarning, match="document extraction error"),
        ):
            extract(Path("report.docx"))

    def test_os_error_returns_none(self) -> None:
        """A future real handler doing file I/O could raise OSError
        (permission denied, corrupt file, ...) — must fail open exactly like
        any other exception, not propagate as a special case."""

        def _raises_os_error(_: Path) -> str | None:
            raise OSError("permission denied")

        with patch.dict(_EXTRACTORS, {".pdf": _raises_os_error}):
            assert extract(Path("spec.pdf")) is None

    def test_value_error_returns_none(self) -> None:
        def _raises_value_error(_: Path) -> str | None:
            raise ValueError("malformed document")

        with patch.dict(_EXTRACTORS, {".docx": _raises_value_error}):
            assert extract(Path("report.docx")) is None

    def test_extract_never_raises_for_any_registered_exception_type(self) -> None:
        """No exception type escapes extract(), regardless of what a
        handler throws."""
        exceptions = [RuntimeError, ValueError, OSError, KeyError, TypeError, ImportError]
        for exc_type in exceptions:

            def _raiser(_: Path, _exc_type: type[Exception] = exc_type) -> str | None:
                raise _exc_type("boom")

            with patch.dict(_EXTRACTORS, {".docx": _raiser}):
                result = extract(Path("report.docx"))  # must not raise
                assert result is None


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


class TestRouting:
    def test_docx_routes_to_docx_handler(self) -> None:
        called_with: list[Path] = []

        def _spy(file_path: Path) -> str | None:
            called_with.append(file_path)
            return "extracted"

        with patch.dict(_EXTRACTORS, {".docx": _spy}):
            result = extract(Path("report.docx"))

        assert result == "extracted"
        assert called_with == [Path("report.docx")]

    def test_pdf_routes_to_pdf_handler_not_docx(self) -> None:
        docx_called = []
        pdf_called = []

        with patch.dict(
            _EXTRACTORS,
            {
                ".docx": lambda p: docx_called.append(p) or "docx",
                ".pdf": lambda p: pdf_called.append(p) or "pdf",
            },
        ):
            result = extract(Path("spec.pdf"))

        assert result == "pdf"
        assert pdf_called == [Path("spec.pdf")]
        assert docx_called == []

    def test_routing_is_case_insensitive(self) -> None:
        """A file path reported with an uppercase extension (e.g. from a
        case-preserving filesystem) must still route correctly."""
        with patch.dict(_EXTRACTORS, {".docx": lambda p: "extracted"}):
            assert extract(Path("REPORT.DOCX")) == "extracted"

    def test_extension_is_matched_on_suffix_only(self) -> None:
        """A filename that merely *contains* "docx" must not match — routing
        is by Path.suffix, not substring search."""
        assert extract(Path("docx_notes.txt")) is None
        assert extract(Path("report.docx.bak")) is None  # real suffix is ".bak"


# ---------------------------------------------------------------------------
# Registry behaviour
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_registry_contains_exactly_docx_and_pdf(self) -> None:
        """QB-007E1 registers only the two binary formats it stubs out — no
        extra, undocumented entries."""
        assert set(_EXTRACTORS.keys()) == {".docx", ".pdf"}

    def test_registry_handlers_are_callable(self) -> None:
        for handler in _EXTRACTORS.values():
            assert callable(handler)

    def test_registering_a_new_extension_is_picked_up_without_code_changes(self) -> None:
        """The dispatch mechanism itself is generic — adding an entry is
        enough to route a new extension, proving extract() contains no
        extension-specific branching logic."""
        with patch.dict(_EXTRACTORS, {".rtf": lambda p: "rtf-extracted"}):
            assert extract(Path("letter.rtf")) == "rtf-extracted"
        # And it's gone again once the patch is torn down.
        assert extract(Path("letter.rtf")) is None


# ---------------------------------------------------------------------------
# No exceptions escape — public contract
# ---------------------------------------------------------------------------


class TestNoExceptionsEscape:
    def test_extract_signature_never_raises_on_any_input(self) -> None:
        paths = [
            Path(""),
            Path("."),
            Path("no_extension"),
            Path("weird.DOCX"),
            Path("weird.PDF"),
            Path("nested/dir/report.docx"),
            Path("C:/Users/dev/spec.pdf"),
        ]
        for p in paths:
            result = extract(p)  # must never raise
            assert result is None
