"""Phase 3 unit tests: filter config model, loader, trust, and registry.

Coverage targets:
  - quor/config/model.py       — QuorConfig, FilterConfig, FilterTest
  - quor/filters/loader.py     — load_filter_file
  - quor/filters/trust.py      — is_git_tracked
  - quor/filters/registry.py   — FilterRegistry (find, apply, run_tests, tiering)
"""

from __future__ import annotations

import subprocess
import warnings
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from quor.config.model import FilterConfig, FilterTest, QuorConfig
from quor.errors import ConfigError
from quor.filters.loader import load_filter_file
from quor.filters.registry import FilterRegistry, _build_stage_entry
from quor.filters.trust import is_git_tracked

# ---------------------------------------------------------------------------
# Config model
# ---------------------------------------------------------------------------


class TestConfigModel:
    def test_quor_config_defaults(self) -> None:
        cfg = QuorConfig()
        assert cfg.schema_version == 1
        assert cfg.filter == []

    def test_filter_config_defaults(self) -> None:
        fc = FilterConfig(name="x", match_command=".*")
        assert fc.abort_unless == []
        assert fc.abort_if == []
        assert fc.on_empty == ""
        assert fc.stages == []
        assert fc.tests == []

    def test_filter_test_defaults(self) -> None:
        ft = FilterTest(description="d", input="i")
        assert ft.must_contain == []
        assert ft.must_not_contain == []
        assert ft.compression_target is None

    def test_quor_config_round_trip(self) -> None:
        data = {
            "schema_version": 1,
            "filter": [
                {
                    "name": "demo",
                    "match_command": "^demo",
                    "on_empty": "done",
                    "stages": [{"type": "remove_ansi"}],
                    "tests": [
                        {
                            "description": "t1",
                            "input": "hello",
                            "must_contain": ["hello"],
                        }
                    ],
                }
            ],
        }
        cfg = QuorConfig.model_validate(data)
        assert cfg.filter[0].name == "demo"
        assert cfg.filter[0].on_empty == "done"
        assert cfg.filter[0].tests[0].must_contain == ["hello"]

    def test_filter_config_frozen(self) -> None:
        fc = FilterConfig(name="x", match_command=".*")
        with pytest.raises(ValidationError):
            fc.name = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


class TestLoader:
    def test_load_valid_toml(self, tmp_path: Path) -> None:
        toml = tmp_path / "demo.toml"
        toml.write_text(
            '[filter]\nname = "demo"\nmatch_command = "^demo"\n\n'
            "[[filter.tests]]\ndescription = \"t1\"\ninput = \"hello\"\n",
            encoding="utf-8",
        )
        # TOML needs [[filter]] (array) not [filter] (table) to produce a list
        toml.write_text(
            "schema_version = 1\n\n"
            '[[filter]]\nname = "demo"\nmatch_command = "^demo"\n',
            encoding="utf-8",
        )
        filters = load_filter_file(toml)
        assert len(filters) == 1
        assert filters[0].name == "demo"

    def test_load_invalid_toml_syntax(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.toml"
        bad.write_text("this is not [valid toml", encoding="utf-8")
        with pytest.raises(ConfigError, match="Invalid TOML"):
            load_filter_file(bad)

    def test_load_invalid_schema(self, tmp_path: Path) -> None:
        # schema_version must be an int; string should fail
        bad = tmp_path / "bad_schema.toml"
        bad.write_text('schema_version = "not-an-int"\n', encoding="utf-8")
        with pytest.raises(ConfigError, match="Invalid filter schema"):
            load_filter_file(bad)

    def test_load_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="Cannot read"):
            load_filter_file(tmp_path / "nonexistent.toml")

    def test_load_empty_filter_list(self, tmp_path: Path) -> None:
        toml = tmp_path / "empty.toml"
        toml.write_text("schema_version = 1\n", encoding="utf-8")
        filters = load_filter_file(toml)
        assert filters == []

    def test_load_multiple_filters(self, tmp_path: Path) -> None:
        toml = tmp_path / "multi.toml"
        toml.write_text(
            "schema_version = 1\n\n"
            '[[filter]]\nname = "a"\nmatch_command = "^a"\n\n'
            '[[filter]]\nname = "b"\nmatch_command = "^b"\n',
            encoding="utf-8",
        )
        filters = load_filter_file(toml)
        assert [f.name for f in filters] == ["a", "b"]


