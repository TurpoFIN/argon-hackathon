import type {Manifest, ScenarioState, Playback, WorldData} from './types';

export async function request<T>(url:string, body?:unknown):Promise<T> {
  const r=await fetch(url,body===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!r.ok) {
    const data=await r.json().catch(()=>({detail:'Request failed'}));
    throw new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));
  }
  return r.json() as Promise<T>;
}
export async function loadResult(id:string):Promise<{manifest:Manifest;grid:Float32Array;flood:Float32Array;world?:WorldData}> {
  const root=`/api/results/${id}/`;
  async function binary(name:string):Promise<Float32Array> {
    const r=await fetch(root+name,{cache:'no-store'});
    if(!r.ok)throw new Error(`Could not load ${name}`);
    return new Float32Array(await r.arrayBuffer());
  }
  const [manifest,grid,flood]=await Promise.all([request<Manifest>(root+'manifest.json'),binary('grid.bin'),binary('flood.bin')]);
  const world=manifest.world_file?await request<WorldData>(root+manifest.world_file):undefined;
  return {manifest,grid,flood,world};
}
export function timeAt(p:Playback,now=Date.now()):number {
  const time=p.time_seconds+(p.playing?(now-p.anchor_ms)/1000*p.speed:0);
  return Math.max(0,Math.min(time%3600,3599.99));
}
export function followState(callback:(state:ScenarioState)=>Promise<void>|void,onError:(error:unknown)=>void):()=>void {
  let stopped=false;
  async function poll():Promise<void> {
    try {await callback(await request<ScenarioState>('/api/state'));}catch(error){onError(error);}
    if(!stopped)setTimeout(poll,1000);
  }
  void poll();
  return ()=>{stopped=true;};
}
