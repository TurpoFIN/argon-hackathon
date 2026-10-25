// Loaders for the prep city data (web/public/data, local metres from grid SW corner) and storm runs.
// Field names are read tolerantly so prep/sim format drift does not break the demo.

export interface TerrainMeta { nx: number; ny: number; dx: number; x0: number; y0: number; z_min?: number; z_max?: number }
export interface Building { id: number; fp: [number, number][]; h: number; z: number; occ: string; value: number }
export interface Tree { id: number; x: number; y: number; h: number; crown: number }
export interface Car { id: number; x: number; y: number; heading: number }
export interface City {
  meta: TerrainMeta; terrain: Float32Array; bay: Uint8Array | null; flood100: Uint8Array | null;
  buildings: Building[]; trees: Tree[]; cars: Car[]; base: string;
  zAt(x: number, y: number): number;
}

async function tryFetch(urls: string[], kind: 'json' | 'buf'): Promise<any> {
  for (const u of urls) {
    try {
      const r = await fetch(u);
      if (!r.ok) continue;
      const ct = r.headers.get('content-type') || '';
      if (ct.includes('text/html')) continue; // SPA fallback page, not data
      return kind === 'json' ? await r.json() : await r.arrayBuffer();
    } catch { /* next */ }
  }
  return null;
}

