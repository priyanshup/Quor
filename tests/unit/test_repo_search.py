"""Unit tests for `quor/pipeline/repo_profile/search.py` (QB-080)."""

from __future__ import annotations

from pathlib import Path

from quor.pipeline.repo_profile import search
from quor.pipeline.repo_profile.explorer_model import CacheUnavailable
from quor.pipeline.repo_profile.intel_model import FileIntelligenceEntry


def _entry(
    *,
    language: str = "python",
    kind: str = "source",
    importance: str = "Low",
    imports: int = 0,
    imported_by: int = 0,
    entry_point: bool = False,
    top_symbols: list[str] | None = None,
    imported_files: list[str] | None = None,
) -> FileIntelligenceEntry:
    return FileIntelligenceEntry(
        language=language,
        kind=kind,  # type: ignore[arg-type]
        importance=importance,  # type: ignore[arg-type]
        imports=imports,
        imported_by=imported_by,
        entry_point=entry_point,
        top_symbols=top_symbols or [],
        imported_files=imported_files or [],
    )


class TestLoadCache:
    def test_missing_when_no_cache_at_all(self, tmp_path: Path) -> None:
        result = search.load_cache(tmp_path)
        assert isinstance(result, CacheUnavailable)
        assert result.status == "missing"

    def test_corrupted_when_file_unreadable(self, tmp_path: Path) -> None:
        from quor.pipeline.repo_profile import intel_store

        intel_store.save_file_intelligence(tmp_path, {})
        path = intel_store.cache_dir(tmp_path) / "file_intelligence.json"
        path.write_bytes(b"{broken")

        result = search.load_cache(tmp_path)
        assert isinstance(result, CacheUnavailable)
        assert result.status == "corrupted"

    def test_returns_entries_dict_on_success(self, tmp_path: Path) -> None:
        from quor.pipeline.repo_profile import intel_store

        entries = {"a.py": _entry()}
        intel_store.save_file_intelligence(tmp_path, entries)

        result = search.load_cache(tmp_path)
        assert result == entries


class TestBuildReverseImportIndex:
    def test_inverts_outgoing_into_incoming(self) -> None:
        entries = {
            "a.py": _entry(imported_files=["b.py", "c.py"]),
            "b.py": _entry(imported_files=["c.py"]),
            "c.py": _entry(),
        }
        reverse = search._build_reverse_import_index(entries)
        assert reverse["c.py"] == ["a.py", "b.py"]
        assert reverse["b.py"] == ["a.py"]
        assert "a.py" not in reverse

    def test_a_file_imported_by_multiple_others(self) -> None:
        entries = {
            "x.py": _entry(imported_files=["shared.py"]),
            "y.py": _entry(imported_files=["shared.py"]),
            "z.py": _entry(imported_files=["shared.py"]),
            "shared.py": _entry(),
        }
        reverse = search._build_reverse_import_index(entries)
        assert reverse["shared.py"] == ["x.py", "y.py", "z.py"]

    def test_duplicate_edges_are_deduplicated(self) -> None:
        # Two entries both claiming to import "dup.py" more than once in
        # aggregate shouldn't be possible from real build data (imported_files
        # is itself already deduplicated per-file), but a single file listing
        # the same target twice must still collapse via the set accumulation.
        entries = {
            "a.py": _entry(imported_files=["dup.py", "dup.py"]),
            "dup.py": _entry(),
        }
        reverse = search._build_reverse_import_index(entries)
        assert reverse["dup.py"] == ["a.py"]


class TestDependencyNeighbors:
    def test_union_of_outgoing_and_incoming_deduplicated_and_sorted(self) -> None:
        entries = {
            "a.py": _entry(imported_files=["c.py", "b.py"]),
            "b.py": _entry(imported_files=["a.py"]),
            "c.py": _entry(),
        }
        reverse = search._build_reverse_import_index(entries)
        neighbors = search._dependency_neighbors("a.py", entries["a.py"], reverse)
        # "b.py" reachable both as an outgoing target and (via reverse) as an
        # incoming source must appear exactly once.
        assert neighbors == ("b.py", "c.py")


