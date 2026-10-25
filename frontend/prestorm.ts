import {workEvidence, workFacts} from './workEvidence';
import {preventiveWorkView, type WorkCandidate, type SelectedWork, type WorkComparison} from './preventiveWork';
import {type ActionPlanning} from './actionPlan';
import type {Constructor, ScenarioState} from './types';
import {consequenceScene} from './consequenceScene';

type Hazard='wind'|'trees'|'structure'|'flood'|'power'|'routes';
type Role='general'|'arborist'|'building'|'electrician'|'traffic';
type Task={site_id:string;condition:'unchecked'|'concern'|'clear';note:string;minutes:number|null;prerequisites_confirmed:boolean};
type Plan={resources:Record<Role|'lead_minutes',number|null>;tasks:Record<Hazard,Task>};
type Site={id:string;label:string;position:number[];basis:string;source_id:string;report_ids?:string[];dates?:string[]};
type Evidence={id:string;label:string;role:Role;check:string;action:string;benefit:string;needs:string[];source_ids:string[];validation:string;validation_detail:string;transfer:string};
type Source={id:string;title:string;url:string;published:string;locator:string;kind:string;finding:string};
type Row={hazard:Hazard;role:Role;mode:string;status:string;start:number|null;finish:number|null;team?:number};
type State={preventive_work:WorkComparison;action_planning:ActionPlanning;version:number;updated_at:string|null;plan:Plan;rows:Row[];review_current:boolean;review?:{order:Hazard[];model:string;reasons:Record<Hazard,string>};evidence:{hazards:Evidence[];sources:Source[];incidents:Array<{position:number[];kind:string}>;validation:{method:string}};sites:Record<Hazard,Site[]>;weather:Record<string,number|string>;coverage:Record<string,number>;power_sources:Source[];job?:{status:string;error:string|null}};
type MapView={goTo(target:unknown,options?:unknown):Promise<unknown>;toScreen?(point:unknown):{x:number;y:number}|null;container?:HTMLElement;whenLayerView?(layer:unknown):Promise<unknown>};
const names:Record<Role,string>={general:'General crews',arborist:'Arborists',building:'Building professionals',electrician:'Electricians',traffic:'Traffic teams'};
const reasons:Record<string,string>={current_concern:'Current concern recorded',historical_incident:'Historical incident at this location',inspection_gap:'Current inspection needed',dependency:'Depends on flooding, power or access'};
const checks:Record<Hazard,string>={wind:'Check glazing and loose items',trees:'Arrange an arborist check',structure:'Arrange an engineer review',flood:'Check inlets and entry paths',power:'Test the complete backup chain',routes:'Check primary and alternative access'};
const hazardNames:Record<Hazard,string>={wind:'Wind',trees:'Trees',structure:'Structures',flood:'Flooding',power:'Power',routes:'Routes'};

