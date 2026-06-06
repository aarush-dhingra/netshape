"""Command-line interface for NetShape."""

from __future__ import annotations

import json
import logging
import logging.handlers
import time
from pathlib import Path
from typing import Optional

import typer

from . import __version__
from .core import (
    SessionError,
    SessionState,
    add_rule,
    adjust_session,
    get_metrics,
    get_metrics_prometheus,
    get_scenario_status,
    get_status,
    is_dashboard_enabled,
    is_first_run,
    list_rules,
    load_config,
    remove_rule,
    save_config,
    toggle_rule,
    run_session,
    start_scenario_on_session,
    stop_scenario_on_session,
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
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        callback=version_callback,
        is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    """Run apps through a local throttling proxy."""
    # On first ever run (no config file yet), automatically launch the setup
    # wizard so the user can choose features before anything else happens.
    # Skip for `netshape setup` itself to avoid running it twice.
    if is_first_run() and ctx.invoked_subcommand != "setup":
        _run_first_time_wizard()


def _run_first_time_wizard() -> None:
    """Shared wizard body — called both on first run and from `netshape setup`."""
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Prompt
    from rich import box
    from rich.table import Table
    from rich.align import Align

    console = Console(stderr=True)
    is_first = is_first_run()

    # ── Welcome banner ────────────────────────────────────────────────────────
    if is_first:
        title_text = (
            "[bold cyan]Welcome to NetShape[/bold cyan]  [dim]v{}[/dim]\n\n"
            "[white]Looks like this is your [bold green]first run[/bold green].\n"
            "Quick setup — takes [bold green]30 seconds[/bold green].[/white]"
        ).format(__version__)
    else:
        title_text = (
            "[bold cyan]NetShape Setup[/bold cyan]  [dim]v{}[/dim]\n\n"
            "[white]Update your preferences.[/white]"
        ).format(__version__)

    console.print()
    console.print(Panel.fit(
        title_text,
        box=box.DOUBLE,
        border_style="cyan",
        padding=(1, 4),
    ))
    console.print()

    # ── Feature selection ─────────────────────────────────────────────────────
    console.print("[bold]  Which features do you want?[/bold]\n")

    features_table = Table(box=box.ROUNDED, show_header=False, padding=(0, 2), border_style="dim")
    features_table.add_column(justify="center", style="bold cyan", width=4)
    features_table.add_column(style="bold white")
    features_table.add_column(style="dim")
    features_table.add_row("1", "Core CLI only",
                           "Terminal commands, no browser UI  (smallest install)")
    features_table.add_row("2", "CLI + Web Dashboard",
                           "Visual controls, live graphs, scenario builder")
    console.print(Align.center(features_table))
    console.print()

    choice = ""
    while choice not in ("1", "2"):
        choice = Prompt.ask(
            "  [cyan]›[/cyan] Enter your choice",
            choices=["1", "2"],
            default="2",
            show_choices=False,
            show_default=True,
        )

    dashboard_enabled = choice == "2"
    console.print()

    # ── Default profile ───────────────────────────────────────────────────────
    console.print("[bold]  Default throttle profile[/bold]  "
                  "[dim](used when you don't specify --profile)[/dim]\n")

    profile_table = Table(box=box.ROUNDED, show_header=False, padding=(0, 2), border_style="dim")
    profile_table.add_column(justify="center", style="bold cyan", width=4)
    profile_table.add_column(style="bold white", width=12)
    profile_table.add_column(style="dim")
    profile_rows = [
        ("1", "none",      "No throttling — full speed"),
        ("2", "3g",        "780 kbps · 200ms · 1% loss  (default)"),
        ("3", "4g",        "4 Mbps · 80ms · 0.3% loss"),
        ("4", "wifi",      "30 Mbps · 25ms · 0.1% loss"),
        ("5", "satellite", "12 Mbps · 650ms · 0.5% loss  (high latency)"),
        ("6", "congested", "1.5 Mbps · 180ms · 2.5% loss  (busy network)"),
    ]
    for row in profile_rows:
        profile_table.add_row(*row)
    console.print(Align.center(profile_table))
    console.print()

    profile_map = {"1": None, "2": "3g", "3": "4g", "4": "wifi", "5": "satellite", "6": "congested"}
    pchoice = ""
    while pchoice not in profile_map:
        pchoice = Prompt.ask(
            "  [cyan]›[/cyan] Enter your choice",
            choices=list(profile_map.keys()),
            default="2",
            show_choices=False,
            show_default=True,
        )
    default_profile = profile_map[pchoice]
    console.print()

    # ── Save ──────────────────────────────────────────────────────────────────
    cfg = load_config()
    cfg["dashboard"] = dashboard_enabled
    cfg["default_profile"] = default_profile
    save_config(cfg)

    summary = Table(box=box.ROUNDED, show_header=False, border_style="green", padding=(0, 2))
    summary.add_column(style="dim")
    summary.add_column(style="bold white")
    summary.add_row("Web Dashboard",
                    "[green]Enabled[/green]" if dashboard_enabled else "[yellow]Disabled[/yellow]")
    summary.add_row("Default profile",
                    default_profile if default_profile else "[dim]none[/dim]")
    summary.add_row("Config saved to", "[dim]~/.netshape/config.json[/dim]")

    console.print(Panel(
        Align.center(summary),
        title="[bold green] Setup complete [/bold green]",
        border_style="green",
        box=box.ROUNDED,
        padding=(1, 2),
    ))
    console.print()

    # ── Next steps (only shown from setup command, not auto-triggered) ────────
    if not is_first:
        profile_flag = f"--profile {default_profile}" if default_profile else ""
        console.print("[bold]  Next steps[/bold]\n")
        console.print(f"  [cyan]›[/cyan]  [white]netshape run {profile_flag} -- your-app[/white]")
        if dashboard_enabled:
            url = "http://127.0.0.1:8091/dashboard"
            console.print(
                f"  [cyan]›[/cyan]  Then open  "
                f"\033]8;;{url}\033\\[underline cyan]{url}[/underline cyan]\033]8;;\033\\"
            )
        console.print()
        console.print("  Run [cyan]netshape setup[/cyan] at any time to change these settings.\n")
    else:
        console.print(
            "  [dim]You can change these at any time with[/dim] "
            "[cyan]netshape setup[/cyan]\n"
        )


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
    log_file: Optional[Path] = typer.Option(None, "--log-file", help="Write JSON log lines to this file (rotating, 10 MB)."),
) -> None:
    """Launch a command with HTTP_PROXY and HTTPS_PROXY pointing at NetShape."""

    if log_file is not None:
        _setup_json_log_file(log_file)

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
            on_ready=_print_startup_banner,
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
def status(
    json_output: bool = typer.Option(False, "--json", help="Print raw JSON status."),
    watch: bool = typer.Option(False, "--watch", "-w", help="Refresh status every second (Ctrl-C to stop)."),
) -> None:
    """Show the active NetShape session status."""

    if watch:
        _watch_status()
        return

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


