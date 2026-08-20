"""Self-healing pre-flight entrypoint for the MCP server (complements the
QB-104-era `quor init --mcp` fix in `quor/cli/commands/init.py`).

That fix made `.mcp.json` point at `sys.executable` instead of a bare
`"python"`, so the scaffolded config resolves to the exact interpreter Quor
was installed into — no more resolving off whatever the MCP client's PATH
happens to have first. But pointing at the *right* interpreter doesn't
guarantee that interpreter's environment stays correct over time: a `.venv`
can drift out of sync with `pyproject.toml` (this repo hit exactly that:
`.venv` last synced before `mcp>=2.0.0` was pinned, so `import mcp` failed
even though `sys.executable` was correct), a dependency can get uninstalled,
or a fresh clone can be pointed at before its `.venv` is ever populated.

Either way, the failure mode is the same one QB-104's fix didn't touch:
`quor/mcp/server.py` fails to import (`from mcp.server.mcpserver import
MCPServer` raises `ModuleNotFoundError`) before the MCP stdio handshake
ever starts, so the client has nothing to show a trust/approval prompt
for — the server just silently never comes up.

This module is `quor init --mcp`'s new scaffolded entrypoint
(`python -m quor.mcp.launcher`, run under `sys.executable`) instead of
`quor.mcp.server` directly. It runs a fast import pre-flight before
touching stdio at all:

  1. Are `mcp` and `quor.mcp.server` cleanly importable? If yes, hand off
     to `quor.mcp.server.main()` immediately — no measurable startup cost
     beyond the two already-necessary imports.
  2. If not, is this interpreter running Quor from a local dev checkout
     (a `pyproject.toml` above this file whose `[project].name == "quor"`,
     the same shape this repo's own `.venv` lives next to)? Try
     `uv sync`/`pip install -e .` against that checkout, using this exact
     interpreter (`sys.executable`) — never a bare `python`/`uv`, for the
     same PATH-resolution reason `quor/rewrite/invocation.py`'s
     `get_quor_invocation()` already documents.
  3. Otherwise (a real `pip install quor` install, no local source tree to
     `-e` install from): reinstall just the missing piece — `pip install`
     whatever version spec `quor`'s own installed metadata declares for
     `mcp`, read dynamically so this never drifts from `pyproject.toml`'s
     actual pin.
  4. Re-check imports after repair. Success hands off to
     `quor.mcp.server.main()`. Failure prints troubleshooting steps to
     `sys.stderr` and exits 1 — a fast, explicit failure the MCP client can
     detect immediately, instead of the silent pre-handshake crash this
     replaces.

Every diagnostic line this module ever prints goes to `sys.stderr`, never
`sys.stdout` — stdio is the MCP transport once `quor.mcp.server.main()`
takes over, and `sys.stdout` must stay pristine JSON-RPC framing from the
moment this process is spawned, the same rule `quor/mcp/server.py` already
follows by never importing `rich`. The same initialization/error events
are also logged to `~/.quor/logs/mcp.log` (a rotating file handler,
`quor/mcp/logging_config.py`) — a durable record for post-mortem debugging
that doesn't depend on the MCP client having kept the process's stderr
around. `QUOR_MCP_DISABLE_AUTOREPAIR=1` skips
straight to the failure path without attempting a network install — for
the offline/locked-down corporate environments this project already
targets (see `docs/final/CLAUDE.md`'s "Primary OS" line), where an
unprompted `pip install` is the wrong default even when it would work.
"""

from __future__ import annotations

import importlib
import logging
import os
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Sequence
from importlib import metadata as importlib_metadata
from pathlib import Path

from quor.mcp.logging_config import LOGGER_NAME, configure_logging

_logger = logging.getLogger(LOGGER_NAME)

_REQUIRED_MODULES: tuple[str, ...] = ("mcp", "quor", "quor.mcp.server")

_DISABLE_AUTOREPAIR_ENV = "QUOR_MCP_DISABLE_AUTOREPAIR"

_REPAIR_TIMEOUT_SECONDS = 180.0

# How far above this file to look for a dev checkout's pyproject.toml.
# `quor/mcp/launcher.py` -> `quor/` -> repo root is 2 levels in this repo's
# own layout; a handful of extra levels of headroom costs nothing (each
# level is one `is_file()` stat) and covers an unusually deep checkout path.
_MAX_CHECKOUT_SEARCH_DEPTH = 6


def expected_venv_python(root: Path) -> Path:
    """OS-appropriate path to a project-local `.venv`'s interpreter,
    relative to `root`: `.venv\\Scripts\\python.exe` on Windows,
    `.venv/bin/python` on macOS/Linux — the two layouts `python -m venv`
    itself creates. Read from `sys.platform` at call time (not a
    module-level constant) so callers can exercise both branches under
    test via `monkeypatch.setattr(sys, "platform", ...)`.

    Shared by `quor/cli/commands/init.py` (deciding whether to scaffold a
    `.venv`-relative `.mcp.json` command) and `quor/cli/commands/doctor.py`
    (checking whether a configured `.venv`-relative command has a real
    interpreter behind it) — one OS-detection rule instead of two that
    could drift apart.
    """
    if sys.platform == "win32":
        return root / ".venv" / "Scripts" / "python.exe"
    return root / ".venv" / "bin" / "python"


