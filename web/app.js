/* =========================================================================
   Smart Solar — Agentic AI dashboard
   ========================================================================= */

const $  = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const CSS = getComputedStyle(document.documentElement);
const COLOR = {
  lime:  CSS.getPropertyValue('--lime').trim()  || '#a3e635',
  green: CSS.getPropertyValue('--green').trim() || '#4ade80',
  teal:  CSS.getPropertyValue('--teal').trim()  || '#2dd4bf',
  cyan:  CSS.getPropertyValue('--cyan').trim()  || '#38bdf8',
  amber: CSS.getPropertyValue('--amber').trim() || '#fbbf24',
  red:   CSS.getPropertyValue('--red').trim()   || '#fb7185',
  muted: CSS.getPropertyValue('--muted').trim() || '#8aa89a',
  faint: CSS.getPropertyValue('--faint').trim() || '#5d7a6c',
};

const state = {
  chartHours: 24,
  summaryPeriod: 'today',
  forecastTarget: 'tomorrow',
  allowControl: false,
  chatHistory: [],
  live: null,
  charts: {},
  flowPower: { pv: 0, load: 0, batt: 0 },
};

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) {}
    throw new Error(detail);
  }
  return res.json();
}

const num = (v, nd = 1) =>
  v === null || v === undefined || Number.isNaN(v) ? '—' : Number(v).toFixed(nd);

/* =========================================================================
   Tabs
   ========================================================================= */
$$('nav.tabs button').forEach((btn) => {
  btn.addEventListener('click', () => {
    $$('nav.tabs button').forEach((b) => b.classList.remove('active'));
    $$('.view').forEach((v) => v.classList.remove('active'));
    btn.classList.add('active');
    $('#view-' + btn.dataset.view).classList.add('active');
    if (btn.dataset.view === 'graph') loadGraph();
    if (btn.dataset.view === 'forecast') loadForecast();
    if (btn.dataset.view === 'decisions') loadDecisions();
    if (btn.dataset.view === 'formulas') loadFormulas();
  });
});

/* =========================================================================
   Rendering helpers
   ========================================================================= */
