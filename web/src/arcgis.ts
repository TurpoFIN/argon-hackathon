// Photoreal base: ArcGIS Maps SDK SceneView (Esri/PLW textured SF buildings + World Imagery + World Elevation)
// with our three.js storm layer injected through a RenderNode that shares the SceneView's camera + depth.
import * as THREE from 'three';
import type { TileFrame } from './geo';

const CDN = 'https://js.arcgis.com/4.33/';
function loadScript(src: string, ms: number) {
  return new Promise<void>((res, rej) => {
    const s = document.createElement('script'); s.src = src; s.async = true;
    const to = setTimeout(() => rej(new Error('timeout ' + src)), ms);
    s.onload = () => { clearTimeout(to); res(); }; s.onerror = () => { clearTimeout(to); rej(new Error('load ' + src)); };
    document.head.appendChild(s);
  });
}
const amd = (mods: string[]) => new Promise<any[]>((res, rej) => (window as any).require(mods, (...a: any[]) => res(a), rej));

export interface ShotCam { lon: number; lat: number; z: number; heading: number; tilt: number }

export interface ArcgisBase {
  view: any; renderer: THREE.WebGLRenderer; camera: THREE.PerspectiveCamera;
  setRender(fn: (r: THREE.WebGLRenderer, cam: THREE.PerspectiveCamera, w: number, h: number) => void): void;
  goTo(c: ShotCam, ms: number): Promise<void>;
  setCamera(c: ShotCam): void;
  setWeather(rain: number, cloud: number, fog: number): void;
  queryElevation(points: [number, number][]): Promise<number[] | null>;
  /** synchronous ground elevation (renderer units) from the loaded terrain, or null */
  groundAt(lon: number, lat: number): number | null;
  /** block ArcGIS's own mouse/keyboard navigation (walk mode drives the camera) */
  setNavBlocked(b: boolean): void;
  ready: Promise<void>;
}