const css=`
/* The planner is also embedded in scene shells that do not import the theme. */
@import url('/tokens.css');
body.planning-mode{--plan-width:480px}.planning-mode .controls,.planning-mode .decision-trace{display:none!important}.planning-mode .workspace{grid-template-columns:minmax(0,1fr);margin-left:var(--plan-width)}
.planning-mode #scene{left:var(--plan-width);width:calc(100% - var(--plan-width));top:56px;height:calc(100% - 56px)}.planning-mode .view-switch{left:auto;right:12px;top:10px}.planning-mode:not(.research-mode) .map-toolbar,.planning-mode:not(.research-mode) .results-row,.planning-mode:not(.research-mode) .world-row,.planning-mode:not(.research-mode) .timeline,.planning-mode:not(.research-mode) .legend,.planning-mode:not(.research-mode) #field{display:none!important}
.planning-mode.research-mode .controls{display:block!important}.planning-mode.research-mode .workspace{grid-template-columns:270px minmax(0,1fr)}
.plan-panel{position:fixed;top:56px;bottom:0;left:0;width:var(--plan-width);z-index:8;overflow:auto;background:var(--card);color:var(--foreground);border-right:1px solid var(--border);font:13px/1.5 system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:20px 20px 32px;box-sizing:border-box}
.plan-panel h1,.plan-panel h2,.plan-panel h3{font-weight:400;line-height:1.3;margin:0}.plan-panel h1{font-size:22px}.plan-panel h2{font-size:16px}.plan-panel h3{font-size:14px}.plan-panel p{margin:8px 0}.plan-panel small,.plan-muted{font-size:12px;color:var(--muted-foreground)}
.plan-panel button,.plan-panel select,.plan-panel input,.plan-panel textarea{font:inherit;color:inherit;border:1px solid var(--border);background:var(--card);box-sizing:border-box}.plan-panel button{border-radius:999px;padding:5px 12px;cursor:pointer;min-height:32px}.plan-panel button:hover{background:var(--muted)}.plan-panel button:disabled{opacity:.5;cursor:wait}.plan-panel input,.plan-panel select{height:32px;border-radius:6px;padding:4px 8px;min-width:0;width:100%}.plan-panel textarea{resize:vertical;width:100%;border-radius:6px;padding:8px;min-height:72px}.plan-panel input[type=checkbox]{width:16px;height:16px;flex:none}
.plan-panel a{color:inherit;text-decoration:underline;text-underline-offset:2px}.plan-panel :is(button,input,select,textarea,summary,a):focus-visible{outline:2px solid var(--foreground);outline-offset:2px}.plan-panel details{border:0;border-top:1px solid var(--border);padding:12px 0}.plan-panel summary{cursor:pointer;font-size:13px}.plan-panel details[open]>summary{margin-bottom:12px}.plan-panel label{display:flex;flex-direction:column;align-items:stretch;font-size:12px;gap:4px;margin:10px 0}.plan-panel .check-label{flex-direction:row;align-items:flex-start;gap:8px}.plan-panel .plan-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.plan-row>*{min-width:0}.plan-setup{display:grid;grid-template-columns:1fr 1fr;gap:0 12px}.plan-setup label:first-child{grid-column:1/-1}
.plan-context{padding:12px 0;border-bottom:1px solid var(--border)}.plan-heading{display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin:20px 0 8px}.plan-list{display:grid;gap:0}.plan-item{display:block!important;text-align:left;border:0!important;border-radius:0!important;border-bottom:1px solid var(--border)!important;padding:10px 8px!important;width:100%;height:auto!important;background:transparent!important}.plan-item[aria-pressed=true]{background:var(--muted)!important;border-left:3px solid var(--foreground)!important;padding-left:5px!important}.plan-item strong{font-weight:500}.plan-item span{display:block}.plan-item .plan-item-top{display:flex;justify-content:space-between;gap:12px}.plan-item .plan-item-top small{white-space:nowrap}.plan-detail{border-top:1px solid var(--border);margin-top:16px;padding-top:16px}.plan-detail ul{margin:8px 0;padding-left:20px}.plan-detail li{margin:4px 0}.plan-benefit{padding:10px 12px;border-left:2px solid var(--border);background:var(--muted);margin:12px 0}.plan-benefit strong{font-weight:500}.plan-error{color:var(--danger-700,#c05252)}.plan-status{font-size:12px;min-height:18px}.plan-actions{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}.plan-plan{border-top:1px solid var(--border);margin-top:20px;padding-top:16px}.plan-map-note{position:fixed;z-index:5;top:68px;right:14px;background:var(--card);color:var(--foreground);border:1px solid var(--border);border-radius:6px;padding:8px 12px;max-width:290px;font:12px/1.5 system-ui;pointer-events:none}.plan-map-note strong{font-weight:500;display:block}.plan-scene-title{position:fixed;left:20px;top:14px;color:var(--foreground);font:14px system-ui;z-index:6}.plan-source{border-top:1px solid var(--border);padding:10px 0}.plan-source p{font-size:12px}.plan-inline{font-size:12px}
@media(max-width:1100px){body.planning-mode{--plan-width:420px}.plan-panel{padding:16px}.plan-map-note{max-width:220px}.planning-mode.research-mode .workspace{grid-template-columns:minmax(0,1fr)}.planning-mode.research-mode .controls{display:none!important}}
@media(max-width:767px){body.planning-mode{--plan-width:100%}.plan-panel{top:42vh;bottom:0;width:100%;border-top:1px solid var(--border);border-right:0;padding:16px}.planning-mode .workspace{margin-left:0;height:calc(42vh - 56px);flex:none}.planning-mode #scene{left:0;width:100%;height:calc(42vh - 56px)}.plan-map-note{top:64px;right:8px;max-width:210px;font-size:11px;padding:6px 8px}.planning-mode .app-header{padding:0 12px}.app-header .muted{display:none}.plan-panel input,.plan-panel select,.plan-panel textarea{font-size:16px}.plan-scene-title{left:12px;font-size:13px}.plan-item .plan-item-top small{white-space:normal;text-align:right}.planning-mode.research-mode .timeline{display:none!important}}

/* The map is the workspace. Planning controls are an optional sheet. */
.planning-mode .workspace{margin-left:0;grid-template-columns:minmax(0,1fr)}
.planning-mode #scene{left:0;width:100%;top:56px;height:calc(100% - 56px)}
.planning-mode .plan-panel{display:none;top:116px;left:16px;bottom:24px;width:400px;max-width:calc(100vw - 32px);border:1px solid var(--border);border-radius:8px;padding:16px;box-shadow:0 8px 32px #0003;z-index:15}
.planning-mode.plan-open .plan-panel{display:block}
.plan-tools{position:fixed;z-index:16;left:140px;right:112px;top:12px;display:flex;gap:6px;align-items:center;font:12px system-ui}
.plan-tools button,.plan-map-note button{font:inherit;color:var(--foreground);background:var(--card);border:1px solid var(--border);border-radius:6px;padding:4px 8px;min-height:28px;cursor:pointer}
.plan-tools button{white-space:nowrap}.plan-tools :is(button,select):focus-visible{outline:2px solid var(--foreground);outline-offset:2px}
.planning-mode .view-switch,.planning-mode .app-header nav{position:fixed;left:auto;right:12px;top:12px;z-index:17;padding:2px;gap:2px;border-radius:6px;background:var(--card);border-color:var(--border);font:12px system-ui}
.planning-mode .view-switch a,.planning-mode .app-header nav a{padding:4px 8px;border-radius:4px;color:var(--foreground);line-height:20px}
.planning-mode .app-header .muted{display:none}
.plan-pin-layer{position:fixed;inset:0;z-index:7;pointer-events:none;overflow:hidden}
.plan-pin{position:absolute;pointer-events:auto;text-align:left;display:flex;align-items:center;gap:6px;background:var(--card);color:var(--foreground);border:1px solid var(--border);border-radius:6px;padding:5px 7px;max-width:200px;min-height:28px;height:auto;font:11px/1.3 system-ui;box-shadow:0 2px 8px #0002;cursor:pointer}
.plan-pin strong{font-size:11px;flex:none;min-width:14px;font-weight:500}.plan-pin span{display:block}.plan-pin small{display:block;color:var(--muted-foreground);font-size:10px;margin-top:2px}.plan-pin[aria-pressed=true]{border:2px solid #215f89;padding:4px 6px}.plan-pin[data-pending=true]{border-style:dashed}
.plan-action-select,.plan-hazard-select{min-width:0;border:1px solid var(--border);border-radius:6px;background:var(--card);color:var(--foreground);font:12px system-ui;padding:4px 6px;height:30px}.plan-action-select{flex:1;width:240px;max-width:400px}.plan-hazard-select{width:122px;flex:none}.plan-pin[hidden],.plan-pin-line[hidden]{display:none}.plan-pin-line{position:absolute;height:1px;background:#215f89;transform-origin:0 0;pointer-events:none}
.planning-mode .plan-map-note{display:block;top:auto;right:auto;left:16px;bottom:24px;max-width:none;width:360px;max-height:calc(100vh - 150px);overflow:auto;pointer-events:auto;padding:14px;border-radius:8px;box-shadow:0 6px 24px #0003;z-index:9;font:12px/1.4 system-ui}
.plan-map-note[hidden]{display:none!important}.plan-map-note .plan-card-close{position:absolute;right:8px;top:8px;padding:0;min-height:28px;width:28px;height:28px;font-size:20px;line-height:1;border:0;background:transparent}.plan-map-note h2{padding-right:22px;font-size:16px;font-weight:400;margin:4px 0}.plan-map-note p{margin:6px 0}.plan-map-note .work-evidence-inline{display:grid;grid-template-columns:70px minmax(0,1fr);gap:6px 10px;margin:12px 0;font-size:12px;line-height:1.4}.plan-map-note dt{color:var(--muted-foreground)}.plan-map-note dd{margin:0}.plan-map-note a{color:inherit;text-decoration:underline;text-underline-offset:2px}.plan-map-note strong{display:inline;font-weight:500}.plan-card-controls{display:flex;gap:6px;justify-content:space-between;margin-top:12px}.plan-card-kicker,.plan-card-meta{font-size:11px;color:var(--muted-foreground)}.plan-card-site{font-size:13px}.plan-map-note details{margin-top:8px;border-top:1px solid var(--border);padding-top:8px}.plan-map-note summary{font-size:12px;cursor:pointer}
.planning-mode.plan-open .plan-map-note{display:none}
@media(max-width:767px){.planning-mode .workspace{height:calc(100vh - 56px);margin-left:0}.planning-mode #scene{height:calc(100% - 56px)}.plan-tools{left:8px;top:64px}.plan-tools small{display:none}.planning-mode .plan-panel{left:8px;top:106px;bottom:12px;max-width:calc(100vw - 16px)}.planning-mode .plan-map-note{left:8px!important;right:8px!important;top:auto!important;bottom:60px!important;width:auto;max-height:58vh;padding:12px}.plan-pin{max-width:158px;font-size:11px}.plan-pin small{font-size:10px}.plan-pin>span>span{display:none}.plan-pin>span>span:first-child{display:block;max-width:100px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.plan-pin>span>small{display:none}}

.plan-comparison-tabs{display:flex;flex:none;gap:2px;padding:2px;border:1px solid var(--border);border-radius:6px;background:var(--card)}
.plan-tools .plan-comparison-tabs button{border:0;background:transparent;min-height:24px;padding:3px 8px;border-radius:4px}.plan-comparison-tabs button[aria-pressed=true]{background:var(--muted);box-shadow:inset 0 0 0 1px var(--border)}
.plan-site-actions{display:flex;flex-wrap:wrap;gap:4px;margin:8px 0}.plan-site-actions button{font-size:11px;padding:3px 6px;min-height:24px}.plan-site-actions [aria-pressed=true]{background:var(--muted);border-color:var(--foreground)}
.plan-pin:not([aria-pressed=true]){width:28px;height:28px;min-height:28px;padding:0;border-radius:50%;justify-content:center}.plan-pin:not([aria-pressed=true])>span{display:none}.plan-pin-line[data-selected=false]{display:none}
.plan-ui-toggle{position:fixed;z-index:30;left:12px;bottom:24px;height:28px;padding:0 10px;border:1px solid var(--border,#59636d);border-radius:6px;background:var(--card,#10151a);color:var(--foreground,#f4f4f4);font:12px system-ui;cursor:pointer}.plan-ui-toggle:focus-visible{outline:2px solid var(--foreground,#fff);outline-offset:2px}
.planning-mode .plan-map-note,.planning-mode .plan-panel,.planning-mode .plan-pin{background:var(--card,#10151a);color:var(--foreground,#f4f4f4)}
.plan-ui-hidden :is(.plan-tools,.plan-map-note,.plan-panel,.plan-pin-layer,.plan-scene-title,.view-switch,.app-header,.cinema-toggle,.cinema-dock,.cinema-frame){display:none!important}
.planning-mode.plan-ui-hidden #scene{top:0;height:100%}.planning-mode.plan-ui-hidden .workspace{height:100vh}
.work-before-after{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:12px}.work-before-after>div{border:1px solid var(--border);border-radius:6px;padding:10px;min-width:0}.work-before-after h3{font-size:12px!important;font-weight:500!important;margin:0 0 6px!important}.work-before-after p{font-size:13px;line-height:1.4;margin:0 0 10px}.work-before-after small{font-size:11px;line-height:1.4;color:var(--muted-foreground);display:block}.work-comparison-qualifier{font-size:11px!important;color:var(--muted-foreground);margin:6px 0!important}
html[data-map-comparison=before] .work-state-before,html[data-map-comparison=after] .work-state-after{border-color:var(--foreground);background:var(--muted)}
html[data-map-comparison=before] .plan-pin-line{background:#70757b}html[data-map-comparison=before] .plan-pin[aria-pressed=true]{border-color:#70757b}
@media(max-width:1100px){.plan-tools{left:12px;right:12px;top:64px}.planning-mode .plan-panel{top:106px}}
@media(max-width:767px){.plan-tools{left:8px;right:8px;max-width:none;gap:4px;flex-wrap:wrap}.plan-tools button{font-size:12px}.plan-action-select{max-width:none;width:180px;flex:1;order:1}.plan-hazard-select{width:120px;order:1}.plan-comparison-tabs{order:0}.plan-tools>button{order:0}.plan-tools .plan-comparison-tabs button{padding:3px 6px}.planning-mode .plan-panel{top:138px}.work-before-after>div{padding:8px}.work-before-after p{font-size:12px}}
`;

