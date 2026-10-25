import type {Constructor, Manifest, Parameters, ScenarioState, WorldData} from './types';
import {followState,loadResult,request,timeAt} from './session';
import {worldLayer,frameAt} from './world';

type Field='flood'|'wind'|'rain';
interface Map2D {
  when():Promise<Map2D>;
  toScreen(point:unknown):{x:number;y:number}|null;
  toMap(point:{x:number;y:number}):{longitude:number;latitude:number}|null;
  on(event:string,callback:(event:{x:number;y:number})=>void):unknown;
}
const $=<T extends HTMLElement>(selector:string):T=>document.querySelector<T>(selector)!;
const defaults:Parameters={rain_mm_h:32,wind_mps:11.3,gust_mps:19.3,wind_from_deg:138,coastal_rise_m:1,drain_capacity_lps:6,blockage_pct:65,runoff_coefficient:.9,roughness:.035,debris_kg:600,clearing_crews:2,response_mode:'argon'};
document.documentElement.dataset.theme=matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';
let state:ScenarioState,manifest:Manifest,grid:Float32Array,flood:Float32Array,loadedId='';
let field:Field='flood',dirty=false,view:Map2D,count=0,nx=0,ny=0,selectedCell=0;
let GeoPoint:Constructor;
const form=$<HTMLFormElement>('#parameters'),fields=$<HTMLFieldSetElement>('#parameter-fields');
const banner=$<HTMLParagraphElement>('#run-status'),canvas=$<HTMLCanvasElement>('#field'),ctx=canvas.getContext('2d')!;
const raster=document.createElement('canvas'),rctx=raster.getContext('2d')!;
let redrawRaster=true,lastFrame=-1,lastDraw=0;
let world:WorldData|undefined,assets:Awaited<ReturnType<typeof worldLayer>>;
let editVersion=0,sending=false,sendAgain=false;
let updateTimer:ReturnType<typeof setTimeout>|undefined;

function showError(error:unknown):void {banner.textContent=error instanceof Error?error.message:String(error);banner.dataset.error='true';}
function fillForm(values:Parameters):void {
  $<HTMLInputElement>('#gust_mps').min=String(values.wind_mps);
  for(const [key,value] of Object.entries({...defaults,...values}))$<HTMLInputElement>(`#${key}`).value=String(value);
  syncSliders();
}
function syncSliders():void {
  const mean=$<HTMLInputElement>('#wind_mps'),gust=$<HTMLInputElement>('#gust_mps');
  const gustValue=gust.valueAsNumber;
  gust.min=mean.value;
  gust.value=String(Math.max(mean.valueAsNumber,gustValue));
  fields.querySelectorAll<HTMLInputElement>('input[type=range]').forEach(slider=>{
    const value=slider.valueAsNumber,precision=slider.step.split('.')[1]?.length??0;
    const formatted=value.toFixed(precision);
    $<HTMLOutputElement>(`#${slider.id}-value`).value=formatted;
    slider.setAttribute('aria-valuetext',`${formatted} ${slider.dataset.unit??''}`.trim());
    slider.style.setProperty('--range-progress',`${(value-Number(slider.min))/(Number(slider.max)-Number(slider.min))*100}%`);
  });
}
function readForm():Parameters {
  return Object.fromEntries(Object.keys(defaults).map(key=>[key,key==='response_mode'?$<HTMLSelectElement>('#response_mode').value:$<HTMLInputElement>(`#${key}`).valueAsNumber])) as unknown as Parameters;
}
function labelState(next:ScenarioState):void {
  const computing=next.status==='computing';
  fields.disabled=false;
  if(dirty)return;
  if(computing){banner.textContent='Updating simulation… you can keep editing.';banner.dataset.error='false';}
  else if(next.status==='failed'){showError(next.error??'Simulation failed; previous result retained.');return;}
  else {banner.textContent=`Live · scenario ${next.revision} synced to both views.`;banner.dataset.error='false';}
  fillForm(next.pending_parameters??next.parameters);
  $('#draft-state').textContent=computing?'Updating':'';
}

