/** Six-hazard action effects embedded in the existing preparation-run viewer. */
type Action={marginal?:Record<string,number>;kind?:string;native_consequence?:boolean;selected?:boolean;applied?:boolean;not_selected_reason?:string;ablation_label?:string;plan_label?:string;assignment?:{priority:number;crew_id:string;start:number;finish:number};power_lines?:Array<{geometry:{type:string;coordinates:any};source:string;name:string;installation:string}>;id:string;operation_id:string;hazard:string;label:string;recorded:boolean;helps:boolean;effect_known:boolean;people:number;role:string;minutes:number[];equipment:Record<string,number>;evidence:string;source_ids:string[];basis:string;site:{position:number[];label:string;kind:string;heading:number;building_height_m:number;display_height_m:number;building_geometry:{coordinates:number[][][][]};height_m:number;road_geometry?:{coordinates:number[][]}};before?:State[];after?:State[];peak_before:string;peak_after:string;storm_id?:string};
type State={summary:string;water_m:number;broken_panes:number;rain_inside_l:number;tree_fallen:boolean;collapse_m2:number;pump_on:boolean;lane_open:boolean;wet_contents:number;restricted:boolean;loose_released?:number;barrier?:boolean};
type Context={run:string;model:string;label:string;familyLabel?:string;map:any;view:any;planLayer:any;frame:number|null};
type Pack={work_plan?:{label:string;name:string;model:string;selected_ids:string[];applied_ids:string[];reasons:string[];resources:{minutes:number;crews:Array<{people:number;role:string}>};input_digest:string};review?:{review:{prioritized_action_ids:string[]}};storm_id:string;hazards:Record<string,string>;weather:Record<string,number>;minutes:number[];actions:Action[];sources:Array<{id:string;title:string;url:string}>;scope:string};
declare global {interface Window {prepRunContext?:Context;}}
const names:Record<string,string>={'fit-barrier':'Fit doorway barrier','sandbag-wall':'Place sandbag barrier','portable-pump':'Place pump','backup-pump':'Connect backup pump','clear-inlet':'Clear inlet','move-contents':'Raise contents','latch-windows':'Latch windows','secure-items':'Secure loose items','prune-defect':'Prune assessed defect','engineered-support':'Install designed support','clear-route':'Clear access','install-cordon':'Close unsafe route'};
const style=`#run-actions{border-top:1px solid var(--edge);border-bottom:1px solid var(--edge);padding:9px 0;margin:9px 0;font-size:12px}#run-actions summary{cursor:pointer;font-weight:500}#run-actions select,#run-actions input{box-sizing:border-box;width:100%;height:28px;margin-top:6px;border:1px solid var(--edge);border-radius:5px;color:var(--fg);background:var(--surface);font:inherit;padding:3px 6px}#run-actions .filters{display:flex;gap:5px}#run-actions .filters select{width:42%}#run-actions .filters input{width:58%}#run-actions .pair{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin:8px 0}#run-actions .pair button{color:var(--fg);background:transparent;border:1px solid var(--edge);border-radius:6px;min-height:28px;font:inherit;cursor:pointer}#run-actions .pair button[aria-pressed=true]{color:var(--bg);background:var(--fg)}#run-actions .effect{line-height:1.4;margin:5px 0}#run-actions small{color:var(--muted);font-size:11px}#run-actions .source{color:var(--fg);text-decoration:underline}#run-actions .locate{border:0;background:none;color:var(--fg);text-decoration:underline;cursor:pointer;font:inherit;padding:0}#run-actions details{margin-top:5px}#run-actions .scope{font-size:10px;color:var(--muted);margin:4px 0}`;

