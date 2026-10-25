// StormSim SF — Evolve view. Vanilla ES module, inline SVG, no dependencies.
// Data: GET /api/evolve/{job} (state.json), polled every 1.5 s while running. ?fixture=1 loads ./fixture.json.

const $ = (s, el = document) => el.querySelector(s);
const q = new URLSearchParams(location.search);
const FIXTURE = q.has('fixture');
const POLL_MS = 1500;
const SVGNS = 'http://www.w3.org/2000/svg';

const OPS = {
  seed: { label: 'Seed', color: 'var(--seed)', hex: '#8fa1b6' },
  crossover: { label: 'Argon crossover', color: 'var(--cross)', hex: '#e58cff' },
  mutate: { label: 'Flash diff', color: 'var(--flash)', hex: '#5fe1ff' },
  tweak: { label: 'Tweak', color: 'var(--tweak)', hex: '#ffb54a' },
};
const STRAT_COLORS = { grey: '#7f9cff', green: '#5dff9d', building: '#ffb54a', response: '#ff7ab6', trees: '#3fd0a6', other: '#8fa1b6', mixed: '#c7d4e2' };
const STRAT_LABELS = { grey: 'Grey infra', green: 'Green infra', building: 'Floodproofing', response: 'Emergency response', trees: 'Tree work', mixed: 'Mixed portfolio', other: 'Other' };
const CAUSE_LABELS = {
  building_structure: 'Building structure', contents: 'Contents', business_interruption: 'Business interruption',
  vehicles: 'Vehicles', tree_strikes: 'Tree strikes', tree_removal: 'Tree removal', life_safety_usd: 'Life safety',
  life_safety: 'Life safety', glazing: 'Glazing / facade', wind: 'Wind', response_ops: 'Response operations',
};
const PENDING = new Set(['writing', 'checking', 'simulating']);

let S = null;            // current state
let jobId = q.get('job');
let selId = q.get('cand');
let tab = q.get('tab') === 'architecture' ? 'architecture' : 'evolution';
let pollTimer = null;
const seen = new Set();  // candidate ids already drawn (for pop-in animation)
let firstRender = true;
const codeCache = new Map();
let diffMode = 'diff';
const cellPrev = {};
let D = null;

// ---------------------------------------------------------------- formatting
function money(v, { sign = false, dp } = {}) {
  if (v == null || !isFinite(v)) return '—';
  const s = v < 0 ? '−' : sign ? '+' : '';
  const a = Math.abs(v);
  let out;
  if (a >= 1e9) out = '$' + (a / 1e9).toFixed(dp ?? 2) + 'B';
  else if (a >= 1e6) out = '$' + (a / 1e6).toFixed(dp ?? (a >= 100e6 ? 0 : 1)) + 'M';
  else if (a >= 1e3) out = '$' + (a / 1e3).toFixed(dp ?? 0) + 'k';
  else out = '$' + a.toFixed(0);
  return s + out;
}
const num = (v, dp = 0) => (v == null || !isFinite(v) ? '—' : Number(v).toLocaleString('en-US', { maximumFractionDigits: dp, minimumFractionDigits: dp }));
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
function dur(sec) {
  if (sec == null || !isFinite(sec)) return '—';
  sec = Math.max(0, Math.round(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? `${h}h ${String(m).padStart(2, '0')}m` : `${m}m ${String(s).padStart(2, '0')}s`;
}
const clock = (t) => (t ? new Date(t * 1000).toLocaleTimeString('en-US', { hour12: false }) : '');
function modelName(m) {
  if (!m) return 'no LLM';
  const map = { 'gemini-4-argon': 'Gemini 4 Argon', 'gemini-3.8-flash': 'Gemini 3.8 Flash', 'gemini-3.5-flash': 'Gemini 3.5 Flash', 'gemini-3.5-flash-lite': 'Gemini 3.5 Flash-Lite' };
  return map[m] || m;
}
const opOf = (c) => OPS[c.op] || OPS.mutate;
const stratLabel = (k) => (S?.archive?.strategy_labels?.[k]) || STRAT_LABELS[k] || k;

// ---------------------------------------------------------------- derived data
function derive(st) {
  const cands = st.candidates || [];
  const byId = new Map(cands.map((c) => [c.id, c]));
  const ok = (c) => c.status === 'scored' && c.score && c.score.within_budget !== false && isFinite(c.score.net_benefit_usd);
  const scored = cands.filter(ok);
  let best = byId.get(st.best_id);
  if (!best || !ok(best)) best = scored.reduce((b, c) => (!b || c.score.net_benefit_usd > b.score.net_benefit_usd ? c : b), null);
  return { cands, byId, ok, scored, best };
}
function lineage(c) {
  const out = [];
  const seenL = new Set();
  let cur = c;
  while (cur && !seenL.has(cur.id)) {
    out.push(cur);
    seenL.add(cur.id);
    cur = cur.parents && cur.parents.length ? D.byId.get(cur.parents[0]) : null;
  }
  return out; // [self, parent, grandparent, ... seed]
}

// ---------------------------------------------------------------- svg helpers
function el(tag, attrs = {}, parent) {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}
function svgIn(container) {
  container.innerHTML = '';
  const r = container.getBoundingClientRect();
  const W = Math.max(200, r.width), H = Math.max(140, r.height);
  const svg = el('svg', { viewBox: `0 0 ${W} ${H}` });
  container.appendChild(svg);
  return { svg, W, H };
}
function niceTicks(lo, hi, n = 5) {
  const span = hi - lo || 1;
  const step0 = span / n;
  const mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n) || 10 * mag;
  const t = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) t.push(+v.toFixed(10));
  return t;
}
const tickM = (v) => (Math.abs(v) >= 1e9 ? `$${(v / 1e9).toFixed(1)}B` : `$${Math.round(v / 1e6)}M`);

// ---------------------------------------------------------------- tooltip
const tip = $('#tip');
function showTip(c, ev) {
  const sc = c.score || {};
  const op = opOf(c);
  const rows = [];
  if (c.score) {
    rows.push(['Net benefit', money(sc.net_benefit_usd, { sign: true })], ['Plan cost', money(sc.plan_cost_usd)], ['Damage avoided', money(sc.damage_avoided_usd)], ['BCR', sc.bcr != null ? num(sc.bcr, 2) : '—']);
  } else rows.push(['Status', c.status.replace('_', ' ')]);
  tip.innerHTML = `<div class="th"><span class="mono">${esc(c.id)}</span><span class="chip"><i style="background:${op.color}"></i>${esc(op.label)}</span><span style="color:var(--faint)">gen ${c.gen}</span></div>
    ${c.note ? `<div class="tn">${esc(c.note)}</div>` : ''}
    <div class="tg">${rows.map(([k, v]) => `<span>${k}</span><span>${v}</span>`).join('')}</div>
    ${c.error ? `<div class="tn" style="color:var(--bad);margin-top:4px">${esc(String(c.error).slice(0, 160))}</div>` : ''}`;
  tip.classList.add('on');
  moveTip(ev);
}
function moveTip(ev) {
  const pad = 14, r = tip.getBoundingClientRect();
  let x = ev.clientX + pad, y = ev.clientY + pad;
  if (x + r.width > innerWidth - 8) x = ev.clientX - r.width - pad;
  if (y + r.height > innerHeight - 8) y = ev.clientY - r.height - pad;
  tip.style.left = x + 'px';
  tip.style.top = y + 'px';
}
const hideTip = () => tip.classList.remove('on');
function bindTip(node, c) {
  node.addEventListener('mouseenter', (e) => showTip(c, e));
  node.addEventListener('mousemove', moveTip);
  node.addEventListener('mouseleave', hideTip);
  node.addEventListener('click', () => select(c.id));
}

