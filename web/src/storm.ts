/* ============================ API for main.ts / HUD agent ============================
 * Frame: everything is in tile-local metres (x east, y north from grid SW, z = elevation) under a parent group whose
 * matrix is LocalFrame.matrix (local -> ENU). Per run: `new RunVisual(run, frame, city, waterGeo, treeGeoms())`
 *   .group                 add to the scene; toggle .visible per view / compare strip
 *   .update(t, dtReal, time)  t = storm sim seconds (negative = pre-landfall), dtReal = real s, time = shared.time.value
 *   .frameAt(t)            fractional frame index;  .damageAt(t) -> $ so far (HUD number; no red damage visuals any more)
 *   .plan                  PlanVisual (see plan.ts) — labels[] have live text (shelter occupancy updates every 0.5 s)
 *   .people.countNear(fi, x, y, r)  sheltered people near a point;  .trees.countFailed(t)
 * Storm ramp: `const drive = new StormDriver()` once, then every frame `const d = drive.update(run, t, dtReal)`
 *   -> sets shared.rain / shared.gust smoothly (calm -72 h, drizzle + wind from -12 h, series after landfall) and returns
 *   {rain mm/h, gust m/s, tide, rn, gn, dark 0..1 (use for the CSS grade), lightningRate strikes/s (peak only)}.
 *   Please use it instead of the step at t = 0 (and don't hard-toggle wind/spray visibility at t = 0; they fade with gust).
 * shared.lightning.value = flash 0..1; shared.cloud advances automatically with gust.
 * Water: unmistakable cyan->deep blue, lifted 0.6 m; trees topple, tumble downwind with leaf bursts; people have trails.
 * ===================================================================================== */
// The storm layer: everything the simulation produces, drawn with three.js in the tile-local frame
// (x east, y north metres from grid SW corner, z = elevation). A parent group carries the affine
// local -> ENU transform, so the same layer renders inside ArcGIS (RenderNode) and in stylized mode.
import * as THREE from 'three';
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js';
import type { City, Run } from './data';
import type { TileFrame } from './geo';
import { buildPlan, PlanVisual } from './plan';
import { BIAS_GLSL } from './bias';
export { BIAS_GLSL };

const R_EARTH = 6371000;

export class LocalFrame {
  matrix = new THREE.Matrix4();
  cx: number; cy: number; zOffset: number;
  constructor(public city: City, public tile: TileFrame) {
    this.cx = tile.w / 2; this.cy = tile.h / 2; this.zOffset = tile.zOffset;
    const e = (x: number, y: number, z: number) => tile.toENU(x, y, z - tile.zOffset) as number[]; // toENU adds zOffset itself
    const c = e(this.cx, this.cy, 0), d = 100;
    const ex = e(this.cx + d, this.cy, 0), wx = e(this.cx - d, this.cy, 0);
    const ny = e(this.cx, this.cy + d, 0), sy = e(this.cx, this.cy - d, 0);
    const up = e(this.cx, this.cy, d), dn = e(this.cx, this.cy, -d);
    const col = (p: number[], q: number[]) => [(p[0] - q[0]) / (2 * d), (p[1] - q[1]) / (2 * d), (p[2] - q[2]) / (2 * d)];
    const X = col(ex, wx), Y = col(ny, sy), Z = col(up, dn);
    const t = [c[0] - X[0] * this.cx - Y[0] * this.cy, c[1] - X[1] * this.cx - Y[1] * this.cy, c[2] - X[2] * this.cx - Y[2] * this.cy];
    // c already includes zOffset in its z (toENU adds it); heights we pass are NAVD88 so translation covers the datum.
    this.matrix.set(X[0], Y[0], Z[0], t[0], X[1], Y[1], Z[1], t[1], X[2], Y[2], Z[2], t[2], 0, 0, 0, 1);
  }
  /** local z for an elevation (NAVD88), with Earth curvature drop so we hug the globe. */
  z(x: number, y: number, elev: number) { return elev - ((x - this.cx) ** 2 + (y - this.cy) ** 2) / (2 * R_EARTH); }
  ground(x: number, y: number) { return this.z(x, y, this.city.zAt(x, y)); }
}

export const windD = { value: new THREE.Vector2(0.34, 0.94) };
export const shared = {
  cloud: { value: 0 },
  time: { value: 0 }, lightning: { value: 0 }, gust: { value: 0.3 }, rain: { value: 0.5 },
  camPos: { value: new THREE.Vector3() },
};

// ---------------- storm driver (smooth ramp for every effect) ----------------
/** Smoothly ramps the storm: calm overcast (-72 h) -> drizzle + rising wind (-12 h..0) -> the run's rain/gust series
 *  (normalised), eased with a real-time EMA so nothing pops. Writes shared.rain / shared.gust and returns the values. */
export class StormDriver {
  rn = 0.02; gn = 0.08; private lastT = NaN;
  update(run: Run | undefined, t: number, dtReal: number) {
    let rain = 0, gust = 0, tide = 1, mr = 50, mg = 30;
    if (run && run.t?.length) {
      const n = run.t.length, ts = run.t; mr = Math.max(1, ...run.rain); mg = Math.max(1, ...run.gust);
      const tt = Math.max(ts[0], Math.min(ts[n - 1], t)); let f = Math.floor(((tt - ts[0]) / ((ts[n - 1] - ts[0]) || 1)) * (n - 1)); f = Math.max(0, Math.min(n - 2, f));
      const a = (tt - ts[f]) / ((ts[f + 1] - ts[f]) || 1);
      rain = run.rain[f] + (run.rain[f + 1] - run.rain[f]) * a; gust = run.gust[f] + (run.gust[f + 1] - run.gust[f]) * a;
      tide = run.tide ? run.tide[f] + (run.tide[f + 1] - run.tide[f]) * a : 1;
    }
    const pre = Math.max(0, Math.min(1, (t + 12 * 3600) / (12 * 3600))); // 0 at -12 h, 1 at landfall
    const ramp = (x: number) => x * x * (3 - 2 * x);
    let tr = t < 0 ? 0.02 + 0.13 * ramp(pre) : Math.max(0.15 * (1 - t / 3600), rain / mr);
    let tg = t < 0 ? 0.08 + 0.27 * ramp(pre) : Math.max(0.35 * (1 - t / 3600), gust / mg);
    if (t >= 0 && t < 1800) { const k = t / 1800; tr = Math.max(tr, 0.15 * (1 - k)); tg = Math.max(tg, 0.35 * (1 - k)); }
    const jump = !isFinite(this.lastT) || Math.abs(t - this.lastT) > 6 * 3600; this.lastT = t;
    const e = jump ? 1 : 1 - Math.exp(-dtReal * 1.5);
    this.rn += (tr - this.rn) * e; this.gn += (tg - this.gn) * e;
    shared.rain.value = this.rn; shared.gust.value = this.gn;
    const peak = Math.max(0, (this.rn * this.gn - 0.3) / 0.7);
    return { rain: t < 0 ? this.rn * mr : rain, gust: t < 0 ? this.gn * mg : gust, tide, rn: this.rn, gn: this.gn,
      /** 0 bright overcast .. 1 darkest storm */ dark: Math.min(1, 0.15 + 0.85 * Math.max(this.rn, this.gn * 0.8)),
      /** expected lightning strikes per real second (only near the peak) */ lightningRate: 0.12 * Math.pow(peak, 1.5) };
  }
}

