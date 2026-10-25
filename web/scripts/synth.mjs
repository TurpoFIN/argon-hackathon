// Placeholder storm runs on the REAL prep data (web/public/data), in the exact runs/<id>/frames format
// from CONTRACT.md, so the frontend can be built before the physics runs exist.
// A cheap cellular water model (not the real solver): rain - inlet capacity, flow to lower neighbours, bay at tide.
// Run: node --experimental-strip-types scripts/synth.mjs
import fs from 'node:fs';
import path from 'node:path';
import { lonLatToUtm } from '../src/geo.ts';

const WEB = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const DATA = path.join(WEB, 'public/data');
const OUT = path.join(WEB, 'public/synth');
const ROOT = path.resolve(WEB, '..');
fs.mkdirSync(OUT, { recursive: true });

let seed = 1234567;
const rnd = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };
function hash2(ix, iy) { let h = ix * 374761393 + iy * 668265263; h = (h ^ (h >> 13)) * 1274126177; return ((h ^ (h >> 16)) >>> 0) / 4294967296; }
function vnoise(x, y) { const ix = Math.floor(x), iy = Math.floor(y), fx = x - ix, fy = y - iy; const s = (t) => t * t * (3 - 2 * t); const a = hash2(ix, iy), b = hash2(ix + 1, iy), c = hash2(ix, iy + 1), d = hash2(ix + 1, iy + 1); const ab = a + (b - a) * s(fx), cd = c + (d - c) * s(fx); return ab + (cd - ab) * s(fy); }
const fbm = (x, y) => { let v = 0, a = 0.5, f = 1; for (let i = 0; i < 4; i++) { v += a * vnoise(x * f, y * f); f *= 2; a *= 0.5; } return v; };

const meta = JSON.parse(fs.readFileSync(path.join(DATA, 'terrain_meta.json')));
const NX = meta.nx, NY = meta.ny, DX = meta.dx, N = NX * NY;
const terrain = new Float32Array(fs.readFileSync(path.join(DATA, 'terrain_8m.f32')).buffer.slice(0));
const bayMask = fs.existsSync(path.join(DATA, 'bay_8m.u8')) ? new Uint8Array(fs.readFileSync(path.join(DATA, 'bay_8m.u8'))) : terrain.map(z => z <= -1.9 ? 1 : 0);
const buildings = JSON.parse(fs.readFileSync(path.join(DATA, 'buildings_web.json'))).map(b => ({ id: b.id, fp: b.footprint || b.fp, h: b.height ?? b.h, z: b.ground_z ?? b.z, value: b.value || 1e6 }));
const cars = JSON.parse(fs.readFileSync(path.join(DATA, 'cars_web.json'))).map(c => ({ id: c.id, x: c.p ? c.p[0] : c.x, y: c.p ? c.p[1] : c.y, heading_deg: c.heading ?? c.heading_deg ?? 0 }));
let trees;
if (fs.existsSync(path.join(DATA, 'trees_web.json'))) trees = JSON.parse(fs.readFileSync(path.join(DATA, 'trees_web.json'))).map(t => ({ id: t.id, x: t.p ? t.p[0] : t.x, y: t.p ? t.p[1] : t.y, h: t.h ?? t.height_m ?? 8 }));
else trees = JSON.parse(fs.readFileSync(path.join(ROOT, 'data/grid/trees.json'))).map(t => ({ id: t.id, x: t.x - meta.x0, y: t.y - meta.y0, h: t.height_m ?? 8 }));
const L = (lon, lat) => { const [x, y] = lonLatToUtm(lon, lat); return [x - meta.x0, y - meta.y0]; };
const tz = (x, y) => { const ix = Math.max(0, Math.min(NX - 1, Math.floor(x / DX))), iy = Math.max(0, Math.min(NY - 1, Math.floor(y / DX))); return terrain[iy * NX + ix]; };
console.log('grid', NX, NY, 'buildings', buildings.length, 'trees', trees.length, 'cars', cars.length);

