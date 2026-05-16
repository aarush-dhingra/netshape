"""Built-in speed test: measure download speed, latency, and packet loss."""

from __future__ import annotations

import os
import platform
import re
import socket
import subprocess
import time
import urllib.request
from dataclasses import dataclass

_DEFAULT_ENDPOINTS = [
    "https://speed.cloudflare.com/__down?bytes=1000000",
    "https://httpbin.org/bytes/1000000",
]

_LATENCY_HOST = "1.1.1.1"
_LATENCY_PORT = 80
_PING_COUNT = 10
_TIMEOUT = 30


@dataclass
class SpeedTestResult:
    download_speed_bps: float
    latency_ms: float
    packet_loss_pct: float
    is_throttled: bool
    profile_name: str | None
    endpoint_used: str | None


def _get_endpoint() -> str:
    """Resolve the speed test endpoint from env var or built-in fallback chain."""
    env_url = os.environ.get("NETSHAPE_SPEEDTEST_URL")
    if env_url:
        return env_url
    return _DEFAULT_ENDPOINTS[0]


def _get_fallback_endpoints() -> list[str]:
    env_url = os.environ.get("NETSHAPE_SPEEDTEST_URL")
    if env_url:
        return [env_url]
    return list(_DEFAULT_ENDPOINTS)


def measure_download_speed(endpoint: str | None = None) -> tuple[float, str]:
    """Download a test file and return (bits per second, endpoint used).

    Tries endpoints in the fallback chain. Raises on total failure.
    """
    endpoints = [endpoint] if endpoint else _get_fallback_endpoints()

    last_error: Exception | None = None
    for url in endpoints:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "netshape-speedtest"})
            start_time = time.monotonic()
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as response:
                data = response.read()
            elapsed = time.monotonic() - start_time

            if elapsed <= 0:
                elapsed = 0.001

            bytes_received = len(data)
            bps = (bytes_received * 8) / elapsed
            return bps, url
        except Exception as e:
            last_error = e
            continue

    raise RuntimeError(
        f"Speed test failed: cannot reach any test endpoint. "
        f"Last error: {last_error}\n"
        f"Set NETSHAPE_SPEEDTEST_URL to use a custom endpoint."
    )


def measure_latency(host: str = _LATENCY_HOST, port: int = _LATENCY_PORT, attempts: int = 3) -> float:
    """Measure TCP connect latency to a host. Returns average milliseconds."""
    times: list[float] = []
    for _ in range(attempts):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            start_time = time.monotonic()
            sock.connect((host, port))
            elapsed = time.monotonic() - start_time
            sock.close()
            times.append(elapsed * 1000)
        except (socket.error, OSError):
            continue

    if not times:
        return -1.0
    return sum(times) / len(times)


def measure_packet_loss(count: int = _PING_COUNT) -> float:
    """Send ICMP pings and return the packet loss percentage (0.0 to 100.0).

    Returns -1.0 if ping fails entirely.
    """
    try:
        if platform.system() == "Windows":
            cmd = ["ping", "-n", str(count), "-w", "2000", _LATENCY_HOST]
        else:
            cmd = ["ping", "-c", str(count), "-W", "2", _LATENCY_HOST]

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_TIMEOUT + 10,
        )
        output = result.stdout

        # Windows: "(X% loss)"
        # macOS/Linux: "X% packet loss" or "X.X% packet loss"
        match = re.search(r"(\d+(?:\.\d+)?)%\s*(?:loss|packet loss)", output)
        if match:
            return float(match.group(1))

        return -1.0
    except (subprocess.SubprocessError, OSError):
        return -1.0


def run_speed_test(
    endpoint: str | None = None,
    active_profile: str | None = None,
    is_throttled: bool = False,
) -> SpeedTestResult:
    """Run a full speed test: download speed, latency, packet loss."""
    speed_bps, used_endpoint = measure_download_speed(endpoint)
    latency = measure_latency()
    loss = measure_packet_loss()

    return SpeedTestResult(
        download_speed_bps=speed_bps,
        latency_ms=latency,
        packet_loss_pct=loss if loss >= 0 else 0.0,
        is_throttled=is_throttled,
        profile_name=active_profile,
        endpoint_used=used_endpoint,
    )


def format_speed(bps: float) -> str:
    """Format bits-per-second into a human-readable string."""
    if bps >= 1_000_000:
        return f"{bps / 1_000_000:.1f} Mbps"
    elif bps >= 1_000:
        return f"{bps / 1_000:.1f} Kbps"
    else:
        return f"{bps:.0f} bps"


def format_speed_bytes(bps: float) -> str:
    """Format bits-per-second into bytes-per-second human-readable string."""
    byte_ps = bps / 8
    if byte_ps >= 1_000_000:
        return f"{byte_ps / 1_000_000:.1f} MB/s"
    elif byte_ps >= 1_000:
        return f"{byte_ps / 1_000:.1f} KB/s"
    else:
        return f"{byte_ps:.0f} B/s"
