"""Session lifecycle management for NetShape."""

from __future__ import annotations

import asyncio
import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .profiles import resolve_settings
from .proxy_server import ThrottleConfig, ThrottledProxy

DEFAULT_TRAFFIC_PORT = 8090
DEFAULT_STATE_PATH = Path.home() / ".netshape" / "state.json"


class SessionError(RuntimeError):
    """Raised when a NetShape session operation fails."""


@dataclass(frozen=True)
class SessionState:
    active: bool
    traffic_port: int
    control_port: int
    pid: int
    started_at: float
    profile: str | None = None
    bandwidth_bps: int = 0
    latency_ms: int = 0
    loss_pct: float = 0.0
    jitter_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProxyRunner:
    """Runs the asyncio proxy in a background thread."""

    def __init__(self, proxy: ThrottledProxy) -> None:
        self.proxy = proxy
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._thread = threading.Thread(target=self._run, name="netshape-proxy", daemon=True)

    def start(self) -> None:
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise SessionError("proxy did not start within 5 seconds")
        if self._error is not None:
            raise SessionError("proxy failed to start") from self._error

    def stop(self) -> None:
        try:
            _post_json(self.proxy.control_port, "/shutdown", {})
        except OSError:
            pass
        self._thread.join(timeout=5)

    def _run(self) -> None:
        async def run_proxy() -> None:
            await self.proxy.start()
            self._ready.set()
            await self.proxy.wait_closed()

        try:
            asyncio.run(run_proxy())
        except BaseException as exc:
            self._error = exc
            self._ready.set()


def run_session(
    *,
    command: list[str],
    profile: str | None = None,
    bandwidth: str | int | float | None = None,
    latency: str | int | float | None = None,
    loss: str | int | float | None = None,
    jitter: str | int | float | None = None,
    traffic_port: int = DEFAULT_TRAFFIC_PORT,
    control_port: int | None = None,
    state_path: Path = DEFAULT_STATE_PATH,
) -> int:
    if not command:
        raise SessionError("command is required")

    settings = resolve_settings(
        profile=profile,
        bandwidth=bandwidth,
        latency=latency,
        loss=loss,
        jitter=jitter,
    )
    if traffic_port == 0:
        resolved_traffic_port = 0
        resolved_control_port = 0 if control_port is None else control_port
    else:
        resolved_traffic_port = _find_free_port(traffic_port)
        resolved_control_port = control_port or _find_free_port(resolved_traffic_port + 1)

    proxy = ThrottledProxy(
        traffic_port=resolved_traffic_port,
        control_port=resolved_control_port,
        config=ThrottleConfig(
            bandwidth_bps=settings.bandwidth_bps,
            latency_ms=settings.latency_ms,
            loss_pct=settings.loss_pct,
            jitter_ms=settings.jitter_ms,
            profile=settings.profile,
        ),
    )
    runner = ProxyRunner(proxy)
    runner.start()

    state = SessionState(
        active=True,
        traffic_port=proxy.traffic_port,
        control_port=proxy.control_port,
        pid=os.getpid(),
        started_at=time.time(),
        profile=settings.profile,
        bandwidth_bps=settings.bandwidth_bps,
        latency_ms=settings.latency_ms,
        loss_pct=settings.loss_pct,
        jitter_ms=settings.jitter_ms,
    )
    write_state(state, state_path)

    env = _proxy_env(os.environ.copy(), proxy.traffic_port)
    process = subprocess.Popen(command, env=env)
    try:
        return process.wait()
    except KeyboardInterrupt:
        process.terminate()
        return process.wait()
    finally:
        runner.stop()
        clear_state(state_path)


def adjust_session(
    *,
    profile: str | None = None,
    bandwidth: str | int | float | None = None,
    latency: str | int | float | None = None,
    loss: str | int | float | None = None,
    jitter: str | int | float | None = None,
    state_path: Path = DEFAULT_STATE_PATH,
) -> dict[str, Any]:
    state = read_state(state_path)
    if state is None:
        raise SessionError("no active NetShape session")

    if profile is not None:
        settings = resolve_settings(
            profile=profile,
            bandwidth=bandwidth,
            latency=latency,
            loss=loss,
            jitter=jitter,
        )
    else:
        settings = resolve_settings(
            bandwidth=state.bandwidth_bps if bandwidth is None else bandwidth,
            latency=state.latency_ms if latency is None else latency,
            loss=state.loss_pct if loss is None else loss,
            jitter=state.jitter_ms if jitter is None else jitter,
        )

    payload = {
        "bandwidth_bps": settings.bandwidth_bps,
        "latency_ms": settings.latency_ms,
        "loss_pct": settings.loss_pct,
        "jitter_ms": settings.jitter_ms,
        "profile": settings.profile if profile is not None else state.profile,
    }
    return _post_json(state.control_port, "/configure", payload)


def get_status(*, state_path: Path = DEFAULT_STATE_PATH) -> dict[str, Any]:
    state = read_state(state_path)
    if state is None:
        return {"active": False}
    status = _get_json(state.control_port, "/status")
    status["active"] = True
    status["pid"] = state.pid
    return status


def stop_session(*, state_path: Path = DEFAULT_STATE_PATH) -> None:
    state = read_state(state_path)
    if state is None:
        return
    try:
        _post_json(state.control_port, "/shutdown", {})
    finally:
        clear_state(state_path)


def write_state(state: SessionState, path: Path = DEFAULT_STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")


def read_state(path: Path = DEFAULT_STATE_PATH) -> SessionState | None:
    if not path.exists():
        return None
    return SessionState(**json.loads(path.read_text(encoding="utf-8")))


def clear_state(path: Path = DEFAULT_STATE_PATH) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _proxy_env(env: dict[str, str], traffic_port: int) -> dict[str, str]:
    proxy_url = f"http://127.0.0.1:{traffic_port}"
    env.update(
        {
            "HTTP_PROXY": proxy_url,
            "HTTPS_PROXY": proxy_url,
            "http_proxy": proxy_url,
            "https_proxy": proxy_url,
            "NO_PROXY": "localhost,127.0.0.1",
            "no_proxy": "localhost,127.0.0.1",
        }
    )
    return env


def _find_free_port(starting: int) -> int:
    for port in range(starting, starting + 100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise SessionError(f"no free port found starting at {starting}")


def _post_json(port: int, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("POST", path, body=body, headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        data = response.read()
        if response.status >= 400:
            raise SessionError(data.decode("utf-8") or f"HTTP {response.status}")
        return json.loads(data.decode("utf-8") or "{}")
    finally:
        conn.close()


def _get_json(port: int, path: str) -> dict[str, Any]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        data = response.read()
        if response.status >= 400:
            raise SessionError(data.decode("utf-8") or f"HTTP {response.status}")
        return json.loads(data.decode("utf-8") or "{}")
    finally:
        conn.close()


def python_command(code: str) -> list[str]:
    """Small helper for tests and examples that need the current interpreter."""

    return [sys.executable, "-c", code]
