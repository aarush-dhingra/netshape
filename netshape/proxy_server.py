"""Asyncio HTTP/HTTPS forward proxy with a local control API."""

from __future__ import annotations

import asyncio
import collections
import importlib.resources
import json
import logging
import re
import socket
import struct
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlsplit

from .profiles import load_builtin_profiles
from .throttle import TokenBucket, calculate_delay_seconds, should_drop_chunk

logger = logging.getLogger("netshape.proxy")

_LOG_BUFFER_SIZE = 200
_LOG_FORMAT = "%(asctime)s %(levelname)-5s %(name)s: %(message)s"
_LOG_DATE_FORMAT = "%H:%M:%S"

# Prometheus metric descriptors: (name, type, help)
_PROMETHEUS_METRICS = [
    ("netshape_bytes_sent_total", "counter", "Total bytes forwarded to upstream"),
    ("netshape_bytes_received_total", "counter", "Total bytes forwarded to clients"),
    ("netshape_connections_total", "counter", "Total client connections accepted"),
    ("netshape_connections_active", "gauge", "Currently active client connections"),
    ("netshape_requests_handled_total", "counter", "Total requests handled (HTTP + SOCKS5)"),
    ("netshape_throttle_sleep_seconds_total", "counter", "Total time spent in throttle sleeps (s)"),
    ("netshape_drops_total", "counter", "Total connections dropped due to loss_pct"),
    ("netshape_latency_added_seconds_total", "counter", "Total artificial latency injected (s)"),
    ("netshape_config_bandwidth_bps", "gauge", "Current configured bandwidth limit (0 = unlimited)"),
    ("netshape_config_latency_ms", "gauge", "Current configured latency (ms)"),
    ("netshape_config_loss_pct", "gauge", "Current configured packet loss fraction (0.0–1.0)"),
    ("netshape_rules_count", "gauge", "Number of active per-endpoint throttle rules"),
]


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
_CONTROL_BODY_LIMIT = 1 * 1024 * 1024  # 1 MB — prevents OOM from oversized POST bodies


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
    connections_total: int = 0
    drops_total: int = 0
    started_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ThrottleRule:
    """Per-target throttle rule.

    ``pattern`` is a case-insensitive regex matched against the target string:
    * HTTPS CONNECT and SOCKS5 — **hostname only** (path is encrypted).
    * Plain HTTP — **full absolute URL**.

    Any field left as ``None`` falls back to the global proxy setting.
    """

    id: str
    pattern: str
    bandwidth_bps: int | None = None
    latency_ms: int | None = None
    jitter_ms: int | None = None
    loss_pct: float | None = None
    comment: str = ""
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _ConnRules:
    """Resolved per-connection throttle settings (internal)."""

    bucket: TokenBucket
    latency_ms: int
    jitter_ms: int
    loss_pct: float
    rule_id: str | None = None