class TestPathTokens:
    def test_produces_stem_filename_and_directory_components(self) -> None:
        assert search._path_tokens("src/auth/login.py") == frozenset({"src", "auth", "login", "login.py"})

    def test_root_level_file_has_no_directory_tokens(self) -> None:
        assert search._path_tokens("main.py") == frozenset({"main", "main.py"})


class TestEvidenceTiers:
    def test_exact_symbol(self) -> None:
        entries = {"a.py": _entry(top_symbols=["PaymentService"])}
        result = search.search(entries, "PaymentService")
        assert result.matches[0].evidence == "exact_symbol"
        assert result.matches[0].matched_value == "PaymentService"

    def test_exact_filename_by_full_name(self) -> None:
        entries = {"src/payment.py": _entry()}
        result = search.search(entries, "payment.py")
        assert result.matches[0].evidence == "exact_filename"

    def test_exact_filename_by_stem(self) -> None:
        entries = {"src/payment.py": _entry()}
        result = search.search(entries, "payment")
        assert result.matches[0].evidence == "exact_filename"

    def test_exact_directory(self) -> None:
        entries = {"src/auth/service.py": _entry()}
        result = search.search(entries, "auth")
        assert result.matches[0].evidence == "exact_directory"
        assert result.matches[0].matched_value == "auth"

    def test_prefix_symbol(self) -> None:
        entries = {"a.py": _entry(top_symbols=["PaymentService"])}
        result = search.search(entries, "Pay")
        assert result.matches[0].evidence == "prefix_symbol"

    def test_filename_contains(self) -> None:
        entries = {"src/payment_utils.py": _entry()}
        result = search.search(entries, "utils")
        assert result.matches[0].evidence == "filename_contains"

    def test_top_symbol_substring(self) -> None:
        entries = {"a.py": _entry(top_symbols=["ProcessPaymentHandler"])}
        result = search.search(entries, "Payment")
        assert result.matches[0].evidence == "top_symbol"

    def test_dependency_exact_token(self) -> None:
        entries = {
            "a.py": _entry(imported_files=["src/payment/models.py"]),
            "src/payment/models.py": _entry(),
        }
        result = search.search(entries, "payment")
        matched = {m.path: m for m in result.matches}
        assert matched["a.py"].evidence == "dependency"
        assert matched["a.py"].matched_value == "src/payment/models.py"

    def test_dependency_tier_uses_exact_token_not_substring(self) -> None:
        """QB-080 second review round: `"auth"` must not spuriously match
        `authentication.py`/`oauth.py` via a dependency edge — only an
        exact path-component/stem token match counts."""
        entries = {
            "a.py": _entry(imported_files=["authentication.py", "oauth.py"]),
            "authentication.py": _entry(),
            "oauth.py": _entry(),
        }
        result = search.search(entries, "auth")
        # No tier matches "a.py" at all: no symbol, no filename/directory
        # match, and the dependency tier's exact-token rule rejects both
        # "authentication.py" and "oauth.py" as neighbors for query "auth".
        assert "a.py" not in {m.path for m in result.matches}

    def test_no_match_returns_empty(self) -> None:
        entries = {"a.py": _entry()}
        result = search.search(entries, "nonexistent_query_xyz")
        assert result.matches == []
        assert result.total_candidates == 0


class TestPriorityOrdering:
    def test_strongest_tier_wins_when_multiple_qualify(self) -> None:
        # "payment" is both an exact symbol name and would also match the
        # filename substring tier — exact_symbol (tier 0) must win.
        entries = {"src/payment_stuff.py": _entry(top_symbols=["payment"])}
        result = search.search(entries, "payment")
        assert result.matches[0].evidence == "exact_symbol"


