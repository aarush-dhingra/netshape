from __future__ import annotations

from typer.testing import CliRunner

from netshape import cli

runner = CliRunner()


def test_version_command() -> None:
    result = runner.invoke(cli.app, ["--version"])

    assert result.exit_code == 0
    assert "netshape 0.1.0" in result.output


def test_run_command_delegates_to_core(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_session(**kwargs):
        captured.update(kwargs)
        return 7

    monkeypatch.setattr(cli, "run_session", fake_run_session)

    result = runner.invoke(
        cli.app,
        ["run", "--profile", "3g", "--latency", "100ms", "--", "python", "app.py"],
    )

    assert result.exit_code == 7
    assert captured["profile"] == "3g"
    assert captured["latency"] == "100ms"
    assert captured["command"] == ["python", "app.py"]


def test_run_command_requires_command() -> None:
    result = runner.invoke(cli.app, ["run", "--profile", "3g"])

    assert result.exit_code != 0
    assert "command is required" in result.output


def test_adjust_command_delegates_to_core(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_adjust_session(**kwargs):
        captured.update(kwargs)
        return {
            "profile": "custom",
            "bandwidth_bps": 100_000,
            "latency_ms": 200,
            "loss_pct": 0.01,
            "jitter_ms": 20,
        }

    monkeypatch.setattr(cli, "adjust_session", fake_adjust_session)

    result = runner.invoke(cli.app, ["adjust", "--bandwidth", "100kbps"])

    assert result.exit_code == 0
    assert captured["bandwidth"] == "100kbps"
    assert "Bandwidth: 100000 bps" in result.output


def test_status_command_prints_inactive(monkeypatch) -> None:
    monkeypatch.setattr(cli, "get_status", lambda: {"active": False})

    result = runner.invoke(cli.app, ["status"])

    assert result.exit_code == 0
    assert "Status: inactive" in result.output


def test_status_command_prints_json(monkeypatch) -> None:
    monkeypatch.setattr(cli, "get_status", lambda: {"active": False})

    result = runner.invoke(cli.app, ["status", "--json"])

    assert result.exit_code == 0
    assert '"active": false' in result.output


def test_stop_command_delegates_to_core(monkeypatch) -> None:
    called = False

    def fake_stop_session() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(cli, "stop_session", fake_stop_session)

    result = runner.invoke(cli.app, ["stop"])

    assert result.exit_code == 0
    assert called is True
    assert "Stopped" in result.output


def test_profiles_command_lists_builtins() -> None:
    result = runner.invoke(cli.app, ["profiles"])

    assert result.exit_code == 0
    assert "3g:" in result.output
    assert "fiber:" in result.output
