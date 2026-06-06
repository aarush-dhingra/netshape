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

const CHART_OPTS = {
  responsive: true,
  maintainAspectRatio: false,
  animation: false,
  scales: {
    y: {
      beginAtZero: true,
      grid:  { color: 'rgba(255,255,255,0.04)', drawBorder: false },
      ticks: { color: '#6b7fa8', font: { size: 10 }, maxTicksLimit: 5 },
      border: { display: false },
    },
    x: {
      grid:  { color: 'rgba(255,255,255,0.04)', drawBorder: false },
      ticks: { color: '#6b7fa8', font: { size: 10 }, maxTicksLimit: 6 },
      border: { display: false },
    },
  },
  plugins: { legend: { display: false } },
};

if (throughputCtx) {
  throughputChart = new Chart(throughputCtx, {
    type: 'line',
    data: {
      labels: [...labels],
      datasets: [
        {
          label: 'Download',
          data: Array(HISTORY_POINTS).fill(null),
          borderColor: '#38bdf8',
          backgroundColor: 'rgba(56, 189, 248, 0.08)',
          borderWidth: 1.5,
          pointRadius: 0,
          tension: 0.4,
          fill: true,
        },
        {
          label: 'Upload',
          data: Array(HISTORY_POINTS).fill(null),
          borderColor: '#34d399',
          backgroundColor: 'rgba(52, 211, 153, 0.08)',
          borderWidth: 1.5,
          pointRadius: 0,
          tension: 0.4,
          fill: true,
        },
      ],
    },
    options: {
      ...CHART_OPTS,
      scales: {
        ...CHART_OPTS.scales,
        y: { ...CHART_OPTS.scales.y, title: { display: true, text: 'Mbps', color: '#6b7fa8', font: { size: 10 } } },
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
          borderColor: '#fb923c',
          backgroundColor: 'rgba(251, 146, 60, 0.08)',
          borderWidth: 1.5,
          pointRadius: 0,
          tension: 0.4,
          fill: true,
        },
      ],
    },
    options: {
      ...CHART_OPTS,
      scales: {
        ...CHART_OPTS.scales,
        y: { ...CHART_OPTS.scales.y, title: { display: true, text: 'ms', color: '#6b7fa8', font: { size: 10 } } },
      },
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
  bwUnitSelect: document.getElementById('bw-unit-select'),
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

// ─── SLIDER DIRTY FLAG ────────────────────────────────────────────────────
// Prevents SSE config updates from snapping sliders back while user is editing.
let slidersDirty = false;
let slidersDirtyTimer = null;

function markSlidersDirty() {
  slidersDirty = true;
  clearTimeout(slidersDirtyTimer);
  // Auto-clear after 15 s of inactivity so SSE can resume syncing
  slidersDirtyTimer = setTimeout(() => { slidersDirty = false; }, 15000);
}

function clearSlidersDirty() {
  slidersDirty = false;
  clearTimeout(slidersDirtyTimer);
}

// ─── BANDWIDTH UNIT STATE ─────────────────────────────────────────────────
let bwUnit = 'mbps'; // 'mbps' | 'kbps'

// Profiles are fetched from the server on load so they always match server values.
let profiles = {
  none: { bandwidth_bps: 0, latency_ms: 0, loss_pct: 0, jitter_ms: 0 },
};

/** Convert the current bandwidth slider value to bps, respecting bwUnit. */
function sliderToBps() {
  const v = parseInt(els.bwSlider.value, 10);
  return bwUnit === 'kbps' ? v * 1_000 : v * 1_000_000;
}

/** Convert bps → slider integer in the current unit. */
function bpsToSlider(bps) {
  return bwUnit === 'kbps'
    ? Math.round(bps / 1_000)
    : Math.round(bps / 1_000_000);
}

/** Sync the CSS --fill variable so the slider track shows a filled portion. */
function syncFill(slider) {
  const min = parseFloat(slider.min) || 0;
  const max = parseFloat(slider.max) || 100;
  const val = parseFloat(slider.value) || 0;
  const pct = ((val - min) / (max - min)) * 100;
  slider.style.setProperty('--fill', `${pct.toFixed(1)}%`);
}

function updateSliderLabels() {
  const v = parseInt(els.bwSlider.value, 10);
  if (v === 0) {
    els.bwValue.textContent = 'Unlimited';
  } else {
    els.bwValue.textContent = `${v} ${bwUnit === 'kbps' ? 'kbps' : 'Mbps'}`;
  }
  els.latValueControl.textContent = `${els.latSlider.value} ms`;
  els.lossValueControl.textContent = `${els.lossSlider.value}%`;
  els.jitterValueControl.textContent = `${els.jitterSlider.value} ms`;
  syncFill(els.bwSlider);
  syncFill(els.latSlider);
  syncFill(els.lossSlider);
  syncFill(els.jitterSlider);
}

function setSlidersFromProfile(profileName) {
  const p = profiles[profileName];
  if (!p) return;
  els.bwSlider.value = bpsToSlider(p.bandwidth_bps);
  els.latSlider.value = p.latency_ms;
  els.lossSlider.value = (p.loss_pct * 100).toFixed(1);
  els.jitterSlider.value = p.jitter_ms;
  updateSliderLabels();
}

async function applyProfile(profileName) {
  const p = profiles[profileName];
  if (!p) return;
  setSlidersFromProfile(profileName);
  // Auto-apply to the server so live status reflects immediately.
  try {
    const res = await fetch('/configure', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        bandwidth_bps: p.bandwidth_bps,
        latency_ms: p.latency_ms,
        loss_pct: p.loss_pct,
        jitter_ms: p.jitter_ms,
      }),
    });
    const data = await res.json();
    updateConfigDisplay(data);
    clearSlidersDirty();
  } catch (err) {
    console.error('Failed to apply profile:', err);
  }
}

