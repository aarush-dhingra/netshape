# NetShape Development Roadmap

## Overview

This document tracks all planned features for NetShape, their technical requirements, proposed solutions, and concrete task breakdowns. Features are ordered by priority and grouped by phase.

## Protocol Support Reference

| Protocol | Status | Notes |
|---|---|---|
| HTTP/1.x (plain) | ✅ Supported | Full request/response proxying |
| HTTPS via CONNECT tunnel | ✅ Supported | Transparent TCP tunnel; TLS handled by client/server |
| HTTP/2 over TLS (h2) | ✅ Works transparently | Negotiated inside CONNECT tunnel via ALPN; proxy never sees HTTP/2 frames |
| HTTP/2 cleartext (h2c) | ❌ Not supported | Proxy parses HTTP/1.1 text headers; h2c binary frames would fail parsing |
| SOCKS5 TCP CONNECT | ✅ Supported | Full hostname and IPv4/IPv6 support |
| HTTP/3 / QUIC (UDP) | ❌ Not supported | Clients auto-downgrade to TCP; see Phase 6 |
| WebSocket (via CONNECT) | ✅ Works transparently | Upgrade happens inside the TCP tunnel |

---

## Phases

### Phase 1: Core Stability

#### 1.1 TokenBucket Burst Capacity Fix

**Status:** ✅ Complete

**Problem:** The original TokenBucket defaulted `capacity_bits = rate_bps` (1 second of burst). For a 6 Mbps limit, that's 6 Mbit = 750 KB of burst capacity. Since the test app downloads/uploads 500 KB per cycle, the entire payload fits inside the burst window and no throttling sleep is ever triggered.

**Implemented Solution:**

Replaced the fixed burst constant with a **proportional formula**:

```
capacity = clamp(rate_bps × 0.1, _MIN_BURST_BITS, _MAX_BURST_BITS)
```

- `_BURST_RATIO = 0.1` — burst window is always ~100 ms of data at any rate
- `_MIN_BURST_BITS = 65,536` (one 8 KB read-chunk) — **mandatory floor**: capacity must be ≥ one chunk or `_refill()` can never accumulate enough tokens to serve a full read, causing effective throughput to diverge from the configured rate
- `_MAX_BURST_BITS = 131,072` (two 8 KB read-chunks) — cap prevents high-speed connections from getting a free pass

| Rate | Capacity | Burst window |
|---|---|---|
| 100 Kbps | 8 KB (floor) | 655 ms |
| 250 Kbps (edge) | 8 KB (floor) | 262 ms |
| 6 Mbps | 16 KB (cap) | **21.8 ms** |
| 100 Mbps | 16 KB (cap) | 1.3 ms |

**Technical Requirements:**
- No breaking changes for existing code paths or tests
- Must still support explicit `capacity_bits` override
- Must handle `rate_bps == 0` (unlimited) correctly via `float("inf")`

**Tasks:**
- [x] Add `_BURST_RATIO`, `_MIN_BURST_BITS`, `_MAX_BURST_BITS` constants in `throttle.py`
- [x] Add `_default_capacity(rate_bps)` helper function
- [x] Update `TokenBucket.__init__` to use `_default_capacity`
- [x] Update `TokenBucket.reset_rate` to clamp tokens to new capacity without full reset
- [x] Add `logger.info` on bucket init (rate, capacity, initial tokens)
- [x] Add `logger.info` in `consume()` each time throttling sleep is triggered
- [x] Update unit tests: decouple mechanics tests via explicit `capacity_bits`, add `test_default_capacity_proportional`
- [x] Run full test suite — 110 passed, 0 failed

---

### Phase 2: Embedded Web Dashboard

#### 2.1 Dashboard Infrastructure

**Status:** ✅ Complete

**Problem:** The Electron test-app is heavy and requires Node.js. Users need a lightweight, always-available GUI.

