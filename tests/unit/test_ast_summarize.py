"""Unit tests for quor/pipeline/ast_summarize/ — QB-005B/QB-005C/QB-005D/
QB-046.

Covers the AST summarization framework in isolation, independent of either
stage that consumes it (python_ast_summarize.py, code_ast_summarize.py —
see tests/unit/test_stages.py for those): the language -> analyzer routing
table (registry.py) and the per-language analyzers (python.py,
javascript.py, typescript.py, go.py, java.py, rust.py, csharp.py). Mirrors
tests/unit/test_extract.py's own separation of "framework tests, patching
internals directly" from "stage/handler tests." Kept as one file covering
every registered language (rather than QB-005A's original per-language-file
suggestion) to match the precedent QB-005B actually established here, not
the design doc's initial, pre-implementation guess.

`tree-sitter`/`tree-sitter-javascript`/`tree-sitter-typescript`
(QB-005C/D, `quor[javascript]`), `tree-sitter-go` (QB-046, `quor[go]`),
`tree-sitter-java` (QB-046, `quor[java]`), `tree-sitter-rust` (QB-046,
`quor[rust]`), and `tree-sitter-c-sharp` (QB-046, `quor[csharp]`) are
listed in the `dev` extra (see pyproject.toml), so
`TestAnalyzeJavaScript`/`TestAnalyzeTypeScript`/`TestAnalyzeGo`/
`TestAnalyzeJava`/`TestAnalyzeRust`/`TestAnalyzeCSharp` below exercise the
*real* parser, not a mock — the same "real fixture coverage" precedent
`tests/unit/test_extract_docx.py`/`test_extract_pdf.py` already
established for python-docx/pdfplumber.
"""

from __future__ import annotations

import warnings
from unittest.mock import patch

import pytest

from quor.pipeline.ast_summarize.csharp import analyze_csharp
from quor.pipeline.ast_summarize.go import analyze_go
from quor.pipeline.ast_summarize.java import analyze_java
from quor.pipeline.ast_summarize.javascript import analyze_javascript
from quor.pipeline.ast_summarize.python import analyze_python
from quor.pipeline.ast_summarize.registry import (
    _ANALYZERS,
    get_analyzer,
    is_language_available,
    registered_languages,
)
from quor.pipeline.ast_summarize.rust import analyze_rust
from quor.pipeline.ast_summarize.typescript import analyze_tsx, analyze_typescript

# ---------------------------------------------------------------------------
# Registry routing
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_python_is_registered(self) -> None:
        analyzer = get_analyzer("python")
        assert analyzer is not None
        assert analyzer is analyze_python

    def test_unregistered_language_returns_none(self) -> None:
        """Unlike quor/pipeline/extract's extract(), which returns None for
        *every* failure mode including a genuine parse error, this registry
        only returns None for a language with no registered analyzer at
        all — see TestRegistryFailOpenContract below for the other half of
        this distinction."""
        assert get_analyzer("cobol") is None
        assert get_analyzer("") is None
        assert get_analyzer("golang") is None

    def test_javascript_is_registered(self) -> None:
        """QB-005C: "javascript" is registered unconditionally (no
        try/except at import time — see registry.py's own module
        docstring) — this holds regardless of whether tree-sitter is
        actually installed in the environment running this test; the
        dependency-availability check happens lazily, per-call, inside
        analyze_javascript() itself (TestAnalyzeJavaScript below)."""
        analyzer = get_analyzer("javascript")
        assert analyzer is not None
        assert analyzer is analyze_javascript

    def test_typescript_and_tsx_are_registered(self) -> None:
        """QB-005D: "typescript" and "tsx" are two separate registry
        entries — not one, and not the same callable — because
        tree-sitter-typescript exposes two distinct grammars that must be
        selected by file extension (see typescript.py's own module
        docstring). Both registered unconditionally, same reasoning as
        "javascript" above."""
        ts_analyzer = get_analyzer("typescript")
        tsx_analyzer = get_analyzer("tsx")
        assert ts_analyzer is not None
        assert tsx_analyzer is not None
        assert ts_analyzer is analyze_typescript
        assert tsx_analyzer is analyze_tsx
        assert ts_analyzer is not tsx_analyzer

    def test_go_is_registered(self) -> None:
        """QB-046: "go" is registered unconditionally (no try/except at
        import time), same reasoning as "javascript" above — dependency
        availability is checked lazily, per-call, inside analyze_go()
        itself (TestAnalyzeGo below)."""
        analyzer = get_analyzer("go")
        assert analyzer is not None
        assert analyzer is analyze_go

    def test_java_is_registered(self) -> None:
        """QB-046: "java" is registered unconditionally (no try/except at
        import time), same reasoning as "go" above — dependency
        availability is checked lazily, per-call, inside analyze_java()
        itself (TestAnalyzeJava below)."""
        analyzer = get_analyzer("java")
        assert analyzer is not None
        assert analyzer is analyze_java

    def test_rust_is_registered(self) -> None:
        """QB-046: "rust" is registered unconditionally (no try/except at
        import time), same reasoning as "go"/"java" above — dependency
        availability is checked lazily, per-call, inside analyze_rust()
        itself (TestAnalyzeRust below)."""
        analyzer = get_analyzer("rust")
        assert analyzer is not None
        assert analyzer is analyze_rust

    def test_csharp_is_registered(self) -> None:
        """QB-046: "csharp" is registered unconditionally (no try/except at
        import time), same reasoning as "go"/"java"/"rust" above —
        dependency availability is checked lazily, per-call, inside
        analyze_csharp() itself (TestAnalyzeCSharp below)."""
        analyzer = get_analyzer("csharp")
        assert analyzer is not None
        assert analyzer is analyze_csharp

    def test_registered_languages_reflects_analyzers_table(self) -> None:
        assert registered_languages() == frozenset(_ANALYZERS)
        assert "python" in registered_languages()

    def test_registered_languages_is_a_snapshot_not_a_live_view(self) -> None:
        """A later registration must not retroactively change a
        previously-returned frozenset — registered_languages() takes a
        snapshot at call time, it does not return a live view over
        _ANALYZERS."""
        before = registered_languages()
        with patch.dict(_ANALYZERS, {"fake-future-language": lambda source: set()}):
            assert "fake-future-language" not in before
            assert "fake-future-language" in registered_languages()

    def test_no_further_languages_registered_yet(self) -> None:
        """After QB-046, python/javascript/typescript/tsx/go/java/rust/
        csharp are registered and nothing else — explicitly asserts the
        current scope boundary so a future language addition is a visible,
        intentional test change here, not a silent expansion. (This test's
        own history: QB-005B's original test asserted "javascript" and
        "typescript" were both absent; QB-005C flipped "javascript" to
        registered and renamed it; QB-005D flipped "typescript"/"tsx" too;
        QB-046 added "go", then "java", then "rust", then "csharp" — each
        rename documents which phase changed the boundary.)"""
        assert _ANALYZERS.keys() == {
            "python",
            "javascript",
            "typescript",
            "tsx",
            "go",
            "java",
            "rust",
            "csharp",
        }