// ---------------------------------------------------------------- top bar
function genTotal() {
  const cfg = S.config || {};
  return cfg.generations || Math.max(0, ...(S.generations || []).map((g) => g.gen));
}
function renderTop() {
  const cfg = S.config || {};
  const end = S.status === 'running' ? Date.now() / 1000 : S.updated || Date.now() / 1000;
  const elapsed = end - (S.started || end);
  $('#sub').textContent = `${cfg.storm || 'storm'} · budget ${money(cfg.budget_usd, { dp: 0 })} · ${cfg.population || '?'} per generation · ${cfg.sim_workers || '?'} parallel sims`;
  $('#budgetHint').textContent = Math.round((cfg.budget_usd || 50e6) / 1e6);
  const stage = S.status === 'running' ? S.stage : '';
  const label = { running: 'Running', completed: 'Completed', failed: 'Failed', awaiting_approval: 'Awaiting planner approval', approved: 'Approved by planner', rejected: 'Rejected by planner', interrupted: 'Interrupted' }[S.status] || S.status;
  $('#status').innerHTML = `<span class="pill ${esc(S.status)}"><span class="dot"></span>${esc(label)}
      <span class="muted">· gen ${S.generation ?? 0}/${genTotal()}${stage ? ' · ' + esc(stage) : ''}</span></span>
    ${cfg.mock_sim ? '<span class="badge mock" title="The real storm simulator was not available; candidates were scored by the mock bathtub model">Mock sim</span>' : ''}`;
  const nAll = D.cands.length, nOk = D.scored.length;
  $('#kpis').innerHTML = `
    <div class="kpi big"><span class="k">Best net benefit</span><span class="v">${D.best ? money(D.best.score.net_benefit_usd, { sign: true }) : '—'}${D.best ? `<small>${esc(D.best.id)} · BCR ${num(D.best.score.bcr, 2)}</small>` : ''}</span></div>
    <div class="kpi"><span class="k">No-plan damage</span><span class="v">${money(S.baseline?.damage_usd)}</span></div>
    <div class="kpi"><span class="k">Plans scored</span><span class="v">${nOk}<small>of ${nAll}</small></span></div>
    <div class="kpi hide-s"><span class="k">Models</span><span class="v models">${esc(modelName(cfg.architect_model))} <span style="color:var(--faint)">+</span> ${esc(modelName(cfg.mutator_model))}</span></div>
    <div class="kpi"><span class="k">Elapsed</span><span class="v">${dur(elapsed)}</span></div>`;
}
function renderLegend() {
  $('#legend').innerHTML = Object.values(OPS).map((o) => `<span><i style="background:${o.color}"></i>${o.label}</span>`).join('') +
    '<span><i class="ring"></i>Rejected</span>';
}

// ---------------------------------------------------------------- main chart
function renderMain() {
  const box = $('#mainChart');
  const { svg, W, H } = svgIn(box);
  const cands = D.cands;
  if (!cands.length) { el('text', { x: W / 2, y: H / 2, 'text-anchor': 'middle' }, svg).textContent = 'waiting for the first candidates…'; return; }
  const g0 = Math.min(...cands.map((c) => c.gen));
  const gMax = Math.max(...cands.map((c) => c.gen), genTotal());
  const gens = Array.from({ length: gMax - g0 + 1 }, (_, i) => g0 + i);
  const m = { l: 56, r: 16, t: 16, b: 62 };
  const plotB = H - m.b;
  const vals = D.scored.map((c) => c.score.net_benefit_usd);
  let lo = Math.min(0, ...vals), hi = Math.max(10e6, ...vals);
  const pad = (hi - lo) * 0.12;
  hi += pad; if (lo < 0) lo -= pad * 0.5;
  const yT = niceTicks(lo, hi, 5);
  lo = Math.min(lo, yT[0]); hi = Math.max(hi, yT[yT.length - 1]);
  const y = (v) => m.t + (1 - (v - lo) / (hi - lo)) * (plotB - m.t);
  const colW = (W - m.l - m.r) / gens.length;
  gens.forEach((g, i) => {
    const x0 = m.l + i * colW;
    if (i % 2 === 0) el('rect', { x: x0, y: m.t, width: colW, height: H - m.t - 22, class: 'genband' }, svg);
    const hasSeeds = cands.some((c) => c.gen === g && c.op === 'seed');
    el('text', { x: x0 + colW / 2, y: H - 6, 'text-anchor': 'middle', class: 'genlabel' }, svg).textContent = hasSeeds && g === g0 ? 'Seeds' : `Gen ${g}`;
  });
  yT.forEach((v) => {
    el('line', { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), class: v === 0 ? 'zero' : 'gridline' }, svg);
    el('text', { x: m.l - 8, y: y(v) + 4, 'text-anchor': 'end' }, svg).textContent = tickM(v);
  });
  const laneP = plotB + 16, laneR = plotB + 34;
  el('line', { x1: m.l, x2: W - m.r, y1: plotB + 5, y2: plotB + 5, class: 'gridline' }, svg);
  el('text', { x: m.l - 8, y: laneP + 4, 'text-anchor': 'end', class: 'lane-label' }, svg).textContent = 'in flight';
  el('text', { x: m.l - 8, y: laneR + 4, 'text-anchor': 'end', class: 'lane-label' }, svg).textContent = 'rejected';
  const pos = new Map();
  gens.forEach((g, i) => {
    const inGen = cands.filter((c) => c.gen === g);
    const n = inGen.length;
    inGen.forEach((c, k) => {
      const x = m.l + i * colW + colW * ((k + 1) / (n + 1));
      const yy = D.ok(c) ? y(c.score.net_benefit_usd) : PENDING.has(c.status) ? laneP : laneR;
      pos.set(c.id, [x, yy]);
    });
  });
  // best-so-far step line
  const ordered = cands.filter(D.ok).sort((a, b) => pos.get(a.id)[0] - pos.get(b.id)[0]);
  let bestV = -Infinity, d = '';
  ordered.forEach((c) => {
    const [x] = pos.get(c.id);
    if (c.score.net_benefit_usd > bestV) {
      d += isFinite(bestV) ? `H${x}V${y(c.score.net_benefit_usd)}` : `M${x},${y(c.score.net_benefit_usd)}`;
      bestV = c.score.net_benefit_usd;
    }
  });
  if (d) el('path', { d: d + `H${W - m.r}`, class: 'bestline' }, svg);
  // lineage of the selection
  const sel = selId && D.byId.get(selId);
  const lineIds = new Set();
  if (sel) {
    const L = lineage(sel);
    L.forEach((c) => lineIds.add(c.id));
    const pts = L.map((c) => pos.get(c.id)).filter(Boolean);
    if (pts.length > 1) el('path', { d: 'M' + pts.map((p) => p.join(',')).join('L'), class: 'lineage' }, svg);
  }
  const r = Math.max(4.5, Math.min(7, colW / 16));
  cands.forEach((c) => {
    const [x, yy] = pos.get(c.id);
    const op = opOf(c);
    let cls = 'pt';
    if (PENDING.has(c.status)) cls += ' pending';
    else if (!D.ok(c)) cls += ' reject';
    if (!seen.has(c.id) && !firstRender) cls += ' new';
    if (c.id === selId) cls += ' sel';
    else if (sel && !lineIds.has(c.id)) cls += ' dim';
    const isBest = D.best && c.id === D.best.id;
    const dot = el('circle', { cx: x, cy: yy, r: isBest ? r + 1.5 : r, fill: op.hex, stroke: PENDING.has(c.status) ? op.hex : 'rgba(7,11,17,.9)', 'stroke-width': 1.2, class: cls }, svg);
    bindTip(dot, c);
  });
  if (D.best) {
    const [x, yy] = pos.get(D.best.id);
    el('circle', { cx: x, cy: yy, r: r + 1.5, class: 'selring', style: 'stroke:var(--good)' }, svg);
    const right = x + 130 < W;
    el('text', { x: right ? x + 12 : x - 12, y: yy - 9, 'text-anchor': right ? 'start' : 'end', style: 'fill:var(--good);font-weight:600' }, svg).textContent = `best ${money(D.best.score.net_benefit_usd, { sign: true })}`;
  }
  if (sel && pos.get(sel.id) && sel !== D.best) {
    const [x, yy] = pos.get(sel.id);
    el('circle', { cx: x, cy: yy, r: r + 2, class: 'selring' }, svg);
  }
}

