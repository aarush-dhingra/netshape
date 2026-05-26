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

**Status:** ✅ Complete

**Problem:** Previously all traffic went through a single global `TokenBucket`. Users could not apply different conditions to different endpoints (e.g. throttle Stripe but not GitHub).

**Implemented Solution:**

Added a `ThrottleRule` dataclass with `id`, `pattern`, `bandwidth_bps`, `latency_ms`, `jitter_ms`, `loss_pct`, and `comment` fields. Rules are stored on `ThrottledProxy` (session-scoped) and matched on every connection. Each matching rule gets its own `TokenBucket` created lazily on first match; compiled regex patterns are cached. Any field left as `None` inherits the global proxy setting.

**Important constraint — HTTPS hostname-only matching:**
For HTTP (plain) traffic the full URL path is visible and can be matched. For HTTPS (CONNECT tunnel) and SOCKS5, the proxy only sees the **hostname and port** — not the URL path or query string, because the request is encrypted. Rules for HTTPS targets can only match on hostname/domain (e.g. `stripe\.com`), not on path.

**Technical Requirements:**
- Rules configurable via control API and CLI ✅
- Default global bucket still used for non-matching traffic ✅
- Compiled regex patterns cached for performance ✅
- HTTP rules match full URL; HTTPS/SOCKS5 rules match hostname ✅
- All per-rule throttle fields (bw, lat, jitter, loss) individually overrideable ✅

**Tasks:**
- [x] Add `ThrottleRule` dataclass and `_ConnRules` internal dataclass to `proxy_server.py`
- [x] Add `_rules`, `_rule_buckets`, `_rule_patterns` to `ThrottledProxy.__init__`
- [x] Add `add_rule`, `remove_rule`, `list_rules`, `_resolve_rules` methods to `ThrottledProxy`
- [x] In `_handle_http`, resolve rules from full target URL and pass `_ConnRules` through the call chain
- [x] In `_handle_connect` and `_handle_socks5`, resolve rules from hostname and pass through tunnel
- [x] Thread `_ConnRules` through `_tunnel_streams → _pipe → _write_throttled` and `_apply_latency`
- [x] Move per-connection loss check into handlers (after hostname is known) so per-rule loss applies correctly
- [x] Add control API endpoints: `GET /rules`, `POST /rules` (201 Created), `DELETE /rules/{id}`
- [x] Add `add_rule`, `remove_rule`, `list_rules` helpers in `core.py`
- [x] Add `netshape rule add / remove / list` CLI sub-commands in `cli.py`
- [x] Add `tests/test_rules.py` with 13 tests covering unit + integration scenarios

**Usage:**
```bash
# Add a rule — throttle all requests to stripe.com at 1 Mbps with 200 ms latency
netshape rule add "stripe\.com" --bandwidth 1mbps --latency 200ms --comment "payment API"

# List active rules
netshape rule list

# Remove a rule by id prefix
netshape rule remove <id>
```

**Logging Verification:**
- [x] Log each rule match at DEBUG level: `Rule match: target=... rule=<id8> (<pattern>)`
- [x] `add_rule` logs at INFO level: id, pattern, bandwidth, latency, loss, comment
- [x] `remove_rule` logs at INFO level: id

---

### Phase 4: Scenario Scripting Engine

#### 4.1 YAML Scenario Parser

**Status:** ✅ Complete

**Problem:** Users can only apply static profiles. They need to simulate dynamic network conditions over time (e.g. "start on 4G, enter a tunnel, emerge on 2G").

**Implemented Solution:**

`netshape/scenario.py` provides `Phase`, `Scenario`, `parse_scenario_dict`, `load_scenario` (from YAML file), `load_builtin_scenario`, and `list_builtin_scenarios`. Scenarios run as an asyncio task inside the proxy server. Pre-scenario config is **always restored** on completion or stop.

YAML format:
```yaml
name: "Subway Commute"
phases:
  - name: "Platform — 4G"
    duration: "30s"
    profile: "4g"          # built-in profile name
  - name: "Tunnel — no signal"
    duration: "20s"
    bandwidth: "0"
    latency: "2000ms"
    loss: "95%"
    jitter: "500ms"
  - name: "Underground — 2G"
    duration: "60s"
    profile: "edge"
```

