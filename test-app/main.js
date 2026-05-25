const { app, BrowserWindow, ipcMain } = require('electron');
const http = require('http');
const https = require('https');
const path = require('path');

// ─── CONFIG (easy to change) ───────────────────────────────────────────────
const CONFIG = {
  proxyHost: "127.0.0.1",
  proxyPort: 8090,
  controlPort: 8091,
  measureIntervalMs: 2500,
  historyPoints: 60,
  downloadUrl: "https://speed.cloudflare.com/__down?bytes=500000",
  downloadTimeoutMs: 30000,
  uploadUrl: "https://speed.cloudflare.com/__up",
  uploadBytes: 500000,
  pingUrl: "https://www.google.com",
};

// ─── LOGGER ────────────────────────────────────────────────────────────────
const LOG_LEVELS = { INFO: 'INFO', WARN: 'WARN', ERROR: 'ERROR' };

function log(level, msg, data) {
  const ts = new Date().toISOString();
  const prefix = `[${ts}] [${level}]`;
  if (data !== undefined) {
    console.log(`${prefix} ${msg}`, typeof data === 'object' ? JSON.stringify(data) : data);
  } else {
    console.log(`${prefix} ${msg}`);
  }
}

const logger = {
  info:  (msg, data) => log(LOG_LEVELS.INFO,  msg, data),
  warn:  (msg, data) => log(LOG_LEVELS.WARN,  msg, data),
  error: (msg, data) => log(LOG_LEVELS.ERROR, msg, data),
};

// ─── STATE ─────────────────────────────────────────────────────────────────
let mainWindow = null;
let measureInterval = null;
let consecutiveErrors = 0;
const history = {
  download: [],
  upload: [],
  latency: [],
};

// ─── HELPERS ───────────────────────────────────────────────────────────────
function now() {
  return new Date().toISOString();
}

function msSince(start) {
  return Date.now() - start;
}

function makeProxyRequest(urlStr, options = {}) {
  const url = new URL(urlStr);
  const isHttps = url.protocol === 'https:';
  const proxyHost = CONFIG.proxyHost;
  const proxyPort = CONFIG.proxyPort;

  if (isHttps) {
    return makeHttpsThroughProxy(url, options);
  }

  const requestOptions = {
    hostname: proxyHost,
    port: proxyPort,
    method: options.method || 'GET',
    path: url.href,
    headers: {
      Host: url.host,
      ...options.headers,
    },
    timeout: options.timeout || 5000,
    agent: false,
  };

  return new Promise((resolve, reject) => {
    const start = Date.now();
    const req = http.request(requestOptions);

    req.on('timeout', () => {
      req.destroy();
      reject(new Error('Request timeout'));
    });

    req.on('error', reject);

    req.on('response', (res) => {
      const chunks = [];
      res.on('data', chunk => chunks.push(chunk));
      res.on('end', () => {
        const elapsed = Date.now() - start;
        resolve({
          bytes: Buffer.concat(chunks).length,
          elapsed,
          status: res.statusCode,
          headers: res.headers,
          error: null,
        });
      });
      res.on('error', reject);
    });

    if (options.body) {
      req.write(options.body);
    }
    req.end();
  });
}