// ---------------------------------------------------------------- pareto
function renderPareto() {
  const box = $('#pareto');
  const { svg, W, H } = svgIn(box);
  const budget = S.config?.budget_usd || 50e6;
  const pts = D.cands.filter((c) => c.score && isFinite(c.score.plan_cost_usd) && isFinite(c.score.damage_avoided_usd));
  const m = { l: 52, r: 14, t: 14, b: 30 };
  const xMax = Math.max(budget * 1.2, ...pts.map((c) => c.score.plan_cost_usd)) * 1.04;
  const yMax = Math.max(budget * 1.5, ...pts.map((c) => c.score.damage_avoided_usd)) * 1.08;
  const xT = niceTicks(0, xMax, 5), yT = niceTicks(0, yMax, 4);
  const X1 = Math.max(xMax, xT.at(-1)), Y1 = Math.max(yMax, yT.at(-1));
  const x = (v) => m.l + (v / X1) * (W - m.l - m.r);
  const y = (v) => H - m.b - (v / Y1) * (H - m.t - m.b);
  yT.forEach((v) => { el('line', { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), class: 'gridline' }, svg); el('text', { x: m.l - 8, y: y(v) + 4, 'text-anchor': 'end' }, svg).textContent = tickM(v); });
  xT.forEach((v) => { el('text', { x: x(v), y: H - m.b + 16, 'text-anchor': 'middle' }, svg).textContent = tickM(v); });
  el('text', { x: W - m.r, y: H - 1, 'text-anchor': 'end', class: 'lane-label' }, svg).textContent = 'plan cost →';
  el('text', { x: m.l + 6, y: m.t + 4, class: 'lane-label' }, svg).textContent = '↑ damage avoided';
  const d1 = Math.min(X1, Y1);
  el('line', { x1: x(0), y1: y(0), x2: x(d1), y2: y(d1), class: 'bcrline' }, svg);
  el('text', { x: x(d1) - 4, y: y(d1) - 6, 'text-anchor': 'end', class: 'lane-label' }, svg).textContent = 'BCR 1';
  el('line', { x1: x(budget), x2: x(budget), y1: m.t, y2: H - m.b, class: 'budgetline' }, svg);
  el('text', { x: x(budget) - 5, y: H - m.b - 6, 'text-anchor': 'end', style: 'fill:var(--warn)', class: 'lane-label' }, svg).textContent = `budget ${money(budget, { dp: 0 })}`;
  const okp = pts.filter((c) => D.ok(c)).sort((a, b) => a.score.plan_cost_usd - b.score.plan_cost_usd);
  const front = [];
  let bestA = -Infinity;
  okp.forEach((c) => { if (c.score.damage_avoided_usd > bestA) { bestA = c.score.damage_avoided_usd; front.push(c); } });
  if (front.length) {
    let d = `M${x(front[0].score.plan_cost_usd)},${y(0)}V${y(front[0].score.damage_avoided_usd)}`;
    front.slice(1).forEach((c) => { d += `H${x(c.score.plan_cost_usd)}V${y(c.score.damage_avoided_usd)}`; });
    d += `H${x(budget)}`;
    el('path', { d: d + `V${y(0)}Z`, class: 'pareto-area' }, svg);
    el('path', { d, class: 'pareto' }, svg);
  }
  const frontIds = new Set(front.map((c) => c.id));
  pts.forEach((c) => {
    const op = opOf(c);
    const cls = 'pt' + (D.ok(c) ? '' : ' reject') + (c.id === selId ? ' sel' : '') + (!seen.has(c.id) && !firstRender ? ' new' : '');
    const onF = frontIds.has(c.id);
    const dot = el('circle', { cx: x(c.score.plan_cost_usd), cy: y(c.score.damage_avoided_usd), r: onF ? 5.5 : 4, fill: op.hex, 'fill-opacity': onF ? 1 : 0.55, stroke: 'rgba(7,11,17,.9)', 'stroke-width': 1, class: cls }, svg);
    bindTip(dot, c);
  });
}

// ---------------------------------------------------------------- archive
function renderArchive() {
  const box = $('#archive');
  const A = S.archive || {};
  const strats = A.strategies || ['grey', 'green', 'building', 'response', 'trees', 'mixed'];
  const bands = A.cost_bands || [[0, 10e6], [10e6, 25e6], [25e6, 40e6], [40e6, 50e6]];
  const cells = A.cells || {};
  box.style.gridTemplateColumns = `auto repeat(${bands.length}, minmax(0, 1fr))`;
  box.style.gridTemplateRows = `auto repeat(${strats.length}, minmax(0, 1fr))`;
  const nets = Object.values(cells).map((id) => D.byId.get(id)).filter((c) => c && c.score).map((c) => c.score.net_benefit_usd);
  const maxN = Math.max(1, ...nets), minN = Math.min(0, ...nets);
  const bl = ([a, b]) => (a <= 0 ? `< ${money(b, { dp: 0 })}` : `${money(a, { dp: 0 })}–${Math.round(b / 1e6)}M`);
  let html = '<div></div>' + bands.map((b) => `<div class="ar-h">${bl(b)}</div>`).join('');
  strats.forEach((s) => {
    html += `<div class="ar-row" title="${esc(stratLabel(s))}"><span class="sw" style="background:${STRAT_COLORS[s] || '#8fa1b6'}"></span>${esc(stratLabel(s))}</div>`;
    bands.forEach((_, bi) => {
      const key = `${s}|${bi}`;
      const c = cells[key] && D.byId.get(cells[key]);
      if (!c || !c.score) { html += '<div class="ar-cell empty"></div>'; return; }
      const v = c.score.net_benefit_usd;
      const t = (v - minN) / (maxN - minN || 1);
      const a = 0.07 + 0.55 * Math.pow(Math.max(0, t), 1.1);
      const bg = v >= 0 ? `rgba(95,225,255,${a.toFixed(3)})` : 'rgba(255,93,108,0.16)';
      const changed = cellPrev[key] && cellPrev[key] !== c.id;
      cellPrev[key] = c.id;
      html += `<div class="ar-cell${D.best && c.id === D.best.id ? ' best' : ''}${c.id === selId ? ' sel' : ''}${changed ? ' flash' : ''}" data-id="${esc(c.id)}" style="background:${bg}">
        <span class="n">${money(v, { sign: true })}</span><span class="id">${esc(c.id)}</span></div>`;
    });
  });
  box.innerHTML = html;
  box.querySelectorAll('.ar-cell[data-id]').forEach((n) => bindTip(n, D.byId.get(n.dataset.id)));
}

