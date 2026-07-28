"""Fixture-repo benchmark corpus for the Repository Context Profile (QB-061).

Mirrors `docs/design/QB-061-repo-context-profile.md` §8's benchmark
strategy: this is not the existing `tests/benchmarks/manifest.toml`
compression-ratio harness (there is no "before" blob to compress against
here) — it's a parallel structure whose primary signal is precision/
recall against hand-labeled expected facts, plus a determinism check and a
performance budget, run against small, real fixture repos committed under
`tests/fixtures/repo_profile/`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from quor.pipeline.repo_profile.model import DetectedItem, RepoProfile
from quor.pipeline.repo_profile.profiler import build_profile

_FIXTURES_ROOT = Path(__file__).parent.parent / "fixtures" / "repo_profile"


@dataclass(frozen=True)
class ExpectedFacts:
    case_name: str
    expected_languages: frozenset[str] = field(default_factory=frozenset)
    expected_build_systems: frozenset[str] = field(default_factory=frozenset)
    expected_package_managers: frozenset[str] = field(default_factory=frozenset)
    expected_frameworks: frozenset[str] = field(default_factory=frozenset)
    expected_test_frameworks: frozenset[str] = field(default_factory=frozenset)
    expected_ci_systems: frozenset[str] = field(default_factory=frozenset)
    expected_containerization: frozenset[str] = field(default_factory=frozenset)
    expected_configuration_files: frozenset[str] = field(default_factory=frozenset)
    expected_services: frozenset[str] = field(default_factory=frozenset)
    expected_entry_point_targets: frozenset[str] = field(default_factory=frozenset)
    must_not_detect: frozenset[str] = field(default_factory=frozenset)
    """Names that must NOT appear in *any* detected category — the
    false-positive check."""


_CASES: list[ExpectedFacts] = [
    ExpectedFacts(
        case_name="flask-pip",
        expected_languages=frozenset({"Python"}),
        expected_package_managers=frozenset({"pip"}),
        expected_frameworks=frozenset({"flask"}),
        expected_test_frameworks=frozenset({"pytest"}),
        expected_ci_systems=frozenset({"github-actions"}),
        expected_containerization=frozenset({"docker"}),
        expected_entry_point_targets=frozenset({"app.py"}),
        must_not_detect=frozenset({"django", "fastapi", "poetry", "npm", "jest"}),
    ),
    ExpectedFacts(
        case_name="node-express-pnpm",
        expected_languages=frozenset({"JavaScript"}),
        expected_package_managers=frozenset({"pnpm"}),
        expected_frameworks=frozenset({"express"}),
        expected_configuration_files=frozenset({"eslint"}),
        expected_entry_point_targets=frozenset({"index.js"}),
        must_not_detect=frozenset({"react", "vue", "flask", "yarn", "npm"}),
    ),
    ExpectedFacts(
        case_name="go-service",
        expected_languages=frozenset({"Go"}),
        expected_build_systems=frozenset({"go-modules"}),
        expected_entry_point_targets=frozenset({"main.go", "cmd/worker/main.go"}),
        must_not_detect=frozenset({"go-test", "gin", "echo"}),
    ),
    ExpectedFacts(
        case_name="polyglot-monorepo",
        expected_languages=frozenset({"Python", "TypeScript"}),
        expected_package_managers=frozenset({"poetry"}),
        expected_frameworks=frozenset({"react"}),
        expected_services=frozenset({"backend", "frontend"}),
        must_not_detect=frozenset({"django", "vue", "npm"}),
    ),
]


def _names(items: list[DetectedItem]) -> frozenset[str]:
    return frozenset(i.name for i in items)


def _all_detected_names(profile: RepoProfile) -> frozenset[str]:
    return frozenset().union(
        _names(profile.frameworks),
        _names(profile.build_systems),
        _names(profile.package_managers),
        _names(profile.test_frameworks),
        _names(profile.ci_systems),
        _names(profile.databases),
        _names(profile.containerization),
        _names(profile.configuration_files),
    )


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c.case_name)
class TestFixtureRepoPrecisionRecall:
    def test_expected_facts_detected(self, case: ExpectedFacts) -> None:
        profile = build_profile(_FIXTURES_ROOT / case.case_name)

        language_names = frozenset(lang.language for lang in profile.languages)
        assert case.expected_languages <= language_names

        assert case.expected_build_systems <= _names(profile.build_systems)
        assert case.expected_package_managers <= _names(profile.package_managers)
        assert case.expected_frameworks <= _names(profile.frameworks)
        assert case.expected_test_frameworks <= _names(profile.test_frameworks)
        assert case.expected_ci_systems <= _names(profile.ci_systems)
        assert case.expected_containerization <= _names(profile.containerization)
        assert case.expected_configuration_files <= _names(profile.configuration_files)

        service_paths = frozenset(s.path for s in profile.services)
        assert case.expected_services <= service_paths

        entry_targets = frozenset(ep.target for ep in profile.entry_points)
        assert case.expected_entry_point_targets <= entry_targets

    def test_no_false_positives(self, case: ExpectedFacts) -> None:
        profile = build_profile(_FIXTURES_ROOT / case.case_name)

        detected = _all_detected_names(profile)
        false_positives = case.must_not_detect & detected
        assert not false_positives, f"false positives in {case.case_name}: {false_positives}"


class TestDeterminism:
    @pytest.mark.parametrize("case", _CASES, ids=lambda c: c.case_name)
    def test_repeated_scan_is_byte_identical(self, case: ExpectedFacts) -> None:
        root = _FIXTURES_ROOT / case.case_name

        first = build_profile(root)
        second = build_profile(root)

        assert first.model_dump() == second.model_dump()


class TestPerformanceBudget:
    def test_whole_corpus_scans_quickly(self) -> None:
        """Not a hook-path budget (this is an explicit, user-invoked
        command — see docs/design/QB-061-repo-context-profile.md §8) —
        a generous ceiling just to catch a pathological regression (e.g.
        an accidental O(n^2) scan)."""
        t0 = time.monotonic()
        for case in _CASES:
            build_profile(_FIXTURES_ROOT / case.case_name)
        elapsed = time.monotonic() - t0

        assert elapsed < 5.0, f"fixture corpus took {elapsed:.2f}s, expected < 5s"