class TestIsLanguageAvailable:
    """QB-038: `is_language_available()` distinguishes "no analyzer
    registered for this language" from "registered, but its optional
    dependency isn't installed" — the second case is what lets
    `FilterRegistry.run_tests()` skip, rather than fail, an inline test that
    can only pass when tree-sitter is actually present.

    `sys.modules[name] = None` (not monkeypatching `builtins.__import__`,
    which `importlib.import_module()` does not reliably respect) is the
    standard, documented technique for forcing `ImportError` on a specific
    module name regardless of whether it's actually installed.
    """

    def _block_import(self, *module_names: str) -> None:
        import sys

        for name in module_names:
            for mod in list(sys.modules):
                if mod == name or mod.startswith(f"{name}."):
                    del sys.modules[mod]
            sys.modules[name] = None  # type: ignore[assignment]

    def _unblock_import(self, *module_names: str) -> None:
        import sys

        for name in module_names:
            if sys.modules.get(name) is None:
                del sys.modules[name]

    def test_unregistered_language_is_unavailable(self) -> None:
        assert is_language_available("cobol") is False

    def test_python_always_available(self) -> None:
        """stdlib `ast` — no optional dependency, no import-probe needed."""
        assert is_language_available("python") is True

    def test_javascript_unavailable_when_tree_sitter_missing(self) -> None:
        self._block_import("tree_sitter", "tree_sitter_javascript")
        try:
            assert is_language_available("javascript") is False
        finally:
            self._unblock_import("tree_sitter", "tree_sitter_javascript")

    def test_javascript_available_when_tree_sitter_present(self) -> None:
        pytest.importorskip("tree_sitter")
        pytest.importorskip("tree_sitter_javascript")
        assert is_language_available("javascript") is True

    def test_typescript_and_tsx_unavailable_when_tree_sitter_missing(self) -> None:
        self._block_import("tree_sitter", "tree_sitter_typescript")
        try:
            assert is_language_available("typescript") is False
            assert is_language_available("tsx") is False
        finally:
            self._unblock_import("tree_sitter", "tree_sitter_typescript")

    def test_javascript_unavailable_when_only_core_tree_sitter_missing(self) -> None:
        """Both the core `tree_sitter` package and the per-language grammar
        package are required — missing either one must report unavailable,
        not just a total absence of both."""
        pytest.importorskip("tree_sitter_javascript")
        self._block_import("tree_sitter")
        try:
            assert is_language_available("javascript") is False
        finally:
            self._unblock_import("tree_sitter")

    def test_go_unavailable_when_tree_sitter_missing(self) -> None:
        self._block_import("tree_sitter", "tree_sitter_go")
        try:
            assert is_language_available("go") is False
        finally:
            self._unblock_import("tree_sitter", "tree_sitter_go")

    def test_go_available_when_tree_sitter_present(self) -> None:
        pytest.importorskip("tree_sitter")
        pytest.importorskip("tree_sitter_go")
        assert is_language_available("go") is True

    def test_java_unavailable_when_tree_sitter_missing(self) -> None:
        self._block_import("tree_sitter", "tree_sitter_java")
        try:
            assert is_language_available("java") is False
        finally:
            self._unblock_import("tree_sitter", "tree_sitter_java")

    def test_java_available_when_tree_sitter_present(self) -> None:
        pytest.importorskip("tree_sitter")
        pytest.importorskip("tree_sitter_java")
        assert is_language_available("java") is True

    def test_rust_unavailable_when_tree_sitter_missing(self) -> None:
        self._block_import("tree_sitter", "tree_sitter_rust")
        try:
            assert is_language_available("rust") is False
        finally:
            self._unblock_import("tree_sitter", "tree_sitter_rust")

    def test_rust_available_when_tree_sitter_present(self) -> None:
        pytest.importorskip("tree_sitter")
        pytest.importorskip("tree_sitter_rust")
        assert is_language_available("rust") is True

    def test_csharp_unavailable_when_tree_sitter_missing(self) -> None:
        self._block_import("tree_sitter", "tree_sitter_c_sharp")
        try:
            assert is_language_available("csharp") is False
        finally:
            self._unblock_import("tree_sitter", "tree_sitter_c_sharp")

    def test_csharp_available_when_tree_sitter_present(self) -> None:
        pytest.importorskip("tree_sitter")
        pytest.importorskip("tree_sitter_c_sharp")
        assert is_language_available("csharp") is True


class TestRegistryFailOpenContract:
    """The registry's fail-open contract deliberately differs from
    quor/pipeline/extract/registry.py's extract(): see registry.py's own
    module docstring. Verified here with a patched-in fake analyzer so this
    is independent of Python's own analyzer implementation."""

    def test_registered_analyzer_exception_propagates_uncaught(self) -> None:
        def _raises(source: str) -> set[int]:
            raise ValueError("simulated parser crash")

        with patch.dict(_ANALYZERS, {"fake": _raises}):
            analyzer = get_analyzer("fake")
            assert analyzer is not None
            with pytest.raises(ValueError, match="simulated parser crash"):
                analyzer("irrelevant source")

    def test_unregistered_language_never_raises(self) -> None:
        """The inverse case: an unregistered language is a clean, silent
        None — never an exception of any kind."""
        assert get_analyzer("a-language-that-will-never-exist") is None


# ---------------------------------------------------------------------------
# analyze_python — direct tests of the relocated function (QB-005B moved
# this from quor/pipeline/stages/python_ast_summarize.py unmodified; see
# tests/unit/test_stages.py::TestPythonAstSummarize for the full behavioral
# battery exercised indirectly through the stage, and this module's
# docstring / backlog.md's QB-005B entry for the before/after equivalence
# proof this move did not change any observable output).
# ---------------------------------------------------------------------------


class TestAnalyzePython:
    def test_empty_source_returns_empty_set(self) -> None:
        assert analyze_python("") == set()

    def test_no_functions_returns_empty_set(self) -> None:
        assert analyze_python("import os\nCONST = 1\n") == set()

    def test_simple_function_body_line_numbers(self) -> None:
        source = 'def add(x, y):\n    """Add."""\n    total = x + y\n    return total\n'
        # Line 1: signature, line 2: docstring, lines 3-4: body.
        assert analyze_python(source) == {3, 4}

    def test_syntax_error_raises_not_none(self) -> None:
        with pytest.raises(SyntaxError):
            analyze_python("def broken(:\n    pass\n")

    def test_null_byte_raises(self) -> None:
        with pytest.raises((SyntaxError, ValueError), match="null byte"):
            analyze_python("def f():\n    pass\n\x00")

    def test_same_line_body_returns_empty_set(self) -> None:
        assert analyze_python("def f(): return 1\n") == set()

    def test_docstring_only_body_returns_empty_set(self) -> None:
        assert analyze_python('def f():\n    """Just a docstring."""\n') == set()


# ---------------------------------------------------------------------------
# analyze_javascript — QB-005C, backed by tree-sitter/tree-sitter-javascript
# (real parser, not a mock — see module docstring). Every line-number
# assertion below was verified against a hand-annotated dump of the actual
# fixture during implementation (see backlog.md's QB-005C entry for the
# methodology), not guessed.
# ---------------------------------------------------------------------------


