"""QB-099A: git diff/show enrichment.

Splits a captured `git diff`/`git show` command's raw output into one
section per changed file, works out (from the *command*, not the diff text
— a limited-context diff usually doesn't contain enough of the file to
reconstruct full content) which two git revisions/working-tree state hold
that file's "old" and "new" full content, and — for each Python file where
fetching both succeeds and the file is genuinely parseable — replaces that
file's hunk text with `structural_diff_python`'s compact rendering.

Every other case (binary, added/deleted, non-Python, an unrecognized
command shape, any single fetch/parse failure) falls through with that
file's original hunk text completely untouched, so downstream
`preserve_patterns`/`collapse_unchanged_context`/`max_tokens` see exactly
what they see today. This module owns pure text/command parsing only — no
subprocess or filesystem access — so it can be unit-tested with plain
strings; `quor/plugins/builtin/git_structural_diff.py` (the actual Plugin)
supplies real `git show`/working-tree-read callables.

See `docs/design/QB-099-structural-diff-compression-investigation.md` §1
for why this exists (a pure reorder/rename/move is 100% `+`/`-`-prefixed in
git diff today, so no existing stage can compress it at all) and the
QB-099A backlog entry for why fetching full file content — rather than
working from the diff text alone — is a deliberate, explicitly-approved new
capability, not an oversight.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from quor.pipeline.ast_summarize.structural_diff_python import diff_python_files, render

# Above this size, a file is not fetched/parsed at all — a safety valve
# against a huge generated/vendored file (a lockfile-shaped .py, a bundled
# migration) costing real subprocess/parse time for a file that was never
# going to compress well structurally anyway. 256 KiB comfortably covers
# any hand-written source file.
_MAX_FILE_BYTES = 256 * 1024

_DIFF_GIT_LINE = re.compile(r"^diff --git a/(.+) b/(.+)$")
_RENAME_FROM = re.compile(r"^rename from (.+)$")
_RENAME_TO = re.compile(r"^rename to (.+)$")


@dataclass(frozen=True)
class FileSection:
    """One file's worth of a multi-file `git diff`/`git show` output."""

    header_text: str  # `diff --git ...` through `+++ b/...`, unchanged
    body_text: str  # everything after the header (the @@ hunks), replaceable
    old_path: str | None
    new_path: str | None
    is_binary: bool
    is_added: bool
    is_deleted: bool
    has_hunks: bool

    @property
    def eligible(self) -> bool:
        """True only for a plain-content-change Python file with both a
        real old and new path — the only shape this module ever rewrites."""
        return (
            self.has_hunks
            and not self.is_binary
            and not self.is_added
            and not self.is_deleted
            and self.old_path is not None
            and self.new_path is not None
            and self.old_path.endswith((".py", ".pyi"))
            and self.new_path.endswith((".py", ".pyi"))
        )


def split_diff_sections(diff_text: str) -> list[str]:
    """Split raw multi-file diff text into per-file chunks, each starting at
    its own `diff --git ` line. Leading text before the first `diff --git `
    (rare — e.g. a `git show <rev>` commit-message preamble) is kept as its
    own leading, non-file chunk, always left untouched by the caller."""
    lines = diff_text.split("\n")
    chunks: list[list[str]] = [[]]
    for line in lines:
        if line.startswith("diff --git "):
            chunks.append([line])
        else:
            chunks[-1].append(line)
    return ["\n".join(chunk) for chunk in chunks]


def count_diff_files(diff_text: str) -> int:
    """Count changed files in a multi-file `git diff`/`git show` output —
    the number of `diff --git ` chunks `parse_file_section` recognizes.

    QB-093's own recommendation: record this per git-diff invocation so a
    future decision on cross-file repeated-edit deduplication (QB-093's
    "idea 2", left evidence-gated pending real-usage proof) can be made
    from real usage data instead of a guess. Not used to build that
    deduplication itself.
    """
    return sum(
        1 for chunk in split_diff_sections(diff_text) if parse_file_section(chunk) is not None
    )


