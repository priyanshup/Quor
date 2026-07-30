"""Unit tests for `quor/pipeline/repo_profile/query_extract.py` (QB-081)."""

from __future__ import annotations

from quor.pipeline.repo_profile.query_extract import MAX_QUERY_TERMS, extract_query_terms


class TestDeterminism:
    def test_identical_input_yields_identical_output(self) -> None:
        text = "Where is `LoginManager` defined? Check src/auth/login.py and file_intelligence.json."
        first = extract_query_terms(text)
        second = extract_query_terms(text)
        assert first == second

    def test_empty_string_yields_empty_list(self) -> None:
        assert extract_query_terms("") == []

    def test_plain_prose_with_no_identifier_shaped_words_yields_empty_list(self) -> None:
        assert extract_query_terms("Where is the login implemented in this repository?") == []


class TestShapeRules:
    def test_snake_case_word_is_extracted(self) -> None:
        assert extract_query_terms("look at file_intelligence next") == ["file_intelligence"]

    def test_camel_case_word_is_extracted(self) -> None:
        assert extract_query_terms("find loginManager please") == ["loginManager"]

    def test_pascal_case_word_is_extracted(self) -> None:
        assert extract_query_terms("find LoginManager please") == ["LoginManager"]

    def test_filename_looking_word_is_extracted(self) -> None:
        assert extract_query_terms("open search.py now") == ["search.py"]

    def test_directory_like_token_is_extracted(self) -> None:
        assert extract_query_terms("check src/auth please") == ["src/auth"]

    def test_import_looking_dotted_path_is_extracted(self) -> None:
        assert extract_query_terms("see quor.pipeline.repo_profile.search") == [
            "quor.pipeline.repo_profile.search"
        ]

    def test_double_quoted_identifier_is_extracted_verbatim(self) -> None:
        assert extract_query_terms('find "payments" logic') == ["payments"]

    def test_backtick_quoted_identifier_is_extracted_verbatim(self) -> None:
        assert extract_query_terms("find `LoginManager` please") == ["LoginManager"]

    def test_single_quote_is_not_treated_as_a_quote_delimiter(self) -> None:
        # Apostrophes in contractions/possessives have no reliable closing
        # partner, so the single quote is deliberately excluded from the
        # quoted-span rule — "don't" and "user's" must not be misparsed.
        assert extract_query_terms("don't touch the user's session") == []

    def test_trailing_sentence_punctuation_is_stripped_before_qualifying(self) -> None:
        assert extract_query_terms("open login.py.") == ["login.py"]

    def test_leading_relative_path_punctuation_is_stripped(self) -> None:
        assert extract_query_terms("run ./search.py") == ["search.py"]

    def test_underscores_only_token_does_not_qualify(self) -> None:
        assert extract_query_terms("___ find nothing ___") == []


class TestDeduplication:
    def test_duplicate_terms_collapse_to_one(self) -> None:
        assert extract_query_terms("find login_manager then login_manager again") == ["login_manager"]

    def test_duplicate_terms_are_case_insensitive(self) -> None:
        # Both spellings independently qualify (snake_case, regardless of
        # letter case), so this exercises casefold-based dedup rather than
        # relying on the second spelling failing to qualify at all.
        result = extract_query_terms("find login_manager then LOGIN_MANAGER again")
        assert result == ["login_manager"]


class TestOrdering:
    def test_terms_are_returned_in_first_seen_order(self) -> None:
        text = "check token.py then session.py then login.py"
        assert extract_query_terms(text) == ["token.py", "session.py", "login.py"]


class TestLimit:
    def test_default_limit_matches_module_constant(self) -> None:
        text = " ".join(f"mod_{i}.py" for i in range(MAX_QUERY_TERMS + 5))
        result = extract_query_terms(text)
        assert len(result) == MAX_QUERY_TERMS

    def test_custom_limit_is_respected(self) -> None:
        text = "mod_a.py mod_b.py mod_c.py mod_d.py"
        result = extract_query_terms(text, limit=2)
        assert result == ["mod_a.py", "mod_b.py"]
