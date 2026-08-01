"""Unit tests for QB-099A's declaration-level structural diff
(`quor.pipeline.ast_summarize.structural_diff_python`) and its
`extract_declarations_python()`/registry wiring sibling capability.

Mirrors the shape of `test_ast_summarize_symbols.py`/
`test_ast_summarize_relationships.py`'s own coverage of a new per-language
`ast_summarize` family, plus the specific cases the QB-099 investigation
(`docs/design/QB-099-structural-diff-compression-investigation.md`) found by
building and benchmarking a prototype: hierarchical (not flat, doubly-
reporting) matching, the minimum-declaration-size floor, and honest
non-detection of extract/inline outside the one case it can prove.
"""

from __future__ import annotations

from quor.pipeline.ast_summarize.declaration_model import Declaration
from quor.pipeline.ast_summarize.python import extract_declarations_python
from quor.pipeline.ast_summarize.registry import get_declaration_extractor, registered_languages
from quor.pipeline.ast_summarize.structural_diff_python import diff_python_files, render


class TestRegistryDeclarationExtractorWiring:
    def test_get_declaration_extractor_returns_none_for_unregistered_language(self) -> None:
        assert get_declaration_extractor("cobol") is None

    def test_get_declaration_extractor_returns_none_for_languages_without_one_yet(self) -> None:
        # QB-099A is Python-only; every other registered `analyze_*()`
        # language still returns None here until a future item extends it.
        for language in registered_languages() - {"python"}:
            assert get_declaration_extractor(language) is None

    def test_get_declaration_extractor_python_extracts_a_real_function(self) -> None:
        extractor = get_declaration_extractor("python")
        assert extractor is not None
        decls = extractor("def f():\n    return 1\n")
        assert len(decls) == 1
        assert decls[0].kind == "function" and decls[0].name == "f" and decls[0].qualname == "f"


class TestExtractDeclarationsPython:
    def test_empty_source_returns_empty_list(self) -> None:
        assert extract_declarations_python("") == []

    def test_top_level_function_and_class_with_methods(self) -> None:
        src = "class Foo:\n    def bar(self):\n        return 1\n\ndef top():\n    return 2\n"
        decls = extract_declarations_python(src)
        by_qualname = {d.qualname: d for d in decls}
        assert set(by_qualname) == {"Foo", "Foo.bar", "top"}
        assert by_qualname["Foo"].kind == "class" and by_qualname["Foo"].parent is None
        assert by_qualname["Foo.bar"].kind == "method" and by_qualname["Foo.bar"].parent == "Foo"
        assert by_qualname["top"].kind == "function" and by_qualname["top"].parent is None

    def test_sibling_rank_is_source_order_per_kind_and_parent(self) -> None:
        src = "def a():\n    return 1\n\ndef b():\n    return 2\n"
        decls = extract_declarations_python(src)
        assert [d.index for d in decls] == [0, 1]

    def test_conditional_definitions_are_included(self) -> None:
        src = "if True:\n    def a():\n        return 1\n"
        decls = extract_declarations_python(src)
        assert [d.name for d in decls] == ["a"]

    def test_node_field_is_the_real_ast_node(self) -> None:
        import ast

        decls = extract_declarations_python("def f():\n    return 1\n")
        assert isinstance(decls[0].node, (ast.FunctionDef, ast.AsyncFunctionDef))


class TestDeclarationQualnameAndLineCount:
    def test_qualname_without_parent(self) -> None:
        d = Declaration("function", "f", None, object(), 0, 1, 2)
        assert d.qualname == "f"

    def test_qualname_with_parent(self) -> None:
        d = Declaration("method", "m", "C", object(), 0, 1, 2)
        assert d.qualname == "C.m"

    def test_line_count_is_inclusive(self) -> None:
        d = Declaration("function", "f", None, object(), 0, 3, 5)
        assert d.line_count == 3


def _render(old_src: str, new_src: str) -> str:
    return render(diff_python_files(old_src, new_src), old_src, new_src)


