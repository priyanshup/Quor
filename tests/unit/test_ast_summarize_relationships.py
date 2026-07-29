"""Unit tests for QB-067's per-language `extract_relationships_*()`
functions and their `ast_summarize.registry` wiring — the sibling
capability to `test_ast_summarize_symbols.py`'s existing
`extract_symbols_*()` coverage. Every node-type/field-name assumption below
was verified against the real, installed tree-sitter grammar during
implementation, not guessed.
"""

from __future__ import annotations

import pytest

from quor.pipeline.ast_summarize.csharp import extract_relationships_csharp
from quor.pipeline.ast_summarize.go import extract_relationships_go
from quor.pipeline.ast_summarize.java import extract_relationships_java
from quor.pipeline.ast_summarize.javascript import extract_relationships_javascript
from quor.pipeline.ast_summarize.python import extract_relationships_python
from quor.pipeline.ast_summarize.registry import get_relationship_extractor, registered_languages
from quor.pipeline.ast_summarize.relationship_model import Relationship
from quor.pipeline.ast_summarize.rust import extract_relationships_rust
from quor.pipeline.ast_summarize.typescript import (
    extract_relationships_tsx,
    extract_relationships_typescript,
)


def _blocked_import(*module_names: str):
    import builtins

    real_import = builtins.__import__

    def _blocked(name: str, *args: object, **kwargs: object) -> object:
        if name in module_names:
            raise ImportError(f"simulated missing dependency: {name}")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    return _blocked


class TestRegistryRelationshipExtractorWiring:
    def test_get_relationship_extractor_returns_none_for_unregistered_language(self) -> None:
        assert get_relationship_extractor("cobol") is None

    def test_get_relationship_extractor_returns_callable_for_every_registered_language(self) -> None:
        for language in registered_languages():
            assert get_relationship_extractor(language) is not None

    def test_get_relationship_extractor_python_extracts_a_real_import(self) -> None:
        extractor = get_relationship_extractor("python")
        assert extractor is not None
        result = extractor("import os\n")
        assert result == [Relationship(kind="import", source="", target="os", line=1, qualifier="os")]


class TestExtractRelationshipsPython:
    def test_empty_source_returns_empty_list(self) -> None:
        assert extract_relationships_python("") == []

    def test_plain_import(self) -> None:
        result = extract_relationships_python("import os\n")
        assert result == [Relationship(kind="import", source="", target="os", line=1, qualifier="os")]

    def test_import_as_alias_binds_alias_name(self) -> None:
        result = extract_relationships_python("import numpy as np\n")
        assert result == [Relationship(kind="import", source="", target="numpy", line=1, qualifier="np")]

    def test_from_import_with_alias(self) -> None:
        result = extract_relationships_python("from .base import Base as B\n")
        assert result == [
            Relationship(kind="import", source="", target=".base", line=1, qualifier="B", origin="Base")
        ]

    def test_relative_import_leading_dots_encode_level(self) -> None:
        result = extract_relationships_python("from ..pkg import x\n")
        assert result[0].target == "..pkg"

    def test_wildcard_import_skipped(self) -> None:
        assert extract_relationships_python("from os import *\n") == []

    def test_inherits_plain_base(self) -> None:
        result = extract_relationships_python("class Foo(Base):\n    pass\n")
        assert Relationship(kind="inherits", source="Foo", target="Base", line=1) in result

    def test_inherits_dotted_base(self) -> None:
        result = extract_relationships_python("class Foo(pkg.Base):\n    pass\n")
        assert Relationship(kind="inherits", source="Foo", target="pkg.Base", line=1) in result

    def test_inherits_skips_metaclass_keyword(self) -> None:
        result = extract_relationships_python("class Foo(Base, metaclass=Meta):\n    pass\n")
        inherits = [r for r in result if r.kind == "inherits"]
        assert inherits == [Relationship(kind="inherits", source="Foo", target="Base", line=1)]

    def test_dunder_all_exports(self) -> None:
        result = extract_relationships_python("__all__ = ['a', 'b']\n")
        exports = [r for r in result if r.kind == "export"]
        assert exports == [
            Relationship(kind="export", source="a", target="", line=1),
            Relationship(kind="export", source="b", target="", line=1),
        ]

    def test_no_overrides_ever_emitted(self) -> None:
        source = "class Foo(Base):\n    def method(self):\n        pass\n"
        assert not [r for r in extract_relationships_python(source) if r.kind == "overrides"]

    def test_bare_call_within_function(self) -> None:
        source = "def outer():\n    helper()\n"
        result = extract_relationships_python(source)
        assert Relationship(kind="calls", source="outer", target="helper", line=2) in result

    def test_self_call_within_method(self) -> None:
        source = "class Foo:\n    def run(self):\n        self.helper()\n"
        result = extract_relationships_python(source)
        assert (
            Relationship(kind="calls", source="run", target="helper", line=3, qualifier="self") in result
        )

    def test_call_inside_nested_def_attributed_to_outer(self) -> None:
        source = "def outer():\n    def inner():\n        helper()\n    inner()\n"
        result = extract_relationships_python(source)
        calls = [r for r in result if r.kind == "calls"]
        assert all(r.source == "outer" for r in calls)
        assert {r.target for r in calls} == {"helper", "inner"}

    def test_decorator_call_attributed_to_decorated_function(self) -> None:
        """A decorator expression is itself a call, evaluated at
        definition time — `ast.walk()` over the `FunctionDef` node
        includes `decorator_list`, and this is not special-cased away, so
        it is reported as a `calls` relationship sourced from the
        decorated function itself (a real, deterministic AST fact, not a
        bug — see `test_repo_profile_graph_benchmark.py`'s flask-pip
        fixture case for where this was first observed)."""
        source = '@app.route("/")\ndef index():\n    return "hello"\n'
        result = extract_relationships_python(source)
        assert (
            Relationship(kind="calls", source="index", target="route", line=1, qualifier="app") in result
        )

    def test_deep_attribute_chain_call_skipped(self) -> None:
        source = "def outer():\n    a.b.c()\n"
        assert extract_relationships_python(source) == []

    def test_raises_syntax_error_on_invalid_source(self) -> None:
        with pytest.raises(SyntaxError):
            extract_relationships_python("def f(:\n")


