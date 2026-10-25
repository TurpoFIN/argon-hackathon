// Storytelling: per-run stats timelines (people at risk, flooded buildings, trees down, peak depth)
// and a data-driven list of narrative events used for captions + timeline markers.
import type { City, Run, PlanObject } from './data';
import type { TileFrame } from './geo';

export interface RunStats {
  nF: number; t: number[];
  atRisk: Int32Array; sheltered: Int32Array; flooded: Int32Array; maxDepth: Float32Array; maxAt: [number, number][];
  treeT: number[]; treeXY: [number, number][]; riskXY: ([number, number] | null)[];
  peakF: number;
}

export interface StoryEvent { t: number; text: string; kind: string; marker?: string; color: string; pri: number }

const PLACES: [string, number, number][] = [
  ['17th & Folsom', -122.4152, 37.7634], ['Mission Creek', -122.3935, 37.7705], ['the Embarcadero', -122.3937, 37.7955],
  ['the Market St canyon', -122.3990, 37.7915], ['Folsom & 7th, SoMa', -122.4080, 37.7757], ['Civic Center', -122.4180, 37.7793],
  ['the Division St underpass', -122.4110, 37.7695], ['Potrero Hill', -122.4010, 37.7600], ['24th & Mission', -122.4183, 37.7524],
  ['Dogpatch', -122.3880, 37.7590], ['Hayes Valley', -122.4245, 37.7760], ['Folsom & 11th', -122.4140, 37.7720],
  ['Harrison & 15th', -122.4125, 37.7668], ['Union Square', -122.4075, 37.7880], ['Rincon Hill', -122.3920, 37.7860],
  ['Showplace Square', -122.4030, 37.7670], ['Chinatown', -122.4078, 37.7941], ['the Tenderloin', -122.4140, 37.7840],
  ['Mission Bay', -122.3910, 37.7680], ['18th & Harrison', -122.4125, 37.7618], ['Bryant & 8th', -122.4060, 37.7720],
];
let placeLocal: [string, number, number][] | null = null;
export function placeName(tile: TileFrame, x: number, y: number) {
  if (!placeLocal) placeLocal = PLACES.map(([n, lon, lat]) => { const [px, py] = tile.lonLatToLocal(lon, lat); return [n, px, py]; });
  let best = placeLocal[0], bd = Infinity;
  for (const p of placeLocal) { const d = (p[1] - x) ** 2 + (p[2] - y) ** 2; if (d < bd) { bd = d; best = p; } }
  return best[0];
}

export function computeStats(run: Run, city: City): RunStats {
  const { nx, ny, dx, nFrames: nF } = run; const N = nx * ny;
  const atRisk = new Int32Array(nF), sheltered = new Int32Array(nF), flooded = new Int32Array(nF), maxDepth = new Float32Array(nF);
  const maxAt: [number, number][] = []; const riskXY: ([number, number] | null)[] = [];
  const bay = city.bay && city.bay.length === N ? city.bay : null;
  // dry-at-start land cells only (exclude bay + permanent channels)
  const dry = new Uint8Array(N); for (let i = 0; i < N; i++) dry[i] = (!bay || !bay[i]) && run.depth[i] < 50 ? 1 : 0;
  for (let f = 0; f < nF; f++) {
    const o = f * N; let m = 0, mi = 0;
    for (let i = 0; i < N; i += 1) { if (!dry[i]) continue; const d = run.depth[o + i]; if (d > m) { m = d; mi = i; } }
    maxDepth[f] = Math.min(m, 6000) / 1000; maxAt.push([((mi % nx) + 0.5) * dx, (Math.floor(mi / nx) + 0.5) * dx]);
  }
  // flooded buildings: >= 0.25 m water next to the building centroid
  const cells: number[] = [];
  for (const b of city.buildings) {
    if (b.fp.length < 3) continue; let cx = 0, cy = 0; for (const p of b.fp) { cx += p[0]; cy += p[1]; } cx /= b.fp.length; cy /= b.fp.length;
    const ix = Math.floor(cx / dx), iy = Math.floor(cy / dx); if (ix < 1 || iy < 1 || ix >= nx - 1 || iy >= ny - 1) continue; cells.push(iy * nx + ix);
  }
  for (let f = 0; f < nF; f++) {
    const o = f * N; let c = 0;
    for (const ci of cells) {
      let m = 0; for (let a = -1; a <= 1; a++) for (let b = -1; b <= 1; b++) { const d = run.depth[o + ci + a * nx + b]; if (d > m) m = d; }
      if (m >= 250) c++;
    }
    flooded[f] = c;
  }
  // people
  const P = run.people, n = run.nPeople;
  for (let f = 0; f < nF; f++) {
    let r = 0, s = 0, sx = 0, sy = 0;
    if (P && n) for (let i = 0; i < n; i++) {
      const k = (f * n + i) * 3; const st = Math.round(P[k + 2]);
      if (st === 3 || st === 5) { r++; let x = P[k], y = P[k + 1]; if (x > 1e5) { x -= city.meta.x0; y -= city.meta.y0; } sx += x; sy += y; } else if (st === 2) s++;
    }
    atRisk[f] = r; sheltered[f] = s; riskXY.push(r ? [sx / r, sy / r] : null);
  }
  // trees
  const tIdx = new Map<number, number>(); city.trees.forEach((t, i) => tIdx.set(t.id, i));
  const tr = run.trees.filter((t) => t.fail_t != null && isFinite(t.fail_t as number)).map((t) => { const ci = tIdx.get(t.id); const c = ci != null ? city.trees[ci] : null; return { t: t.fail_t as number, xy: (c ? [c.x, c.y] : [0, 0]) as [number, number] }; }).sort((a, b) => a.t - b.t);
  let peakF = 0, pv = -1;
  for (let f = 0; f < nF; f++) { const v = flooded[f] * 1000 + maxDepth[f]; if (v > pv) { pv = v; peakF = f; } }
  return { nF, t: run.t, atRisk, sheltered, flooded, maxDepth, maxAt, treeT: tr.map((x) => x.t), treeXY: tr.map((x) => x.xy), riskXY, peakF };
}

