"""Resource-constrained work across six hazards, on one immutable water replay.

The shared destruction engine owns tree/roof/wire failures. The existing
multi-hazard projection owns entrance flooding, critical circuits and access.
Argon chooses action IDs, never harm numbers or weather. All site prerequisites
and resource availability are conditional planning assumptions.
"""
import copy
import json
import math
from pathlib import Path

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from scipy.spatial import cKDTree

from . import multihazard, risk_assets, workplan
from .destruction_adapter import Inputs

PROFILES={
    'rapid':dict(label='30 min · field crews',minutes=30,roles=[('general',2),('general',2)],
        equipment=dict(handtool_kit=2,tie_down=20,cart=1)),
    'staged':dict(label='2 h · staged crews',minutes=120,
        roles=[('general',2),('general',2),('electrician',2),('traffic',2)],
        equipment=dict(handtool_kit=2,tie_down=40,cart=1,fitted_barrier=4,
                       generator_20kw=2,fuel_2h=2,backup_circuit=2,electrical_kit=1,traffic_set=4)),
    'specialists':dict(label='8 h · specialist crews',minutes=480,
        roles=[('general',2),('general',2),('electrician',2),('traffic',2),('arborist',3),('building',3)],
        equipment=dict(handtool_kit=2,tie_down=80,cart=2,fitted_barrier=8,
                       generator_20kw=4,fuel_2h=4,backup_circuit=4,electrical_kit=1,traffic_set=8,
                       arborist_kit=1,tree_removal_kit=1,designed_support=1)),
}
# Upper bounds include setup, then the scheduler adds real street-graph travel
# and return. Durations are planning estimates, not measured local production.
OPERATIONS={
    'secure_loose':('secure-items','wind','Secure accessible loose items','general',2,30,{'handtool_kit':1,'tie_down':10}),
    'latch_windows':('latch-windows','wind','Latch accessible windows','general',2,20,{}),
    'prune_tree':('prune-defect','trees','Prune assessed defect','arborist',3,300,{'arborist_kit':1}),
    'remove_tree':('remove-tree','trees','Remove assessed hazardous tree','arborist',3,360,{'tree_removal_kit':1}),
    'brace_structure':('engineered-support','structure','Install designed roof anchors','building',3,420,{'designed_support':1}),
    'protect_entrance':('fit-barrier','flood','Fit rated entrance barrier','general',2,30,{'fitted_barrier':1,'handtool_kit':1}),
    'raise_contents':('move-contents','flood','Raise movable contents','general',2,35,{'cart':1}),
    'backup_power':('backup-pump','power','Connect backup critical circuit','electrician',2,80,{'generator_20kw':1,'fuel_2h':1,'backup_circuit':1,'electrical_kit':1}),
    'close_route':('install-cordon','routes','Close hazardous street link','traffic',2,30,{'traffic_set':1}),
}
ASSUMPTIONS=[
    'Resource scenarios, not a verified roster or dispatch: 4 people/30 minutes; 8 people/2 hours; 14 people/8 hours. Equipment is finite and explicitly listed.',
    'Street-graph walking at 1.4 m/s includes return to a fixed public-geometry staging proxy. Dry pre-storm access and owner permission are assumed.',
    'Work durations are conservative planning allowances, not measured local task times. Roof anchors require a completed design; tree work requires an arborist assessment; route closure requires an approved alternative.',
    'A generator serves one hypothetical 10 kW critical circuit with two hours of fuel, never an entire building or public feeder. A protected connection and pad below 0.8 m water are required.',
    'Objects, four accessible open windows and ten movable contents boxes per candidate building are hypothetical inventories. No basement is asserted.',
    'The exact saved water frames, wind field, weather, source geometry and latent failure draws are reused for every plan and action removal. Entrance devices do not drain streets.',
    'Failure mechanisms have storm evidence; numerical local treatment benefits remain extrapolated. Road exposure is hazardous open-link minutes, not predicted injuries.',
]


class PlanInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name:str
    action_ids:list[str]
    rationale:str


class ProfileProposal(BaseModel):
    model_config=ConfigDict(extra='forbid')
    profile: str
    plans: list[PlanInput]


class Proposals(BaseModel):
    model_config=ConfigDict(extra='forbid')
    profiles: list[ProfileProposal]


class ProfileDecision(BaseModel):
    model_config=ConfigDict(extra='forbid')
    profile: str
    selected_plan: str
    reason: str


class Decisions(BaseModel):
    model_config=ConfigDict(extra='forbid')
    profiles:list[ProfileDecision]