async function sendLatest():Promise<void> {
  if(sending){sendAgain=true;return;}
  if(!form.checkValidity()){banner.textContent='Finish entering valid values to update.';banner.dataset.error='false';return;}
  const values=readForm();
  if(values.gust_mps<values.wind_mps){showError('Gust speed must be at least the mean wind speed.');return;}
  const submittedVersion=editVersion;sending=true;
  banner.textContent='Updating simulation…';banner.dataset.error='false';
  try{
    const next=await request<ScenarioState>('/api/scenarios',values);
    if(submittedVersion===editVersion)dirty=false;
    state=next;labelState(next);
  }catch(error){showError(error);}
  finally{
    sending=false;
    if(sendAgain||editVersion!==submittedVersion){sendAgain=false;queueUpdate();}
  }
}
function queueUpdate():void {
  // Throttle while dragging, rather than postponing every update until release.
  if(updateTimer===undefined)updateTimer=setTimeout(()=>{updateTimer=undefined;void sendLatest();},200);
}
function scheduleUpdate():void {
  syncSliders();dirty=true;editVersion++;
  $('#draft-state').textContent='Updating';banner.textContent='Updating simulation…';banner.dataset.error='false';
  queueUpdate();
}
form.addEventListener('input',scheduleUpdate);
function flushUpdate():void {clearTimeout(updateTimer);updateTimer=undefined;void sendLatest();}
form.addEventListener('change',flushUpdate);
form.addEventListener('submit',event=>{event.preventDefault();flushUpdate();});
$('#restore').addEventListener('click',()=>{fillForm(defaults);scheduleUpdate();});
document.querySelectorAll<HTMLButtonElement>('[data-field]').forEach(button=>button.addEventListener('click',()=>{
  field=button.dataset.field as Field;
  document.querySelectorAll('[data-field]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));
  redrawRaster=true;updateLegend();document.documentElement.dataset.selectedField=field;
}));
$('#vectors').addEventListener('change',()=>{redrawRaster=true;});

async function playback(update:unknown):Promise<void> {
  try{state=await request<ScenarioState>('/api/playback',update);}catch(error){showError(error);}
}
$('#play').addEventListener('click',()=>{void playback({playing:!state.playback.playing});});
$('#speed').addEventListener('change',()=>{void playback({speed:Number($<HTMLSelectElement>('#speed').value)});});
let scrubTimer:ReturnType<typeof setTimeout>;
$<HTMLInputElement>('#time').addEventListener('input',()=>{
  const seconds=Number($<HTMLInputElement>('#time').value);
  state.playback={...state.playback,time_seconds:seconds,anchor_ms:Date.now(),playing:false};
  clearTimeout(scrubTimer);scrubTimer=setTimeout(()=>{void playback({time_seconds:seconds,playing:false});},120);
});

function updateLegend():void {
  $('#legend-title').textContent=field==='flood'?'Flood depth · m':field==='wind'?'Wind speed · m/s':'Rain intensity · mm/h';
  $('#legend-max').textContent=field==='flood'?'≥2 m':field==='wind'?'≥30 m/s':'≥100 mm/h';
  $('#legend-gradient').style.background=field==='flood'?'linear-gradient(90deg,#dceef7,#1084cb,#034063)':field==='wind'?'linear-gradient(90deg,#fff0c7,#eeb443,#ad4b1c)':'linear-gradient(90deg,#eee8fc,#9b83d7,#533a99)';
}
function surfaceTime():{frame:number;blend:number;seconds:number} {
  const seconds=timeAt(state.playback),frame=Math.min(Math.floor(seconds/120),manifest.times_seconds.length-2);
  return {seconds,frame,blend:(seconds%120)/120};
}
function depthAt(i:number,frame:number,blend:number):number {return flood[frame*count+i]*(1-blend)+flood[(frame+1)*count+i]*blend;}
function mapPoint(i:number):{longitude:number;latitude:number} {return {longitude:grid[i],latitude:grid[count+i]};}
function screenPoint(i:number):{x:number;y:number}|null {return view.toScreen(new GeoPoint({...mapPoint(i),spatialReference:{wkid:4326}}));}

function makeRaster(frame:number,blend:number):void {
  const image=rctx.createImageData(nx,ny);
  for(let i=0;i<count;i++) {
    if(grid[count*6+i]>.5||grid[count*3+i]>2)continue;
    let value=0,t=0,r=0,g=0,b=0;
    if(field==='flood') {value=depthAt(i,frame,blend);t=Math.min(value/2,1);r=16-13*t;g=150-86*t;b=215-116*t;}
    if(field==='wind') {value=Math.hypot(grid[count*4+i],grid[count*5+i]);t=Math.min(value/30,1);r=247-74*t;g=212-137*t;b=124-96*t;}
    if(field==='rain') {value=manifest.rainfall_mm_per_hour;t=Math.min(value/100,1);r=170-87*t;g=151-93*t;b=224-71*t;}
    if(value<.025)continue;
    const offset=i*4;image.data[offset]=r;image.data[offset+1]=g;image.data[offset+2]=b;image.data[offset+3]=Math.min(220,85+135*t);
  }
  rctx.putImageData(image,0,0);redrawRaster=false;
}

function draw():void {
  requestAnimationFrame(draw);
  if(!state||!manifest||!view||document.hidden||performance.now()-lastDraw<70)return;
  lastDraw=performance.now();
  const {seconds,frame,blend}=surfaceTime(),frameKey=Math.floor(seconds/10);
  if(frameKey!==lastFrame){redrawRaster=true;lastFrame=frameKey;}
  if(redrawRaster)makeRaster(frame,blend);
  const width=canvas.clientWidth,height=canvas.clientHeight,dpr=devicePixelRatio||1;
  if(canvas.width!==Math.round(width*dpr)||canvas.height!==Math.round(height*dpr)){canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);}
  ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,width,height);
  const a=screenPoint(0),b=screenPoint(nx-1),c=screenPoint((ny-1)*nx);
  if(!a||!b||!c)return;
  const ux=(b.x-a.x)/(nx-1),uy=(b.y-a.y)/(nx-1),vx=(c.x-a.x)/(ny-1),vy=(c.y-a.y)/(ny-1);
  document.documentElement.dataset.fieldWidthPixels=String(Math.round(Math.hypot(b.x-a.x,b.y-a.y)));
  ctx.save();ctx.setTransform(dpr*ux,dpr*uy,dpr*vx,dpr*vy,dpr*(a.x-ux/2-vx/2),dpr*(a.y-uy/2-vy/2));
  ctx.imageSmoothingEnabled=false;ctx.drawImage(raster,0,0);ctx.restore();
  if($<HTMLInputElement>('#vectors').checked){
    ctx.strokeStyle=field==='wind'?'#69420c':'#c78b20';ctx.lineWidth=1.3;
    const spacing=Math.max(5,Math.ceil(42/Math.hypot(ux,uy)));
    for(let y=2;y<ny-2;y+=spacing)for(let x=2;x<nx-2;x+=spacing){
      const i=y*nx+x;if(grid[count*3+i]>2)continue;
      const u=grid[count*4+i],v=grid[count*5+i],speed=Math.hypot(u,v);if(speed<.2)continue;
      const px=a.x+ux*x+vx*y,py=a.y+uy*x+vy*y;
      const dx=ux*u-vx*v,dy=uy*u-vy*v,length=Math.hypot(dx,dy),scale=Math.min(20,8+speed*.45)/length;
      const tx=dx*scale,ty=dy*scale;
      ctx.beginPath();ctx.moveTo(px-tx/2,py-ty/2);ctx.lineTo(px+tx/2,py+ty/2);
      ctx.lineTo(px+tx/2-tx*.28-ty*.22,py+ty/2-ty*.28+tx*.22);
      ctx.moveTo(px+tx/2,py+ty/2);ctx.lineTo(px+tx/2-tx*.28+ty*.22,py+ty/2-ty*.28-tx*.22);ctx.stroke();
    }
  }
  const time=`${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(Math.floor(seconds%60)).padStart(2,'0')}`;
  $('#clock').textContent=time;
  if(document.activeElement!==$('#time'))$<HTMLInputElement>('#time').value=String(Math.floor(seconds));
  $('#play').textContent=state.playback.playing?'Pause':'Play';
  $<HTMLSelectElement>('#speed').value=String(state.playback.speed);
  let wet=0,max=0;
  for(let i=0;i<count;i++)if(grid[count*6+i]<.5&&grid[count*3+i]<=2){const d=depthAt(i,frame,blend);if(d>.05)wet++;max=Math.max(max,d);}
  $('#flood-area').textContent=`Flooded ${(wet*manifest.grid.cell_m**2/1e6).toFixed(3)} km²`;
  $('#max-depth').textContent=`Max depth ${max.toFixed(2)} m`;
  const p=mapPoint(selectedCell),depth=depthAt(selectedCell,frame,blend),wind=Math.hypot(grid[count*4+selectedCell],grid[count*5+selectedCell]);
  let inspected=field==='flood'?depth.toFixed(2)+' m water':field==='wind'?wind.toFixed(1)+' m/s wind':manifest.rainfall_mm_per_hour.toFixed(1)+' mm/h rain';
  if(field==='flood'&&grid[count*6+selectedCell]>.5)inspected='Open water · coastal boundary';
  else if(field==='flood'&&grid[count*3+selectedCell]>2)inspected='Building interior not modeled';
  $('#probe').textContent=`${p.latitude.toFixed(4)}, ${p.longitude.toFixed(4)} · ${inspected}`;
  document.documentElement.dataset.analysisFrame=String(frame);
  assets?.draw(seconds);
  if(world){
    const current=frameAt(world,seconds),event=world.events.filter(e=>e.time_seconds<=seconds).at(-1);
    $('#world-metrics').textContent=`${current.metrics.blocked_drains} blocked inlets · ${current.crews.length} crews · ${current.metrics.cleared_kg.toFixed(1)} kg cleared`;
    $('#world-event').textContent=event?`${Math.floor(event.time_seconds/60)}m · ${event.kind.replaceAll('_',' ')}${event.drain_id?' · inlet '+event.drain_id:''}${event.crew?' · '+event.crew:''}`:'No events yet';
  }
}