async function loadProfiles() {
  try {
    const res = await fetch('/profiles');
    if (!res.ok) return;
    const data = await res.json();
    // Merge server profiles into local object; keep 'none' sentinel.
    Object.assign(profiles, data);

    // Rebuild dropdown from server data so all profiles are present.
    if (!els.profileSelect) return;
    // Clear existing options except the placeholder.
    els.profileSelect.innerHTML = '<option value="none">— none —</option>';
    // Sort by bandwidth ascending for a natural ordering.
    const names = Object.keys(data).sort(
      (a, b) => (data[a].bandwidth_bps || 0) - (data[b].bandwidth_bps || 0)
    );
    names.forEach(name => {
      const opt = document.createElement('option');
      opt.value = name;
      const bps = data[name].bandwidth_bps;
      const bwStr = bps >= 1_000_000
        ? `${(bps / 1_000_000).toFixed(0)} Mbps`
        : bps >= 1_000 ? `${(bps / 1_000).toFixed(0)} kbps` : 'blocked';
      opt.textContent = `${name}  (${bwStr}, ${data[name].latency_ms}ms)`;
      els.profileSelect.appendChild(opt);
    });
  } catch (err) {
    console.error('Failed to load profiles:', err);
  }
}

// ─── SLIDER / UNIT EVENT LISTENERS ───────────────────────────────────────
els.profileSelect.addEventListener('change', () => {
  const v = els.profileSelect.value;
  if (v === 'none') {
    // 'none' just clears sliders to 0 locally — user can then adjust.
    setSlidersFromProfile('none');
  } else {
    applyProfile(v);
  }
});

[els.bwSlider, els.latSlider, els.lossSlider, els.jitterSlider].forEach(s => {
  s.addEventListener('input', () => { markSlidersDirty(); updateSliderLabels(); });
});