function makeHttpsThroughProxy(url, options) {
  const proxyHost = CONFIG.proxyHost;
  const proxyPort = CONFIG.proxyPort;

  logger.info(`HTTPS CONNECT → ${url.hostname}:443 via ${proxyHost}:${proxyPort}`);

  return new Promise((resolve, reject) => {
    const start = Date.now();
    const connectReq = http.request({
      hostname: proxyHost,
      port: proxyPort,
      method: 'CONNECT',
      path: `${url.hostname}:443`,
      timeout: options.timeout || 5000,
    });

    connectReq.on('error', (err) => {
      logger.error(`CONNECT failed for ${url.hostname}: ${err.message}`);
      reject(err);
    });
    connectReq.on('connect', (res, socket) => {
      logger.info(`CONNECT tunnel established → ${url.hostname} (proxy status ${res.statusCode})`);
      const tls = require('tls');
      let settled = false;
      const settle = (fn, val) => {
        if (!settled) { settled = true; fn(val); }
      };

      const tlsConn = tls.connect({
        socket,
        servername: url.hostname,
        timeout: options.timeout || 5000,
      });

      tlsConn.on('error', (err) => {
        logger.error(`TLS error for ${url.hostname}: ${err.message}`);
        settle(reject, err);
      });
      tlsConn.on('timeout', () => {
        logger.warn(`TLS timeout for ${url.hostname}`);
        tlsConn.destroy();
        settle(reject, new Error('TLS timeout'));
      });

      tlsConn.on('secureConnect', () => {
        logger.info(`TLS handshake complete → ${url.hostname} (+${Date.now() - start}ms)`);
        let reqStr = `${options.method || 'GET'} ${url.pathname}${url.search} HTTP/1.1\r\n`;
        reqStr += `Host: ${url.host}\r\n`;
        reqStr += `Connection: close\r\n`;
        if (options.headers) {
          for (const [k, v] of Object.entries(options.headers)) {
            reqStr += `${k}: ${v}\r\n`;
          }
        }
        reqStr += `\r\n`;
        if (options.body) {
          reqStr += options.body.toString();
        }
        tlsConn.write(reqStr);

        const deadline = setTimeout(() => {
          logger.warn(`Response timeout for ${url.hostname} after ${Date.now() - start}ms`);
          tlsConn.destroy();
          settle(reject, new Error('Response timeout'));
        }, options.timeout || 5000);

        let response = Buffer.alloc(0);
        tlsConn.on('data', (chunk) => {
          response = Buffer.concat([response, chunk]);
        });
        tlsConn.on('end', () => {
          clearTimeout(deadline);
          const elapsed = Date.now() - start;
          logger.info(`Response complete from ${url.hostname}: ${response.length} bytes in ${elapsed}ms`);
          settle(resolve, {
            bytes: response.length,
            elapsed,
            status: 200,
            headers: {},
            error: null,
          });
        });
      });
    });
    connectReq.end();
  });
}

// ─── MEASUREMENT FUNCTIONS ──────────────────────────────────────────────────
async function measureDownload() {
  const t = Date.now();
  try {
    const res = await makeProxyRequest(CONFIG.downloadUrl, { timeout: CONFIG.downloadTimeoutMs });
    if (res.error) throw res.error;
    const mbps = (res.bytes * 8) / (res.elapsed / 1000) / 1_000_000;
    logger.info(`download OK: ${mbps.toFixed(2)} Mbps (${res.bytes} bytes in ${res.elapsed}ms)`);
    return { timestamp: now(), mbps, error: null };
  } catch (err) {
    logger.error(`download FAIL (${Date.now() - t}ms): ${err.message}`);
    return { timestamp: now(), mbps: null, error: err.message };
  }
}

async function measureUpload() {
  const data = require('crypto').randomBytes(CONFIG.uploadBytes);
  const t = Date.now();
  try {
    const res = await makeProxyRequest(CONFIG.uploadUrl, {
      method: 'POST',
      body: data,
      headers: {
        'Content-Type': 'application/octet-stream',
        'Content-Length': data.length,
      },
    });
    if (res.error) throw res.error;
    const mbps = (CONFIG.uploadBytes * 8) / (res.elapsed / 1000) / 1_000_000;
    logger.info(`upload OK: ${mbps.toFixed(2)} Mbps (${CONFIG.uploadBytes} bytes in ${res.elapsed}ms)`);
    return { timestamp: now(), mbps, error: null };
  } catch (err) {
    logger.error(`upload FAIL (${Date.now() - t}ms): ${err.message}`);
    return { timestamp: now(), mbps: null, error: err.message };
  }
}

async function measureLatency() {
  const t = Date.now();
  try {
    const start = Date.now();
    const res = await makeProxyRequest(CONFIG.pingUrl, { method: 'HEAD' });
    if (res.error) throw res.error;
    const elapsed = Date.now() - start;
    logger.info(`latency OK: ${elapsed}ms`);
    return { timestamp: now(), ms: elapsed, error: null };
  } catch (err) {
    logger.error(`latency FAIL (${Date.now() - t}ms): ${err.message}`);
    return { timestamp: now(), ms: null, error: err.message };
  }
}

// ─── CLASSIFICATION ───────────────────────────────────────────────────────
function classifyMeasurement(download, upload, latency) {
  if (download.error || upload.error || latency.error) {
    return 'ERROR';
  }
  const d = download.mbps;
  const l = latency.ms;

  if (d < 1 || l > 500) return 'SEVERE';
  if (d < 5 || l > 200) return 'POOR';
  if (d < 10 || l > 100) return 'SLOW';
  return 'NORMAL';
}

