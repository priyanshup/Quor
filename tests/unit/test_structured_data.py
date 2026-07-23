"""Unit tests for quor/pipeline/structured_data (QB-040)."""

from __future__ import annotations

from quor.pipeline.mask import ContentMask, Decision
from quor.pipeline.stages.structured_data_summarize import (
    StructuredDataSummarizeConfig,
    StructuredDataSummarizeStage,
)
from quor.pipeline.structured_data.json_fmt import analyze_json
from quor.pipeline.structured_data.toml_fmt import analyze_toml
from quor.pipeline.structured_data.yaml_fmt import analyze_yaml


class TestAnalyzeJson:
    def test_short_array_not_collapsed(self) -> None:
        source = '{\n  "deps": [\n    "a",\n    "b",\n    "c"\n  ]\n}'
        assert analyze_json(source) == []

    def test_long_homogeneous_scalar_array_collapsed(self) -> None:
        items = "\n".join(f'    "{c}"' + ("," if c != "g" else "") for c in "abcdefg")
        source = "{\n  \"deps\": [\n" + items + "\n  ]\n}"
        ranges = analyze_json(source)
        assert len(ranges) == 1
        r = ranges[0]
        assert r.compress_start == 6  # line with "d" (4th element, 1st omitted)
        assert r.compress_end == 9  # line with "g"
        assert "4 more items omitted (7 total)" in r.summary

    def test_heterogeneous_array_not_collapsed(self) -> None:
        elems = ['"a"', "1", "true", "null", '{"x": 1}', "[1, 2]", '"g"']
        items = "\n".join(f"    {e}," if i < len(elems) - 1 else f"    {e}" for i, e in enumerate(elems))
        source = "{\n  \"mixed\": [\n" + items + "\n  ]\n}"
        assert analyze_json(source) == []

    def test_long_homogeneous_object_array_collapsed(self) -> None:
        entries = [
            f'    {{"name": "{n}", "version": "1.0.{i}"}}' for i, n in enumerate("abcdefgh")
        ]
        items = ",\n".join(entries)
        source = "{\n  \"dependencies\": [\n" + items + "\n  ]\n}"
        ranges = analyze_json(source)
        assert len(ranges) == 1
        r = ranges[0]
        assert "5 more items omitted (8 total)" in r.summary
        # Kept lines (first 3 entries, source lines 3-5, 1-indexed) must
        # never fall inside the compressed range.
        assert r.compress_start > 5

    def test_nested_array_inside_kept_element_still_collapses(self) -> None:
        # Outer array has 3 elements (below threshold, not collapsed itself);
        # element 0 contains its own long homogeneous inner array.
        inner_items = ",\n".join(f'        "{c}"' for c in "abcdefgh")
        source = (
            "{\n"
            '  "outer": [\n'
            "    {\n"
            '      "inner": [\n' + inner_items + "\n      ]\n"
            "    },\n"
            "    {},\n"
            "    {}\n"
            "  ]\n"
            "}"
        )
        ranges = analyze_json(source)
        assert len(ranges) == 1
        assert "5 more items omitted (8 total)" in ranges[0].summary

    def test_nested_array_inside_omitted_element_is_discarded(self) -> None:
        # Outer array is itself long+homogeneous (will collapse); one of the
        # OMITTED elements has its own long homogeneous inner array, whose
        # collapse range must NOT appear (that whole region is already
        # fully compressed by the outer collapse).
        def make_elem(big_inner: bool) -> str:
            if big_inner:
                inner_items = ",\n".join(f'        "{c}"' for c in "abcdefgh")
                return '{\n      "inner": [\n' + inner_items + "\n      ]\n    }"
            return '{\n      "inner": [\n        "x"\n      ]\n    }'

        elems = [make_elem(i == 5) for i in range(7)]  # element index 5 is omitted (keep_head=3)
        items = ",\n".join(f"    {e}" for e in elems)
        source = "{\n  \"outer\": [\n" + items + "\n  ]\n}"
        ranges = analyze_json(source)
        assert len(ranges) == 1
        assert "4 more items omitted (7 total)" in ranges[0].summary

    def test_scalar_top_level_returns_empty(self) -> None:
        assert analyze_json('"just a string"') == []
        assert analyze_json("42") == []

    def test_compact_single_line_array_not_collapsed(self) -> None:
        # All elements crammed onto one line -- cannot safely collapse
        # without partially truncating a line ContentMask can't split.
        items = ", ".join(f'"{c}"' for c in "abcdefg")
        source = '{"deps": [' + items + "]}"
        assert analyze_json(source) == []