class TestExtractRelationshipsJavaScript:
    def test_named_and_default_imports(self) -> None:
        result = extract_relationships_javascript("import Def, { a, b as c } from './mod';\n")
        assert Relationship(kind="import", source="", target="./mod", line=1, qualifier="Def") in result
        assert (
            Relationship(kind="import", source="", target="./mod", line=1, qualifier="c", origin="b")
            in result
        )

    def test_namespace_import(self) -> None:
        result = extract_relationships_javascript("import * as ns from './mod';\n")
        assert result == [Relationship(kind="import", source="", target="./mod", line=1, qualifier="ns")]

    def test_side_effect_import_no_qualifier(self) -> None:
        result = extract_relationships_javascript("import './side';\n")
        assert result == [Relationship(kind="import", source="", target="./side", line=1)]

    def test_export_declaration(self) -> None:
        result = extract_relationships_javascript("export function foo() {}\n")
        assert Relationship(kind="export", source="foo", target="", line=1) in result

    def test_re_export(self) -> None:
        result = extract_relationships_javascript("export { x } from './mod';\n")
        assert result == [Relationship(kind="export", source="x", target="./mod", line=1)]

    def test_class_extends(self) -> None:
        result = extract_relationships_javascript("class Foo extends Base {}\n")
        assert Relationship(kind="inherits", source="Foo", target="Base", line=1) in result

    def test_no_implements_or_overrides_ever_emitted(self) -> None:
        source = "class Foo extends Base {\n  method() {}\n}\n"
        kinds = {r.kind for r in extract_relationships_javascript(source)}
        assert "implements_interface" not in kinds
        assert "overrides" not in kinds

    def test_this_call_in_method(self) -> None:
        source = "class Foo {\n  method() { this.helper(); }\n}\n"
        result = extract_relationships_javascript(source)
        assert (
            Relationship(kind="calls", source="method", target="helper", line=2, qualifier="this") in result
        )

    def test_missing_dependency_fails_open_empty_list(self) -> None:
        with pytest.MonkeyPatch.context() as mp:
            import builtins

            mp.setattr(builtins, "__import__", _blocked_import("tree_sitter_javascript"))
            with pytest.warns(UserWarning, match="tree-sitter-javascript"):
                assert extract_relationships_javascript("import x from './y';\n") == []


