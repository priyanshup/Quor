"""Unit tests for quor/adapters/hook_manifest.py — the declarative hook manifest
`quor init --claude` and `quor doctor` both iterate (QB-037)."""

from __future__ import annotations

import os

import pytest

from quor.adapters.hook_manifest import (
    BASH_HOOK_SPEC,
    HOOK_SPECS,
    POSIX_SHELL,
    READ_HOOK_SPEC,
    is_windows,
    render_hook_script,
)


@pytest.fixture(autouse=True)
def _pin_windows_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """QB-082: every test in this module predates cross-platform hook
    launcher support and was written assuming Windows (.ps1 script names/
    templates). Pin `os.name` to `"nt"` so they keep passing identically
    regardless of what OS actually runs pytest; `TestPosixPlatformResolution`
    below explicitly overrides this per-test via its own monkeypatch call,
    which `pytest.MonkeyPatch` restores to this module-wide default at that
    test's teardown, not at suite-level."""
    monkeypatch.setattr(os, "name", "nt")


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


class TestIsWindowsHelper:
    def test_true_when_os_name_nt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os, "name", "nt")
        assert is_windows() is True

    def test_false_when_os_name_posix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os, "name", "posix")
        assert is_windows() is False


class TestPosixShellResolution:
    """POSIX_SHELL is resolved once, at module import time, from
    `shutil.which("sh")` falling back to `/bin/sh` — these tests document
    that contract by re-deriving it independently rather than re-importing
    the module (which would not observe a monkeypatched `shutil.which`
    anyway, since resolution already happened at the original import)."""

    def test_resolves_to_a_non_empty_string(self) -> None:
        assert isinstance(POSIX_SHELL, str)
        assert POSIX_SHELL

    def test_matches_shutil_which_or_bin_sh_fallback(self) -> None:
        import shutil

        assert (shutil.which("sh") or "/bin/sh") == POSIX_SHELL


class TestPosixPlatformResolution:
    """QB-082: script_name/template resolve to the POSIX (.sh) family when
    is_windows() is False — the module-wide autouse fixture above pins
    Windows, so every test here explicitly monkeypatches os.name back to
    "posix" itself."""

    def test_script_name_resolves_to_sh_on_posix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os, "name", "posix")
        assert BASH_HOOK_SPEC.script_name == "claude-hook.sh"
        assert READ_HOOK_SPEC.script_name == "claude-hook-read.sh"

    def test_script_name_resolves_to_ps1_on_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Explicit positive Windows case, not just relying on the
        module-wide autouse fixture — documents the branch directly."""
        monkeypatch.setattr(os, "name", "nt")
        assert BASH_HOOK_SPEC.script_name == "claude-hook.ps1"
        assert READ_HOOK_SPEC.script_name == "claude-hook-read.ps1"

    def test_template_resolves_to_sh_template_on_posix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os, "name", "posix")
        assert "#!/bin/sh" in BASH_HOOK_SPEC.template
        assert "$ErrorActionPreference" not in BASH_HOOK_SPEC.template

    def test_render_hook_script_posix_uses_exec_and_python_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(os, "name", "posix")
        rendered = render_hook_script(BASH_HOOK_SPEC, python="/usr/bin/python3")
        assert "/usr/bin/python3" in rendered
        assert "exec" in rendered
        assert "#!/bin/sh" in rendered
        assert "$ErrorActionPreference" not in rendered

    def test_read_spec_posix_template_invokes_claude_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os, "name", "posix")
        rendered = render_hook_script(READ_HOOK_SPEC, python="/usr/bin/python3")
        assert "hook claude-read" in rendered
        assert f"# quor-hook-schema: {READ_HOOK_SPEC.schema_version}" in rendered
