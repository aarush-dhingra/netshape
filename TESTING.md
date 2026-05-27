# NetShape — Testing Guide

Step-by-step instructions for verifying every feature of the project,
followed by a complete command reference.

---

## Prerequisites

```bash
# Clone (if not already done)
git clone <repo-url>
cd netshape

# Install in editable mode with dev + scenario extras
pip install -e ".[dev,scenarios]"

# Confirm installation
netshape --version
```

> **Windows note:** All commands below work in PowerShell or Command Prompt.
> Replace `curl` with `Invoke-WebRequest` if curl is not available, or install
> it via `winget install curl.curl`.

---

## 1. Automated Test Suite

Run the full pytest suite. All 154 tests should pass in under 30 seconds.

```bash
python -m pytest tests/ -q
```

Run a specific test file:

```bash
python -m pytest tests/test_core.py -q
python -m pytest tests/test_proxy_server.py -q
python -m pytest tests/test_throttle.py -q
python -m pytest tests/test_units.py -q
python -m pytest tests/test_rules.py -q
python -m pytest tests/test_scenarios.py -q
python -m pytest tests/test_metrics.py -q
```

Run a single test by name:

```bash
python -m pytest tests/test_scenarios.py::test_scenario_restores_config_on_stop -v
```

Run with verbose output and show first failure immediately:

```bash
python -m pytest tests/ -v -x
```

---

## 2. CLI Smoke Tests

Quick end-to-end sanity checks that require no external network.

```bash
# Version banner
netshape --version

# List all built-in profiles
netshape profiles

# Run a trivial command through the proxy (no throttling)
netshape run -- python -c "print('proxy ok')"

# Run with 3G throttling; command should complete slowly
netshape run --profile 3g -- python -c "print('3g ok')"

# Built-in speed test (starts proxy, downloads payload, reports timing)
netshape test --profile 3g --bytes 8192
```

---

## 3. Core Proxy — Basic Session Lifecycle

Open **two terminal windows** and keep both visible throughout this section.

### Terminal 1 — start a session

```bash
netshape run --profile 3g -- python -c "
import urllib.request, time
time.sleep(60)
"
```

### Terminal 2 — inspect and adjust

```bash
# Check that the session is active
netshape status

# Check as JSON
netshape status --json

# Live-refresh table (Ctrl-C to stop)
netshape status --watch

# Change bandwidth while session runs
netshape adjust --bandwidth 500kbps

# Change multiple settings at once
netshape adjust --latency 800ms --loss 5%

# Switch to a named profile
netshape adjust --profile satellite

# Verify the change took effect
netshape status

# Stop the session
netshape stop
```

---

## 4. Manual HTTP Proxy Smoke Test

Tests that real HTTP traffic is throttled correctly.

```bash
# Start the proxy with 1 Mbps bandwidth and 200 ms latency
netshape run --bandwidth 1mbps --latency 200ms -- python -c "
import time; time.sleep(120)
"
```

In another terminal:

```bash
# Time a download through the proxy (should be noticeably slow)
curl --proxy http://127.0.0.1:8090 -o /dev/null -s -w "%{time_total}s\n" http://example.com

# Compare with a direct request (much faster)
curl -o /dev/null -s -w "%{time_total}s\n" http://example.com
```

---

## 5. Web Dashboard

The dashboard is served by the proxy's control port.

```bash
# Start a session
netshape run --profile 3g -- python -c "import time; time.sleep(300)"
```

Open your browser and navigate to:

```
http://127.0.0.1:8091/
```

**What to verify:**

| Element | Expected behaviour |
|---|---|
| Status banner | Shows `SLOW` or `POOR` in yellow/orange |
| Download / Upload gauges | Update every second (live throughput) |
| Throughput chart | Lines animate in real time as traffic flows |
| Latency chart | Reflects configured latency |
| Bandwidth slider | Drag and click **Apply Changes** → banner updates instantly |
| Profile dropdown | Select `satellite` → sliders jump to satellite values |
| Log panel | Proxy log lines stream in every 3 seconds |
| Scenario panel | Shows built-in dropdown (requires pyyaml) |

