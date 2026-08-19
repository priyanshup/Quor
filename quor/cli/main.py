"""Typer application and command registration.

The six V1 commands (do not add more without approval) plus ten exempt
utility commands — `schema` (JSON Schema dump), `map` (QB-061's
deterministic Repository Context Profile), `symbols` (QB-066's
deterministic Repository Symbols index), `graph` (QB-067's deterministic
Repository Dependency Graph), `version` (QB-073), `repo` (QB-076's
Repository Intelligence Dashboard), `explore` (QB-078's Repository
Explorer, ADR-042 — a cache-only reporting sub-app, distinct from `repo` in
that it never calls `ensure_repo_intelligence()`, see
`quor/cli/commands/explore.py`'s own module docstring), `search`
(QB-080's Semantic Repository Search — deterministic, cache-only, reads
only `file_intelligence.json`, never `ensure_repo_intelligence()` and never
`symbol_facts.json`/`graph_facts.json`, see
`quor/cli/commands/search.py`'s own module docstring), `dashboard`
(QB-083's live terminal token-savings view — reads the same tracking DB
`quor gain` does, computes nothing new, see
`quor/cli/commands/dashboard.py`'s own module docstring for why it's a
terminal TUI and not a browser dashboard), and `discover` (QB-034's
retroactive session scan — reads Claude Code's own local session
transcripts under `~/.claude/projects/`, scores every `Bash` invocation
Quor never compressed against the real filter pipeline, and reports what
switching to (or fully adopting) Quor would have saved; see
`quor/discovery/session_scan.py`'s own module docstring for why this is
safe against `ANTI_GOALS.md` #4/#5 — nothing scanned is stored or
transmitted) — none is a filtering operation, so none counts against the
six:
  quor init --mcp
  quor validate [file]
  quor explain <command>
  quor gain
  quor verify
  quor doctor
  quor schema
  quor map
  quor symbols
  quor graph
  quor repo
  quor explore
  quor search
  quor version
  quor dashboard
  quor discover

`init --mcp` scaffolds MCP server registration (writes `./.mcp.json`,
prints the `claude_desktop_config.json` equivalent) — QB-104's replacement
for the old hook-installation `init`, which `doctor --fix` used to repair
alongside it; neither `doctor` nor `init` installs a launcher script
anymore, since Quor's MCP server (`quor/mcp/server.py`) is registered via
client-side config, not a Quor-generated script. `init` also runs an
unprompted legacy-hook cleanup pass on every invocation (QB-104 Phase 3).
`uninstall-hooks` remains for anyone who wants that same cleanup without
also scaffolding MCP registration.

Commands are grouped into three `rich_help_panel`s for `--help` (QB-073):
Installation (init, doctor, uninstall-hooks), Analysis (map, symbols,
graph, repo, explore, search), and Utilities (everything else) — purely a
`--help` presentation grouping, Typer's own built-in mechanism; it changes
nothing about how any command is invoked, routed
(`__main__.py::_CLI_COMMANDS`), or tested.
"""

import typer

from quor import __version__
from quor.cli.commands.dashboard import dashboard_command
from quor.cli.commands.discover import discover
from quor.cli.commands.doctor import doctor
from quor.cli.commands.explain import explain
from quor.cli.commands.explore import explore_app
from quor.cli.commands.gain import gain
from quor.cli.commands.graph import graph_command
from quor.cli.commands.init import init
from quor.cli.commands.map import map_command
from quor.cli.commands.repo import repo_command
from quor.cli.commands.search import search_command
from quor.cli.commands.symbols import symbols_command
from quor.cli.commands.uninstall_hooks import uninstall_hooks
from quor.cli.commands.validate import validate
from quor.cli.commands.verify import verify
from quor.cli.commands.version import version_command

_PANEL_INSTALLATION = "Installation"
_PANEL_ANALYSIS = "Analysis"
_PANEL_UTILITIES = "Utilities"

app = typer.Typer(
    name="quor",
    help="Rule-based command-output optimization and context-compression layer for AI coding assistants.",
    add_completion=False,
    rich_markup_mode="rich",
    no_args_is_help=False,
)

app.command(rich_help_panel=_PANEL_INSTALLATION)(init)
app.command(rich_help_panel=_PANEL_INSTALLATION)(doctor)
app.command(name="uninstall-hooks", rich_help_panel=_PANEL_INSTALLATION)(uninstall_hooks)
app.command(name="map", rich_help_panel=_PANEL_ANALYSIS)(map_command)
app.command(name="symbols", rich_help_panel=_PANEL_ANALYSIS)(symbols_command)
app.command(name="graph", rich_help_panel=_PANEL_ANALYSIS)(graph_command)
app.command(name="repo", rich_help_panel=_PANEL_ANALYSIS)(repo_command)
app.add_typer(explore_app, name="explore", rich_help_panel=_PANEL_ANALYSIS)
app.command(name="search", rich_help_panel=_PANEL_ANALYSIS)(search_command)
app.command(name="dashboard", rich_help_panel=_PANEL_ANALYSIS)(dashboard_command)
app.command(name="discover", rich_help_panel=_PANEL_ANALYSIS)(discover)
app.command(rich_help_panel=_PANEL_UTILITIES)(explain)
app.command(rich_help_panel=_PANEL_UTILITIES)(validate)
app.command(rich_help_panel=_PANEL_UTILITIES)(verify)
app.command(rich_help_panel=_PANEL_UTILITIES)(gain)
app.command(name="version", rich_help_panel=_PANEL_UTILITIES)(version_command)


def _version_callback(show_version: bool) -> None:
    # is_eager: processed before any other option/subcommand resolution —
    # the same convention `git --version`/`node --version` follow, and
    # exactly what `--version` (unlike the bare-invocation banner below,
    # which only fires with zero arguments at all) needs to work regardless
    # of what else appears on the command line.
    if show_version:
        typer.echo(f"quor {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the installed Quor version and exit.",
    ),
) -> None:
    """Quor — reduces unnecessary LLM context from AI coding assistant command output before it hits the context window, while preserving what matters."""
    if ctx.invoked_subcommand is None:
        typer.echo(f"quor {__version__}")
        raise typer.Exit()


@app.command(rich_help_panel=_PANEL_UTILITIES)
def schema() -> None:
    """Output the Quor filter file JSON Schema to stdout."""
    import orjson

    from quor.config.model import QuorConfig

    typer.echo(
        orjson.dumps(QuorConfig.model_json_schema(), option=orjson.OPT_INDENT_2).decode()
    )
