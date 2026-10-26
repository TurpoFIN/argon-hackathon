// Stage 3 "How Argon learned": real job events only (run_result + the plan note that produced each run).
// Mock-sim runs are excluded unless ?src=synth. If nothing real exists yet: "waiting for runs".
interface RunPt { idx: number; runId: string; damage: number; atRisk: number | null; cost: number; note: string; mock: boolean }
interface JobLine { id: string; model: string; status: string; runs: RunPt[]; phase: string; budget: number }

const usd = (v: number) => (v >= 1e9 ? `$${(v / 1e9).toFixed(2)}B` : v >= 1e6 ? `$${(v / 1e6).toFixed(1)}M` : `$${Math.round(v / 1e3)}k`);
const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]!));
const num = (s: any, k: string) => { if (s && typeof s === 'object') return +s[k]; const m = String(s || '').match(new RegExp(`'${k}': ([-0-9.e]+)`)); return m ? +m[1] : NaN; };

async function readEvents(id: string, ms = 5000): Promise<any[]> {
  const out: any[] = [];
  try {
    const ctl = new AbortController(); const to = setTimeout(() => ctl.abort(), ms);
    const r = await fetch(`/api/jobs/${id}/events`, { signal: ctl.signal }); if (!r.ok || !r.body) return out;
    const rd = r.body.getReader(); const dec = new TextDecoder(); let buf = '';
    try { for (;;) { const { value, done } = await rd.read(); if (done) break; buf += dec.decode(value, { stream: true }); } } catch { /* aborted: live job */ }
    clearTimeout(to);
    for (const line of buf.split('\n')) if (line.startsWith('data: ')) { try { out.push(JSON.parse(line.slice(6))); } catch { /* skip */ } }
  } catch { /* offline */ }
  return out;
}

export async function loadLearning(allowMock: boolean): Promise<{ jobs: JobLine[]; board: any[] }> {
  let jobs: any[] = []; let board: any[] = [];
  try { const r = await fetch('/api/jobs'); if (r.ok) jobs = await r.json(); } catch { /* none */ }
  try { const r = await fetch('/api/leaderboard'); if (r.ok) { const lb = await r.json(); board = (lb.entries || []).map((e: any) => ({ budget_usd: lb.budget_usd, ...e })); } } catch { /* none */ }
  const opt = jobs.filter((j) => /optimize|managed/.test(String(j.id || j.kind))).slice(0, 8);
  const lines: JobLine[] = [];
  await Promise.all(opt.map(async (j) => {
    const ev = await readEvents(j.id);
    let model = j.model || j.params?.model || ''; let phase = ''; let budget = NaN; const notes = new Map<number, string>(); const runs: RunPt[] = [];
    for (const e of ev) {
      if (e.model && !model) model = e.model;
      if (e.type === 'phase') phase = e.title || e.phase;
      if (e.type === 'job_start') budget = +e.budget_usd;
      if (e.type === 'critique' && e.run_index != null) notes.set(+e.run_index, String(e.text || e.critique || ''));
      if (e.type === 'code' && e.note) notes.set(+e.run_index, String(e.note));
      if (e.type === 'run_result') {
        const s = e.summary; const mock = String(e.mock) === 'true' || String(e.mock) === 'True' || num(s, 'mock') === 1;
        runs.push({ idx: +e.run_index, runId: e.run_id, damage: num(s, 'damage_total_usd'), atRisk: ['lives_at_risk', 'at_risk_peak', 'people_at_risk_peak'].map((k) => num(s, k)).find((x) => isFinite(x)) ?? null, cost: num(s, 'plan_cost_usd'), note: '', mock });
      }
    }
    for (const r of runs) r.note = (notes.get(r.idx) || '').replace(/^Run \d+:\s*/, '');
    lines.push({ id: j.id, model, status: j.status || '', phase, budget, runs: runs.filter((r) => allowMock || !r.mock) });
  }));
  return { jobs: lines.sort((a, b) => (/argon/i.test(b.model) ? 1 : 0) - (/argon/i.test(a.model) ? 1 : 0)), board: board.filter((b) => allowMock || !/mock/.test(String(b.run_id))) };
}

