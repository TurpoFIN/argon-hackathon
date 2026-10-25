// Storm atmosphere: cloud decks, rain curtains, bay/rooftop spray, city window lights with spreading outages,
// camera shake and synthesized rain/wind/thunder audio. All in the tile-local frame (z = elevation).
import * as THREE from 'three';
import type { City } from './data';
import type { LocalFrame } from './storm';
import { shared, windD } from './storm';
import { BIAS_GLSL } from './bias';

const NOISE = `
float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1,311.7)))*43758.5453); }
float noise(vec2 p){ vec2 i=floor(p), f=fract(p); f=f*f*(3.0-2.0*f); return mix(mix(hash(i),hash(i+vec2(1,0)),f.x), mix(hash(i+vec2(0,1)),hash(i+vec2(1,1)),f.x), f.y); }
float fbm(vec2 p){ float v=0.0, a=0.5; for(int i=0;i<5;i++){ v+=a*noise(p); p=p*2.03+vec2(1.7,9.2); a*=0.5; } return v; }`;

export const windDir = windD; // direction the wind blows TO (local x,y)

export function cloudDeck(frame: LocalFrame, z: number, size: number, opts: { alpha: number; speed: number; scale: number; dark: number }) {
  const g = new THREE.PlaneGeometry(size, size, 1, 1); g.translate(frame.cx, frame.cy, z);
  const m = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, side: THREE.DoubleSide,
    uniforms: { uTime: shared.time, uCloud: shared.cloud, uL: shared.lightning, uGust: shared.gust, uRain: shared.rain, uWind: windDir, uC: { value: new THREE.Vector2(frame.cx, frame.cy) }, uSize: { value: size }, uBolt: { value: new THREE.Vector2(frame.cx, frame.cy) } },
    vertexShader: 'varying vec2 vP; void main(){ vP = position.xy; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }',
    fragmentShader: `uniform float uTime, uCloud, uL, uGust, uRain, uSize; uniform vec2 uWind, uC, uBolt; varying vec2 vP; ${NOISE}
      void main(){
        vec2 p = (vP - uC) / ${opts.scale.toFixed(1)};
        vec2 drift = uWind * uCloud * ${opts.speed.toFixed(3)};
        vec2 q = p - drift; vec2 w = vec2(fbm(q * 0.7 + uTime * 0.01), fbm(q * 0.7 + 5.2));
        float c = fbm(q + 1.8 * w);
        float cover = smoothstep(0.32 - 0.12 * uRain, 0.72, c);
        float r = length(vP - uC) / (uSize * 0.5); float edge = 1.0 - smoothstep(0.55, 1.0, r);
        vec3 col = mix(vec3(0.20, 0.24, 0.22), vec3(0.08, 0.10, 0.10), c) * ${opts.dark.toFixed(2)};
        float lit = uL * exp(-length(vP - uBolt) / 2500.0) * (0.6 + c);
        col += lit * vec3(0.75, 0.78, 0.95);
        gl_FragColor = vec4(col, cover * edge * ${opts.alpha.toFixed(2)}); }`,
  });
  const mesh = new THREE.Mesh(g, m); mesh.frustumCulled = false; mesh.renderOrder = 15;
  return mesh;
}

