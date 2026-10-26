// Stage 2 "What Gemini decided": user-controlled step-through of the plan, one item per card.
import type { PlanObject } from './data';
import type { TileFrame } from './geo';
import { placeName } from './story';

/** Readable unit cost from a cost_basis value (string, {usd, unit} item, or list of them). */
function fmtBasis(cb: any): string {
  if (!cb) return '';
  if (typeof cb === 'string') return cb;
  const one = (x: any) => (x && typeof x === 'object' && x.usd != null ? `$${Math.round(+x.usd).toLocaleString('en-US')} per ${String(x.unit || 'unit').replace(/^m /, 'm of ')}` : '');
  const list = Array.isArray(cb) ? cb : [cb];
  return list.map(one).filter(Boolean).slice(0, 2).join(' · ');
}

export interface Step { kind: 'overview' | 'item' | 'ops'; objs: PlanObject[]; id?: string }
const usd = (v: number) => (v >= 1e9 ? `$${(v / 1e9).toFixed(2)}B` : v >= 1e6 ? `$${(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `$${Math.round(v / 1e3)}k` : v > 0 ? `$${Math.round(v)}` : '—');
const esc = (s: any) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]!));
const nf = (v: number) => Math.round(v).toLocaleString('en-US');

const KIND: [RegExp, string, string][] = [
  [/seawall/, 'Seawall', 'Raises the shoreline so the bay cannot pour into the streets at high tide.'],
  [/floodwall|^wall/, 'Floodwall', 'A wall that holds surface water out of the area behind it.'],
  [/levee|berm/, 'Earth berm', 'A low earth bank that diverts or holds back shallow flooding.'],
  [/sandbag/, 'Sandbag line', 'Temporary barrier that keeps shallow water out of doorways.'],
  [/gate|custom_mesh/, 'Flood gate', 'Closes an opening (creek mouth, underpass) when water rises.'],
  [/tunnel/, 'Relief tunnel', 'Carries storm water underground to the bay, bypassing the full sewer.'],
  [/storage|tank|cistern/, 'Storage tank', 'Holds storm water during the peak and releases it later.'],
  [/pump/, 'Pump', 'Pumps water out of a low spot when gravity drains cannot.'],
  [/permeable|bioswale|green_roof/, 'Green infrastructure', 'Soaks up rain where it falls, so less reaches the drains.'],
  [/inlet|drain/, 'Drain upgrade', 'Clears or enlarges street inlets so water gets into the sewer.'],
  [/shelter/, 'Shelter', 'Dry beds and supplies for people moved out of the flood zone.'],
  [/evacuation_zone|evacuate/, 'Evacuation zone', 'People in this area are moved out before the water arrives.'],
  [/evacuation_route|route/, 'Evacuation route', 'The path people take to shelters, kept clear of deep water.'],
  [/crew|rescue/, 'Crew', 'Staff and equipment staged before the storm.'],
  [/depot|supply|truck/, 'Supply depot', 'Food, water and equipment staged near the flood zone.'],
  [/road_closure|close_road/, 'Road closure', 'Keeps cars out of streets that will flood.'],
  [/brace|prune|tree/, 'Tree work', 'Braces or prunes trees likely to fall in strong gusts.'],
  [/wind/, 'Wind screen', 'Breaks up dangerous gusts between tall buildings.'],
  [/medical/, 'Medical post', 'First aid close to where people get hurt.'],
  [/alert/, 'Public alert', 'Tells residents what to do and when.'],
];
const kindOf = (o: PlanObject) => KIND.find(([r]) => r.test(o.type)) || [null, o.type.replace(/_/g, ' '), ''];
const lenOf = (c: number[][]) => { let l = 0; for (let i = 0; i < c.length - 1; i++) l += Math.hypot(c[i + 1][0] - c[i][0], c[i + 1][1] - c[i][1]); return l; };
const areaOf = (c: number[][]) => { let a = 0; for (let i = 0, j = c.length - 1; i < c.length; j = i++) a += (c[j][0] + c[i][0]) * (c[j][1] - c[i][1]); return Math.abs(a / 2); };
const centre = (c: number[][]) => { let x = 0, y = 0; for (const p of c) { x += p[0]; y += p[1]; } return [x / c.length, y / c.length]; };

export function plainName(o: PlanObject, tile: TileFrame) {
  const [, kind] = kindOf(o); const parts = [kind as string];
  if (o.height) parts.push(`${o.height} m${o.material ? ' ' + o.material : ''}`);
  const c = o.coords || []; const [cx, cy] = c.length ? centre(c) : [0, 0]; const where = c.length ? placeName(tile, cx, cy) : '';
  if (o.kind.includes('line') && c.length > 1) parts.push(`${nf(lenOf(c))} m near ${where}`);
  else if (o.kind.includes('poly') && c.length > 2) parts.push(`${(areaOf(c) / 1e4).toFixed(1)} ha near ${where}`);
  else if (where) parts.push(`at ${where}`);
  return parts.join(' · ');
}
const why = (o: PlanObject, brief: any) => {
  const r = o.raw || {}; const own = r.why || r.notes || r.rationale || r.reasoning || r.reason;
  if (own) return String(own);
  const pi = (brief?.plan_items || []).find((p: any) => p.name && (p.name === o.label || (o.label && String(o.label).toLowerCase().includes(String(p.name).toLowerCase().slice(0, 18)))));
  return pi?.why ? String(pi.why) : '';
};
const CATKEY: [RegExp, RegExp][] = [
  [/wall|gate|berm|levee|sandbag|custom_mesh/, /defen|wall|flood prot|barrier/i], [/pump|tank|storage|cistern|tunnel|drain|inlet|permeable|bioswale|green/, /drain|water|storm ?water|green/i],
  [/shelter|evacua|crew|medical|rescue/, /emergen|response|evacu|shelter/i], [/depot|supply|truck|generator/, /logist|supply/i], [/road|closure|tree|brace|alert|wind|tow|power/, /operat|city/i],
];
const optionsFor = (o: PlanObject, brief: any) => {
  const acts: any[] = brief?.actions || []; if (!acts.length) return '';
  const ck = CATKEY.find(([t]) => t.test(o.type)); const list = ck ? acts.filter((a) => ck[1].test(a.category || '')) : [];
  if (!list.length) return '';
  const t = o.type.replace(/_/g, ' ').toLowerCase().split(' ')[0];
  return `<div class="st-k">OPTIONS IT HAD</div><ul class="st-opt">${list.slice(0, 6).map((a) => { const on = String(a.name || '').toLowerCase().includes(t); return `<li class="${on ? 'on' : ''}">${on ? '✓ ' : ''}<b>${esc(a.name)}</b>${fmtBasis(a.cost_basis) ? ` <span>${esc(fmtBasis(a.cost_basis))}</span>` : ''}</li>`; }).join('')}</ul>`;
};

export function buildSteps(objs: PlanObject[]): Step[] {
  const ok = objs.filter((o) => isFinite(o.t_s) && o.type !== 'alert');
  const major = ok.filter((o) => (o.cost_usd || 0) >= 1e6 || /wall|gate|tunnel|pump|tank|storage|cistern|shelter|evacuation_zone|custom_mesh/.test(o.type));
  const top = [...major].sort((a, b) => (b.cost_usd || 0) - (a.cost_usd || 0)).slice(0, 9).sort((a, b) => a.t_s - b.t_s);
  const rest = ok.filter((o) => !top.includes(o));
  const steps: Step[] = [{ kind: 'overview', objs: ok }, ...top.map((o) => ({ kind: 'item' as const, objs: [o], id: o.id }))];
  if (rest.length) steps.push({ kind: 'ops', objs: rest });
  return steps;
}

export function renderStep(el: HTMLElement, steps: Step[], i: number, tile: TileFrame, brief: any, planCost: number, who: string) {
  const s = steps[i]; const nav = `<div class="st-nav"><button data-s="prev" ${i === 0 ? 'disabled' : ''}>← Prev</button><span>${i === 0 ? 'Overview' : `${i} of ${steps.length - 1}`}</span><button data-s="next" ${i >= steps.length - 1 ? 'disabled' : ''}>Next →</button></div>`;
  let body = '';
  if (!s) body = `<div class="st-t">No plan objects in this run.</div>`;
  else if (s.kind === 'overview') {
    const b = brief;
    const inputs = (b?.inputs || []).map((x: any) => `${x.count != null ? Number(x.count).toLocaleString('en-US') + ' ' : ''}${x.name}`).join(' · ');
    const items = steps.filter((x: any) => x.kind === 'item').map((x: any) => x.objs[0]);
    const built = items.slice(0, 6).map((o: any) => `<li><span>${esc(plainName(o, tile))}</span><b>${usd(o.cost_usd)}</b></li>`).join('');
    const strat = String(b?.strategy || ''); const stratShort = strat.length > 260 ? strat.slice(0, 257).replace(/\s+\S*$/, '') + '…' : strat;
    body = `<div class="st-kk">WHAT ${esc(who.toUpperCase())} DECIDED</div>
      <div class="st-t st-t-sm">Protect San Francisco from the El Niño storm with ${b?.budget_usd ? usd(+b.budget_usd) : '$50M'}</div>
      <div class="st-m">Plan cost <b>${usd(planCost)}</b> · ${items.length} major decisions + smaller operations</div>
      ${built ? `<div class="st-k">WHAT IT BUILT</div><ul class="st-built">${built}</ul>` : ''}
      <div class="st-k">MAP KEY</div><div class="st-key">
        <span><i style="background:#e8c27a"></i>tan lines · sandbag lines</span><span><i style="background:#ff8a1f"></i>orange arrows · evacuation routes</span>
        <span><i style="background:#ffd23f"></i>yellow · flood gates & barriers</span><span><i style="background:#a9b4be"></i>grey · floodwalls</span>
        <span><i style="background:#3a8bff"></i>blue · pumps & storage</span><span><i style="background:#5dff9d"></i>green · shelters & permeable streets</span>
        <span><i style="background:#ff3d3d"></i>red · closed roads</span><span><i style="background:#ffb347"></i>orange areas · evacuation zones</span></div>

      ${stratShort ? `<div class="st-k">ITS STRATEGY</div><blockquote class="st-q">“${esc(stratShort)}”</blockquote>` : (b ? '' : `<div class="st-wait">Briefing: waiting for Gemini.</div>`)}
      ${inputs ? `<div class="st-k">WHAT IT GOT${b.total_tokens ? ` · ≈${Math.round(b.total_tokens / 1000)}k tokens` : ''}</div><div class="st-got">${esc(inputs)}</div>` : ''}
      <div class="st-hint">Press → to step through each decision.</div>`;
  } else if (s.kind === 'item') {
    const o = s.objs[0]; const [, , does] = kindOf(o); const w = why(o, brief); const src = o.raw?.cost_source || fmtBasis(o.raw?.cost_basis) || o.raw?.source;
    body = `<div class="st-kk">${esc(o.label || '')}</div><div class="st-t">${esc(plainName(o, tile))}</div>
      ${does ? `<div class="st-k">WHAT IT DOES</div><div class="st-p">${esc(does)}</div>` : ''}
      <div class="st-k">WHY — IN GEMINI'S WORDS</div><div class="st-p ${w ? 'q' : 'dim'}">${w ? '“' + esc(w) + '”' : 'Gemini gave no reason for this item.'}</div>
      <div class="st-g"><div><span>COST</span><b>${usd(o.cost_usd)}</b>${src ? `<small>${esc(src)}</small>` : ''}</div><div><span>READY</span><b>${o.t_s < 0 ? (-o.t_s >= 72 * 3600 ? Math.round(-o.t_s / 86400) + ' days before landfall' : Math.round(-o.t_s / 3600) + ' h before landfall') : 'during storm'}</b></div></div>
      ${optionsFor(o, brief)}`;
  } else {
    const tot = s.objs.reduce((a, o) => a + (o.cost_usd || 0), 0);
    body = `<div class="st-kk">OPERATIONS</div><div class="st-t">${s.objs.length} smaller actions · ${usd(tot)}</div>
      <ul class="st-ops">${s.objs.slice(0, 12).map((o) => `<li><b>${esc(o.label || (kindOf(o)[1] as string))}</b><span>${usd(o.cost_usd)}</span></li>`).join('')}${s.objs.length > 12 ? `<li>+ ${s.objs.length - 12} more</li>` : ''}</ul>`;
  }
  el.innerHTML = nav + `<div class="st-body">${body}</div>`;
}
