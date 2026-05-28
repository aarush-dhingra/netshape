const { app, BrowserWindow, ipcMain } = require('electron');
const http = require('http');
const https = require('https');
const path = require('path');
const { performance } = require('perf_hooks');

const { createLocalServer } = require('./server');
const { TimedRequest } = require('./TimedRequest');

// ─── STATE ───────────────────────────────────────────────────────────────────
let mainWindow = null;
const sessionStats = {
  totalRequests: 0,
  totalBytes: 0,
  totalDuration: 0,
  latencySum: 0,
  latencyCount: 0,
};

// ─── LOCAL BACKEND ───────────────────────────────────────────────────────────
const localServer = createLocalServer();
const LOCAL_PORT = 7331;
const CONTROL_PORT = 8091;
const PROXY_STATUS_URL = `http://127.0.0.1:${CONTROL_PORT}/status`;

function startLocalServer() {
  return new Promise((resolve) => {
    const server = localServer.listen(LOCAL_PORT, () => {
      console.log(`[backend] Local server listening on http://127.0.0.1:${LOCAL_PORT}`);
      resolve(server);
    });
  });
}

// ─── HELPERS ─────────────────────────────────────────────────────────────────
function updateSessionStats(result) {
  sessionStats.totalRequests++;
  sessionStats.totalBytes += result.bytes || 0;
  sessionStats.totalDuration += result.duration_ms || 0;
  if (result.duration_ms != null && !result.error) {
    sessionStats.latencySum += result.duration_ms;
    sessionStats.latencyCount++;
  }
}

function getSessionSummary() {
  return {
    totalRequests: sessionStats.totalRequests,
    totalBytes: sessionStats.totalBytes,
    avgLatency:
      sessionStats.latencyCount > 0
        ? Math.round(sessionStats.latencySum / sessionStats.latencyCount)
        : 0,
  };
}

// Keep a simple event-emitter pattern for streaming results back to the renderer.
const resultListeners = new Set();
function emitResult(type, data) {
  for (const win of resultListeners) {
    win.webContents.send('test-result', { type, data });
  }
}

// ─── LOGGING HELPERS ─────────────────────────────────────────────────────────
// Emit a plain-English log line to the renderer's activity log panel.
function log(level, msg) {
  emitResult('log', { level, msg, ts: Date.now() });
  console.log(`[${level.toUpperCase()}] ${msg}`);
}

