"""Unit tests for quor/pipeline/ast_summarize/ — QB-005B/QB-005C/QB-005D.

Covers the AST summarization framework in isolation, independent of either
stage that consumes it (python_ast_summarize.py, code_ast_summarize.py —
see tests/unit/test_stages.py for those): the language -> analyzer routing
table (registry.py) and the per-language analyzers (python.py,
javascript.py, typescript.py). Mirrors tests/unit/test_extract.py's own
separation of "framework tests, patching internals directly" from
"stage/handler tests." Kept as one file covering every registered language
(rather than QB-005A's original per-language-file suggestion) to match the
precedent QB-005B actually established here, not the design doc's initial,
pre-implementation guess.

`tree-sitter`/`tree-sitter-javascript`/`tree-sitter-typescript` (QB-005C/D,
`quor[javascript]`) are listed in the `dev` extra (see pyproject.toml), so
`TestAnalyzeJavaScript`/`TestAnalyzeTypeScript` below exercise the *real*
parser, not a mock — the same "real fixture coverage" precedent
`tests/unit/test_extract_docx.py`/`test_extract_pdf.py` already established
for python-docx/pdfplumber.
"""

from __future__ import annotations

import warnings
from unittest.mock import patch

import pytest

from quor.pipeline.ast_summarize.javascript import analyze_javascript
from quor.pipeline.ast_summarize.python import analyze_python
from quor.pipeline.ast_summarize.registry import _ANALYZERS, get_analyzer, registered_languages
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
        """After QB-005D, python/javascript/typescript/tsx are registered
        and nothing else — explicitly asserts the current scope boundary
        (no Go/Rust/Java/etc.) so a future language addition is a visible,
        intentional test change here, not a silent expansion. (This test's
        own history: QB-005B's original test asserted "javascript" and
        "typescript" were both absent; QB-005C flipped "javascript" to
        registered and renamed it; QB-005D now flips "typescript"/"tsx"
        too — each rename documents which phase changed the boundary.)"""
        assert _ANALYZERS.keys() == {"python", "javascript", "typescript", "tsx"}


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
