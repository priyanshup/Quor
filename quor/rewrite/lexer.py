"""Quote-aware shell command lexer for Quor's rewrite layer.

Handles the structural analysis needed for command rewriting:
- Heredoc detection (excludes from rewriting)
- Compound operator splitting (&&, ||, ;) respecting quotes
- Env-var assignment prefix extraction
- Argument tokenization respecting single/double quotes
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class TokenKind(enum.Enum):
    WORD = "word"
    SINGLE_QUOTED = "single_quoted"
    DOUBLE_QUOTED = "double_quoted"
    ENV_ASSIGN = "env_assign"
    COMPOUND_AND = "&&"
    COMPOUND_OR = "||"
    COMPOUND_SEMI = ";"
    COMPOUND_BG = "&"
    PIPE = "|"
    REDIRECT_HEREDOC = "<<"
    REDIRECT_OTHER = "redirect"


@dataclass(frozen=True)
class Token:
    value: str
    kind: TokenKind

    def is_compound(self) -> bool:
        return self.kind in (
            TokenKind.COMPOUND_AND,
            TokenKind.COMPOUND_OR,
            TokenKind.COMPOUND_SEMI,
        )


@dataclass(frozen=True)
class CommandHead:
    """Parsed structure of the start of a simple command."""

    env_prefix: str          # "FORCE_COLOR=1 " (with trailing space) or ""
    transparent_prefix: str  # "docker exec container " or ""
    base_command: str        # "git", "pytest", "python", …
    args: list[str]          # remaining args after base_command
    raw_rest: str            # original text after env + transparent prefix


_WORD_STOP_CHARS = (" ", "\t", "&", "|", ";", "<", ">")
_PLAIN_STOP_CHARS = (" ", "\t", "'", '"', "&", "|", ";", "<", ">")


def tokenize(cmd: str) -> list[Token]:
    """Tokenize a shell command string into Tokens.

    Handles single/double quoting and compound operators.
    Does NOT expand variables or perform any substitution.
    """
    tokens: list[Token] = []
    i = 0
    n = len(cmd)

    while i < n:
        # Skip whitespace
        if cmd[i] == " " or cmd[i] == "\t":
            i += 1
            continue

        # <<  heredoc redirect
        if cmd[i : i + 2] == "<<":
            tokens.append(Token("<<", TokenKind.REDIRECT_HEREDOC))
            i += 2
            continue

        # && compound operator
        if cmd[i : i + 2] == "&&":
            tokens.append(Token("&&", TokenKind.COMPOUND_AND))
            i += 2
            continue

        # || compound operator
        if cmd[i : i + 2] == "||":
            tokens.append(Token("||", TokenKind.COMPOUND_OR))
            i += 2
            continue

        # & background operator (must check after &&)
        if cmd[i] == "&":
            tokens.append(Token("&", TokenKind.COMPOUND_BG))
            i += 1
            continue

        # ; semicolon
        if cmd[i] == ";":
            tokens.append(Token(";", TokenKind.COMPOUND_SEMI))
            i += 1
            continue

        # | pipe
        if cmd[i] == "|":
            tokens.append(Token("|", TokenKind.PIPE))
            i += 1
            continue

        # Redirect (>, <, >>), optionally preceded by a file-descriptor
        # number with no intervening space (e.g. "2>&1", "1>>log", "0<file").
        # The descriptor must be consumed as part of the same token: a real
        # shell requires "2" and ">&" to be adjacent for fd-duplication
        # syntax (2>&1) — inserting a space between them (2 >&1) makes "2" a
        # literal argument instead, changing what the command does. Space
        # *after* the operator (2>& 1, 2> file) is harmless and unaffected.
        if cmd[i].isdigit():
            k = i
            while k < n and cmd[k].isdigit():
                k += 1
            if k < n and cmd[k] in (">", "<"):
                j = k + 1
                if j < n and cmd[j] in (">", "&"):
                    j += 1
                tokens.append(Token(cmd[i:j], TokenKind.REDIRECT_OTHER))
                i = j
                continue
        if cmd[i] in (">", "<"):
            j = i + 1
            if j < n and cmd[j] in (">", "&"):
                j += 1
            tokens.append(Token(cmd[i:j], TokenKind.REDIRECT_OTHER))
            i = j
            continue

        # Shell word: one argument can be built from several adjacent
        # fragments — quoted and unquoted — with no whitespace between them
        # (e.g. --format="%h %ad", foo"bar"baz, KEY="a b"). Real shell
        # grammar treats these as a SINGLE word; whitespace is the only
        # argument separator. Scan every contiguous fragment here so a
        # reconstruction step downstream (`" ".join(...)`) can never insert
        # a space a real shell would never have parsed — that space is what
        # previously turned one argument into two (e.g. `--format=` and a
        # separate `"%h %ad"`, changing what the command meant to the real
        # shell that finally executes it).
        start = i
        fragment_count = 0
        solo_kind = TokenKind.WORD
        while i < n and cmd[i] not in _WORD_STOP_CHARS:
            if cmd[i] == "'":
                # Single-quoted fragment: no escapes inside.
                j = i + 1
                while j < n and cmd[j] != "'":
                    j += 1
                i = j + 1
                solo_kind = TokenKind.SINGLE_QUOTED
            elif cmd[i] == '"':
                # Double-quoted fragment: backslash escapes apply.
                j = i + 1
                while j < n and cmd[j] != '"':
                    if cmd[j] == "\\" and j + 1 < n:
                        j += 1
                    j += 1
                i = j + 1
                solo_kind = TokenKind.DOUBLE_QUOTED
            else:
                # Unquoted fragment (including backslash-escaped chars).
                j = i
                while j < n and cmd[j] not in _PLAIN_STOP_CHARS:
                    if cmd[j] == "\\" and j + 1 < n:
                        j += 1
                    j += 1
                i = j
                solo_kind = TokenKind.WORD
            fragment_count += 1

        word = cmd[start:i]
        if word:
            # A lone quoted fragment keeps its specific kind (existing
            # SINGLE_QUOTED/DOUBLE_QUOTED granularity); anything merged from
            # more than one fragment — or a bare unquoted run — is WORD or
            # ENV_ASSIGN, decided the same way regardless of what's inside
            # (quotes don't stop a VAR=value prefix from being recognized).
            if fragment_count == 1 and solo_kind in (TokenKind.SINGLE_QUOTED, TokenKind.DOUBLE_QUOTED):
                tokens.append(Token(word, solo_kind))
            else:
                eq = word.find("=")
                if eq > 0 and word[:eq].replace("_", "").isalnum() and word[0].isalpha():
                    tokens.append(Token(word, TokenKind.ENV_ASSIGN))
                else:
                    tokens.append(Token(word, TokenKind.WORD))

    return tokens


def has_heredoc(cmd: str) -> bool:
    """Return True if cmd contains a heredoc operator outside of quotes."""
    return any(token.kind == TokenKind.REDIRECT_HEREDOC for token in tokenize(cmd))


def split_compound(cmd: str) -> list[tuple[str, str | None]]:
    """Split on top-level &&, ||, ; operators (respecting quotes).

    Returns list of (segment, operator_after) pairs.
    The last pair always has operator=None.

    Example:
        "git status && git diff" → [("git status", "&&"), ("git diff", None)]
    """
    tokens = tokenize(cmd)
    if not any(t.is_compound() for t in tokens):
        return [(cmd.strip(), None)]

    # Rebuild segments from tokens
    result: list[tuple[str, str | None]] = []
    current_parts: list[str] = []

    for token in tokens:
        if token.is_compound():
            result.append((" ".join(current_parts), token.value))
            current_parts = []
        else:
            current_parts.append(token.value)

    result.append((" ".join(current_parts), None))
    return result


def parse_args(cmd: str) -> list[str]:
    """Return the list of word-tokens from a simple (non-compound) command.

    Includes REDIRECT_OTHER (e.g. "2>&", ">>") so a known command's redirect
    survives rewriting instead of being silently dropped from the arg list.
    """
    words: list[str] = []
    for token in tokenize(cmd):
        if token.kind in (
            TokenKind.WORD,
            TokenKind.SINGLE_QUOTED,
            TokenKind.DOUBLE_QUOTED,
            TokenKind.ENV_ASSIGN,
            TokenKind.REDIRECT_OTHER,
        ):
            words.append(token.value)
    return words