class CriticalLoadInputs(Inputs):
    def plan(self,actions):
        # The stock engine's backup flag implies all customers in a building.
        # A portable 20 kW unit only powers the projection's one critical load.
        return super().plan([a for a in actions if a['kind']!='backup_power'])


class ResourceModel(multihazard.Model):
    def __init__(self,assets,world):
        super().__init__(assets,world)
        self.destruction_inputs=CriticalLoadInputs(assets,world)
        self.receipt_override=None
        for t in self.trees:self.targets[('remove_tree',t['id'])]=self.targets[('prune_tree',t['id'])]
        for b in self.buildings:
            for kind in ('latch_windows','raise_contents'):
                self.targets[(kind,b['id'])]={'item':b,'node':int(self.ntree.query(b['xy'])[1])}

    def execute(self,actions):
        if self.receipt_override is None:return super().execute(actions)
        wanted={(a['kind'],a['target']) for a in actions}
        completed={(a['kind'],a['target']) for a in self.receipt_override['completed']}
        if completed!=wanted or any(a not in self.targets for a in completed):
            raise ValueError('Execution receipt does not match compatible simulation actions')
        return copy.deepcopy(self.receipt_override)

    def _start(self,actions,capacity_case):
        s=super()._start(actions,capacity_case)
        s.metrics.update(windborne_objects=0,rain_entry_l=0.,wet_contents=0)
        return s

    def _consequences(self,s,h,step,duration,record=True,flowwalls=None):
        super()._consequences(s,h,step,duration,record,flowwalls)
        # Direct pane failure remains unchanged by a latch. Only open-window
        # rain entry changes, and raising contents does not remove water.
        open_windows=np.array([0 if ('latch_windows',b['id']) in s.done else 4 for b in self.buildings])
        if duration>0:
            s.metrics['rain_entry_l']+=float((open_windows*.6+s.glassfailed.sum(axis=1)*.5).sum())*s.p.rain_mm_h*duration/3600
        levels=np.array([1. if ('raise_contents',b['id']) in s.done else .05 for b in self.buildings])
        s.metrics['wet_contents']=int((s.interior_depth>levels).sum()*10)
        s.metrics['windborne_objects']=int(s.objectreleased.sum())

    def _result(self,s,peak,residual):
        result=super()._result(s,peak,residual)
        # DS3 and DS4 together hide a real DS4 -> DS3 treatment effect.
        result['metrics']['destroyed_buildings']=sum(b['ds']==4 for b in result['destruction']['buildings'])
        result['metrics']['roof_cover_lost_m2']=sum(b['roof_lost_m2'] for b in result['destruction']['buildings'])
        return result


def resource_profile(model,key):
    p=copy.deepcopy(PROFILES[key]);start=list(risk_assets.FROM(*model.nodes[model.home]))
    counts={};crews=[]
    for role,people in p.pop('roles'):
        counts[role]=counts.get(role,0)+1
        crews.append(dict(id=f'{role}-{counts[role]}',role=role,people=people,position=start))
    return dict(**p,crews=crews,key=key,basis='Explicit resource scenario; availability not verified.',
                travel_basis='Shortest mapped street-graph walking path,1.4 m/s,including return.')


def travel_oracle(model):
    def walk(a,b):
        ax,ay=risk_assets.TO(*a);bx,by=risk_assets.TO(*b)
        na=int(model.ntree.query([ax,ay])[1]);nb=int(model.ntree.query([bx,by])[1])
        return float(model.distances[na,nb]/1.4/60)
    return walk