class TestFilters:
    def test_kind_filter(self) -> None:
        entries = {
            "src/payment.py": _entry(kind="source"),
            "tests/test_payment.py": _entry(kind="test"),
        }
        result = search.search(entries, "payment", kind="test")
        assert {m.path for m in result.matches} == {"tests/test_payment.py"}

    def test_language_filter_case_insensitive(self) -> None:
        entries = {
            "a.py": _entry(language="python"),
            "b.md": _entry(language="Markdown"),
        }
        result_py = search.search(entries, "a", language="PYTHON")
        assert {m.path for m in result_py.matches} == {"a.py"}

    def test_importance_filter(self) -> None:
        entries = {
            "a.py": _entry(importance="High"),
            "a_other.py": _entry(importance="Low"),
        }
        result = search.search(entries, "a", importance="High")
        assert {m.path for m in result.matches} == {"a.py"}

    def test_entry_points_only_filter(self) -> None:
        entries = {
            "main.py": _entry(entry_point=True),
            "main_helper.py": _entry(entry_point=False),
        }
        result = search.search(entries, "main", entry_points_only=True)
        assert {m.path for m in result.matches} == {"main.py"}


class TestTieBreaks:
    def test_entry_point_ranks_before_plain_importance(self) -> None:
        entries = {
            "payment_a.py": _entry(entry_point=True, importance="Low"),
            "payment_b.py": _entry(entry_point=False, importance="High"),
        }
        result = search.search(entries, "payment")
        assert [m.path for m in result.matches] == ["payment_a.py", "payment_b.py"]

    def test_filename_length_closeness_breaks_ties_for_filename_tiers(self) -> None:
        # "payment.py" (10 chars) is an exact_filename match (its own,
        # strictly higher-priority tier) — always first. The other two are
        # both filename_contains matches: "payment12.py" (12 chars) is
        # closer to the 7-char query than "payment123456.py" (16 chars),
        # |12-7|=5 vs |16-7|=9, so it must rank ahead.
        entries = {
            "payment123456.py": _entry(),
            "payment.py": _entry(),
            "payment12.py": _entry(),
        }
        result = search.search(entries, "payment")
        assert [m.path for m in result.matches] == ["payment.py", "payment12.py", "payment123456.py"]

    def test_filename_length_signal_does_not_affect_non_filename_tiers(self) -> None:
        """QB-080 fourth review round: the filename-length tiebreak must be
        `0` outside `exact_filename`/`filename_contains` — two files tied on
        the `exact_directory` tier must not be reordered by unrelated
        filename lengths. Deliberately set up so the (buggy) filename-length
        order and the (correct) path order disagree: if the exclusion were
        ever dropped, "auth/z_x.py" (filename length far closer to the
        4-char query "auth") would incorrectly jump ahead of
        "auth/a_very_long_filename.py"."""
        entries = {
            "auth/a_very_long_filename.py": _entry(),
            "auth/z_x.py": _entry(),
        }
        result = search.search(entries, "auth")
        assert [m.path for m in result.matches] == ["auth/a_very_long_filename.py", "auth/z_x.py"]

    def test_path_beats_connectivity_within_same_tier(self) -> None:
        # Both are filename_contains matches with identical filename-length
        # distance (same filename length). "payment_a.py" has *lower*
        # connectivity than "payment_b.py" — if connectivity were checked
        # before path, "payment_b.py" would win. Path decides first instead,
        # so the alphabetically earlier "payment_a.py" wins despite its
        # lower connectivity.
        entries = {
            "payment_b.py": _entry(imports=0, imported_by=100),
            "payment_a.py": _entry(imports=0, imported_by=0),
        }
        result = search.search(entries, "payment")
        assert [m.path for m in result.matches] == ["payment_a.py", "payment_b.py"]


class TestLimitAndTruncation:
    def test_limit_truncates_and_reports_total_candidates(self) -> None:
        entries = {f"payment_{i}.py": _entry() for i in range(5)}
        result = search.search(entries, "payment", limit=2)
        assert len(result.matches) == 2
        assert result.total_candidates == 5
        assert result.truncated is True

    def test_no_truncation_when_under_limit(self) -> None:
        entries = {"payment.py": _entry()}
        result = search.search(entries, "payment", limit=20)
        assert result.truncated is False
        assert result.total_candidates == 1


class TestCaseInsensitivity:
    def test_query_case_does_not_matter(self) -> None:
        entries = {"a.py": _entry(top_symbols=["PaymentService"])}
        lower = search.search(entries, "paymentservice")
        upper = search.search(entries, "PAYMENTSERVICE")
        assert lower.matches[0].evidence == "exact_symbol"
        assert upper.matches[0].evidence == "exact_symbol"