// ---------------- water ----------------
const waterVS = /* glsl */`
uniform sampler2D uDepthA, uDepthB, uTerr;
${BIAS_GLSL}
uniform float uFrac, uTide, uTime, uGust;
uniform vec2 uGrid;
attribute vec2 aCell;
varying float vD; varying vec2 vFlow; varying vec3 vPos; varying float vBay; varying vec2 vEdge; varying float vShore;
float dep(ivec2 c){ c = clamp(c, ivec2(0), ivec2(uGrid) - 1); vec2 tb = texelFetch(uTerr, c, 0).rg;
  if (tb.g > 0.5) return max(0.0, uTide - tb.r);
  return mix(texelFetch(uDepthA, c, 0).r, texelFetch(uDepthB, c, 0).r, uFrac); }
float H(ivec2 c){ c = clamp(c, ivec2(0), ivec2(uGrid) - 1); return texelFetch(uTerr, c, 0).r + dep(c); }
void main(){
  ivec2 c = ivec2(aCell);
  vec2 tb = texelFetch(uTerr, c, 0).rg;
  float d = dep(c);
  vBay = tb.g;
  { float nb = 0.0; for (int k = -3; k <= 3; k += 3) for (int l = -3; l <= 3; l += 3) nb += texelFetch(uTerr, clamp(c + ivec2(k, l), ivec2(0), ivec2(uGrid) - 1), 0).g; vShore = tb.g > 0.5 ? smoothstep(8.5, 6.0, nb) : 0.0; }
  vD = d;
  vFlow = -vec2(H(c + ivec2(1,0)) - H(c - ivec2(1,0)), H(c + ivec2(0,1)) - H(c - ivec2(0,1)));
  vec3 p = position;
  if (tb.g > 0.5) { float lvl = max(0.7, uTide - 0.35); p.z += (lvl - tb.r) + (0.15 + 0.5 * uGust) * sin(uTime * 1.7 + p.x * 0.045 + p.y * 0.03) * sin(uTime * 0.9 + p.y * 0.021); vD = 1.0; }
  else { p.z += max(d, 0.0) + 0.6; }
  vPos = p;
  vEdge = aCell / uGrid;
  gl_Position = projectionMatrix * biasMV(modelViewMatrix * vec4(p, 1.0));
}`;
const waterFS = /* glsl */`
uniform float uTime, uLightning, uRain, uGust, uMode;
uniform vec3 uCam; uniform vec2 uWindD;
varying float vD; varying vec2 vFlow; varying vec3 vPos; varying float vBay; varying vec2 vEdge; varying float vShore;
float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float noise(vec2 p){ vec2 i = floor(p), f = fract(p); f = f*f*(3.0-2.0*f);
  return mix(mix(hash(i), hash(i+vec2(1,0)), f.x), mix(hash(i+vec2(0,1)), hash(i+vec2(1,1)), f.x), f.y); }
float rings(vec2 p, float t){ vec2 cell = floor(p); vec2 f = fract(p) - 0.5; float h = hash(cell);
  float ph = fract(t * 0.9 + h); float r = length(f + (vec2(hash(cell+3.1), hash(cell+7.7)) - 0.5) * 0.5);
  return smoothstep(0.06, 0.0, abs(r - ph * 0.45)) * (1.0 - ph); }
void main(){
  float d = vD;
  if (d < 0.035) discard;
  vec2 fl = vFlow; float sp = length(fl); vec2 dir = sp > 1e-4 ? fl / sp : vec2(0.0);
  float spd = clamp(sp * 6.0, 0.0, 1.0);
  vec2 q = vPos.xy * 0.22 - dir * uTime * (0.6 + 3.0 * spd);
  float n1 = noise(q) + 0.5 * noise(q * 2.3 + uTime * 0.4);
  float n2 = noise(q + vec2(0.37, 0.11)) + 0.5 * noise(q * 2.3 + vec2(0.37, 0.11) + uTime * 0.4);
  float n3 = noise(q + vec2(0.0, 0.37)) + 0.5 * noise(q * 2.3 + vec2(0.0, 0.37) + uTime * 0.4);
  vec3 V = normalize(uCam - vPos);
  float dist = length(uCam - vPos);
  float amp = mix(1.0, 0.12, smoothstep(250.0, 1600.0, dist));
  float rr = rings(vPos.xy * 0.9, uTime) * uRain * amp;
  vec3 N = normalize(vec3(((n1 - n2) * (0.5 + spd) + rr * 0.6) * amp, ((n1 - n3) * (0.5 + spd) + rr * 0.6) * amp, 1.0));
  if (vBay > 0.5) { N = normalize(vec3((n1 - n2) * (0.9 + uGust) * amp, (n1 - n3) * (0.9 + uGust) * amp, 1.0)); }
  float fres = 0.04 + 0.96 * pow(1.0 - max(dot(N, V), 0.0), 5.0);
  vec3 R = reflect(-V, N);
  vec3 sky = mix(vec3(0.46, 0.54, 0.58), vec3(0.74, 0.80, 0.84), clamp(R.z, 0.0, 1.0)) + uLightning * vec3(0.6, 0.65, 0.8);
  // unmistakable flood water: shallow light cyan -> deep dark blue, self-lit so it reads in the storm grade
  float k = smoothstep(0.05, 1.5, d);
  vec3 col = mix(vec3(0.30, 0.86, 0.98), vec3(0.04, 0.22, 0.62), k);
  col = mix(col, vec3(0.02, 0.10, 0.38), smoothstep(1.5, 3.5, d));
  col = col * (0.85 + 0.25 * (n1 - 0.75)) + vec3(0.05, 0.25, 0.35) * 0.35 * uMode;
  col = mix(col, sky, 0.10 + 0.45 * fres);
  vec3 L = normalize(vec3(0.3, -0.5, 0.6));
  float spec = pow(max(dot(R, L), 0.0), 90.0) + 0.35 * pow(max(dot(R, normalize(vec3(-0.5, 0.4, 0.5))), 0.0), 30.0);
  col += spec * (0.8 + 2.5 * uLightning);
  float foam = smoothstep(0.55, 0.95, noise(q * 3.0)) * spd * smoothstep(0.05, 0.4, d) * amp;
  float rim = 1.0 - smoothstep(0.04, 0.16, d);
  col = mix(col, vec3(0.92, 0.98, 1.0), max(foam * 0.7, rim * 0.55));
  float a = smoothstep(0.035, 0.15, d) * mix(0.88, 0.98, fres);
  if (vBay > 0.5) {
    col = mix(vec3(0.05, 0.09, 0.11), sky, 0.2 + 0.6 * fres) + spec * 0.45;
    float wc = smoothstep(0.72, 0.95, noise(vPos.xy * 0.12 + uWindD * uTime * 2.5) * noise(vPos.xy * 0.031 - uTime * 0.2) * 1.6) * (0.25 + uGust);
    col += wc * 0.45;
    col = mix(col, vec3(0.85, 0.9, 0.92), vShore * (0.35 + 0.4 * sin(uTime * 2.2 + vPos.x * 0.02 + vPos.y * 0.03)) * (0.4 + uGust));
    float e = min(min(vEdge.x, vEdge.y), min(1.0 - vEdge.x, 1.0 - vEdge.y));
    a = 0.62 * smoothstep(0.0, 0.03, e);
  }
  gl_FragColor = vec4(col, a);
}`;

