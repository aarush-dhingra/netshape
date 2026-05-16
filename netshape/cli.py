"""NetShape CLI entry point."""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from netshape import __version__

app = typer.Typer(
    name="netshape",
    help="Network throttling in one command.",
    add_completion=False,
    no_args_is_help=True,
)
profile_app = typer.Typer(help="Manage custom profiles.")
app.add_typer(profile_app, name="profile")

console = Console()
err_console = Console(stderr=True)


def version_callback(value: bool) -> None:
    if value:
        console.print(f"netshape {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Optional[bool] = typer.Option(
        None, "--version", "-v", help="Show version and exit.",
        callback=version_callback, is_eager=True,
    ),
) -> None:
    """Network throttling in one command."""


@app.command()
def start(
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Named profile (e.g. 3g, 4g, slow-wifi)."),
    bandwidth: Optional[str] = typer.Option(None, "--bandwidth", "-b", help="Bandwidth limit (e.g. 500kbps, 1mbps)."),
    latency: Optional[str] = typer.Option(None, "--latency", "-l", help="Latency in ms (e.g. 200ms, 0.2s)."),
    loss: Optional[str] = typer.Option(None, "--loss", help="Packet loss (e.g. 2%, 0.02)."),
    jitter: Optional[str] = typer.Option(None, "--jitter", "-j", help="Jitter in ms (e.g. 30ms)."),
    force: bool = typer.Option(False, "--force", "-f", help="Override active throttle session."),
    interface: Optional[str] = typer.Option(None, "--interface", "-i", help="Network interface to throttle."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show commands without executing."),
    verbose: bool = typer.Option(False, "--verbose", help="Print detailed subprocess output."),
) -> None:
    """Start network throttling."""
    from netshape import core
    from netshape.platforms.windows import WindowsBackend

    try:
        resolved, rules = core.start(
            profile=profile,
            bandwidth=bandwidth,
            latency=latency,
            loss=loss,
            jitter=jitter,
            force=force,
            interface=interface,
            dry_run=dry_run,
        )
    except core.PrivilegeError as e:
        err_console.print(f"[bold red]Error:[/] {e}")
        raise typer.Exit(1)
    except core.AlreadyActiveError as e:
        err_console.print(f"[bold yellow]Warning:[/] {e}")
        raise typer.Exit(1)
    except (core.NetShapeError, Exception) as e:
        err_console.print(f"[bold red]Error:[/] {e}")
        raise typer.Exit(1)

    if dry_run:
        console.print("[bold cyan]Dry run[/] — no rules applied.")
        for rule in rules:
            console.print(f"  {rule}")
        raise typer.Exit(0)

    # Show unsupported parameter warnings on Windows
    backend = core.get_backend()
    if isinstance(backend, WindowsBackend) and backend.unsupported_warnings:
        warnings = ", ".join(backend.unsupported_warnings)
        console.print(
            f"[bold yellow]Note:[/] Windows v1 only supports bandwidth throttling. "
            f"Ignored: {warnings}."
        )

    console.print(f"[bold green]Throttle active[/]", end="")
    if resolved.name:
        console.print(f" (profile: [bold]{resolved.name}[/])")
    else:
        console.print()

    _print_params(resolved.bandwidth_bps, resolved.latency_ms, resolved.loss_pct, resolved.jitter_ms)

    if verbose:
        console.print("\n[dim]Rules applied:[/dim]")
        for rule in rules:
            console.print(f"  [dim]{rule}[/dim]")


@app.command()
def stop(
    verbose: bool = typer.Option(False, "--verbose", help="Print detailed output."),
) -> None:
    """Stop network throttling and restore normal network."""
    from netshape import core
    core.stop()
    console.print("[bold green]Throttle stopped.[/] Network restored to normal.")


@app.command()
def status() -> None:
    """Show current throttling state."""
    from netshape import core

    result = core.status()

    if not result.active:
        console.print("Status: [bold green]NORMAL[/] (no throttle active)")
        return

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold")
    table.add_column()

    table.add_row("Status", "[bold red]THROTTLED[/]")
    if result.profile:
        table.add_row("Profile", result.profile)

    from netshape.speed_test import format_speed_bytes
    table.add_row("Bandwidth", format_speed_bytes(result.bandwidth_bps))
    table.add_row("Latency", f"{result.latency_ms}ms")
    table.add_row("Packet loss", f"{result.loss_pct * 100:.1f}%")
    table.add_row("Jitter", f"{result.jitter_ms}ms")

    if result.started_at:
        table.add_row("Started at", result.started_at)

    if result.stale:
        table.add_row(
            "Warning",
            "[bold yellow]Session appears stale (process may have crashed). Run 'netshape cleanup'.[/]",
        )

    console.print(table)


@app.command()
def test(
    endpoint: Optional[str] = typer.Option(None, "--endpoint", help="Custom speed test endpoint URL."),
) -> None:
    """Run a speed test to verify throttling is working."""
    from netshape import core
    from netshape.speed_test import format_speed, format_speed_bytes, run_speed_test

    st = core.status()

    with console.status("Running speed test..."):
        result = run_speed_test(
            endpoint=endpoint,
            active_profile=st.profile if st.active else None,
            is_throttled=st.active,
        )

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold")
    table.add_column()

    if result.is_throttled:
        table.add_row("Network Status", f"[bold red]THROTTLED ({result.profile_name or 'custom'})[/]")
    else:
        table.add_row("Network Status", "[bold green]NORMAL[/]")

    table.add_row("Download speed", f"{format_speed_bytes(result.download_speed_bps)} ({format_speed(result.download_speed_bps)})")
    table.add_row("Latency", f"{result.latency_ms:.0f} ms" if result.latency_ms >= 0 else "N/A")
    table.add_row("Packet loss", f"{result.packet_loss_pct:.1f}%")

    if result.is_throttled and st.active:
        expected_bw = format_speed_bytes(st.bandwidth_bps)
        table.add_row("Expected", f"~{expected_bw}, {st.latency_ms}ms latency")

    console.print(table)


@app.command(name="cleanup")
def cleanup_cmd(
    verbose: bool = typer.Option(False, "--verbose", help="Print detailed output."),
) -> None:
    """Emergency cleanup: remove lingering throttle rules."""
    from netshape import core

    count = core.cleanup()
    if count > 0:
        console.print(f"[bold green]Cleaned up {count} lingering network rule(s).[/] Network restored to normal.")
    else:
        console.print("[green]No lingering rules found.[/] Network is clean.")


@app.command(name="profiles")
def list_profiles() -> None:
    """List all available profiles (built-in + custom)."""
    from netshape.profiles import list_builtin, list_custom, resolve_profile
    from netshape.speed_test import format_speed_bytes

    builtins = list_builtin()
    custom = list_custom()

    table = Table(title="Built-in Profiles")
    table.add_column("Name", style="bold cyan")
    table.add_column("Bandwidth")
    table.add_column("Latency")
    table.add_column("Loss")
    table.add_column("Jitter")
    table.add_column("Description", style="dim")

    for name in sorted(builtins.keys()):
        p = resolve_profile(profile_name=name)
        table.add_row(
            name,
            format_speed_bytes(p.bandwidth_bps),
            f"{p.latency_ms}ms",
            f"{p.loss_pct * 100:.1f}%",
            f"{p.jitter_ms}ms",
            p.description,
        )

    console.print(table)

    if custom:
        custom_table = Table(title="Custom Profiles")
        custom_table.add_column("Name", style="bold green")
        custom_table.add_column("Bandwidth")
        custom_table.add_column("Latency")
        custom_table.add_column("Loss")
        custom_table.add_column("Jitter")
        custom_table.add_column("Description", style="dim")

        for name in sorted(custom.keys()):
            p = resolve_profile(profile_name=name)
            custom_table.add_row(
                name,
                format_speed_bytes(p.bandwidth_bps),
                f"{p.latency_ms}ms",
                f"{p.loss_pct * 100:.1f}%",
                f"{p.jitter_ms}ms",
                p.description,
            )
        console.print(custom_table)


# --- Profile subcommands ---

@profile_app.command(name="save")
def profile_save(
    name: str = typer.Argument(help="Profile name (alphanumeric + hyphens)."),
    bandwidth: str = typer.Option(..., "--bandwidth", "-b", help="Bandwidth (e.g. 800kbps)."),
    latency: str = typer.Option(..., "--latency", "-l", help="Latency (e.g. 120ms)."),
    loss: str = typer.Option("0%", "--loss", help="Packet loss (e.g. 1%)."),
    jitter: str = typer.Option("0ms", "--jitter", "-j", help="Jitter (e.g. 20ms)."),
    description: str = typer.Option("", "--description", "-d", help="Profile description."),
) -> None:
    """Save a custom named profile."""
    from netshape.profiles import ProfileError, save_custom

    try:
        path = save_custom(name, bandwidth, latency, loss, jitter, description)
        console.print(f"[bold green]Profile saved:[/] {name} -> {path}")
    except ProfileError as e:
        err_console.print(f"[bold red]Error:[/] {e}")
        raise typer.Exit(1)


@profile_app.command(name="delete")
def profile_delete(
    name: str = typer.Argument(help="Profile name to delete."),
) -> None:
    """Delete a custom profile."""
    from netshape.profiles import ProfileError, delete_custom

    try:
        delete_custom(name)
        console.print(f"[bold green]Profile deleted:[/] {name}")
    except ProfileError as e:
        err_console.print(f"[bold red]Error:[/] {e}")
        raise typer.Exit(1)


@profile_app.command(name="export")
def profile_export(
    name: str = typer.Argument(help="Profile name to export."),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output file path."),
) -> None:
    """Export a profile to JSON."""
    import json

    from netshape.profiles import ProfileError, list_all

    all_profiles = list_all()
    if name not in all_profiles:
        err_console.print(f"[bold red]Error:[/] Profile '{name}' not found.")
        raise typer.Exit(1)

    data = {"name": name, **all_profiles[name]}
    json_str = json.dumps(data, indent=2)

    if output:
        with open(output, "w") as f:
            f.write(json_str)
        console.print(f"[bold green]Exported:[/] {name} -> {output}")
    else:
        console.print(json_str)


@profile_app.command(name="import")
def profile_import(
    source: str = typer.Argument(help="Path or URL to a profile JSON file."),
) -> None:
    """Import a profile from a JSON file or URL."""
    import json

    from netshape.profiles import ProfileError, save_custom

    try:
        if source.startswith("http://") or source.startswith("https://"):
            import urllib.request
            req = urllib.request.Request(source, headers={"User-Agent": "netshape"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        else:
            with open(source) as f:
                data = json.load(f)

        name = data.get("name", "")
        if not name:
            err_console.print("[bold red]Error:[/] Profile JSON must include a 'name' field.")
            raise typer.Exit(1)

        save_custom(
            name=name,
            bandwidth=data["bandwidth"],
            latency=data["latency"],
            loss=data.get("loss", "0%"),
            jitter=data.get("jitter", "0ms"),
            description=data.get("description", ""),
        )
        console.print(f"[bold green]Imported:[/] {name}")

    except (json.JSONDecodeError, KeyError) as e:
        err_console.print(f"[bold red]Error:[/] Invalid profile JSON: {e}")
        raise typer.Exit(1)
    except ProfileError as e:
        err_console.print(f"[bold red]Error:[/] {e}")
        raise typer.Exit(1)


def _print_params(bandwidth_bps: int, latency_ms: int, loss_pct: float, jitter_ms: int) -> None:
    from netshape.speed_test import format_speed_bytes
    console.print(f"  Bandwidth: {format_speed_bytes(bandwidth_bps)}")
    console.print(f"  Latency:   {latency_ms}ms")
    console.print(f"  Loss:      {loss_pct * 100:.1f}%")
    console.print(f"  Jitter:    {jitter_ms}ms")
