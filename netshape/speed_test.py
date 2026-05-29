"""Proxy verification helpers."""

from __future__ import annotations

import http.server
import socket
import struct
import threading
import time
import urllib.request
from dataclasses import dataclass
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
            socks5_proxy=("127.0.0.1", proxy.traffic_port),
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


def _timed_download(url: str, *, socks5_proxy: tuple[str, int] | None = None) -> float:
    started = time.perf_counter()
    if socks5_proxy is None:
        # URL is always http://127.0.0.1:<port>/payload — the local test server
        # started a few lines above. No user-supplied or external URL is possible.
        with urllib.request.urlopen(url, timeout=10) as response:  # nosec B310
            response.read()
    else:
        _download_via_socks5(url, socks5_proxy)
    return time.perf_counter() - started


def _download_via_socks5(url: str, proxy: tuple[str, int]) -> bytes:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    port = parsed.port or 80
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    with socket.create_connection(proxy, timeout=10) as sock:
        sock.settimeout(10)
        sock.sendall(b"\x05\x01\x00")
        reply = _recv_exact(sock, 2)
        if reply != b"\x05\x00":
            raise OSError("SOCKS5 proxy did not accept no-auth method")

        encoded_host = host.encode("idna")
        request = (
            b"\x05\x01\x00\x03"
            + bytes([len(encoded_host)])
            + encoded_host
            + struct.pack(">H", port)
        )
        sock.sendall(request)
        reply = _recv_exact(sock, 4)
        if reply[1] != 0x00:
            raise OSError(f"SOCKS5 connect failed with code {reply[1]}")
        _discard_socks5_bind_address(sock, reply[3])

        http_request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("ascii")
        sock.sendall(http_request)

        chunks: list[bytes] = []
        while chunk := sock.recv(8192):
            chunks.append(chunk)
        return b"".join(chunks)


def _discard_socks5_bind_address(sock: socket.socket, address_type: int) -> None:
    if address_type == 0x01:
        _recv_exact(sock, 4)
    elif address_type == 0x03:
        length = _recv_exact(sock, 1)[0]
        _recv_exact(sock, length)
    elif address_type == 0x04:
        _recv_exact(sock, 16)
    else:
        raise OSError("SOCKS5 proxy returned unsupported address type")
    _recv_exact(sock, 2)


def _recv_exact(sock: socket.socket, byte_count: int) -> bytes:
    chunks: list[bytes] = []
    remaining = byte_count
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise OSError("connection closed before expected bytes arrived")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


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