class TestAnalyzeJavaScript:
    def test_empty_source_returns_empty_set(self) -> None:
        assert analyze_javascript("") == set()

    def test_whitespace_only_returns_empty_set(self) -> None:
        assert analyze_javascript("   \n\n  ") == set()

    def test_no_functions_returns_empty_set(self) -> None:
        assert analyze_javascript('import os from "os";\nconst CONST = 1;\n') == set()

    def test_simple_function_body_line_numbers(self) -> None:
        source = "function add(x, y) {\n  return x + y;\n}\n"
        # Line 1: signature + opening brace, line 2: body, line 3: closing brace.
        assert analyze_javascript(source) == {2}

    def test_same_line_arrow_body_not_compressed(self) -> None:
        """A single-expression arrow function has no statement_block at
        all — mirrors Python's same-line-body rule, generalized."""
        assert analyze_javascript("const oneLiner = (a) => a + 1;\n") == set()

    def test_same_line_function_body_not_compressed(self) -> None:
        """A statement_block whose open/close braces are on the same
        physical line — nothing meaningful to compress without touching
        the signature itself."""
        assert analyze_javascript("function f() { return 1; }\n") == set()

    def test_empty_body_not_compressed(self) -> None:
        assert analyze_javascript("function f() {\n}\n") == set()

    def test_arrow_function_block_body_compressed(self) -> None:
        source = "const arrow = (a, b) => {\n  return a + b;\n};\n"
        assert analyze_javascript(source) == {2}

    def test_class_methods_compressed_independently_extends_preserved(self) -> None:
        source = (
            "class Widget extends Base {\n"  # 1
            "  constructor(x) {\n"  # 2
            "    this.x = x;\n"  # 3
            "  }\n"  # 4
            "\n"  # 5
            "  render() {\n"  # 6
            "    return this.x;\n"  # 7
            "  }\n"  # 8
            "}\n"  # 9
        )
        # Only the two body interiors (3, 7) compress; the class signature,
        # "extends Base", both method signatures, and both closing braces
        # (and the blank separator line) all survive untouched.
        assert analyze_javascript(source) == {3, 7}

    def test_decorators_preserved(self) -> None:
        source = (
            "@decorator\n"  # 1 — class decorator
            "class Bar {\n"  # 2
            "  @readonly\n"  # 3 — method decorator
            "  method() {\n"  # 4
            "    return 1;\n"  # 5
            "  }\n"  # 6
            "}\n"  # 7
        )
        assert analyze_javascript(source) == {5}

    def test_jsdoc_preceding_function_not_in_compress_set(self) -> None:
        """A JSDoc block is a sibling node entirely outside the function's
        own span (unlike Python, where a docstring is the function's own
        first body statement and needs explicit exclusion) — so no
        JSDoc-specific logic is needed; this test proves that structural
        fact holds, not that special-case code works."""
        source = "/**\n * Adds two numbers.\n */\nfunction add(x, y) {\n  return x + y;\n}\n"
        assert analyze_javascript(source) == {5}

    def test_export_function_body_compressed(self) -> None:
        source = "export function greet(name) {\n  return name;\n}\n"
        assert analyze_javascript(source) == {2}

    def test_export_const_arrow_body_compressed(self) -> None:
        source = "export const helper = (x) => {\n  return x * 2;\n};\n"
        assert analyze_javascript(source) == {2}

    def test_export_default_class_methods_compressed(self) -> None:
        source = "export default class Foo {\n  run() {\n    return 1;\n  }\n}\n"
        assert analyze_javascript(source) == {3}

    def test_generator_function_body_compressed(self) -> None:
        source = "function* gen() {\n  yield 1;\n}\n"
        assert analyze_javascript(source) == {2}

    def test_var_and_let_function_expression_bodies_compressed(self) -> None:
        source = (
            "var oldStyle = function () {\n"  # 1
            "  return 1;\n"  # 2
            "};\n"  # 3
            "let anonGen = function* () {\n"  # 4
            "  return 2;\n"  # 5
            "};\n"  # 6
        )
        assert analyze_javascript(source) == {2, 5}

    def test_jsx_body_compressed_without_error(self) -> None:
        """.jsx files route through this same analyzer (cat-javascript.toml
        matches .js/.jsx/.mjs/.cjs) — vanilla tree-sitter-javascript parses
        JSX natively, verified empirically during implementation."""
        source = (
            'function Widget(props) {\n  return <div className="box">{props.label}</div>;\n}\n'
        )
        assert analyze_javascript(source) == {2}

    def test_syntax_error_in_signature_excludes_swallowed_region(self) -> None:
        """A malformed function *signature* can make tree-sitter's error
        recovery swallow everything up to EOF into one ERROR node —
        verified empirically during implementation (see backlog.md's
        QB-005C entry). The function before the error still compresses;
        nothing inside the swallowed region does, because it's never even
        visited as a function-like candidate — more conservative than the
        overlap-exclusion rule alone would require, and correctly so."""
        source = (
            "function good1(x) {\n"  # 1-3
            "  return x + 1;\n"
            "}\n"
            "\n"  # 4
            "function broken(: {\n"  # 5
            "  return 1;\n"  # 6
            "}\n"  # 7
            "\n"  # 8
            "function good2(y) {\n"  # 9
            "  return y + 2;\n"  # 10
            "}\n"  # 11
        )
        assert analyze_javascript(source) == {2}

    def test_syntax_error_in_body_excludes_only_that_function(self) -> None:
        """A malformed *body* expression is a more localized error that
        tree-sitter recovers from without swallowing sibling declarations —
        this is QB-005A Section 4.1/Section 7's own stated expectation
        ("a function overlapping the error is not compressed; a function
        far from it, in a large file, still is"), verified here against
        the real parser rather than assumed."""
        source = (
            "function good1(x) {\n"  # 1-3
            "  return x + 1;\n"
            "}\n"
            "\n"  # 4
            "function alsoBroken(y) {\n"  # 5
            "  return y +++ * ;\n"  # 6
            "}\n"  # 7
            "\n"  # 8
            "function good2(z) {\n"  # 9
            "  return z + 2;\n"  # 10
            "}\n"  # 11
        )
        assert analyze_javascript(source) == {2, 10}

    def test_large_synthetic_file_compresses_every_function_body(self) -> None:
        n = 100
        chunks = [f"function func_{i}(x) {{\n  return x + {i};\n}}\n" for i in range(n)]
        source = "".join(chunks)
        result = analyze_javascript(source)
        # Each 3-line chunk's body is its own middle line: 2, 5, 8, ...
        assert result == {3 * i + 2 for i in range(n)}

    def test_missing_dependency_fails_open_with_warning(self) -> None:
        """Simulates quor[javascript] not being installed by blocking the
        two lazy imports analyze_javascript() performs internally — mirrors
        tests/unit/test_extract_docx.py's missing-dependency fail-open
        pattern for python-docx."""
        import builtins

        real_import = builtins.__import__

        def _blocked(name: str, *args: object, **kwargs: object) -> object:
            if name in ("tree_sitter", "tree_sitter_javascript"):
                raise ImportError(f"simulated missing dependency: {name}")
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        with patch("builtins.__import__", side_effect=_blocked), warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = analyze_javascript("function f() {\n  return 1;\n}\n")

        assert result == set()
        assert any("quor[javascript]" in str(w.message) for w in caught)


# ---------------------------------------------------------------------------
# analyze_typescript / analyze_tsx — QB-005D, backed by
# tree-sitter/tree-sitter-typescript (real parser, not a mock). Every
# line-number assertion below was verified against a hand-annotated dump of
# the actual fixture during implementation (see backlog.md's QB-005D entry
# for the methodology), not guessed.
# ---------------------------------------------------------------------------


