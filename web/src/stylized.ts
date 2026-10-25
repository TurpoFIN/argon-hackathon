// Offline fallback renderer (?mode=stylized or when ArcGIS can't load): our own terrain + lidar buildings,
// night-storm look with lit windows, sky, fog and GPU rain. Same storm layer and camera shots as photoreal.
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import type { City } from './data';
import type { LocalFrame } from './storm';
import { shared } from './storm';

export interface Stylized {
  renderer: THREE.WebGLRenderer; camera: THREE.PerspectiveCamera; scene: THREE.Scene; controls: OrbitControls; world: THREE.Group;
  rain: THREE.LineSegments;
}

const FOG = new THREE.Color(0x1a222c);

export function createStylized(container: HTMLElement, city: City, frame: LocalFrame): Stylized {
  const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio)); renderer.setSize(container.clientWidth, container.clientHeight);
  renderer.outputColorSpace = THREE.SRGBColorSpace; renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.15;
  container.appendChild(renderer.domElement);
  const scene = new THREE.Scene(); scene.background = FOG.clone(); scene.fog = new THREE.FogExp2(FOG.getHex(), 0.00028);
  const camera = new THREE.PerspectiveCamera(42, container.clientWidth / container.clientHeight, 25, 60000); camera.up.set(0, 0, 1);
  const controls = new OrbitControls(camera, renderer.domElement); controls.enableDamping = true; controls.dampingFactor = 0.08; controls.maxPolarAngle = Math.PI * 0.47;
  const world = new THREE.Group(); world.matrixAutoUpdate = false; world.matrix.copy(frame.matrix); scene.add(world);
  scene.add(new THREE.HemisphereLight(0x8aa0b8, 0x20262c, 1.1));
  const dl = new THREE.DirectionalLight(0xb8c6d8, 0.9); dl.position.set(-0.4, -0.6, 1); scene.add(dl);

  // sky dome with moving storm clouds + lightning
  const sky = new THREE.Mesh(new THREE.SphereGeometry(30000, 32, 16), new THREE.ShaderMaterial({
    side: THREE.BackSide, depthWrite: false, fog: false, uniforms: { uTime: shared.time, uL: shared.lightning },
    vertexShader: 'varying vec3 vD; void main(){ vD = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }',
    fragmentShader: `uniform float uTime, uL; varying vec3 vD;
      float h(vec2 p){ return fract(sin(dot(p, vec2(127.1,311.7)))*43758.5453); }
      float n(vec2 p){ vec2 i=floor(p), f=fract(p); f=f*f*(3.0-2.0*f); return mix(mix(h(i),h(i+vec2(1,0)),f.x), mix(h(i+vec2(0,1)),h(i+vec2(1,1)),f.x), f.y); }
      float fbm(vec2 p){ float v=0.0, a=0.5; for(int i=0;i<5;i++){ v+=a*n(p); p*=2.1; a*=0.5;} return v; }
      void main(){ float e = clamp(vD.z, -0.1, 1.0); vec2 p = vD.xy / (0.25 + max(e, 0.02)) * 2.0 + vec2(uTime*0.03, uTime*0.05);
        float c = fbm(p); vec3 base = mix(vec3(0.10,0.13,0.17), vec3(0.03,0.04,0.06), smoothstep(0.0, 0.6, e));
        vec3 col = base + vec3(0.09,0.10,0.12) * c + uL * vec3(0.55,0.6,0.8) * (0.4 + c);
        col = mix(vec3(0.10,0.13,0.17), col, smoothstep(-0.05, 0.12, e)); gl_FragColor = vec4(col, 1.0); }`,
  }));
  scene.add(sky);

  // terrain (8 m) with hillshade, wet asphalt look; flood100 zone hint
  const { nx, ny, dx } = city.meta; const n = nx * ny;
  const pos = new Float32Array(n * 3), col = new Float32Array(n * 3);
  for (let iy = 0; iy < ny; iy++) for (let ix = 0; ix < nx; ix++) {
    const i = iy * nx + ix, x = (ix + 0.5) * dx, y = (iy + 0.5) * dx; const z = city.terrain[i];
    pos[i * 3] = x; pos[i * 3 + 1] = y; pos[i * 3 + 2] = frame.z(x, y, city.bay?.[i] ? Math.min(z, -3) : z);
    const zl = city.terrain[iy * nx + Math.max(0, ix - 1)], zr = city.terrain[iy * nx + Math.min(nx - 1, ix + 1)], zd = city.terrain[Math.max(0, iy - 1) * nx + ix], zu = city.terrain[Math.min(ny - 1, iy + 1) * nx + ix];
    const sx = (zr - zl) / (2 * dx), sy = (zu - zd) / (2 * dx); const shade = Math.max(0.25, Math.min(1.3, 0.85 - sx * 2.2 + sy * 1.6));
    const park = 0; void park;
    const base = city.bay?.[i] ? [0.03, 0.05, 0.07] : [0.17, 0.18, 0.19];
    col[i * 3] = base[0] * shade; col[i * 3 + 1] = base[1] * shade; col[i * 3 + 2] = base[2] * shade * 1.05;
  }
  const idx = new Uint32Array((nx - 1) * (ny - 1) * 6); let k = 0;
  for (let iy = 0; iy < ny - 1; iy++) for (let ix = 0; ix < nx - 1; ix++) { const a = iy * nx + ix, b = a + 1, c = a + nx, d = c + 1; idx[k++] = a; idx[k++] = b; idx[k++] = d; idx[k++] = a; idx[k++] = d; idx[k++] = c; }
  const tg = new THREE.BufferGeometry(); tg.setAttribute('position', new THREE.BufferAttribute(pos, 3)); tg.setAttribute('color', new THREE.BufferAttribute(col, 3)); tg.setIndex(new THREE.BufferAttribute(idx, 1)); tg.computeVertexNormals();
  const terrain = new THREE.Mesh(tg, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.55, metalness: 0.1 }));
  world.add(terrain);

  // buildings: merged walls + roofs, procedural lit windows
  const P: number[] = [], N: number[] = [], UV: number[] = [], ID: number[] = [];
  city.buildings.forEach((b, bi) => {
    let fp = b.fp; if (fp.length > 3 && fp[0][0] === fp[fp.length - 1][0] && fp[0][1] === fp[fp.length - 1][1]) fp = fp.slice(0, -1);
    if (fp.length < 3) return;
    let cx = 0, cy = 0; for (const p of fp) { cx += p[0]; cy += p[1]; } cx /= fp.length; cy /= fp.length;
    const z0 = frame.z(cx, cy, b.z) - 2, z1 = frame.z(cx, cy, b.z) + b.h; let u = 0;
    for (let i = 0; i < fp.length; i++) {
      const p = fp[i], q = fp[(i + 1) % fp.length]; const l = Math.hypot(q[0] - p[0], q[1] - p[1]); const nx2 = (q[1] - p[1]) / (l || 1), ny2 = -(q[0] - p[0]) / (l || 1);
      const v = [[p[0], p[1], z0, u, -2], [q[0], q[1], z0, u + l, -2], [q[0], q[1], z1, u + l, b.h], [p[0], p[1], z0, u, -2], [q[0], q[1], z1, u + l, b.h], [p[0], p[1], z1, u, b.h]];
      for (const w of v) { P.push(w[0], w[1], w[2]); N.push(nx2, ny2, 0); UV.push(w[3], w[4]); ID.push(bi); }
      u += l;
    }
    const shape = fp.map((p) => new THREE.Vector2(p[0], p[1]));
    try { for (const [a, bb, c] of THREE.ShapeUtils.triangulateShape(shape, [])) for (const s of [a, bb, c]) { P.push(shape[s].x, shape[s].y, z1); N.push(0, 0, 1); UV.push(-1, -1); ID.push(bi); } } catch { /* skip roof */ }
  });
  const bg = new THREE.BufferGeometry();
  bg.setAttribute('position', new THREE.Float32BufferAttribute(P, 3)); bg.setAttribute('normal', new THREE.Float32BufferAttribute(N, 3));
  bg.setAttribute('uv', new THREE.Float32BufferAttribute(UV, 2)); bg.setAttribute('aId', new THREE.Float32BufferAttribute(ID, 1));
  const bm = new THREE.ShaderMaterial({
    fog: true, uniforms: THREE.UniformsUtils.merge([THREE.UniformsLib.fog, { uL: { value: 0 } }]),
    vertexShader: `attribute float aId; varying vec2 vUv; varying vec3 vN; varying float vId; varying vec3 vW;
      #include <fog_pars_vertex>
      void main(){ vUv = uv; vN = normal; vId = aId; vec4 mvPosition = modelViewMatrix * vec4(position,1.0); vW = position; gl_Position = projectionMatrix * mvPosition;
      #include <fog_vertex>
      }`,
    fragmentShader: `uniform float uL; varying vec2 vUv; varying vec3 vN; varying float vId; varying vec3 vW;
      #include <fog_pars_fragment>
      float h(vec2 p){ return fract(sin(dot(p, vec2(127.1,311.7)))*43758.5453); }
      void main(){ float hb = h(vec2(vId, 3.7)); vec3 wall = mix(vec3(0.20,0.22,0.25), vec3(0.32,0.30,0.28), hb);
        float lam = 0.45 + 0.55 * max(0.0, dot(normalize(vN), normalize(vec3(-0.4,-0.6,0.8))));
        vec3 c = wall * lam;
        if (vUv.y < 0.0 && vUv.x < 0.0) { c = vec3(0.16,0.17,0.19) * (0.9 + 0.2*hb); }
        else if (vUv.y > 1.0) {
          vec2 cell = vec2(floor(vUv.x / 3.2), floor(vUv.y / 3.6)); vec2 f = vec2(fract(vUv.x / 3.2), fract(vUv.y / 3.6));
          float win = step(0.18, f.x) * step(f.x, 0.82) * step(0.25, f.y) * step(f.y, 0.8);
          float on = step(0.62, h(cell + vId * 1.37)); vec3 wc = mix(vec3(1.0,0.78,0.45), vec3(0.65,0.8,1.0), step(0.7, h(cell * 1.7 + vId)));
          c = mix(c, vec3(0.05,0.07,0.09) + 0.08 * uL, win * 0.85);
          c += win * on * wc * 0.85;
        }
        c += uL * 0.35 * lam;
        gl_FragColor = vec4(c, 1.0);
        #include <fog_fragment>
      }`,
  });
  bm.uniforms.uL = shared.lightning as any;
  const bmesh = new THREE.Mesh(bg, bm); bmesh.frustumCulled = false; world.add(bmesh);

  const rain = makeRain(); scene.add(rain);


  addEventListener('resize', () => { camera.aspect = container.clientWidth / container.clientHeight; camera.updateProjectionMatrix(); renderer.setSize(container.clientWidth, container.clientHeight); });
  return { renderer, camera, scene, controls, world, rain };
}

