"""Tests for Phase 5: Metrics & Observability."""

from __future__ import annotations

import asyncio
import json

import pytest

from netshape.proxy_server import ThrottleConfig, ThrottledProxy


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _get_raw(port: int, path: str) -> tuple[int, bytes]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".encode())
    await writer.drain()
    data = await reader.read(8192)
    writer.close()
    header_end = data.index(b"\r\n\r\n")
    status_line = data[:header_end].split(b"\r\n")[0]
    status_code = int(status_line.split()[1])
    body = data[header_end + 4:]
    return status_code, body


async def _get_json(port: int, path: str) -> tuple[int, dict]:
    status, body = await _get_raw(port, path)
    return status, json.loads(body)


# ── Metric counters ───────────────────────────────────────────────────────────

def test_throttle_config_has_new_counters():
    config = ThrottleConfig()
    assert config.connections_total == 0
    assert config.drops_total == 0


def test_proxy_has_metric_attrs():
    proxy = ThrottledProxy()
    assert hasattr(proxy, "_connections_active")
    assert hasattr(proxy, "_throttle_sleep_seconds")
    assert hasattr(proxy, "_latency_added_seconds")
    assert proxy._connections_active == 0
    assert proxy._throttle_sleep_seconds == 0.0
    assert proxy._latency_added_seconds == 0.0


# ── /metrics JSON endpoint ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_metrics_json_endpoint():
    proxy = ThrottledProxy(config=ThrottleConfig(bandwidth_bps=1_000_000, latency_ms=50, loss_pct=0.01))
    await proxy.start()
    try:
        status, data = await _get_json(proxy.control_port, "/metrics?format=json")
        assert status == 200
        expected_keys = [
            "netshape_bytes_sent_total",
            "netshape_bytes_received_total",
            "netshape_connections_total",
            "netshape_connections_active",
            "netshape_requests_handled_total",
            "netshape_throttle_sleep_seconds_total",
            "netshape_drops_total",
            "netshape_latency_added_seconds_total",
            "netshape_config_bandwidth_bps",
            "netshape_config_latency_ms",
            "netshape_config_loss_pct",
            "netshape_rules_count",
        ]
        for key in expected_keys:
            assert key in data, f"missing metric: {key}"

        assert data["netshape_config_bandwidth_bps"] == 1_000_000
        assert data["netshape_config_latency_ms"] == 50
        assert data["netshape_config_loss_pct"] == pytest.approx(0.01)
        assert data["netshape_connections_total"] == 0
        assert data["netshape_drops_total"] == 0
    finally:
        await proxy.close()


# ── /metrics Prometheus endpoint ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_metrics_prometheus_endpoint():
    proxy = ThrottledProxy()
    await proxy.start()
    try:
        status, body = await _get_raw(proxy.control_port, "/metrics")
        assert status == 200
        text = body.decode("utf-8")
        assert "# HELP netshape_bytes_sent_total" in text
        assert "# TYPE netshape_bytes_sent_total counter" in text
        assert "netshape_bytes_sent_total 0" in text
        assert "# HELP netshape_connections_total" in text
        assert "netshape_config_bandwidth_bps 0" in text
    finally:
        await proxy.close()


@pytest.mark.asyncio
async def test_metrics_prometheus_content_type():
    proxy = ThrottledProxy()
    await proxy.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.control_port)
        writer.write(b"GET /metrics HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        await writer.drain()
        data = await reader.read(2048)
        writer.close()
        assert b"text/plain" in data
        assert b"version=0.0.4" in data
    finally:
        await proxy.close()


# ── connections_total counter ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_connections_total_increments():
    proxy = ThrottledProxy()
    await proxy.start()
    try:
        # Make two connections to the traffic port (they'll fail at the HTTP level but still count)
        for _ in range(2):
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", proxy.traffic_port)
                writer.write(b"INVALID\r\n\r\n")
                await writer.drain()
                await reader.read(256)
                writer.close()
            except Exception:
                pass

        await asyncio.sleep(0.2)
        _, data = await _get_json(proxy.control_port, "/metrics?format=json")
        assert data["netshape_connections_total"] >= 2
    finally:
        await proxy.close()


# ── drops_total counter ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_drops_total_increments_with_100pct_loss():
    proxy = ThrottledProxy(config=ThrottleConfig(loss_pct=1.0))
    await proxy.start()
    try:
        # Send a CONNECT request; with 100% loss it should be dropped
        for _ in range(3):
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", proxy.traffic_port)
                writer.write(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com\r\n\r\n")
                await writer.drain()
                await reader.read(256)
                writer.close()
            except Exception:
                pass

        await asyncio.sleep(0.3)
        _, data = await _get_json(proxy.control_port, "/metrics?format=json")
        # With 100% loss, all CONNECT attempts should be dropped
        assert data["netshape_drops_total"] >= 3
    finally:
        await proxy.close()


# ── _metrics_dict reflects configure changes ──────────────────────────────────

@pytest.mark.asyncio
async def test_metrics_dict_reflects_configure():
    proxy = ThrottledProxy()
    await proxy.start()
    try:
        await proxy.configure({"bandwidth_bps": 2_500_000, "latency_ms": 75})
        _, data = await _get_json(proxy.control_port, "/metrics?format=json")
        assert data["netshape_config_bandwidth_bps"] == 2_500_000
        assert data["netshape_config_latency_ms"] == 75
    finally:
        await proxy.close()


# ── throttle_sleep_seconds accumulates ────────────────────────────────────────

@pytest.mark.asyncio
async def test_throttle_sleep_seconds_accumulates():
    """Writing throttled data should increment _throttle_sleep_seconds."""
    import asyncio

    # Very tight bandwidth → any write should incur a sleep
    proxy = ThrottledProxy(config=ThrottleConfig(bandwidth_bps=8 * 8192))  # 8 chunks/s
    await proxy.start()
    try:
        # Set up a tiny echo-less TCP server so the proxy can actually forward data
        async def _echo_server(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
            try:
                while chunk := await r.read(8192):
                    w.write(chunk)
                    await w.drain()
            except Exception:
                pass
            finally:
                w.close()

        echo_server = await asyncio.start_server(_echo_server, "127.0.0.1", 0)
        echo_port = echo_server.sockets[0].getsockname()[1]

        payload = b"X" * (READ_CHUNK * 4)  # 4 chunks → should take ~0.5s
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", proxy.traffic_port)
            connect_req = f"CONNECT 127.0.0.1:{echo_port} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".encode()
            writer.write(connect_req)
            await writer.drain()
            response = await reader.readuntil(b"\r\n\r\n")
            assert b"200" in response

            writer.write(payload)
            await writer.drain()
            await asyncio.sleep(1.0)  # give time for throttled write to complete
            writer.close()
        except Exception:
            pass
        finally:
            echo_server.close()
            await echo_server.wait_closed()

        assert proxy._throttle_sleep_seconds > 0
    finally:
        await proxy.close()


READ_CHUNK = 8192