class TestAnalyzeTypeScript:
    def test_empty_source_returns_empty_set(self) -> None:
        assert analyze_typescript("") == set()

    def test_no_functions_returns_empty_set(self) -> None:
        assert analyze_typescript('import os from "os";\nconst CONST: number = 1;\n') == set()

    def test_simple_typed_function_body_line_numbers(self) -> None:
        source = "function add(x: number, y: number): number {\n  return x + y;\n}\n"
        assert analyze_typescript(source) == {2}

    def test_same_line_body_not_compressed(self) -> None:
        assert analyze_typescript("function f(): number { return 1; }\n") == set()

    def test_interface_preserved_whole(self) -> None:
        source = "interface Point {\n  x: number;\n  y: number;\n}\n"
        assert analyze_typescript(source) == set()

    def test_type_alias_preserved_whole(self) -> None:
        assert analyze_typescript("type Coordinates = [number, number];\n") == set()

    def test_enum_preserved_whole(self) -> None:
        source = "enum Color {\n  Red,\n  Green,\n  Blue,\n}\n"
        assert analyze_typescript(source) == set()

    def test_namespace_preserved_whole_including_nested_function(self) -> None:
        """A deliberate, documented scope limitation (see typescript.py's
        module docstring): a namespace's own body is never recursed into,
        so a function declared inside one is preserved in full, not
        compressed — this test proves that's what actually happens, not
        just that it's undocumented-and-untested."""
        source = (
            "namespace Utils {\n"
            "  export function helper(x: number): number {\n"
            "    return x + 1;\n"
            "  }\n"
            "}\n"
        )
        assert analyze_typescript(source) == set()

    def test_overload_signatures_preserved_only_implementation_compressed(self) -> None:
        source = (
            "function overload(x: number): number;\n"  # 1
            "function overload(x: string): string;\n"  # 2
            "function overload(x: any): any {\n"  # 3
            "  return x;\n"  # 4
            "}\n"  # 5
        )
        assert analyze_typescript(source) == {4}

    def test_abstract_class_method_preserved_concrete_method_compressed(self) -> None:
        source = (
            "abstract class Shape {\n"  # 1
            "  abstract area(): number;\n"  # 2
            "\n"  # 3
            "  describe(): string {\n"  # 4
            '    return "a shape";\n'  # 5
            "  }\n"  # 6
            "}\n"  # 7
        )
        assert analyze_typescript(source) == {5}

    def test_decorators_and_implements_clause_preserved(self) -> None:
        source = (
            '@Component({selector: "app-foo"})\n'  # 1
            "class FooComponent implements OnInit {\n"  # 2
            "  @Input()\n"  # 3
            "  value: string;\n"  # 4
            "\n"  # 5
            "  ngOnInit(): void {\n"  # 6
            "    console.log(this.value);\n"  # 7
            "  }\n"  # 8
            "}\n"  # 9
        )
        assert analyze_typescript(source) == {7}

    def test_extends_and_generic_type_parameters_preserved(self) -> None:
        source = (
            "class Box<T> extends Container<T> {\n"
            "  get(): T {\n"
            "    return this.value;\n"
            "  }\n"
            "}\n"
        )
        assert analyze_typescript(source) == {3}

    def test_jsdoc_preceding_function_not_in_compress_set(self) -> None:
        source = (
            "/**\n * Adds two numbers.\n */\n"
            "function add(x: number, y: number): number {\n"
            "  return x + y;\n"
            "}\n"
        )
        assert analyze_typescript(source) == {5}

    def test_export_function_and_export_default_class_compressed(self) -> None:
        source = "export function greet(name: string): string {\n  return name;\n}\n"
        assert analyze_typescript(source) == {2}
        source2 = "export default class Foo {\n  run(): number {\n    return 1;\n  }\n}\n"
        assert analyze_typescript(source2) == {3}

    def test_generic_function_type_parameters_do_not_confuse_body_detection(self) -> None:
        source = "function identity<T>(x: T): T {\n  return x;\n}\n"
        assert analyze_typescript(source) == {2}

    def test_arrow_function_with_type_annotations_compressed(self) -> None:
        source = "const add = (x: number, y: number): number => {\n  return x + y;\n};\n"
        assert analyze_typescript(source) == {2}

    def test_syntax_error_in_body_excludes_only_that_function(self) -> None:
        """Reuses the exact same ERROR-node-overlap rule javascript.py
        uses (via the shared _treesitter_utils module) — verified directly
        against malformed TypeScript, per this task's own instruction."""
        source = (
            "function good1(x: number): number {\n"  # 1-3
            "  return x + 1;\n"
            "}\n"
            "\n"  # 4
            "function alsoBroken(y: number): number {\n"  # 5
            "  return y +++ * ;\n"  # 6
            "}\n"  # 7
            "\n"  # 8
            "function good2(z: number): number {\n"  # 9
            "  return z + 2;\n"  # 10
            "}\n"  # 11
        )
        assert analyze_typescript(source) == {2, 10}

    def test_large_synthetic_file_compresses_every_function_body(self) -> None:
        """Also the exact scale (100 functions) that originally surfaced
        the tree-sitter==0.26.0 memory-corruption bug for JavaScript in
        QB-005C — re-run here against the TypeScript grammar specifically
        as part of this task's own pre-flight compatibility gate."""
        n = 100
        chunks = [
            f"function func_{i}(x: number): number {{\n  return x + {i};\n}}\n" for i in range(n)
        ]
        source = "".join(chunks)
        result = analyze_typescript(source)
        assert result == {3 * i + 2 for i in range(n)}

    def test_missing_dependency_fails_open_with_warning(self) -> None:
        import builtins

        real_import = builtins.__import__

        def _blocked(name: str, *args: object, **kwargs: object) -> object:
            if name in ("tree_sitter", "tree_sitter_typescript"):
                raise ImportError(f"simulated missing dependency: {name}")
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        with patch("builtins.__import__", side_effect=_blocked), warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = analyze_typescript("function f(): number {\n  return 1;\n}\n")

        assert result == set()
        assert any("quor[javascript]" in str(w.message) for w in caught)


class TestAnalyzeTsx:
    """analyze_tsx() — the language_tsx() grammar variant, JSX-aware."""

    def test_jsx_function_body_compressed(self) -> None:
        source = (
            "interface Props {\n"  # 1
            "  label: string;\n"  # 2
            "}\n"  # 3
            "\n"  # 4
            "function Widget(props: Props): JSX.Element {\n"  # 5
            '  return <div className="box">{props.label}</div>;\n'  # 6
            "}\n"  # 7
        )
        assert analyze_tsx(source) == {6}

    def test_arrow_component_with_jsx_compressed(self) -> None:
        source = (
            "const List = ({ items }: { items: string[] }) => {\n"  # 1
            "  return (\n"  # 2
            "    <ul>\n"  # 3
            "      {items.map((item) => <li key={item}>{item}</li>)}\n"  # 4
            "    </ul>\n"  # 5
            "  );\n"  # 6
            "};\n"  # 7
        )
        assert analyze_tsx(source) == {2, 3, 4, 5, 6}

    def test_generic_function_alongside_jsx_both_compressed(self) -> None:
        """Proves language_tsx() disambiguates `<T>` generics from JSX
        correctly in the same file — the exact ambiguity QB-005A Section 8
        flagged as a correctness risk."""
        source = (
            "function identity<T>(x: T): T {\n"  # 1
            "  return x;\n"  # 2
            "}\n"  # 3
            "\n"  # 4
            "function Widget(): JSX.Element {\n"  # 5
            "  return <div />;\n"  # 6
            "}\n"  # 7
        )
        assert analyze_tsx(source) == {2, 6}

    def test_jsx_fails_to_parse_cleanly_under_plain_typescript_grammar(self) -> None:
        """Confirms grammar-variant selection genuinely matters (QB-005A
        Section 8) rather than being a hypothetical concern: JSX content
        run through analyze_typescript() (the non-TSX grammar) hits a real
        parse error and is excluded via the ERROR-node-overlap rule, never
        misparsed into a wrong compress range."""
        source = "function Widget(): JSX.Element {\n  return <div />;\n}\n"
        assert analyze_typescript(source) == set()

    def test_missing_dependency_fails_open_with_warning(self) -> None:
        import builtins

        real_import = builtins.__import__

        def _blocked(name: str, *args: object, **kwargs: object) -> object:
            if name in ("tree_sitter", "tree_sitter_typescript"):
                raise ImportError(f"simulated missing dependency: {name}")
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        with patch("builtins.__import__", side_effect=_blocked), warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = analyze_tsx("function f(): JSX.Element {\n  return <div />;\n}\n")

        assert result == set()
        assert any("quor[javascript]" in str(w.message) for w in caught)