def parse_file_section(chunk: str) -> FileSection | None:
    """Parse one `diff --git `-prefixed chunk. Returns `None` if `chunk`
    doesn't actually start with a `diff --git ` line (the leading preamble
    chunk `split_diff_sections()` may produce)."""
    lines = chunk.split("\n")
    if not lines or not lines[0].startswith("diff --git "):
        return None

    m = _DIFF_GIT_LINE.match(lines[0])
    old_path = m.group(1) if m else None
    new_path = m.group(2) if m else None

    is_binary = is_added = is_deleted = False
    header_end = len(lines)
    for i, line in enumerate(lines[1:], start=1):
        if line.startswith("new file mode"):
            is_added = True
        elif line.startswith("deleted file mode"):
            is_deleted = True
        elif line.startswith("Binary files ") and " differ" in line:
            is_binary = True
        elif (rm := _RENAME_FROM.match(line)) is not None:
            old_path = rm.group(1)
        elif (rt := _RENAME_TO.match(line)) is not None:
            new_path = rt.group(1)
        elif line.startswith("@@ "):
            header_end = i
            break
        elif line.startswith("--- ") or line.startswith("+++ "):
            continue

    has_hunks = any(line.startswith("@@ ") for line in lines)
    header_text = "\n".join(lines[:header_end])
    body_text = "\n".join(lines[header_end:])
    return FileSection(
        header_text=header_text,
        body_text=body_text,
        old_path=old_path,
        new_path=new_path,
        is_binary=is_binary,
        is_added=is_added,
        is_deleted=is_deleted,
        has_hunks=has_hunks,
    )


# ---------------------------------------------------------------------------
# Command classification — which two content sources are "old" and "new"
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContentPlan:
    """How to fetch a changed file's "old" and "new" full content for a
    specific, recognized `git diff`/`git show` invocation shape.

    `old_ref`/`new_ref` are always fed to `git show` as `f"{ref}:{path}"` —
    an *empty string* (not the literal `":"`) is therefore the index
    sentinel, since `git show :path` (leading colon, nothing before it) is
    itself git's own syntax for "the index," and `f"{'':}{':'}{path}"`
    already produces exactly that without any special-casing in the fetch
    callable. Using `":"` here instead would build `"::path"`, which `git
    show` rejects — caught during this item's own end-to-end testing
    against a real repo, not by a unit test (every unit test below stubs
    `git_show`, so a git-syntax mistake like this one is invisible to them
    by construction).
    """

    old_ref: str  # a git revision, or "" for the index
    new_ref: str | None  # a git revision/"" for the index, or None for the working tree


_FLAGS_TAKING_NO_VALUE = {
    "--staged", "--cached", "--no-color", "--color", "-p", "--patch",
    "-U", "--stat", "--numstat", "--name-only", "--name-status",
}


def classify_command(cmd_str: str) -> ContentPlan | None:
    """Return a `ContentPlan` for the small set of `git diff`/`git show`
    shapes this module knows how to enrich, or `None` for anything else
    (range syntax like `a..b`/`a...b`, more than two revisions, `git log -p`,
    unrecognized flags) — deliberately conservative: an unrecognized shape
    means no enrichment at all, never a guess."""
    tokens = cmd_str.split()
    if len(tokens) < 2 or tokens[0] != "git":
        return None

    subcommand = tokens[1]
    rest = tokens[2:]

    # Strip a trailing `-- <paths...>` — paths never affect which revisions
    # are being compared.
    if "--" in rest:
        rest = rest[: rest.index("--")]

    # Anything token-shaped like a flag but not one of the small set we
    # understand makes the whole invocation unrecognized — e.g. `--merge-base`,
    # `-w`/`--ignore-all-space` (changes hunk content in a way this module
    # doesn't account for), `-U<n>` with a value (context width changes
    # whether the *original* diff already had a chance to show more).
    flags = [t for t in rest if t.startswith("-")]
    revisions = [t for t in rest if not t.startswith("-")]
    unknown_flags = [f for f in flags if f not in _FLAGS_TAKING_NO_VALUE]
    if unknown_flags:
        return None
    if any(".." in r for r in revisions):
        return None  # a..b / a...b range syntax — different semantics, skip

    if subcommand == "show":
        if len(revisions) != 1:
            return None
        rev = revisions[0]
        return ContentPlan(old_ref=f"{rev}^", new_ref=rev)

    if subcommand != "diff":
        return None

    staged = "--staged" in flags or "--cached" in flags

    if staged:
        if len(revisions) != 0:
            return None  # `git diff --staged <rev>` is a different comparison — skip
        return ContentPlan(old_ref="HEAD", new_ref="")

    if len(revisions) == 0:
        return ContentPlan(old_ref="", new_ref=None)  # index vs working tree
    if len(revisions) == 1:
        return ContentPlan(old_ref=revisions[0], new_ref=None)  # rev vs working tree
    if len(revisions) == 2:
        return ContentPlan(old_ref=revisions[0], new_ref=revisions[1])
    return None


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