# ---------------------------------------------------------------------------
# Trust
# ---------------------------------------------------------------------------


class TestTrust:
    def test_tracked_file_returns_true(self) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            assert is_git_tracked(Path("some/file.toml")) is True

    def test_untracked_file_returns_false(self) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            assert is_git_tracked(Path("some/file.toml")) is False

    def test_git_not_found_returns_false(self) -> None:
        with patch("subprocess.run", side_effect=OSError("git not found")):
            assert is_git_tracked(Path("some/file.toml")) is False

    def test_timeout_returns_false(self) -> None:
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 5)):
            assert is_git_tracked(Path("some/file.toml")) is False


# ---------------------------------------------------------------------------
# Registry — helpers
# ---------------------------------------------------------------------------


class TestBuildStageEntry:
    def test_known_type_remove_ansi(self) -> None:
        entry = _build_stage_entry({"type": "remove_ansi"})
        assert entry.handler.stage_type == "remove_ansi"

    def test_known_type_strip_lines(self) -> None:
        entry = _build_stage_entry({"type": "strip_lines", "patterns": ["^x"]})
        assert entry.handler.stage_type == "strip_lines"

    def test_known_type_max_tokens(self) -> None:
        entry = _build_stage_entry({"type": "max_tokens", "limit": 100, "strategy": "tail"})
        assert entry.handler.stage_type == "max_tokens"

    def test_unknown_type_raises_config_error(self) -> None:
        with pytest.raises(ConfigError, match="Unknown stage type"):
            _build_stage_entry({"type": "does_not_exist"})

    def test_invalid_config_raises_config_error(self) -> None:
        # max_tokens requires limit > 0; limit=-1 should fail
        with pytest.raises(ConfigError, match="Invalid config"):
            _build_stage_entry({"type": "max_tokens", "limit": -1, "strategy": "tail"})


# ---------------------------------------------------------------------------
# Registry — tiering
# ---------------------------------------------------------------------------


def _make_filter_toml(tmp_path: Path, name: str, match: str) -> Path:
    p = tmp_path / f"{name}.toml"
    p.write_text(
        f'schema_version = 1\n\n[[filter]]\nname = "{name}"\nmatch_command = "{match}"\n',
        encoding="utf-8",
    )
    return p