export class Water {
  mesh: THREE.Mesh; mat: THREE.ShaderMaterial; texA: THREE.DataTexture; texB: THREE.DataTexture; terr: THREE.DataTexture;
  bufA: Float32Array; bufB: Float32Array; curF = -1; nx: number; ny: number;
  constructor(public run: Run, terrTex: THREE.DataTexture, geo: THREE.BufferGeometry) {
    this.nx = run.nx; this.ny = run.ny;
    this.bufA = new Float32Array(this.nx * this.ny); this.bufB = new Float32Array(this.nx * this.ny);
    const mk = (b: Float32Array) => { const t = new THREE.DataTexture(b, this.nx, this.ny, THREE.RedFormat, THREE.FloatType); t.needsUpdate = true; return t; };
    this.texA = mk(this.bufA); this.texB = mk(this.bufB); this.terr = terrTex;
    this.mat = new THREE.ShaderMaterial({
      vertexShader: waterVS, fragmentShader: waterFS, transparent: true, depthWrite: false,
      uniforms: {
        uDepthA: { value: this.texA }, uDepthB: { value: this.texB }, uTerr: { value: terrTex }, uFrac: { value: 0 },
        uTide: { value: 1 }, uTime: shared.time, uLightning: shared.lightning, uRain: shared.rain, uGust: shared.gust,
        uGrid: { value: new THREE.Vector2(this.nx, this.ny) }, uCam: shared.camPos, uMode: { value: 1 }, uWindD: windD,
      },
    });
    this.mesh = new THREE.Mesh(geo, this.mat); this.mesh.frustumCulled = false; this.mesh.renderOrder = 2;
  }
  private tmp: Float32Array | null = null;
  private fill(buf: Float32Array, f: number) {
    const nx = this.nx, ny = this.ny, n = nx * ny, o = Math.max(0, Math.min(this.run.nFrames - 1, f)) * n, d = this.run.depth;
    const t = this.tmp || (this.tmp = new Float32Array(n));
    for (let i = 0; i < n; i++) t[i] = d[o + i] * 0.001;
    // two cross-blur passes: removes solver checkerboarding and 8 m stair-steps so streets read as continuous water
    for (let pass = 0; pass < 2; pass++) {
      const src = pass === 0 ? t : buf, dst = pass === 0 ? buf : t;
      for (let iy = 0; iy < ny; iy++) for (let ix = 0; ix < nx; ix++) {
        const i = iy * nx + ix; const c = src[i];
        const l = ix > 0 ? src[i - 1] : c, r = ix < nx - 1 ? src[i + 1] : c, dn = iy > 0 ? src[i - nx] : c, up = iy < ny - 1 ? src[i + nx] : c;
        dst[i] = c * 0.4 + (l + r + dn + up) * 0.15;
      }
    }
    buf.set(t);
  }
  setFrame(fi: number, tide: number) {
    const f = Math.floor(fi);
    if (f !== this.curF) {
      if (f === this.curF + 1) { const t = this.bufA; this.bufA = this.bufB; this.bufB = t; const tt = this.texA; this.texA = this.texB; this.texB = tt; this.fill(this.bufB, f + 1); (this.texB.image as any).data = this.bufB; this.texB.needsUpdate = true; }
      else { this.fill(this.bufA, f); this.fill(this.bufB, f + 1); this.texA.needsUpdate = true; this.texB.needsUpdate = true; }
      this.mat.uniforms.uDepthA.value = this.texA; this.mat.uniforms.uDepthB.value = this.texB;
      this.curF = f;
    }
    this.mat.uniforms.uFrac.value = fi - f;
    this.mat.uniforms.uTide.value = tide;
  }
}

export function makeWaterGeometry(frame: LocalFrame, run: Run, city: City) {
  const { nx, ny, dx } = run; const n = nx * ny;
  const pos = new Float32Array(n * 3), cell = new Float32Array(n * 2);
  const sameGrid = city.meta.nx === nx && city.meta.ny === ny;
  for (let iy = 0; iy < ny; iy++) for (let ix = 0; ix < nx; ix++) {
    const i = iy * nx + ix, x = (ix + 0.5) * dx, y = (iy + 0.5) * dx;
    const z = sameGrid ? city.terrain[i] : city.zAt(x, y);
    pos[i * 3] = x; pos[i * 3 + 1] = y; pos[i * 3 + 2] = frame.z(x, y, z); cell[i * 2] = ix; cell[i * 2 + 1] = iy;
  }
  const idx = new Uint32Array((nx - 1) * (ny - 1) * 6); let k = 0;
  for (let iy = 0; iy < ny - 1; iy++) for (let ix = 0; ix < nx - 1; ix++) {
    const a = iy * nx + ix, b = a + 1, c = a + nx, d = c + 1;
    idx[k++] = a; idx[k++] = b; idx[k++] = d; idx[k++] = a; idx[k++] = d; idx[k++] = c;
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3)); g.setAttribute('aCell', new THREE.BufferAttribute(cell, 2));
  g.setIndex(new THREE.BufferAttribute(idx, 1));
  // terrain + bay texture on the run grid
  const tb = new Float32Array(n * 2);
  for (let iy = 0; iy < ny; iy++) for (let ix = 0; ix < nx; ix++) {
    const i = iy * nx + ix; const x = (ix + 0.5) * dx, y = (iy + 0.5) * dx;
    tb[i * 2] = sameGrid ? city.terrain[i] : city.zAt(x, y);
    const bay = city.bay && sameGrid ? city.bay[i] : (tb[i * 2] <= -1.9 ? 1 : 0);
    tb[i * 2 + 1] = bay ? 1 : 0;
  }
  const tex = new THREE.DataTexture(tb, nx, ny, THREE.RGFormat, THREE.FloatType); tex.needsUpdate = true;
  return { geo: g, tex };
}

export function biasMat<T extends THREE.Material>(m: T): T {
  m.onBeforeCompile = (sh) => { sh.vertexShader = sh.vertexShader.replace('#include <project_vertex>', '#include <project_vertex>\n  gl_Position = projectionMatrix * biasMV(mvPosition);').replace('void main() {', BIAS_GLSL + '\nvoid main() {'); };
  return m;
}
// ---------------- trees ----------------
function treeGeoms() {
  const low = new THREE.CylinderGeometry(0.035, 0.05, 0.3, 5, 1); low.rotateX(Math.PI / 2); low.translate(0, 0, 0.15);
  const trunk = new THREE.CylinderGeometry(0.025, 0.035, 0.3, 5, 1); trunk.rotateX(Math.PI / 2); trunk.translate(0, 0, 0.45);
  const crown = new THREE.IcosahedronGeometry(0.5, 1); crown.scale(1, 1, 0.75 / 1.0); crown.translate(0, 0, 0.68);
  const cpos = crown.getAttribute('position') as THREE.BufferAttribute;
  for (let i = 0; i < cpos.count; i++) { const j = 0.85 + 0.3 * Math.abs(Math.sin(i * 12.9898) * 43758.5453 % 1); cpos.setXYZ(i, cpos.getX(i) * j, cpos.getY(i) * j, 0.68 + (cpos.getZ(i) - 0.68) * j); }
  const colorize = (g: THREE.BufferGeometry, c: THREE.Color) => { const n = g.getAttribute('position').count; const a = new Float32Array(n * 3); for (let i = 0; i < n; i++) { a[i * 3] = c.r; a[i * 3 + 1] = c.g; a[i * 3 + 2] = c.b; } g.setAttribute('color', new THREE.BufferAttribute(a, 3)); return g; };
  const bark = new THREE.Color(0.24, 0.17, 0.11);
  const lowG = colorize(low.toNonIndexed(), bark);
  const upper = mergeGeometries([colorize(trunk.toNonIndexed(), bark), colorize(crown.toNonIndexed(), new THREE.Color(1, 1, 1))])!;
  upper.computeVertexNormals();
  return { low: lowG, upper };
}

