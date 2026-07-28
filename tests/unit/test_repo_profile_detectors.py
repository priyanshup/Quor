"""Unit tests for quor/pipeline/repo_profile/detectors/ (model, loader, registry)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from quor.errors import ConfigError
from quor.pipeline.repo_profile.detectors.loader import load_detector_file
from quor.pipeline.repo_profile.detectors.model import DetectorRule
from quor.pipeline.repo_profile.detectors.registry import DetectorRegistry


class TestDetectorRuleModel:
    def test_requires_at_least_one_file_matcher(self) -> None:
        with pytest.raises(ValidationError, match="match_basename/match_path_regex"):
            DetectorRule(
                name="bad",
                category="framework",
                match_content=["foo"],
                evidence="should never construct",
            )

    def test_match_basename_alone_is_valid(self) -> None:
        rule = DetectorRule(
            name="docker",
            category="containerization",
            match_basename=["Dockerfile"],
            evidence="Dockerfile present",
        )
        assert rule.match_basename == ["Dockerfile"]

    def test_unknown_category_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DetectorRule(
                name="bad",
                category="not_a_real_category",  # type: ignore[arg-type]
                match_basename=["x"],
                evidence="x",
            )

    def test_model_is_frozen(self) -> None:
        rule = DetectorRule(
            name="docker",
            category="containerization",
            match_basename=["Dockerfile"],
            evidence="Dockerfile present",
        )
        with pytest.raises(ValidationError):
            rule.name = "changed"  # type: ignore[misc]


class TestLoadDetectorFile:
    def test_loads_valid_file(self, tmp_path: Path) -> None:
        toml_path = tmp_path / "rules.toml"
        toml_path.write_text(
            """
schema_version = 1

[[detector]]
name = "docker"
category = "containerization"
match_basename = ["Dockerfile"]
evidence = "Dockerfile present"
""",
            encoding="utf-8",
        )

        rules = load_detector_file(toml_path)

        assert len(rules) == 1
        assert rules[0].name == "docker"

    def test_invalid_toml_raises_config_error(self, tmp_path: Path) -> None:
        toml_path = tmp_path / "bad.toml"
        toml_path.write_text("this is not [ valid toml", encoding="utf-8")

        with pytest.raises(ConfigError):
            load_detector_file(toml_path)

    def test_schema_violation_raises_config_error(self, tmp_path: Path) -> None:
        toml_path = tmp_path / "bad_schema.toml"
        toml_path.write_text(
            """
[[detector]]
name = "bad"
category = "framework"
evidence = "no file matcher at all"
""",
            encoding="utf-8",
        )

        with pytest.raises(ConfigError):
            load_detector_file(toml_path)

    def test_unreadable_file_raises_config_error(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist.toml"
        with pytest.raises(ConfigError):
            load_detector_file(missing)


def _write_rule_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestDetectorRegistryLoading:
    def test_loads_builtin_rules_by_default(self, tmp_path: Path) -> None:
        registry = DetectorRegistry(project_root=tmp_path, skip_user=True)
        tiers = {tier for tier, _rule in registry.all_rules()}
        assert "builtin" in tiers
        assert len(registry.all_rules()) > 0

    def test_project_rules_require_git_tracking(self, tmp_path: Path) -> None:
        """Mirrors `test_filters.py`'s own convention for this exact
        scenario: `is_git_tracked` is mocked directly rather than driven
        through real git subprocess calls, since the real check's
        behavior is already covered by `quor/filters/trust.py`'s own tests
        — this only needs to prove the registry *honors* the trust check.
        """
        _write_rule_file(
            tmp_path / ".quor" / "detectors" / "custom.toml",
            """
[[detector]]
name = "custom-tool"
category = "framework"
match_basename = ["CUSTOM_MARKER"]
evidence = "custom marker present"
""",
        )

        with patch(
            "quor.pipeline.repo_profile.detectors.registry.is_git_tracked",
            return_value=False,
        ):
            registry = DetectorRegistry(project_root=tmp_path, skip_user=True)
            names = {rule.name for _tier, rule in registry.all_rules()}
            assert "custom-tool" not in names

        with patch(
            "quor.pipeline.repo_profile.detectors.registry.is_git_tracked",
            return_value=True,
        ):
            registry2 = DetectorRegistry(project_root=tmp_path, skip_user=True)
            names2 = {rule.name for _tier, rule in registry2.all_rules()}
            assert "custom-tool" in names2


class TestDetectorRegistryMatching:
    def _registry_with_rules(self, rules_toml: str, tmp_path: Path) -> DetectorRegistry:
        registry = DetectorRegistry(project_root=None, skip_user=True, skip_project=True)
        # Directly inject test rules into the builtin list to test matching
        # logic in isolation from the real, large builtin rule set.
        registry._builtin = load_detector_file(_write_and_return(tmp_path, rules_toml))
        return registry

    def test_presence_only_rule_fires_on_basename_match(self, tmp_path: Path) -> None:
        registry = self._registry_with_rules(
            """
