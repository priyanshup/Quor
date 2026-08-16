"""Deterministic search-term extraction from free text (QB-081).

Pure text processing only — no repository access, no cache reads. This
module knows nothing about `file_intelligence.json` or `search()`; it
exists so `quor/mcp/server.py`'s `get_repo_context` tool has a query list
to hand to `search.merge_search()`.

Every extracted term comes from one of two deterministic, shape-based
rules — never a stopword list, a word-frequency count, or any other
"this token looks important" guess. That mirrors this codebase's existing
rejection of unevidenced classification fields (`intel_model.FileKind`'s
own docstring: no library/application split "as there is no existing
deterministic signal for that distinction"): a plain English word like
"find" or "implementation" has no reliable shape signal separating it from
an identifier, so rather than guess, it is simply never extracted.

Same input string always yields the same output list, in the same order —
required by QB-081's own "identical prompt -> identical injected files"
test, and easy to keep true because there is nothing non-deterministic
(no set iteration order, no randomness, no wall-clock/locale dependence)
anywhere in this module.
"""

from __future__ import annotations

import re

MAX_QUERY_TERMS = 4
"""Hard cap on how many distinct terms one `extract_query_terms()` call
returns. This is the number that actually bounds QB-081's worst-case added
latency: each returned term drives one full `search.search()` pass over
`file_intelligence.json` (an O(files-in-cache) scan), so total added cost
is `~MAX_QUERY_TERMS * (one search() call)`, independent of how long or
identifier-dense the source query is. Deliberately small (not the 3-8 the
ticket allows for *displayed* results) — this caps *searches*, a different,
cost-bearing knob from `quor.mcp.server._MAX_RELEVANT_FILES`, which only
caps what's shown after merging."""

# One combined pattern, scanned left-to-right in a single pass so the
# returned order always matches first-appearance order in `text`:
#   - `quote`/`quoted`: a backtick- or double-quote-delimited span, closed
#     by the *same* quote character (`(?P=quote)` backreference) — a
#     single quote is deliberately excluded from the quote set, since an
#     apostrophe inside a contraction or possessive ("don't", "user's")
#     has no reliable closing partner, so allowing it invites spans that
#     swallow half a sentence.
#   - `word`: any run starting with a letter/underscore and continuing
#     through word characters plus path/extension punctuation
#     (`. / \ -`) — deliberately permissive at the regex level; shape
#     qualification happens afterward in `_qualify_bare_word()`, not here,
#     so the two concerns (what a token *can* look like vs. whether it
#     *counts*) stay separate and independently testable.
_TOKEN_PATTERN = re.compile(
    r'(?P<quote>["`])(?P<quoted>[^"`\n]{1,80})(?P=quote)' r"|(?P<word>[A-Za-z_][\w./\\-]*)"
)

_CASE_TRANSITION_RE = re.compile(r"[a-z0-9][A-Z]")
"""Signals camelCase/PascalCase: a lowercase letter or digit immediately
followed by an uppercase letter (`fileIntelligence`, `LoginManager`)."""


def extract_query_terms(text: str, *, limit: int = MAX_QUERY_TERMS) -> list[str]:
    """Extract up to `limit` deterministic, identifier-looking search terms
    from `text`, in first-seen order, deduplicated case-insensitively
    (case-fold equal terms keep only the first spelling seen — `search()`
    itself case-folds every query, so two spellings of the same term would
    only ever produce duplicate work, never a different result).

    A quoted span is always taken verbatim, whatever shape its contents
    have — quoting is itself the evidence of intent, e.g. `` `LoginManager` ``
    or `"payments"`. A bare word is taken only if `_qualify_bare_word()`
    finds a shape signal; otherwise it is silently dropped. Returns `[]`
    for text with no qualifying token (an empty string, or ordinary prose
    with no identifier-shaped words) — a normal, non-error outcome, mirrored
    by every downstream caller treating an empty list as "nothing to
    search for," not a failure.
    """
    seen: set[str] = set()
    terms: list[str] = []
    for match in _TOKEN_PATTERN.finditer(text):
        if len(terms) >= limit:
            break
        quoted = match.group("quoted")
        candidate = quoted.strip() if quoted is not None else _qualify_bare_word(match.group("word"))
        if not candidate:
            continue
        key = candidate.casefold()
        if key in seen:
            continue
        seen.add(key)
        terms.append(candidate)
    return terms


def _qualify_bare_word(word: str) -> str | None:
    """Trim incidental leading/trailing path or sentence punctuation
    (`./search.py.` -> `search.py`, catching a common "mentioned at the
    end of a sentence" shape), then qualify the remainder as
    identifier-looking by exactly one of: contains an underscore
    (snake_case), contains a path separator (directory-like,
    `src/auth`), contains a dot (filename- or import-looking,
    `login.py`, `quor.pipeline.search`), or has a lowercase-to-uppercase
    case transition (camelCase/PascalCase). A word matching none of these
    (`the`, `find`, `auth` with no punctuation or case shape) is not
    identifier-looking by this module's own definition and returns
    `None` — this is the entire filter, not a partial one refined
    elsewhere."""
    stripped = word.strip("./\\-")
    if not stripped or not any(c.isalnum() for c in stripped):
        return None
    if "_" in stripped:
        return stripped
    if "/" in stripped or "\\" in stripped:
        return stripped
    if "." in stripped:
        return stripped
    if _CASE_TRANSITION_RE.search(stripped):
        return stripped
    return None