/** Big translucent rain sheets that sweep across the city with the wind. */
export function rainCurtains(frame: LocalFrame, n = 16) {
  const W = frame.city.meta.nx * frame.city.meta.dx, H = frame.city.meta.ny * frame.city.meta.dx;
  const pos: number[] = [], uv: number[] = [], seed: number[] = [];
  for (let i = 0; i < n; i++) {
    const s = Math.random();
    const w = 900 + Math.random() * 900, h = 1300;
    const q = [[-w / 2, 0], [w / 2, 0], [w / 2, h], [-w / 2, 0], [w / 2, h], [-w / 2, h]];
    for (const [a, b] of q) { pos.push(a, 0, b); uv.push(a / w + 0.5, b / h); seed.push(i + s); }
  }
  const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3)); g.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2)); g.setAttribute('aS', new THREE.Float32BufferAttribute(seed, 1));
  const m = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, side: THREE.DoubleSide,
    uniforms: { uTime: shared.time, uRain: shared.rain, uGust: shared.gust, uL: shared.lightning, uWind: windDir, uBox: { value: new THREE.Vector4(-1500, -1500, W + 3000, H + 3000) }, uZ: { value: frame.ground(W / 2, H / 2) } },
    vertexShader: `attribute float aS; uniform float uTime, uGust, uZ; uniform vec2 uWind; uniform vec4 uBox; varying vec2 vUv; varying float vS;
      void main(){ vUv = uv; vS = aS; vec2 perp = vec2(-uWind.y, uWind.x);
        float h1 = fract(sin(aS * 91.7) * 4375.5), h2 = fract(sin(aS * 17.3) * 9137.1);
        float along = mod(h1 * (uBox.z + uBox.w) + uTime * (40.0 + 60.0 * uGust), uBox.z + uBox.w) - uBox.w * 0.3;
        vec2 c = uBox.xy + uWind * along + perp * (h2 - 0.5) * uBox.w * 1.2 + vec2(uBox.z, uBox.w) * 0.25;
        vec3 p = vec3(c + perp * position.x, uZ + position.z - 50.0);
        p.xy += uWind * position.z * 0.35; // slant with the wind
        gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0); }`,
    fragmentShader: `uniform float uTime, uRain, uL; varying vec2 vUv; varying float vS; ${NOISE}
      void main(){ float streak = fbm(vec2(vUv.x * 60.0 + vS * 7.0, vUv.y * 3.0 + uTime * 3.5));
        float body = fbm(vec2(vUv.x * 4.0 + vS, vUv.y * 1.5 + uTime * 0.4));
        float edge = smoothstep(0.0, 0.4, vUv.x) * smoothstep(1.0, 0.6, vUv.x) * pow(smoothstep(1.0, 0.2, vUv.y), 2.0) * smoothstep(0.0, 0.08, vUv.y) * smoothstep(0.25, 0.6, body);
        float a = (0.35 * streak + 0.65 * body) * edge * smoothstep(0.15, 0.7, uRain) * 0.5;
        gl_FragColor = vec4(vec3(0.62, 0.67, 0.70) + uL * 0.5, a); }`,
  });
  const mesh = new THREE.Mesh(g, m); mesh.frustumCulled = false; mesh.renderOrder = 16; return mesh;
}

/** Spray and mist streaming off the bay edge and rooftops, density scales with gust. */
export function spray(frame: LocalFrame, city: City, n = 5000) {
  const { nx, ny, dx } = city.meta; const seeds: number[] = [];
  const shore: number[] = [];
  if (city.bay) for (let iy = 1; iy < ny - 1; iy++) for (let ix = 1; ix < nx - 1; ix++) { const i = iy * nx + ix; if (city.bay[i] && (!city.bay[i - 1] || !city.bay[i + 1] || !city.bay[i - nx] || !city.bay[i + nx])) shore.push(ix, iy); }
  const tall = city.buildings.filter((b) => b.h > 35);
  for (let k = 0; k < n; k++) {
    let x: number, y: number, z: number;
    if (k % 3 !== 0 && shore.length) { const j = Math.floor(Math.random() * shore.length / 2) * 2; x = (shore[j] + Math.random()) * dx; y = (shore[j + 1] + Math.random()) * dx; z = frame.z(x, y, 0.8); }
    else if (tall.length) { const b = tall[Math.floor(Math.random() * tall.length)]; const p = b.fp[Math.floor(Math.random() * b.fp.length)]; x = p[0]; y = p[1]; z = frame.z(x, y, b.z + b.h); }
    else continue;
    seeds.push(x, y, z);
  }
  const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.Float32BufferAttribute(seeds, 3));
  const ph = new Float32Array(seeds.length / 3).map(() => Math.random()); g.setAttribute('aP', new THREE.BufferAttribute(ph, 1));
  const m = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    uniforms: { uTime: shared.time, uGust: shared.gust, uWind: windDir, uL: shared.lightning },
    vertexShader: `attribute float aP; uniform float uTime, uGust; uniform vec2 uWind; varying float vA;
      void main(){ float age = fract(uTime * (0.18 + 0.25 * uGust) + aP); vec3 p = position;
        p.xy += uWind * age * (60.0 + 220.0 * uGust); p.z += age * (8.0 + 30.0 * uGust) * (0.5 + aP);
        p.xy += vec2(sin(aP * 40.0 + uTime), cos(aP * 33.0 + uTime)) * 6.0 * age;
        vA = sin(age * 3.14159) * smoothstep(0.25, 0.8, uGust);
        vec4 mv = modelViewMatrix * vec4(p, 1.0); gl_PointSize = clamp((900.0 + 2500.0 * age) / -mv.z, 1.0, 60.0); gl_Position = projectionMatrix * mv; }`,
    fragmentShader: `uniform float uL; varying float vA; void main(){ float r = length(gl_PointCoord - 0.5); if (r > 0.5) discard; float a = smoothstep(0.5, 0.0, r) * vA * 0.07; gl_FragColor = vec4(vec3(0.75, 0.8, 0.85) * (a + uL * a), a); }`,
  });
  const pts = new THREE.Points(g, m); pts.frustumCulled = false; pts.renderOrder = 17; return pts;
}