// ---------------------------------------------------------------- leaderboard
function chip(c) {
  const op = opOf(c);
  return `<span class="chip"><i style="background:${op.color}"></i>${esc(op.label)}</span>`;
}
function renderBoard() {
  const top = [...D.scored].sort((a, b) => b.score.net_benefit_usd - a.score.net_benefit_usd).slice(0, 10);
  if (!top.length) { $('#board').innerHTML = '<div class="empty-note">No scored plans yet.</div>'; return; }
  $('#board').innerHTML = `<table><thead><tr><th>#</th><th>Plan</th><th>Gen</th><th>Cost</th><th>Avoided</th><th>Net</th><th>BCR</th></tr></thead><tbody>
    ${top.map((c, i) => `<tr data-id="${esc(c.id)}" class="${c.id === selId ? 'sel' : ''}"><td class="rank">${i + 1}</td>
      <td><span class="mono" style="color:#fff;margin-right:6px">${esc(c.id)}</span>${chip(c)}</td><td>${c.gen}</td>
      <td>${money(c.score.plan_cost_usd)}</td><td>${money(c.score.damage_avoided_usd)}</td>
      <td class="net">${money(c.score.net_benefit_usd, { sign: true })}</td><td>${num(c.score.bcr, 1)}</td></tr>`).join('')}
  </tbody></table>`;
  $('#board').querySelectorAll('tr[data-id]').forEach((tr) => tr.addEventListener('click', () => select(tr.dataset.id)));
}

// ---------------------------------------------------------------- generations
function renderGens() {
  const gens = [...(S.generations || [])].sort((a, b) => b.gen - a.gen);
  if (!gens.length) { $('#gens').innerHTML = '<div class="empty-note">Argon writes its first directions after the seeds are scored.</div>'; return; }
  $('#gens').innerHTML = gens.map((g) => {
    const best = g.best_id && D.byId.get(g.best_id);
    const meta = `${g.n_scored ?? 0} scored${g.n_over_budget ? ` · ${g.n_over_budget} over` : ''}${g.n_failed ? ` · ${g.n_failed} failed` : ''}`;
    const isSeed = g.gen === 0 && !(g.directions || []).length;
    const bestNet = g.best_net_usd ?? best?.score?.net_benefit_usd;
    return `<div class="gen">
      <div class="gen-h"><b>${isSeed ? 'Seeds' : `Generation ${g.gen}`}</b>${!isSeed ? `<span class="chip"><i style="background:var(--argon)"></i>${esc(modelName(g.architect_model || S.config?.architect_model))}</span>` : ''}
        <span class="meta">${meta}${best ? ` · best <a href="#" data-id="${esc(best.id)}" class="glink">${money(bestNet, { sign: true })}</a>` : ''}</span></div>
      ${(g.insights || []).length ? `<div class="ins">${g.insights.map((t) => `<p>${esc(t)}</p>`).join('')}</div>` : ''}
      ${(g.directions || []).length ? `<ol>${g.directions.map((t) => `<li>${esc(t)}</li>`).join('')}</ol>` : isSeed ? `<div class="seeds">${D.cands.filter((c) => c.gen === 0).length} seed plans, one per strategy.</div>` : '<div class="empty-note">Argon is reading the archive…</div>'}
    </div>`;
  }).join('');
  $('#gens').querySelectorAll('a[data-id]').forEach((a) => a.addEventListener('click', (e) => { e.preventDefault(); select(a.dataset.id); }));
}

// ---------------------------------------------------------------- detail panel
async function fetchCode(url) {
  if (!url) return null;
  if (codeCache.has(url)) return codeCache.get(url);
  const p = fetch(url).then((r) => (r.ok ? r.text() : null)).catch(() => null);
  codeCache.set(url, p);
  const v = await p;
  if (v == null) codeCache.delete(url); // retry later (file may not exist yet)
  return v;
}
function lcsDiff(a, b) {
  const n = a.length, m = b.length;
  if (n * m > 4e6) return null;
  const dp = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { out.push({ t: ' ', s: b[j], no: j + 1 }); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { out.push({ t: '-', s: a[i] }); i++; }
    else { out.push({ t: '+', s: b[j], no: j + 1 }); j++; }
  }
  while (i < n) out.push({ t: '-', s: a[i++] });
  while (j < m) { out.push({ t: '+', s: b[j], no: j + 1 }); j++; }
  return out;
}
const lineHtml = (l) => `<div class="ln ${l.t === '+' ? 'add' : l.t === '-' ? 'del' : ''}"><span class="no">${l.no ?? ''}</span><span class="sg">${l.t === ' ' ? '' : l.t}</span><span class="tx">${esc(l.s) || ' '}</span></div>`;
function renderCode(lines, ctx = 3) {
  const keep = new Array(lines.length).fill(false);
  const anyChange = lines.some((l) => l.t !== ' ');
  lines.forEach((l, i) => { if (l.t !== ' ') for (let k = Math.max(0, i - ctx); k <= Math.min(lines.length - 1, i + ctx); k++) keep[k] = true; });
  let html = '', i = 0;
  while (i < lines.length) {
    if (anyChange && !keep[i]) {
      let j = i; while (j < lines.length && !keep[j]) j++;
      html += `<div class="ln fold" data-from="${i}" data-to="${j}">⋯ ${j - i} unchanged line${j - i === 1 ? '' : 's'}</div>`;
      i = j; continue;
    }
    html += lineHtml(lines[i]);
    i++;
  }
  return html;
}