export class Trees {
  low: THREE.InstancedMesh; upper: THREE.InstancedMesh; group = new THREE.Group();
  failed: { i: number; t: number; mode: string; dir: number; last: number }[] = [];
  base: Float32Array; scale: Float32Array;
  private m = new THREE.Matrix4(); private q = new THREE.Quaternion(); private v = new THREE.Vector3(); private s = new THREE.Vector3();
  private m2 = new THREE.Matrix4(); private m3 = new THREE.Matrix4();
  constructor(frame: LocalFrame, city: City, run: Run | null, geoms = treeGeoms()) {
    const n = city.trees.length;
    const swayMat = (mat: THREE.MeshLambertMaterial) => {
      mat.onBeforeCompile = (sh) => {
        sh.uniforms.uTime = shared.time; sh.uniforms.uGust = shared.gust;
        sh.vertexShader = 'uniform float uTime; uniform float uGust;\n' + sh.vertexShader.replace('#include <begin_vertex>', `#include <begin_vertex>
          float sw = max(0.0, position.z - 0.3) * (0.03 + 0.22 * uGust) * sin(uTime * (1.5 + uGust * 2.0) + float(gl_InstanceID) * 0.7);
          transformed.x += sw; transformed.y += sw * 0.6;`).replace('#include <project_vertex>', '#include <project_vertex>\n  gl_Position = projectionMatrix * biasMV(mvPosition);').replace('void main() {', BIAS_GLSL + '\nvoid main() {');
      };
      return mat;
    };
    this.low = new THREE.InstancedMesh(geoms.low, biasMat(new THREE.MeshLambertMaterial({ vertexColors: true })), n);
    this.upper = new THREE.InstancedMesh(geoms.upper, swayMat(new THREE.MeshLambertMaterial({ vertexColors: true })), n);
    this.base = new Float32Array(n * 3); this.scale = new Float32Array(n * 2);
    const col = new THREE.Color();
    for (let i = 0; i < n; i++) {
      const t = city.trees[i]; const h = Math.max(3, Math.min(30, t.h || 8)); const cw = Math.max(2.2, Math.min(16, t.crown || h * 0.55));
      this.base[i * 3] = t.x; this.base[i * 3 + 1] = t.y; this.base[i * 3 + 2] = frame.ground(t.x, t.y);
      this.scale[i * 2] = cw; this.scale[i * 2 + 1] = h;
      this.setMatrix(i, 'none', 0, 0);
      const g = 0.75 + 0.5 * ((i * 0.618) % 1);
      col.setRGB(0.10 * g, 0.21 * g, 0.09 * g); this.upper.setColorAt(i, col);
      col.setRGB(1, 1, 1); this.low.setColorAt(i, col);
    }
    this.low.frustumCulled = false; this.upper.frustumCulled = false;
    this.group.add(this.low, this.upper);
    if (run) {
      const idIndex = new Map<number, number>(); city.trees.forEach((t, i) => idIndex.set(t.id, i));
      for (const tr of run.trees) if (tr.fail_t != null) { const i = idIndex.get(tr.id); if (i != null) this.failed.push({ i, t: tr.fail_t, mode: tr.mode || 'snap', dir: tr.fall_dir_deg || 0, last: -1 }); }
      this.failed.sort((a, b) => a.t - b.t);
      this.makeBurst();
    }
  }
  private qa = new THREE.Quaternion(); private qb = new THREE.Quaternion(); private sv = new THREE.Vector3();
  /** mode none|snap|uproot; angle = topple angle; off = flight offset (m); k = visual exaggeration; spin = tumble angle */
  setMatrix(i: number, mode: string, angle: number, dirDeg: number, off: number[] = [0, 0, 0], k = 1, spin = 0) {
    const bx = this.base[i * 3], by = this.base[i * 3 + 1], bz = this.base[i * 3 + 2], cw = this.scale[i * 2] * k, h = this.scale[i * 2 + 1] * k;
    const th = dirDeg * Math.PI / 180, ax = -Math.cos(th), ay = Math.sin(th);
    this.qa.setFromAxisAngle(this.v.set(ax, ay, 0), angle);
    if (spin) { this.qb.setFromAxisAngle(this.sv.set(Math.sin(th), Math.cos(th), 0.3).normalize(), spin); this.qa.premultiply(this.qb); }
    this.q.copy(this.qa); this.s.set(cw, cw, h);
    const piv = mode === 'snap' ? 0.3 * h : 0;
    this.m2.makeTranslation(0, 0, -piv); this.m3.makeRotationFromQuaternion(this.q);
    this.m.makeScale(cw, cw, h).premultiply(this.m2).premultiply(this.m3).premultiply(this.m2.makeTranslation(bx + off[0], by + off[1], bz + piv + off[2]));
    this.upper.setMatrixAt(i, this.m);
    if (mode === 'snap') { this.m.compose(this.v.set(bx, by, bz), new THREE.Quaternion(), this.sv.set(this.scale[i * 2], this.scale[i * 2], this.scale[i * 2 + 1])); this.low.setMatrixAt(i, this.m); }
    else this.low.setMatrixAt(i, this.m);
  }
  update(t: number) {
    // exaggerated so it reads from 300-800 m: topple (240 s sim), then tumble downwind (900 s sim), land lying, 1.8x size
    const FALL = 240, FLY = 900; let dirty = false; const col = new THREE.Color(); const w = windD.value; const off = [0, 0, 0];
    for (const f of this.failed) {
      const age = t - f.t; const p = Math.max(0, Math.min(1, age / FALL)); const pf = Math.max(0, Math.min(1, (age - FALL * 0.5) / FLY));
      const key = age < 0 ? -1 : age > FALL * 0.5 + FLY ? 2 : age;
      if (key === f.last) continue;
      if (age < 0) { this.setMatrix(f.i, 'none', 0, 0); }
      else {
        const fly = f.mode === 'uproot' || (f.i % 3 === 0); // a third of the snaps get carried off
        const k = 1 + 1.6 * Math.min(1, p * 2) - (fly ? 0 : 0.6 * p);
        const dist = fly ? 90 * (1 - Math.pow(1 - pf, 2)) * (1 + (f.i % 5) * 0.3) : 0;
        off[0] = w.x * dist; off[1] = w.y * dist; off[2] = fly ? Math.sin(pf * Math.PI) * 22 : 0;
        const dir = fly ? Math.atan2(w.x, w.y) * 180 / Math.PI : f.dir;
        this.setMatrix(f.i, f.mode === 'snap' ? 'snap' : 'uproot', 1.5 * p * p, dir, off, k, fly ? pf * Math.PI * 3 : 0);
      }
      if ((age >= 0) !== (f.last as number >= 0)) { if (age >= 0) col.setRGB(0.55, 0.36, 0.10); else { const g = 0.75 + 0.5 * ((f.i * 0.618) % 1); col.setRGB(0.10 * g, 0.21 * g, 0.09 * g); } this.upper.setColorAt(f.i, col); this.upper.instanceColor!.needsUpdate = true; }
      f.last = key; dirty = true;
    }
    if (dirty) { this.low.instanceMatrix.needsUpdate = true; this.upper.instanceMatrix.needsUpdate = true; }
    if (this.burst) (this.burst.material as THREE.ShaderMaterial).uniforms.uT.value = t;
  }
  burst: THREE.Points | null = null;
  /** leaf/branch bursts streaming downwind from every failing tree */
  makeBurst() {
    const per = 6; const n = this.failed.length * per; if (!n) return;
    const st = new Float32Array(n * 3), t0 = new Float32Array(n), rr = new Float32Array(n * 3);
    let k = 0;
    for (const f of this.failed) for (let j = 0; j < per; j++, k++) {
      const i = f.i; st[k * 3] = this.base[i * 3]; st[k * 3 + 1] = this.base[i * 3 + 1]; st[k * 3 + 2] = this.base[i * 3 + 2] + this.scale[i * 2 + 1] * 0.7;
      t0[k] = f.t + Math.random() * 200; rr[k * 3] = Math.random(); rr[k * 3 + 1] = Math.random(); rr[k * 3 + 2] = Math.random();
    }
    const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.BufferAttribute(st, 3)); g.setAttribute('aT0', new THREE.BufferAttribute(t0, 1)); g.setAttribute('aR', new THREE.BufferAttribute(rr, 3));
    const m = new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, uniforms: { uT: { value: -1e9 }, uWind: windD, uGust: shared.gust },
      vertexShader: BIAS_GLSL + `
        attribute float aT0; attribute vec3 aR; uniform float uT, uGust; uniform vec2 uWind; varying float vA; varying vec3 vC;
        void main(){ float age = (uT - aT0) / 1400.0; vA = (age > 0.0 && age < 1.0) ? (1.0 - age) : 0.0;
          vec3 p = position; float d = age * (80.0 + 160.0 * aR.x) * (0.6 + uGust);
          vec2 perp = vec2(-uWind.y, uWind.x);
          p.xy += uWind * d + perp * (aR.y - 0.5) * 40.0 * age; p.z += sin(age * 3.14159) * (10.0 + 25.0 * aR.z) - age * age * 12.0;
          vC = mix(vec3(0.35, 0.75, 0.25), vec3(0.75, 0.55, 0.25), aR.z);
          vec4 mv = modelViewMatrix * vec4(p, 1.0); gl_PointSize = vA > 0.0 ? clamp(2600.0 / -mv.z, 2.0, 7.0) : 0.0; gl_Position = projectionMatrix * biasMV(mv); }`,
      fragmentShader: 'varying float vA; varying vec3 vC; void main(){ if (vA <= 0.0) discard; vec2 d = gl_PointCoord - 0.5; if (abs(d.x) + abs(d.y) > 0.5) discard; gl_FragColor = vec4(vC * 1.2, vA); }',
    });
    this.burst = new THREE.Points(g, m); this.burst.frustumCulled = false; this.burst.renderOrder = 6; this.group.add(this.burst);
  }
  countFailed(t: number) { let n = 0; for (const f of this.failed) if (f.t <= t) n++; return n; }
}