const bmask = new Int32Array(N).fill(-1), bh = new Float32Array(N);
for (const b of buildings) {
  const xs = b.fp.map(p => p[0]), ys = b.fp.map(p => p[1]);
  let hit = false;
  const fill = (ix, iy) => { if (ix < 0 || iy < 0 || ix >= NX || iy >= NY) return; bmask[iy * NX + ix] = b.id; bh[iy * NX + ix] = b.h; hit = true; };
  for (let iy = Math.floor(Math.min(...ys) / DX); iy <= Math.floor(Math.max(...ys) / DX); iy++)
    for (let ix = Math.floor(Math.min(...xs) / DX); ix <= Math.floor(Math.max(...xs) / DX); ix++) {
      const px = (ix + 0.5) * DX, py = (iy + 0.5) * DX; let inside = false;
      for (let i = 0, j = b.fp.length - 1; i < b.fp.length; j = i++) { const [xi, yi] = b.fp[i], [xj, yj] = b.fp[j]; if ((yi > py) !== (yj > py) && px < (xj - xi) * (py - yi) / (yj - yi) + xi) inside = !inside; }
      if (inside) fill(ix, iy);
    }
  if (!hit) { b.cell = -1; }
}

// ---------- storm forcing ----------
const NF = 120, DT_FRAME = 180;
const tArr = [], rain = [], gust = [], tide = [];
for (let f = 0; f < NF; f++) {
  const t = f * DT_FRAME, th = t / 3600; tArr.push(t);
  rain.push(+(4 + 46 * Math.exp(-((th - 2.6) ** 2) / 0.9) + 22 * Math.exp(-((th - 4.2) ** 2) / 0.3) + 6 * Math.sin(f * 0.9) ** 2).toFixed(1));
  gust.push(+(12 + 22 * Math.exp(-((th - 3.0) ** 2) / 1.4) + 4 * Math.sin(f * 1.7) ** 2).toFixed(1));
  tide.push(+(1.0 + 1.25 * Math.exp(-((th - 3.4) ** 2) / 1.6)).toFixed(2));
}
// wind slice at reference speed, FROM 200 deg, canyon acceleration around tall buildings
let wind;
const realWind = path.join(ROOT, 'runs/dev_wind/wind_slice_syn_200_fast.f32');
const U = 10;
if (fs.existsSync(realWind) && fs.statSync(realWind).size === N * 8) { wind = new Float32Array(fs.readFileSync(realWind).buffer.slice(0)); console.log('using dev_wind slice'); }
else {
  wind = new Float32Array(N * 2); const dirTo = 20 * Math.PI / 180;
  for (let iy = 0; iy < NY; iy++) for (let ix = 0; ix < NX; ix++) {
    const i = iy * NX + ix; let dens = 0;
    for (let k = -4; k <= 4; k += 2) for (let l = -4; l <= 4; l += 2) { const jx = Math.min(NX - 1, Math.max(0, ix + k)), jy = Math.min(NY - 1, Math.max(0, iy + l)); dens += bh[jy * NX + jx]; }
    dens /= 25; const a2 = dirTo + (fbm(ix / 18, iy / 18) - 0.5) * 1.0;
    let sp = U * (1 + Math.min(1.6, dens / 30)) * (0.8 + 0.4 * fbm(ix / 9 + 7, iy / 9)); if (bmask[i] >= 0) sp *= 0.15;
    wind[i * 2] = Math.sin(a2) * sp; wind[i * 2 + 1] = Math.cos(a2) * sp;
  }
}

function rasterLine(coords, fn) { for (let k = 0; k < coords.length - 1; k++) { const [ax, ay] = coords[k], [bx, by] = coords[k + 1]; const n = Math.ceil(Math.hypot(bx - ax, by - ay) / (DX / 2)); for (let s = 0; s <= n; s++) { const x = ax + (bx - ax) * s / n, y = ay + (by - ay) * s / n; const ix = Math.floor(x / DX), iy = Math.floor(y / DX); if (ix >= 0 && iy >= 0 && ix < NX && iy < NY) fn(iy * NX + ix); } } }
function rasterPoly(poly, fn) {
  const xs = poly.map(p => p[0]), ys = poly.map(p => p[1]);
  for (let iy = Math.floor(Math.min(...ys) / DX); iy <= Math.floor(Math.max(...ys) / DX); iy++) for (let ix = Math.floor(Math.min(...xs) / DX); ix <= Math.floor(Math.max(...xs) / DX); ix++) {
    const px = (ix + 0.5) * DX, py = (iy + 0.5) * DX; let inside = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) { const [xi, yi] = poly[i], [xj, yj] = poly[j]; if ((yi > py) !== (yj > py) && px < (xj - xi) * (py - yi) / (yj - yi) + xi) inside = !inside; }
    if (inside && ix >= 0 && iy >= 0 && ix < NX && iy < NY) fn(iy * NX + ix);
  }
}

