"""Language histogram — extension-routed, computed directly from the file
walk (no detector rules involved; see
`docs/design/QB-061-repo-context-profile.md` §3.2's "language ... no
detector rules needed, computed straight from the walk" note).

The extension table below is deliberately a fresh, purpose-built table for
this module rather than importing `claude_read.py`'s
`_SOURCE_CODE_FILTER_NAMES_BY_EXTENSION` — that table is scoped to "which
extensions have an AST-summarization filter today" (a narrower, filter-
routing concern that's missing several languages the AST registry itself
already supports, e.g. Go/Java/Rust/C#) and is a private, underscore-
prefixed implementation detail of a different module. A repository census
is a different question ("what language is this file written in") that
should not be coupled to, or silently limited by, filter coverage.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from quor.pipeline.repo_profile.model import LanguageStat

# Extension -> language display name. Deliberately conservative: only
# extensions that unambiguously identify a programming/markup language are
# included. Config/data formats (.json, .yaml, .toml, .lock, ...) are
# intentionally excluded from the language census — they are reported
# elsewhere in the profile (configuration_files, lockfiles), and counting
# them here would dilute every real language's percentage share.
_EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".py": "Python",
    ".pyi": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".go": "Go",
    ".java": "Java",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".rs": "Rust",
    ".cs": "C#",
    ".c": "C",
    ".h": "C",
    ".cpp": "C++",
    ".cc": "C++",
    ".cxx": "C++",
    ".hpp": "C++",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".scala": "Scala",
    ".sh": "Shell",
    ".bash": "Shell",
    ".ps1": "PowerShell",
    ".sql": "SQL",
    ".html": "HTML",
    ".htm": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".less": "Less",
    ".vue": "Vue",
    ".dart": "Dart",
    ".lua": "Lua",
    ".r": "R",
    ".m": "Objective-C",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".erl": "Erlang",
    ".clj": "Clojure",
    ".hs": "Haskell",
}


def compute_language_stats(files: list[str]) -> list[LanguageStat]:
    """Return per-language file counts/percentages, sorted by file count
    descending (ties broken alphabetically for deterministic output).

    Percentage is relative to the total count of files with a *recognized
    language extension* — not every file in the repo (see module docstring:
    config/data files aren't language files and would otherwise dilute
    every real language's share).
    """
    counts: dict[str, int] = {}
    for f in files:
        ext = PurePosixPath(f).suffix.lower()
        language = _EXTENSION_TO_LANGUAGE.get(ext)
        if language is not None:
            counts[language] = counts.get(language, 0) + 1

    total = sum(counts.values())
    if total == 0:
        return []

    stats = [
        LanguageStat(
            language=language,
            file_count=count,
            percentage=round(count / total * 100, 1),
        )
        for language, count in counts.items()
    ]
    stats.sort(key=lambda s: (-s.file_count, s.language))
    return stats
