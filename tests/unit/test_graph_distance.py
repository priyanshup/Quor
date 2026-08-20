"""Unit tests for `quor/pipeline/repo_profile/graph_distance.py` — the
breadth-first hop-distance tiering `get_repo_context` uses to annotate
"Relevant repository files" matches relative to a focus file (metadata
enrichment). Mirrors `test_repo_search.py`'s own `_entry()` helper
convention for building `FileIntelligenceEntry` fixtures.
"""

from __future__ import annotations

from quor.pipeline.repo_profile.graph_distance import DEFAULT_MAX_HOPS, compute_hop_distances
from quor.pipeline.repo_profile.intel_model import FileIntelligenceEntry


def _entry(*, imported_files: list[str] | None = None) -> FileIntelligenceEntry:
    return FileIntelligenceEntry(
        language="python",
        kind="source",
        importance="Low",
        imported_files=imported_files or [],
    )


class TestStartIsAlwaysZero:
    def test_start_file_gets_distance_zero(self) -> None:
        entries = {"a.py": _entry()}
        distances = compute_hop_distances(entries, "a.py")
        assert distances["a.py"] == 0

    def test_start_not_in_entries_still_returns_itself_at_zero(self) -> None:
        """A file with no cached entry of its own (untracked/new) can still
        ask "what's near me" via its own would-be neighbors — there just
        aren't any to expand from, so only {start: 0} comes back."""
        distances = compute_hop_distances({}, "unknown.py")
        assert distances == {"unknown.py": 0}


class TestOneHop:
    def test_direct_outgoing_import_is_one_hop(self) -> None:
        entries = {
            "a.py": _entry(imported_files=["b.py"]),
            "b.py": _entry(),
        }
        distances = compute_hop_distances(entries, "a.py")
        assert distances["b.py"] == 1

    def test_direct_incoming_importer_is_one_hop(self) -> None:
        """The reverse direction — a.py doesn't import anything, but c.py
        imports a.py — must resolve to 1-hop too (BFS traverses both
        edge directions, not just outgoing)."""
        entries = {
            "a.py": _entry(),
            "c.py": _entry(imported_files=["a.py"]),
        }
        distances = compute_hop_distances(entries, "a.py")
        assert distances["c.py"] == 1


class TestTwoHop:
    def test_transitive_import_via_one_intermediate_is_two_hop(self) -> None:
        entries = {
            "a.py": _entry(imported_files=["b.py"]),
            "b.py": _entry(imported_files=["c.py"]),
            "c.py": _entry(),
        }
        distances = compute_hop_distances(entries, "a.py")
        assert distances["b.py"] == 1
        assert distances["c.py"] == 2

    def test_mixed_direction_chain(self) -> None:
        """a.py -> b.py (outgoing), then d.py -> b.py (incoming from b.py's
        perspective) — b.py is 1-hop, d.py is 2-hop via b.py regardless of
        which direction each individual edge runs."""
        entries = {
            "a.py": _entry(imported_files=["b.py"]),
            "b.py": _entry(),
            "d.py": _entry(imported_files=["b.py"]),
        }
        distances = compute_hop_distances(entries, "a.py")
        assert distances["b.py"] == 1
        assert distances["d.py"] == 2


class TestMaxHopsCap:
    def test_default_cap_is_two(self) -> None:
        assert DEFAULT_MAX_HOPS == 2

    def test_nodes_beyond_cap_are_absent_not_included_with_a_large_distance(self) -> None:
        entries = {
            "a.py": _entry(imported_files=["b.py"]),
            "b.py": _entry(imported_files=["c.py"]),
            "c.py": _entry(imported_files=["d.py"]),
            "d.py": _entry(),
        }
        distances = compute_hop_distances(entries, "a.py", max_hops=2)
        assert distances["b.py"] == 1
        assert distances["c.py"] == 2
        assert "d.py" not in distances

    def test_max_hops_zero_returns_only_start(self) -> None:
        entries = {"a.py": _entry(imported_files=["b.py"]), "b.py": _entry()}
        distances = compute_hop_distances(entries, "a.py", max_hops=0)
        assert distances == {"a.py": 0}

    def test_caller_can_widen_the_cap(self) -> None:
        entries = {
            "a.py": _entry(imported_files=["b.py"]),
            "b.py": _entry(imported_files=["c.py"]),
            "c.py": _entry(imported_files=["d.py"]),
            "d.py": _entry(),
        }
        distances = compute_hop_distances(entries, "a.py", max_hops=3)
        assert distances["d.py"] == 3


class TestCyclesAndDiamonds:
    def test_import_cycle_does_not_infinite_loop_and_keeps_shortest_distance(self) -> None:
        entries = {
            "a.py": _entry(imported_files=["b.py"]),
            "b.py": _entry(imported_files=["a.py"]),
        }
        distances = compute_hop_distances(entries, "a.py")
        assert distances == {"a.py": 0, "b.py": 1}

    def test_diamond_shape_keeps_the_shorter_of_two_paths(self) -> None:
        """a.py imports both b.py and c.py directly (1-hop each); both also
        import d.py, which would be 2-hop via either — BFS's visited-once
        guarantee means d.py is recorded at 2, never revisited/overwritten
        with a longer path found later."""
        entries = {
            "a.py": _entry(imported_files=["b.py", "c.py"]),
            "b.py": _entry(imported_files=["d.py"]),
            "c.py": _entry(imported_files=["d.py"]),
            "d.py": _entry(),
        }
        distances = compute_hop_distances(entries, "a.py")
        assert distances["d.py"] == 2


class TestDeterminism:
    def test_repeated_calls_return_identical_results(self) -> None:
        entries = {
            "a.py": _entry(imported_files=["b.py"]),
            "b.py": _entry(imported_files=["c.py"]),
            "c.py": _entry(),
        }
        first = compute_hop_distances(entries, "a.py")
        second = compute_hop_distances(entries, "a.py")
        assert first == second
