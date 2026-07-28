"""Unit tests for QB-066's per-language `extract_symbols_*()` functions and
their `ast_summarize.registry` wiring — the sibling capability to
`test_ast_summarize.py`'s existing `analyze_*()` coverage (compressible
line ranges). Every node-type/field-name assumption below was verified
against the real, installed tree-sitter grammar during implementation (see
this repo's CLAUDE.md "empirically verified against the installed grammar
version" convention), not guessed.
"""

from __future__ import annotations

import warnings
from unittest.mock import patch

from quor.pipeline.ast_summarize.csharp import extract_symbols_csharp
from quor.pipeline.ast_summarize.go import extract_symbols_go
from quor.pipeline.ast_summarize.java import extract_symbols_java
from quor.pipeline.ast_summarize.javascript import extract_symbols_javascript
from quor.pipeline.ast_summarize.python import extract_symbols_python
from quor.pipeline.ast_summarize.registry import get_symbol_extractor, registered_languages
from quor.pipeline.ast_summarize.rust import extract_symbols_rust
from quor.pipeline.ast_summarize.symbol_model import Symbol
from quor.pipeline.ast_summarize.typescript import extract_symbols_tsx, extract_symbols_typescript


def _blocked_import(*module_names: str):
    """Returns a `builtins.__import__` replacement that raises ImportError
    for exactly `module_names` — mirrors `test_ast_summarize.py`'s own
    `test_missing_dependency_fails_open_with_warning` pattern, factored out
    since every language below needs an identical shape."""
    import builtins

    real_import = builtins.__import__

    def _blocked(name: str, *args: object, **kwargs: object) -> object:
        if name in module_names:
            raise ImportError(f"simulated missing dependency: {name}")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    return _blocked


class TestRegistrySymbolExtractorWiring:
    def test_get_symbol_extractor_returns_none_for_unregistered_language(self) -> None:
        assert get_symbol_extractor("cobol") is None

    def test_get_symbol_extractor_returns_callable_for_every_registered_language(self) -> None:
        for language in registered_languages():
            assert get_symbol_extractor(language) is not None

    def test_get_symbol_extractor_python_extracts_a_real_function(self) -> None:
        extractor = get_symbol_extractor("python")
        assert extractor is not None
        assert extractor("def f():\n    return 1\n") == [
            Symbol(name="f", kind="function", line=1, is_public=True, is_entry_point=False)
        ]


class TestExtractSymbolsPython:
    def test_empty_source_returns_empty_list(self) -> None:
        assert extract_symbols_python("") == []

    def test_module_level_function(self) -> None:
        source = "def greet(name):\n    return f'hi {name}'\n"
        assert extract_symbols_python(source) == [
            Symbol(name="greet", kind="function", line=1, is_public=True, is_entry_point=False)
        ]

    def test_private_function_by_leading_underscore(self) -> None:
        result = extract_symbols_python("def _helper():\n    pass\n")
        assert result == [Symbol(name="_helper", kind="function", line=1, is_public=False)]

    def test_class_and_its_methods(self) -> None:
        source = (
            "class Widget:\n"  # 1
            "    def __init__(self):\n"  # 2
            "        self.x = 1\n"  # 3
            "    def _private(self):\n"  # 4
            "        pass\n"  # 5
        )
        assert extract_symbols_python(source) == [
            Symbol(name="Widget", kind="class", line=1, is_public=True),
            Symbol(name="__init__", kind="method", line=2, is_public=False),
            Symbol(name="_private", kind="method", line=4, is_public=False),
        ]

    def test_nested_class_recorded_and_recursed_into(self) -> None:
        source = "class Outer:\n    class Inner:\n        def m(self):\n            pass\n"
        assert extract_symbols_python(source) == [
            Symbol(name="Outer", kind="class", line=1, is_public=True),
            Symbol(name="Inner", kind="class", line=2, is_public=True),
            Symbol(name="m", kind="method", line=3, is_public=True),
        ]

    def test_function_inside_if_type_checking_is_found(self) -> None:
        source = "if True:\n    def conditional():\n        pass\n"
        assert extract_symbols_python(source) == [
            Symbol(name="conditional", kind="function", line=2, is_public=True)
        ]

    def test_entry_point_main_function_flagged(self) -> None:
        result = extract_symbols_python("def main():\n    pass\n")
        assert result == [Symbol(name="main", kind="function", line=1, is_public=True, is_entry_point=True)]

    def test_method_named_main_is_not_flagged_entry_point(self) -> None:
        """Only a module-level `main` is a real entry point — a method
        named `main` on some unrelated class is not."""
        source = "class C:\n    def main(self):\n        pass\n"
        result = extract_symbols_python(source)
        assert result == [
            Symbol(name="C", kind="class", line=1, is_public=True),
            Symbol(name="main", kind="method", line=2, is_public=True, is_entry_point=False),
        ]

    def test_raises_syntax_error_on_invalid_source(self) -> None:
        import pytest

        with pytest.raises(SyntaxError):
            extract_symbols_python("def f(:\n")