While the dashboard is open, adjust settings in another terminal:

```bash
netshape adjust --bandwidth 10mbps --latency 10ms
```

The status banner should switch to `NORMAL` within one second.

---

## 6. Control API (direct HTTP)

Verify the control API endpoints directly with curl or Invoke-WebRequest.

```bash
# Start a session first
netshape run -- python -c "import time; time.sleep(120)"
```

```bash
# Status
curl -s http://127.0.0.1:8091/status | python -m json.tool

# Configure
curl -s -X POST http://127.0.0.1:8091/configure \
  -H "Content-Type: application/json" \
  -d '{"bandwidth_bps": 2000000, "latency_ms": 100}' | python -m json.tool

# Metrics — JSON
curl -s "http://127.0.0.1:8091/metrics?format=json" | python -m json.tool

# Metrics — Prometheus text
curl -s http://127.0.0.1:8091/metrics

# Scenario status
curl -s http://127.0.0.1:8091/scenario/status | python -m json.tool

# Built-in scenario list
curl -s http://127.0.0.1:8091/scenarios | python -m json.tool
```

---

## 7. Per-Endpoint Throttle Rules

Rules let you apply different throttle settings to different hosts/URLs.

```bash
# Start an unthrottled session
netshape run -- python -c "import time; time.sleep(300)"
```

```bash
# Add a rule: throttle example.com to 100 kbps with 500 ms latency
netshape rule add "example\.com" --bandwidth 100kbps --latency 500ms --comment "slow example"

# List active rules
netshape rule list

# List as JSON
netshape rule list --json

# Test that the rule applies (should be slow)
curl --proxy http://127.0.0.1:8090 -o /dev/null -s -w "%{time_total}s\n" http://example.com

# Test that other hosts are unaffected (should be fast)
curl --proxy http://127.0.0.1:8090 -o /dev/null -s -w "%{time_total}s\n" http://httpbin.org/get

# Remove the rule (use the 8-char prefix shown by `rule list`)
netshape rule remove <rule-id-prefix>

# Verify it's gone
netshape rule list
```

### Rule matching details

- **HTTPS CONNECT / SOCKS5**: pattern matched against **hostname only** (`example.com`)
- **Plain HTTP**: pattern matched against **full URL** (`http://example.com/path`)
- First matching rule wins; unmatched traffic uses global settings
- Pattern is a case-insensitive Python regex

---

## 8. Scenario Scripting Engine

Scenarios apply a sequence of network conditions over time.

### 8a. List and run a built-in scenario

```bash
# Start an unthrottled session in Terminal 1
netshape run -- python -c "import time; time.sleep(600)"

# In Terminal 2 — list available built-in scenarios
netshape scenario list

# Run the subway scenario (watch bandwidth/latency change every phase)
netshape scenario run --builtin subway
```

You will see output like:

```
Scenario started.
Press Ctrl-C to stop early.

  Phase 1/5  'Platform — 4G'  0s / 30s (0%)
  Phase 1/5  'Platform — 4G'  5s / 30s (17%)
  ...
  Phase 2/5  'Entering Tunnel'  2s / 8s (25%)
```

Meanwhile, the web dashboard (port 8091) will show bandwidth and latency
changing in real time as each phase applies.

Press **Ctrl-C** to stop early — the pre-scenario config is restored automatically.

### 8b. Verify config restore on completion

```bash
# Set a known config
netshape adjust --bandwidth 10mbps --latency 10ms

# Run a very short scenario
netshape scenario run --builtin coffee-shop-wifi --no-wait
sleep 120   # wait for it to complete

# Config should be back to 10 Mbps / 10 ms
netshape status
```

### 8c. Run a custom YAML scenario

Create a file `my-scenario.yaml`:

```yaml
name: "Quick Test"
description: "Two-phase test scenario."
phases:
  - name: "Fast"
    duration: "10s"
    bandwidth: "50mbps"
    latency: "5ms"

  - name: "Throttled"
    duration: "15s"
    bandwidth: "500kbps"
    latency: "300ms"
    loss: "2%"
```

