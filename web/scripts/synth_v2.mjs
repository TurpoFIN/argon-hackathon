// Adds CONTRACT v2 emergency-response plan objects + people.f32 to the synthetic runs (placeholder only).
// Run after synth.mjs: node --experimental-strip-types scripts/synth_v2.mjs
import fs from 'node:fs';
import path from 'node:path';
import { lonLatToUtm } from '../src/geo.ts';

const WEB = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const DATA = path.join(WEB, 'public/data'), OUT = path.join(WEB, 'public/synth/runs');
const meta = JSON.parse(fs.readFileSync(path.join(DATA, 'terrain_meta.json')));
const NX = meta.nx, NY = meta.ny, DX = meta.dx, N = NX * NY;
const terrain = new Float32Array(fs.readFileSync(path.join(DATA, 'terrain_8m.f32')).buffer.slice(0));
const B = JSON.parse(fs.readFileSync(path.join(DATA, 'buildings_web.json')));
const L = (lon, lat) => { const [x, y] = lonLatToUtm(lon, lat); return [x - meta.x0, y - meta.y0]; };
let seed = 42; const rnd = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };
const cen = (b) => { let x = 0, y = 0; for (const p of b.footprint) { x += p[0]; y += p[1]; } return [x / b.footprint.length, y / b.footprint.length]; };
const area = (b) => { let a = 0; const f = b.footprint; for (let i = 0, j = f.length - 1; i < f.length; j = i++) a += (f[j][0] + f[i][0]) * (f[j][1] - f[i][1]); return Math.abs(a / 2); };
const basin = L(-122.4150, 37.7634);
const zone = [[basin[0] - 500, basin[1] - 380], [basin[0] + 650, basin[1] - 380], [basin[0] + 650, basin[1] + 420], [basin[0] - 500, basin[1] + 420]];
// shelters: big buildings on higher ground 0.8-2 km from the basin
const cands = B.filter((b) => { const [x, y] = cen(b); const d = Math.hypot(x - basin[0], y - basin[1]); return d > 800 && d < 2200 && b.ground_z > 14 && area(b) > 1500; })
  .sort((a, b) => (/EDU|GOV/.test(b.occupancy) ? 1 : 0) - (/EDU|GOV/.test(a.occupancy) ? 1 : 0) || area(b) - area(a));