**Proposed Solution:** Embed a web dashboard served directly from the proxy's control port (`127.0.0.1:{control_port}/dashboard`). Built with pure HTML/CSS/JS + Chart.js via CDN (no framework, no npm, no build step). Served by extending `_handle_control` in `proxy_server.py` with static file responses — **no FastAPI or uvicorn required**. Dashboard HTML/CSS/JS files live in `netshape/dashboard/` and are included in the pip package via `package_data` in `pyproject.toml`.

**Technical Requirements:**
- Dashboard must not depend on npm/node — pure browser assets
- Must work alongside existing control API on the same port, no port conflicts
- Real-time updates via **Server-Sent Events** (SSE) on `GET /events` — preferred over polling for low overhead
- Dashboard files must be included in `package_data` so `pip install netshape` bundles them

**Tasks:**
- [x] Add `/dashboard`, `/events`, and `/logs` routes to `ThrottledProxy._handle_control`
- [x] Create `netshape/dashboard/` with `index.html`, `style.css`, `app.js`, `__init__.py`
- [x] Add `package_data = {"netshape": ["dashboard/*"]}` to `pyproject.toml`
- [x] Implement SSE endpoint that streams instantaneous metrics every second
- [x] Add profile/controls section with sliders for bandwidth, latency, loss, jitter
- [x] Profile dropdown (none, 3g, edge, satellite, dsl) that calls `POST /configure`
- [x] Apply button

**Logging Verification:**
- [x] Log each SSE connection/disconnection (`logger.info` in `_serve_sse`)
- [x] Log dashboard requests (`logger.debug` in `_serve_dashboard`)
- [x] Log when controls are applied (existing `configure()` path)

---

#### 2.2 Dashboard Features

**Tasks:**
- [x] Real-time throughput chart (download + upload, 60 data points) — instantaneous delta per tick
- [x] Real-time latency chart (RTT, 60 data points)
- [x] Status banner with network classification (NORMAL / SLOW / POOR / SEVERE / OFFLINE)
- [x] Metric cards: download Mbps, upload Mbps, latency ms, loss %
- [x] Live log tail viewer — `GET /logs` JSON endpoint + `_ProxyLogHandler` capturing all `netshape.*` log records; dashboard polls every 3 s and auto-scrolls
- [x] Connection status indicator (green/red dot) with live traffic port shown
- [x] Responsive layout — media queries at 900 px and 480 px breakpoints (stacked columns, 2-col metric grid)

**Logging Verification:**
- [x] Log chart data point counts and update intervals (visible in log viewer)
- [x] Classification thresholds: NORMAL / SLOW (<2 Mbps or >50 ms latency) / POOR (<300 Kbps or >200 ms or >1% loss) / SEVERE (>5% loss or >500 ms latency)

---

### Phase 3: Per-Endpoint Throttling Rules

#### 3.1 Rule Engine

**Status:** Planned

**Problem:** Currently all traffic goes through a single global `TokenBucket`. Users cannot apply different conditions to different endpoints (e.g. throttle Stripe but not GitHub).

**Proposed Solution:**

Add a `ThrottleRule` dataclass and `rules` list to `ThrottledProxy`. Each rule has a pattern and a throttle config subset. When handling a connection, match the target against all rules and use the first matching rule's bucket instead of the global default.

**Important constraint — HTTPS hostname-only matching:**
For HTTP (plain) traffic the full URL path is visible and can be matched. For HTTPS (CONNECT tunnel) and SOCKS5, the proxy only sees the **hostname and port** — not the URL path or query string, because the request is encrypted. Rules for HTTPS targets can only match on hostname/domain (e.g. `stripe\.com`), not on path.

**Technical Requirements:**
- Rules must be configurable via control API and CLI
- Default global bucket must still exist for non-matching traffic
- Compiled regex patterns must be cached for performance
- Rules must be persisted in `state.json`
- HTTP rules can match full URL; HTTPS/SOCKS5 rules can only match hostname

