"""Asyncio HTTP/HTTPS forward proxy with a local control API."""

from __future__ import annotations

import asyncio
import collections
import importlib.resources
import json
import logging
import socket
import struct
import time
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlsplit

from .throttle import TokenBucket, calculate_delay_seconds, should_drop_chunk

logger = logging.getLogger("netshape.proxy")

_LOG_BUFFER_SIZE = 200
_LOG_FORMAT = "%(asctime)s %(levelname)-5s %(name)s: %(message)s"
_LOG_DATE_FORMAT = "%H:%M:%S"


class _ProxyLogHandler(logging.Handler):
    """Logging handler that appends formatted records to a deque."""

    def __init__(self, buffer: collections.deque) -> None:
        super().__init__()
        self._buffer = buffer

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._buffer.append(self.format(record))
        except Exception:
            self.handleError(record)

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
        self._sse_writers: set[asyncio.StreamWriter] = set()
        self._last_status_push: dict[str, Any] = {}
        # Instantaneous throughput tracking (reset each SSE tick)
        self._last_bytes_sent: int = 0
        self._last_bytes_received: int = 0
        self._last_metrics_time: float = time.monotonic()
        # Live log buffer — captures all netshape.* log records
        self._log_buffer: collections.deque[str] = collections.deque(maxlen=_LOG_BUFFER_SIZE)
        self._log_handler = _ProxyLogHandler(self._log_buffer)
        self._log_handler.setFormatter(
            logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT)
        )
        logging.getLogger("netshape").addHandler(self._log_handler)

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
        logging.getLogger("netshape").removeHandler(self._log_handler)
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
        protocol = "http"
        reply_sent = False
        try:
            if should_drop_chunk(self.config.loss_pct):
                return
            first_byte = await reader.readexactly(1)
            if first_byte == b"\x05":
                protocol = "socks5"
                try:
                    await self._handle_socks5(reader, writer)
                except Exception:
                    pass
                return

            header_bytes, _ = await self._read_headers(reader, prefix=first_byte)
            request_line, headers = self._parse_request(header_bytes)
            method, target, version = self._split_request_line(request_line)
            self.config.requests_handled += 1

            if method.upper() == "CONNECT":
                await self._handle_connect(reader, writer, target)
            else:
                await self._handle_http(reader, writer, method, target, version, headers)
        except Exception:
            if protocol == "socks5" and not reply_sent:
                await self._send_socks5_reply(writer, 0x01)
            else:
                await self._send_error(writer, 502)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionResetError, OSError):
                pass
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
        await self._tunnel_streams(client_reader, upstream_reader, client_writer, upstream_writer)

    async def _handle_socks5(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        methods_count = (await client_reader.readexactly(1))[0]
        methods = await client_reader.readexactly(methods_count)
        if 0x00 not in methods:
            client_writer.write(b"\x05\xff")
            await client_writer.drain()
            return
        client_writer.write(b"\x05\x00")
        await client_writer.drain()

        version, command, reserved, address_type = await client_reader.readexactly(4)
        if version != 0x05 or reserved != 0x00:
            await self._send_socks5_reply(client_writer, 0x01, address_type=address_type)
            return
        if command != 0x01:
            await self._send_socks5_reply(client_writer, 0x07, address_type=address_type)
            return

        try:
            host = await self._read_socks5_host(client_reader, address_type)
        except ValueError:
            await self._send_socks5_reply(client_writer, 0x08)
            return
        port = struct.unpack(">H", await client_reader.readexactly(2))[0]
        self.config.requests_handled += 1

        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(host, port)
        except OSError:
            await self._send_socks5_reply(client_writer, 0x04, address_type=address_type)
            return

        self._active_writers.add(upstream_writer)
        await self._send_socks5_reply(client_writer, 0x00, address_type=address_type)
        try:
            await self._tunnel_streams(client_reader, upstream_reader, client_writer, upstream_writer)
        except Exception:
            pass

    async def _tunnel_streams(
        self,
        client_reader: asyncio.StreamReader,
        upstream_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        upstream_writer: asyncio.StreamWriter,
    ) -> None:
        connection_start = time.time()
        client_to_upstream = asyncio.create_task(
            self._pipe(client_reader, upstream_writer, direction="sent", connection_start=connection_start)
        )
        upstream_to_client = asyncio.create_task(
            self._pipe(upstream_reader, client_writer, direction="received", connection_start=connection_start)
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
            try:
                await upstream_writer.wait_closed()
            except (ConnectionResetError, OSError):
                pass
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
        transfer_encoding = headers.get("transfer-encoding", "").lower()
        if content_length:
            body = await client_reader.readexactly(content_length)
            await self._write_throttled(upstream_writer, body, direction="sent")
        elif "chunked" in transfer_encoding:
            await self._forward_chunked_body(client_reader, upstream_writer)

        try:
            connection_start = time.time()
            await self._pipe(upstream_reader, client_writer, direction="received", connection_start=connection_start)
        finally:
            upstream_writer.close()
            try:
                await upstream_writer.wait_closed()
            except (ConnectionResetError, OSError):
                pass
            self._active_writers.discard(upstream_writer)

    async def _pipe(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        direction: str,
        connection_start: float | None = None,
    ) -> None:
        latency_applied = False
        while True:
            chunk = await reader.read(READ_CHUNK_SIZE)
            if not chunk:
                break
            if not latency_applied:
                await self._apply_latency(connection_start)
                latency_applied = True
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

        writer.write(chunk)
        await writer.drain()
        if direction == "sent":
            self.config.bytes_sent += len(chunk)
        else:
            self.config.bytes_received += len(chunk)

    async def _apply_latency(self, connection_start: float | None = None) -> None:
        delay = calculate_delay_seconds(self.config.latency_ms, self.config.jitter_ms)
        if delay:
            if connection_start is not None:
                elapsed = time.time() - connection_start
                remaining = delay - elapsed
                if remaining > 0:
                    await asyncio.sleep(remaining)
            else:
                await asyncio.sleep(delay)

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
            elif method == "GET" and path == "/events":
                await self._serve_sse(writer)
                return
            elif method == "GET" and path == "/logs":
                await self._serve_logs(writer)
            elif method == "GET" and path.startswith("/dashboard"):
                await self._serve_dashboard(writer, path)
            elif method == "GET" and path == "/":
                await self._serve_dashboard(writer, "/dashboard/index.html")
            else:
                await self._send_json(writer, {"error": "not found"}, status=404)
        except Exception as exc:
            await self._send_json(writer, {"error": str(exc)}, status=400)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionResetError, OSError):
                pass

    def _status_payload(self) -> dict[str, Any]:
        payload = self.config.to_dict()
        payload.update(
            {
                "traffic_port": self.traffic_port,
                "control_port": self.control_port,
                "running_for_seconds": max(0.0, time.time() - self.config.started_at),
                "bandwidth_model": "shared_bidirectional",
                "protocols": ["http", "https-connect", "socks5-connect"],
            }
        )
        return payload

    async def _read_headers(
        self,
        reader: asyncio.StreamReader,
        *,
        prefix: bytes = b"",
    ) -> tuple[bytes, bytes]:
        data = prefix + await reader.readuntil(b"\r\n\r\n")
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

    async def _serve_sse(self, writer: asyncio.StreamWriter) -> None:
        """Serve Server-Sent Events stream for the dashboard."""
        peer = writer.get_extra_info("peername", "<unknown>")
        logger.info("SSE client connected from %s", peer)
        try:
            writer.write(
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: text/event-stream\r\n"
                "Cache-Control: no-cache\r\n"
                "Connection: keep-alive\r\n"
                "\r\n".encode("ascii")
            )
            await writer.drain()
            self._sse_writers.add(writer)
            while True:
                await asyncio.sleep(1.0)
                data = self._build_sse_data()
                writer.write(f"data: {json.dumps(data)}\n\n".encode("utf-8"))
                await writer.drain()
        except (ConnectionResetError, OSError, BrokenPipeError):
            pass
        finally:
            self._sse_writers.discard(writer)
            logger.info("SSE client disconnected from %s", peer)

    async def _serve_logs(self, writer: asyncio.StreamWriter) -> None:
        """Return the recent proxy log buffer as JSON."""
        await self._send_json(writer, {"lines": list(self._log_buffer)})

    def _build_sse_data(self) -> dict[str, Any]:
        """Build the data payload for SSE events.

        Throughput is measured as a per-tick delta (bytes since the last call)
        so the chart reflects the current rate rather than the session average.
        """
        now = time.monotonic()
        elapsed = max(0.001, now - self._last_metrics_time)

        bytes_sent_delta = self.config.bytes_sent - self._last_bytes_sent
        bytes_recv_delta = self.config.bytes_received - self._last_bytes_received
        sent_mbps = (bytes_sent_delta * 8) / elapsed / 1_000_000
        recv_mbps = (bytes_recv_delta * 8) / elapsed / 1_000_000

        self._last_bytes_sent = self.config.bytes_sent
        self._last_bytes_received = self.config.bytes_received
        self._last_metrics_time = now

        bw = self.config.bandwidth_bps
        loss = self.config.loss_pct
        latency = self.config.latency_ms

        if loss > 0.05 or latency > 500:
            classification = "SEVERE"
        elif loss > 0.01 or (0 < bw < 300_000) or latency > 200:
            classification = "POOR"
        elif (0 < bw < 2_000_000) or latency > 50:
            classification = "SLOW"
        else:
            classification = "NORMAL"

        return {
            "download_mbps": round(recv_mbps, 3),
            "upload_mbps": round(sent_mbps, 3),
            "latency_ms": latency,
            "loss_pct": loss,
            "classification": classification,
            "connected": True,
            "traffic_port": self.traffic_port,
            "requests_handled": self.config.requests_handled,
        }

    async def _serve_dashboard(self, writer: asyncio.StreamWriter, path: str) -> None:
        """Serve static dashboard files from the netshape.dashboard package."""
        logger.debug("Dashboard request: %s", path)
        file_map = {
            "/dashboard": "index.html",
            "/dashboard/": "index.html",
            "/dashboard/index.html": "index.html",
            "/dashboard/style.css": "style.css",
            "/dashboard/app.js": "app.js",
        }
        filename = file_map.get(path)
        if not filename:
            await self._send_json(writer, {"error": "not found"}, status=404)
            return

        try:
            content = importlib.resources.files("netshape.dashboard").joinpath(filename).read_text("utf-8")
        except FileNotFoundError:
            await self._send_json(writer, {"error": "not found"}, status=404)
            return

        content_type = {
            "index.html": "text/html",
            "style.css": "text/css",
            "app.js": "application/javascript",
        }.get(filename, "text/plain")

        body = content.encode("utf-8")
        writer.write(
            (
                f"HTTP/1.1 200 OK\r\n"
                f"Content-Type: {content_type}\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n"
                "\r\n"
            ).encode("ascii")
            + body
        )
        await writer.drain()

    async def _read_socks5_host(self, reader: asyncio.StreamReader, address_type: int) -> str:
        if address_type == 0x01:
            return ".".join(str(part) for part in await reader.readexactly(4))
        if address_type == 0x03:
            length = (await reader.readexactly(1))[0]
            return (await reader.readexactly(length)).decode("idna")
        if address_type == 0x04:
            return socket.inet_ntop(socket.AF_INET6, await reader.readexactly(16))
        raise ValueError("unsupported SOCKS5 address type")

    async def _send_socks5_reply(
        self,
        writer: asyncio.StreamWriter,
        code: int,
        *,
        address_type: int = 0x01,
    ) -> None:
        if address_type == 0x03:
            bind_address = b"\x00"
        elif address_type == 0x04:
            bind_address = b"\x00" * 16
        else:
            address_type = 0x01
            bind_address = b"\x00" * 4
        writer.write(b"\x05" + bytes([code]) + b"\x00" + bytes([address_type]) + bind_address + b"\x00\x00")
        await writer.drain()

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

    async def _forward_chunked_body(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while True:
            line = await reader.readuntil(b"\r\n")
            await self._write_throttled(writer, line, direction="sent")
            chunk_size_hex = line.decode("ascii").split(";", 1)[0].strip()
            chunk_size = int(chunk_size_hex, 16)
            if chunk_size == 0:
                await self._write_throttled(writer, b"\r\n", direction="sent")
                break
            chunk = await reader.readexactly(chunk_size)
            await self._write_throttled(writer, chunk, direction="sent")
            crlf = await reader.readexactly(2)
            await self._write_throttled(writer, crlf, direction="sent")

    async def _send_error(
        self,
        writer: asyncio.StreamWriter,
        status: int,
        message: str = "",
    ) -> None:
        body = "Proxy Error".encode("utf-8") if not message else message.encode("utf-8")
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
