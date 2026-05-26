"""Tests for Phase 3: per-endpoint throttle rules."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from netshape.proxy_server import ThrottleConfig, ThrottledProxy, ThrottleRule


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _ctrl(port: int, request: str | bytes) -> dict:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request.encode("ascii") if isinstance(request, str) else request)
    await writer.drain()
    raw = await reader.read(65536)
    writer.close()
    await writer.wait_closed()
    _, _, body = raw.partition(b"\r\n\r\n")
    return json.loads(body.decode("utf-8"))


async def _post(port: int, path: str, payload: dict) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8")
    request = (
        f"POST {path} HTTP/1.1\r\n"
        "Host: localhost\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "\r\n"
    ).encode("ascii") + body
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request)
    await writer.drain()
    raw = await reader.read(65536)
    writer.close()
    await writer.wait_closed()
    status_line = raw.split(b"\r\n", 1)[0].decode()
    status = int(status_line.split()[1])
    _, _, rbody = raw.partition(b"\r\n\r\n")
    return status, json.loads(rbody.decode("utf-8"))


async def _delete(port: int, path: str) -> tuple[int, dict]:
    request = (
        f"DELETE {path} HTTP/1.1\r\nHost: localhost\r\n\r\n"
    ).encode("ascii")
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request)
    await writer.drain()
    raw = await reader.read(65536)
    writer.close()
    await writer.wait_closed()
    status_line = raw.split(b"\r\n", 1)[0].decode()
    status = int(status_line.split()[1])
    _, _, rbody = raw.partition(b"\r\n\r\n")
    return status, json.loads(rbody.decode("utf-8"))


def _server_port(server: asyncio.AbstractServer) -> int:
    sockets = server.sockets
    assert sockets is not None
    return int(sockets[0].getsockname()[1])


# ── Unit tests: _resolve_rules ───────────────────────────────────────────────

def test_resolve_rules_returns_global_when_no_rules() -> None:
    proxy = ThrottledProxy(
        traffic_port=0, control_port=0,
        config=ThrottleConfig(bandwidth_bps=1_000_000, latency_ms=50),
    )
    rules = proxy._resolve_rules("api.example.com")
    assert rules.bucket is proxy.bucket
    assert rules.latency_ms == 50
    assert rules.rule_id is None


def test_resolve_rules_matches_hostname_pattern() -> None:
    proxy = ThrottledProxy(traffic_port=0, control_port=0)
    proxy.add_rule({"pattern": r"stripe\.com", "bandwidth_bps": 500_000, "latency_ms": 200})

    matched = proxy._resolve_rules("api.stripe.com")
    assert matched.rule_id is not None
    assert matched.latency_ms == 200
    assert matched.bucket is not proxy.bucket

    unmatched = proxy._resolve_rules("github.com")
    assert unmatched.rule_id is None
    assert unmatched.bucket is proxy.bucket


def test_resolve_rules_first_match_wins() -> None:
    proxy = ThrottledProxy(traffic_port=0, control_port=0)
    proxy.add_rule({"pattern": r"stripe\.com", "latency_ms": 100})
    proxy.add_rule({"pattern": r"stripe", "latency_ms": 999})

    matched = proxy._resolve_rules("api.stripe.com")
    assert matched.latency_ms == 100  # first rule wins


def test_resolve_rules_inherits_global_when_field_is_none() -> None:
    proxy = ThrottledProxy(
        traffic_port=0, control_port=0,
        config=ThrottleConfig(latency_ms=75, jitter_ms=10),
    )
    proxy.add_rule({"pattern": r"example\.com", "bandwidth_bps": 1_000_000})

    matched = proxy._resolve_rules("example.com")
    assert matched.latency_ms == 75   # inherited from global
    assert matched.jitter_ms == 10    # inherited from global


def test_resolve_rules_full_url_for_http() -> None:
    proxy = ThrottledProxy(traffic_port=0, control_port=0)
    proxy.add_rule({"pattern": r"/v1/charges", "latency_ms": 300})

    matched = proxy._resolve_rules("http://api.stripe.com/v1/charges")
    assert matched.latency_ms == 300

    unmatched = proxy._resolve_rules("http://api.stripe.com/v1/customers")
    assert unmatched.rule_id is None


# ── Unit tests: add/remove/list rules ────────────────────────────────────────

def test_add_rule_assigns_uuid() -> None:
    proxy = ThrottledProxy(traffic_port=0, control_port=0)
    rule = proxy.add_rule({"pattern": r"example\.com", "latency_ms": 100})
    assert len(rule.id) == 36  # UUID4 format
    assert rule.pattern == r"example\.com"
    assert rule.latency_ms == 100


def test_add_rule_rejects_invalid_regex() -> None:
    proxy = ThrottledProxy(traffic_port=0, control_port=0)
    with pytest.raises(ValueError, match="invalid regex"):
        proxy.add_rule({"pattern": "[unclosed"})


def test_add_rule_rejects_missing_pattern() -> None:
    proxy = ThrottledProxy(traffic_port=0, control_port=0)
    with pytest.raises(ValueError, match="pattern"):
        proxy.add_rule({})


def test_remove_rule_returns_true_when_found() -> None:
    proxy = ThrottledProxy(traffic_port=0, control_port=0)
    rule = proxy.add_rule({"pattern": r"example\.com"})
    assert proxy.remove_rule(rule.id) is True
    assert proxy.list_rules() == []


def test_remove_rule_returns_false_when_not_found() -> None:
    proxy = ThrottledProxy(traffic_port=0, control_port=0)
    assert proxy.remove_rule("nonexistent-id") is False


def test_remove_rule_clears_cached_bucket_and_pattern() -> None:
    proxy = ThrottledProxy(traffic_port=0, control_port=0)
    rule = proxy.add_rule({"pattern": r"example\.com", "bandwidth_bps": 1_000_000})
    # Trigger bucket and pattern caching via _resolve_rules
    proxy._resolve_rules("example.com")
    assert rule.id in proxy._rule_buckets
    assert rule.id in proxy._rule_patterns

    proxy.remove_rule(rule.id)
    assert rule.id not in proxy._rule_buckets
    assert rule.id not in proxy._rule_patterns


# ── Integration tests: /rules control API ────────────────────────────────────

def test_rules_api_crud() -> None:
    asyncio.run(_test_rules_api_crud())


async def _test_rules_api_crud() -> None:
    proxy = ThrottledProxy(traffic_port=0, control_port=0)
    await proxy.start()
    try:
        # GET /rules — initially empty
        result = await _ctrl(
            proxy.control_port,
            "GET /rules HTTP/1.1\r\nHost: localhost\r\n\r\n",
        )
        assert result["rules"] == []

        # POST /rules — add a rule
        status, rule = await _post(
            proxy.control_port,
            "/rules",
            {"pattern": r"stripe\.com", "bandwidth_bps": 500_000, "latency_ms": 200},
        )
        assert status == 201
        assert rule["pattern"] == r"stripe\.com"
        assert rule["bandwidth_bps"] == 500_000
        rule_id = rule["id"]

        # GET /rules — one rule
        result = await _ctrl(
            proxy.control_port,
            "GET /rules HTTP/1.1\r\nHost: localhost\r\n\r\n",
        )
        assert len(result["rules"]) == 1
        assert result["rules"][0]["id"] == rule_id

        # GET /status — rules_count exposed
        status_payload = await _ctrl(
            proxy.control_port,
            "GET /status HTTP/1.1\r\nHost: localhost\r\n\r\n",
        )
        assert status_payload["rules_count"] == 1

        # DELETE /rules/{id}
        del_status, del_result = await _delete(
            proxy.control_port,
            f"/rules/{rule_id}",
        )
        assert del_status == 200
        assert del_result["ok"] is True

        # Rule gone
        result = await _ctrl(
            proxy.control_port,
            "GET /rules HTTP/1.1\r\nHost: localhost\r\n\r\n",
        )
        assert result["rules"] == []

        # DELETE non-existent → 404
        del_status, _ = await _delete(
            proxy.control_port,
            f"/rules/{rule_id}",
        )
        assert del_status == 404
    finally:
        await proxy.close()


def test_rule_invalid_pattern_returns_400() -> None:
    asyncio.run(_test_rule_invalid_pattern_returns_400())


async def _test_rule_invalid_pattern_returns_400() -> None:
    proxy = ThrottledProxy(traffic_port=0, control_port=0)
    await proxy.start()
    try:
        status, result = await _post(
            proxy.control_port, "/rules", {"pattern": "[bad"}
        )
        assert status == 400
        assert "error" in result
    finally:
        await proxy.close()


# ── Integration test: per-rule bandwidth applied to HTTP connections ──────────

def test_rule_bandwidth_throttles_matching_http() -> None:
    asyncio.run(_test_rule_bandwidth_throttles_matching_http())


async def _test_rule_bandwidth_throttles_matching_http() -> None:
    """Rule matched by URL path throttles; non-matching path is fast."""
    payload_size = 64 * 1024  # 64 KB
    payload = b"x" * payload_size

    async def handle_upstream(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        # Consume the request headers, then serve the payload regardless of path.
        await reader.readuntil(b"\r\n\r\n")
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            + f"Content-Length: {len(payload)}\r\n".encode()
            + b"Connection: close\r\n\r\n"
            + payload
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    upstream = await asyncio.start_server(handle_upstream, "127.0.0.1", 0)
    upstream_port = _server_port(upstream)

    # Rule throttles requests whose URL contains "/slow" at 500 Kbps.
    # The "/fast" path will not match and uses the global (unlimited) bucket.
    proxy = ThrottledProxy(traffic_port=0, control_port=0)
    proxy.add_rule({"pattern": r"/slow", "bandwidth_bps": 500_000})
    await proxy.start()

    base = f"http://127.0.0.1:{upstream_port}"

    async def read_all(r: asyncio.StreamReader) -> bytes:
        """Read until EOF (connection closed by proxy after upstream closes)."""
        parts: list[bytes] = []
        while True:
            chunk = await r.read(8192)
            if not chunk:
                break
            parts.append(chunk)
        return b"".join(parts)

    try:
        # --- Throttled request (/slow path matches rule) ---
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.traffic_port)
        writer.write(
            f"GET {base}/slow HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{upstream_port}\r\n"
            "\r\n".encode()
        )
        await writer.drain()
        start = time.monotonic()
        response = await read_all(reader)
        elapsed_throttled = time.monotonic() - start
        writer.close()
        await writer.wait_closed()
        assert b"HTTP/1.1 200 OK" in response, f"unexpected: {response[:80]}"

        # --- Unthrottled request (/fast path doesn't match) ---
        reader2, writer2 = await asyncio.open_connection("127.0.0.1", proxy.traffic_port)
        writer2.write(
            f"GET {base}/fast HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{upstream_port}\r\n"
            "\r\n".encode()
        )
        await writer2.drain()
        start2 = time.monotonic()
        response2 = await read_all(reader2)
        elapsed_unthrottled = time.monotonic() - start2
        writer2.close()
        await writer2.wait_closed()
        assert b"HTTP/1.1 200 OK" in response2, f"unexpected: {response2[:80]}"

        # At 500 Kbps, 64 KB takes at least ≈ 1 s in theory.  On loopback TCP
        # the effective rate may be ~2× higher due to kernel buffer pipelining,
        # so we use a conservative lower bound of 0.3 s.
        assert elapsed_throttled > 0.3, (
            f"throttled transfer too fast: {elapsed_throttled:.2f}s"
        )
        # Unthrottled (no rule matches) must be at least 3× faster than throttled.
        assert elapsed_unthrottled < elapsed_throttled / 3, (
            f"unthrottled not meaningfully faster: "
            f"throttled={elapsed_throttled:.2f}s  unthrottled={elapsed_unthrottled:.2f}s"
        )
    finally:
        upstream.close()
        await upstream.wait_closed()
        await proxy.close()
