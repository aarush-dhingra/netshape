"""Command-line interface for NetShape."""

from __future__ import annotations

import json
from typing import Optional

import typer

from . import __version__
from .core import (
    SessionError,
    add_rule,
    adjust_session,
    get_status,
    list_rules,
    remove_rule,
    run_session,
    stop_session,
)
from .profiles import ProfileError, list_builtin_profiles
from .speed_test import run_speed_test

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
    timeout: Optional[str] = typer.Option(None, "--timeout", "-t", help="Auto-stop after a duration, e.g. 30m."),
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
            timeout=timeout,
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
    if warning := payload.get("warning"):
        typer.echo(f"Warning: {warning}")


@app.command()
def stop() -> None:
    """Stop the currently running proxy session."""

    stop_session()
    typer.echo("Stopped")


@app.command("test")
def test_command(
    profile: Optional[str] = typer.Option("3g", "--profile", "-p", help="Profile to test."),
    bandwidth: Optional[str] = typer.Option(None, "--bandwidth", "-b", help="Bandwidth override."),
    latency: Optional[str] = typer.Option(None, "--latency", "-l", help="Latency override."),
    loss: Optional[str] = typer.Option(None, "--loss", help="Packet loss override."),
    jitter: Optional[str] = typer.Option(None, "--jitter", "-j", help="Jitter override."),
    bytes_count: int = typer.Option(64 * 1024, "--bytes", help="Payload size to download."),
) -> None:
    """Verify that traffic can flow through the NetShape proxy."""

    try:
        result = run_speed_test(
            profile=profile,
            bandwidth=bandwidth,
            latency=latency,
            loss=loss,
            jitter=jitter,
            byte_count=bytes_count,
        )
    except (ProfileError, SessionError, ValueError, OSError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Downloaded: {result.bytes_downloaded} bytes")
    typer.echo(f"Direct: {result.direct_seconds:.3f}s")
    typer.echo(f"Proxied: {result.proxied_seconds:.3f}s")
    typer.echo(f"Proxy requests: {result.requests_handled}")
    if result.proxy_detected:
        typer.echo("Result: proxy is handling traffic")
    else:
        typer.echo("Result: no proxy traffic detected")
        raise typer.Exit(1)


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


rule_app = typer.Typer(help="Manage per-endpoint throttle rules.", no_args_is_help=True)
app.add_typer(rule_app, name="rule")


@rule_app.command("add")
def rule_add(
    pattern: str = typer.Argument(..., help="Regex matched against the target host or URL."),
    bandwidth: Optional[str] = typer.Option(None, "--bandwidth", "-b", help="Bandwidth for this rule, e.g. 1mbps."),
    latency: Optional[str] = typer.Option(None, "--latency", "-l", help="Latency for this rule, e.g. 200ms."),
    loss: Optional[str] = typer.Option(None, "--loss", help="Packet loss for this rule, e.g. 5%."),
    jitter: Optional[str] = typer.Option(None, "--jitter", "-j", help="Jitter for this rule, e.g. 20ms."),
    comment: str = typer.Option("", "--comment", "-c", help="Human-readable label for this rule."),
) -> None:
    """Add a per-endpoint throttle rule to the running proxy session.

    Examples:\n
      netshape rule add stripe\\.com --bandwidth 1mbps --latency 200ms\n
      netshape rule add "api\\." --loss 5% --comment "flaky API"
    """
    try:
        rule = add_rule(
            pattern=pattern,
            bandwidth=bandwidth,
            latency=latency,
            loss=loss,
            jitter=jitter,
            comment=comment,
        )
    except (SessionError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Rule added: {rule['id'][:8]}  pattern={rule['pattern']!r}")
    if rule.get("comment"):
        typer.echo(f"  comment: {rule['comment']}")


@rule_app.command("remove")
def rule_remove(rule_id: str = typer.Argument(..., help="Rule id (or prefix) to remove.")) -> None:
    """Remove a throttle rule from the running proxy session."""
    try:
        remove_rule(rule_id)
    except SessionError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Rule {rule_id} removed.")


@rule_app.command("list")
def rule_list(json_output: bool = typer.Option(False, "--json", help="Print raw JSON.")) -> None:
    """List active throttle rules."""
    try:
        rules = list_rules()
    except SessionError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(rules, indent=2))
        return

    if not rules:
        typer.echo("No rules configured.")
        return

    for rule in rules:
        parts = [f"{rule['id'][:8]}  pattern={rule['pattern']!r}"]
        if rule.get("bandwidth_bps") is not None:
            parts.append(f"bw={rule['bandwidth_bps']}bps")
        if rule.get("latency_ms") is not None:
            parts.append(f"lat={rule['latency_ms']}ms")
        if rule.get("loss_pct") is not None:
            parts.append(f"loss={rule['loss_pct'] * 100:.1f}%")
        if rule.get("jitter_ms") is not None:
            parts.append(f"jitter={rule['jitter_ms']}ms")
        if rule.get("comment"):
            parts.append(f"({rule['comment']})")
        typer.echo("  ".join(parts))


def _format_config(config: dict[str, object]) -> str:
    profile = config.get("profile") or "custom"
    return (
        f"Profile: {profile}\n"
        f"Bandwidth: {config.get('bandwidth_bps', 0)} bps\n"
        f"Latency: {config.get('latency_ms', 0)} ms\n"
        f"Loss: {float(config.get('loss_pct', 0.0)) * 100:g}%\n"
        f"Jitter: {config.get('jitter_ms', 0)} ms"
    )