def catalog(model,baseline,limit=12,diagnostic=None):
    """Real target IDs and full-engine baseline harms select the candidate cut."""
    operations=json.loads((Path(__file__).resolve().parents[1]/'evidence/preventive-work.json').read_text())
    planning=json.loads((Path(__file__).resolve().parents[1]/'evidence/planning-sources.json').read_text())
    operations['sources']+=planning['sources']
    source_ops={o['id']:o for o in operations['operations']}
    eng=baseline['destruction'];tree_fail={x['id']:x for x in eng['trees']}
    buildings={x['id']:x for x in eng['buildings']}
    diag_trees={x['id'] for x in (diagnostic or eng)['trees']}
    diag_buildings={x['id']:x.get('ds',0) for x in (diagnostic or eng)['buildings']}
    struck_trees={x['tree_id'] for x in eng['tree_strikes']}
    wire_trees={x.get('tree_id') for x in eng['power_spans']+eng['ocs_spans']}
    unpowered=set(baseline['frames'][-1]['unpowered_ids']);peak=baseline['peak']
    bindex=cKDTree([b['xy'] for b in model.buildings]);rows=[]
    for kind,operation in OPERATIONS.items():
        op,hazard,title,role,people,minutes,equipment=operation;ranked=[]
        for (k,identity),target in model.targets.items():
            if k!=kind or not np.isfinite(model.distances[model.home,target['node']]):continue
            item=target['item'];d=float(peak[tuple(item['cell'])]) if 'cell' in item else 0
            if kind in ('prune_tree','remove_tree'):
                i=model.tid[identity];failure=tree_fail.get(i)
                score=10000*bool(failure and i not in diag_trees)+5000*(i in struck_trees or i in wire_trees)+1000*bool(failure)+item['height_m']*item['crown_m']
            elif kind=='brace_structure':
                i=model.bid[identity];ds=buildings.get(i,{}).get('ds',0)
                score=10000*(ds-diag_buildings.get(i,0))+1000*ds+item['area_m2']*.01
            elif kind=='backup_power':
                stable=buildings.get(model.bid[identity],{}).get('ds',0)<3
                score=1000*bool(identity in unpowered and d<.8 and stable)+d
            elif kind in ('protect_entrance','raise_contents'):
                stable=buildings.get(model.bid[identity],{}).get('ds',0)<3
                score=1000*bool(.15<d<(.6 if kind=='protect_entrance' else 1.15) and stable)+min(d,2)
            elif kind=='close_route':score=d+.01*item['length_m']
            else:score=item.get('height_m',0)
            ranked.append((score,identity,item,target))
        for score,identity,item,target in sorted(ranked,key=lambda r:r[0],reverse=True)[:limit]:
            b=model.buildings[int(bindex.query(item['xy'] if kind!='close_route' else item['xy'][0])[1])]
            road=min(model.roads,key=lambda r:np.linalg.norm(np.subtract(r['xy'][0],b['xy'])))
            site=dict(position=item['position'],label=road['name']+' vicinity · '+identity,
                kind='tree' if hazard=='trees' else 'road' if hazard=='routes' else 'building',
                building_geometry=b.get('geometry'),building_height_m=b['height_m'],display_height_m=b['height_m'],
                heading=math.degrees(b.get('angle_rad',0)),height_m=item.get('height_m',12),
                road_geometry=item.get('geometry') if hazard=='routes' else None)
            reference=source_ops.get(op,source_ops['prune-defect'] if kind=='remove_tree' else {})
            conditions=list(reference.get('conditions',[]))
            if kind=='backup_power':conditions.append('Compatible transfer connection; one10kW circuit; raised protected equipment;2h fuel')
            if kind=='brace_structure':conditions.append('Pre-engineered roof-connection design and verified substrate; not an arbitrary façade prop')
            if kind=='secure_loose':conditions.append('Hypothetical accessible terrace/roof inventory; no unsafe work at height assumed')
            rows.append(dict(id=op+':'+identity,operation_id=op,kind=kind,target=identity,hazard=hazard,label=title,
                role=role,people=people,minutes=[minutes,minutes],equipment=equipment,site=site,recorded=False,
                source_ids=reference.get('source_ids',[]),evidence=reference.get('evidence','Post-storm mechanism evidence; local effect extrapolated.'),
                conditions=conditions,basis='Same saved storm and source geometry; conditional site/inventory assumptions.',
                native_consequence=hazard in ('trees','structure'),effect_known=True,helps=False,
                exclusive_group=('tree:'+identity if hazard=='trees' else None)))
    return rows,operations['sources']


def evaluate(model,frames,times,ids,candidates,resources):
    receipt=workplan.schedule(ids,candidates,resources,travel_oracle(model))
    rows={a['id']:a for a in candidates}
    selected=[dict(kind=rows[j['id']]['kind'],target=rows[j['id']]['target']) for j in receipt['completed']]
    model.receipt_override=dict(completed=selected,skipped=[],crew_minutes=list(receipt['crew_minutes'].values()),stock_remaining=receipt['stock_remaining'])
    try:result=model.replay(frames,times,selected)
    finally:model.receipt_override=None
    result['execution']=receipt;result['action_ids']=[j['id'] for j in receipt['completed']]
    return result


