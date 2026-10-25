import type {WorkCandidate, WorkComparison} from './preventiveWork';

type Finding={site_id:string;condition:'unchecked'|'concern'|'clear';note:string;prerequisites_confirmed:boolean};
export type WorkFindings=Record<string,Finding>;
type Fact={name:string;without:string;benefit:string;evidence:string;source:string;sourceLabel:string;inspect:string};
// Short labels for the cited findings, not new treatment-effect estimates.
export const workFacts:Record<string,Fact>={
  'backup-pump':{name:'Connect backup pump',without:'No added backup pumping',benefit:'Pump can run during an outage',evidence:'Sandy: backup pumps controlled water. Local benefit unmeasured.',source:'sandy',sourceLabel:'FEMA · Sandy',inspect:'Electrician: pump, backup circuit, capacity and discharge.'},
  'fit-barrier':{name:'Fit doorway barrier',without:'No added doorway protection',benefit:'Can block entry below its rated height',evidence:'Sandy gates overtopped. Benefit depends on water level and fit.',source:'sandy',sourceLabel:'FEMA · Sandy',inspect:'Verify fit, water level, backflow and a safe exit.'},
  'move-contents':{name:'Move vulnerable contents',without:'Items stay where they are',benefit:'Moves those items above water reach',evidence:'Official guidance; avoided damage has not been measured.',source:'fema234',sourceLabel:'FEMA · P-234',inspect:'Identify vulnerable items; verify flood level and dry storage.'},
  'secure-items':{name:'Secure loose items',without:'Any loose objects remain',benefit:'Removes those windborne debris sources',evidence:'SF 2023: debris caused glass failures. Tie-down benefit untested.',source:'sf-glass',sourceLabel:'SF DBI · 2023',inspect:'Confirm loose objects, safe access and rated attachment points.'},
  'latch-windows':{name:'Latch windows',without:'Open or unlatched windows remain',benefit:'Closes those openings only',evidence:'SF 2023: hardware failures. Closing benefit not measured.',source:'sf-glass',sourceLabel:'SF DBI · 2023',inspect:'Find open windows; check operable hardware and safe access.'},
  'clear-inlet':{name:'Clear inlet',without:'Any surface debris remains',benefit:'Clears surface blockage only',evidence:'SF crews clear basins; avoided flood damage unmeasured.',source:'sf-works',sourceLabel:'SF Public Works · 2023',inspect:'Match the inlet; confirm debris and dry, safe access.'},
  'prune-defect':{name:'Prune assessed defect',without:'Any assessed defect remains',benefit:'Removes the assessed defect only',evidence:'Andrew: pruning linked to survival; not a causal local estimate.',source:'uf-trees',sourceLabel:'UF/IFAS · Andrew',inspect:'Arborist: defect, cut plan, utilities and permissions.'},
  'engineered-support':{name:'Install designed support',without:'Any defect has no added support',benefit:'Supports one designed load path',evidence:'Andrew: connection failures. Applicability to this building unproven.',source:'fema-andrew',sourceLabel:'FEMA · Andrew',inspect:'Engineer: structure, defect, support design and permits.'},
  'sandbag-wall':{name:'Build sandbag barrier',without:'No added flow diversion',benefit:'Complete wall can divert shallow flow',evidence:'Official guidance; no measured local damage reduction.',source:'fema234',sourceLabel:'FEMA · P-234',inspect:'Verify siting, ends, water level and all 100 bags plus fill.'},
  'clear-route':{name:'Clear access',without:'Any light obstruction remains',benefit:'Opens that access segment',evidence:'SF 2023: debris blocked roads. Clearance benefit unmeasured.',source:'sf-works',sourceLabel:'SF Public Works · 2023',inspect:'Confirm obstruction, no wires and authorized safe access.'},
  'install-cordon':{name:'Set authorized closure',without:'No new access restriction',benefit:'Limits entry to the danger area',evidence:'Official storm protocol; avoided injuries unmeasured.',source:'sf-storm-roles',sourceLabel:'SF · Storm annex',inspect:'Confirm hazard, authority and a usable alternate route.'},
};

export function workEvidence(work:WorkCandidate,data:WorkComparison,findings:WorkFindings){
  const fact=workFacts[work.operation_id];
  const root=document.createElement('dl');root.className='work-evidence-inline';
  const add=(label:string,value:string)=>{const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=value;root.append(dt,dd);return dd;};
  add('No work',fact?.without??work.without);
  add('With work',fact?.benefit??work.with);
  const evidence=add('Evidence',fact?.evidence??work.evidence_grade);
  const source=data.sources.find(s=>s.id===fact?.source);
  if(source){const a=document.createElement('a');a.textContent=fact.sourceLabel+' ↗';a.href=source.url;a.target='_blank';a.rel='noreferrer';a.title=source.title;evidence.append(' ',a);}
  const finding=findings[work.hazard];
  const applies=finding?.site_id===work.site.id&&finding.condition!=='unchecked'&&finding.note.trim();
  const label=applies?(finding.condition==='clear'?'No concern found':'Site reviewed'):'Inspect first';
  add(label,applies&&finding.condition==='clear'?'No extra benefit established. '+(fact?.inspect??work.conditions[0]):(fact?.inspect??work.conditions[0]));
  root.dataset.inspection=applies?finding.condition:'unchecked';
  return root;
}