function runStorm(id, label, model, plan) {
  const dir = path.join(OUT, 'runs', id, 'frames'); fs.mkdirSync(dir, { recursive: true });
  const zz = Float32Array.from(terrain);
  for (let i = 0; i < N; i++) if (bmask[i] >= 0) zz[i] += Math.max(3, bh[i]);
  for (const w of plan.walls || []) rasterLine(w.coords, (i) => { zz[i] = Math.max(zz[i], terrain[i] + w.height); });
  const bay = bayMask;
  const rainK = new Float32Array(N).fill(1); for (const p of plan.permeable || []) rasterPoly(p, (i) => { rainK[i] = 0.3; });
  const sink = new Float32Array(N);
  for (const s of plan.sinks || []) { const r = s.r / DX; const cx = s.x / DX, cy = s.y / DX; for (let iy = Math.floor(cy - r); iy <= cy + r; iy++) for (let ix = Math.floor(cx - r); ix <= cx + r; ix++) if (ix >= 0 && iy >= 0 && ix < NX && iy < NY && (ix - cx) ** 2 + (iy - cy) ** 2 <= r * r) sink[iy * NX + ix] += s.mmh; }
  const h = new Float32Array(N), out = new Float32Array(N * 4), maxd = new Float32Array(N), depth = new Uint16Array(NF * N);
  const SUB = 30, dtS = DT_FRAME / SUB;
  for (let f = 0; f < NF; f++) {
    const rmmh = rain[f] * 2.6; // exaggerated for placeholder visibility
    const drain = f > 35 ? 14 : 40; // inlets choke as the sewer surcharges
    for (let s = 0; s < SUB; s++) {
      for (let i = 0; i < N; i++) {
        if (bay[i]) { h[i] = Math.max(0, tide[f] - zz[i]); continue; }
        const roof = bmask[i] >= 0;
        h[i] = Math.max(0, h[i] + (rmmh * rainK[i] - (roof ? 0 : drain) - sink[i]) / 3.6e6 * dtS);
      }
      for (let iy = 0; iy < NY; iy++) for (let ix = 0; ix < NX; ix++) {
        const i = iy * NX + ix, hi = h[i], o = i * 4; out[o] = out[o + 1] = out[o + 2] = out[o + 3] = 0; if (hi <= 1e-5 || bay[i]) continue;
        const Hs = zz[i] + hi; let tot = 0;
        const nb0 = ix > 0 ? i - 1 : -1, nb1 = ix < NX - 1 ? i + 1 : -1, nb2 = iy > 0 ? i - NX : -1, nb3 = iy < NY - 1 ? i + NX : -1;
        const nb = [nb0, nb1, nb2, nb3];
        for (let k = 0; k < 4; k++) { const j = nb[k]; if (j < 0) continue; const d = Hs - (zz[j] + h[j]); if (d > 0) { out[o + k] = d; tot += d; } }
        if (tot <= 0) continue; const mv = Math.min(hi, tot * 0.45) / tot;
        for (let k = 0; k < 4; k++) out[o + k] *= mv;
      }
      for (let iy = 0; iy < NY; iy++) for (let ix = 0; ix < NX; ix++) {
        const i = iy * NX + ix, o = i * 4;
        if (out[o] > 0) { h[i] -= out[o]; if (ix > 0) h[i - 1] += out[o]; }
        if (out[o + 1] > 0) { h[i] -= out[o + 1]; if (ix < NX - 1) h[i + 1] += out[o + 1]; }
        if (out[o + 2] > 0) { h[i] -= out[o + 2]; if (iy > 0) h[i - NX] += out[o + 2]; }
        if (out[o + 3] > 0) { h[i] -= out[o + 3]; if (iy < NY - 1) h[i + NX] += out[o + 3]; }
      }
    }
    for (let i = 0; i < N; i++) { const d = bay[i] ? 0 : (bmask[i] >= 0 ? 0 : h[i]); depth[f * N + i] = Math.min(65535, Math.round(d * 1000)); if (d > maxd[i]) maxd[i] = d; }
  }
  fs.writeFileSync(path.join(dir, 'depth.u16'), Buffer.from(depth.buffer));
  fs.writeFileSync(path.join(dir, 'wind_slice.f32'), Buffer.from(wind.buffer));
  const tr = trees.map((t) => {
    const ix = Math.min(NX - 1, Math.max(0, Math.floor(t.x / DX))), iy = Math.min(NY - 1, Math.max(0, Math.floor(t.y / DX))); const i = iy * NX + ix;
    const wet = Math.min(1, maxd[i] * 4 + 0.2); const wi = Math.hypot(wind[i * 2], wind[i * 2 + 1]) / U;
    const p = (0.02 + 0.07 * wet * wi) * (plan.treeFactor ?? 1);
    if (rnd() < p) {
      const ft = (2.2 + rnd() * 2.2 + (rnd() < 0.3 ? rnd() * 1.2 : 0)) * 3600;
      const a2 = Math.atan2(wind[i * 2], wind[i * 2 + 1]) * 180 / Math.PI + (rnd() - 0.5) * 50;
      return { id: t.id, fail_t: Math.round(ft), mode: wet > 0.5 ? 'uproot' : 'snap', fall_dir_deg: Math.round((a2 + 360) % 360) };
    }
    return { id: t.id, fail_t: null, mode: null, fall_dir_deg: null };
  });
  fs.writeFileSync(path.join(dir, 'trees.json'), JSON.stringify(tr));
  const NC = cars.length; const cf = new Float32Array(NF * NC * 4);
  const cx = cars.map(c => c.x), cy = cars.map(c => c.y), cyaw = cars.map(c => c.heading_deg * Math.PI / 180);
  for (let f = 0; f < NF; f++) for (let c = 0; c < NC; c++) {
    const ix = Math.min(NX - 2, Math.max(1, Math.floor(cx[c] / DX))), iy = Math.min(NY - 2, Math.max(1, Math.floor(cy[c] / DX))); const i = iy * NX + ix;
    const D = (j) => depth[f * N + j] / 1000; const d = D(i); let zc = terrain[i];
    if (d > 0.4) {
      const gx = (zz[i + 1] + D(i + 1)) - (zz[i - 1] + D(i - 1)), gy = (zz[i + NX] + D(i + NX)) - (zz[i - NX] + D(i - NX));
      const gl = Math.hypot(gx, gy) + 1e-6; const sp = Math.min(5, 2 + d * 3);
      const nx2 = cx[c] - gx / gl * sp, ny2 = cy[c] - gy / gl * sp; const j = Math.floor(ny2 / DX) * NX + Math.floor(nx2 / DX);
      if (j > 0 && j < N && bmask[j] < 0) { cx[c] = nx2; cy[c] = ny2; }
      cyaw[c] += (rnd() - 0.5) * 0.35; zc += d - 0.35;
    }
    const o = (f * NC + c) * 4; cf[o] = cx[c]; cf[o + 1] = cy[c]; cf[o + 2] = zc; cf[o + 3] = cyaw[c];
  }
  fs.writeFileSync(path.join(dir, 'cars.f32'), Buffer.from(cf.buffer));
  const ND = plan.debris ?? 420; const df = new Float32Array(NF * ND * 3); const dmeta = [];
  const tall = buildings.filter(b => b.h > 40);
  for (let k = 0; k < ND; k++) {
    const kind = k % 3 === 0 ? 'branch' : k % 3 === 1 ? 'glass' : 'sign';
    let x, y, z;
    if (kind === 'branch') { const t = trees[Math.floor(rnd() * trees.length)]; x = t.x; y = t.y; z = tz(x, y) + t.h * 0.7; }
    else { const b = tall[Math.floor(rnd() * tall.length)] || buildings[0]; const p = b.fp[Math.floor(rnd() * b.fp.length)]; x = p[0]; y = p[1]; z = b.z + b.h * (0.3 + 0.6 * rnd()); }
    const launch = Math.floor(45 + rnd() * 45); let vz = 2 + rnd() * 3; let landed = false;
    dmeta.push({ id: k, kind, size_m: kind === 'glass' ? 1.2 : kind === 'sign' ? 1.8 : 2.5 });
    for (let f = 0; f < NF; f++) {
      if (f > launch && !landed) {
        const ix = Math.min(NX - 1, Math.max(0, Math.floor(x / DX))), iy = Math.min(NY - 1, Math.max(0, Math.floor(y / DX))); const i = iy * NX + ix;
        const g = gust[f] / U * 3.5; x += wind[i * 2] * g * (0.7 + rnd() * 0.6); y += wind[i * 2 + 1] * g * (0.7 + rnd() * 0.6);
        z += vz * 3; vz -= 0.35; const gz = tz(x, y); if (z <= gz + 0.2) { z = gz + 0.2; landed = true; }
      }
      const o = (f * ND + k) * 3; df[o] = x; df[o + 1] = y; df[o + 2] = z;
    }
  }
  fs.writeFileSync(path.join(dir, 'debris.f32'), Buffer.from(df.buffer));
  fs.writeFileSync(path.join(dir, 'debris.json'), JSON.stringify({ n: ND, items: dmeta }));
  const byB = {}; let flood = 0;
  for (const b of buildings) {
    const xs = b.fp.map(p => p[0]), ys = b.fp.map(p => p[1]); let md = 0;
    for (let iy = Math.floor(Math.min(...ys) / DX) - 1; iy <= Math.floor(Math.max(...ys) / DX) + 1; iy++) for (let ix = Math.floor(Math.min(...xs) / DX) - 1; ix <= Math.floor(Math.max(...xs) / DX) + 1; ix++) { if (ix < 0 || iy < 0 || ix >= NX || iy >= NY) continue; const i = iy * NX + ix; if (bmask[i] < 0) md = Math.max(md, maxd[i]); }
    if (md > 0.08) { const frac = Math.min(0.45, 0.25 * Math.sqrt(md)); const usd = Math.round(Math.min(b.value, 6e6) * frac * 0.3); byB[b.id] = usd; flood += usd; }
  }
  const treeUsd = tr.filter(t => t.fail_t != null).length * 4200;
  let carsDam = 0; for (let c = 0; c < NC; c++) { const o = ((NF - 1) * NC + c) * 4; if (Math.hypot(cf[o] - cars[c].x, cf[o + 1] - cars[c].y) > 1) carsDam++; }
  const total = flood + treeUsd + carsDam * 14000;
  const damage = { total_usd: total, by_building: byB, by_cause: { flood_structure: Math.round(flood * 0.6), flood_contents: Math.round(flood * 0.4), trees: treeUsd, vehicles: carsDam * 14000 } };
  fs.writeFileSync(path.join(dir, 'damage.json'), JSON.stringify(damage));
  const manifest = { run_id: id, label, model, storm: 'elnino_ar_2026', nx: NX, ny: NY, dx_frame: DX, n_frames: NF, dt_frame_s: DT_FRAME, t: tArr, gust, rain, tide, n_cars: NC, n_debris: ND, wind_u_ref: U, files: { depth: 'depth.u16', trees: 'trees.json', debris: 'debris.f32', cars: 'cars.f32', wind: 'wind_slice.f32', damage: 'damage.json' }, synthetic: true };
  fs.writeFileSync(path.join(dir, 'manifest.json'), JSON.stringify(manifest));
  fs.writeFileSync(path.join(OUT, 'runs', id, 'plan_objects.json'), JSON.stringify(plan.objects || []));
  const cost = (plan.objects || []).reduce((s, o) => s + (o.cost_usd || 0), 0);
  fs.writeFileSync(path.join(OUT, 'runs', id, 'result.json'), JSON.stringify({ run_id: id, label, model, storm: 'elnino_ar_2026', ledger: damage, plan_cost: { total_usd: cost }, synthetic: true }));
  console.log(id, 'damage $', (total / 1e6).toFixed(1), 'M  cost $', (cost / 1e6).toFixed(1), 'M');
  return { run_id: id, label, model, total_usd: total, plan_cost_usd: cost };
}