Phase keys (`bandwidth`, `latency`, `loss`, `jitter`) override the referenced `profile`. A phase with no `profile` uses all keys directly.

**New files:**
- `netshape/scenario.py` — `Phase`, `Scenario`, `parse_scenario_dict`, `load_scenario`, `load_builtin_scenario`, `list_builtin_scenarios`
- `netshape/data/scenarios/subway.yaml` — Urban transit scenario
- `netshape/data/scenarios/flight-mode.yaml` — Airport → in-flight → landing
- `netshape/data/scenarios/coffee-shop-wifi.yaml` — Café congestion degradation
- `netshape/data/scenarios/satellite.yaml` — Geostationary link with rain fade

**Server-side scenario runner (in `proxy_server.py`):**
- `ThrottledProxy.start_scenario(dict)` — starts asyncio task, stops any existing scenario
- `ThrottledProxy.stop_scenario()` — signals task via `asyncio.Event`, waits ≤3s, then cancels
- `ThrottledProxy._run_scenario_task(dict)` — applies phases sequentially; `finally:` always restores pre-scenario config
- `_ScenarioState` dataclass — tracks running, name, current_phase, total_phases, phase_name, phase_elapsed_s, phase_duration_s

**Control API:**
```
GET  /scenarios          → {"scenarios": ["subway", "flight-mode", ...]}
POST /scenario/start     → body: full scenario dict OR {"builtin": "subway"}
POST /scenario/stop      → {}
GET  /scenario/status    → {"running": bool, "name": ..., "current_phase": ...}
```

**CLI:**
```bash
netshape scenario run --builtin subway        # built-in scenario
netshape scenario run ./my-scenario.yaml      # custom YAML
netshape scenario stop                         # stop and restore
netshape scenario status                       # show phase progress
netshape scenario list                         # list built-in scenarios
```

**Dashboard:** Scenario panel in right column with built-in dropdown, phase progress bar, and Stop button. Updated via SSE `scenario` field every second.

**Tasks:**
- [x] Add `pyyaml` to optional dependencies in `pyproject.toml` (`netshape[scenarios]`)
- [x] Create `netshape/scenario.py` with `Scenario`, `Phase`, `parse_scenario_dict`, `load_scenario`
- [x] Snapshot pre-scenario config at start; restore on interrupt or completion
- [x] Add `netshape scenario run/stop/status/list` CLI subcommand group
- [x] Add `POST /scenario/start`, `POST /scenario/stop`, `GET /scenario/status` control endpoints
- [x] Add `GET /scenarios` endpoint for dashboard dropdown population
- [x] Add dashboard scenario panel with built-in dropdown, progress bar, and Stop button
- [x] Bundle built-in scenarios: `subway.yaml`, `flight-mode.yaml`, `coffee-shop-wifi.yaml`, `satellite.yaml`
- [x] Tests: `tests/test_scenarios.py` — unit tests for parsing + integration tests for start/stop/restore

**Logging Verification:**
- [x] Log each phase transition: `Phase %d/%d: %r — bw=%d lat=%d loss=%.3f jitter=%d (%.1fs)`
- [x] Log scenario completion: `Scenario completed: %r`
- [x] Log scenario interrupt: `Scenario stopped at phase %d/%d`
- [x] Log pre-scenario config snapshot and final restored state

---

### Phase 5: Metrics & Observability

#### 5.1 Real-Time Metrics Export

**Status:** ✅ Complete

**Problem:** Metrics are only visible in the dashboard. Users need to export them for CI pipelines, load test tooling, and post-run analysis.

**Implemented Solution:**

1. **Structured JSON log file** — `--log-file <path>` on `netshape run` writes rotating JSON log lines (10 MB, 3 backups) via `logging.handlers.RotatingFileHandler` with a custom `_JsonLogFormatter`.
2. **Prometheus text format** — `GET /metrics` returns standard text/plain exposition format (compatible with Prometheus, `promtool`, and Grafana).
3. **JSON metrics** — `GET /metrics?format=json` returns a JSON object with all metric names as keys.
4. **Live terminal table** — `netshape status --watch` uses `rich.Live` + `rich.Table` to refresh status every second.
5. **`netshape metrics`** CLI command — prints all metrics to stdout (JSON or `--prometheus` for Prometheus text).

