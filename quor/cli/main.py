"""Typer application and command registration.

The six V1 commands (do not add more without approval) plus five exempt
utility commands — `schema` (JSON Schema dump), `map` (QB-061's
deterministic Repository Context Profile), `symbols` (QB-066's
deterministic Repository Symbols index), `graph` (QB-067's deterministic
Repository Dependency Graph), and `version` (QB-073) — none is a filtering
operation, so none counts against the six:
  quor init --claude
  quor validate [file]
  quor explain <command>
  quor gain
  quor verify
  quor doctor
  quor schema
  quor map
  quor symbols
  quor graph
  quor version

Commands are grouped into three `rich_help_panel`s for `--help` (QB-073):
Installation (init, doctor), Analysis (map, symbols, graph), and Utilities
(everything else) — purely a `--help` presentation grouping, Typer's own
built-in mechanism; it changes nothing about how any command is invoked,
routed (`__main__.py::_CLI_COMMANDS`), or tested.
"""

import typer

from quor import __version__
from quor.cli.commands.doctor import doctor, should_warn_stale_hooks
from quor.cli.commands.explain import explain
from quor.cli.commands.gain import gain
from quor.cli.commands.graph import graph_command
from quor.cli.commands.init import init
from quor.cli.commands.map import map_command
from quor.cli.commands.symbols import symbols_command
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
app.command(name="map", rich_help_panel=_PANEL_ANALYSIS)(map_command)
app.command(name="symbols", rich_help_panel=_PANEL_ANALYSIS)(symbols_command)
app.command(name="graph", rich_help_panel=_PANEL_ANALYSIS)(graph_command)
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

    # Skip on "init"/"doctor": init is the fix itself, and doctor already
    # reports this in full detail — showing the same one-liner ahead of
    # either would just be redundant, not additional information.
    if ctx.invoked_subcommand not in ("init", "doctor"):
        _warn_if_hooks_stale()


def _warn_if_hooks_stale() -> None:
    """Post-upgrade nudge (see `should_warn_stale_hooks` docstring for why
    this is needed at all: `pip install --upgrade quor` never touches the
    hook scripts/settings.json entries `quor init --claude` writes).
    `should_warn_stale_hooks` itself is warn-once-per-schema, so this fires
    at most once per stale schema, not on every command. Fail-open — an
    error here must never block the subcommand it precedes, since this is a
    courtesy nudge, not a health check."""
    try:
        if should_warn_stale_hooks():
            typer.secho(
                "⚠ Quor hooks are out of date — run: quor init --claude",
                fg=typer.colors.YELLOW,
            )
    except Exception:  # noqa: BLE001
        pass


@app.command(rich_help_panel=_PANEL_UTILITIES)
def schema() -> None:
    """Output the Quor filter file JSON Schema to stdout."""
    import orjson

    from quor.config.model import QuorConfig

    typer.echo(
        orjson.dumps(QuorConfig.model_json_schema(), option=orjson.OPT_INDENT_2).decode()
    )