class TestExtractRelationshipsTypeScript:
    def test_implements_interface(self) -> None:
        result = extract_relationships_typescript("class Foo implements A, B {}\n")
        kinds = {(r.kind, r.target) for r in result}
        assert ("implements_interface", "A") in kinds
        assert ("implements_interface", "B") in kinds

    def test_interface_extends_reported_as_implements_interface(self) -> None:
        result = extract_relationships_typescript("interface IFoo extends IBar, IBaz {}\n")
        assert {r.target for r in result} == {"IBar", "IBaz"}
        assert all(r.kind == "implements_interface" for r in result)

    def test_override_modifier_detected(self) -> None:
        source = "class Foo extends Base {\n  override method() {}\n}\n"
        result = extract_relationships_typescript(source)
        overrides = [r for r in result if r.kind == "overrides"]
        assert overrides == [
            Relationship(kind="overrides", source="method", target="method", line=2, qualifier="Base")
        ]

    def test_method_without_override_modifier_not_reported(self) -> None:
        source = "class Foo extends Base {\n  method() {}\n}\n"
        result = extract_relationships_typescript(source)
        assert not [r for r in result if r.kind == "overrides"]

    def test_qualified_extends_base(self) -> None:
        result = extract_relationships_typescript("class Foo extends ns.Base {}\n")
        assert Relationship(kind="inherits", source="Foo", target="ns.Base", line=1) in result

    def test_tsx_parses_jsx_syntax(self) -> None:
        result = extract_relationships_tsx("const x = <div>{foo()}</div>;\n")
        assert isinstance(result, list)


class TestExtractRelationshipsGo:
    def test_single_import(self) -> None:
        result = extract_relationships_go('package main\nimport "fmt"\n')
        assert result == [Relationship(kind="import", source="", target="fmt", line=2, qualifier="fmt")]

    def test_grouped_import_with_alias(self) -> None:
        result = extract_relationships_go('package main\nimport (\n\t"os"\n\tf "fmt"\n)\n')
        targets = {(r.target, r.qualifier) for r in result}
        assert ("os", "os") in targets
        assert ("fmt", "f") in targets

    def test_no_inherits_or_implements_ever_emitted(self) -> None:
        source = 'package main\ntype Widget struct{}\nfunc (w *Widget) Render() { w.helper() }\n'
        kinds = {r.kind for r in extract_relationships_go(source)}
        assert kinds <= {"import", "calls"}

    def test_selector_call(self) -> None:
        source = 'package main\nfunc helper() {\n\tfmt.Println("x")\n}\n'
        result = extract_relationships_go(source)
        assert (
            Relationship(kind="calls", source="helper", target="Println", line=3, qualifier="fmt") in result
        )

    def test_missing_dependency_fails_open_empty_list(self) -> None:
        with pytest.MonkeyPatch.context() as mp:
            import builtins

            mp.setattr(builtins, "__import__", _blocked_import("tree_sitter_go"))
            with pytest.warns(UserWarning, match="tree-sitter-go"):
                assert extract_relationships_go('package main\nimport "fmt"\n') == []


class TestExtractRelationshipsJava:
    def test_import_binds_last_segment(self) -> None:
        result = extract_relationships_java("import java.util.List;\n")
        assert result == [
            Relationship(
                kind="import", source="", target="java.util.List", line=1, qualifier="List", origin="List"
            )
        ]

    def test_wildcard_import_skipped(self) -> None:
        assert extract_relationships_java("import java.util.*;\n") == []

    def test_extends_and_implements(self) -> None:
        source = "class Foo extends Base implements A, B {}\n"
        result = extract_relationships_java(source)
        assert Relationship(kind="inherits", source="Foo", target="Base", line=1) in result
        assert Relationship(kind="implements_interface", source="Foo", target="A", line=1) in result
        assert Relationship(kind="implements_interface", source="Foo", target="B", line=1) in result

    def test_override_annotation_detected(self) -> None:
        source = "class Foo extends Base {\n    @Override\n    void method() {}\n}\n"
        result = extract_relationships_java(source)
        overrides = [r for r in result if r.kind == "overrides"]
        assert overrides == [
            Relationship(kind="overrides", source="method", target="method", line=2, qualifier="Base")
        ]

    def test_method_without_override_annotation_not_reported(self) -> None:
        source = "class Foo extends Base {\n    void method() {}\n}\n"
        assert not [r for r in extract_relationships_java(source) if r.kind == "overrides"]

    def test_super_and_this_calls(self) -> None:
        source = "class Foo {\n    void m() { this.other(); super.m(); helper(); }\n}\n"
        result = extract_relationships_java(source)
        qualifiers = {(r.target, r.qualifier) for r in result if r.kind == "calls"}
        assert ("other", "this") in qualifiers
        assert ("m", "super") in qualifiers
        assert ("helper", None) in qualifiers

    def test_missing_dependency_fails_open_empty_list(self) -> None:
        with pytest.MonkeyPatch.context() as mp:
            import builtins

            mp.setattr(builtins, "__import__", _blocked_import("tree_sitter_java"))
            with pytest.warns(UserWarning, match="tree-sitter-java"):
                assert extract_relationships_java("import java.util.List;\n") == []