// ---------------- cars ----------------
function carGeom() {
  const body = new THREE.BoxGeometry(1.8, 4.4, 0.8); body.translate(0, 0, 0.55);
  const cab = new THREE.BoxGeometry(1.6, 2.2, 0.6); cab.translate(0, -0.2, 1.25);
  const g = mergeGeometries([body.toNonIndexed(), cab.toNonIndexed()])!; return g;
}
export class Cars {
  mesh: THREE.InstancedMesh; moving: number[] = []; private m = new THREE.Matrix4(); private q = new THREE.Quaternion(); private v = new THREE.Vector3(); private one = new THREE.Vector3(1, 1, 1); private z = new THREE.Vector3(0, 0, 1);
  degrees = false; lastF = -2;
  constructor(public frame: LocalFrame, public city: City, public run: Run | null) {
    const n = run?.nCars || city.cars.length;
    this.mesh = new THREE.InstancedMesh(carGeom(), biasMat(new THREE.MeshLambertMaterial()), n);
    const palette = [0x9aa3ad, 0x1c1f24, 0xe8e8e8, 0x7a1d1d, 0x23395b, 0x5d6168, 0xb9b2a2, 0x3b4d3b];
    const col = new THREE.Color();
    for (let i = 0; i < n; i++) { col.setHex(palette[(i * 7919) % palette.length]); this.mesh.setColorAt(i, col); }
    this.mesh.frustumCulled = false;
    if (run?.cars) {
      const c = run.cars, nf = run.nFrames; let maxYaw = 0;
      for (let i = 0; i < n; i++) {
        const x0 = c[i * 4], y0 = c[i * 4 + 1]; maxYaw = Math.max(maxYaw, Math.abs(c[i * 4 + 3]));
        const o = ((nf - 1) * n + i) * 4; const mid = ((Math.floor(nf / 2)) * n + i) * 4;
        if (Math.abs(c[o] - x0) > 0.3 || Math.abs(c[o + 1] - y0) > 0.3 || Math.abs(c[mid + 2] - c[i * 4 + 2]) > 0.2) this.moving.push(i);
      }
      this.degrees = maxYaw > 7;
      this.place(0, true);
    } else {
      city.cars.forEach((cc, i) => this.set(i, cc.x, cc.y, frame.ground(cc.x, cc.y), cc.heading * Math.PI / 180));
    }
  }
  set(i: number, x: number, y: number, z: number, yaw: number) {
    this.q.setFromAxisAngle(this.z, -yaw); this.m.compose(this.v.set(x, y, z), this.q, this.one); this.mesh.setMatrixAt(i, this.m);
  }
  place(fi: number, all = false) {
    const run = this.run!; const c = run.cars!; const n = run.nCars; const f = Math.max(0, Math.min(run.nFrames - 1, Math.floor(fi))); const f2 = Math.min(run.nFrames - 1, f + 1); const a = fi - f;
    const list = all ? Array.from({ length: n }, (_, i) => i) : this.moving;
    const k = this.degrees ? Math.PI / 180 : 1;
    for (const i of list) {
      const o = (f * n + i) * 4, p = (f2 * n + i) * 4;
      const x = c[o] + (c[p] - c[o]) * a, y = c[o + 1] + (c[p + 1] - c[o + 1]) * a;
      const xx = x > 1e5 ? x - this.city.meta.x0 : x, yy = y > 1e5 ? y - this.city.meta.y0 : y;
      let z = c[o + 2] + (c[p + 2] - c[o + 2]) * a; const g = this.city.zAt(xx, yy); if (!(z > g - 1)) z = g;
      this.set(i, xx, yy, this.frame.z(xx, yy, z), (c[o + 3] + (c[p + 3] - c[o + 3]) * a) * k);
    }
    this.mesh.instanceMatrix.needsUpdate = true;
  }
  update(fi: number) { if (this.run?.cars && this.moving.length && fi !== this.lastF) { this.place(fi); this.lastF = fi; } }
}