**Tracked metrics (canonical names):**

| Metric | Type | Description |
|---|---|---|
| `netshape_bytes_sent_total` | Counter | Total bytes forwarded to upstream |
| `netshape_bytes_received_total` | Counter | Total bytes forwarded to clients |
| `netshape_connections_total` | Counter | Total client connections accepted |
| `netshape_connections_active` | Gauge | Currently active client connections |
| `netshape_requests_handled_total` | Counter | Total requests handled (HTTP + SOCKS5) |
| `netshape_throttle_sleep_seconds_total` | Counter | Total wall-clock time spent in throttle sleeps |
| `netshape_drops_total` | Counter | Total connections dropped due to `loss_pct` |
| `netshape_latency_added_seconds_total` | Counter | Total artificial latency injected |
| `netshape_config_bandwidth_bps` | Gauge | Current configured bandwidth limit (0=unlimited) |
| `netshape_config_latency_ms` | Gauge | Current configured latency |
| `netshape_config_loss_pct` | Gauge | Current configured loss fraction (0.0–1.0) |
| `netshape_rules_count` | Gauge | Number of active per-endpoint throttle rules |

**Technical implementation:**
- All counters are plain Python `int`/`float` fields updated directly in the asyncio event loop — no locking needed.
- `connections_total` and `drops_total` added to `ThrottleConfig` (persists across `configure()` calls).
- `_connections_active`, `_throttle_sleep_seconds`, `_latency_added_seconds` on `ThrottledProxy` (ephemeral per-run counters).
- `_write_throttled` accumulates `_throttle_sleep_seconds` before each `asyncio.sleep`.
- `_apply_latency` accumulates `_latency_added_seconds` tracking actual sleep duration.
- `_handle_client` increments `connections_total` and tracks `_connections_active` with a try/finally decrement.
- Handlers increment `drops_total` whenever `should_drop_chunk` returns `True`.
- `_PROMETHEUS_METRICS` is a module-level list of `(name, type, help)` tuples — new metrics are added in one place.

**Control API:**
```
GET /metrics              → Prometheus text format (text/plain; version=0.0.4)
GET /metrics?format=json  → JSON object with all metric keys
```

**CLI:**
```bash
netshape run --log-file proxy.jsonl -- curl https://example.com
netshape status --watch          # live rich.Live table (Ctrl-C to stop)
netshape metrics                 # print all metrics as key: value
netshape metrics --prometheus    # print Prometheus text format
```

**Tasks:**
- [x] Add `--log-file` CLI flag on `netshape run` with rotating JSON handler
- [x] Add metrics counters to `ThrottleConfig` (`connections_total`, `drops_total`) and `ThrottledProxy` (`_connections_active`, `_throttle_sleep_seconds`, `_latency_added_seconds`)
- [x] Implement `GET /metrics` (Prometheus text/plain; version=0.0.4)
- [x] Implement `GET /metrics?format=json`
- [x] Add `rich.Live` table for `netshape status --watch`
- [x] Add `netshape metrics` CLI command (JSON and `--prometheus` flags)
- [x] Tests: `tests/test_metrics.py` — Prometheus format, JSON format, counter increments

**Logging Verification:**
- [x] Prometheus output contains `# HELP` and `# TYPE` for every metric
- [x] JSON metrics object contains all 12 canonical metric names
- [x] `connections_total` increments on every new client connection
- [x] `drops_total` increments when `loss_pct=1.0`

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
| 3 | Per-endpoint rules | **High** | Medium | ✅ Done |
| 4 | Scenario scripting | **High** | Medium | ✅ Done |
| 5 | Metrics export | **Medium** | Small | ✅ Done |
| 6 | UDP / advanced protocols | Low | Large | Deferred |
| 7 | Packaging + pytest plugin + GH Actions | **High** | Medium | In Progress |

## Next Steps

1. Finalize packaging + pytest plugin — Phase 7 (adoption driver)
2. UDP / WebSocket frame-level throttling — Phase 6 (low priority, deferred)