// Tiny formatters for use inside the main process (no DOM available here).
function _ms(n) { return n != null ? `${Math.round(n)} ms` : '--'; }
function _bps(n) {
  if (!n) return '--';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)} Mbps`;
  if (n >= 1_000)     return `${(n / 1_000).toFixed(0)} Kbps`;
  return `${Math.round(n)} bps`;
}
function _bytes(n) {
  if (n == null) return '--';
  if (n < 1024)             return `${n} B`;
  if (n < 1024 * 1024)      return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}
function _host(url) {
  try { const u = new URL(url); return u.hostname + u.pathname.slice(0, 24); }
  catch { return url.slice(0, 30); }
}

// ─── IPC HANDLERS ───────────────────────────────────────────────────────────

// Register/unregister renderer windows so we can push streaming results.
ipcMain.on('register-listener', (event) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  if (win) resultListeners.add(win);
});

ipcMain.on('unregister-listener', (event) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  if (win) resultListeners.delete(win);
});

// Session stats (cumulative totals across all test runs).
ipcMain.handle('get-session-stats', () => getSessionSummary());

// Proxy status polling (read-only).
ipcMain.handle('get-proxy-status', async () => {
  return new Promise((resolve) => {
    const req = http.get(PROXY_STATUS_URL, { timeout: 5000 }, (res) => {
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => {
        try {
          const parsed = JSON.parse(data);
          resolve({ ok: true, ...parsed });
        } catch {
          resolve({ ok: false, error: 'Invalid JSON from proxy status' });
        }
      });
    });
    req.on('error', () => resolve({ ok: false, error: 'Proxy unreachable' }));
    req.on('timeout', () => { req.destroy(); resolve({ ok: false, error: 'Proxy timeout' }); });
  });
});

// ─── TEST 1: Latency Ping Fire ───────────────────────────────────────────────
ipcMain.handle('test-latency', async () => {
  log('info', '▶ Latency – sending 5 sequential pings to httpbin.org/get');
  const results = [];
  for (let i = 0; i < 5; i++) {
    const req = new TimedRequest('https://httpbin.org/get', { timeout: 30000 });
    const result = await req.run();
    updateSessionStats(result);
    results.push(result);
    emitResult('latency', { index: i, result });
    const ok = result.status === 200 && !result.error;
    log(ok ? 'ok' : 'error',
      `  ${ok ? '✓' : '✗'} Ping ${i + 1}/5 → ${_ms(result.duration_ms)}` +
      (result.error ? `  [${result.error}]` : `  (TTFB ${_ms(result.ttfb_ms)})`));
  }

  const durations = results.filter((r) => r.duration_ms != null).map((r) => r.duration_ms);
  const sorted = [...durations].sort((a, b) => a - b);
  const min = sorted[0] || 0;
  const max = sorted[sorted.length - 1] || 0;
  const avg = sorted.length ? sorted.reduce((a, b) => a + b, 0) / sorted.length : 0;
  const p95 = sorted.length ? percentile(sorted, 0.95) : 0;

  log('ok', `  Done – avg ${_ms(Math.round(avg))}  p95 ${_ms(Math.round(p95))}  min ${_ms(min)}  max ${_ms(max)}`);
  return {
    results,
    metrics: { min, avg: Math.round(avg), max, p95: Math.round(p95) },
  };
});

function percentile(sorted, p) {
  const idx = Math.ceil((sorted.length - 1) * p);
  return sorted[Math.min(idx, sorted.length - 1)];
}

// ─── TEST 2: Download Throughput ─────────────────────────────────────────────
ipcMain.handle('test-download', async () => {
  log('info', '▶ Download – fetching 50 KB / 500 KB / 2 MB from httpbin.org/bytes');
  const sizes = [
    { label: '50 KB', bytes: 51200 },
    { label: '500 KB', bytes: 512000 },
    { label: '2 MB', bytes: 2097152 },
  ];

  const results = [];
  for (let i = 0; i < sizes.length; i++) {
    const { label, bytes } = sizes[i];
    const url = `https://httpbin.org/bytes/${bytes}`;
    const req = new TimedRequest(url, { timeout: 60000 });
    const result = await req.run();
    updateSessionStats(result);
    results.push({ label, ...result });
    emitResult('download', { index: i, label, result });
    const ok = result.status === 200 && !result.error;
    log(ok ? 'ok' : 'error',
      `  ${ok ? '✓' : '✗'} ${label} → ${_bps(result.throughput_bps)} in ${_ms(result.duration_ms)}` +
      (result.error ? `  [${result.error}]` : `  (received ${_bytes(result.bytes)})`));
  }

  return { results };
});

// ─── TEST 3: Upload Throughput ───────────────────────────────────────────────
ipcMain.handle('test-upload', async () => {
  log('info', '▶ Upload – POSTing 50 KB / 500 KB / 2 MB random data to httpbin.org/post');
  const sizes = [
    { label: '50 KB', bytes: 51200 },
    { label: '500 KB', bytes: 512000 },
    { label: '2 MB', bytes: 2097152 },
  ];

  const results = [];
  for (let i = 0; i < sizes.length; i++) {
    const { label, bytes } = sizes[i];
    const body = require('crypto').randomBytes(bytes);
    const req = new TimedRequest('https://httpbin.org/post', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/octet-stream',
        'Content-Length': bytes,
      },
      body,
      timeout: 60000,
    });
    const result = await req.run();
    updateSessionStats(result);
    results.push({ label, ...result });
    emitResult('upload', { index: i, label, result });
    const ok = result.status === 200 && !result.error;
    log(ok ? 'ok' : 'error',
      `  ${ok ? '✓' : '✗'} ${label} → ${_bps(result.throughput_bps)} in ${_ms(result.duration_ms)}` +
      (result.error ? `  [${result.error}]` : ''));
  }

  return { results };
});

