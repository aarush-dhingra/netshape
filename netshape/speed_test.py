"""Proxy verification helpers."""

from __future__ import annotations

import http.server
import threading
import time
import urllib.request
from dataclasses import dataclass
from http.client import HTTPConnection
from socketserver import ThreadingTCPServer
from urllib.parse import urlsplit

from .core import ProxyRunner
from .profiles import resolve_settings
from .proxy_server import ThrottleConfig, ThrottledProxy


@dataclass(frozen=True)
class SpeedTestResult:
    bytes_downloaded: int
    direct_seconds: float
    proxied_seconds: float
    requests_handled: int

    @property
    def proxy_detected(self) -> bool:
        return self.requests_handled > 0


def run_speed_test(
    *,
    profile: str | None = "3g",
    bandwidth: str | int | float | None = None,
    latency: str | int | float | None = None,
    loss: str | int | float | None = None,
    jitter: str | int | float | None = None,
    byte_count: int = 64 * 1024,
) -> SpeedTestResult:
    """Download bytes directly and through NetShape's proxy."""

    payload = b"x" * byte_count
    server = _PayloadServer(payload)
    server.start()
    settings = resolve_settings(
        profile=profile,
        bandwidth=bandwidth,
        latency=latency,
        loss=loss,
        jitter=jitter,
    )
    proxy = ThrottledProxy(
        traffic_port=0,
        control_port=0,
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
    try:
        url = f"http://127.0.0.1:{server.port}/payload"
        direct_seconds = _timed_download(url)
        proxied_seconds = _timed_download(
            url,
            proxy_url=f"http://127.0.0.1:{proxy.traffic_port}",
        )
        return SpeedTestResult(
            bytes_downloaded=byte_count,
            direct_seconds=direct_seconds,
            proxied_seconds=proxied_seconds,
            requests_handled=proxy.config.requests_handled,
        )
    finally:
        runner.stop()
        server.stop()


def _timed_download(url: str, *, proxy_url: str | None = None) -> float:
    started = time.perf_counter()
    if proxy_url is None:
        with urllib.request.urlopen(url, timeout=10) as response:
            response.read()
    else:
        parsed_proxy = urlsplit(proxy_url)
        conn = HTTPConnection(parsed_proxy.hostname or "127.0.0.1", parsed_proxy.port or 80, timeout=10)
        try:
            conn.request("GET", url)
            response = conn.getresponse()
            response.read()
        finally:
            conn.close()
    return time.perf_counter() - started


class _PayloadServer:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self._server: ThreadingTCPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("server is not running")
        return int(self._server.server_address[1])

    def start(self) -> None:
        payload = self.payload

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: object) -> None:
                return

        self._server = ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
