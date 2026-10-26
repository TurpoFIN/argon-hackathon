/* ============================ API for main.ts / HUD / Plan Inspector ============================
 * setActivePlan(visuals[active].plan)    choose which run's plan the module functions act on
 * listPlanObjects() -> [{id, type, category, label, cost_usd, t_s, phase, notes, center:[x,y,z], bbox:[x0,y0,z0,x1,y1,z1]}]
 *   (local metres; to screen: new Vector3(...center).applyMatrix4(frame.matrix).project(camera))
 *   category: wall | gate | wind | pump | storage | tunnel | green | shelter | evacuation | logistics | crew | closure | trees | alert | custom | other
 * focus(id | null)             = highlightObject; the ONLY way labels appear (plan.labels is empty unless focused)
 * highlightObject(id | null)   dims the city (full-screen veil) + other plan objects, glows the target
 * buildObject(id, durationMs)  plays its rise/assemble animation (walls extrude up with sparks, beams switch on, routes draw)
 * setAllBuilt(true|false)      force everything built / back to timeline (objects build in when t passes t_s, ~1.6 s real)
 * hoverPick(sx, sy, camera, frame.matrix, cssW, cssH) -> id | null
 * declutterLabels(plan.labels, camera, frame.matrix, cssW, cssH, t, max=10, minPx=90) -> labels to draw (big, cost-ranked)
 * plan.setHighlight(true) = veil without a target ("highlight plan" mode)
 * ============================================================================================== */
// Plan objects (runs/<id>/plan_objects.json, CONTRACT v2) -> glowing holographic 3D.
// Every object builds in when the timeline reaches its t_s (grow animation in the vertex shader).
import * as THREE from 'three';
import type { City, PlanObject } from './data';
import type { LocalFrame } from './storm';
import { shared } from './storm';
import { BIAS_GLSL } from './bias';

export const TYPE_COLORS: Record<string, string> = {
  wall: '#a9b4be', floodwall: '#a9b4be', seawall: '#a9b4be', levee: '#9fb0a0', berm: '#b9a26a', gate: '#ffd23f', deployable_gate: '#ffd23f',
  sandbags: '#e8c27a', wind_screen: '#c7a6ff', tunnel: '#3df0ff', storage_tank: '#4fa8ff', tank: '#4fa8ff', cistern: '#4fa8ff', storage: '#4fa8ff',
  pump: '#3a8bff', pump_station: '#3a8bff', permeable: '#5dff9d', permeable_street: '#5dff9d', bioswale: '#3ee07a', green_roof: '#8dff5d',
  brace_trees: '#ffe15d', tree_work: '#ffe15d', prune: '#ffe15d', replace_trees: '#ffe15d', shelter: '#5dff9d', evacuation_zone: '#ffb347', evacuate: '#ffb347',
  evacuation_route: '#ff8a1f', route: '#ff8a1f', supply_route: '#5fe1ff', depot: '#d9a35b', truck: '#ffffff', crew: '#ffb54a', road_closure: '#ff3d3d',
  close_road: '#ff3d3d', medical_post: '#ff5d8a', generator: '#ffd25f', alert: '#ffb54a', custom_mesh: '#b48cff', inlet_upgrade: '#3df0ff', regrade: '#b9a26a',
};
const colorOf = (o: PlanObject) => new THREE.Color(o.color || TYPE_COLORS[o.type] || '#5fe1ff');
const is = (o: PlanObject, ...k: string[]) => k.some((s) => o.type.includes(s));
export function categoryOf(o: PlanObject): string {
  if (is(o, 'gate')) return 'gate';
  if (is(o, 'wall', 'levee', 'berm', 'sandbag', 'barrier')) return 'wall';
  if (is(o, 'wind')) return 'wind';
  if (is(o, 'pump', 'generator')) return 'pump';
  if (is(o, 'tank', 'cistern', 'storage', 'vault')) return 'storage';
  if (is(o, 'tunnel', 'pipe', 'culvert')) return 'tunnel';
  if (is(o, 'permeable', 'bioswale', 'green_roof', 'regrade', 'inlet')) return 'green';
  if (is(o, 'shelter')) return 'shelter';
  if (is(o, 'evacuat', 'route')) return 'evacuation';
  if (is(o, 'supply', 'depot', 'truck')) return 'logistics';
  if (is(o, 'crew', 'medical')) return 'crew';
  if (is(o, 'road_closure', 'close_road')) return 'closure';
  if (is(o, 'tree', 'brace', 'prune')) return 'trees';
  if (is(o, 'alert')) return 'alert';
  if (is(o, 'custom')) return 'custom';
  return 'other';
}

const holoVS = `${BIAS_GLSL}
attribute float aT; attribute float aH; attribute vec3 aC; uniform float uT, uGrow; varying float vH; varying vec3 vC; varying vec3 vN; varying vec3 vP; varying float vG;
void main(){ float g = clamp((uT - aT) / uGrow, 0.0, 1.0); g = 1.0 - pow(1.0 - g, 3.0); vG = g; vH = aH; vC = aC;
  vec3 p = position; p.z = mix(p.z - aH * 1.0, p.z, 1.0) ; p.z -= (1.0 - g) * aH;
  vN = normalize(normalMatrix * normal); vec4 mv = modelViewMatrix * vec4(p, 1.0); vP = mv.xyz; gl_Position = projectionMatrix * biasMV(mv); }`;