def states(model,result,action,frames,times):
    """Per-action display is a projection of the paired replay, not new physics."""
    op=action['operation_id'];kind=action['kind'];target=action['target'];done=action['id'] in result['action_ids']
    engine=result['destruction'];bidx=model.bid.get(target);tidx=model.tid.get(target)
    b=next((x for x in engine['buildings'] if x['id']==bidx),None)
    tree=next((x for x in engine['trees'] if x['id']==tidx),None)
    object_event=next((e for e in result['events'] if e['kind']=='windborne_object' and e['id']==target),None)
    item=model.targets[(kind,target)]['item'];out=[]
    for k,t in enumerate(times):
        f=result['frames'][k]
        depth=float(frames[k][tuple(item['cell'])]) if 'cell' in item else 0.
        row=dict(water_m=round(depth,3),broken_panes=0,rain_inside_l=0,tree_fallen=False,collapse_m2=0,
                 pump_on=False,lane_open=True,wet_contents=0,restricted=False,loose_released=0,barrier=False)
        if kind in ('prune_tree','remove_tree'):
            fallen=bool(tree and tree['fail_t']<=t);row['tree_fallen']=fallen
            row['summary']='Assessed tree removed before storm' if kind=='remove_tree' and done else 'Tree falls; cascade replayed' if fallen else 'Tree standing'
        elif kind=='brace_structure':
            ds=max((j+1 for j,at in enumerate(b['ds_t']) if at is not None and at<=t),default=0) if b else 0
            row.update(damage_state=ds,collapse_m2=item['area_m2'] if ds>=3 else 0)
            row['summary']=f'DS{ds}'+(' · modeled destruction/collapse' if ds==4 else ' · major damage remains' if ds==3 else ' · lower damage state')
        elif kind=='secure_loose':
            released=bool(object_event and object_event['time_seconds']<=t);row['loose_released']=int(released)
            row['summary']='Loose object airborne' if released else 'Object secured' if done else 'Loose object on roof'
        elif kind=='latch_windows':
            litres=round(model.world.p.rain_mm_h*.6*(0 if done else 4)*t/3600)
            row['rain_inside_l']=litres;row['summary']=f'{litres} L through assumed open windows · direct wind failure unchanged'
        elif kind in ('protect_entrance','raise_contents'):
            inside=0 if done and kind=='protect_entrance' and depth<=.6 else max(0,depth-.15)
            wet=10 if inside>(1 if done and kind=='raise_contents' else .05) else 0
            row.update(water_m=round(inside,3),wet_contents=wet,barrier=done and kind=='protect_entrance')
            row['summary']=f'{round(inside*100)} cm entry water · {wet} assumed boxes wet'
        elif kind=='backup_power':
            running=done and depth<.8 and target not in f['structure_ids']
            row['pump_on']=running;row['summary']='Critical10kW circuit supplied · public grid unchanged' if running else 'Critical circuit without backup'
        else:
            closed=done;blocked=target in f['blocked_route_ids'];row.update(lane_open=not blocked,restricted=closed)
            row['summary']='Entry restricted · physical hazards remain' if closed else 'Hazardous link open to entry' if blocked else 'Street link open'
        out.append(row)
    return out


def public(binding,label,reference=None):
    """One selected resource plan owns assignment, toggles and consequences."""
    family=reference if reference in binding['profile_labels'].values() else binding['parent_labels'].get(label,binding['selected_label'])
    main=binding['entries'][family];entry=binding['entries'][label];profile=main['profile']
    selected={j['id']:j for j in main['receipt']['completed']};applied=set(entry['action_ids'])
    rows=copy.deepcopy(binding['candidates'])
    for a in rows:
        a['label']=OPERATIONS[a['kind']][2]
        identity=a['id'];a.update(selected=identity in selected,recorded=identity in applied,applied=identity in applied,
            assignment=selected.get(identity),plan_label=family,ablation_label=binding['profile_ablations'][family].get(identity))
        a.update(binding['profile_comparisons'][family].get(identity,{}))
        if identity not in selected:
            feasibility=binding['feasibility'][identity][profile]
            a['not_selected_reason']=feasibility['skipped'][0]['reason'] if feasibility['skipped'] else 'No modeled harm benefit in this storm' if not a['helps'] else 'Argon chose other work after coupled tests'
    source_rows=binding['sources']+json.loads((Path(__file__).resolve().parents[1]/'evidence/planning-sources.json').read_text())['sources']
    sources=list({s['id']:s for s in source_rows}.values())
    return dict(run=binding['run'],model='argon',plan=label,storm_id=binding['storm_id'],weather=binding['weather'],minutes=binding['minutes'],
        hazards=viewer_names(),actions=rows,sources=sources,power_sources=binding['power_sources'],execution=entry['receipt'],
        work_plan=dict(label=family,name=main['name'],model=binding['model'],reasons=[main['reason']],resources=binding['resource_profiles'][profile],
            selected_ids=list(selected),applied_ids=list(applied),current_label=label,input_digest=binding['input_digest']),
        scope='Paired six-hazard replay. Conditions and local treatment benefits are extrapolated; resource availability requires confirmation.')


def viewer_names():
    return {'wind':'Wind','trees':'Falling trees','structure':'Structural collapse','flood':'Flooding','power':'Power loss','routes':'Blocked routes'}