// Bandwidth unit toggle (Mbps ↔ kbps)
els.bwUnitSelect?.addEventListener('change', () => {
  const newUnit = els.bwUnitSelect.value;
  if (newUnit === bwUnit) return;
  // Convert the current slider value to bps then back in the new unit
  const currentBps = sliderToBps();
  bwUnit = newUnit;
  if (bwUnit === 'kbps') {
    els.bwSlider.max = 100_000; // 100 000 kbps = 100 Mbps
    els.bwSlider.step = 100;
  } else {
    els.bwSlider.max = 200;
    els.bwSlider.step = 1;
  }
  els.bwSlider.value = bpsToSlider(currentBps);
  updateSliderLabels();
});

els.applyBtn.addEventListener('click', async () => {
  const payload = {
    bandwidth_bps: sliderToBps(),
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
    clearSlidersDirty(); // Applied — SSE may resume syncing
  } catch (err) {
    console.error('Failed to apply config:', err);
  }
});

// ─── UPDATE UI ────────────────────────────────────────────────────────────
function updateConfigDisplay(config) {
  if (els.configDisplay) els.configDisplay.textContent = JSON.stringify(config, null, 2);
}

function updateSlidersFromConfig(config) {
  // Skip while the user is actively editing the sliders to prevent snap-back.
  if (slidersDirty) return;
  if (config.bandwidth_bps !== undefined) {
    els.bwSlider.value = bpsToSlider(config.bandwidth_bps);
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

    const builtin = data.scenarios || [];
    const user = data.user_scenarios || [];

    function addOptions(names, groupLabel) {
      if (names.length === 0) return;
      const grp = document.createElement('optgroup');
      grp.label = groupLabel;
      names.forEach(name => {
        const opt = document.createElement('option');
        opt.value = name;
        opt.textContent = name.replace(/[-_]/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
        grp.appendChild(opt);
      });
      scenarioSelect?.appendChild(grp);
    }

    addOptions(builtin, 'Built-in');
    addOptions(user, 'Saved');

    const total = builtin.length + user.length;
    if (runScenarioBtn) runScenarioBtn.disabled = total === 0;
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

/** Build scenario dict from the custom form inputs. Returns null if validation fails. */
function buildCustomScenarioDict() {
  const customMsg = document.getElementById('custom-scenario-msg');
  const name = document.getElementById('custom-scenario-name')?.value.trim() || 'Custom Scenario';
  const rows = document.querySelectorAll('#custom-phases-list .phase-row');

  if (rows.length === 0) {
    if (customMsg) { customMsg.style.color = '#f44336'; customMsg.textContent = 'Add at least one phase.'; }
    return null;
  }

  const phases = [...rows].map((row, i) => {
    const phaseName = row.querySelector('.phase-name')?.value.trim() || `Phase ${i + 1}`;
    const durSec = parseInt(row.querySelector('.phase-duration')?.value || '10', 10);
    const bwKbps = parseFloat(row.querySelector('.phase-bw')?.value || '0');
    const latMs = parseInt(row.querySelector('.phase-lat')?.value || '0', 10);
    const lossPct = parseFloat(row.querySelector('.phase-loss')?.value || '0');
    const jitterMs = parseInt(row.querySelector('.phase-jitter')?.value || '0', 10);

    const phase = { name: phaseName, duration_ms: durSec * 1000 };
    if (bwKbps > 0) phase.bandwidth_bps = Math.round(bwKbps * 1000);
    if (latMs > 0) phase.latency_ms = latMs;
    if (lossPct > 0) phase.loss_pct = lossPct / 100;
    if (jitterMs > 0) phase.jitter_ms = jitterMs;
    return phase;
  });

  return { name, phases };
}

document.getElementById('save-custom-btn')?.addEventListener('click', async () => {
  const customMsg = document.getElementById('custom-scenario-msg');
  const scenarioDict = buildCustomScenarioDict();
  if (!scenarioDict) return;

  try {
    const res = await fetch('/scenarios/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(scenarioDict),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      if (customMsg) { customMsg.style.color = '#f44336'; customMsg.textContent = `Save failed: ${data.error || res.status}`; }
    } else {
      if (customMsg) { customMsg.style.color = '#81c784'; customMsg.textContent = `Saved as "${data.saved}"`; }
      setTimeout(() => { if (customMsg) customMsg.textContent = ''; }, 4000);
      // Refresh the built-in/saved scenario list
      if (scenarioSelect) {
        while (scenarioSelect.children.length > 1) scenarioSelect.removeChild(scenarioSelect.lastChild);
      }
      loadBuiltinScenarios();
    }
  } catch (err) {
    if (customMsg) { customMsg.style.color = '#f44336'; customMsg.textContent = `Failed: ${err.message}`; }
  }
});

document.getElementById('run-custom-btn')?.addEventListener('click', async () => {
  const customMsg = document.getElementById('custom-scenario-msg');
  const scenarioDict = buildCustomScenarioDict();
  if (!scenarioDict) return;

  try {
    const res = await fetch('/scenario/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(scenarioDict),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      if (customMsg) { customMsg.style.color = '#f44336'; customMsg.textContent = `Error: ${data.error || res.status}`; }
    } else {
      if (customMsg) { customMsg.style.color = '#81c784'; customMsg.textContent = `Started: ${scenarioDict.name}`; }
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
    const enabled = r.enabled !== false; // default true
    return `
      <div class="rule-row ${enabled ? '' : 'rule-row--disabled'}" data-id="${escapeHtml(r.id)}">
        <button class="rule-toggle ${enabled ? 'rule-toggle--on' : 'rule-toggle--off'}"
                title="${enabled ? 'Disable rule' : 'Enable rule'}"
                data-id="${escapeHtml(r.id)}"
                data-enabled="${enabled}">
          <span class="rule-toggle-dot"></span>
        </button>
        <div class="rule-info">
          <div class="rule-name">${escapeHtml(label)}</div>
          <div class="rule-pattern">${escapeHtml(r.pattern)}</div>
          <div class="rule-detail">${escapeHtml(detail)}</div>
        </div>
        <button class="btn-icon rule-remove-btn" title="Remove rule" data-id="${escapeHtml(r.id)}">×</button>
      </div>`;
  }).join('');

  container.querySelectorAll('.rule-toggle').forEach(btn => {
    btn.addEventListener('click', () => {
      const currently = btn.dataset.enabled === 'true';
      toggleRule(btn.dataset.id, !currently);
    });
  });

  container.querySelectorAll('.rule-remove-btn').forEach(btn => {
    btn.addEventListener('click', () => removeRule(btn.dataset.id));
  });
}

async function toggleRule(ruleId, enabled) {
  try {
    const res = await fetch(`/rules/${encodeURIComponent(ruleId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    });
    if (res.ok) {
      await refreshRules();
    } else {
      const data = await res.json().catch(() => ({}));
      showRuleMsg(`Toggle failed: ${data.error || res.status}`, true);
    }
  } catch (err) {
    showRuleMsg(`Failed: ${err.message}`, true);
  }
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

  const bwVal   = parseFloat(document.getElementById('rule-bw-input')?.value || '0');
  const bwUnitR = document.getElementById('rule-bw-unit')?.value || 'kbps';
  const latMs   = parseInt(document.getElementById('rule-lat-input')?.value || '0', 10);
  const lossPct = parseFloat(document.getElementById('rule-loss-input')?.value || '0');
  const jitterMs = parseInt(document.getElementById('rule-jitter-input')?.value || '0', 10);
  const comment = document.getElementById('rule-comment-input')?.value.trim() || '';

  const payload = { pattern, comment };
  if (bwVal > 0)    payload.bandwidth_bps = Math.round(bwVal * (bwUnitR === 'mbps' ? 1_000_000 : 1_000));
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
  loadProfiles();
  loadBuiltinScenarios();
  refreshRules();
  setInterval(refreshRules, 5000);

  try {
    const res = await fetch('/status');
    const config = await res.json();
    updateConfigDisplay(config);

    if (config.bandwidth_bps !== undefined) {
      els.bwSlider.value = bpsToSlider(config.bandwidth_bps);
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
