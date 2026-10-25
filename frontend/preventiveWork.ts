import {workEvidence, workFacts, type WorkFindings} from './workEvidence';
export type WorkSettings={profile:'basic'|'staged'|'specialists';minutes:number};
type Source={id:string;title:string;url:string;finding:string;locator?:string;kind?:string};
export type HarmComparison={metric:string;before:boolean|null;after:boolean|null;improvement:number|null;support:'measured'|'conditional'|'unavailable';source_ref:string|null;missing:string[];storm_before:string;storm_after:string;field_calibrated:boolean;limitation:string};
export type ConsequenceState={summary:string;broken_panes:number;rain_inside_l:number;water_cm:number;wet_contents:number;tree_fallen:boolean;collapse_m2:number;pump_on:boolean;grid_on:boolean;lane_open:boolean;lane_width_m:number;exposed_passages:number;loose_released?:number;barrier?:boolean};
export type ConsequenceScenario={basis:string;model:string;validated:boolean;storm_before:string;storm_after:string;before:ConsequenceState;after:ConsequenceState;changed:boolean;placement:{position:number[];source_position:number[];heading:number;tree_height_m:number;asset_basis:string;building_position?:number[];building_geometry?:{type:string;coordinates:number[][][][]};building_height_m?:number}};
export type WorkCandidate={scenario?:ConsequenceScenario;harm?:HarmComparison;id:string;operation_id:string;hazard:string;label:string;site:{id:string;label:string;position:number[];basis:string};role:string;people:number;minimum_minutes:number;maximum_minutes:number;time_basis:string;equipment:Record<string,number>;output:string;without:string;with:string;evidence:string;evidence_grade:string;source_ids:string[];conditions:string[];site_status:string;feasibility:{fits:boolean;reasons:string[];crew_ids:string[];travel_by_crew:Record<string,number[]>;minimum_total:number;maximum_total:number}};
export type SelectedWork=WorkCandidate&{priority:number;assignment:string;crew_id:string;start:number;finish:number;labor_minutes:number;travel_minutes:number;why:string};
export type WorkComparison={settings:WorkSettings;profiles:Record<string,{label:string;description:string}>;studies:Array<{settings:WorkSettings;label:string;jobs:number;work:string[];labor_minutes:number;conclusion:string}>;resources:{description:string;crews:Array<{id:string;role:string;people:number}>;equipment:Record<string,number>;minutes:number;provenance:string;travel_assumption:string};equipment_names:Record<string,string>;current:boolean;baseline:{physical_change:string;storm_loss:string};candidates:WorkCandidate[];selected:SelectedWork[];result?:{conclusion:string;why_not_do_nothing:string;remaining_gaps:string;model:string;generated_at:string;result_sha256:string}|null;totals:{jobs:number;labor_minutes:number;latest_finish:number};sources:Source[];interpretation:string};
const names:Record<string,string>={wind:'Wind',flood:'Flooding',trees:'Falling trees',structure:'Structural collapse',power:'Power loss',routes:'Blocked routes'};
const node=(tag:string,value='',cls='')=>{const n=document.createElement(tag);n.textContent=value;n.className=cls;return n;};