```bash
netshape scenario run my-scenario.yaml
```

### 8d. Stop a scenario from a second terminal

```bash
# Terminal 1 — start scenario with --no-wait
netshape scenario run --builtin satellite --no-wait

# Terminal 2 — check status and stop
netshape scenario status
netshape scenario stop
netshape scenario status   # should show "No scenario running"
```

---

## 9. Metrics & Observability

### 9a. CLI metrics

```bash
# Start a session and generate some traffic
netshape run --bandwidth 2mbps --latency 50ms -- python -c "import time; time.sleep(120)"

# Generate traffic (another terminal)
curl --proxy http://127.0.0.1:8090 -o /dev/null http://example.com

# Print all metrics as key:value
netshape metrics

# Print in Prometheus text format
netshape metrics --prometheus
```

**Expected metrics output includes:**

```
netshape_bytes_sent_total: <nonzero>
netshape_connections_total: <nonzero>
netshape_config_bandwidth_bps: 2000000
netshape_config_latency_ms: 50
```

### 9b. Live status watch

```bash
netshape status --watch
```

This opens a `rich` table that refreshes every second. Make adjustments
in another terminal and watch the values update live.

### 9c. JSON log file

```bash
netshape run --log-file proxy.jsonl --profile 3g -- python -c "import time; time.sleep(30)"
```

Open `proxy.jsonl` — each line is a JSON object:

```json
{"ts": "2026-05-27T12:00:00", "level": "INFO", "name": "netshape.proxy", "msg": "..."}
```

The file rotates at 10 MB and keeps 3 backups (`proxy.jsonl.1`, `.2`, `.3`).

---

## 10. Edge Cases and Stress Tests

```bash
# Zero bandwidth (still passes data, just slowly)
netshape run --bandwidth 0 -- curl --proxy http://127.0.0.1:8090 http://example.com

# 100% packet loss (all CONNECT/SOCKS5 connections dropped immediately)
netshape run --loss 100% -- curl --proxy http://127.0.0.1:8090 http://example.com

# Very high latency
netshape run --latency 2000ms -- python -c "import time; time.sleep(30)"

# Speed test with a large payload
netshape test --profile edge --bytes 131072

# Start with port 0 (OS picks a free port automatically)
netshape run --port 0 --profile wifi -- python -c "import time; time.sleep(10)"
```

---

## 11. Running the Test-App (Electron Dashboard)

The `test-app/` folder contains a standalone Electron testing application.

```bash
# Install dependencies (first time only)
cd test-app
npm install

# Start the app (opens an Electron window)
npm start
```

The app connects to a running NetShape session and shows live metrics.
Start a `netshape run` session first in another terminal.

---

## Complete Command Reference

### Top-level commands

| Command | Description |
|---|---|
| `netshape --version` | Print the installed version |
| `netshape --help` | Show all commands |

---

### `netshape run`

Start a new proxy session and launch a command inside it.

```
netshape run [OPTIONS] -- <command> [args...]
```

| Option | Default | Description |
|---|---|---|
| `--profile`, `-p` | — | Built-in profile name (e.g. `3g`, `satellite`) |
| `--bandwidth`, `-b` | unlimited | Bandwidth cap (e.g. `1mbps`, `500kbps`, `100000`) |
| `--latency`, `-l` | 0 | Added latency per connection (e.g. `200ms`) |
| `--loss` | 0% | Packet loss rate (e.g. `5%`, `0.05`) |
| `--jitter`, `-j` | 0 | Latency jitter (e.g. `50ms`) |
| `--timeout`, `-t` | none | Auto-stop after duration (e.g. `30m`, `1h`) |
| `--port` | 8090 | Traffic proxy port (0 = OS assigns free port) |
| `--log-file` | — | Write rotating JSON log lines to this path |

Examples:
```bash
netshape run --profile 3g -- curl http://example.com
netshape run --bandwidth 2mbps --latency 300ms --loss 1% -- myapp
netshape run --timeout 5m --log-file run.jsonl -- python app.py
```