# ---------------------------------------------------------------------------
# analyze_go — QB-046, backed by tree-sitter/tree-sitter-go (real parser,
# not a mock — see module docstring). Every line-number assertion below was
# verified against the real, installed grammar's output during
# implementation, not guessed.
# ---------------------------------------------------------------------------


class TestAnalyzeGo:
    def test_empty_source_returns_empty_set(self) -> None:
        assert analyze_go("") == set()

    def test_whitespace_only_returns_empty_set(self) -> None:
        assert analyze_go("   \n\n  ") == set()

    def test_no_functions_returns_empty_set(self) -> None:
        source = 'package main\n\nimport "fmt"\n\nconst Foo = 1\n\nvar _ = fmt.Sprintf\n'
        assert analyze_go(source) == set()

    def test_simple_function_body_line_numbers(self) -> None:
        source = "func Add(x, y int) int {\n  return x + y\n}\n"
        # Line 1: signature + opening brace, line 2: body, line 3: closing brace.
        assert analyze_go(source) == {2}

    def test_same_line_function_body_not_compressed(self) -> None:
        """A `block` whose open/close braces are on the same physical
        line — nothing meaningful to compress without touching the
        signature itself, mirrors javascript.py's identical rule."""
        assert analyze_go("func f() { return 1 }\n") == set()

    def test_empty_body_not_compressed(self) -> None:
        assert analyze_go("func f() {\n}\n") == set()

    def test_var_func_literal_body_compressed(self) -> None:
        source = "var handler = func(x int) int {\n  return x * 2\n}\n"
        assert analyze_go(source) == {2}

    def test_method_body_compressed_receiver_and_struct_preserved(self) -> None:
        """A method's receiver clause makes `method_declaration` its own
        top-level sibling node (Go has no classes) — see go.py's module
        docstring. The struct type's own field lines are untouched:
        `type_declaration` is never visited as a function-like candidate."""
        source = (
            "type Widget struct {\n"  # 1
            "\tX int\n"  # 2
            "}\n"  # 3
            "\n"  # 4
            "func (w *Widget) Render() string {\n"  # 5
            '\treturn "hi"\n'  # 6
            "}\n"  # 7
        )
        assert analyze_go(source) == {6}

    def test_doc_comment_preceding_function_not_in_compress_set(self) -> None:
        """A `//` doc comment is a sibling node entirely outside the
        function's own span (unlike Python's docstring, which is the
        function's own first body statement) — mirrors javascript.py's
        identical JSDoc case."""
        source = "// Add returns the sum.\nfunc Add(x, y int) int {\n  return x + y\n}\n"
        assert analyze_go(source) == {3}

    def test_grouped_var_block_func_literals_compressed_independently(self) -> None:
        """A parenthesized `var (...)` block nests each `var_spec` one
        level deeper (inside a `var_spec_list`) than a single `var f = ...`
        statement does — go.py's `_visit_var_declaration()` handles both
        shapes; this proves the grouped-block shape specifically."""
        source = (
            "var (\n"  # 1
            "\ta = func() int {\n"  # 2
            "\t\treturn 1\n"  # 3
            "\t}\n"  # 4
            "\tb = func() int {\n"  # 5
            "\t\treturn 2\n"  # 6
            "\t}\n"  # 7
            ")\n"  # 8
        )
        assert analyze_go(source) == {3, 6}

    def test_closure_inside_function_body_not_separately_recursed(self) -> None:
        """A `go func() {...}()` closure nested inside a function's body is
        implementation detail of the outer function — its lines are
        covered by the outer function's own compress range, not visited or
        excluded as a candidate of its own (mirrors python.py/
        javascript.py's identical "no further recursion" rule)."""
        source = (
            "func Outer() {\n"  # 1
            "\tgo func() {\n"  # 2
            '\t\tfmt.Println("hi")\n'  # 3
            "\t}()\n"  # 4
            "}\n"  # 5
        )
        assert analyze_go(source) == {2, 3, 4}

    def test_syntax_error_in_signature_excludes_swallowed_region(self) -> None:
        """A malformed function *signature* can make tree-sitter's error
        recovery swallow everything up to EOF into one ERROR node — the
        function before the error still compresses; nothing inside the
        swallowed region does, mirrors javascript.py's identical case."""
        source = (
            "func good1(x int) int {\n"  # 1-3
            "  return x + 1\n"
            "}\n"
            "\n"  # 4
            "func broken( {\n"  # 5
            "  return 1\n"  # 6
            "}\n"  # 7
            "\n"  # 8
            "func good2(y int) int {\n"  # 9
            "  return y + 2\n"  # 10
            "}\n"  # 11
        )
        assert analyze_go(source) == {2, 10}

    def test_syntax_error_in_body_excludes_only_that_function(self) -> None:
        source = (
            "func good1(x int) int {\n"  # 1-3
            "  return x + 1\n"
            "}\n"
            "\n"  # 4
            "func alsoBroken(y int) int {\n"  # 5
            "  return y +++ * \n"  # 6
            "}\n"  # 7
            "\n"  # 8
            "func good2(z int) int {\n"  # 9
            "  return z + 2\n"  # 10
            "}\n"  # 11
        )
        assert analyze_go(source) == {2, 10}

    def test_large_synthetic_file_compresses_every_function_body(self) -> None:
        n = 100
        chunks = [f"func func_{i}(x int) int {{\n  return x + {i}\n}}\n" for i in range(n)]
        source = "".join(chunks)
        result = analyze_go(source)
        # Each 3-line chunk's body is its own middle line: 2, 5, 8, ...
        assert result == {3 * i + 2 for i in range(n)}

    def test_missing_dependency_fails_open_with_warning(self) -> None:
        """Simulates quor[go] not being installed by blocking the two lazy
        imports analyze_go() performs internally — mirrors
        analyze_javascript()'s identical missing-dependency fail-open
        test."""
        import builtins

        real_import = builtins.__import__

        def _blocked(name: str, *args: object, **kwargs: object) -> object:
            if name in ("tree_sitter", "tree_sitter_go"):
                raise ImportError(f"simulated missing dependency: {name}")
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        with patch("builtins.__import__", side_effect=_blocked), warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = analyze_go("func f() {\n  return 1\n}\n")

        assert result == set()
        assert any("quor[go]" in str(w.message) for w in caught)


# ---------------------------------------------------------------------------
# analyze_java — QB-046, backed by tree-sitter/tree-sitter-java (real
# parser, not a mock — see module docstring). Every line-number assertion
# below was verified against the real, installed grammar's output during
# implementation, not guessed.
# ---------------------------------------------------------------------------