// ─── TEST 4: REST API Chain ──────────────────────────────────────────────────
ipcMain.handle('test-api-chain', async () => {
  log('info', '▶ API Chain – 5 sequential calls to real public APIs');
  const urls = [
    'https://jsonplaceholder.typicode.com/posts/1',
    'https://jsonplaceholder.typicode.com/users/1',
    'https://api.open-meteo.com/v1/forecast?latitude=48.85&longitude=2.35&current_weather=true',
    'https://httpbin.org/uuid',
    'https://httpbin.org/headers',
  ];

  const results = [];
  let cumulative = 0;
  for (let i = 0; i < urls.length; i++) {
    const req = new TimedRequest(urls[i], { timeout: 30000 });
    const result = await req.run();
    updateSessionStats(result);
    cumulative += result.duration_ms || 0;
    results.push({ ...result, cumulative });
    emitResult('api-chain', { index: i, result: { ...result, cumulative } });
    const ok = result.status === 200 && !result.error;
    log(ok ? 'ok' : 'error',
      `  ${ok ? '✓' : '✗'} Step ${i + 1}/5  ${_host(urls[i])} → ` +
      `${result.status ?? 'ERR'}  ${_ms(result.duration_ms)}` +
      (result.error ? `  [${result.error}]` : ''));
  }

  log('ok', `  Done – total chain time ${_ms(cumulative)}`);
  return { results };
});

// ─── TEST 5: Streaming / TTFB ────────────────────────────────────────────────
ipcMain.handle('test-stream', async () => {
  log('info', '▶ Stream – consuming 20-chunk stream from httpbin.org/stream/20');
  const req = new TimedRequest('https://httpbin.org/stream/20', { timeout: 30000 });
  const result = await req.run();
  updateSessionStats(result);
  const ok = result.status === 200 && !result.error;
  log(ok ? 'ok' : 'error',
    `  ${ok ? '✓' : '✗'} TTFB ${_ms(result.ttfb_ms)}  total ${_ms(result.duration_ms)}` +
    `  ${result.chunks} chunks  ${_bps(result.throughput_bps)}` +
    (result.error ? `  [${result.error}]` : ''));
  return { result };
});

// ─── TEST 6: LLM API Call (non-streaming fallback, kept as dead code) ────────
// The renderer always calls 'test-llm-stream' directly, so this is not used.
ipcMain.handle('test-llm', async (event, apiKey) => {
  if (!apiKey) return { error: 'No API key provided' };
  log('info', '▶ LLM (simple) – single round-trip to OpenAI');
  const body = JSON.stringify({
    model: 'gpt-4o-mini',
    messages: [{ role: 'user', content: 'Reply with exactly 100 words about space.' }],
    stream: true,
  });
  const req = new TimedRequest('https://api.openai.com/v1/chat/completions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${apiKey}` },
    body,
    timeout: 60000,
  });
  const result = await req.run();
  updateSessionStats(result);
  return { result };
});

// ─── TEST 7: Concurrent Burst ────────────────────────────────────────────────
ipcMain.handle('test-burst', async (event, count) => {
  log('info', `▶ Burst – firing ${count} parallel GET requests to httpbin.org/get simultaneously`);
  const url = 'https://httpbin.org/get';
  const promises = Array.from({ length: count }, () => {
    const req = new TimedRequest(url, { timeout: 30000 });
    return req.run();
  });

  const results = await Promise.all(promises);
  results.forEach(updateSessionStats);

  const durations = results.filter((r) => r.duration_ms != null).map((r) => r.duration_ms);
  const sorted = [...durations].sort((a, b) => a - b);
  const fastest = sorted[0] || 0;
  const slowest = sorted[sorted.length - 1] || 0;
  const median = sorted.length ? sorted[Math.floor(sorted.length / 2)] : 0;
  const completed = results.filter((r) => !r.error && r.status === 200).length;
  const failed = results.length - completed;

  const allPassed = failed === 0;
  log(allPassed ? 'ok' : 'warn',
    `  ${allPassed ? '✓' : '⚠'} Burst done – ${completed}/${count} passed` +
    `  fastest ${_ms(fastest)}  median ${_ms(median)}  slowest ${_ms(slowest)}` +
    (failed > 0 ? `  (${failed} failed)` : ''));

  return {
    results,
    metrics: { fastest, slowest, median, completed, failed },
  };
});

