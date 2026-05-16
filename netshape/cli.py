"""Command-line interface for NetShape."""

from __future__ import annotations

import json
from typing import Optional

import typer

from . import __version__
from .core import SessionError, adjust_session, get_status, run_session, stop_session
from .profiles import ProfileError, list_builtin_profiles

app = typer.Typer(
    help="Run apps through a local throttling proxy.",
    no_args_is_help=True,
)


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"netshape {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=version_callback,
        is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    """Run apps through a local throttling proxy."""


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def run(
    ctx: typer.Context,
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Built-in profile name."),
    bandwidth: Optional[str] = typer.Option(None, "--bandwidth", "-b", help="Bandwidth, e.g. 100kbps."),
    latency: Optional[str] = typer.Option(None, "--latency", "-l", help="Latency, e.g. 300ms."),
    loss: Optional[str] = typer.Option(None, "--loss", help="Packet loss, e.g. 2%."),
    jitter: Optional[str] = typer.Option(None, "--jitter", "-j", help="Jitter, e.g. 50ms."),
    traffic_port: int = typer.Option(8090, "--port", help="Traffic proxy port."),
) -> None:
    """Launch a command with HTTP_PROXY and HTTPS_PROXY pointing at NetShape."""

    command = list(ctx.args)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise typer.BadParameter("command is required after --")

    try:
        exit_code = run_session(
            command=command,
            profile=profile,
            bandwidth=bandwidth,
            latency=latency,
            loss=loss,
            jitter=jitter,
            traffic_port=traffic_port,
        )
    except (ProfileError, SessionError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    raise typer.Exit(exit_code)


@app.command()
def adjust(
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Switch to a built-in profile."),
    bandwidth: Optional[str] = typer.Option(None, "--bandwidth", "-b", help="Bandwidth, e.g. 100kbps."),
    latency: Optional[str] = typer.Option(None, "--latency", "-l", help="Latency, e.g. 300ms."),
    loss: Optional[str] = typer.Option(None, "--loss", help="Packet loss, e.g. 2%."),
    jitter: Optional[str] = typer.Option(None, "--jitter", "-j", help="Jitter, e.g. 50ms."),
) -> None:
    """Adjust the currently running proxy session."""

    try:
        config = adjust_session(
            profile=profile,
            bandwidth=bandwidth,
            latency=latency,
            loss=loss,
            jitter=jitter,
        )
    except (ProfileError, SessionError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(_format_config(config))


@app.command()
def status(json_output: bool = typer.Option(False, "--json", help="Print raw JSON status.")) -> None:
    """Show the active NetShape session status."""

    try:
        payload = get_status()
    except SessionError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    if not payload.get("active"):
        typer.echo("Status: inactive")
        return

    typer.echo("Status: active")
    typer.echo(_format_config(payload))
    typer.echo(f"Proxy: 127.0.0.1:{payload['traffic_port']}")
    typer.echo(f"PID: {payload['pid']}")


@app.command()
def stop() -> None:
    """Stop the currently running proxy session."""

    stop_session()
    typer.echo("Stopped")


@app.command("profiles")
def profiles_command() -> None:
    """List built-in network profiles."""

    for profile in list_builtin_profiles():
        typer.echo(
            f"{profile.name}: "
            f"{profile.bandwidth_bps}bps, "
            f"{profile.latency_ms}ms latency, "
            f"{profile.loss_pct * 100:g}% loss, "
            f"{profile.jitter_ms}ms jitter"
        )


def _format_config(config: dict[str, object]) -> str:
    profile = config.get("profile") or "custom"
    return (
        f"Profile: {profile}\n"
        f"Bandwidth: {config.get('bandwidth_bps', 0)} bps\n"
        f"Latency: {config.get('latency_ms', 0)} ms\n"
        f"Loss: {float(config.get('loss_pct', 0.0)) * 100:g}%\n"
        f"Jitter: {config.get('jitter_ms', 0)} ms"
    )
