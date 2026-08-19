"""Deterministic entry-point detection (QB-061).

Scoped to two verifiable sources only — no AST/tree-sitter symbol
extraction (deferred; see `docs/design/QB-061-repo-context-profile.md`
§6 "Phase D" and §10's phasing note):

1. **Manifest-declared entry points** — structured field reads from
   `pyproject.toml` ([project.scripts]/[project.gui-scripts]/
   [tool.poetry.scripts]), `package.json` (`bin`/`main`), `Cargo.toml`
   ([[bin]] tables, or the implicit `src/main.rs` convention), and `go.mod`
   (module path, plus the `main.go` / `cmd/*/main.go` convention). Each of
   these reuses the same stdlib parser Quor's `structured_data` package
   already calls for the same file formats (`tomllib`/`json`) — not a
   second, hand-rolled parser.
2. **A bounded content marker** — root-level (not recursive) `.py` files
   containing `if __name__ == "__main__":`, the single, unambiguous Python
   convention for "this file is meant to be run directly."

Every failure mode (malformed manifest, missing field, unreadable file)
fails open to "no entry point from this source" rather than raising —
entry-point detection is best-effort evidence, never a hard requirement.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path, PurePosixPath

import regex

from quor.pipeline.repo_profile._longpath import to_long_path
from quor.pipeline.repo_profile.model import EntryPoint

_MAIN_GUARD_RE = r"^if\s+__name__\s*==\s*[\"']__main__[\"']\s*:"
_MAIN_GUARD_TIMEOUT = 1.0
_MAX_SCAN_BYTES = 65_536  # 64 KiB — a __main__ guard is always near a small script


def detect_entry_points(root: Path, files: list[str]) -> list[EntryPoint]:
    """Return every deterministically detected entry point, in a stable,
    source-grouped order (Python manifest, Node, Rust, Go, then the
    root-level `__main__` scan)."""
    file_set = set(files)
    entries: list[EntryPoint] = []

    if "pyproject.toml" in file_set:
        entries.extend(_python_pyproject_entry_points(root))
    if "package.json" in file_set:
        entries.extend(_node_package_json_entry_points(root))
    if "Cargo.toml" in file_set:
        entries.extend(_cargo_entry_points(root, file_set))
    if "go.mod" in file_set:
        entries.extend(_go_entry_points(root, file_set))
    entries.extend(_python_main_guard_entry_points(root, files))

    return entries


def _load_toml(path: Path) -> dict[str, object] | None:
    try:
        with open(to_long_path(path), "rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return None


def _load_json(path: Path) -> dict[str, object] | None:
    try:
        with open(to_long_path(path), encoding="utf-8", errors="ignore") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _python_pyproject_entry_points(root: Path) -> list[EntryPoint]:
    data = _load_toml(root / "pyproject.toml")
    if data is None:
        return []
    entries: list[EntryPoint] = []

    project = data.get("project")
    if isinstance(project, dict):
        for section, label in (
            ("scripts", "[project.scripts]"),
            ("gui-scripts", "[project.gui-scripts]"),
        ):
            scripts = project.get(section)
            if isinstance(scripts, dict):
                for name, target in scripts.items():
                    entries.append(
                        EntryPoint(
                            target=f"{name} = {target}",
                            evidence=f"pyproject.toml {label}",
                        )
                    )

    tool = data.get("tool")
    if isinstance(tool, dict):
        poetry = tool.get("poetry")
        if isinstance(poetry, dict):
            scripts = poetry.get("scripts")
            if isinstance(scripts, dict):
                for name, target in scripts.items():
                    entries.append(
                        EntryPoint(
                            target=f"{name} = {target}",
                            evidence="pyproject.toml [tool.poetry.scripts]",
                        )
                    )

    return entries


def _node_package_json_entry_points(root: Path) -> list[EntryPoint]:
    data = _load_json(root / "package.json")
    if data is None:
        return []
    entries: list[EntryPoint] = []

    bin_field = data.get("bin")
    if isinstance(bin_field, str):
        entries.append(EntryPoint(target=bin_field, evidence="package.json bin"))
    elif isinstance(bin_field, dict):
        for name, target in bin_field.items():
            entries.append(
                EntryPoint(target=f"{name} = {target}", evidence="package.json bin")
            )

    main_field = data.get("main")
    if isinstance(main_field, str) and main_field:
        entries.append(EntryPoint(target=main_field, evidence="package.json main"))

    return entries


def _cargo_entry_points(root: Path, files: set[str]) -> list[EntryPoint]:
    data = _load_toml(root / "Cargo.toml")
    entries: list[EntryPoint] = []

    if data is not None:
        bins = data.get("bin")
        if isinstance(bins, list):
            for entry in bins:
                if isinstance(entry, dict) and "name" in entry:
                    path = entry.get("path", "src/main.rs")
                    entries.append(
                        EntryPoint(
                            target=str(entry["name"]),
                            evidence=f"Cargo.toml [[bin]] ({path})",
                        )
                    )

    if not entries and "src/main.rs" in files:
        entries.append(
            EntryPoint(target="src/main.rs", evidence="Cargo default binary convention")
        )

    return entries


def _go_entry_points(root: Path, files: set[str]) -> list[EntryPoint]:
    del root  # not needed — go.mod's module path isn't itself an entry point
    entries: list[EntryPoint] = []

    if "main.go" in files:
        entries.append(EntryPoint(target="main.go", evidence="Go main package convention"))

    for f in files:
        parts = PurePosixPath(f).parts
        if len(parts) == 3 and parts[0] == "cmd" and parts[2] == "main.go":
            entries.append(
                EntryPoint(target=f, evidence="cmd/<name>/main.go convention")
            )

    return entries


def _python_main_guard_entry_points(root: Path, files: list[str]) -> list[EntryPoint]:
    """Root-level (non-recursive) `.py` files with an `if __name__ ==
    "__main__":` guard — bounded to the repo root so this never becomes an
    unbounded, whole-tree content scan."""
    entries: list[EntryPoint] = []
    root_level_py = sorted(
        f for f in files if f.endswith(".py") and "/" not in f
    )
    for f in root_level_py:
        try:
            with open(to_long_path(root / f), "rb") as fh:
                raw = fh.read(_MAX_SCAN_BYTES)
        except OSError:
            continue
        content = raw.decode("utf-8", errors="ignore")
        try:
            if regex.search(_MAIN_GUARD_RE, content, flags=regex.MULTILINE, timeout=_MAIN_GUARD_TIMEOUT):
                entries.append(EntryPoint(target=f, evidence='if __name__ == "__main__": guard'))
        except TimeoutError:
            continue
    return entries