**Tasks:**
- [ ] Add `ThrottleRule` dataclass to `proxy_server.py`
- [ ] Add `rules: list[ThrottleRule]` to `ThrottleConfig`
- [ ] In `_handle_http`, extract full URL and resolve matching rule
- [ ] In `_handle_connect` and `_handle_socks5`, extract hostname and match rules
- [ ] Add per-rule `TokenBucket` creation and caching
- [ ] Modify `_write_throttled` to accept an optional rule override
- [ ] Add control API endpoints: `GET /rules`, `POST /rules`, `DELETE /rules/{id}`
- [ ] Add CLI commands: `netshape rule add / remove / list`
- [ ] Update tests for rule matching and per-rule throttling

**Logging Verification:**
- Log each rule match with target and matched rule id
- Log when a request falls through to the default bucket
- Log per-rule bytes and throttle sleep totals

---

### Phase 4: Scenario Scripting Engine

#### 4.1 YAML Scenario Parser

**Status:** Planned

**Problem:** Users can only apply static profiles. They need to simulate dynamic network conditions over time (e.g. "start on 4G, enter a tunnel, emerge on 2G").

**Proposed Solution:**

A `Scenario` is a YAML file with a list of `phases`. Each phase has a `duration` and a `profile` (built-in name or inline config). A `ScenarioRunner` executes phases sequentially, calling `configure()` at each transition. On Ctrl-C or `POST /scenario/stop`, the scenario is interrupted and the **pre-scenario config is restored** (not just stopped at current state).

YAML format:
```yaml
name: "Subway Commute"
phases:
  - name: "Platform - 4G"
    duration: "30s"
    profile: "4g"
  - name: "Tunnel - no signal"
    duration: "10s"
    profile:
      bandwidth_bps: 0
      latency_ms: 0
      loss_pct: 1.0
  - name: "Underground - 2G"
    duration: "60s"
    profile: "edge"
```

**Technical Requirements:**
- `pyyaml` as an optional dependency (`netshape[scenarios]`)
- Phase transitions via `asyncio.sleep` + `configure()` calls
- Ctrl-C and `POST /scenario/stop` must **restore the pre-scenario config**, not just halt
- Must log each phase transition with name, duration, and applied config

**Tasks:**
- [ ] Add `pyyaml` to optional dependencies in `pyproject.toml`
- [ ] Create `netshape/scenario.py` with `Scenario`, `Phase`, `ScenarioRunner`
- [ ] Snapshot pre-scenario config at start; restore on interrupt or completion
- [ ] Add `netshape run-scenario <file.yaml>` CLI command
- [ ] Add `POST /scenario/start` and `POST /scenario/stop` control endpoints
- [ ] Add dashboard UI for scenario upload / run / stop with phase progress indicator
- [ ] Bundle built-in scenarios in `netshape/data/scenarios/`: `subway.yaml`, `flight-mode.yaml`, `coffee-shop-wifi.yaml`, `satellite.yaml`
- [ ] Test with a 3-phase scenario and verify config is restored after interrupt

**Logging Verification:**
- Log each phase transition: name, duration, applied config
- Log scenario completion or interrupt with total elapsed time
- Log pre-scenario config snapshot and final restored state

---

### Phase 5: Metrics & Observability

#### 5.1 Real-Time Metrics Export

**Status:** Planned

**Problem:** Metrics are only visible in the dashboard. Users need to export them for CI pipelines, load test tooling, and post-run analysis.

**Proposed Solution:**

1. **Structured events** — JSON lines written to a rotating file when `--log-file` is passed
2. **Prometheus text format** — `GET /metrics` (standard exposition format, compatible with `promtool` and Grafana)
3. **JSON metrics** — `GET /metrics?format=json`

**Tracked metrics (canonical names):**

