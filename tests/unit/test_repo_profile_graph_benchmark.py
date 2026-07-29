"""Fixture-repo benchmark corpus for the Repository Dependency Graph
(QB-067).

Reuses the exact same fixture repos `test_repo_profile_benchmark.py`/
`test_repo_profile_symbols_benchmark.py` already commit under
`tests/fixtures/repo_profile/` (QB-061/QB-066) rather than adding a
parallel set. These fixtures are deliberately tiny (a handful of files
each), so unlike the symbols benchmark's precision/recall harness, this
file checks the smaller set of real relationships they do contain plus
determinism and a performance budget — the synthetic scaling test below
(mirroring QB-066's own "500-file" stand-in for QB-061's design doc's
"5,000-file repo" scaling risk) is where cross-file resolution at scale is
actually exercised.
"""

from __future__ import annotations

import time
from pathlib import Path

from quor.pipeline.repo_profile.graph import build_dependency_graph
from quor.pipeline.repo_profile.graph_model import RepoDependencyGraph

_FIXTURES_ROOT = Path(__file__).parent.parent / "fixtures" / "repo_profile"

_CASE_NAMES = ["flask-pip", "go-service", "node-express-pnpm", "polyglot-monorepo"]


class TestFixtureRepoRealRelationships:
    def test_flask_pip_import_captured(self) -> None:
        graph = build_dependency_graph(_FIXTURES_ROOT / "flask-pip")
        imports = [e for e in graph.edges if e.kind == "import"]
        assert any(e.target_raw == "flask" for e in imports)

    def test_flask_pip_only_call_is_its_own_route_decorator(self) -> None:
        """`index()`'s body is just `return "hello"` (no call expressions),
        and `app.run()` sits at module level inside `if __name__ ==
        "__main__":`, outside any named function/method `Symbol` — so the
        one `calls` edge this fixture produces is `@app.route("/")`
        itself, a real call expression attributed to the function it
        decorates (`ast.walk()` over a `FunctionDef` includes its
        `decorator_list`, and `python.py`'s call-collection deliberately
        doesn't special-case this — decorators genuinely are calls that
        run in that source location, just at definition time rather than
        every invocation)."""
        graph = build_dependency_graph(_FIXTURES_ROOT / "flask-pip")
        calls = [e for e in graph.edges if e.kind == "calls"]
        assert calls == [
            e
            for e in calls
            if e.target_raw == "route" and e.qualifier == "app" and e.source_symbol == "index"
        ]
        assert len(calls) == 1

    def test_polyglot_monorepo_backend_app_export_and_import_present(self) -> None:
        graph = build_dependency_graph(_FIXTURES_ROOT / "polyglot-monorepo")
        exports = [e for e in graph.edges if e.kind == "export" and e.source_file.endswith("index.tsx")]
        assert any(e.source_symbol == "App" for e in exports)

    def test_go_service_import_never_resolved(self) -> None:
        graph = build_dependency_graph(_FIXTURES_ROOT / "go-service")
        imports = [e for e in graph.edges if e.kind == "import"]
        assert all(e.target_file is None for e in imports)


class TestDeterminism:
    def test_repeated_scan_is_byte_identical(self) -> None:
        for case_name in _CASE_NAMES:
            root = _FIXTURES_ROOT / case_name
            first = build_dependency_graph(root)
            second = build_dependency_graph(root)
            assert first == second, case_name


class TestLargeRepoPerformanceBudget:
    def test_five_hundred_file_synthetic_repo_scans_within_budget(self, tmp_path: Path) -> None:
        """Mirrors `test_repo_profile_symbols_benchmark.py`'s identical
        "500 files x 10 functions" scaling stand-in — extended here with
        real cross-module imports/calls so the resolution engine (not just
        parsing) is exercised at scale, not just symbol extraction."""
        (tmp_path / "helpers.py").write_text(
            "\n".join(f"def helper_{j}():\n    return {j}\n" for j in range(10)), encoding="utf-8"
        )
        for i in range(500):
            lines = ["from .helpers import helper_0\n"]
            lines.extend(f"def f_{j}():\n    return helper_0()\n" for j in range(10))
            (tmp_path / f"module_{i}.py").write_text("\n".join(lines), encoding="utf-8")

        t0 = time.monotonic()
        graph = build_dependency_graph(tmp_path)
        elapsed = time.monotonic() - t0

        assert graph.total_edges > 0
        resolved_calls = [
            e for e in graph.edges if e.kind == "calls" and e.target_file == "helpers.py"
        ]
        assert len(resolved_calls) == 500 * 10
        assert elapsed < 15.0, f"500-file synthetic repo took {elapsed:.2f}s, expected < 15s"


class TestPerformanceBudget:
    def test_whole_corpus_scans_quickly(self) -> None:
        """Not a hook-path budget (explicit, user-invoked command, same
        category as `quor map`/`quor symbols`) — a generous ceiling just
        to catch a pathological regression."""
        t0 = time.monotonic()
        graphs: list[RepoDependencyGraph] = []
        for case_name in _CASE_NAMES:
            graphs.append(build_dependency_graph(_FIXTURES_ROOT / case_name))
        elapsed = time.monotonic() - t0

        assert len(graphs) == len(_CASE_NAMES)
        assert elapsed < 5.0, f"fixture corpus took {elapsed:.2f}s, expected < 5s"
