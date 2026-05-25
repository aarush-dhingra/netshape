# NetShape Test App

A standalone Electron visual integration test harness for the NetShape network throttling proxy.

## Prerequisites

- Node.js 18+
- npm

## Install

```bash
cd test-app
npm install
```

## Usage

Start NetShape with a profile and then launch the test app:

```bash
netshape run --profile 3g -- npx electron .
```

Alternatively, if `netshape` is not in your PATH, use:

```bash
python -m netshape run --profile 3g -- npx electron .
```

The `netshape run` command sets `HTTP_PROXY` and `HTTPS_PROXY` environment variables automatically, so the Electron app routes all measurement traffic through the proxy.

## What the UI shows

- **Left column**: Real-time metrics (download Mbps, upload Mbps, latency ms, loss %) and two live Chart.js line charts showing throughput and latency history.
- **Right column**: NetShape controls — select a preset profile, adjust bandwidth/latency/loss/jitter sliders, and apply changes to the running proxy.

## Important note

The control panel connects directly to NetShape's control port (`127.0.0.1:8091`) — not through the proxy — so it works even under severe throttling. Measurement traffic goes through the proxy (`127.0.0.1:8090`).