// ---------- plans on real places ----------
const rect = ([x, y], w, h2) => [[x - w / 2, y - h2 / 2], [x + w / 2, y - h2 / 2], [x + w / 2, y + h2 / 2], [x - w / 2, y + h2 / 2]];
// seawall follows the real shoreline: first bay cell from the west on each row, north half
const seawall = [];
for (let iy = Math.floor(NY * 0.45); iy < NY - 2; iy += 6) { let ix = 0; while (ix < NX && !bayMask[iy * NX + ix]) ix++; if (ix < NX - 1) seawall.push([ix * DX - 10, iy * DX + 4]); }
const folsom17 = L(-122.4150, 37.7634), channel = L(-122.3905, 37.7765), market = L(-122.4020, 37.7890);
const mid1 = L(-122.4085, 37.7680), mid2 = L(-122.3995, 37.7725);
const flashPlan = {
  walls: [{ coords: seawall.slice(4, 14), height: 0.8 }],
  permeable: [rect(L(-122.4040, 37.7745), 220, 160)],
  sinks: [{ x: L(-122.4120, 37.7650)[0], y: L(-122.4120, 37.7650)[1], r: 50, mmh: 500 }],
  treeFactor: 0.9, debris: 400,
  objects: [
    { type: 'sandbags', geometry: { type: 'polyline', coords: seawall.slice(4, 14) }, height: 0.8, label: 'Embarcadero sandbags', cost_usd: 2.4e6 },
    { type: 'permeable', geometry: { type: 'polygon', coords: rect(L(-122.4040, 37.7745), 220, 160) }, height: 0, label: 'Permeable pavers, SoMa', cost_usd: 9.5e6 },
    { type: 'storage_tank', geometry: { type: 'point', coords: L(-122.4120, 37.7650) }, height: 8, radius_m: 22, label: 'Cistern 6,000 m³', cost_usd: 18e6 },
    { type: 'wall', geometry: { type: 'polyline', coords: [L(-122.4060, 37.7700), L(-122.4030, 37.7718)] }, height: 1.2, label: 'Flood wall', cost_usd: 6e6 },
    { type: 'pump', geometry: { type: 'point', coords: channel }, height: 6, label: 'Pump 2 m³/s', cost_usd: 11e6 },
  ],
};
const argonPlan = {
  walls: [{ coords: seawall, height: 1.6 }],
  permeable: [rect(folsom17, 700, 560), rect(mid1, 500, 420), rect(mid2, 480, 400)],
  sinks: [{ x: folsom17[0], y: folsom17[1], r: 260, mmh: 2600 }, { x: mid1[0], y: mid1[1], r: 200, mmh: 2000 }, { x: mid2[0], y: mid2[1], r: 180, mmh: 1600 }, { x: channel[0] - 300, y: channel[1], r: 200, mmh: 1500 }],
  treeFactor: 0.45, debris: 260,
  objects: [
    { type: 'tunnel', geometry: { type: 'polyline', coords: [folsom17, mid1, mid2, channel] }, height: 6, label: 'Mission Creek relief tunnel 25 m³/s', cost_usd: 21e6 },
    { type: 'storage_tank', geometry: { type: 'point', coords: folsom17 }, height: 10, radius_m: 34, label: '17th & Folsom cistern 20,000 m³', cost_usd: 9.8e6 },
    { type: 'seawall', geometry: { type: 'polyline', coords: seawall }, height: 1.6, label: 'Embarcadero seawall +1.6 m', cost_usd: 6.1e6 },
    { type: 'permeable', geometry: { type: 'polygon', coords: rect(folsom17, 360, 280) }, height: 0, label: 'Permeable streets, Mission', cost_usd: 2.9e6 },
    { type: 'bioswale', geometry: { type: 'polygon', coords: rect(mid1, 280, 220) }, height: 0, label: 'Bioswales, Folsom corridor', cost_usd: 1.6e6 },
    { type: 'bioswale', geometry: { type: 'polygon', coords: rect(mid2, 260, 200) }, height: 0, label: 'Bioswales, Harrison', cost_usd: 1.4e6 },
    { type: 'green_roof', geometry: { type: 'polygon', coords: rect(L(-122.3990, 37.7840), 450, 350) }, height: 0, label: 'Green roofs, SoMa', cost_usd: 2.2e6 },
    { type: 'wind_screen', geometry: { type: 'polyline', coords: [L(-122.4045, 37.7875), market, L(-122.3995, 37.7905)] }, height: 12, label: 'Wind baffles, Market canyon', cost_usd: 1.8e6 },
    { type: 'brace_trees', geometry: { type: 'polygon', coords: rect(L(-122.4160, 37.7620), 900, 700) }, height: 0, label: 'Brace 1,200 street trees', cost_usd: 1.2e6 },
    { type: 'pump', geometry: { type: 'point', coords: channel }, height: 8, label: 'Outfall pump 12 m³/s', cost_usd: 1.5e6 },
  ],
};
const runs = [
  runStorm('synth_none', 'No plan', 'none', { objects: [] }),
  runStorm('synth_flash', 'Gemini 3.8 Flash', 'gemini-3.8-flash', flashPlan),
  runStorm('synth_argon', 'Gemini 4 Argon', 'gemini-4-argon', argonPlan),
];
fs.writeFileSync(path.join(OUT, 'runs', 'index.json'), JSON.stringify(runs));
console.log('done ->', OUT);
