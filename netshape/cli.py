"""NetShape CLI entry point."""

from typing import Optional

import typer
from rich.console import Console

from netshape import __version__

app = typer.Typer(
    name="netshape",
    help="Network throttling in one command.",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()


def version_callback(value: bool) -> None:
    if value:
        console.print(f"netshape {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Optional[bool] = typer.Option(
        None, "--version", "-v", help="Show version and exit.", callback=version_callback,
        is_eager=True,
    ),
) -> None:
    """Network throttling in one command."""
