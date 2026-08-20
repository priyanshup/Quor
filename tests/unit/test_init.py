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

from quor.cli.main import app

runner = CliRunner()


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
        assert written["mcpServers"]["quor"]["command"] == sys.executable

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
        assert sys.executable in result.stdout

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
        assert written["mcpServers"]["quor"]["command"] == sys.executable