---

### `netshape adjust`

Change throttle settings on the currently running session.

```
netshape adjust [OPTIONS]
```

| Option | Description |
|---|---|
| `--profile`, `-p` | Switch to a named profile |
| `--bandwidth`, `-b` | New bandwidth cap |
| `--latency`, `-l` | New latency |
| `--loss` | New loss rate |
| `--jitter`, `-j` | New jitter |

Examples:
```bash
netshape adjust --profile satellite
netshape adjust --bandwidth 10mbps --latency 20ms
netshape adjust --loss 0%    # clear packet loss
```

---

### `netshape status`

Show the status of the active session.

```
netshape status [OPTIONS]
```

| Option | Description |
|---|---|
| `--json` | Print raw JSON payload |
| `--watch`, `-w` | Refresh every second in a live table (Ctrl-C to stop) |

---

### `netshape stop`

Stop the currently running proxy session and restore the system to normal.

```
netshape stop
```

---

### `netshape test`

Verify that traffic flows through the proxy and measure throttle timing.

```
netshape test [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--profile`, `-p` | `3g` | Profile to test |
| `--bandwidth`, `-b` | — | Bandwidth override |
| `--latency`, `-l` | — | Latency override |
| `--loss` | — | Loss override |
| `--jitter`, `-j` | — | Jitter override |
| `--bytes` | 65536 | Payload size to download (bytes) |

---

### `netshape profiles`

List all built-in network profiles with their parameters.

```
netshape profiles
```

**Available profiles:**

| Name | Bandwidth | Latency | Loss | Jitter | Description |
|---|---|---|---|---|---|
| `2g` | 50 kbps | 500 ms | 2% | 150 ms | Typical 2G mobile |
| `3g` | 780 kbps | 200 ms | 1% | 60 ms | Typical 3G mobile |
| `4g` | 4 Mbps | 80 ms | 0.3% | 25 ms | Typical 4G mobile |
| `5g` | 50 Mbps | 30 ms | 0.1% | 10 ms | Fast mobile |
| `edge` | 240 kbps | 400 ms | 3% | 120 ms | Very slow EDGE |
| `wifi` | 30 Mbps | 25 ms | 0.1% | 8 ms | Home Wi-Fi |
| `cable` | 100 Mbps | 20 ms | 0.05% | 5 ms | Residential cable |
| `dsl` | 6 Mbps | 60 ms | 0.2% | 20 ms | Older DSL |
| `fiber` | 1 Gbps | 5 ms | 0% | 2 ms | Fast wired fiber |
| `satellite` | 12 Mbps | 650 ms | 0.5% | 80 ms | High-latency satellite |
| `congested` | 1.5 Mbps | 180 ms | 2.5% | 140 ms | Busy shared network |
| `offline` | 0 | 0 ms | 100% | 0 ms | All traffic dropped |

---

### `netshape metrics`

Print proxy metrics for the active session.

```
netshape metrics [OPTIONS]
```

| Option | Description |
|---|---|
| `--prometheus`, `-p` | Print in Prometheus text/plain exposition format |

Without flags, prints each metric as `key: value`. With `--prometheus`, outputs the full Prometheus format compatible with `promtool` and Grafana scrapers.

---

### `netshape rule` subcommands

Manage per-endpoint throttle rules on the active session.

#### `netshape rule add`

```
netshape rule add <pattern> [OPTIONS]
```

`<pattern>` is a **case-insensitive Python regex** matched against:
- **CONNECT / SOCKS5**: the hostname (`example.com`)
- **Plain HTTP**: the full URL (`http://example.com/path?q=1`)

| Option | Description |
|---|---|
| `--bandwidth`, `-b` | Bandwidth cap for matching connections |
| `--latency`, `-l` | Latency for matching connections |
| `--loss` | Packet loss for matching connections |
| `--jitter`, `-j` | Jitter for matching connections |
| `--comment`, `-c` | Human-readable label |

Unspecified fields fall back to the global session settings.

