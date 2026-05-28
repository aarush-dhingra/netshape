const http = require('http');
const https = require('https');
const { URL } = require('url');
const { HttpsProxyAgent } = require('https-proxy-agent');

/**
 * A wrapper around Node's http/https modules that:
 *  - Records high-resolution timing (TTFB, total duration, throughput).
 *  - Respects the standard HTTP_PROXY / HTTPS_PROXY environment variables
 *    by automatically using https-proxy-agent when those vars are set.
 */
class TimedRequest {
  /**
   * @param {string} url  – Full URL to request.
   * @param {object} [options={}] – Extra options.
   * @param {string} [options.method='GET']
   * @param {object} [options.headers]
   * @param {Buffer|string} [options.body]
   * @param {number} [options.timeout=30000]
   * @param {boolean} [options.useProxy=true]  – Set false to bypass proxy.
   */
  constructor(url, options = {}) {
    this.url = url;
    this.method = options.method || 'GET';
    this.headers = options.headers || {};
    this.body = options.body || null;
    this.timeout = options.timeout || 30000;
    this.useProxy = options.useProxy !== false;

    this.parsed = new URL(url);
    this.isHttps = this.parsed.protocol === 'https:';
  }

  /**
   * Execute the request, returning timing & metadata.
   * @returns {Promise<{
   *    url: string,
   *    status: number|null,
   *    ttfb_ms: number|null,
   *    duration_ms: number|null,
   *    bytes: number,
   *    throughput_bps: number|null,
   *    error: string|null,
   *    chunks: number
   * }>}
   */
  async run() {
    return new Promise((resolve) => {
      const startTime = performance.now();
      let ttfb = null;
      let status = null;
      const chunks = [];
      let chunkCount = 0;
      let resolved = false;

      const requestOptions = this._buildRequestOptions();

      const client = this.isHttps ? https : http;
      const req = client.request(requestOptions, (res) => {
        status = res.statusCode;

        res.on('data', (chunk) => {
          if (ttfb === null) {
            ttfb = performance.now() - startTime;
          }
          chunks.push(chunk);
          chunkCount++;
        });

        res.on('end', () => {
          if (resolved) return;
          resolved = true;
          const endTime = performance.now();
          const duration = endTime - startTime;
          const bytes = Buffer.concat(chunks).length;
          const throughput = duration > 0 ? (bytes * 8) / (duration / 1000) : 0;

          resolve({
            url: this.url,
            status,
            ttfb_ms: ttfb !== null ? Math.round(ttfb * 100) / 100 : null,
            duration_ms: Math.round(duration * 100) / 100,
            bytes,
            throughput_bps: Math.round(throughput * 100) / 100,
            error: null,
            chunks: chunkCount,
          });
        });

        res.on('error', (err) => {
          if (resolved) return;
          resolved = true;
          resolve(this._errorResponse(startTime, status, err.message));
        });
      });

      req.on('error', (err) => {
        if (resolved) return;
        resolved = true;
        resolve(this._errorResponse(startTime, status, err.message));
      });

      req.on('timeout', () => {
        if (resolved) return;
        resolved = true;
        req.destroy();
        resolve(this._errorResponse(startTime, status, 'Request timeout'));
      });

      req.setTimeout(this.timeout);

      if (this.body) {
        req.write(this.body);
      }

      req.end();
    });
  }

  _buildRequestOptions() {
    const options = {
      hostname: this.parsed.hostname,
      port: this.parsed.port || (this.isHttps ? 443 : 80),
      path: this.parsed.pathname + this.parsed.search,
      method: this.method,
      headers: this.headers,
      timeout: this.timeout,
    };

    // Only use proxy if explicitly enabled and env vars are present.
    if (this.useProxy) {
      const proxyUrl = process.env.HTTPS_PROXY || process.env.HTTP_PROXY;
      if (proxyUrl) {
        options.agent = new HttpsProxyAgent(proxyUrl);
      }
    }

    return options;
  }

  _errorResponse(startTime, status, message) {
    const duration = performance.now() - startTime;
    return {
      url: this.url,
      status,
      ttfb_ms: null,
      duration_ms: Math.round(duration * 100) / 100,
      bytes: 0,
      throughput_bps: 0,
      error: message,
      chunks: 0,
    };
  }
}

module.exports = { TimedRequest };
