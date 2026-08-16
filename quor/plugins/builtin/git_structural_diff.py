"""QB-099A: the builtin PRE_FILTER plugin that gives
`quor.pipeline.git_diff_enrich` real subprocess/filesystem access.

Registered at the "builtin" tier by `quor.engine.dispatcher._setup_plugins()`
— not discovered via entry points, always present, no installation needed.
`PluginCategory.PRE_FILTER` is the category this project's own plugin API
(`quor/plugins/base.py`) documents for exactly this: "input enrichment that
later plugins depend on" — the FILTER-stage `git-diff` `ContentMask` pipeline
(`quor/filters/builtin/git.toml`) still runs afterward, unchanged, on
whatever this plugin leaves behind.

Fail-open at three independent levels, weakest to strongest:
  1. `quor.pipeline.git_diff_enrich.enrich_git_diff()` itself is per-file
     fail-open (one file's fetch/parse failure never affects any other
     file's chunk).
  2. `execute()` below never lets a git/filesystem call raise past its own
     `try`/`except` — a `_git_show`/`_read_working_tree` failure returns
     `None`, which `enrich_git_diff()` already treats as "leave this file's
     chunk untouched."
  3. The plugin executor itself (`quor.plugins.registry.PluginRegistry`)
     catches any exception this module still somehow lets through and
     passes the payload on unchanged — the same guarantee every other
     plugin gets "for free," never relied on as the only safety net here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import ClassVar

import regex

from quor.pipeline.git_diff_enrich import enrich_git_diff
from quor.plugins.base import (
    CAPABILITY_CONTENT_TRANSFORM,
    QUOR_PLUGIN_API_VERSION,
    PluginCategory,
    PluginContext,
    PluginMetadata,
    PluginPayload,
    PluginResult,
)

# Per-subprocess-call budget. Small and strict: this is supplementary
# enrichment layered on top of a command dispatch that already has its own
# 25s ceiling (`quor.engine.dispatcher._run_subprocess`) — a slow git
# invocation here must degrade to "this file's chunk stays as the original
# diff text," never stall the whole hook.
_GIT_SHOW_TIMEOUT_S = 2.0

# Command-shape match — deliberately the exact same regex `git-diff`'s own
# `match_command` uses (`quor/filters/builtin/git.toml`), so this plugin
# never does work for a command the downstream filter wouldn't even touch.
_MATCH_COMMAND = regex.compile(r"^git\s+(diff|show)\b")


def _git_show(ref: str, path: str, cwd: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "show", f"{ref}:{path}"],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_SHOW_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _read_working_tree(path: str, cwd: Path) -> str | None:
    try:
        return (cwd / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


class GitStructuralDiffPlugin:
    """Rewrites declaration-shaped Python changes (reorder/rename/
    formatting-only/import-only — QB-099A) in `git diff`/`git show` output
    into a compact structural summary before `ContentMask` compression."""

    api_version: ClassVar[int] = QUOR_PLUGIN_API_VERSION

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            plugin_id="quor.git-structural-diff",
            display_name="Git Structural Diff (QB-099A)",
            version="1.0.0",
            category=PluginCategory.PRE_FILTER,
            author="Quor",
            description=(
                "Rewrites declaration-shaped Python changes (reorder/rename/"
                "formatting-only/import-only) in git diff/show output into a "
                "compact structural summary before ContentMask compression."
            ),
            priority=50,
            capabilities=(CAPABILITY_CONTENT_TRANSFORM,),
        )

    def initialize(self, ctx: PluginContext) -> None:
        pass

    def execute(self, payload: PluginPayload, ctx: PluginContext) -> PluginResult:
        if _MATCH_COMMAND.search(payload.command) is None:
            return PluginResult(payload=payload)

        cwd = ctx.project_root or Path.cwd()
        try:
            enriched = enrich_git_diff(
                payload.command,
                payload.current_output,
                cwd,
                git_show=_git_show,
                read_working_tree=_read_working_tree,
            )
        except Exception:  # noqa: BLE001 — belt-and-suspenders; see module docstring
            return PluginResult(payload=payload)

        if enriched == payload.current_output:
            return PluginResult(payload=payload)
        return PluginResult(
            payload=payload.replace_output(enriched),
            was_modified=True,
            note="Replaced declaration-shaped Python file diffs with a structural summary (QB-099A).",
        )

    def shutdown(self) -> None:
        pass
