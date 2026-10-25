// StormSim SF — Evolve view. Vanilla ES module, inline SVG, no dependencies.
// Data: GET /api/evolve/{job} (state.json), polled every 1.5 s while running. ?fixture=1 loads ./fixture.json.

const $ = (s, el = document) => el.querySelector(s);
const q = new URLSearchParams(location.search);
const FIXTURE = q.has('fixture');
const POLL_MS = 1500;
const SVGNS = 'http://www.w3.org/2000/svg';

const OPS = {
  seed: { label: 'Seed', color: 'var(--seed)', hex: '#c2c2c2' },
  crossover: { label: 'Argon crossover', color: 'var(--cross)', hex: '#0a0a0a' },
  mutate: { label: 'Flash diff', color: 'var(--flash)', hex: '#555555' },
  tweak: { label: 'Tweak', color: 'var(--tweak)', hex: '#9a9a9a' },
};
const STRAT_COLORS = { grey: '#8c8c8c', green: '#0a0a0a', building: '#9a9a9a', response: '#7a7a7a', trees: '#8c8c8c', other: '#c2c2c2', mixed: '#d4d4d4' };
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
let tab = ['architecture', 'agents', 'evolution'].includes(q.get('tab')) ? q.get('tab') : 'overview';
let treeMode = q.get('tree') === 'pareto' ? 'pareto' : 'tree';
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
  const ag = agentOf(c);
  tip.innerHTML = `<div class="th"><span class="mono">${esc(c.id)}</span><span class="chip"><i style="background:${ag ? ag.color : op.color}"></i>${esc(ag ? ag.label : op.label)}</span><span style="color:var(--faint)">${esc(op.label)} · gen ${c.gen}</span></div>
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
  const models = cfg.architect_model ? `${modelName(cfg.architect_model)} + ${modelName(cfg.mutator_model)} · ` : 'no LLM (tweaks only) · ';
  $('#sub').textContent = `${models}${cfg.storm || 'storm'} · budget ${money(cfg.budget_usd, { dp: 0 })} · ${cfg.population || '?'} per generation · ${cfg.sim_workers || '?'} parallel sims`;
  $('#sub').title = $('#sub').textContent;
  $('#budgetHint').textContent = Math.round((cfg.budget_usd || 50e6) / 1e6);
  const stage = S.status === 'running' ? S.stage : '';
  const label = { running: 'Running', completed: 'Completed', failed: 'Failed', awaiting_approval: 'Awaiting planner approval', approved: 'Approved by planner', rejected: 'Rejected by planner', interrupted: 'Interrupted' }[S.status] || S.status;
  $('#status').innerHTML = `<span class="pill ${esc(S.status)}"><span class="dot"></span>${esc(label)}
      <span class="muted">· gen ${S.generation ?? 0}/${genTotal()}${stage ? ' · ' + esc(stage) : ''}</span></span>
    ${cfg.mock_sim ? '<span class="badge mock" title="The real storm simulator was not available; candidates were scored by the mock bathtub model">MOCK sim</span>' : '<span class="badge real" title="Candidates were scored by sim.run with the real storm physics">REAL sim</span>'}`;
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
    const dot = el('circle', { cx: x, cy: yy, r: isBest ? r + 1.5 : r, fill: op.hex, stroke: PENDING.has(c.status) ? op.hex : 'rgba(255,255,255,.95)', 'stroke-width': 1.2, class: cls }, svg);
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
    const dot = el('circle', { cx: x(c.score.plan_cost_usd), cy: y(c.score.damage_avoided_usd), r: onF ? 5.5 : 4, fill: op.hex, 'fill-opacity': onF ? 1 : 0.55, stroke: 'rgba(255,255,255,.95)', 'stroke-width': 1, class: cls }, svg);
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
    html += `<div class="ar-row" title="${esc(stratLabel(s))}"><span class="sw" style="background:${STRAT_COLORS[s] || '#c2c2c2'}"></span>${esc(stratLabel(s))}</div>`;
    bands.forEach((_, bi) => {
      const key = `${s}|${bi}`;
      const c = cells[key] && D.byId.get(cells[key]);
      if (!c || !c.score) { html += '<div class="ar-cell empty"></div>'; return; }
      const v = c.score.net_benefit_usd;
      const t = (v - minN) / (maxN - minN || 1);
      const a = 0.07 + 0.55 * Math.pow(Math.max(0, t), 1.1);
      const bg = v >= 0 ? `rgba(0,0,0,${a.toFixed(3)})` : 'rgba(0,0,0,0.05)';
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
      <td><span class="mono" style="margin-right:6px">${esc(c.id)}</span>${chip(c)}</td><td>${c.gen}</td>
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
      ${(g.directions || []).length ? `<ol>${g.directions.map((t) => `<li>${esc(t)}</li>`).join('')}</ol>` : isSeed ? `<div class="seeds">${D.cands.filter((c) => c.gen === 0).length} seed plans, one per strategy.</div>` : `<div class="empty-note">${!S.config?.architect_model ? 'No architect in this run (numeric tweaks only).' : g.t_end ? 'No directions returned; mutators used default directions.' : 'Argon is reading the archive…'}</div>`}
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
      <div class="stack">${cbsSorted.map(([k, v]) => `<span style="width:${(100 * v) / cbsTot}%;background:${STRAT_COLORS[k] || '#c2c2c2'}" title="${esc(stratLabel(k))} ${money(v)}"></span>`).join('')}</div>
      <div class="stack-leg">${cbsSorted.map(([k, v]) => `<span><i style="background:${STRAT_COLORS[k] || '#c2c2c2'}"></i>${esc(stratLabel(k))} ${money(v)}</span>`).join('')}</div></div>` : '';

  const can3d = !!c.run_id;
  box.innerHTML = `
    <div class="d-h"><span class="cid">${esc(c.id)}</span>${chip(c)}<span class="chip">${esc(modelName(c.model))}</span><span class="chip">gen ${c.gen}</span>${isBest ? '<span class="best-tag">★ Best</span>' : ''}</div>
    ${metaHtml(c)}
    ${c.note ? `<div class="d-note">${esc(c.note)}</div>` : ''}
    ${c.directive ? `<div class="d-dir"><b>Directive</b>${esc(c.directive)}</div>` : ''}
    ${reviewHtml(c)}
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
    ${'' && causeHtml}${'' && spendHtml}
    <details class="d-more"><summary>Plan code</summary><div class="d-sec"><div class="lab">Plan code<span class="r codebar"><span class="dstat" id="dstat"></span>
      <span class="seg"><button data-m="diff" class="${diffMode === 'diff' ? 'on' : ''}">Diff vs parent</button><button data-m="full" class="${diffMode === 'full' ? 'on' : ''}">Full</button></span></span></div>
      <div class="code" id="code"><div class="ln"><span class="no"></span><span class="sg"></span><span class="tx faint">loading…</span></div></div></div></details>`;

  box.querySelectorAll('.tnode[data-id]').forEach((n) => { if (n.dataset.id !== c.id) n.addEventListener('click', () => select(n.dataset.id)); });
  box.querySelectorAll('.seg button').forEach((b) => b.addEventListener('click', () => { diffMode = b.dataset.m; lastDetailKey = ''; renderDetail(); }));
  $('#btnVideo').addEventListener('click', () => openVideo(c));
  box.querySelectorAll('[data-approve]').forEach((b) => b.addEventListener('click', () => approve(b.dataset.approve === '1')));
  const vb = $('#btnVerify'); if (vb) vb.addEventListener('click', verifyManaged);

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
  const c = { argon: 0, flash: 0, tweak: 0, review: 0, check: 0, sim: 0 };
  if (S.activity && S.activity.length) {
    S.activity.forEach((a) => {
      if (a.t1 != null) return;
      if (a.kind === 'sim') c.sim++;
      else if (a.kind === 'check') c.check++;
      else if (a.kind === 'review') c.review++;
      else if (a.kind === 'tweak') c.tweak++;
      else if (a.agent === 'architect' || /argon/.test(a.model || '')) c.argon++;
      else c.flash++;
    });
    return c;
  }
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
  [['ah', 'rgba(0,0,0,.6)'], ['ahg', 'rgba(0,0,0,.65)'], ['ahp', 'rgba(0,0,0,.7)'], ['ahb', 'rgba(0,0,0,.7)']].forEach(([id, col]) => {
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
  const specialists = roster().filter((a) => a.model && a.key !== 'architect' && a.key !== 'reviewer');
  const persona = specialists.length > 1
    ? [specialists.slice(0, 3).map((a) => a.label).join(' · '), specialists.slice(3, 6).map((a) => a.label).join(' · ')].filter(Boolean)
    : ['one directive each from the architect'];
  const hasReviewer = !!agentByKey('reviewer') || D.cands.some((c) => c.review);
  const nodes = {
    sampler: { x: 20, y: 96, w: 250, h: 100, k: 'Prompt sampler', t: 'Parent + inspirations', s: ['a parent from an archive cell,', 'top elites as inspiration'], c: '#555555', on: running && (llmOn || st === 'architect' || st === 'mutating') },
    db: { x: 20, y: 352, w: 250, h: 112, k: 'Program database', t: 'MAP-Elites archive', s: [`${nCells} elite cells · ${nScored} scored plans`, 'best plan per strategy × cost band'], c: '#0a0a0a', on: running && st === 'selecting', n: nCells },
    argon: { x: 330, y: 22, w: 300, h: 100, k: 'Architect', t: modelName(S.config?.architect_model || 'gemini-4-argon'), s: ['reads the whole archive, dispatches', 'specialists, writes a crossover plan'], c: '#0a0a0a', on: running && (cnt.argon > 0 || st === 'architect'), n: cnt.argon },
    flash: { x: 330, y: 144, w: 300, h: 136, k: `${specialists.length > 1 ? specialists.length + ' specialist' : ''} mutators × ${Math.max(1, N - 3)} in parallel`, t: modelName(S.config?.mutator_model || 'gemini-3.8-flash'), s: ['SEARCH/REPLACE diffs on the parent', ...persona], c: '#555555', on: running && cnt.flash > 0, n: cnt.flash },
    tweak: { x: 330, y: 302, w: 300, h: 78, k: 'Hill-climb', t: 'Numeric tweak', s: ['no LLM: perturbs sizes, heights, flows'], c: '#9a9a9a', on: running && cnt.tweak > 0, n: cnt.tweak },
    review: { x: 676, y: 160, w: 200, h: 100, k: 'Pre-sim critic', t: 'Reviewer', s: [modelName(agentByKey('reviewer')?.model || 'gemini-3.5-flash-lite'), 'predicts better / same / worse'], c: '#8c8c8c', on: running && cnt.review > 0, n: cnt.review, off: !hasReviewer },
    sandbox: { x: 920, y: 60, w: 170, h: 100, k: 'Isolation', t: 'Sandbox', s: ['Landlock + seccomp,', 'no network, no secrets'], c: '#555555', on: running && cnt.check > 0, n: cnt.check },
    cost: { x: 1106, y: 60, w: 168, h: 100, k: 'Locked', t: 'Cost planner', s: ['prices the physical', 'diff, cited unit costs'], c: '#9a9a9a', on: running && cnt.check > 0 },
    sim: { x: 920, y: 300, w: 354, h: 128, k: 'Deterministic evaluator', t: 'Storm simulation', s: ['shallow-water flood · LBM wind', 'tree failure · people · Hazus damage', 'same plan → same ledger, every time'], c: '#7a7a7a', on: running && cnt.sim > 0, n: cnt.sim },
    eop: { x: 330, y: 446, w: 300, h: 92, k: 'People stay in control', t: 'Planner approval', s: ['the planner approves the best plan', 'before anything is built or spent'], c: '#0a0a0a', on: DONE.has(S.status) },
  };
  const simOut = running && (cnt.sim > 0 || st === 'selecting');
  const chk = cnt.check > 0 && running;
  const edges = [
    { d: 'M145,350 L145,198', on: nodes.sampler.on, c: '#555555', label: 'elites', lx: 154, ly: 278 },
    { d: 'M272,124 C300,124 300,72 328,72', on: nodes.argon.on, c: '#0a0a0a', m: 'ahp' },
    { d: 'M272,146 C300,146 300,212 328,212', on: cnt.flash > 0 && running, c: '#555555' },
    { d: 'M272,168 C300,168 300,341 328,341', on: cnt.tweak > 0 && running, c: '#9a9a9a' },
    { d: 'M632,90 C656,90 652,190 674,190', on: (cnt.argon > 0 || cnt.review > 0) && running, c: '#0a0a0a', m: 'ahp' },
    { d: 'M632,212 L674,212', on: (cnt.flash > 0 || cnt.review > 0) && running, c: '#555555' },
    { d: 'M878,206 C900,206 896,124 918,124', on: chk, c: '#8c8c8c', m: 'ahb' },
    { d: 'M632,336 C800,336 940,312 940,164', on: chk, c: '#9a9a9a' },
    { d: 'M1092,110 L1104,110', on: chk, c: '#9a9a9a' },
    { d: 'M1190,162 L1190,298', on: cnt.sim > 0 && running, c: '#7a7a7a', label: 'within budget', lx: 1198, ly: 236 },
    { d: 'M918,392 C720,400 480,408 272,408', on: simOut, c: '#0a0a0a', m: 'ahg', label: 'ledger + score', lx: 700, ly: 416 },
    { d: 'M200,466 C200,492 280,492 328,492', on: DONE.has(S.status), c: '#0a0a0a', m: 'ahg', exit: true, label: 'best plan', lx: 214, ly: 512 },
    { d: 'M480,20 C480,2 96,2 96,94', on: nodes.argon.on, c: '#0a0a0a', m: 'ahp', thin: true, label: 'reads every plan so far', lx: 112, ly: 64 },
  ];
  if (!hasReviewer) { edges.splice(4, 3, { d: 'M632,90 C780,90 800,110 918,110', on: chk, c: '#0a0a0a', m: 'ahp' }, { d: 'M632,212 C780,212 800,134 918,134', on: chk, c: '#555555' }); delete nodes.review; }
  edges.forEach((e) => {
    const pth = el('path', { d: e.d, class: 'aedge' + (e.on ? ' on' : '') + (e.exit ? ' exit' : ''), style: `--c:${e.c}`, 'marker-end': `url(#${e.m || 'ah'})` }, svg);
    if (e.thin && !e.on) pth.setAttribute('stroke-dasharray', '2 5');
    if (e.label) el('text', { x: e.lx, y: e.ly, class: 'alabel' }, svg).textContent = e.label;
  });
  Object.values(nodes).forEach((n) => {
    const g = el('g', { class: 'anode' + (n.on ? ' on' : ''), style: `--c:${n.c};--glow:${n.c}66`, transform: `translate(${n.x},${n.y})` }, svg);
    el('rect', { class: 'box', width: n.w, height: n.h, rx: 10 }, g);
    el('rect', { class: 'stripe', x: 0, y: 14, width: 3, height: n.h - 28, rx: 1.5 }, g);
    el('text', { class: 'k', x: 18, y: 25 }, g).textContent = n.k;
    el('text', { class: 't', x: 18, y: 46 }, g).textContent = n.t;
    n.s.forEach((s2, i) => { el('text', { class: 's' + (n === nodes.flash && i > 0 ? ' persona' : ''), x: 18, y: 66 + i * 17 }, g).textContent = s2; });
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
// monochrome: shade by model tier (Argon darkest, Flash mid, Flash-Lite light, no model lightest)
const monoFor = (m) => (!m ? '#b5b5b5' : /argon/.test(m) ? '#0a0a0a' : /lite/.test(m) ? '#8c8c8c' : '#555555');
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
  $('#view-overview').classList.toggle('hidden', t !== 'overview');
  document.body.classList.toggle('is-overview', t === 'overview');
  $('#view-evolution').classList.toggle('hidden', t !== 'evolution');
  $('#view-architecture').classList.toggle('hidden', t !== 'architecture');
  $('#view-agents').classList.toggle('hidden', t !== 'agents');
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
  if (tab === 'overview') renderOverview();
  else if (tab === 'evolution') { renderMain(); renderTreeCard(); renderArchive(); renderBoard(); renderGens(); }
  else if (tab === 'agents') renderAgents();
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
async function demoJob() {
  if (FIXTURE) return null;
  try {
    const r = await fetch('/runs/evolve/DEMO_JOB', { cache: 'no-store' });
    if (!r.ok) return null;
    const id = (await r.text()).trim();
    return /^evolve-[A-Za-z0-9_-]+$/.test(id) ? id : null;
  } catch { return null; }
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
  setTab(tab);
  const list = await loadJobs();
  if (!FIXTURE && !jobId) {
    if (!list.length) { showEmpty('No evolution runs yet.'); return; }
    const preferred = await demoJob();
    jobId = list.some((j) => j.id === preferred) ? preferred : list[0].id;
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
    <span class="acts"><button class="btn" data-approve="0">Reject</button><button class="btn" id="btnVerify" title="An Antigravity managed agent re-runs the storm in its own sandbox and audits the plan">Verify in Antigravity</button><button class="btn primary" data-approve="1">Approve plan</button></span></div>
    <div class="verify-out" id="verifyOut">${managedJob ? verifyLine(managedJob) : ''}</div>`;
}
async function approve(ok) {
  if (!S || !S.id) return;
  const note = ok ? '' : (prompt('Reason for rejecting (optional):') || '');
  const r = await fetch(`/api/evolve/${encodeURIComponent(S.id)}/approve`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ approved: ok, approver: 'planner', note }) });
  if (!r.ok) { alert('Approval failed: ' + (await r.text()).slice(0, 200)); return; }
  S.updated = 0; lastDetailKey = ''; poll();
}

// ================================================================ agents (round 2)
const FALLBACK_AGENTS = [
  { key: 'architect', label: 'Architect', model: 'gemini-4-argon', role: 'reads the whole archive, writes directions and crossovers', color: '#0a0a0a' },
  { key: 'flash', label: 'Mutators', model: 'gemini-3.8-flash', role: 'write SEARCH/REPLACE diffs on a parent plan', color: '#555555' },
  { key: 'tweak', label: 'Hill-climber', model: null, role: 'no LLM: rescales sizes, heights and flows', color: '#9a9a9a' },
  { key: 'sandbox', label: 'Sandbox + cost planner', model: null, role: 'Landlock sandbox, locked cited cost table', color: '#c2c2c2' },
  { key: 'sim', label: 'Storm simulator', model: null, role: 'deterministic flood, wind, trees, people, damage', color: '#7a7a7a' },
];
let _rosterCache = null;
function roster() {
  if (_rosterCache && _rosterCache.S === S) return _rosterCache.list;
  const list = (S.agents && S.agents.length ? S.agents : FALLBACK_AGENTS).map((a) => ({ ...a, color: monoFor(a.model) }));
  _rosterCache = { S, list, by: new Map(list.map((a) => [a.key, a])) };
  return list;
}
const agentByKey = (k) => { roster(); return _rosterCache.by.get(k); };
function agentKeyOfCand(c) {
  if (c.agent) return c.agent;
  if (c.op === 'tweak' || !c.model) return S.agents ? 'tweak' : (c.op === 'seed' && !c.model ? 'tweak' : 'tweak');
  if (/argon/.test(c.model || '') && c.op !== 'seed') return 'architect';
  return S.agents ? (c.op === 'crossover' ? 'architect' : 'hydrologist') : 'flash';
}
function agentOf(c) {
  if (!c) return null;
  const a = agentByKey(agentKeyOfCand(c));
  if (a) return a;
  const k = agentKeyOfCand(c);
  return { key: k, label: c.agent_label || EXTRA_LABELS[k] || k, color: '#c2c2c2', model: c.model };
}
const EXTRA_LABELS = { import: 'Imported', repair: 'Repair', flash: 'Mutators' };
const initials = (label) => { const w = String(label || '?').split(/[\s+]+/).filter(Boolean); return (w.length > 1 ? w[0][0] + w[1][0] : w[0].slice(0, 2)).toUpperCase(); };

/** Activity log; reconstructed from candidate timestamps for older runs. */
let _actCache = null;
function activities() {
  if (_actCache && _actCache.S === S) return _actCache.list;
  let list = S.activity;
  let recon = false;
  if (!list || !list.length) {
    recon = true;
    list = [];
    D.cands.forEach((c) => {
      const t0 = c.t_created; if (!t0) return;
      const ak = agentKeyOfCand(c);
      const llm = c.llm_s || (c.op === 'tweak' ? 0.5 : null);
      const kind = c.op === 'tweak' ? 'tweak' : c.op === 'crossover' ? 'crossover' : c.op === 'seed' ? 'seed' : 'mutate';
      const writingEnd = c.status === 'writing' ? null : t0 + (llm || 5);
      if (!(c.op === 'seed' && !c.model)) list.push({ agent: ak, kind, cand: c.id, gen: c.gen, t0, t1: writingEnd, ok: c.status === 'failed' && !c.run_id ? false : true });
      if (c.eval_s && c.t_done) list.push({ agent: 'sim', kind: 'sim', cand: c.id, gen: c.gen, t0: c.t_done - c.eval_s, t1: c.t_done, ok: true });
      else if (c.status === 'simulating') list.push({ agent: 'sim', kind: 'sim', cand: c.id, gen: c.gen, t0: writingEnd || t0, t1: null, ok: null });
    });
    (S.generations || []).forEach((g) => { if (g.gen > 0 && g.t_start && S.config?.architect_model) {
      const first = Math.min(...D.cands.filter((c) => c.gen === g.gen).map((c) => c.t_created || Infinity));
      list.push({ agent: 'architect', kind: 'architect', cand: null, gen: g.gen, t0: g.t_start, t1: isFinite(first) ? first : (S.status === 'running' && S.stage === 'architect' ? null : g.t_start + 60), ok: true });
    } });
  }
  _actCache = { S, list, recon };
  return list;
}
const nowT = () => (S.status === 'running' ? (FIXTURE ? S.updated : Date.now() / 1000) : Math.max(S.updated || 0, ...activities().map((a) => a.t1 || 0)));

// ---------------------------------------------------------------- detail extras
function metaHtml(c) {
  const ag = agentOf(c);
  const bits = [];
  if (ag) bits.push(`<span class="agent-chip" style="--ac:${ag.color}"><span class="av">${esc(initials(ag.label))}</span>${esc(ag.label)}${ag.model ? `<span class="am">${esc(modelName(ag.model))}</span>` : ''}</span>`);
  if (c.selection) bits.push(`<span class="sel-m" title="How the parent was chosen">parent: ${esc(c.selection)}</span>`);
  if (c.repaired) bits.push('<span class="badge repaired" title="Rejected by the cascade once and fixed by its author">↺ repaired</span>');
  return bits.length ? `<div class="d-meta">${bits.join('')}</div>` : '';
}
function reviewHtml(c) {
  const r = c.review;
  if (!r) return '';
  const held = c.improved == null ? null : (r.predicted === 'better') === !!c.improved;
  const outcome = c.improved == null ? '<span class="faint">outcome pending</span>'
    : `<span class="${held ? 'ok' : 'no'}">${held ? '✓ prediction held' : '✕ prediction missed'}</span> <span class="faint">· actually ${c.improved ? 'better' : 'not better'} than its parent</span>`;
  return `<div class="review"><div class="rv-h"><span class="av" style="--ac:${agentByKey('reviewer')?.color || '#8c8c8c'}">RV</span><b>Reviewer</b><span class="faint">${esc(modelName(r.model || 'gemini-3.5-flash-lite'))}</span>
    <span class="pred ${esc(r.predicted)}">predicted ${esc(r.predicted)}</span>${r.verdict === 'risky' ? '<span class="badge risky">risky</span>' : ''}</div>
    ${r.reason ? `<div class="rv-r">${esc(r.reason)}</div>` : ''}<div class="rv-o">${outcome}</div></div>`;
}
let managedJob = null;
const verifyLine = (j) => j.error ? `<span style="color:var(--bad)">${esc(j.error)}</span>`
  : `Antigravity verification started · job <span class="mono">${esc(j.job_id)}</span> · <a href="${esc(j.events_url || `/api/jobs/${j.job_id}/events`)}" target="_blank">live events ↗</a>`;
async function verifyManaged() {
  if (!S?.id || FIXTURE) return;
  const b = $('#btnVerify'); if (b) b.disabled = true;
  try {
    managedJob = await getJSON('/api/managed', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ job_id: S.id, storm: S.config?.storm || 'elnino_ar_2026' }) });
  } catch (e) { managedJob = { error: 'Could not start: ' + e.message }; if (b) b.disabled = false; }
  const o = $('#verifyOut'); if (o) o.innerHTML = verifyLine(managedJob);
}

// ---------------------------------------------------------------- family tree (phylogeny)
document.querySelectorAll('#treeSeg button').forEach((b) => b.addEventListener('click', () => {
  treeMode = b.dataset.m;
  const u = new URL(location.href); u.searchParams.set('tree', treeMode); history.replaceState(null, '', u);
  renderTreeCard();
}));
function renderTreeCard() {
  document.querySelectorAll('#treeSeg button').forEach((x) => x.classList.toggle('on', x.dataset.m === treeMode));
  $('#treeTitle').textContent = treeMode === 'tree' ? 'Family tree' : 'Cost vs damage avoided';
  $('#treeHint').textContent = treeMode === 'tree' ? 'every plan, parent → child · best lineage glows' : 'Pareto front · BCR = 1 diagonal';
  if (treeMode === 'tree') renderTree(); else renderPareto();
}
function renderTree() {
  const box = $('#pareto');
  const { svg, W, H } = svgIn(box);
  const cands = D.cands;
  if (!cands.length) { el('text', { x: W / 2, y: H / 2, 'text-anchor': 'middle' }, svg).textContent = 'waiting for the first plans…'; return; }
  const gens = [...new Set(cands.map((c) => c.gen))].sort((a, b) => a - b);
  const showLeg = W > 340 && !!S.agents;
  const m = { l: 14, r: 14, t: showLeg ? 28 : 10, b: 22 };
  const colW = (W - m.l - m.r) / Math.max(1, gens.length);
  const pos = new Map();
  const rank = (c) => (D.ok(c) ? c.score.net_benefit_usd : PENDING.has(c.status) ? -1e30 : -1e31);
  gens.forEach((g, gi) => {
    const col = cands.filter((c) => c.gen === g).sort((a, b) => rank(b) - rank(a));
    const n = col.length;
    const step = (H - m.t - m.b) / Math.max(1, n);
    col.forEach((c, k) => pos.set(c.id, [m.l + colW * (gi + 0.5), m.t + step * (k + 0.5)]));
    el('text', { x: m.l + colW * (gi + 0.5), y: H - 6, 'text-anchor': 'middle', class: 'genlabel' }, svg).textContent = g === gens[0] && cands.some((c) => c.gen === g && c.op === 'seed') ? 'Seeds' : `Gen ${g}`;
    if (gi % 2 === 0) el('rect', { x: m.l + colW * gi, y: m.t - 4, width: colW, height: H - m.t - m.b + 4, class: 'genband', rx: 6 }, svg);
  });
  const bestLine = new Set(D.best ? lineage(D.best).map((c) => c.id) : []);
  const sel = selId && D.byId.get(selId);
  const selLine = new Set(sel ? lineage(sel).map((c) => c.id) : []);
  const edgeG = el('g', {}, svg);
  cands.forEach((c) => (c.parents || []).forEach((pid, pi) => {
    const a = pos.get(pid), b = pos.get(c.id);
    if (!a || !b) return;
    const mx = (a[0] + b[0]) / 2;
    const onBest = bestLine.has(c.id) && bestLine.has(pid) && pi === 0;
    const onSel = selLine.has(c.id) && selLine.has(pid) && pi === 0;
    const ag = agentOf(c);
    el('path', { d: `M${a[0]},${a[1]} C${mx},${a[1]} ${mx},${b[1]} ${b[0]},${b[1]}`,
      class: 'tedge' + (onBest ? ' best' : '') + (onSel ? ' sel' : '') + (pi > 0 ? ' x2' : ''), style: `--ec:${ag ? ag.color : opOf(c).hex}` }, edgeG);
  }));
  const r = Math.max(4, Math.min(7.5, (H - m.t - m.b) / Math.max(...gens.map((g) => cands.filter((c) => c.gen === g).length)) / 3.2));
  cands.forEach((c) => {
    const [x, y] = pos.get(c.id);
    const ag = agentOf(c);
    const col = ag ? ag.color : opOf(c).hex;
    let cls = 'pt tnodept';
    if (PENDING.has(c.status)) cls += ' pending'; else if (!D.ok(c)) cls += ' reject';
    if (!seen.has(c.id) && !firstRender) cls += ' new';
    if (c.id === selId) cls += ' sel';
    if (bestLine.has(c.id)) cls += ' onbest';
    const dot = el('circle', { cx: x, cy: y, r: D.best && c.id === D.best.id ? r + 2 : r, fill: col, stroke: PENDING.has(c.status) ? col : 'rgba(255,255,255,.95)', 'stroke-width': 1.3, class: cls }, svg);
    bindTip(dot, c);
  });
  if (D.best && pos.get(D.best.id)) {
    const [x, y] = pos.get(D.best.id);
    el('circle', { cx: x, cy: y, r: r + 2, class: 'selring', style: 'stroke:var(--good)' }, svg);
  }
  // compact agent legend (only agents that produced plans)
  const used = [...new Set(cands.map((c) => agentOf(c)?.key).filter(Boolean))].map(agentByKey).filter(Boolean);
  if (used.length && showLeg) {
    const fo = el('foreignObject', { x: 0, y: 0, width: W, height: 18 }, svg);
    const div = document.createElement('div'); div.className = 'tree-leg';
    div.innerHTML = used.map((a) => `<span><i style="background:${a.color}"></i>${esc(a.label)}</span>`).join('');
    fo.appendChild(div);
  }
}

// ---------------------------------------------------------------- agents tab
function renderAgents() {
  renderRoster();
  renderSwim();
  renderFunnel();
  renderCalib();
}
function agentStats(a) {
  const acts = activities().filter((x) => x.agent === a.key);
  const kids = D.cands.filter((c) => agentKeyOfCand(c) === a.key);
  const judged = kids.filter((c) => c.improved != null);
  const imp = judged.filter((c) => c.improved).length;
  const best = kids.filter(D.ok).reduce((b, c) => (!b || c.score.net_benefit_usd > b.score.net_benefit_usd ? c : b), null);
  const working = acts.some((x) => x.t1 == null) && S.status === 'running';
  const busy = acts.reduce((s, x) => s + ((x.t1 ?? nowT()) - x.t0), 0);
  return { calls: acts.length, kids: kids.length, imp, judged: judged.length, best, working, busy };
}
function renderRoster() {
  const list = roster();
  $('#rosterHint').textContent = S.agents ? `${list.length} agents · ${list.filter((a) => a.model).length} of them are models` : 'older run: agents reconstructed from the candidates';
  $('#roster').innerHTML = `<div class="roster">${list.map((a) => {
    const st = agentStats(a);
    const isEval = a.key === 'sandbox' || a.key === 'sim';
    const stats = isEval
      ? `<span><b>${num(st.calls)}</b> ${a.key === 'sim' ? 'storm runs' : 'checks'}</span><span><b>${dur(st.busy)}</b> busy</span>`
      : `<span><b>${num(st.calls)}</b> calls</span><span><b>${num(st.kids)}</b> plans</span>${st.judged ? `<span><b>${Math.round((100 * st.imp) / st.judged)}%</b> beat parent</span>` : ''}${st.best ? `<span class="bn" data-id="${esc(st.best.id)}">best <b>${money(st.best.score.net_benefit_usd, { sign: true })}</b></span>` : ''}`;
    return `<div class="agent ${st.working ? 'working' : ''}" style="--ac:${a.color}">
      <div class="ag-h"><span class="av">${esc(initials(a.label))}</span><div class="ag-n"><div class="nm">${esc(a.label)}</div>
        <div class="md">${a.model ? esc(modelName(a.model)) : isEval ? 'locked evaluator' : 'no LLM'}</div></div>
        <span class="state">${st.working ? '<i></i>working' : st.calls ? 'idle' : 'waiting'}</span></div>
      <div class="ag-r">${esc(a.role || '')}</div>
      <div class="ag-s">${stats}</div></div>`;
  }).join('')}</div>`;
  $('#roster').querySelectorAll('.bn[data-id]').forEach((n) => n.addEventListener('click', () => select(n.dataset.id)));
}
const KIND_LABEL = { architect: 'reads the archive, writes directions', seed: 'writes a seed plan', mutate: 'writes a diff', crossover: 'writes a crossover', repair: 'repairs a rejected plan', review: 'reviews a change', tweak: 'numeric tweak', check: 'sandbox + cost check', sim: 'storm simulation' };
function renderSwim() {
  const box = $('#swim');
  box.innerHTML = '';
  const acts = activities();
  const recon = _actCache.recon;
  $('#swimHint').textContent = recon ? 'reconstructed from candidate timestamps (older run) · click a bar to open its plan' : 'every model call, review, sandbox check and storm run on one clock · click a bar to open its plan';
  if (!acts.length) { box.innerHTML = '<div class="empty-note">No activity yet.</div>'; return; }
  const t0 = Math.min(S.started || Infinity, ...acts.map((a) => a.t0));
  const t1 = Math.max(nowT(), ...acts.map((a) => a.t1 || 0)) + 2;
  const W = Math.max(600, box.getBoundingClientRect().width);
  const labelW = W < 900 ? 150 : 196, padR = 16, rowH = 15, laneGap = 9, axisH = 36;
  const x = (t) => labelW + ((t - t0) / (t1 - t0)) * (W - labelW - padR);
  const lanes = [];
  roster().forEach((a) => {
    const items = acts.filter((x2) => x2.agent === a.key).sort((p, q2) => p.t0 - q2.t0);
    const rowsEnd = [];
    items.forEach((it) => {
      const end = it.t1 ?? t1;
      let r = rowsEnd.findIndex((e) => e <= it.t0 - 0.5);
      if (r < 0) { r = rowsEnd.length; rowsEnd.push(end); } else rowsEnd[r] = end;
      it._row = r;
    });
    let rows = Math.max(1, rowsEnd.length);
    if (a.key === 'sim') rows = Math.max(rows, S.config?.sim_workers || 1);
    lanes.push({ a, items, rows });
  });
  // agents present in activity but missing from roster
  const known = new Set(roster().map((a) => a.key));
  [...new Set(acts.map((a) => a.agent))].filter((k) => !known.has(k)).forEach((k) => {
    const items = acts.filter((x2) => x2.agent === k);
    const rowsEnd = [];
    items.sort((p, q2) => p.t0 - q2.t0).forEach((it) => { const end = it.t1 ?? t1; let r = rowsEnd.findIndex((e) => e <= it.t0 - 0.5); if (r < 0) { r = rowsEnd.length; rowsEnd.push(end); } else rowsEnd[r] = end; it._row = r; });
    lanes.push({ a: { key: k, label: EXTRA_LABELS[k] || k, color: '#c2c2c2' }, items, rows: Math.max(1, rowsEnd.length) });
  });
  let y = axisH;
  lanes.forEach((L) => { L.y = y; L.h = L.rows * rowH + 8; y += L.h + laneGap; });
  const H = y + 4;
  const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, width: '100%', height: H, class: 'swim' });
  box.appendChild(svg);
  const defs = el('defs', {}, svg);
  const pat = el('pattern', { id: 'stripes', width: 8, height: 8, patternUnits: 'userSpaceOnUse', patternTransform: 'rotate(45)' }, defs);
  el('rect', { width: 4, height: 8, fill: 'rgba(0,0,0,0.12)' }, pat);
  // axis ticks (elapsed)
  const span = t1 - t0;
  const step = [10, 15, 30, 60, 120, 300, 600, 900, 1800].find((s2) => span / s2 <= 12) || 3600;
  for (let t = 0; t <= span; t += step) {
    const xx = x(t0 + t);
    el('line', { x1: xx, x2: xx, y1: axisH - 6, y2: H, class: 'sw-grid' }, svg);
    el('text', { x: xx, y: 10, 'text-anchor': 'middle', class: 'sw-tick' }, svg).textContent = t >= 3600 ? dur(t) : `${Math.floor(t / 60)}:${String(Math.round(t % 60)).padStart(2, '0')}`;
  }
  // generation boundaries
  (S.generations || []).forEach((g) => {
    if (!g.t_start || g.gen === 0) return;
    const xx = x(g.t_start);
    el('line', { x1: xx, x2: xx, y1: axisH - 8, y2: H, class: 'sw-gen' }, svg);
    el('text', { x: xx + 4, y: axisH - 9, class: 'sw-genl' }, svg).textContent = `Gen ${g.gen}`;
  });
  lanes.forEach((L, li) => {
    if (li % 2 === 0) el('rect', { x: 0, y: L.y - 3, width: W, height: L.h + 6, class: 'sw-band', rx: 6 }, svg);
    const g = el('g', { transform: `translate(8,${L.y})` }, svg);
    const st = L.items.some((it) => it.t1 == null) && S.status === 'running';
    el('circle', { cx: 11, cy: 11, r: 10, class: 'sw-av' + (st ? ' on' : ''), style: `--ac:${L.a.color}` }, g);
    el('text', { x: 11, y: 14.5, 'text-anchor': 'middle', class: 'sw-avt', style: `fill:${L.a.color}` }, g).textContent = initials(L.a.label);
    el('text', { x: 28, y: 10, class: 'sw-name' }, g).textContent = L.a.label;
    el('text', { x: 28, y: 22, class: 'sw-model' }, g).textContent = L.a.model ? modelName(L.a.model) : (L.a.key === 'sim' ? `${L.rows} parallel slots` : 'no LLM');
    L.items.forEach((it) => {
      const xa = x(it.t0), xb = x(it.t1 ?? t1 - 2);
      const w = Math.max(3, xb - xa);
      const yy = L.y + 4 + it._row * rowH;
      const running = it.t1 == null && S.status === 'running';
      const r = el('rect', { x: xa, y: yy, width: w, height: rowH - 4, rx: 3, class: 'sw-bar' + (running ? ' run' : '') + (it.ok === false ? ' bad' : '') + (it.cand && it.cand === selId ? ' sel' : ''), style: `--ac:${L.a.color}` }, svg);
      if (running) el('rect', { x: xa, y: yy, width: w, height: rowH - 4, rx: 3, fill: 'url(#stripes)', class: 'sw-stripe' }, svg);
      r.addEventListener('mouseenter', (e) => showActTip(it, L.a, e));
      r.addEventListener('mousemove', moveTip);
      r.addEventListener('mouseleave', hideTip);
      if (it.cand) r.addEventListener('click', () => select(it.cand));
    });
  });
  if (S.status === 'running') {
    const xx = x(nowT());
    el('line', { x1: xx, x2: xx, y1: axisH - 8, y2: H, class: 'sw-now' }, svg);
    el('text', { x: xx, y: axisH - 9, 'text-anchor': 'end', dx: -4, class: 'sw-nowl' }, svg).textContent = 'now';
  }
}
function showActTip(it, a, ev) {
  const c = it.cand && D.byId.get(it.cand);
  const d = (it.t1 ?? nowT()) - it.t0;
  tip.innerHTML = `<div class="th"><span class="chip"><i style="background:${a.color}"></i>${esc(a.label)}</span><span style="color:var(--faint)">${it.gen != null ? 'gen ' + it.gen : ''}</span></div>
    <div class="tn">${esc(KIND_LABEL[it.kind] || it.kind)}${c ? ` · <span class="mono" style="color:#0a0a0a">${esc(c.id)}</span>` : ''}</div>
    <div class="tg"><span>Duration</span><span>${d < 60 ? d.toFixed(1) + ' s' : dur(d)}</span>${it.model ? `<span>Model</span><span>${esc(modelName(it.model))}</span>` : ''}
    <span>Result</span><span style="color:${it.t1 == null ? 'var(--accent)' : it.ok === false ? 'var(--bad)' : 'var(--good)'}">${it.t1 == null ? 'running' : it.ok === false ? 'failed' : 'ok'}</span></div>
    ${c && c.note ? `<div class="tn" style="margin-top:4px">${esc(c.note)}</div>` : ''}`;
  tip.classList.add('on');
  moveTip(ev);
}
function deriveFunnel() {
  if (S.funnel) return S.funnel;
  const C = D.cands;
  const syntax = (c) => /SyntaxError|no usable|define a top-level/.test(String(c.error || ''));
  return {
    proposed: C.length, compiled: C.filter((c) => !syntax(c) && c.status !== 'writing').length,
    cost_ok: C.filter((c) => c.run_id || c.status === 'simulating').length, repaired: C.filter((c) => c.repaired || /\(repaired\)/.test(c.note || '')).length,
    simulated: C.filter((c) => c.run_id).length, within_budget: D.scored.length, elites: Object.keys(S.archive?.cells || {}).length,
    improved: C.filter((c) => c.improved === true || (c.improved == null && c.parents?.length && D.ok(c) && D.byId.get(c.parents[0]) && D.ok(D.byId.get(c.parents[0])) && c.score.net_benefit_usd > D.byId.get(c.parents[0]).score.net_benefit_usd)).length,
  };
}
function renderFunnel() {
  const F = deriveFunnel();
  const stages = [['proposed', 'Plans proposed', 'every seed, diff, crossover and tweak'], ['compiled', 'Compile', 'parses and defines apply(city)'],
    ['cost_ok', 'Sandbox + cost', 'runs without error, within budget'], ['simulated', 'Storm simulated', 'full deterministic storm run'],
    ['within_budget', 'Scored in budget', 'measured cost ≤ budget'], ['elites', 'Archive elites', 'best in their strategy × cost cell'],
    ['improved', 'Beat their parent', 'net benefit above the parent plan']];
  const max = Math.max(1, F.proposed || 0);
  $('#funnel').innerHTML = `<div class="funnel">${stages.map(([k, label, sub], i) => {
    const v = F[k] ?? 0;
    const pct = (100 * v) / max;
    return `<div class="fs" style="--w:${Math.max(4, pct)}%;--d:${i * 60}ms">
      <div class="fl"><b>${esc(label)}</b><span>${esc(sub)}</span></div>
      <div class="fb"><span></span></div>
      <div class="fv"><b>${num(v)}</b><span>${Math.round(pct)}%</span></div></div>
      ${k === 'cost_ok' && F.repaired ? `<div class="fr">↺ ${num(F.repaired)} rejected plan${F.repaired === 1 ? '' : 's'} went back to ${F.repaired === 1 ? 'its' : 'their'} author with the error for one repair round</div>` : ''}`;
  }).join('')}</div>`;
}
function renderCalib() {
  const rev = D.cands.filter((c) => c.review && c.review.predicted);
  if (!rev.length) { $('#calib').innerHTML = `<div class="empty-note">${S.agents ? 'No reviews yet.' : 'This run had no reviewer agent.'}</div>`; return; }
  const P = ['better', 'same', 'worse'];
  const cell = {}; let n = 0, right = 0, pending = 0, risky = 0;
  rev.forEach((c) => {
    if (c.review.verdict === 'risky') risky++;
    if (c.improved == null) { pending++; return; }
    const k = `${c.review.predicted}|${c.improved ? 1 : 0}`; cell[k] = (cell[k] || 0) + 1; n++;
    if ((c.review.predicted === 'better') === !!c.improved) right++;
  });
  const mx = Math.max(1, ...Object.values(cell));
  const td = (p, a) => { const v = cell[`${p}|${a}`] || 0; const good = (p === 'better') === !!a; return `<td class="${good ? 'g' : 'b'}" style="--o:${(0.08 + 0.6 * v / mx).toFixed(2)}">${v}</td>`; };
  $('#calib').innerHTML = `<div class="calib">
    <div class="acc"><b>${n ? Math.round((100 * right) / n) : '—'}${n ? '%' : ''}</b><span>of ${n} reviewed plans called right${pending ? ` · ${pending} pending` : ''}${risky ? ` · ${risky} flagged risky` : ''}</span></div>
    <table class="cm"><thead><tr><th></th><th>beat parent</th><th>did not</th></tr></thead><tbody>
      ${P.map((p) => `<tr><th>predicted ${p}</th>${td(p, 1)}${td(p, 0)}</tr>`).join('')}</tbody></table>
    <div class="faint cnote">Gemini 3.5 Flash-Lite reads each diff and the parent's ledger and predicts the outcome in a second, before the minutes-long storm run. Green cells are correct calls.</div></div>`;
}

// ---------------------------------------------------------------- overview (AlphaEvolve-style explainer)
document.querySelectorAll('[data-go]').forEach((a) => a.addEventListener('click', (e) => { e.preventDefault(); setTab(a.dataset.go); }));
function renderOverview() {
  const gens = [...new Set(D.cands.map((c) => c.gen))].sort((a, b) => a - b);
  const seeds = D.scored.filter((c) => c.gen === 0);
  const seedBest = seeds.length ? Math.max(...seeds.map((c) => c.score.net_benefit_usd)) : null;
  const best = D.best ? D.best.score.net_benefit_usd : null;
  const ratio = best && seedBest > 0 ? best / seedBest : null;
  const nAgents = (S.agents || []).length || 11;
  void ratio; void nAgents;
  $('#ovStats').innerHTML = `
    <div class="ov-hero"><span class="from">${seedBest != null ? money(seedBest) : '—'}</span><span class="arr">→</span><span class="to">${best != null ? money(best) : '—'}</span></div>
    <div class="ov-cap">net benefit of the best plan, first try → after ${Math.max(0, gens.length - 1)} generations · ${D.cands.length} plans tried</div>`;
  renderOvWatch();
  renderOvLoop();
  renderOvChart(gens, seedBest);
}
// "Argon watched the replay" moment (state.watch is written by the visual-verification step, if enabled)
function renderOvWatch() {
  const box = $('#ovWatch');
  if (!box) return;
  const w = (S.watch || []).filter((x) => x.status === 'ok' && x.critique).slice(-1)[0];
  if (!w) { box.innerHTML = ''; return; }
  const sentence = String(w.critique).split(/(?<=[.!?])\s/)[0];
  const fixes = D.scored.filter((c) => c.gen === w.applied_to_gen && String(c.directive || '').startsWith('Visual verification directive'));
  const fix = fixes.sort((a, b) => b.score.net_benefit_usd - a.score.net_benefit_usd)[0];
  const img = w.media?.images?.[0];
  box.innerHTML = `${img ? `<img src="${esc(img)}" alt="replay still Argon watched">` : ''}
    <div><div class="k">Argon watched the replay of ${esc(w.cand)}</div><div class="q">“${esc(sentence)}”</div>
    ${fix ? `<div class="k">→ fixed in <a href="?job=${encodeURIComponent(S.id)}&tab=evolution&cand=${encodeURIComponent(fix.id)}">${esc(fix.id)}</a> · ${money(fix.score.net_benefit_usd)}</div>` : ''}</div>`;
}
function renderOvLoop() {
  const box = $('#ovLoop');
  box.innerHTML = '';
  const W = 1000, H = 520, cx = 500, cy = 262, R = 168;
  const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'xMidYMid meet' });
  box.appendChild(svg);
  const defs = el('defs', {}, svg);
  const mk = el('marker', { id: 'ovArrow', viewBox: '0 0 10 10', refX: 8, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse' }, defs);
  el('path', { d: 'M0,0 L10,5 L0,10 z', fill: 'rgba(0,0,0,.55)' }, mk);
  const glow = el('filter', { id: 'ovGlow', x: '-30%', y: '-30%', width: '160%', height: '160%' }, defs);
  el('feGaussianBlur', { stdDeviation: 6, result: 'b' }, glow);
  const fm = el('feMerge', {}, glow); el('feMergeNode', { in: 'b' }, fm); el('feMergeNode', { in: 'SourceGraphic' }, fm);
  const running = S.status === 'running';
  const cnt = archCounts();
  const st = S.stage;
  const boxes = [
    { n: 1, t: 'Pick the best plans', s: [], x: cx, y: cy - R, c: '#0a0a0a', on: running && st === 'mutating' && cnt.flash === 0 && cnt.argon === 0 },
    { n: 2, t: 'Gemini writes changes', s: [], x: cx + R * 1.55, y: cy, c: '#0a0a0a', on: running && (cnt.argon + cnt.flash + cnt.review > 0 || st === 'architect' || st === 'seeding') },
    { n: 3, t: 'Simulate the storm', s: [], x: cx, y: cy + R, c: '#0a0a0a', on: running && cnt.check + cnt.sim > 0 },
    { n: 4, t: 'Keep the winners', s: [], x: cx - R * 1.55, y: cy, c: '#0a0a0a', on: running && st === 'selecting' },
  ];
  // ring path (ellipse) for the travelling pulse
  const rx = R * 1.55, ry = R;
  const ring = `M ${cx} ${cy - ry} A ${rx} ${ry} 0 0 1 ${cx + rx} ${cy} A ${rx} ${ry} 0 0 1 ${cx} ${cy + ry} A ${rx} ${ry} 0 0 1 ${cx - rx} ${cy} A ${rx} ${ry} 0 0 1 ${cx} ${cy - ry}`;
  el('path', { d: ring, fill: 'none', stroke: 'rgba(0,0,0,.10)', 'stroke-width': 2 }, svg);
  // arrows between boxes along the ring (quarter arcs, trimmed)
  const P = (deg) => [cx + rx * Math.cos(deg * Math.PI / 180), cy + ry * Math.sin(deg * Math.PI / 180)];
  const arcs = [[-90, 0], [0, 90], [90, 180], [180, 270]].map(([a, b]) => {
    const [x1, y1] = P(a + 34), [x2, y2] = P(b - 30);
    return `M ${x1} ${y1} A ${rx} ${ry} 0 0 1 ${x2} ${y2}`;
  });
  arcs.forEach((d) => el('path', { d, fill: 'none', stroke: 'rgba(0,0,0,.45)', 'stroke-width': 2, 'marker-end': 'url(#ovArrow)' }, svg));
  if (running) {
    const pulse = el('circle', { r: 6, fill: '#0a0a0a', class: 'ov-pulse' }, svg);
    el('animateMotion', { dur: '6s', repeatCount: 'indefinite', path: ring }, pulse);
  }
  boxes.forEach((b) => {
    const w = 300, hh = 64;
    const g = el('g', { class: 'ov-node' + (b.on ? ' on' : '') }, svg);
    el('rect', { x: b.x - w / 2, y: b.y - hh / 2, width: w, height: hh, rx: 16, fill: '#ffffff', stroke: b.c, 'stroke-opacity': b.on ? 1 : 0.18, 'stroke-width': b.on ? 2.5 : 1.5, filter: b.on ? 'url(#ovGlow)' : null }, g);
    el('circle', { cx: b.x - w / 2 + 24, cy: b.y - hh / 2 + 26, r: 13, fill: b.c }, g);
    const nn = el('text', { x: b.x - w / 2 + 24, y: b.y - hh / 2 + 31, 'text-anchor': 'middle', class: 'ov-n' }, g); nn.textContent = b.n;
    const tt = el('text', { x: b.x - w / 2 + 50, y: b.y - hh / 2 + 33, class: 'ov-t' }, g); tt.textContent = b.t;
    b.s.forEach((line, i) => { const s = el('text', { x: b.x - w / 2 + 20, y: b.y - hh / 2 + 62 + i * 21, class: 'ov-s' }, g); s.textContent = line; });
  });
  // inputs (left) and output (right) as side notes
  const side = (x, y, lines, cls) => { lines.forEach((l, i) => { const t = el('text', { x, y: y + i * 22, class: cls }, svg); t.textContent = l; }); };
  const budget = money(S.config?.budget_usd || 50e6, { dp: 0 });
  void side; void budget;
}
function renderOvChart(gens, seedBest) {
  const box = $('#ovChart');
  const { svg, W, H } = svgIn(box);
  const pts = [];
  let run = null;
  gens.forEach((g) => {
    const v = D.scored.filter((c) => c.gen === g).map((c) => c.score.net_benefit_usd);
    if (v.length) run = run == null ? Math.max(...v) : Math.max(run, ...v);
    if (run != null) pts.push({ g, v: run });
  });
  if (!pts.length) { const t = el('text', { x: W / 2, y: H / 2, 'text-anchor': 'middle', class: 'ov-ax' }, svg); t.textContent = 'waiting for the first scored plans…'; return; }
  const m = { l: 40, r: 40, t: 50, b: 56 };
  const lo = Math.min(0, ...pts.map((p) => p.v)), hi = Math.max(...pts.map((p) => p.v)) * 1.12 || 1;
  const gMax = Math.max(1, pts[pts.length - 1].g);
  const x = (g) => m.l + (W - m.l - m.r) * (g / gMax);
  const y = (v) => H - m.b - (H - m.t - m.b) * ((v - lo) / (hi - lo));
  el('line', { x1: m.l, x2: W - m.r, y1: y(lo), y2: y(lo), stroke: 'rgba(0,0,0,.15)' }, svg);
  pts.forEach((p) => { const t = el('text', { x: x(p.g), y: H - m.b + 32, 'text-anchor': 'middle', class: 'ov-ax' }, svg); t.textContent = p.g === 0 ? 'first plans' : 'gen ' + p.g; });
  let d = `M ${x(pts[0].g)} ${y(pts[0].v)}`;
  pts.slice(1).forEach((p) => { d += ` H ${x(p.g)} V ${y(p.v)}`; });
  const area = d + ` V ${y(lo)} H ${x(pts[0].g)} Z`;
  el('path', { d: area, fill: 'rgba(0,0,0,.07)' }, svg);
  el('path', { d, fill: 'none', stroke: '#0a0a0a', 'stroke-width': 3.5, 'stroke-linejoin': 'round' }, svg);
  if (false && seedBest != null) {
    el('line', { x1: m.l, x2: W - m.r, y1: y(seedBest), y2: y(seedBest), stroke: 'rgba(0,0,0,.35)', 'stroke-dasharray': '5 6' }, svg);
    const t = el('text', { x: W - m.r, y: y(seedBest) + 22, 'text-anchor': 'end', class: 'ov-ax' }, svg); t.textContent = 'best first plan ' + money(seedBest);
  }
  pts.forEach((p, i) => {
    el('circle', { cx: x(p.g), cy: y(p.v), r: i === pts.length - 1 ? 9 : 6, fill: i === pts.length - 1 ? '#0a0a0a' : '#ffffff', stroke: '#0a0a0a', 'stroke-width': 3 }, svg);
    if (i !== 0 && i !== pts.length - 1) return;
    const t = el('text', { x: x(p.g), y: y(p.v) - 18, 'text-anchor': i === 0 ? 'start' : 'end', class: 'ov-val' + (i === pts.length - 1 ? ' last' : '') }, svg);
    t.textContent = money(p.v);
  });
}