class TestAnalyzeJava:
    def test_empty_source_returns_empty_set(self) -> None:
        assert analyze_java("") == set()

    def test_whitespace_only_returns_empty_set(self) -> None:
        assert analyze_java("   \n\n  ") == set()

    def test_no_classes_returns_empty_set(self) -> None:
        assert analyze_java("import java.util.List;\n\n// just imports\n") == set()

    def test_simple_method_body_line_numbers(self) -> None:
        source = "public class Foo {\n  public int add(int x, int y) {\n    return x + y;\n  }\n}\n"
        # Line 1: class signature, line 2: method signature + opening brace,
        # line 3: body, line 4: method closing brace, line 5: class closing brace.
        assert analyze_java(source) == {3}

    def test_same_line_method_body_not_compressed(self) -> None:
        """A `block` whose open/close braces are on the same physical
        line — nothing meaningful to compress without touching the
        signature itself, mirrors go.py's identical rule."""
        assert analyze_java("public class Foo {\n  public int f() { return 1; }\n}\n") == set()

    def test_empty_body_not_compressed(self) -> None:
        assert analyze_java("public class Foo {\n  public void f() {\n  }\n}\n") == set()

    def test_constructor_body_compressed_field_declaration_preserved(self) -> None:
        """A constructor's body node is `constructor_body`, not `block`
        (empirically verified against the installed grammar) — this proves
        `_METHOD_LIKE_BLOCK_TYPES`'s per-member `block_type` selection
        actually matters, not just `method_declaration`'s."""
        source = (
            "public class Widget {\n"  # 1
            "  private int x;\n"  # 2
            "\n"  # 3
            "  public Widget(int x) {\n"  # 4
            "    this.x = x;\n"  # 5
            "  }\n"  # 6
            "}\n"  # 7
        )
        assert analyze_java(source) == {5}

    def test_extends_implements_and_method_signature_preserved(self) -> None:
        source = (
            "public class Widget extends Base implements Runnable {\n"  # 1
            "  public void run() {\n"  # 2
            '    System.out.println("running");\n'  # 3
            "  }\n"  # 4
            "}\n"  # 5
        )
        assert analyze_java(source) == {3}

    def test_javadoc_preceding_class_not_in_compress_set(self) -> None:
        """A Javadoc block is a sibling node entirely outside the class's
        own span — no Javadoc-specific logic is needed, mirrors
        javascript.py's identical JSDoc case."""
        source = (
            "/**\n * Add.\n */\npublic class Foo {\n"
            "  public int add(int x, int y) {\n    return x + y;\n  }\n}\n"
        )
        assert analyze_java(source) == {6}

    def test_lambda_field_block_body_compressed(self) -> None:
        source = (
            "public class Foo {\n"  # 1
            "  private Runnable handler = () -> {\n"  # 2
            '    System.out.println("lambda");\n'  # 3
            "  };\n"  # 4
            "}\n"  # 5
        )
        assert analyze_java(source) == {3}

    def test_expression_lambda_not_compressed(self) -> None:
        """A single-expression lambda (`() -> expr`) has no `block` body at
        all — mirrors JS's same-line arrow-function rule."""
        source = 'public class Foo {\n  private Runnable oneLiner = () -> System.out.println("x");\n}\n'
        assert analyze_java(source) == set()

    def test_interface_default_method_compressed_abstract_method_untouched(self) -> None:
        """An interface's abstract method (`greet(String name);`, no body
        at all) has no `block` field to compress — only the `default`
        method's real body is found."""
        source = (
            "interface Greeter {\n"  # 1
            "  String greet(String name);\n"  # 2
            "\n"  # 3
            "  default String defaultGreet() {\n"  # 4
            '    return "hi";\n'  # 5
            "  }\n"  # 6
            "}\n"  # 7
        )
        assert analyze_java(source) == {5}

    def test_nested_class_not_recursed_into(self) -> None:
        """A member class/interface nested inside another type's body is
        not itself visited — documented, deliberate scope boundary (see
        java.py's `_visit_type_body()` docstring)."""
        source = (
            "public class Outer {\n"
            "  class Inner {\n"
            "    void innerMethod() {\n"
            '      System.out.println("inner");\n'
            "    }\n"
            "  }\n"
            "}\n"
        )
        assert analyze_java(source) == set()

    def test_syntax_error_in_signature_excludes_swallowed_region(self) -> None:
        """A malformed method *signature* can make tree-sitter's error
        recovery swallow everything up to EOF into one ERROR node — the
        method before the error still compresses; nothing inside the
        swallowed region does, mirrors go.py's identical case."""
        source = (
            "public class Foo {\n"
            "  public int good1(int x) {\n"  # 2-4
            "    return x + 1;\n"
            "  }\n"
            "\n"  # 5
            "  public int broken( {\n"  # 6
            "    return 1;\n"  # 7
            "  }\n"  # 8
            "\n"  # 9
            "  public int good2(int y) {\n"  # 10
            "    return y + 2;\n"  # 11
            "  }\n"  # 12
            "}\n"  # 13
        )
        assert analyze_java(source) == {3, 11}

    def test_syntax_error_in_body_excludes_only_that_method(self) -> None:
        source = (
            "public class Foo {\n"
            "  public int good1(int x) {\n"  # 2-4
            "    return x + 1;\n"
            "  }\n"
            "\n"  # 5
            "  public int alsoBroken(int y) {\n"  # 6
            "    return y +++ * ;\n"  # 7
            "  }\n"  # 8
            "\n"  # 9
            "  public int good2(int z) {\n"  # 10
            "    return z + 2;\n"  # 11
            "  }\n"  # 12
            "}\n"  # 13
        )
        assert analyze_java(source) == {3, 11}

    def test_large_synthetic_file_compresses_every_method_body(self) -> None:
        n = 50
        chunks = [f"  public int func_{i}(int x) {{\n    return x + {i};\n  }}\n" for i in range(n)]
        source = "public class Foo {\n" + "".join(chunks) + "}\n"
        result = analyze_java(source)
        # Class signature is line 1; each 3-line chunk's body is its own
        # middle line: 3, 6, 9, ...
        assert result == {3 * i + 3 for i in range(n)}

    def test_missing_dependency_fails_open_with_warning(self) -> None:
        """Simulates quor[java] not being installed by blocking the two
        lazy imports analyze_java() performs internally — mirrors
        analyze_go()'s identical missing-dependency fail-open test."""
        import builtins

        real_import = builtins.__import__

        def _blocked(name: str, *args: object, **kwargs: object) -> object:
            if name in ("tree_sitter", "tree_sitter_java"):
                raise ImportError(f"simulated missing dependency: {name}")
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        with patch("builtins.__import__", side_effect=_blocked), warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = analyze_java("public class Foo {\n  public void f() {\n    return;\n  }\n}\n")

        assert result == set()
        assert any("quor[java]" in str(w.message) for w in caught)


# ---------------------------------------------------------------------------
# analyze_rust — QB-046, backed by tree-sitter/tree-sitter-rust (real
# parser, not a mock — see module docstring). Every line-number assertion
# below was verified against the real, installed grammar's output during
# implementation, not guessed.
# ---------------------------------------------------------------------------