@dataclass
class _ScenarioState:
    """Mutable scenario execution state."""

    running: bool = False
    name: str = ""
    current_phase: int = 0
    total_phases: int = 0
    phase_name: str = ""
    phase_elapsed_s: float = 0.0
    phase_duration_s: float = 0.0
    started_at: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "running": self.running,
            "name": self.name,
            "current_phase": self.current_phase,
            "total_phases": self.total_phases,
            "phase_name": self.phase_name,
            "phase_elapsed_s": round(self.phase_elapsed_s, 2),
            "phase_duration_s": round(self.phase_duration_s, 2),
            "started_at": self.started_at,
        }
        if self.error:
            d["error"] = self.error
        return d


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
        # Live log buffer
        self._log_buffer: collections.deque[str] = collections.deque(maxlen=_LOG_BUFFER_SIZE)
        self._log_handler = _ProxyLogHandler(self._log_buffer)
        self._log_handler.setFormatter(
            logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT)
        )
        _nsl = logging.getLogger("netshape")
        _nsl.addHandler(self._log_handler)
        # Ensure INFO-level messages reach the dashboard log buffer even when the
        # root logger defaults to WARNING (which is Python's default).
        if _nsl.level == logging.NOTSET or _nsl.level > logging.INFO:
            _nsl.setLevel(logging.INFO)
        # Per-endpoint throttle rules
        self._rules: list[ThrottleRule] = []
        self._rule_buckets: dict[str, TokenBucket] = {}
        self._rule_patterns: dict[str, re.Pattern[str]] = {}
        # Phase 5 metrics counters
        self._connections_active: int = 0
        self._throttle_sleep_seconds: float = 0.0
        self._latency_added_seconds: float = 0.0
        # Phase 4 scenario runner
        self._scenario_task: asyncio.Task[None] | None = None
        self._scenario_stop: asyncio.Event = asyncio.Event()
        self._scenario_state: _ScenarioState = _ScenarioState()

    # ── Lifecycle ────────────────────────────────────────────────────────────

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
        if self._scenario_task and not self._scenario_task.done():
            self._scenario_task.cancel()
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

    # ── Configuration ────────────────────────────────────────────────────────

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

    # ── Rule management ──────────────────────────────────────────────────────

    def add_rule(self, rule_data: dict[str, Any]) -> ThrottleRule:
        rule_id = str(uuid.uuid4())
        pattern = rule_data.get("pattern")
        if not pattern:
            raise ValueError("rule must have a non-empty pattern")
        try:
            compiled = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"invalid regex pattern: {exc}") from exc
        rule = ThrottleRule(
            id=rule_id,
            pattern=pattern,
            bandwidth_bps=_opt_int(rule_data.get("bandwidth_bps")),
            latency_ms=_opt_int(rule_data.get("latency_ms")),
            jitter_ms=_opt_int(rule_data.get("jitter_ms")),
            loss_pct=_opt_float(rule_data.get("loss_pct")),
            comment=str(rule_data.get("comment", "")),
        )
        self._rules.append(rule)
        self._rule_patterns[rule_id] = compiled
        logger.info(
            "Rule added: id=%s pattern=%r bw=%s lat=%s loss=%s comment=%r",
            rule_id[:8], pattern, rule.bandwidth_bps, rule.latency_ms, rule.loss_pct, rule.comment,
        )
        return rule

    def remove_rule(self, rule_id: str) -> bool:
        """Remove a rule by exact ID or unambiguous prefix (e.g. first 8 chars)."""
        # Resolve prefix → full ID so the bucket/pattern maps can be cleaned up.
        matched = [r.id for r in self._rules if r.id == rule_id or r.id.startswith(rule_id)]
        if len(matched) > 1:
            # Ambiguous prefix — require more characters
            return False
        if not matched:
            return False
        full_id = matched[0]
        self._rules = [r for r in self._rules if r.id != full_id]
        self._rule_buckets.pop(full_id, None)
        self._rule_patterns.pop(full_id, None)
        logger.info("Rule removed: id=%s", full_id[:8])
        return True

    def toggle_rule(self, rule_id: str, enabled: bool) -> ThrottleRule | None:
        """Enable or disable a rule by exact ID or unambiguous prefix."""
        matched = [r for r in self._rules if r.id == rule_id or r.id.startswith(rule_id)]
        if len(matched) != 1:
            return None
        rule = matched[0]
        rule.enabled = enabled
        logger.info(
            "Rule %s: id=%s pattern=%r",
            "enabled" if enabled else "disabled",
            rule.id[:8],
            rule.pattern,
        )
        return rule

    def list_rules(self) -> list[ThrottleRule]:
        return list(self._rules)

    def _resolve_rules(self, target: str) -> _ConnRules:
        for rule in self._rules:
            if not rule.enabled:
                continue
            pattern = self._rule_patterns.get(rule.id)
            if pattern is None:
                try:
                    pattern = re.compile(rule.pattern, re.IGNORECASE)
                    self._rule_patterns[rule.id] = pattern
                except re.error:
                    continue
            if pattern.search(target):
                bps = rule.bandwidth_bps if rule.bandwidth_bps is not None else self.config.bandwidth_bps
                if rule.id not in self._rule_buckets:
                    self._rule_buckets[rule.id] = TokenBucket(bps)
                logger.debug("Rule match: target=%r rule=%s (%s)", target, rule.id[:8], rule.pattern)
                return _ConnRules(
                    bucket=self._rule_buckets[rule.id],
                    latency_ms=rule.latency_ms if rule.latency_ms is not None else self.config.latency_ms,
                    jitter_ms=rule.jitter_ms if rule.jitter_ms is not None else self.config.jitter_ms,
                    loss_pct=rule.loss_pct if rule.loss_pct is not None else self.config.loss_pct,
                    rule_id=rule.id,
                )
        return _ConnRules(
            bucket=self.bucket,
            latency_ms=self.config.latency_ms,
            jitter_ms=self.config.jitter_ms,
            loss_pct=self.config.loss_pct,
        )

    # ── Scenario management ───────────────────────────────────────────────────

    async def start_scenario(self, scenario_dict: dict[str, Any]) -> _ScenarioState:
        """Start a scenario; stops any currently running one first."""
        await self.stop_scenario()
        self._scenario_stop.clear()
        self._scenario_task = asyncio.create_task(
            self._run_scenario_task(scenario_dict),
            name="netshape-scenario",
        )
        await asyncio.sleep(0)  # give task one tick to initialise
        return self._scenario_state

    async def stop_scenario(self) -> _ScenarioState:
        """Signal the running scenario to stop and wait up to 3 s for it."""
        if self._scenario_task and not self._scenario_task.done():
            self._scenario_stop.set()
            try:
                await asyncio.wait_for(asyncio.shield(self._scenario_task), timeout=3.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._scenario_task.cancel()
        self._scenario_state.running = False
        return self._scenario_state

    async def _run_scenario_task(self, scenario_dict: dict[str, Any]) -> None:
        from .scenario import ScenarioError, parse_scenario_dict

        try:
            scenario = parse_scenario_dict(scenario_dict)
        except Exception as exc:
            msg = str(exc)
            logger.error(
                "Scenario parse error: %s\n"
                "  Dict keys at top level: %s\n"
                "  Phase[0] keys: %s",
                msg,
                list(scenario_dict.keys()) if isinstance(scenario_dict, dict) else type(scenario_dict),
                list(scenario_dict["phases"][0].keys())
                if isinstance(scenario_dict, dict) and scenario_dict.get("phases")
                else "(no phases)",
            )
            self._scenario_state.error = msg
            self._scenario_state.running = False
            return

        pre_config = {
            "bandwidth_bps": self.config.bandwidth_bps,
            "latency_ms": self.config.latency_ms,
            "loss_pct": self.config.loss_pct,
            "jitter_ms": self.config.jitter_ms,
            "profile": self.config.profile,
        }
        logger.info("Scenario start: %r (%d phases, %.0fs total)",
                    scenario.name, len(scenario.phases), scenario.total_duration_ms() / 1000)

        self._scenario_state.running = True
        self._scenario_state.name = scenario.name
        self._scenario_state.total_phases = len(scenario.phases)
        self._scenario_state.current_phase = 0
        self._scenario_state.error = None
        self._scenario_state.started_at = time.time()

        try:
            for i, phase in enumerate(scenario.phases):
                if self._scenario_stop.is_set():
                    logger.info("Scenario stopped at phase %d/%d", i + 1, len(scenario.phases))
                    break

                self._scenario_state.current_phase = i + 1
                self._scenario_state.phase_name = phase.name
                self._scenario_state.phase_duration_s = phase.duration_ms / 1000.0
                self._scenario_state.phase_elapsed_s = 0.0

                await self.configure(phase.to_config())
                logger.info(
                    "Phase %d/%d: %r — bw=%d lat=%d loss=%.3f jitter=%d (%.1fs)",
                    i + 1, len(scenario.phases), phase.name,
                    phase.bandwidth_bps, phase.latency_ms, phase.loss_pct, phase.jitter_ms,
                    phase.duration_ms / 1000.0,
                )

                phase_start = time.monotonic()
                duration_s = phase.duration_ms / 1000.0
                while not self._scenario_stop.is_set():
                    elapsed = time.monotonic() - phase_start
                    self._scenario_state.phase_elapsed_s = elapsed
                    remaining = duration_s - elapsed
                    if remaining <= 0:
                        break
                    await asyncio.sleep(min(0.25, remaining))
            else:
                logger.info("Scenario completed: %r", scenario.name)
        finally:
            logger.info(
                "Scenario: restoring pre-scenario config — bw=%d lat=%d",
                pre_config["bandwidth_bps"], pre_config["latency_ms"],
            )
            try:
                await self.configure(pre_config)
            except Exception as exc:
                logger.warning("Failed to restore pre-scenario config: %s", exc)
            self._scenario_state.running = False

    # ── Traffic handlers ──────────────────────────────────────────────────────

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self._active_writers.add(writer)
        self.config.connections_total += 1
        self._connections_active += 1
        protocol = "http"
        reply_sent = False
        try:
            first_byte = await reader.readexactly(1)
            if first_byte == b"\x05":
                protocol = "socks5"
                try:
                    await self._handle_socks5(reader, writer)
                except Exception:  # nosec B110 – connection teardown errors are not actionable
                    pass
                return

            header_bytes = await self._read_headers(reader, prefix=first_byte)
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
            self._connections_active -= 1
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
        rules = self._resolve_rules(host)
        if should_drop_chunk(rules.loss_pct):
            self.config.drops_total += 1
            logger.debug("CONNECT %s dropped (loss_pct=%.2f)", host, rules.loss_pct)
            return
        upstream_reader, upstream_writer = await asyncio.open_connection(host, port)
        self._active_writers.add(upstream_writer)
        client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await client_writer.drain()
        await self._tunnel_streams(
            client_reader, upstream_reader, client_writer, upstream_writer, rules=rules
        )

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

        rules = self._resolve_rules(host)
        if should_drop_chunk(rules.loss_pct):
            self.config.drops_total += 1
            logger.debug("SOCKS5 %s dropped (loss_pct=%.2f)", host, rules.loss_pct)
            await self._send_socks5_reply(client_writer, 0x04, address_type=address_type)
            return

        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(host, port)
        except OSError:
            await self._send_socks5_reply(client_writer, 0x04, address_type=address_type)
            return

        self._active_writers.add(upstream_writer)
        await self._send_socks5_reply(client_writer, 0x00, address_type=address_type)
        try:
            await self._tunnel_streams(
                client_reader, upstream_reader, client_writer, upstream_writer, rules=rules
            )
        except Exception:  # nosec B110 – tunnel teardown errors (EOF, reset) are not actionable
            pass

    async def _tunnel_streams(
        self,
        client_reader: asyncio.StreamReader,
        upstream_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        upstream_writer: asyncio.StreamWriter,
        *,
        rules: _ConnRules | None = None,
    ) -> None:
        connection_start = time.time()
        client_to_upstream = asyncio.create_task(
            self._pipe(client_reader, upstream_writer,
                       direction="sent", connection_start=connection_start, rules=rules)
        )
        upstream_to_client = asyncio.create_task(
            self._pipe(upstream_reader, client_writer,
                       direction="received", connection_start=connection_start, rules=rules)
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

        match_target = target if parsed.scheme else f"http://{host}{path}"
        rules = self._resolve_rules(match_target)
        if should_drop_chunk(rules.loss_pct):
            self.config.drops_total += 1
            logger.debug("HTTP %s %s dropped (loss_pct=%.2f)", method, match_target, rules.loss_pct)
            return

        upstream_reader, upstream_writer = await asyncio.open_connection(host, port)
        self._active_writers.add(upstream_writer)
        outbound_headers = {
            key: value
            for key, value in headers.items()
            if key.lower() not in {"proxy-connection", "connection"}
        }
        outbound_headers["host"] = headers.get("host", f"{host}:{port}")
        request = self._build_request(method, path, version, outbound_headers)
        await self._write_throttled(upstream_writer, request, direction="sent", rules=rules)

        content_length = int(headers.get("content-length", "0") or "0")
        transfer_encoding = headers.get("transfer-encoding", "").lower()
        if content_length:
            body = await client_reader.readexactly(content_length)
            await self._write_throttled(upstream_writer, body, direction="sent", rules=rules)
        elif "chunked" in transfer_encoding:
            await self._forward_chunked_body(client_reader, upstream_writer, rules=rules)

        try:
            connection_start = time.time()
            await self._pipe(upstream_reader, client_writer,
                             direction="received", connection_start=connection_start, rules=rules)
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
        rules: _ConnRules | None = None,
    ) -> None:
        latency_applied = False
        while True:
            chunk = await reader.read(READ_CHUNK_SIZE)
            if not chunk:
                break
            if not latency_applied:
                await self._apply_latency(connection_start, rules=rules)
                latency_applied = True
            await self._write_throttled(writer, chunk, direction=direction, rules=rules)

    async def _write_throttled(
        self,
        writer: asyncio.StreamWriter,
        chunk: bytes,
        *,
        direction: str,
        rules: _ConnRules | None = None,
    ) -> None:
        bucket = rules.bucket if rules is not None else self.bucket
        wait = bucket.consume(len(chunk))
        if wait:
            self._throttle_sleep_seconds += wait
            await asyncio.sleep(wait)

        writer.write(chunk)
        await writer.drain()
        if direction == "sent":
            self.config.bytes_sent += len(chunk)
        else:
            self.config.bytes_received += len(chunk)

    async def _apply_latency(
        self,
        connection_start: float | None = None,
        *,
        rules: _ConnRules | None = None,
    ) -> None:
        lat = rules.latency_ms if rules is not None else self.config.latency_ms
        jit = rules.jitter_ms if rules is not None else self.config.jitter_ms
        delay = calculate_delay_seconds(lat, jit)
        if delay:
            actual_sleep = 0.0
            if connection_start is not None:
                elapsed = time.time() - connection_start
                remaining = delay - elapsed
                if remaining > 0:
                    actual_sleep = remaining
                    await asyncio.sleep(remaining)
            else:
                actual_sleep = delay
                await asyncio.sleep(delay)
            self._latency_added_seconds += actual_sleep

    # ── Control API ───────────────────────────────────────────────────────────

    async def _handle_control(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            header_bytes = await self._read_headers(reader)
            request_line, headers = self._parse_request(header_bytes)
            method, path, _ = request_line.split(" ", 2)

            # ── CSRF / DNS-rebinding protection ───────────────────────────────
            # Mutating requests from a browser tab on a malicious page would
            # carry an Origin header pointing to the attacker's site. We reject
            # any mutating request whose Origin is not our own control port or
            # absent (CLI callers never send Origin).
            if method in {"POST", "PATCH", "DELETE"}:
                origin = headers.get("origin", "")
                if origin and origin not in {
                    f"http://127.0.0.1:{self.control_port}",
                    f"http://localhost:{self.control_port}",
                }:
                    await self._send_json(writer, {"error": "forbidden"}, status=403)
                    return

            body = b""
            content_length = int(headers.get("content-length", "0") or "0")
            if content_length > _CONTROL_BODY_LIMIT:
                await self._send_json(writer, {"error": "request body too large"}, status=413)
                return
            if content_length > 0:
                body = await reader.readexactly(content_length)

            if method == "GET" and path == "/status":
                await self._send_json(writer, self._status_payload())
            elif method == "POST" and path == "/configure":
                updates = json.loads(body.decode("utf-8") or "{}")
                config = await self.configure(updates)
                await self._send_json(writer, config.to_dict())
            elif method == "POST" and path == "/shutdown":
                await self._send_json(writer, {"ok": True})
                self._shutdown_event.set()
            # ── Rules ─────────────────────────────────────────────────────
            elif method == "GET" and path == "/rules":
                await self._send_json(writer, {"rules": [r.to_dict() for r in self._rules]})
            elif method == "POST" and path == "/rules":
                rule_data = json.loads(body.decode("utf-8") or "{}")
                rule = self.add_rule(rule_data)
                await self._send_json(writer, rule.to_dict(), status=201)
            elif method == "DELETE" and path.startswith("/rules/"):
                rule_id = path[len("/rules/"):]
                if self.remove_rule(rule_id):
                    await self._send_json(writer, {"ok": True})
                else:
                    await self._send_json(writer, {"error": "rule not found"}, status=404)
            elif method == "PATCH" and path.startswith("/rules/"):
                rule_id = path[len("/rules/"):]
                patch = json.loads(body.decode("utf-8") or "{}")
                if "enabled" not in patch:
                    await self._send_json(writer, {"error": "body must contain 'enabled'"}, status=400)
                else:
                    rule = self.toggle_rule(rule_id, bool(patch["enabled"]))
                    if rule is None:
                        await self._send_json(writer, {"error": "rule not found"}, status=404)
                    else:
                        await self._send_json(writer, rule.to_dict())
            # ── Scenarios ──────────────────────────────────────────────────
            elif method == "GET" and path == "/scenarios":
                from .scenario import list_builtin_scenarios, list_user_scenarios
                await self._send_json(writer, {
                    "scenarios": list_builtin_scenarios(),
                    "user_scenarios": list_user_scenarios(),
                })
            elif method == "POST" and path == "/scenarios/save":
                from .scenario import save_user_scenario
                scenario_data = json.loads(body.decode("utf-8") or "{}")
                dest = save_user_scenario(scenario_data)
                logger.info("Scenario saved: %r → %s", scenario_data.get("name"), dest)
                await self._send_json(writer, {"saved": dest.stem, "path": str(dest)})
            elif method == "POST" and path == "/scenario/start":
                data = json.loads(body.decode("utf-8") or "{}")
                if "builtin" in data:
                    # "builtin" resolves built-in first, then falls back to user scenarios.
                    from .scenario import ScenarioError, load_builtin_scenario, load_user_scenario
                    try:
                        scenario = load_builtin_scenario(str(data["builtin"]))
                    except ScenarioError:
                        scenario = load_user_scenario(str(data["builtin"]))
                    data = scenario.to_dict()
                state = await self.start_scenario(data)
                await self._send_json(writer, state.to_dict())
            elif method == "POST" and path == "/scenario/stop":
                state = await self.stop_scenario()
                await self._send_json(writer, state.to_dict())
            elif method == "GET" and path == "/scenario/status":
                await self._send_json(writer, self._scenario_state.to_dict())
            # ── Profiles ───────────────────────────────────────────────────
            elif method == "GET" and path == "/profiles":
                profiles = load_builtin_profiles()
                await self._send_json(writer, {
                    name: {
                        "bandwidth_bps": p.bandwidth_bps,
                        "latency_ms": p.latency_ms,
                        "loss_pct": p.loss_pct,
                        "jitter_ms": p.jitter_ms,
                        "description": p.description,
                    }
                    for name, p in profiles.items()
                })
            # ── Metrics ────────────────────────────────────────────────────
            elif method == "GET" and path.startswith("/metrics"):
                await self._serve_metrics(writer, path)
            # ── Dashboard / SSE / Logs ─────────────────────────────────────
            elif method == "GET" and path == "/events":
                await self._serve_sse(writer)
                return
            elif method == "GET" and path == "/logs":
                await self._serve_logs(writer)
            elif method == "GET" and path.startswith("/dashboard"):
                await self._serve_dashboard(writer, path)
            elif method == "GET" and path == "/":
                await self._serve_dashboard(writer, "/")
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
                "rules_count": len(self._rules),
                "connections_active": self._connections_active,
                "scenario_running": self._scenario_state.running,
            }
        )
        return payload

    # ── Metrics ───────────────────────────────────────────────────────────────

    def _metrics_dict(self) -> dict[str, Any]:
        return {
            "netshape_bytes_sent_total": self.config.bytes_sent,
            "netshape_bytes_received_total": self.config.bytes_received,
            "netshape_connections_total": self.config.connections_total,
            "netshape_connections_active": self._connections_active,
            "netshape_requests_handled_total": self.config.requests_handled,
            "netshape_throttle_sleep_seconds_total": round(self._throttle_sleep_seconds, 6),
            "netshape_drops_total": self.config.drops_total,
            "netshape_latency_added_seconds_total": round(self._latency_added_seconds, 6),
            "netshape_config_bandwidth_bps": self.config.bandwidth_bps,
            "netshape_config_latency_ms": self.config.latency_ms,
            "netshape_config_loss_pct": self.config.loss_pct,
            "netshape_rules_count": len(self._rules),
        }

    def _build_prometheus_text(self) -> str:
        metrics = self._metrics_dict()
        lines: list[str] = []
        for name, kind, help_text in _PROMETHEUS_METRICS:
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {kind}")
            lines.append(f"{name} {metrics[name]}")
        return "\n".join(lines) + "\n"

    async def _serve_metrics(self, writer: asyncio.StreamWriter, path: str) -> None:
        if "format=json" in path:
            await self._send_json(writer, self._metrics_dict())
            return
        body = self._build_prometheus_text().encode("utf-8")
        writer.write(
            (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: text/plain; version=0.0.4; charset=utf-8\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n"
                "\r\n"
            ).encode("ascii")
            + body
        )
        await writer.drain()

    # ── Dashboard / SSE / Logs ────────────────────────────────────────────────

    async def _serve_sse(self, writer: asyncio.StreamWriter) -> None:
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
        await self._send_json(writer, {"lines": list(self._log_buffer)})

    def _build_sse_data(self) -> dict[str, Any]:
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
            # "connected" is intentionally absent: receiving this SSE frame already
            # proves the stream is open; the JS client tracks connection state itself.
            "traffic_port": self.traffic_port,
            "requests_handled": self.config.requests_handled,
            "rules_count": len(self._rules),
            "scenario": self._scenario_state.to_dict(),
            # Live config snapshot — lets the dashboard keep sliders/Current Config
            # panel in sync when a scenario or external adjust changes the settings.
            "config": {
                "bandwidth_bps": self.config.bandwidth_bps,
                "latency_ms": self.config.latency_ms,
                "loss_pct": self.config.loss_pct,
                "jitter_ms": self.config.jitter_ms,
                "profile": self.config.profile,
            },
        }

    async def _serve_dashboard(self, writer: asyncio.StreamWriter, path: str) -> None:
        logger.debug("Dashboard request: %s", path)

        # Check user preference — if dashboard was opted out during setup, return a
        # helpful plain-text page rather than a 404.
        from .core import is_dashboard_enabled
        if not is_dashboard_enabled():
            body = (
                b"<!DOCTYPE html><html><head><title>NetShape</title></head><body>"
                b"<h2>Web Dashboard is disabled</h2>"
                b"<p>You opted out of the web dashboard during setup.</p>"
                b"<p>To enable it, run: <code>netshape setup</code></p>"
                b"</body></html>"
            )
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                + body
            )
            await writer.drain()
            return

        file_map = {
            "/": "index.html",
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

    # ── HTTP parsing helpers ──────────────────────────────────────────────────

    async def _read_headers(
        self,
        reader: asyncio.StreamReader,
        *,
        prefix: bytes = b"",
    ) -> bytes:
        """Read HTTP headers up to and including the blank line.

        Returns the header block *without* the trailing CRLF CRLF.
        ``readuntil`` reads exactly until the delimiter so there are never
        any stray body bytes to return — the old ``(headers, body_prefix)``
        tuple was misleading because body_prefix was always empty.
        """
        data = prefix + await reader.readuntil(b"\r\n\r\n")
        if len(data) > HEADER_LIMIT:
            raise ValueError("HTTP headers too large")
        return data[:-4]

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

    # ── SOCKS5 helpers ────────────────────────────────────────────────────────

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

    # ── HTTP response helpers ─────────────────────────────────────────────────

    def _build_request(
        self,
        method: str,
        path: str,
        version: str,
        headers: dict[str, str],
    ) -> bytes:
        lines = [f"{method} {path} {version}"]
        for key, value in headers.items():
            # Strip CR/LF from header values to prevent header injection.
            safe_value = value.replace("\r", "").replace("\n", "")
            lines.append(f"{key}: {safe_value}")
        return ("\r\n".join(lines) + "\r\n\r\n").encode("iso-8859-1")

    async def _send_json(
        self,
        writer: asyncio.StreamWriter,
        payload: dict[str, Any],
        *,
        status: int = 200,
    ) -> None:
        body = json.dumps(payload).encode("utf-8")
        reason = {200: "OK", 201: "Created", 400: "Bad Request", 404: "Not Found"}.get(status, "Error")
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

    async def _forward_chunked_body(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        rules: _ConnRules | None = None,
    ) -> None:
        while True:
            line = await reader.readuntil(b"\r\n")
            await self._write_throttled(writer, line, direction="sent", rules=rules)
            chunk_size_hex = line.decode("ascii").split(";", 1)[0].strip()
            chunk_size = int(chunk_size_hex, 16)
            if chunk_size == 0:
                await self._write_throttled(writer, b"\r\n", direction="sent", rules=rules)
                break
            chunk = await reader.readexactly(chunk_size)
            await self._write_throttled(writer, chunk, direction="sent", rules=rules)
            crlf = await reader.readexactly(2)
            await self._write_throttled(writer, crlf, direction="sent", rules=rules)

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

    # ── Misc helpers ─────────────────────────────────────────────────────────

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


# ── Module-level helpers ──────────────────────────────────────────────────────

def _opt_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _opt_float(value: Any) -> float | None:
    return None if value is None else float(value)