export async function loadCity(): Promise<City> {
  const bases = ['data/', '/data/', 'synth/'];
  let base = '';
  let meta: any = null;
  for (const b of bases) { meta = await tryFetch([b + 'terrain_meta.json'], 'json'); if (meta) { base = b; break; } }
  if (!meta) throw new Error('no terrain_meta.json found');
  const [tb, bayb, fb, bj, tj, cj] = await Promise.all([
    tryFetch([base + 'terrain_8m.f32'], 'buf'),
    tryFetch([base + 'bay_8m.u8'], 'buf'),
    tryFetch([base + 'flood100_8m.u8'], 'buf'),
    tryFetch([base + 'buildings_web.json'], 'json'),
    tryFetch([base + 'trees_web.json', 'synth/trees_web.json'], 'json'),
    tryFetch([base + 'cars_web.json'], 'json'),
  ]);
  const terrain = new Float32Array(tb);
  const m: TerrainMeta = { nx: meta.nx, ny: meta.ny, dx: meta.dx || 8, x0: meta.x0 || 0, y0: meta.y0 || 0, z_min: meta.z_min, z_max: meta.z_max };
  const P = (o: any): [number, number] => o.p ? [o.p[0], o.p[1]] : [o.x, o.y];
  const fixXY = (x: number, y: number): [number, number] => (x > 1e5 ? [x - m.x0, y - m.y0] : [x, y]);
  const buildings: Building[] = (bj || []).map((b: any, i: number) => ({
    id: b.id ?? i, fp: (b.footprint || b.fp || []).map((p: number[]) => fixXY(p[0], p[1])),
    h: Math.max(3, +(b.height ?? b.height_m ?? b.h ?? 8)), z: +(b.ground_z ?? b.z ?? 0), occ: b.occupancy || b.occ || 'RES1', value: +(b.value ?? (b.value_structure || 0) + (b.value_contents || 0)),
  }));
  const trees: Tree[] = (tj || []).map((t: any, i: number) => { const [x, y] = fixXY(...P(t)); return { id: t.id ?? i, x, y, h: +(t.h ?? t.height_m ?? t.height ?? 8), crown: +(t.crown ?? t.crown_m ?? 4) }; });
  const cars: Car[] = (cj || []).map((c: any, i: number) => { const [x, y] = fixXY(...P(c)); return { id: c.id ?? i, x, y, heading: +(c.heading ?? c.heading_deg ?? 0) }; });
  const { nx, ny, dx } = m;
  const zAt = (x: number, y: number) => {
    const fx = x / dx - 0.5, fy = y / dx - 0.5;
    const ix = Math.max(0, Math.min(nx - 2, Math.floor(fx))), iy = Math.max(0, Math.min(ny - 2, Math.floor(fy)));
    const tx = Math.max(0, Math.min(1, fx - ix)), ty = Math.max(0, Math.min(1, fy - iy));
    const a = terrain[iy * nx + ix], b = terrain[iy * nx + ix + 1], c = terrain[(iy + 1) * nx + ix], d = terrain[(iy + 1) * nx + ix + 1];
    return (a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty;
  };
  return { meta: m, terrain, bay: bayb ? new Uint8Array(bayb) : null, flood100: fb ? new Uint8Array(fb) : null, buildings, trees, cars, base, zAt };
}

// ---------------- runs ----------------
export interface RunInfo { run_id: string; label: string; model: string; total_usd: number; plan_cost_usd: number; base: string; mock?: boolean; storm?: string }
export interface PlanObject {
  id: string; type: string; phase?: string; t_s: number; kind: string; coords: number[][]; height: number; width: number;
  profile?: number[][]; color?: string; material?: string; label?: string; cost_usd: number; capacity?: number; mesh?: { positions: number[]; indices: number[] };
  raw: any;
}
export interface Run {
  info: RunInfo; manifest: any; nx: number; ny: number; dx: number; nFrames: number; t: number[]; gust: number[]; rain: number[]; tide: number[] | null;
  depth: Uint16Array; trees: { id: number; fail_t: number | null; mode: string; fall_dir_deg: number }[];
  cars: Float32Array | null; nCars: number; debris: Float32Array | null; nDebris: number; debrisMeta: any;
  wind: Float32Array | null; damage: any; result: any; plan: PlanObject[];
  people: Float32Array | null; nPeople: number; peopleMeta: any;
}

export async function listRuns(): Promise<RunInfo[]> {
  const out: RunInfo[] = [];
  const api = await tryFetch(['/api/runs'], 'json');
  const arr = Array.isArray(api) ? api : api?.runs;
  if (Array.isArray(arr)) for (const r of arr) {
    const id = r.run_id || r.id; if (!id) continue;
    out.push({ run_id: id, label: r.label || id, model: r.model || '', total_usd: +(r.total_usd ?? r.damage_usd ?? r.ledger?.total_usd ?? 0), plan_cost_usd: +(r.plan_cost_usd ?? r.plan_cost?.total_usd ?? 0), base: '/runs/' + id + '/', mock: !!r.mock, storm: r.storm });
  }
  const synth = await tryFetch(['synth/runs/index.json'], 'json');
  if (Array.isArray(synth)) for (const r of synth) out.push({ ...r, base: 'synth/runs/' + r.run_id + '/' });
  return out;
}

function normPlan(raw: any): PlanObject[] {
  const list = Array.isArray(raw) ? raw : (raw?.objects || []);
  return list.map((o: any, i: number) => {
    const g = o.geometry || {};
    let coords = g.coords ?? g.coordinates ?? [];
    const kind = g.kind || g.type || (Array.isArray(coords) && typeof coords[0] === 'number' ? 'point' : 'polyline');
    if (Array.isArray(coords) && typeof coords[0] === 'number') coords = [coords];
    coords = coords.map((p: number[]) => (p[0] > 1e5 ? [p[0] - (window as any).__x0, p[1] - (window as any).__y0] : p));
    return {
      id: String(o.id ?? i), type: String(o.type || 'object').toLowerCase(), phase: o.phase, t_s: +(o.t_s ?? o.t ?? -3600 * 24),
      kind: String(kind).toLowerCase(), coords, height: +(g.height_m ?? o.height_m ?? o.height ?? 0), width: +(g.width_m ?? o.width_m ?? 0),
      profile: g.profile || o.profile, color: o.style?.color || o.color, material: o.style?.material || o.material, label: o.label || o.name,
      cost_usd: +(o.cost_usd || 0), capacity: o.capacity, mesh: o.mesh || g.mesh, raw: o,
    };
  });
}

export async function loadRun(info: RunInfo): Promise<Run> {
  const fb = info.base + 'frames/';
  const manifest = await tryFetch([fb + 'manifest.json'], 'json');
  if (!manifest) throw new Error('no manifest for ' + info.run_id);
  const files = manifest.files || {};
  const name = (k: string, d: string) => (typeof files[k] === 'string' ? files[k] : files[k]?.file || d);
  const [depthB, treesJ, carsB, debB, debJ, windB, damJ, resJ, planJ, peopB, peopJ] = await Promise.all([
    tryFetch([fb + name('depth', 'depth.u16')], 'buf'),
    tryFetch([fb + name('trees', 'trees.json')], 'json'),
    tryFetch([fb + name('cars', 'cars.f32')], 'buf'),
    tryFetch([fb + name('debris', 'debris.f32')], 'buf'),
    tryFetch([fb + 'debris.json'], 'json'),
    tryFetch([fb + name('wind', 'wind_slice.f32')], 'buf'),
    tryFetch([fb + name('damage', 'damage.json')], 'json'),
    tryFetch([info.base + 'result.json'], 'json'),
    tryFetch([info.base + 'plan_objects.json', fb + 'plan_objects.json'], 'json'),
    tryFetch([fb + name('people', 'people.f32')], 'buf'),
    tryFetch([fb + 'people.json', info.base + 'people.json'], 'json'),
  ]);
  const nx = manifest.nx8 ?? manifest.nx, ny = manifest.ny8 ?? manifest.ny;
  const nFrames = manifest.n_frames ?? manifest.t?.length;
  const depth = depthB ? new Uint16Array(depthB) : new Uint16Array(nx * ny * nFrames);
  const nCars = carsB ? (manifest.n_cars ?? carsB.byteLength / 16 / nFrames) : 0;
  const nDebris = debB ? (manifest.n_debris ?? debJ?.n ?? debB.byteLength / 12 / nFrames) : 0;
  const nPeople = peopB ? (manifest.n_people ?? peopJ?.n_agents ?? peopB.byteLength / 12 / nFrames) : 0;
  const result = resJ || {};
  const damage = damJ || result.ledger || { total_usd: 0, by_building: {} };
  const plan_cost = +(result.plan_cost?.total_usd ?? result.plan_cost_usd ?? result.cost_usd ?? info.plan_cost_usd ?? 0);
  const plan = normPlan(planJ);
  const info2 = { ...info, total_usd: +(damage.total_usd ?? info.total_usd), plan_cost_usd: plan_cost || plan.reduce((s, o) => s + o.cost_usd, 0), label: manifest.label || result.label || info.label };
  return {
    info: info2, manifest, nx, ny, dx: manifest.dx_frame ?? 8, nFrames, t: manifest.t, gust: manifest.gust, rain: manifest.rain, tide: manifest.tide ?? null,
    depth, trees: Array.isArray(treesJ) ? treesJ : (treesJ?.trees || []), cars: carsB ? new Float32Array(carsB) : null, nCars,
    debris: debB ? new Float32Array(debB) : null, nDebris, debrisMeta: debJ, wind: windB ? new Float32Array(windB) : null,
    damage, result, plan, people: peopB ? new Float32Array(peopB) : null, nPeople, peopleMeta: peopJ,
  };
}

/** Pick none / flash / argon from available runs; URL ?a=&b=&c= or ?run= override. */
export function pickRuns(all: RunInfo[], q: URLSearchParams): RunInfo[] {
  const by = (id: string | null) => (id ? all.find((r) => r.run_id === id) : undefined);
  const storm = q.get('storm') || 'elnino_ar_2026';
  const synth = all.filter((r) => r.base.startsWith('synth'));
  // real = server runs for this storm; mock-sim runs only if requested (?mock=1)
  const real = all.filter((r) => !r.base.startsWith('synth') && (!r.storm || r.storm === storm) && (!r.mock || q.get('mock') === '1'));
  const txt = (r: RunInfo) => r.label + ' ' + r.model + ' ' + r.run_id;
  const isNone = (r: RunInfo) => /none|no.?plan|baseline/i.test(txt(r)) && !/walls/i.test(txt(r));
  const isFlash = (r: RunInfo) => /flash/i.test(txt(r));
  const isArgon = (r: RunInfo) => /argon/i.test(txt(r));
  const best = (c: RunInfo[]) => [...c].sort((x, y) => (x.total_usd || Infinity) - (y.total_usd || Infinity))[0];
  if (q.get('run')) { const r = by(q.get('run')) || (q.get('run')!.startsWith('synth') ? undefined : { run_id: q.get('run')!, label: q.get('run')!, model: '', total_usd: 0, plan_cost_usd: 0, base: '/runs/' + q.get('run') + '/' }); return r ? [r] : []; }
  const src = q.get('src');
  const realNone = real.find(isNone); // API is newest-first
  const realPlans = real.filter((r) => !isNone(r));
  if (src !== 'synth' && realNone && realPlans.length) {
    const flash = best(realPlans.filter(isFlash));
    const argon = best(realPlans.filter(isArgon)) || best(realPlans.filter((r) => !isFlash(r)));
    return [by(q.get('a')) || realNone, by(q.get('b')) || flash, by(q.get('c')) || argon].filter(Boolean) as RunInfo[];
  }
  if (src === 'real' && realNone) return [realNone];
  const pick = (p: (r: RunInfo) => boolean) => synth.find(p);
  return [by(q.get('a')) || pick(isNone), by(q.get('b')) || pick(isFlash), by(q.get('c')) || pick(isArgon)].filter(Boolean) as RunInfo[];
}
