"""Fixture-repo benchmark corpus for Repository Symbols (QB-066).

Reuses the exact same fixture repos `test_repo_profile_benchmark.py`
already commits under `tests/fixtures/repo_profile/` (QB-061) rather than
adding a parallel set — same corpus, a second, symbol-shaped set of
expectations layered on top. Mirrors that file's own structure: precision/
recall against hand-labeled expected symbols (verified by reading each
fixture file directly, not guessed), a false-positive check, a
byte-identical determinism check, and a performance budget.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from quor.pipeline.repo_profile.symbols import build_symbol_index
from quor.pipeline.repo_profile.symbols_model import RepoSymbolIndex

_FIXTURES_ROOT = Path(__file__).parent.parent / "fixtures" / "repo_profile"


@dataclass(frozen=True)
class ExpectedSymbols:
    case_name: str
    expected_names: frozenset[str] = field(default_factory=frozenset)
    """Symbol names that must be found somewhere in the index."""

    expected_entry_points: frozenset[str] = field(default_factory=frozenset)
    """Symbol names that must be found with `is_entry_point=True`."""

    must_not_detect: frozenset[str] = field(default_factory=frozenset)


_CASES: list[ExpectedSymbols] = [
    ExpectedSymbols(
        case_name="flask-pip",
        expected_names=frozenset({"index"}),
        must_not_detect=frozenset({"main", "app"}),
    ),
    ExpectedSymbols(
        case_name="go-service",
        expected_names=frozenset({"main"}),
        expected_entry_points=frozenset({"main"}),
    ),
    ExpectedSymbols(
        case_name="node-express-pnpm",
        # index.js's only "declarations" are `const express = require(...)`/
        # `const app = express()` — neither is a function-like value, so a
        # correct extractor finds nothing here; asserted via
        # test_no_false_positives/the dedicated "no symbols" test below,
        # not expected_names (nothing to require).
        must_not_detect=frozenset({"express", "app", "listen", "get"}),
    ),
    ExpectedSymbols(
        case_name="polyglot-monorepo",
        expected_names=frozenset({"main", "App"}),
        expected_entry_points=frozenset({"main"}),
    ),
]


def _all_names(index: RepoSymbolIndex) -> frozenset[str]:
    return frozenset(symbol.name for file_symbols in index.files for symbol in file_symbols.symbols)


def _entry_point_names(index: RepoSymbolIndex) -> frozenset[str]:
    return frozenset(
        symbol.name
        for file_symbols in index.files
        for symbol in file_symbols.symbols
        if symbol.is_entry_point
    )


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c.case_name)
class TestFixtureRepoPrecisionRecall:
    def test_expected_symbols_found(self, case: ExpectedSymbols) -> None:
        index = build_symbol_index(_FIXTURES_ROOT / case.case_name)

        assert case.expected_names <= _all_names(index)
        assert case.expected_entry_points <= _entry_point_names(index)

    def test_no_false_positives(self, case: ExpectedSymbols) -> None:
        index = build_symbol_index(_FIXTURES_ROOT / case.case_name)

        false_positives = case.must_not_detect & _all_names(index)
        assert not false_positives, f"false positives in {case.case_name}: {false_positives}"


class TestNodeExpressHasNoSymbols:
    def test_index_js_has_no_named_declarations(self) -> None:
        """`index.js` only has `require`/inline arrow-function-as-argument
        shapes, neither of which is a named top-level declaration — the
        precise case `extract_symbols_javascript()`'s own docstring
        documents as correctly producing no symbols, not a gap."""
        index = build_symbol_index(_FIXTURES_ROOT / "node-express-pnpm")
        assert index.files == []


class TestDeterminism:
    @pytest.mark.parametrize("case", _CASES, ids=lambda c: c.case_name)
    def test_repeated_scan_is_byte_identical(self, case: ExpectedSymbols) -> None:
        root = _FIXTURES_ROOT / case.case_name

        first = build_symbol_index(root)
        second = build_symbol_index(root)

        assert first == second


class TestLargeRepoPerformanceBudget:
    def test_five_hundred_file_synthetic_repo_scans_within_budget(self, tmp_path: Path) -> None:
        """The committed fixture corpus above is deliberately tiny (real,
        hand-labeled files) — this synthetic repo exercises the scaling
        axis QB-061's own design doc flagged as an explicit, unresolved
        risk for this future phase (§7 risk 4: "per-file AST symbol
        extraction ... needs an explicit cap/sampling strategy plus its own
        performance budget"). 500 files x 10 functions is a deliberately
        modest stand-in for that doc's own "5,000-file repo" example scale
        (kept smaller here so the default, non---integration test suite
        stays fast — see CLAUDE.md's 30s default-suite budget); the ratio
        this asserts (files-per-second) is the meaningful signal, not the
        absolute file count.
        """
        for i in range(500):
            functions = "\n".join(f"def f_{j}():\n    return {j}\n" for j in range(10))
            (tmp_path / f"module_{i}.py").write_text(functions, encoding="utf-8")

        t0 = time.monotonic()
        index = build_symbol_index(tmp_path)
        elapsed = time.monotonic() - t0

        assert index.total_symbols == 500 * 10
        assert elapsed < 10.0, f"500-file synthetic repo took {elapsed:.2f}s, expected < 10s"


class TestPerformanceBudget:
    def test_whole_corpus_scans_quickly(self) -> None:
        """Not a hook-path budget (this is an explicit, user-invoked
        command, same category as `quor map` — see
        docs/design/QB-061-repo-context-profile.md §8) — a generous
        ceiling just to catch a pathological regression (e.g. an
        accidental O(n^2) scan, or a parser re-instantiated per line
        instead of per file)."""
        t0 = time.monotonic()
        for case in _CASES:
            build_symbol_index(_FIXTURES_ROOT / case.case_name)
        elapsed = time.monotonic() - t0

        assert elapsed < 5.0, f"fixture corpus took {elapsed:.2f}s, expected < 5s"
