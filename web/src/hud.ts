// HUD: big mode label + run toggle, KPI cards, clock, full-width phase timeline with markers + speed,
// lower-third captions, legend, control bar, NWS banner, final comparison card, plan labels.
import * as THREE from 'three';
import type { PlanLabel } from './plan';

const $ = <T extends HTMLElement = HTMLElement>(html: string): T => { const d = document.createElement('div'); d.innerHTML = html.trim(); return d.firstElementChild as T; };
export const usd = (v: number, digits = 1) => {
  const a = Math.abs(v); const s = v < 0 ? '−' : '';
  return a >= 1e9 ? `${s}$${(a / 1e9).toFixed(2)}B` : a >= 1e6 ? `${s}$${(a / 1e6).toFixed(digits)}M` : a >= 1e3 ? `${s}$${(a / 1e3).toFixed(0)}k` : `${s}$${a.toFixed(0)}`;
};

export interface ColumnStat { label: string; model: string; spend: number; damage: number; finalDamage: number; baseline: number }
export interface Kpi { label: string; value: string; cls?: string; sub?: string }
export interface Phase { u0: number; u1: number; label: string; sub?: string; cls: string }
export interface Marker { u: number; label: string; color: string }

export class Hud {
  root = $(`<div id="hud"></div>`);
  banner = $(`<div class="banner hidden"><div class="b-tag">NWS LIVE</div><div class="b-text"></div></div>`);
  top = $(`<div class="topc"><div class="mode"><div class="m-l">NO PLAN</div><div class="m-s"></div></div>
    <div class="runbar"><button data-r="none">1 · No plan</button><button data-r="plan">2 · What Gemini decided</button><button data-r="learn">3 · Benchmark</button></div></div>`);
  brand = $(`<div class="brand"><div class="b1">STORMSIM <span>SF</span></div><div class="b2">El Niño atmospheric river · Gemini 4 Argon</div>
    <div class="clock"><div class="k-t">T−72 h</div><div class="k-p"></div><div class="k-d"></div><div class="k-w"></div></div></div>`);
  kpis = $(`<div class="kpis"></div>`);
  tl = $(`<div class="tl"><div class="tl-row"><button class="tl-play" title="Play / pause (space)">▶</button><div class="tl-speed"></div><div class="tl-now"></div></div>
    <div class="tl-bar"><div class="tl-phases"></div><div class="tl-fill"></div><div class="tl-marks"></div><div class="tl-head"></div></div></div>`);
  caption = $(`<div class="cap hidden"><div class="cap-t"></div><div class="cap-x"></div></div>`);
  legend = $(`<div class="legend"></div>`);
  ctl = $(`<div class="ctl"><button data-c="walk">Street level <kbd>F</kbd></button><div class="ctl-h">WASD move · Shift+mouse look · Q/E up/down</div></div>`);
  finalCard = $(`<div class="final hidden"></div>`);
  labels = $(`<div class="labels"></div>`);
  toasts = $(`<div class="toasts"></div>`);
  flash = $(`<div class="flash"></div>`);
  cols = $(`<div class="cols hidden"><div class="col none"><b>NO PLAN</b></div><div class="col argon"><b>GEMINI 4 ARGON PLAN</b></div></div>`);
  private labelEls: HTMLElement[] = [];
  private shownToasts = new Set<string>();
  private capKey = '';
  onSeek: ((u: number) => void) | null = null;
  onPlay: (() => void) | null = null;
  onSpeed: ((s: number) => void) | null = null;
  onRun: ((r: 'none' | 'plan' | 'cmp' | 'learn') => void) | null = null;
  onCtl: ((c: string) => void) | null = null;