let detailToken = 0;
async function renderDetail() {
  const box = $('#detail');
  const c = selId && D.byId.get(selId);
  if (!c) {
    box.innerHTML = '<div class="d-empty"><div style="font-size:14px;color:var(--dim)">Select a candidate</div><div>Click any dot, archive cell or leaderboard row<br/>to see its plan, lineage and code diff.</div></div>';
    return;
  }
  const sc = c.score || {};
  const isBest = D.best && c.id === D.best.id;
  const L = lineage(c);
  const base = S.baseline || {};
  const causes = Object.keys({ ...(base.by_cause || {}), ...(c.by_cause || {}) });
  const maxCause = Math.max(1, ...causes.map((k) => Math.max(base.by_cause?.[k] || 0, c.by_cause?.[k] || 0)));
  const cbs = c.cost_by_strategy || {};
  const cbsTot = Object.values(cbs).reduce((a, b) => a + (b || 0), 0);
  const cbsSorted = Object.entries(cbs).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);

  const lineageHtml = L.map((n, i) => {
    const o = opOf(n);
    const v = n.score && n.status === 'scored' ? n.score.net_benefit_usd : null;
    const extra = n.parents && n.parents.length > 1 ? `<span class="xp">× ${n.parents.slice(1).map(esc).join(', ')}</span>` : '';
    return `<div class="tnode ${i === 0 ? 'cur' : ''}" data-id="${esc(n.id)}"><span class="knob" style="background:${o.color}"></span>
      <span class="tx"><span class="mono">${esc(n.id)}</span><span class="nt">${esc(n.note || o.label)}</span>${extra}</span>
      <span class="nv ${v == null || v < 0 ? 'neg' : ''}">${v == null ? esc(n.status.replace('_', ' ')) : money(v, { sign: true })}</span></div>`;
  }).join('');

  const scores = c.score ? `<div class="scores">
      <div class="score hl"><div class="k">Net benefit</div><div class="v">${money(sc.net_benefit_usd, { sign: true })}</div></div>
      <div class="score"><div class="k">Plan cost</div><div class="v" style="${sc.within_budget === false ? 'color:var(--bad)' : ''}">${money(sc.plan_cost_usd)}</div></div>
      <div class="score"><div class="k">Damage avoided</div><div class="v">${money(sc.damage_avoided_usd)}</div></div>
      <div class="score"><div class="k">Benefit–cost ratio</div><div class="v">${num(sc.bcr, 2)}</div></div>
      <div class="score"><div class="k">At-risk person-hours</div><div class="v">${num(sc.at_risk_person_hours)}</div></div>
      <div class="score"><div class="k">Expected deaths</div><div class="v">${num(sc.expected_fatalities, 2)}</div></div></div>` : '';

  const causeHtml = c.by_cause && Object.keys(c.by_cause).length ? `<div class="d-sec"><div class="lab">Damage by cause<span class="r leg2"><i class="b"></i>no plan <i class="n"></i>this plan</span></div><div class="bars">
      ${causes.sort((a, b) => (base.by_cause?.[b] || 0) - (base.by_cause?.[a] || 0)).map((k) => `<div class="bar-row"><span class="nm">${esc(CAUSE_LABELS[k] || k.replace(/_/g, ' '))}</span>
        <span class="bar-track"><span class="bar-base" style="width:${(100 * (base.by_cause?.[k] || 0)) / maxCause}%"></span><span class="bar-now" style="width:${(100 * (c.by_cause?.[k] || 0)) / maxCause}%"></span></span>
        <span class="val">${money(c.by_cause?.[k] || 0)}</span></div>`).join('')}</div></div>` : '';

  const spendHtml = cbsTot > 0 ? `<div class="d-sec"><div class="lab">Spend by strategy<span class="r">${money(cbsTot)}</span></div>
      <div class="stack">${cbsSorted.map(([k, v]) => `<span style="width:${(100 * v) / cbsTot}%;background:${STRAT_COLORS[k] || '#8fa1b6'}" title="${esc(stratLabel(k))} ${money(v)}"></span>`).join('')}</div>
      <div class="stack-leg">${cbsSorted.map(([k, v]) => `<span><i style="background:${STRAT_COLORS[k] || '#8fa1b6'}"></i>${esc(stratLabel(k))} ${money(v)}</span>`).join('')}</div></div>` : '';

  const can3d = !!c.run_id;
  box.innerHTML = `
    <div class="d-h"><span class="cid">${esc(c.id)}</span>${chip(c)}<span class="chip">${esc(modelName(c.model))}</span><span class="chip">gen ${c.gen}</span>${isBest ? '<span class="best-tag">★ Best</span>' : ''}</div>
    ${c.note ? `<div class="d-note">${esc(c.note)}</div>` : ''}
    ${c.directive ? `<div class="d-dir"><b>Directive</b>${esc(c.directive)}</div>` : ''}
    ${c.error ? `<div class="err">${esc(c.error)}</div>` : ''}
    ${PENDING.has(c.status) ? `<div class="d-dir live">● ${esc(c.status)}…</div>` : ''}
    ${scores}
    ${isBest ? approvalHtml() : ''}
    <div class="btns">
      <button class="btn" id="btnVideo" ${c.video_url ? '' : 'disabled'}>▶ Watch replay</button>
      <a class="btn" ${can3d ? `href="/?a=${encodeURIComponent(base.run_id || '')}&c=${encodeURIComponent(c.run_id)}" target="_blank"` : 'disabled'}>Open in 3D ↗</a>
      ${c.code_url ? `<a class="btn ghost" href="${esc(c.code_url)}" target="_blank">plan.py</a>` : ''}
    </div>
    <div class="d-sec"><div class="lab">Lineage<span class="r faint">${L.length - 1} ancestor${L.length === 2 ? '' : 's'}</span></div><div class="tree">${lineageHtml}</div></div>
    ${causeHtml}
    ${spendHtml}
    <div class="d-sec"><div class="lab">Plan code<span class="r codebar"><span class="dstat" id="dstat"></span>
      <span class="seg"><button data-m="diff" class="${diffMode === 'diff' ? 'on' : ''}">Diff vs parent</button><button data-m="full" class="${diffMode === 'full' ? 'on' : ''}">Full</button></span></span></div>
      <div class="code" id="code"><div class="ln"><span class="no"></span><span class="sg"></span><span class="tx faint">loading…</span></div></div></div>`;

  box.querySelectorAll('.tnode[data-id]').forEach((n) => { if (n.dataset.id !== c.id) n.addEventListener('click', () => select(n.dataset.id)); });
  box.querySelectorAll('.seg button').forEach((b) => b.addEventListener('click', () => { diffMode = b.dataset.m; lastDetailKey = ''; renderDetail(); }));
  $('#btnVideo').addEventListener('click', () => openVideo(c));
  box.querySelectorAll('[data-approve]').forEach((b) => b.addEventListener('click', () => approve(b.dataset.approve === '1')));

  const tok = ++detailToken;
  const parent = c.parents && c.parents.length ? D.byId.get(c.parents[0]) : null;
  const [code, pcode] = await Promise.all([fetchCode(c.code_url), diffMode === 'diff' && parent ? fetchCode(parent.code_url) : null]);
  if (tok !== detailToken) return;
  const codeBox = $('#code');
  if (!codeBox) return;
  if (code == null) {
    codeBox.innerHTML = `<div class="ln"><span class="no"></span><span class="sg"></span><span class="tx faint">${c.status === 'writing' ? 'the model is still writing this plan…' : 'code not available'}</span></div>`;
    return;
  }
  const b = code.replace(/\s+$/, '').split('\n');
  let lines;
  if (diffMode === 'diff' && pcode != null) {
    lines = lcsDiff(pcode.replace(/\s+$/, '').split('\n'), b) || b.map((s, i) => ({ t: ' ', s, no: i + 1 }));
    const add = lines.filter((l) => l.t === '+').length, del = lines.filter((l) => l.t === '-').length;
    $('#dstat').innerHTML = `vs <span class="mono">${esc(parent.id)}</span> <span class="a">+${add}</span> <span class="d">−${del}</span>`;
  } else {
    lines = b.map((s, i) => ({ t: ' ', s, no: i + 1 }));
    $('#dstat').textContent = `${b.length} lines${diffMode === 'diff' && !parent ? ' · seed' : ''}`;
  }
  codeBox.innerHTML = renderCode(lines);
  codeBox.querySelectorAll('.ln.fold').forEach((f) => f.addEventListener('click', () => {
    f.outerHTML = lines.slice(+f.dataset.from, +f.dataset.to).map(lineHtml).join('');
  }));
}