export async function planningView(_host:HTMLElement,map:{add(layer:unknown):void},onFlood:(value:Float32Array|undefined)=>void){
  document.body.classList.add('planning-mode');document.title='Embarcadero · Pre-storm plan';
  if(!document.documentElement.dataset.theme)document.documentElement.dataset.theme=getComputedStyle(document.documentElement).colorScheme.includes('dark')?'dark':'light';
  const style=document.createElement('style');style.textContent=css;document.head.append(style);
  document.querySelector('.app-header .muted')?.replaceChildren(document.createTextNode('Pre-storm planning'));
  for(const [href,label,title] of [['/','3D','Three-dimensional storm view'],['/analysis','Map','Plan and map view']]){const nav=document.querySelector<HTMLAnchorElement>(`nav a[href="${href}"]`);if(nav){nav.textContent=label;nav.title=title;nav.setAttribute('aria-label',title);}}
  if(document.querySelector('#scene')){const title=document.createElement('div');title.className='plan-scene-title';title.textContent='Embarcadero';document.body.append(title);}
  const panel=document.createElement('aside');panel.className='plan-panel';panel.setAttribute('aria-label','Pre-storm action plan');panel.innerHTML='<h1>Before the storm</h1><p>What to do, where, and why.</p><p role="status">Loading evidence and locations…</p>';document.body.append(panel);
  const mapNote=document.createElement('section');mapNote.className='plan-map-note';mapNote.setAttribute('aria-label','Selected map action');mapNote.textContent='Loading work sites…';document.body.append(mapNote);
  const pinLayer=document.createElement('div');pinLayer.className='plan-pin-layer';pinLayer.setAttribute('aria-label','Actions at mapped sites');document.body.append(pinLayer);
  const mapTools=document.createElement('div');mapTools.className='plan-tools';document.body.append(mapTools);
  let planningLayer:{visible:boolean}|undefined;
  const chromeToggle=document.createElement('button');chromeToggle.type='button';chromeToggle.className='plan-ui-toggle';document.body.append(chromeToggle);
  const setChrome=(hidden:boolean)=>{document.body.classList.toggle('plan-ui-hidden',hidden);if(planningLayer)planningLayer.visible=!hidden;chromeToggle.textContent=hidden?'Show UI':'Hide UI';chromeToggle.setAttribute('aria-label',hidden?'Show planning controls':'Hide planning controls');chromeToggle.setAttribute('aria-pressed',String(hidden));chromeToggle.title=(hidden?'Show':'Hide')+' planning controls (H)';try{sessionStorage.setItem('plan-ui-hidden',String(hidden));}catch{/* Storage can be disabled. */}};
  chromeToggle.onclick=()=>setChrome(!document.body.classList.contains('plan-ui-hidden'));
  document.addEventListener('keydown',event=>{if(!event.repeat&&event.key.toLowerCase()==='h'&&!event.ctrlKey&&!event.metaKey&&!event.altKey&&!(event.target instanceof Element&&event.target.closest('input,textarea,select,[contenteditable=true]'))){event.preventDefault();chromeToggle.click();}});
  let chromeHidden=false;try{chromeHidden=sessionStorage.getItem('plan-ui-hidden')==='true';}catch{/* Start with controls visible. */}setChrome(chromeHidden);

  const [Layer,Graphic,Point]=await new Promise<Constructor[]>((resolve,reject)=>window.require(['esri/layers/GraphicsLayer','esri/Graphic','esri/geometry/Point'],(...m)=>resolve(m as Constructor[]),reject));
  const layer=new Layer({title:'Historical reports and preparation locations',elevationInfo:{mode:'relative-to-ground',offset:2}}) as {visible:boolean;removeAll():void;addMany(a:unknown[]):void};planningLayer=layer;layer.visible=!chromeHidden;map.add(layer);
  const consequences=await consequenceScene(map,Boolean(document.querySelector('#scene')));
  let state:State|undefined,selected:Hazard='wind',draft:Plan|undefined,dirty=false,busy=false,polling=false,view:MapView|undefined,showPower=false,showHistory=false,app:ScenarioState|undefined,lastStamp='';
  type Focus=WorkCandidate&Partial<SelectedWork>;
  let focusedAction:Focus|undefined;
  let cardClosed=false;
  let overview=true;
  let modelDetail=false;
  let mapMode:'before'|'after'='after';
  let hazardFilter:Hazard|'all'='all';
  let pinLayoutKey='',lastPinFrame=0;
  let pins:Array<{element:HTMLButtonElement;line:HTMLElement;point:unknown;ids:string[]}>=[];
  let powerLines:Array<{geometry:{type:string;coordinates:unknown};installation:string}>=[];
  const text=(tag:string,value:string,cls='')=>{const el=document.createElement(tag);el.textContent=value;el.className=cls;return el;};
  const button=(value:string,fn:()=>void)=>{const el=text('button',value) as HTMLButtonElement;el.type='button';el.onclick=fn;return el;};
  function link(s:Source){const a=text('a',s.title) as HTMLAnchorElement;a.href=s.url;a.target='_blank';a.rel='noreferrer';return a;}
  function sourceFor(id:string){return state!.evidence.sources.find(s=>s.id===id)!;}
  function siteFor(h:Hazard){return state!.sites[h].find(s=>s.id===draft!.tasks[h].site_id)!;}
  function announce(message:string,error=false){const el=panel.querySelector('.plan-status');if(el){el.textContent=message;el.classList.toggle('plan-error',error);}}
  function changed(){dirty=true;announce('Unsaved changes. Save to update both views and the schedule.');}
  function selectedEvidence(){return state!.evidence.hazards.find(h=>h.id===selected)!;}
  function allWork(value=state):Focus[]{
    if(!value)return [];
    const planned=value.preventive_work.current?value.preventive_work.selected:[];
    const byId=new Map(planned.map(work=>[work.id,work]));
    return value.preventive_work.candidates.map<Focus>(work=>byId.get(work.id)??work).sort((a,b)=>(a.priority??Infinity)-(b.priority??Infinity));
  }
  function mapWork():Focus[]{return allWork().filter(work=>hazardFilter==='all'||work.hazard===hazardFilter);}
  async function focusWork(work:Focus){
    const position=await consequences.anchor(work);
    if(focusedAction?.id!==work.id||!modelDetail)return;
    const target=new Point({longitude:position[0],latitude:position[1],z:position[2],spatialReference:{wkid:4326}});
    if(view)void view.goTo({target,scale:1800,tilt:50},{animate:false}).catch(()=>{});
  }
  function chooseWork(work:Focus){if(hazardFilter!=='all'&&hazardFilter!==work.hazard)hazardFilter='all';cardClosed=false;focusedAction=work;draw();document.body.classList.remove('plan-open');updateTools();if(modelDetail)void focusWork(work);}
  function updateTools(){
    const open=document.body.classList.contains('plan-open');
    const comparison=text('div','','plan-comparison-tabs');comparison.setAttribute('role','group');comparison.setAttribute('aria-label','Map comparison');
    for(const [value,label] of [['before','No action'],['after','With action']] as const){const tab=button(label,()=>{mapMode=value;draw();});tab.setAttribute('aria-pressed',String(mapMode===value));comparison.append(tab);}
    const all=allWork(),visible=mapWork();
    const filter=document.createElement('select');filter.className='plan-hazard-select';filter.setAttribute('aria-label','Filter actions by hazard');
    for(const [id,label] of [['all','All'],...Object.entries(hazardNames)]){const option=document.createElement('option');option.value=id;option.textContent=`${label} (${id==='all'?all.length:all.filter(w=>w.hazard===id).length})`;filter.append(option);}filter.value=hazardFilter;
    filter.onchange=()=>{hazardFilter=filter.value as Hazard|'all';if(!mapWork().some(w=>w.id===focusedAction?.id))focusedAction=mapWork()[0];cardClosed=false;draw();};
    const select=document.createElement('select');select.setAttribute('aria-label','Action to compare');select.className='plan-action-select';
    const addGroup=(label:string,works:Focus[])=>{if(!works.length)return;const group=document.createElement('optgroup');group.label=`${label} (${works.length})`;for(const work of works){const option=document.createElement('option');option.value=work.id;option.textContent=(work.priority!==undefined?`${work.priority}. `:'')+(workFacts[work.operation_id]?.name??work.label)+' · '+work.site.label+(!work.feasibility.fits?' · needs resources':'');group.append(option);}select.append(group);};
    addGroup('Scheduled',visible.filter(w=>w.priority!==undefined));
    for(const [hazard,label] of Object.entries(hazardNames))addGroup(label+' alternatives',visible.filter(w=>w.hazard===hazard&&w.priority===undefined));
    select.value=focusedAction?.id??'';select.disabled=!visible.length;select.onchange=()=>{const work=allWork().find(w=>w.id===select.value);if(work)chooseWork(work);};
    const detail=button(modelDetail?'Area':'Detail',()=>{modelDetail=!modelDetail;overview=!modelDetail;draw();if(modelDetail&&focusedAction)void focusWork(focusedAction);else if(view&&pins.length)void view.goTo({target:pins.map(p=>p.point)},{animate:false}).catch(()=>{});});detail.title=modelDetail?'Return to neighborhood':'Show illustrative 3D consequences';detail.setAttribute('aria-label',detail.title);detail.setAttribute('aria-pressed',String(modelDetail));
    const plan=button(open?'Close':'Plan',()=>{document.body.classList.toggle('plan-open');updateTools();});plan.title='Resources and scheduled work';plan.setAttribute('aria-expanded',String(open));
    const sites=button('⤢',()=>{modelDetail=false;overview=true;draw();if(view&&pins.length)void view.goTo({target:pins.map(p=>p.point)},{animate:false}).catch(()=>{});});sites.title='Show all visible sites';sites.setAttribute('aria-label',sites.title);
    mapTools.replaceChildren(comparison,filter,select,detail,plan,sites);

  }
  function renderMapCard(){
    if(!state||!draft)return;
    mapNote.hidden=cardClosed;if(cardClosed)return;
    const work=focusedAction;mapNote.replaceChildren();
    const close=button('×',()=>{cardClosed=true;mapNote.hidden=true;pinLayoutKey='';positionPins();document.querySelector<HTMLButtonElement>('.plan-pin[aria-pressed=true]')?.focus();});close.className='plan-card-close';close.setAttribute('aria-label','Close action details');mapNote.append(close);
    if(!work){mapNote.append(text('h2','Choose work for this storm'),text('p','Set resources and generate a plan.'),button('Resources & plan',()=>{document.body.classList.add('plan-open');updateTools();}));return;}
    const planned=work.priority!==undefined;
    mapNote.dataset.workId=work.id;
    mapNote.append(text('div',(planned?`Priority ${work.priority}`:work.feasibility.fits?'Alternative · fits resources':'Alternative · needs resources')+' · illustrative outcome','plan-card-kicker'),text('h2',workFacts[work.operation_id]?.name??work.label),text('p',work.site.label,'plan-card-site'),text('p',planned?`${work.assignment} · ${work.people} people · +${work.start}–${work.finish} min`:`${work.people}-person ${work.role} crew · ${work.feasibility.minimum_total}–${work.feasibility.maximum_total} min`,'plan-card-meta'));
    if(['backup-pump','fit-barrier','sandbag-wall','clear-inlet','move-contents'].includes(work.operation_id))mapNote.append(text('p',modelDetail?'Ground-level cutaway · assumed indoor conditions; layout unknown':'Assumed indoor conditions · layout unknown','plan-card-meta'));
    mapNote.append(workEvidence(work,state.preventive_work,draft.tasks,true));
    const sameSite=mapWork().filter(w=>w.site.id===work.site.id);
    if(sameSite.length>1){const actions=text('div','','plan-site-actions');actions.setAttribute('role','group');actions.setAttribute('aria-label','Other actions at this site');for(const action of sameSite){const choice=button(workFacts[action.operation_id]?.name??action.label,()=>chooseWork(action));choice.setAttribute('aria-pressed',String(action.id===work.id));actions.append(choice);}mapNote.append(actions);}
  }
  function positionPins(){
    if(!view?.toScreen)return;
    const now=performance.now();if(now-lastPinFrame<50)return;lastPinFrame=now;
    const screens=pins.map(p=>view!.toScreen!(p.point));
    const key=JSON.stringify([innerWidth,innerHeight,mapNote.offsetHeight,focusedAction?.id,document.body.classList.contains('plan-open'),screens.map(s=>s?[Math.round(s.x),Math.round(s.y)]:null)]);
    if(key===pinLayoutKey)return;pinLayoutKey=key;
    const rect=view.container?.getBoundingClientRect()??document.querySelector('#scene,#map')!.getBoundingClientRect();
    const toolbar=mapTools.getBoundingClientRect();
    const boxes:Array<{x:number;y:number;w:number;h:number}>=[{x:toolbar.left,y:toolbar.top,w:toolbar.width,h:toolbar.height}];
    if(!cardClosed&&innerWidth>767){
      const w=mapNote.offsetWidth,h=mapNote.offsetHeight,left=innerWidth-w-16,top=120;
      mapNote.style.left=left+'px';mapNote.style.top=top+'px';mapNote.style.bottom='auto';boxes.push({x:left,y:top,w,h});
    }
    for(const [index,pin] of pins.entries()){
      const screen=screens[index];const x=(screen?.x??-9999)+rect.left,y=(screen?.y??-9999)+rect.top;
      const visible=overview&&Number.isFinite(x)&&Number.isFinite(y)&&x>0&&x<innerWidth&&y>56&&y<innerHeight-10;
      pin.element.hidden=!visible;pin.line.hidden=!visible;if(!visible)continue;
      const w=pin.element.offsetWidth,h=pin.element.offsetHeight;
      if(pin.element.getAttribute('aria-pressed')!=='true'){pin.element.style.left=(x-w/2)+'px';pin.element.style.top=(y-h/2)+'px';continue;}
      const choices:number[][]=[];for(let step=0;step<8;step++)for(const dx of [16,-w-16])for(const dy of [-h-14-step*(h+12),16+step*(h+12)])choices.push([dx,dy]);
      const fit=choices.map(([dx,dy])=>({x:Math.max(6,Math.min(innerWidth-w-6,x+dx)),y:Math.max(108,Math.min(innerHeight-h-12,y+dy)),w,h})).find(b=>!boxes.some(a=>b.x<a.x+a.w+8&&b.x+b.w+8>a.x&&b.y<a.y+a.h+8&&b.y+b.h+8>a.y))??{x:Math.max(6,Math.min(innerWidth-w-6,x+16)),y:Math.max(108,Math.min(innerHeight-h-12,y-h-14)),w,h};
      pin.element.style.left=fit.x+'px';pin.element.style.top=fit.y+'px';boxes.push(fit);
      const endX=Math.max(fit.x,Math.min(fit.x+w,x)),endY=Math.max(fit.y,Math.min(fit.y+h,y));
      pin.line.style.left=x+'px';pin.line.style.top=y+'px';pin.line.style.width=Math.hypot(endX-x,endY-y)+'px';pin.line.style.transform=`rotate(${Math.atan2(endY-y,endX-x)}rad)`;
    }
  }
  function draw(){
    if(!state||!draft)return;const items:unknown[]=[];
    if(showHistory)for(const p of state.evidence.incidents)items.push(new Graphic({geometry:{type:'point',longitude:p.position[0],latitude:p.position[1]},symbol:{type:'simple-marker',size:4,color:'#8d9296',outline:{width:0}}}));
    if(showPower)for(const p of powerLines){const coordinates=p.geometry.coordinates;items.push(new Graphic({geometry:{type:'polyline',paths:p.geometry.type==='LineString'?[coordinates]:coordinates,spatialReference:{wkid:4326}},symbol:{type:'simple-line',color:p.installation==='overhead'?'#a46d39':'#8b7aa5',width:1.5}}));}
    const groups=new Map<string,Focus[]>();for(const work of mapWork()){const rows=groups.get(work.site.id)??[];rows.push(work);groups.set(work.site.id,rows);}
    pinLayer.replaceChildren();pins=[];
    for(const rows of groups.values()){
      const work=rows[0],s=work.site,selectedHere=rows.some(w=>w.id===focusedAction?.id);
      const point=new Point({longitude:s.position[0],latitude:s.position[1],spatialReference:{wkid:4326}});
      items.push(new Graphic({geometry:point,symbol:{type:'simple-marker',style:'circle',size:selectedHere?13:9,color:mapMode==='before'?'#70757b':'#215f89',outline:{color:'#fff',width:2}}}));
      const index=rows.findIndex(w=>w.id===focusedAction?.id);
      const pin=button('',()=>chooseWork(rows[(index+1)%rows.length]));pin.className='plan-pin';pin.setAttribute('aria-label',rows.map(w=>workFacts[w.operation_id]?.name??w.label).join(', ')+' at '+s.label);pin.setAttribute('aria-pressed',String(selectedHere));pin.dataset.siteId=s.id;pin.dataset.longitude=String(s.position[0]);pin.dataset.latitude=String(s.position[1]);pin.dataset.pending=String(rows.every(w=>w.priority===undefined));
      pin.dataset.workCount=String(rows.length);pin.title=rows.map(w=>workFacts[w.operation_id]?.name??w.label).join(' · ');
      pin.append(text('strong',String(rows.length)));
      const labels=text('span','');labels.append(text('span',s.label));const planned=rows.filter(w=>w.priority!==undefined);labels.append(text('small',`${rows.length===1?'1 action':rows.length+' actions'}${planned.length?' · '+planned.length+' scheduled':''}`));pin.append(labels);
      const line=text('div','','plan-pin-line');line.dataset.selected=String(selectedHere);pinLayer.append(line,pin);pins.push({element:pin,line,point,ids:rows.map(w=>w.id)});
    }
    document.documentElement.dataset.mapComparison=mapMode;
    layer.removeAll();layer.addMany(items);void consequences.draw(focusedAction,mapMode,modelDetail);renderMapCard();updateTools();pinLayoutKey='';lastPinFrame=0;positionPins();
    document.documentElement.dataset.mapComparison=mapMode;document.documentElement.dataset.planAction=focusedAction?.id??'';document.documentElement.dataset.planHazard=focusedAction?.hazard??selected;document.documentElement.dataset.planLocation=focusedAction?.site.id??siteFor(selected).id;document.documentElement.dataset.planPowerLines=String(showPower?powerLines.length:0);document.documentElement.dataset.planHistoricalReports=String(showHistory?state.evidence.incidents.length:0);
  }
  function focusSite(){focusedAction=undefined;draw();const s=siteFor(selected);void view?.goTo({center:s.position,zoom:16},{animate:false}).catch(()=>{});}
  async function api<T>(url:string,method='GET',data?:unknown):Promise<T>{const r=await fetch(url,{method,cache:'no-store',headers:{'Content-Type':'application/json'},body:data?JSON.stringify(data):undefined});if(!r.ok){const e=await r.json();throw Error(typeof e.detail==='string'?e.detail:Array.isArray(e.detail)?e.detail.map((v:{msg:string})=>v.msg).join(' '):'Check the location, inspection note and time inputs.');}return r.json() as Promise<T>;}
  async function save(){if(!draft||!state)return;busy=true;try{state=await api<State>('/api/prestorm','PUT',{expected_version:state.version,plan:draft});draft=structuredClone(state.plan);dirty=false;render();announce('Saved. Both views use this plan.');}catch(e){announce(String(e),true);}finally{busy=false;}}
  function numeric(key:Role|'lead_minutes',label:string){const wrap=text('label',label) as HTMLLabelElement;const input=document.createElement('input');input.type='number';input.min=key==='lead_minutes'?'1':'0';input.max=key==='lead_minutes'?'10080':'100';input.value=draft!.resources[key]?.toString()??'';input.setAttribute('aria-label',label);input.placeholder=key==='general'?`${state!.action_planning.resources.general} from scenario`:key==='lead_minutes'?`${state!.action_planning.resources.lead_minutes} from scenario`:'Request separately';input.oninput=()=>{draft!.resources[key]=input.value===''?null:Number(input.value);changed();};wrap.append(input);return wrap;}
  function render(){
    if(!state||!draft)return;const active=selectedEvidence(),task=draft.tasks[selected],site=siteFor(selected);panel.replaceChildren();
    panel.append(text('h1','Before the storm'));
    const context=text('div','','plan-context');
    const weather=app?.parameters??state.weather;context.append(text('div',`Scenario · ${weather.rain_mm_h} mm/h rain · ${weather.gust_mps} m/s gusts`),text('small','Planning scenario · not a live forecast'));
    panel.append(context);
    panel.append(preventiveWorkView(state.preventive_work,state.job?.status==='running',async settings=>{await save();if(dirty||!state)return;try{await api('/api/prestorm/compare-work','POST',{expected_version:state.version,settings});await poll();}catch(e){announce(String(e),true);}},step=>{chooseWork(step);void view?.goTo({center:step.site.position,zoom:16},{animate:false}).catch(()=>{});},draft.tasks));
    const editor=document.createElement('details');editor.className='plan-editor';editor.append(text('summary','Update observations & resources'));panel.append(editor);
    const heading=text('div','','plan-heading');heading.append(text('h2','1. Check all six hazards'),text('small',state.review_current?'Argon-reviewed order':'Evidence checklist'));editor.append(heading);
    const list=text('div','','plan-list');list.id='plan-checks';
    for(const row of state.rows){const h=state.evidence.hazards.find(h=>h.id===row.hazard)!,s=siteFor(row.hazard);const item=button('',()=>{selected=row.hazard;render();focusSite();});item.className='plan-item';item.setAttribute('aria-pressed',String(selected===row.hazard));item.dataset.hazard=row.hazard;
      const title=text('span','','plan-item-top');title.append(text('strong',h.label),text('small',draft.tasks[row.hazard].condition==='unchecked'?'Needs inspection':draft.tasks[row.hazard].condition==='concern'?'Concern recorded':'No concern recorded'));
      item.append(title,text('span',checks[row.hazard]+' · '+s.label,'plan-muted'));list.append(item);
    }editor.append(list);
    const detail=text('section','','plan-detail');detail.setAttribute('aria-label',active.label+' action details');detail.append(text('h2',active.label),text('p',task.condition==='concern'?active.action:active.check));
    const location=text('label','Where') as HTMLLabelElement;const select=document.createElement('select');select.setAttribute('aria-label','Location for '+active.label);
    const search=document.createElement('input');search.type='search';search.placeholder='Filter by address, asset ID or species';search.setAttribute('aria-label','Filter locations');
    const options=(query='')=>{select.replaceChildren();const matches=state!.sites[selected].filter(s=>s.label.toLowerCase().includes(query.toLowerCase())).slice(0,100);if(!matches.some(s=>s.id===task.site_id))matches.unshift(siteFor(selected));for(const s of matches){const o=document.createElement('option');o.value=s.id;o.textContent=s.label;select.append(o);}select.value=task.site_id;};options();search.oninput=()=>options(search.value);location.append(search);
    select.onchange=()=>{task.site_id=select.value;task.condition='unchecked';task.note='';task.minutes=null;task.prerequisites_confirmed=false;changed();render();focusSite();announce('Location changed. Prior inspection cleared; save the new location.');};location.append(select);detail.append(location,text('p',site.basis,'plan-muted'));
    if(site.report_ids){detail.append(text('p',`Reports ${site.report_ids.join(', ')} · ${site.dates?.join(', ')}`,'plan-muted'));}
    const mapButtons=text('div','','plan-row');mapButtons.append(button('Show location',focusSite));const sl=link(sourceFor(site.source_id));sl.textContent='Location evidence ↗';mapButtons.append(sl);detail.append(mapButtons);
    const benefit=text('div','','plan-benefit');benefit.append(text('strong','Why this could help'),text('p',active.benefit),text('small',active.transfer));detail.append(benefit);
    const validation=document.createElement('details');validation.append(text('summary','Storm evidence · '+active.validation),text('p',active.validation_detail));for(const id of active.source_ids){const source=sourceFor(id),block=text('div','','plan-source');block.append(link(source),text('p',source.finding),text('small',source.kind+' · '+source.locator+' · '+source.published));validation.append(block);}detail.append(validation);
    const condition=text('label','Current site finding') as HTMLLabelElement;const cs=document.createElement('select');cs.setAttribute('aria-label','Current site finding');for(const [id,label] of [['unchecked','Not inspected for this plan'],['concern','Concern recorded by operator'],['clear','Inspected; no concern recorded']]){const o=document.createElement('option');o.value=id;o.textContent=label;cs.append(o);}cs.value=task.condition;cs.onchange=()=>{task.condition=cs.value as Task['condition'];task.prerequisites_confirmed=false;changed();render();};condition.append(cs);detail.append(condition);
    const note=text('label','Inspection date, observer and finding / source') as HTMLLabelElement;const notes=document.createElement('textarea');notes.setAttribute('aria-label','Inspection date, observer and finding / source');notes.value=task.note;notes.placeholder='Record a real inspection or source. Historical reports alone do not confirm today’s condition.';notes.oninput=()=>{task.note=notes.value;changed();};note.append(notes);detail.append(note);
    const needs=document.createElement('details');needs.append(text('summary','Before mitigation can be scheduled'));const ul=document.createElement('ul');active.needs.forEach(n=>ul.append(text('li',n)));needs.append(ul);detail.append(needs);
    if(task.condition==='concern'){const l=text('label','','check-label') as HTMLLabelElement;const c=document.createElement('input');c.type='checkbox';c.checked=task.prerequisites_confirmed;c.onchange=()=>{task.prerequisites_confirmed=c.checked;changed();};l.append(c,document.createTextNode('The listed site prerequisites are confirmed for this work.'));detail.append(l);}
    const estimate=text('label','Total task minutes, including travel and setup') as HTMLLabelElement;const et=document.createElement('input');et.type='number';et.min='1';et.max='10080';et.placeholder='Estimate required to schedule';et.value=task.minutes?.toString()??'';et.setAttribute('aria-label','Total task minutes, including travel and setup');et.oninput=()=>{task.minutes=et.value===''?null:Number(et.value);changed();};estimate.append(et);detail.append(estimate,text('small','Operator estimate; the app does not infer inspection or repair time.'));
    editor.append(detail);
    const setup=document.createElement('details');setup.id='plan-resources';setup.open=false;setup.append(text('summary','2. Time and available teams'));const inputs=text('div','','plan-setup');inputs.append(numeric('lead_minutes','Minutes before the storm'));for(const role of Object.keys(names) as Role[])inputs.append(numeric(role,names[role]));setup.append(inputs,text('small','Enter available teams, not headcount. Building work needs an appropriately qualified professional. Blank means not supplied; zero means unavailable.'));editor.append(setup);
    const actions=text('div','','plan-actions');const saveButton=button('Save observations and resources',()=>void save());actions.append(saveButton);editor.append(actions);
    const status=text('p',dirty?'Unsaved changes':state.job?.error??(state.preventive_work.current?'Plan saved':'Choose resources and compare physical work with doing nothing.'),'plan-status');status.setAttribute('role','status');if(state.job?.error)status.classList.add('plan-error');panel.insertBefore(status,editor);
    const schedule=text('section','','plan-plan');schedule.id='plan-work';schedule.append(text('h2','3. Work before the storm'),text('p','Checks stay separate from mitigation. No damage-reduction total is claimed.','plan-muted'));
    for(const row of state.rows){const h=state.evidence.hazards.find(h=>h.id===row.hazard)!;const el=text('div','','plan-source');el.append(text('strong',h.label),text('p',row.status+(row.finish!==null?` · ${names[row.role]} ${row.team} · +${row.start} to +${row.finish} min`:''),'plan-inline'));if(state.review_current)el.append(text('small',reasons[state.review!.reasons[row.hazard]]));schedule.append(el);}editor.append(schedule);
    const mapOptions=document.createElement('details');mapOptions.append(text('summary','Map sources and coverage'));for(const [label,current,fn] of [['Historical reports',showHistory,(v:boolean)=>{showHistory=v;draw();}],['Power lines',showPower,(v:boolean)=>{showPower=v;draw();}]] as [string,boolean,(v:boolean)=>void][]){const l=text('label','','check-label') as HTMLLabelElement,c=document.createElement('input');c.type='checkbox';c.checked=current;c.onchange=()=>fn(c.checked);l.append(c,document.createTextNode(label));mapOptions.append(l);}
    mapOptions.append(text('p',`${state.evidence.incidents.length} historical reports · ${state.coverage.buildings} building footprints · ${state.coverage.trees} mapped trees.`),text('p',`${state.coverage.distribution_feeders} PG&E feeder features · ${state.coverage.transmission_lines} CEC line features · ${state.coverage.substations} substations.`),text('p',`${state.coverage.overhead_lines} confirmed overhead features in this cut; ${state.coverage.unknown_installation} have unknown installation. No customer or live outage topology.`));for(const id of ['pge','cec'])mapOptions.append(link(sourceFor(id)),text('br',''));panel.append(mapOptions);
    const lab=document.createElement('details');lab.append(text('summary','Research model · unvalidated local benefits'),text('p','Earlier synthetic failure counts and reduction percentages are excluded from this plan. The hydrology and wind controls remain available for research.'));const toggle=button(document.body.classList.contains('research-mode')?'Hide model layers':'Explore model layers',()=>{document.body.classList.toggle('research-mode');toggle.textContent=document.body.classList.contains('research-mode')?'Hide model layers':'Explore model layers';onFlood(undefined);});lab.append(toggle);panel.append(lab);
    document.documentElement.dataset.planVersion=String(state.version);document.documentElement.dataset.planReview=String(state.review_current);document.documentElement.dataset.planReady='true';draw();
  }
  async function poll(){if(polling||busy)return;polling=true;try{const next=await api<State>('/api/prestorm');const stamp=JSON.stringify([next.version,next.job,next.review_current,next.weather]);if(!dirty&&stamp!==lastStamp){state=next;const active=allWork(next).filter(w=>hazardFilter==='all'||w.hazard===hazardFilter);focusedAction=active.find(s=>s.id===focusedAction?.id)??active[0];draft=structuredClone(next.plan);lastStamp=stamp;const scroll=panel.scrollTop;render();panel.scrollTop=scroll;}}catch(e){if(state){announce('Refresh failed; saved plan retained. '+String(e),true);mapNote.prepend(text('p','Refresh failed. Showing the saved plan.','plan-error'));}else{mapNote.replaceChildren(text('h2','Work sites could not load'),button('Retry',()=>void poll()));}}finally{polling=false;}}
  try{const assets=await api<{power_lines:typeof powerLines}>('/api/risk/assets');powerLines=assets.power_lines;}catch{/* Failure is also reported by the planning inventory endpoint. */}
  await poll();setInterval(()=>void poll(),2500);
  return {status(s:ScenarioState){app=s;},draw(_seconds:number){positionPins();},setView(v:MapView){view=v;positionPins();}};
}
