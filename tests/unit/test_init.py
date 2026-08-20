"""Tests for `quor init --mcp`'s `.mcp.json` scaffolding.

Regression coverage for the bare-"python" bug: `_mcp_server_entry()` used to
be a module-level constant hardcoding `"command": "python"`. An MCP client
spawns that command as a raw subprocess — no shell, no venv activation, no
PATH resolution the way an interactive terminal does it — so on any machine
where the first "python" on the *client's* PATH wasn't the interpreter Quor
was installed into (pipx, conda, a separate project venv), the server
process crashed before completing the MCP handshake. Silently: no error
surfaced, and no trust/approval prompt ever appeared for it, since the
client had nothing to prompt about. See `sys.executable`'s existing use in
`quor/rewrite/invocation.py`'s `get_quor_invocation()` for the established
fix pattern this now also applies to the MCP scaffolding path.
"""

from __future__ import annotations

import sys
from pathlib import Path

import orjson
import pytest
from typer.testing import CliRunner

from quor.cli.commands import init as init_module
from quor.cli.main import app

runner = CliRunner()

# `_mcp_server_entry()` posix-ifies `sys.executable` before writing it into
# `.mcp.json` (forward slashes only, even on Windows) — see its docstring.
_EXPECTED_SYS_EXECUTABLE_COMMAND = Path(sys.executable).as_posix()


@pytest.fixture(autouse=True)
def _chdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`_scaffold_mcp` resolves `./.mcp.json` via `Path.cwd()`."""
    monkeypatch.chdir(tmp_path)


def _run_init_mcp() -> None:
    result = runner.invoke(app, ["init", "--mcp", "--yes"])
    assert result.exit_code == 0, result.stdout


class TestMcpServerEntryUsesRealInterpreter:
    def test_scaffolded_command_is_not_bare_python(self, tmp_path: Path) -> None:
        _run_init_mcp()
        written = orjson.loads((tmp_path / ".mcp.json").read_bytes())
        command = written["mcpServers"]["quor"]["command"]
        assert command != "python"

    def test_scaffolded_command_is_sys_executable(self, tmp_path: Path) -> None:
        _run_init_mcp()
        written = orjson.loads((tmp_path / ".mcp.json").read_bytes())
        assert written["mcpServers"]["quor"]["command"] == _EXPECTED_SYS_EXECUTABLE_COMMAND

    def test_scaffolded_args_invoke_the_self_healing_launcher(
        self, tmp_path: Path
    ) -> None:
        _run_init_mcp()
        written = orjson.loads((tmp_path / ".mcp.json").read_bytes())
        assert written["mcpServers"]["quor"]["args"] == ["-m", "quor.mcp.launcher"]

    def test_desktop_config_snippet_also_uses_real_interpreter(self) -> None:
        result = runner.invoke(app, ["init", "--mcp", "--yes"])
        assert result.exit_code == 0
        assert '"command": "python"' not in result.stdout
        expected_command = orjson.dumps(_EXPECTED_SYS_EXECUTABLE_COMMAND).decode()
        assert expected_command in result.stdout

    def test_rerun_is_idempotent_and_reports_already_up_to_date(self) -> None:
        _run_init_mcp()
        result = runner.invoke(app, ["init", "--mcp", "--yes"])
        assert result.exit_code == 0
        assert "already up to date" in result.stdout

    def test_existing_unrelated_mcp_servers_are_preserved(self, tmp_path: Path) -> None:
        mcp_json = tmp_path / ".mcp.json"
        mcp_json.write_bytes(
            orjson.dumps({"mcpServers": {"other-tool": {"command": "other-tool"}}})
        )
        _run_init_mcp()
        written = orjson.loads(mcp_json.read_bytes())
        assert written["mcpServers"]["other-tool"] == {"command": "other-tool"}
        assert written["mcpServers"]["quor"]["command"] == _EXPECTED_SYS_EXECUTABLE_COMMAND


class TestMcpServerEntryPrefersLocalVenv:
    """When `project_root` already has a populated `.venv`, scaffold an
    OS-aware `${CLAUDE_PROJECT_DIR:-.}`-relative command into it instead of
    `sys.executable` — matches this repo's own already-committed `.mcp.json`
    convention and survives a `.venv` recreation without a re-scaffold.
    Narrower than always preferring the relative form: see
    `_mcp_server_entry()`'s docstring for why a real local `.venv` is the
    gate, not just "on this OS"."""

    def _make_venv_python(self, tmp_path: Path, *, platform: str) -> None:
        if platform == "win32":
            venv_python = tmp_path / ".venv" / "Scripts" / "python.exe"
        else:
            venv_python = tmp_path / ".venv" / "bin" / "python"
        venv_python.parent.mkdir(parents=True)
        venv_python.write_text("", encoding="utf-8")

    def test_windows_venv_scaffolds_scripts_relative_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        self._make_venv_python(tmp_path, platform="win32")

        entry = init_module._mcp_server_entry(tmp_path)

        assert entry["command"] == "${CLAUDE_PROJECT_DIR:-.}/.venv/Scripts/python.exe"
        assert entry["args"] == ["-m", "quor.mcp.launcher"]

    def test_posix_venv_scaffolds_bin_relative_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        self._make_venv_python(tmp_path, platform="linux")

        entry = init_module._mcp_server_entry(tmp_path)

        assert entry["command"] == "${CLAUDE_PROJECT_DIR:-.}/.venv/bin/python"

    def test_no_local_venv_falls_back_to_sys_executable(self, tmp_path: Path) -> None:
        entry = init_module._mcp_server_entry(tmp_path)

        assert entry["command"] == _EXPECTED_SYS_EXECUTABLE_COMMAND

    def test_venv_directory_without_the_interpreter_still_falls_back(
        self, tmp_path: Path
    ) -> None:
        """An empty/partially-created `.venv` (e.g. `python -m venv` still
        running, or a stale directory) must not scaffold a command pointing
        at an interpreter that isn't actually there yet."""
        (tmp_path / ".venv").mkdir()

        entry = init_module._mcp_server_entry(tmp_path)

        assert entry["command"] == _EXPECTED_SYS_EXECUTABLE_COMMAND

    def test_cli_scaffold_uses_relative_command_when_venv_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        self._make_venv_python(tmp_path, platform="win32")

        _run_init_mcp()

        written = orjson.loads((tmp_path / ".mcp.json").read_bytes())
        assert (
            written["mcpServers"]["quor"]["command"]
            == "${CLAUDE_PROJECT_DIR:-.}/.venv/Scripts/python.exe"
        )