| Metric | Type | Description |
|---|---|---|
| `netshape_bytes_sent_total` | Counter | Total bytes forwarded to upstream |
| `netshape_bytes_received_total` | Counter | Total bytes forwarded to client |
| `netshape_connections_total` | Counter | Total connections accepted |
| `netshape_connections_active` | Gauge | Currently open connections |
| `netshape_throttle_sleep_seconds_total` | Counter | Total wall-clock time spent in throttle sleeps |
| `netshape_drops_total` | Counter | Total connections dropped due to loss_pct |
| `netshape_latency_added_seconds_total` | Counter | Total artificial latency injected |
| `netshape_config_bandwidth_bps` | Gauge | Current configured bandwidth limit |
| `netshape_config_latency_ms` | Gauge | Current configured latency |
| `netshape_config_loss_pct` | Gauge | Current configured loss rate |

**Technical Requirements:**
- Metrics counters must be lock-free where possible (asyncio single-threaded)
- Must not block the event loop
- File rotation handled by Python's `RotatingFileHandler`

**Tasks:**
- [ ] Add `--log-file` and `--log-format` CLI flags
- [ ] Add rotating JSON log handler in `core.py`
- [ ] Add metrics counters to `ThrottleConfig` or a separate `MetricsStore`
- [ ] Implement `GET /metrics` (Prometheus text format)
- [ ] Implement `GET /metrics?format=json`
- [ ] Add `rich.Live` table for terminal real-time metrics (`netshape status --watch`)
- [ ] Add `--metrics-port` flag to expose metrics on a dedicated port

**Logging Verification:**
- Verify Prometheus output parses with `promtool check metrics`
- Verify JSON log file contains all event types after a complete session

---

#### 5.2 Request-Level Logs

**Tasks:**
- [ ] Log each proxy request: method, target, bytes sent/received, elapsed ms
- [ ] Log throttling decisions: sleep duration, connection dropped
- [ ] Log connection events: connect, disconnect, upstream error
- [ ] Add configurable log levels (DEBUG, INFO, WARN, ERROR) via `--log-level`
- [ ] Support `--log-format text|json`

---

### Phase 6: Future / Low-Priority

#### 6.1 UDP Proxy Support (SOCKS5 UDP ASSOCIATE)

**Status:** Deferred — low priority for developer testing use case

**Rationale:** The primary audience is web developers testing HTTP/REST/gRPC APIs. UDP is only relevant for games, VoIP, and QUIC. Browsers automatically downgrade from HTTP/3 (QUIC/UDP) to HTTP/2 or HTTP/1.1 over TCP when UDP is unavailable, so developer testing is unaffected. Implementation is also significantly more complex (NAT-like datagram mapping, fragmentation handling).

**Tasks (when prioritised):**
- [ ] Add `SOCKS5Command.UDP_ASSOCIATE` handler
- [ ] Implement UDP relay with `asyncio.DatagramProtocol`
- [ ] Add `--protocols http,socks5,udp` CLI flag
- [ ] Add dashboard toggle

---

#### 6.2 WebSocket Throttling at Frame Level

**Status:** Deferred

**Current behaviour:** WebSocket connections are established via an HTTP `Upgrade` request, which becomes a plain TCP tunnel after the handshake. The proxy currently throttles WebSocket traffic at the **byte stream level** (same as any CONNECT tunnel), which is accurate for overall bandwidth but does not model individual WebSocket frame timing.

**Future work:** Inspect WS frames and simulate per-message latency/loss to better model realtime app degradation (chat, live collaboration, multiplayer games).

---

### Phase 7: Packaging & Distribution

#### 7.1 pip Package Structure

**Status:** In Progress

**Proposed extras:**

```
pip install netshape              # core proxy + CLI, zero extra deps
pip install netshape[scenarios]   # adds pyyaml for YAML scenario files
pip install netshape[all]         # everything
```