class TestAnalyzeYaml:
    def test_short_block_sequence_not_collapsed(self) -> None:
        source = "deps:\n  - a\n  - b\n  - c\n"
        assert analyze_yaml(source) == []

    def test_long_homogeneous_block_scalar_sequence_collapsed(self) -> None:
        source = "deps:\n" + "\n".join(f"  - {c}" for c in "abcdefg") + "\n"
        ranges = analyze_yaml(source)
        assert len(ranges) == 1
        r = ranges[0]
        lines = source.split("\n")
        assert lines[r.compress_start - 1].strip() == "- d"
        assert lines[r.compress_end - 1].strip() == "- g"
        assert "4 more items omitted (7 total)" in r.summary

    def test_long_homogeneous_block_mapping_sequence_collapsed(self) -> None:
        entries = "\n".join(
            f"  - name: {n}\n    version: 1.0.{i}" for i, n in enumerate("abcdefgh")
        )
        source = "dependencies:\n" + entries + "\n"
        ranges = analyze_yaml(source)
        assert len(ranges) == 1
        assert "5 more items omitted (8 total)" in ranges[0].summary
        lines = source.split("\n")
        assert lines[ranges[0].compress_start - 1].strip() == "- name: d"

    def test_heterogeneous_sequence_not_collapsed(self) -> None:
        source = "mixed:\n  - a\n  - 1\n  - true\n  - null\n  - {x: 1}\n  - [1, 2]\n  - g\n"
        assert analyze_yaml(source) == []

    def test_flow_sequence_all_on_one_line_not_collapsed(self) -> None:
        source = "deps: [a, b, c, d, e, f, g]\n"
        # All on one line -- cannot safely collapse (same-line guard).
        assert analyze_yaml(source) == []

    def test_flow_sequence_multiline_collapsed(self) -> None:
        source = "deps: [\n  a,\n  b,\n  c,\n  d,\n  e,\n  f,\n  g\n]\n"
        ranges = analyze_yaml(source)
        assert len(ranges) == 1
        assert "4 more items omitted (7 total)" in ranges[0].summary

    def test_scalar_document_returns_empty(self) -> None:
        assert analyze_yaml("just a string\n") == []

    def test_nested_sequence_inside_kept_element_still_collapses(self) -> None:
        source = (
            "outer:\n"
            "  - inner:\n"
            + "\n".join(f"      - {c}" for c in "abcdefgh")
            + "\n"
            "  - {}\n"
            "  - {}\n"
        )
        ranges = analyze_yaml(source)
        assert len(ranges) == 1
        assert "5 more items omitted (8 total)" in ranges[0].summary


