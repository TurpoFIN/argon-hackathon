/* Cinema mode: stand inside the storm. A toggle in the 3D view (or the C key) turns the scene into the
   storm that is set: a ragged cloud deck that swallows the towers, wind-driven rain that passes behind
   buildings, soaked reflective streets, the bay at the storm's tide, lightning with thunder delayed by
   distance, and a filmic grade, by night or by day. Rain, mean wind, gusts, wind direction and tide are
   the scenario's forcing from sim/storm.py (exported to data/web/storm_<name>.json) at the current
   moment; when the destruction playback has a run loaded, cinema mode follows and drives its timeline.
   Lightning and the look are presentation only. Everything is a post-process RenderNode plus the
   SceneView's own weather, so the city geometry and data layers are untouched. */
(() => {
  const SCENARIOS = [
    { id: "elnino_ar_2026", label: "El Niño AR 2026 (forecast)" },
    { id: "dec31_2022", label: "31 Dec 2022 (replay)" },
    { id: "atlas14_100yr", label: "100-year design storm" }
  ];
  const MSL_NAVD88 = 0.969; // NOAA 9414290 San Francisco: MSL 2.773 m − NAVD88 1.804 m above station datum
  const EARTH = 6378137; // SceneView's global render sphere, metres
  const ORIGIN = { lon: -122.3937, lat: 37.7955 }; // Ferry Building: origin of the local noise frame
  const CLOUD_BASE = 235; // metres; the tops of Salesforce Tower (326 m) and its neighbours vanish into it
  const STORM_SECONDS_PER_SECOND = 3600 / 10; // playback: one storm hour every ten seconds
  // Low over the bay off the Ferry Building, looking up Market Street into the downtown towers.
  const VANTAGE = { position: { longitude: -122.3868, latitude: 37.7986, z: 48 }, heading: 236, tilt: 88 };
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");

  const storms = {};
  const loadStorm = (id) => (storms[id] ??= fetch(`/data/web/storm_${id}.json`).then((r) => {
    if (!r.ok) throw new Error(`storm ${id}: ${r.status}`);
    return r.json();
  }));

  // Conditions at storm time t (s): rain is block-constant per frame (as the solvers use it), the rest
  // linear. A destruction run may override gust and direction (e.g. its x1.6 stress test).
  function conditionsAt(storm, t, run) {
    const n = storm.t.length;
    const tc = Math.min(Math.max(t, 0), storm.duration_s);
    const i = Math.min(n - 1, Math.floor(tc / storm.dt_frame_s));
    const j = Math.min(n - 1, i + 1);
    const f = Math.min(1, (tc - storm.t[i]) / storm.dt_frame_s);
    const lerp = (a) => a[i] + (a[j] - a[i]) * f;
    const gusts = run?.gust?.length === n ? run.gust : storm.gust_ms;
    const dirs = run?.dir_from?.length === n ? run.dir_from : storm.wind_dir_t ?? storm.t.map(() => storm.wind_dir_deg);
    const d0 = dirs[i], d1 = dirs[j];
    const turn = ((d1 - d0 + 540) % 360) - 180;
    const rainMmH = storm.rain_mm_h[i];
    const gustMs = lerp(gusts);
    return {
      rainMmH,
      meanMs: Math.min(lerp(storm.mean_ms), gustMs),
      gustMs,
      fromDeg: (d0 + turn * f + 360) % 360,
      tideM: lerp(storm.tide_m),
      // Visual rain amount: about 40 mm/h fills the frame; cloudbursts beyond push visibility down further.
      rain: Math.min(1.5, Math.pow(rainMmH / 40, 0.6))
    };
  }

  const COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
  const compass = (deg) => COMPASS[Math.round(deg / 22.5) % 16];
  const pad = (n) => String(n).padStart(2, "0");
  const elapsed = (t) => `T+${Math.floor(t / 3600)}:${pad(Math.floor(t / 60) % 60)}`;
  // Local clock time of storm time t from the scenario's start ("2026-12-24T07:07-08:00").
  function localClock(storm, t) {
    const m = /T(\d\d):(\d\d)/.exec(storm.start_local || "");
    if (!m) return "";
    const minutes = (Number(m[1]) * 60 + Number(m[2]) + Math.floor(t / 60)) % 1440;
    return `${pad(Math.floor(minutes / 60))}:${pad(minutes % 60)} PST`;
  }

  // ---------------------------------------------------------------------------------------------
  // Shader: one full-screen pass over the composite colour and depth of the SceneView.

  const VERTEX = `#version 300 es
in vec2 position;
out vec2 uv;
void main() {
  gl_Position = vec4(position, 0.0, 1.0);
  uv = position * 0.5 + 0.5;
}`;

  const FRAGMENT = `#version 300 es
precision highp float;
in vec2 uv;
out vec4 fragColor;

uniform sampler2D colorTex;
uniform sampler2D depthTex;
uniform mat4 proj;
uniform mat4 invProj;
uniform vec3 upV;        // local up, east, north and horizontal forward/right, in view space
uniform vec3 eastV;
uniform vec3 northV;
uniform vec3 fwdH;
uniform vec3 rightH;
uniform float eyeAlt;    // camera height above the render sphere, m
uniform vec2 eyeEN;      // camera east/north of the Ferry Building, m
uniform float heading;   // unwrapped camera heading, rad: anchors the rain to the world
uniform float radPerPx;
uniform float time;
uniform float rain;      // 0..1
uniform float rainTilt;  // crosswind slant of the rain on screen
uniform vec2 windEN;     // direction the wind blows toward
uniform float windMs;
uniform float cloudBase;
uniform float flash;
uniform vec3 flashV;     // view-space direction to the strike
uniform vec3 bolt[40];   // uv.x, uv.y, distance (m); distance < 0 breaks the line
uniform int boltN;
uniform float boltA;
uniform float night;     // 0 = storm daylight, 1 = night
uniform float waterLevel; // bay surface above the elevation model's zero, m

const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);
const vec3 FLASH = vec3(0.80, 0.86, 1.0);

float hash(vec2 p) {
  p = fract(p * vec2(123.34, 456.21));
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}
float vnoise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x), mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x), u.y);
}
float fbm(vec2 p) {
  float s = 0.0, a = 0.5;
  for (int i = 0; i < 4; i++) { s += a * vnoise(p); p = mat2(1.6, 1.2, -1.2, 1.6) * p; a *= 0.5; }
  return s;
}
vec3 viewPos(vec2 st, float d) {
  vec4 p = invProj * vec4(st * 2.0 - 1.0, d * 2.0 - 1.0, 1.0);
  return p.xyz / p.w;
}
float depthAt(vec2 st) {
  return texelFetch(depthTex, ivec2(clamp(st, vec2(0.0), vec2(0.9999)) * vec2(textureSize(depthTex, 0))), 0).r;
}
vec3 aces(vec3 x) {
  return clamp((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14), 0.0, 1.0);
}
// Optical depth of a medium sigma0 * exp(-h / H) along a ray from height h0 with slope k over L metres.
float expDepth(float sigma0, float H, float h0, float k, float L) {
  float a = exp(-max(h0, 0.0) / H);
  if (abs(k) < 1e-4) return sigma0 * a * L;
  return sigma0 * H / k * (a - exp(-max(h0 + k * L, 0.0) / H));
}
vec3 normalAt(vec2 st, vec3 P, vec2 px) {
  vec3 Pr = viewPos(st + vec2(px.x, 0.0), depthAt(st + vec2(px.x, 0.0)));
  vec3 Pl = viewPos(st - vec2(px.x, 0.0), depthAt(st - vec2(px.x, 0.0)));
  vec3 Pu = viewPos(st + vec2(0.0, px.y), depthAt(st + vec2(0.0, px.y)));
  vec3 Pd = viewPos(st - vec2(0.0, px.y), depthAt(st - vec2(0.0, px.y)));
  vec3 dx = abs(Pr.z - P.z) < abs(P.z - Pl.z) ? Pr - P : P - Pl;
  vec3 dy = abs(Pu.z - P.z) < abs(P.z - Pd.z) ? Pu - P : P - Pd;
  vec3 N = normalize(cross(dx, dy));
  return dot(N, P) > 0.0 ? -N : N;
}
// The scene is rendered under a soft midday sky and used as albedo. By day it is darkened to storm
// light; by night it is relit by street lamps, light-polluted cloud and lit windows.
float lumAt(vec2 st) {
  return dot(pow(texture(colorTex, st).rgb, vec3(2.2)), LUMA);
}
vec3 shade(vec2 st, vec3 albedo, vec3 P, vec3 N, float flashGlow) {
  float h = eyeAlt + dot(P, upV);
  vec2 en = eyeEN + vec2(dot(P, eastV), dot(P, northV));
  float up = dot(N, upV);
  vec3 day = mix(vec3(dot(albedo, LUMA)), albedo, 0.55) * 0.3;
  vec3 lit = day;
  if (night > 0.0) {
    // Street light fades with height; on the street it pools under lamps about every 30 m, warm
    // sodium or white LED. The bay surface gets none.
    float street = exp(-max(h - 6.0, 0.0) / 30.0) * smoothstep(waterLevel + 0.5, waterLevel + 1.4, h);
    vec2 lampCell = en / 30.0;
    vec2 lampId = floor(lampCell);
    vec2 lampAt = vec2(hash(lampId + 1.3), hash(lampId + 8.1)) * 0.5 + 0.25;
    float pool = (1.0 - smoothstep(0.0, 0.3 + 0.2 * hash(lampId + 4.4), length(fract(lampCell) - lampAt))) * step(0.45, hash(lampId));
    vec3 lampCol = mix(vec3(1.0, 0.76, 0.52), vec3(0.9, 0.92, 1.0), step(0.5, hash(lampId + 2.2)));
    float ground = smoothstep(0.6, 0.9, up) * exp(-max(h - 12.0, 0.0) / 6.0);
    vec3 lamp = street * mix(vec3(0.035, 0.028, 0.02), lampCol * (0.012 + 0.06 * pool), ground);
    vec3 nightC = albedo * (vec3(0.0012, 0.0013, 0.0018) + lamp);
    // Lit windows: window-sized cells whose bay width and storey height vary per building side, a
    // minority switched on in runs along a floor. Near the camera the facade texture gates the glow,
    // since glass reads darker than the wall around it.
    float facade = 1.0 - smoothstep(0.25, 0.45, abs(up));
    if (facade > 0.0 && h > waterLevel + 3.0) {
      vec3 t = normalize(cross(upV, N));
      float s = dot(en, vec2(dot(t, eastV), dot(t, northV)));
      vec2 nEN = normalize(vec2(dot(N, eastV), dot(N, northV)) + 1e-5);
      vec2 plane = vec2(floor(atan(nEN.y, nEN.x) * 4.0), floor(dot(en, nEN) / 5.0));
      float busy = hash(plane + 3.3);
      vec2 cell = vec2(1.4 + hash(plane + 1.1), 3.3 + 0.9 * hash(plane + 2.2));
      vec2 g = vec2(s, h) / cell + hash(plane + 4.4) * 7.0;
      vec2 id = floor(g), f = fract(g);
      float run = hash(vec2(floor(id.x / 6.0), id.y) + plane * 0.37);
      if (run < 0.08 + 0.3 * busy && hash(id + 0.37) < 0.75) {
        vec2 o = 2.0 / vec2(textureSize(colorTex, 0));
        float around = (lumAt(st + vec2(o.x, 0.0)) + lumAt(st - vec2(o.x, 0.0)) + lumAt(st + vec2(0.0, o.y)) + lumAt(st - vec2(0.0, o.y))) * 0.25;
        float glass = mix(1.0, smoothstep(-0.01, 0.04, around - dot(albedo, LUMA)), smoothstep(150.0, 40.0, length(P)));
        float pane = smoothstep(0.1, 0.2, f.x) * (1.0 - smoothstep(0.8, 0.9, f.x)) * smoothstep(0.25, 0.32, f.y) * (1.0 - smoothstep(0.78, 0.85, f.y));
        vec3 wc = mix(vec3(1.0, 0.78, 0.55), vec3(0.88, 0.92, 1.0), step(0.6, hash(id + 5.1)));
        nightC += wc * pane * glass * facade * (0.15 + 0.5 * hash(id + 2.9));
      }
    }
    lit = mix(day, nightC, night);
  }
  return lit + albedo * FLASH * flashGlow * mix(0.22, 0.32, night);
}

void main() {
  vec2 res = vec2(textureSize(colorTex, 0));
  vec2 st = uv;
  vec3 col = pow(texture(colorTex, st).rgb, vec3(2.2));

  float d = depthAt(st);
  bool sky = d >= 0.999999;
  vec3 P = viewPos(st, sky ? 1.0 : d);
  vec3 dir = normalize(P);
  float dist = sky ? 40000.0 : length(P);
  float k = dot(dir, upV);
  vec2 dirEN = vec2(dot(dir, eastV), dot(dir, northV));
  vec2 pEN = eyeEN + dirEN * dist;
  float jitter = hash(gl_FragCoord.xy + fract(time * 7.0) * 61.0);
  float flashGlow = flash * (0.5 + 2.4 * pow(max(dot(dir, flashV), 0.0), 5.0));

  vec2 px = 1.0 / res;
  if (sky) {
    // Above the deck the sky is a dark, heavy grey, a little lighter toward the horizon; near black at night.
    float horizon = pow(1.0 - clamp(k, 0.0, 1.0), 4.0);
    col = mix(mix(vec3(0.022, 0.027, 0.034), vec3(0.07, 0.08, 0.092), horizon), mix(vec3(0.0005, 0.0006, 0.0009), vec3(0.004, 0.0028, 0.002), horizon), night)
      + FLASH * flashGlow * 0.07;
  } else {
    vec3 N = normalAt(st, P, px);
    vec3 albedo = col;
    col = shade(st, albedo, P, N, flashGlow);

    // Soaked ground: upward-facing surfaces mirror the city with rain-ripple jitter (screen-space reflection).
    float wet = smoothstep(0.02, 0.25, rain) * smoothstep(0.82, 0.97, dot(N, upV)) * (1.0 - smoothstep(700.0, 2600.0, dist));
    if (wet > 0.01) {
      float puddle = smoothstep(0.38, 0.72, fbm(pEN * 0.11));
      // Ripples tilt the surface mostly toward and away from the camera, independently per column,
      // so each reflected light breaks into a vertical streak (the smear below joins it up).
      vec2 rq = pEN * 1.3 / (1.0 + dist / 90.0);
      vec2 rip = vec2(vnoise(rq + time * 1.7), vnoise(rq * 1.3 - time * 1.3 + 7.0)) - 0.5;
      vec3 Nw = normalize(N + (fwdH * rip.x * 2.0 + rightH * rip.y * 0.25) * mix(0.12, 0.05, puddle) * (0.4 + min(rain, 1.0)));
      vec3 R = reflect(dir, Nw);
      vec3 skyRefl = mix(vec3(0.045, 0.052, 0.062), vec3(0.0035, 0.0026, 0.002), night) + FLASH * flashGlow * 0.06;
      vec3 refl = vec3(0.0);
      float hit = 0.0;
      float stepL = 1.0 + dist * 0.012;
      vec3 p = P + N * 0.25;
      for (int i = 0; i < 28; i++) {
        p += R * stepL * (0.94 + 0.12 * jitter);
        stepL *= 1.16;
        vec4 c = proj * vec4(p, 1.0);
        if (c.w <= 0.0) break;
        vec2 s = c.xy / c.w * 0.5 + 0.5;
        if (s.x < 0.0 || s.y < 0.0 || s.x > 1.0 || s.y > 1.0) break;
        float sd = depthAt(s);
        if (sd >= 0.999999) continue;
        float behind = viewPos(s, sd).z - p.z;
        if (behind > 0.0 && behind < stepL * 3.0) {
          // Refine the crossing so neighbouring pixels agree on what they reflect.
          vec3 a = p - R * stepL, b = p;
          for (int j = 0; j < 5; j++) {
            vec3 m = (a + b) * 0.5;
            vec4 mc = proj * vec4(m, 1.0);
            vec2 ms = mc.xy / mc.w * 0.5 + 0.5;
            if (viewPos(ms, depthAt(ms)).z - m.z > 0.0) b = m; else a = m;
          }
          vec4 bc = proj * vec4(b, 1.0);
          s = clamp(bc.xy / bc.w * 0.5 + 0.5, vec2(0.0), vec2(1.0));
          sd = depthAt(s);
          vec2 e = smoothstep(0.0, 0.1, s) * (1.0 - smoothstep(0.9, 1.0, s));
          hit = e.x * e.y;
          // Smear the reflection vertically: rain-roughened water turns each light into a streak.
          float spread = (0.006 + 0.03 * min(rain, 1.0)) * (1.0 - 0.4 * puddle);
          for (int k = 0; k < 4; k++) {
            vec2 sk = clamp(s + vec2(0.0, (float(k) - 1.5 + jitter - 0.5) * spread), vec2(0.0), vec2(1.0));
            float dk = depthAt(sk);
            if (dk >= 0.999999) { refl += skyRefl; continue; }
            vec3 hk = viewPos(sk, dk);
            refl += shade(sk, pow(texture(colorTex, sk).rgb, vec3(2.2)), hk, normalAt(sk, hk, px), flashGlow);
          }
          refl *= 0.25;
          break;
        }
      }
      refl = mix(skyRefl, refl, hit);
      float fres = 0.04 + 0.96 * pow(1.0 - max(dot(-dir, N), 0.0), 5.0);
      float amount = wet * mix(0.3, 0.92, puddle) * clamp(fres * 1.7 + 0.12, 0.0, 1.0);
      col = mix(col * (1.0 - 0.4 * wet), refl, amount);
    }
  }

  // Media: rain haze and street spray hugging the ground, sweeping in curtains with the wind ...
  float L = min(dist, 40000.0);
  float sheet = 0.55 + 0.9 * fbm((eyeEN + dirEN * min(L, 1600.0) * 0.6 - windEN * time * (4.0 + windMs)) / 320.0);
  // Visibility falls with the rain rate but is held to about 2 km at cloudburst rates so the city reads.
  float odHaze = expDepth(mix(0.00012, 0.0011, rain / 1.5) * sheet * mix(1.0, 0.7, night), 420.0, eyeAlt, k, L);
  float odSpray = expDepth(0.0035 * rain * mix(1.0, 0.6, night), 18.0, eyeAlt, k, min(L, 3000.0));
  float tHaze = exp(-(odHaze + odSpray));
  // At night the rain haze glows with city light, warmest near the street.
  vec3 hazeCol = mix(vec3(0.050, 0.058, 0.066), vec3(0.0055, 0.0048, 0.0042) * (0.6 + 0.8 * exp(-max(eyeAlt, 0.0) / 300.0)), night)
    + FLASH * flashGlow * 0.06;

  // ... and a ragged cloud deck that the tall towers disappear into.
  float top = cloudBase + 900.0;
  float t0 = 0.0, t1 = -1.0;
  if (abs(k) < 1e-4) {
    if (eyeAlt > cloudBase - 90.0 && eyeAlt < top) t1 = L;
  } else {
    float ta = (cloudBase - 90.0 - eyeAlt) / k, tb = (top - eyeAlt) / k;
    t0 = max(0.0, min(ta, tb));
    t1 = min(L, max(ta, tb));
  }
  t1 = min(t1, t0 + 9000.0);
  float tCloud = 1.0;
  vec3 sCloud = vec3(0.0);
  if (t1 > t0) {
    float dt = (t1 - t0) / 16.0;
    for (int i = 0; i < 16; i++) {
      float t = t0 + (float(i) + jitter) * dt;
      float h = eyeAlt + k * t;
      vec2 q = eyeEN + dirEN * t - windEN * time * (6.0 + 1.5 * windMs);
      float base = cloudBase - 80.0 + 150.0 * vnoise(q / 170.0 + time * 0.03);
      float band = smoothstep(base, base + 110.0, h) * (1.0 - smoothstep(top - 260.0, top, h));
      float dens = band * (smoothstep(0.28, 0.66, fbm(q / 650.0)) * 0.011 + 0.0013);
      if (dens <= 0.0) continue;
      float hb = clamp((h - cloudBase) / (top - cloudBase), 0.0, 1.0);
      // By night the underside of the deck is lit orange-grey by the city below.
      vec3 c = mix(mix(vec3(0.016, 0.019, 0.024), vec3(0.10, 0.11, 0.125), hb), mix(vec3(0.03, 0.024, 0.019), vec3(0.0025, 0.0023, 0.0022), hb), night)
        + FLASH * flashGlow * mix(0.32, 0.1, hb);
      float a = 1.0 - exp(-dens * dt);
      sCloud += tCloud * a * c;
      tCloud *= 1.0 - a;
      if (tCloud < 0.01) break;
    }
  }
  col = col * tCloud + sCloud;
  col = col * tHaze + hazeCol * (1.0 - tHaze);

  // Lightning channel: occluded by nearer geometry, softened by the rain between it and the camera.
  if (boltA > 0.001 && boltN > 1) {
    vec2 fp = st * res;
    float dmin = 1e9, bd = 0.0;
    for (int i = 0; i < 39; i++) {
      if (i + 1 >= boltN) break;
      vec3 a = bolt[i], b = bolt[i + 1];
      if (a.z < 0.0 || b.z < 0.0) continue;
      vec2 A = a.xy * res, B = b.xy * res, AB = B - A;
      float h = clamp(dot(fp - A, AB) / max(dot(AB, AB), 1e-4), 0.0, 1.0);
      float dd = length(fp - A - AB * h);
      if (dd < dmin) { dmin = dd; bd = mix(a.z, b.z, h); }
    }
    float visible = step(bd, dist + 40.0);
    float glow = exp(-dmin / 1.4) * 3.2 + exp(-dmin / 8.0) * 0.7 + exp(-dmin / 90.0) * 0.14;
    col += FLASH * glow * boltA * visible * mix(0.3, 1.0, tHaze);
  }

  // Rain: streaks on world-anchored cylinders around the camera, each hidden behind nearer geometry.
  if (rain > 0.005) {
    float hl = max(length(vec2(dot(dir, fwdH), dot(dir, rightH))), 1e-3);
    float theta = atan(dot(dir, rightH), dot(dir, fwdH)) + heading;
    float elevT = k / hl;
    float steep = 1.0 - smoothstep(0.8, 0.96, abs(k));
    // Rain shows where something lights it: the background brightness stands in for nearby lights.
    vec3 rainCol = mix(vec3(0.05, 0.056, 0.064) + col * 0.4, vec3(0.02, 0.018, 0.016) + col * 0.8, night) + FLASH * flash * 0.25;
    for (int i = 0; i < 5; i++) {
      float D = 2.2 * pow(2.15, float(i));
      float occ = smoothstep(D * 0.8, D * 1.2, dist);
      if (occ <= 0.0) continue;
      vec2 c = vec2(theta * D, elevT * D);
      c.x -= c.y * rainTilt;
      c.y += time * (8.0 + 0.4 * float(i));
      vec2 cell = vec2(0.085, 1.15);
      vec2 g = c / cell + vec2(float(i) * 7.31, float(i) * 3.7);
      vec2 id = floor(g), f = fract(g);
      if (hash(id + float(i) * 13.1) > min(rain, 1.0) * 0.5 + 0.02) continue;
      float x0 = 0.15 + 0.7 * hash(id + 4.7);
      float aa = D * radPerPx / cell.x + 0.02;
      float line = 1.0 - smoothstep(0.012, 0.012 + aa, abs(f.x - x0));
      float yy = f.y - hash(id + 9.2) * 0.45;
      float len = 0.3 + 0.35 * hash(id + 2.3);
      float seg = smoothstep(0.0, 0.1, yy) * (1.0 - smoothstep(len - 0.15, len, yy));
      col += rainCol * line * seg * occ * steep * (0.09 - 0.014 * float(i)) * (0.4 + 0.6 * min(rain, 1.0));
    }
  }

  // Grade: filmic curve, cold steel shadows, a little warmth kept in the highlights.
  col = aces(col * 2.5);
  float l = dot(col, LUMA);
  col = mix(vec3(l), col, 0.9);
  col *= mix(vec3(0.94, 0.99, 1.04), vec3(1.02, 1.0, 0.98), smoothstep(0.04, 0.55, l));
  col = pow(col, vec3(1.0 / 2.2));
  col = (col - 0.5) * 1.03 + 0.5;
  vec2 v = uv - 0.5;
  v.x *= res.x / res.y;
  col *= 1.0 - smoothstep(0.55, 1.2, length(v)) * 0.25;
  col += (hash(gl_FragCoord.xy + fract(time * 24.0) * 97.0) - 0.5) * 0.012;
  fragColor = vec4(clamp(col, 0.0, 1.0), 1.0);
}`;

  function compile(gl, type, source) {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(`Cinema shader: ${gl.getShaderInfoLog(shader)}`);
    return shader;
  }

  function invert(m) {
    const [a00, a01, a02, a03, a10, a11, a12, a13, a20, a21, a22, a23, a30, a31, a32, a33] = m;
    const b00 = a00 * a11 - a01 * a10, b01 = a00 * a12 - a02 * a10, b02 = a00 * a13 - a03 * a10;
    const b03 = a01 * a12 - a02 * a11, b04 = a01 * a13 - a03 * a11, b05 = a02 * a13 - a03 * a12;
    const b06 = a20 * a31 - a21 * a30, b07 = a20 * a32 - a22 * a30, b08 = a20 * a33 - a23 * a30;
    const b09 = a21 * a32 - a22 * a31, b10 = a21 * a33 - a23 * a31, b11 = a22 * a33 - a23 * a32;
    const det = 1 / (b00 * b11 - b01 * b10 + b02 * b09 + b03 * b08 - b04 * b07 + b05 * b06);
    return [
      (a11 * b11 - a12 * b10 + a13 * b09) * det, (a02 * b10 - a01 * b11 - a03 * b09) * det,
      (a31 * b05 - a32 * b04 + a33 * b03) * det, (a22 * b04 - a21 * b05 - a23 * b03) * det,
      (a12 * b08 - a10 * b11 - a13 * b07) * det, (a00 * b11 - a02 * b08 + a03 * b07) * det,
      (a32 * b02 - a30 * b05 - a33 * b01) * det, (a20 * b05 - a22 * b02 + a23 * b01) * det,
      (a10 * b10 - a11 * b08 + a13 * b06) * det, (a01 * b08 - a00 * b10 - a03 * b06) * det,
      (a30 * b04 - a31 * b02 + a33 * b00) * det, (a21 * b02 - a20 * b04 - a23 * b00) * det,
      (a11 * b07 - a10 * b09 - a12 * b06) * det, (a00 * b09 - a01 * b07 + a02 * b06) * det,
      (a31 * b01 - a30 * b03 - a32 * b00) * det, (a20 * b03 - a21 * b01 + a22 * b00) * det
    ];
  }
  // Column-major matrix × point (w = 1) and × direction (w = 0).
  const transform = (m, x, y, z) => [
    m[0] * x + m[4] * y + m[8] * z + m[12], m[1] * x + m[5] * y + m[9] * z + m[13],
    m[2] * x + m[6] * y + m[10] * z + m[14], m[3] * x + m[7] * y + m[11] * z + m[15]
  ];
  const rotate = (m, [x, y, z]) => [m[0] * x + m[4] * y + m[8] * z, m[1] * x + m[5] * y + m[9] * z, m[2] * x + m[6] * y + m[10] * z];
  const normalize = ([x, y, z]) => { const l = Math.hypot(x, y, z) || 1; return [x / l, y / l, z / l]; };
  const cross = ([ax, ay, az], [bx, by, bz]) => [ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx];
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];

  // ---------------------------------------------------------------------------------------------
  // Sound: synthesised rain, wind and thunder (no recordings to license).

  class StormAudio {
    constructor() { this.muted = false; }
    build() {
      const ctx = this.ctx = new AudioContext();
      const noise = (seconds, brown) => {
        const buffer = ctx.createBuffer(2, ctx.sampleRate * seconds, ctx.sampleRate);
        for (let c = 0; c < 2; c++) {
          const data = buffer.getChannelData(c);
          let last = 0;
          for (let i = 0; i < data.length; i++) {
            const white = Math.random() * 2 - 1;
            data[i] = brown ? (last = (last + 0.02 * white) / 1.02) * 3.5 : white;
          }
        }
        return buffer;
      };
      this.white = noise(4, false);
      this.brown = noise(8, true);
      const compressor = new DynamicsCompressorNode(ctx, { threshold: -18, ratio: 4, attack: 0.01, release: 0.4 });
      this.master = new GainNode(ctx, { gain: 0 });
      this.master.connect(compressor).connect(ctx.destination);
      const loop = (buffer, ...chain) => {
        const source = new AudioBufferSourceNode(ctx, { buffer, loop: true });
        chain.reduce((node, next) => node.connect(next), source).connect(this.master);
        source.start(0, Math.random() * buffer.duration);
      };
      this.rainHiss = new GainNode(ctx, { gain: 0 });
      loop(this.white, new BiquadFilterNode(ctx, { type: "highpass", frequency: 900 }), new BiquadFilterNode(ctx, { type: "lowpass", frequency: 8000 }), this.rainHiss);
      this.rainBody = new GainNode(ctx, { gain: 0 });
      loop(this.brown, new BiquadFilterNode(ctx, { type: "lowpass", frequency: 700 }), this.rainBody);
      this.windFilter = new BiquadFilterNode(ctx, { type: "bandpass", frequency: 420, Q: 1.6 });
      this.windGain = new GainNode(ctx, { gain: 0 });
      loop(this.brown, this.windFilter, this.windGain);
    }
    start() {
      if (!this.ctx) this.build();
      this.ctx.resume();
      this.master.gain.setTargetAtTime(this.muted ? 0 : 0.9, this.ctx.currentTime, 0.6);
    }
    stop() {
      if (!this.ctx) return;
      this.master.gain.setTargetAtTime(0, this.ctx.currentTime, 0.3);
    }
    setMuted(muted) {
      this.muted = muted;
      if (this.ctx) this.master.gain.setTargetAtTime(muted ? 0 : 0.9, this.ctx.currentTime, 0.2);
    }
    update(rain, windMs, gustiness) {
      if (!this.ctx) return;
      const now = this.ctx.currentTime;
      this.rainHiss.gain.setTargetAtTime(0.02 + 0.32 * rain, now, 0.5);
      this.rainBody.gain.setTargetAtTime(0.05 + 0.5 * rain, now, 0.5);
      const w = Math.min(1, windMs / 16) * (0.7 + 0.6 * gustiness);
      this.windGain.gain.setTargetAtTime(0.05 + 0.75 * w, now, 0.25);
      this.windFilter.frequency.setTargetAtTime(260 + 700 * w, now, 0.3);
    }
    thunder(distance, pan) {
      if (!this.ctx || this.muted) return;
      const ctx = this.ctx, now = ctx.currentTime;
      const loud = 1.4 / (1 + distance / 1800);
      const length = 3 + Math.min(5, distance / 900);
      const panner = new StereoPannerNode(ctx, { pan: Math.max(-0.9, Math.min(0.9, pan)) });
      panner.connect(this.master);
      const rumble = new AudioBufferSourceNode(ctx, { buffer: this.brown });
      const low = new BiquadFilterNode(ctx, { type: "lowpass", frequency: 900, Q: 0.7 });
      low.frequency.setValueAtTime(distance < 2500 ? 1400 : 600, now);
      low.frequency.exponentialRampToValueAtTime(70, now + length);
      const gain = new GainNode(ctx, { gain: 0 });
      gain.gain.setValueAtTime(0, now);
      gain.gain.linearRampToValueAtTime(loud, now + 0.08 + distance / 20000);
      // Rolling echoes off the hills and towers.
      for (let i = 1; i < 4; i++) gain.gain.linearRampToValueAtTime(loud * (0.45 + 0.4 * Math.random()), now + 0.35 * i + Math.random() * 0.3);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + length);
      rumble.connect(low).connect(gain).connect(panner);
      rumble.start(now, Math.random() * 4, length + 0.2);
      if (distance < 2200) {
        const crack = new AudioBufferSourceNode(ctx, { buffer: this.white });
        const high = new BiquadFilterNode(ctx, { type: "highpass", frequency: 1800 });
        const snap = new GainNode(ctx, { gain: loud * 0.9 });
        snap.gain.exponentialRampToValueAtTime(0.0001, now + 0.35);
        crack.connect(high).connect(snap).connect(panner);
        crack.start(now, Math.random() * 3, 0.4);
      }
    }
  }

  // ---------------------------------------------------------------------------------------------

  const waitForScene = () => new Promise((resolve) => {
    const check = () => (window.sfScene?.view ? resolve(window.sfScene) : setTimeout(check, 100));
    check();
  });

  require([
    "esri/views/3d/webgl/RenderNode",
    "esri/views/3d/webgl",
    "esri/geometry/SpatialReference",
    "esri/layers/GraphicsLayer",
    "esri/Graphic"
  ], (RenderNode, webgl, SpatialReference, GraphicsLayer, Graphic) => {
    const CALM = { rainMmH: 0, meanMs: 0, gustMs: 0, fromDeg: 200, tideM: MSL_NAVD88, rain: 0 };
    const state = {
      active: false,
      playing: false,
      scenario: SCENARIOS[0].id,
      storm: null,
      t: null, // storm seconds; null until a scenario is loaded (then its peak rain unless set elsewhere)
      weather: CALM,
      night: 1, // lighting target: 1 night, 0 day
      nightNow: 1,
      link: null,
      gustiness: 0,
      strikes: [],
      nextStrike: 0,
      bolt: null,
      headingUnwrapped: 0,
      lastHeading: null
    };
    const audio = new StormAudio();
    let view = null;
    let node = null;
    let water = null;
    let saved = null;
    let lastFrame = performance.now();
    let appliedWeather = null;
    let appliedTide = null;
    let pushedT = null;

    // The destruction playback (destruction-layer.js) owns a storm timeline too. When it has a run
    // loaded, cinema mode takes its scenario and moment, then keeps the two clocks together.
    const destructionSlider = () => document.querySelector('[aria-label="Storm destruction playback"] input[type="range"]');
    function destructionLink() {
      const run = window.sfDestruction?.run;
      const base = run && SCENARIOS.find(({ id }) => run.scenario === id || String(run.scenario).startsWith(`${id}_`));
      return base ? { run, scenario: base.id } : null;
    }

    async function selectScenario(id, t) {
      state.scenario = id;
      const storm = await loadStorm(id);
      if (state.scenario !== id) return;
      state.storm = storm;
      // Without a set moment, open on the scenario's heaviest rain.
      const peak = storm.rain_mm_h.indexOf(Math.max(...storm.rain_mm_h));
      state.t = Math.min(storm.duration_s, Math.max(0, t ?? (peak + 0.5) * storm.dt_frame_s));
      hud.sync();
    }

    // Lightning: a jagged channel from inside the cloud deck to the ground, in front of the camera.
    function strike(now) {
      const camera = view.camera;
      const { longitude: lon0, latitude: lat0 } = camera.position;
      // Mostly in view and near enough to see the channel under the cloud deck; now and then far off.
      const az = (camera.heading + (Math.random() * 56 - 28)) * Math.PI / 180;
      const distance = Math.random() < 0.75 ? 600 + Math.random() * 1900 : 2500 + Math.random() * 3500;
      const toLonLat = (east, north) => [
        lon0 + east / (EARTH * Math.cos(lat0 * Math.PI / 180)) * 180 / Math.PI,
        lat0 + north / EARTH * 180 / Math.PI
      ];
      let e = distance * Math.sin(az), n = distance * Math.cos(az);
      const top = CLOUD_BASE + 550;
      const main = [];
      const steps = 22;
      let de = 0, dn = 0;
      for (let i = 0; i <= steps; i++) {
        const z = top - (top - 4) * (i / steps) + (i > 0 && i < steps ? (Math.random() - 0.5) * 30 : 0);
        main.push([...toLonLat(e, n), z]);
        de = de * 0.4 + (Math.random() - 0.5) * 70;
        dn = dn * 0.4 + (Math.random() - 0.5) * 70;
        e += de; n += dn;
      }
      const lines = [main];
      for (let b = 0; b < 2; b++) {
        const from = 4 + Math.floor(Math.random() * 9);
        const [lon, lat, z0] = main[from];
        const branch = [[lon, lat, z0]];
        let be = 0, bn = 0;
        const side = Math.random() * Math.PI * 2;
        for (let i = 1; i < 7; i++) {
          be += Math.sin(side) * 45 + (Math.random() - 0.5) * 50;
          bn += Math.cos(side) * 45 + (Math.random() - 0.5) * 50;
          const [blon, blat] = [lon + be / (EARTH * Math.cos(lat0 * Math.PI / 180)) * 180 / Math.PI, lat + bn / EARTH * 180 / Math.PI];
          branch.push([blon, blat, z0 - i * (25 + Math.random() * 30)]);
        }
        lines.push(branch);
      }
      // Flatten to render coordinates; NaN rows break the polyline between branches.
      const points = [];
      for (const line of lines) {
        if (points.length) points.push(null);
        points.push(...line);
      }
      const flat = points.flatMap((p) => p ?? [ORIGIN.lon, ORIGIN.lat, 0]);
      const render = new Float64Array(flat.length);
      webgl.toRenderCoordinates(view, flat, 0, SpatialReference.WGS84, render, 0, points.length);
      state.bolt = { render, breaks: points.map((p) => p === null), t0: now };
      const pulses = [[0, 1], [0.36 + Math.random() * 0.12, 0.55 + Math.random() * 0.3]];
      if (Math.random() < 0.5) pulses.push([0.8 + Math.random() * 0.2, 0.35]);
      state.strikes.push({ t0: now, pulses, distance, thunderAt: now + distance / 343, pan: Math.sin(az - camera.heading * Math.PI / 180) });
    }

    function envelope(now, decay) {
      let sum = 0;
      for (const s of state.strikes) {
        for (const [offset, amp] of s.pulses) {
          const t = now - s.t0 - offset;
          if (t >= 0) sum += amp * Math.min(1, t / 0.015) * Math.exp(-t / decay);
        }
      }
      return sum;
    }

    // Per-frame storm state: clock, gusts, lightning schedule, thunder.
    function tick(now) {
      const dt = Math.min(0.1, (now - lastFrame) / 1000);
      lastFrame = now;
      state.link = destructionLink();
      if (state.storm && state.t !== null) {
        // A moment set on the destruction slider (outside cinema's own controls) wins.
        const theirs = state.link ? Number(destructionSlider()?.value) : NaN;
        if (Number.isFinite(theirs) && pushedT !== null && Math.abs(theirs - pushedT) > 1) state.t = theirs;
        if (state.playing) {
          state.t += dt * STORM_SECONDS_PER_SECOND;
          if (state.t > state.storm.duration_s) state.t = 0;
        }
        if (state.link && (pushedT === null || Math.abs(state.t - pushedT) > 1)) {
          window.sfDestruction.setTime(state.t);
          pushedT = state.t;
        }
        state.weather = conditionsAt(state.storm, state.t, state.link?.scenario === state.scenario ? state.link.run : null);
        if (state.playing || now - (hud.syncedAt ?? 0) > 250) hud.sync();
      }
      state.nightNow += (state.night - state.nightNow) * (1 - Math.exp(-dt * 1.6));
      const t = now / 1000;
      // Gusts arrive as slow swells toward the reported gust speed.
      state.gustiness = Math.max(0, Math.sin(t * 0.37) * 0.6 + Math.sin(t * 0.91 + 1.3) * 0.4);
      const sec = now / 1000;
      const w = state.weather;
      if (sec > state.nextStrike) {
        // Lightning is rare in Bay Area atmospheric rivers: only in the heaviest rain, a minute or so apart.
        if (state.nextStrike && w.rainMmH >= 20) strike(sec);
        state.nextStrike = sec + 35 + Math.random() * 55;
      }
      for (const s of state.strikes) {
        if (!s.heard && sec >= s.thunderAt) {
          s.heard = true;
          audio.thunder(s.distance, s.pan);
        }
      }
      state.strikes = state.strikes.filter((s) => !s.heard || sec - s.t0 < 3);
      audio.update(Math.min(1, w.rain), w.meanMs + (w.gustMs - w.meanMs) * state.gustiness, state.gustiness);
      applyWeather();
    }

    function loop(now) {
      if (!state.active) return;
      tick(now);
      requestAnimationFrame(loop);
    }

    // SceneView weather, the bay's waves and its level follow the conditions, but only when they change.
    function applyWeather() {
      const w = state.weather;
      // The bay at the storm's tide. The elevation model is roughly mean sea level, so the surface sits
      // at the tide above MSL; land and piers above it stay dry, low ground below it floods.
      const level = Math.round(Math.max(0.4, w.tideM - MSL_NAVD88) * 20) / 20;
      if (water && level !== appliedTide) {
        appliedTide = level;
        water.elevationInfo = { mode: "absolute-height", offset: level };
      }
      const key = `${w.meanMs > 9}|${Math.round(w.fromDeg / 15)}`;
      if (key === appliedWeather) return;
      appliedWeather = key;
      // Overcast, but no built-in rain: the shader draws rain that is lit correctly by day and night.
      view.environment.weather = { type: "cloudy", cloudCover: 1 };
      const symbol = water?.graphics.getItemAt(0)?.symbol?.clone();
      if (symbol) {
        symbol.symbolLayers.getItemAt(0).waveDirection = (w.fromDeg + 180) % 360;
        symbol.symbolLayers.getItemAt(0).waveStrength = w.meanMs > 9 ? "moderate" : "slight";
        water.graphics.getItemAt(0).symbol = symbol;
      }
    }

    const StormNode = RenderNode.createSubclass({
      constructor() {
        this.consumes = { required: ["composite-color"] };
        this.produces = "composite-color";
      },

      ensure(gl) {
        if (this.program) return;
        const program = gl.createProgram();
        gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, VERTEX));
        gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, FRAGMENT));
        gl.linkProgram(program);
        if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(`Cinema shader: ${gl.getProgramInfoLog(program)}`);
        this.program = program;
        this.u = {};
        const count = gl.getProgramParameter(program, gl.ACTIVE_UNIFORMS);
        for (let i = 0; i < count; i++) {
          const name = gl.getActiveUniform(program, i).name.replace(/\[0\]$/, "");
          this.u[name] = gl.getUniformLocation(program, name);
        }
        this.vao = gl.createVertexArray();
        gl.bindVertexArray(this.vao);
        this.buffer = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, this.buffer);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
        const position = gl.getAttribLocation(program, "position");
        gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
        gl.enableVertexAttribArray(position);
        gl.bindVertexArray(null);
        this.boltData = new Float32Array(40 * 3);
      },

      render(inputs) {
        const input = inputs.find(({ name }) => name === "composite-color");
        const gl = this.gl;
        const color = input.getTexture();
        const depth = input.getTexture(gl.DEPTH_STENCIL_ATTACHMENT);
        const output = this.acquireOutputFramebuffer();
        const now = performance.now();
        this.ensure(gl);
        gl.disable(gl.BLEND);
        gl.disable(gl.DEPTH_TEST);
        gl.disable(gl.CULL_FACE);
        gl.useProgram(this.program);
        const u = this.u;
        const cam = this.camera;

        // Local frame at the eye, rotated into view space.
        const eye = cam.eye;
        const up = normalize(eye);
        const east = normalize([-up[1], up[0], 0]);
        const north = cross(up, east);
        const upV = rotate(cam.viewMatrix, up);
        const fwd = [0, 0, -1];
        let fwdH = normalize(fwd.map((c, i) => c - upV[i] * dot(fwd, upV)));
        if (!Number.isFinite(fwdH[0]) || Math.abs(dot(fwd, upV)) > 0.999) fwdH = normalize(rotate(cam.viewMatrix, north));
        const rightH = normalize(cross(fwdH, upV));
        const radius = Math.hypot(...eye);
        const lat = Math.asin(eye[2] / radius), lon = Math.atan2(eye[1], eye[0]);
        const eyeEN = [
          (lon - ORIGIN.lon * Math.PI / 180) * EARTH * Math.cos(ORIGIN.lat * Math.PI / 180),
          (lat - ORIGIN.lat * Math.PI / 180) * EARTH
        ];
        const heading = view.camera.heading;
        if (state.lastHeading !== null) {
          let delta = heading - state.lastHeading;
          if (delta > 180) delta -= 360;
          if (delta < -180) delta += 360;
          state.headingUnwrapped += delta;
        }
        state.lastHeading = heading;

        const w = state.weather;
        const windMs = w.meanMs + (w.gustMs - w.meanMs) * state.gustiness;
        const toward = (w.fromDeg + 180) * Math.PI / 180;
        const windEN = [Math.sin(toward), Math.cos(toward)];
        const h = heading * Math.PI / 180;
        const crosswind = windMs * (windEN[0] * Math.cos(h) - windEN[1] * Math.sin(h));

        // Lightning: flash envelope and the channel projected with this frame's camera.
        const sec = now / 1000;
        const calm = reducedMotion.matches ? 0.3 : 1;
        // state.holdFlash freezes a strike at full brightness, for stills.
        const flash = state.holdFlash ? 1 : Math.min(1.6, envelope(sec, 0.07)) * calm;
        const boltA = state.holdFlash ? 1 : Math.min(1.5, envelope(sec, 0.11)) * calm;
        let boltN = 0;
        let flashV = [0, 0, -1];
        if (state.bolt && (state.holdFlash || sec - state.bolt.t0 < 1.6)) {
          const { render, breaks } = state.bolt;
          const count = Math.min(40, breaks.length);
          let mid = null;
          for (let i = 0; i < count; i++) {
            const o = i * 3;
            const p = transform(cam.viewMatrix, render[o], render[o + 1], render[o + 2]);
            const c = transform(cam.projectionMatrix, p[0], p[1], p[2]);
            if (breaks[i] || c[3] <= 0) {
              this.boltData.set([0, 0, -1], o);
            } else {
              this.boltData.set([c[0] / c[3] * 0.5 + 0.5, c[1] / c[3] * 0.5 + 0.5, Math.hypot(p[0], p[1], p[2])], o);
              if (i === 8) mid = p;
            }
          }
          boltN = count;
          if (mid) flashV = normalize(mid);
        }

        gl.activeTexture(gl.TEXTURE0);
        gl.bindTexture(gl.TEXTURE_2D, color.glName);
        gl.uniform1i(u.colorTex, 0);
        gl.activeTexture(gl.TEXTURE1);
        gl.bindTexture(gl.TEXTURE_2D, depth.glName);
        gl.uniform1i(u.depthTex, 1);
        gl.activeTexture(gl.TEXTURE0);
        gl.uniformMatrix4fv(u.proj, false, cam.projectionMatrix);
        gl.uniformMatrix4fv(u.invProj, false, invert(cam.projectionMatrix));
        gl.uniform3fv(u.upV, upV);
        gl.uniform3fv(u.eastV, rotate(cam.viewMatrix, east));
        gl.uniform3fv(u.northV, rotate(cam.viewMatrix, north));
        gl.uniform3fv(u.fwdH, fwdH);
        gl.uniform3fv(u.rightH, rightH);
        gl.uniform1f(u.eyeAlt, radius - EARTH);
        gl.uniform2fv(u.eyeEN, eyeEN);
        gl.uniform1f(u.heading, state.headingUnwrapped * Math.PI / 180);
        gl.uniform1f(u.radPerPx, cam.fovY / cam.viewport[3]);
        gl.uniform1f(u.time, (sec % 3600));
        gl.uniform1f(u.rain, w.rain);
        gl.uniform1f(u.rainTilt, Math.max(-0.85, Math.min(0.85, crosswind / 8.5)));
        gl.uniform2fv(u.windEN, windEN);
        gl.uniform1f(u.windMs, windMs);
        gl.uniform1f(u.cloudBase, CLOUD_BASE);
        gl.uniform1f(u.flash, flash);
        gl.uniform3fv(u.flashV, flashV);
        gl.uniform3fv(u.bolt, this.boltData);
        gl.uniform1i(u.boltN, boltN);
        gl.uniform1f(u.boltA, boltN ? boltA : 0);
        gl.uniform1f(u.night, state.nightNow);
        gl.uniform1f(u.waterLevel, appliedTide ?? 0.6);

        gl.bindVertexArray(this.vao);
        gl.drawArrays(gl.TRIANGLES, 0, 3);
        gl.bindVertexArray(null);

        output.attachDepth(input.getAttachment(gl.DEPTH_STENCIL_ATTACHMENT));
        // Keep the storm moving even when the camera is still.
        this.requestRender();
        return output;
      },

      destroy() {
        const gl = this.gl;
        if (gl && this.program) {
          gl.deleteProgram(this.program);
          gl.deleteBuffer(this.buffer);
          gl.deleteVertexArray(this.vao);
        }
      }
    });

    // The bay as a moving, wind-roughened water surface at sea level; the land and piers sit above it.
    function makeWater() {
      return new GraphicsLayer({
        title: "Cinema bay surface",
        listMode: "hide",
        elevationInfo: { mode: "absolute-height", offset: 0.6 },
        graphics: [new Graphic({
          geometry: {
            type: "polygon",
            rings: [[[-122.56, 37.69], [-122.56, 37.87], [-122.29, 37.87], [-122.29, 37.69], [-122.56, 37.69]]],
            spatialReference: { wkid: 4326 }
          },
          symbol: {
            type: "polygon-3d",
            symbolLayers: [{ type: "water", color: [38, 52, 58, 0.92], waveStrength: "moderate", waterbodySize: "large", waveDirection: 160 }]
          }
        })]
      });
    }

    async function enable() {
      if (state.active) return;
      state.active = true;
      document.body.classList.add("cinema");
      hud.sync();
      const scene = await waitForScene();
      view = scene.view;
      // Wait for the city's first full load: the storm renders every frame, which would keep the
      // view "updating" and hold scene.js's loading message up.
      await new Promise((resolve) => {
        const check = () => (document.documentElement.dataset.sceneReady || !state.active ? resolve() : setTimeout(check, 200));
        check();
      });
      if (!state.active || saved) return;
      view.closePopup?.();
      const env = view.environment;
      saved = { lighting: env.lighting.clone(), weather: env.weather?.clone?.() ?? null };
      // Soft midday light without shadows: the shader treats this render as albedo and relights it.
      env.lighting = {
        type: "sun",
        date: new Date("2022-12-31T20:00:00Z"),
        directShadowsEnabled: false,
        waterReflectionEnabled: true,
        cameraTrackingEnabled: false
      };
      appliedWeather = null;
      // Data overlays step aside for the film; each layer's own visibility comes back on exit.
      saved.hidden = view.map.layers.filter((layer) => layer !== scene.buildings && layer.visible).toArray();
      saved.hidden.forEach((layer) => { layer.visible = false; });
      water = makeWater();
      appliedTide = null;
      view.map.add(water);
      // Take the scenario and moment already set on the destruction timeline, if any.
      const link = destructionLink();
      const theirs = link ? Number(destructionSlider()?.value) : NaN;
      pushedT = Number.isFinite(theirs) ? theirs : null;
      if (link) selectScenario(link.scenario, Number.isFinite(theirs) ? theirs : undefined).catch(console.error);
      else if (!state.storm) selectScenario(state.scenario).catch(console.error);
      state.headingUnwrapped = 0;
      state.lastHeading = null;
      state.nextStrike = performance.now() / 1000 + 2.5;
      lastFrame = performance.now();
      try {
        node = new StormNode({ view });
      } catch (error) {
        console.error(error);
      }
      audio.start();
      requestAnimationFrame(loop);
      if (view.camera.position.z > 450) view.goTo(VANTAGE, { duration: 3200, easing: "in-out-cubic" }).catch(() => {});
      hud.sync();
    }

    function disable() {
      if (!state.active) return;
      state.active = false;
      document.body.classList.remove("cinema");
      if (!saved) return hud.sync(); // left before the city had loaded; nothing was changed yet
      node?.destroy();
      node = null;
      if (water) view.map.remove(water);
      water = null;
      view.environment.lighting = saved.lighting;
      view.environment.weather = saved.weather ?? { type: "sunny", cloudCover: 0.5 };
      saved.hidden.forEach((layer) => { layer.visible = true; });
      saved = null;
      audio.stop();
      state.strikes = [];
      state.bolt = null;
      hud.sync();
    }

    // ---------------------------------------------------------------------------------------------
    // Controls: the toggle, and a letterbox with the storm clock and the observations behind it.

    const el = (tag, className, text) => {
      const node = document.createElement(tag);
      if (className) node.className = className;
      if (text != null) node.textContent = text;
      return node;
    };
    const FILM_ICON = '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M2 3.5A1.5 1.5 0 0 1 3.5 2h9A1.5 1.5 0 0 1 14 3.5v9a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 2 12.5v-9Zm1.5-.5a.5.5 0 0 0-.5.5V5h1.5V3h-1Zm2 0v4.5h5V3h-5Zm6 0v2H13V3.5a.5.5 0 0 0-.5-.5h-1ZM13 6h-1.5v1.5H13V6Zm0 2.5h-1.5V10H13V8.5Zm0 2.5h-1.5v2h1a.5.5 0 0 0 .5-.5V11Zm-2.5 2V8.5h-5V13h5Zm-6 0v-2H3v1.5a.5.5 0 0 0 .5.5h1ZM3 10h1.5V8.5H3V10Zm0-2.5h1.5V6H3v1.5Z"/></svg>';

    const hud = (() => {
      const toggle = el("button", "cinema-toggle");
      toggle.type = "button";
      toggle.id = "cinema-toggle";
      toggle.title = "Cinema mode (C)";

      const frame = el("div", "cinema-frame");
      frame.setAttribute("aria-hidden", "true");
      const top = el("div", "cinema-bar cinema-top");
      const title = el("div", "cinema-title");
      const subtitle = el("span", "cinema-sub");
      title.append(el("strong", null, "StormSim SF"), subtitle);
      const time = el("span", "cinema-clock");
      top.append(title, time);

      const bottom = el("div", "cinema-bar cinema-bottom");
      const readouts = el("dl", "cinema-readouts");
      const readout = (label) => {
        const group = el("div");
        const value = el("dd");
        group.append(el("dt", null, label), value);
        readouts.append(group);
        return value;
      };
      const rainOut = readout("Rain");
      const windOut = readout("Wind");
      const gustOut = readout("Gusts");
      const tideOut = readout("Bay");
      const controls = el("div", "cinema-controls");
      const scenario = el("select", "cinema-select");
      scenario.setAttribute("aria-label", "Storm scenario");
      for (const { id, label } of SCENARIOS) scenario.append(new Option(label, id));
      const phase = el("div", "cinema-phase");
      phase.setAttribute("role", "group");
      phase.setAttribute("aria-label", "Light");
      const nightButton = el("button", "cinema-button", "Night");
      const dayButton = el("button", "cinema-button", "Day");
      for (const [button, value] of [[nightButton, 1], [dayButton, 0]]) {
        button.type = "button";
        button.addEventListener("click", () => { state.night = value; sync(); });
        phase.append(button);
      }
      const play = el("button", "cinema-button");
      play.type = "button";
      const slider = el("input", "cinema-time");
      Object.assign(slider, { type: "range", min: "0", max: "21600", step: "60" });
      slider.setAttribute("aria-label", "Storm time");
      const sound = el("button", "cinema-button");
      sound.type = "button";
      controls.append(scenario, phase, play, slider, sound);
      const source = el("p", "cinema-source");
      bottom.append(readouts, controls, source);
      frame.append(top, bottom);
      document.body.append(frame, toggle);

      const one = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1, minimumFractionDigits: 1 });
      const two = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2, minimumFractionDigits: 2 });
      const sync = () => {
        hud.syncedAt = performance.now();
        const on = state.active;
        toggle.setAttribute("aria-pressed", String(on));
        toggle.innerHTML = `${FILM_ICON}<span>${on ? "Exit cinema" : "Cinema"}</span>`;
        frame.setAttribute("aria-hidden", String(!on));
        frame.inert = !on;
        const storm = state.storm;
        const w = state.weather;
        const linked = state.link?.scenario === state.scenario;
        subtitle.textContent = storm ? storm.title + (linked ? " · synced with destruction playback" : "") : "Loading storm…";
        time.textContent = storm && state.t !== null ? [elapsed(state.t), localClock(storm, state.t)].filter(Boolean).join(" · ") : "";
        rainOut.textContent = `${one.format(w.rainMmH)} mm/h`;
        windOut.textContent = `${one.format(w.meanMs)} m/s ${compass(w.fromDeg)}`;
        gustOut.textContent = `${one.format(w.gustMs)} m/s`;
        tideOut.textContent = `${two.format(w.tideM)} m NAVD88`;
        scenario.value = state.scenario;
        if (storm) slider.max = String(storm.duration_s);
        if (document.activeElement !== slider && state.t !== null) slider.value = String(state.t);
        nightButton.setAttribute("aria-pressed", String(state.night === 1));
        dayButton.setAttribute("aria-pressed", String(state.night === 0));
        play.textContent = state.playing ? "Pause" : "Play";
        play.setAttribute("aria-label", state.playing ? "Pause the storm clock" : "Play the storm clock");
        sound.textContent = audio.muted ? "Sound off" : "Sound on";
        sound.setAttribute("aria-pressed", String(!audio.muted));
        source.textContent = `Conditions: sim/storm.py forcing for ${state.scenario}${linked && state.link.run.gust ? ", gusts from the destruction run" : ""}. Lightning is illustrative.`;
        readouts.title = source.textContent;
      };
      toggle.addEventListener("click", () => (state.active ? disable() : enable()));
      play.addEventListener("click", () => { state.playing = !state.playing; sync(); });
      slider.addEventListener("input", () => { state.t = Number(slider.value); sync(); });
      scenario.addEventListener("change", () => {
        const id = scenario.value;
        // Keep the destruction playback on the same storm when it has a run for it.
        if (state.link && state.link.scenario !== id) window.sfDestruction.load?.(id)?.catch(() => {});
        selectScenario(id, state.t ?? undefined).catch(console.error);
      });
      sound.addEventListener("click", () => { audio.setMuted(!audio.muted); sync(); });
      addEventListener("keydown", (event) => {
        if (event.key !== "c" && event.key !== "C") return;
        if (event.metaKey || event.ctrlKey || event.altKey || event.target.closest?.("input, textarea, select, [contenteditable]")) return;
        state.active ? disable() : enable();
      });
      addEventListener("keydown", (event) => { if (event.key === "Escape" && state.active) disable(); });
      const hud = { sync, syncedAt: 0 };
      sync();
      return hud;
    })();

    window.sfCinema = { enable, disable, state, conditionsAt, strike: () => strike(performance.now() / 1000), get node() { return node; } };
  });
})();