// ---------------- debris ----------------
export class Debris {
  mesh: THREE.InstancedMesh | null = null; private m = new THREE.Matrix4(); private q = new THREE.Quaternion(); private e = new THREE.Euler(); private v = new THREE.Vector3(); private s = new THREE.Vector3();
  constructor(public frame: LocalFrame, public city: City, public run: Run) {
    if (!run.debris || !run.nDebris) return;
    const g = new THREE.BoxGeometry(1, 1, 0.08);
    this.mesh = new THREE.InstancedMesh(g, new THREE.MeshBasicMaterial({ toneMapped: false }), run.nDebris);
    const items = run.debrisMeta?.items || [];
    const col = new THREE.Color();
    for (let i = 0; i < run.nDebris; i++) {
      const k = items[i]?.kind || 'glass';
      col.setHex(k === 'glass' ? 0xbfefff : k === 'branch' ? 0x6b4a2a : 0xffe2a0); this.mesh.setColorAt(i, col);
    }
    this.mesh.frustumCulled = false;
  }
  update(fi: number, time: number) {
    if (!this.mesh) return; const run = this.run, d = run.debris!, n = run.nDebris;
    const f = Math.max(0, Math.min(run.nFrames - 1, Math.floor(fi))), f2 = Math.min(run.nFrames - 1, f + 1), a = fi - f;
    const items = run.debrisMeta?.items || [];
    for (let i = 0; i < n; i++) {
      const o = (f * n + i) * 3, p = (f2 * n + i) * 3;
      let x = d[o] + (d[p] - d[o]) * a, y = d[o + 1] + (d[p + 1] - d[o + 1]) * a; const z = d[o + 2] + (d[p + 2] - d[o + 2]) * a;
      if (x > 1e5) { x -= this.city.meta.x0; y -= this.city.meta.y0; }
      const moving = Math.abs(d[p] - d[o]) + Math.abs(d[p + 1] - d[o + 1]) > 0.05;
      const sz = (items[i]?.size_m || 1.2) * 1.6;
      this.e.set(moving ? time * (2 + i % 5) : 1.3, moving ? time * (1.5 + i % 3) : 0, i);
      this.q.setFromEuler(this.e);
      this.m.compose(this.v.set(x, y, this.frame.z(x, y, z)), this.q, this.s.set(sz, sz * (items[i]?.kind === 'branch' ? 0.25 : 0.7), sz));
      this.mesh.setMatrixAt(i, this.m);
    }
    this.mesh.instanceMatrix.needsUpdate = true;
  }
}

// ---------------- wind streaklines ----------------
const K = 10;
export class Wind {
  lines: THREE.LineSegments; n: number; px: Float32Array; py: Float32Array; ph: Float32Array; age: Float32Array; life: Float32Array;
  trail: Float32Array; pos: Float32Array; alpha: Float32Array; head = 0;
  constructor(public frame: LocalFrame, public city: City, public field: Float32Array, public nx: number, public ny: number, public dx: number, n = 3500) {
    this.n = n; this.px = new Float32Array(n); this.py = new Float32Array(n); this.ph = new Float32Array(n); this.age = new Float32Array(n); this.life = new Float32Array(n);
    this.trail = new Float32Array(n * K * 3); this.pos = new Float32Array(n * (K - 1) * 2 * 3); this.alpha = new Float32Array(n * (K - 1) * 2);
    for (let i = 0; i < n; i++) this.spawn(i, true);
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(this.pos, 3).setUsage(THREE.DynamicDrawUsage));
    g.setAttribute('aA', new THREE.BufferAttribute(this.alpha, 1).setUsage(THREE.DynamicDrawUsage));
    const mat = new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      uniforms: { uGust: shared.gust, uLightning: shared.lightning },
      vertexShader: 'attribute float aA; varying float vA; void main(){ vA = aA; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }',
      fragmentShader: 'uniform float uGust, uLightning; varying float vA; void main(){ gl_FragColor = vec4(vec3(0.55,0.85,1.0) * vA * (0.10 + 0.25*uGust + 0.5*uLightning) * smoothstep(0.12, 0.45, uGust), 1.0); }',
    });
    this.lines = new THREE.LineSegments(g, mat); this.lines.frustumCulled = false; this.lines.renderOrder = 3;
  }
  sample(x: number, y: number): [number, number] {
    const ix = Math.floor(x / this.dx), iy = Math.floor(y / this.dx);
    if (ix < 0 || iy < 0 || ix >= this.nx || iy >= this.ny) return [0, 0];
    const i = (iy * this.nx + ix) * 2; const u = this.field[i], v = this.field[i + 1];
    return [isFinite(u) ? u : 0, isFinite(v) ? v : 0];
  }
  spawn(i: number, init = false) {
    const W = this.nx * this.dx, H = this.ny * this.dx;
    for (let tries = 0; tries < 8; tries++) {
      this.px[i] = Math.random() * W; this.py[i] = Math.random() * H;
      const [u, v] = this.sample(this.px[i], this.py[i]); if (u * u + v * v > 4) break;
    }
    this.ph[i] = 8 + Math.random() * Math.random() * 70; this.age[i] = init ? Math.random() * 4 : 0; this.life[i] = 2.5 + Math.random() * 3;
    const z = this.frame.ground(this.px[i], this.py[i]) + this.ph[i];
    for (let k = 0; k < K; k++) { const o = (i * K + k) * 3; this.trail[o] = this.px[i]; this.trail[o + 1] = this.py[i]; this.trail[o + 2] = z; }
  }
  update(dt: number, gust: number, uref: number) {
    const sc = Math.max(0.2, gust / uref) * 2.2; dt = Math.min(dt, 0.05);
    const pos = this.pos, al = this.alpha, tr = this.trail;
    this.head = (this.head + 1) % K;
    for (let i = 0; i < this.n; i++) {
      const [u, v] = this.sample(this.px[i], this.py[i]); const sp = Math.hypot(u, v);
      this.px[i] += u * sc * dt * 6; this.py[i] += v * sc * dt * 6; this.age[i] += dt;
      if (this.age[i] > this.life[i] || sp < 0.3) { this.spawn(i); }
      const o = (i * K + this.head) * 3; tr[o] = this.px[i]; tr[o + 1] = this.py[i]; tr[o + 2] = this.frame.ground(this.px[i], this.py[i]) + this.ph[i];
      const fade = Math.min(1, this.age[i] * 2, (this.life[i] - this.age[i]) * 2) * Math.min(1, sp / 8);
      for (let k = 0; k < K - 1; k++) {
        const a = (this.head - k + K) % K, b = (this.head - k - 1 + K) % K;
        const oa = (i * K + a) * 3, ob = (i * K + b) * 3, w = ((i * (K - 1) + k) * 2);
        pos[w * 3] = tr[oa]; pos[w * 3 + 1] = tr[oa + 1]; pos[w * 3 + 2] = tr[oa + 2];
        pos[w * 3 + 3] = tr[ob]; pos[w * 3 + 4] = tr[ob + 1]; pos[w * 3 + 5] = tr[ob + 2];
        const f0 = (1 - k / (K - 1)) * fade, f1 = (1 - (k + 1) / (K - 1)) * fade;
        al[w] = f0; al[w + 1] = f1;
      }
    }
    (this.lines.geometry.getAttribute('position') as THREE.BufferAttribute).needsUpdate = true;
    (this.lines.geometry.getAttribute('aA') as THREE.BufferAttribute).needsUpdate = true;
  }
}

// ---------------- damage shells + damage timeline ----------------
const shellVS = `${BIAS_GLSL}
attribute float aB; attribute float aH; uniform sampler2D uDmg; varying float vV; varying float vH; varying vec3 vN; varying vec3 vP;
void main(){ ivec2 c = ivec2(int(mod(aB, 1024.0)), int(aB / 1024.0)); vV = texelFetch(uDmg, c, 0).r; vH = aH; vN = normalize(normalMatrix * normal);
  vec4 mv = modelViewMatrix * vec4(position, 1.0); vP = mv.xyz; gl_Position = projectionMatrix * biasMV(mv); }`;
