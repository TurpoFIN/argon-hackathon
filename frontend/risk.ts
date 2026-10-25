import type {Constructor,ScenarioState} from './types';
import {request} from './session';

type Metric={mean:number;low:number;high:number};
type Work={kind:string;target:string;crew:number;work_minutes:number;travel_minutes:number;finish_minutes_before_storm:number};
type Row={id:string;name:string;metrics:Record<string,Metric>;avoided:Record<string,Metric>;receipt:{completed:Work[];skipped:unknown[];crew_minutes:number[]}};
type Report={signature:string;scenario_id:string;parameters:Record<string,unknown>;preparation_minutes:number;selected_id:string;
  selection:{reason:string;tradeoffs:string[]};rows:Row[];coverage:Record<string,number>;assumptions:string[];sources:Array<{name:string;url:string}>};
type State={status:string;progress:string;error:string|null;viewed_id:string|null;report:Report|null;source_stale:boolean};
type Asset={id:string;position:number[];geometry?:{type:string;coordinates:number[][]|number[][][]};name?:string;installation?:string;source?:string};
type Assets={buildings:Asset[];trees:Asset[];roads:Asset[];power_lines:Asset[];substations:Asset[]};
type Frame={time_seconds:number;tree_ids:string[];structure_ids:string[];flooded_ids:string[];unpowered_ids:string[];blocked_route_ids:string[]};
type Replay={signature:string;frames:Frame[]};
interface Graphics {removeAll():void;addMany(items:unknown[]):void;}
const columns:[string,string][]=[['damaged_envelope_m2','Envelope m²'],['trees_failed','Trees'],['partial_collapses','Partial failures'],
  ['flooded_entrances','Wet entries'],['unserved_load_hours','Load-hours lost'],['route_hazard_minutes','Route risk min'],['isolated_building_minutes','Isolation min']];
const css=`
.risk-panel{position:absolute;top:12px;right:12px;z-index:6;width:min(900px,calc(100% - 24px));max-height:calc(100% - 24px);overflow:auto;background:var(--card,#fff);color:var(--foreground,#19252b);border:1px solid var(--border,#bec5c9);border-radius:6px;font:12px/1.5 system-ui,sans-serif}
.scene-decisions .risk-panel{position:fixed}.risk-panel>summary{padding:10px 12px;cursor:pointer;font-weight:500;position:sticky;top:0;background:var(--card,#fff);z-index:2}
.risk-body{padding:0 12px 12px}.risk-body p{margin:7px 0}.risk-body button,.risk-body input{font:inherit;color:inherit;background:var(--card,#fff);border:1px solid var(--border,#bec5c9);border-radius:4px;padding:5px 8px}.risk-body button{cursor:pointer}.risk-body button:disabled{opacity:.55;cursor:wait}.risk-body input{width:70px}.risk-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.risk-scroll{overflow:auto}.risk-table{border-collapse:collapse;min-width:810px;width:100%;font-variant-numeric:tabular-nums}.risk-table th,.risk-table td{padding:4px 6px;text-align:right;border-bottom:1px solid var(--border,#bec5c9);vertical-align:top}.risk-table th:first-child,.risk-table td:first-child{text-align:left;min-width:150px}.risk-table tr[data-selected=true]{background:var(--muted,#edf1f3)}.risk-table small{display:block;white-space:nowrap;font-size:10px;line-height:1.3}.risk-table [data-change=benefit]{color:color-mix(in srgb,var(--foreground,#19252b) 78%,#19aa66)}.risk-table [data-change=harm]{color:color-mix(in srgb,var(--foreground,#19252b) 65%,#e04444)}.risk-body a{color:inherit;text-decoration:underline}.risk-body details{padding:7px 0}.risk-body li{margin:4px 0}.risk-notice{border-left:3px solid var(--border,#aaa);padding-left:8px}.risk-live{min-height:18px}.risk-error{color:color-mix(in srgb,var(--foreground,#19252b) 65%,#e04444)}body:has(.risk-panel[open]) .decision-trace{display:none}.risk-legend{font-size:11px;color:var(--muted-foreground,#68747c)}@media(max-width:600px){.risk-panel{position:fixed;top:56px;left:12px;right:12px;width:auto;max-height:calc(100dvh - 76px)}.scene-decisions .risk-panel{top:64px}}
`;