class TestAnalyzeRust:
    def test_empty_source_returns_empty_set(self) -> None:
        assert analyze_rust("") == set()

    def test_whitespace_only_returns_empty_set(self) -> None:
        assert analyze_rust("   \n\n  ") == set()

    def test_no_functions_returns_empty_set(self) -> None:
        source = "use std::fmt;\n\nconst FOO: i32 = 1;\n"
        assert analyze_rust(source) == set()

    def test_simple_function_body_line_numbers(self) -> None:
        source = "fn add(x: i32, y: i32) -> i32 {\n  return x + y;\n}\n"
        # Line 1: signature + opening brace, line 2: body, line 3: closing brace.
        assert analyze_rust(source) == {2}

    def test_same_line_function_body_not_compressed(self) -> None:
        """A `block` whose open/close braces are on the same physical
        line — nothing meaningful to compress without touching the
        signature itself, mirrors go.py's identical rule."""
        assert analyze_rust("fn f() { return 1; }\n") == set()

    def test_empty_body_not_compressed(self) -> None:
        assert analyze_rust("fn f() {\n}\n") == set()

    def test_method_body_compressed_struct_and_impl_header_preserved(self) -> None:
        """A method lives inside an `impl` block's own `declaration_list`
        body — one level deeper than a top-level `function_item` — see
        rust.py's module docstring. The struct's own field lines are
        untouched: `struct_item` is never visited as a function-like
        candidate."""
        source = (
            "struct Widget {\n"  # 1
            "    x: i32,\n"  # 2
            "}\n"  # 3
            "\n"  # 4
            "impl Widget {\n"  # 5
            "    fn render(&self) -> String {\n"  # 6
            '        String::from("hi")\n'  # 7
            "    }\n"  # 8
            "}\n"  # 9
        )
        assert analyze_rust(source) == {7}

    def test_doc_comment_preceding_function_not_in_compress_set(self) -> None:
        """A `///` doc comment is a sibling node entirely outside the
        function's own span — mirrors go.py/java.py's identical doc-comment
        case."""
        source = "/// Add returns the sum.\nfn add(x: i32, y: i32) -> i32 {\n  return x + y;\n}\n"
        assert analyze_rust(source) == {3}

    def test_trait_default_method_compressed_signature_only_untouched(self) -> None:
        """A trait method with no default implementation
        (`fn greet(&self) -> String;`) is its own distinct node type,
        `function_signature_item` — not a `function_item` with an absent
        body — so it has no `block` field to compress at all; only the
        default method's real body is found. Mirrors java.py's interface
        default-method case."""
        source = (
            "trait Greeter {\n"  # 1
            "    fn greet(&self) -> String;\n"  # 2
            "\n"  # 3
            "    fn default_greet(&self) -> String {\n"  # 4
            '        String::from("hi")\n'  # 5
            "    }\n"  # 6
            "}\n"  # 7
        )
        assert analyze_rust(source) == {5}

    def test_trait_impl_header_preserved(self) -> None:
        source = (
            "impl Shape for Circle {\n"  # 1
            "    fn area(&self) -> f64 {\n"  # 2
            "        1.0\n"  # 3
            "    }\n"  # 4
            "}\n"  # 5
        )
        assert analyze_rust(source) == {3}

    def test_closure_inside_function_body_not_separately_recursed(self) -> None:
        """A closure nested inside a function's body is implementation
        detail of the outer function — its lines are covered by the outer
        function's own compress range, not visited or excluded as a
        candidate of its own (mirrors python.py/javascript.py/go.py's
        identical "no further recursion" rule)."""
        source = (
            "fn outer() {\n"  # 1
            "    let f = || {\n"  # 2
            '        println!("hi");\n'  # 3
            "    };\n"  # 4
            "}\n"  # 5
        )
        assert analyze_rust(source) == {2, 3, 4}

    def test_module_not_recursed_into(self) -> None:
        """A `mod` block nested inside a file is not itself visited —
        documented, deliberate scope boundary (see rust.py's
        `_visit_top_level()` docstring), mirroring java.py's identical
        "member class/interface/enum nested inside this body" limitation."""
        source = (
            "mod inner {\n"
            "    fn helper() {\n"
            "        do_thing();\n"
            "    }\n"
            "}\n"
        )
        assert analyze_rust(source) == set()

    def test_syntax_error_in_signature_excludes_swallowed_region(self) -> None:
        """A malformed function *signature* can make tree-sitter's error
        recovery swallow everything up to EOF into one ERROR node — the
        function before the error still compresses; nothing inside the
        swallowed region does, mirrors go.py's identical case."""
        source = (
            "fn good1(x: i32) -> i32 {\n"  # 1-3
            "  return x + 1;\n"
            "}\n"
            "\n"  # 4
            "fn broken( {\n"  # 5
            "  return 1;\n"  # 6
            "}\n"  # 7
            "\n"  # 8
            "fn good2(y: i32) -> i32 {\n"  # 9
            "  return y + 2;\n"  # 10
            "}\n"  # 11
        )
        assert analyze_rust(source) == {2, 10}

    def test_syntax_error_in_body_excludes_only_that_function(self) -> None:
        source = (
            "fn good1(x: i32) -> i32 {\n"  # 1-3
            "  return x + 1;\n"
            "}\n"
            "\n"  # 4
            "fn also_broken(y: i32) -> i32 {\n"  # 5
            "  return y +++ * ;\n"  # 6
            "}\n"  # 7
            "\n"  # 8
            "fn good2(z: i32) -> i32 {\n"  # 9
            "  return z + 2;\n"  # 10
            "}\n"  # 11
        )
        assert analyze_rust(source) == {2, 10}

    def test_large_synthetic_file_compresses_every_function_body(self) -> None:
        n = 100
        chunks = [f"fn func_{i}(x: i32) -> i32 {{\n  return x + {i};\n}}\n" for i in range(n)]
        source = "".join(chunks)
        result = analyze_rust(source)
        # Each 3-line chunk's body is its own middle line: 2, 5, 8, ...
        assert result == {3 * i + 2 for i in range(n)}

    def test_missing_dependency_fails_open_with_warning(self) -> None:
        """Simulates quor[rust] not being installed by blocking the two
        lazy imports analyze_rust() performs internally — mirrors
        analyze_go()'s identical missing-dependency fail-open test."""
        import builtins

        real_import = builtins.__import__

        def _blocked(name: str, *args: object, **kwargs: object) -> object:
            if name in ("tree_sitter", "tree_sitter_rust"):
                raise ImportError(f"simulated missing dependency: {name}")
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        with patch("builtins.__import__", side_effect=_blocked), warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = analyze_rust("fn f() {\n  return 1;\n}\n")

        assert result == set()
        assert any("quor[rust]" in str(w.message) for w in caught)


# ---------------------------------------------------------------------------
# analyze_csharp — QB-046, backed by tree-sitter/tree-sitter-c-sharp (real
# parser, not a mock — see module docstring). Every line-number assertion
# below was verified against the real, installed grammar's output during
# implementation, not guessed.
# ---------------------------------------------------------------------------