@app.command("setup")
def setup_command() -> None:
    """Interactive setup wizard — configure NetShape preferences."""
    _run_first_time_wizard()


@app.command("profiles")
def profiles_command() -> None:
    """List built-in network profiles."""

    for profile in list_builtin_profiles():
        typer.echo(
            f"{profile.name}: "
            f"{_format_bps(profile.bandwidth_bps)}, "
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
        flag = "[on] " if rule.get("enabled", True) else "[off]"
        parts = [f"{flag} {rule['id'][:8]}  pattern={rule['pattern']!r}"]
        if rule.get("bandwidth_bps") is not None:
            parts.append(f"bw={_format_bps(int(rule['bandwidth_bps']))}")
        if rule.get("latency_ms") is not None:
            parts.append(f"lat={rule['latency_ms']}ms")
        if rule.get("loss_pct") is not None:
            parts.append(f"loss={rule['loss_pct'] * 100:.1f}%")
        if rule.get("jitter_ms") is not None:
            parts.append(f"jitter={rule['jitter_ms']}ms")
        if rule.get("comment"):
            parts.append(f"({rule['comment']})")
        typer.echo("  ".join(parts))


@rule_app.command("enable")
def rule_enable(
    rule_id: str = typer.Argument(help="Rule id prefix or comment/name."),
) -> None:
    """Enable a per-endpoint throttle rule."""
    try:
        rule = toggle_rule(rule_id, enabled=True)
        label = rule.get("comment") or rule.get("id", "")[:8]
        typer.echo(f"Rule enabled: {label}")
    except SessionError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc


@rule_app.command("disable")
def rule_disable(
    rule_id: str = typer.Argument(help="Rule id prefix or comment/name."),
) -> None:
    """Disable a per-endpoint throttle rule (keeps it saved, just inactive)."""
    try:
        rule = toggle_rule(rule_id, enabled=False)
        label = rule.get("comment") or rule.get("id", "")[:8]
        typer.echo(f"Rule disabled: {label}")
    except SessionError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc


scenario_app = typer.Typer(help="Run and manage network condition scenarios.", no_args_is_help=True)
app.add_typer(scenario_app, name="scenario")


@scenario_app.command("run")
def scenario_run(
    scenario_file: Optional[Path] = typer.Argument(None, help="Path to a .yaml/.json scenario file."),
    builtin: Optional[str] = typer.Option(
        None, "--builtin", "-b",
        help="Run a built-in or saved scenario by name.",
    ),
    no_wait: bool = typer.Option(False, "--no-wait", help="Submit scenario and return immediately."),
) -> None:
    """Run a scenario against the active proxy session.

    Examples:\n
      netshape scenario run --builtin subway\n
      netshape scenario run --builtin my-custom-scenario\n
      netshape scenario run ./my-scenario.yaml
    """
    try:
        if builtin:
            # The server resolves built-in first, then falls back to user-saved scenarios.
            scenario_dict: dict = {"builtin": builtin}
        elif scenario_file:
            from .scenario import ScenarioError, load_scenario
            try:
                scenario = load_scenario(scenario_file)
            except ScenarioError as exc:
                typer.echo(f"Error: {exc}", err=True)
                raise typer.Exit(1) from exc
            scenario_dict = scenario.to_dict()
        else:
            typer.echo("Error: provide a scenario file or --builtin <name>", err=True)
            raise typer.Exit(1)

        start_scenario_on_session(scenario_dict)
        typer.echo(f"Scenario started.")

        if no_wait:
            return

        typer.echo("Press Ctrl-C to stop early.\n")
        try:
            while True:
                st = get_scenario_status()
                if not st.get("running"):
                    typer.echo("Scenario completed.")
                    break
                pct = 0.0
                if st.get("phase_duration_s", 0) > 0:
                    pct = st.get("phase_elapsed_s", 0) / st["phase_duration_s"] * 100
                typer.echo(
                    f"  Phase {st.get('current_phase')}/{st.get('total_phases')}  "
                    f"{st.get('phase_name', '?')!r}  "
                    f"{st.get('phase_elapsed_s', 0):.0f}s / {st.get('phase_duration_s', 0):.0f}s "
                    f"({pct:.0f}%)",
                    nl=True,
                )
                time.sleep(1)
        except KeyboardInterrupt:
            typer.echo("\nStopping scenario…")
            stop_scenario_on_session()
            typer.echo("Scenario stopped and pre-scenario config restored.")
    except SessionError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc


@scenario_app.command("stop")
def scenario_stop() -> None:
    """Stop the running scenario and restore the pre-scenario configuration."""
    try:
        stop_scenario_on_session()
    except SessionError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo("Scenario stopped.")


@scenario_app.command("status")
def scenario_status(json_output: bool = typer.Option(False, "--json")) -> None:
    """Show the current scenario execution status."""
    try:
        st = get_scenario_status()
    except SessionError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    if json_output:
        typer.echo(json.dumps(st, indent=2))
        return
    if not st.get("running"):
        typer.echo("No scenario running.")
        return
    typer.echo(f"Scenario: {st.get('name')!r}")
    typer.echo(f"Phase {st.get('current_phase')}/{st.get('total_phases')}: {st.get('phase_name')!r}")
    typer.echo(f"  {st.get('phase_elapsed_s', 0):.1f}s / {st.get('phase_duration_s', 0):.1f}s")


@scenario_app.command("list")
def scenario_list() -> None:
    """List available built-in and user-saved scenarios."""
    from .scenario import list_builtin_scenarios, list_user_scenarios

    builtin = list_builtin_scenarios()
    user = list_user_scenarios()

    if not builtin and not user:
        typer.echo("No scenarios found. Make sure pyyaml is installed.")
        return

    if builtin:
        typer.echo("Built-in:")
        for name in builtin:
            typer.echo(f"  {name}")

    if user:
        typer.echo("Saved:")
        for name in user:
            typer.echo(f"  {name}")


# ── Metrics command ───────────────────────────────────────────────────────────

@app.command("metrics")
def metrics_command(
    prometheus: bool = typer.Option(False, "--prometheus", "-p", help="Output Prometheus text format."),
) -> None:
    """Show proxy metrics (requests, bytes, throttle, drops, latency)."""
    try:
        if prometheus:
            typer.echo(get_metrics_prometheus(), nl=False)
        else:
            data = get_metrics()
            for key, val in data.items():
                typer.echo(f"{key}: {val}")
    except SessionError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc


# ── Live watch ────────────────────────────────────────────────────────────────

def _watch_status() -> None:
    from rich.console import Console
    from rich.live import Live
    from rich.table import Table

    console = Console()
    with Live(console=console, refresh_per_second=2) as live:
        try:
            while True:
                try:
                    payload = get_status()
                except (SessionError, OSError):
                    # OSError covers ConnectionRefusedError when the proxy exits
                    # unexpectedly while we are watching.
                    live.update("[red]No active session (or proxy unreachable)[/red]")
                    time.sleep(1)
                    continue
                live.update(_make_watch_table(payload))
                time.sleep(1)
        except KeyboardInterrupt:
            pass


def _make_watch_table(payload: dict) -> "Table":
    from rich import box
    from rich.table import Table

    active = payload.get("active", False)
    title = "[green]NetShape — Active[/green]" if active else "[red]NetShape — Inactive[/red]"
    t = Table(title=title, box=box.ROUNDED, show_header=False, expand=True)
    t.add_column(style="bold cyan", min_width=28)
    t.add_column()

    if not active:
        return t

    t.add_row("Bandwidth", _format_bps(int(payload.get("bandwidth_bps", 0) or 0)))
    t.add_row("Latency", f"{payload.get('latency_ms', 0)} ms")
    t.add_row("Loss", f"{float(payload.get('loss_pct', 0)) * 100:g}%")
    t.add_row("Jitter", f"{payload.get('jitter_ms', 0)} ms")
    t.add_section()
    t.add_row("Proxy port", str(payload.get("traffic_port", "?")))
    t.add_row("PID", str(payload.get("pid", "?")))
    t.add_row("Uptime", f"{payload.get('running_for_seconds', 0):.0f}s")
    t.add_section()
    t.add_row("Requests handled", str(payload.get("requests_handled", 0)))
    t.add_row("Connections total", str(payload.get("connections_total", 0)))
    t.add_row("Connections active", str(payload.get("connections_active", 0)))
    t.add_row("Drops (loss)", str(payload.get("drops_total", 0)))
    t.add_row("Bytes sent", f"{payload.get('bytes_sent', 0):,}")
    t.add_row("Bytes received", f"{payload.get('bytes_received', 0):,}")
    t.add_row("Active rules", str(payload.get("rules_count", 0)))
    if payload.get("scenario_running"):
        t.add_section()
        t.add_row("Scenario", "[yellow]Running[/yellow]")
    if warning := payload.get("warning"):
        t.add_section()
        t.add_row("[yellow]Warning[/yellow]", warning)
    return t


# ── Startup banner ───────────────────────────────────────────────────────────

def _print_startup_banner(state: "SessionState") -> None:
    """Print a rich startup banner after the proxy is ready."""
    from rich.console import Console
    from rich.panel import Panel
    from rich import box

    console = Console(stderr=True)

    dashboard_url = f"http://127.0.0.1:{state.control_port}/dashboard"
    clickable = f"\033]8;;{dashboard_url}\033\\{dashboard_url}\033]8;;\033\\"

    profile_str = state.profile or "custom"
    bw_str = _format_bps(int(state.bandwidth_bps or 0))
    lat_str = f"{state.latency_ms} ms"
    loss_str = f"{float(state.loss_pct or 0) * 100:g}%"
    jitter_str = f"{state.jitter_ms} ms"

    lines = [
        f"[bold green] NetShape is active[/bold green]  [dim]·[/dim]  "
        f"[cyan]{profile_str}[/cyan]",
        "",
        f"  [dim]Bandwidth[/dim]  [white]{bw_str}[/white]  "
        f"[dim]Latency[/dim]  [white]{lat_str}[/white]  "
        f"[dim]Loss[/dim]  [white]{loss_str}[/white]  "
        f"[dim]Jitter[/dim]  [white]{jitter_str}[/white]",
        "",
    ]

    if is_dashboard_enabled():
        lines.append(
            f"  [dim]Dashboard[/dim]  [underline cyan]{clickable}[/underline cyan]"
        )
        lines.append(
            f"  [dim]Adjust   [/dim]  [white]netshape adjust --profile 2g[/white]"
        )
    else:
        lines.append(
            "  [dim]Dashboard[/dim]  [yellow]disabled[/yellow]  "
            "[dim](run netshape setup to enable)[/dim]"
        )
        lines.append(
            f"  [dim]Adjust   [/dim]  [white]netshape adjust --profile 2g[/white]"
        )

    lines.append("")
    lines.append("  [dim]Press Ctrl-C to stop.[/dim]")

    console.print(
        Panel(
            "\n".join(lines),
            box=box.ROUNDED,
            border_style="cyan",
            padding=(0, 1),
        )
    )
    console.print()


# ── Log file setup ────────────────────────────────────────────────────────────

class _JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "name": record.name,
            "msg": record.getMessage(),
        })


def _setup_json_log_file(path: Path) -> None:
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(_JsonLogFormatter())
    root = logging.getLogger("netshape")
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)


def _format_bps(bps: int) -> str:
    """Return a human-readable bandwidth string."""
    if bps == 0:
        return "Unlimited"
    if bps >= 1_000_000:
        val = bps / 1_000_000
        return f"{val:.1f} Mbps" if val != int(val) else f"{int(val)} Mbps"
    if bps >= 1_000:
        val = bps / 1_000
        return f"{val:.1f} kbps" if val != int(val) else f"{int(val)} kbps"
    return f"{bps} bps"


def _format_config(config: dict[str, object]) -> str:
    profile = config.get("profile") or "custom"
    bw = int(config.get("bandwidth_bps", 0) or 0)
    return (
        f"Profile: {profile}\n"
        f"Bandwidth: {_format_bps(bw)}\n"
        f"Latency: {config.get('latency_ms', 0)} ms\n"
        f"Loss: {float(config.get('loss_pct', 0.0)) * 100:g}%\n"
        f"Jitter: {config.get('jitter_ms', 0)} ms"
    )
