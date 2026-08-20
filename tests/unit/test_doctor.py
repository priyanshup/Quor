"""Tests for the MCP-specific diagnostics `quor doctor` runs: interpreter
isolation, `mcp`/`quor` importability, `.mcp.json`/`claude_desktop_config.json`
validation, and the live `quor.mcp.launcher` stdio handshake dry-run.

Each collaborator is tested directly against its own return value
(`tuple[str, Status, str]`) rather than through the full `doctor()` CLI
command — `_run_doctor()` also runs the pre-existing sqlite/filter/tee/
plugin checks, which are already real-filesystem/real-DB operations with
their own coverage elsewhere, and asserting on 20+ interleaved check rows
per test would make failures hard to attribute. `_check_mcp_launcher_handshake`
mostly has its `anyio.run` collaborator monkeypatched, the same reasoning
`test_mcp_launcher.py` already documents for not exercising a real stdio
loop in the default (non-integration) suite — one real end-to-end handshake
test is kept, marked `@pytest.mark.integration`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import orjson
import pytest

from quor.cli.commands import doctor
from quor.cli.commands.doctor import Status


class TestMcpInterpreterIsolation:
    def test_venv_prefix_mismatch_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "prefix", "/some/venv")
        monkeypatch.setattr(sys, "base_prefix", "/usr", raising=False)
        monkeypatch.delenv("VIRTUAL_ENV", raising=False)
        monkeypatch.delenv("CONDA_PREFIX", raising=False)

        _, status, detail = doctor._check_mcp_interpreter_isolation()

        assert status is Status.PASS
        assert "isolated virtual environment" in detail

    def test_virtual_env_var_alone_passes_even_with_matching_prefixes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "prefix", "/usr")
        monkeypatch.setattr(sys, "base_prefix", "/usr", raising=False)
        monkeypatch.setenv("VIRTUAL_ENV", "/some/venv")
        monkeypatch.delenv("CONDA_PREFIX", raising=False)

        _, status, _ = doctor._check_mcp_interpreter_isolation()

        assert status is Status.PASS

    def test_global_interpreter_warns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "prefix", "/usr")
        monkeypatch.setattr(sys, "base_prefix", "/usr", raising=False)
        monkeypatch.delenv("VIRTUAL_ENV", raising=False)
        monkeypatch.delenv("CONDA_PREFIX", raising=False)

        name, status, detail = doctor._check_mcp_interpreter_isolation()

        assert status is Status.WARN
        assert sys.executable in name
        assert "not an isolated virtual environment" in detail
        assert "quor init --mcp" in detail


class TestMcpDependencies:
    def test_both_importable_pass(self) -> None:
        """`mcp` and `quor` are real installed dependencies of this repo's
        own dev environment — this is the "valid environment" case, no
        mocking needed."""
        results = doctor._check_mcp_dependencies()

        assert [name for name, _, _ in results] == [
            "MCP dependency 'mcp' importable",
            "MCP dependency 'quor' importable",
        ]
        assert all(status is Status.PASS for _, status, _ in results)

    def test_missing_dependency_fails_with_repair_hint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import importlib

        real_import_module = importlib.import_module

        def _fake_import_module(name: str) -> Any:
            if name == "mcp":
                raise ImportError("No module named 'mcp'")
            return real_import_module(name)

        monkeypatch.setattr(importlib, "import_module", _fake_import_module)

        results = doctor._check_mcp_dependencies()
        by_name = {name: (status, detail) for name, status, detail in results}

        mcp_status, mcp_detail = by_name["MCP dependency 'mcp' importable"]
        assert mcp_status is Status.FAIL
        assert "No module named 'mcp'" in mcp_detail
        assert f"{sys.executable} -m pip install mcp" in mcp_detail

        quor_status, _ = by_name["MCP dependency 'quor' importable"]
        assert quor_status is Status.PASS


class TestValidateMcpConfigFile:
    def _write(self, path: Path, payload: dict[str, Any]) -> None:
        path.write_bytes(orjson.dumps(payload))

    def test_missing_file_warns(self, tmp_path: Path) -> None:
        _, status, detail = doctor._validate_mcp_config_file(
            tmp_path / ".mcp.json", "workspace"
        )
        assert status is Status.WARN
        assert "not found" in detail
        assert "quor init --mcp" in detail

    def test_invalid_json_fails(self, tmp_path: Path) -> None:
        path = tmp_path / ".mcp.json"
        path.write_text("{not valid json", encoding="utf-8")

        _, status, detail = doctor._validate_mcp_config_file(path, "workspace")

        assert status is Status.FAIL
        assert "invalid JSON" in detail

    def test_non_object_json_fails(self, tmp_path: Path) -> None:
        path = tmp_path / ".mcp.json"
        path.write_bytes(orjson.dumps([1, 2, 3]))

        _, status, detail = doctor._validate_mcp_config_file(path, "workspace")

        assert status is Status.FAIL
        assert "JSON object" in detail

    def test_no_quor_entry_warns(self, tmp_path: Path) -> None:
        path = tmp_path / ".mcp.json"
        self._write(path, {"mcpServers": {"other-server": {"command": "x"}}})

        _, status, detail = doctor._validate_mcp_config_file(path, "workspace")

        assert status is Status.WARN
        assert "no 'quor' entry" in detail

    def test_quor_entry_not_object_fails(self, tmp_path: Path) -> None:
        path = tmp_path / ".mcp.json"
        self._write(path, {"mcpServers": {"quor": "not-an-object"}})

        _, status, detail = doctor._validate_mcp_config_file(path, "workspace")

        assert status is Status.FAIL
        assert "not a JSON object" in detail

    def test_missing_command_fails(self, tmp_path: Path) -> None:
        path = tmp_path / ".mcp.json"
        self._write(path, {"mcpServers": {"quor": {"args": ["-m", "quor.mcp.launcher"]}}})

        _, status, detail = doctor._validate_mcp_config_file(path, "workspace")

        assert status is Status.FAIL
        assert "missing a valid 'command'" in detail

    def test_command_interpreter_not_on_disk_fails(self, tmp_path: Path) -> None:
        path = tmp_path / ".mcp.json"
        self._write(
            path,
            {
                "mcpServers": {
                    "quor": {
                        "command": "/nonexistent/path/to/python",
                        "args": ["-m", "quor.mcp.launcher"],
                    }
                }
            },
        )

        _, status, detail = doctor._validate_mcp_config_file(path, "workspace")

        assert status is Status.FAIL
        assert "interpreter not found" in detail
        assert "quor init --mcp --yes" in detail

    def test_shell_variable_placeholder_command_warns_not_fails(self, tmp_path: Path) -> None:
        """This repo's own `.mcp.json` uses `${CLAUDE_PROJECT_DIR:-.}/.venv/bin/python`
        — a Claude Code client-side template Python can't resolve statically.
        That must never be reported as a broken interpreter path."""
        path = tmp_path / ".mcp.json"
        self._write(
            path,
            {
                "mcpServers": {
                    "quor": {
                        "command": "${CLAUDE_PROJECT_DIR:-.}/.venv/bin/python",
                        "args": ["-m", "quor.mcp.launcher"],
                    }
                }
            },
        )

        _, status, detail = doctor._validate_mcp_config_file(path, "workspace")

        assert status is Status.WARN
        assert "unexpanded variable" in detail

    def test_args_missing_launcher_warns(self, tmp_path: Path) -> None:
        path = tmp_path / ".mcp.json"
        self._write(
            path,
            {"mcpServers": {"quor": {"command": sys.executable, "args": ["-m", "quor.mcp.server"]}}},
        )

        _, status, detail = doctor._validate_mcp_config_file(path, "workspace")

        assert status is Status.WARN
        assert "does not invoke quor.mcp.launcher" in detail

    def test_fully_valid_entry_passes(self, tmp_path: Path) -> None:
        path = tmp_path / ".mcp.json"
        self._write(
            path,
            {
                "mcpServers": {
                    "quor": {"command": sys.executable, "args": ["-m", "quor.mcp.launcher"]}
                }
            },
        )

        _, status, detail = doctor._validate_mcp_config_file(path, "workspace")

        assert status is Status.PASS
        assert "quor registered" in detail


class TestCheckMcpJsonFiles:
    def test_covers_workspace_and_global_candidates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace = tmp_path / "workspace"
        home = tmp_path / "home"
        workspace.mkdir()
        home.mkdir()
        monkeypatch.chdir(workspace)
        monkeypatch.setattr(Path, "home", lambda: home)

        rows = doctor._check_mcp_json_files()

        scopes = [name for name, _, _ in rows]
        assert any("workspace" in s for s in scopes)
        assert any("global" in s for s in scopes)
        # None of the candidates exist in this isolated tmp_path — every
        # row should be an advisory WARN ("not found"), never a FAIL.
        assert all(status is Status.WARN for _, status, _ in rows)


class TestMcpLauncherHandshake:
    def test_import_failure_fails_cleanly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import builtins

        real_import = builtins.__import__

        def _fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "anyio":
                raise ImportError("No module named 'anyio'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)

        _, status, detail = doctor._check_mcp_launcher_handshake()

        assert status is Status.FAIL
        assert "mcp client library unavailable" in detail

    def test_timeout_fails_with_actionable_hint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import anyio

        def _raise_timeout(_func: Any) -> None:
            raise TimeoutError

        monkeypatch.setattr(anyio, "run", _raise_timeout)

        _, status, detail = doctor._check_mcp_launcher_handshake()

        assert status is Status.FAIL
        assert "no response within" in detail
        assert "quor.mcp.launcher" in detail

    def test_handshake_exception_fails_with_actionable_hint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import anyio

        def _raise(_func: Any) -> None:
            raise RuntimeError("boom")

        monkeypatch.setattr(anyio, "run", _raise)

        _, status, detail = doctor._check_mcp_launcher_handshake()

        assert status is Status.FAIL
        assert "handshake failed: boom" in detail

    def test_successful_handshake_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import anyio

        monkeypatch.setattr(anyio, "run", lambda _func: "Quor Context Compressor")

        name, status, detail = doctor._check_mcp_launcher_handshake()

        assert status is Status.PASS
        assert "dry-run" in name
        assert "Quor Context Compressor" in detail

    @pytest.mark.integration
    def test_real_subprocess_handshake_succeeds(self) -> None:
        """End-to-end: actually spawns `python -m quor.mcp.launcher` and
        completes a real MCP stdio `initialize` handshake against it, the
        same handshake any MCP client performs on startup. Excluded from
        the default suite (real subprocess spawn, real stdio I/O) — run
        explicitly with `pytest -m integration`, same as this project's
        other real-subprocess tests."""
        name, status, detail = doctor._check_mcp_launcher_handshake()

        assert status is Status.PASS, detail
        assert "Quor Context Compressor" in detail
        assert "dry-run" in name
