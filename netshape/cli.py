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
    add_rule,
    adjust_session,
    get_metrics,
    get_metrics_prometheus,
    get_scenario_status,
    get_status,
    list_rules,
    remove_rule,
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


scenario_app = typer.Typer(help="Run and manage network condition scenarios.", no_args_is_help=True)
app.add_typer(scenario_app, name="scenario")


@scenario_app.command("run")
def scenario_run(
    scenario_file: Optional[Path] = typer.Argument(None, help="Path to a .yaml scenario file."),
    builtin: Optional[str] = typer.Option(None, "--builtin", "-b", help="Run a built-in scenario by name."),
    no_wait: bool = typer.Option(False, "--no-wait", help="Submit scenario and return immediately."),
) -> None:
    """Run a scenario against the active proxy session.

    Examples:\n
      netshape scenario run --builtin subway\n
      netshape scenario run ./my-scenario.yaml
    """
    try:
        if builtin:
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
    """List available built-in scenarios."""
    from .scenario import list_builtin_scenarios
    names = list_builtin_scenarios()
    if not names:
        typer.echo("No built-in scenarios found. Make sure pyyaml is installed.")
        return
    for name in names:
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

    bw = payload.get("bandwidth_bps", 0)
    bw_str = "Unlimited" if bw == 0 else f"{bw:,} bps"
    t.add_row("Bandwidth", bw_str)
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


def _format_config(config: dict[str, object]) -> str:
    profile = config.get("profile") or "custom"
    return (
        f"Profile: {profile}\n"
        f"Bandwidth: {config.get('bandwidth_bps', 0)} bps\n"
        f"Latency: {config.get('latency_ms', 0)} ms\n"
        f"Loss: {float(config.get('loss_pct', 0.0)) * 100:g}%\n"
        f"Jitter: {config.get('jitter_ms', 0)} ms"
    )
