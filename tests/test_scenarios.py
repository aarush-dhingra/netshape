"""Tests for the scenario scripting engine (Phase 4)."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest

from netshape.proxy_server import ThrottleConfig, ThrottledProxy
from netshape.scenario import (
    Phase,
    Scenario,
    ScenarioError,
    _parse_loss_value,
    _parse_phase,
    parse_scenario_dict,
)


# ── parse_scenario_dict unit tests ───────────────────────────────────────────

def test_parse_minimal_scenario():
    data = {
        "name": "Test",
        "phases": [
            {"name": "P1", "duration": "5s", "bandwidth": "1mbps", "latency": "100ms"},
        ],
    }
    scenario = parse_scenario_dict(data)
    assert scenario.name == "Test"
    assert len(scenario.phases) == 1
    p = scenario.phases[0]
    assert p.name == "P1"
    assert p.duration_ms == 5000
    assert p.bandwidth_bps == 1_000_000
    assert p.latency_ms == 100


def test_parse_scenario_missing_phases_raises():
    with pytest.raises(ScenarioError, match="at least one"):
        parse_scenario_dict({"name": "X", "phases": []})


def test_parse_scenario_no_phases_key_raises():
    with pytest.raises(ScenarioError):
        parse_scenario_dict({"name": "X"})


def test_parse_phase_missing_duration_raises():
    with pytest.raises(ScenarioError, match="missing 'duration'"):
        _parse_phase(0, {"name": "P", "bandwidth": "1mbps"})


def test_parse_phase_negative_duration_raises():
    with pytest.raises(ScenarioError, match="duration must be > 0"):
        _parse_phase(0, {"name": "P", "duration": "0s"})


def test_parse_phase_with_builtin_profile():
    # '4g' must be a recognised profile
    phase = _parse_phase(0, {"name": "4G", "duration": "10s", "profile": "4g"})
    assert phase.bandwidth_bps > 0
    assert phase.latency_ms >= 0


def test_parse_phase_with_unknown_profile_raises():
    with pytest.raises(ScenarioError, match="unknown profile"):
        _parse_phase(0, {"name": "X", "duration": "5s", "profile": "nonexistent_profile_xyz"})


def test_parse_phase_inline_profile_dict():
    phase = _parse_phase(0, {
        "name": "Custom",
        "duration": "5s",
        "profile": {
            "bandwidth_bps": 2_000_000,
            "latency_ms": 50,
            "loss_pct": 0.01,
        },
    })
    assert phase.bandwidth_bps == 2_000_000
    assert phase.latency_ms == 50
    assert phase.loss_pct == pytest.approx(0.01)


def test_parse_phase_phase_keys_override_profile():
    phase = _parse_phase(0, {
        "name": "Override",
        "duration": "5s",
        "profile": "edge",
        "bandwidth": "10mbps",  # override profile bandwidth
    })
    assert phase.bandwidth_bps == 10_000_000


def test_parse_loss_value_fraction():
    assert _parse_loss_value(0.05) == pytest.approx(0.05)


def test_parse_loss_value_string_pct():
    assert _parse_loss_value("5%") == pytest.approx(0.05)


def test_parse_loss_value_integer_pct():
    assert _parse_loss_value(5) == pytest.approx(0.05)


def test_parse_loss_value_none():
    assert _parse_loss_value(None) == 0.0


def test_scenario_total_duration():
    scenario = parse_scenario_dict({
        "name": "T",
        "phases": [
            {"name": "A", "duration": "10s", "bandwidth": "1mbps"},
            {"name": "B", "duration": "5s", "bandwidth": "500kbps"},
        ],
    })
    assert scenario.total_duration_ms() == 15_000


def test_scenario_to_dict_round_trips():
    scenario = parse_scenario_dict({
        "name": "RoundTrip",
        "description": "test",
        "phases": [{"name": "P1", "duration": "3s", "bandwidth": "2mbps"}],
    })
    d = scenario.to_dict()
    assert d["name"] == "RoundTrip"
    assert len(d["phases"]) == 1
    assert d["phases"][0]["duration_ms"] == 3000


# ── Integration tests against live proxy ─────────────────────────────────────

def _ctrl(port: int) -> tuple[str, int]:
    return "127.0.0.1", port


async def _post(port: int, path: str, body: Any) -> dict[str, Any]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    payload = json.dumps(body).encode("utf-8")
    writer.write(
        f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n"
        f"Content-Type: application/json\r\nContent-Length: {len(payload)}\r\n\r\n".encode()
        + payload
    )
    await writer.drain()
    data = await reader.read(4096)
    writer.close()
    body_start = data.index(b"\r\n\r\n") + 4
    return json.loads(data[body_start:])


async def _get(port: int, path: str) -> dict[str, Any]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".encode())
    await writer.drain()
    data = await reader.read(4096)
    writer.close()
    body_start = data.index(b"\r\n\r\n") + 4
    return json.loads(data[body_start:])


@pytest.mark.asyncio
async def test_scenario_api_idle_status():
    proxy = ThrottledProxy()
    await proxy.start()
    try:
        st = await _get(proxy.control_port, "/scenario/status")
        assert st["running"] is False
    finally:
        await proxy.close()


@pytest.mark.asyncio
async def test_scenario_start_and_status():
    proxy = ThrottledProxy()
    await proxy.start()
    try:
        scenario_dict = {
            "name": "Quick Test",
            "phases": [
                {"name": "Phase A", "duration": "2s", "bandwidth": "1mbps"},
                {"name": "Phase B", "duration": "2s", "bandwidth": "500kbps"},
            ],
        }
        resp = await _post(proxy.control_port, "/scenario/start", scenario_dict)
        assert resp.get("name") == "Quick Test"

        await asyncio.sleep(0.2)  # let scenario start
        st = await _get(proxy.control_port, "/scenario/status")
        assert st["running"] is True
        assert st["name"] == "Quick Test"
        assert st["total_phases"] == 2

        # Stop it early
        stop_resp = await _post(proxy.control_port, "/scenario/stop", {})
        assert stop_resp.get("running") is False
    finally:
        await proxy.close()


@pytest.mark.asyncio
async def test_scenario_restores_config_on_stop():
    proxy = ThrottledProxy(config=ThrottleConfig(bandwidth_bps=5_000_000, latency_ms=50))
    await proxy.start()
    try:
        original_bw = proxy.config.bandwidth_bps
        original_lat = proxy.config.latency_ms

        scenario_dict = {
            "name": "Override",
            "phases": [
                {"name": "Throttle", "duration": "5s", "bandwidth": "100kbps", "latency": "500ms"},
            ],
        }
        await _post(proxy.control_port, "/scenario/start", scenario_dict)
        await asyncio.sleep(0.3)  # wait for phase to be applied

        # Config should now be from the scenario
        assert proxy.config.bandwidth_bps == 100_000

        await _post(proxy.control_port, "/scenario/stop", {})
        await asyncio.sleep(0.5)  # wait for restore

        assert proxy.config.bandwidth_bps == original_bw
        assert proxy.config.latency_ms == original_lat
    finally:
        await proxy.close()


@pytest.mark.asyncio
async def test_scenario_restores_config_on_completion():
    proxy = ThrottledProxy(config=ThrottleConfig(bandwidth_bps=10_000_000))
    await proxy.start()
    try:
        scenario_dict = {
            "name": "Short",
            "phases": [
                {"name": "Fast", "duration": "0.3s", "bandwidth": "200kbps"},
            ],
        }
        await _post(proxy.control_port, "/scenario/start", scenario_dict)
        await asyncio.sleep(1.5)  # wait for scenario to complete + restore

        # Config should be back to original 10 Mbps
        assert proxy.config.bandwidth_bps == 10_000_000
        st = await _get(proxy.control_port, "/scenario/status")
        assert st["running"] is False
    finally:
        await proxy.close()


@pytest.mark.asyncio
async def test_scenario_builtin_list():
    proxy = ThrottledProxy()
    await proxy.start()
    try:
        resp = await _get(proxy.control_port, "/scenarios")
        assert "scenarios" in resp
        assert isinstance(resp["scenarios"], list)
        # Built-in scenarios should be listed (requires the files to be present)
        assert len(resp["scenarios"]) >= 4
        assert "subway" in resp["scenarios"]
    finally:
        await proxy.close()


@pytest.mark.asyncio
async def test_scenario_invalid_dict_returns_error():
    proxy = ThrottledProxy()
    await proxy.start()
    try:
        # Missing phases → ScenarioError captured as state.error
        resp = await _post(proxy.control_port, "/scenario/start", {"name": "Bad", "phases": []})
        # The error may propagate as a 400 from the outer except, or as state.error
        # Either way, scenario should not be running
        await asyncio.sleep(0.1)
        st = await _get(proxy.control_port, "/scenario/status")
        assert st["running"] is False
    finally:
        await proxy.close()