/** Lit windows as points on facades; a per-building flood texture knocks power out as water arrives. */
export class WindowLights {
  points: THREE.Points; tex: THREE.DataTexture; buf: Float32Array;
  constructor(frame: LocalFrame, city: City, cap = 90000) {
    const P: number[] = [], B: number[] = [], R: number[] = [];
    const nb = city.buildings.length; const per = Math.max(2, Math.floor(cap / Math.max(1, nb)));
    city.buildings.forEach((b, bi) => {
      const fp = b.fp; if (fp.length < 3) return; let cx = 0, cy = 0; for (const p of fp) { cx += p[0]; cy += p[1]; } cx /= fp.length; cy /= fp.length;
      const floors = Math.max(1, Math.min(60, Math.floor(b.h / 3.4))); const g0 = frame.z(cx, cy, b.z);
      const count = Math.min(per * (b.h > 40 ? 6 : 1), floors * fp.length * 2);
      for (let k = 0; k < count; k++) {
        if (Math.random() > 0.55) continue;
        const e = Math.floor(Math.random() * fp.length); const p = fp[e], q = fp[(e + 1) % fp.length]; const t = Math.random();
        let x = p[0] + (q[0] - p[0]) * t, y = p[1] + (q[1] - p[1]) * t; const ox = x - cx, oy = y - cy, ol = Math.hypot(ox, oy) || 1; x += ox / ol * 0.6; y += oy / ol * 0.6;
        const f = Math.floor(Math.random() * floors); P.push(x, y, g0 + 2 + f * 3.4); B.push(bi); R.push(Math.random());
      }
    });
    const rows = Math.ceil(nb / 1024); this.buf = new Float32Array(1024 * rows);
    this.tex = new THREE.DataTexture(this.buf, 1024, rows, THREE.RedFormat, THREE.FloatType); this.tex.needsUpdate = true;
    const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.Float32BufferAttribute(P, 3)); g.setAttribute('aB', new THREE.Float32BufferAttribute(B, 1)); g.setAttribute('aR', new THREE.Float32BufferAttribute(R, 1));
    const m = new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      uniforms: { uTime: shared.time, uOut: { value: this.tex }, uGrid: { value: 0 }, uL: shared.lightning },
      vertexShader: BIAS_GLSL + `
attribute float aB; attribute float aR; uniform sampler2D uOut; uniform float uTime, uGrid; varying vec3 vC; varying float vA;
        void main(){ float fl = texelFetch(uOut, ivec2(int(mod(aB, 1024.0)), int(aB / 1024.0)), 0).r;
          float thr = 0.15 + 0.6 * aR; float off = step(thr, fl + uGrid * aR);
          float flick = (fl > thr - 0.15 && off < 0.5) ? step(0.35, fract(sin(floor(uTime * 14.0) * 12.9 + aR * 77.0) * 437.5)) : 1.0;
          vA = (1.0 - off) * flick * (0.55 + 0.45 * aR);
          vC = mix(vec3(1.0, 0.72, 0.38), vec3(0.75, 0.86, 1.0), step(0.72, aR));
          vec4 mv = modelViewMatrix * vec4(position, 1.0); gl_PointSize = clamp(2600.0 / -mv.z, 1.6, 5.0); gl_Position = projectionMatrix * biasMV(mv); }`,
      fragmentShader: `varying vec3 vC; varying float vA; void main(){ if (vA <= 0.01) discard; float r = length(gl_PointCoord - 0.5); gl_FragColor = vec4(vC * vA * 1.4 * smoothstep(0.5, 0.1, r), vA); }`,
    });
    this.points = new THREE.Points(g, m); this.points.frustumCulled = false; this.points.renderOrder = 5;
  }
}