async function receive(next:ScenarioState):Promise<void> {
  if(state&&(next.request_version??0)<(state.request_version??0))return;
  state=next;labelState(next);
  if(loadedId!==next.scenario_id){
    const data=await loadResult(next.scenario_id);manifest=data.manifest;grid=data.grid;flood=data.flood;world=data.world;assets?.set(world);loadedId=next.scenario_id;
    nx=manifest.grid.nx;ny=manifest.grid.ny;count=nx*ny;
    if(grid.length!==count*7||flood.length!==count*manifest.times_seconds.length)throw new Error('Scenario data dimensions do not match');
    raster.width=nx;raster.height=ny;
    selectedCell=0;let closest=Infinity;
    for(let i=0;i<count;i++)if(grid[count*6+i]<.5&&grid[count*3+i]<=2){
      const distance=(grid[i]+122.3955)**2+(grid[count+i]-37.7948)**2;
      if(distance<closest){closest=distance;selectedCell=i;}
    }
    redrawRaster=true;
    $('#source-counts').textContent=`${(manifest.metrics as unknown as {building_records:number}).building_records.toLocaleString()} building records · ${(manifest.metrics as unknown as {drain_records:number}).drain_records.toLocaleString()} drain records · ${manifest.grid.cell_m} m terrain grid.`;
    document.documentElement.dataset.scenarioId=loadedId;
    document.documentElement.dataset.rainMmH=String(manifest.rainfall_mm_per_hour);
    $('#engine-status').textContent=world?(world.planner_status.includes('unavailable')||world.planner_status.includes('failed')?'Argon not connected · baseline response':world.planner_status):'Coupled simulation will start on the next parameter change.';
    $('#perception-status').textContent=world?`${world.source_mode}. ${world.perception.status==='ready'?`${world.perception.zones?.length??0} candidate zones from georeferenced imagery. ${world.perception.limitation??''}`:world.perception.reason??'Perception disabled.'}`:'Public GIS assets loaded.';
    $('#world-details').textContent=world?world.assumptions.join(' '):'';
    $('#play').removeAttribute('disabled');
  }
}