export const treesDownAt = (s: RunStats, t: number) => { let lo = 0, hi = s.treeT.length; while (lo < hi) { const m = (lo + hi) >> 1; if (s.treeT[m] <= t) lo = m + 1; else hi = m; } return lo; };
export const statAt = (s: RunStats, arr: ArrayLike<number>, t: number) => {
  if (t <= 0) return 0; const ts = s.t; const n = ts.length; if (t >= ts[n - 1]) return arr[n - 1];
  const dt = (ts[n - 1] - ts[0]) / (n - 1); const f = Math.max(0, Math.min(n - 2, Math.floor((t - ts[0]) / dt))); const a = (t - ts[f]) / (ts[f + 1] - ts[f] || 1);
  return arr[f] + (arr[f + 1] - arr[f]) * a;
};

export const fmtT = (t: number) => {
  if (t < 0) { const h = Math.round(-t / 3600); return `T−${h} h`; }
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60); return `T+${h}:${String(m).padStart(2, '0')}`;
};
const usdS = (v: number) => (v >= 1e9 ? `$${(v / 1e9).toFixed(2)}B` : v >= 1e6 ? `$${(v / 1e6).toFixed(1)}M` : `$${Math.round(v / 1e3)}k`);
const nf = (v: number) => Math.round(v).toLocaleString('en-US');

const CAT: [string, RegExp][] = [
  ['alert', /^alert/], ['evac', /evacua/], ['shelter', /shelter/],
  ['flood', /tunnel|storage|tank|cistern|seawall|wall|levee|gate|pump|custom_mesh|inlet|regrade|sandbag|berm/],
  ['green', /permeable|bioswale|green_roof/], ['trees', /brace|prune|tree|wind_screen/],
  ['logistics', /depot|supply|crew|medical|generator|truck/], ['road', /road_closure|close_road/],
];

