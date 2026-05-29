# NetShape — Quick Start Guide

## Install

```bash
pip install netshape
```

Verify:
```bash
netshape --version
```

---

## Starting Your App Through NetShape

NetShape wraps your app — it sets up the proxy and launches your command. Everything after `--` is the command to run.

```bash
# Run with a built-in profile
netshape run --profile 3g -- your-app-command

# Examples
netshape run --profile 3g -- npx electron .
netshape run --profile 4g -- python app.py
netshape run --profile satellite -- node server.js
netshape run --profile wifi -- npm start

# Run with custom settings (no profile)
netshape run --bandwidth 1mbps --latency 200ms --loss 1% -- your-app

# Run with a profile and override one value
netshape run --profile 3g --latency 500ms -- your-app

# Auto-stop after a duration
netshape run --profile 3g --timeout 30m -- your-app
```

---

## Built-in Profiles

| Profile | Bandwidth | Latency | Loss | Jitter | Description |
|---|---|---|---|---|---|
| `2g` | 50 Kbps | 500 ms | 2% | 150 ms | Typical 2G mobile |
| `3g` | 780 Kbps | 200 ms | 1% | 60 ms | Typical 3G mobile |
| `4g` | 4 Mbps | 80 ms | 0.3% | 25 ms | Typical 4G mobile |
| `5g` | 50 Mbps | 30 ms | 0.1% | 10 ms | Fast mobile |
| `edge` | 240 Kbps | 400 ms | 3% | 120 ms | Very slow mobile edge |
| `wifi` | 30 Mbps | 25 ms | 0.1% | 8 ms | Common home Wi-Fi |
| `dsl` | 6 Mbps | 60 ms | 0.2% | 20 ms | Older DSL broadband |
| `cable` | 100 Mbps | 20 ms | 0.05% | 5 ms | Residential cable |
| `fiber` | 1 Gbps | 5 ms | 0% | 2 ms | Fast wired fiber |
| `satellite` | 12 Mbps | 650 ms | 0.5% | 80 ms | High-latency satellite |
| `congested` | 1.5 Mbps | 180 ms | 2.5% | 140 ms | Busy shared network |
| `offline` | 0 | 0 ms | 100% | 0 ms | All traffic dropped |

List them anytime:
```bash
netshape profiles
```

---

## Throttling Commands

All `adjust` commands apply **live** to the running session — no restart needed.

```bash
# Switch to a different profile
netshape adjust --profile satellite

# Change individual values
netshape adjust --bandwidth 500kbps
netshape adjust --latency 300ms
netshape adjust --loss 2%
netshape adjust --jitter 50ms

# Change multiple values at once
netshape adjust --bandwidth 1mbps --latency 200ms --loss 1% --jitter 30ms

# Reset to no throttling
netshape adjust --bandwidth 0 --latency 0 --loss 0 --jitter 0
```

**Value formats accepted:**

| Parameter | Examples |
|---|---|
| Bandwidth | `500kbps`, `1mbps`, `5mbps`, `100kbps`, `0` |
| Latency | `200ms`, `1s`, `500ms`, `0` |
| Loss | `1%`, `0.5%`, `2%`, `0` |
| Jitter | `50ms`, `20ms`, `0` |

---

## Session Commands

```bash
# Check current status
netshape status

# Watch status live (updates every second, Ctrl-C to stop)
netshape status --watch

# Get status as JSON
netshape status --json

# Show metrics (requests, bytes, drops, etc.)
netshape metrics

# Stop the proxy session
netshape stop

# Verify proxy is working
netshape test
netshape test --profile 3g
```

---

## Per-Endpoint Rules

Rules let you throttle specific domains or API endpoints differently from the global settings.

```bash
# Add a rule (pattern is a regex matched against the host/URL)
netshape rule add stripe\.com --bandwidth 1mbps --latency 200ms --comment "payment API"
netshape rule add "api\." --loss 5% --comment "flaky API"
netshape rule add openai\.com --bandwidth 500kbps --latency 300ms

# List all rules
netshape rule list

# Enable / disable a rule (by ID prefix or comment name)
netshape rule enable "payment API"
netshape rule disable "payment API"
netshape rule enable ab12cd34

# Remove a rule
netshape rule remove "payment API"
netshape rule remove ab12cd34
```

Rules are **persistent** — they are saved to `~/.netshape/rules.json` and automatically restored when you start a new session (in disabled state by default).

---

## Scenarios

Scenarios run a sequence of network conditions automatically over time.

```bash
# List available scenarios
netshape scenario list

# Run a built-in scenario
netshape scenario run --builtin subway
netshape scenario run --builtin satellite
netshape scenario run --builtin coffee-shop-wifi
netshape scenario run --builtin flight-mode

# Run a custom scenario file
netshape scenario run ./my-scenario.yaml

# Run and return immediately (don't wait for completion)
netshape scenario run --builtin subway --no-wait

# Check scenario progress
netshape scenario status

# Stop a running scenario
netshape scenario stop
```

---

## Dashboard

The dashboard is a live web UI built into the proxy. It lets you control all settings visually without using the terminal.

### Starting the dashboard

The dashboard starts automatically with every `netshape run` session. While a session is active, open:

```
http://127.0.0.1:8091/dashboard
```

in your browser.

### Dashboard overview

**Left column — Live metrics:**
- Real-time download / upload speed graphs
- Current bandwidth, latency, loss, jitter readings
- Proxy connection status

**Right column — Controls:**

| Section | What it does |
|---|---|
| **Current Config** | Shows the active throttle values (collapsible) |
| **Controls** | Sliders to adjust bandwidth, latency, loss, jitter live |
| **Per-Endpoint Rules** | Add, enable/disable, and remove domain-specific rules |
| **Scenarios** | Run built-in or saved scenarios, build custom ones |
| **Logs** | Live activity log from the proxy |

### Using the controls

1. **Bandwidth slider** — drag to change speed. Use the `kbps / Mbps` dropdown next to it to switch units.
2. **Latency, Loss, Jitter sliders** — drag to apply immediately.
3. Click **Apply** to send changes to the proxy.

### Adding a per-endpoint rule from the dashboard

1. Scroll to **Per-Endpoint Rules**
2. Click **+ Add Rule**
3. Fill in:
   - **Pattern** — regex for the domain, e.g. `stripe\.com` or `api\.`
   - **Bandwidth** — with kbps / Mbps toggle
   - **Latency, Loss, Jitter** — optional
   - **Name** — a label for the rule
4. Click **Add Rule**
5. Toggle it on/off with the pill switch next to each rule

### Running a scenario from the dashboard

1. Scroll to **Scenarios**
2. Select a scenario from the dropdown (built-in and your saved ones are listed)
3. Click **▶ Run**

Or build a custom one:
1. Click **Build Custom Scenario**
2. Enter a scenario name
3. Click **+ Add Phase** for each phase, set duration and throttle values
4. Click **▶ Run** to run immediately, or **💾 Save** to save for later use

---

## Saving logs to a file

```bash
netshape run --profile 3g --log-file proxy.log -- your-app
```

Logs are written as JSON lines, rotating at 10 MB.