class TestFileLevelClassification:
    def test_byte_identical_source_is_formatting_only(self) -> None:
        src = "def a():\n    return 1\n"
        result = diff_python_files(src, src)
        assert result.classification == "formatting-only"

    def test_whitespace_only_change_is_formatting_only(self) -> None:
        old = "def a():\n    return 1\n"
        new = "def a():\n\n    return 1\n"
        result = diff_python_files(old, new)
        assert result.classification == "formatting-only"

    def test_import_reorder_is_import_only(self) -> None:
        old = "import os\nimport sys\n\ndef f():\n    return os.getcwd()\n"
        new = "import sys\nimport os\n\ndef f():\n    return os.getcwd()\n"
        result = diff_python_files(old, new)
        assert result.classification == "import-only"

    def test_unparseable_new_source_is_reported_not_raised(self) -> None:
        result = diff_python_files("def a():\n    return 1\n", "def a(:\n")
        assert result.classification == "unparseable"
        assert render(result, "x", "y") == ""

    def test_real_declaration_change_is_structural(self) -> None:
        old = "def a():\n    return 1\n"
        new = "def a():\n    return 2\n"
        result = diff_python_files(old, new)
        assert result.classification == "structural"


class TestReorderAndRename:
    def test_pure_reorder_reports_every_moved_declaration(self) -> None:
        old = "def a():\n    x = 1\n    return x\n\ndef b():\n    y = 2\n    return y\n"
        new = "def b():\n    y = 2\n    return y\n\ndef a():\n    x = 1\n    return x\n"
        text = _render(old, new)
        assert "reordered: a" in text
        assert "reordered: b" in text
        assert "unchanged content" in text

    def test_recursive_rename_matches_via_self_call_blinding(self) -> None:
        old = (
            "def fib(n):\n"
            "    if n <= 1:\n"
            "        return n\n"
            "    return fib(n - 1) + fib(n - 2)\n"
        )
        new = (
            "def fib_memo(n):\n"
            "    if n <= 1:\n"
            "        return n\n"
            "    return fib_memo(n - 1) + fib_memo(n - 2)\n"
        )
        text = _render(old, new)
        assert "renamed: fib -> fib_memo (body unchanged)" in text

    def test_reorder_and_rename_are_never_reported_for_a_genuinely_modified_declaration(self) -> None:
        old = "def a():\n    x = 1\n    return x\n"
        new = "def a():\n    x = 1\n    return x + 1\n"
        text = _render(old, new)
        assert "reordered" not in text
        assert "renamed" not in text
        assert "modified: a" in text


class TestMinimumSizeFloor:
    def test_trivial_unchanged_declaration_in_place_is_silent(self) -> None:
        old = "def a():\n    x = 1\n    y = 2\n    return x + y\n\ndef stub():\n    return 1\n"
        new = "def a():\n    x = 1\n    y = 2\n    return x + y\n\ndef stub():\n    return 1\n"
        result = diff_python_files(old, new)
        # Byte-identical source -> formatting-only short-circuit fires
        # before per-declaration classification even runs.
        assert result.classification == "formatting-only"

    def test_trivial_unchanged_declaration_reordered_is_still_reported_as_reordered_not_modified(self) -> None:
        old = "def a():\n    x = 1\n    y = 2\n    return x + y\n\ndef stub():\n    return 1\n"
        new = "def stub():\n    return 1\n\ndef a():\n    x = 1\n    y = 2\n    return x + y\n"
        text = _render(old, new)
        assert "reordered: stub" in text
        assert "modified: stub" not in text

    def test_two_unrelated_trivial_stubs_are_never_reported_as_moved_or_renamed(self) -> None:
        # Two structurally-identical one-line bodies that are NOT the same
        # declaration (different names, no name in common between the two
        # files) must not be paired via coincidental exact-content match —
        # the size floor should leave both as add/remove, not a false
        # rename/move.
        old = "def __repr__(self):\n    return self.name\n"
        new = "def __str__(self):\n    return self.name\n"
        text = _render(old, new)
        assert "renamed" not in text
        assert "removed: __repr__" in text
        assert "added: __str__" in text


class TestModifiedAddedRemoved:
    def test_added_and_removed_functions(self) -> None:
        old = "def a():\n    return 1\n"
        new = "def a():\n    return 1\n\ndef b():\n    return 2\n"
        text = _render(old, new)
        assert "added: b" in text

    def test_modified_function_shows_scoped_diff(self) -> None:
        old = "def a():\n    x = 1\n    return x\n"
        new = "def a():\n    x = 2\n    return x\n"
        text = _render(old, new)
        assert "modified: a" in text
        assert "-    x = 1" in text
        assert "+    x = 2" in text


