import type {Constructor, Layer, Manifest, Reactive, RenderNodeConstructor, SceneView, WebGLCoordinates} from './types';
import {installStormRenderer} from './renderer';

const status = document.querySelector<HTMLParagraphElement>('#status')!;
function fail(error: unknown): void {
  console.error(error);
  status.hidden = false;
  status.textContent = 'Storm data could not load. Check the connection and reload.';
}
async function fetchChecked(path: string): Promise<Response> {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${path}: ${response.status}`);
  return response;
}
async function main(): Promise<void> {
  const [manifest, gridBuffer, floodBuffer] = await Promise.all([
    fetchChecked('/api/manifest').then(r => r.json() as Promise<Manifest>),
    fetchChecked('/api/grid.bin').then(r => r.arrayBuffer()),
    fetchChecked('/api/flood.bin').then(r => r.arrayBuffer())
  ]);
  const modules = await new Promise<unknown[]>((resolve, reject) => window.require([
    'esri/Map', 'esri/Basemap', 'esri/layers/TileLayer', 'esri/layers/SceneLayer',
    'esri/layers/ElevationLayer', 'esri/views/SceneView', 'esri/core/reactiveUtils',
    'esri/views/3d/webgl/RenderNode', 'esri/views/3d/webgl', 'esri/geometry/Multipoint'
  ], (...loaded) => resolve(loaded), reject));
  const [Map, Basemap, TileLayer, SceneLayer, ElevationLayer, View, reactive, RenderNode, coordinates, Multipoint] = modules as [Constructor, Constructor, Constructor<Layer>, Constructor<Layer>, Constructor, Constructor<SceneView>, Reactive, RenderNodeConstructor, WebGLCoordinates, Constructor];
  const buildings = new SceneLayer({
    url: 'https://tiles.arcgis.com/tiles/z2tnIkrLQ2BRzr6P/arcgis/rest/services/SanFrancisco_Bldgs/SceneServer/layers/0',
    copyright: 'Buildings: PLW / Esri · Terrain: USGS · Storm inputs: NOAA / DataSF · Compound scenario, not a forecast',
    popupEnabled: false
  });
  const map = new Map({
    basemap: new Basemap({baseLayers: [new TileLayer({url: 'https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer'})]}),
    ground: {layers: [new ElevationLayer({url: 'https://elevation3d.arcgis.com/arcgis/rest/services/WorldElevation3D/Terrain3D/ImageServer'})]},
    layers: [buildings]
  });
  const view = new View({
    container: 'scene', map, qualityProfile: 'high', ui: {components: ['attribution']}, popupEnabled: false,
    camera: {position: {longitude: -122.3818, latitude: 37.8005, z: 690}, heading: 245, tilt: 64},
    environment: {lighting: {date: new Date('2023-01-05T20:00:00Z'), directShadowsEnabled: false}, weather: {type: 'cloudy', cloudCover: 0.72}, atmosphereEnabled: true, starsEnabled: false},
    constraints: {altitude: {min: 20, max: 5000000}}
  });
  window.storm = {manifest, view, frame: 0, renderedFrames: 0, wetVertices: 0, windSegments: 0, elapsedSeconds: 0};
  await view.when();
  status.textContent = 'Loading storm surface…';
  const grid = new Float32Array(gridBuffer);
  const count = manifest.grid.nx * manifest.grid.ny;
  if (grid.length !== count * 7 || floodBuffer.byteLength !== count * manifest.times_seconds.length * 4) {
    throw new Error('Simulation payload does not match the grid contract');
  }
  const points = Array.from({length: count}, (_, i) => [grid[i], grid[count + i]]);
  // Sample the actual display terrain; apply physical depth above that terrain.
  // This avoids mixing renderer EGM96 heights with hydrology's NAVD88 heights.
  const sampled = await view.map.ground.queryElevation(new Multipoint({points, spatialReference: {wkid: 4326}}), {demResolution: 'finest-contiguous', noDataValue: -99999});
  if (sampled.geometry.points.some(p => !Number.isFinite(p[2]) || p[2] === -99999)) throw new Error('Display terrain has missing elevations');
  installStormRenderer(view, RenderNode, coordinates, manifest, grid, new Float32Array(floodBuffer), sampled.geometry.points);
  const layerView = await view.whenLayerView(buildings);
  await reactive.whenOnce(() => !layerView.updating);
  status.hidden = true;
  document.documentElement.dataset.stormReady = 'true';
  reactive.watch(() => view.fatalError, error => {if (error) fail(error);});
}
main().catch(fail);
