// ─── CHART SETUP ───────────────────────────────────────────────────────────
const historyPoints = 60;

const throughputCtx = document.getElementById('throughput-chart').getContext('2d');
const latencyCtx = document.getElementById('latency-chart').getContext('2d');

const labels = Array.from({ length: historyPoints }, (_, i) => {
  return `${(historyPoints - i) * 2.5}s`;
});

const throughputChart = new Chart(throughputCtx, {
  type: 'line',
  data: {
    labels: [...labels],
    datasets: [
      {
        label: 'Download',
        data: Array(historyPoints).fill(null),
        borderColor: '#4fc3f7',
        backgroundColor: 'rgba(79, 195, 247, 0.1)',
        pointRadius: 0,
        tension: 0.4,
        fill: true,
      },
      {
        label: 'Upload',
        data: Array(historyPoints).fill(null),
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

const latencyChart = new Chart(latencyCtx, {
  type: 'line',
  data: {
    labels: [...labels],
    datasets: [
      {
        label: 'RTT',
        data: Array(historyPoints).fill(null),
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
  toast: document.getElementById('toast'),
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
  els.bwValue.textContent = `${els.bwSlider.value} Mbps`;
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

function applyProfileChange() {
  const profileName = els.profileSelect.value;
  setSlidersFromProfile(profileName);
}

function showToast(message) {
  els.toast.textContent = message;
  els.toast.classList.add('visible');
  setTimeout(() => els.toast.classList.remove('visible'), 3000);
}

// ─── EVENT LISTENERS ──────────────────────────────────────────────────────
els.profileSelect.addEventListener('change', applyProfileChange);

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
    const result = await window.netshape.setConfig(payload);
    updateConfigDisplay(result);
    showToast('Configuration applied');
  } catch (err) {
    showToast(`Error: ${err.message || 'Failed to apply config'}`);
  }
});

// ─── UPDATE UI ────────────────────────────────────────────────────────────
function updateConfigDisplay(config) {
  els.configDisplay.textContent = JSON.stringify(config, null, 2);
}

function updateStatus(data) {
  const { classification, connected } = data;

  els.statusBanner.className = 'status-banner';
  if (classification === 'NORMAL') els.statusBanner.classList.add('normal');
  else if (classification === 'SLOW') els.statusBanner.classList.add('slow');
  else if (classification === 'POOR') els.statusBanner.classList.add('poor');
  else if (classification === 'SEVERE') els.statusBanner.classList.add('severe');
  else els.statusBanner.classList.add('error');

  const fmt = (n) => n !== null && n !== undefined ? n.toFixed(1) : '--';
  const dl = data.download && data.download.mbps;
  const ul = data.upload && data.upload.mbps;
  const lat = data.latency && data.latency.ms;
  const lossRate = data.lossRate ?? 0;

  const emojiMap = { NORMAL: '🟢', SLOW: '🟡', POOR: '🟠', SEVERE: '🔴', ERROR: '❌' };
  const emoji = emojiMap[classification] || '⚪';

  els.statusBanner.textContent = `${emoji} ${classification || 'UNKNOWN'} — DL ${fmt(dl)} Mbps, UL ${fmt(ul)} Mbps, ${fmt(lat)} ms`;
  els.dlValue.textContent = fmt(dl);
  els.ulValue.textContent = fmt(ul);
  els.latValue.textContent = fmt(lat);
  els.lossValue.textContent = `${(lossRate * 100).toFixed(0)}%`;

  els.proxyStatusDot.className = `status-dot ${connected ? 'connected' : ''}`;
  els.proxyStatusText.textContent = connected ? 'Connected (port 8090)' : 'Disconnected';
}

function updateCharts(data) {
  const dl = data.download && data.download.mbps !== null ? data.download.mbps : null;
  const ul = data.upload && data.upload.mbps !== null ? data.upload.mbps : null;
  const lat = data.latency && data.latency.ms !== null ? data.latency.ms : null;

  throughputChart.data.datasets[0].data.push(dl);
  throughputChart.data.datasets[0].data.shift();
  throughputChart.data.datasets[1].data.push(ul);
  throughputChart.data.datasets[1].data.shift();
  throughputChart.update('none');

  latencyChart.data.datasets[0].data.push(lat);
  latencyChart.data.datasets[0].data.shift();
  latencyChart.update('none');
}

// ─── IPC LISTENER ──────────────────────────────────────────────────────────
window.netshape.onMeasurement((data) => {
  updateStatus(data);
  updateCharts(data);
});

// ─── INIT ─────────────────────────────────────────────────────────────────
(async function init() {
  updateSliderLabels();
  try {
    const config = await window.netshape.getConfig();
    updateConfigDisplay(config);
    // Pre-fill sliders from current config
    if (config.bandwidth_bps !== undefined) {
      els.bwSlider.value = Math.round(config.bandwidth_bps / 1_000_000);
      els.latSlider.value = config.latency_ms || 0;
      els.lossSlider.value = (config.loss_pct || 0) * 100;
      els.jitterSlider.value = config.jitter_ms || 0;
      updateSliderLabels();
    }
  } catch (err) {
    showToast(`Error: ${err.message || 'Failed to fetch config'}`);
    els.configDisplay.textContent = 'Error loading config';
  }
})();
