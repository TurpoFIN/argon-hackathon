/* Destruction in 3D: plays a sim/destruction.py run (exported by sim/destruction_web.py to /data/web/) on the
   storm timeline. Wires sag to the street when they come down, poles, streetlights, signal masts and trees topple
   in the fall direction the engine computed, damaged buildings take their Hazus damage-state colour, buildings and
   Muni sections without power turn violet, and roof debris traces its flight. Nothing here is decorative: every
   object and time comes from the run file. */
(() => {
  const M_PER_DEG = 111320;
  const DS_COLORS = [null, [242, 178, 0], [255, 109, 0], [232, 20, 60], [160, 0, 60]];
  const C = {
    wood: [139, 105, 72], steel: [150, 154, 160], wire: [215, 215, 215, 0.95], ocs: [0, 209, 239, 0.8],
    out: [173, 93, 255], down: [255, 45, 85], lampOn: [255, 214, 102], lampOff: [55, 55, 60], sigOk: [0, 199, 105],
    sigBad: [255, 109, 0], sigDark: [173, 93, 255], sigFlood: [0, 108, 255], glass: [140, 205, 255, 0.55],
    smashed: [246, 68, 185], trunk: [110, 80, 50], crown: [46, 139, 70], crownDown: [128, 140, 60], debris: [255, 209, 102]
  };
  const FALL_MS = 900;
  const TREE_TILT = -88; // negative tilt topples an ObjectSymbol3DLayer toward its heading (checked in the scene)
  const STORM_HOUR_MS = 8000; // playback: one storm hour every 8 s

  const waitScene = () => new Promise((resolve) => {
    const check = () => (window.sfScene?.view ? resolve(window.sfScene) : setTimeout(check, 100));
    check();
  });

  require([
    "esri/layers/GraphicsLayer", "esri/Graphic", "esri/geometry/Polyline", "esri/geometry/Polygon", "esri/geometry/Point",
    "esri/symbols/LineSymbol3D", "esri/symbols/PointSymbol3D", "esri/symbols/PolygonSymbol3D",
    "esri/layers/FeatureLayer"
  ], async (GraphicsLayer, Graphic, Polyline, Polygon, Point, LineSymbol3D, PointSymbol3D, PolygonSymbol3D, FeatureLayer) => {
    const { view, map } = await waitScene();
    const SR = { wkid: 4326 };

    // ------------------------------------------------------------ symbols (built once, shared)
    const tube = (color, w) => new LineSymbol3D({ symbolLayers: [{ type: "path", profile: "circle", width: w, height: w, material: { color }, cap: "round" }] });
    const line = (color, size) => new LineSymbol3D({ symbolLayers: [{ type: "line", size, material: { color } }] });
    const ball = (color, size, primitive = "sphere") => new PointSymbol3D({
      symbolLayers: [{ type: "object", resource: { primitive }, width: size, depth: size, height: size, anchor: "center", material: { color } }]
    });
    const box = (color, w, d, h) => new PointSymbol3D({
      symbolLayers: [{ type: "object", resource: { primitive: "cube" }, width: w, depth: d, height: h, anchor: "bottom", material: { color } }]
    });
    const extrude = (rgb, a, h) => new PolygonSymbol3D({
      symbolLayers: [{ type: "extrude", size: h, material: { color: [...rgb, a] }, edges: { type: "solid", color: [...rgb, Math.min(1, a + 0.3)], size: 0.6 } }]
    });
    const S = {
      pole: tube(C.wood, 0.36), poleDown: tube([190, 70, 50], 0.36), lightPole: tube(C.steel, 0.22), lightDown: tube([200, 200, 205], 0.22),
      wire: line(C.wire, 2), wireOut: line([...C.out, 0.95], 3.5), wireDown: tube(C.down, 0.35),
      ocs: line(C.ocs, 1.6), ocsOut: line([...C.out, 0.95], 3.5), ocsDown: tube(C.down, 0.35),
      lampOn: ball(C.lampOn, 0.9), lampOff: ball(C.lampOff, 0.9),
      sigPole: tube([70, 72, 76], 0.25), sigPoleDown: tube([200, 120, 60], 0.25),
      sigOk: box(C.sigOk, 1.0, 0.5, 1.1), sigBad: box(C.sigBad, 1.0, 0.5, 1.1), sigDark: box(C.sigDark, 1.0, 0.5, 1.1), sigFlood: box(C.sigFlood, 1.0, 0.5, 1.1),
      shelter: box(C.glass, 3.2, 1.6, 2.5), smashed: box(C.smashed, 3.4, 1.8, 0.6),
      debris: line([...C.debris, 0.95], 1.3)
    };
    const dsSym = new Map();
    const buildingSym = (ds, h) => {
      const k = `${ds}:${Math.round(h)}`;
      if (!dsSym.has(k)) dsSym.set(k, extrude(DS_COLORS[ds], 0.42, Math.round(h) + 0.8));
      return dsSym.get(k);
    };
    const outSym = new Map();
    const outageSym = (h) => {
      const k = Math.round(h);
      if (!outSym.has(k)) outSym.set(k, extrude(C.out, 0.22, k + 1.6));
      return outSym.get(k);
    };

    // ------------------------------------------------------------ geometry helpers (metres -> degrees)
    const offset = (lon, lat, east, north) => [lon + east / (M_PER_DEG * Math.cos(lat * Math.PI / 180)), lat + north / M_PER_DEG];
    const pl = (pts) => new Polyline({ paths: [pts], hasZ: true, spatialReference: SR });
    // A rigid stick of length L standing at (lon, lat), rotated phi degrees from vertical toward bearing deg.
    const stick = (lon, lat, L, bearing, phi, z0 = 0) => {
      const b = bearing * Math.PI / 180, p = phi * Math.PI / 180;
      const [lo, la] = offset(lon, lat, Math.sin(b) * Math.sin(p) * L, Math.cos(b) * Math.sin(p) * L);
      return [[lon, lat, z0], [lo, la, z0 + Math.cos(p) * L]];
    };
    const tip = (lon, lat, L, bearing, phi, z0 = 0) => {
      const [, end] = stick(lon, lat, L, bearing, phi, z0);
      return new Point({ longitude: end[0], latitude: end[1], z: Math.max(0.6, end[2]), spatialReference: SR });
    };
    const ring = (flat) => {
      const r = [];
      for (let i = 0; i < flat.length; i += 2) r.push([flat[i], flat[i + 1]]);
      return r;
    };

    // ------------------------------------------------------------ layers
    const make = (title, mode) => new GraphicsLayer({ title, elevationInfo: { mode }, listMode: "hide" });
    const L = {
      buildings: make("Destruction: buildings", "on-the-ground"),
      outage: make("Destruction: without power", "on-the-ground"),
      things: make("Destruction: infrastructure", "relative-to-ground")
    };
    const group = [L.outage, L.buildings, L.things];
    map.addMany(group);

    // ------------------------------------------------------------ state machine
    let run = null;
    let items = [];       // {times: [...ascending], state: -1, apply(stateIdx, animate)}
    let t = 0, playing = false, lastFrame = 0;
    const anims = new Set();

    const stateAt = (times, tt) => {
      let s = 0;
      for (const x of times) if (x !== null && tt >= x) s++;
      return s;
    };
    const animate = (fn) => {
      const a = { start: performance.now(), fn };
      anims.add(a);
      fn(0);
    };
    function tick(now) {
      for (const a of anims) {
        const u = Math.min(1, (now - a.start) / FALL_MS);
        a.fn(u * u);  // gravity-like ease-in
        if (u >= 1) anims.delete(a);
      }
      if (playing && run) {
        const dt = now - (lastFrame || now);
        setTime(Math.min(run.t[run.t.length - 1], t + dt / STORM_HOUR_MS * 3600), true);
        if (t >= run.t[run.t.length - 1]) setPlaying(false);
      }
      lastFrame = now;
      flushTrees();
      requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);

    function setTime(next, fromPlayback = false) {
      const forward = next >= t;
      t = next;
      for (const it of items) {
        const s = stateAt(it.times, t);
        if (s !== it.state) {
          const anim = fromPlayback && forward && s === it.state + 1;
          it.state = s;
          it.apply(s, anim);
        }
      }
      renderPanel();
    }

    // A toppling stick (pole, light mast, signal mast, tree) with an optional head object riding its tip.
    function toppler({ lon, lat, len, tFail, bearing, symUp, symDown, head, headUp, headDown, restPhi = 86, extraTimes = [], headFor }) {
      const g = new Graphic({ geometry: pl(stick(lon, lat, len, 0, 0)), symbol: symUp });
      const h = head ? new Graphic({ geometry: tip(lon, lat, len, 0, 0), symbol: headUp }) : null;
      L.things.add(g);
      if (h) L.things.add(h);
      if (tFail === null && !extraTimes.length) return;
      const times = [tFail, ...extraTimes];
      items.push({
        times, state: 0, apply(s, anim) {
          const fallen = tFail !== null && t >= tFail;
          const pose = (phi) => {
            g.geometry = pl(stick(lon, lat, len, bearing ?? 0, phi));
            if (h) h.geometry = tip(lon, lat, len, bearing ?? 0, phi);
          };
          g.symbol = fallen ? symDown : symUp;
          if (h) h.symbol = headFor ? headFor(t, fallen) : (fallen ? headDown : headUp);
          if (fallen && anim && t - tFail < 400) animate((u) => pose(u * restPhi));
          else pose(fallen ? restPhi : 0);
        }
      });
    }

    // The standing urban forest: every inventory street tree as a realistic model (genus, height, crown from the
    // inventory). Trees that fail in the run are left out here and drawn by the run so they can fall.
    // Trees that fail keep their fall bearing as heading and topple by tilting about the base (attribute edits).
    let streetTrees = null, standing = null, shown = true, treeFeatures = [], treePending = new Map(), treeEditing = false;
    const setTreeTilt = (id, tilt) => treePending.set(id, tilt);
    async function flushTrees() {
      if (treeEditing || !treePending.size || !standing) return;
      treeEditing = true;
      const updateFeatures = [...treePending].map(([id, tilt]) => {
        const f = treeFeatures[id];
        f.attributes = { ...f.attributes, tilt };
        return new Graphic({ geometry: f.geometry, attributes: f.attributes });
      });
      treePending = new Map();
      try { await standing.applyEdits({ updateFeatures }); } catch (error) { console.error(error); }
      treeEditing = false;
    }
    async function buildStanding(fallDir) {
      streetTrees ??= await (await fetch("/data/web/destruction_street_trees.json")).json();
      if (standing) { map.remove(standing); standing.destroy(); }
      treePending = new Map();
      treeFeatures = streetTrees.trees.map(([lon, lat, h, cr, m], id) => new Graphic({
        geometry: new Point({ longitude: lon, latitude: lat, spatialReference: SR }),
        attributes: { oid: id + 1, model: streetTrees.models[m], h: Math.max(3, h), crown: Math.max(2, cr),
                      heading: fallDir.get(id) ?? (id * 137.508) % 360, tilt: 0 }
      }));
      const source = treeFeatures;
      const web = (name) => ({ type: "web-style", styleName: "EsriRealisticTreesStyle", name });
      standing = new FeatureLayer({
        title: "Street trees (3D)", source, objectIdField: "oid", geometryType: "point", spatialReference: SR, listMode: "hide", popupEnabled: false,
        fields: [{ name: "oid", type: "oid" }, { name: "model", type: "string" }, { name: "h", type: "double" }, { name: "crown", type: "double" },
                 { name: "heading", type: "double" }, { name: "tilt", type: "double" }],
        elevationInfo: { mode: "on-the-ground" }, visible: shown,
        renderer: {
          type: "unique-value", field: "model", defaultSymbol: web("Unknown"),
          uniqueValueInfos: streetTrees.models.map((name) => ({ value: name, symbol: web(name) })),
          visualVariables: [
            { type: "size", field: "h", axis: "height", valueUnit: "meters" },
            { type: "size", field: "crown", axis: "width-and-depth", valueUnit: "meters" },
            { type: "rotation", field: "heading", axis: "heading", rotationType: "geographic" },
            { type: "rotation", field: "tilt", axis: "tilt" }
          ]
        }
      });
      map.add(standing);
    }

    function clear() {
      group.forEach((l) => l.removeAll());
      items = [];
      anims.clear();
    }

    let ocsLen = [];
    async function build(d) {
      clear();
      run = d;
      ocsLen = d.ocs.map(([lon0, lat0, lon1, lat1]) => Math.hypot((lon1 - lon0) * M_PER_DEG * Math.cos(lat0 * Math.PI / 180), (lat1 - lat0) * M_PER_DEG));
      const hw = d.heights.power_wire, ow = d.heights.ocs_wire;
      // buildings: damage state rises over time
      for (const [, , dsT, h, flat] of d.buildings) {
        const g = new Graphic({ geometry: new Polygon({ rings: [ring(flat)], spatialReference: SR }), symbol: null, visible: false });
        L.buildings.add(g);
        items.push({ times: dsT.filter((v) => v !== null), state: 0, apply(s) {
          g.visible = s > 0;
          if (s > 0) g.symbol = buildingSym(s, h);
        } });
      }
      for (const [, tOut, h, flat] of d.outage) {
        const g = new Graphic({ geometry: new Polygon({ rings: [ring(flat)], spatialReference: SR }), symbol: outageSym(h), visible: false });
        L.outage.add(g);
        items.push({ times: [tOut], state: 0, apply(s) { g.visible = s > 0; } });
      }
      // power poles
      const poleT = d.poles.map((p) => p[2]);
      d.poles.forEach(([lon, lat, tf, dir]) => toppler({ lon, lat, len: d.heights.pole, tFail: tf, bearing: dir, symUp: S.pole, symDown: S.poleDown }));
      // power spans: static ones merged into one graphic; changing ones sag to the street when down
      const staticPaths = [];
      for (const [lon0, lat0, lon1, lat1, tDown, tOut, pa, pb] of d.spans) {
        const up = [[lon0, lat0, hw], [lon1, lat1, hw]];
        if (tDown === null && tOut === null) { staticPaths.push(up); continue; }
        const g = new Graphic({ geometry: pl(up), symbol: S.wire });
        L.things.add(g);
        const endsT = [pa >= 0 ? poleT[pa] : null, pb >= 0 ? poleT[pb] : null];
        items.push({ times: [tDown, tOut, ...endsT].filter((v) => v !== null), state: 0, apply() {
          const down = tDown !== null && t >= tDown;
          if (!down) {
            g.geometry = pl(up);
            g.symbol = tOut !== null && t >= tOut ? S.wireOut : S.wire;
            return;
          }
          const za = endsT[0] !== null && t >= endsT[0] ? 0.3 : hw, zb = endsT[1] !== null && t >= endsT[1] ? 0.3 : hw;
          const mid = [(lon0 + lon1) / 2, (lat0 + lat1) / 2, 0.25];
          g.geometry = pl([[lon0, lat0, za], [lon0 + (lon1 - lon0) * 0.3, lat0 + (lat1 - lat0) * 0.3, 0.3], mid,
                           [lon0 + (lon1 - lon0) * 0.7, lat0 + (lat1 - lat0) * 0.7, 0.3], [lon1, lat1, zb]]);
          g.symbol = S.wireDown;
        } });
      }
      if (staticPaths.length) L.things.add(new Graphic({ geometry: new Polyline({ paths: staticPaths, hasZ: true, spatialReference: SR }), symbol: S.wire }));
      // Muni overhead contact wire
      const ocsStatic = [];
      for (const [lon0, lat0, lon1, lat1, tDown, tOut] of d.ocs) {
        const up = [[lon0, lat0, ow], [lon1, lat1, ow]];
        if (tDown === null && tOut === null) { ocsStatic.push(up); continue; }
        const g = new Graphic({ geometry: pl(up), symbol: S.ocs });
        L.things.add(g);
        items.push({ times: [tDown, tOut].filter((v) => v !== null), state: 0, apply() {
          if (tDown !== null && t >= tDown) {
            g.geometry = pl([[lon0, lat0, ow], [(lon0 + lon1) / 2, (lat0 + lat1) / 2, 0.25], [lon1, lat1, ow]]);
            g.symbol = S.ocsDown;
          } else {
            g.geometry = pl(up);
            g.symbol = tOut !== null && t >= tOut ? S.ocsOut : S.ocs;
          }
        } });
      }
      if (ocsStatic.length) L.things.add(new Graphic({ geometry: new Polyline({ paths: ocsStatic, hasZ: true, spatialReference: SR }), symbol: S.ocs }));
      // streetlights: topple (wind / tree) or go dark (outage / flooded base)
      for (const [lon, lat, h, tf, dir, tDark, flooded] of d.lights) {
        const darkT = flooded ? d.t[d.gust.indexOf(Math.max(...d.gust))] : tDark;
        toppler({ lon, lat, len: h, tFail: tf, bearing: dir, symUp: S.lightPole, symDown: S.lightDown, head: true,
                  headUp: S.lampOn, headDown: S.lampOff, extraTimes: darkT !== null ? [darkT] : [],
                  headFor: (tt, fallen) => (fallen || (darkT !== null && tt >= darkT) ? S.lampOff : S.lampOn) });
      }
      // traffic signals: heads torn / mast down / dark / flooded cabinet
      const peakT = d.t[d.gust.indexOf(Math.max(...d.gust))];
      for (const [lon, lat, tHead, tStruct, tDark, flooded] of d.signals) {
        const tFlood = flooded ? peakT : null;
        const headFor = (tt) => (tHead !== null && tt >= tHead ? S.sigBad : (tFlood !== null && tt >= tFlood ? S.sigFlood
          : (tDark !== null && tt >= tDark ? S.sigDark : S.sigOk)));
        toppler({ lon, lat, len: d.heights.signal, tFail: tStruct, bearing: 0, symUp: S.sigPole, symDown: S.sigPoleDown, head: true,
                  headUp: S.sigOk, headDown: S.sigBad, extraTimes: [tHead, tDark, tFlood].filter((v) => v !== null), headFor, restPhi: 80 });
      }
      // bus shelters
      for (const [lon, lat, tf] of d.shelters) {
        const g = new Graphic({ geometry: new Point({ longitude: lon, latitude: lat, z: 0, spatialReference: SR }), symbol: S.shelter });
        L.things.add(g);
        if (tf !== null) items.push({ times: [tf], state: 0, apply(s) { g.symbol = s ? S.smashed : S.shelter; } });
      }
      // failed trees: stand until they snap or uproot, then fall along the computed direction
      await buildStanding(new Map(d.trees.map((x) => [x[10], x[6]])));
      for (const row of d.trees) {
        const id = row[10], tf = row[5];
        items.push({ times: [tf], state: 0, apply(s, anim) {
          if (s > 0 && anim) animate((u) => setTreeTilt(id, u * TREE_TILT));
          else setTreeTilt(id, s > 0 ? TREE_TILT : 0);
        } });
      }
      // roof debris flights
      for (const [lon0, lat0, z0, lon1, lat1, z1, t0] of d.debris) {
        const pts = [];
        for (let k = 0; k <= 6; k++) {
          const u = k / 6;
          pts.push([lon0 + (lon1 - lon0) * u, lat0 + (lat1 - lat0) * u, z0 + (z1 - z0) * u * u]);
        }
        const g = new Graphic({ geometry: pl(pts), symbol: S.debris, visible: false });
        L.things.add(g);
        items.push({ times: [t0], state: 0, apply(s) { g.visible = s > 0; } });
      }
      t = -1;
      items.forEach((it) => { it.state = -1; });
      setTime(0);
    }

    // ------------------------------------------------------------ panel
    const panel = document.createElement("section");
    panel.className = "destruction";
    panel.setAttribute("aria-label", "Storm destruction playback");
    panel.innerHTML = `
      <header><strong>Destruction</strong><select aria-label="Scenario"></select></header>
      <div class="destruction-row">
        <button type="button" class="destruction-play" aria-label="Play">▶</button>
        <input type="range" min="0" max="21600" step="60" value="0" aria-label="Storm time" />
        <output class="destruction-clock">T+0:00</output>
      </div>
      <dl class="destruction-stats"></dl>
      <div class="destruction-cams"><button type="button" data-cam="worst">Worst block</button><button type="button" data-cam="power">Power cascade</button><button type="button" data-hide>Hide</button></div>
      <p class="destruction-note"></p>`;
    document.body.appendChild(panel);
    const sel = panel.querySelector("select"), play = panel.querySelector(".destruction-play"), slider = panel.querySelector("input");
    const clock = panel.querySelector(".destruction-clock"), stats = panel.querySelector(".destruction-stats"), note = panel.querySelector(".destruction-note");

    function setPlaying(p) {
      playing = p;
      if (p && run && t >= run.t[run.t.length - 1]) setTime(0);
      lastFrame = 0;
      play.textContent = p ? "❚❚" : "▶";
      play.setAttribute("aria-label", p ? "Pause" : "Play");
    }
    const count = (arr, f) => arr.reduce((n, x) => n + (f(x) ? 1 : 0), 0);
    const fmt = (n) => n.toLocaleString("en-US");
    function renderPanel() {
      if (!run) return;
      slider.value = String(Math.round(t));
      const k = Math.max(0, run.t.findIndex((x) => x > t) - 1 < 0 ? run.t.length - 1 : run.t.findIndex((x) => x > t) - 1);
      const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60);
      clock.textContent = `T+${h}:${String(m).padStart(2, "0")} · gust ${run.gust[k].toFixed(1)} m/s`;
      const ev = (tt) => tt !== null && t >= tt;
      const rows = [
        ["Trees down", count(run.trees, (x) => ev(x[5]))],
        ["Buildings damaged", count(run.buildings, (x) => ev(x[2][0]))],
        ["Power poles down", count(run.poles, (x) => ev(x[2]))],
        ["Wire spans down", count(run.spans, (x) => ev(x[4])) + count(run.ocs, (x) => ev(x[4]))],
        ["Customers without power", run.customers_t[k] ?? 0],
        ["Muni wire de-energised", `${(run.ocs.reduce((s, x, i) => s + (ev(x[5]) ? ocsLen[i] : 0), 0) / 1000).toFixed(1)} km`],
        ["Signals damaged / dark", `${count(run.signals, (x) => ev(x[2]) || ev(x[3]))} / ${count(run.signals, (x) => ev(x[4]) || x[5])}`],
        ["Streetlights down", count(run.lights, (x) => ev(x[3]))],
        ["Destruction cost (end)", `$${(run.ledger_total_usd / 1e6).toFixed(2)}M`]
      ];
      stats.innerHTML = rows.map(([a, b]) => `<dt>${a}</dt><dd>${typeof b === "number" ? fmt(b) : b}</dd>`).join("");
    }

    async function load(id, index) {
      const entry = index.find((e) => e.id === id) ?? index[0];
      sel.value = entry.id;
      note.textContent = "Loading…";
      setPlaying(false);
      const d = await (await fetch(entry.file)).json();
      await build(d);
      note.textContent = `${d.title}. Wind: ${d.wind_source}. Engine: sim/destruction.py.`;
    }

    play.addEventListener("click", () => setPlaying(!playing));
    slider.addEventListener("input", () => { setPlaying(false); setTime(Number(slider.value)); });
    // Cameras come from the run: the 200 m block with the most destruction, and the first downed power span.
    function focus(kind) {
      if (!run) return;
      let at = null;
      if (kind === "power") {
        const s = run.spans.filter((x) => x[4] !== null).sort((a, b) => a[4] - b[4])[0] ?? run.ocs.filter((x) => x[4] !== null).sort((a, b) => a[4] - b[4])[0];
        if (s) at = [(s[0] + s[2]) / 2, (s[1] + s[3]) / 2];
      } else {
        const bins = new Map();
        const add = (lon, lat, w) => {
          const k = `${Math.round(lon / 0.0022)},${Math.round(lat / 0.0018)}`;
          const b = bins.get(k) ?? { w: 0, lon: 0, lat: 0 };
          b.w += w; b.lon += lon * w; b.lat += lat * w;
          bins.set(k, b);
        };
        run.trees.forEach((x) => add(x[0], x[1], 1));
        run.spans.forEach((x) => x[4] !== null && add(x[0], x[1], 3));
        run.ocs.forEach((x) => x[4] !== null && add(x[0], x[1], 3));
        run.poles.forEach((x) => x[2] !== null && add(x[0], x[1], 3));
        run.buildings.forEach((x) => add(x[4][0], x[4][1], x[1]));
        const best = [...bins.values()].sort((a, b) => b.w - a.w)[0];
        if (best) at = [best.lon / best.w, best.lat / best.w];
      }
      if (!at) return;
      const k = Math.max(0, run.t.findIndex((x) => x >= t));
      const heading = (run.dir_from[k] ?? 200) % 360;   // look downwind, as things fall away from the camera
      view.goTo({ target: new Point({ longitude: at[0], latitude: at[1], spatialReference: SR }), zoom: 19, tilt: 68, heading: (heading + 180) % 360 }, { duration: 2500 });
    }
    panel.querySelectorAll("[data-cam]").forEach((b) => b.addEventListener("click", () => focus(b.dataset.cam)));
    panel.querySelector("[data-hide]").addEventListener("click", (e) => {
      const show = (shown = !group[0].visible);
      group.forEach((l) => { l.visible = show; });
      if (standing) standing.visible = show;
      e.currentTarget.textContent = show ? "Hide" : "Show";
    });

    try {
      const index = await (await fetch("/data/web/destruction_index.json")).json();
      sel.innerHTML = index.map((e) => `<option value="${e.id}">${e.title}</option>`).join("");
      sel.addEventListener("change", () => load(sel.value, index));
      await view.when();
      await load(index[0].id, index);
    } catch (error) {
      note.textContent = "Destruction run could not load: run python3 -m sim.destruction_web.";
      console.error(error);
    }
    window.sfDestruction = { focus, setTime: (v) => setTime(v), play: () => setPlaying(true), load: (id) => fetch("/data/web/destruction_index.json").then((r) => r.json()).then((ix) => load(id, ix)), layers: L, get run() { return run; } };
  });
})();