/** Synthesized storm audio (no assets): rain hiss, gusting wind, thunder. Starts on first user gesture. */
export class StormAudio {
  ctx: AudioContext | null = null; rain!: GainNode; wind!: GainNode; windF!: BiquadFilterNode; master!: GainNode; noise!: AudioBuffer;
  start() {
    if (this.ctx) return; const ctx = new AudioContext(); this.ctx = ctx;
    const len = ctx.sampleRate * 3; this.noise = ctx.createBuffer(1, len, ctx.sampleRate); const d = this.noise.getChannelData(0);
    let b = 0; for (let i = 0; i < len; i++) { const w = Math.random() * 2 - 1; b = 0.97 * b + 0.03 * w; d[i] = w * 0.6 + b * 2.5; }
    this.master = ctx.createGain(); this.master.gain.value = 0.7; this.master.connect(ctx.destination);
    const src = (f: BiquadFilterNode) => { const s = ctx.createBufferSource(); s.buffer = this.noise; s.loop = true; s.loopStart = Math.random(); s.connect(f); s.start(0, Math.random() * 2); };
    const hp = ctx.createBiquadFilter(); hp.type = 'highpass'; hp.frequency.value = 900; this.rain = ctx.createGain(); this.rain.gain.value = 0; hp.connect(this.rain); this.rain.connect(this.master); src(hp);
    this.windF = ctx.createBiquadFilter(); this.windF.type = 'bandpass'; this.windF.frequency.value = 350; this.windF.Q.value = 0.9; this.wind = ctx.createGain(); this.wind.gain.value = 0; this.windF.connect(this.wind); this.wind.connect(this.master); src(this.windF);
  }
  set(rain: number, gust: number, time: number) {
    if (!this.ctx) return; const t = this.ctx.currentTime;
    this.rain.gain.setTargetAtTime(0.05 + rain * 0.5, t, 0.4);
    this.wind.gain.setTargetAtTime((0.05 + gust * 0.9) * (0.7 + 0.3 * Math.sin(time * 0.7)), t, 0.3);
    this.windF.frequency.setTargetAtTime(250 + gust * 500 + 80 * Math.sin(time * 1.3), t, 0.3);
  }
  thunder(delay: number) {
    const ctx = this.ctx; if (!ctx) return; const t = ctx.currentTime + delay;
    const s = ctx.createBufferSource(); s.buffer = this.noise; const lp = ctx.createBiquadFilter(); lp.type = 'lowpass'; lp.frequency.value = 160;
    const g = ctx.createGain(); g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(1.6, t + 0.08); g.gain.exponentialRampToValueAtTime(0.4, t + 0.7); g.gain.exponentialRampToValueAtTime(0.001, t + 3.2);
    s.connect(lp); lp.connect(g); g.connect(this.master); s.start(t, Math.random()); s.stop(t + 3.3);
  }
}

/** Storm sky dome around the camera (ENU frame). Depth-tested, so in photoreal mode it only replaces sky pixels
 *  and the distant horizon; the city in front stays untouched. */
export function skyDome() {
  const m = new THREE.ShaderMaterial({
    side: THREE.BackSide, depthWrite: false, transparent: false,
    uniforms: { uTime: shared.time, uCloud: shared.cloud, uL: shared.lightning, uRain: shared.rain, uWind: windD },
    vertexShader: 'varying vec3 vD; void main(){ vD = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }',
    fragmentShader: `uniform float uTime, uCloud, uL, uRain; uniform vec2 uWind; varying vec3 vD; ${NOISE}
      void main(){ float e = vD.z; vec2 p = vD.xy / (0.18 + max(e, 0.0)) * 1.6 - uWind * uCloud * 0.06;
        float c = fbm(p + fbm(p * 0.6 + uTime * 0.02)); float c2 = fbm(p * 2.7 - uWind * uCloud * 0.12);
        vec3 top = vec3(0.07, 0.09, 0.09), mid = vec3(0.19, 0.23, 0.21), haze = vec3(0.24, 0.28, 0.27);
        vec3 col = mix(mid, top, smoothstep(0.0, 0.5, e)) * (0.7 + 0.6 * c) + vec3(0.04) * c2;
        col = mix(haze * (0.85 + 0.3 * c), col, smoothstep(-0.02, 0.12, e));
        col += uL * vec3(0.55, 0.6, 0.8) * (0.3 + 1.2 * c * c) * smoothstep(-0.05, 0.3, e);
        gl_FragColor = vec4(col, 1.0); }`,
  });
  const mesh = new THREE.Mesh(new THREE.SphereGeometry(1, 48, 24), m); mesh.frustumCulled = false; mesh.renderOrder = -10;
  return mesh;
}