**Note:** The web dashboard (Phase 2) requires **no additional pip dependencies** — it is pure HTML/CSS/JS served by the existing asyncio control server. Dashboard files are bundled as `package_data`. There is no `netshape[gui]` extra needed.

**Tasks:**
- [ ] Add optional dep `pyyaml` under `[project.optional-dependencies] scenarios` in `pyproject.toml`
- [ ] Add `[all]` extra that pulls in `scenarios`
- [ ] Add `package_data = {"netshape": ["dashboard/*", "data/scenarios/*.yaml"]}` to `pyproject.toml`
- [ ] Verify `pip install -e .` works for local dev
- [ ] Verify `pip install netshape` from TestPyPI installs cleanly with no missing files
- [ ] Add `pyproject.toml` classifiers (Development Status, Topic, Python version)
- [ ] Write `CONTRIBUTING.md` with dev setup instructions

---

#### 7.2 pytest Plugin (`pytest-netshape`)

**Status:** Planned — high-value differentiator

**Problem:** Developers want to write automated tests that assert their app behaves correctly under degraded network conditions. No existing tool integrates this into pytest natively.

**Proposed Solution:**

A `pytest-netshape` plugin (separate pip package) that provides a `netshape_session` fixture. Tests can declare network conditions via markers or fixture params, and the proxy is started/stopped automatically per test or per session.

```python
@pytest.fixture
def slow_network(netshape_session):
    netshape_session.configure(bandwidth_bps=500_000, latency_ms=200)
    yield

def test_api_timeout_on_slow_network(slow_network, http_client):
    with pytest.raises(TimeoutError):
        http_client.get("https://api.example.com/data", timeout=0.5)
```

Or via markers:

```python
@pytest.mark.netshape(profile="3g")
def test_image_loads_on_3g():
    ...
```

**Tasks:**
- [ ] Create `pytest-netshape/` package alongside `netshape/`
- [ ] Implement `netshape_session` and `netshape_proxy` fixtures
- [ ] Implement `@pytest.mark.netshape(profile=..., bandwidth_bps=..., latency_ms=...)` marker
- [ ] Auto-configure `HTTP_PROXY` / `HTTPS_PROXY` env vars within fixture scope
- [ ] Restore previous proxy state after each test
- [ ] Publish as separate `pytest-netshape` package on PyPI

---

#### 7.3 GitHub Actions Integration

**Status:** Planned — distribution/adoption driver

**Proposed Solution:** A ready-made GitHub Actions step that wraps a test job in a network condition:

```yaml
- uses: netshape/action@v1
  with:
    profile: 3g        # or: bandwidth_bps: 500000, latency_ms: 200
    scope: job         # start before job, stop after
```

**Tasks:**
- [ ] Create `netshape/action` GitHub repo with `action.yml`
- [ ] Action installs `netshape` via pip, runs proxy in background, sets proxy env vars
- [ ] Supports `profile`, `bandwidth_bps`, `latency_ms`, `loss_pct` inputs
- [ ] Publishes usage docs and example workflows

---

## Summary

| Phase | Feature | Priority | Effort | Status |
|-------|---------|----------|--------|--------|
| 1 | TokenBucket burst fix | **Critical** | Small | ✅ Done |
| 2 | Web dashboard | **High** | Medium | ✅ Done |
| 3 | Per-endpoint rules | **High** | Medium | Planned |
| 4 | Scenario scripting | **High** | Medium | Planned |
| 5 | Metrics export | **Medium** | Small | Planned |
| 6 | UDP / advanced protocols | Low | Large | Deferred |
| 7 | Packaging + pytest plugin + GH Actions | **High** | Medium | In Progress |

## Next Steps

1. Start web dashboard — Phase 2 (embedded, zero extra deps)
2. Per-endpoint throttling rules — Phase 3
3. Scenario scripting engine — Phase 4 (biggest differentiator)
4. Metrics export — Phase 5 (enables CI integration)
5. Finalize packaging + pytest plugin — Phase 7