const shellFS = `uniform float uTime; varying float vV; varying float vH; varying vec3 vN; varying vec3 vP;
void main(){ if (vV < 0.01) discard; float rim = pow(1.0 - abs(dot(normalize(-vP), vN)), 2.0);
  vec3 c = mix(vec3(1.0, 0.45, 0.12), vec3(0.95, 0.22, 0.06), clamp(vV, 0.0, 1.0));
  float pulse = 0.85 + 0.15 * sin(uTime * 2.0 + vP.x * 0.01);
  float band = exp(-vH / 2.5);
  float a = clamp(vV, 0.0, 1.0) * (0.32 * band) * pulse;
  gl_FragColor = vec4(c * a, a); }`;

export class DamageView {
  mesh: THREE.Mesh | null = null; tex: THREE.DataTexture; buf: Float32Array; ids: number[] = []; usd: Float32Array; curve: Float32Array; nF: number;
  nonFlood = 0; flood = 0; total = 0;
  constructor(public frame: LocalFrame, public city: City, public run: Run) {
    const by = run.damage?.by_building || {};
    const bIndex = new Map<number, number>(); city.buildings.forEach((b, i) => bIndex.set(b.id, i));
    const items: [number, number][] = [];
    for (const k in by) { const v = +by[k]; const bi = bIndex.get(+k); if (bi != null && v > 0) items.push([bi, v]); }
    items.sort((a, b) => b[1] - a[1]);
    const top = items.slice(0, 6000);
    this.ids = top.map((x) => x[0]); this.usd = Float32Array.from(top.map((x) => x[1]));
    this.flood = items.reduce((s, x) => s + x[1], 0);
    this.total = +(run.damage?.total_usd ?? this.flood); this.nonFlood = Math.max(0, this.total - this.flood);
    this.nF = run.nFrames;
    // per-building time curve: running max of depth near the footprint, normalised
    const n = this.ids.length; this.curve = new Float32Array(n * this.nF);
    const { nx, ny, dx } = run, N = nx * ny;
    const cells: number[][] = this.ids.map((bi) => {
      const b = city.buildings[bi]; const xs = b.fp.map((p) => p[0]), ys = b.fp.map((p) => p[1]);
      const out: number[] = []; const x0 = Math.floor(Math.min(...xs) / dx) - 1, x1 = Math.floor(Math.max(...xs) / dx) + 1, y0 = Math.floor(Math.min(...ys) / dx) - 1, y1 = Math.floor(Math.max(...ys) / dx) + 1;
      for (let iy = y0; iy <= y1; iy += Math.max(1, Math.floor((y1 - y0) / 4))) for (let ix = x0; ix <= x1; ix += Math.max(1, Math.floor((x1 - x0) / 4))) if (ix >= 0 && iy >= 0 && ix < nx && iy < ny) out.push(iy * nx + ix);
      return out;
    });
    for (let j = 0; j < n; j++) {
      let m = 0; const cs = cells[j];
      for (let f = 0; f < this.nF; f++) { const o = f * N; for (const c of cs) { const d = run.depth[o + c]; if (d > m) m = d; } this.curve[j * this.nF + f] = m; }
      const mx = this.curve[j * this.nF + this.nF - 1] || 1;
      for (let f = 0; f < this.nF; f++) this.curve[j * this.nF + f] = mx > 0 ? this.curve[j * this.nF + f] / mx : f / this.nF;
    }
    const rows = Math.max(1, Math.ceil(n / 1024));
    this.buf = new Float32Array(1024 * rows);
    this.tex = new THREE.DataTexture(this.buf, 1024, rows, THREE.RedFormat, THREE.FloatType); this.tex.needsUpdate = true;
    if (!n) return;
    // geometry: extruded footprints of damaged buildings
    const pos: number[] = [], nor: number[] = [], aB: number[] = [], aH: number[] = [];
    for (let j = 0; j < n; j++) {
      const b = city.buildings[this.ids[j]]; const fp = b.fp; if (fp.length < 3) continue;
      let cx = 0, cy = 0; for (const p of fp) { cx += p[0]; cy += p[1]; } cx /= fp.length; cy /= fp.length;
      const g0 = frame.z(cx, cy, b.z) - 0.5, h = b.h + 1.5; const s = 1.04;
      for (let k = 0; k < fp.length; k++) {
        const p = fp[k], q = fp[(k + 1) % fp.length];
        const ax = cx + (p[0] - cx) * s, ay = cy + (p[1] - cy) * s, bx = cx + (q[0] - cx) * s, by2 = cy + (q[1] - cy) * s;
        const nxv = by2 - ay, nyv = -(bx - ax); const l = Math.hypot(nxv, nyv) || 1;
        const quad = [[ax, ay, g0, 0], [bx, by2, g0, 0], [bx, by2, g0 + h, h], [ax, ay, g0, 0], [bx, by2, g0 + h, h], [ax, ay, g0 + h, h]];
        for (const v of quad) { pos.push(v[0], v[1], v[2]); nor.push(nxv / l, nyv / l, 0); aB.push(j); aH.push(v[3]); }
      }
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3)); g.setAttribute('normal', new THREE.Float32BufferAttribute(nor, 3));
    g.setAttribute('aB', new THREE.Float32BufferAttribute(aB, 1)); g.setAttribute('aH', new THREE.Float32BufferAttribute(aH, 1));
    const mat = new THREE.ShaderMaterial({ vertexShader: shellVS, fragmentShader: shellFS, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide, uniforms: { uDmg: { value: this.tex }, uTime: shared.time } });
    this.mesh = new THREE.Mesh(g, mat); this.mesh.frustumCulled = false; this.mesh.renderOrder = 4;
  }
  lastF = -1;
  update(fi: number) {
    if (fi === this.lastF) return; this.lastF = fi;
    const f = Math.max(0, Math.min(this.nF - 1, Math.floor(fi))), f2 = Math.min(this.nF - 1, f + 1), a = fi - f;
    const n = this.ids.length; const maxU = this.usd[0] || 1;
    for (let j = 0; j < n; j++) {
      const c = this.curve[j * this.nF + f] * (1 - a) + this.curve[j * this.nF + f2] * a;
      const sev = 0.35 + 0.65 * Math.min(1, Math.log10(1 + this.usd[j]) / Math.log10(1 + maxU));
      this.buf[j] = c * sev;
    }
    this.tex.needsUpdate = true;
  }
  fillOutage(out: Float32Array, fi: number) {
    const f = Math.max(0, Math.min(this.nF - 1, Math.floor(fi))), f2 = Math.min(this.nF - 1, f + 1), a = fi - f;
    for (let j = 0; j < this.ids.length; j++) out[this.ids[j]] = this.curve[j * this.nF + f] * (1 - a) + this.curve[j * this.nF + f2] * a;
  }
  /** $ damage accumulated by sim time fraction fi (frames) */
  damageAt(fi: number, treeFrac: number) {
    const f = Math.max(0, Math.min(this.nF - 1, Math.floor(fi))), f2 = Math.min(this.nF - 1, f + 1), a = fi - f;
    let s = 0; const n = this.ids.length;
    for (let j = 0; j < n; j++) s += this.usd[j] * (this.curve[j * this.nF + f] * (1 - a) + this.curve[j * this.nF + f2] * a);
    const shownFlood = this.usd.reduce((x, y) => x + y, 0) || 1;
    return s * (this.flood / shownFlood) + this.nonFlood * treeFrac;
  }
}