export function installRunActions(){
  const panel=document.querySelector('#panel');if(!panel)return;
  const css=document.createElement('style');css.textContent=style;document.head.append(css);
  const section=document.createElement('details');section.id='run-actions';section.open=true;
  section.innerHTML='<summary>Actions · loading</summary><div class="filters"><select aria-label="Hazard"></select><input type="search" aria-label="Find action or location" placeholder="Find action or place"></div><select aria-label="Action and location"></select><div class="pair"><button type="button" data-mode="before">Without this action</button><button type="button" data-mode="after" aria-pressed="true">With this action</button></div><div class="effect" aria-live="polite"></div><small class="resources"></small> · <button class="locate" type="button">Locate</button><p class="scope">Plan-linked replay · same storm · extrapolated outcomes</p><details class="evidence"><summary>Evidence & inspection</summary><p></p></details>';
  document.querySelector('#plans')!.after(section);
  const heading=section.querySelector('summary')!,hazard=section.querySelector<HTMLSelectElement>('[aria-label="Hazard"]')!,search=section.querySelector<HTMLInputElement>('input')!,select=section.querySelector<HTMLSelectElement>('[aria-label="Action and location"]')!,effect=section.querySelector<HTMLElement>('.effect')!,resources=section.querySelector<HTMLElement>('.resources')!,evidence=section.querySelector<HTMLElement>('.evidence p')!;
  let ctx:Context|undefined,pack:Pack|undefined,active:Action|undefined,mode:'before'|'after'='after',frame:number|null=null,revision=0;
  let layer:any,markers:any,Graphic:any,Point:any;
  const ready=new Promise<void>(resolve=>window.require(['esri/layers/GraphicsLayer','esri/Graphic','esri/geometry/Point'],(L:any,G:any,P:any)=>{Graphic=G;Point=P;layer=new L({title:'Per-action consequences (extrapolated)',elevationInfo:{mode:'relative-to-ground'}});markers=new L({title:'Preventive actions',elevationInfo:{mode:'relative-to-ground'}});resolve();}));
  const endpoint=(id='')=>`/api/prep-runs/${encodeURIComponent(ctx!.run)}/${ctx!.model}/${ctx!.label}/actions${id?'/'+encodeURIComponent(id):''}${ctx!.familyLabel?'?reference='+encodeURIComponent(ctx!.familyLabel):''}`;
  function filtered(){const q=search.value.toLowerCase();const order=pack?.work_plan?.selected_ids??pack?.actions.filter(a=>a.selected).map(a=>a.id)??[];return [...(pack?.actions??[])].sort((a,b)=>{const x=order.indexOf(a.id),y=order.indexOf(b.id);return (x<0?999:x)-(y<0?999:y);}).filter(a=>(hazard.value==='plan'?a.selected:(!hazard.value||a.hazard===hazard.value))&&`${a.label} ${a.site.label}`.toLowerCase().includes(q));}
  function options(refresh=false){
    const list=filtered();select.replaceChildren();
    for(const a of list){const o=document.createElement('option');o.value=a.id;o.textContent=`${a.selected?(a.assignment?.priority??'●')+' · ':''}${a.kind?a.label:names[a.operation_id]??a.label} · ${a.site.label}`;select.append(o);}
    select.disabled=!list.length;
    if(active&&list.some(a=>a.id===active!.id)&&!refresh)select.value=active.id;
    else if(list.length)void choose(list.find(a=>a.id===active?.id)??list.find(a=>a.helps)??list[0]);
    else{active=undefined;effect.textContent=hazard.value==='plan'?'No actions selected in this plan. Choose a hazard to inspect alternatives.':'No matching actions.';resources.textContent='';evidence.replaceChildren();for(const b of Array.from(section.querySelectorAll<HTMLButtonElement>('[data-mode]')))b.disabled=true;layer?.removeAll();resetOriginal();}
    drawMarkers();
  }
  function drawMarkers(){
    if(!markers||!ctx)return;markers.removeAll();
    for(const a of filtered())markers.add(new Graphic({geometry:{type:'point',longitude:a.site.position[0],latitude:a.site.position[1]},attributes:{action_id:a.id},symbol:{type:'point-3d',symbolLayers:[{type:'icon',resource:{primitive:a.id===active?.id?'kite':'circle'},size:a.id===active?.id?13:5,material:{color:a.id===active?.id?'#00d1ef':'#a4a4a4'},outline:{color:'#161616',size:1}}],verticalOffset:{screenLength:18,minWorldLength:3,maxWorldLength:120},callout:{type:'line',size:1,color:'#a4a4a4'}}}));
  }
  async function choose(a:Action){
    active=a;select.value=a.id;effect.textContent='Loading matched action…';
    const id=a.id,token=revision;
    try{const r=await fetch(endpoint(id));if(!r.ok)throw Error('Action calculation unavailable');const detail:Action=await r.json();if(token!==revision||active?.id!==id)return;active=detail;drawMarkers();draw();}
    catch(e){effect.textContent=String(e);}
  }
  function resetOriginal(){for(const title of ['Buildings','Pumps','Pump cells','Cleared drains']){const original=ctx?.planLayer?.layers?.find((l:any)=>l.title===title);if(original)original.definitionExpression=null;}}
  function draw(){
    if(!active?.before||!pack||!ctx||!Graphic)return;
    const k=frame===null?active.before.length-1:Math.min(frame,active.before.length-1);
    const before=active.before[k],after=active.after![k];mode=active.applied?'after':'before';const s=mode==='after'?after:before;
    effect.replaceChildren();
    const a=document.createElement('div'),b=document.createElement('div');a.textContent='Without: '+before.summary;b.textContent='With: '+after.summary;effect.append(a,b);
    const comparison=document.createElement('small');
    const metrics:Record<string,string>={windborne_objects:'airborne objects avoided',trees_failed:'tree falls avoided',destroyed_buildings:'DS4 states avoided',flooded_entrances:'entries kept dry',unserved_load_hours:'critical load-h restored',route_hazard_minutes:'hazardous open-link min avoided'};
    const changes=Object.entries(active.marginal??{}).filter(([k,v])=>metrics[k]&&Math.abs(v)>.0001).map(([k,v])=>`${Number(v.toFixed(2))} ${metrics[k]}`);
    comparison.textContent=!active.selected?(active.not_selected_reason??'Not selected by Argon'):changes.length?'Modelled plan difference: '+changes.join(' · '):!active.effect_known?'Marginal water effect is not isolated in this saved run.':before.summary===after.summary?'No harm reduction at this storm time.':'Matched change shown in 3D.';effect.append(comparison);
    if(active.kind){const provenance=document.createElement('small');provenance.style.display='block';provenance.textContent='Extrapolated benefit · ';const source=pack.sources.find(x=>active!.source_ids.includes(x.id));if(source){const a=document.createElement('a');a.className='source';a.href=source.url;a.target='_blank';a.rel='noreferrer';a.textContent='Mechanism evidence ↗';provenance.append(a);}else provenance.append('mechanism evidence in details');effect.append(provenance);}
    resources.textContent=active.assignment?`${active.applied?'':'Not applied · '}#${active.assignment.priority} · ${active.assignment.crew_id} · ${active.people} people · ${active.assignment.start}–${active.assignment.finish} min · inspect site`:`${active.people} ${active.role} · ${active.minutes[0]}–${active.minutes[1]} min · not scheduled`;
    evidence.replaceChildren(document.createTextNode(active.evidence+' '));
    const runLink=document.createElement('a');runLink.className='source';runLink.href=`/runs/${ctx.run}/${ctx.model}/${ctx.label}_layers.json`;runLink.target='_blank';runLink.textContent='Run data ↗ ';evidence.append(runLink);
    for(const id of active.source_ids){const source=pack.sources.find(s=>s.id===id);if(!source)continue;const link=document.createElement('a');link.className='source';link.href=source.url;link.target='_blank';link.rel='noreferrer';link.textContent=source.title+' ↗ ';evidence.append(link);}
    const note=document.createElement('p');note.textContent=active.basis+'. Site condition and applicability need inspection. '+Object.entries(active.equipment).map(([n,q])=>`${q} ${n.replaceAll('_',' ')}`).join(', ');evidence.append(note);
    for(const button of Array.from(section.querySelectorAll<HTMLButtonElement>('[data-mode]'))){button.setAttribute('aria-pressed',String(button.dataset.mode===mode));button.disabled=!active.selected||!active.ablation_label;}
    layer.removeAll();
    const planLayers=ctx.planLayer?.layers;
    resetOriginal();
    const index=Number(active.id.split(':').at(-1));
    if(Number.isFinite(index)&&['fit-barrier','sandbag-wall','move-contents'].includes(active.operation_id)){const original=planLayers?.find((l:any)=>l.title==='Buildings');if(original)original.definitionExpression=`fd_id <> ${index}`;}
    if(active.recorded&&!pack.work_plan&&mode==='before'&&active.operation_id==='portable-pump')for(const title of ['Pumps','Pump cells']){const original=planLayers?.find((l:any)=>l.title===title);if(original)original.definitionExpression=`job_index <> ${index}`;}
    const site=active.site,[lon,lat]=site.position,baseZ=['building','pump'].includes(site.kind)?site.display_height_m+8:0,angle=(site.heading||0)*Math.PI/180;
    const xy=(x:number,y:number,z=0)=>[lon+(x*Math.cos(angle)+y*Math.sin(angle))/88000,lat+(-x*Math.sin(angle)+y*Math.cos(angle))/111320,z+baseZ];
    const point=(x:number,y:number,z=0)=>{const p=xy(x,y,z);return {type:'point',longitude:p[0],latitude:p[1],z:p[2]};};
    const add=(geometry:any,symbol:any,kind:string)=>layer.add(new Graphic({geometry,symbol,attributes:{kind,action_id:active!.id,mode,storm_id:pack!.storm_id}}));
    const box=(x:number,y:number,z:number,w:number,d:number,h:number,color:any,kind:string,primitive='cube')=>add(point(x,y,z),{type:'point-3d',symbolLayers:[{type:'object',resource:{primitive},width:w,depth:d,height:h,heading:site.heading,anchor:'bottom',material:{color}}]},kind);
    const path=(points:number[][],width:number,color:string,kind:string)=>add({type:'polyline',paths:[points.map(p=>xy(p[0],p[1],p[2]??.1))],hasZ:true,spatialReference:{wkid:4326}},{type:'line-3d',symbolLayers:[{type:'path',profile:'circle',width,material:{color}}]},kind);
    const patch=(x:number,y:number,w:number,d:number,h:number,color:any,kind:string)=>add({type:'polygon',rings:[[[-w/2,-d/2],[-w/2,d/2],[w/2,d/2],[w/2,-d/2],[-w/2,-d/2]].map(([u,v])=>xy(x+u,y+v,.1))],hasZ:true,spatialReference:{wkid:4326}},{type:'polygon-3d',symbolLayers:[{type:'extrude',size:Math.max(.03,h),material:{color}}]},kind);
    const roof=0;
    if(baseZ)path([[0,0,-baseZ],[0,0,0]],.08,'#a4a4a4','site-connector');

    if(active.native_consequence){document.documentElement.dataset.actionPlan=ctx.label;document.documentElement.dataset.actionEffect=active.id;document.documentElement.dataset.actionMode=mode;document.documentElement.dataset.actionStorm=pack.storm_id;document.documentElement.dataset.actionFrame=String(k);document.documentElement.dataset.actionSummary=s.summary;return;}
    const op=active.operation_id;
    if(op==='backup-pump')for(const line of active.power_lines??[]){
      const paths=line.geometry.type==='LineString'?[line.geometry.coordinates]:line.geometry.coordinates;
      add({type:'polyline',paths,spatialReference:{wkid:4326}},{type:'simple-line',width:2.5,color:'#ad8cdb'},'public-feeder-unverified-connection');
      const p=document.createElement('p');p.textContent=`${line.source}: ${line.name??'mapped line'} · connection unverified`;evidence.append(p);
    }
    if(active.kind==='backup_power'){
      // A portable generator supplies one critical circuit. It is not a pump
      // and cannot drain the local water patch or restore the public feeder.
      box(-3,-4,0,1.8,1,1.2,mode==='after'?'#d2b76e':'#4a4a4a','critical-generator');
      box(1,-3,0,1,.4,1.6,s.pump_on?'#00c769':'#4a4a4a',s.pump_on?'critical-circuit-powered':'critical-circuit-off');
      if(mode==='after')path([[-3,-4,.2],[-1,-4,.2],[1,-3,.3]],.09,s.pump_on?'#00c769':'#555555','critical-power-cable');
    }else if(op==='prune-defect'){
      const h=Math.min(24,site.height_m),tip=s.tree_fallen?[h*.7,0,1]:[0,0,h];
      path([[0,0,0],tip],.55,'#775940',s.tree_fallen?'fallen-tree':'standing-tree');
      box(tip[0],tip[1],tip[2]-2,mode==='after'?4:6,mode==='after'?4:6,4,'#567a55','tree-crown','sphere');
    }else if(op==='engineered-support'){
      if(s.collapse_m2)for(let i=0;i<8;i++)box((i%4)*2-3,-5+Math.floor(i/4)*2,0,1.5,1,.5,'#bd9778','fallen-masonry');
      else path([[-4,-5,0],[-4,-5,6],[4,-5,6],[4,-5,0]],.18,'#a4a4a4','standing-section');
      if(mode==='after')for(const x of [-3,0,3])path([[x,-8,.1],[x,-5,5]],.2,'#00c769','support');
    }else if(op==='latch-windows'||op==='secure-items'){
      box(0,-3.2,0,10,.4,5,'#606666','illustrative-facade-section');
      for(let i=0;i<10;i++)box((i%5)*1.4-3,-3,.7+Math.floor(i/5)*1.8,1,.1,1.3,i<s.broken_panes?'#1a1a1a':'#77b5ce',i<s.broken_panes?'broken-pane':'intact-pane');
      if(s.rain_inside_l)patch(0,-4,9,5,.06,[40,140,205,.8],'rain-entry');
      for(let i=0;i<(s.loose_released??0);i++)box(i-3,-6,.1,.7,.7,.4,'#99744b','loose-debris');
    }else if(active.hazard==='routes'){
      patch(0,0,6,24,s.water_m,[40,140,205,.55],'road-water');
      if(!s.lane_open)for(let i=0;i<4;i++)path([[-2,-3+i,.3],[2,1+i,.3]],.3,'#88754f','obstruction');
      if(s.restricted)for(const y of [-9,9])box(0,y,0,6,.3,1,'#ff9e38','access-closure');
      else if(s.lane_open)path([[0,-12,.15],[0,12,.15]],.2,'#00c769','open-lane');
    }else{
      // The water patch is a local overlay at the mapped site, not a new city flood solve.
      if(s.water_m>.005)patch(0,-6,12,12,s.water_m,[36,147,210,.8],'local-water');
      for(const [x,y]of [[-6,-12],[6,-12],[6,0],[-6,0]])path([[x,y,.1],[x,y,1.5]],.15,'#f2b200','selected-cell-corner');
      if(op==='fit-barrier'||op==='sandbag-wall'){
        box(0,-3,roof+.15,7,5,.15,s.water_m>0?'#f2b200':'#00d1ef','building-entry-status');
        if(mode==='after')box(0,-5,0,4,.4,op==='fit-barrier'?.6:.4,'#d2b76e','doorway-barrier');
      }
      if(op==='clear-inlet'){box(0,-6,.1,1,.7,.1,'#333333','inlet-grate');if(mode==='before')for(let i=0;i<4;i++)box(i*.22-.3,-6,.2,.2,.3,.15,'#806d4a','inlet-debris');}
      if(op==='move-contents')for(let i=0;i<10;i++)box((i%5)*.7-2,-5-Math.floor(i/5),mode==='after'?1:.05,.5,.6,.5,i<s.wet_contents?'#77553f':'#bc9659','contents');
      if(op==='portable-pump'||op==='backup-pump'){
        box(-3,-6,0,1,.8,1,s.pump_on?'#00c769':'#4a4a4a',s.pump_on?'running-pump':'stopped-pump');
        path([[-3,-6,.3],[-5,-7,.2],[-7,-9,.1]],.15,s.pump_on?'#00d1ef':'#555555','discharge');
        if(op==='backup-pump'&&mode==='after')box(-5,-4,0,1.5,1,1,'#d2b76e','backup-power');
      }
    }
    document.documentElement.dataset.actionPlan=ctx.label;document.documentElement.dataset.actionEffect=active.id;document.documentElement.dataset.actionMode=mode;document.documentElement.dataset.actionStorm=pack.storm_id;document.documentElement.dataset.actionFrame=String(k);document.documentElement.dataset.actionSummary=s.summary;
  }
  async function load(next:Context){
    ctx=next;frame=next.frame;const token=++revision;await ready;
    if(token!==revision)return;
    if(!next.map.layers.includes(layer)){next.map.add(markers);next.map.add(layer);next.view.on('click',async(e:any)=>{const hit=await next.view.hitTest(e);const id=hit.results?.find((r:any)=>r.graphic?.attributes?.action_id)?.graphic.attributes.action_id;const action=pack?.actions.find(a=>a.id===id);if(action){section.open=true;hazard.value='';search.value='';options();void choose(action);}});}
    try{const r=await fetch(endpoint());if(!r.ok)throw Error('Action comparisons unavailable');const nextPack:Pack=await r.json();if(token!==revision)return;pack=nextPack;heading.textContent=pack.work_plan?`Plan work · ${pack.work_plan.applied_ids.length}/${pack.work_plan.selected_ids.length} applied`:`Archived work · ${pack.actions.filter(a=>a.selected).length} selected`;const chosen=hazard.options.length?hazard.value:'plan';hazard.innerHTML='<option value="plan">Selected work</option><option value="">All alternatives</option>';section.querySelector('.scope')!.textContent=pack.work_plan?`Argon · ${pack.work_plan.resources.crews.length} crews · ${pack.work_plan.resources.crews.reduce((n,c)=>n+c.people,0)} people · ${pack.work_plan.resources.minutes} min · same storm · extrapolated`:'Archived flood plan · selected work only';for(const [id,name]of Object.entries(pack.hazards)){const o=document.createElement('option');o.value=id;o.textContent=name+' ('+pack.actions.filter(a=>a.hazard===id).length+')';hazard.append(o);}hazard.value=chosen;options(true);}
    catch(e){heading.textContent='Actions unavailable';effect.textContent=String(e);}
  }
  hazard.onchange=()=>options();search.oninput=()=>options();select.onchange=()=>{const a=pack?.actions.find(a=>a.id===select.value);if(a)void choose(a);};
  for(const b of Array.from(section.querySelectorAll<HTMLButtonElement>('[data-mode]')))b.onclick=()=>{if(!active?.selected||!active.ablation_label)return;mode=b.dataset.mode as typeof mode;window.dispatchEvent(new CustomEvent('prep-request-plan',{detail:{label:mode==='after'?active.plan_label:active.ablation_label}}));};
  section.querySelector<HTMLButtonElement>('.locate')!.onclick=()=>{if(active&&ctx){window.dispatchEvent(new Event('prep-user-navigation'));void ctx.view.goTo({target:new Point({longitude:active.site.position[0],latitude:active.site.position[1]}),scale:2500,tilt:35},{animate:true});}};
  window.addEventListener('prep-plan-shown',e=>void load((e as CustomEvent).detail));
  window.addEventListener('prep-frame-change',e=>{frame=(e as CustomEvent).detail.index;draw();});
  window.addEventListener('prep-panel-visibility',e=>{if(layer){layer.visible=(e as CustomEvent).detail;markers.visible=(e as CustomEvent).detail;if((e as CustomEvent).detail)draw();else resetOriginal();}});
  if(window.prepRunContext)void load(window.prepRunContext);
}