export function renderLearning(el: HTMLElement, d: { jobs: JobLine[]; board: any[] }) {
  const argon = d.jobs.filter((j) => /argon/i.test(j.model));
  const withRuns = argon.find((j) => j.runs.length) || null;
  let html = `<div class="lr-h">HOW GEMINI 4 ARGON LEARNED <span>real simulation runs · from the job log</span></div>`;
  if (!withRuns) {
    const live = argon[0];
    html += `<div class="lr-wait">Waiting for runs — no completed real simulation run yet.${live ? `<small>Argon job ${esc(live.id)} · ${esc(live.status)}${live.phase ? ' · ' + esc(live.phase) : ''}</small>` : ''}</div>`;
  } else {
    const rs = [...withRuns.runs].sort((a, b) => a.idx - b.idx); const mx = Math.max(...rs.map((r) => r.damage).filter(isFinite), 1);
    html += `<div class="lr-runs">${rs.map((r) => `<div class="lr-run"><div class="lr-bar"><i style="height:${Math.max(4, (r.damage / mx) * 100)}%"></i></div>
      <div class="lr-t"><b>Run ${r.idx}</b> · damage <em>${isFinite(r.damage) ? usd(r.damage) : '–'}</em>${r.atRisk != null ? ` · ${Math.round(r.atRisk)} at risk` : ''}${isFinite(r.cost) ? ` · plan ${usd(r.cost)}` : ''}${r.mock ? ' · <u>mock sim</u>' : ''}
      <span>${esc(r.note || '(no critique recorded)')}</span><small>run ${esc(r.runId)}</small></div></div>`).join('')}</div>`;
  }
  const others = d.board.filter((b) => b.damage_usd != null);
  if (others.length) html += `<div class="lr-b"><div class="lr-bh">BENCHMARK · same storm, same data</div>${others.slice(0, 6).map((b) => `<div><b>${esc(b.name)}</b><span>${usd(+b.damage_usd)}</span><small>${b.bcr ? 'BCR ' + (+b.bcr).toFixed(1) : ''}</small></div>`).join('')}</div>`;
  else html += `<div class="lr-b"><div class="lr-bh">BENCHMARK</div><div class="lr-wait">Waiting for runs</div></div>`;
  el.innerHTML = html;
}