function renderTable(target, table, opts = {}) {
  const el = typeof target === 'string' ? $(target) : target;
  if (!el) return;
  if (!table || !table.rows || !table.rows.length) {
    el.innerHTML = '<div class="empty">No data to show yet.</div>';
    return;
  }
  const limit = opts.limit || table.rows.length;
  const rows = table.rows.slice(0, limit);

  const head = table.columns
    .map((c, i) => `<th class="${i > 0 ? 'num' : ''}">${esc(c)}</th>`)
    .join('');

  const body = rows
    .map((r) => {
      const tds = r
        .map((cell, i) => {
          const isNum = i > 0 && !Number.isNaN(parseFloat(cell)) && typeof cell !== 'boolean';
          return `<td class="${isNum ? 'num' : ''}">${tagOrText(cell)}</td>`;
        })
        .join('');
      return `<tr>${tds}</tr>`;
    })
    .join('');

  el.innerHTML =
    (table.title && opts.showTitle !== false
      ? `<div class="faint" style="font-size:11.5px;margin-bottom:9px">${esc(table.title)}</div>`
      : '') +
    `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function tagOrText(cell) {
  const s = String(cell === null || cell === undefined ? '—' : cell);
  const t = s.toUpperCase();
  if (t === 'PASS')   return '<span class="tag pass">PASS</span>';
  if (t === 'FAIL')   return '<span class="tag fail">FAIL</span>';
  if (t === 'REJECT') return '<span class="tag reject">REJECT</span>';
  if (t === 'PASSED') return '<span class="tag pass">PASSED</span>';
  if (t === 'REJECTED') return '<span class="tag reject">REJECTED</span>';
  if (t === 'WARNING') return '<span class="tag warn">WARNING</span>';
  if (t === 'CRITICAL') return '<span class="tag fail">CRITICAL</span>';
  return esc(s);
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function renderFormulas(target, formulas, opts = {}) {
  const el = typeof target === 'string' ? $(target) : target;
  if (!el) return;
  if (!formulas || !formulas.length) { el.innerHTML = ''; return; }
  const list = opts.limit ? formulas.slice(0, opts.limit) : formulas;

  el.innerHTML = list
    .map((f) => `
      <div class="formula-item">
        <div class="fname">${esc(f.name)}${
          f.value !== null && f.value !== undefined
            ? ` <span class="mono" style="color:var(--lime)"> = ${f.value} ${esc(f.unit || '')}</span>`
            : ''}</div>
        <div class="katex-target">$$${f.latex}$$</div>
        <pre style="margin:9px 0 0"><code>${esc(f.substitution)}</code></pre>
        <div class="faint" style="font-size:11.5px;margin-top:8px">${esc(f.explanation)}</div>
        ${(f.assumptions && f.assumptions.length)
          ? `<div style="font-size:11.5px;margin-top:8px;color:var(--amber)">
               <b>Assumptions:</b> ${f.assumptions.map(esc).join(' ')}</div>`
          : ''}
      </div>`)
    .join('');
  typesetMath(el);
}

function typesetMath(el) {
  if (!window.renderMathInElement) return;
  try {
    renderMathInElement(el, {
      delimiters: [
        { left: '$$', right: '$$', display: true },
        { left: '$',  right: '$',  display: false },
        { left: '\\[', right: '\\]', display: true },
        { left: '\\(', right: '\\)', display: false },
      ],
      throwOnError: false,
    });
  } catch (e) { /* formatting only — never break the page */ }
}

/* =========================================================================
   Energy flow diagram
   ========================================================================= */
const FLOW_NODES = [
  { id: 'sun',   x: 90,  y: 60,  label: 'Sun',     icon: '☀' },
  { id: 'panel', x: 90,  y: 200, label: 'Panel',   icon: '▦' },
  { id: 'hub',   x: 360, y: 170, label: 'Agent',   icon: '◆' },
  { id: 'load',  x: 620, y: 80,  label: 'Load',    icon: '⏻' },
  { id: 'batt',  x: 620, y: 265, label: 'Battery', icon: '▮' },
];

const FLOW_LINKS = [
  { id: 'sun-panel',  from: 'sun',   to: 'panel', key: 'pv'   },
  { id: 'panel-hub',  from: 'panel', to: 'hub',   key: 'pv'   },
  { id: 'hub-load',   from: 'hub',   to: 'load',  key: 'load' },
  { id: 'hub-batt',   from: 'hub',   to: 'batt',  key: 'batt' },
];

function buildFlow() {
  const svg = d3.select('#flow');
  svg.selectAll('*').remove();

  const defs = svg.append('defs');
  const grad = defs.append('linearGradient').attr('id', 'flowGrad')
    .attr('x1', '0%').attr('x2', '100%');
  grad.append('stop').attr('offset', '0%').attr('stop-color', COLOR.lime);
  grad.append('stop').attr('offset', '100%').attr('stop-color', COLOR.teal);

  const glow = defs.append('filter').attr('id', 'nodeGlow')
    .attr('x', '-60%').attr('y', '-60%').attr('width', '220%').attr('height', '220%');
  glow.append('feGaussianBlur').attr('stdDeviation', '5').attr('result', 'b');
  const merge = glow.append('feMerge');
  merge.append('feMergeNode').attr('in', 'b');
  merge.append('feMergeNode').attr('in', 'SourceGraphic');

  const byId = Object.fromEntries(FLOW_NODES.map((n) => [n.id, n]));

  // Links
  const linkG = svg.append('g').attr('class', 'links');
  FLOW_LINKS.forEach((l) => {
    const a = byId[l.from], b = byId[l.to];
    const mx = (a.x + b.x) / 2;
    const d = `M ${a.x} ${a.y} C ${mx} ${a.y}, ${mx} ${b.y}, ${b.x} ${b.y}`;
    linkG.append('path').attr('class', 'flow-path').attr('id', 'path-' + l.id).attr('d', d);
    linkG.append('g').attr('class', 'particles').attr('id', 'parts-' + l.id);
  });

  // Nodes
  const nodeG = svg.append('g').attr('class', 'nodes');
  FLOW_NODES.forEach((n) => {
    const g = nodeG.append('g').attr('transform', `translate(${n.x},${n.y})`);
    g.append('circle').attr('r', 34)
      .attr('fill', 'rgba(255,255,255,.045)')
      .attr('stroke', 'rgba(134,239,172,.3)').attr('stroke-width', 1.4);
    g.append('circle').attr('r', 34).attr('fill', 'none')
      .attr('stroke', COLOR.green).attr('stroke-width', 1.6)
      .attr('id', 'ring-' + n.id).attr('opacity', 0.25);
    g.append('text').attr('text-anchor', 'middle').attr('dy', 6)
      .attr('font-size', 21).attr('fill', COLOR.lime).text(n.icon);
    g.append('text').attr('class', 'flow-label').attr('text-anchor', 'middle')
      .attr('dy', 54).text(n.label);
    g.append('text').attr('class', 'flow-value').attr('text-anchor', 'middle')
      .attr('dy', 74).attr('id', 'flowval-' + n.id).text('—');
  });

  animateParticles();
}

let particleTimer = null;
function animateParticles() {
  if (particleTimer) clearInterval(particleTimer);
  particleTimer = setInterval(() => {
    FLOW_LINKS.forEach((l) => {
      const watts = Math.abs(state.flowPower[l.key] || 0);
      if (watts < 0.35) return;
      // Emission probability scales with power, capped so it stays readable
      if (Math.random() > Math.min(0.85, 0.12 + watts / 45)) return;
      emitParticle(l);
    });
  }, 260);
}

function emitParticle(link) {
  const pathEl = document.getElementById('path-' + link.id);
  const host = d3.select('#parts-' + link.id);
  if (!pathEl || host.empty()) return;
  const len = pathEl.getTotalLength();
  const reverse = (state.flowPower[link.key] || 0) < 0;

  const dot = host.append('circle')
    .attr('r', 3.4)
    .attr('fill', link.key === 'batt' ? COLOR.teal : COLOR.lime)
    .attr('filter', 'url(#nodeGlow)')
    .attr('opacity', 0.95);

  dot.transition()
    .duration(1500 + Math.random() * 500)
    .ease(d3.easeLinear)
    .attrTween('transform', () => (t) => {
      const p = pathEl.getPointAtLength((reverse ? 1 - t : t) * len);
      return `translate(${p.x},${p.y})`;
    })
    .attr('opacity', 0.15)
    .remove();
}

function updateFlow(live) {
  const pv   = live.reading.power || 0;
  const load = live.reading.load_power || 0;
  const batt = pv - load;

  state.flowPower = { pv, load, batt };

  const set = (id, text) => {
    const el = document.getElementById('flowval-' + id);
    if (el) el.textContent = text;
  };
  set('sun',   num(live.derived.irradiance, 0) + ' W/m²');
  set('panel', num(pv, 1) + ' W');
  set('hub',   (live.decision.action || '—').replace(/_/g, ' ').toLowerCase());
  set('load',  num(load, 1) + ' W');
  set('batt',  num(live.derived.soc, 1) + ' %');

  // Ring brightness reflects activity at each node
  const ring = (id, v, max) => {
    const el = document.getElementById('ring-' + id);
    if (el) el.setAttribute('opacity', String(0.2 + 0.75 * Math.min(1, v / max)));
  };
  ring('sun',   live.derived.irradiance || 0, 900);
  ring('panel', pv, 40);
  ring('load',  load, 20);
  ring('batt',  live.derived.soc || 0, 100);
  ring('hub',   live.health.status === 'HEALTHY' ? 100 : 30, 100);

  d3.select('#path-panel-hub').classed('hot', pv > 3);
  d3.select('#path-hub-load').classed('hot', load > 1);
  d3.select('#path-hub-batt').classed('hot', Math.abs(batt) > 3);
}

/* =========================================================================
   Battery gauge
   ========================================================================= */
function drawGauge(soc) {
  const svg = d3.select('#gauge');
  svg.selectAll('*').remove();
  const cx = 100, cy = 105, r = 78;
  const arc = d3.arc().innerRadius(r - 13).outerRadius(r)
    .startAngle(-Math.PI / 2).cornerRadius(7);

  const g = svg.append('g').attr('transform', `translate(${cx},${cy})`);
  g.append('path')
    .attr('d', arc.endAngle(Math.PI / 2)())
    .attr('fill', 'rgba(255,255,255,.06)');

  const pct = Math.max(0, Math.min(100, soc || 0));
  const color = pct < 20 ? COLOR.red : pct < 40 ? COLOR.amber : COLOR.green;

  g.append('path')
    .attr('d', arc.endAngle(-Math.PI / 2 + (pct / 100) * Math.PI)())
    .attr('fill', color)
    .style('filter', 'drop-shadow(0 0 9px ' + color + '66)');

  g.append('text').attr('text-anchor', 'middle').attr('dy', -6)
    .attr('font-family', 'JetBrains Mono, monospace')
    .attr('font-size', 34).attr('font-weight', 600).attr('fill', color)
    .text(num(pct, 1));
  g.append('text').attr('text-anchor', 'middle').attr('dy', 16)
    .attr('font-size', 11).attr('fill', COLOR.faint)
    .attr('letter-spacing', '.1em').text('PERCENT CHARGED');
}

/* =========================================================================
   Live view
   ========================================================================= */
async function loadLive() {
  let live;
  try {
    live = await api('/api/state');
  } catch (e) {
    $('#chip-source').textContent = 'offline';
    $('#dot-live').className = 'dot bad';
    return;
  }
  if (!live.ok) {
    $('#health-banner').innerHTML =
      `<div class="banner bad"><span class="ico">&#9888;</span>
       <span>No data available from the source. ${esc(live.reading.reason || '')}</span></div>`;
    return;
  }
  state.live = live;

  // Header chips
  const src = live.source;
  $('#chip-source').textContent =
    `${src.source} · ${src.rows.toLocaleString()} rows`;
  $('#dot-live').className = 'dot live';

  const hs = live.health.status;
  $('#chip-health').textContent = hs;
  $('#dot-health').className =
    'dot ' + (hs === 'HEALTHY' ? '' : hs === 'WARNING' ? 'warn' : 'bad');

  // Stat tiles
  $('#s-pv').textContent = num(live.reading.power, 1);
  $('#s-pv-sub').textContent =
    `${num(live.reading.voltage, 2)} V × ${num(live.reading.current, 3)} A`;
  $('#s-soc').textContent = num(live.derived.soc, 1);
  $('#s-soc-sub').textContent =
    `${num(live.reading.battery_v, 2)} V · ${num(live.battery.usable_energy_wh, 1)} Wh usable`;
  $('#s-load').textContent = num(live.reading.load_power, 1);
  $('#s-load-sub').textContent =
    `relay ${live.reading.load_state ? 'ON' : 'OFF'} · ` +
    `${num(live.battery.autonomy_hours, 1)} h autonomy`;
  $('#s-eff').textContent = num(live.derived.efficiency, 2);
  $('#s-eff-sub').textContent = `${num(live.derived.irradiance, 0)} W/m² estimated`;

  // Health banner
  const hb = $('#health-banner');
  if (live.health.faults.length) {
    hb.innerHTML = `<div class="banner bad"><span class="ico">&#9888;</span>
      <span><b>Fault.</b> ${live.health.faults.map(esc).join(' ')}</span></div>`;
  } else if (live.health.warnings.length) {
    hb.innerHTML = `<div class="banner warn"><span class="ico">&#9888;</span>
      <span><b>Warning.</b> ${live.health.warnings.map(esc).join(' ')}</span></div>`;
  } else {
    hb.innerHTML = `<div class="banner ok"><span class="ico">&#10003;</span>
      <span>All health checks pass. Data is ${num(live.reading.age_seconds, 0)} s old.</span></div>`;
  }

  // Decision
  $('#dec-action').textContent = (live.decision.action || '—').replace(/_/g, ' ');
  $('#dec-reason').textContent = live.decision.reason || '';
  $('#dec-rule').textContent = live.decision.rule_fired || '';
  const sc = live.validation.safety_check;
  $('#dec-safety').innerHTML =
    `<span class="tag ${sc === 'PASSED' ? 'pass' : 'reject'}">${sc}</span>`;
  $('#dec-control').textContent = live.decision.requires_control ? 'required' : 'not required';
  $('#dec-relay').textContent =
    live.decision.target_relay_state === null || live.decision.target_relay_state === undefined
      ? 'unchanged' : (live.decision.target_relay_state ? 'ON' : 'OFF');

  renderTable('#safety-table', live.validation.table, { showTitle: false });
  renderTable('#battery-table', live.battery.table, { showTitle: false });

  drawGauge(live.derived.soc);
  updateFlow(live);
}

/* =========================================================================
   Charts
   ========================================================================= */
async function loadChart() {
  const data = await api(`/api/timeseries?hours=${state.chartHours}`);
  const pts = data.points || [];
  const labels = pts.map((p) => new Date(p.t));

  const cfg = {
    type: 'line',
    data: {
      labels,
      datasets: [
        { label: 'PV power (W)', data: pts.map((p) => p.power),
          borderColor: COLOR.lime, backgroundColor: COLOR.lime + '22',
          fill: true, tension: 0.35, pointRadius: 0, borderWidth: 2, yAxisID: 'y' },
        { label: 'Load (W)', data: pts.map((p) => p.load),
          borderColor: COLOR.cyan, backgroundColor: 'transparent',
          tension: 0.35, pointRadius: 0, borderWidth: 1.6, yAxisID: 'y' },
        { label: 'Battery SOC (%)', data: pts.map((p) => p.soc),
          borderColor: COLOR.green, backgroundColor: 'transparent',
          tension: 0.35, pointRadius: 0, borderWidth: 1.6,
          borderDash: [5, 4], yAxisID: 'y1' },
      ],
    },
    options: chartOptions('Power (W)', 'SOC (%)'),
  };

  if (state.charts.power) state.charts.power.destroy();
  state.charts.power = new Chart($('#chart-power'), cfg);
}

function chartOptions(yLabel, y1Label) {
  return {
    responsive: true,
    maintainAspectRatio: true,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: {
        labels: { color: COLOR.muted, boxWidth: 11, boxHeight: 11,
                  font: { size: 11, family: 'Inter' }, usePointStyle: true },
      },
      tooltip: {
        backgroundColor: 'rgba(5,16,11,.96)', borderColor: 'rgba(134,239,172,.28)',
        borderWidth: 1, titleColor: '#e8f6ee', bodyColor: '#8aa89a', padding: 11,
        titleFont: { family: 'Inter', size: 12 },
        bodyFont: { family: 'JetBrains Mono', size: 11.5 },
      },
    },
    scales: {
      x: {
        type: 'time',
        time: { unit: state.chartHours > 48 ? 'day' : 'hour',
                displayFormats: { hour: 'HH:mm', day: 'dd MMM' } },
        grid: { color: 'rgba(255,255,255,.045)' },
        ticks: { color: COLOR.faint, font: { size: 10, family: 'JetBrains Mono' },
                 maxRotation: 0, autoSkipPadding: 24 },
      },
      y: {
        position: 'left',
        title: { display: true, text: yLabel, color: COLOR.faint, font: { size: 10 } },
        grid: { color: 'rgba(255,255,255,.045)' },
        ticks: { color: COLOR.faint, font: { size: 10, family: 'JetBrains Mono' } },
      },
      y1: {
        position: 'right', min: 0, max: 100,
        title: { display: true, text: y1Label, color: COLOR.faint, font: { size: 10 } },
        grid: { drawOnChartArea: false },
        ticks: { color: COLOR.faint, font: { size: 10, family: 'JetBrains Mono' } },
      },
    },
  };
}

/* =========================================================================
   Energy summary
   ========================================================================= */
async function loadSummary() {
  const s = await api(`/api/summary?period=${state.summaryPeriod}`);
  if (s.error) {
    $('#summary-stats').innerHTML = `<div class="empty">${esc(s.error)}</div>`;
    return;
  }
  const h = s.headline;
  $('#summary-stats').innerHTML = [
    tile('Generated', num(h.generated_wh, 1), 'Wh', `peak ${num(h.peak_power_w, 1)} W`, 'lime'),
    tile('Consumed', num(h.consumed_wh, 1), 'Wh', `${h.samples.toLocaleString()} samples`, 'cyan'),
    tile('Self-sufficiency', num(h.self_sufficiency_pct, 1), '%',
         `net ${num(h.net_wh, 1)} Wh`, 'green'),
    tile('Cost avoided', num(h.cost_saved_inr, 2), 'INR',
         `${num(h.co2_avoided_kg, 3)} kg CO₂`, 'teal'),
  ].join('');
  renderTable('#summary-table', s.table, { limit: 26 });
  renderFormulas('#summary-formulas', s.formulas, { limit: 4 });
}

function tile(label, value, unit, sub, accent) {
  return `<div class="stat accent-${accent}">
    <div class="label">${esc(label)}</div>
    <div class="value">${esc(value)}<span class="unit">${esc(unit)}</span></div>
    <div class="sub">${esc(sub)}</div>
  </div>`;
}

/* =========================================================================
   Forecast
   ========================================================================= */
async function loadForecast() {
  const f = await api(`/api/forecast?target=${state.forecastTarget}`);
  if (f.error) {
    $('#forecast-stats').innerHTML = `<div class="empty">${esc(f.error)}</div>`;
    return;
  }
  const h = f.headline;
  $('#forecast-title').textContent = f.table.title;
  $('#forecast-stats').innerHTML = [
    tile('Predicted generation', num(h.predicted_generation_wh, 1), 'Wh',
         `peak ${num(h.peak_power_w, 1)} W at ${h.peak_hour}`, 'lime'),
    tile('Projected consumption', num(h.predicted_consumption_wh, 1), 'Wh',
         `${h.daylight_hours} daylight hours`, 'cyan'),
    tile('Net balance', num(h.predicted_net_wh, 1), 'Wh', h.verdict, 'green'),
    tile('Performance ratio', num(h.performance_ratio_pct, 1), '%',
         'measured on recent data', 'teal'),
  ].join('');

  renderTable('#forecast-table', f.table, { showTitle: false });
  renderFormulas('#forecast-formulas', f.formulas);

  const rows = f.table.rows;
  const cfg = {
    type: 'bar',
    data: {
      labels: rows.map((r) => r[0]),
      datasets: [
        { label: 'Predicted PV (W)', data: rows.map((r) => r[2]),
          backgroundColor: COLOR.lime + 'bb', borderRadius: 4, order: 2 },
        { label: 'Projected load (W)', data: rows.map((r) => r[3]),
          type: 'line', borderColor: COLOR.cyan, backgroundColor: 'transparent',
          tension: 0.35, pointRadius: 0, borderWidth: 2, order: 1 },
      ],
    },
    options: {
      responsive: true,
      plugins: {
        legend: { labels: { color: COLOR.muted, usePointStyle: true, boxWidth: 11,
                            font: { size: 11, family: 'Inter' } } },
        tooltip: { backgroundColor: 'rgba(5,16,11,.96)', borderColor: 'rgba(134,239,172,.28)',
                   borderWidth: 1, titleColor: '#e8f6ee', bodyColor: '#8aa89a', padding: 11 },
      },
      scales: {
        x: { grid: { display: false },
             ticks: { color: COLOR.faint, font: { size: 10, family: 'JetBrains Mono' } } },
        y: { grid: { color: 'rgba(255,255,255,.045)' },
             title: { display: true, text: 'Watts', color: COLOR.faint, font: { size: 10 } },
             ticks: { color: COLOR.faint, font: { size: 10, family: 'JetBrains Mono' } } },
      },
    },
  };
  if (state.charts.forecast) state.charts.forecast.destroy();
  state.charts.forecast = new Chart($('#chart-forecast'), cfg);
}

/* =========================================================================
   Correlation graph (D3 force)
   ========================================================================= */
async function loadGraph() {
  const g = await api('/api/graph?days=14');
  $('#graph-source').textContent =
    g.source === 'neo4j' ? 'live from Neo4j' : 'computed locally (Neo4j not connected)';
  $('#chip-graph').textContent = g.source === 'neo4j' ? 'Neo4j' : 'pandas';
  $('#cypher').textContent = g.cypher || '';
  renderTable('#graph-table', g.table, { showTitle: false });
  renderFormulas('#graph-formulas', g.formulas, { limit: 1 });

  drawForceGraph(g.nodes || [], g.edges || []);
}

function drawForceGraph(nodesIn, edgesIn) {
  const svg = d3.select('#graph');
  svg.selectAll('*').remove();
  const el = svg.node();
  const W = el.clientWidth || 900;
  const H = 520;
  svg.attr('viewBox', `0 0 ${W} ${H}`);

  if (!nodesIn.length) {
    svg.append('text').attr('x', W / 2).attr('y', H / 2)
      .attr('text-anchor', 'middle').attr('fill', COLOR.faint)
      .text('Not enough data to compute correlations yet.');
    return;
  }

  const nodes = nodesIn.map((n) => ({ ...n }));
  const links = edgesIn.map((e) => ({ ...e, source: e.source, target: e.target }));

  const maxSamples = d3.max(nodes, (n) => n.samples) || 1;
  const rScale = d3.scaleSqrt().domain([0, maxSamples]).range([18, 34]);

  const sim = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links).id((d) => d.id)
      .distance((d) => 90 + (1 - Math.abs(d.r)) * 190)
      .strength((d) => Math.abs(d.r) * 0.75))
    .force('charge', d3.forceManyBody().strength(-780))
    .force('center', d3.forceCenter(W / 2, H / 2))
    .force('collide', d3.forceCollide().radius((d) => rScale(d.samples) + 26));

  const defs = svg.append('defs');
  const glow = defs.append('filter').attr('id', 'gGlow')
    .attr('x', '-60%').attr('y', '-60%').attr('width', '220%').attr('height', '220%');
  glow.append('feGaussianBlur').attr('stdDeviation', '4').attr('result', 'b');
  const m = glow.append('feMerge');
  m.append('feMergeNode').attr('in', 'b');
  m.append('feMergeNode').attr('in', 'SourceGraphic');

  const tip = $('#tooltip');
  const showTip = (html, ev) => {
    tip.innerHTML = html;
    tip.style.opacity = 1;
    tip.style.left = Math.min(ev.clientX + 15, window.innerWidth - 305) + 'px';
    tip.style.top = (ev.clientY + 15) + 'px';
  };
  const hideTip = () => { tip.style.opacity = 0; };

  const link = svg.append('g').selectAll('line').data(links).join('line')
    .attr('stroke', (d) => (d.r > 0 ? COLOR.green : COLOR.red))
    .attr('stroke-opacity', (d) => 0.2 + Math.abs(d.r) * 0.6)
    .attr('stroke-width', (d) => 1 + Math.abs(d.r) * 6)
    .attr('stroke-linecap', 'round')
    .style('cursor', 'pointer')
    .on('mousemove', (ev, d) => showTip(
      `<b>${d.r > 0 ? 'Positive' : 'Negative'} correlation</b><br>
       r = <b>${d.r > 0 ? '+' : ''}${d.r.toFixed(3)}</b> · ${d.strength}<br>
       R² = ${d.r_squared} — ${(d.r_squared * 100).toFixed(1)} % of the variation
       in one is tracked by the other.`, ev))
    .on('mouseleave', hideTip);

  const edgeLabel = svg.append('g').selectAll('text').data(links).join('text')
    .attr('class', 'edge-label')
    .text((d) => (d.r > 0 ? '+' : '') + d.r.toFixed(2));

  const node = svg.append('g').selectAll('g').data(nodes).join('g')
    .style('cursor', 'grab')
    .call(d3.drag()
      .on('start', (ev, d) => {
        if (!ev.active) sim.alphaTarget(0.28).restart();
        d.fx = d.x; d.fy = d.y;
      })
      .on('drag', (ev, d) => { d.fx = ev.x; d.fy = ev.y; })
      .on('end', (ev, d) => {
        if (!ev.active) sim.alphaTarget(0);
        d.fx = null; d.fy = null;
      }));

  node.append('circle')
    .attr('class', 'node-circle')
    .attr('r', (d) => rScale(d.samples))
    .attr('fill', (d, i) => d3.interpolateRgb(COLOR.green, COLOR.teal)(i / nodes.length))
    .attr('fill-opacity', 0.22)
    .attr('stroke', (d, i) => d3.interpolateRgb(COLOR.lime, COLOR.cyan)(i / nodes.length))
    .attr('stroke-width', 2)
    .attr('filter', 'url(#gGlow)')
    .on('mousemove', (ev, d) => showTip(
      `<b>${d.label}</b><br>
       mean ${d.mean} ${d.unit} · sd ${d.std}<br>
       range ${d.min} … ${d.max} ${d.unit}<br>
       ${Number(d.samples).toLocaleString()} samples`, ev))
    .on('mouseleave', hideTip);

  node.append('text').attr('class', 'node-text').attr('dy', 4)
    .text((d) => d.label);

  sim.on('tick', () => {
    link.attr('x1', (d) => d.source.x).attr('y1', (d) => d.source.y)
        .attr('x2', (d) => d.target.x).attr('y2', (d) => d.target.y);
    edgeLabel.attr('x', (d) => (d.source.x + d.target.x) / 2)
             .attr('y', (d) => (d.source.y + d.target.y) / 2 - 4);
    node.attr('transform', (d) => `translate(${d.x},${d.y})`);
  });
}

$('#btn-sync-graph').addEventListener('click', async (e) => {
  e.target.textContent = 'Syncing…';
  try {
    const r = await api('/api/graph/sync', { method: 'POST' });
    e.target.textContent = r.synced
      ? `Synced ${r.nodes} nodes, ${r.edges} links`
      : 'Neo4j not connected';
    if (!r.synced) {
      alert('Neo4j is not connected: ' + (r.reason || 'unknown') +
            '\n\nThe graph still works — correlations are computed locally.' +
            '\nTo use the real Neo4j, set NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD in .env');
    }
    await loadGraph();
  } catch (err) {
    e.target.textContent = 'Sync failed';
  }
  setTimeout(() => { e.target.textContent = 'Sync to Neo4j'; }, 3200);
});

/* =========================================================================
   Agent chat
   ========================================================================= */
function addMsg(role, html) {
  const log = $('#chat-log');
  const div = document.createElement('div');
  div.className = 'msg ' + (role === 'user' ? 'user' : 'bot');
  div.innerHTML = `<div class="who">${role === 'user' ? 'You' : 'AI'}</div>
                   <div class="body">${html}</div>`;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}

async function sendQuestion(q) {
  if (!q.trim()) return;
  $('#chat-input').value = '';
  $('#btn-send').disabled = true;
  addMsg('user', esc(q));

  const pending = addMsg('bot',
    `<div class="thinking"><span></span><span></span><span></span>
     <span style="margin-left:5px">reading sensors and computing…</span></div>`);

  try {
    const res = await api('/api/ask', {
      method: 'POST',
      body: JSON.stringify({
        question: q,
        history: state.chatHistory.slice(-6),
        allow_control: state.allowControl,
      }),
    });

    const answerHtml = marked.parse(res.answer || '');
    let traceHtml = '';
    if (res.tool_calls && res.tool_calls.length) {
      traceHtml = `<details class="trace">
        <summary>▸ ${res.tool_calls.length} tool call${res.tool_calls.length > 1 ? 's' : ''}
          · ${esc(res.model || '')}</summary>
        ${res.tool_calls.map((t) => `
          <div class="trace-item">
            <div class="fn">${esc(t.tool)}()</div>
            ${Object.keys(t.arguments || {}).length
              ? `<div class="args">${esc(JSON.stringify(t.arguments))}</div>` : ''}
          </div>`).join('')}
      </details>`;
    }

    pending.querySelector('.body').innerHTML =
      `<div class="answer">${answerHtml}</div>${traceHtml}`;
    typesetMath(pending);

    state.chatHistory.push({ role: 'user', content: q });
    state.chatHistory.push({ role: 'assistant', content: res.answer });
  } catch (err) {
    pending.querySelector('.body').innerHTML =
      `<div class="banner bad"><span class="ico">&#9888;</span>
       <span>${esc(err.message)}</span></div>`;
  } finally {
    $('#btn-send').disabled = false;
    $('#chat-log').scrollTop = $('#chat-log').scrollHeight;
  }
}

$('#btn-send').addEventListener('click', () => sendQuestion($('#chat-input').value));
$('#chat-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') sendQuestion($('#chat-input').value);
});
$('#btn-clear').addEventListener('click', () => {
  $('#chat-log').innerHTML = '';
  state.chatHistory = [];
  greet();
});
$('#btn-control').addEventListener('click', (e) => {
  state.allowControl = !state.allowControl;
  e.target.classList.toggle('on', state.allowControl);
  e.target.textContent = 'Hardware control: ' + (state.allowControl ? 'ON' : 'OFF');
});

function greet() {
  addMsg('bot', `<div class="answer">
    <p>I can read the live system, summarise past energy, forecast future generation,
    run health checks and explain how the parameters relate to each other.</p>
    <p class="faint" style="font-size:12.5px">Every number I give you is computed in Python
    and comes with its formula. I never calculate anything myself — that is what keeps the
    answers exact.</p></div>`);
}

/* =========================================================================
   Decisions + formulas
   ========================================================================= */
async function loadDecisions() {
  const d = await api('/api/decisions?limit=40');
  if (!d.table) {
    $('#decisions-table').innerHTML =
      `<div class="empty">${esc(d.note || 'No decisions logged yet.')}
       Run a decision cycle to create one.</div>`;
    return;
  }
  renderTable('#decisions-table', d.table, { showTitle: false });
}

$('#btn-cycle').addEventListener('click', async (e) => {
  e.target.textContent = 'Running…';
  try {
    await api('/api/cycle?use_llm=false&allow_control=false', { method: 'POST' });
    await loadDecisions();
    e.target.textContent = 'Cycle complete';
  } catch (err) {
    e.target.textContent = 'Failed';
  }
  setTimeout(() => { e.target.textContent = 'Run one decision cycle'; }, 2200);
});
$('#btn-refresh-log').addEventListener('click', loadDecisions);

async function loadFormulas() {
  const f = await api('/api/formulas');
  const byCat = {};
  f.catalogue.forEach((x) => { (byCat[x.category] ||= []).push(x); });
  $('#formula-list').innerHTML = Object.entries(byCat).map(([cat, items]) => `
    <div style="margin-bottom:26px">
      <div style="font-size:11px;text-transform:uppercase;letter-spacing:.09em;
                  color:var(--teal);font-weight:700;margin-bottom:11px">${esc(cat)}</div>
      ${items.map((i) => `
        <div class="formula-item">
          <div class="fname">${esc(i.name)}</div>
          <div>$$${i.latex}$$</div>
        </div>`).join('')}
    </div>`).join('');
  typesetMath($('#formula-list'));
}

/* =========================================================================
   Toggle button groups
   ========================================================================= */
function group(selector, attr, onChange) {
  $$(selector).forEach((btn) => {
    btn.addEventListener('click', () => {
      $$(selector).forEach((b) => b.classList.remove('on'));
      btn.classList.add('on');
      onChange(btn.dataset[attr]);
    });
  });
}
group('[data-hours]', 'hours', (v) => { state.chartHours = +v; loadChart(); });
group('[data-period]', 'period', (v) => { state.summaryPeriod = v; loadSummary(); });
group('[data-target]', 'target', (v) => { state.forecastTarget = v; loadForecast(); });

/* =========================================================================
   Boot
   ========================================================================= */
async function boot() {
  buildFlow();
  greet();

  try {
    const cfg = await api('/api/config');
    $('#chip-llm').textContent = cfg.llm.configured ? cfg.llm.model.split('-')[0] : 'no key';
    if (!cfg.llm.configured) {
      $('#chat-log').insertAdjacentHTML('beforeend',
        `<div class="banner warn"><span class="ico">&#9888;</span>
         <span><b>No Groq API key.</b> Put <code>GROQ_API_KEY=…</code> in your
         <code>.env</code> file to enable the agent. Everything else on this dashboard
         works without it. Free key: console.groq.com/keys</span></div>`);
    }
    $('#chip-graph').textContent = cfg.neo4j.connected ? 'Neo4j' : 'pandas';

    $('#suggestions').innerHTML = (cfg.suggested_questions || [])
      .map((q) => `<button>${esc(q)}</button>`).join('');
    $$('#suggestions button').forEach((b) =>
      b.addEventListener('click', () => sendQuestion(b.textContent)));
  } catch (e) { /* config is optional */ }

  await loadLive();
  await loadChart();
  await loadSummary();

  setInterval(loadLive, 5000);
  setInterval(loadChart, 60000);
}

boot();