export function preventiveWorkView(data:WorkComparison,running:boolean,compare:(settings:WorkSettings)=>void,focus:(work:SelectedWork)=>void,findings:WorkFindings){
  const root=node('section','','work-comparison');root.id='work-comparison';
  const style=node('style');style.textContent=`
    .work-comparison{margin:12px 0}.work-comparison h2{font-size:15px;margin:12px 0 4px}
    .work-config{display:grid;grid-template-columns:minmax(0,1fr) 100px;gap:8px}.work-config label{margin:0}
    .work-comparison .work-config-note{font-size:12px;color:var(--muted-foreground);margin:6px 0}
    .work-run{width:100%;margin:8px 0}.work-heading{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
    .work-plan-row{border-top:1px solid var(--border);padding:10px 0;display:grid;grid-template-columns:20px minmax(0,1fr);gap:0 6px}
    .work-number{font-size:12px;color:var(--muted-foreground);padding-top:2px}.work-row-body{min-width:0}
    .work-row-title{display:flex;align-items:baseline;justify-content:space-between;gap:8px}
    .work-row-title h3{font-size:13px!important;font-weight:500!important;margin:0!important}
    .work-time{font-size:11px;white-space:nowrap;color:var(--muted-foreground)}
    .work-comparison .work-location{font-size:12px;margin:2px 0}.work-comparison .work-effect{font-size:11px;color:var(--muted-foreground);margin:2px 0}
    .work-row-tools{display:flex;gap:12px;align-items:flex-start;margin-top:3px}
    .work-row-tools>button{border:0!important;padding:0!important;min-height:24px!important;background:none!important;font-size:12px;text-decoration:underline;text-underline-offset:2px;flex:none}
    .work-comparison .work-why{border:0;padding:0;flex:1;min-width:0}.work-why>summary{font-size:12px;line-height:24px;list-style:none;text-decoration:underline;text-underline-offset:2px}.work-why>summary::-webkit-details-marker{display:none}
    .work-why[open]>summary{margin-bottom:6px}.work-why p,.work-comparison details p{font-size:12px}
    .work-comparison details{padding:9px 0}.work-comparison details[open]>summary{margin-bottom:8px}
    .work-source{display:block;margin:6px 0;font-size:12px}.work-comparison ul{padding-left:18px;font-size:12px}
    .work-option{border-top:1px solid var(--border);padding:8px 0}.work-option summary{font-size:12px}
    .work-study{border-top:1px solid var(--border);padding:8px 0}.work-study strong{font-size:12px;font-weight:500}.work-study p{margin:3px 0}
    .work-vs{border-left:2px solid var(--border);padding-left:8px;margin:8px 0}.work-vs strong{font-weight:500}
    .work-evidence-inline{display:grid;grid-template-columns:72px minmax(0,1fr);gap:5px 8px;font-size:12px;line-height:1.4;margin:8px 0}.work-evidence-inline dt{color:var(--muted-foreground)}.work-evidence-inline dd{margin:0}.work-evidence-inline a{white-space:normal}
    .work-total{display:flex;justify-content:space-between;gap:8px;font-size:11px;color:var(--muted-foreground);padding:8px 0;border-top:1px solid var(--border)}
    @media(max-width:420px){.work-row-title{align-items:flex-start}.work-row-title h3{max-width:210px}.work-time{font-size:10px}}
  `;root.append(style);
  const settings={...data.settings};
  const config=node('div','','work-config');
  const label=node('label','Resources'),profile=document.createElement('select');profile.setAttribute('aria-label','Preventive resource package');
  for(const [id,p]of Object.entries(data.profiles)){const option=document.createElement('option');option.value=id;option.textContent=p.label;profile.append(option);}profile.value=settings.profile;
  const timeLabel=node('label','Time available'),time=document.createElement('select');time.setAttribute('aria-label','Preventive lead time');
  for(const minutes of [...new Set([30,120,480,settings.minutes])].sort((a,b)=>a-b)){const option=document.createElement('option');option.value=String(minutes);option.textContent=minutes<60?`${minutes} min`:`${minutes/60} hours`;time.append(option);}time.value=String(settings.minutes);
  label.append(profile);timeLabel.append(time);config.append(label,timeLabel);root.append(config);
  const people=data.resources.crews.reduce((sum,c)=>sum+c.people,0);
  const note=node('p',`${data.resources.crews.length} crews · ${people} people · assumed available`,'work-config-note');root.append(note);
  const changed=()=>{settings.profile=profile.value as WorkSettings['profile'];settings.minutes=Number(time.value);note.textContent='Resources changed. Generate a plan to apply.';};profile.onchange=changed;time.onchange=changed;
  const run=node('button',running?'Argon is comparing…':'Generate plan','work-run') as HTMLButtonElement;run.type='button';run.disabled=running;run.onclick=()=>compare(settings);root.append(run);
  if(data.current&&data.result){
    const heading=node('div','','work-heading');heading.append(node('h2','Proposed work queue'),node('small',`${data.totals.jobs} jobs`));root.append(heading,node('p','Confirm site conditions before dispatch.','work-config-note'));
    if(!data.selected.length)root.append(node('p','No work fits these resources and conditions.','work-config-note'));
    for(const work of data.selected){
      const row=node('article','','work-plan-row');row.dataset.workId=work.id;
      const body=node('div','','work-row-body'),title=node('div','','work-row-title');
      title.append(node('h3',workFacts[work.operation_id]?.name??work.label),node('span',`+${work.start}–${work.finish} min`,'work-time'));
      body.append(title,node('p',`${work.site.label} · ${work.assignment} (${work.people})`,'work-location'),workEvidence(work,data,findings));
      const tools=node('div','','work-row-tools'),map=node('button','Map') as HTMLButtonElement;map.type='button';map.setAttribute('aria-label','Show work at '+work.site.label);map.onclick=()=>focus(work);
      const why=document.createElement('details');why.className='work-why';why.append(node('summary','Work details'),node('p',work.why),comparison(work),detail(work));tools.append(map,why);body.append(tools);row.append(node('span',String(work.priority),'work-number'),body);root.append(row);
    }
    const total=node('div','','work-total');total.append(node('span',`Finish +${data.totals.latest_finish} min`),node('span',`${data.totals.labor_minutes} person-min`));root.append(total);
    const judgement=document.createElement('details');judgement.append(node('summary','Compared with doing nothing'),node('p','No work uses no crew time or stock, and adds no protection. Local damage reduction has not been measured.'),node('p',data.result.conclusion),node('p',data.result.why_not_do_nothing),node('p',data.result.remaining_gaps));root.append(judgement);
  }else root.append(node('p','Generate a plan to rank work against doing nothing.','work-config-note'));
  const resourceDetail=document.createElement('details');resourceDetail.append(node('summary','Crews, equipment & assumptions'),node('p',data.resources.description),node('p',data.resources.provenance));
  for(const c of data.resources.crews)resourceDetail.append(node('p',`${c.id.replace('-',' ')} · ${c.people} people`));
  resourceDetail.append(node('p',Object.entries(data.resources.equipment).map(([k,n])=>`${n} ${data.equipment_names[k]}`).join(' · ')),node('p',data.resources.travel_assumption),node('p','Slots include setup and estimated travel. Scheduling uses the upper time bound; most rates are planning estimates, not measured local productivity.'));root.append(resourceDetail);
  if(data.studies.length>1){
    const studies=document.createElement('details');studies.append(node('summary','Compare resource scenarios'));
    for(const study of data.studies){const row=node('details','','work-study') as HTMLDetailsElement;row.append(node('summary',`${study.label} · ${study.settings.minutes} min · ${study.jobs} jobs`),node('p',study.work.join('; ')),node('p',`${study.labor_minutes} person-minutes`),node('p',study.conclusion));studies.append(row);}root.append(studies);
  }
  const comparisons=document.createElement('details');comparisons.append(node('summary',`All six hazards · ${data.candidates.length} work options`));
  const selectedIds=new Set(data.selected.map(s=>s.id));
  for(const hazard of Object.keys(names)){
    const group=document.createElement('details');group.append(node('summary',names[hazard]));
    for(const work of data.candidates.filter(w=>w.hazard===hazard)){
      const row=node('details','','work-option') as HTMLDetailsElement;row.dataset.selected=String(selectedIds.has(work.id));row.dataset.operation=work.operation_id;
      row.append(node('summary',`${workFacts[work.operation_id]?.name??work.label} · ${work.site.label}`),node('p',selectedIds.has(work.id)?'Selected':work.feasibility.fits?'Fits this resource and time scenario':'Needs resources or prerequisites'),node('p',`${work.people}-person ${work.role} crew · ${work.feasibility.minimum_total}–${work.feasibility.maximum_total} min including setup${Object.keys(work.feasibility.travel_by_crew).length?' and estimated travel':''}`));
      if(work.feasibility.reasons.length)row.append(node('p',work.feasibility.reasons.join('. ')+'.'));
      row.append(workEvidence(work,data,findings),detail(work));group.append(row);
    }comparisons.append(group);
  }root.append(comparisons);
  const evidence=document.createElement('details');evidence.append(node('summary','Evidence & limits'),node('p',data.interpretation));
  if(data.current&&data.result)evidence.append(node('p',`${data.result.model} · ${new Date(data.result.generated_at).toLocaleString()}`),node('small',data.result.result_sha256.slice(0,16)));root.append(evidence);
  return root;
  function equipment(work:WorkCandidate){return Object.entries(work.equipment).map(([k,n])=>`${n} ${data.equipment_names[k]}`).join(' · ')||'No special equipment; owner access required';}
  function comparison(work:WorkCandidate){const block=node('div','','work-vs');block.append(node('strong','Doing nothing'),node('p',work.without),node('strong','With this work'),node('p',work.with),node('strong',work.evidence_grade));return block;}
  function detail(work:WorkCandidate){
    const result=node('div');result.append(node('p',work.output),node('p',equipment(work)),node('p',`${work.minimum_minutes}–${work.maximum_minutes} min on site. ${work.time_basis}`),node('p',work.site.basis));
    const list=node('ul');for(const condition of work.conditions)list.append(node('li',condition));result.append(list,node('p',work.evidence));
    for(const id of work.source_ids){const source=data.sources.find(s=>s.id===id);if(!source)continue;const a=node('a',source.title+' ↗','work-source') as HTMLAnchorElement;a.href=source.url;a.target='_blank';a.rel='noreferrer';result.append(a);if(source.locator)result.append(node('small',source.locator));}return result;
  }
}