Examples:
```bash
netshape rule add "stripe\.com" --bandwidth 1mbps --latency 200ms
netshape rule add "api\." --loss 5% --comment "flaky API"
netshape rule add "\.png$" --bandwidth 100kbps     # throttle image downloads
```

#### `netshape rule list`

```
netshape rule list [--json]
```

Lists all active rules with their IDs, patterns, and parameters.

#### `netshape rule remove`

```
netshape rule remove <rule-id>
```

Removes a rule by its full UUID or an unambiguous prefix (first 8 chars).

---

### `netshape scenario` subcommands

Run time-sequenced network condition scenarios.

#### `netshape scenario run`

```
netshape scenario run [<file.yaml>] [OPTIONS]
```

| Option | Description |
|---|---|
| `--builtin`, `-b` | Run a built-in scenario by name |
| `--no-wait` | Submit and return immediately (no progress display) |

The command polls phase progress every second. Press **Ctrl-C** to stop
early — the pre-scenario config is restored automatically.

Examples:
```bash
netshape scenario run --builtin subway
netshape scenario run --builtin coffee-shop-wifi --no-wait
netshape scenario run my-scenario.yaml
```

#### `netshape scenario stop`

```
netshape scenario stop
```

Stops the running scenario and restores the pre-scenario configuration.

#### `netshape scenario status`

```
netshape scenario status [--json]
```

Shows which phase is currently running and how much time has elapsed.

#### `netshape scenario list`

```
netshape scenario list
```

Lists available built-in scenario names. Requires `pyyaml` to be installed (`pip install 'netshape[scenarios]'`).

**Built-in scenarios:**

| Name | Description |
|---|---|
| `subway` | Urban transit: 4G platform → tunnel dead zone → 2G re-emergence |
| `flight-mode` | Airport WiFi → in-flight high latency → offline → landing |
| `coffee-shop-wifi` | Quiet morning → lunch congestion → packet-loss hell → recovery |
| `satellite` | Clear sky → cloud cover → rain fade → outage → restore |

---

### Control API (HTTP — port 8091 by default)

All endpoints return JSON unless noted. The control port is shown in `netshape status`.

| Method | Path | Description |
|---|---|---|
| `GET` | `/status` | Full session status + config |
| `POST` | `/configure` | Update throttle settings (`bandwidth_bps`, `latency_ms`, `loss_pct`, `jitter_ms`, `profile`) |
| `POST` | `/shutdown` | Gracefully shut down the proxy |
| `GET` | `/metrics` | Prometheus text exposition (all 12 metrics) |
| `GET` | `/metrics?format=json` | Same metrics as a JSON object |
| `GET` | `/rules` | List all per-endpoint rules |
| `POST` | `/rules` | Add a rule (`pattern`, optional `bandwidth_bps`, `latency_ms`, `loss_pct`, `jitter_ms`, `comment`) |
| `DELETE` | `/rules/<id>` | Remove a rule by UUID |
| `GET` | `/scenarios` | List built-in scenario names |
| `POST` | `/scenario/start` | Start a scenario (body: full dict or `{"builtin": "subway"}`) |
| `POST` | `/scenario/stop` | Stop the running scenario and restore config |
| `GET` | `/scenario/status` | Current scenario phase and progress |
| `GET` | `/events` | Server-Sent Events stream (1 event/s with live metrics + scenario) |
| `GET` | `/logs` | Last 200 proxy log lines as JSON |
| `GET` | `/` or `/dashboard` | Web dashboard HTML |

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Error: no active NetShape session` | No session is running | Run `netshape run -- <cmd>` first |
| `Error: pyyaml is required` | pyyaml not installed | `pip install 'netshape[scenarios]'` |
| Dashboard shows "Offline" | Browser opened before session started | Reload the page |
| `curl: (7) Failed to connect` | Proxy port not listening | Check `netshape status` for the port |
| Tests fail with `asyncio` errors | Wrong Python version | Requires Python ≥ 3.10 |
| `netshape` not found | Package not installed | `pip install -e ".[dev,scenarios]"` |