class TestHierarchicalMatching:
    def test_class_with_one_changed_method_reports_method_once_not_twice(self) -> None:
        old = (
            "class C:\n"
            "    def unrelated(self):\n"
            "        return 0\n\n"
            "    def m(self):\n"
            "        x = 1\n"
            "        return x\n"
        )
        new = (
            "class C:\n"
            "    def unrelated(self):\n"
            "        return 0\n\n"
            "    def m(self):\n"
            "        x = 2\n"
            "        return x\n"
        )
        result = diff_python_files(old, new)
        modified_qualnames = [o.new.qualname for o in result.ops if o.kind == "modified" and o.new is not None]
        # C.m must appear exactly once — the ops list also carries the
        # parent class's own "modified" fact (true: C's content did change,
        # via its member), but rendering suppresses that into a one-line
        # header instead of a second, duplicate text diff (checked below) —
        # the investigation's own regression was a flat two-level match
        # reporting the *same diff body* twice, -11.3% net on the
        # method_extraction benchmark case.
        assert modified_qualnames.count("C.m") == 1
        text = render(result, old, new)
        assert "modified: class C (members changed, see below)" in text
        assert text.count("x = 1") == 1  # the scoped diff body appears once, not duplicated

    def test_unchanged_method_inside_a_modified_class_is_silent(self) -> None:
        old = "class C:\n    def a(self):\n        return 1\n\n    def b(self):\n        x = 1\n        return x\n"
        new = "class C:\n    def a(self):\n        return 1\n\n    def b(self):\n        x = 2\n        return x\n"
        text = _render(old, new)
        assert "C.a" not in text


class TestExtractInline:
    def test_verbatim_extraction_is_detected(self) -> None:
        old = (
            "def process(order):\n"
            "    subtotal = order.price * order.qty\n"
            "    discount = subtotal * 0.1\n"
            "    order.total = subtotal - discount\n"
            "    order.status = 'processed'\n"
            "    return order.total\n"
        )
        new = (
            "def process(order):\n"
            "    compute_total(order)\n"
            "    order.status = 'processed'\n"
            "    return order.total\n\n"
            "def compute_total(order):\n"
            "    subtotal = order.price * order.qty\n"
            "    discount = subtotal * 0.1\n"
            "    order.total = subtotal - discount\n"
        )
        text = _render(old, new)
        assert "extracted: compute_total extracted from process" in text

    def test_realistic_extraction_with_adapted_statements_is_not_detected(self) -> None:
        # The QB-099 investigation's own central negative finding: an
        # extraction that adapts the lifted code even slightly (here, the
        # last statement becomes a `return` instead of an assignment, the
        # way a real extraction usually gets written) is NOT recognized as
        # an extraction — it must degrade gracefully to modified + added,
        # never crash and never claim a false extraction.
        old = (
            "def process(order):\n"
            "    subtotal = order.price * order.qty\n"
            "    discount = subtotal * 0.1\n"
            "    order.total = subtotal - discount\n"
            "    order.status = 'processed'\n"
            "    return order.total\n"
        )
        new = (
            "def process(order):\n"
            "    order.total = compute_total(order)\n"
            "    order.status = 'processed'\n"
            "    return order.total\n\n"
            "def compute_total(order):\n"
            "    subtotal = order.price * order.qty\n"
            "    discount = subtotal * 0.1\n"
            "    return subtotal - discount\n"
        )
        text = _render(old, new)
        assert "extracted" not in text
        assert "modified: process" in text
        assert "added: compute_total" in text

    def test_verbatim_inline_is_detected(self) -> None:
        old = (
            "def process(order):\n"
            "    compute_total(order)\n"
            "    return order.total\n\n"
            "def compute_total(order):\n"
            "    order.total = order.price * order.qty\n"
        )
        new = (
            "def process(order):\n"
            "    order.total = order.price * order.qty\n"
            "    return order.total\n"
        )
        text = _render(old, new)
        assert "inlined: compute_total inlined into process" in text