function openVideo(c) {
  if (!c.video_url) return;
  const base = S.baseline || {};
  $('#modalBox').innerHTML = `<h3><span class="mono">${esc(c.id)}</span> · storm replay <span class="faint" style="font-weight:400">${esc(c.note || '')}</span></h3>
    <video src="${esc(c.video_url)}" controls autoplay muted playsinline></video>
    <div class="form-actions">${base.video_url ? '<button class="btn" id="vbase">Watch the no-plan run</button>' : ''}<button class="btn primary" id="vclose">Close</button></div>`;
  $('#modal').classList.remove('hidden');
  $('#vclose').addEventListener('click', closeModal);
  const vb = $('#vbase');
  if (vb) vb.addEventListener('click', () => { $('#modalBox video').src = base.video_url; });
}
function closeModal() { $('#modal').classList.add('hidden'); $('#modalBox').innerHTML = ''; }
$('#modal').addEventListener('click', (e) => { if (e.target.id === 'modal') closeModal(); });
addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

// ---------------------------------------------------------------- architecture
function archCounts() {
  const c = { argon: 0, flash: 0, tweak: 0, check: 0, sim: 0 };
  D.cands.forEach((x) => {
    if (x.status === 'writing') {
      if (!x.model) c.tweak++;
      else if (/argon/.test(x.model)) c.argon++;
      else c.flash++;
    } else if (x.status === 'checking') c.check++;
    else if (x.status === 'simulating') c.sim++;
  });
  return c;
}
function renderArch() {
  const box = $('#arch');
  box.innerHTML = '';
  const W = 1290, H = 548;
  const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'xMidYMid meet' });
  box.appendChild(svg);
  const defs = el('defs', {}, svg);
  [['ah', 'rgba(95,225,255,.6)'], ['ahg', 'rgba(93,255,157,.65)'], ['ahp', 'rgba(180,140,255,.7)']].forEach(([id, col]) => {
    const mk = el('marker', { id, viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 6.5, markerHeight: 6.5, orient: 'auto-start-reverse' }, defs);
    el('path', { d: 'M0,0L10,5L0,10z', fill: col }, mk);
  });
  const cnt = archCounts();
  const st = S.stage;
  const running = S.status === 'running';
  const N = S.config?.population || 8;
  const nScored = D.scored.length;
  const nCells = Object.keys(S.archive?.cells || {}).length;
  const llmOn = cnt.argon + cnt.flash + cnt.tweak > 0;
  const nodes = {
    sampler: { x: 30, y: 96, w: 270, h: 100, k: 'Prompt sampler', t: 'Parent + inspirations', s: ['a parent from an archive cell, the top', 'elites as inspiration, their ledgers'], c: '#5fe1ff', on: running && (llmOn || st === 'architect' || st === 'mutating') },
    db: { x: 30, y: 352, w: 270, h: 112, k: 'Program database', t: 'MAP-Elites archive', s: [`${nCells} elite cells · ${nScored} scored plans`, 'best plan per strategy × cost band'], c: '#5dff9d', on: running && st === 'selecting', n: nCells },
    argon: { x: 400, y: 40, w: 310, h: 100, k: 'Architect', t: modelName(S.config?.architect_model || 'gemini-4-argon'), s: ['reads the whole archive, writes research', 'directions and a crossover plan'], c: '#b48cff', on: running && (cnt.argon > 0 || st === 'architect'), n: cnt.argon },
    flash: { x: 400, y: 166, w: 310, h: 100, k: `Mutators × ${Math.max(1, N - 2)} in parallel`, t: modelName(S.config?.mutator_model || 'gemini-3.8-flash'), s: ['SEARCH/REPLACE diffs on the parent', 'plan, one directive each'], c: '#5fe1ff', on: running && cnt.flash > 0, n: cnt.flash },
    tweak: { x: 400, y: 292, w: 310, h: 78, k: 'Hill-climb', t: 'Numeric tweak', s: ['no LLM: perturbs sizes, heights, flows'], c: '#ffb54a', on: running && cnt.tweak > 0, n: cnt.tweak },
    sandbox: { x: 800, y: 70, w: 210, h: 100, k: 'Isolation', t: 'Sandbox', s: ['Landlock + seccomp,', 'no network, no secrets'], c: '#5fe1ff', on: running && cnt.check > 0, n: cnt.check },
    cost: { x: 1050, y: 70, w: 210, h: 100, k: 'Locked', t: 'Cost planner', s: ['prices the physical diff', 'from cited unit costs'], c: '#ffb54a', on: running && cnt.check > 0 },
    sim: { x: 800, y: 300, w: 460, h: 128, k: 'Deterministic evaluator', t: 'Storm simulation', s: ['shallow-water flood on 4 m lidar · LBM wind', 'tree failure · people & response · Hazus damage', 'same plan → same ledger, every time'], c: '#5fe1ff', on: running && cnt.sim > 0, n: cnt.sim },
    eop: { x: 400, y: 446, w: 340, h: 78, k: 'People stay in control', t: 'Planner approval', s: ['the planner approves the best plan', 'before anything is built or spent'], c: '#5dff9d', on: DONE.has(S.status) },
  };
  const simOut = running && (cnt.sim > 0 || st === 'selecting');
  const edges = [
    { d: 'M165,350 L165,198', on: nodes.sampler.on, c: '#5fe1ff', label: 'elites', lx: 176, ly: 278 },
    { d: 'M302,130 C350,130 350,90 398,90', on: nodes.argon.on, c: '#b48cff', m: 'ahp' },
    { d: 'M302,146 C350,146 350,216 398,216', on: cnt.flash > 0 && running, c: '#5fe1ff' },
    { d: 'M302,162 C342,162 350,331 398,331', on: cnt.tweak > 0 && running, c: '#ffb54a' },
    { d: 'M712,90 C756,90 756,104 798,104', on: cnt.check > 0 && running, c: '#5fe1ff' },
    { d: 'M712,216 C756,216 756,126 798,126', on: cnt.check > 0 && running, c: '#5fe1ff' },
    { d: 'M712,331 C770,331 760,148 798,148', on: cnt.check > 0 && running, c: '#ffb54a' },
    { d: 'M1012,120 L1048,120', on: cnt.check > 0 && running, c: '#ffb54a' },
    { d: 'M1155,172 L1155,298', on: cnt.sim > 0 && running, c: '#5fe1ff', label: 'within budget', lx: 1166, ly: 240 },
    { d: 'M798,384 C620,384 480,408 302,408', on: simOut, c: '#5dff9d', m: 'ahg', label: 'ledger + score', lx: 480, ly: 392 },
    { d: 'M240,466 C240,486 330,486 398,486', on: DONE.has(S.status), c: '#5dff9d', m: 'ahg', exit: true, label: 'best plan', lx: 270, ly: 504 },
    { d: 'M555,38 C555,8 110,8 110,94', on: nodes.argon.on, c: '#b48cff', m: 'ahp', thin: true, label: 'reads every plan so far', lx: 150, ly: 58 },
  ];
  edges.forEach((e) => {
    const p = el('path', { d: e.d, class: 'aedge' + (e.on ? ' on' : '') + (e.exit ? ' exit' : ''), style: `--c:${e.c}`, 'marker-end': `url(#${e.m || 'ah'})` }, svg);
    if (e.thin && !e.on) p.setAttribute('stroke-dasharray', '2 5');
    if (e.label) el('text', { x: e.lx, y: e.ly, class: 'alabel' }, svg).textContent = e.label;
  });
  Object.values(nodes).forEach((n) => {
    const g = el('g', { class: 'anode' + (n.on ? ' on' : ''), style: `--c:${n.c};--glow:${n.c}66`, transform: `translate(${n.x},${n.y})` }, svg);
    el('rect', { class: 'box', width: n.w, height: n.h, rx: 10 }, g);
    el('rect', { class: 'stripe', x: 0, y: 14, width: 3, height: n.h - 28, rx: 1.5 }, g);
    el('text', { class: 'k', x: 18, y: 25 }, g).textContent = n.k;
    el('text', { class: 't', x: 18, y: 46 }, g).textContent = n.t;
    n.s.forEach((s, i) => { el('text', { class: 's', x: 18, y: 66 + i * 16 }, g).textContent = s; });
    if (n.n) {
      const cg = el('g', { class: 'cnt', transform: `translate(${n.w - 12},12)` }, g);
      const label = String(n.n);
      const w = 14 + label.length * 7;
      el('rect', { x: -w, y: 0, width: w, height: 20, rx: 10 }, cg);
      el('text', { x: -w / 2, y: 14, 'text-anchor': 'middle' }, cg).textContent = label;
    }
  });
  renderUsage();
}
function renderUsage() {
  const rows = Object.entries(S.usage || {});
  if (!rows.length) { $('#usage').innerHTML = '<div class="empty-note">No model calls yet.</div>'; return; }
  const price = { 'gemini-4-argon': [2, 10] };
  let tot = 0, any = false;
  $('#usage').innerHTML = `<table class="usage-t"><thead><tr><th>Model</th><th>Calls</th><th>Input</th><th>Output</th><th>Est. cost</th></tr></thead><tbody>
    ${rows.map(([m, v]) => {
      const p = price[m];
      const cost = p ? ((v.input_tokens || 0) * p[0] + (v.output_tokens || 0) * p[1]) / 1e6 : null;
      if (cost != null) { tot += cost; any = true; }
      return `<tr><td>${esc(modelName(m))}</td><td>${num(v.calls)}</td><td>${num((v.input_tokens || 0) / 1e3)}k</td><td>${num((v.output_tokens || 0) / 1e3)}k</td><td>${cost != null ? '$' + cost.toFixed(2) : '—'}</td></tr>`;
    }).join('')}</tbody></table>
    <div class="usage-note">Argon at list price, $2 / $10 per 1M tokens, before implicit-cache discounts.${any ? ` Argon total ≈ $${tot.toFixed(2)}.` : ''}</div>`;
}

