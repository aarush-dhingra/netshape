// ─── CONFIG ──────────────────────────────────────────────────────────────
const HISTORY_POINTS = 60;
const UPDATE_INTERVAL_MS = 1000;

// ─── CHART SETUP ─────────────────────────────────────────────────────────
const throughputCtx = document.getElementById('throughput-chart')?.getContext('2d');
const latencyCtx = document.getElementById('latency-chart')?.getContext('2d');

const labels = Array.from({ length: HISTORY_POINTS }, (_, i) =>
  `${(HISTORY_POINTS - i) * (UPDATE_INTERVAL_MS / 1000)}s`
);

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
      plugins: { legend: { labels: { color: '#e0e0e0' } } },
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
      plugins: { legend: { labels: { color: '#e0e0e0' } } },
    },
  });
}

// ─── UNIT HELPERS ─────────────────────────────────────────────────────────
/**
 * Convert Mbps to a human-readable {value, unit} pair for metric cards.
 * Input is the raw SSE `download_mbps` / `upload_mbps` value.
 */
function formatBandwidth(mbps) {
  if (mbps === null || mbps === undefined) return { value: '--', unit: 'Mbps' };
  if (mbps >= 1000) return { value: (mbps / 1000).toFixed(2), unit: 'Gbps' };
  if (mbps >= 1.0)  return { value: mbps.toFixed(1), unit: 'Mbps' };
  if (mbps >= 0.001) return { value: (mbps * 1000).toFixed(1), unit: 'kbps' };
  if (mbps > 0) return { value: (mbps * 1_000_000).toFixed(0), unit: 'bps' };
  return { value: '0.0', unit: 'Mbps' };
}

/**
 * Convert a raw bps integer to a human-readable string (for rule lists, etc).
 */
function formatBps(bps) {
  if (!bps) return 'Unlimited';
  if (bps >= 1_000_000) {
    const v = bps / 1_000_000;
    return `${Number.isInteger(v) ? v : v.toFixed(1)} Mbps`;
  }
  if (bps >= 1_000) {
    const v = bps / 1_000;
    return `${Number.isInteger(v) ? v : v.toFixed(1)} kbps`;
  }
  return `${bps} bps`;
}

// ─── UI STATE ──────────────────────────────────────────────────────────────
const els = {
  statusBanner: document.getElementById('status-banner'),
  dlValue: document.getElementById('dl-value'),
  dlUnit: document.getElementById('dl-unit'),
  ulValue: document.getElementById('ul-value'),
  ulUnit: document.getElementById('ul-unit'),
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
  none:      { bandwidth_bps: 0,       latency_ms: 0,   loss_pct: 0,     jitter_ms: 0  },
  '3g':      { bandwidth_bps: 1500000, latency_ms: 100, loss_pct: 0.01,  jitter_ms: 20 },
  edge:      { bandwidth_bps: 250000,  latency_ms: 300, loss_pct: 0.02,  jitter_ms: 50 },
  satellite: { bandwidth_bps: 5000000, latency_ms: 650, loss_pct: 0.005, jitter_ms: 80 },
  dsl:       { bandwidth_bps: 5000000, latency_ms: 30,  loss_pct: 0.001, jitter_ms: 5  },
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

// ─── SLIDER EVENT LISTENERS ───────────────────────────────────────────────
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
  if (els.configDisplay) els.configDisplay.textContent = JSON.stringify(config, null, 2);
}