class TestExtractSymbolsJavaScript:
    def test_empty_source_returns_empty_list(self) -> None:
        assert extract_symbols_javascript("") == []

    def test_exported_function_is_public(self) -> None:
        result = extract_symbols_javascript("export function topFn() {}\n")
        assert result == [Symbol(name="topFn", kind="function", line=1, is_public=True)]

    def test_unexported_function_is_private(self) -> None:
        result = extract_symbols_javascript("function priv() {}\n")
        assert result == [Symbol(name="priv", kind="function", line=1, is_public=False)]

    def test_exported_arrow_function_assigned_to_const(self) -> None:
        result = extract_symbols_javascript("export const arrowFn = () => {};\n")
        assert result == [Symbol(name="arrowFn", kind="function", line=1, is_public=True)]

    def test_class_and_private_method(self) -> None:
        source = "export class Foo {\n  #secret() { return 1; }\n  bar() { return 2; }\n}\n"
        assert extract_symbols_javascript(source) == [
            Symbol(name="Foo", kind="class", line=1, is_public=True),
            Symbol(name="#secret", kind="method", line=2, is_public=False),
            Symbol(name="bar", kind="method", line=3, is_public=True),
        ]

    def test_entry_point_main_function_flagged(self) -> None:
        result = extract_symbols_javascript("function main() {}\n")
        assert result == [Symbol(name="main", kind="function", line=1, is_public=False, is_entry_point=True)]

    def test_anonymous_default_export_is_omitted(self) -> None:
        assert extract_symbols_javascript("export default function() {}\n") == []

    def test_missing_dependency_fails_open_with_warning(self) -> None:
        with (
            patch(
                "builtins.__import__",
                side_effect=_blocked_import("tree_sitter", "tree_sitter_javascript"),
            ),
            warnings.catch_warnings(record=True) as caught,
        ):
            warnings.simplefilter("always")
            result = extract_symbols_javascript("function f() {}\n")
        assert result == []
        assert any("quor[javascript]" in str(w.message) for w in caught)


class TestExtractSymbolsTypeScript:
    def test_interface_and_enum(self) -> None:
        source = "export interface Shape {\n  area(): number;\n}\nexport enum Color { Red, Green }\n"
        assert extract_symbols_typescript(source) == [
            Symbol(name="Shape", kind="interface", line=1, is_public=True),
            Symbol(name="Color", kind="enum", line=4, is_public=True),
        ]

    def test_accessibility_modifiers_control_method_visibility(self) -> None:
        source = (
            "class Foo {\n"
            "  public pubMethod() {}\n"
            "  private privMethod() {}\n"
            "  protected protMethod() {}\n"
            "  noModMethod() {}\n"
            "}\n"
        )
        result = extract_symbols_typescript(source)
        by_name = {s.name: s.is_public for s in result if s.kind == "method"}
        assert by_name == {
            "pubMethod": True,
            "privMethod": False,
            "protMethod": False,
            "noModMethod": True,  # TS default for an unmarked member is public
        }

    def test_missing_dependency_fails_open_with_warning(self) -> None:
        with (
            patch(
                "builtins.__import__",
                side_effect=_blocked_import("tree_sitter", "tree_sitter_typescript"),
            ),
            warnings.catch_warnings(record=True) as caught,
        ):
            warnings.simplefilter("always")
            result = extract_symbols_typescript("interface I {}\n")
        assert result == []
        assert any("quor[javascript]" in str(w.message) for w in caught)