const holoFS = `uniform float uTime; uniform float uSolid; varying float vH; varying vec3 vC; varying vec3 vN; varying vec3 vP; varying float vG;
void main(){ if (vG <= 0.001) discard; vec3 N = normalize(vN); float rim = pow(1.0 - abs(dot(normalize(-vP), N)), 3.0);
  float lit = 0.45 + 0.55 * max(0.0, dot(N, normalize(vec3(0.3, 0.6, 0.7))));
  vec3 col = vC * lit * 1.15 + vC * rim * 0.35 + vC * (1.0 - vG) * 0.8;
  gl_FragColor = vec4(col, mix(0.7, 0.97, uSolid)); }`;
const flatVS = `${BIAS_GLSL}
attribute float aT; attribute vec3 aC; attribute float aS; uniform float uT, uGrow; varying vec3 vC; varying float vG; varying vec2 vXY; varying float vS;
void main(){ vG = clamp((uT - aT) / uGrow, 0.0, 1.0); vC = aC; vXY = position.xy; vS = aS; gl_Position = projectionMatrix * biasMV(modelViewMatrix * vec4(position, 1.0)); }`;
const flatFS = `uniform float uTime; varying vec3 vC; varying float vG; varying vec2 vXY; varying float vS;
void main(){ if (vG <= 0.001) discard; vec2 g = abs(fract(vXY / 8.0) - 0.5); float grid = smoothstep(0.46, 0.5, max(g.x, g.y));
  float pulse = vS > 0.5 ? 0.55 + 0.45 * sin(uTime * 3.0) : 1.0;
  float a = (0.07 + 0.42 * grid) * vG * pulse; gl_FragColor = vec4(vC * (0.7 + grid), a); }`;
const routeVS = `${BIAS_GLSL}
attribute float aT; attribute float aU; attribute float aV; attribute vec3 aC; uniform float uT, uGrow; varying vec3 vC; varying float vG; varying float vU; varying float vV;
void main(){ vG = clamp((uT - aT) / uGrow, 0.0, 1.0); vC = aC; vU = aU; vV = aV; gl_Position = projectionMatrix * biasMV(modelViewMatrix * vec4(position, 1.0)); }`;
const routeFS = `uniform float uTime; uniform float uSpeed; varying vec3 vC; varying float vG; varying float vU; varying float vV;
void main(){ if (vG <= 0.001) discard; float s = fract(vU / 18.0 - uTime * uSpeed); float chevron = smoothstep(0.0, 0.08, s - abs(vV - 0.5) * 0.6) * smoothstep(0.55, 0.35, s - abs(vV - 0.5) * 0.6);
  float edge = smoothstep(0.38, 0.5, abs(vV - 0.5)); float a = (0.35 + 0.65 * chevron + 0.5 * edge) * vG; gl_FragColor = vec4(vC * (0.6 + chevron), a); }`;

class Batch {
  static cur = -1; static minor = new Set<number>(); aId: number[] = []; aMn: number[] = [];
  pos: number[] = []; nor: number[] = []; aT: number[] = []; aH: number[] = []; aC: number[] = []; aU: number[] = []; aV: number[] = []; aS: number[] = [];
  tri(a: number[], b: number[], c: number[], n: number[], t: number, col: THREE.Color, h: number[] = [0, 0, 0], uv: number[][] = [[0, 0], [0, 0], [0, 0]], s = 0) {
    for (const [i, p] of [a, b, c].entries()) { this.pos.push(p[0], p[1], p[2]); this.nor.push(n[0], n[1], n[2]); this.aT.push(t); this.aH.push(h[i]); this.aC.push(col.r, col.g, col.b); this.aU.push(uv[i][0]); this.aV.push(uv[i][1]); this.aS.push(s); this.aId.push(Batch.cur); this.aMn.push(Batch.minor.has(Batch.cur) ? 1 : 0); }
  }
  quad(a: number[], b: number[], c: number[], d: number[], n: number[], t: number, col: THREE.Color, h = [0, 0, 1, 1], uv = [[0, 0], [1, 0], [1, 1], [0, 1]], s = 0) {
    this.tri(a, b, c, n, t, col, [h[0], h[1], h[2]], [uv[0], uv[1], uv[2]], s); this.tri(a, c, d, n, t, col, [h[0], h[2], h[3]], [uv[0], uv[2], uv[3]], s);
  }
  geo() {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(this.pos, 3)); g.setAttribute('normal', new THREE.Float32BufferAttribute(this.nor, 3));
    g.setAttribute('aT', new THREE.Float32BufferAttribute(this.aT, 1)); g.setAttribute('aH', new THREE.Float32BufferAttribute(this.aH, 1));
    g.setAttribute('aC', new THREE.Float32BufferAttribute(this.aC, 3)); g.setAttribute('aU', new THREE.Float32BufferAttribute(this.aU, 1));
    g.setAttribute('aV', new THREE.Float32BufferAttribute(this.aV, 1)); g.setAttribute('aS', new THREE.Float32BufferAttribute(this.aS, 1)); g.setAttribute('aId', new THREE.Float32BufferAttribute(this.aId, 1)); g.setAttribute('aMn', new THREE.Float32BufferAttribute(this.aMn, 1));
    return g;
  }
}