// ---------------------------------------------------------------- log
let logOpen = false;
const DONE = new Set(['completed', 'awaiting_approval', 'approved', 'rejected']);
const KIND_COLORS = { architect: 'var(--argon)', insight: 'var(--argon)', score: 'var(--good)', best: 'var(--good)', elite: 'var(--good)', fail: 'var(--bad)', reject: 'var(--warn)', error: 'var(--bad)', api_error: 'var(--bad)', sim_error: 'var(--bad)', over: 'var(--warn)', repair: 'var(--warn)', info: 'var(--accent)', start: 'var(--accent)', generation: 'var(--accent)', video: 'var(--accent)', stop: 'var(--warn)' };
function renderLog() {
  const L = [...(S.log || [])].reverse();
  const items = (logOpen ? L : L.slice(0, 6)).map((l) => `<span class="le"><span class="tm">${clock(l.t)}</span><span class="kd" style="color:${KIND_COLORS[l.kind] || 'var(--dim)'}">${esc(l.kind)}</span><span class="tx">${esc(l.text)}</span></span>`).join('');
  $('#log').innerHTML = (logOpen ? '' : '<span class="lab">Log</span>') + (items || '<span class="le">no events yet</span>');
  $('#log').classList.toggle('open', logOpen);
}
$('#log').addEventListener('click', () => { logOpen = !logOpen; renderLog(); });

// ---------------------------------------------------------------- selection + tabs
function select(id) {
  selId = id;
  const u = new URL(location.href);
  if (id) u.searchParams.set('cand', id); else u.searchParams.delete('cand');
  history.replaceState(null, '', u);
  hideTip();
  renderAll();
}
function setTab(t) {
  tab = t;
  document.querySelectorAll('.tab').forEach((x) => x.classList.toggle('active', x.dataset.tab === t));
  $('#view-evolution').classList.toggle('hidden', t !== 'evolution');
  $('#view-architecture').classList.toggle('hidden', t !== 'architecture');
  const u = new URL(location.href); u.searchParams.set('tab', t); history.replaceState(null, '', u);
  renderAll();
}
document.querySelectorAll('.tab').forEach((b) => b.addEventListener('click', () => setTab(b.dataset.tab)));

let lastDetailKey = '';
function renderAll() {
  if (!S) return;
  D = derive(S);
  if (!selId && D.best) selId = D.best.id;
  renderTop();
  if (tab === 'evolution') { renderMain(); renderPareto(); renderArchive(); renderBoard(); renderGens(); }
  else renderArch();
  const c = selId && D.byId.get(selId);
  const key = c ? `${c.id}|${c.status}|${c.score?.net_benefit_usd}|${diffMode}|${D.best?.id}` : '';
  if (key !== lastDetailKey) { lastDetailKey = key; renderDetail(); }
  renderLog();
  D.cands.forEach((x) => seen.add(x.id));
  firstRender = false;
  document.documentElement.dataset.ready = 'true';
}