/** One server-owned selection and clock for both real map views. */
export async function riskView(host:HTMLElement,map:{add(layer:unknown):void},onFlood:(value:Float32Array|undefined)=>void){
  if(!document.querySelector('#risk-style')){const style=document.createElement('style');style.id='risk-style';style.textContent=css;document.head.append(style);}
  const [Layer,Graphic]=await new Promise<Constructor[]>((resolve,reject)=>window.require(['esri/layers/GraphicsLayer','esri/Graphic'],(...m)=>resolve(m as Constructor[]),reject));
  const layer=new Layer({title:'Preparedness outcomes and sourced power lines',elevationInfo:{mode:'relative-to-ground',offset:2}}) as Graphics;map.add(layer);
  const panel=document.createElement('details');panel.className='risk-panel';panel.id='risk-panel';panel.open=true;
  const title=document.createElement('summary');title.textContent='Damage and risk · preventive-action experiments';panel.append(title);
  const body=document.createElement('div');body.className='risk-body';panel.append(body);host.append(panel);
  const el=(tag:string,text:string)=>{const n=document.createElement(tag);n.textContent=text;return n;};
  const controls=document.createElement('div');controls.className='risk-actions';
  const minutes=document.createElement('input');minutes.type='number';minutes.min='30';minutes.max='1440';minutes.value='120';minutes.setAttribute('aria-label','Preparation minutes');
  const run=document.createElement('button');run.textContent='Have Argon test actions';
  const base=document.createElement('button');base.textContent='Show original storm';
  const power=document.createElement('input');power.type='checkbox';power.checked=true;power.style.width='auto';power.id='risk-power-lines';
  const label=document.createElement('label');label.htmlFor=power.id;label.append(power,document.createTextNode(' Power lines'));
  controls.append(el('span','Preparation (min)'),minutes,run,base,label);
  const live=el('p','Loading experiments…');live.className='risk-live';live.setAttribute('role','status');
  const content=document.createElement('div');body.append(controls,live,content);
  let app:ScenarioState|undefined,server:State|undefined,assets:Assets|undefined,replay:Replay|undefined,viewId:string|undefined,key='',generation=0,lastFrame=-1,lastTable='',active=false;
  let bmap=new Map<string,Asset>(),tmap=new Map<string,Asset>(),rmap=new Map<string,Asset>();
  let pending=false;
  const count=(n:number)=>n.toLocaleString(undefined,{maximumFractionDigits:n<100?1:0});
  const planName=(name:string)=>name.replace(/^\d+_?/,'').replaceAll('_',' ');
  function matches(){return !!server?.report&&!server.source_stale&&!!app&&server.report.scenario_id===app.scenario_id;}
  function render(){
    const s=server,r=s?.report;run.disabled=s?.status==='running';minutes.disabled=run.disabled;
    live.className='risk-live'+(s?.error?' risk-error':'');
    live.textContent=s?.error??(s?.status==='running'?s.progress+(r?' · previous result shown below':''):r?(matches()?'Same storm as map · 2 general crews + 3 specialists · matched capacity cases':'Recorded experiment differs from the current storm or model. Run again to compare.'):'No experiments yet. Run Argon to compare all six preventive-action families.');
    if(!r)return;
    const stamp=r.signature+'|'+String(viewId);if(stamp===lastTable)return;lastTable=stamp;
    content.replaceChildren();
    content.append(el('p','Conditional simulation: asset strengths and condition are assumed. These are scenario comparisons, not predictions of actual failures.'));
    content.append(el('p',`Argon recommends: ${planName(r.rows.find(x=>x.id===r.selected_id)?.name??'')}. Select a row to replay.`));
    content.append(el('p','Means of three what-if cases, not a probability forecast. Arrows show non-zero changes from no preparation; ↑ means worse.'));
    const scroll=document.createElement('div');scroll.className='risk-scroll';const table=document.createElement('table');table.className='risk-table';table.setAttribute('aria-label','Matched preventive action damage and risk comparison');
    const head=document.createElement('tr');head.append(el('th','Portfolio / replay'));columns.forEach(([,label])=>head.append(el('th',label)));table.append(head);
    for(const row of r.rows){
      const tr=document.createElement('tr');tr.dataset.selected=String(row.id===viewId);const name=document.createElement('td');const button=document.createElement('button');button.textContent=(row.id===r.selected_id?'Argon: ':'')+planName(row.name);button.setAttribute('aria-pressed',String(row.id===viewId));button.onclick=()=>void request('/api/risk/select',{plan_id:row.id}).then(poll).catch(showError);name.append(button,el('small',`${row.receipt.completed.length} completed · ${row.receipt.skipped.length} skipped`));tr.append(name);
      for(const [metric] of columns){const value=row.metrics[metric],change=row.avoided[metric];const td=el('td',count(value.mean));td.title=`Outcome range ${count(value.low)}–${count(value.high)}; avoided range ${count(change.low)}–${count(change.high)}`;const delta=el('small',Math.abs(change.mean)<.005?'':`${change.mean>0?'↓':'↑'} ${count(Math.abs(change.mean))}`);delta.dataset.change=change.mean>.005?'benefit':change.mean<-.005?'harm':'none';td.append(delta);tr.append(td);}
      table.append(tr);
    }
    scroll.append(table);content.append(scroll);
    const viewed=r.rows.find(x=>x.id===viewId);
    content.append(el('p',viewed?`Map replay: ${viewed.name}, central capacity case. Flood surface and outcome markers follow the shared storm clock.`:'Map shows the original storm. Select a portfolio to replay its coupled outcomes.'));
    const legend=el('p','Red ×: partial structural failure · amber: fallen tree · blue: flooded entry · violet: power unavailable · thick lines: blocked routes · thin grey: sourced power routes');legend.className='risk-legend';content.append(legend);
    const actions=document.createElement('details');actions.append(el('summary','Completed work and tradeoffs'));
    actions.append(el('p',r.selection.reason));
    for(const item of viewed?.receipt.completed??[])actions.append(el('p',`${item.kind.replaceAll('_',' ')} · ${item.target} · crew ${item.crew} · ${item.work_minutes} min work + ${count(item.travel_minutes)} min travel · finished ${count(item.finish_minutes_before_storm)} min before storm`));
    r.selection.tradeoffs.forEach(t=>actions.append(el('p',t)));content.append(actions);
    const sources=document.createElement('details');sources.append(el('summary','Power-line sources, coverage and model assumptions'));
    sources.append(el('p',`${r.coverage.distribution_feeders} PG&E feeders, ${r.coverage.transmission_lines} CEC transmission lines, ${r.coverage.substations} PG&E substations. ${r.coverage.overhead_lines} confirmed overhead lines; ${r.coverage.unknown_installation} routes have unknown installation type.`));
    sources.append(el('p','PG&E routes are public planning data. Customer connections are unverified. The model never treats an unknown route as an overhead line.'));
    for(const src of r.sources){const p=document.createElement('p'),a=document.createElement('a');a.href=src.url;a.target='_blank';a.rel='noopener noreferrer';a.textContent=src.name;p.append(a);sources.append(p);}
    for(const text of r.assumptions)sources.append(el('p',text));content.append(sources);
  }
  function showError(e:unknown){live.textContent=e instanceof Error?e.message:String(e);live.className='risk-live risk-error';}
  async function sync(){
    const r=server?.report;const wanted=matches()?(server?.viewed_id==='argon'?r?.selected_id:server?.viewed_id):null;
    viewId=wanted??undefined;render();
    const next=wanted&&r?r.signature+'/'+wanted:'';if(next===key)return;key=next;const turn=++generation;replay=undefined;lastFrame=-1;layer.removeAll();
    if(!next){if(active)onFlood(undefined);active=false;delete document.documentElement.dataset.riskPlan;return;}
    const [rep,binary]=await Promise.all([request<Replay>('/api/risk/replay/'+wanted),fetch('/api/risk/flood/'+next).then(async x=>{if(!x.ok)throw Error('Portfolio water replay unavailable');return x.arrayBuffer();})]);
    if(turn!==generation)return;if(rep.signature!==r!.signature)throw Error('Experiment changed while loading; reload selection');replay=rep;lastFrame=-1;active=true;onFlood(new Float32Array(binary));document.documentElement.dataset.riskPlan=wanted!;document.documentElement.dataset.riskSignature=r!.signature;
  }
  async function poll(){if(pending)return;pending=true;try{server=await request<State>('/api/risk');await sync();}catch(e){showError(e);}finally{pending=false;}}
  run.onclick=()=>{const value=Number(minutes.value);if(!Number.isInteger(value)||value<30||value>1440){showError('Use 30–1440 preparation minutes');return;}void request('/api/risk/run',{preparation_minutes:value}).then(poll).catch(showError);};
  base.onclick=()=>void request('/api/risk/select',{plan_id:null}).then(poll).catch(showError);
  power.onchange=()=>{lastFrame=-1;};
  try{assets=await request<Assets>('/api/risk/assets');bmap=new Map(assets.buildings.map(b=>[b.id,b]));tmap=new Map(assets.trees.map(b=>[b.id,b]));rmap=new Map(assets.roads.map(b=>[b.id,b]));}catch(e){showError(e);}
  function line(asset:Asset,color:string,width:number){if(!asset.geometry)return null;const paths=asset.geometry.type==='LineString'?[asset.geometry.coordinates]:asset.geometry.coordinates;return new Graphic({geometry:{type:'polyline',paths,spatialReference:{wkid:4326}},symbol:{type:'simple-line',color,width},attributes:{id:asset.id,source:asset.source,installation:asset.installation},popupTemplate:{title:asset.name??asset.id,content:`${asset.source??'DataSF'} · ${asset.installation??'route'}`}});}
  function point(asset:Asset,color:string,style='circle'){return new Graphic({geometry:{type:'point',longitude:asset.position[0],latitude:asset.position[1],spatialReference:{wkid:4326}},symbol:{type:'simple-marker',style,color,size:style==='x'?11:5,outline:{color,width:1}},attributes:{id:asset.id}});}
  void poll();setInterval(()=>void poll(),1700);
  return {status(s:ScenarioState){app=s;void sync().catch(showError);},draw(seconds:number){
    const index=Math.min(30,Math.floor(seconds/120));if(index===lastFrame||!assets)return;lastFrame=index;const items:unknown[]=[];
    if(power.checked)for(const asset of assets.power_lines){const item=line(asset,asset.installation==='overhead'?'#bd7951':'#879199',1);if(item)items.push(item);}
    const frame=replay?.frames[index];if(frame){
      for(const id of frame.tree_ids){const a=tmap.get(id);if(a)items.push(point(a,'#bd8c31'));}
      for(const id of frame.flooded_ids){const a=bmap.get(id);if(a)items.push(point(a,'#477ca0'));}
      for(const id of frame.unpowered_ids){const a=bmap.get(id);if(a)items.push(point(a,'#9a69b7'));}
      for(const id of frame.structure_ids){const a=bmap.get(id);if(a)items.push(point(a,'#ca4a43','x'));}
      for(const id of frame.blocked_route_ids){const a=rmap.get(id);if(a){const item=line(a,'#c77848',3);if(item)items.push(item);}}
      document.documentElement.dataset.riskFrame=String(index);document.documentElement.dataset.riskTrees=String(frame.tree_ids.length);document.documentElement.dataset.riskStructures=String(frame.structure_ids.length);
    }
    layer.removeAll();layer.addMany(items);document.documentElement.dataset.riskPowerLines=String(power.checked?assets.power_lines.length:0);
  }};
}