export interface PlanLabel { pos: THREE.Vector3; text: string; sub: string; color: string; t_s: number; kind: string; obj: PlanObject }
export interface PlanRecord { id: string; index: number; type: string; category: string; label: string; cost_usd: number; t_s: number; phase?: string; notes?: string; center: number[]; bbox: number[] }
const OBJ_GLSL = `attribute float aId; attribute float aMn; uniform float uHi, uBuildId, uBuildP, uAll; varying float vHi;
float growG(float aT){ if (uAll > 0.5) return 1.0; if (abs(aId - uBuildId) < 0.5) return uBuildP; return clamp((uT - aT) / uGrow, 0.0, 1.0); }
float hiF(){ bool me = abs(aId - uHi) < 0.5; if (aMn > 0.5 && !me) return 0.0; return uHi < -0.5 ? 1.0 : (me ? 2.0 : 0.0); }
`;
export interface PlanVisual { records: PlanRecord[]; highlightObject(id: string | null): void; buildObject(id: string, durationMs?: number): void; setAllBuilt(on: boolean): void; group: THREE.Group; labels: PlanLabel[]; alerts: { t_s: number; text: string }[]; update(t: number, time: number): void; objects: PlanObject[]; setHighlight(on: boolean): void }

function densify(coords: number[][], step: number) {
  const out: number[][] = [];
  for (let i = 0; i < coords.length - 1; i++) { const [ax, ay] = coords[i], [bx, by] = coords[i + 1]; const n = Math.max(1, Math.ceil(Math.hypot(bx - ax, by - ay) / step)); for (let k = 0; k < n; k++) out.push([ax + (bx - ax) * k / n, ay + (by - ay) * k / n]); }
  out.push(coords[coords.length - 1]); return out;
}
function inPoly(x: number, y: number, poly: number[][]) { let inside = false; for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) { const [xi, yi] = poly[i], [xj, yj] = poly[j]; if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside; } return inside; }
const fmt = (v: number) => v >= 1e9 ? `$${(v / 1e9).toFixed(2)}B` : v >= 1e6 ? `$${(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `$${(v / 1e3).toFixed(0)}k` : `$${v.toFixed(0)}`;

export function buildPlan(objects: PlanObject[], frame: LocalFrame, city: City): PlanVisual {
  const group = new THREE.Group();
  const solid = new Batch(), flat = new Batch(), route = new Batch(), tunnel = new Batch(), xray = new Batch(), edge = new Batch();
  const labels: PlanLabel[] = []; const allLabels: PlanLabel[] = []; const alerts: { t_s: number; text: string }[] = [];
  const movers: { path: number[][]; len: number; t0: number; mesh: number; speed: number }[] = [];
  const beacons: { x: number; y: number; z: number; t: number; col: THREE.Color; h: number; id?: number }[] = [];
  Batch.minor = new Set<number>();
  const MAJOR = new Set(['wall', 'gate', 'pump', 'storage', 'tunnel', 'shelter', 'evacuation', 'custom']);
  const G = (x: number, y: number) => frame.ground(x, y);
  const centroid = (c: number[][]) => { let x = 0, y = 0; for (const p of c) { x += p[0]; y += p[1]; } return [x / c.length, y / c.length]; };
  const bIndex = new Map<number, number>(); city.buildings.forEach((b, i) => bIndex.set(b.id, i));

  const ribbonWall = (o: PlanObject, coords: number[][], height: number, width: number, profile?: number[][]) => {
    const col = colorOf(o); const pts = densify(coords, 6); const t = o.t_s;
    // cross-section: list of [w, h] around the centreline (default trapezoid / rectangle)
    let prof = profile && profile.length >= 2 ? profile : null;
    if (!prof) { const w = width || (is(o, 'berm', 'levee') ? Math.max(4, height * 3) : is(o, 'sandbag') ? 1.2 : 0.6); const top = is(o, 'berm', 'levee', 'sandbag') ? w * 0.35 : w; prof = [[-w / 2, 0], [-top / 2, height], [top / 2, height], [w / 2, 0]]; }
    // visual exaggeration so the defence line reads from 1-3 km (real height is in the label)
    const vis = is(o, 'wind') ? 1.5 : is(o, 'sandbag', 'closure', 'close_road') ? 3 : 4;
    prof = prof.map(([w, h]) => [w * (is(o, 'berm', 'levee') ? 1 : 2.5), Math.max(h * vis, h > 0 ? 6 : 0)]);
    const H = Math.max(0.5, ...prof.map((p) => p[1]));
    for (let i = 0; i < pts.length - 1; i++) {
      { const [ax, ay] = pts[i], [bx, by] = pts[i + 1]; const ga = G(ax, ay) + H, gb = G(bx, by) + H; const w2 = 0.9;
        edge.quad([ax, ay, ga - w2], [bx, by, gb - w2], [bx, by, gb + w2], [ax, ay, ga + w2], [0, 0, 1], t, is(o, 'gate') ? new THREE.Color('#ffe14a') : is(o, 'sandbag', 'berm') ? col : new THREE.Color('#5fe9ff'), [H, H, H, H]); }
      const [ax, ay] = pts[i], [bx, by] = pts[i + 1]; const dx = bx - ax, dy = by - ay, l = Math.hypot(dx, dy) || 1; const nx = -dy / l, ny = dx / l;
      const ga = G(ax, ay), gb = G(bx, by);
      for (let k = 0; k < prof.length - 1; k++) {
        const [w0, h0] = prof[k], [w1, h1] = prof[k + 1];
        const A = [ax + nx * w0, ay + ny * w0, ga + h0], B = [bx + nx * w0, by + ny * w0, gb + h0], C = [bx + nx * w1, by + ny * w1, gb + h1], D = [ax + nx * w1, ay + ny * w1, ga + h1];
        const sx = (h1 - h0), sw = (w1 - w0); const nn = [nx * sx, ny * sx, -sw]; const nl = Math.hypot(...nn) || 1;
        solid.quad(A, B, C, D, [nn[0] / nl, nn[1] / nl, nn[2] / nl], t, col, [h0 / H, h0 / H, h1 / H, h1 / H].map((v) => v * H) as number[]);
      }
    }
    // end caps omitted; gates get posts
    if (is(o, 'gate')) for (const p of [coords[0], coords[coords.length - 1]]) beacons.push({ x: p[0], y: p[1], z: G(p[0], p[1]), t, col, h: height + 3 });
  };
  const drape = (o: PlanObject, poly: number[][], col: THREE.Color, lift = 0.6, pulse = 0, roofZ?: number) => {
    const xs = poly.map((p) => p[0]), ys = poly.map((p) => p[1]); const s = 8;
    for (let y = Math.min(...ys); y < Math.max(...ys); y += s) for (let x = Math.min(...xs); x < Math.max(...xs); x += s) {
      if (!inPoly(x + s / 2, y + s / 2, poly)) continue;
      const z = (xx: number, yy: number) => (roofZ ?? G(xx, yy)) + lift;
      flat.quad([x, y, z(x, y)], [x + s, y, z(x + s, y)], [x + s, y + s, z(x + s, y + s)], [x, y + s, z(x, y + s)], [0, 0, 1], o.t_s, col, [0, 0, 0, 0], undefined, pulse);
    }
    // outline wall (thin glowing curb)
    const pts = [...poly, poly[0]];
    for (let i = 0; i < pts.length - 1; i++) { const [ax, ay] = pts[i], [bx, by] = pts[i + 1]; const za = (roofZ ?? G(ax, ay)), zb = (roofZ ?? G(bx, by)); solid.quad([ax, ay, za], [bx, by, zb], [bx, by, zb + 2], [ax, ay, za + 2], [0, 0, 1], o.t_s, col, [0, 0, 2, 2]); }
  };
  const roofs = (o: PlanObject, ids: number[], col: THREE.Color) => {
    for (const id of ids) { const bi = bIndex.get(id); if (bi == null) continue; const b = city.buildings[bi]; if (b.fp.length < 3) continue; const [cx, cy] = centroid(b.fp); const zr = frame.z(cx, cy, b.z + b.h) + 0.4;
      const shape = b.fp.map((p) => new THREE.Vector2(p[0], p[1])); const tris = THREE.ShapeUtils.triangulateShape(shape, []);
      for (const [a, bb, c] of tris) flat.tri([shape[a].x, shape[a].y, zr], [shape[bb].x, shape[bb].y, zr], [shape[c].x, shape[c].y, zr], [0, 0, 1], o.t_s, col); }
  };
  const ribbon = (o: PlanObject, coords: number[][], width: number, col: THREE.Color, lift = 1.2) => {
    const pts = densify(coords, 8); let u = 0;
    for (let i = 0; i < pts.length - 1; i++) {
      const [ax, ay] = pts[i], [bx, by] = pts[i + 1]; const dx = bx - ax, dy = by - ay, l = Math.hypot(dx, dy) || 1; const nx = -dy / l * width / 2, ny = dx / l * width / 2;
      const za = G(ax, ay) + lift, zb = G(bx, by) + lift;
      route.quad([ax + nx, ay + ny, za], [bx + nx, by + ny, zb], [bx - nx, by - ny, zb], [ax - nx, ay - ny, za], [0, 0, 1], o.t_s, col, [0, 0, 0, 0], [[u, 0], [u + l, 0], [u + l, 1], [u, 1]]);
      u += l;
    }
    return u;
  };
  const tube = (o: PlanObject, coords: number[][], r: number, depth: number, col: THREE.Color) => {
    const pts = densify(coords, 10); let u = 0; const seg = 8;
    for (let i = 0; i < pts.length - 1; i++) {
      const [ax, ay] = pts[i], [bx, by] = pts[i + 1]; const dx = bx - ax, dy = by - ay, l = Math.hypot(dx, dy) || 1; const nx = -dy / l, ny = dx / l;
      const za = G(ax, ay) - depth, zb = G(bx, by) - depth;
      for (let k = 0; k < seg; k++) {
        const a0 = k / seg * Math.PI * 2, a1 = (k + 1) / seg * Math.PI * 2;
        const off = (a: number) => [nx * Math.cos(a) * r, ny * Math.cos(a) * r, Math.sin(a) * r];
        const o0 = off(a0), o1 = off(a1);
        tunnel.quad([ax + o0[0], ay + o0[1], za + o0[2]], [bx + o0[0], by + o0[1], zb + o0[2]], [bx + o1[0], by + o1[1], zb + o1[2]], [ax + o1[0], ay + o1[1], za + o1[2]], [Math.cos(a0) * nx, Math.cos(a0) * ny, Math.sin(a0)], o.t_s, col, [0, 0, 0, 0], [[u, k / seg], [u + l, k / seg], [u + l, (k + 1) / seg], [u, (k + 1) / seg]]);
      }
      u += l;
    }
    // shafts at ends
    for (const p of [coords[0], coords[coords.length - 1]]) beacons.push({ x: p[0], y: p[1], z: G(p[0], p[1]) - depth, t: o.t_s, col, h: depth + 8 });
  };
  const cylinder = (o: PlanObject, x: number, y: number, r: number, h: number, col: THREE.Color, below = 0, B: Batch = solid) => {
    const seg = 28; const z0 = G(x, y) - below; const solid = B;
    for (let k = 0; k < seg; k++) {
      const a0 = k / seg * Math.PI * 2, a1 = (k + 1) / seg * Math.PI * 2; const c0 = Math.cos(a0), s0 = Math.sin(a0), c1 = Math.cos(a1), s1 = Math.sin(a1);
      solid.quad([x + c0 * r, y + s0 * r, z0], [x + c1 * r, y + s1 * r, z0], [x + c1 * r, y + s1 * r, z0 + h], [x + c0 * r, y + s0 * r, z0 + h], [c0, s0, 0], o.t_s, col, [0, 0, h, h]);
      solid.tri([x, y, z0 + h], [x + c0 * r, y + s0 * r, z0 + h], [x + c1 * r, y + s1 * r, z0 + h], [0, 0, 1], o.t_s, col, [h, h, h]);
    }
  };
  const box = (o: PlanObject, x: number, y: number, w: number, d: number, h: number, col: THREE.Color, rot = 0) => {
    const z0 = G(x, y); const c = Math.cos(rot), s = Math.sin(rot);
    const P = (u: number, v: number, z: number) => [x + u * c - v * s, y + u * s + v * c, z0 + z];
    const hw = w / 2, hd = d / 2;
    const corners = [[-hw, -hd], [hw, -hd], [hw, hd], [-hw, hd]];
    for (let k = 0; k < 4; k++) { const [u0, v0] = corners[k], [u1, v1] = corners[(k + 1) % 4]; const nx = (v1 - v0), ny = -(u1 - u0); const l = Math.hypot(nx, ny);
      solid.quad(P(u0, v0, 0), P(u1, v1, 0), P(u1, v1, h), P(u0, v0, h), [(nx * c - ny * s) / l, (nx * s + ny * c) / l, 0], o.t_s, col, [0, 0, h, h]); }
    solid.quad(P(-hw, -hd, h), P(hw, -hd, h), P(hw, hd, h), P(-hw, hd, h), [0, 0, 1], o.t_s, col, [h, h, h, h]);
  };

  const records: PlanRecord[] = [];
  for (const [oi, o] of objects.entries()) {
    Batch.cur = oi; const recStart = labels.length;
    if (!MAJOR.has(categoryOf(o)) || (is(o, 'evacuat') && o.kind === 'polygon')) Batch.minor.add(oi);
    const beaconStart = beacons.length;
    const c = o.coords || []; if (!c.length && o.type !== 'alert' && !o.mesh) continue;
    const col = colorOf(o); const [cx, cy] = c.length ? centroid(c) : [frame.cx, frame.cy];
    const H = o.height || 0; let lp: number[] | null = [cx, cy, G(cx, cy) + Math.max(H, 6) + 6];
    if (o.type === 'alert' || o.type === 'alert_zone') { alerts.push({ t_s: o.t_s, text: o.label || o.raw?.text || 'Alert' }); if (o.kind === 'polygon' && c.length > 2) drape(o, c, col, 0.8, 1); else continue; }
    else if (o.type === 'custom_mesh' || o.kind === 'mesh') {
      const m = o.mesh; if (m?.positions?.length) {
        const g0 = G(cx, cy); const P = m.positions; const I = m.indices?.length ? m.indices : Array.from({ length: P.length / 3 }, (_, i) => i);
        const absolute = o.raw?.geometry?.z_base === 'absolute';
        const V = (i: number) => { let x = P[i * 3], y = P[i * 3 + 1]; const z = P[i * 3 + 2]; if (x > 1e5) { x -= city.meta.x0; y -= city.meta.y0; } return [x, y, absolute ? frame.z(x, y, z) : g0 + z]; };
        let mh = 1; for (let i = 0; i < P.length / 3; i++) mh = Math.max(mh, P[i * 3 + 2]);
        for (let k = 0; k + 2 < I.length; k += 3) { const a = V(I[k]), b = V(I[k + 1]), cc = V(I[k + 2]);
          const u = [b[0] - a[0], b[1] - a[1], b[2] - a[2]], v = [cc[0] - a[0], cc[1] - a[1], cc[2] - a[2]]; const n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]; const nl = Math.hypot(...n) || 1;
          solid.tri(a, b, cc, [n[0] / nl, n[1] / nl, n[2] / nl], o.t_s, col, [a[2] - g0, b[2] - g0, cc[2] - g0]); }
        lp = [cx, cy, g0 + mh + 8];
      }
    }
    else if (is(o, 'tunnel', 'pipe', 'culvert')) tube(o, c, Math.max(2.5, (o.width || 6) / 2), Math.max(12, o.raw?.depth_m || 25), col);
    else if (is(o, 'evacuation_route', 'route', 'supply_run') && c.length > 1) { const len = ribbon(o, c, Math.max(14, (o.width || 7) * 2), col, 2.2); if (is(o, 'supply')) movers.push({ path: c, len, t0: o.t_s, mesh: 0, speed: 9 }); }
    else if (is(o, 'road_closure', 'close_road') && c.length > 1) { ribbonWall(o, c, 1.2, 0.5); }
    else if (is(o, 'wall', 'seawall', 'levee', 'berm', 'gate', 'sandbag', 'wind_screen', 'barrier') && c.length > 1) ribbonWall(o, c, H || (is(o, 'wind') ? 10 : 1.5), o.width, o.profile);
    else if (is(o, 'tank', 'cistern', 'storage', 'vault')) {
      const hh = Math.abs(H) || 8; const vol = o.raw?.volume_m3 || o.capacity;
      const r = o.raw?.radius_m || (o.width ? o.width / 2 : vol ? Math.sqrt(vol / (Math.PI * hh)) : 20);
      if (H < 0) { cylinder(o, cx, cy, Math.min(80, r), hh, col, hh + 1, xray); beacons.push({ x: cx, y: cy, z: G(cx, cy), t: o.t_s, col, h: 30 }); lp = [cx, cy, G(cx, cy) + 20]; }
      else { cylinder(o, cx, cy, Math.min(80, r), hh, col, 0); lp = [cx, cy, G(cx, cy) + hh + 8]; }
    }
    else if (is(o, 'pump', 'generator')) { box(o, cx, cy, 26, 16, 10, col); box(o, cx - 7, cy, 6, 6, 18, new THREE.Color('#9ec9ff')); box(o, cx + 7, cy, 6, 6, 15, new THREE.Color('#9ec9ff')); beacons.push({ x: cx, y: cy, z: G(cx, cy), t: o.t_s, col, h: 90 }); lp = [cx, cy, G(cx, cy) + 30]; }
    else if (is(o, 'shelter')) {
      const bid = o.raw?.building_id ?? o.raw?.building; const bi = bid != null ? bIndex.get(+bid) : undefined;
      if (bi != null) { const b = city.buildings[bi]; const pts = [...b.fp, b.fp[0]]; const [bx, by] = centroid(b.fp); const z0 = frame.z(bx, by, b.z);
        for (let i = 0; i < pts.length - 1; i++) solid.quad([pts[i][0], pts[i][1], z0], [pts[i + 1][0], pts[i + 1][1], z0], [pts[i + 1][0], pts[i + 1][1], z0 + b.h + 2], [pts[i][0], pts[i][1], z0 + b.h + 2], [0, 0, 1], o.t_s, col, [0, 0, b.h + 2, b.h + 2]);
        beacons.push({ x: bx, y: by, z: z0 + b.h, t: o.t_s, col, h: 260 }); lp = [bx, by, z0 + b.h + 30]; }
      else { cylinder(o, cx, cy, 14, 10, col); beacons.push({ x: cx, y: cy, z: G(cx, cy), t: o.t_s, col, h: 120 }); }
    }
    else if (is(o, 'evacuat') && c.length > 2) drape(o, c, col, 1.0, 1);
    else if (is(o, 'green_roof')) { const ids: number[] = o.raw?.building_ids || []; if (ids.length) roofs(o, ids, col); else if (c.length > 2) { const inside = city.buildings.filter((b) => { const [bx, by] = centroid(b.fp); return inPoly(bx, by, c); }).map((b) => b.id); roofs(o, inside.slice(0, 400), col); } }
    else if (c.length > 2 && o.kind === 'polygon') drape(o, c, col, is(o, 'brace', 'tree', 'prune') ? 1.5 : 0.6, 0);
    else if (c.length > 1) ribbon(o, c, o.width || 5, col);
    else { // generic point: depot, crew, medical post, ...
      if (is(o, 'depot')) { for (let k = 0; k < 6; k++) box(o, cx + (k % 3) * 7 - 7, cy + Math.floor(k / 3) * 7 - 3.5, 5.5, 5.5, 4 + (k % 2) * 3, col, 0); }
      else if (is(o, 'crew')) { for (let k = 0; k < 3; k++) { box(o, cx + k * 9 - 9, cy, 3, 7, 2.6, col, 0.4); box(o, cx + k * 9 - 9, cy + 2, 2.8, 2.5, 3.6, new THREE.Color('#ffffff'), 0.4); } }
      else box(o, cx, cy, 10, 10, 6, col, 0.6);
      beacons.push({ x: cx, y: cy, z: G(cx, cy), t: o.t_s, col, h: is(o, 'medical') ? 80 : 50 });
      if (is(o, 'depot')) for (const tgt of (o.raw?.routes || [])) if (Array.isArray(tgt) && tgt.length > 1) movers.push({ path: tgt, len: 0, t0: o.t_s, mesh: 0, speed: 9 });
    }
    if (lp && o.type !== 'alert' && o.label) allLabels.push({ pos: new THREE.Vector3(lp[0], lp[1], lp[2]), text: o.label || o.type.replace(/_/g, ' '), sub: [o.cost_usd ? fmt(o.cost_usd) : '', o.capacity ? `cap ${o.capacity}` : ''].filter(Boolean).join(' · '), color: '#' + col.getHexString(), t_s: o.t_s, kind: o.type, obj: o });
    { const xs = c.map((q) => q[0]), ys = c.map((q) => q[1]); const z0 = c.length ? G(cx, cy) : 0;
      records.push({ id: o.id, index: oi, type: o.type, category: categoryOf(o), label: o.label || o.type.replace(/_/g, ' '), cost_usd: o.cost_usd, t_s: o.t_s, phase: o.phase, notes: o.raw?.notes,
        center: lp ? [lp[0], lp[1], lp[2]] : [cx, cy, z0], bbox: c.length ? [Math.min(...xs), Math.min(...ys), z0, Math.max(...xs), Math.max(...ys), z0 + Math.max(6, H * 4)] : [cx, cy, 0, cx, cy, 0] }); void recStart; for (let k = beaconStart; k < beacons.length; k++) beacons[k].id = oi; }
  }
  // beacons: vertical light beams
  const beam = new Batch();
  for (const b of beacons) { Batch.cur = b.id ?? -1; const w = 3.2; for (const [dx, dy] of [[1, 0], [0, 1]]) beam.quad([b.x - dx * w, b.y - dy * w, b.z], [b.x + dx * w, b.y + dy * w, b.z], [b.x + dx * w, b.y + dy * w, b.z + b.h], [b.x - dx * w, b.y - dy * w, b.z + b.h], [dy, -dx, 0], b.t, b.col, [0, 0, 1, 1]); }

  const uT = { value: -1e9 };
  const objU = { uHi: { value: -1 }, uBuildId: { value: -1 }, uBuildP: { value: 1 }, uAll: { value: 0 } };
  const mk = (b: Batch, vs: string, fs: string, opts: any, order: number, extra: any = {}) => {
    if (!b.pos.length) return null;
    vs = vs.replace(/clamp\(\(uT - aT\) ?\/ ?uGrow, 0\.0, 1\.0\)/g, 'growG(aT)').replace('void main(){', OBJ_GLSL + 'void main(){ vHi = hiF();');
    fs = 'varying float vHi;\n' + fs.replace('void main(){', 'void main(){ if (vHi <= 0.01) discard;').replace(/}\s*$/, ' gl_FragColor.a *= min(vHi, 1.0); gl_FragColor.rgb *= vHi > 1.5 ? 1.8 : 1.0; }');
    extra = { ...objU, ...extra };
    const m = new THREE.Mesh(b.geo(), new THREE.ShaderMaterial({ vertexShader: vs, fragmentShader: fs, transparent: true, depthWrite: false, side: THREE.DoubleSide, uniforms: { uT, uTime: shared.time, ...extra }, ...opts }));
    m.frustumCulled = false; m.renderOrder = order; group.add(m); return m;
  };
  const uGrow = { value: 3600 };
  mk(solid, holoVS, holoFS, { depthWrite: true }, 6, { uSolid: { value: 0.85 }, uGrow });
  mk(edge, holoVS, `uniform float uTime; varying float vG; varying vec3 vC; void main(){ if (vG <= 0.001) discard; gl_FragColor = vec4(vC * 2.2 + 0.4 + (1.0 - vG) * 3.0, 1.0); }`, { blending: THREE.AdditiveBlending }, 10, { uGrow });
  // highlight-plan mode: full-screen dim drawn after the scene, before the plan
  const dim = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), new THREE.ShaderMaterial({ transparent: true, depthTest: false, depthWrite: false, uniforms: { uA: { value: 0.6 } },
    vertexShader: 'void main(){ gl_Position = vec4(position.xy, 0.0, 1.0); }', fragmentShader: 'uniform float uA; void main(){ gl_FragColor = vec4(0.0, 0.02, 0.05, uA); }' }));
  dim.frustumCulled = false; dim.renderOrder = 4.5; dim.visible = false; group.add(dim);
  mk(flat, flatVS, flatFS, { polygonOffset: true, polygonOffsetFactor: -2 }, 5, { uGrow });
  mk(route, routeVS, routeFS, { blending: THREE.AdditiveBlending }, 7, { uSpeed: { value: 1.2 }, uGrow });
  mk(tunnel, routeVS, routeFS, { blending: THREE.AdditiveBlending, depthTest: false }, 8, { uSpeed: { value: 2.0 }, uGrow });
  mk(xray, holoVS, holoFS, { blending: THREE.AdditiveBlending, depthTest: false }, 8, { uSolid: { value: 0.2 }, uGrow });
  mk(beam, `attribute float aT; attribute float aH; attribute vec3 aC; uniform float uT, uGrow; varying float vH; varying vec3 vC; varying float vG; void main(){ vG = clamp((uT - aT)/uGrow, 0.0, 1.0); vH = aH; vC = aC; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }`,
    `uniform float uTime; varying float vH; varying vec3 vC; varying float vG; void main(){ if (vG <= 0.0) discard; float a = (1.0 - vH) * (0.55 + 0.25 * sin(uTime * 2.0 + vH * 10.0)) * vG; gl_FragColor = vec4(vC * a * 1.6, a); }`,
    { blending: THREE.AdditiveBlending }, 9, { uGrow });
  // trucks moving along supply routes
  let trucks: THREE.InstancedMesh | null = null;
  movers.length = 0; // trucks off (clarity)
  if (movers.length) {
    for (const m of movers) { let l = 0; for (let i = 0; i < m.path.length - 1; i++) l += Math.hypot(m.path[i + 1][0] - m.path[i][0], m.path[i + 1][1] - m.path[i][1]); m.len = l; }
    const per = 4; trucks = new THREE.InstancedMesh(new THREE.BoxGeometry(2.6, 8, 3).translate(0, 0, 1.6), new THREE.MeshBasicMaterial({ color: 0xffffff, toneMapped: false }), movers.length * per);
    trucks.frustumCulled = false; group.add(trucks);
  }
  let lastTime = 0, lastT = 0, rate = 2000; let build: { id: number; t0: number; dur: number } | null = null;
  const mm = new THREE.Matrix4(), qq = new THREE.Quaternion(), vv = new THREE.Vector3(), ss = new THREE.Vector3(1, 1, 1), zz = new THREE.Vector3(0, 0, 1);
  return {
    group, labels, alerts, objects,
    records,
    setHighlight(on: boolean) { dim.visible = on; },
    highlightObject(id: string | null) { const r = id == null ? undefined : records.find((x) => x.id === id); objU.uHi.value = r ? r.index : -1; dim.visible = !!r;
      labels.length = 0; if (r) { const l = allLabels.find((x) => x.obj.id === r.id); if (l) labels.push(l); } },
    buildObject(id: string, durationMs = 2500) { const r = records.find((x) => x.id === id); if (!r) return; build = { id: r.index, t0: performance.now(), dur: durationMs }; objU.uBuildId.value = r.index; objU.uBuildP.value = 0; },
    setAllBuilt(on: boolean) { objU.uAll.value = on ? 1 : 0; },
    update(t: number, time: number) {
      const dtr = time - lastTime, dts = t - lastT; lastTime = time; lastT = t;
      if (dtr > 0 && dtr < 0.5 && dts > 0) rate = rate * 0.9 + (dts / dtr) * 0.1;
      uGrow.value = Math.max(240, Math.min(200000, rate * 1.6));
      uT.value = t;
      if (build) { const p = (performance.now() - build.t0) / build.dur; objU.uBuildP.value = Math.min(1, p); if (p >= 1.5) { build = null; objU.uBuildId.value = -1; } }
      if (trucks) {
        let k = 0;
        for (const m of movers) for (let j = 0; j < 4; j++, k++) {
          const active = t > m.t0; let s = ((time * m.speed * 6 + j * m.len / 4) % (m.len || 1));
          let i = 0; while (i < m.path.length - 2 && s > Math.hypot(m.path[i + 1][0] - m.path[i][0], m.path[i + 1][1] - m.path[i][1])) { s -= Math.hypot(m.path[i + 1][0] - m.path[i][0], m.path[i + 1][1] - m.path[i][1]); i++; }
          const [ax, ay] = m.path[i], [bx, by] = m.path[i + 1]; const l = Math.hypot(bx - ax, by - ay) || 1; const f = Math.min(1, s / l);
          const x = ax + (bx - ax) * f, y = ay + (by - ay) * f; qq.setFromAxisAngle(zz, Math.atan2(by - ay, bx - ax) - Math.PI / 2);
          mm.compose(vv.set(x, y, active ? G(x, y) : -1e4), qq, ss); trucks.setMatrixAt(k, mm);
        }
        trucks.instanceMatrix.needsUpdate = true;
      }
    },
  };
}

// ---------------- module-level plan API (for the HUD / Plan Inspector / build tour) ----------------
let activePlan: PlanVisual | null = null;
/** Choose which run's plan the functions below act on (e.g. visuals[active].plan). */
export function setActivePlan(p: PlanVisual | null) { if (activePlan && activePlan !== p) { activePlan.highlightObject(null); } activePlan = p; }
export function getActivePlan() { return activePlan; }
/** [{id, type, category, label, cost_usd, t_s, phase, notes, center:[x,y,z] local m, bbox:[x0,y0,z0,x1,y1,z1]}] */
export function listPlanObjects(p = activePlan) { return p ? p.records.map(({ index, ...r }) => r) : []; }
/** Dim everything else, glow the target (null clears). */
export function highlightObject(id: string | null, p = activePlan) { p?.highlightObject(id); }
/** Focus one object: show only its label, dim the rest (null = back to the clean overview, no labels). */
export function focus(id: string | null, p = activePlan) { p?.highlightObject(id); }
/** Play the rise/assemble animation for one object (ignores the timeline while it runs). */
export function buildObject(id: string, durationMs = 2500, p = activePlan) { p?.buildObject(id, durationMs); }
/** Force every object visible regardless of timeline t (false = back to timeline-driven build-in). */
export function setAllBuilt(on: boolean, p = activePlan) { p?.setAllBuilt(on); }
/** Pick the object whose centre is nearest to a screen point (CSS px) within 48 px.
 *  camera = the three camera used for rendering; world = LocalFrame.matrix (local -> world). */
export function hoverPick(screenX: number, screenY: number, camera: THREE.Camera, world: THREE.Matrix4, width: number, height: number, p = activePlan): string | null {
  if (!p) return null; const v = new THREE.Vector3(); let best: string | null = null, bd = 48 * 48;
  for (const r of p.records) {
    v.set(r.center[0], r.center[1], r.center[2]).applyMatrix4(world).project(camera); if (v.z > 1 || v.z < -1) continue;
    const sx = (v.x * 0.5 + 0.5) * width, sy = (-v.y * 0.5 + 0.5) * height; const d = (sx - screenX) ** 2 + (sy - screenY) ** 2;
    if (d < bd) { bd = d; best = r.id; }
  }
  return best;
}
/** Labels decluttered by screen distance: returns at most `max` labels, larger-cost first, >= minPx apart. */
export function declutterLabels(labels: PlanLabel[], camera: THREE.Camera, world: THREE.Matrix4, width: number, height: number, t: number, max = 10, minPx = 90) {
  const v = new THREE.Vector3(); const out: PlanLabel[] = []; const pts: number[][] = [];
  for (const l of [...labels].filter((l) => t >= l.t_s).sort((a, b) => (b.obj.cost_usd || 0) - (a.obj.cost_usd || 0))) {
    v.copy(l.pos).applyMatrix4(world).project(camera); if (v.z > 1 || Math.abs(v.x) > 1 || Math.abs(v.y) > 1) continue;
    const sx = (v.x * 0.5 + 0.5) * width, sy = (-v.y * 0.5 + 0.5) * height;
    if (pts.some(([x, y]) => (x - sx) ** 2 + (y - sy) ** 2 < minPx * minPx)) continue;
    pts.push([sx, sy]); out.push(l); if (out.length >= max) break;
  }
  return out;
}