function updateSlidersFromConfig(config) {
  if (config.bandwidth_bps !== undefined) {
    els.bwSlider.value = Math.round(config.bandwidth_bps / 1_000_000);
  }
  if (config.latency_ms !== undefined) els.latSlider.value = config.latency_ms;
  if (config.loss_pct !== undefined) els.lossSlider.value = (config.loss_pct * 100).toFixed(1);
  if (config.jitter_ms !== undefined) els.jitterSlider.value = config.jitter_ms;
  updateSliderLabels();
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function logLineClass(line) {
  if (line.includes(' ERROR ')) return 'log-line log-error';
  if (line.includes(' WARN  ') || line.includes(' WARNING ')) return 'log-line log-warn';
  if (line.includes(' INFO  ')) return 'log-line log-info';
  if (line.includes(' DEBUG ')) return 'log-line log-debug';
  return 'log-line';
}

function updateStatus(data) {
  const classification = data.classification || 'OFFLINE';
  els.statusBanner.className = 'status-banner';
  if (classification === 'NORMAL') els.statusBanner.classList.add('normal');
  else if (classification === 'SLOW') els.statusBanner.classList.add('slow');
  else if (classification === 'POOR') els.statusBanner.classList.add('poor');
  else if (classification === 'SEVERE') els.statusBanner.classList.add('severe');
  else els.statusBanner.classList.add('offline');

  const dl = data.download_mbps;
  const ul = data.upload_mbps;
  const lat = data.latency_ms;
  const loss = data.loss_pct;

  const dlFmt = dl !== null && dl !== undefined ? formatBandwidth(dl) : { value: '--', unit: 'Mbps' };
  const ulFmt = ul !== null && ul !== undefined ? formatBandwidth(ul) : { value: '--', unit: 'Mbps' };

  els.dlValue.textContent = dlFmt.value;
  if (els.dlUnit) els.dlUnit.textContent = dlFmt.unit;
  els.ulValue.textContent = ulFmt.value;
  if (els.ulUnit) els.ulUnit.textContent = ulFmt.unit;
  els.latValue.textContent = lat !== null && lat !== undefined ? lat.toFixed(1) : '--';
  els.lossValue.textContent = loss !== null && loss !== undefined ? `${(loss * 100).toFixed(0)}%` : '--';

  const emojiMap = { NORMAL: '🟢', SLOW: '🟡', POOR: '🟠', SEVERE: '🔴', OFFLINE: '⚪' };
  const emoji = emojiMap[classification] || '⚪';
  const dlStr = dlFmt.value !== '--' ? `DL ${dlFmt.value} ${dlFmt.unit}` : 'DL --';
  const ulStr = ulFmt.value !== '--' ? `UL ${ulFmt.value} ${ulFmt.unit}` : 'UL --';
  const latStr = lat !== null && lat !== undefined ? `${lat.toFixed(1)} ms` : '--';
  els.statusBanner.textContent = `${emoji} ${classification} — ${dlStr}, ${ulStr}, ${latStr}`;
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

// ─── CONNECTION STATE ─────────────────────────────────────────────────────
let sseConnected = false;

function setConnectionState(connected, port) {
  sseConnected = connected;
  if (els.proxyStatusDot) {
    els.proxyStatusDot.className = `status-dot ${connected ? 'connected' : ''}`;
  }
  if (els.proxyStatusText) {
    els.proxyStatusText.textContent = connected
      ? `Connected (port ${port || 8090})`
      : 'Disconnected';
  }
  if (!connected) {
    els.statusBanner.className = 'status-banner offline';
    els.statusBanner.textContent = '⚪ OFFLINE — waiting for proxy…';
  }
}

// ─── SSE (REAL-TIME UPDATES) ──────────────────────────────────────────────
function startEventSource() {
  const events = new EventSource('/events');

  events.addEventListener('open', () => {
    setConnectionState(true, els._trafficPort || 8090);
  });

  events.addEventListener('message', (event) => {
    try {
      const data = JSON.parse(event.data);
      els._trafficPort = data.traffic_port;
      setConnectionState(true, data.traffic_port);
      updateStatus(data);
      updateCharts(data);
      if (data.scenario) updateScenarioUI(data.scenario);
      if (data.config) {
        updateConfigDisplay(data.config);
        updateSlidersFromConfig(data.config);
      }
    } catch (err) {
      console.error('Failed to parse SSE data:', err);
    }
  });

  events.addEventListener('error', () => {
    setConnectionState(false);
    events.close();
    setTimeout(startEventSource, 3000);
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

    const lines = data.lines.slice(-100);
    const wasAtBottom =
      logViewer.scrollHeight - logViewer.scrollTop <= logViewer.clientHeight + 24;

    logViewer.innerHTML = lines
      .map(l => `<div class="${logLineClass(l)}">${escapeHtml(l)}</div>`)
      .join('');

    if (logCount) logCount.textContent = `${data.lines.length} lines`;
    if (wasAtBottom) logViewer.scrollTop = logViewer.scrollHeight;
  } catch (_) {
    // silent — proxy may not be ready yet
  }
}

setInterval(refreshLogs, 2000);

// ─── SCENARIO ─────────────────────────────────────────────────────────────
const scenarioSelect = document.getElementById('scenario-select');
const runScenarioBtn = document.getElementById('run-scenario-btn');
const stopScenarioBtn = document.getElementById('stop-scenario-btn');
const scenarioIdleControls = document.getElementById('scenario-idle-controls');
const scenarioRunning = document.getElementById('scenario-running');
const scenarioNameDisplay = document.getElementById('scenario-name-display');
const scenarioPhaseDisplay = document.getElementById('scenario-phase-display');
const scenarioProgress = document.getElementById('scenario-progress');
const scenarioTimeDisplay = document.getElementById('scenario-time-display');
const scenarioStatusMsg = document.getElementById('scenario-status-msg');

async function loadBuiltinScenarios() {
  try {
    const res = await fetch('/scenarios');
    if (!res.ok) return;
    const data = await res.json();
    const names = data.scenarios || [];
    names.forEach(name => {
      const opt = document.createElement('option');
      opt.value = name;
      opt.textContent = name.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
      scenarioSelect?.appendChild(opt);
    });
    if (runScenarioBtn) runScenarioBtn.disabled = names.length === 0;
  } catch (_) {}
}

function updateScenarioUI(scenarioData) {
  if (!scenarioIdleControls) return;
  const running = scenarioData?.running;
  scenarioIdleControls.style.display = running ? 'none' : 'block';
  if (scenarioRunning) scenarioRunning.style.display = running ? 'block' : 'none';

  if (scenarioData?.error) {
    if (scenarioStatusMsg) {
      scenarioStatusMsg.style.color = '#f44336';
      scenarioStatusMsg.textContent = `Server error: ${scenarioData.error}`;
    }
  } else if (scenarioStatusMsg && scenarioStatusMsg.style.color === 'rgb(244, 67, 54)') {
    scenarioStatusMsg.style.color = '';
    scenarioStatusMsg.textContent = '';
  }

  if (!running) return;
  if (scenarioNameDisplay) scenarioNameDisplay.textContent = scenarioData.name || '';
  if (scenarioPhaseDisplay) {
    scenarioPhaseDisplay.textContent =
      `Phase ${scenarioData.current_phase}/${scenarioData.total_phases}: ${scenarioData.phase_name || ''}`;
  }
  const duration = scenarioData.phase_duration_s || 0;
  const elapsed = scenarioData.phase_elapsed_s || 0;
  const pct = duration > 0 ? Math.min(100, (elapsed / duration) * 100) : 0;
  if (scenarioProgress) scenarioProgress.value = pct;
  if (scenarioTimeDisplay) {
    scenarioTimeDisplay.textContent = `${elapsed.toFixed(0)}s / ${duration.toFixed(0)}s`;
  }
}

runScenarioBtn?.addEventListener('click', async () => {
  const name = scenarioSelect?.value;
  if (!name) return;
  try {
    const res = await fetch('/scenario/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ builtin: name }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      if (scenarioStatusMsg) scenarioStatusMsg.textContent = `Error: ${data.error || res.status}`;
    } else if (scenarioStatusMsg) {
      scenarioStatusMsg.textContent = `Started: ${name}`;
      setTimeout(() => { scenarioStatusMsg.textContent = ''; }, 3000);
    }
  } catch (err) {
    if (scenarioStatusMsg) scenarioStatusMsg.textContent = `Failed: ${err.message}`;
  }
});

stopScenarioBtn?.addEventListener('click', async () => {
  try {
    const res = await fetch('/scenario/stop', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
    if (scenarioStatusMsg) {
      scenarioStatusMsg.textContent = res.ok ? 'Scenario stopped.' : 'Stop failed.';
      setTimeout(() => { scenarioStatusMsg.textContent = ''; }, 3000);
    }
  } catch (err) {
    if (scenarioStatusMsg) scenarioStatusMsg.textContent = `Failed: ${err.message}`;
  }
});

// ─── CUSTOM SCENARIO BUILDER ──────────────────────────────────────────────
let phaseCount = 0;

function addPhaseRow() {
  phaseCount++;
  const idx = phaseCount;
  const div = document.createElement('div');
  div.className = 'phase-row';
  div.dataset.phaseIdx = idx;
  div.innerHTML = `
    <div class="phase-row-header">
      <span class="phase-label">Phase ${idx}</span>
      <button class="btn-icon remove-phase-btn" title="Remove phase">×</button>
    </div>
    <div class="form-row">
      <div class="form-group half">
        <label class="form-label-sm">Name</label>
        <input type="text" class="text-input phase-name" placeholder="Phase ${idx}" />
      </div>
      <div class="form-group half">
        <label class="form-label-sm">Duration</label>
        <div class="input-with-unit">
          <input type="number" class="text-input phase-duration" min="1" value="10" />
          <span class="unit-label">s</span>
        </div>
      </div>
    </div>
    <div class="form-row">
      <div class="form-group half">
        <label class="form-label-sm">Bandwidth</label>
        <div class="input-with-unit">
          <input type="number" class="text-input phase-bw" min="0" placeholder="unlimited" />
          <span class="unit-label">kbps</span>
        </div>
      </div>
      <div class="form-group half">
        <label class="form-label-sm">Latency</label>
        <div class="input-with-unit">
          <input type="number" class="text-input phase-lat" min="0" placeholder="0" />
          <span class="unit-label">ms</span>
        </div>
      </div>
    </div>
    <div class="form-row">
      <div class="form-group half">
        <label class="form-label-sm">Loss</label>
        <div class="input-with-unit">
          <input type="number" class="text-input phase-loss" min="0" max="100" step="0.1" placeholder="0" />
          <span class="unit-label">%</span>
        </div>
      </div>
      <div class="form-group half">
        <label class="form-label-sm">Jitter</label>
        <div class="input-with-unit">
          <input type="number" class="text-input phase-jitter" min="0" placeholder="0" />
          <span class="unit-label">ms</span>
        </div>
      </div>
    </div>`;
  div.querySelector('.remove-phase-btn').addEventListener('click', () => div.remove());
  document.getElementById('custom-phases-list').appendChild(div);
}

document.getElementById('add-phase-btn')?.addEventListener('click', addPhaseRow);

document.getElementById('run-custom-btn')?.addEventListener('click', async () => {
  const customMsg = document.getElementById('custom-scenario-msg');
  const name = document.getElementById('custom-scenario-name')?.value.trim() || 'Custom Scenario';
  const rows = document.querySelectorAll('#custom-phases-list .phase-row');

  if (rows.length === 0) {
    if (customMsg) { customMsg.style.color = '#f44336'; customMsg.textContent = 'Add at least one phase.'; }
    return;
  }

  const phases = [...rows].map((row, i) => {
    const phaseName = row.querySelector('.phase-name')?.value.trim() || `Phase ${i + 1}`;
    const durSec = parseInt(row.querySelector('.phase-duration')?.value || '10', 10);
    const bwKbps = parseFloat(row.querySelector('.phase-bw')?.value || '0');
    const latMs = parseInt(row.querySelector('.phase-lat')?.value || '0', 10);
    const lossPct = parseFloat(row.querySelector('.phase-loss')?.value || '0');
    const jitterMs = parseInt(row.querySelector('.phase-jitter')?.value || '0', 10);

    const phase = {
      name: phaseName,
      duration_ms: durSec * 1000,
    };
    if (bwKbps > 0) phase.bandwidth_bps = Math.round(bwKbps * 1000);
    if (latMs > 0) phase.latency_ms = latMs;
    if (lossPct > 0) phase.loss_pct = lossPct / 100;
    if (jitterMs > 0) phase.jitter_ms = jitterMs;
    return phase;
  });

  try {
    const res = await fetch('/scenario/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, phases }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      if (customMsg) { customMsg.style.color = '#f44336'; customMsg.textContent = `Error: ${data.error || res.status}`; }
    } else {
      if (customMsg) { customMsg.style.color = '#81c784'; customMsg.textContent = `Started: ${name}`; }
      setTimeout(() => { if (customMsg) customMsg.textContent = ''; }, 4000);
    }
  } catch (err) {
    if (customMsg) { customMsg.style.color = '#f44336'; customMsg.textContent = `Failed: ${err.message}`; }
  }
});

// ─── RULES MANAGEMENT ────────────────────────────────────────────────────
async function refreshRules() {
  try {
    const res = await fetch('/rules');
    if (!res.ok) return;
    const data = await res.json();
    renderRulesList(data.rules || []);
  } catch (_) {}
}

function renderRulesList(rules) {
  const container = document.getElementById('rules-list');
  if (!container) return;

  if (rules.length === 0) {
    container.innerHTML = '<div class="rules-empty">No rules active.</div>';
    return;
  }

  container.innerHTML = rules.map(r => {
    const parts = [];
    if (r.bandwidth_bps != null) parts.push(`bw: ${formatBps(r.bandwidth_bps)}`);
    if (r.latency_ms != null)    parts.push(`lat: ${r.latency_ms}ms`);
    if (r.loss_pct != null)      parts.push(`loss: ${(r.loss_pct * 100).toFixed(1)}%`);
    if (r.jitter_ms != null)     parts.push(`jitter: ${r.jitter_ms}ms`);
    const label = r.comment || r.pattern;
    const detail = parts.length ? parts.join(' · ') : 'no throttle overrides';
    return `
      <div class="rule-row" data-id="${escapeHtml(r.id)}">
        <div class="rule-info">
          <div class="rule-name">${escapeHtml(label)}</div>
          <div class="rule-pattern">${escapeHtml(r.pattern)}</div>
          <div class="rule-detail">${escapeHtml(detail)}</div>
        </div>
        <button class="btn-icon rule-remove-btn" title="Remove rule" data-id="${escapeHtml(r.id)}">×</button>
      </div>`;
  }).join('');

  container.querySelectorAll('.rule-remove-btn').forEach(btn => {
    btn.addEventListener('click', () => removeRule(btn.dataset.id));
  });
}

async function removeRule(ruleId) {
  try {
    const res = await fetch(`/rules/${encodeURIComponent(ruleId)}`, { method: 'DELETE' });
    if (res.ok) {
      await refreshRules();
    } else {
      const data = await res.json().catch(() => ({}));
      showRuleMsg(`Error: ${data.error || res.status}`, true);
    }
  } catch (err) {
    showRuleMsg(`Failed: ${err.message}`, true);
  }
}

function showRuleMsg(text, isError = false) {
  const el = document.getElementById('rule-msg');
  if (!el) return;
  el.style.color = isError ? '#f44336' : '#81c784';
  el.textContent = text;
  setTimeout(() => { el.textContent = ''; }, 4000);
}

// Rule form toggle
document.getElementById('toggle-rule-form-btn')?.addEventListener('click', () => {
  const form = document.getElementById('rule-form');
  if (!form) return;
  const visible = form.style.display !== 'none';
  form.style.display = visible ? 'none' : 'block';
  document.getElementById('toggle-rule-form-btn').textContent = visible ? '+ Add' : '– Close';
});

document.getElementById('cancel-rule-btn')?.addEventListener('click', () => {
  const form = document.getElementById('rule-form');
  if (form) form.style.display = 'none';
  document.getElementById('toggle-rule-form-btn').textContent = '+ Add';
});

document.getElementById('add-rule-submit-btn')?.addEventListener('click', async () => {
  const pattern = document.getElementById('rule-pattern-input')?.value.trim();
  if (!pattern) { showRuleMsg('Pattern is required.', true); return; }

  const bwKbps  = parseFloat(document.getElementById('rule-bw-input')?.value || '0');
  const latMs   = parseInt(document.getElementById('rule-lat-input')?.value || '0', 10);
  const lossPct = parseFloat(document.getElementById('rule-loss-input')?.value || '0');
  const jitterMs = parseInt(document.getElementById('rule-jitter-input')?.value || '0', 10);
  const comment = document.getElementById('rule-comment-input')?.value.trim() || '';

  const payload = { pattern, comment };
  if (bwKbps > 0)   payload.bandwidth_bps = Math.round(bwKbps * 1000);
  if (latMs > 0)    payload.latency_ms = latMs;
  if (lossPct > 0)  payload.loss_pct = lossPct / 100;
  if (jitterMs > 0) payload.jitter_ms = jitterMs;

  try {
    const res = await fetch('/rules', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showRuleMsg(`Error: ${data.error || res.status}`, true);
    } else {
      showRuleMsg('Rule added.');
      // Clear form
      ['rule-pattern-input', 'rule-bw-input', 'rule-lat-input',
       'rule-loss-input', 'rule-jitter-input', 'rule-comment-input'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = '';
      });
      // Collapse form
      const form = document.getElementById('rule-form');
      if (form) form.style.display = 'none';
      document.getElementById('toggle-rule-form-btn').textContent = '+ Add';
      await refreshRules();
    }
  } catch (err) {
    showRuleMsg(`Failed: ${err.message}`, true);
  }
});

// ─── INIT ─────────────────────────────────────────────────────────────────
(async function init() {
  updateSliderLabels();
  loadBuiltinScenarios();
  refreshRules();
  setInterval(refreshRules, 5000);

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

      const lat = config.latency_ms || 0;
      const loss = config.loss_pct || 0;
      els.latValue.textContent = lat.toFixed(1);
      els.lossValue.textContent = `${(loss * 100).toFixed(0)}%`;
      els.dlValue.textContent = '--';
      els.ulValue.textContent = '--';

      els._trafficPort = config.traffic_port;
      els.statusBanner.className = 'status-banner normal';
      els.statusBanner.textContent = '⟳ Connecting…';
    }
  } catch (err) {
    console.error('Failed to fetch config:', err);
    if (els.configDisplay) els.configDisplay.textContent = 'Error loading config';
    els.statusBanner.className = 'status-banner offline';
    els.statusBanner.textContent = '⚪ OFFLINE — proxy not reachable';
  }

  startEventSource();
  refreshLogs();
})();