// ─── LLM STREAMING (custom handler for live token display) ───────────────────
ipcMain.handle('test-llm-stream', async (event, apiKey) => {
  if (!apiKey) {
    log('warn', '  ⚠ LLM skipped – no API key provided (enter one in the card or set OPENAI_API_KEY)');
    return { error: 'No API key provided' };
  }
  log('info', '▶ LLM – streaming GPT-4o-mini via OpenAI (tokens will appear live below)');

  return new Promise((resolve) => {
    const startTime = performance.now();
    let ttfb = null;
    let firstTokenTime = null;
    let tokenCount = 0;
    let chunkCount = 0;
    const tokens = [];
    let status = null;

    const body = JSON.stringify({
      model: 'gpt-4o-mini',
      messages: [{ role: 'user', content: 'Reply with exactly 100 words about space.' }],
      stream: true,
    });

    const proxyUrl = process.env.HTTPS_PROXY || process.env.HTTP_PROXY;
    const options = {
      hostname: 'api.openai.com',
      port: 443,
      path: '/v1/chat/completions',
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${apiKey}`,
      },
      timeout: 60000,
    };

    if (proxyUrl) {
      const { HttpsProxyAgent } = require('https-proxy-agent');
      options.agent = new HttpsProxyAgent(proxyUrl);
    }

    const req = https.request(options, (res) => {
      status = res.statusCode;

      res.on('data', (chunk) => {
        if (ttfb === null) {
          ttfb = performance.now() - startTime;
        }
        chunkCount++;
        const lines = chunk.toString().split('\n');
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6);
            if (data === '[DONE]') continue;
            try {
              const parsed = JSON.parse(data);
              const delta = parsed.choices?.[0]?.delta?.content;
              if (delta) {
                if (firstTokenTime === null) {
                  firstTokenTime = performance.now() - startTime;
                }
                tokens.push(delta);
                tokenCount++;
                emitResult('llm-token', { token: delta });
              }
            } catch {
              // ignore parse errors for non-JSON lines
            }
          }
        }
      });

      res.on('end', () => {
        const endTime = performance.now();
        const duration = endTime - startTime;
        const result = {
          url: 'https://api.openai.com/v1/chat/completions',
          status,
          ttfb_ms: ttfb !== null ? Math.round(ttfb * 100) / 100 : null,
          duration_ms: Math.round(duration * 100) / 100,
          bytes: tokens.join('').length,
          throughput_bps: 0,
          error: null,
          chunks: chunkCount,
          firstTokenTime,
          tokenCount,
          text: tokens.join(''),
        };
        updateSessionStats(result);
        const ok = status === 200;
        log(ok ? 'ok' : 'error',
          `  ${ok ? '✓' : '✗'} LLM done – first token ${_ms(firstTokenTime)}` +
          `  total ${_ms(Math.round(duration))}  ${tokenCount} tokens` +
          (ok ? '' : `  [HTTP ${status}]`));
        resolve({ result });
      });

      res.on('error', (err) => {
        resolve({
          result: {
            url: 'https://api.openai.com/v1/chat/completions',
            status,
            ttfb_ms: null,
            duration_ms: performance.now() - startTime,
            bytes: 0,
            throughput_bps: 0,
            error: err.message,
            chunks: chunkCount,
          },
        });
      });
    });

    req.on('error', (err) => {
      resolve({
        result: {
          url: 'https://api.openai.com/v1/chat/completions',
          status: null,
          ttfb_ms: null,
          duration_ms: performance.now() - startTime,
          bytes: 0,
          throughput_bps: 0,
          error: err.message,
          chunks: 0,
        },
      });
    });

    req.on('timeout', () => {
      req.destroy();
      resolve({
        result: {
          url: 'https://api.openai.com/v1/chat/completions',
          status: null,
          ttfb_ms: null,
          duration_ms: performance.now() - startTime,
          bytes: 0,
          throughput_bps: 0,
          error: 'Request timeout',
          chunks: 0,
        },
      });
    });

    req.write(body);
    req.end();
  });
});

// ─── ELECTRON MAIN ───────────────────────────────────────────────────────────
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      preload: path.join(__dirname, 'preload.js'),
    },
  });

  mainWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'));
  // mainWindow.webContents.openDevTools({ mode: 'detach' });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.whenReady().then(async () => {
  await startLocalServer();
  createWindow();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});
