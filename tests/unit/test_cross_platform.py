"""Cross-platform audit: parametrized `sys.platform`/`platform.system()`
coverage for the three places Quor's OS-detection actually branches —
`quor.mcp.launcher.expected_venv_python()`, `quor.cli.commands.init`'s
`.mcp.json` scaffolding, and `quor.cli.commands.doctor`'s `.mcp.json`/
`claude_desktop_config.json` diagnostics.

Each collaborator already has its own targeted unit coverage next to its
code (test_mcp_launcher.py, test_init.py, test_doctor.py) — this file
isn't a replacement for those. It exists as the single place that runs
the *same* win32/darwin/linux parametrization across all three modules
together, so a regression that only shows up in how they interact (e.g.
`init` scaffolding a command `doctor` then can't validate on some OS)
has one obvious place to land, and so `quor init --mcp`'s and `doctor`'s
MCP checks are proven not to raise on any of the three OSes regardless
of which OS actually runs the suite.
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path

import orjson
import pytest
from typer.testing import CliRunner

from quor.cli.commands import doctor
from quor.cli.commands import init as init_module
from quor.cli.main import app
from quor.mcp import launcher

runner = CliRunner()

_SYS_PLATFORMS = ["win32", "darwin", "linux"]


@pytest.fixture(autouse=True)
def _chdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


def _make_venv_python(root: Path, *, sys_platform: str) -> Path:
    venv_python = (
        root / ".venv" / "Scripts" / "python.exe"
        if sys_platform == "win32"
        else root / ".venv" / "bin" / "python"
    )
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")
    return venv_python


class TestExpectedVenvPython:
    @pytest.mark.parametrize("sys_platform", _SYS_PLATFORMS)
    def test_resolves_to_an_os_appropriate_path(
        self, sys_platform: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(sys, "platform", sys_platform)

        result = launcher.expected_venv_python(tmp_path)

        expected = (
            tmp_path / ".venv" / "Scripts" / "python.exe"
            if sys_platform == "win32"
            else tmp_path / ".venv" / "bin" / "python"
        )
        assert result == expected


class TestQuorInitAcrossPlatforms:
    """`quor init --mcp --yes` must complete and write a valid `.mcp.json`
    regardless of the mocked target OS, both with and without a local
    `.venv` already populated."""

    @pytest.mark.parametrize("sys_platform", _SYS_PLATFORMS)
    def test_no_local_venv(
        self, sys_platform: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(sys, "platform", sys_platform)

        result = runner.invoke(app, ["init", "--mcp", "--yes"])

        assert result.exit_code == 0, result.stdout
        written = orjson.loads((tmp_path / ".mcp.json").read_bytes())
        command = written["mcpServers"]["quor"]["command"]
        assert "\\" not in command

    @pytest.mark.parametrize("sys_platform", _SYS_PLATFORMS)
    def test_with_local_venv(
        self, sys_platform: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(sys, "platform", sys_platform)
        _make_venv_python(tmp_path, sys_platform=sys_platform)

        result = runner.invoke(app, ["init", "--mcp", "--yes"])

        assert result.exit_code == 0, result.stdout
        written = orjson.loads((tmp_path / ".mcp.json").read_bytes())
        command = written["mcpServers"]["quor"]["command"]
        assert "\\" not in command
        suffix = "Scripts/python.exe" if sys_platform == "win32" else "bin/python"
        assert command == f"${{CLAUDE_PROJECT_DIR:-.}}/.venv/{suffix}"

    @pytest.mark.parametrize("sys_platform", _SYS_PLATFORMS)
    def test_mcp_server_entry_never_raises(
        self, sys_platform: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Directly exercises `_mcp_server_entry()` for a `project_root`
        that doesn't exist yet at all (not even created by `Path.cwd()`'s
        own chdir) — `expected_venv_python()`'s `.exists()` check must
        degrade to the `sys.executable` fallback, never raise."""
        monkeypatch.setattr(sys, "platform", sys_platform)
        missing_root = tmp_path / "does-not-exist"

        entry = init_module._mcp_server_entry(missing_root)

        assert "\\" not in entry["command"]


class TestQuorDoctorAcrossPlatforms:
    """The `platform.system()`-branched global config candidates, and the
    `sys.platform`-branched `.venv` normalization for a templated
    workspace command, must resolve without raising under all three
    platforms, whether or not anything actually exists on disk."""

    @pytest.mark.parametrize(
        ("system_name", "sys_platform"),
        [("Windows", "win32"), ("Darwin", "darwin"), ("Linux", "linux")],
    )
    def test_global_candidates_never_raise(
        self,
        system_name: str,
        sys_platform: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(platform, "system", lambda: system_name)
        monkeypatch.setattr(Path, "home", lambda: home)
        monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))

        candidates = doctor._global_mcp_config_candidates()

        assert all(isinstance(c, Path) for c in candidates)
        assert home / ".mcp.json" in candidates

    def test_windows_without_appdata_still_returns_the_bare_mcp_json_candidate(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A stripped-down environment (e.g. some CI containers, or a
        service account) may not have `APPDATA` set at all — this must
        degrade gracefully, not raise `TypeError`/`KeyError`."""
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        monkeypatch.setattr(Path, "home", lambda: home)
        monkeypatch.delenv("APPDATA", raising=False)

        candidates = doctor._global_mcp_config_candidates()

        assert candidates == [home / ".mcp.json"]

    @pytest.mark.parametrize(
        ("system_name", "sys_platform"),
        [("Windows", "win32"), ("Darwin", "darwin"), ("Linux", "linux")],
    )
    def test_check_mcp_json_files_never_raises_on_a_clean_checkout(
        self,
        system_name: str,
        sys_platform: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(platform, "system", lambda: system_name)
        monkeypatch.setattr(Path, "home", lambda: home)
        monkeypatch.setattr(sys, "platform", sys_platform)
        monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))

        rows = doctor._check_mcp_json_files()

        assert rows
        assert all(status is doctor.Status.WARN for _, status, _ in rows)

    @pytest.mark.parametrize("sys_platform", _SYS_PLATFORMS)
    def test_validates_a_venv_relative_workspace_command_scaffolded_by_init(
        self, sys_platform: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """End-to-end across the two modules: whatever `init` scaffolds
        for this OS, `doctor` must be able to validate without raising,
        reporting the OS-appropriate interpreter it expects."""
        monkeypatch.setattr(sys, "platform", sys_platform)
        _make_venv_python(tmp_path, sys_platform=sys_platform)
        runner.invoke(app, ["init", "--mcp", "--yes"])

        _, status, detail = doctor._validate_mcp_config_file(
            tmp_path / ".mcp.json", "workspace"
        )

        assert status is doctor.Status.WARN
        assert "expected .venv interpreter exists" in detail