// ─── IPC HANDLERS ───────────────────────────────────────────────────────────
ipcMain.handle('get-config', async () => {
  logger.info(`get-config → control port ${CONFIG.controlPort}`);
  return new Promise((resolve, reject) => {
    const req = http.get(`http://127.0.0.1:${CONFIG.controlPort}/status`, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try {
          const parsed = JSON.parse(data);
          logger.info('get-config OK', { bandwidth_bps: parsed.bandwidth_bps, latency_ms: parsed.latency_ms, loss_pct: parsed.loss_pct });
          resolve(parsed);
        } catch (e) {
          logger.error(`get-config parse error: ${e.message}`);
          reject(e);
        }
      });
    });
    req.on('error', (err) => {
      logger.error(`get-config FAIL: ${err.message} (is NetShape running on port ${CONFIG.controlPort}?)`);
      reject(err);
    });
    req.setTimeout(5000, () => {
      req.destroy();
      logger.error(`get-config timeout after 5s`);
      reject(new Error('Control port timeout'));
    });
  });
});

ipcMain.handle('set-config', async (event, payload) => {
  logger.info('set-config →', payload);
  return new Promise((resolve, reject) => {
    const postData = JSON.stringify(payload);
    const req = http.request({
      hostname: '127.0.0.1',
      port: CONFIG.controlPort,
      path: '/configure',
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(postData),
      },
      timeout: 5000,
    }, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try {
          const parsed = JSON.parse(data);
          logger.info('set-config OK', parsed);
          resolve(parsed);
        } catch (e) {
          logger.warn(`set-config response not JSON: ${data}`);
          resolve(data);
        }
      });
    });
    req.on('error', (err) => {
      logger.error(`set-config FAIL: ${err.message}`);
      reject(err);
    });
    req.write(postData);
    req.end();
  });
});

// ─── MEASUREMENT LOOP ─────────────────────────────────────────────────────
let cycleCount = 0;
async function runMeasurementCycle() {
  cycleCount++;
  logger.info(`── cycle #${cycleCount} start ──`);
  const [download, upload, latency] = await Promise.all([
    measureDownload(),
    measureUpload(),
    measureLatency(),
  ]);

  const classification = classifyMeasurement(download, upload, latency);
  logger.info(`── cycle #${cycleCount} result: ${classification} | DL=${download.mbps != null ? download.mbps.toFixed(2)+'Mbps' : 'ERR'} UL=${upload.mbps != null ? upload.mbps.toFixed(2)+'Mbps' : 'ERR'} LAT=${latency.ms != null ? latency.ms+'ms' : 'ERR'} ──`);

  if (classification === 'ERROR') {
    consecutiveErrors++;
    logger.warn(`consecutive errors: ${consecutiveErrors}`);
  } else {
    consecutiveErrors = 0;
  }

  // rolling loss: fraction of failed measurement values in last N cycles
  const recentCycles = 10;
  const failedCount = [
    download, upload, latency
  ].filter(m => !!m.error).length;
  history.loss = history.loss || [];
  history.loss.push(failedCount > 0);
  if (history.loss.length > recentCycles) history.loss.shift();
  const lossRate = history.loss.length
    ? history.loss.filter(Boolean).length / history.loss.length
    : 0;

  history.download.push(download);
  history.upload.push(upload);
  history.latency.push(latency);

  if (history.download.length > CONFIG.historyPoints) {
    history.download.shift();
    history.upload.shift();
    history.latency.shift();
  }

  if (mainWindow) {
    mainWindow.webContents.send('measurement', {
      download,
      upload,
      latency,
      classification,
      connected: consecutiveErrors < 3,
      lossRate,
    });
  }
}

function createWindow() {
  logger.info(`NetShape Test App starting — proxy ${CONFIG.proxyHost}:${CONFIG.proxyPort} | control :${CONFIG.controlPort} | interval ${CONFIG.measureIntervalMs}ms`);
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      preload: path.join(__dirname, 'preload.js'),
    },
  });

  mainWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'));
  mainWindow.webContents.openDevTools({ mode: 'detach' });

  // Start measurement loop
  measureInterval = setInterval(runMeasurementCycle, CONFIG.measureIntervalMs);
  runMeasurementCycle();

  mainWindow.on('closed', () => {
    mainWindow = null;
    if (measureInterval) {
      clearInterval(measureInterval);
      measureInterval = null;
    }
  });
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  if (measureInterval) {
    clearInterval(measureInterval);
    measureInterval = null;
  }
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});