class TestAnalyzeCSharp:
    def test_empty_source_returns_empty_set(self) -> None:
        assert analyze_csharp("") == set()

    def test_whitespace_only_returns_empty_set(self) -> None:
        assert analyze_csharp("   \n\n  ") == set()

    def test_no_classes_returns_empty_set(self) -> None:
        assert analyze_csharp("using System;\n\n// just a using directive\n") == set()

    def test_simple_method_body_line_numbers(self) -> None:
        source = "public class Foo\n{\n  public int Add(int x, int y)\n  {\n    return x + y;\n  }\n}\n"
        # Line 1: class signature, line 2: opening brace, line 3: method
        # signature, line 4: method opening brace, line 5: body, line 6:
        # method closing brace, line 7: class closing brace.
        assert analyze_csharp(source) == {5}

    def test_same_line_method_body_not_compressed(self) -> None:
        """A `block` whose open/close braces are on the same physical
        line — nothing meaningful to compress without touching the
        signature itself, mirrors go.py's identical rule."""
        assert analyze_csharp("public class Foo\n{\n  public int F() { return 1; }\n}\n") == set()

    def test_empty_body_not_compressed(self) -> None:
        assert analyze_csharp("public class Foo\n{\n  public void F()\n  {\n  }\n}\n") == set()

    def test_constructor_body_compressed_field_declaration_preserved(self) -> None:
        source = (
            "public class Widget\n"  # 1
            "{\n"  # 2
            "  private int x;\n"  # 3
            "\n"  # 4
            "  public Widget(int x)\n"  # 5
            "  {\n"  # 6
            "    this.x = x;\n"  # 7
            "  }\n"  # 8
            "}\n"  # 9
        )
        assert analyze_csharp(source) == {7}

    def test_base_list_and_method_signature_preserved(self) -> None:
        source = (
            "public class Widget : Base, IRunnable\n"  # 1
            "{\n"  # 2
            "  public void Run()\n"  # 3
            "  {\n"  # 4
            '    Console.WriteLine("running");\n'  # 5
            "  }\n"  # 6
            "}\n"  # 7
        )
        assert analyze_csharp(source) == {5}

    def test_xml_doc_comment_preceding_class_not_in_compress_set(self) -> None:
        """An XML doc comment (`///`) is a sibling `comment` node entirely
        outside the class's own span — no doc-comment-specific logic is
        needed, mirrors java.py's identical Javadoc case."""
        source = (
            "/// <summary>\n/// Add.\n/// </summary>\npublic class Foo\n{\n"
            "  public int Add(int x, int y)\n  {\n    return x + y;\n  }\n}\n"
        )
        assert analyze_csharp(source) == {8}

    def test_lambda_field_block_body_compressed(self) -> None:
        source = (
            "public class Foo\n"  # 1
            "{\n"  # 2
            "  private Action handler = () =>\n"  # 3
            "  {\n"  # 4
            '    Console.WriteLine("lambda");\n'  # 5
            "  };\n"  # 6
            "}\n"  # 7
        )
        assert analyze_csharp(source) == {5}

    def test_expression_lambda_not_compressed(self) -> None:
        """A single-expression lambda (`() => expr`) has no `block` body at
        all — mirrors JS's same-line arrow-function rule."""
        source = 'public class Foo\n{\n  private Action oneLiner = () => Console.WriteLine("x");\n}\n'
        assert analyze_csharp(source) == set()

    def test_interface_default_method_compressed_abstract_method_untouched(self) -> None:
        """An interface's abstract method (`string Greet(string name);`, no
        body at all) has no `block` field to compress — only the default
        method's real body is found."""
        source = (
            "interface IGreeter\n"  # 1
            "{\n"  # 2
            "  string Greet(string name);\n"  # 3
            "\n"  # 4
            "  string DefaultGreet()\n"  # 5
            "  {\n"  # 6
            '    return "hi";\n'  # 7
            "  }\n"  # 8
            "}\n"  # 9
        )
        assert analyze_csharp(source) == {7}

    def test_struct_method_body_compressed(self) -> None:
        """`struct_declaration` shares `class_declaration`'s own body node
        type in this grammar (see csharp.py's module docstring) — this
        proves it is actually reached, not just class/interface."""
        source = (
            "public struct Point\n"  # 1
            "{\n"  # 2
            "  public int Add(int a, int b)\n"  # 3
            "  {\n"  # 4
            "    return a + b;\n"  # 5
            "  }\n"  # 6
            "}\n"  # 7
        )
        assert analyze_csharp(source) == {5}

    def test_block_scoped_namespace_unwrapped(self) -> None:
        """A block-scoped `namespace X { ... }` wraps its class in its own
        `declaration_list` body — this proves `_visit_top_level()` actually
        unwraps it, not just walks `compilation_unit`'s direct children
        (see csharp.py's module docstring)."""
        source = (
            "namespace Widgets\n"  # 1
            "{\n"  # 2
            "  public class Foo\n"  # 3
            "  {\n"  # 4
            "    public int Add(int x, int y)\n"  # 5
            "    {\n"  # 6
            "      return x + y;\n"  # 7
            "    }\n"  # 8
            "  }\n"  # 9
            "}\n"  # 10
        )
        assert analyze_csharp(source) == {7}

    def test_file_scoped_namespace_needs_no_unwrapping(self) -> None:
        """A file-scoped `namespace X;` (C# 10+) does not wrap anything —
        the class stays a direct top-level sibling, reached without any
        unwrapping at all (see csharp.py's module docstring)."""
        source = (
            "namespace Widgets;\n"  # 1
            "\n"  # 2
            "public class Foo\n"  # 3
            "{\n"  # 4
            "  public int Add(int x, int y)\n"  # 5
            "  {\n"  # 6
            "    return x + y;\n"  # 7
            "  }\n"  # 8
            "}\n"  # 9
        )
        assert analyze_csharp(source) == {7}

    def test_nested_class_not_recursed_into(self) -> None:
        """A member class nested inside another type's body is not itself
        visited — documented, deliberate scope boundary (see csharp.py's
        `_visit_type_body()` docstring)."""
        source = (
            "public class Outer\n"
            "{\n"
            "  class Inner\n"
            "  {\n"
            "    void InnerMethod()\n"
            "    {\n"
            '      Console.WriteLine("inner");\n'
            "    }\n"
            "  }\n"
            "}\n"
        )
        assert analyze_csharp(source) == set()

    def test_syntax_error_in_signature_excludes_swallowed_region(self) -> None:
        """A malformed method *signature* (missing closing paren) makes
        tree-sitter's error recovery swallow the broken method into one
        ERROR node — the method before the error still compresses; nothing
        inside the swallowed region does, mirrors go.py's identical case.
        Line 3: `Good1` signature, line 5: its body (compressed), line 8:
        `Broken`'s malformed signature (swallowed, excluded entirely), line
        13: `Good2` signature, line 15: its body (compressed)."""
        source = (
            "public class Foo\n"  # 1
            "{\n"  # 2
            "  public int Good1(int x)\n"  # 3
            "  {\n"  # 4
            "    return x + 1;\n"  # 5
            "  }\n"  # 6
            "\n"  # 7
            "  public int Broken(\n"  # 8
            "  {\n"  # 9
            "    return 1;\n"  # 10
            "  }\n"  # 11
            "\n"  # 12
            "  public int Good2(int y)\n"  # 13
            "  {\n"  # 14
            "    return y + 2;\n"  # 15
            "  }\n"  # 16
            "}\n"  # 17
        )
        assert analyze_csharp(source) == {5, 15}

    def test_syntax_error_in_body_excludes_only_that_method(self) -> None:
        """A `$` token is not valid anywhere in this grammar, so — unlike a
        malformed expression built entirely from otherwise-legal operators
        (empirically found, while implementing this module, to sometimes
        produce only a zero-width `has_error`-flagged leaf that neither
        `collect_error_ranges()`'s `ERROR`-type nor `is_missing` check
        catches) — it reliably produces a genuine `ERROR` node, exercising
        the same overlap-exclusion mechanism go.py/java.py's identical
        tests exercise."""
        source = (
            "public class Foo\n"  # 1
            "{\n"  # 2
            "  public int Good1(int x)\n"  # 3
            "  {\n"  # 4
            "    return x + 1;\n"  # 5
            "  }\n"  # 6
            "\n"  # 7
            "  public int AlsoBroken(int y)\n"  # 8
            "  {\n"  # 9
            "    return y $ y;\n"  # 10
            "  }\n"  # 11
            "\n"  # 12
            "  public int Good2(int z)\n"  # 13
            "  {\n"  # 14
            "    return z + 2;\n"  # 15
            "  }\n"  # 16
            "}\n"  # 17
        )
        assert analyze_csharp(source) == {5, 15}

    def test_large_synthetic_file_compresses_every_method_body(self) -> None:
        n = 50
        chunks = [
            f"  public int Func{i}(int x)\n  {{\n    return x + {i};\n  }}\n" for i in range(n)
        ]
        source = "public class Foo\n{\n" + "".join(chunks) + "}\n"
        result = analyze_csharp(source)
        # Class signature is line 1, opening brace line 2; each 4-line
        # chunk's body is its own 3rd line: 5, 9, 13, ...
        assert result == {4 * i + 5 for i in range(n)}

    def test_missing_dependency_fails_open_with_warning(self) -> None:
        """Simulates quor[csharp] not being installed by blocking the two
        lazy imports analyze_csharp() performs internally — mirrors
        analyze_go()'s identical missing-dependency fail-open test."""
        import builtins

        real_import = builtins.__import__

        def _blocked(name: str, *args: object, **kwargs: object) -> object:
            if name in ("tree_sitter", "tree_sitter_c_sharp"):
                raise ImportError(f"simulated missing dependency: {name}")
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        with patch("builtins.__import__", side_effect=_blocked), warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = analyze_csharp("public class Foo\n{\n  public void F()\n  {\n    return;\n  }\n}\n")

        assert result == set()
        assert any("quor[csharp]" in str(w.message) for w in caught)