class TestExtractSymbolsTsx:
    def test_tsx_grammar_extracts_interface(self) -> None:
        result = extract_symbols_tsx("export interface Props {\n  name: string;\n}\n")
        assert result == [Symbol(name="Props", kind="interface", line=1, is_public=True)]


class TestExtractSymbolsGo:
    def test_struct_and_interface(self) -> None:
        source = "package main\n\ntype Widget struct {\n\tName string\n}\n\ntype shape interface {\n\tArea() float64\n}\n"
        result = extract_symbols_go(source)
        assert result == [
            Symbol(name="Widget", kind="struct", line=3, is_public=True),
            Symbol(name="shape", kind="interface", line=7, is_public=False),
        ]

    def test_exported_vs_unexported_functions(self) -> None:
        source = "package main\n\nfunc Exported() {}\n\nfunc unexported() {}\n"
        result = extract_symbols_go(source)
        assert result == [
            Symbol(name="Exported", kind="function", line=3, is_public=True),
            Symbol(name="unexported", kind="function", line=5, is_public=False),
        ]

    def test_method_with_receiver(self) -> None:
        source = "package main\n\nfunc (w *Widget) Render() {}\n"
        assert extract_symbols_go(source) == [
            Symbol(name="Render", kind="method", line=3, is_public=True)
        ]

    def test_entry_point_main_function_flagged(self) -> None:
        source = "package main\n\nfunc main() {}\n"
        result = extract_symbols_go(source)
        assert result == [
            Symbol(name="main", kind="function", line=3, is_public=False, is_entry_point=True)
        ]

    def test_missing_dependency_fails_open_with_warning(self) -> None:
        with (
            patch("builtins.__import__", side_effect=_blocked_import("tree_sitter", "tree_sitter_go")),
            warnings.catch_warnings(record=True) as caught,
        ):
            warnings.simplefilter("always")
            result = extract_symbols_go("package main\nfunc f() {}\n")
        assert result == []
        assert any("quor[go]" in str(w.message) for w in caught)


class TestExtractSymbolsJava:
    def test_class_interface_enum(self) -> None:
        source = "public class Foo {}\ninterface Shape {}\npublic enum Color { RED }\n"
        result = extract_symbols_java(source)
        assert result == [
            Symbol(name="Foo", kind="class", line=1, is_public=True),
            Symbol(name="Shape", kind="interface", line=2, is_public=False),
            Symbol(name="Color", kind="enum", line=3, is_public=True),
        ]

    def test_method_visibility_modifiers(self) -> None:
        source = (
            "public class Foo {\n"
            "  public void pubMethod() {}\n"
            "  private void privMethod() {}\n"
            "  void pkgMethod() {}\n"
            "}\n"
        )
        result = extract_symbols_java(source)
        by_name = {s.name: s.is_public for s in result if s.kind == "method"}
        assert by_name == {"pubMethod": True, "privMethod": False, "pkgMethod": False}

    def test_constructor_counted_as_method(self) -> None:
        source = "public class Foo {\n  public Foo() {}\n}\n"
        result = extract_symbols_java(source)
        assert result == [
            Symbol(name="Foo", kind="class", line=1, is_public=True),
            Symbol(name="Foo", kind="method", line=2, is_public=True),
        ]

    def test_entry_point_main_method_flagged(self) -> None:
        source = "public class Foo {\n  public static void main(String[] args) {}\n}\n"
        result = extract_symbols_java(source)
        main_symbol = next(s for s in result if s.kind == "method")
        assert main_symbol.is_entry_point is True

    def test_member_nested_inside_class_not_visited(self) -> None:
        """Matches `_visit_type_body()`'s existing one-level scope
        boundary: a class nested inside another class is not itself
        recursed into for its own methods."""
        source = "public class Outer {\n  class Inner {\n    void m() {}\n  }\n}\n"
        result = extract_symbols_java(source)
        assert [s.name for s in result] == ["Outer"]

    def test_missing_dependency_fails_open_with_warning(self) -> None:
        with (
            patch("builtins.__import__", side_effect=_blocked_import("tree_sitter", "tree_sitter_java")),
            warnings.catch_warnings(record=True) as caught,
        ):
            warnings.simplefilter("always")
            result = extract_symbols_java("class C {}\n")
        assert result == []
        assert any("quor[java]" in str(w.message) for w in caught)