/** Narrative events for one run. `base` = no-plan stats/run for comparisons (null when this run is the baseline). */
export function buildStory(run: Run, s: RunStats, tile: TileFrame, opts: { isPlan: boolean; model: string; baseRun?: Run | null; baseStats?: RunStats | null }): StoryEvent[] {
  const ev: StoryEvent[] = []; const who = /argon/i.test(opts.model) ? 'Gemini 4 Argon' : /flash/i.test(opts.model) ? 'Gemini Flash' : 'Gemini';
  // ---- prep: group plan objects by category within 24 h buckets ----
  const groups = new Map<string, PlanObject[]>();
  for (const o of run.plan) {
    if (!isFinite(o.t_s)) continue; const c = (CAT.find(([, r]) => r.test(o.type)) || ['other'])[0];
    if (c === 'alert') { ev.push({ t: o.t_s, text: `${o.t_s < 0 ? 'Alert' : who + ' alert'}: ${o.label || o.raw?.text || 'warning'}`, kind: 'alert', marker: o.t_s < 0 ? 'Alert' : 'Warning', color: '#ffb54a', pri: 3 }); continue; }
    const key = c + ':' + (c === 'flood' || c === 'green' ? 0 : Math.floor(o.t_s / 86400)); if (!groups.has(key)) groups.set(key, []); groups.get(key)!.push(o);
  }
  for (const [key, list] of groups) {
    const c = key.split(':')[0]; list.sort((a, b) => a.t_s - b.t_s); const t0 = list[0].t_s; const cost = list.reduce((x, o) => x + (o.cost_usd || 0), 0);
    const names = list.map((o) => o.label).filter(Boolean) as string[];
    const join = (a: string[], k = 3) => a.slice(0, k).join(', ') + (a.length > k ? ` +${a.length - k} more` : '');
    if (c === 'shelter') {
      const cots = list.reduce((x, o) => x + (+o.capacity! || 0), 0);
      ev.push({ t: t0, text: list.length === 1 ? `${who} opens ${names[0] || 'a shelter'} as a shelter${cots ? ` (${nf(cots)} cots)` : ''}` : `${who} opens ${list.length} shelters${cots ? ` · ${nf(cots)} cots` : ''}`, kind: 'shelter', marker: 'Shelters open', color: '#5dff9d', pri: 3 });
    } else if (c === 'evac') {
      ev.push({ t: t0, text: `${names.find((n) => /evac/i.test(n)) || 'Evacuation order issued'} — routes marked, vulnerable residents first`, kind: 'evac', marker: 'Evacuation order', color: '#ff5d5d', pri: 3 });
    } else if (c === 'flood') {
      // big-ticket items get their own caption, the rest one summary
      const big = list.filter((o) => (o.cost_usd || 0) >= 3e6), rest = list.filter((o) => (o.cost_usd || 0) < 3e6);
      for (const o of big) ev.push({ t: o.t_s, text: `${who} builds ${o.label || o.type.replace(/_/g, ' ')} · ${usdS(o.cost_usd)}`, kind: 'build', color: '#5fe1ff', pri: 2 });
      if (rest.length) ev.push({ t: rest[0].t_s, text: `Flood defences: ${join(rest.map((o) => o.label || o.type.replace(/_/g, ' ')))}`, kind: 'build', color: '#5fe1ff', pri: 1 });
    } else if (c === 'green') ev.push({ t: t0, text: `Green infrastructure soaks up rain: ${join(names)} · ${usdS(cost)}`, kind: 'build', color: '#5dff9d', pri: 1 });
    else if (c === 'trees') ev.push({ t: t0, text: `Tree crews: ${join(names)}`, kind: 'build', color: '#ffe15d', pri: 1 });
    else if (c === 'logistics') ev.push({ t: t0, text: `Crews & supplies staged: ${join(names.length ? names : ['supply runs'])}`, kind: 'build', color: '#ffb54a', pri: 1 });
    else if (c === 'road') ev.push({ t: t0, text: `${names[0] || 'Road closed'} — traffic diverted before the water comes`, kind: 'build', color: '#ff3d3d', pri: 1 });
    else ev.push({ t: t0, text: `${who} deploys ${join(names.length ? names : list.map((o) => o.type.replace(/_/g, ' ')))}`, kind: 'build', color: '#b48cff', pri: 1 });
  }
  if (!opts.isPlan) {
    ev.push({ t: -60 * 3600, text: 'No plan: the city waits — no shelters, no walls, no evacuation', kind: 'none', color: '#ff9d7a', pri: 2 });
    ev.push({ t: -12 * 3600, text: 'Storm drains, streets and residents face the storm as-is', kind: 'none', color: '#ff9d7a', pri: 1 });
  }
  // ---- storm ----
  const at = (f: number) => s.t[Math.max(0, Math.min(s.nF - 1, f))];
  const rain0 = Math.max(...run.rain), gust0 = Math.max(...run.gust);
  ev.push({ t: 0, text: `Landfall · atmospheric river hits San Francisco — rain up to ${Math.round(rain0)} mm/h, gusts ${Math.round(gust0)} m/s`, kind: 'landfall', color: '#ff9d7a', pri: 3 });
  const maxFl = Math.max(1, ...s.flooded);
  let ff = -1; for (let f = 0; f < s.nF; f++) if (s.flooded[f] >= Math.max(3, maxFl * 0.04) || s.maxDepth[f] > 0.3) { ff = f; break; }
  if (ff >= 0) { const [x, y] = s.maxAt[ff]; ev.push({ t: Math.max(600, at(ff)), text: `Storm drains near ${placeName(tile, x, y)} overwhelmed — streets flooding`, kind: 'flood', marker: 'First floods', color: '#4fa8ff', pri: 3 }); }
  if (opts.isPlan) {
    const sh = Math.max(...s.sheltered); if (sh > 20) ev.push({ t: Math.max(900, at(Math.min(s.nF - 1, Math.max(ff, 6)))), text: `${nf(sh)} residents already safe in ${who}'s shelters`, kind: 'people', color: '#5dff9d', pri: 2 });
  }
  let rf = -1; for (let f = 0; f < s.nF; f++) if (s.atRisk[f] >= 10) { rf = f; break; }
  if (rf >= 0 && rf !== s.peakF) { const p = s.riskXY[rf]; ev.push({ t: at(rf), text: `${nf(s.atRisk[rf])} people caught in rising water${p ? ' near ' + placeName(tile, p[0], p[1]) : ''}`, kind: 'people', color: '#ff3833', pri: 2 }); }
  if (s.treeT.length > 3) {
    const bin = 1200; const h = new Map<number, number[]>(); s.treeT.forEach((t, i) => { const b = Math.floor(t / bin); if (!h.has(b)) h.set(b, []); h.get(b)!.push(i); });
    let bb = 0, bn = -1; for (const [b, l] of h) if (l.length > bn) { bn = l.length; bb = b; }
    const idx = h.get(bb)!; const tm = (bb + 1) * bin; let sx = 0, sy = 0; for (const i of idx) { sx += s.treeXY[i][0]; sy += s.treeXY[i][1]; }
    const cnt = s.treeT.filter((t) => t <= tm).length;
    ev.push({ t: Math.max(300, bb * bin + bin / 2), text: `${nf(cnt)} trees down — worst near ${placeName(tile, sx / idx.length, sy / idx.length)}`, kind: 'trees', marker: 'Trees down peak', color: '#ffe15d', pri: 2 });
  }
  const pf = s.peakF;
  ev.push({ t: Math.max(1200, at(pf)), text: `Peak · ${s.maxDepth[pf].toFixed(1)} m of water · ${nf(s.atRisk[pf])} people at risk · ${nf(s.flooded[pf])} buildings flooded`, kind: 'peak', marker: 'Peak', color: '#ff4d4d', pri: 3 });
  const tEnd = s.t[s.nF - 1];
  const dmg = run.info.total_usd;
  let endTxt = `Aftermath · ${usdS(dmg)} in damage · ${nf(Math.max(...s.flooded))} buildings flooded · ${nf(s.treeT.length)} trees down`;
  if (opts.isPlan && opts.baseRun) {
    const avoided = opts.baseRun.info.total_usd - dmg; const bcr = run.info.plan_cost_usd > 0 ? avoided / run.info.plan_cost_usd : NaN;
    endTxt = `Aftermath · ${who}'s plan avoided ${usdS(Math.max(0, avoided))} in damage${isFinite(bcr) ? ` · BCR ${bcr.toFixed(1)}` : ''} vs no plan`;
  }
  ev.push({ t: tEnd - 600, text: endTxt, kind: 'end', color: opts.isPlan ? '#5fe1ff' : '#ff9d7a', pri: 3 });
  return ev.sort((a, b) => a.t - b.t);
}

/** Deterministic caption slots along the playback axis u (0..1) so scrubbing and replay show the same captions. */
export interface CaptionSlot { u: number; ev: StoryEvent }
export function layoutCaptions(evs: StoryEvent[], uOf: (t: number) => number, gap: number, maxLag: number): CaptionSlot[] {
  const out: CaptionSlot[] = []; let last = -1;
  const sorted = [...evs].sort((a, b) => a.t - b.t);
  // first pass: drop low priority items that would lag too much
  for (const e of sorted) {
    const u = uOf(e.t); const s = Math.max(u, last + gap);
    if (s - u > maxLag && e.pri < 3) continue;
    out.push({ u: s, ev: e }); last = s;
  }
  return out;
}
