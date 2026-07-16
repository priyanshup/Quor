"""Typer application and command registration.

The six V1 commands (do not add more without approval) plus the `schema`
utility command (JSON Schema dump, not a filtering operation — exempt from
the six-command count):
  quor init --claude
  quor validate [file]
  quor explain <command>
  quor gain
  quor verify
  quor doctor
  quor schema
"""

import typer

from quor import __version__
from quor.cli.commands.doctor import doctor, should_warn_stale_hooks
from quor.cli.commands.explain import explain
from quor.cli.commands.gain import gain
from quor.cli.commands.init import init
from quor.cli.commands.validate import validate
from quor.cli.commands.verify import verify

app = typer.Typer(
    name="quor",
    help="Rule-based command-output optimization and context-compression layer for AI coding assistants.",
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


@app.command()
def schema() -> None:
    """Output the Quor filter file JSON Schema to stdout."""
    import orjson

    from quor.config.model import QuorConfig

    typer.echo(
        orjson.dumps(QuorConfig.model_json_schema(), option=orjson.OPT_INDENT_2).decode()
    )