GitShow = Callable[[str, str, Path], "str | None"]
"""`(ref, path, cwd) -> content, or None on any failure` — `ref` is a git
revision or `""` for the index (the callable builds `f"{ref}:{path}"`, so an
empty `ref` naturally becomes git's own `:path` index syntax); `path` is
repo-relative."""

ReadWorkingTree = Callable[[str, Path], "str | None"]
"""`(path, cwd) -> content, or None on any failure` (missing file, not
UTF-8-decodable, etc.)."""


def _fetch(
    plan: ContentPlan,
    old_path: str,
    new_path: str,
    cwd: Path,
    git_show: GitShow,
    read_working_tree: ReadWorkingTree,
) -> tuple[str | None, str | None]:
    old_content = git_show(plan.old_ref, old_path, cwd)
    if plan.new_ref is None:
        new_content = read_working_tree(new_path, cwd)
    else:
        new_content = git_show(plan.new_ref, new_path, cwd)
    return old_content, new_content


def enrich_git_diff(
    cmd_str: str,
    diff_text: str,
    cwd: Path,
    *,
    git_show: GitShow,
    read_working_tree: ReadWorkingTree,
) -> str:
    """Return `diff_text` with every eligible Python file's hunk section
    replaced by a compact structural-diff rendering. Falls through to
    `diff_text` unchanged (whole-input fail-open) if the command shape isn't
    recognized; falls through per-file (file's original chunk unchanged) for
    every other reason a specific file can't be enriched."""
    plan = classify_command(cmd_str)
    if plan is None:
        return diff_text

    chunks = split_diff_sections(diff_text)
    # The leading preamble (text before the first `diff --git ` line — e.g.
    # `git show <rev>`'s own commit message) is always untouched, but is
    # usually empty (`git diff`'s own output starts directly with
    # `diff --git `) — included only when non-empty, so a diff with nothing
    # eligible to enrich round-trips byte-for-byte rather than picking up a
    # spurious leading blank line from `"\n".join()`.
    rewritten: list[str] = [chunks[0]] if chunks[0] else []

    for chunk in chunks[1:]:
        section = parse_file_section(chunk)
        if section is None or not section.eligible:
            rewritten.append(chunk)
            continue

        assert section.old_path is not None and section.new_path is not None
        try:
            old_content, new_content = _fetch(
                plan, section.old_path, section.new_path, cwd, git_show, read_working_tree
            )
        except Exception:  # noqa: BLE001 — any fetch failure is per-file fail-open
            rewritten.append(chunk)
            continue

        if (
            old_content is None
            or new_content is None
            or len(old_content) > _MAX_FILE_BYTES
            or len(new_content) > _MAX_FILE_BYTES
        ):
            rewritten.append(chunk)
            continue

        try:
            result = diff_python_files(old_content, new_content)
            if result.classification == "unparseable":
                rewritten.append(chunk)
                continue
            rendered = render(result, old_content, new_content)
        except Exception:  # noqa: BLE001
            rewritten.append(chunk)
            continue

        rewritten.append(f"{section.header_text}\n{rendered}")

    return "\n".join(rewritten)