class TestCrossContainerMove:
    """QB-099C: `_reconcile_cross_container_moves()`'s own coverage — a
    method/class relocated to a different container is reported as one
    `"moved"` line, not an independent remove+add pair. Both classes below
    keep an unrelated, unchanged member so neither class's own top-level
    content coincidentally exact/rename-matches the other purely from the
    relocated member moving — that would be a different, legitimate match
    these tests aren't about."""

    def test_method_moved_to_a_different_class_is_reported_as_moved(self) -> None:
        old = (
            "class A:\n"
            "    def keep_a(self):\n"
            "        return 'a'\n\n"
            "    def m(self):\n"
            "        x = 1\n"
            "        return x\n\n"
            "class B:\n"
            "    def keep_b(self):\n"
            "        return 'b'\n"
        )
        new = (
            "class A:\n"
            "    def keep_a(self):\n"
            "        return 'a'\n\n"
            "class B:\n"
            "    def keep_b(self):\n"
            "        return 'b'\n\n"
            "    def m(self):\n"
            "        x = 1\n"
            "        return x\n"
        )
        text = _render(old, new)
        assert "moved: A.m -> B.m (unchanged content)" in text
        assert "removed: A.m" not in text
        assert "added: B.m" not in text
        assert "A.keep_a" not in text  # unrelated, genuinely unchanged member stays silent
        assert "B.keep_b" not in text

    def test_method_promoted_to_module_scope_is_reported_as_moved(self) -> None:
        old = (
            "class A:\n"
            "    def keep_a(self):\n"
            "        return 'a'\n\n"
            "    def helper(self):\n"
            "        x = 1\n"
            "        return x\n"
        )
        new = (
            "class A:\n"
            "    def keep_a(self):\n"
            "        return 'a'\n\n"
            "def helper(self):\n"
            "    x = 1\n"
            "    return x\n"
        )
        text = _render(old, new)
        assert "moved: A.helper -> helper (unchanged content)" in text

    def test_method_demoted_from_module_scope_into_a_class_is_reported_as_moved(self) -> None:
        old = "def helper(self):\n    x = 1\n    return x\n\nclass A:\n    def keep_a(self):\n        return 'a'\n"
        new = "class A:\n    def keep_a(self):\n        return 'a'\n\n    def helper(self):\n        x = 1\n        return x\n"
        text = _render(old, new)
        assert "moved: helper -> A.helper (unchanged content)" in text

    def test_same_container_match_takes_priority_over_cross_container_coincidence(self) -> None:
        # `m` stays in A (reordered relative to `keep_a`); a *different*,
        # unrelated function with byte-identical content is added at module
        # scope. Same-container matching must claim `m` first — the new
        # module-level function is a genuine `"added"`, never mistaken for
        # `m` having "moved" out of A.
        old = "class A:\n    def keep_a(self):\n        return 'a'\n\n    def m(self):\n        x = 1\n        return x\n"
        new = (
            "def unrelated(self):\n    x = 1\n    return x\n\n"
            "class A:\n    def m(self):\n        x = 1\n        return x\n\n    def keep_a(self):\n        return 'a'\n"
        )
        text = _render(old, new)
        assert "reordered: A.m" in text
        assert "added: unrelated" in text
        assert "moved" not in text

    def test_trivial_relocated_stub_is_not_reported_as_moved(self) -> None:
        # Same min-size-floor guard as Steps 1/2 — a one-line stub moving
        # "coincidentally" alongside an unrelated one-line stub in another
        # container must not be reported as a confident move.
        old = "class A:\n    def keep_a(self):\n        return 'a'\n\n    def stub(self):\n        return 1\n\nclass B:\n    def keep_b(self):\n        return 'b'\n"
        new = "class A:\n    def keep_a(self):\n        return 'a'\n\nclass B:\n    def keep_b(self):\n        return 'b'\n\n    def stub(self):\n        return 1\n"
        text = _render(old, new)
        assert "\nmoved:" not in text and not text.startswith("moved:")
        assert "removed: A.stub" in text
        assert "added: B.stub" in text


class TestDeterminism:
    def test_same_input_produces_byte_identical_output_across_runs(self) -> None:
        old = "class C:\n    def a(self):\n        return 1\n\ndef top():\n    x = 1\n    return x\n"
        new = "def top():\n    x = 1\n    return x\n\nclass C:\n    def a(self):\n        return 1\n"
        first = _render(old, new)
        second = _render(old, new)
        assert first == second
