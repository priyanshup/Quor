"""Unit tests for quor/pipeline/repo_profile/graph_render.py."""

from __future__ import annotations

import orjson

from quor.pipeline.repo_profile.graph_model import Edge, RepoDependencyGraph
from quor.pipeline.repo_profile.graph_render import render_json, render_markdown


def _sample_graph() -> RepoDependencyGraph:
    return RepoDependencyGraph(
        root="/repo",
        edges=[
            Edge(
                kind="import",
                source_file="main.py",
                target_raw=".base",
                line=1,
                qualifier="Base",
                target_file="base.py",
            ),
            Edge(
                kind="inherits",
                source_file="main.py",
                source_symbol="Foo",
                target_raw="Base",
                line=2,
                target_file="base.py",
                target_symbol="Base",
            ),
            Edge(
                kind="calls",
                source_file="main.py",
                source_symbol="run",
                target_raw="helper",
                line=4,
                qualifier="self",
            ),
        ],
        languages_covered=["python"],
        languages_skipped=["go"],
        total_edges=3,
        resolved_edges=2,
        notes=["go files were found but skipped (missing optional dependency)."],
    )


class TestRenderMarkdown:
    def test_includes_root_and_file_section(self) -> None:
        output = render_markdown(_sample_graph())
        assert "# Repository Dependency Graph" in output
        assert "Root: /repo" in output
        assert "## main.py" in output

    def test_import_edge_shows_resolved_target(self) -> None:
        output = render_markdown(_sample_graph())
        assert "imports `Base` from `.base` -> `base.py` (line 1)" in output

    def test_inherits_edge_shows_resolved_symbol(self) -> None:
        output = render_markdown(_sample_graph())
        assert "`Foo` inherits `Base` -> `base.py::Base` (line 2)" in output

    def test_unresolved_call_has_no_resolution_suffix(self) -> None:
        output = render_markdown(_sample_graph())
        assert "`run` calls `self.helper` (line 4)" in output

    def test_statistics_section_present(self) -> None:
        output = render_markdown(_sample_graph())
        assert "## Statistics" in output
        assert "- Total edges: 3" in output
        assert "- Resolved edges: 2 (67%)" in output
        assert "- Languages covered: python" in output

    def test_notes_section_present_when_notes_exist(self) -> None:
        output = render_markdown(_sample_graph())
        assert "## Notes" in output
        assert "missing optional dependency" in output

    def test_no_notes_section_when_empty(self) -> None:
        graph = RepoDependencyGraph(root="/repo")
        output = render_markdown(graph)
        assert "## Notes" not in output

    def test_empty_graph_says_no_relationships_found(self) -> None:
        graph = RepoDependencyGraph(root="/repo")
        output = render_markdown(graph)
        assert "(no relationships found)" in output

    def test_ends_with_single_trailing_newline(self) -> None:
        output = render_markdown(_sample_graph())
        assert output.endswith("\n")
        assert not output.endswith("\n\n")

    def test_overrides_edge_shows_qualifier_prefixed_target(self) -> None:
        graph = RepoDependencyGraph(
            root="/repo",
            edges=[
                Edge(
                    kind="overrides",
                    source_file="a.py",
                    source_symbol="method",
                    target_raw="method",
                    line=1,
                    qualifier="Base",
                )
            ],
        )
        output = render_markdown(graph)
        assert "`method` overrides `Base.method` (line 1)" in output

    def test_export_edge_with_reexport_shows_source_module(self) -> None:
        graph = RepoDependencyGraph(
            root="/repo",
            edges=[
                Edge(kind="export", source_file="a.js", source_symbol="x", target_raw="./mod", line=1)
            ],
        )
        output = render_markdown(graph)
        assert "exports `x` (re-exported from `./mod`) (line 1)" in output


class TestRenderJson:
    def test_produces_valid_json_matching_fields(self) -> None:
        output = render_json(_sample_graph())
        parsed = orjson.loads(output)
        assert parsed["root"] == "/repo"
        assert parsed["total_edges"] == 3
        assert parsed["edges"][0]["kind"] == "import"
        assert parsed["edges"][0]["target_file"] == "base.py"
        assert parsed["languages_skipped"] == ["go"]
