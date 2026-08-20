# Quor MCP Server (`quor/mcp/server.py`)

Testing notes for Quor's MCP-over-stdio server — the sole integration
surface as of QB-104 (the former hook-based integration was removed; see
`backlog.md`'s QB-104 entry).

## What it is

`quor/mcp/server.py` starts a local MCP server (`"Quor Context Compressor"`)
exposing two tools:

- `compress_context(raw_text: str) -> str` — runs the input through Quor's
  `FilterRegistry` + `Pipeline` machinery (the same code path
  `quor/engine/dispatcher.py` uses for Bash output) and returns the
  compressed text prefixed with a `[Quor Compressed: XX% saved]` header.
- `get_repo_context(file_path: str = "", query: str = "") -> str` — returns
  deterministic repository intelligence for a file and/or files relevant to
  a query, ported from the former Read-hook's "Repository Context"/
  "Relevant repository files" features. Requires `quor map` to have been
  run first.

`mcp>=2.0.0` is a core dependency (declared in `pyproject.toml`), installed
automatically with `pip install quor`.

## 1. Register the server

### Claude Code CLI

```
claude mcp add quor -- python -m quor.mcp.launcher
```

Run this from the repo root (so `python -m quor.mcp.launcher` resolves
against this checkout). This runs `quor/mcp/launcher.py`, not
`quor/mcp/server.py` directly — a fast pre-flight import check that
self-repairs a stale `.venv`/missing dependency before handing off to the
real server, instead of the server crashing pre-handshake with nothing to
show a trust prompt for. Verify registration with:

```
claude mcp list
```

### `claude_desktop_config.json`

Add an entry under `mcpServers`, pointing `command` at the exact Python
interpreter Quor is installed into — a bare `"python"` resolves off
whatever PATH the client process happens to have, which is almost never
the interpreter Quor is actually installed in (venv/pipx/conda all break
this silently, with no trust prompt to signal it — see the "MCP server
never starts" note below). Find it with `python -c "import sys;
print(sys.executable)"` run from the same environment `quor` is installed
in:

```json
{
  "mcpServers": {
    "quor": {
      "command": "C:/Users/PUSHPP02/OneDrive - Heineken International/Desktop/Workspace/Quor/.venv/Scripts/python.exe",
      "args": ["-m", "quor.mcp.launcher"],
      "cwd": "C:/Users/PUSHPP02/OneDrive - Heineken International/Desktop/Workspace/Quor"
    }
  }
}
```

`quor init --mcp` generates this correctly automatically (it writes
`sys.executable` and `-m quor.mcp.launcher`, not a bare `"python"` running
the server module directly) — this manual snippet is only for
`claude_desktop_config.json`, which `init --mcp` can't write for you.

**If the MCP client's trust/approval prompt for `quor` never appears at
startup:** that's the symptom of exactly this misconfiguration, not a
client bug. A bare `"python"` (or any interpreter missing the `mcp`
package) makes the server process crash before it completes the MCP
handshake, so the client has nothing to show a trust prompt for — it fails
silently pre-handshake rather than surfacing an error.
`quor/mcp/launcher.py` self-repairs the common cases of this (stale
`.venv`, missing dependency) automatically before that crash can happen —
see its module docstring. If the prompt still never appears after that,
the repair itself failed (e.g. no network access — check `sys.stderr` for
what the launcher printed, or set `QUOR_MCP_DISABLE_AUTOREPAIR=1` and
install dependencies manually if you're offline).

Restart Claude Desktop after editing the config for it to pick up the new
server.

## 2. Test it locally

### `compress_context`

Generate some high-volume output to compress, e.g.:

```
git log -n 50
```

Then, in a Claude session with the `quor` MCP server registered, paste that
output (or ask Claude to run the command itself) and prompt:

> Use the `compress_context` tool on this git log output.

Expect a response starting with `[Quor Compressed: XX% saved]` followed by
the compressed text. Since `git log` output doesn't match any of Quor's
command-shaped filter patterns (those match the *command* string, e.g.
`^git\s+log\b`, not the log *output*), this exercises the built-in `generic`
fallback filter (`quor/filters/builtin/z_generic.toml`) — the same catch-all
path `quor/engine/dispatcher.py` uses for any unrecognized command.

### `get_repo_context`

Run `quor map` in this repo first to build repository intelligence, then
prompt:

> Use the `get_repo_context` tool for file_path `quor/mcp/server.py`.

Expect a "Repository Context" block (language, exported symbols, import
counts). Passing `query` instead (e.g. `"compress_context"`) returns a
"Relevant repository files" block from `quor search`'s same matching logic.

## Migration note

Anyone with a pre-QB-104 install (`quor init --claude` or
`quor init --agent gemini` from an older release) should run
`quor uninstall-hooks` to remove the now-inert leftover hook scripts and
settings.json entries.
