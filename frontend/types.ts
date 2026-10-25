export interface Manifest {
  name: string;
  grid: {nx: number; ny: number; cell_m: number; bands: string[]};
  times_seconds: number[];
  rainfall_mm_per_hour: number;
  wind: {mean_mps: number; gust_mps: number; from_deg: number};
  metrics: {wet_land_cells: number; max_land_depth_m: number; mass_balance_error_m3: number};
  assumptions: string[];
  sources: Record<string, string>;
  parameters?: Parameters;
  frame_metrics?: Array<{time_seconds: number; max_depth_m: number; flooded_area_m2: number; water_volume_m3: number}>;
}
export interface Parameters {
  rain_mm_h:number; wind_mps:number; gust_mps:number; wind_from_deg:number;
  coastal_rise_m:number; drain_capacity_lps:number; blockage_pct:number;
  runoff_coefficient:number; roughness:number;
}
export interface Playback {time_seconds:number; playing:boolean; speed:number; anchor_ms:number;}
export interface ScenarioState {
  scenario_id:string; revision:number; parameters:Parameters; pending_parameters?:Parameters;
  status:'ready'|'computing'|'failed'; error:string|null; playback:Playback;
}
export interface Layer { load(): Promise<Layer>; }
export interface SceneView {
  when(): Promise<SceneView>;
  whenLayerView(layer: Layer): Promise<{updating: boolean}>;
  updating: boolean;
  fatalError: Error | null;
  map: {ground: {queryElevation(points: unknown, options: Record<string, unknown>): Promise<{geometry: {points: number[][]}}>} };
}
export interface RenderHost {
  gl: WebGL2RenderingContext;
  camera: {viewMatrix: number[]; projectionMatrix: number[]};
  consumes: {required: string[]};
  produces: string;
  bindRenderTarget(): unknown;
  resetWebGLState(): void;
  requestRender(): void;
}
export interface RenderNodeConstructor {
  createSubclass(properties: Record<string, unknown>): new (properties: {view: SceneView}) => RenderHost;
}
export interface WebGLCoordinates {
  toRenderCoordinates(view: SceneView, source: number[] | Float64Array, sourceOffset: number, sr: {wkid: number}, destination: Float64Array | Float32Array, destinationOffset: number, count: number): Float64Array | Float32Array;
}
export type Constructor<T = unknown> = new (properties: Record<string, unknown>) => T;
export interface Reactive {
  whenOnce(predicate: () => boolean): Promise<void>;
  watch(getter: () => unknown, callback: (value: Error | null) => void): void;
}
declare global {
  interface Window {
    require: (modules: string[], loaded: (...modules: unknown[]) => void, failed?: (error: Error) => void) => void;
    storm: {manifest: Manifest; view: SceneView; frame: number; renderedFrames: number; wetVertices: number; windSegments: number; elapsedSeconds: number};
  }
}