const shelters = cands.slice(0, 3);
console.log('shelters', shelters.map((b) => [b.id, b.occupancy, Math.round(area(b)), b.ground_z]));
const route = (a, b) => [a, [a[0], (a[1] + b[1]) / 2], [b[0], (a[1] + b[1]) / 2], b];
const zc = [basin[0] + 75, basin[1] + 20];
const H = 3600;
const v2 = [
  { id: 'alert_1', type: 'alert', t_s: -60 * H, label: 'NWS: Atmospheric river watch — Mission Creek basin, prepare to evacuate', cost_usd: 0, geometry: { kind: 'point', coords: [zc] } },
  { id: 'evac_zone', type: 'evacuation_zone', t_s: -48 * H, label: 'Evacuation order · Mission Creek basin', cost_usd: 350000, geometry: { kind: 'polygon', coords: zone } },
  { id: 'alert_2', type: 'alert', t_s: -48 * H, label: 'Evacuation order issued: 17th–Division, Folsom–Harrison (vulnerable residents first)', cost_usd: 0, geometry: { kind: 'point', coords: [zc] } },
  ...shelters.map((b, i) => ({ id: 'shelter_' + i, type: 'shelter', t_s: -44 * H + i * 1800, label: `Shelter ${['A', 'B', 'C'][i]} · ${b.occupancy}`, capacity: 800 + i * 300, cost_usd: 240000, building_id: b.id, geometry: { kind: 'point', coords: [cen(b)] } })),
  ...shelters.map((b, i) => ({ id: 'route_' + i, type: 'evacuation_route', t_s: -47 * H, label: i === 0 ? 'Evacuation routes' : undefined, cost_usd: 0, geometry: { kind: 'polyline', coords: route(zc, cen(b)), width_m: 7 } })),
  { id: 'depot_1', type: 'depot', t_s: -40 * H, label: 'Supply depot · water + meals', cost_usd: 420000, geometry: { kind: 'point', coords: [L(-122.4035, 37.7745)] } },
  ...shelters.map((b, i) => ({ id: 'supply_' + i, type: 'supply_run', t_s: -36 * H, cost_usd: 60000, geometry: { kind: 'polyline', coords: route(L(-122.4035, 37.7745), cen(b)), width_m: 4 } })),
  { id: 'crew_sand', type: 'crew', t_s: -30 * H, label: 'Sandbag crew ×6', cost_usd: 180000, geometry: { kind: 'point', coords: [L(-122.3895, 37.7880)] } },
  { id: 'crew_pump', type: 'crew', t_s: -20 * H, label: 'Pump crew ×4', cost_usd: 140000, geometry: { kind: 'point', coords: [L(-122.4120, 37.7660)] } },
  { id: 'crew_rescue', type: 'crew', t_s: -2 * H, label: 'Swift-water rescue boats ×3', cost_usd: 220000, geometry: { kind: 'point', coords: [L(-122.4100, 37.7650)] } },
  { id: 'medical', type: 'medical_post', t_s: -24 * H, label: 'Medical post', cost_usd: 90000, geometry: { kind: 'point', coords: [cen(shelters[0] || B[0])] } },
  { id: 'closure_1', type: 'road_closure', t_s: -3 * H, label: 'Road closed · 17th St underpass', cost_usd: 15000, geometry: { kind: 'polyline', coords: [[basin[0] - 60, basin[1] - 10], [basin[0] + 60, basin[1] - 10]] } },
  { id: 'alert_3', type: 'alert', t_s: 1.2 * H, label: 'Flash flood warning — stay off Folsom & Harrison', cost_usd: 0, geometry: { kind: 'point', coords: [zc] } },
];
// custom mesh: a deployable flood gate at the Mission Creek channel, authored as raw triangles
{
  const [gx, gy] = L(-122.3930, 37.7760); const P = [], I = [];
  const box = (x0, y0, z0, x1, y1, z1) => { const b = P.length / 3; for (const [x, y, z] of [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0], [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]]) P.push(gx + x, gy + y, z); for (const f of [[0, 1, 5, 0, 5, 4], [1, 2, 6, 1, 6, 5], [2, 3, 7, 2, 7, 6], [3, 0, 4, 3, 4, 7], [4, 5, 6, 4, 6, 7]]) I.push(...f.map((k) => b + k)); };
  box(-30, -2, 0, -26, 2, 9); box(26, -2, 0, 30, 2, 9); box(-26, -0.6, 0, 26, 0.6, 5.5); box(-30, -2, 8, 30, 2, 9.5);
  v2.push({ id: 'gate_mesh', type: 'custom_mesh', t_s: -26 * H, label: 'Mission Creek tide gate (Argon design)', cost_usd: 3.2e6, geometry: { kind: 'mesh', coords: [[gx, gy]], z_base: 'ground' }, mesh: { positions: P, indices: I } });
}
for (const id of ['synth_argon', 'synth_flash', 'synth_none']) {
  const dir = path.join(OUT, id); const man = JSON.parse(fs.readFileSync(path.join(dir, 'frames/manifest.json')));
  let objs = JSON.parse(fs.readFileSync(path.join(dir, 'plan_objects.json'))); if (!Array.isArray(objs)) objs = objs.objects;
  // stagger infrastructure build-in over the pre-landfall days
  objs = objs.filter((o) => !String(o.id || '').match(/^(alert|evac|shelter|route|depot|supply|crew|medical|closure|gate_mesh)/)).map((o, i) => ({ ...o, id: o.id || o.type + i, t_s: o.t_s ?? -(70 - i * 6) * H }));
  if (id === 'synth_argon') objs.push(...v2);
  if (id === 'synth_flash') objs.push(v2[0], { ...v2[1], t_s: -4 * H }, v2[3], v2[3 + shelters.length]);
  fs.writeFileSync(path.join(dir, 'plan_objects.json'), JSON.stringify({ objects: objs }));
  // people
  const depth = new Uint16Array(fs.readFileSync(path.join(dir, 'frames/depth.u16')).buffer.slice(0));
  const NF = man.n_frames, NP = 5000, pf = new Float32Array(NF * NP * 3);
  const homes = B.filter((b) => b.occupancy?.startsWith('RES'));
  const evac = id === 'synth_argon' ? 0.92 : id === 'synth_flash' ? 0.35 : 0;
  const tOrder = id === 'synth_argon' ? -48 * H : -4 * H;
  const counts = { sheltered: 0, at_risk: 0, stranded: 0, rescued: 0 };
  for (let k = 0; k < NP; k++) {
    const inZone = k < NP * 0.6; let hx, hy;
    if (inZone) { hx = zone[0][0] + rnd() * (zone[1][0] - zone[0][0]); hy = zone[0][1] + rnd() * (zone[2][1] - zone[0][1]); }
    else { const b = homes[Math.floor(rnd() * homes.length)]; [hx, hy] = cen(b); }
    const goes = inZone && rnd() < evac; const sh = shelters[k % Math.max(1, shelters.length)]; const target = sh ? cen(sh) : [hx, hy];
    const path2 = route([hx, hy], [target[0] + (rnd() - 0.5) * 40, target[1] + (rnd() - 0.5) * 40]);
    let plen = 0; for (let i = 0; i < 3; i++) plen += Math.hypot(path2[i + 1][0] - path2[i][0], path2[i + 1][1] - path2[i][1]);
    const speed = 1.1 * (0.6 + rnd() * 0.6); const depart = Math.max(man.t[0], tOrder + rnd() * 6 * H);
    let rescued = false;
    for (let f = 0; f < NF; f++) {
      const t = man.t[f]; let x = hx, y = hy, st = 0;
      if (goes) {
        const s = Math.max(0, (t - depart) * speed * 0.25); // walking with stops; storm frames start at t=0
        if (t >= depart || depart < 0) { let r = Math.min(plen, depart < 0 ? plen : s); st = r >= plen ? 2 : 1; for (let i = 0; i < 3; i++) { const l = Math.hypot(path2[i + 1][0] - path2[i][0], path2[i + 1][1] - path2[i][1]); if (r <= l) { x = path2[i][0] + (path2[i + 1][0] - path2[i][0]) * r / l; y = path2[i][1] + (path2[i + 1][1] - path2[i][1]) * r / l; break; } r -= l; x = path2[i + 1][0]; y = path2[i + 1][1]; } }
      }
      if (st !== 2) {
        const ix = Math.min(NX - 1, Math.max(0, Math.floor(x / DX))), iy = Math.min(NY - 1, Math.max(0, Math.floor(y / DX)));
        const d = depth[f * N + iy * NX + ix] / 1000;
        if (d > 0.25) st = st === 1 ? 5 : 3;
        if (st === 3 && id === 'synth_argon' && f > NF * 0.55 && rnd() < 0.08) rescued = true;
        if (rescued) st = 4;
        x += (rnd() - 0.5) * 3; y += (rnd() - 0.5) * 3;
      }
      const o = (f * NP + k) * 3; pf[o] = x; pf[o + 1] = y; pf[o + 2] = st;
    }
    const last = pf[((NF - 1) * NP + k) * 3 + 2]; if (last === 2) counts.sheltered++; if (last === 3) counts.at_risk++; if (last === 5) counts.stranded++; if (last === 4) counts.rescued++;
  }
  fs.writeFileSync(path.join(dir, 'frames/people.f32'), Buffer.from(pf.buffer));
  fs.writeFileSync(path.join(dir, 'frames/people.json'), JSON.stringify({ n_agents: NP, states: ['home', 'moving', 'sheltered', 'at_risk', 'rescued', 'stranded'], final: counts }));
  man.n_people = NP; man.files.people = 'people.f32'; fs.writeFileSync(path.join(dir, 'frames/manifest.json'), JSON.stringify(man));
  console.log(id, counts);
}
