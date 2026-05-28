# NetShape Test App

A standalone Electron test harness for measuring real-world network impact through the NetShape proxy.

## What it does

The Traffic Lab generates real network traffic through the NetShape proxy and measures the results. It comprises seven independent test suites—latency, throughput (upload/download), REST API chaining, streaming, LLM API calls, and concurrent burst testing—each reporting timing, throughput, and pass/fail status. Results are shown in real time with bar charts, throughput bars, and a live summary panel.

## Prerequisites

- Node.js 18+
- npm
- (Optional) OpenAI API key if you want to run the LLM test

## Install

```bash
cd test-app
npm install
```

## Usage

You must launch the app through `netshape run` for the proxy environment variables (`HTTP_PROXY` / `HTTPS_PROXY`) to be injected. From the `test-app/` directory:

```bash
netshape run -- npx electron .
```

Or, if `netshape` is not in your PATH:

```bash
python -m netshape run -- npx electron .
```

## Important notes

- **Proxy traffic**: All outbound HTTP/HTTPS tests use Node's `https` module (via TimedRequest) which respects `HTTP_PROXY` / `HTTPS_PROXY`.
- **Local backend**: The test app starts a local Express server on `http://127.0.0.1:7331`. Local loopback addresses are NOT proxied by design, so these routes bypass NetShape and serve as baseline timings.
- **LLM API key**: You can provide an OpenAI API key in the UI (stored in memory only, never on disk) or set the `OPENAI_API_KEY` environment variable before launching.

## Test suites

| # | Suite | Description |
|---|-------|-------------|
| 1 | Latency Ping Fire | 5 sequential GET requests to measure round-trip latency |
| 2 | Download Throughput | Downloads of 50 KB, 500 KB, and 2 MB from httpbin.org |
| 3 | Upload Throughput | POSTs of 50 KB, 500 KB, and 2 MB to httpbin.org |
| 4 | REST API Chain | Sequential calls across multiple public APIs |
| 5 | Streaming / TTFB | Chunked stream from httpbin.org with TTFB and throughput |
| 6 | LLM API Call | Streaming GPT-4o-mini request to OpenAI |
| 7 | Concurrent Burst | Parallel GET burst with configurable concurrency |