class TestExtractRelationshipsRust:
    def test_simple_use(self) -> None:
        result = extract_relationships_rust("use std::collections::HashMap;\n")
        assert result == [
            Relationship(
                kind="import",
                source="",
                target="std::collections::HashMap",
                line=1,
                qualifier="HashMap",
                origin="HashMap",
            )
        ]

    def test_aliased_use(self) -> None:
        result = extract_relationships_rust("use crate::foo::Bar as Baz;\n")
        assert result == [
            Relationship(
                kind="import",
                source="",
                target="crate::foo::Bar",
                line=1,
                qualifier="Baz",
                origin="Bar",
            )
        ]

    def test_grouped_use(self) -> None:
        result = extract_relationships_rust("use std::{fmt, collections::HashSet};\n")
        targets = {r.target for r in result}
        assert targets == {"std::fmt", "std::collections::HashSet"}

    def test_wildcard_use_skipped(self) -> None:
        assert extract_relationships_rust("use std::*;\n") == []

    def test_implements_trait(self) -> None:
        source = "struct Widget;\nimpl Shape for Widget {}\n"
        result = extract_relationships_rust(source)
        assert result == [
            Relationship(kind="implements_trait", source="Widget", target="Shape", line=2)
        ]

    def test_plain_impl_no_trait_relationship(self) -> None:
        source = "struct Widget;\nimpl Widget {\n    fn helper(&self) {}\n}\n"
        assert not [r for r in extract_relationships_rust(source) if r.kind == "implements_trait"]

    def test_self_and_path_qualified_calls(self) -> None:
        source = (
            "struct Widget;\nimpl Widget {\n"
            "    fn area(&self) -> f64 { self.helper(); other::func(); 0.0 }\n}\n"
        )
        result = extract_relationships_rust(source)
        assert Relationship(kind="calls", source="area", target="helper", line=3, qualifier="self") in result
        assert Relationship(kind="calls", source="area", target="func", line=3, qualifier="other") in result

    def test_missing_dependency_fails_open_empty_list(self) -> None:
        with pytest.MonkeyPatch.context() as mp:
            import builtins

            mp.setattr(builtins, "__import__", _blocked_import("tree_sitter_rust"))
            with pytest.warns(UserWarning, match="tree-sitter-rust"):
                assert extract_relationships_rust("use std::fmt;\n") == []


class TestExtractRelationshipsCSharp:
    def test_using_directive_no_qualifier(self) -> None:
        result = extract_relationships_csharp("using System.Collections.Generic;\n")
        assert result == [
            Relationship(kind="import", source="", target="System.Collections.Generic", line=1)
        ]

    def test_base_list_all_reported_as_inherits(self) -> None:
        source = "class Foo : Base, IBar {}\n"
        result = extract_relationships_csharp(source)
        assert result == [
            Relationship(kind="inherits", source="Foo", target="Base", line=1),
            Relationship(kind="inherits", source="Foo", target="IBar", line=1),
        ]

    def test_no_implements_interface_ever_emitted(self) -> None:
        source = "class Foo : Base, IBar {\n    void M() {}\n}\n"
        assert not [r for r in extract_relationships_csharp(source) if r.kind == "implements_interface"]

    def test_override_modifier_detected(self) -> None:
        source = "class Foo : Base {\n    public override void Method() {}\n}\n"
        result = extract_relationships_csharp(source)
        overrides = [r for r in result if r.kind == "overrides"]
        assert overrides == [
            Relationship(kind="overrides", source="Method", target="Method", line=2, qualifier="Base")
        ]

    def test_this_and_base_calls(self) -> None:
        source = "class Foo {\n    void M() { this.Other(); base.M(); Helper(); }\n}\n"
        result = extract_relationships_csharp(source)
        qualifiers = {(r.target, r.qualifier) for r in result if r.kind == "calls"}
        assert ("Other", "this") in qualifiers
        assert ("M", "base") in qualifiers
        assert ("Helper", None) in qualifiers

    def test_namespace_nesting_still_finds_type(self) -> None:
        source = "namespace App {\n    class Foo : Base {}\n}\n"
        result = extract_relationships_csharp(source)
        assert Relationship(kind="inherits", source="Foo", target="Base", line=2) in result

    def test_missing_dependency_fails_open_empty_list(self) -> None:
        with pytest.MonkeyPatch.context() as mp:
            import builtins

            mp.setattr(builtins, "__import__", _blocked_import("tree_sitter_c_sharp"))
            with pytest.warns(UserWarning, match="tree-sitter-c-sharp"):
                assert extract_relationships_csharp("using System;\n") == []
