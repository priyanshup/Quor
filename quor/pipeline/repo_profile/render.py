"""RepoProfile -> deterministic output (Markdown default, JSON optional).

Fixed template only — no invented prose, no content that isn't already a
field on `RepoProfile`. Sections with nothing detected are omitted
entirely (rather than printed as "(none detected)") to keep the profile as
token-lean as the rest of Quor's compression work, except `Languages` and
`Statistics`, which always appear since they describe the scan itself.
"""

from __future__ import annotations

import orjson

from quor.pipeline.repo_profile.model import DetectedItem, RepoProfile


def render_markdown(profile: RepoProfile) -> str:
    lines: list[str] = ["# Repository Profile", "", f"Root: {profile.root}", ""]

    lines.extend(_render_languages(profile))
    lines.extend(_render_detected_section("Build System", profile.build_systems))
    lines.extend(_render_detected_section("Package Managers", profile.package_managers))
    lines.extend(_render_detected_section("Frameworks", profile.frameworks))
    lines.extend(_render_detected_section("Test Framework", profile.test_frameworks))
    lines.extend(_render_detected_section("CI System", profile.ci_systems))
    lines.extend(_render_detected_section("Containerization", profile.containerization))
    lines.extend(_render_detected_section("Databases", profile.databases))
    lines.extend(_render_detected_section("Configuration Files", profile.configuration_files))
    lines.extend(_render_lockfiles(profile))
    lines.extend(_render_entry_points(profile))
    lines.extend(_render_services(profile))
    lines.extend(_render_important_directories(profile))
    lines.extend(_render_statistics(profile))
    lines.extend(_render_notes(profile))

    return "\n".join(lines).rstrip() + "\n"


def render_json(profile: RepoProfile) -> str:
    """JSON output mode (`--json`) — a secondary interface, not the
    primary one (per QB-061's own design: "if JSON output naturally fits,
    implement it as an optional output mode"). Same data as the Markdown
    render, just machine-readable."""
    return orjson.dumps(profile.model_dump(), option=orjson.OPT_INDENT_2).decode()


def _pluralize_files(count: int) -> str:
    return "file" if count == 1 else "files"


def _render_languages(profile: RepoProfile) -> list[str]:
    lines = ["## Languages", ""]
    if not profile.languages:
        lines.append("(no recognized language files)")
    else:
        for lang in profile.languages:
            noun = _pluralize_files(lang.file_count)
            lines.append(f"- {lang.language} — {lang.file_count} {noun} ({lang.percentage}%)")
    lines.append("")
    return lines


def _render_detected_section(title: str, items: list[DetectedItem]) -> list[str]:
    if not items:
        return []
    lines = [f"## {title}", ""]
    for item in items:
        evidence = "; ".join(item.evidence)
        lines.append(f"- {item.name} — {evidence}")
    lines.append("")
    return lines


def _render_lockfiles(profile: RepoProfile) -> list[str]:
    if not profile.lockfiles:
        return []
    lines = ["## Lockfiles", ""]
    lines.extend(f"- {path}" for path in profile.lockfiles)
    lines.append("")
    return lines


def _render_entry_points(profile: RepoProfile) -> list[str]:
    if not profile.entry_points:
        return []
    lines = ["## Entry Points", ""]
    for ep in profile.entry_points:
        lines.append(f"- {ep.target} — {ep.evidence}")
    lines.append("")
    return lines


def _render_services(profile: RepoProfile) -> list[str]:
    if not profile.services:
        return []
    lines = ["## Services / Modules", ""]
    for svc in profile.services:
        lines.append(f"- {svc.path}/ — {svc.manifest}")
    lines.append("")
    return lines


def _render_important_directories(profile: RepoProfile) -> list[str]:
    if not profile.important_directories:
        return []
    lines = ["## Important Directories", ""]
    for d in profile.important_directories:
        noun = _pluralize_files(d.file_count)
        lines.append(f"- {d.path}/ — {d.file_count} {noun}")
    lines.append("")
    return lines


def _render_statistics(profile: RepoProfile) -> list[str]:
    stats = profile.statistics
    lines = ["## Statistics", ""]
    lines.append(f"- Total files: {stats.total_files}")
    lines.append(f"- Total directories: {stats.total_directories}")
    lines.append(f"- Primary language: {stats.primary_language or 'unknown'}")
    if stats.git_commit_count is not None:
        lines.append(f"- Git commits: {stats.git_commit_count}")
    lines.append("")
    return lines


def _render_notes(profile: RepoProfile) -> list[str]:
    if not profile.notes:
        return []
    lines = ["## Notes", ""]
    lines.extend(f"- {note}" for note in profile.notes)
    lines.append("")
    return lines