class TestFilterRegistryTiering:
    def test_builtin_filters_loaded(self) -> None:
        registry = FilterRegistry(skip_user=True, skip_project=True)
        # All five built-in filters should load (generic, pytest, git-status, …)
        names = [f.name for _, f in registry.all_filters()]
        assert "generic" in names
        assert "pytest" in names

    def test_find_returns_first_match(self) -> None:
        registry = FilterRegistry(skip_user=True, skip_project=True)
        f = registry.find("pytest tests/")
        assert f is not None
        assert f.name == "pytest"

    def test_find_returns_none_on_no_match(self) -> None:
        # Override _builtin to empty so nothing matches
        registry = FilterRegistry(skip_user=True, skip_project=True)
        registry._builtin = []
        registry._user = []
        registry._project = []
        assert registry.find("some_unknown_cmd") is None

    def test_project_filter_overrides_builtin(self, tmp_path: Path) -> None:
        project_root = tmp_path / "repo"
        (project_root / ".quor" / "filters").mkdir(parents=True)
        # Use a simple pattern without backslash escapes to avoid TOML escape issues
        _make_filter_toml(
            project_root / ".quor" / "filters", "my-pytest", "^pytest"
        )
        # Make trust check pass
        with patch("quor.filters.registry.is_git_tracked", return_value=True):
            registry = FilterRegistry(project_root=project_root, skip_user=True)

        f = registry.find("pytest tests/")
        assert f is not None
        tier = next(tier for tier, fc in registry.all_filters() if fc is f)
        assert tier == "project"

    def test_untrusted_project_filter_skipped(self, tmp_path: Path) -> None:
        project_root = tmp_path / "repo"
        (project_root / ".quor" / "filters").mkdir(parents=True)
        _make_filter_toml(project_root / ".quor" / "filters", "custom", "^custom")

        with (
            patch("quor.filters.registry.is_git_tracked", return_value=False),
            warnings.catch_warnings(record=True) as caught,
        ):
            warnings.simplefilter("always")
            registry = FilterRegistry(project_root=project_root, skip_user=True)

        assert registry._project == []
        assert any("not git-tracked" in str(w.message) for w in caught)

    def test_user_filter_loaded(self, tmp_path: Path) -> None:
        # The autouse fixture already patches platformdirs and creates the config dir.
        # We just need to create the filters/ subdirectory inside it.
        import platformdirs

        user_config = Path(platformdirs.user_config_dir("quor"))
        filters_dir = user_config / "filters"
        filters_dir.mkdir(exist_ok=True)
        _make_filter_toml(filters_dir, "myfilter", "^myfilter")

        registry = FilterRegistry(skip_project=True)
        names = [f.name for _, f in registry.all_filters()]
        assert "myfilter" in names

    def test_invalid_builtin_file_warns_not_raises(self, tmp_path: Path) -> None:
        bad_toml = tmp_path / "broken.toml"
        bad_toml.write_text("not valid toml [[[", encoding="utf-8")
        registry = FilterRegistry.__new__(FilterRegistry)
        registry._builtin = []
        registry._user = []
        registry._project = []
        with (
            patch("quor.filters.registry._BUILTIN_DIR", tmp_path),
            warnings.catch_warnings(record=True) as caught,
        ):
            warnings.simplefilter("always")
            registry._load_builtin()
        assert any("broken.toml" in str(w.message) for w in caught)


# ---------------------------------------------------------------------------
# Registry — apply (abort logic + on_empty)
# ---------------------------------------------------------------------------


def _simple_filter(
    *,
    abort_unless: list[str] | None = None,
    abort_if: list[str] | None = None,
    on_empty: str = "",
    stages: list | None = None,
) -> FilterConfig:
    return FilterConfig(
        name="test",
        match_command=".*",
        abort_unless=abort_unless or [],
        abort_if=abort_if or [],
        on_empty=on_empty,
        stages=stages or [],
    )


class TestFilterApply:
    reg = FilterRegistry(skip_user=True, skip_project=True)

    def test_no_abort_conditions_pipeline_runs(self) -> None:
        fc = _simple_filter(
            stages=[{"type": "strip_lines", "patterns": ["^DROP"]}]
        )
        result = self.reg.apply(fc, "DROP me\nKEEP me")
        assert "DROP" not in result
        assert "KEEP me" in result

    def test_abort_unless_no_trigger_string_returns_original(self) -> None:
        fc = _simple_filter(
            abort_unless=["SPECIAL"],
            stages=[{"type": "strip_lines", "patterns": [".*"]}],
        )
        content = "ordinary line\nanother line"
        result = self.reg.apply(fc, content)
        assert result == content  # pipeline not run

    def test_abort_unless_trigger_string_present_pipeline_runs(self) -> None:
        fc = _simple_filter(
            abort_unless=["SPECIAL"],
            stages=[{"type": "strip_lines", "patterns": ["^DROP"]}],
        )
        result = self.reg.apply(fc, "SPECIAL token\nDROP me")
        assert "SPECIAL" in result
        assert "DROP" not in result

    def test_abort_if_trigger_string_returns_original(self) -> None:
        fc = _simple_filter(
            abort_if=["UNSAFE"],
            stages=[{"type": "strip_lines", "patterns": [".*"]}],
        )
        content = "UNSAFE content here"
        result = self.reg.apply(fc, content)
        assert result == content

    def test_abort_if_no_trigger_pipeline_runs(self) -> None:
        fc = _simple_filter(
            abort_if=["UNSAFE"],
            stages=[{"type": "strip_lines", "patterns": ["^DROP"]}],
        )
        result = self.reg.apply(fc, "safe content\nDROP me")
        assert "DROP" not in result
        assert "safe content" in result

    def test_on_empty_returned_when_output_blank(self) -> None:
        fc = _simple_filter(
            on_empty="(no output)",
            stages=[{"type": "strip_lines", "patterns": [".*"]}],
        )
        result = self.reg.apply(fc, "line1\nline2")
        assert result == "(no output)"

    def test_on_empty_not_returned_when_output_non_blank(self) -> None:
        fc = _simple_filter(
            on_empty="(no output)",
            stages=[{"type": "strip_lines", "patterns": ["^DROP"]}],
        )
        result = self.reg.apply(fc, "KEEP me\nDROP me")
        assert result != "(no output)"
        assert "KEEP me" in result

    def test_unknown_stage_type_warns_and_skips(self) -> None:
        fc = _simple_filter(stages=[{"type": "nonexistent_stage"}])
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = self.reg.apply(fc, "content line")
        assert "content line" in result  # fail-open
        assert any("Skipping" in str(w.message) for w in caught)


