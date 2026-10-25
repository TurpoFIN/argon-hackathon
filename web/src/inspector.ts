// Plan inspector: left panel listing Gemini's plan by category, object card (click / hover), focus ring.
import * as THREE from 'three';
import type { PlanObject } from './data';
import { TYPE_COLORS } from './plan';
import { fmtT } from './story';

export interface InspItem { o: PlanObject; pos: THREE.Vector3; cat: string; color: string; major: boolean; name: string }

export const CATS: [string, string, RegExp][] = [
  ['flood', 'Flood defences', /wall|gate|berm|levee|sandbag|custom_mesh|barrier/],
  ['drain', 'Drainage', /pump|tank|storage|cistern|tunnel|drain|inlet|permeable|bioswale|green_roof|regrade|culvert/],
  ['emerg', 'Emergency response', /shelter|evacua|crew|medical|rescue|boat/],
  ['logi', 'Logistics', /depot|supply|truck|generator/],
  ['ops', 'City operations', /road|closure|alert|tow|power|tree|brace|prune|wind/],
];
export const catOf = (type: string) => (CATS.find(([, , r]) => r.test(type)) || ['ops'])[0];
const GLYPH: Record<string, string> = { flood: '▮', drain: '◉', emerg: '✚', logi: '▲', ops: '◆' };
const usd = (v: number) => (v >= 1e9 ? `$${(v / 1e9).toFixed(2)}B` : v >= 1e6 ? `$${(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `$${Math.round(v / 1e3)}k` : v > 0 ? `$${Math.round(v)}` : '—');
const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]!));

const WHY: [RegExp, string][] = [
  [/tunnel/, 'Diverts basin runoff under the city to the bay, so low streets drain while it is still raining.'],
  [/storage|tank|cistern/, 'Holds the first flush of runoff so the combined sewer does not surcharge at the low point.'],
  [/seawall|levee|wall/, 'Raises the shoreline above the forecast tide + surge so the bay cannot pour into the streets.'],
  [/gate/, 'Closes the creek mouth at high tide so storm water cannot be pushed back inland.'],
  [/berm|sandbag/, 'Low temporary barrier that keeps shallow sheet flow out of doorways and garages.'],
  [/pump/, 'Pumps the sump out to the bay when gravity outfalls are blocked by the high tide.'],
  [/permeable|bioswale|green_roof/, 'Soaks up rain where it falls, cutting peak runoff into the drains.'],
  [/shelter/, 'Dry, staffed beds on high ground for residents in the flood zone.'],
  [/evacua/, 'Moves vulnerable residents out before streets flood, along routes that stay dry.'],
  [/crew|rescue|boat/, 'Pre-positioned crews cut response time once water rises.'],
  [/medical/, 'Treats injuries near the flood zone without crossing flooded streets.'],
  [/depot|supply|truck/, 'Water, meals and cots staged before roads close.'],
  [/road|closure/, 'Keeps cars out of the underpass that floods first.'],
  [/brace|prune|tree/, 'Braces the street trees most likely to fail in forecast gusts.'],
  [/wind/, 'Breaks up the gust funnel in the high-rise canyon.'],
  [/alert/, 'Warns residents early enough to act.'],
];
export const reasonOf = (o: PlanObject) => {
  const r = o.raw || {}; const txt = r.notes || r.rationale || r.reasoning || r.reason || r.why || r.description;
  if (txt) return String(txt);
  return (WHY.find(([re]) => re.test(o.type)) || [null, 'Part of Gemini’s storm plan.'])[1];
};
export const nameOf = (o: PlanObject) => o.label || o.raw?.name || o.type.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase());

export class Inspector {
  el = document.createElement('div'); card = document.createElement('div'); ring = document.createElement('div');
  items: InspItem[] = []; private rows = new Map<string, HTMLElement>(); private head!: HTMLElement;
  pinned: InspItem | null = null; total = 0; hovered: InspItem | null = null;
  onPick: ((it: InspItem) => void) | null = null; onTour: (() => void) | null = null; onClose: (() => void) | null = null;
  constructor(parent: HTMLElement) {
    this.el.className = 'insp empty'; this.card.className = 'icard hidden'; this.ring.className = 'iring hidden';
    parent.append(this.el, this.card, this.ring);
    this.card.addEventListener('click', (e) => { if ((e.target as HTMLElement).closest('.ic-x')) { this.pin(null); this.onClose?.(); } });
  }
  setItems(items: InspItem[], who: string, planCost = 0) {
    this.items = items; this.rows.clear(); this.pin(null);
    this.el.classList.toggle('empty', !items.length);
    if (!items.length) { this.el.innerHTML = `<div class="i-h"><b>NO PLAN</b><span>nothing deployed — the city faces the storm as-is</span></div>`; return; }
    const total = planCost || items.reduce((s, it) => s + (it.o.cost_usd || 0), 0); this.total = total;
    this.el.innerHTML = `<div class="i-h"><button class="i-col" title="collapse">‹</button><b>${esc(who)}’S PLAN</b><span>${items.length} actions · <em class="i-tot">${usd(total)}</em></span></div>
      <button class="i-tour">▶ Watch Gemini build the plan <kbd>B</kbd></button><div class="i-list"></div>`;
    this.head = this.el.querySelector('.i-tot') as HTMLElement;
    const list = this.el.querySelector('.i-list') as HTMLElement;
    for (const [k, title] of CATS) {
      const its = items.filter((i) => i.cat === k); if (!its.length) continue;
      const sec = document.createElement('div'); sec.className = 'i-sec';
      sec.innerHTML = `<div class="i-st"><span>${title}</span><em>${usd(its.reduce((s, i) => s + (i.o.cost_usd || 0), 0))}</em></div>`;
      for (const it of its.sort((a, b) => a.o.t_s - b.o.t_s)) {
        const r = document.createElement('div'); r.className = 'i-row';
        r.innerHTML = `<i style="--c:${it.color}">${GLYPH[k]}</i><b>${esc(it.name)}</b><span>${usd(it.o.cost_usd)}</span><small>${fmtT(it.o.t_s)}</small>`;
        r.addEventListener('click', () => this.onPick?.(it));
        r.addEventListener('mouseenter', () => { if (!this.pinned) this.show(it); });
        r.addEventListener('mouseleave', () => { if (!this.pinned) this.show(null); });
        sec.append(r); this.rows.set(it.o.id, r);
      }
      list.append(sec);
    }
    (this.el.querySelector('.i-col') as HTMLElement).onclick = () => this.el.classList.toggle('collapsed');
    (this.el.querySelector('.i-tour') as HTMLElement).onclick = () => this.onTour?.();
  }
  /** grey out rows not yet deployed at sim time t; returns running cost of deployed objects */
  setTime(t: number) {
    let c = 0;
    for (const it of this.items) { const r = this.rows.get(it.o.id); const on = t >= it.o.t_s; if (on) c += it.o.cost_usd || 0; if (r && r.classList.contains('built') !== on) r.classList.toggle('built', on); }
    return c;
  }
  setRunningCost(c: number, all: boolean) { if (this.head) this.head.textContent = all ? usd(this.total) : usd(c) + ' deployed'; }
  pin(it: InspItem | null) { this.pinned = it; this.show(it); for (const [id, r] of this.rows) r.classList.toggle('sel', !!it && id === it.o.id); if (it) this.rows.get(it.o.id)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); }
  show(it: InspItem | null) {
    if (!it) { if (this.pinned) it = this.pinned; else { this.card.classList.add('hidden'); return; } }
    const o = it.o; const r = o.raw || {};
    const cap = o.capacity ?? r.capacity_m3 ?? r.capacity_m3s ?? r.capacity_people;
    const src = r.cost_source || r.source || r.cost_basis || 'planner unit-cost estimate';
    this.card.style.setProperty('--c', it.color);
    this.card.innerHTML = `<button class="ic-x">×</button><div class="ic-k">${CATS.find((c) => c[0] === it.cat)?.[1] || ''} · ${esc(o.type.replace(/_/g, ' '))}</div>
      <div class="ic-n">${esc(it.name)}</div>
      <div class="ic-why"><span>GEMINI’S REASONING</span>${esc(reasonOf(o))}</div>
      <div class="ic-g"><div><span>COST</span><b>${usd(o.cost_usd)}</b><small>${esc(String(src))}</small></div>
      <div><span>DEPLOYS</span><b>${fmtT(o.t_s)}</b><small>${o.t_s < 0 ? 'before landfall' : 'during storm'}</small></div>
      ${cap ? `<div><span>CAPACITY</span><b>${typeof cap === 'number' ? cap.toLocaleString('en-US') : esc(String(cap))}</b><small>${/shelter/.test(o.type) ? 'cots' : ''}</small></div>` : ''}
      ${o.height ? `<div><span>HEIGHT</span><b>${o.height} m</b><small></small></div>` : ''}</div>`;
    this.card.classList.remove('hidden');
  }
  /** screen-space ring around the focused object (pos in renderer world coords already projected by caller) */
  setRing(x: number, y: number, visible: boolean, color = '#5fe1ff') {
    this.ring.classList.toggle('hidden', !visible); if (!visible) return;
    this.ring.style.transform = `translate(${x}px, ${y}px)`; this.ring.style.setProperty('--c', color);
  }
}
