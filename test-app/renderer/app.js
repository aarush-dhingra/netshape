/* ─── app.js – Network Traffic Lab Renderer ───────────────────────────────── */

(function () {
  'use strict';

  // ─── HELPERS ───────────────────────────────────────────────────────────────
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  const fmtNum = (n, d = 2) => (n != null ? Number(n).toFixed(d) : '--');
  const fmtBytes = (n) => {
    if (n == null) return '--';
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / (1024 * 1024)).toFixed(2)} MB`;
  };
  const fmtDuration = (ms) => (ms != null ? `${Math.round(ms)} ms` : '--');
  const fmtThroughput = (bps) => {
    if (bps == null) return '--';
    if (bps >= 1_000_000) return `${(bps / 1_000_000).toFixed(2)} Mbps`;
    if (bps >= 1_000) return `${(bps / 1_000).toFixed(2)} Kbps`;
    return `${Math.round(bps)} bps`;
  };
  const statusBadge = (ok) => {
    const cls = ok ? 'badge-pass' : 'badge-fail';
    const txt = ok ? 'PASS' : 'FAIL';
    return `<span class="badge ${cls}">${txt}</span>`;
  };

  // ─── RESULT RENDERERS ─────────────────────────────────────────────────────

  function renderLatency(card, data) {
    const { results, metrics } = data;
    const durations = results.map((r) => r.duration_ms);
    const chartId = `chart-${card.dataset.suite}`;
    const maxMs = Math.max(...durations, 100);

    const bars = results
      .map(
        (r, i) =>
          `<div class="latency-bar" style="height:${(r.duration_ms / maxMs) * 100}%;" title="Request ${i + 1}: ${fmtDuration(r.duration_ms)}"></div>`
      )
      .join('');

    card.querySelector('.card-body').innerHTML = `
      <div class="metrics-row">
        <div class="metric-chip">Min <strong>${fmtDuration(metrics.min)}</strong></div>
        <div class="metric-chip">Avg <strong>${fmtDuration(metrics.avg)}</strong></div>
        <div class="metric-chip">Max <strong>${fmtDuration(metrics.max)}</strong></div>
        <div class="metric-chip">P95 <strong>${fmtDuration(metrics.p95)}</strong></div>
      </div>
      <div class="chart-wrapper">
        <div class="bar-chart" id="${chartId}">${bars}</div>
      </div>
      ${results
        .map(
          (r, i) =>
            `<div class="result-row"><span>#${i + 1}</span><span>${fmtDuration(r.duration_ms)}</span>${statusBadge(r.status === 200)}</div>`
        )
        .join('')}
    `;
  }

  function renderDownload(card, data) {
    const { results } = data;
    const speeds = results.map((r) => r.throughput_bps);
    const maxSpeed = Math.max(...speeds, 1);

    card.querySelector('.card-body').innerHTML = results
      .map(
        (r) => `
      <div class="result-row throughput-row">
        <span>${r.label}</span>
        <div class="throughput-bar-bg">
          <div class="throughput-bar" style="width:${(r.throughput_bps / maxSpeed) * 100}%;background:hsl(${(r.throughput_bps / maxSpeed) * 120},70%,50%);"></div>
        </div>
        <span>${fmtThroughput(r.throughput_bps)}</span>
        ${statusBadge(r.status === 200)}
      </div>
    `
      )
      .join('');
  }

  function renderUpload(card, data) {
    const { results } = data;
    const speeds = results.map((r) => r.throughput_bps);
    const maxSpeed = Math.max(...speeds, 1);

    card.querySelector('.card-body').innerHTML = results
      .map(
        (r) => `
      <div class="result-row throughput-row">
        <span>${r.label}</span>
        <div class="throughput-bar-bg">
          <div class="throughput-bar" style="width:${(r.throughput_bps / maxSpeed) * 100}%;background:hsl(${(r.throughput_bps / maxSpeed) * 120},70%,50%);"></div>
        </div>
        <span>${fmtThroughput(r.throughput_bps)}</span>
        ${statusBadge(r.status === 200)}
      </div>
    `
      )
      .join('');
  }

  function renderApiChain(card, data) {
    const { results } = data;
    const rows = results
      .map(
        (r, i) => `
      <div class="result-row">
        <span class="url-short">${shortUrl(r.url, 40)}</span>
        <span class="badge ${r.status === 200 ? 'badge-pass' : 'badge-fail'}">${r.status ?? 'Err'}</span>
        <span>${fmtDuration(r.duration_ms)}</span>
        <span>∑ ${fmtDuration(r.cumulative)}</span>
      </div>
    `
      )
      .join('');

    card.querySelector('.card-body').innerHTML = `
      <div class="result-rows">${rows}</div>
      <div class="result-row total-row"><strong>Total</strong><span></span><span></span><span>${fmtDuration(results[results.length - 1]?.cumulative ?? 0)}</span></div>
    `;
  }

  function renderStream(card, data) {
    const r = data.result;
    card.querySelector('.card-body').innerHTML = `
      <div class="metrics-row">
        <div class="metric-chip">TTFB <strong>${fmtDuration(r.ttfb_ms)}</strong></div>
        <div class="metric-chip">Total <strong>${fmtDuration(r.duration_ms)}</strong></div>
        <div class="metric-chip">Bytes/s <strong>${fmtThroughput(r.throughput_bps)}</strong></div>
        <div class="metric-chip">Chunks <strong>${r.chunks}</strong></div>
      </div>
      <div class="result-row"><strong>Status</strong><span>${r.status ?? 'Err'}</span>${statusBadge(r.status === 200)}</div>
    `;
  }

  function renderLlm(card, data) {
    if (data.error) {
      card.querySelector('.card-body').innerHTML = `<div class="result-placeholder dim">${data.error}</div>`;
      return;
    }
    const r = data.result;
    card.querySelector('.card-body').innerHTML = `
      <div class="metrics-row">
        <div class="metric-chip">TTFB <strong>${fmtDuration(r.ttfb_ms)}</strong></div>
        <div class="metric-chip">Total <strong>${fmtDuration(r.duration_ms)}</strong></div>
        <div class="metric-chip">Chunks <strong>${r.chunks}</strong></div>
        <div class="metric-chip">Size <strong>${fmtBytes(r.bytes)}</strong></div>
      </div>
      <div class="result-row">${statusBadge(!r.error)}</div>
      <pre class="llm-text">${r.text || ''}</pre>
    `;
  }

  function renderBurst(card, data) {
    const { results, metrics } = data;
    const maxMs = Math.max(...results.map((r) => r.duration_ms || 0), 100);
    const rows = results
      .map(
        (r, i) => `
      <div class="gantt-row">
        <span class="gantt-label">#${i + 1}</span>
        <div class="gantt-track"><div class="gantt-bar" style="margin-left:${((r.ttfb_ms || 0) / maxMs) * 100}%;width:${(((r.duration_ms || 0) - (r.ttfb_ms || 0)) / maxMs) * 100}%;background:${r.error ? '#e57373' : '#81c784'};"></div></div>
        <span class="gantt-val">${fmtDuration(r.duration_ms)}</span>
      </div>
    `
      )
      .join('');

    card.querySelector('.card-body').innerHTML = `
      <div class="metrics-row">
        <div class="metric-chip">Fastest <strong>${fmtDuration(metrics.fastest)}</strong></div>
        <div class="metric-chip">Slowest <strong>${fmtDuration(metrics.slowest)}</strong></div>
        <div class="metric-chip">Median <strong>${fmtDuration(metrics.median)}</strong></div>
        <div class="metric-chip">Completed <strong>${metrics.completed}/${results.length}</strong></div>
      </div>
      <div class="gantt-chart">${rows}</div>
    `;
  }

  // ─── RUNNING TESTS ─────────────────────────────────────────────────────────

  async function runTest(type, card) {
    const body = card.querySelector('.card-body');
    body.innerHTML = '<div class="spinner"></div> Running…';

    try {
      let data;
      switch (type) {
        case 'latency':
          data = await window.netshape.runTest('latency');
          renderLatency(card, data);
          break;
        case 'download':
          data = await window.netshape.runTest('download');
          renderDownload(card, data);
          break;
        case 'upload':
          data = await window.netshape.runTest('upload');
          renderUpload(card, data);
          break;
        case 'api-chain':
          data = await window.netshape.runTest('api-chain');
          renderApiChain(card, data);
          break;
        case 'stream':
          data = await window.netshape.runTest('stream');
          renderStream(card, data);
          break;
        case 'llm': {
          const key = document.getElementById('openai-key')?.value || '';
          // Show a live text box immediately so tokens stream in before the final result.
          body.innerHTML = `
            <div class="spinner"></div> Waiting for first token…
            <pre class="llm-text llm-text-live" style="margin-top:8px"></pre>`;
          data = await window.netshape.runTest('llm-stream', key);
          renderLlm(card, data);
          break;
        }
        case 'burst': {
          const count = parseInt(document.getElementById('burst-count').value, 10);
          data = await window.netshape.runTest('burst', count);
          renderBurst(card, data);
          break;
        }
      }
      // Update session summary after every test
      updateSummary();
    } catch (err) {
      body.innerHTML = `<div class="error-msg">Error: ${err.message || err}</div>`;
    }
  }

  function clearTest(type, card) {
    const body = card.querySelector('.card-body');
    // For the LLM card, preserve the API key input that lives inside card-body.
    if (type === 'llm') {
      const key = document.getElementById('openai-key')?.value || '';
      body.innerHTML = `
        <div class="api-key-row">
          <input type="password" id="openai-key"
                 placeholder="OpenAI API key (optional, also env OPENAI_API_KEY)"
                 value="${key.replace(/"/g, '&quot;')}">
        </div>
        <div class="result-placeholder">Not yet run</div>`;
    } else {
      body.innerHTML = '<div class="result-placeholder">Not yet run</div>';
    }
  }

  async function updateSummary() {
    try {
      const stats = await window.netshape.getSessionStats();
      const el = (id) => document.getElementById(id);
      if (el('sum-requests')) el('sum-requests').textContent = stats.totalRequests;
      if (el('sum-bytes'))    el('sum-bytes').textContent    = fmtBytes(stats.totalBytes);
      if (el('sum-latency'))  el('sum-latency').textContent  =
        stats.avgLatency ? `${stats.avgLatency} ms` : '--';
    } catch { /* proxy may not be running */ }
  }

  // ─── PROXY STATUS POLLING ─────────────────────────────────────────────────

  async function pollProxyStatus() {
    try {
      const status = await window.netshape.getProxyStatus();
      const dot = document.getElementById('proxy-dot');
      const state = document.getElementById('proxy-state');
      if (status.ok) {
        dot.classList.add('connected');
        state.textContent = 'Connected';
        document.getElementById('proxy-profile').textContent = status.profile || 'custom';
        document.getElementById('proxy-bandwidth').textContent = status.bandwidth_bps ? fmtThroughput(status.bandwidth_bps) : 'unlimited';
        document.getElementById('proxy-latency').textContent = status.latency_ms != null ? `${status.latency_ms} ms` : '--';
        document.getElementById('proxy-loss').textContent = status.loss_pct != null ? `${Math.round(status.loss_pct * 1000) / 10}%` : '--';
      } else {
        dot.classList.remove('connected');
        state.textContent = 'Disconnected';
      }
    } catch {
      const dot = document.getElementById('proxy-dot');
      dot.classList.remove('connected');
      document.getElementById('proxy-state').textContent = 'Error';
    }
  }

  // ─── ACTIVITY LOG ─────────────────────────────────────────────────────────

  function appendLog(level, msg, ts) {
    const list = document.getElementById('activity-log');
    if (!list) return;
    const time = ts ? new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '';
    const item = document.createElement('div');
    item.className = `log-line log-${level}`;
    item.innerHTML = `<span class="log-ts">${time}</span><span class="log-msg">${msg}</span>`;
    list.appendChild(item);
    list.scrollTop = list.scrollHeight;
    // Trim to last 200 lines so it never gets unwieldy.
    while (list.children.length > 200) list.removeChild(list.firstChild);
  }

  // ─── LIVE STREAMING EVENTS ────────────────────────────────────────────────

  // Handle push events from the main process (log lines, llm tokens, etc.)
  window.netshape.onResult(({ type, data }) => {
    if (type === 'log') {
      appendLog(data.level, data.msg, data.ts);
    } else if (type === 'llm-token') {
      // Append the token to the live LLM text box if it exists.
      let pre = document.querySelector('.llm-text-live');
      if (!pre) {
        const card = document.querySelector('.test-card[data-suite="llm"] .card-body');
        if (card) {
          pre = document.createElement('pre');
          pre.className = 'llm-text llm-text-live';
          card.appendChild(pre);
        }
      }
      if (pre) {
        pre.textContent += data.token;
        pre.scrollTop = pre.scrollHeight;
      }
    }
  });

  // ─── EVENT BINDING ─────────────────────────────────────────────────────────

  document.addEventListener('DOMContentLoaded', () => {
    // Run / Clear buttons
    document.querySelectorAll('.run-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const suite = btn.dataset.run;
        const card = document.querySelector(`.test-card[data-suite="${suite}"]`);
        if (card) runTest(suite, card);
      });
    });

    document.querySelectorAll('.clear-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const suite = btn.dataset.clear;
        const card = document.querySelector(`.test-card[data-suite="${suite}"]`);
        if (card) clearTest(suite, card);
      });
    });

    // Run All
    document.getElementById('run-all').addEventListener('click', async () => {
      const suites = ['latency', 'download', 'upload', 'api-chain', 'stream', 'llm', 'burst'];
      for (const suite of suites) {
        const card = document.querySelector(`.test-card[data-suite="${suite}"]`);
        if (card) await runTest(suite, card);
      }
    });

    // Clear log button
    const clearLogBtn = document.getElementById('clear-log');
    if (clearLogBtn) {
      clearLogBtn.addEventListener('click', () => {
        const list = document.getElementById('activity-log');
        if (list) list.innerHTML = '';
      });
    }

    // Proxy status poll
    pollProxyStatus();
    setInterval(pollProxyStatus, 2000);
  });

  // ─── UTILS ─────────────────────────────────────────────────────────────────
  function shortUrl(url, max) {
    if (url.length <= max) return url;
    return url.slice(0, max - 3) + '…';
  }
})();