def _check_imports(modules: Sequence[str] = _REQUIRED_MODULES) -> tuple[bool, str]:
    for module_name in modules:
        try:
            importlib.import_module(module_name)
        except ImportError as exc:
            return False, f"{module_name}: {exc}"
    return True, ""


def _find_dev_checkout_root() -> Path | None:
    """Return the repo root if this file is running from a local Quor
    checkout (editable install or `python -m quor.mcp.launcher` run
    straight from source), else `None` for a normal `pip install quor`."""
    here = Path(__file__).resolve()
    for parent in (here, *here.parents)[: _MAX_CHECKOUT_SEARCH_DEPTH + 1]:
        pyproject = parent / "pyproject.toml"
        if not pyproject.is_file():
            continue
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
            continue
        if data.get("project", {}).get("name") == "quor":
            return parent
    return None


def _mcp_requirement_spec() -> str:
    """The version spec `quor`'s own installed metadata declares for `mcp`
    (e.g. `"mcp>=2.0.0"`) — read dynamically so a future bump to
    `pyproject.toml`'s pin never needs a matching edit here."""
    try:
        reqs = importlib_metadata.requires("quor") or []
    except importlib_metadata.PackageNotFoundError:
        return "mcp"
    for req in reqs:
        name = re.split(r"[<>=!~;\s]", req, maxsplit=1)[0].strip()
        if name.lower() == "mcp":
            return req.split(";")[0].strip()
    return "mcp"


def _run_repair_command(cmd: Sequence[str], *, timeout: float = _REPAIR_TIMEOUT_SECONDS) -> bool:
    print(f"[quor-mcp-launcher] running: {' '.join(cmd)}", file=sys.stderr)
    try:
        result = subprocess.run(
            list(cmd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"[quor-mcp-launcher] repair command failed to run: {exc}", file=sys.stderr)
        return False
    if result.returncode != 0:
        print(
            f"[quor-mcp-launcher] repair command exited {result.returncode}",
            file=sys.stderr,
        )
        if result.stderr:
            print(result.stderr.strip(), file=sys.stderr)
        return False
    return True


def _attempt_self_repair() -> bool:
    if os.environ.get(_DISABLE_AUTOREPAIR_ENV):
        print(
            f"[quor-mcp-launcher] auto-repair disabled via {_DISABLE_AUTOREPAIR_ENV}",
            file=sys.stderr,
        )
        return False

    dev_root = _find_dev_checkout_root()
    candidates: list[list[str]] = []
    if dev_root is not None:
        if shutil.which("uv"):
            candidates.append(["uv", "sync", "--project", str(dev_root)])
        candidates.append([sys.executable, "-m", "pip", "install", "-e", str(dev_root)])
    else:
        candidates.append([sys.executable, "-m", "pip", "install", _mcp_requirement_spec()])

    for cmd in candidates:
        if not _run_repair_command(cmd):
            continue
        importlib.invalidate_caches()
        ok, _ = _check_imports()
        if ok:
            return True
    return False


def _print_troubleshooting(error: str) -> None:
    print("quor MCP server failed to start.", file=sys.stderr)
    print(f"  Import error: {error}", file=sys.stderr)
    print("", file=sys.stderr)
    print("Automatic repair was attempted and did not fix it. To fix manually:", file=sys.stderr)
    print(f"  1. {sys.executable} -m pip install --upgrade quor", file=sys.stderr)
    print(
        "  2. If you're working from a local checkout of the quor repo, from the repo root:",
        file=sys.stderr,
    )
    print(f"     {sys.executable} -m pip install -e .", file=sys.stderr)
    print(
        "  3. Re-run `quor init --mcp --yes` to re-scaffold .mcp.json against this interpreter.",
        file=sys.stderr,
    )
    print(
        f"  4. Offline or on a restricted network? Set {_DISABLE_AUTOREPAIR_ENV}=1 and install "
        "dependencies manually first — automatic repair needs network access.",
        file=sys.stderr,
    )


def _handoff() -> None:
    from quor.mcp.server import main as server_main

    server_main()


def main() -> None:
    configure_logging()
    _logger.info("quor-mcp-launcher starting (interpreter=%s)", sys.executable)
    ok, error = _check_imports()
    if not ok:
        print(
            f"[quor-mcp-launcher] pre-flight check failed ({error}); attempting self-repair...",
            file=sys.stderr,
        )
        _logger.warning("pre-flight check failed (%s); attempting self-repair...", error)
        if not _attempt_self_repair():
            _print_troubleshooting(error)
            _logger.error("self-repair failed; giving up (%s)", error)
            sys.exit(1)
        print("[quor-mcp-launcher] self-repair succeeded; starting server.", file=sys.stderr)
        _logger.info("self-repair succeeded; starting server")
    _handoff()


if __name__ == "__main__":
    main()