async function main():Promise<void> {
  const modules=await new Promise<unknown[]>((resolve,reject)=>window.require(['esri/Map','esri/Basemap','esri/layers/TileLayer','esri/views/MapView','esri/geometry/Point'],(...m)=>resolve(m),reject));
  const [Map,Basemap,TileLayer,View,Point]=modules as [Constructor,Constructor,Constructor,Constructor<Map2D>,Constructor];
  GeoPoint=Point;
  const map=new Map({basemap:new Basemap({baseLayers:[new TileLayer({url:'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer'})],referenceLayers:[new TileLayer({url:'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Reference/MapServer'})]})});
  assets=await worldLayer(map as {add(layer:unknown):void},false);
  view=new View({container:'map',map,center:[-122.396,37.797],zoom:15.2,rotation:0,ui:{components:['attribution','zoom']},constraints:{rotationEnabled:false,minZoom:13,maxZoom:20},popupEnabled:false});
  await Promise.all([view.when(),receive(await request<ScenarioState>('/api/state'))]);
  view.on('pointer-move',event=>{
    const p=view.toMap(event);if(!p)return;
    // Solve the local affine grid coordinates from three real georeferenced cells.
    const a=mapPoint(0),b=mapPoint(nx-1),c=mapPoint((ny-1)*nx);
    const ux=(b.longitude-a.longitude)/(nx-1),uy=(b.latitude-a.latitude)/(nx-1),vx=(c.longitude-a.longitude)/(ny-1),vy=(c.latitude-a.latitude)/(ny-1);
    const dx=p.longitude-a.longitude,dy=p.latitude-a.latitude,det=ux*vy-uy*vx;
    const x=Math.round((dx*vy-dy*vx)/det),y=Math.round((dy*ux-dx*uy)/det);
    if(x>=0&&x<nx&&y>=0&&y<ny)selectedCell=y*nx+x;
  });
  $('#map-status').hidden=true;fields.disabled=false;
  document.documentElement.dataset.analysisReady='true';updateLegend();
  followState(receive,showError);draw();
}
main().catch(error=>{showError(error);$('#map-status').textContent='Analytical map unavailable. '+(error instanceof Error?error.message:String(error));});
