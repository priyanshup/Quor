"""Unit tests for quor/pipeline/repo_profile/languages.py."""

from __future__ import annotations

from quor.pipeline.repo_profile.languages import compute_language_stats, is_vendor_or_build_path


class TestComputeLanguageStats:
    def test_empty_file_list_returns_empty(self) -> None:
        assert compute_language_stats([]) == []

    def test_no_recognized_extensions_returns_empty(self) -> None:
        assert compute_language_stats(["README.md", "LICENSE", "data.bin"]) == []

    def test_single_language_is_100_percent(self) -> None:
        stats = compute_language_stats(["a.py", "b.py", "c.py"])

        assert len(stats) == 1
        assert stats[0].language == "Python"
        assert stats[0].file_count == 3
        assert stats[0].percentage == 100.0

    def test_percentages_exclude_non_language_files(self) -> None:
        """A config/data file (no recognized language extension) must not
        dilute the percentage of files that do have one."""
        stats = compute_language_stats(["a.py", "b.py", "config.json", "README.md"])

        assert len(stats) == 1
        assert stats[0].percentage == 100.0

    def test_multiple_languages_sorted_by_count_descending(self) -> None:
        files = ["a.py", "b.py", "c.py", "x.go", "y.go", "z.ts"]

        stats = compute_language_stats(files)

        assert [s.language for s in stats] == ["Python", "Go", "TypeScript"]
        assert stats[0].file_count == 3
        assert stats[1].file_count == 2
        assert stats[2].file_count == 1

    def test_ties_broken_alphabetically(self) -> None:
        stats = compute_language_stats(["a.go", "b.rs"])

        assert [s.language for s in stats] == ["Go", "Rust"]

    def test_extension_matching_is_case_insensitive(self) -> None:
        stats = compute_language_stats(["A.PY", "b.Py"])

        assert len(stats) == 1
        assert stats[0].language == "Python"
        assert stats[0].file_count == 2

    def test_jsx_mjs_cjs_all_count_as_javascript(self) -> None:
        stats = compute_language_stats(["a.jsx", "b.mjs", "c.cjs", "d.js"])

        assert len(stats) == 1
        assert stats[0].language == "JavaScript"
        assert stats[0].file_count == 4

    def test_percentage_is_deterministic_and_rounded(self) -> None:
        # 1 of 3 -> 33.3%, not an unbounded float
        stats = compute_language_stats(["a.py", "b.go", "c.go"])
        python_stat = next(s for s in stats if s.language == "Python")

        assert python_stat.percentage == 33.3

    def test_vendor_directory_files_excluded(self) -> None:
        """A committed vendor/ tree must not inflate that language's share
        with dependency code the repo's own author didn't write."""
        stats = compute_language_stats(["app.go", "vendor/github.com/pkg/errors.go"])

        assert len(stats) == 1
        assert stats[0].language == "Go"
        assert stats[0].file_count == 1

    def test_all_vendor_files_returns_empty(self) -> None:
        assert compute_language_stats(["vendor/lib.go", "third_party/dep.py"]) == []


class TestIsVendorOrBuildPath:
    def test_vendor_directory(self) -> None:
        assert is_vendor_or_build_path("vendor/github.com/pkg/errors.go") is True

    def test_third_party_directory(self) -> None:
        assert is_vendor_or_build_path("third_party/lib/util.py") is True

    def test_nested_dist_directory(self) -> None:
        assert is_vendor_or_build_path("packages/ui/dist/bundle.js") is True

    def test_case_insensitive_on_windows(self) -> None:
        assert is_vendor_or_build_path("Vendor/pkg/foo.go") is True

    def test_filename_named_vendor_is_not_a_directory_match(self) -> None:
        """Only directory components count — a file literally named
        `vendor.go` at the repo root is not vendored code."""
        assert is_vendor_or_build_path("vendor.go") is False

    def test_ordinary_source_path(self) -> None:
        assert is_vendor_or_build_path("src/app/service.py") is False
