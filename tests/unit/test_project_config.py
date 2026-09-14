"""Unit tests for QB-130: project-level `.quor.toml` overrides, plus other
`quor/engine/dispatcher.py::apply_filter_pipeline()` behavior that has
grown alongside it in this same file rather than starting a second one.

Covers quor/config/model.py's ProjectConfig/CompressionOverrides/
IgnoreOverrides, quor/config/loader.py's find_and_load_project_config()/
resolve_effective_config(), the exclude_patterns/min_token_threshold/
ast_pruning_enabled (QB-132) wiring, and — unrelated to any `.quor.toml`
setting — QB-133's extension-based filter-lookup fallback (`_lookup_filter()`'s
`file_path` parameter). Does NOT test `aggressiveness` changing compression
behavior — see TestAggressivenessIsParsedButNotWired below for why that's
the point, not a gap.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from quor.config.loader import find_and_load_project_config, resolve_effective_config
from quor.config.model import CompressionOverrides, IgnoreOverrides, ProjectConfig, QuorUserConfig
from quor.engine.dispatcher import apply_filter_pipeline
from quor.errors import ConfigError

_REPEATED_LINE_TEXT = "identical line\n" * 200
"""Genuinely compressible under the built-in `generic` filter's
deduplicate_consecutive stage — used wherever a test needs to prove a real
filter actually ran, as opposed to a bypass leaving `filter_config` `None`."""


def _write_project_config(directory: Path, content: str) -> Path:
    path = directory / ".quor.toml"
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# find_and_load_project_config — root discovery
# ---------------------------------------------------------------------------


class TestFindAndLoadProjectConfig:
    def test_returns_none_when_nothing_found(self, tmp_path: Path) -> None:
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        assert find_and_load_project_config(nested) is None

    def test_finds_config_in_target_directory_itself(self, tmp_path: Path) -> None:
        _write_project_config(tmp_path, "[compression]\nmin_token_threshold = 100\n")
        config = find_and_load_project_config(tmp_path)
        assert config is not None
        assert config.compression.min_token_threshold == 100

    def test_walks_up_from_a_nested_directory(self, tmp_path: Path) -> None:
        _write_project_config(tmp_path, "[compression]\nmin_token_threshold = 250\n")
        nested = tmp_path / "src" / "pkg"
        nested.mkdir(parents=True)

        config = find_and_load_project_config(nested)

        assert config is not None
        assert config.compression.min_token_threshold == 250

    def test_walks_up_from_a_file_path_starting_at_its_parent(self, tmp_path: Path) -> None:
        _write_project_config(tmp_path, "[compression]\nmin_token_threshold = 42\n")
        nested_file = tmp_path / "src" / "pkg" / "module.py"
        nested_file.parent.mkdir(parents=True)
        nested_file.write_text("x = 1\n", encoding="utf-8")

        config = find_and_load_project_config(nested_file)

        assert config is not None
        assert config.compression.min_token_threshold == 42

    def test_nearest_ancestor_wins_over_a_more_distant_one(self, tmp_path: Path) -> None:
        _write_project_config(tmp_path, "[compression]\nmin_token_threshold = 1\n")
        nested = tmp_path / "sub"
        nested.mkdir()
        _write_project_config(nested, "[compression]\nmin_token_threshold = 2\n")

        config = find_and_load_project_config(nested / "deeper")

        assert config is not None
        assert config.compression.min_token_threshold == 2

    def test_raises_config_error_on_invalid_toml(self, tmp_path: Path) -> None:
        _write_project_config(tmp_path, "[compression\nnot valid toml")

        with pytest.raises(ConfigError, match="Invalid TOML"):
            find_and_load_project_config(tmp_path)

    def test_returns_default_project_config_for_an_empty_file(self, tmp_path: Path) -> None:
        _write_project_config(tmp_path, "")
        config = find_and_load_project_config(tmp_path)
        assert config == ProjectConfig()


# ---------------------------------------------------------------------------
# ProjectConfig / CompressionOverrides / IgnoreOverrides — TOML parsing
# ---------------------------------------------------------------------------


class TestProjectConfigParsing:
    def test_parses_full_compression_and_ignore_sections(self, tmp_path: Path) -> None:
        _write_project_config(
            tmp_path,
            """
            [compression]
            min_token_threshold = 500
            ast_pruning_enabled = false
            aggressiveness = "aggressive"

            [ignore]
            exclude_patterns = ["*.md", "vendor/**"]
            """,
        )

        config = find_and_load_project_config(tmp_path)

        assert config is not None
        assert config.compression.min_token_threshold == 500
        assert config.compression.ast_pruning_enabled is False
        assert config.compression.aggressiveness == "aggressive"
        assert config.ignore.exclude_patterns == ["*.md", "vendor/**"]

    def test_omitted_sections_default_to_all_unset(self, tmp_path: Path) -> None:
        _write_project_config(tmp_path, "")
        config = find_and_load_project_config(tmp_path)

        assert config is not None
        assert config.compression.min_token_threshold is None
        assert config.compression.ast_pruning_enabled is None
        assert config.compression.aggressiveness is None
        assert config.ignore.exclude_patterns == []

    def test_partial_compression_section_leaves_other_fields_unset(self, tmp_path: Path) -> None:
        _write_project_config(tmp_path, "[compression]\nmin_token_threshold = 10\n")
        config = find_and_load_project_config(tmp_path)

        assert config is not None
        assert config.compression.min_token_threshold == 10
        assert config.compression.ast_pruning_enabled is None


# ---------------------------------------------------------------------------
# resolve_effective_config — override precedence, no mutation
# ---------------------------------------------------------------------------


class TestResolveEffectiveConfig:
    def test_none_project_config_returns_global_config_unchanged(self) -> None:
        global_config = QuorUserConfig()
        result = resolve_effective_config(global_config, None)
        assert result is global_config

    def test_project_min_token_threshold_overrides_global(self) -> None:
        global_config = QuorUserConfig(min_token_threshold=0)
        project_config = ProjectConfig(compression=CompressionOverrides(min_token_threshold=999))

        result = resolve_effective_config(global_config, project_config)

        assert result.min_token_threshold == 999

    def test_project_ast_pruning_enabled_overrides_global(self) -> None:
        global_config = QuorUserConfig(ast_pruning_enabled=True)
        project_config = ProjectConfig(compression=CompressionOverrides(ast_pruning_enabled=False))

        result = resolve_effective_config(global_config, project_config)

        assert result.ast_pruning_enabled is False

    def test_project_aggressiveness_overrides_global(self) -> None:
        global_config = QuorUserConfig(aggressiveness="balanced")
        project_config = ProjectConfig(compression=CompressionOverrides(aggressiveness="aggressive"))

        result = resolve_effective_config(global_config, project_config)

        assert result.aggressiveness == "aggressive"

    def test_project_exclude_patterns_overrides_global(self) -> None:
        global_config = QuorUserConfig(exclude_patterns=[])
        project_config = ProjectConfig(ignore=IgnoreOverrides(exclude_patterns=["*.log"]))

        result = resolve_effective_config(global_config, project_config)

        assert result.exclude_patterns == ["*.log"]

    def test_unset_project_fields_fall_through_to_global_values(self) -> None:
        global_config = QuorUserConfig(
            min_token_threshold=50,
            ast_pruning_enabled=False,
            aggressiveness="light",
            exclude_patterns=["*.lock"],
        )
        # A project config that only sets one field...
        project_config = ProjectConfig(compression=CompressionOverrides(min_token_threshold=500))

        result = resolve_effective_config(global_config, project_config)

        # ...overrides only that field...
        assert result.min_token_threshold == 500
        # ...and every other global value survives untouched.
        assert result.ast_pruning_enabled is False
        assert result.aggressiveness == "light"
        assert result.exclude_patterns == ["*.lock"]

    def test_does_not_mutate_the_global_config_instance(self) -> None:
        global_config = QuorUserConfig(min_token_threshold=0)
        project_config = ProjectConfig(compression=CompressionOverrides(min_token_threshold=999))

        resolve_effective_config(global_config, project_config)

        # The frozen input instance must be untouched — the merge returns a
        # *new* object, never mutates a runtime singleton in place.
        assert global_config.min_token_threshold == 0

    def test_returns_a_new_instance_when_overrides_apply(self) -> None:
        global_config = QuorUserConfig(min_token_threshold=0)
        project_config = ProjectConfig(compression=CompressionOverrides(min_token_threshold=999))

        result = resolve_effective_config(global_config, project_config)

        assert result is not global_config

    def test_project_config_with_no_overrides_set_returns_global_unchanged(self) -> None:
        global_config = QuorUserConfig()
        project_config = ProjectConfig()  # every field unset/default

        result = resolve_effective_config(global_config, project_config)

        assert result is global_config


# ---------------------------------------------------------------------------
# apply_filter_pipeline — exclude_patterns glob matching (QB-130 wiring)
# ---------------------------------------------------------------------------


class TestExcludePatternsWiring:
    def test_matching_extension_pattern_bypasses_compression(self, tmp_path: Path) -> None:
        file_path = tmp_path / "notes.md"

        output, filter_config = apply_filter_pipeline(
            _REPEATED_LINE_TEXT,
            _REPEATED_LINE_TEXT,
            file_path=file_path,
            exclude_patterns=["*.md"],
        )

        assert filter_config is None
        assert output == _REPEATED_LINE_TEXT

    def test_matching_path_segment_pattern_bypasses_compression(self, tmp_path: Path) -> None:
        file_path = tmp_path / "tests" / "fixtures" / "sample.py"

        output, filter_config = apply_filter_pipeline(
            _REPEATED_LINE_TEXT,
            _REPEATED_LINE_TEXT,
            file_path=file_path,
            exclude_patterns=["tests/**"],
        )

        assert filter_config is None
        assert output == _REPEATED_LINE_TEXT

    def test_non_matching_pattern_does_not_bypass(self, tmp_path: Path) -> None:
        file_path = tmp_path / "notes.md"

        _output, filter_config = apply_filter_pipeline(
            _REPEATED_LINE_TEXT,
            _REPEATED_LINE_TEXT,
            file_path=file_path,
            exclude_patterns=["*.json"],
        )

        assert filter_config is not None

    def test_no_file_path_means_exclude_patterns_never_match(self) -> None:
        """No file identity (e.g. MCP's raw_text path) — a pattern that
        would match a *filename* has nothing to match against, and must
        not accidentally bypass compression for unrelated content."""
        _output, filter_config = apply_filter_pipeline(
            _REPEATED_LINE_TEXT,
            _REPEATED_LINE_TEXT,
            exclude_patterns=["*.md"],
        )

        assert filter_config is not None

    def test_empty_exclude_patterns_never_bypasses(self, tmp_path: Path) -> None:
        file_path = tmp_path / "notes.md"

        _output, filter_config = apply_filter_pipeline(
            _REPEATED_LINE_TEXT, _REPEATED_LINE_TEXT, file_path=file_path, exclude_patterns=[]
        )

        assert filter_config is not None


# ---------------------------------------------------------------------------
# apply_filter_pipeline — min_token_threshold (QB-130 wiring)
# ---------------------------------------------------------------------------


class TestMinTokenThresholdWiring:
    def test_content_below_threshold_bypasses_compression(self) -> None:
        short_text = "short\n"

        output, filter_config = apply_filter_pipeline(
            short_text, short_text, min_token_threshold=1_000_000
        )

        assert filter_config is None
        assert output == short_text

    def test_content_at_or_above_threshold_is_processed_normally(self) -> None:
        _output, filter_config = apply_filter_pipeline(
            _REPEATED_LINE_TEXT, _REPEATED_LINE_TEXT, min_token_threshold=1
        )

        assert filter_config is not None

    def test_zero_threshold_means_no_threshold(self) -> None:
        """min_token_threshold=0 (the global default) must behave exactly
        like omitting the argument entirely — 0 means "unset," not "block
        everything" — contrasted against a threshold high enough to
        actually bypass the same content, proving the gate is threshold-
        sensitive rather than always/never firing."""
        short_text = "x\n"

        default_output, default_filter = apply_filter_pipeline(short_text, short_text)
        zero_output, zero_filter = apply_filter_pipeline(short_text, short_text, min_token_threshold=0)
        blocked_output, blocked_filter = apply_filter_pipeline(
            short_text, short_text, min_token_threshold=1_000
        )

        assert zero_output == default_output
        assert (zero_filter is None) == (default_filter is None)
        assert blocked_filter is None
        assert blocked_output == short_text


# ---------------------------------------------------------------------------
# apply_filter_pipeline — ast_pruning_enabled (QB-132 wiring)
# ---------------------------------------------------------------------------


class TestAstPruningEnabledWiring:
    """Unlike min_token_threshold/exclude_patterns (which bypass the whole
    filter), ast_pruning_enabled=False only disables that filter's own
    python_ast_summarize/code_ast_summarize stage — the filter still runs,
    and still matches, so `filter_config` is never None here."""

    _PY_SOURCE = 'def foo(x, y):\n    """Add two numbers."""\n    total = x + y\n    return total\n'

    def test_disabled_preserves_function_body(self) -> None:
        output, filter_config = apply_filter_pipeline(
            "cat script.py", self._PY_SOURCE, ast_pruning_enabled=False
        )

        assert filter_config is not None and filter_config.name == "cat-python"
        assert "total = x + y" in output

    def test_default_true_strips_function_body(self) -> None:
        """Regression guard: omitting the argument reproduces today's
        existing AST-body-stripping behavior unchanged."""
        output, filter_config = apply_filter_pipeline("cat script.py", self._PY_SOURCE)

        assert filter_config is not None and filter_config.name == "cat-python"
        assert "total = x + y" not in output

    def test_explicit_true_matches_default(self) -> None:
        default_output, _ = apply_filter_pipeline("cat script.py", self._PY_SOURCE)
        explicit_output, _ = apply_filter_pipeline(
            "cat script.py", self._PY_SOURCE, ast_pruning_enabled=True
        )

        assert explicit_output == default_output

    def test_run_dispatch_honors_the_global_setting(self) -> None:
        """QB-132: unlike min_token_threshold/exclude_patterns (which only
        ever reach apply_filter_pipeline() — no test claims run_dispatch()
        honors those either), the *global* QuorUserConfig.ast_pruning_enabled
        does reach the real Bash CLI dispatch path (run_dispatch() has no
        project-config resolution of its own, see apply_filter_pipeline()'s
        own call site in dispatcher.py for why only the global setting, not
        a `.quor.toml` override, applies here)."""
        import io
        import subprocess
        from unittest.mock import MagicMock, patch

        from quor.config.model import QuorUserConfig
        from quor.engine.dispatcher import run_dispatch

        proc = MagicMock(spec=subprocess.CompletedProcess)
        proc.stdout = self._PY_SOURCE
        proc.returncode = 0

        captured = io.StringIO()
        with (
            patch("subprocess.run", return_value=proc),
            patch("sys.stdout", captured),
            patch(
                "quor.engine.dispatcher.load_user_config",
                return_value=QuorUserConfig(ast_pruning_enabled=False),
            ),
        ):
            exit_code = run_dispatch(["cat", "script.py"], tracking=None)

        assert exit_code == 0
        assert "total = x + y" in captured.getvalue()


# ---------------------------------------------------------------------------
# apply_filter_pipeline / _lookup_filter — extension-based routing fallback
# (QB-133)
# ---------------------------------------------------------------------------


class TestExtensionBasedFilterLookup:
    """`_lookup_filter()`'s `file_path` parameter — a synthesized
    `cat <path>` command tried before `match_str` itself, for the one class
    of caller `match_str` alone can never route correctly: a real file's
    *content* passed as `match_str` (MCP's `compress_context(focal_file=...)`,
    `quor benchmark`), which never looks like a `cat <path>` command and has
    no detectable `match_content_types` shape for most extension-specific
    filters either (source code, YAML, `.env`/`.ini` — unlike JSON/TOML/diff)
    — see backlog.md's QB-133 entry for the empirical repro this fixes.
    Generalized beyond any fixed extension list (no `EXTENSION_TO_LANGUAGE`-
    style table here): the synthesized command's result is accepted whenever
    it names something more specific than the two universal catch-alls
    (`cat`, `generic`) — see `_TOO_GENERIC_FOR_FILE_PATH_ROUTING`'s own
    comment. Unrelated to any `.quor.toml`/`QuorUserConfig` setting — this
    fires unconditionally whenever `file_path` is given, matching the fact
    that every real caller that passes `file_path` also passes that same
    file's content as `match_str` (there is no real call site where the two
    point at different files)."""

    _TS_SOURCE = (
        "function add(x: number, y: number): number {\n  const total = x + y;\n  return total;\n}\n"
    )
    _PY_SOURCE = 'def foo(x, y):\n    """Add two numbers."""\n    total = x + y\n    return total\n'

    def test_ts_content_routes_to_cat_typescript_when_file_path_given(
        self, tmp_path: Path
    ) -> None:
        pytest.importorskip("tree_sitter_typescript")
        file_path = tmp_path / "sample.ts"

        output, filter_config = apply_filter_pipeline(
            self._TS_SOURCE, self._TS_SOURCE, file_path=file_path
        )

        assert filter_config is not None and filter_config.name == "cat-typescript"
        assert "const total = x + y;" not in output  # AST-compressed, not the generic passthrough

    def test_py_content_routes_to_cat_python_when_file_path_given(self, tmp_path: Path) -> None:
        file_path = tmp_path / "sample.py"

        output, filter_config = apply_filter_pipeline(
            self._PY_SOURCE, self._PY_SOURCE, file_path=file_path
        )

        assert filter_config is not None and filter_config.name == "cat-python"
        assert "total = x + y" not in output

    def test_yaml_content_routes_to_cat_yaml_when_file_path_given(self, tmp_path: Path) -> None:
        """YAML has no `match_content_types` entry (`ContentType` has no
        "yaml" member at all) — unlike JSON/TOML, it was never covered by
        QB-109's content-type fix, and relies entirely on this path-based
        one."""
        yaml_text = "name: sample\nitems:\n" + "".join(f"  - item{i}\n" for i in range(20))
        file_path = tmp_path / "sample.yaml"

        _output, filter_config = apply_filter_pipeline(yaml_text, yaml_text, file_path=file_path)

        assert filter_config is not None and filter_config.name == "cat-yaml"

    def test_dotenv_content_routes_to_dotenv_filter_when_file_path_given(
        self, tmp_path: Path
    ) -> None:
        """`cat-dotenv.toml` also declares a bare-filename `match_command`
        branch (`^\\S*\\.env$`) that looks like it was meant for a caller
        passing a plain path as `match_str` — but no real caller does that
        today (every real caller passes either a shell command or the
        file's content), so that branch was just as unreachable as the
        `cat-<language>` filters were before this fix. The synthesized
        `cat <path>` command reaches it through the *other* half of its
        pattern instead."""
        env_text = "API_KEY=abc123\nDEBUG=true\nPORT=8080\n"
        file_path = tmp_path / "sample.env"

        _output, filter_config = apply_filter_pipeline(env_text, env_text, file_path=file_path)

        assert filter_config is not None and filter_config.name == "dotenv"

    def test_ini_content_routes_to_ini_filter_when_file_path_given(self, tmp_path: Path) -> None:
        ini_text = "[section]\nkey=value\nother=thing\n"
        file_path = tmp_path / "sample.ini"

        _output, filter_config = apply_filter_pipeline(ini_text, ini_text, file_path=file_path)

        assert filter_config is not None and filter_config.name == "ini"

    def test_lockfile_basename_routes_to_cat_toml_when_file_path_given(
        self, tmp_path: Path
    ) -> None:
        """`cat-toml.toml`'s `match_command` also matches the literal
        basenames `poetry.lock`/`Cargo.lock`, not just a `.toml` extension —
        this fix has to work for a basename match, not only a suffix match,
        since it reuses `match_command` as-is rather than deriving from
        `file_path.suffix`."""
        lock_text = 'name = "example"\nversion = "1.0.0"\n'
        file_path = tmp_path / "poetry.lock"

        _output, filter_config = apply_filter_pipeline(lock_text, lock_text, file_path=file_path)

        assert filter_config is not None and filter_config.name == "cat-toml"

    def test_same_content_without_file_path_falls_through_to_generic(self) -> None:
        """The bug this fixes, pinned as a regression guard: with no
        `file_path` at all (MCP's plain `raw_text` path has no file
        identity), the exact same TypeScript-shaped content has no way to
        be routed to `cat-typescript` — it falls through to the generic
        catch-all, exactly as it did before this fix. Expected, not a
        remaining gap; see `_lookup_filter()`'s own docstring."""
        _output, filter_config = apply_filter_pipeline(self._TS_SOURCE, self._TS_SOURCE)

        assert filter_config is not None and filter_config.name == "generic"

    def test_json_extension_is_unaffected(self, tmp_path: Path) -> None:
        """A `.json` file already routes correctly via `match_content_types`
        (QB-109) — this fix must not interfere with, or duplicate, that
        existing mechanism."""
        json_text = '{"a": 1, "b": null}'
        file_path = tmp_path / "sample.json"

        _output, filter_config = apply_filter_pipeline(json_text, json_text, file_path=file_path)

        assert filter_config is not None and filter_config.name == "cat-json"

    def test_extension_with_only_a_too_generic_match_falls_back_to_match_str(
        self, tmp_path: Path
    ) -> None:
        """`.pyi` has no filter of its own — `cat-python.toml`'s
        `match_command` pattern (`\\.py\\b`) doesn't match it (no word
        boundary between "y" and "i" — confirmed by direct regex test, not
        assumed) — and the synthesized `cat <path>` command only ever
        matches the extension-agnostic `cat.toml` catch-all
        (`_TOO_GENERIC_FOR_FILE_PATH_ROUTING` excludes it by design — see
        that constant's own comment for why silently promoting content into
        `cat` instead of `generic` would be an unrequested behavior change).
        So this must fall back to match_str-based matching exactly as if no
        `file_path` had been given at all."""
        file_path = tmp_path / "sample.pyi"

        without_path_output, without_path_filter = apply_filter_pipeline(
            self._PY_SOURCE, self._PY_SOURCE
        )
        with_path_output, with_path_filter = apply_filter_pipeline(
            self._PY_SOURCE, self._PY_SOURCE, file_path=file_path
        )

        assert with_path_filter is not None and with_path_filter.name == "generic"
        assert with_path_filter.name == (without_path_filter and without_path_filter.name)
        assert with_path_output == without_path_output

    def test_route_by_extension_false_opts_out(self, tmp_path: Path) -> None:
        """`mcp/server.py`'s `_compress_context_tiered()` needs this: its
        `payload` is a multi-file synthesized rendering, not `file_path`'s
        own raw content, so path-based routing must be disableable without
        also losing `exclude_patterns`' use of the same `file_path`."""
        file_path = tmp_path / "sample.ts"

        _output, filter_config = apply_filter_pipeline(
            self._TS_SOURCE, self._TS_SOURCE, file_path=file_path, route_by_extension=False
        )

        assert filter_config is not None and filter_config.name == "generic"


# ---------------------------------------------------------------------------
# aggressiveness — parsed and merged, deliberately NOT wired into behavior
# ---------------------------------------------------------------------------


class TestAggressivenessIsParsedButNotWired:
    """QB-130 (per product decision): aggressiveness round-trips through
    ProjectConfig/QuorUserConfig/resolve_effective_config exactly like any
    other override, but apply_filter_pipeline() has no parameter for it at
    all and PROTECT-marked content's output must be identical regardless
    of its value — see quor/config/model.py's CompressionOverrides
    docstring for the QB-039/ADR-031 reasoning."""

    def test_apply_filter_pipeline_has_no_aggressiveness_parameter(self) -> None:
        import inspect

        params = inspect.signature(apply_filter_pipeline).parameters
        assert "aggressiveness" not in params

    @pytest.mark.parametrize("aggressiveness", ["light", "balanced", "aggressive", "anything-at-all"])
    def test_resolves_and_merges_correctly_despite_going_nowhere(self, aggressiveness: str) -> None:
        """`resolve_effective_config()` still does real work for this field
        (parses, validates, merges) even though nothing downstream ever
        reads the result — proving the schema/merge layer is fully
        functional independent of the (deliberate) absence of pipeline
        wiring."""
        global_config = QuorUserConfig(aggressiveness="balanced")
        project_config = ProjectConfig(compression=CompressionOverrides(aggressiveness=aggressiveness))

        effective = resolve_effective_config(global_config, project_config)

        assert effective.aggressiveness == aggressiveness

    def test_protect_marked_content_is_unaffected_by_aggressiveness_either_way(self) -> None:
        """The concrete correctness claim behind "un-wired": PROTECT-marked
        content (git-diff's preserve_patterns is the real, shipped example
        ADR-031 protects) renders identically whether or not a project's
        `.quor.toml` sets an aggressiveness value — because nothing in the
        call path below is capable of reading it."""
        diff_text = (
            "diff --git a/foo.py b/foo.py\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -1,3 +1,3 @@\n"
            "-old line\n"
            "+new line\n"
            " context line\n"
        )

        output_a, filter_a = apply_filter_pipeline("git diff", diff_text)
        output_b, filter_b = apply_filter_pipeline("git diff", diff_text)

        assert output_a == output_b
        assert (filter_a.name if filter_a else None) == (filter_b.name if filter_b else None)

    def test_invalid_aggressiveness_string_is_accepted_without_error(self) -> None:
        """No enum/Literal validation (matches QuorUserConfig.mode's own
        existing precedent) — an un-wired field has no behavior to be
        wrong about yet, so it must not become a hard config-load failure."""
        project_config = ProjectConfig(
            compression=CompressionOverrides(aggressiveness="not-a-real-level")
        )
        assert project_config.compression.aggressiveness == "not-a-real-level"
