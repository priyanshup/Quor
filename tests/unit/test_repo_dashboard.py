"""Unit tests for the Repository Dashboard aggregation (QB-076).

Fixtures write directly to `intel_store`'s cache files (rather than running
a real `quor map`/`quor symbols`/`quor graph` build against source files)
so these tests exercise `build_dashboard()`'s own aggregation logic in
isolation — and, just as importantly, so they double as a guarantee that
`build_dashboard()` never re-parses or re-walks anything: there is no
source file on disk for it to read even if it tried.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from quor.pipeline.ast_summarize.relationship_model import Relationship
from quor.pipeline.ast_summarize.symbol_model import Symbol
from quor.pipeline.repo_profile import intel_store
from quor.pipeline.repo_profile.dashboard import build_dashboard
from quor.pipeline.repo_profile.graph import FileFacts
from quor.pipeline.repo_profile.intel_model import RepoIntelState
from quor.pipeline.repo_profile.model import LanguageStat, RepoProfile, RepoStatistics
from quor.pipeline.repo_profile.symbols_model import FileSymbols


def _write_full_cache(root: Path, *, last_completed_build: str | None = None) -> None:
    """Populate all four cache files `build_dashboard()` reads, mirroring a
    two-file Python repo (`a.py` importing/calling into `b.py`) plus one
    recorded parse failure per artifact."""
    now = last_completed_build or datetime.now(UTC).isoformat()
    intel_store.save_state(
        root,
        RepoIntelState(
            schema_version=1,
            quor_version="0.0.0",
            repo_root=root.resolve().as_posix(),
            git_head="abcdef0123456789",
            file_count=2,
            last_scan_timestamp=now,
            last_completed_build=now,
            fingerprints={},
        ),
    )
    intel_store.save_profile(
        root,
        RepoProfile(
            root=root.resolve().as_posix(),
            languages=[LanguageStat(language="Python", file_count=2, percentage=100.0)],
            statistics=RepoStatistics(
                total_files=2, total_directories=1, primary_language="Python", git_commit_count=1
            ),
            notes=["Not a git repository — used filesystem walk fallback."],
        ),
    )

    symbol_files = {
        "a.py": FileSymbols(
            path="a.py",
            language="python",
            symbols=[
                Symbol(name="Foo", kind="class", line=1, is_public=True),
                Symbol(name="helper", kind="function", line=10, is_public=False),
            ],
        ),
        "b.py": FileSymbols(
            path="b.py", language="python", symbols=[Symbol(name="Bar", kind="class", line=1, is_public=True)]
        ),
    }
    intel_store.save_symbol_facts(root, symbol_files, {"broken.py"})

    graph_facts = {
        "a.py": FileFacts(
            language="python",
            symbol_counts=Counter({"Foo": 1, "helper": 1}),
            relationships=[
                Relationship(kind="import", source="", target=".b", line=1, qualifier="b"),
                Relationship(kind="calls", source="helper", target="Bar", line=11, qualifier="b"),
            ],
        ),
        "b.py": FileFacts(language="python", symbol_counts=Counter({"Bar": 1}), relationships=[]),
    }
    intel_store.save_graph_facts(root, graph_facts, {"broken_graph.py"})


class TestBuildDashboardMissingCache:
    def test_returns_none_when_no_cache_exists(self, tmp_path: Path) -> None:
        assert build_dashboard(tmp_path) is None

    def test_returns_none_when_cache_is_partial(self, tmp_path: Path) -> None:
        _write_full_cache(tmp_path)
        (intel_store.cache_dir(tmp_path) / "graph_facts.json").unlink()

        assert build_dashboard(tmp_path) is None

    def test_returns_none_when_a_cache_file_is_corrupted(self, tmp_path: Path) -> None:
        _write_full_cache(tmp_path)
        (intel_store.cache_dir(tmp_path) / "state.json").write_text("not json", encoding="utf-8")

        assert build_dashboard(tmp_path) is None


class TestBuildDashboardAggregation:
    def test_reuses_profile_and_state_fields_verbatim(self, tmp_path: Path) -> None:
        _write_full_cache(tmp_path)

        dashboard = build_dashboard(tmp_path)

        assert dashboard is not None
        assert dashboard.root == tmp_path.resolve().as_posix()
        assert dashboard.git_head == "abcdef0123456789"
        assert dashboard.total_files == 2
        assert dashboard.total_directories == 1
        assert dashboard.primary_language == "Python"
        assert dashboard.languages[0].language == "Python"
        assert dashboard.languages[0].percentage == 100.0
        assert dashboard.profile_notes == ["Not a git repository — used filesystem walk fallback."]

    def test_computes_symbol_totals_from_cached_facts(self, tmp_path: Path) -> None:
        _write_full_cache(tmp_path)

        dashboard = build_dashboard(tmp_path)

        assert dashboard is not None
        assert dashboard.total_symbols == 3
        assert dashboard.symbols_by_language == {"python": 3}
        assert dashboard.symbol_parse_failures == 1

    def test_resolves_graph_edges_and_ranks_most_connected(self, tmp_path: Path) -> None:
        _write_full_cache(tmp_path)

        dashboard = build_dashboard(tmp_path)

        assert dashboard is not None
        assert dashboard.graph_edges == 2
        assert dashboard.graph_resolved_edges == 2  # both the import and the qualified call resolve
        assert dashboard.relationship_counts == {"import": 1, "calls": 1}
        assert dashboard.graph_parse_failures == 1
        assert dashboard.graph_nodes == 2

        # Both edges have source_file="a.py" (outgoing=2) and target_file="b.py"
        # (incoming=2) — tied at total=2, alphabetical tie-break puts a.py first.
        top = dashboard.most_connected_files[0]
        assert top.path == "a.py"
        assert (top.outgoing, top.incoming, top.total) == (2, 0, 2)
        second = dashboard.most_connected_files[1]
        assert second.path == "b.py"
        assert (second.outgoing, second.incoming, second.total) == (0, 2, 2)

    def test_ranks_largest_modules_by_symbol_count(self, tmp_path: Path) -> None:
        _write_full_cache(tmp_path)

        dashboard = build_dashboard(tmp_path)

        assert dashboard is not None
        assert [m.path for m in dashboard.largest_modules] == ["a.py", "b.py"]
        assert dashboard.largest_modules[0].symbol_count == 2
        assert dashboard.largest_modules[1].symbol_count == 1

    def test_cache_age_reflects_last_completed_build(self, tmp_path: Path) -> None:
        stale = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        _write_full_cache(tmp_path, last_completed_build=stale)

        dashboard = build_dashboard(tmp_path)

        assert dashboard is not None
        assert 7200 <= dashboard.cache_age_seconds < 7260
