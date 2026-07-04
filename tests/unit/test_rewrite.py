"""Unit tests for quor/rewrite/: lexer, rules, and classifier."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from quor.rewrite.classifier import classify_command, rewrite_command
from quor.rewrite.invocation import get_quor_invocation
from quor.rewrite.lexer import (
    TokenKind,
    has_heredoc,
    parse_args,
    split_compound,
    tokenize,
)
from quor.rewrite.rules import (
    cat_is_safe_to_rewrite,
    has_structured_output_flag,
    is_known_command,
    is_transparent_prefix,
    pipe_segment_is_safe,
)

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "commands"

# The command classifier's rewritten output is prefixed with the shell-safe
# Quor invocation (`sys.executable -m quor`, not the bare `quor` launcher —
# see quor/rewrite/invocation.py). Tests compare against this same helper
# rather than hardcoding a literal so they remain valid on every machine.
Q = get_quor_invocation()


# ---------------------------------------------------------------------------
# Lexer tests
# ---------------------------------------------------------------------------


class TestTokenize:
    def test_simple_words(self) -> None:
        tokens = tokenize("git status")
        assert [t.value for t in tokens] == ["git", "status"]
        assert all(t.kind == TokenKind.WORD for t in tokens)

    def test_single_quoted(self) -> None:
        tokens = tokenize("git commit -m 'my message'")
        assert tokens[-1].kind == TokenKind.SINGLE_QUOTED
        assert tokens[-1].value == "'my message'"

    def test_double_quoted(self) -> None:
        tokens = tokenize('git commit -m "my message"')
        assert tokens[-1].kind == TokenKind.DOUBLE_QUOTED
        assert tokens[-1].value == '"my message"'

    def test_double_quoted_escape(self) -> None:
        tokens = tokenize(r'echo "say \"hi\""')
        quoted = [t for t in tokens if t.kind == TokenKind.DOUBLE_QUOTED]
        assert len(quoted) == 1

    def test_env_assign(self) -> None:
        tokens = tokenize("FORCE_COLOR=1 git status")
        assert tokens[0].kind == TokenKind.ENV_ASSIGN
        assert tokens[0].value == "FORCE_COLOR=1"
        assert tokens[1].value == "git"

    def test_compound_and(self) -> None:
        tokens = tokenize("git status && git diff")
        ops = [t for t in tokens if t.kind == TokenKind.COMPOUND_AND]
        assert len(ops) == 1
        assert ops[0].value == "&&"

    def test_compound_or(self) -> None:
        tokens = tokenize("pytest || echo fail")
        ops = [t for t in tokens if t.kind == TokenKind.COMPOUND_OR]
        assert len(ops) == 1

    def test_compound_semicolon(self) -> None:
        tokens = tokenize("git status; git diff")
        ops = [t for t in tokens if t.kind == TokenKind.COMPOUND_SEMI]
        assert len(ops) == 1

    def test_pipe(self) -> None:
        tokens = tokenize("git log | grep feat")
        pipes = [t for t in tokens if t.kind == TokenKind.PIPE]
        assert len(pipes) == 1

    def test_heredoc_detection(self) -> None:
        tokens = tokenize("cat << EOF")
        assert any(t.kind == TokenKind.REDIRECT_HEREDOC for t in tokens)

    def test_amp_background(self) -> None:
        tokens = tokenize("git fetch &")
        assert tokens[-1].kind == TokenKind.COMPOUND_BG

    def test_empty_string(self) -> None:
        assert tokenize("") == []

    def test_whitespace_only(self) -> None:
        assert tokenize("   ") == []


class TestHasHeredoc:
    def test_detects_heredoc(self) -> None:
        assert has_heredoc("git commit -m << EOF") is True

    def test_no_heredoc(self) -> None:
        assert has_heredoc("git status") is False

    def test_heredoc_in_single_quotes(self) -> None:
        # Inside single quotes, << is not a real heredoc
        assert has_heredoc("echo '<<'") is False

    def test_heredoc_in_double_quotes(self) -> None:
        assert has_heredoc('echo "<<"') is False


class TestSplitCompound:
    def test_no_operator(self) -> None:
        result = split_compound("git status")
        assert result == [("git status", None)]

    def test_and_operator(self) -> None:
        result = split_compound("git status && git diff")
        assert result == [("git status", "&&"), ("git diff", None)]

    def test_or_operator(self) -> None:
        result = split_compound("pytest || echo fail")
        assert result == [("pytest", "||"), ("echo fail", None)]

    def test_semicolon(self) -> None:
        result = split_compound("git status ; git diff")
        assert result == [("git status", ";"), ("git diff", None)]

    def test_three_segments(self) -> None:
        result = split_compound("git status && git diff && pytest")
        assert len(result) == 3
        assert result[0][1] == "&&"
        assert result[1][1] == "&&"
        assert result[2][1] is None


class TestParseArgs:
    def test_simple(self) -> None:
        assert parse_args("git log --oneline") == ["git", "log", "--oneline"]

    def test_quoted_arg(self) -> None:
        args = parse_args("git commit -m 'fix: patch'")
        assert args[-1] == "'fix: patch'"

    def test_env_included(self) -> None:
        args = parse_args("FORCE_COLOR=1 git status")
        assert args[0] == "FORCE_COLOR=1"


# ---------------------------------------------------------------------------
# Rules tests
# ---------------------------------------------------------------------------


class TestRules:
    def test_known_command_git(self) -> None:
        assert is_known_command("git", ["status"]) is True

    def test_known_command_pytest(self) -> None:
        assert is_known_command("pytest", ["-x"]) is True

    def test_known_command_python_m_pytest(self) -> None:
        assert is_known_command("python", ["-m", "pytest"]) is True

    def test_known_command_python_m_mypy(self) -> None:
        assert is_known_command("python", ["-m", "mypy"]) is True

    def test_known_command_python_script_false(self) -> None:
        assert is_known_command("python", ["script.py"]) is False

    def test_known_command_python_bare_false(self) -> None:
        assert is_known_command("python", []) is False

    def test_known_command_unknown(self) -> None:
        assert is_known_command("npm", ["install"]) is False

    def test_structured_output_json_flag(self) -> None:
        assert has_structured_output_flag(["--json"]) is True

    def test_structured_output_porcelain(self) -> None:
        assert has_structured_output_flag(["--porcelain"]) is True

    def test_structured_output_none(self) -> None:
        assert has_structured_output_flag(["--oneline", "-20"]) is False

    def test_cat_safe_no_flags(self) -> None:
        assert cat_is_safe_to_rewrite([]) is True

    def test_cat_safe_n_flag(self) -> None:
        assert cat_is_safe_to_rewrite(["-n", "file.txt"]) is True

    def test_cat_unsafe_e_flag(self) -> None:
        assert cat_is_safe_to_rewrite(["-e", "file.txt"]) is False

    def test_cat_unsafe_v_flag(self) -> None:
        assert cat_is_safe_to_rewrite(["-v"]) is False

    def test_pipe_safe_grep(self) -> None:
        assert pipe_segment_is_safe("grep feat") is True

    def test_pipe_safe_head(self) -> None:
        assert pipe_segment_is_safe("head -20") is True

    def test_pipe_unsafe_xargs(self) -> None:
        assert pipe_segment_is_safe("xargs echo") is False

    def test_pipe_unsafe_awk(self) -> None:
        assert pipe_segment_is_safe("awk '{print $1}'") is False

    def test_pipe_unsafe_sed(self) -> None:
        assert pipe_segment_is_safe("sed 's/a/b/'") is False

    def test_transparent_prefix_sudo(self) -> None:
        result = is_transparent_prefix(["sudo", "git", "status"])
        assert result is not None
        prefix, remaining = result
        assert prefix == "sudo"
        assert remaining == ["git", "status"]

    def test_transparent_prefix_docker_exec(self) -> None:
        result = is_transparent_prefix(["docker", "exec", "mycontainer", "git", "status"])
        assert result is not None
        prefix, remaining = result
        assert prefix == "docker exec mycontainer"
        assert remaining == ["git", "status"]

    def test_transparent_prefix_none(self) -> None:
        result = is_transparent_prefix(["git", "status"])
        assert result is None

    def test_transparent_prefix_empty(self) -> None:
        result = is_transparent_prefix([])
        assert result is None


# ---------------------------------------------------------------------------
# Classifier unit tests
# ---------------------------------------------------------------------------


class TestClassifySimple:
    def test_known_command_rewritten(self) -> None:
        r = classify_command("git status")
        assert r.should_rewrite is True
        assert r.rewritten == f"{Q} git status"

    def test_unknown_command_passthrough(self) -> None:
        r = classify_command("npm install")
        assert r.should_rewrite is False
        assert r.rewritten is None

    def test_empty_command(self) -> None:
        r = classify_command("")
        assert r.should_rewrite is False

    def test_whitespace_only(self) -> None:
        r = classify_command("   ")
        assert r.should_rewrite is False

    def test_python_m_pytest_rewritten(self) -> None:
        r = classify_command("python -m pytest tests/")
        assert r.should_rewrite is True
        assert r.rewritten == f"{Q} python -m pytest tests/"

    def test_python_script_passthrough(self) -> None:
        r = classify_command("python script.py")
        assert r.should_rewrite is False

    def test_cat_safe_rewritten(self) -> None:
        r = classify_command("cat pyproject.toml")
        assert r.should_rewrite is True
        assert r.rewritten == f"{Q} cat pyproject.toml"

    def test_cat_unsafe_passthrough(self) -> None:
        r = classify_command("cat -e file.txt")
        assert r.should_rewrite is False

    def test_structured_output_excluded(self) -> None:
        r = classify_command("git status --porcelain")
        assert r.should_rewrite is False
        assert "structured output" in r.reason.lower() or "porcelain" in r.reason.lower()


class TestClassifyHeredoc:
    def test_heredoc_excluded(self) -> None:
        r = classify_command("git commit -m << EOF")
        assert r.should_rewrite is False
        assert "heredoc" in r.reason.lower()

    def test_cat_heredoc_excluded(self) -> None:
        r = classify_command("cat << EOF")
        assert r.should_rewrite is False


class TestClassifyCompound:
    def test_and_both_known(self) -> None:
        r = classify_command("git status && git diff")
        assert r.should_rewrite is True
        assert r.rewritten == f"{Q} git status && {Q} git diff"

    def test_and_one_unknown(self) -> None:
        r = classify_command("npm install && git status")
        assert r.should_rewrite is True
        assert "npm install" in (r.rewritten or "")
        assert f"{Q} git status" in (r.rewritten or "")

    def test_or_both_known(self) -> None:
        r = classify_command("pytest tests/ || echo fail")
        assert r.should_rewrite is True
        assert r.rewritten == f"{Q} pytest tests/ || echo fail"

    def test_semicolon(self) -> None:
        r = classify_command("git status ; git log --oneline")
        assert r.should_rewrite is True
        assert f"{Q} git status" in (r.rewritten or "")
        assert f"{Q} git log" in (r.rewritten or "")

    def test_all_unknown_compound(self) -> None:
        r = classify_command("npm install && make build")
        assert r.should_rewrite is False


class TestClassifyPipe:
    def test_pipe_to_grep_allowed(self) -> None:
        r = classify_command("git log --oneline | grep feat")
        assert r.should_rewrite is True
        assert f"{Q} git log" in (r.rewritten or "")

    def test_pipe_to_xargs_excluded(self) -> None:
        r = classify_command("git log --oneline | xargs echo")
        assert r.should_rewrite is False
        assert "xargs" in r.reason.lower()

    def test_pipe_to_awk_excluded(self) -> None:
        r = classify_command("git status | awk '{print $2}'")
        assert r.should_rewrite is False

    def test_pipe_to_sed_excluded(self) -> None:
        r = classify_command("git diff | sed 's/a/b/'")
        assert r.should_rewrite is False

    def test_first_segment_unknown_pipe(self) -> None:
        r = classify_command("npm list | grep express")
        assert r.should_rewrite is False


class TestClassifyEnvPrefix:
    def test_env_prefix_rewritten(self) -> None:
        r = classify_command("FORCE_COLOR=1 git status")
        assert r.should_rewrite is True
        assert r.rewritten == f"FORCE_COLOR=1 {Q} git status"

    def test_multiple_env_vars(self) -> None:
        r = classify_command("CI=true PYTHONPATH=src python -m pytest")
        assert r.should_rewrite is True
        assert "CI=true" in (r.rewritten or "")
        assert f"{Q} python" in (r.rewritten or "")

    def test_env_prefix_unknown_command(self) -> None:
        r = classify_command("DEBUG=1 npm install")
        assert r.should_rewrite is False


class TestClassifyTransparentPrefix:
    def test_sudo_git(self) -> None:
        r = classify_command("sudo git status")
        assert r.should_rewrite is True
        assert r.rewritten == f"sudo {Q} git status"

    def test_docker_exec_git(self) -> None:
        r = classify_command("docker exec mycontainer git status")
        assert r.should_rewrite is True
        assert r.rewritten == f"docker exec mycontainer {Q} git status"

    def test_sudo_unknown_passthrough(self) -> None:
        r = classify_command("sudo npm install")
        assert r.should_rewrite is False


class TestRewriteCommand:
    def test_known_returns_string(self) -> None:
        assert rewrite_command("git status") == f"{Q} git status"

    def test_unknown_returns_none(self) -> None:
        assert rewrite_command("npm install") is None

    def test_excluded_returns_none(self) -> None:
        assert rewrite_command("git status --porcelain") is None


# ---------------------------------------------------------------------------
# Fixture-based tests (100+ cases)
# ---------------------------------------------------------------------------


def _load_fixture_cases(fixture_file: str) -> list[dict]:
    path = _FIXTURES_DIR / fixture_file
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    return data.get("case", [])


def _fixture_id(case: dict) -> str:
    return case.get("input", "")[:60].replace("\n", "↵")


@pytest.mark.parametrize("case", _load_fixture_cases("simple.toml"), ids=_fixture_id)
def test_fixture_simple(case: dict) -> None:
    _run_fixture_case(case)


@pytest.mark.parametrize("case", _load_fixture_cases("compound.toml"), ids=_fixture_id)
def test_fixture_compound(case: dict) -> None:
    _run_fixture_case(case)


@pytest.mark.parametrize("case", _load_fixture_cases("env_prefix.toml"), ids=_fixture_id)
def test_fixture_env_prefix(case: dict) -> None:
    _run_fixture_case(case)


@pytest.mark.parametrize("case", _load_fixture_cases("transparent_prefix.toml"), ids=_fixture_id)
def test_fixture_transparent_prefix(case: dict) -> None:
    _run_fixture_case(case)


@pytest.mark.parametrize("case", _load_fixture_cases("exclusions.toml"), ids=_fixture_id)
def test_fixture_exclusions(case: dict) -> None:
    _run_fixture_case(case)


# Fixtures encode the rewrite prefix as the literal word "quor" (they predate
# the sys.executable-based invocation). Substitute only the inserted prefix
# token — not incidental occurrences like the "quor/" source directory in
# `mypy quor/` — by requiring the match be followed by whitespace, which the
# prefix always is and a bare directory-name argument never is.
_QUOR_PREFIX_RE = re.compile(r"\bquor(?=\s)")


def _expected_rewrite(raw: str) -> str:
    # Replacement is a callable, not a string: Q may contain backslashes
    # (Windows interpreter paths), which re.sub would otherwise try to
    # interpret as backreferences/escapes.
    return _QUOR_PREFIX_RE.sub(lambda _match: Q, raw)


def _run_fixture_case(case: dict) -> None:
    cmd = case["input"]
    should_rewrite: bool = case["should_rewrite"]
    result = classify_command(cmd)

    assert result.should_rewrite == should_rewrite, (
        f"should_rewrite mismatch for {cmd!r}: "
        f"expected={should_rewrite}, got={result.should_rewrite}, reason={result.reason!r}"
    )

    if should_rewrite and "expected_rewrite" in case:
        expected = _expected_rewrite(case["expected_rewrite"])
        assert result.rewritten == expected, (
            f"rewrite mismatch for {cmd!r}: expected={expected!r}, got={result.rewritten!r}"
        )

    if not should_rewrite:
        assert result.rewritten is None
