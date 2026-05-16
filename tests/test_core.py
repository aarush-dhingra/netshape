from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

from netshape.core import (
    ProxyRunner,
    SessionState,
    SessionError,
    _find_free_port,
    adjust_session,
    clear_state,
    get_status,
    read_state,
    run_session,
    stop_session,
    write_state,
)
from netshape.proxy_server import ThrottleConfig, ThrottledProxy


def test_state_round_trip(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state = SessionState(
        active=True,
        traffic_port=8090,
        control_port=8091,
        pid=123,
        started_at=1.5,
        profile="3g",
        bandwidth_bps=100_000,
        latency_ms=200,
        loss_pct=0.01,
        jitter_ms=20,
    )

    write_state(state, state_path)
    assert read_state(state_path) == state

    clear_state(state_path)
    assert read_state(state_path) is None


def test_run_session_sets_proxy_environment_and_cleans_state(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    output_path = tmp_path / "env.json"
    code = (
        "import json, os, pathlib; "
        f"pathlib.Path({str(output_path)!r}).write_text("
        "json.dumps({"
        "'HTTP_PROXY': os.environ.get('HTTP_PROXY'), "
        "'HTTPS_PROXY': os.environ.get('HTTPS_PROXY'), "
        "'NO_PROXY': os.environ.get('NO_PROXY')"
        "}))"
    )

    exit_code = run_session(
        command=[sys.executable, "-c", code],
        profile="3g",
        traffic_port=0,
        state_path=state_path,
    )

    env = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert env["HTTP_PROXY"].startswith("http://127.0.0.1:")
    assert env["HTTPS_PROXY"] == env["HTTP_PROXY"]
    assert env["NO_PROXY"] == "localhost,127.0.0.1"
    assert read_state(state_path) is None


def test_status_adjust_and_stop_session(tmp_path: Path) -> None:
    asyncio.run(_test_status_adjust_and_stop_session(tmp_path))


async def _test_status_adjust_and_stop_session(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    proxy = ThrottledProxy(traffic_port=0, control_port=0, config=ThrottleConfig())
    runner = ProxyRunner(proxy)
    runner.start()
    try:
        write_state(
            SessionState(
                active=True,
                traffic_port=proxy.traffic_port,
                control_port=proxy.control_port,
                pid=123,
                started_at=time.time(),
            ),
            state_path,
        )

        status = get_status(state_path=state_path)
        assert status["active"] is True
        assert status["traffic_port"] == proxy.traffic_port

        adjusted = adjust_session(latency="300ms", bandwidth="100kbps", state_path=state_path)
        assert adjusted["latency_ms"] == 300
        assert adjusted["bandwidth_bps"] == 100_000

        stop_session(state_path=state_path)
        assert read_state(state_path) is None
    finally:
        runner.stop()


def test_status_warns_when_no_traffic_detected(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    proxy = ThrottledProxy(traffic_port=0, control_port=0, config=ThrottleConfig())
    runner = ProxyRunner(proxy)
    runner.start()
    try:
        proxy.config.started_at = time.time() - 11
        write_state(
            SessionState(
                active=True,
                traffic_port=proxy.traffic_port,
                control_port=proxy.control_port,
                pid=123,
                started_at=time.time() - 11,
            ),
            state_path,
        )

        status = get_status(state_path=state_path)

        assert "No traffic detected" in status["warning"]
    finally:
        runner.stop()


def test_run_session_timeout_terminates_child(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    started = time.perf_counter()

    exit_code = run_session(
        command=[sys.executable, "-c", "import time; time.sleep(30)"],
        timeout="100ms",
        traffic_port=0,
        state_path=state_path,
    )

    assert exit_code != 0
    assert time.perf_counter() - started < 10
    assert read_state(state_path) is None


def test_find_free_port_reports_clear_range_when_exhausted(monkeypatch) -> None:
    class BusySocket:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def bind(self, address):
            raise OSError("busy")

    monkeypatch.setattr("socket.socket", lambda *args, **kwargs: BusySocket())

    with pytest.raises(SessionError, match="range 8090-8092"):
        _find_free_port(8090, attempts=3)
