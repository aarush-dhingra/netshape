# NetShape

**Simulate real-world network conditions for any app — without touching your code.**

NetShape is a local HTTP/HTTPS forward proxy that wraps any process and applies bandwidth throttling, latency, packet loss, and jitter to its network traffic.

[![Tests](https://github.com/aarush-dhingra/netshape/actions/workflows/test.yml/badge.svg)](https://github.com/aarush-dhingra/netshape/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/netshape.svg)](https://pypi.org/project/netshape/)
[![Python](https://img.shields.io/pypi/pyversions/netshape.svg)](https://pypi.org/project/netshape/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

---

## Quick Start

```bash
pip install netshape

# Wrap any command — proxy env vars are injected automatically
netshape run --profile 3g -- python my_app.py
netshape run --profile slow-wifi -- npm start
netshape run --profile 4g -- npx electron .
```

Adjust conditions live without restarting the process:

```bash
netshape adjust --bandwidth 500kbps --latency 200ms --loss 2%
netshape adjust --profile 2g
```

Open the dashboard in your browser to visualise and control in real time:

```
http://127.0.0.1:8090/dashboard
```

---

## How It Works

NetShape starts a local forward proxy on `127.0.0.1` and injects three environment variables into the child process:

```
HTTP_PROXY=http://127.0.0.1:8090
HTTPS_PROXY=http://127.0.0.1:8090
ALL_PROXY=http://127.0.0.1:8090
```

Any HTTP/HTTPS library that respects these variables (the overwhelming majority do) routes through the proxy automatically. No code changes required.

```
Your App → NetShape Proxy (throttle/delay/drop) → Internet
```

For HTTPS, NetShape establishes a transparent `CONNECT` tunnel — it does **not** decrypt TLS traffic.

A second port (by default `+1`) hosts the control API used by CLI commands and the dashboard.

---

## Built-in Profiles

| Profile | Bandwidth | Latency | Loss | Jitter |
|---|---|---|---|---|
| `2g` | 250 kbps | 600 ms | 2.5% | 200 ms |
| `3g` | 780 kbps | 200 ms | 1% | 60 ms |
| `4g` | 9 Mbps | 50 ms | 0.1% | 20 ms |
| `lte` | 20 Mbps | 30 ms | 0.05% | 10 ms |
| `wifi` | 25 Mbps | 10 ms | 0.01% | 5 ms |
| `broadband` | 10 Mbps | 20 ms | 0% | 5 ms |
| `cable` | 50 Mbps | 15 ms | 0% | 3 ms |
| `fiber` | 1 Gbps | 2 ms | 0% | 1 ms |
| `satellite` | 2 Mbps | 600 ms | 0.5% | 100 ms |

```bash
netshape profiles          # list all profiles
netshape run --profile 3g -- <cmd>
netshape adjust --profile satellite
```

---

## CLI Reference

```
netshape run [--profile PROFILE] [--bandwidth BW] [--latency MS]
             [--loss PCT] [--jitter MS] [--port PORT] -- <command>

netshape adjust [--profile PROFILE] [--bandwidth BW] [--latency MS]
                [--loss PCT] [--jitter MS]

netshape status            # current proxy state
netshape metrics           # traffic statistics
netshape test              # run a self-test through the proxy

netshape rule add --host PATTERN [--bandwidth BW] [--latency MS]
netshape rule list
netshape rule enable <id>
netshape rule disable <id>
netshape rule remove <id>

netshape scenario run <name>    # run a built-in or saved scenario
netshape scenario stop
netshape scenario status
netshape scenario list

netshape profiles          # list built-in network profiles
```

---

## Per-Endpoint Rules

Apply different conditions to different hosts:

```bash
netshape rule add --host "stripe\.com" --latency 300ms --loss 5%
netshape rule add --host "api\.slow-service\.io" --bandwidth 100kbps
netshape rule list
```

Rules match by regular expression against the request hostname.

---

## Scenario Scripting

Chain multiple phases to simulate changing conditions over time:

```bash
netshape scenario run tunnel-drop   # built-in: good → bad → recovery
netshape scenario list              # see all built-in and saved scenarios
```

---

## Multiple Services

Run separate NetShape instances for each process — ports are auto-assigned:

```bash
# Terminal 1
netshape run --profile 3g -- npx electron .          # proxy on :8090

# Terminal 2
netshape run --profile satellite -- python backend/main.py  # proxy on :8092
```

Each instance has its own dashboard, rules, and metrics.

---

## Compatibility

Works with any app that reads standard proxy environment variables, including:

- `requests`, `httpx`, `aiohttp`, `urllib3`
- Node.js (`node-fetch`, `axios`, `got`, `undici`)
- Electron (renderer process via `session.setProxy()`)
- LiteLLM, OpenAI SDK, Anthropic SDK
- `curl`, `wget`

> **Python note:** NetShape sets `ALL_PROXY=http://...`. Libraries such as LiteLLM that use `httpx` do not require the `socksio` package.

---

## Security

NetShape is a local developer tool. The proxy and control API bind to `127.0.0.1` only and are never exposed to the network. See [SECURITY.md](SECURITY.md) for the full security model.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).