/** GPU rain streaks in a box that follows the camera (ENU frame, z up); density follows shared.rain. */
export function makeRain(n = 26000, box = 520) {
  const NR = n; const rp = new Float32Array(NR * 2 * 3), rs = new Float32Array(NR * 2);
  for (let i = 0; i < NR; i++) { const x = Math.random(), y = Math.random(), z = Math.random(); for (let e = 0; e < 2; e++) { rp.set([x, y, z], (i * 2 + e) * 3); rs[i * 2 + e] = e; } }
  const rg = new THREE.BufferGeometry(); rg.setAttribute('position', new THREE.BufferAttribute(rp, 3)); rg.setAttribute('aE', new THREE.BufferAttribute(rs, 1));
  const rain = new THREE.LineSegments(rg, new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, fog: false,
    uniforms: { uTime: shared.time, uCam: { value: new THREE.Vector3() }, uRain: shared.rain, uGust: shared.gust, uL: shared.lightning, uBox: { value: box } },
    vertexShader: `attribute float aE; uniform float uTime, uRain, uGust, uBox; uniform vec3 uCam; varying float vA;
      void main(){ vec3 p = position; float fall = 34.0; vec3 wind = vec3(0.34, 0.94, 0.0) * (8.0 + 26.0 * uGust);
        vec3 w = p * uBox + vec3(wind.xy, -fall) * uTime; w = mod(w - uCam + uBox * 0.5, uBox) + uCam - uBox * 0.5;
        vec3 vel = normalize(vec3(wind.xy, -fall)); w -= vel * aE * (9.0 + 9.0 * uRain);
        vA = step(position.x, uRain * 1.0) * (0.27 + 0.2 * aE) * (0.45 + 0.55 * smoothstep(-0.3, 1.0, sin(dot(w.xy, normalize(wind.xy)) * 0.012 - uTime * 2.4)));
        gl_Position = projectionMatrix * viewMatrix * vec4(w, 1.0); }`,
    fragmentShader: 'uniform float uL; varying float vA; void main(){ if (vA <= 0.0) discard; gl_FragColor = vec4(vec3(0.7,0.78,0.9) * vA * (1.0 + uL * 0.6), 1.0); }',
  }));
  rain.frustumCulled = false; rain.renderOrder = 20; return rain;
}