  constructor() {
    document.body.append(this.root, this.flash);
    this.root.append(this.cols, this.labels, this.brand, this.top, this.banner, this.kpis, this.toasts, this.caption, this.legend, this.ctl, this.finalCard, this.tl);
    const bar = this.tl.querySelector('.tl-bar') as HTMLElement;
    const seek = (e: PointerEvent) => { const r = bar.getBoundingClientRect(); this.onSeek?.(Math.max(0, Math.min(1, (e.clientX - r.left) / r.width))); };
    bar.addEventListener('pointerdown', (e) => { seek(e); const mv = (ev: PointerEvent) => seek(ev); addEventListener('pointermove', mv); addEventListener('pointerup', () => removeEventListener('pointermove', mv), { once: true }); });
    (this.tl.querySelector('.tl-play') as HTMLElement).onclick = () => this.onPlay?.();
    const sp = this.tl.querySelector('.tl-speed') as HTMLElement;
    for (const s of [0.5, 1, 2, 4]) { const b = $(`<button data-s="${s}">${s}×</button>`); b.onclick = () => this.onSpeed?.(s); sp.append(b); }
    this.top.querySelectorAll('button').forEach((b) => (b.onclick = () => this.onRun?.((b as HTMLElement).dataset.r as any)));
    this.ctl.querySelectorAll('button').forEach((b) => (b.onclick = () => this.onCtl?.((b as HTMLElement).dataset.c!)));
    this.legend.innerHTML = `<div class="lg-r"><span class="lg-n">WATER</span><span class="lg-w"></span><span class="lg-s"><i>0.1</i><i>0.5</i><i>1 m</i><i>3 m+</i></span></div>
      <div class="lg-r"><span class="lg-n">PEOPLE</span>${[['home', '#bfc7d1'], ['moving', '#ffd940'], ['sheltered', '#59ff8c'], ['at risk', '#ff3833'], ['rescued', '#4dd9ff']].map(([n, c]) => `<span class="lg-d"><i style="background:${c}"></i>${n}</span>`).join('')}</div>
      <div class="lg-r"><span class="lg-n">PLAN</span>${[['walls / gates', '#5fe1ff'], ['shelters', '#5dff9d'], ['evacuation', '#ff5d5d'], ['routes', '#ffd25f'], ['pumps', '#ff9b3d'], ['storage', '#4fa8ff']].map(([n, c]) => `<span class="lg-d"><i class="sq" style="background:${c}"></i>${n}</span>`).join('')}</div>`;
  }
  showBanner(text: string) { (this.banner.querySelector('.b-text') as HTMLElement).innerHTML = text; this.banner.classList.remove('hidden'); }
  hideBanner() { this.banner.classList.add('hidden'); }
  setMode(label: string, sub: string, cls: string, run: 'none' | 'plan' | 'cmp' | 'learn', planName: string) {
    const l = this.top.querySelector('.m-l') as HTMLElement; if (l.textContent !== label) { l.textContent = label; this.top.className = 'topc ' + cls; }
    (this.top.querySelector('.m-s') as HTMLElement).innerHTML = sub;
    this.top.querySelectorAll('button').forEach((b) => b.classList.toggle('on', (b as HTMLElement).dataset.r === run));
    void planName;
    this.cols.classList.toggle('hidden', run !== 'cmp');
  }
  setClock(t: number, startDate: Date, phase: string, weather: string) {
    const s = Math.abs(t), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    const pre = t < 0;
    (this.brand.querySelector('.k-t') as HTMLElement).textContent = pre ? `T−${h} h ${String(m).padStart(2, '0')}m` : `T+${h}:${String(m).padStart(2, '0')}`;
    (this.brand.querySelector('.k-p') as HTMLElement).textContent = phase;
    const d = new Date(startDate.getTime() + t * 1000);
    (this.brand.querySelector('.k-d') as HTMLElement).textContent = d.toLocaleString('en-US', { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'America/Los_Angeles' }) + ' PT';
    (this.brand.querySelector('.k-w') as HTMLElement).innerHTML = weather;
    this.brand.classList.toggle('pre', pre);
    (this.tl.querySelector('.tl-now') as HTMLElement).innerHTML = `<b>${pre ? `T−${h} h` : `T+${h}:${String(m).padStart(2, '0')}`}</b><span>${phase}</span>`;
  }
  setKpis(k: Kpi[]) {
    if (this.kpis.children.length !== k.length) { this.kpis.innerHTML = ''; for (let i = 0; i < k.length; i++) this.kpis.append($(`<div class="kpi"><div class="kp-l"></div><div class="kp-v"></div><div class="kp-s"></div></div>`)); }
    k.forEach((x, i) => {
      const e = this.kpis.children[i] as HTMLElement; e.className = 'kpi ' + (x.cls || '');
      const L = e.children[0] as HTMLElement, V = e.children[1] as HTMLElement, S = e.children[2] as HTMLElement;
      if (L.textContent !== x.label) L.textContent = x.label; if (V.innerHTML !== x.value) V.innerHTML = x.value; const sub = x.sub || ''; if (S.innerHTML !== sub) S.innerHTML = sub;
    });
  }
  setTimelineStatic(phases: Phase[], marks: Marker[]) {
    const ph = this.tl.querySelector('.tl-phases') as HTMLElement; ph.innerHTML = '';
    for (const p of phases) ph.append($(`<div class="ph ${p.cls}" style="left:${p.u0 * 100}%;width:${(p.u1 - p.u0) * 100}%"><b>${p.label}</b>${p.sub ? `<span>${p.sub}</span>` : ''}</div>`));
    const mk = this.tl.querySelector('.tl-marks') as HTMLElement; mk.innerHTML = '';
    let lastU = -1, row = 0;
    for (const m of [...marks].sort((a, b) => a.u - b.u)) { row = m.u - lastU < 0.075 ? row + 1 : 0; lastU = m.u; mk.append($(`<div class="mk r${row}" style="left:${m.u * 100}%;--c:${m.color}"><i></i><span>${m.label}</span></div>`)); }
  }
  setTimeline(u: number, playing: boolean, speed: number) {
    (this.tl.querySelector('.tl-fill') as HTMLElement).style.width = `${u * 100}%`;
    (this.tl.querySelector('.tl-head') as HTMLElement).style.left = `${u * 100}%`;
    const pb = this.tl.querySelector('.tl-play') as HTMLElement; const g = playing ? '❚❚' : '▶'; if (pb.textContent !== g) pb.textContent = g;
    this.tl.querySelectorAll('.tl-speed button').forEach((b) => b.classList.toggle('on', +(b as HTMLElement).dataset.s! === speed));
  }
  setCaption(key: string, time: string, text: string, color: string) {
    if (key === this.capKey) return; this.capKey = key;
    if (!key) { this.caption.classList.add('hidden'); return; }
    (this.caption.querySelector('.cap-t') as HTMLElement).textContent = time;
    (this.caption.querySelector('.cap-x') as HTMLElement).textContent = text;
    this.caption.style.setProperty('--c', color);
    this.caption.classList.remove('hidden'); this.caption.classList.remove('in'); void this.caption.offsetWidth; this.caption.classList.add('in');
  }
  setCtl(walk: boolean, director: boolean, tour: boolean) {
    
    this.ctl.querySelector('[data-c="walk"]')!.classList.toggle('on', walk);
    void director;
    (this.ctl.querySelector('.ctl-h') as HTMLElement).textContent = walk ? 'street level · WASD move · drag or Shift+mouse look · F exit' : director ? 'demo running · P to stop' : 'WASD move · Shift+mouse look · Q/E up/down';
    document.body.classList.toggle('walking', walk);
  }
  showFinal(html: string | null) { if (!html) { this.finalCard.classList.add('hidden'); return; } this.finalCard.innerHTML = html; this.finalCard.classList.remove('hidden'); }
  setLabels(items: PlanLabel[], cam: THREE.Camera, world: THREE.Matrix4, w: number, h: number, t: number, offsetX = 0, scale = 1) {
    while (this.labelEls.length < items.length) { const e = $(`<div class="lbl"><i></i><b></b><span></span></div>`); this.labels.append(e); this.labelEls.push(e); }
    const v = new THREE.Vector3();
    for (let i = 0; i < this.labelEls.length; i++) {
      const e = this.labelEls[i]; const it = items[i];
      if (!it || t < it.t_s) { e.style.display = 'none'; continue; }
      v.copy(it.pos).applyMatrix4(world).project(cam);
      if (v.z > 1 || v.z < -1 || Math.abs(v.x) > 1.05 || Math.abs(v.y) > 1.05) { e.style.display = 'none'; continue; }
      e.style.display = '';
      if (e.dataset.k !== it.text) { e.dataset.k = it.text; (e.querySelector('b') as HTMLElement).textContent = it.text; (e.querySelector('span') as HTMLElement).textContent = it.sub; (e.querySelector('i') as HTMLElement).style.background = it.color; e.style.setProperty('--c', it.color); }
      e.style.transform = `translate(${offsetX + (v.x * 0.5 + 0.5) * w * scale}px, ${(-v.y * 0.5 + 0.5) * h}px)`;
    }
  }
  toast(id: string, text: string, kind = 'alert') {
    if (this.shownToasts.has(id)) return; this.shownToasts.add(id);
    const e = $(`<div class="toast ${kind}"><b>${kind === 'alert' ? 'ALERT' : kind.toUpperCase()}</b><span></span></div>`); (e.querySelector('span') as HTMLElement).textContent = text;
    this.toasts.prepend(e); setTimeout(() => e.classList.add('out'), 5200); setTimeout(() => e.remove(), 6000);
    while (this.toasts.children.length > 3) this.toasts.lastElementChild!.remove();
  }
  resetToasts() { this.shownToasts.clear(); }
  setFlash(a: number) { this.flash.style.opacity = String(a); }
}
