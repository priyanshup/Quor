"""Unit tests for quor/pipeline/repo_profile/symbols_render.py."""

from __future__ import annotations

import orjson

from quor.pipeline.ast_summarize.symbol_model import Symbol
from quor.pipeline.repo_profile.symbols_model import FileSymbols, RepoSymbolIndex
from quor.pipeline.repo_profile.symbols_render import render_json, render_markdown


def _sample_index() -> RepoSymbolIndex:
    return RepoSymbolIndex(
        root="/repo",
        files=[
            FileSymbols(
                path="app.py",
                language="python",
                symbols=[
                    Symbol(name="Widget", kind="class", line=1, is_public=True),
                    Symbol(name="main", kind="function", line=10, is_public=True, is_entry_point=True),
                ],
            )
        ],
        languages_covered=["python"],
        languages_skipped=["go"],
        total_symbols=2,
        notes=["go files were found but skipped (missing optional dependency)."],
    )


class TestRenderMarkdown:
    def test_includes_root_and_file_section(self) -> None:
        output = render_markdown(_sample_index())
        assert "# Repository Symbols" in output
        assert "Root: /repo" in output
        assert "## app.py (python)" in output

    def test_symbol_line_shows_kind_name_line_and_visibility(self) -> None:
        output = render_markdown(_sample_index())
        assert "- class Widget (line 1) [public]" in output

    def test_entry_point_tag_shown(self) -> None:
        output = render_markdown(_sample_index())
        assert "- function main (line 10) [public] [entry-point]" in output

    def test_private_symbol_shows_private_tag(self) -> None:
        index = RepoSymbolIndex(
            root="/repo",
            files=[
                FileSymbols(
                    path="a.py",
                    language="python",
                    symbols=[Symbol(name="_helper", kind="function", line=1, is_public=False)],
                )
            ],
        )
        output = render_markdown(index)
        assert "- function _helper (line 1) [private]" in output

    def test_statistics_section_present(self) -> None:
        output = render_markdown(_sample_index())
        assert "## Statistics" in output
        assert "- Files with symbols: 1" in output
        assert "- Total symbols: 2" in output
        assert "- Languages covered: python" in output

    def test_notes_section_present_when_notes_exist(self) -> None:
        output = render_markdown(_sample_index())
        assert "## Notes" in output
        assert "missing optional dependency" in output

    def test_no_notes_section_when_empty(self) -> None:
        index = RepoSymbolIndex(root="/repo")
        output = render_markdown(index)
        assert "## Notes" not in output

    def test_empty_index_says_no_symbols_found(self) -> None:
        index = RepoSymbolIndex(root="/repo")
        output = render_markdown(index)
        assert "(no symbols found)" in output

    def test_ends_with_single_trailing_newline(self) -> None:
        output = render_markdown(_sample_index())
        assert output.endswith("\n")
        assert not output.endswith("\n\n")


class TestRenderJson:
    def test_produces_valid_json_matching_fields(self) -> None:
        output = render_json(_sample_index())
        parsed = orjson.loads(output)
        assert parsed["root"] == "/repo"
        assert parsed["total_symbols"] == 2
        assert parsed["files"][0]["path"] == "app.py"
        assert parsed["files"][0]["symbols"][0]["name"] == "Widget"
        assert parsed["languages_skipped"] == ["go"]
