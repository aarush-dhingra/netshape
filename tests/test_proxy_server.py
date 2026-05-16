from __future__ import annotations

import asyncio
import json

from netshape.proxy_server import ThrottleConfig, ThrottledProxy


def test_control_api_reports_and_updates_status() -> None:
    asyncio.run(_test_control_api_reports_and_updates_status())


async def _test_control_api_reports_and_updates_status() -> None:
    proxy = ThrottledProxy(traffic_port=0, control_port=0)
    await proxy.start()
    try:
        status = await _request_json(proxy.control_port, "GET /status HTTP/1.1\r\nHost: localhost\r\n\r\n")
        assert status["traffic_port"] == proxy.traffic_port
        assert status["control_port"] == proxy.control_port
        assert status["latency_ms"] == 0

        body = json.dumps({"latency_ms": 250, "bandwidth_bps": 100_000}).encode()
        response = await _request_json(
            proxy.control_port,
            (
                "POST /configure HTTP/1.1\r\n"
                "Host: localhost\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                "\r\n"
            ).encode()
            + body,
        )

        assert response["latency_ms"] == 250
        assert response["bandwidth_bps"] == 100_000
    finally:
        await proxy.close()


def test_http_proxy_forwards_absolute_url_requests() -> None:
    asyncio.run(_test_http_proxy_forwards_absolute_url_requests())


async def _test_http_proxy_forwards_absolute_url_requests() -> None:
    seen_request: list[str] = []

    async def handle_upstream(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        data = await reader.readuntil(b"\r\n\r\n")
        seen_request.append(data.decode("iso-8859-1"))
        body = b"proxied response"
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            + f"Content-Length: {len(body)}\r\n".encode("ascii")
            + b"Connection: close\r\n\r\n"
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    upstream = await asyncio.start_server(handle_upstream, "127.0.0.1", 0)
    upstream_port = _server_port(upstream)
    proxy = ThrottledProxy(traffic_port=0, control_port=0)
    await proxy.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.traffic_port)
        writer.write(
            (
                f"GET http://127.0.0.1:{upstream_port}/hello?x=1 HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{upstream_port}\r\n"
                "Proxy-Connection: keep-alive\r\n"
                "\r\n"
            ).encode("ascii")
        )
        await writer.drain()
        response = await reader.read()
        writer.close()
        await writer.wait_closed()

        assert b"HTTP/1.1 200 OK" in response
        assert b"proxied response" in response
        assert seen_request
        assert seen_request[0].startswith("GET /hello?x=1 HTTP/1.1")
        assert "proxy-connection" not in seen_request[0].lower()
        assert proxy.config.requests_handled == 1
        assert proxy.config.bytes_received > 0
    finally:
        upstream.close()
        await upstream.wait_closed()
        await proxy.close()


def test_connect_proxy_tunnels_bytes() -> None:
    asyncio.run(_test_connect_proxy_tunnels_bytes())


async def _test_connect_proxy_tunnels_bytes() -> None:
    async def handle_echo(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        data = await reader.read(1024)
        writer.write(b"echo:" + data)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    upstream = await asyncio.start_server(handle_echo, "127.0.0.1", 0)
    upstream_port = _server_port(upstream)
    proxy = ThrottledProxy(traffic_port=0, control_port=0, config=ThrottleConfig())
    await proxy.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.traffic_port)
        writer.write(f"CONNECT 127.0.0.1:{upstream_port} HTTP/1.1\r\n\r\n".encode("ascii"))
        await writer.drain()

        connect_response = await reader.readuntil(b"\r\n\r\n")
        assert b"200 Connection Established" in connect_response

        writer.write(b"hello")
        await writer.drain()
        assert await reader.read(10) == b"echo:hello"
        writer.close()
        await writer.wait_closed()
    finally:
        upstream.close()
        await upstream.wait_closed()
        await proxy.close()


async def _request_json(port: int, request: str | bytes) -> dict[str, object]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request.encode("ascii") if isinstance(request, str) else request)
    await writer.drain()
    response = await reader.read()
    writer.close()
    await writer.wait_closed()

    _, _, body = response.partition(b"\r\n\r\n")
    return json.loads(body.decode("utf-8"))


def _server_port(server: asyncio.AbstractServer) -> int:
    sockets = server.sockets
    assert sockets is not None
    return int(sockets[0].getsockname()[1])
