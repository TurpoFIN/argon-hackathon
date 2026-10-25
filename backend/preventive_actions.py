"""Finite pre-storm work plans for the benefit audit; no weather changes.

Routes use the same four-neighbour open-land grid as the engine. Additional teams
start at the original two crew depots. Pump capacity/setup are disclosed scenario
assumptions, not procurement or engineering recommendations.
"""
from collections import OrderedDict
import copy
import math
import numpy as np
from scipy.ndimage import maximum_filter, uniform_filter
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra
from pydantic import BaseModel,ConfigDict,Field,model_validator
from typing import Literal


class ActionPlan(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name:str=Field(min_length=1,max_length=100)
    strategy:Literal['shallow_drain_clearing','throughput_drain_clearing','temporary_pumps','clearing_and_pumps']
    crews:Literal[2,8,20,40]
    lead_minutes:Literal[30,120]
    pump_count:int=Field(ge=0,le=8)
    pump_lps:Literal[100,500]

    @model_validator(mode='after')
    def resources(self):
        if self.pump_count>self.crews:raise ValueError('At least one team per pump setup')
        pumping=self.strategy in ('temporary_pumps','clearing_and_pumps')
        if pumping != bool(self.pump_count):raise ValueError('Pump count must match the strategy')
        return self


class ActionExperiment(BaseModel):
    model_config=ConfigDict(extra='forbid')
    plans:list[ActionPlan]=Field(min_length=3,max_length=5)


def implement(base,control_fields,plan):
    """Return an actual prepared state and a bounded work receipt."""
    w=copy.copy(base);w.p=copy.copy(base.p)
    w.clog=base.clog.copy();w.parcels=copy.deepcopy(base.parcels)
    w.crews=[];w.events=[]
    blocked=base.blocked.copy()
    for item in base.evidence:
        if not item.get('included') or item.get('status')!='ready':continue
        if any(h['confidence']>=.7 and h['kind'] in item['expected_hazards'] for h in item['interpretation']['hazards']):
            lon,lat=item['center']
            blocked|=np.hypot((base.lon-lon)*111320*np.cos(np.deg2rad(lat)),(base.lat-lat)*111320)<=item['radius_m']
    ny,nx=blocked.shape;size=blocked.size
    cells=np.arange(size).reshape(ny,nx)
    edge_a=[];edge_b=[]
    for a,b in ((cells[:,:-1],cells[:,1:]),(cells[:-1,:],cells[1:,:])):
        valid=~blocked.ravel()[a]&~blocked.ravel()[b]
        edge_a.extend(a[valid]);edge_b.extend(b[valid])
    a=np.asarray(edge_a);b=np.asarray(edge_b)
    graph=coo_matrix((np.ones(len(a)*2),(np.r_[a,b],np.r_[b,a])),shape=(size,size)).tocsr()
    safe=(control_fields['peak']<=.15)&~blocked
    if not safe.any():raise ValueError('No dry staging cells')
    to_safe,_,safe_sources=dijkstra(graph,directed=False,indices=np.flatnonzero(safe),min_only=True,unweighted=True,return_predecessors=True)
    targets=np.array([np.ravel_multi_index(d['cell'],blocked.shape) for d in w.drains])
    eligible=~blocked.ravel()[targets]
    depth=maximum_filter(control_fields['peak'],size=5)
    duration=uniform_filter(control_fields['flood_duration'].astype(float),size=5)
    shallow=(depth>.05)&(depth<=.35)
    score=duration.ravel()[targets]*shallow.ravel()[targets]
    if plan.strategy=='throughput_drain_clearing':
        score=uniform_filter(control_fields['peak'],size=5).ravel()[targets]*1000+duration.ravel()[targets]
    # Pump intakes prefer persistent wet pockets, not merely a deep coastal cell.
    pump_score=duration.ravel()[targets]*np.minimum(depth.ravel()[targets],.5)
    distances=OrderedDict()
    def distances_from(source):
        if source not in distances:
            distances[source]=dijkstra(graph,directed=False,indices=source,unweighted=True)
            if len(distances)>64:distances.popitem(last=False)
        return distances[source]
    origins=[np.ravel_multi_index((round(c['y']),round(c['x'])),blocked.shape) for c in base.crews]
    if not origins:raise ValueError('Original crew depots unavailable')
    workers=[dict(id='Team '+str(i+1),cell=origins[i%len(origins)],seconds=0.,cleared=0.) for i in range(plan.crews)]
    budget=plan.lead_minutes*60;actions=[];assigned=set();pumps=[]
    def best(worker,ranking,setup):
        travel=distances_from(worker['cell'])[targets]*12/1.4
        reserve=to_safe[targets]*12/1.4
        feasible=eligible & np.isfinite(travel) & np.isfinite(reserve) & (travel+reserve+setup+worker['seconds']<=budget)
        feasible &= np.array([i not in assigned for i in range(len(targets))])
        utility=np.where(feasible,ranking/(travel+setup+60),-1.)
        index=int(np.argmax(utility))
        return (index,float(travel[index])) if utility[index]>0 else None
    for worker in workers[:plan.pump_count]:
        choice=best(worker,pump_score,600)
        if choice:
            i,travel=choice;assigned.add(i)
            worker['seconds']+=travel+600;worker['cell']=int(targets[i]);pumps.append(w.drains[i]['cell'])
            actions.append(dict(team=worker['id'],kind='pump_setup',target=w.drains[i]['id'],travel_seconds=travel,work_seconds=600,completed_before_storm_seconds=budget-worker['seconds']))
    if plan.strategy!='temporary_pumps':
        # Every real inlet can be chosen once; no model-selected 16-inlet cap.
        for _ in range(len(w.drains)):
            choices=[]
            for wi,worker in enumerate(workers):
                option=best(worker,score,30)
                if option:
                    i,travel=option;choices.append((worker['seconds']+travel,wi,i,travel))
            if not choices:break
            _,wi,i,travel=min(choices)
            assigned.add(i);worker=workers[wi]
            needed=float(w.clog[i])/.025
            reserve=float(to_safe[targets[i]])*12/1.4
            work=min(needed,max(0,budget-worker['seconds']-travel-reserve))
            removed=min(float(w.clog[i]),work*.025)
            w.clog[i]-=removed;worker['cleared']+=removed;worker['seconds']+=travel+work;worker['cell']=int(targets[i])
            actions.append(dict(team=worker['id'],kind='initial_clog_clearing',target=w.drains[i]['id'],travel_seconds=travel,work_seconds=work,removed_kg=removed,completed_before_storm_seconds=budget-worker['seconds']))
    for worker in workers:
        reserve=float(to_safe[worker['cell']])*12/1.4
        if not math.isfinite(reserve):raise ValueError('Staging unreachable')
        worker['seconds']+=reserve;worker['cell']=int(safe_sources[worker['cell']])
        if worker['seconds']>budget+1e-6:raise AssertionError('Preparation exceeds work budget')
    receipt=dict(plan=plan.model_dump(),completed_clearings=sum(a['kind']=='initial_clog_clearing' and a['removed_kg']>0 for a in actions),
        removed_clog_kg=sum(worker['cleared'] for worker in workers),installed_pumps=len(pumps),
        crew_seconds=[round(worker['seconds'],3) for worker in workers],actions=actions,
        staged_teams=[dict(id=worker['id'],cell=[int(x) for x in np.unravel_index(worker['cell'],blocked.shape)]) for worker in workers],
        assumptions={'walk_mps':1.4,'clear_kg_s':.025,'pump_setup_seconds':600,'pump_capacity_lps':plan.pump_lps,
            'pump_discharge':'External sink, independent of gravity tide lock. No hose friction, head curve, fuel or offsite receiving-capacity model.',
            'extra_team_origins':'Original two depots; staffing and pump inventories are scenario resource changes.',
            'ranking':'Deterministic geodesic routing; Argon chooses the experimental policy and resources. No globally optimal route claim.'})
    return w,pumps,receipt