export async function createArcgis(container: HTMLElement, tile: TileFrame, opts: { quality?: string }): Promise<ArcgisBase> {
  const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = CDN + 'esri/themes/dark/main.css'; document.head.appendChild(css);
  await loadScript(CDN, 12000);
  const [EMap, Basemap, TileLayer, SceneLayer, ElevationLayer, SceneView, RenderNode, webgl, SpatialReference, Multipoint, reactiveUtils, Camera, Point] = await amd([
    'esri/Map', 'esri/Basemap', 'esri/layers/TileLayer', 'esri/layers/SceneLayer', 'esri/layers/ElevationLayer', 'esri/views/SceneView',
    'esri/views/3d/webgl/RenderNode', 'esri/views/3d/webgl', 'esri/geometry/SpatialReference', 'esri/geometry/Multipoint', 'esri/core/reactiveUtils', 'esri/Camera', 'esri/geometry/Point',
  ]);
  const buildings = new SceneLayer({
    url: 'https://tiles.arcgis.com/tiles/z2tnIkrLQ2BRzr6P/arcgis/rest/services/SanFrancisco_Bldgs/SceneServer/layers/0',
    title: 'San Francisco textured 3D buildings', copyright: 'San Francisco 3D buildings: Precision LightWorks (PLW) / Esri', popupEnabled: false, legendEnabled: false,
  });
  const imagery = new TileLayer({ url: 'https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer' });
  const elevation = new ElevationLayer({ url: 'https://elevation3d.arcgis.com/arcgis/rest/services/WorldElevation3D/Terrain3D/ImageServer' });
  const map = new EMap({ basemap: new Basemap({ baseLayers: [imagery] }), ground: { layers: [elevation] }, layers: [buildings] });
  const view = new SceneView({
    container, map, qualityProfile: opts.quality || 'high',
    camera: { position: { longitude: -122.3794, latitude: 37.8077, z: 1350 }, heading: 237, tilt: 66 },
    ui: { components: ['attribution'] }, popupEnabled: false,
    highlightOptions: { fillOpacity: 0, haloOpacity: 0 },
    environment: {
      lighting: { type: 'sun', date: new Date('2026-10-26T00:40:00Z'), directShadowsEnabled: false, displayUTCOffset: -7 },
      atmosphereEnabled: true, starsEnabled: false,
      weather: { type: 'rainy', cloudCover: 0.85, precipitation: 0.6 },
    },
    constraints: { altitude: { min: -100, max: 200000 } },
  });
  (window as any).sfScene = { view, map, buildings };
  let navBlocked = false;
  for (const ev of ['drag', 'mouse-wheel', 'double-click', 'key-down', 'hold']) view.on(ev, (e: any) => { if (navBlocked) e.stopPropagation(); });
  const pt = new Point({ longitude: 0, latitude: 0, spatialReference: SpatialReference.WGS84 });

  let renderFn: ((r: THREE.WebGLRenderer, cam: THREE.PerspectiveCamera, w: number, h: number) => void) | null = null;
  const camera = new THREE.PerspectiveCamera();
  camera.matrixAutoUpdate = false; camera.matrixWorldAutoUpdate = false;
  let renderer: THREE.WebGLRenderer = null as any;
  const local = new Float64Array(16), vl = new THREE.Matrix4(), tmp = new THREE.Matrix4();
  let fbo: WebGLFramebuffer | null = null;

  const StormNode = RenderNode.createSubclass({
    constructor() { this.consumes.required.push('composite-color'); this.produces = 'composite-color'; },
    setup() {
      const gl: WebGL2RenderingContext = this.gl;
      renderer = new THREE.WebGLRenderer({ canvas: gl.canvas as HTMLCanvasElement, context: gl, alpha: true, antialias: false });
      renderer.autoClear = false; renderer.setPixelRatio(1);
      renderer.outputColorSpace = THREE.SRGBColorSpace;
      const orig = renderer.setRenderTarget.bind(renderer);
      (renderer as any).setRenderTarget = (target: any, a?: number, b?: number) => { orig(target, a, b); if (target == null && fbo) gl.bindFramebuffer(gl.FRAMEBUFFER, fbo); };
      webgl.renderCoordinateTransformAt(view, [tile.lon0, tile.lat0, 0], SpatialReference.WGS84, local);
    },
    render() {
      const gl: WebGL2RenderingContext = this.gl;
      if (!renderer) this.setup();
      const out = this.bindRenderTarget();
      fbo = gl.getParameter(gl.FRAMEBUFFER_BINDING);
      if (renderFn && renderer) {
        const c = this.camera;
        // view (render coords) x local(ENU at tile centre) computed in float64, then handed to three
        tmp.fromArray(c.viewMatrix as any); vl.fromArray(local as any); tmp.multiply(vl);
        camera.matrixWorldInverse.copy(tmp); camera.matrixWorld.copy(tmp).invert();
        camera.projectionMatrix.fromArray(c.projectionMatrix as any); camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert();
        camera.position.setFromMatrixPosition(camera.matrixWorld);
        const vp = c.viewport; renderer.resetState();
        gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
        renderer.setViewport(vp[0], vp[1], vp[2], vp[3]);
        renderFn(renderer, camera, vp[2], vp[3]);
      }
      this.resetWebGLState();
      this.requestRender();
      return out;
    },
  });
  // eslint-disable-next-line no-new
  new StormNode({ view });

  const ready = (async () => {
    await view.when();
    await Promise.race([reactiveUtils.whenOnce(() => !view.updating), new Promise((r) => setTimeout(r, 15000))]);
  })();

  const toCam = (c: ShotCam) => new Camera({ position: { longitude: c.lon, latitude: c.lat, z: c.z }, heading: c.heading, tilt: c.tilt });
  return {
    view, get renderer() { return renderer; }, camera, ready,
    setRender(fn) { renderFn = fn; },
    goTo(c, ms) { return view.goTo(toCam(c), { duration: ms, easing: 'in-out-cubic' }).catch(() => {}); },
    setCamera(c) { view.camera = toCam(c); },
    setWeather(rain, cloud, fog) {
      const w = view.environment.weather;
      if (fog > 0.5) { if (w.type !== 'foggy') view.environment.weather = { type: 'foggy', fogStrength: fog }; else w.fogStrength = fog; return; }
      if (w.type !== 'rainy') view.environment.weather = { type: 'rainy', cloudCover: cloud, precipitation: rain };
      else { if (Math.abs(w.precipitation - rain) > 0.02) w.precipitation = rain; if (Math.abs(w.cloudCover - cloud) > 0.02) w.cloudCover = cloud; }
    },
    groundAt(lon, lat) {
      try { pt.longitude = lon; pt.latitude = lat; const r = view.groundView?.elevationSampler?.queryElevation(pt); const z = r?.z; return typeof z === 'number' && isFinite(z) ? z : null; } catch { return null; }
    },
    setNavBlocked(b) { navBlocked = b; },
    async queryElevation(points) {
      try {
        const mp = new Multipoint({ points, spatialReference: SpatialReference.WGS84 });
        const r = await elevation.queryElevation(mp, { demResolution: 'finest-contiguous' });
        return r.geometry.points.map((p: number[]) => p[2]);
      } catch (e) { console.warn('elevation query failed', e); return null; }
    },
  } as ArcgisBase;
}
