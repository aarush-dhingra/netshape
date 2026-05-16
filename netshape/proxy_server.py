"""Asyncio HTTP/HTTPS forward proxy with a local control API."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlsplit

from .throttle import TokenBucket, calculate_delay_seconds, should_drop_chunk

READ_CHUNK_SIZE = 8192
HEADER_LIMIT = 64 * 1024


@dataclass
class ThrottleConfig:
    bandwidth_bps: int = 0
    latency_ms: int = 0
    loss_pct: float = 0.0
    jitter_ms: int = 0
    profile: str | None = None
    bytes_sent: int = 0
    bytes_received: int = 0
    requests_handled: int = 0
    started_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ThrottledProxy:
    def __init__(
        self,
        *,
        traffic_host: str = "127.0.0.1",
        traffic_port: int = 8090,
        control_host: str = "127.0.0.1",
        control_port: int = 8091,
        config: ThrottleConfig | None = None,
    ) -> None:
        self.traffic_host = traffic_host
        self.traffic_port = traffic_port
        self.control_host = control_host
        self.control_port = control_port
        self.config = config or ThrottleConfig()
        self.bucket = TokenBucket(self.config.bandwidth_bps)
        self._traffic_server: asyncio.AbstractServer | None = None
        self._control_server: asyncio.AbstractServer | None = None
        self._shutdown_event = asyncio.Event()
        self._config_lock = asyncio.Lock()
        self._active_writers: set[asyncio.StreamWriter] = set()

    async def start(self) -> None:
        self.config.started_at = time.time()
        self.bucket = TokenBucket(self.config.bandwidth_bps)
        self._shutdown_event.clear()
        self._traffic_server = await asyncio.start_server(
            self._handle_client,
            self.traffic_host,
            self.traffic_port,
        )
        self._control_server = await asyncio.start_server(
            self._handle_control,
            self.control_host,
            self.control_port,
        )
        self.traffic_port = self._bound_port(self._traffic_server)
        self.control_port = self._bound_port(self._control_server)

    async def serve(self) -> None:
        await self.start()
        await self.wait_closed()

    async def wait_closed(self) -> None:
        await self._shutdown_event.wait()
        await self.close()

    async def close(self) -> None:
        for server in (self._traffic_server, self._control_server):
            if server is not None:
                server.close()
                await server.wait_closed()
        for writer in list(self._active_writers):
            writer.close()
        await asyncio.gather(
            *(writer.wait_closed() for writer in list(self._active_writers)),
            return_exceptions=True,
        )

    async def configure(self, updates: dict[str, Any]) -> ThrottleConfig:
        async with self._config_lock:
            if "bandwidth_bps" in updates:
                self.config.bandwidth_bps = int(updates["bandwidth_bps"])
                self.bucket.reset_rate(self.config.bandwidth_bps)
            if "latency_ms" in updates:
                self.config.latency_ms = int(updates["latency_ms"])
            if "loss_pct" in updates:
                self.config.loss_pct = float(updates["loss_pct"])
            if "jitter_ms" in updates:
                self.config.jitter_ms = int(updates["jitter_ms"])
            if "profile" in updates:
                self.config.profile = updates["profile"]
            self._validate_config()
            return self.config

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self._active_writers.add(writer)
        try:
            header_bytes, _ = await self._read_headers(reader)
            request_line, headers = self._parse_request(header_bytes)
            method, target, version = self._split_request_line(request_line)
            self.config.requests_handled += 1

            if method.upper() == "CONNECT":
                await self._handle_connect(reader, writer, target)
            else:
                await self._handle_http(reader, writer, method, target, version, headers)
        except Exception as exc:
            await self._send_error(writer, 502, str(exc))
        finally:
            writer.close()
            await writer.wait_closed()
            self._active_writers.discard(writer)

    async def _handle_connect(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        target: str,
    ) -> None:
        host, port = self._split_host_port(target, default_port=443)
        upstream_reader, upstream_writer = await asyncio.open_connection(host, port)
        self._active_writers.add(upstream_writer)
        client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await client_writer.drain()

        client_to_upstream = asyncio.create_task(
            self._pipe(client_reader, upstream_writer, direction="sent")
        )
        upstream_to_client = asyncio.create_task(
            self._pipe(upstream_reader, client_writer, direction="received")
        )
        try:
            done, pending = await asyncio.wait(
                {client_to_upstream, upstream_to_client},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)
        finally:
            upstream_writer.close()
            await upstream_writer.wait_closed()
            self._active_writers.discard(upstream_writer)

    async def _handle_http(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        method: str,
        target: str,
        version: str,
        headers: dict[str, str],
    ) -> None:
        parsed = urlsplit(target)
        if not parsed.scheme or not parsed.hostname:
            host_header = headers.get("host")
            if not host_header:
                raise ValueError("HTTP proxy requests must include absolute URL or Host header")
            host, port = self._split_host_port(host_header, default_port=80)
            path = target
        else:
            host = parsed.hostname
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            path = parsed.path or "/"
            if parsed.query:
                path = f"{path}?{parsed.query}"

        upstream_reader, upstream_writer = await asyncio.open_connection(host, port)
        self._active_writers.add(upstream_writer)
        outbound_headers = {
            key: value
            for key, value in headers.items()
            if key.lower() not in {"proxy-connection", "connection"}
        }
        outbound_headers["host"] = headers.get("host", f"{host}:{port}")
        request = self._build_request(method, path, version, outbound_headers)
        await self._write_throttled(upstream_writer, request, direction="sent")

        content_length = int(headers.get("content-length", "0") or "0")
        if content_length:
            body = await client_reader.readexactly(content_length)
            await self._write_throttled(upstream_writer, body, direction="sent")

        try:
            await self._pipe(upstream_reader, client_writer, direction="received")
        finally:
            upstream_writer.close()
            await upstream_writer.wait_closed()
            self._active_writers.discard(upstream_writer)

    async def _pipe(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        direction: str,
    ) -> None:
        while True:
            chunk = await reader.read(READ_CHUNK_SIZE)
            if not chunk:
                break
            await self._write_throttled(writer, chunk, direction=direction)

    async def _write_throttled(
        self,
        writer: asyncio.StreamWriter,
        chunk: bytes,
        *,
        direction: str,
    ) -> None:
        wait = self.bucket.consume(len(chunk))
        if wait:
            await asyncio.sleep(wait)

        delay = calculate_delay_seconds(self.config.latency_ms, self.config.jitter_ms)
        if delay:
            await asyncio.sleep(delay)

        if should_drop_chunk(self.config.loss_pct):
            return

        writer.write(chunk)
        await writer.drain()
        if direction == "sent":
            self.config.bytes_sent += len(chunk)
        else:
            self.config.bytes_received += len(chunk)

    async def _handle_control(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            header_bytes, body_prefix = await self._read_headers(reader)
            request_line, headers = self._parse_request(header_bytes)
            method, path, _ = request_line.split(" ", 2)
            body = body_prefix
            content_length = int(headers.get("content-length", "0") or "0")
            if content_length > len(body):
                body += await reader.readexactly(content_length - len(body))

            if method == "GET" and path == "/status":
                await self._send_json(writer, self._status_payload())
            elif method == "POST" and path == "/configure":
                updates = json.loads(body.decode("utf-8") or "{}")
                config = await self.configure(updates)
                await self._send_json(writer, config.to_dict())
            elif method == "POST" and path == "/shutdown":
                await self._send_json(writer, {"ok": True})
                self._shutdown_event.set()
            else:
                await self._send_json(writer, {"error": "not found"}, status=404)
        except Exception as exc:
            await self._send_json(writer, {"error": str(exc)}, status=400)
        finally:
            writer.close()
            await writer.wait_closed()

    def _status_payload(self) -> dict[str, Any]:
        payload = self.config.to_dict()
        payload.update(
            {
                "traffic_port": self.traffic_port,
                "control_port": self.control_port,
                "running_for_seconds": max(0.0, time.time() - self.config.started_at),
                "bandwidth_model": "shared_bidirectional",
            }
        )
        return payload

    async def _read_headers(self, reader: asyncio.StreamReader) -> tuple[bytes, bytes]:
        data = await reader.readuntil(b"\r\n\r\n")
        if len(data) > HEADER_LIMIT:
            raise ValueError("HTTP headers too large")
        return data[:-4], b""

    def _parse_request(self, header_bytes: bytes) -> tuple[str, dict[str, str]]:
        lines = header_bytes.decode("iso-8859-1").split("\r\n")
        request_line = lines[0]
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if not line:
                continue
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()
        return request_line, headers

    def _split_request_line(self, request_line: str) -> tuple[str, str, str]:
        parts = request_line.split()
        if len(parts) != 3:
            raise ValueError("malformed HTTP request line")
        method, target, version = parts
        if not version.startswith("HTTP/"):
            raise ValueError("malformed HTTP version")
        return method, target, version

    def _build_request(
        self,
        method: str,
        path: str,
        version: str,
        headers: dict[str, str],
    ) -> bytes:
        lines = [f"{method} {path} {version}"]
        lines.extend(f"{key}: {value}" for key, value in headers.items())
        return ("\r\n".join(lines) + "\r\n\r\n").encode("iso-8859-1")

    async def _send_json(
        self,
        writer: asyncio.StreamWriter,
        payload: dict[str, Any],
        *,
        status: int = 200,
    ) -> None:
        body = json.dumps(payload).encode("utf-8")
        reason = "OK" if status == 200 else "Error"
        writer.write(
            (
                f"HTTP/1.1 {status} {reason}\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n"
                "\r\n"
            ).encode("ascii")
            + body
        )
        await writer.drain()

    async def _send_error(
        self,
        writer: asyncio.StreamWriter,
        status: int,
        message: str,
    ) -> None:
        body = message.encode("utf-8")
        writer.write(
            (
                f"HTTP/1.1 {status} Error\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n"
                "\r\n"
            ).encode("ascii")
            + body
        )
        await writer.drain()

    def _split_host_port(self, target: str, *, default_port: int) -> tuple[str, int]:
        if target.startswith("["):
            host, _, rest = target[1:].partition("]")
            port = int(rest.removeprefix(":") or default_port)
            return host, port

        host, sep, port_text = target.partition(":")
        return host, int(port_text) if sep else default_port

    def _validate_config(self) -> None:
        if self.config.bandwidth_bps < 0:
            raise ValueError("bandwidth_bps must be non-negative")
        if self.config.latency_ms < 0:
            raise ValueError("latency_ms must be non-negative")
        if self.config.loss_pct < 0 or self.config.loss_pct > 1:
            raise ValueError("loss_pct must be between 0.0 and 1.0")
        if self.config.jitter_ms < 0:
            raise ValueError("jitter_ms must be non-negative")

    def _bound_port(self, server: asyncio.AbstractServer) -> int:
        sockets = server.sockets or []
        if not sockets:
            raise RuntimeError("server did not expose a bound socket")
        return int(sockets[0].getsockname()[1])