class TestExtractSymbolsRust:
    def test_struct_enum_trait(self) -> None:
        source = "pub struct Widget {}\nstruct Private;\npub enum Color { Red }\npub trait Shape {}\n"
        result = extract_symbols_rust(source)
        assert result == [
            Symbol(name="Widget", kind="struct", line=1, is_public=True),
            Symbol(name="Private", kind="struct", line=2, is_public=False),
            Symbol(name="Color", kind="enum", line=3, is_public=True),
            Symbol(name="Shape", kind="trait", line=4, is_public=True),
        ]

    def test_impl_methods_and_trait_signature_method(self) -> None:
        source = "impl Widget {\n    pub fn new() -> Self { Widget {} }\n    fn helper(&self) {}\n}\n"
        result = extract_symbols_rust(source)
        assert result == [
            Symbol(name="new", kind="method", line=2, is_public=True),
            Symbol(name="helper", kind="method", line=3, is_public=False),
        ]

    def test_trait_method_signature_with_no_body_counted_as_method(self) -> None:
        result = extract_symbols_rust("trait Shape {\n    fn area(&self) -> f64;\n}\n")
        assert result[-1] == Symbol(name="area", kind="method", line=2, is_public=False)

    def test_entry_point_main_function_flagged(self) -> None:
        result = extract_symbols_rust("fn main() {}\n")
        assert result == [Symbol(name="main", kind="function", line=1, is_public=False, is_entry_point=True)]

    def test_missing_dependency_fails_open_with_warning(self) -> None:
        with (
            patch("builtins.__import__", side_effect=_blocked_import("tree_sitter", "tree_sitter_rust")),
            warnings.catch_warnings(record=True) as caught,
        ):
            warnings.simplefilter("always")
            result = extract_symbols_rust("fn f() {}\n")
        assert result == []
        assert any("quor[rust]" in str(w.message) for w in caught)


class TestExtractSymbolsCSharp:
    def test_class_interface_struct_enum(self) -> None:
        source = "public class C {}\npublic interface I {}\npublic struct S {}\npublic enum E { A }\n"
        result = extract_symbols_csharp(source)
        assert [(s.name, s.kind) for s in result] == [
            ("C", "class"),
            ("I", "interface"),
            ("S", "struct"),
            ("E", "enum"),
        ]

    def test_namespace_nested_class_still_found(self) -> None:
        source = "namespace App {\n  public class Widget {}\n}\n"
        result = extract_symbols_csharp(source)
        assert result == [Symbol(name="Widget", kind="class", line=2, is_public=True)]

    def test_method_visibility_modifiers(self) -> None:
        source = (
            "public class Foo {\n"
            "  public void PubMethod() {}\n"
            "  private void PrivMethod() {}\n"
            "  void DefaultMethod() {}\n"
            "}\n"
        )
        result = extract_symbols_csharp(source)
        by_name = {s.name: s.is_public for s in result if s.kind == "method"}
        assert by_name == {"PubMethod": True, "PrivMethod": False, "DefaultMethod": False}

    def test_entry_point_main_method_flagged_capital_m(self) -> None:
        source = "public class Foo {\n  public static void Main(string[] args) {}\n}\n"
        result = extract_symbols_csharp(source)
        main_symbol = next(s for s in result if s.kind == "method")
        assert main_symbol.is_entry_point is True

    def test_missing_dependency_fails_open_with_warning(self) -> None:
        with (
            patch(
                "builtins.__import__", side_effect=_blocked_import("tree_sitter", "tree_sitter_c_sharp")
            ),
            warnings.catch_warnings(record=True) as caught,
        ):
            warnings.simplefilter("always")
            result = extract_symbols_csharp("class C {}\n")
        assert result == []
        assert any("quor[csharp]" in str(w.message) for w in caught)