class TestDeterminism:
    def test_repeated_search_is_byte_identical(self) -> None:
        entries = {
            "src/payment/service.py": _entry(top_symbols=["PaymentService"], imported_files=["src/payment/models.py"]),
            "src/payment/models.py": _entry(),
            "tests/test_payment.py": _entry(kind="test"),
        }
        first = search.search(entries, "payment")
        second = search.search(entries, "payment")
        assert first == second


class TestMergeSearch:
    """`search.merge_search()` (QB-081) — combines several independent
    `search()` calls into one deduplicated, deterministically ordered list.
    """

    def test_single_query_matches_plain_search(self) -> None:
        entries = {"src/auth/login.py": _entry(top_symbols=["LoginManager"])}
        merged = search.merge_search(entries, ["LoginManager"], limit=5)
        plain = search.search(entries, "LoginManager").matches
        assert [m.path for m in merged] == [m.path for m in plain]

    def test_results_from_different_queries_are_merged(self) -> None:
        entries = {
            "src/auth/login.py": _entry(top_symbols=["LoginManager"]),
            "src/auth/session.py": _entry(imported_files=["src/auth/login.py"]),
            "src/auth/token.py": _entry(),
        }
        merged = search.merge_search(entries, ["LoginManager", "token.py"], limit=5)
        assert {m.path for m in merged} == {"src/auth/login.py", "src/auth/token.py"}

    def test_duplicate_query_terms_do_not_duplicate_a_file(self) -> None:
        entries = {"src/auth/login.py": _entry(top_symbols=["LoginManager"])}
        merged = search.merge_search(entries, ["LoginManager", "LoginManager"], limit=5)
        assert [m.path for m in merged] == ["src/auth/login.py"]

    def test_same_file_matched_by_multiple_queries_keeps_strongest_tier(self) -> None:
        # "login" only reaches login.py via a weaker tier (a symbol-prefix
        # match); "LoginManager" reaches it via the exact_symbol tier. The
        # merged result must keep the stronger one.
        entries = {"src/auth/login.py": _entry(top_symbols=["LoginManager"])}
        merged = search.merge_search(entries, ["login", "LoginManager"], limit=5)
        assert len(merged) == 1
        assert merged[0].evidence == "exact_symbol"

    def test_exclude_removes_a_matching_path(self) -> None:
        entries = {
            "src/auth/login.py": _entry(top_symbols=["LoginManager"]),
            "src/auth/session.py": _entry(top_symbols=["LoginManager"]),
        }
        merged = search.merge_search(
            entries, ["LoginManager"], limit=5, exclude=frozenset({"src/auth/login.py"})
        )
        assert [m.path for m in merged] == ["src/auth/session.py"]

    def test_limit_caps_the_merged_result(self) -> None:
        entries = {f"mod_{i}.py": _entry(top_symbols=[f"Mod{i}"]) for i in range(10)}
        queries = [f"Mod{i}" for i in range(10)]
        merged = search.merge_search(entries, queries, limit=3)
        assert len(merged) == 3

    def test_empty_queries_yields_empty_list(self) -> None:
        entries = {"a.py": _entry()}
        assert search.merge_search(entries, [], limit=5) == []

    def test_result_order_is_tier_then_path(self) -> None:
        entries = {
            "z_exact.py": _entry(top_symbols=["Target"]),
            "a_exact.py": _entry(top_symbols=["Target"]),
            "b_contains.py": _entry(),
        }
        merged = search.merge_search(entries, ["Target", "b_contains"], limit=5)
        assert [m.path for m in merged] == ["a_exact.py", "z_exact.py", "b_contains.py"]

    def test_repeated_call_is_deterministic(self) -> None:
        entries = {
            "src/auth/login.py": _entry(top_symbols=["LoginManager"]),
            "src/auth/session.py": _entry(imported_files=["src/auth/login.py"]),
        }
        first = search.merge_search(entries, ["LoginManager", "session.py"], limit=5)
        second = search.merge_search(entries, ["LoginManager", "session.py"], limit=5)
        assert first == second
