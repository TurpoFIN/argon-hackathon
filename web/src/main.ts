import * as THREE from 'three';
import './style.css';
import { loadCity, listRuns, loadRun, pickRuns, type Run, type City } from './data';
import { TileFrame } from './geo';
import { LocalFrame, RunVisual, Wind, makeWaterGeometry, shared, treeGeoms } from './storm';
import { createArcgis, type ArcgisBase, type ShotCam } from './arcgis';
import { createStylized, makeRain, type Stylized } from './stylized';
import { Hud, usd, type Phase } from './hud';
import { computeStats, buildStory, layoutCaptions, statAt, treesDownAt, fmtT, type RunStats, type StoryEvent, type CaptionSlot } from './story';
import { Inspector, catOf, nameOf, reasonOf, CATS, type InspItem } from './inspector';
import { TYPE_COLORS } from './plan';
import { Walker } from './nav';
import { createAgentPanel } from './agentPanel';
import { cloudDeck, rainCurtains, spray, WindowLights, StormAudio, windDir, skyDome } from './fx';

const q = new URLSearchParams(location.search);
const shotMode = q.has('shot');
const STORM_START = new Date('2026-10-27T02:00:00-07:00'); // landfall used for the clock display

const loading = document.createElement('div'); loading.className = 'loading';
loading.innerHTML = '<div class="l1">STORMSIM SF</div><div class="l2">loading city · lidar · storm frames</div>';
document.body.append(loading);
const setLoading = (s: string) => { (loading.querySelector('.l2') as HTMLElement).textContent = s; };
const container = document.getElementById('scene')!;

interface Shot { lon: number; lat: number; dist: number; heading: number; tilt: number; compare?: boolean }
const SHOTS: Record<string, Shot> = {
  overview: { lon: -122.4060, lat: 37.7770, dist: 4600, heading: 245, tilt: 71 },
  basin: { lon: -122.4150, lat: 37.7640, dist: 1000, heading: 300, tilt: 72 },
  canyon: { lon: -122.4010, lat: 37.7890, dist: 700, heading: 225, tilt: 73 },
  embarcadero: { lon: -122.3925, lat: 37.7905, dist: 950, heading: 215, tilt: 74 },
  compare: { lon: -122.4100, lat: 37.7690, dist: 2600, heading: 320, tilt: 64, compare: true },
  plan: { lon: -122.4050, lat: 37.7740, dist: 5600, heading: 340, tilt: 35 },
};
// numeric ?shot=N in the URL is 0-based (server critique screenshots); keyboard 1-5 uses SHOT_KEYS
const URL_SHOTS = ['plan', 'basin', 'embarcadero', 'canyon', 'overview', 'compare'];
const SHOT_KEYS = ['overview', 'basin', 'canyon', 'embarcadero', 'compare'];