// ---------------------------------------------------------------- data loading
async function getJSON(url, opt) {
  const r = await fetch(url, opt);
  if (!r.ok) throw new Error(`${r.status} ${(await r.text().catch(() => '')).slice(0, 160)}`);
  return r.json();
}
async function loadJobs() {
  const sel = $('#jobPicker');
  if (FIXTURE) { sel.innerHTML = '<option>fixture</option>'; sel.disabled = true; return []; }
  let list = [];
  try { list = await getJSON('/api/evolve'); } catch { list = []; }
  list = (Array.isArray(list) ? list : list.jobs || []).sort((a, b) => (b.started || 0) - (a.started || 0));
  sel.innerHTML = list.length ? list.map((j) => `<option value="${esc(j.id)}">${esc(j.id.replace(/^evolve-/, ''))} · ${esc(j.status)}${j.best_net_usd != null ? ' · ' + money(j.best_net_usd, { sign: true }) : ''}</option>`).join('') : '<option value="">no runs yet</option>';
  if (jobId) sel.value = jobId;
  return list;
}
$('#jobPicker').addEventListener('change', (e) => {
  if (!e.target.value) return;
  const u = new URL(location.href); u.searchParams.set('job', e.target.value); u.searchParams.delete('cand'); location.href = u;
});

async function poll() {
  clearTimeout(pollTimer);
  try {
    const st = FIXTURE ? await getJSON('./fixture.json') : await getJSON(`/api/evolve/${encodeURIComponent(jobId)}`);
    const changed = !S || st.updated !== S.updated || (st.candidates || []).length !== (S.candidates || []).length || st.status !== S.status;
    S = st;
    if (changed) renderAll(); else renderTop();
    if (S.status === 'running' && !FIXTURE) pollTimer = setTimeout(poll, POLL_MS);
    if (!FIXTURE && S.status !== 'running') loadJobs();
  } catch (e) {
    if (!S) showEmpty(`Could not load evolution run ${jobId || ''}: ${e.message}`);
    pollTimer = setTimeout(poll, POLL_MS * 3);
  }
}
function showEmpty(msg) {
  $('#sub').textContent = 'no evolution run loaded';
  $('#status').innerHTML = '';
  $('#kpis').innerHTML = '';
  $('#view-evolution').innerHTML = `<div class="card empty-card">
    <div style="font-size:15px">${esc(msg)}</div>
    <div class="faint">Start one with <b>New run</b>, or from the CLI:<br/><code>scripts/gem.sh .venv/bin/python -m agent.evolve --generations 4 --population 8</code></div></div>`;
  $('#detail').classList.add('hidden');
  document.body.classList.add('is-empty');
  document.documentElement.dataset.ready = 'true';
}

// ---------------------------------------------------------------- new run form
$('#newRunBtn').addEventListener('click', () => {
  const cfg = S?.config || {};
  $('#modalBox').innerHTML = `<h3>New evolution run</h3>
    <form class="form" id="nf" onsubmit="return false">
      <label>Storm<select name="storm"><option value="elnino_ar_2026">El Niño atmospheric river 2026</option><option value="dec31_2022">31.12.2022 replay</option></select></label>
      <label>Budget ($M)<input name="budget" type="number" min="1" step="1" value="${Math.round((cfg.budget_usd || 50e6) / 1e6)}"/></label>
      <label>Generations<input name="generations" type="number" min="1" max="20" value="${cfg.generations || 4}"/></label>
      <label>Candidates per generation<input name="population" type="number" min="2" max="32" value="${cfg.population || 8}"/></label>
      <label>Architect<select name="architect"><option value="gemini-4-argon">Gemini 4 Argon</option><option value="gemini-3.8-flash">Gemini 3.8 Flash</option></select></label>
      <label>Mutators<select name="mutator"><option value="gemini-3.8-flash">Gemini 3.8 Flash</option><option value="gemini-4-argon">Gemini 4 Argon</option><option value="gemini-3.5-flash">Gemini 3.5 Flash</option><option value="gemini-3.5-flash-lite">Gemini 3.5 Flash-Lite</option></select></label>
      <label>Parallel sims<input name="workers" type="number" min="1" max="16" value="${cfg.sim_workers || 4}"/></label>
      <label>Time cap (min)<input name="minutes" type="number" min="2" max="120" value="${cfg.max_minutes || 30}"/></label>
    </form>
    <div class="form-err" id="nfErr"></div>
    <div class="form-actions"><button class="btn" id="nfCancel">Cancel</button><button class="btn primary" id="nfGo">Start evolution</button></div>`;
  $('#modal').classList.remove('hidden');
  $('#nfCancel').addEventListener('click', closeModal);
  $('#nfGo').addEventListener('click', async () => {
    const f = new FormData($('#nf'));
    const body = {
      storm: f.get('storm'), budget_usd: +f.get('budget') * 1e6, generations: +f.get('generations'), population: +f.get('population'),
      architect_model: f.get('architect'), mutator_model: f.get('mutator'), sim_workers: +f.get('workers'), max_minutes: +f.get('minutes'),
    };
    if (FIXTURE) { $('#nfErr').textContent = 'Fixture mode: open /evolve/ without ?fixture=1 to start a real run.'; return; }
    $('#nfGo').disabled = true;
    try {
      const r = await getJSON('/api/evolve', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const u = new URL(location.href); u.searchParams.set('job', r.job_id || r.id); u.searchParams.delete('cand'); location.href = u;
    } catch (e) { $('#nfErr').textContent = 'Could not start: ' + e.message; $('#nfGo').disabled = false; }
  });
});

// ---------------------------------------------------------------- boot
let rsz;
addEventListener('resize', () => { clearTimeout(rsz); rsz = setTimeout(() => { lastDetailKey = lastDetailKey; renderAll(); }, 120); });
setInterval(() => { if (S && S.status === 'running') renderTop(); }, 1000);

(async function boot() {
  renderLegend();
  if (tab === 'architecture') setTab('architecture');
  const list = await loadJobs();
  if (!FIXTURE && !jobId) {
    if (!list.length) { showEmpty('No evolution runs yet.'); return; }
    jobId = list[0].id;
    $('#jobPicker').value = jobId;
  }
  poll();
})();

// expand clamped directives / insights on click
document.addEventListener('click', (e) => {
  const el = e.target instanceof Element ? e.target.closest('.d-dir, .ins p') : null;
  if (el) el.classList.toggle('open');
});

// ---------------------------------------------------------------- planner approval (people stay in control)
function approvalHtml() {
  const a = S.approval;
  if (a) {
    const t = new Date((a.t || 0) * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    return `<div class="approval ${a.approved ? 'ok' : 'no'}"><b>${a.approved ? '✓ Approved' : '✕ Rejected'}</b> by ${esc(a.approver || 'planner')} at ${esc(t)}${a.note ? ` · ${esc(a.note)}` : ''}</div>`;
  }
  if (S.status !== 'awaiting_approval' || FIXTURE) return '';
  return `<div class="approval ask"><div><b>Planner approval</b><span>Nothing is built or spent until a person approves this plan.</span></div>
    <span class="acts"><button class="btn" data-approve="0">Reject</button><button class="btn primary" data-approve="1">Approve plan</button></span></div>`;
}
async function approve(ok) {
  if (!S || !S.id) return;
  const note = ok ? '' : (prompt('Reason for rejecting (optional):') || '');
  const r = await fetch(`/api/evolve/${encodeURIComponent(S.id)}/approve`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ approved: ok, approver: 'planner', note }) });
  if (!r.ok) { alert('Approval failed: ' + (await r.text()).slice(0, 200)); return; }
  S.updated = 0; lastDetailKey = ''; poll();
}