/** Stage 3 benchmark sheet: real runs only; pending jobs show "running…". */
export function renderBench(el: HTMLElement, d: { jobs: JobLine[]; board: any[] }, base: { damage: number; lives: number | null; runId: string } | null, brief: any) {
  type Row = { name: string; sub?: string; damage: number; lives: number | null; cost: number; budget: number; pending?: boolean; note?: string; runId?: string };
  const rows: Row[] = [];
  if (base) rows.push({ name: 'No plan', damage: base.damage, lives: base.lives, cost: 0, budget: NaN, runId: base.runId });
  const fam = (m: string) => (/argon/i.test(m) ? 'Gemini 4 Argon' : /flash-lite/i.test(m) ? 'Gemini Flash-Lite' : /flash/i.test(m) ? 'Gemini Flash' : m || 'model');
  const briefRuns: any[] = brief?.runs || [];
  const seen = new Set<string>();
  // One session per model, shown as try 1, 2, 3…: the session with the most finished tries (ties → best in-budget result).
  const score = (j: JobLine) => Math.min(...j.runs.filter((r) => isFinite(r.cost) && r.cost <= (j.budget || 50e6)).map((r) => r.damage + r.cost), Infinity);
  const pickJobs: JobLine[] = [];
  for (const fm of [...new Set(d.jobs.map((j) => fam(j.model)))]) {
    const js = d.jobs.filter((j) => fam(j.model) === fm && j.runs.length);
    if (js.length) pickJobs.push(js.sort((a, b) => b.runs.length - a.runs.length || score(a) - score(b))[0]);
  }
  for (const j of pickJobs) {
    const name = fam(j.model);
    const rs = [...j.runs].sort((a, b) => a.idx - b.idx);
    rs.forEach((r) => { seen.add(r.runId); const br = /argon/i.test(j.model) && [brief?.job_id, brief?.job, brief?.id].filter(Boolean).every((x: any) => x === j.id) ? briefRuns.find((x) => +x.run === r.idx) : null;
      rows.push({ name: `${name} · try ${r.idx}`, damage: r.damage, lives: br?.lives_at_risk ?? r.atRisk, cost: r.cost, budget: +(brief?.budget_usd ?? j.budget), note: br ? [br.critique, br.change_made].filter(Boolean).join(' → ') : r.note, runId: r.runId }); });
    if (!rs.length && /running|queued|awaiting/i.test(j.status)) rows.push({ name: `${name} · try 1`, damage: NaN, lives: null, cost: NaN, budget: NaN, pending: true, sub: j.phase });
  }
  for (const b of d.board) if (b.run_id && !seen.has(b.run_id) && !/no plan|baseline|argon|flash|gemini/i.test(b.name || '')) rows.push({ name: b.name, damage: +b.damage_usd, lives: b.lives_at_risk ?? b.people?.at_risk_peak ?? null, cost: +b.plan_cost_usd, budget: +(b.budget_usd ?? NaN), runId: b.run_id });
  const usd = (v: number) => (!isFinite(v) ? '–' : v >= 1e9 ? `$${(v / 1e9).toFixed(2)}B` : v >= 1e6 ? `$${(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `$${Math.round(v / 1e3)}k` : `$${Math.round(v)}`);
  const per = (r: Row) => (base && r.cost > 0 && isFinite(r.damage) ? (base.damage - r.damage) / r.cost : NaN);
  let best = -1, bv = -Infinity; rows.forEach((r, i) => { const v = per(r); if (isFinite(v) && v > bv) { bv = v; best = i; } });
  const body = rows.map((r, i) => `<tr title="${esc(r.runId || '')}" class="${i === best ? 'best' : ''} ${r.pending ? 'pend' : ''}"><td class="nm">${esc(r.name)}${i === best ? ' <em>best</em>' : ''}</td>
    <td>${r.pending ? 'running…' : usd(r.damage)}</td><td>${r.lives != null && isFinite(+r.lives) ? Math.round(+r.lives).toLocaleString('en-US') : '–'}</td><td>${r.name === 'No plan' ? '—' : usd(r.cost)}</td>
    <td>${isFinite(r.budget) && isFinite(r.cost) ? (r.cost <= r.budget ? '<b class="ok">✓</b>' : '<b class="no">✗</b>') : '–'}</td><td>${isFinite(per(r)) ? '$' + per(r).toFixed(1) : '–'}</td></tr>
    ${r.note ? `<tr class="nt"><td colspan="6">${esc(r.note)}</td></tr>` : ''}`).join('');
  // Cost vs damage quadrant: x = plan cost (log), y = damage left after the storm. Bottom-left = cheap and effective.
  const quad = (() => {
    const pts = rows.filter((r) => !r.pending && isFinite(r.damage) && isFinite(r.cost));
    if (pts.length < 2) return '';
    const W = 760, H = 380, L = 70, R = 20, T = 24, B = 46;
    const budget = +(brief?.budget_usd ?? rows.find((r) => isFinite(r.budget))?.budget ?? 50e6);
    const cmin = 5e6, cmax = Math.max(1e9, ...pts.map((r) => r.cost * 1.3));
    const dmax = Math.max(...pts.map((r) => r.damage)) * 1.08;
    const X = (c: number) => L + (Math.log10(Math.max(cmin, c || cmin)) - Math.log10(cmin)) / (Math.log10(cmax) - Math.log10(cmin)) * (W - L - R);
    const Y = (d: number) => T + (1 - d / dmax) * (H - T - B);
    const col = (n: string) => /argon/i.test(n) ? '#4fd1ff' : /flash/i.test(n) ? '#ffb347' : /no plan/i.test(n) ? '#ff5c5c' : '#9aa7b4';
    const ticks = [5e6, 1e7, 5e7, 1e8, 5e8, 1e9].filter((t) => t <= cmax).map((t) => `<text x="${X(t)}" y="${H - B + 18}" class="qa">${t >= 1e9 ? '$' + t / 1e9 + 'B' : '$' + t / 1e6 + 'M'}</text>`).join('');
    const yt = [0, 0.25, 0.5, 0.75, 1].map((f) => `<text x="${L - 8}" y="${Y(f * dmax) + 4}" class="qa" text-anchor="end">${usd(f * dmax)}</text>`).join('');
    const dots = pts.map((r) => { const x = r.cost > 0 ? X(r.cost) : L; const y = Y(r.damage); const c = col(r.name); const lbl = r.name.replace(/^FINAL\s*/i, '').replace(/gemini[- ]?/i, '').replace(/\(.*?\)/g, '').trim();
      return `<circle cx="${x}" cy="${y}" r="9" fill="${c}" stroke="#0b1016" stroke-width="2"/><text x="${x + 13}" y="${y + 5}" class="ql" fill="${c}">${esc(lbl)}</text>`; }).join('');
    const bx = X(budget);
    return `<svg class="quad" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">
      <rect x="${L}" y="${T}" width="${bx - L}" height="${(H - B) - Y(dmax / 2)}" transform="translate(0 ${Y(dmax / 2) - T})" fill="rgba(79,209,255,0.08)"/>
      <line x1="${bx}" y1="${T}" x2="${bx}" y2="${H - B}" class="qline"/><text x="${bx + 6}" y="${T + 12}" class="qa">budget ${usd(budget)}</text>
      <line x1="${L}" y1="${H - B}" x2="${W - R}" y2="${H - B}" class="qax"/><line x1="${L}" y1="${T}" x2="${L}" y2="${H - B}" class="qax"/>
      <text x="${L + 10}" y="${H - B - 10}" class="qq">cheap & effective</text><text x="${W - R - 10}" y="${T + 14}" class="qq" text-anchor="end">expensive & ineffective</text>
      ${ticks}${yt}<text x="${(L + W - R) / 2}" y="${H - 6}" class="qa" text-anchor="middle">plan cost (log scale)</text>
      <text x="16" y="${(T + H - B) / 2}" class="qa" transform="rotate(-90 16 ${(T + H - B) / 2})" text-anchor="middle">damage after the storm</text>${dots}</svg>`;
  })();
  el.innerHTML = `<div class="bs-h">BENCHMARK <span>same storm · same data · real simulation runs only</span></div>
    ${quad}${rows.length > (base ? 1 : 0) ? `<table class="bs"><tr><th></th><th>Damage</th><th>Lives at risk</th><th>Plan cost</th><th>Within budget</th><th>Damage avoided per $1</th></tr>${body}</table>` : '<div class="lr-wait">Waiting for runs — no real model run has finished yet.</div>'}`;
}