// ---------------- people ----------------
const STATE_COLORS = [[0.75, 0.78, 0.82], [1.0, 0.9, 0.4], [0.3, 1.0, 0.45], [1.0, 0.18, 0.15], [0.3, 1.0, 0.45], [1.0, 0.18, 0.15]]; // home, moving, sheltered, at-risk, rescued(safe), stranded(at risk)
const TRAIL = 1;
/** People as bright dots with comet trails (state colours); shelter occupancy counts for labels. */
export class People {
  points: THREE.Points | null = null; pos: Float32Array; col: Float32Array; lastF = -1; n: number;
  constructor(public frame: LocalFrame, public city: City, public run: Run) {
    const n = (this.n = run.nPeople); this.pos = new Float32Array(n * TRAIL * 3); this.col = new Float32Array(n * TRAIL * 4);
    if (!run.people || !n) return;
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(this.pos, 3).setUsage(THREE.DynamicDrawUsage));
    g.setAttribute('aC', new THREE.BufferAttribute(this.col, 4).setUsage(THREE.DynamicDrawUsage));
    const mat = new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, blending: THREE.NormalBlending,
      vertexShader: BIAS_GLSL + '\nattribute vec4 aC; varying vec4 vC; void main(){ vC = aC; vec4 mv = modelViewMatrix * vec4(position,1.0); gl_PointSize = aC.a <= 0.0 ? 0.0 : clamp(5200.0 / -mv.z, 3.0, 12.0) * (0.45 + 0.55 * aC.a); gl_Position = projectionMatrix * biasMV(mv); }',
      fragmentShader: 'varying vec4 vC; void main(){ vec2 d = gl_PointCoord - 0.5; float r = length(d); if (r > 0.5) discard; float a = smoothstep(0.5, 0.15, r) * vC.a; gl_FragColor = vec4(vC.rgb * a * 1.5, a); }',
    });
    this.points = new THREE.Points(g, mat); this.points.frustumCulled = false; this.points.renderOrder = 9;
    this.update(0);
  }
  private sample(fi: number, i: number, out: number[]) {
    const run = this.run, p = run.people!, n = run.nPeople;
    fi = Math.max(0, Math.min(run.nFrames - 1, fi));
    const f = Math.floor(fi), f2 = Math.min(run.nFrames - 1, f + 1), a = fi - f;
    const o = (f * n + i) * 3, q = (f2 * n + i) * 3;
    let x = p[o] + (p[q] - p[o]) * a, y = p[o + 1] + (p[q + 1] - p[o + 1]) * a;
    if (x > 1e5) { x -= this.city.meta.x0; y -= this.city.meta.y0; }
    out[0] = x; out[1] = y; out[2] = Math.round(a < 0.5 ? p[o + 2] : p[q + 2]);
  }
  update(fi: number) {
    if (!this.points || fi === this.lastF) return; this.lastF = fi;
    const n = this.n, s = [0, 0, 0], h = [0, 0, 0];
    for (let i = 0; i < n; i++) {
      this.sample(fi, i, h);
      const c = STATE_COLORS[Math.max(0, Math.min(5, h[2]))];
      const home = h[2] === 0;
      if (i % 2 === 1) { this.col[i * TRAIL * 4 + 3] = 0; continue; } // draw every 2nd agent
      for (let j = 0; j < TRAIL; j++) {
        const k = i * TRAIL + j;
        if (j === 0) { s[0] = h[0]; s[1] = h[1]; } else this.sample(fi - j * 0.3, i, s);
        const moved = j > 0 && Math.hypot(s[0] - h[0], s[1] - h[1]) > 1.5;
        this.pos[k * 3] = s[0]; this.pos[k * 3 + 1] = s[1]; this.pos[k * 3 + 2] = this.frame.ground(s[0], s[1]) + 3;
        this.col[k * 4] = c[0]; this.col[k * 4 + 1] = c[1]; this.col[k * 4 + 2] = c[2];
        this.col[k * 4 + 3] = j === 0 ? (home ? 0.35 : 1.0) : moved ? (1 - j / TRAIL) * 0.7 : 0;
      }
    }
    this.points.geometry.getAttribute('position').needsUpdate = true; this.points.geometry.getAttribute('aC').needsUpdate = true;
  }
  /** number of people in state `sheltered` within r metres of (x, y) at frame fi */
  countNear(fi: number, x: number, y: number, r = 260) {
    if (!this.run.people) return 0; let c = 0; const h = [0, 0, 0];
    for (let i = 0; i < this.n; i++) { this.sample(fi, i, h); if (h[2] === 2 && Math.abs(h[0] - x) < r && Math.abs(h[1] - y) < r) c++; }
    return c;
  }
}

// ---------------- per-run visual bundle ----------------
export class RunVisual {
  group = new THREE.Group(); water: Water; trees: Trees; cars: Cars; debris: Debris; dmg: DamageView; people: People; plan: PlanVisual;
  constructor(public run: Run, frame: LocalFrame, city: City, waterGeo: { geo: THREE.BufferGeometry; tex: THREE.DataTexture }, treeG: ReturnType<typeof treeGeoms>) {
    this.water = new Water(run, waterGeo.tex, waterGeo.geo);
    this.trees = new Trees(frame, city, run, treeG);
    this.cars = new Cars(frame, city, run);
    this.debris = new Debris(frame, city, run);
    this.dmg = new DamageView(frame, city, run);
    this.people = new People(frame, city, run);
    this.plan = buildPlan(run.plan, frame, city);
    this.group.add(this.water.mesh, this.trees.group, this.cars.mesh, this.plan.group);
    if (this.debris.mesh) this.group.add(this.debris.mesh);
    if (this.people.points) this.group.add(this.people.points);
  }
  frameAt(t: number) {
    const ts = this.run.t; if (!ts?.length) return 0;
    if (t <= ts[0]) return 0; const n = ts.length; if (t >= ts[n - 1]) return n - 1;
    const dt = (ts[n - 1] - ts[0]) / (n - 1); let f = Math.floor((t - ts[0]) / dt); f = Math.max(0, Math.min(n - 2, f));
    return f + (t - ts[f]) / (ts[f + 1] - ts[f] || 1);
  }
  private static lastCloud = -1;
  update(t: number, dtReal: number, time: number) {
    if (time !== RunVisual.lastCloud) { RunVisual.lastCloud = time; shared.cloud.value += Math.min(0.1, dtReal) * (0.4 + 1.6 * shared.gust.value); }
    const fi = this.frameAt(t); const f = Math.floor(fi);
    const tide = this.run.tide ? this.run.tide[f] + ((this.run.tide[Math.min(this.run.nFrames - 1, f + 1)] - this.run.tide[f]) * (fi - f)) : 1.2;
    this.water.setFrame(fi, tide);
    this.trees.update(t); this.cars.update(fi); this.debris.update(fi, time); this.dmg.update(fi); this.people.update(fi);
    this.plan.update(t, time);
    if (this.people.points && time - this.occT > 0.5) { // live shelter occupancy in the label text
      this.occT = time;
      for (const l of this.plan.labels) if (l.kind.includes('shelter')) {
        const baseText = (l as any).baseText || ((l as any).baseText = l.text);
        const n = t >= l.t_s ? this.people.countNear(fi, l.pos.x, l.pos.y) : 0;
        l.text = n > 0 ? `${baseText} · ${n.toLocaleString('en-US')}${l.obj.capacity ? ' / ' + l.obj.capacity : ''} sheltered` : baseText;
      }
    }
  }
  occT = -1;
  damageAt(t: number) {
    const fi = this.frameAt(t); const nf = this.trees.failed.length;
    return this.dmg.damageAt(fi, nf ? this.trees.countFailed(t) / nf : fi / Math.max(1, this.run.nFrames - 1));
  }
}

export { treeGeoms };