[[detector]]
name = "docker"
category = "containerization"
match_basename = ["Dockerfile"]
evidence = "Dockerfile present"
""",
            tmp_path,
        )
        (tmp_path / "Dockerfile").write_text("FROM python:3.11\n", encoding="utf-8")

        detected = registry.detect(["Dockerfile", "app.py"], tmp_path)

        assert "containerization" in detected
        assert detected["containerization"][0].name == "docker"
        assert "Dockerfile" in detected["containerization"][0].evidence[0]

    def test_no_matching_file_produces_no_detection(self, tmp_path: Path) -> None:
        registry = self._registry_with_rules(
            """
[[detector]]
name = "docker"
category = "containerization"
match_basename = ["Dockerfile"]
evidence = "Dockerfile present"
""",
            tmp_path,
        )

        detected = registry.detect(["app.py", "README.md"], tmp_path)

        assert detected == {}

    def test_content_rule_requires_pattern_match(self, tmp_path: Path) -> None:
        registry = self._registry_with_rules(
            """
[[detector]]
name = "flask"
category = "framework"
match_basename = ["requirements.txt"]
match_content = ['(?mi)^flask\\b']
evidence = "flask dependency declared"
""",
            tmp_path,
        )
        (tmp_path / "requirements.txt").write_text("django==5.0\n", encoding="utf-8")

        detected = registry.detect(["requirements.txt"], tmp_path)
        assert detected == {}

        (tmp_path / "requirements.txt").write_text("flask==3.0\n", encoding="utf-8")
        detected2 = registry.detect(["requirements.txt"], tmp_path)
        assert detected2["framework"][0].name == "flask"

    def test_path_regex_matcher(self, tmp_path: Path) -> None:
        registry = self._registry_with_rules(
            """
[[detector]]
name = "github-actions"
category = "ci_system"
match_path_regex = ['^\\.github/workflows/.*\\.ya?ml$']
evidence = "workflow file present"
""",
            tmp_path,
        )

        detected = registry.detect([".github/workflows/ci.yml", "app.py"], tmp_path)

        assert detected["ci_system"][0].name == "github-actions"

    def test_project_tier_overrides_builtin_for_same_key(self, tmp_path: Path) -> None:
        registry = DetectorRegistry(project_root=None, skip_user=True, skip_project=True)
        registry._project = load_detector_file(
            _write_and_return(
                tmp_path / "project.toml",
                """
[[detector]]
name = "docker"
category = "containerization"
match_basename = ["Dockerfile"]
evidence = "PROJECT OVERRIDE"
""",
            )
        )
        registry._builtin = load_detector_file(
            _write_and_return(
                tmp_path / "builtin.toml",
                """
[[detector]]
name = "docker"
category = "containerization"
match_basename = ["Dockerfile"]
evidence = "builtin default"
""",
            )
        )

        detected = registry.detect(["Dockerfile"], tmp_path)

        assert "PROJECT OVERRIDE" in detected["containerization"][0].evidence[0]
        assert len(detected["containerization"]) == 1

    def test_detected_items_sorted_by_name_within_category(self, tmp_path: Path) -> None:
        registry = self._registry_with_rules(
            """
[[detector]]
name = "zeta-tool"
category = "framework"
match_basename = ["zeta.txt"]
evidence = "zeta present"

[[detector]]
name = "alpha-tool"
category = "framework"
match_basename = ["alpha.txt"]
evidence = "alpha present"
""",
            tmp_path,
        )

        detected = registry.detect(["zeta.txt", "alpha.txt"], tmp_path)

        names = [item.name for item in detected["framework"]]
        assert names == ["alpha-tool", "zeta-tool"]

    def test_unreadable_content_file_fails_open(self, tmp_path: Path) -> None:
        registry = self._registry_with_rules(
            """
[[detector]]
name = "flask"
category = "framework"
match_basename = ["requirements.txt"]
match_content = ['flask']
evidence = "flask dependency declared"
""",
            tmp_path,
        )
        # "requirements.txt" is in the files list but doesn't actually exist
        # on disk — must not raise.
        detected = registry.detect(["requirements.txt"], tmp_path)
        assert detected == {}


def _write_and_return(path_or_dir: Path, content: str) -> Path:
    path = path_or_dir if path_or_dir.suffix == ".toml" else path_or_dir / "rules.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
