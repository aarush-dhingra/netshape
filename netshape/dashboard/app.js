// ─── CONFIG ──────────────────────────────────────────────────────────────
const HISTORY_POINTS = 60;
const UPDATE_INTERVAL_MS = 1000;

// ─── CHART SETUP ─────────────────────────────────────────────────────────
const throughputCtx = document.getElementById('throughput-chart')?.getContext('2d');
const latencyCtx = document.getElementById('latency-chart')?.getContext('2d');

const labels = Array.from({ length: HISTORY_POINTS }, (_, i) => {
  return `${(HISTORY_POINTS - i) * (UPDATE_INTERVAL_MS / 1000)}s`;
});

let throughputChart, latencyChart;

if (throughputCtx) {
  throughputChart = new Chart(throughputCtx, {
    type: 'line',
    data: {
      labels: [...labels],
      datasets: [
        {
          label: 'Download',
          data: Array(HISTORY_POINTS).fill(null),
          borderColor: '#4fc3f7',
          backgroundColor: 'rgba(79, 195, 247, 0.1)',
          pointRadius: 0,
          tension: 0.4,
          fill: true,
        },
        {
          label: 'Upload',
          data: Array(HISTORY_POINTS).fill(null),
          borderColor: '#81c784',
          backgroundColor: 'rgba(129, 199, 132, 0.1)',
          pointRadius: 0,
          tension: 0.4,
          fill: true,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      scales: {
        y: {
          beginAtZero: true,
          title: { display: true, text: 'Mbps' },
          grid: { color: 'rgba(255,255,255,0.05)' },
          ticks: { color: '#a0a0a0' },
        },
        x: {
          grid: { color: 'rgba(255,255,255,0.05)' },
          ticks: { color: '#a0a0a0', maxTicksLimit: 6 },
        },
      },
      plugins: {
        legend: {
          labels: { color: '#e0e0e0' },
        },
      },
    },
  });
}

if (latencyCtx) {
  latencyChart = new Chart(latencyCtx, {
    type: 'line',
    data: {
      labels: [...labels],
      datasets: [
        {
          label: 'RTT',
          data: Array(HISTORY_POINTS).fill(null),
          borderColor: '#ffb74d',
          backgroundColor: 'rgba(255, 183, 77, 0.1)',
          pointRadius: 0,
          tension: 0.4,
          fill: true,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      scales: {
        y: {
          beginAtZero: true,
          title: { display: true, text: 'ms' },
          grid: { color: 'rgba(255,255,255,0.05)' },
          ticks: { color: '#a0a0a0' },
        },
        x: {
          grid: { color: 'rgba(255,255,255,0.05)' },
          ticks: { color: '#a0a0a0', maxTicksLimit: 6 },
        },
      },
      plugins: {
        legend: {
          labels: { color: '#e0e0e0' },
        },
      },
    },
  });
}

// ─── UI STATE ──────────────────────────────────────────────────────────────
const els = {
  statusBanner: document.getElementById('status-banner'),
  dlValue: document.getElementById('dl-value'),
  ulValue: document.getElementById('ul-value'),
  latValue: document.getElementById('lat-value'),
  lossValue: document.getElementById('loss-value'),
  profileSelect: document.getElementById('profile-select'),
  bwSlider: document.getElementById('bw-slider'),
  bwValue: document.getElementById('bw-value'),
  latSlider: document.getElementById('lat-slider'),
  latValueControl: document.getElementById('lat-value-control'),
  lossSlider: document.getElementById('loss-slider'),
  lossValueControl: document.getElementById('loss-value-control'),
  jitterSlider: document.getElementById('jitter-slider'),
  jitterValueControl: document.getElementById('jitter-value-control'),
  applyBtn: document.getElementById('apply-btn'),
  proxyStatusDot: document.getElementById('proxy-status-dot'),
  proxyStatusText: document.getElementById('proxy-status-text'),
  configDisplay: document.getElementById('config-display'),
};

const profiles = {
  none: { bandwidth_bps: 0, latency_ms: 0, loss_pct: 0, jitter_ms: 0 },
  '3g': { bandwidth_bps: 1500000, latency_ms: 100, loss_pct: 0.01, jitter_ms: 20 },
  edge: { bandwidth_bps: 250000, latency_ms: 300, loss_pct: 0.02, jitter_ms: 50 },
  satellite: { bandwidth_bps: 5000000, latency_ms: 650, loss_pct: 0.005, jitter_ms: 80 },
  dsl: { bandwidth_bps: 5000000, latency_ms: 30, loss_pct: 0.001, jitter_ms: 5 },
};

function bpsToMbps(bps) {
  return (bps / 1_000_000).toFixed(1);
}

function updateSliderLabels() {
  if (els.bwSlider.value === '0') {
    els.bwValue.textContent = 'Unlimited';
  } else {
    els.bwValue.textContent = `${els.bwSlider.value} Mbps`;
  }
  els.latValueControl.textContent = `${els.latSlider.value} ms`;
  els.lossValueControl.textContent = `${els.lossSlider.value}%`;
  els.jitterValueControl.textContent = `${els.jitterSlider.value} ms`;
}

function setSlidersFromProfile(profileName) {
  const p = profiles[profileName];
  if (!p) return;
  els.bwSlider.value = bpsToMbps(p.bandwidth_bps);
  els.latSlider.value = p.latency_ms;
  els.lossSlider.value = (p.loss_pct * 100).toFixed(1);
  els.jitterSlider.value = p.jitter_ms;
  updateSliderLabels();
}

// ─── EVENT LISTENERS ───────────────────────────────────────────────────────
els.profileSelect.addEventListener('change', () => {
  setSlidersFromProfile(els.profileSelect.value);
});

[els.bwSlider, els.latSlider, els.lossSlider, els.jitterSlider].forEach(s => {
  s.addEventListener('input', updateSliderLabels);
});

els.applyBtn.addEventListener('click', async () => {
  const payload = {
    bandwidth_bps: parseInt(els.bwSlider.value) * 1_000_000,
    latency_ms: parseInt(els.latSlider.value),
    loss_pct: parseFloat(els.lossSlider.value) / 100,
    jitter_ms: parseInt(els.jitterSlider.value),
  };
  try {
    const res = await fetch('/configure', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    updateConfigDisplay(data);
  } catch (err) {
    console.error('Failed to apply config:', err);
  }
});

// ─── UPDATE UI ────────────────────────────────────────────────────────────
function updateConfigDisplay(config) {
  els.configDisplay.textContent = JSON.stringify(config, null, 2);
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function updateStatus(data) {
  const classification = data.classification || 'OFFLINE';

  els.statusBanner.className = 'status-banner';
  if (classification === 'NORMAL') els.statusBanner.classList.add('normal');
  else if (classification === 'SLOW') els.statusBanner.classList.add('slow');
  else if (classification === 'POOR') els.statusBanner.classList.add('poor');
  else if (classification === 'SEVERE') els.statusBanner.classList.add('severe');
  else els.statusBanner.classList.add('offline');

  const fmt = (n) => n !== null && n !== undefined ? n.toFixed(1) : '--';
  const dl = data.download_mbps;
  const ul = data.upload_mbps;
  const lat = data.latency_ms;
  const loss = data.loss_pct;

  const emojiMap = { NORMAL: '🟢', SLOW: '🟡', POOR: '🟠', SEVERE: '🔴', OFFLINE: '⚪' };
  const emoji = emojiMap[classification] || '⚪';

  els.statusBanner.textContent = `${emoji} ${classification} — DL ${fmt(dl)} Mbps, UL ${fmt(ul)} Mbps, ${fmt(lat)} ms`;
  els.dlValue.textContent = fmt(dl);
  els.ulValue.textContent = fmt(ul);
  els.latValue.textContent = fmt(lat);
  els.lossValue.textContent = `${(loss * 100).toFixed(0)}%`;

  const port = data.traffic_port || 8090;
  els.proxyStatusDot.className = `status-dot ${data.connected ? 'connected' : ''}`;
  els.proxyStatusText.textContent = data.connected ? `Connected (port ${port})` : 'Disconnected';
}

function updateCharts(data) {
  if (!throughputChart || !latencyChart) return;

  const dl = data.download_mbps !== null ? data.download_mbps : null;
  const ul = data.upload_mbps !== null ? data.upload_mbps : null;
  const lat = data.latency_ms !== null ? data.latency_ms : null;

  throughputChart.data.datasets[0].data.push(dl);
  throughputChart.data.datasets[0].data.shift();
  throughputChart.data.datasets[1].data.push(ul);
  throughputChart.data.datasets[1].data.shift();
  throughputChart.update('none');

  latencyChart.data.datasets[0].data.push(lat);
  latencyChart.data.datasets[0].data.shift();
  latencyChart.update('none');
}

// ─── SSE (REAL-TIME UPDATES) ──────────────────────────────────────────────
function startEventSource() {
  const events = new EventSource('/events');

  events.addEventListener('message', (event) => {
    try {
      const data = JSON.parse(event.data);
      updateStatus(data);
      updateCharts(data);
    } catch (err) {
      console.error('Failed to parse SSE data:', err);
    }
  });

  events.addEventListener('error', (err) => {
    console.error('SSE error:', err);
    events.close();
    setTimeout(startEventSource, 3000); // retry after 3s
  });

  events.addEventListener('open', () => {
    console.log('SSE connected');
  });
}

// ─── LOG VIEWER ───────────────────────────────────────────────────────────
const logViewer = document.getElementById('log-viewer');
const logCount = document.getElementById('log-count');

async function refreshLogs() {
  try {
    const res = await fetch('/logs');
    if (!res.ok) return;
    const data = await res.json();
    if (!logViewer || !data.lines) return;

    const lines = data.lines.slice(-80);
    const wasScrolledToBottom =
      logViewer.scrollHeight - logViewer.scrollTop <= logViewer.clientHeight + 20;

    logViewer.innerHTML = lines
      .map(l => `<div class="log-line">${escapeHtml(l)}</div>`)
      .join('');

    if (logCount) logCount.textContent = `${data.lines.length} lines`;

    if (wasScrolledToBottom) {
      logViewer.scrollTop = logViewer.scrollHeight;
    }
  } catch (_) {
    // silent — proxy may not be ready yet
  }
}

setInterval(refreshLogs, 3000);

// ─── INIT ─────────────────────────────────────────────────────────────────
(async function init() {
  updateSliderLabels();

  // Fetch current config
  try {
    const res = await fetch('/status');
    const config = await res.json();
    updateConfigDisplay(config);
    if (config.bandwidth_bps !== undefined) {
      els.bwSlider.value = Math.round(config.bandwidth_bps / 1_000_000);
      els.latSlider.value = config.latency_ms || 0;
      els.lossSlider.value = (config.loss_pct || 0) * 100;
      els.jitterSlider.value = config.jitter_ms || 0;
      updateSliderLabels();
    }
  } catch (err) {
    console.error('Failed to fetch config:', err);
    els.configDisplay.textContent = 'Error loading config';
  }

  // Start SSE and initial log fetch
  startEventSource();
  refreshLogs();
})();