class TestLargeInputPerformance:
    """IA-S03 (RELEASE_CRITERIA.md): a 10MB input must not hang the pipeline
    for more than 5 seconds. Verified manually during the 2026-07-08 gate
    walk (0.58s) but had no permanent test guarding it — QB-030 closes that
    gap so a future change to line-by-line stage handling can't silently
    regress this without a test catching it."""

    def test_ten_megabyte_input_completes_within_five_seconds(self) -> None:
        import time

        from quor.filters.registry import FilterRegistry

        line = "some output line with a bit of realistic content here\n"
        target_bytes = 10 * 1024 * 1024
        repeat = -(-target_bytes // len(line))  # ceiling division: meet or exceed target_bytes
        content = line * repeat
        assert len(content) >= target_bytes  # sanity-check the fixture itself

        registry = FilterRegistry(skip_user=True, skip_project=True)
        filter_config = registry.find("some-totally-unknown-tool-xyz")
        assert filter_config is not None  # falls through to the generic filter

        start = time.monotonic()
        registry.apply(filter_config, content, content_type="text")
        elapsed = time.monotonic() - start

        assert elapsed < 5.0, f"10MB input took {elapsed:.2f}s, exceeding the 5s IA-S03 budget"


# ---------------------------------------------------------------------------
# Registry — inline test runner
# ---------------------------------------------------------------------------


class TestRunTests:
    reg = FilterRegistry(skip_user=True, skip_project=True)

    def test_passing_tests_return_empty_list(self) -> None:
        fc = FilterConfig(
            name="demo",
            match_command=".*",
            stages=[{"type": "strip_lines", "patterns": ["^DROP"]}],
            tests=[
                FilterTest(
                    description="drop lines",
                    input="DROP me\nKEEP me",
                    must_contain=["KEEP me"],
                    must_not_contain=["DROP"],
                )
            ],
        )
        failures = self.reg.run_tests(fc)
        assert failures == []

    def test_must_contain_failure(self) -> None:
        fc = FilterConfig(
            name="demo",
            match_command=".*",
            stages=[],
            tests=[
                FilterTest(
                    description="check",
                    input="hello",
                    must_contain=["MISSING"],
                )
            ],
        )
        failures = self.reg.run_tests(fc)
        assert len(failures) == 1
        assert "must_contain" in failures[0]
        assert "MISSING" in failures[0]

    def test_must_not_contain_failure(self) -> None:
        fc = FilterConfig(
            name="demo",
            match_command=".*",
            stages=[],
            tests=[
                FilterTest(
                    description="check",
                    input="hello world",
                    must_not_contain=["hello"],
                )
            ],
        )
        failures = self.reg.run_tests(fc)
        assert len(failures) == 1
        assert "must_not_contain" in failures[0]

    def test_compression_target_failure(self) -> None:
        fc = FilterConfig(
            name="demo",
            match_command=".*",
            stages=[],  # no compression → ratio ≈ 0
            tests=[
                FilterTest(
                    description="check compression",
                    input="x" * 100,
                    compression_target=0.5,
                )
            ],
        )
        failures = self.reg.run_tests(fc)
        assert any("compression_target" in f for f in failures)

    def test_no_tests_returns_empty(self) -> None:
        fc = FilterConfig(name="demo", match_command=".*")
        assert self.reg.run_tests(fc) == []


# ---------------------------------------------------------------------------
# Built-in filter inline tests
# ---------------------------------------------------------------------------


class TestBuiltinFilterTests:
    """Run all inline [[filter.tests]] from every built-in TOML file."""

    registry = FilterRegistry(skip_user=True, skip_project=True)

    @pytest.mark.parametrize(
        "filter_config",
        [fc for _, fc in FilterRegistry(skip_user=True, skip_project=True).all_filters()
         if fc.tests],
        ids=lambda fc: fc.name,
    )
    def test_builtin_filter_inline_tests(self, filter_config: FilterConfig) -> None:
        failures = self.registry.run_tests(filter_config)
        assert failures == [], "\n".join(failures)


# ---------------------------------------------------------------------------
# QB-004 / ADR-031 regression: git-diff's max_tokens is best-effort, not hard
# ---------------------------------------------------------------------------


class TestGitDiffBestEffortBudget:
    """Locks in QB-004's investigated finding using the real built-in
    git-diff filter (git.toml: preserve_patterns for +/-/@@, max_tokens
    limit=600) — not a synthetic stage composition — so a future edit to
    git.toml's stage config would be caught here if it silently changed this
    documented, decided behavior (ADR-031).
    """

    registry = FilterRegistry(skip_user=True, skip_project=True)

    def _large_diff(self, changed_lines: int = 400) -> str:
        header = (
            "diff --git a/big_file.py b/big_file.py\n"
            "index abc1234..def5678 100644\n"
            "--- a/big_file.py\n"
            "+++ b/big_file.py\n"
            "@@ -1,400 +1,400 @@\n"
        )
        # Every changed line matches preserve_patterns ('^\+' or '^-'), so it
        # is marked PROTECT before max_tokens ever runs — mirroring the real
        # `git show` output QB-004 investigated (298/515 PROTECT lines).
        body = "\n".join(f"+changed_line_{i}_with_some_realistic_content_padding" for i in range(changed_lines))
        return header + body

    def test_large_protect_heavy_diff_exceeds_configured_limit(self) -> None:
        filter_config = self.registry.find("git diff")
        assert filter_config is not None
        assert filter_config.name == "git-diff"

        diff = self._large_diff(changed_lines=400)
        rendered = self.registry.apply(filter_config, diff)

        # git-diff's max_tokens limit is 600 (git.toml) — a 400-line, all-"+"
        # diff has far more than 600 tokens of PROTECT content alone, so the
        # rendered output must exceed the limit. This is QB-004's confirmed,
        # correct behavior (ADR-031), not a defect to "fix" later.
        rendered_tokens = len(rendered) // 4
        assert rendered_tokens > 600, (
            "git-diff's preserve_patterns should mark added/removed lines PROTECT, "
            "and max_tokens must never compress them to fit — if this fails, "
            "either preserve_patterns or the best-effort budget semantics regressed"
        )

        # And the added lines themselves must genuinely still be present —
        # confirming the overage is real protected content, not an unrelated bug.
        assert "+changed_line_0_" in rendered
        assert "+changed_line_399_" in rendered

    def test_small_diff_stays_within_limit(self) -> None:
        """Sanity check on the other side: a small diff well under the
        budget is unaffected — the overage above is about volume, not a
        universal property of the filter."""
        filter_config = self.registry.find("git diff")
        assert filter_config is not None

        diff = self._large_diff(changed_lines=3)
        rendered = self.registry.apply(filter_config, diff)
        rendered_tokens = len(rendered) // 4
        assert rendered_tokens <= 600