async function main() {
  setLoading('loading city');
  const city = await loadCity();
  (window as any).__x0 = city.meta.x0; (window as any).__y0 = city.meta.y0;
  const tile = new TileFrame(city.meta.x0, city.meta.y0, city.meta.nx * city.meta.dx, city.meta.ny * city.meta.dx);

  // ---- base renderer ----
  let arc: ArcgisBase | null = null; let sty: Stylized | null = null;
  const wantStylized = q.get('mode') === 'stylized';
  if (!wantStylized) {
    try {
      setLoading('connecting to photoreal San Francisco');
      arc = await createArcgis(container, tile, { quality: q.get('quality') || 'high' });
      // datum: align our NAVD88 DEM with the renderer's ground (median of samples)
      const pts: [number, number][] = []; const loc: [number, number][] = [];
      for (let k = 0; k < 160; k++) {
        const ix = Math.floor(Math.random() * city.meta.nx), iy = Math.floor(Math.random() * city.meta.ny), i = iy * city.meta.nx + ix;
        if (city.bay?.[i] || city.terrain[i] < 1) continue;
        const x = (ix + 0.5) * city.meta.dx, y = (iy + 0.5) * city.meta.dx; loc.push([x, y]); pts.push(tile.localToLonLat(x, y));
      }
      const el = await Promise.race([arc.queryElevation(pts), new Promise<null>((r) => setTimeout(() => r(null), 7000))]);
      if (el) { const d = el.map((z, i) => z - city.zAt(loc[i][0], loc[i][1])).filter((v) => isFinite(v)).sort((a, b) => a - b); if (d.length) tile.zOffset = d[Math.floor(d.length / 2)]; }
      console.log('datum offset (esri - navd88) m', tile.zOffset.toFixed(2));
    } catch (e) { console.warn('ArcGIS unavailable, falling back to stylized: ' + ((e as any)?.message || e) + ' ' + ((e as any)?.stack || '').slice(0, 400)); arc = null; container.innerHTML = ''; }
  }
  const frame = new LocalFrame(city, tile);
  if (!arc) sty = createStylized(container, city, frame);
  document.body.classList.add(arc ? 'photoreal' : 'stylized');
  if (!shotMode) { const v = document.createElement('div'); v.className = 'vignette'; document.body.append(v); }
  if (arc) { const g = document.createElement('div'); g.className = 'grade'; document.body.append(g); }

  // ---- runs ----
  setLoading('loading storm runs');
  const infos = pickRuns(await listRuns(), q);
  const settled = await Promise.allSettled(infos.map(loadRun));
  settled.forEach((r, i) => { if (r.status === 'rejected') console.warn('run failed', infos[i].run_id, String(r.reason)); });
  const runs: Run[] = settled.filter((r) => r.status === 'fulfilled').map((r) => (r as PromiseFulfilledResult<Run>).value);
  console.log('picked ' + infos.map((r) => r.run_id).join(',') + ' loaded ' + runs.map((r) => r.info.run_id).join(','));

  // ---- storm layer ----
  setLoading('building storm layer');
  const scene = sty ? sty.scene : new THREE.Scene();
  const root = sty ? sty.world : new THREE.Group();
  if (!sty) {
    root.matrixAutoUpdate = false; root.matrix.copy(frame.matrix); scene.add(root);
    scene.add(new THREE.HemisphereLight(0xb8c8d8, 0x30343a, 1.6));
    const dl = new THREE.DirectionalLight(0xdde6f0, 0.9); dl.position.set(-0.4, -0.6, 1); scene.add(dl);
  }
  const rainFx = sty ? sty.rain : makeRain(70000, 650); if (!sty) scene.add(rainFx);
  const dome = arc ? skyDome() : null; if (dome) scene.add(dome);
  const treeG = treeGeoms();
  let visuals: RunVisual[] = [];
  let waterGeo: ReturnType<typeof makeWaterGeometry> | null = null;
  if (runs.length) {
    waterGeo = makeWaterGeometry(frame, runs[0], city);
    visuals = runs.map((r) => new RunVisual(r, frame, city, waterGeo!, treeG));
    for (const v of visuals) root.add(v.group);
  }
  const base = runs[0];
  let wind: Wind | null = null; let uref = 10;
  const wf = runs.find((r) => r.wind)?.wind;
  if (base && wf) {
    let s = 0, n = 0; for (let i = 0; i < wf.length; i += 2) { const m = Math.hypot(wf[i], wf[i + 1]); if (m > 0.5 && isFinite(m)) { s += m; n++; } }
    uref = base.manifest.wind_u_ref ?? base.manifest.u_ref ?? (n ? s / n : 10);
    wind = new Wind(frame, city, wf, base.nx, base.ny, base.dx, shotMode ? 1600 : 2200); root.add(wind.lines);
  }

  // ---- atmosphere ----
  if (wf) { let su = 0, sv = 0; for (let i = 0; i < wf.length; i += 2) if (isFinite(wf[i])) { su += wf[i]; sv += wf[i + 1]; } const l = Math.hypot(su, sv); if (l > 0) windDir.value.set(su / l, sv / l); }
  const deckHi = cloudDeck(frame, 3000, 46000, { alpha: 0.92, speed: 0.035, scale: 2600, dark: 1.0 });
  const deckLo = cloudDeck(frame, 700, 18000, { alpha: 0.38, speed: 0.11, scale: 800, dark: 1.25 });
  const curtains = rainCurtains(frame, 44), sprayFx = spray(frame, city, 5000), lights = new WindowLights(frame, city);
  root.add(deckHi, deckLo, sprayFx, lights.points); if (q.get('curtains') === '1') root.add(curtains);
  const audio = new StormAudio();
  const startAudio = () => { if (!shotMode && q.get('audio') !== '0') audio.start(); };
  addEventListener('pointerdown', startAudio); addEventListener('keydown', startAudio);

  // ---- timeline: prep (−72 h → 0) takes PREP_U of the bar / playback, the 6 h storm the rest ----
  const tEnd = base ? base.t[base.t.length - 1] : 21600;
  const tStart = -72 * 3600;
  const PREP_U = 0.25, TOTAL_S = 100; // 1× = 25 s prep + 75 s storm
  const uOf = (tt: number) => (tt < 0 ? PREP_U * Math.max(0, (tt - tStart) / -tStart) : PREP_U + (1 - PREP_U) * Math.min(1, tt / tEnd));
  const tOf = (u: number) => (u < PREP_U ? tStart + (u / PREP_U) * -tStart : ((u - PREP_U) / (1 - PREP_U)) * tEnd);
  let t = q.has('t') ? +q.get('t')! : tStart;
  if (shotMode && !q.has('t') && base) { // critique shots default to peak flooding, all plan objects built
    const n = base.nx * base.ny; let best = 0, bf = 0;
    for (let f = 0; f < base.nFrames; f++) { let sum = 0; for (let i = f * n; i < (f + 1) * n; i += 7) sum += base.depth[i]; if (sum > best) { best = sum; bf = f; } }
    t = base.t[bf];
  }
  let playing = !shotMode && q.get('autoplay') === '1'; let speed = 1;
  const maxRain = base ? Math.max(1, ...base.rain) : 50, maxGust = base ? Math.max(1, ...base.gust) : 30;
  const interp = (arr: number[] | null | undefined, tt: number, d = 0) => { if (!arr || !base) return d; const fi = visuals[0].frameAt(tt); const f = Math.floor(fi); return arr[f] + ((arr[Math.min(arr.length - 1, f + 1)] ?? arr[f]) - arr[f]) * (fi - f); };

  // ---- runs: labels, roles ----
  const hud = new Hud();
  for (const v of visuals) { const i = v.run.info; const tx = i.label + ' ' + i.model + ' ' + i.run_id;
    i.label = /none|no.?plan|baseline/i.test(tx) && !/walls/i.test(tx) ? 'No plan' : /argon/i.test(tx) ? 'Gemini 4 Argon' : /flash/i.test(tx) ? 'Gemini Flash' : i.label; }
  const isNoneV = (v: RunVisual) => /none|no.?plan|baseline/i.test(v.run.info.label + v.run.info.model + v.run.info.run_id) || !v.run.plan.length;
  let noneIdx = Math.max(0, visuals.findIndex(isNoneV));
  const findPlan = () => { const a = visuals.findIndex((v) => /argon/i.test(v.run.info.label + v.run.info.model)); return a >= 0 ? a : Math.max(0, visuals.length - 1); };
  let planIdx = findPlan();
  let active = q.get('view') === 'plan' ? planIdx : noneIdx;
  if (q.has('run')) active = 0;
  let compare = false;
  const baseline = () => visuals[noneIdx]?.run.info.total_usd ?? Math.max(...visuals.map((v) => v.run.info.total_usd));

  // ---- stats + story per run ----
  setLoading('reading the storm');
  const stats = new Map<RunVisual, RunStats>(); const stories = new Map<RunVisual, StoryEvent[]>();
  const statsOf = (v: RunVisual) => { let s = stats.get(v); if (!s) { s = computeStats(v.run, city); stats.set(v, s); } return s; };
  const storyOf = (v: RunVisual) => {
    let e = stories.get(v); if (e) return e;
    const nv = visuals[noneIdx]; const isPlan = !isNoneV(v);
    e = buildStory(v.run, statsOf(v), tile, { isPlan, model: v.run.info.label + ' ' + v.run.info.model, baseRun: isPlan && nv !== v ? nv?.run : null, baseStats: isPlan && nv ? statsOf(nv) : null });
    stories.set(v, e); return e;
  };
  for (const v of visuals) { statsOf(v); storyOf(v); }
  const CAP_GAP = 0.034, CAP_LEN = 0.036;
  let slots: CaptionSlot[] = [];
  const phasesFor = (v: RunVisual | undefined): Phase[] => {
    const s = v ? statsOf(visuals[noneIdx] || v) : null; const tp = s ? s.t[s.peakF] : tEnd / 2;
    const p0 = Math.max(1800, tp - 3000), p1 = Math.min(tEnd - 1800, tp + 3000);
    const plan = v && !isNoneV(v);
    return [
      { u0: 0, u1: PREP_U, label: 'PREP  −72 h → −1 h', sub: plan ? 'Gemini’s plan deploys' : 'no plan · nothing deployed', cls: 'prep' },
      { u0: PREP_U, u1: uOf(p0), label: 'LANDFALL', cls: 'land' },
      { u0: uOf(p0), u1: uOf(p1), label: 'PEAK', cls: 'peak' },
      { u0: uOf(p1), u1: 1, label: 'AFTERMATH', cls: 'after' },
    ];
  };
  const phaseName = (tt: number) => { const ph = phasesFor(visuals[active]); const u = uOf(tt); return tt < 0 ? 'PREP' : (ph.find((p) => u >= p.u0 && u <= p.u1)?.label || 'AFTERMATH'); };
  const refreshStory = () => {
    const v = compare ? visuals[planIdx] : visuals[active]; if (!v) return;
    slots = layoutCaptions(storyOf(v), uOf, CAP_GAP, 0.07);
    hud.setTimelineStatic(phasesFor(v), storyOf(v).filter((e) => e.marker).map((e) => ({ u: uOf(e.t), label: e.marker!, color: e.color })));
  };

  // ---- plan inspector ----
  const insp = new Inspector(hud.root);
  let inspFor: RunVisual | null = null;
  const itemsFor = (v: RunVisual): InspItem[] => {
    const byId = new Map<string, THREE.Vector3>(); for (const l of v.plan.labels) if (l.obj) byId.set(l.obj.id, l.pos);
    return v.run.plan.filter((o) => isFinite(o.t_s) && o.coords?.length).map((o) => {
      let pos = byId.get(o.id);
      if (!pos) { let x = 0, y = 0; for (const p of o.coords) { x += p[0]; y += p[1]; } x /= o.coords.length; y /= o.coords.length; pos = new THREE.Vector3(x, y, frame.ground(x, y) + 12); }
      const cat = catOf(o.type);
      const major = (o.cost_usd || 0) >= 1e6 || /shelter|evacuation_zone|gate|wall|tunnel|pump|tank|storage|custom_mesh/.test(o.type);
      return { o, pos, cat, color: o.color || TYPE_COLORS[o.type] || '#5fe1ff', major, name: nameOf(o) };
    });
  };
  const syncInspector = () => {
    const v = compare ? visuals[planIdx] : visuals[active];
    if (!v || v === inspFor) return; inspFor = v;
    insp.setItems(isNoneV(v) ? [] : itemsFor(v), (v.run.info.label || 'Gemini').toUpperCase(), v.run.info.plan_cost_usd);
  };
  let focusId: string | null = null;
  const setFocus = (it: InspItem | null) => {
    focusId = it ? it.o.id : null; insp.pin(it);
    for (const v of visuals) { v.plan.setHighlight(!!it); (v.plan as any).focus?.(focusId); } // plan.ts may expose focus(id|null) for per-object highlight
  };
  const flyTo = (it: InspItem, ms = 1800) => {
    let ext = 0; for (const p of it.o.coords) ext = Math.max(ext, Math.hypot(p[0] - it.pos.x, p[1] - it.pos.y));
    const [lon, lat] = tile.localToLonLat(it.pos.x, it.pos.y);
    goCustom({ lon, lat, dist: Math.max(380, Math.min(2600, ext * 2.4 + 300)), heading: 330, tilt: 62 }, ms);
  };
  insp.onPick = (it) => { stopAuto(); if (walker.active) setWalk(false); setFocus(it); flyTo(it); };
  insp.onClose = () => setFocus(null);
  insp.onTour = () => runTour();

  // NWS alert (live from api.weather.gov, then server, then the advisory text fetched this morning)
  const alertText = async () => {
    try {
      const r = await fetch('https://api.weather.gov/alerts/active?point=37.7749,-122.4194', { headers: { Accept: 'application/geo+json' } });
      const j = await r.json(); const f = j.features?.[0]?.properties; if (f) return `<b>${f.event}</b> · ${f.areaDesc?.split(';')[0] || 'San Francisco'}<small>${f.headline} · El Niño atmospheric river inbound</small>`;
    } catch { /* offline */ }
    try { const j = await (await fetch('/api/alerts')).json(); const f = (j.features?.[0]?.properties) || j[0]; if (f) return `<b>${f.event}</b><small>${f.headline}</small>`; } catch { /* none */ }
    return `<b>Coastal Flood Advisory</b> · San Francisco Bay Shoreline<small>NWS San Francisco CA · until Oct 29 3:00 PM PDT · El Niño atmospheric river inbound</small>`;
  };
  let alertHtml = '';
  if (!shotMode) alertText().then((s) => { alertHtml = s; hud.showBanner(s); });

  // ---- agent drawer ----
  const panel = shotMode ? null : createAgentPanel({
    onApprove: (info) => hud.toast('approve', `Plan approved by planner${info.runId ? ' · ' + info.runId : ''} — generating cited report`, 'approved'),
    onRunResult: async (ev) => {
      const id = ev.run_id; if (!id || String(id).startsWith('mock') || (ev.model && !/argon/i.test(ev.model))) return;
      try {
        const r = await loadRun({ run_id: id, label: 'Gemini 4 Argon' + (ev.run_index != null ? ' · run ' + ev.run_index : ''), model: 'gemini-4-argon', total_usd: 0, plan_cost_usd: 0, base: '/runs/' + id + '/' });
        const v = new RunVisual(r, frame, city, waterGeo!, treeG);
        const ai = visuals.findIndex((x) => /argon/i.test(x.run.info.label + x.run.info.model));
        if (ai >= 0) { root.remove(visuals[ai].group); visuals[ai] = v; } else visuals.push(v);
        root.add(v.group); planIdx = findPlan(); inspFor = null; refreshStory(); hud.toast('run' + id, `Argon run ${id} loaded · damage ${usd(r.info.total_usd)}`, 'run');
      } catch (e) { console.warn(e); }
    },
  });
  if (panel && q.get('job')) { panel.connect(q.get('job')!); panel.toggle(true); }

  // ---- camera ----
  const shotCam = (s: Shot): ShotCam & { target: number[]; pos: number[] } => {
    const [tx, ty] = tile.lonLatToLocal(s.lon, s.lat); const tz = city.zAt(tx, ty);
    const h = s.heading * Math.PI / 180, ti = s.tilt * Math.PI / 180;
    const px = tx - Math.sin(h) * Math.sin(ti) * s.dist, py = ty - Math.cos(h) * Math.sin(ti) * s.dist, pz = tz + Math.cos(ti) * s.dist;
    const [lon, lat] = tile.localToLonLat(px, py);
    return { lon, lat, z: pz + tile.zOffset, heading: s.heading, tilt: s.tilt, target: [tx, ty, tz], pos: [px, py, pz] };
  };
  let orbit: { s: Shot; t0: number } | null = null; let camAnim: { from: THREE.Vector3; to: THREE.Vector3; ft: THREE.Vector3; tt: THREE.Vector3; t0: number; dur: number } | null = null;
  let boltNear: number[] | null = null;
  const P = (p: number[]) => new THREE.Vector3(p[0], p[1], frame.z(p[0], p[1], p[2])).applyMatrix4(frame.matrix);
  const goCustom = (s: Shot, ms = 3200, instant = false, thenOrbit = false) => {
    const c = shotCam(s); boltNear = c.target; orbit = null;
    if (arc) { if (instant) arc.setCamera(c); else arc.goTo(c, ms).then(() => { if (thenOrbit && !shotMode && !walker.active) orbit = { s: { ...s }, t0: performance.now() }; }); }
    else if (sty) {
      const to = P(c.pos), tt = P(c.target);
      if (instant) { sty.camera.position.copy(to); sty.controls.target.copy(tt); sty.controls.update(); }
      else camAnim = { from: sty.camera.position.clone(), to, ft: sty.controls.target.clone(), tt, t0: performance.now(), dur: ms };
    }
  };
  const goShot = (name: string, instant = false) => {
    const s = SHOTS[name]; if (!s) return; if (walker.active) setWalk(false);
    if (s.compare && visuals.length > 1) { compare = true; refreshStory(); }
    goCustom(s, 3200, instant, !s.compare);
  };
  container.addEventListener('pointerdown', () => { orbit = null; camAnim = null; });
  container.addEventListener('wheel', () => { orbit = null; camAnim = null; }, { passive: true });

  // ---- walk / fly mode ----
  const walker = new Walker(container);
  let walkReadyAt = 0;
  const groundR = (x: number, y: number) => {
    if (arc) { const [lon, lat] = tile.localToLonLat(x, y); const g = arc.groundAt(lon, lat); return g ?? city.zAt(x, y) + tile.zOffset; }
    return frame.ground(x, y);
  };
  const invFrame = new THREE.Matrix4().copy(frame.matrix).invert();
  const setWalk = (on: boolean, at?: { x: number; y: number; yaw: number }) => {
    if (on === walker.active && !at) return;
    orbit = null; camAnim = null;
    if (on) {
      let x = 0, y = 0, yaw = 0;
      if (at) ({ x, y, yaw } = at);
      else if (arc) { const c = arc.view.center; [x, y] = tile.lonLatToLocal(c.longitude, c.latitude); yaw = arc.view.camera.heading; }
      else if (sty) { const tg = sty.controls.target.clone().applyMatrix4(invFrame); x = tg.x; y = tg.y; const d = sty.controls.target.clone().sub(sty.camera.position).applyMatrix4(new THREE.Matrix4().extractRotation(invFrame)); yaw = (Math.atan2(d.x, d.y) * 180 / Math.PI + 360) % 360; }
      walker.start(x, y, yaw);
      if (arc) {
        arc.setNavBlocked(true);
        const [lon, lat] = tile.localToLonLat(x, y);
        arc.goTo({ lon, lat, z: groundR(x, y) + 2, heading: yaw, tilt: 86 }, 1500); walkReadyAt = performance.now() + 1550;
      } else if (sty) {
        sty.controls.enabled = false; sty.camera.near = 0.5; sty.camera.updateProjectionMatrix(); walkReadyAt = performance.now();
      }
    } else {
      walker.stop();
      if (arc) arc.setNavBlocked(false);
      if (sty) {
        sty.controls.enabled = true; sty.camera.near = 25; sty.camera.updateProjectionMatrix();
        const dir = new THREE.Vector3(); sty.camera.getWorldDirection(dir); sty.controls.target.copy(sty.camera.position).addScaledVector(dir, 300);
        sty.camera.position.addScaledVector(dir, -200).add(new THREE.Vector3(0, 0, 150)); sty.controls.update();
      }
    }
    hud.setCtl(walker.active, !!dirToken, !!tourToken);
  };
  const applyWalk = (dt: number) => {
    if (!walker.active || performance.now() < walkReadyAt) return;
    const z = walker.update(dt, groundR);
    if (arc) { const [lon, lat] = tile.localToLonLat(walker.x, walker.y); arc.setCamera({ lon, lat, z, heading: walker.yaw, tilt: 90 + walker.pitch }); }
    else if (sty) {
      const h = walker.yaw * Math.PI / 180, p = walker.pitch * Math.PI / 180;
      const pos = new THREE.Vector3(walker.x, walker.y, z).applyMatrix4(frame.matrix);
      const look = new THREE.Vector3(walker.x + Math.sin(h) * Math.cos(p) * 50, walker.y + Math.cos(h) * Math.cos(p) * 50, z + Math.sin(p) * 50).applyMatrix4(frame.matrix);
      sty.camera.position.copy(pos); sty.camera.lookAt(look);
    }
  };

  // ---- view switching ----
  const setView = (r: 'none' | 'plan' | 'cmp') => {
    if (r === 'cmp') compare = visuals.length > 1;
    else { compare = false; active = r === 'none' ? noneIdx : planIdx; }
    refreshStory(); syncInspector();
  };
  hud.onRun = (r) => { stopAuto(); setView(r); };
  hud.onSeek = (u) => { t = tOf(u); hud.resetToasts(); };
  hud.onPlay = () => { playing = !playing; if (playing && t >= tEnd) t = tStart; };
  hud.onSpeed = (s) => { speed = s; };
  hud.onCtl = (c) => { if (c === 'walk') setWalk(true); else if (c === 'orbit') setWalk(false); else if (c === 'dir') toggleDirector(); };

  // ---- build tour (B) + demo director (P) ----
  let tourToken = 0, dirToken = 0; let capOverride: { key: string; time: string; text: string; color: string } | null = null;
  let tourCost = -1;
  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
  const stopAuto = () => { if (tourToken || dirToken) { tourToken = 0; dirToken = 0; capOverride = null; tourCost = -1; hud.showFinal(null); hud.setCtl(walker.active, false, false); } };
  const fmtUsd = (v: number) => usd(v);
  const runTour = async (fromDirector = false) => {
    if (tourToken && !fromDirector) { stopAuto(); return; }
    const my = ++tourToken; if (!fromDirector) dirToken = 0; hud.setCtl(walker.active, !!dirToken, true);
    if (walker.active) setWalk(false);
    if (isNoneV(visuals[active]) || compare) setView('plan');
    syncInspector(); const items = [...insp.items].sort((a, b) => a.o.t_s - b.o.t_s);
    if (!items.length) { tourToken = 0; return; }
    // steps: majors one by one, minor items batched per category
    type Step = { its: InspItem[] };
    const steps: Step[] = []; const minor = new Map<string, InspItem[]>();
    for (const it of items) { if (it.major) steps.push({ its: [it] }); else { if (!minor.has(it.cat)) minor.set(it.cat, []); minor.get(it.cat)!.push(it); } }
    for (const l of minor.values()) steps.push({ its: l });
    steps.sort((a, b) => a.its[0].o.t_s - b.its[0].o.t_s);
    const cap = fromDirector ? 14 : 40; const use = steps.length > cap ? [...steps].sort((a, b) => b.its.reduce((s, i) => s + (i.o.cost_usd || 0), 0) - a.its.reduce((s, i) => s + (i.o.cost_usd || 0), 0)).slice(0, cap).sort((a, b) => a.its[0].o.t_s - b.its[0].o.t_s) : steps;
    playing = false; t = tStart; hud.resetToasts(); tourCost = 0;
    goShot('plan'); capOverride = { key: 'tour0', time: 'T−72 h', text: `Watch Gemini build the plan · ${items.length} actions before landfall`, color: '#5fe1ff' };
    await sleep(2600); if (my !== tourToken) return;
    for (let i = 0; i < use.length; i++) {
      const st = use[i]; const it = st.its[0]; const nextT = use[i + 1]?.its[0].o.t_s ?? -3600;
      const t0 = it.o.t_s - 1; const t1 = Math.max(t0 + 1, Math.min(nextT - 1, it.o.t_s + 7200));
      t = Math.max(t, t0);
      if (st.its.length === 1) { setFocus(it); flyTo(it, 1300); } else {
        setFocus(null); let x = 0, y = 0; for (const s of st.its) { x += s.pos.x; y += s.pos.y; } x /= st.its.length; y /= st.its.length;
        let ext = 0; for (const s of st.its) ext = Math.max(ext, Math.hypot(s.pos.x - x, s.pos.y - y));
        const [lon, lat] = tile.localToLonLat(x, y); goCustom({ lon, lat, dist: Math.max(900, Math.min(4200, ext * 2.2 + 600)), heading: 330, tilt: 58 }, 1300);
        for (const v of visuals) v.plan.setHighlight(true);
      }
      const cost = st.its.reduce((s, x) => s + (x.o.cost_usd || 0), 0);
      const name = st.its.length === 1 ? it.name : `${CATS.find((c) => c[0] === it.cat)?.[1]}: ${st.its.slice(0, 3).map((s) => s.name).join(', ')}${st.its.length > 3 ? ` +${st.its.length - 3}` : ''}`;
      capOverride = { key: 'tour' + (i + 1), time: fmtT(it.o.t_s), text: `${name}${cost ? ' · ' + fmtUsd(cost) : ''} — ${reasonOf(it.o)}`, color: it.color };
      const dur = st.its.length === 1 ? 2900 : 2500; const s0 = performance.now();
      await sleep(700); if (my !== tourToken) return;
      while (performance.now() - s0 < dur) { const k = Math.min(1, (performance.now() - s0 - 700) / (dur - 900)); t = t0 + (t1 - t0) * k; await sleep(30); if (my !== tourToken) return; }
      t = t1; tourCost += cost;
    }
    setFocus(null); t = -3600; for (const v of visuals) v.plan.setHighlight(true);
    goShot('plan');
    const total = items.reduce((s, x) => s + (x.o.cost_usd || 0), 0);
    capOverride = { key: 'tourEnd', time: 'T−1 h', text: `${visuals[active].run.info.label} plan in place before landfall · ${items.length} actions · ${fmtUsd(total)}`, color: '#5fe1ff' };
    await sleep(4500); if (my !== tourToken) return;
    for (const v of visuals) v.plan.setHighlight(false);
    capOverride = null; tourCost = -1; tourToken = 0; hud.setCtl(walker.active, !!dirToken, false);
  };
  const finalHtml = () => {
    const nv = visuals[noneIdx], pv = visuals[planIdx]; if (!nv || !pv || nv === pv) return null;
    const ns = statsOf(nv), ps = statsOf(pv); const mx = (a: ArrayLike<number>) => Math.max(...Array.from(a));
    const avoided = nv.run.info.total_usd - pv.run.info.total_usd; const bcr = pv.run.info.plan_cost_usd > 0 ? avoided / pv.run.info.plan_cost_usd : NaN;
    const row = (l: string, a: string, b: string) => `<tr><td>${l}</td><td class="n">${a}</td><td class="a">${b}</td></tr>`;
    return `<div class="f-h">SAME STORM · TWO OUTCOMES</div><table><tr><th></th><th class="n">NO PLAN</th><th class="a">${pv.run.info.label.toUpperCase()}</th></tr>
      ${row('Damage', usd(nv.run.info.total_usd, 2), usd(pv.run.info.total_usd, 2))}
      ${row('Peak people at risk', mx(ns.atRisk).toLocaleString('en-US'), mx(ps.atRisk).toLocaleString('en-US'))}
      ${row('Buildings flooded (peak)', mx(ns.flooded).toLocaleString('en-US'), mx(ps.flooded).toLocaleString('en-US'))}
      ${row('Trees down', ns.treeT.length.toLocaleString('en-US'), ps.treeT.length.toLocaleString('en-US'))}
      ${row('Plan cost', '—', usd(pv.run.info.plan_cost_usd))}</table>
      <div class="f-b">Avoided <b>${usd(Math.max(0, avoided))}</b> · benefit-cost ratio <b>${isFinite(bcr) ? bcr.toFixed(1) : '–'}</b></div>`;
  };
  const toggleDirector = () => { if (dirToken) { stopAuto(); return; } runDirector(); };
  const runDirector = async () => {
    stopAuto(); const my = ++dirToken; hud.setCtl(walker.active, true, false); hud.showFinal(null);
    const ok = () => my === dirToken;
    const cap = (time: string, text: string, color = '#ffb54a') => { capOverride = { key: 'd' + Math.random(), time, text, color }; };
    const walkAt = (lon: number, lat: number, yaw: number, fwd: number) => { const [x, y] = tile.lonLatToLocal(lon, lat); setWalk(true, { x, y, yaw }); walker.auto = fwd; walker.autoYaw = 2; };
    // 1. incoming storm
    setView('none'); playing = false; t = -6 * 3600; speed = 1;
    if (alertHtml) hud.showBanner(alertHtml);
    goShot('overview'); cap('LIVE', 'Incoming: El Niño atmospheric river · NWS alert live for San Francisco', '#ff5a36');
    await sleep(6500); if (!ok()) return;
    // 2. no-plan storm at 2×
    cap('NO PLAN', 'First: the same storm hits a city with no plan', '#ff9d7a'); await sleep(3000); if (!ok()) return;
    capOverride = null; t = -1800; speed = 2; playing = true; goShot('basin');
    await sleep(12000); if (!ok()) return;
    walkAt(-122.4148, 37.7640, 300, 3); cap('STREET LEVEL', 'Ground level at 17th & Folsom — the basin fills first', '#4fa8ff');
    await sleep(4000); if (!ok()) return; capOverride = null;
    await sleep(6000); if (!ok()) return;
    setWalk(false); goShot('canyon'); await sleep(9000); if (!ok()) return;
    while (t < tEnd && ok()) await sleep(200); if (!ok()) return;
    await sleep(3500); if (!ok()) return;
    // 3. switch to Argon: build tour
    hud.hideBanner(); setView('plan'); playing = false; speed = 1;
    cap('GEMINI 4 ARGON', 'Now: Gemini 4 Argon plans for the storm 72 hours ahead', '#5fe1ff'); await sleep(3000); if (!ok()) return;
    await runTour(true); if (!ok()) return;
    // 4. same storm with the plan
    capOverride = null; t = -1800; speed = 2; playing = true; goShot('basin');
    await sleep(11000); if (!ok()) return;
    walkAt(-122.4148, 37.7640, 300, 3); cap('STREET LEVEL', 'Same street, same storm — with Gemini’s plan', '#5dff9d');
    await sleep(4000); if (!ok()) return; capOverride = null;
    await sleep(5000); if (!ok()) return;
    setWalk(false); goShot('embarcadero'); await sleep(8000); if (!ok()) return;
    goShot('compare');
    while (t < tEnd && ok()) await sleep(200); if (!ok()) return;
    // 5. final comparison
    const f = finalHtml(); if (f) hud.showFinal(f);
    await sleep(20000); if (!ok()) return;
    dirToken = 0; hud.setCtl(walker.active, false, false);
  };

  // ---- keys ----
  addEventListener('keydown', (e) => {
    if ((e.target as HTMLElement)?.closest?.('input,textarea')) return;
    const k = e.key;
    if (k === 'f' || k === 'F') { setWalk(!walker.active); return; }
    if (k === 'Escape') { if (walker.active) setWalk(false); stopAuto(); setFocus(null); return; }
    if (k === 'p' || k === 'P') { toggleDirector(); return; }
    if (k === 'b' || k === 'B') { runTour(); return; }
    if (k === ' ') { playing = !playing; if (t >= tEnd) { t = tStart; hud.resetToasts(); } e.preventDefault(); }
    else if (k >= '1' && k <= '5') { stopAuto(); goShot(SHOT_KEYS[+k - 1]); }
    else if (k === 'c' || k === 'C') setView(compare ? 'plan' : 'cmp');
    else if (k === 'v' || k === 'V') setView(compare ? 'none' : active === noneIdx ? 'plan' : 'cmp');
    else if (k === 'a' || k === 'A') setView('none');
    else if (k === 'd' || k === 'D') setView('plan');
    else if (k === 'Tab') { compare = false; active = (active + 1) % Math.max(1, visuals.length); refreshStory(); syncInspector(); e.preventDefault(); }
    else if (k === 'g' || k === 'G') { panel?.toggle(); }
    else if (k === 'ArrowRight') t = tOf(Math.min(1, uOf(t) + 0.03));
    else if (k === 'ArrowLeft') { t = tOf(Math.max(0, uOf(t) - 0.03)); hud.resetToasts(); }
    else if (k === '+' || k === '=') speed = Math.min(4, speed * 2);
    else if (k === '-') speed = Math.max(0.5, speed / 2);
    else if (k === 'r' || k === 'R') { t = tStart; hud.resetToasts(); playing = true; }
    else if (k === 's' || k === 'S') { t = 0; hud.resetToasts(); playing = true; }
    else if (k === 'n' || k === 'N') hud.banner.classList.toggle('hidden');
    else if (k === 'h' || k === 'H') document.getElementById('hud')!.classList.toggle('hidden');
    else if (k === 'o' || k === 'O') { const s = SHOTS.overview; orbit = { s: { ...s }, t0: performance.now() }; }
  });

  // ---- hover / click on plan objects in 3D ----
  let mouse: { x: number; y: number } | null = null; let downAt: { x: number; y: number } | null = null;
  container.addEventListener('pointermove', (e) => { mouse = { x: e.clientX, y: e.clientY }; });
  container.addEventListener('pointerleave', () => { mouse = null; });
  container.addEventListener('pointerdown', (e) => { downAt = { x: e.clientX, y: e.clientY }; });
  const projV = new THREE.Vector3();
  const screenOf = (pos: THREE.Vector3, cam: THREE.Camera, w: number, h: number) => { projV.copy(pos).applyMatrix4(frame.matrix).project(cam); if (projV.z > 1 || projV.z < -1) return null; return [(projV.x * 0.5 + 0.5) * w, (-projV.y * 0.5 + 0.5) * h]; };
  const pickAt = (x: number, y: number): InspItem | null => {
    const cam = arc ? arc.camera : sty!.camera; const w = container.clientWidth, h = container.clientHeight; let best: InspItem | null = null, bd = 34 * 34;
    for (const it of insp.items) { if (t < it.o.t_s) continue; const s = screenOf(it.pos, cam, w, h); if (!s) continue; const d = (s[0] - x) ** 2 + (s[1] - y) ** 2; if (d < bd) { bd = d; best = it; } }
    return best;
  };
  container.addEventListener('pointerup', (e) => {
    if (!downAt || walker.active || Math.hypot(e.clientX - downAt.x, e.clientY - downAt.y) > 5) return;
    const it = pickAt(e.clientX, e.clientY); if (it) { stopAuto(); setFocus(it); flyTo(it); }
  });

  // ---- render ----
  const renderStorm = (r: THREE.WebGLRenderer, cam: THREE.PerspectiveCamera, w: number, h: number) => {
    shared.camPos.value.copy(cam.position).applyMatrix4(invFrame);
    (rainFx.material as THREE.ShaderMaterial).uniforms.uCam.value.copy(cam.position);
    if (dome) { const pm = cam.projectionMatrix.elements; let far = pm[14] / (pm[10] + 1); if (!isFinite(far) || far <= 0) far = 60000; dome.position.copy(cam.position); dome.scale.setScalar(Math.min(60000, Math.abs(far) * 0.7)); dome.updateMatrixWorld(); }
    if (compare && visuals.length > 1 && noneIdx !== planIdx) {
      r.setScissorTest(true); const pair = [noneIdx, planIdx];
      for (let i = 0; i < 2; i++) {
        visuals.forEach((v, j) => (v.group.visible = j === pair[i]));
        const x0 = Math.round((i * w) / 2), x1 = Math.round(((i + 1) * w) / 2);
        r.setScissor(x0, 0, x1 - x0, h); r.render(scene, cam);
      }
      r.setScissorTest(false);
    } else {
      visuals.forEach((v, j) => (v.group.visible = j === active));
      r.render(scene, cam);
    }
  };
  if (arc) arc.setRender(renderStorm);

  // ---- lightning bolt geometry (rebuilt on each strike) ----
  const boltPos = new Float32Array(400 * 3); const boltGeo = new THREE.BufferGeometry(); boltGeo.setAttribute('position', new THREE.BufferAttribute(boltPos, 3));
  const boltMat = new THREE.LineBasicMaterial({ color: 0xe8f0ff, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false });
  const bolt = new THREE.LineSegments(boltGeo, boltMat); bolt.frustumCulled = false; bolt.renderOrder = 30; root.add(bolt);
  const makeBolt = () => {
    let k = 0; const W = city.meta.nx * city.meta.dx, H = city.meta.ny * city.meta.dx;
    const seg = (x: number, y: number, z: number, len: number, depth: number) => {
      let px = x, py = y, pz = z; const n = Math.floor(len / 40);
      for (let i = 0; i < n && k < 398; i++) {
        const nx = px + (Math.random() - 0.5) * 70, ny = py + (Math.random() - 0.5) * 70, nz = pz - 25 - Math.random() * 40;
        const g = frame.ground(nx, ny); const z2 = Math.max(g, nz);
        boltPos.set([px, py, pz, nx, ny, z2], k * 3); k += 2; px = nx; py = ny; pz = z2;
        if (depth < 2 && Math.random() < 0.08) seg(px, py, pz, len * 0.3, depth + 1);
        if (z2 <= g) break;
      }
    };
    let x = W * (0.15 + Math.random() * 0.7), y = H * (0.15 + Math.random() * 0.7);
    if (boltNear) { x = boltNear[0] + (Math.random() - 0.5) * 600; y = boltNear[1] + 500 + Math.random() * 900; }
    seg(x, y, frame.ground(x, y) + 1400, 2000, 0);
    for (let i = k; i < 400; i++) boltPos.set([0, 0, -1e4], i * 3);
    boltGeo.attributes.position.needsUpdate = true; boltGeo.setDrawRange(0, k);
  };
  let flashT = -1, flashSeq: number[] = []; let holdFlash = 0;
  const lightning = (now: number) => {
    if (holdFlash) return holdFlash; if (flashT < 0) return 0; const e = (now - flashT) / 1000; let a = 0;
    for (const s of flashSeq) { const d = e - s; if (d > 0) a = Math.max(a, Math.exp(-d * 11) * (d < 0.03 ? d / 0.03 : 1)); }
    if (e > 1.5) flashT = -1; return a;
  };

  // ---- main loop ----
  refreshStory(); syncInspector(); hud.setCtl(false, false, false);
  let last = performance.now(), weatherT = 0, hudT = 0;
  const nfmt = (v: number) => Math.round(v).toLocaleString('en-US');
  const loop = () => {
    requestAnimationFrame(loop);
    const now = performance.now(); const dt = Math.min(0.1, (now - last) / 1000); last = now;
    if (playing && !tourToken) { const u = Math.min(1, uOf(t) + dt * speed / TOTAL_S); t = tOf(u); if (u >= 1) { t = tEnd; playing = false; } }
    shared.time.value += dt;
    const rain = Math.max(0, interp(base?.rain, t)), gust = Math.max(0, interp(base?.gust, t)), tide = interp(base?.tide, t, 1);
    // weather builds over the last 12 h before landfall instead of switching on at T0
    const preK = t < 0 ? Math.max(0, Math.min(1, 1 + t / (12 * 3600))) : 1;
    const pre = preK < 0.5;
    const rn = 0.08 + (rain / maxRain - 0.08) * preK, gn = 0.15 + (gust / maxGust - 0.15) * preK;
    shared.rain.value = rn; shared.gust.value = gn;
    if (!shotMode && !pre && flashT < 0 && Math.random() < dt * (0.05 + 0.6 * rn * gn)) { flashT = now; if (Math.random() < 0.6) makeBolt(); else boltGeo.setDrawRange(0, 0); audio.thunder(0.2 + Math.random() * 1.6); (deckHi.material as THREE.ShaderMaterial).uniforms.uBolt.value.set(boltPos[0], boltPos[1]); flashSeq = [0, 0.09 + Math.random() * 0.1, 0.3 + Math.random() * 0.3].slice(0, 2 + (Math.random() < 0.5 ? 1 : 0)); }
    const L = lightning(now); shared.lightning.value = L; hud.setFlash(L * 0.8); boltMat.opacity = Math.min(1, L * 2.2);
    if (arc) document.body.style.setProperty('--bright', String((0.88 - 0.1 * rn * preK) + L * 1.0));
    for (const v of visuals) if (v === visuals[active] || (compare && (v === visuals[noneIdx] || v === visuals[planIdx]))) v.update(t, dt, shared.time.value);
    { const lv = visuals[active]; lights.buf.fill(0); if (lv && t > 0) lv.dmg.fillOutage(lights.buf, lv.frameAt(t)); lights.tex.needsUpdate = true;
      (lights.points.material as THREE.ShaderMaterial).uniforms.uGrid.value = t < 0 ? 0 : 0.25 * Math.min(1, t / tEnd) * (/argon/i.test(lv?.run.info.label || '') ? 0.3 : 1); }
    curtains.visible = sprayFx.visible = !pre; deckLo.visible = !pre || t > -18 * 3600;
    audio.set(rn, gn, shared.time.value);
    if (!shotMode && !pre && !walker.active) { const sh = Math.max(0, gn - 0.75) * 6 + L * 1.5; container.style.transform = sh > 0.05 ? `scale(1.008) translate(${(Math.sin(now * 0.061) + Math.sin(now * 0.137)) * sh}px, ${(Math.cos(now * 0.053) + Math.sin(now * 0.171)) * sh * 0.6}px)` : ''; } else container.style.transform = '';
    if (wind && !pre) { wind.lines.visible = true; wind.update(dt, gust, uref); } else if (wind) wind.lines.visible = false;
    if (arc && now - weatherT > 400) { weatherT = now; arc.setWeather(0.05 + (0.1 + 0.85 * rn) * preK, 0.55 + (0.3 + 0.15 * rn) * preK, 0); }
    // camera
    applyWalk(dt);
    if (orbit && arc && !walker.active) { const s = orbit.s; s.heading = (s.heading + dt * 1.6) % 360; arc.setCamera(shotCam(s)); }
    if (sty) {
      if (camAnim && !walker.active) { const k = Math.min(1, (now - camAnim.t0) / camAnim.dur); const e = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2; sty.camera.position.lerpVectors(camAnim.from, camAnim.to, e); sty.controls.target.lerpVectors(camAnim.ft, camAnim.tt, e); if (k >= 1) camAnim = null; }
      if (!walker.active) sty.controls.update();
      sty.renderer.setViewport(0, 0, container.clientWidth, container.clientHeight);
      renderStorm(sty.renderer, sty.camera, container.clientWidth, container.clientHeight);
    }
    // ---- HUD ----
    const v = visuals[active]; const cam = arc ? arc.camera : sty!.camera; const W = container.clientWidth, H = container.clientHeight;
    const u = uOf(t);
    hud.setTimeline(u, playing, speed);
    if (v && !compare) hud.setLabels(v.plan.labels.slice(0, 14), cam, frame.matrix, W, H, t); else hud.setLabels([], cam, frame.matrix, 1, 1, t);
    // focus ring follows the pinned / hovered object
    const fit = insp.pinned || insp.hovered; const fs = fit ? screenOf(fit.pos, cam, W, H) : null; insp.setRing(fs ? fs[0] : 0, fs ? fs[1] : 0, !!fs && t >= (fit?.o.t_s ?? 0), fit?.color);
    if (now - hudT > 120) {
      hudT = now;
      const ph = phaseName(t);
      hud.setClock(t, STORM_START, t < 0 ? (preK > 0 ? 'PREP · storm approaching' : 'PREP · plan deploys') : ph, t < 0 && preK <= 0 ? 'Forecast: atmospheric river landfall in ' + Math.round(-t / 3600) + ' h' : `Rain <b>${rain.toFixed(0)}</b> mm/h · Gusts <b>${gust.toFixed(0)}</b> m/s · Tide <b>${tide.toFixed(1)}</b> m`);
      const nv = visuals[noneIdx], pv = visuals[planIdx];
      const shown = compare ? pv : v;
      if (shown) {
        const isPlan = !isNoneV(shown); const s = statsOf(shown); const ns = nv ? statsOf(nv) : null;
        const dmg = t <= 0 ? 0 : shown.damageAt(t), ndmg = nv && t > 0 ? nv.damageAt(t) : 0;
        const vs = (x: number, f = nfmt) => (isPlan && ns ? `without plan: <b>${f(x)}</b>` : '');
        const risk = statAt(s, s.atRisk, t), fl = statAt(s, s.flooded, t), tr = treesDownAt(s, t);
        hud.setKpis([
          { label: 'STORM DAMAGE', value: usd(dmg, 1), cls: isPlan ? 'good' : 'bad', sub: isPlan ? `without plan: <b>${usd(ndmg)}</b>` : `final: <b>${usd(shown.run.info.total_usd)}</b>` },
          { label: 'PEOPLE AT RISK', value: nfmt(risk), cls: risk > 0 ? 'bad' : '', sub: ns ? vs(statAt(ns, ns.atRisk, t)) : '' },
          { label: 'BUILDINGS FLOODED', value: nfmt(fl), sub: ns ? vs(statAt(ns, ns.flooded, t)) : '' },
          { label: 'TREES DOWN', value: nfmt(tr), sub: ns ? vs(treesDownAt(ns, t)) : '' },
        ]);
        const avoided = baseline() - (pv?.run.info.total_usd ?? 0); const spend = pv?.run.info.plan_cost_usd ?? 0; const bcr = spend > 0 ? avoided / spend : NaN;
        const pname = pv ? pv.run.info.label.toUpperCase() : 'GEMINI';
        const planSub = `plan ${usd(spend)} · ${pv?.run.plan.length ?? 0} actions · avoids <b>${usd(Math.max(0, avoided))}</b> · BCR <b>${isFinite(bcr) ? bcr.toFixed(1) : '–'}</b>`;
        const tourSub = tourCost >= 0 ? `building the plan · <b>${usd(tourCost)}</b> deployed` : '';
        if (compare) hud.setMode(`NO PLAN  vs  ${pname} PLAN`, tourSub || planSub, 'cmp', 'cmp', pv?.run.info.label || 'Gemini');
        else if (isPlan) hud.setMode(`${shown.run.info.label.toUpperCase()} PLAN`, tourSub || planSub, /argon/i.test(shown.run.info.label) ? 'argon' : 'gflash', 'plan', pv?.run.info.label || 'Gemini');
        else hud.setMode('NO PLAN', 'the city faces the storm as-is', 'none', 'none', pv?.run.info.label || 'Gemini');
      }
      // caption
      if (capOverride) hud.setCaption(capOverride.key, capOverride.time, capOverride.text, capOverride.color);
      else {
        let cur: CaptionSlot | null = null; for (const s of slots) { if (s.u <= u) cur = s; else break; }
        if (cur && u - cur.u < CAP_LEN && !shotMode) hud.setCaption('c' + cur.u + cur.ev.text, fmtT(cur.ev.t), cur.ev.text, cur.ev.color); else hud.setCaption('', '', '', '');
      }
      // inspector rows light up as objects deploy; hover card
      syncInspector(); insp.setTime(t);
      if (mouse && !walker.active && !insp.pinned) { const it = pickAt(mouse.x, mouse.y); if (it !== insp.hovered) { insp.hovered = it; insp.show(it); } }
      else if (!mouse && insp.hovered && !insp.pinned) { insp.hovered = null; insp.show(null); }
    }
    if (t > 600 && !shotMode && !dirToken) hud.hideBanner();
    document.body.classList.toggle('agent-open', !!panel?.isOpen());
  };

  // ---- start ----
  const sq = q.get('shot') || 'overview';
  goShot(/^\d$/.test(sq) ? URL_SHOTS[+sq] || 'overview' : sq, true);
  if (arc) { setLoading('streaming textured buildings'); await Promise.race([arc.ready, new Promise((r) => setTimeout(r, shotMode ? 30000 : 9000))]); }
  loading.classList.add('done'); setTimeout(() => loading.remove(), 1500);
  requestAnimationFrame(loop);
  (window as any).storm = { setT: (x: number) => (t = x), play: () => (playing = true), shot: goShot, visuals, frame, tile, city, director: runDirector, tour: runTour, walk: setWalk, get t() { return t; } };
  if (q.get('demo') === '1') setTimeout(() => runDirector(), 1500);
  if (shotMode) {
    // render-watch-critique: fixed camera, fixed time, signal readiness for the server's headless screenshot
    if (q.get('compare') === '1') compare = visuals.length > 1;
    const fl = q.get('flash') === '1';
    setTimeout(() => { if (fl) { makeBolt(); holdFlash = 0.32; } }, arc ? 2300 : 600);
    setTimeout(() => { document.documentElement.dataset.ready = 'true'; }, arc ? 2500 : 800);
  }
}

main().catch((e) => { console.error(e); setLoading('error: ' + (e?.message || e)); });
export type { City };
