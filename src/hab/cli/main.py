"""`hab` entry point."""
from __future__ import annotations

import typer

from hab import __version__
from hab.cli.export import export_command
from hab.cli.init import init_command
from hab.cli.replay import replay_command
from hab.cli.run import run_command

app = typer.Typer(
    name="hab",
    help="HoldemAgentBench — AI agents at the poker table.",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        None,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the HAB version and exit.",
    ),
) -> None:
    """HoldemAgentBench — AI agents at the poker table."""


@app.command("init")
def init():
    """Set up ~/.hab/config.yaml."""
    init_command()


app.command("run")(run_command)
app.command("export-run")(export_command)
app.command("replay")(replay_command)


@app.command("version")
def version():
    """Print the HAB version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
