"""Unit tests for quor/adapters/hook_manifest.py — the declarative hook manifest
`quor init --claude` and `quor doctor` both iterate (QB-037)."""

from __future__ import annotations

from quor.adapters.hook_manifest import (
    BASH_HOOK_SPEC,
    HOOK_SPECS,
    READ_HOOK_SPEC,
    render_hook_script,
)


class TestHookSpecs:
    def test_bash_and_read_both_present(self) -> None:
        assert BASH_HOOK_SPEC in HOOK_SPECS
        assert READ_HOOK_SPEC in HOOK_SPECS
        assert len(HOOK_SPECS) == 2

    def test_hook_ids_unique(self) -> None:
        ids = [spec.hook_id for spec in HOOK_SPECS]
        assert len(ids) == len(set(ids))

    def test_script_names_unique(self) -> None:
        names = [spec.script_name for spec in HOOK_SPECS]
        assert len(names) == len(set(names))

    def test_bash_spec_targets_pre_tool_use(self) -> None:
        assert BASH_HOOK_SPEC.event == "PreToolUse"
        assert BASH_HOOK_SPEC.matcher == "Bash"
        assert BASH_HOOK_SPEC.script_name == "claude-hook.ps1"

    def test_read_spec_targets_post_tool_use(self) -> None:
        assert READ_HOOK_SPEC.event == "PostToolUse"
        assert READ_HOOK_SPEC.matcher == "Read"
        assert READ_HOOK_SPEC.script_name == "claude-hook-read.ps1"

    def test_schema_version_is_independent_of_package_version(self) -> None:
        """QB-037 correction: schema_version identifies this hook's own
        definition, not the installed Quor package — must not be sourced
        from quor.__version__ anywhere in the spec."""
        from quor import __version__

        assert BASH_HOOK_SPEC.schema_version != __version__
        assert isinstance(BASH_HOOK_SPEC.schema_version, int)
        assert isinstance(READ_HOOK_SPEC.schema_version, int)


class TestRenderHookScript:
    def test_embeds_python_executable(self) -> None:
        rendered = render_hook_script(BASH_HOOK_SPEC, python=r"C:\Python\python.exe")
        assert r"C:\Python\python.exe" in rendered

    def test_embeds_own_schema_version_not_package_version(self) -> None:
        rendered = render_hook_script(BASH_HOOK_SPEC, python="python")
        assert f"# quor-hook-schema: {BASH_HOOK_SPEC.schema_version}" in rendered

    def test_read_spec_renders_its_own_template(self) -> None:
        rendered = render_hook_script(READ_HOOK_SPEC, python="python")
        assert "hook claude-read" in rendered
        assert f"# quor-hook-schema: {READ_HOOK_SPEC.schema_version}" in rendered

    def test_bumping_package_version_does_not_change_rendered_schema_line(self) -> None:
        """The whole point of decoupling: rendering must depend only on
        spec.schema_version, never on whatever quor.__version__ happens to
        be at render time — proven by bumping the package version and
        confirming the rendered script is byte-for-byte unchanged."""
        from unittest.mock import patch

        rendered_before = render_hook_script(BASH_HOOK_SPEC, python="python")
        with patch("quor.__version__", "999.999.999"):
            rendered_during = render_hook_script(BASH_HOOK_SPEC, python="python")
        assert rendered_before == rendered_during
        assert "999.999.999" not in rendered_during