class TestAnalyzeToml:
    def test_short_array_of_tables_not_collapsed(self) -> None:
        source = "\n".join(f'[[package]]\nname = "{n}"\nversion = "1.0"\n' for n in "abc")
        assert analyze_toml(source) == []

    def test_long_homogeneous_array_of_tables_collapsed(self) -> None:
        blocks = [f'[[package]]\nname = "{n}"\nversion = "1.0"' for n in "abcdefgh"]
        source = "\n\n".join(blocks) + "\n"
        ranges = analyze_toml(source)
        assert len(ranges) == 1
        r = ranges[0]
        lines = source.split("\n")
        assert lines[r.compress_start - 1] == "[[package]]"
        assert "5 more [[package]] entries omitted (8 total)" in r.summary
        # The 4th block ("d") is the first omitted one -- 3 blocks kept
        # (a, b, c), each 3 lines + 1 blank separator = 4 lines, so the
        # 4th block's header starts at 1-indexed line 13.
        assert r.compress_start == 13

    def test_heterogeneous_array_of_tables_not_collapsed(self) -> None:
        blocks = []
        for i, n in enumerate("abcdefgh"):
            if i == 4:
                blocks.append(f'[[package]]\nname = "{n}"\nversion = "1.0"\nextra = true')
            else:
                blocks.append(f'[[package]]\nname = "{n}"\nversion = "1.0"')
        source = "\n\n".join(blocks) + "\n"
        assert analyze_toml(source) == []

    def test_interleaved_headers_break_the_run(self) -> None:
        blocks = [f'[[package]]\nname = "{n}"' for n in "abc"]
        source = (
            "\n\n".join(blocks[:2])
            + '\n\n[metadata]\nlock-version = "2.0"\n\n'
            + "\n\n".join([f'[[package]]\nname = "{n}"' for n in "defghij"])
            + "\n"
        )
        # Two separate runs of 2 and 7 -- neither exceeds a run of 8, and
        # the interleaved [metadata] table breaks strict consecutiveness,
        # so nothing collapses.
        assert analyze_toml(source) == []

    def test_inline_array_not_collapsed(self) -> None:
        items = ", ".join(f'"{c}"' for c in "abcdefgh")
        source = f"deps = [{items}]\n"
        assert analyze_toml(source) == []

    def test_non_array_table_ignored(self) -> None:
        source = '[project]\nname = "quor"\nversion = "0.4.1"\n'
        assert analyze_toml(source) == []


class TestStructuredDataSummarizeStage:
    def _apply(
        self, source: str, format_: str, preserve_patterns: list[str] | None = None
    ) -> ContentMask:
        mask = ContentMask.from_text(source)
        config = StructuredDataSummarizeConfig(
            type="structured_data_summarize",
            format=format_,
            preserve_patterns=preserve_patterns or [],
        )
        stage = StructuredDataSummarizeStage()
        return stage.apply(mask, config)

    def test_json_collapse_preserves_kept_lines_byte_for_byte(self) -> None:
        items = "\n".join(f'    "{c}"' + ("," if c != "g" else "") for c in "abcdefg")
        source = '{\n  "deps": [\n' + items + "\n  ]\n}"
        result = self._apply(source, "json")
        rendered = result.render()
        assert '"a"' in rendered
        assert '"b"' in rendered
        assert '"c"' in rendered
        assert '"d"' not in rendered  # part of the omitted/compressed run
        assert "4 more items omitted (7 total)" in rendered
        # Untouched original lines survive byte-for-byte.
        original_lines = source.split("\n")
        for lm in result.lines:
            if lm.decision is Decision.KEEP and "more items omitted" not in lm.line:
                assert lm.line in original_lines

    def test_unsupported_format_fails_open(self) -> None:
        source = "irrelevant content\nline two"
        result = self._apply(source, "xml")
        assert result.render() == source

    def test_no_collapse_opportunity_returns_unchanged(self) -> None:
        source = '{"a": 1, "b": 2}'
        result = self._apply(source, "json")
        assert result.render() == source

    def test_preserve_pattern_blocks_a_collapse(self) -> None:
        items = "\n".join(f'    "{c}"' + ("," if c != "g" else "") for c in "abcdefg")
        source = '{\n  "deps": [\n' + items + "\n  ]\n}"
        # Protect the line containing "e" (inside the would-be-omitted run)
        result = self._apply(source, "json", preserve_patterns=[r'"e"'])
        rendered = result.render()
        # Collapse must be skipped entirely -- every element still present.
        for c in "abcdefg":
            assert f'"{c}"' in rendered

    def test_toml_array_of_tables_collapse(self) -> None:
        blocks = [f'[[package]]\nname = "{n}"\nversion = "1.0"' for n in "abcdefgh"]
        source = "\n\n".join(blocks) + "\n"
        result = self._apply(source, "toml")
        rendered = result.render()
        assert 'name = "a"' in rendered
        assert 'name = "d"' not in rendered
        assert "5 more [[package]] entries omitted (8 total)" in rendered

    def test_yaml_sequence_collapse(self) -> None:
        source = "deps:\n" + "\n".join(f"  - {c}" for c in "abcdefg") + "\n"
        result = self._apply(source, "yaml")
        rendered = result.render()
        assert "- a" in rendered
        assert "- d" not in rendered
        assert "4 more items omitted (7 total)" in rendered
