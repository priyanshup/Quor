"""Typer application and command registration.

All six V1 commands are registered here (do not add more without approval):
  quor init --claude
  quor validate [file]
  quor explain <command>
  quor gain
  quor verify
  quor doctor
"""

import typer

from quor import __version__
from quor.cli.commands.doctor import doctor
from quor.cli.commands.explain import explain
from quor.cli.commands.gain import gain
from quor.cli.commands.init import init
from quor.cli.commands.validate import validate
from quor.cli.commands.verify import verify

app = typer.Typer(
    name="quor",
    help="Context compression middleware for AI coding assistants.",
    add_completion=False,
    rich_markup_mode="rich",
    no_args_is_help=False,
)

app.command()(init)
app.command()(validate)
app.command()(explain)
app.command()(gain)
app.command()(verify)
app.command()(doctor)


@app.callback(invoke_without_command=True)
def root(ctx: typer.Context) -> None:
    """Quor — compress AI coding assistant command output before it hits the context window."""
    if ctx.invoked_subcommand is None:
        typer.echo(f"quor {__version__}")
        raise typer.Exit()


@app.command()
def schema() -> None:
    """Output the Quor filter file JSON Schema to stdout."""
    import orjson

    from quor.config.model import QuorConfig

    typer.echo(
        orjson.dumps(QuorConfig.model_json_schema(), option=orjson.OPT_INDENT_2).decode()
    )
