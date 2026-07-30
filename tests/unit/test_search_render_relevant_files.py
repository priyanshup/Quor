"""Unit tests for `search_render.render_relevant_files_block()` (QB-081)."""

from __future__ import annotations

from quor.pipeline.repo_profile.search_model import SearchMatch
from quor.pipeline.repo_profile.search_render import render_relevant_files_block


def _match(
    path: str,
    evidence: str,
    matched_value: str,
    *,
    language: str = "python",
    kind: str = "source",
    importance: str = "Low",
    imports: int = 0,
    imported_by: int = 0,
    entry_point: bool = False,
) -> SearchMatch:
    return SearchMatch(
        path=path,
        evidence=evidence,  # type: ignore[arg-type]
        matched_value=matched_value,
        language=language,
        kind=kind,  # type: ignore[arg-type]
        importance=importance,  # type: ignore[arg-type]
        imports=imports,
        imported_by=imported_by,
        entry_point=entry_point,
    )


class TestEmptyInput:
    def test_empty_matches_yields_empty_string(self) -> None:
        assert render_relevant_files_block([]) == ""


class TestFormat:
    def test_header_and_path_and_label_appear(self) -> None:
        block = render_relevant_files_block(
            [_match("src/auth/login.py", "exact_symbol", "LoginManager")]
        )
        assert "Relevant repository files" in block
        assert "- src/auth/login.py" in block
        assert "  Exact symbol: LoginManager" in block

    def test_no_scores_or_confidence_shown(self) -> None:
        block = render_relevant_files_block(
            [_match("src/auth/login.py", "exact_symbol", "LoginManager")]
        )
        assert "%" not in block
        assert "score" not in block.lower()
        assert "confidence" not in block.lower()

    def test_every_evidence_tier_has_a_label(self) -> None:
        tiers = [
            ("exact_symbol", "Exact symbol"),
            ("exact_filename", "Exact filename"),
            ("exact_directory", "Exact directory"),
            ("prefix_symbol", "Symbol prefix"),
            ("filename_contains", "Filename contains"),
            ("top_symbol", "Symbol"),
            ("dependency", "Dependency"),
        ]
        for evidence, label in tiers:
            block = render_relevant_files_block([_match("a.py", evidence, "value")])
            assert f"  {label}: value" in block

    def test_ends_with_a_blank_line_separator(self) -> None:
        block = render_relevant_files_block([_match("a.py", "exact_symbol", "A")])
        assert block.endswith("\n\n")

    def test_multiple_matches_each_get_their_own_entry(self) -> None:
        block = render_relevant_files_block(
            [
                _match("src/auth/login.py", "exact_symbol", "LoginManager"),
                _match("src/auth/session.py", "dependency", "src/auth/login.py"),
                _match("src/auth/token.py", "filename_contains", "token"),
            ]
        )
        assert block.index("login.py") < block.index("session.py") < block.index("token.py")


class TestDeterminism:
    def test_repeated_render_is_byte_identical(self) -> None:
        matches = [_match("src/auth/login.py", "exact_symbol", "LoginManager")]
        assert render_relevant_files_block(matches) == render_relevant_files_block(matches)
