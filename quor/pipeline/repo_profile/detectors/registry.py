"""Three-tier detector-rule registry: project > user > built-in.

Loading is structurally identical to `quor/filters/registry.py`'s
`FilterRegistry` (same `_load_builtin`/`_load_user`/`_load_project` shape,
the same `quor/filters/trust.py::is_git_tracked` check for project-local
rules, reused directly rather than reimplemented). Matching is different by
necessity: `FilterRegistry.find()` returns the first matching filter for a
command (one command, one filter); a repository legitimately satisfies many
detector rules at once (a repo can be a Flask app *and* Dockerized *and*
built on GitHub Actions), so `detect()` evaluates every loaded rule and
returns every match, grouped by category.
"""

from __future__ import annotations

import warnings
from pathlib import Path, PurePosixPath

import platformdirs
import regex

from quor.errors import ConfigError
from quor.filters.trust import is_git_tracked
from quor.pipeline.repo_profile.detectors.loader import load_detector_file
from quor.pipeline.repo_profile.detectors.model import DetectorRule
from quor.pipeline.repo_profile.model import DetectedItem

_BUILTIN_DIR = Path(__file__).parent / "builtin"
_PATH_REGEX_TIMEOUT: float = 0.5
_CONTENT_REGEX_TIMEOUT: float = 1.0
_MAX_CONTENT_READ_BYTES = 262_144  # 256 KiB — generous for any real manifest/config file


class DetectorRegistry:
    """Three-tier detector-rule registry: project (highest) > user > built-in (lowest)."""

    def __init__(
        self,
        project_root: Path | None = None,
        *,
        skip_user: bool = False,
        skip_project: bool = False,
    ) -> None:
        self._builtin: list[DetectorRule] = []
        self._user: list[DetectorRule] = []
        self._project: list[DetectorRule] = []

        self._load_builtin()
        if not skip_user:
            self._load_user()
        if not skip_project and project_root is not None:
            self._load_project(project_root)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load_builtin(self) -> None:
        if not _BUILTIN_DIR.exists():
            return
        for toml_path in sorted(_BUILTIN_DIR.glob("*.toml")):
            try:
                self._builtin.extend(load_detector_file(toml_path))
            except ConfigError as exc:
                warnings.warn(
                    f"[quor] Failed to load built-in detector {toml_path.name}: {exc}",
                    stacklevel=2,
                )

    def _load_user(self) -> None:
        user_dir = Path(platformdirs.user_config_dir("quor")) / "detectors"
        if not user_dir.exists():
            return
        for toml_path in sorted(user_dir.glob("*.toml")):
            try:
                self._user.extend(load_detector_file(toml_path))
            except ConfigError as exc:
                warnings.warn(
                    f"[quor] Failed to load user detector {toml_path.name}: {exc}",
                    stacklevel=2,
                )

    def _load_project(self, project_root: Path) -> None:
        project_dir = project_root / ".quor" / "detectors"
        if not project_dir.exists():
            return
        for toml_path in sorted(project_dir.glob("*.toml")):
            if not is_git_tracked(toml_path):
                warnings.warn(
                    f"[quor] Project detector {toml_path.name} is not git-tracked; skipping",
                    stacklevel=2,
                )
                continue
            try:
                self._project.extend(load_detector_file(toml_path))
            except ConfigError as exc:
                warnings.warn(
                    f"[quor] Failed to load project detector {toml_path.name}: {exc}",
                    stacklevel=2,
                )

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def all_rules(self) -> list[tuple[str, DetectorRule]]:
        """Return all rules as (tier, rule) pairs, project > user > builtin."""
        result: list[tuple[str, DetectorRule]] = []
        for r in self._project:
            result.append(("project", r))
        for r in self._user:
            result.append(("user", r))
        for r in self._builtin:
            result.append(("builtin", r))
        return result

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def detect(self, files: list[str], root: Path) -> dict[str, list[DetectedItem]]:
        """Evaluate every loaded rule against `files` (repo-relative POSIX
        paths, as returned by `walk.walk_repository()`) and `root` (used
        only to read the small number of candidate files a content-checked
        rule needs). Returns detected items grouped by category, each
        category's items sorted by name for deterministic output.

        A (category, name) pair is reported at most once, even if rules
        with the same name exist in multiple tiers — the highest-priority
        tier's match wins, mirroring `FilterRegistry`'s project > user >
        built-in precedence (`all_rules()` already yields tiers in that
        order, so "first seen wins" is sufficient).

        **Performance (QB-072 perf follow-up).** Each file's basename is
        computed exactly once here and passed to every rule, rather than
        every rule re-deriving it via its own `PurePosixPath(file_path).name`
        call — this loop runs once per (rule, file) pair, so at real repo
        scale (hundreds of files x dozens of built-in rules) the repeated
        `PurePosixPath` construction was, empirically, the single largest
        cost in a full `quor map` build (profiled via `cProfile` against an
        800-file synthetic repo — see `tests/benchmarks/repo_intel_benchmark.py`).
        Precomputing it once turns that cost from O(rules x files) `PurePosixPath`
        constructions into O(files), with no change to which files match or
        in what order (`file_basenames` is built via one pass over `files`
        in the same order `_match_rule`'s own list comprehension already
        iterated them, so `candidates[0]`/first-content-match tie-breaking
        is bit-for-bit unchanged).
        """
        file_basenames = {f: PurePosixPath(f).name for f in files}

        seen: dict[tuple[str, str], DetectedItem] = {}
        for _tier, rule in self.all_rules():
            key = (rule.category, rule.name)
            if key in seen:
                continue
            item = self._match_rule(rule, files, root, file_basenames)
            if item is not None:
                seen[key] = item

        grouped: dict[str, list[DetectedItem]] = {}
        for (category, _name), item in seen.items():
            grouped.setdefault(category, []).append(item)
        for items in grouped.values():
            items.sort(key=lambda i: i.name)
        return grouped

    def _match_rule(
        self, rule: DetectorRule, files: list[str], root: Path, file_basenames: dict[str, str]
    ) -> DetectedItem | None:
        candidates = [f for f in files if self._file_matches(f, rule, file_basenames[f])]
        if not candidates:
            return None

        if not rule.match_content:
            return DetectedItem(
                name=rule.name,
                category=rule.category,
                evidence=[f"{candidates[0]} — {rule.evidence}"],
            )

        for path in candidates:
            content = _read_capped(root / path)
            if content is None:
                continue
            for pattern in rule.match_content:
                try:
                    if regex.search(pattern, content, timeout=_CONTENT_REGEX_TIMEOUT):
                        return DetectedItem(
                            name=rule.name,
                            category=rule.category,
                            evidence=[f"{path} — {rule.evidence}"],
                        )
                except (regex.error, TimeoutError):
                    continue
        return None

    @staticmethod
    def _file_matches(file_path: str, rule: DetectorRule, basename: str) -> bool:
        if rule.match_basename and basename in rule.match_basename:
            return True
        for pattern in rule.match_path_regex:
            try:
                if regex.search(pattern, file_path, timeout=_PATH_REGEX_TIMEOUT):
                    return True
            except (regex.error, TimeoutError):
                continue
        return False


def _read_capped(path: Path) -> str | None:
    """Read up to `_MAX_CONTENT_READ_BYTES` of `path` as UTF-8 text.

    Returns None on any read/decode failure (missing file, permission
    error, binary content) — fail-open, matching the discipline used
    throughout the hook path (`claude_read.py`, `dispatcher.py`)."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(_MAX_CONTENT_READ_BYTES)
    except OSError:
        return None
    return raw.decode("utf-8", errors="ignore")
