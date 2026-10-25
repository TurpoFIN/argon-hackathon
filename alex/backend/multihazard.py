"""Conditional multi-hazard preparedness experiments on sourced SF geometry.

The numerical model owns consequences. Argon can schedule concrete work only.
Unknown condition/capacity is varied explicitly; this is not a surveyed digital twin.
"""
import copy
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra, connected_components
from scipy.spatial import cKDTree
from shapely.geometry import LineString, Point, shape
from shapely.ops import transform

from . import risk_assets
from .simulation import advance_water, Budget

ROOT=Path(__file__).resolve().parents[1]
KINDS=('secure_loose','prune_tree','brace_structure','protect_entrance','backup_power','close_route')
# Durations and equipment quantities are explicit scenario assumptions, not quotations.
WORK={
    'secure_loose':('general',15, 'tie-down kit'),
    'prune_tree':('arborist',40, 'tree-work kit'),
    'brace_structure':('structural',75, 'four 5 kN anchors'),
    'protect_entrance':('general',25, '0.6 m rated entrance barrier'),
    'backup_power':('electrician',75, '20 kW generator, 2 h fuel'),
    'close_route':('general',12, 'barrier and signage kit'),
}
CREWS=('general','general','arborist','structural','electrician')
LIMITS={'secure_loose':12,'prune_tree':6,'brace_structure':4,'protect_entrance':8,'backup_power':4,'close_route':8}
ASSUMPTIONS=[
    'Conditional stress test: mapped geometry is real; structural condition, load capacities, roof objects, entrance ratings and work durations are scenario inputs, not surveyed findings.',
    'Three matched capacity cases (0.65, 1.0, 1.35) are sensitivity cases, not event probabilities. Same latent strengths and weather are reused for every plan.',
    'Roof/wall assembly failure is modeled for buildings below 20 m. Whole high-rise stability, dynamic response, waves and foundation scour remain outside this model.',
    'Wind loads use q=0.613 V² with directional coefficients. This is a simplified load model, not CFD validation. Tree dimensions use DBH-based allometry; tree condition is unobserved.',
    'Tree overturning capacity is an explicit 35–55 m/s dry-anchorage sensitivity, with crown streamlining and wet-soil reduction. It is not a universal failure threshold or a calibration to SF trees; stem fracture is not resolved.',
    'Only source-classified overhead lines can be struck by trees. PG&E feeder installation type is unknown. Building-to-feeder association is a nearest-route assumption, not a verified customer connection.',
    'Substation equipment height and damage threshold are conditional inputs; no actual outage is asserted. Local islanded backup powers one modeled 10 kW load, not the public network.',
    'Backup candidates assume a compatible pre-installed transfer connection. Bracing assumes an engineered four-anchor kit and access method. These prerequisites are scenario conditions, not facts in GIS.',
    'Route exposure counts hazardous open street-link minutes, not people or injuries. Closures affect access and can worsen isolation. No casualty predictions or dollar-loss claims.',
    'Five crews (two general, one arborist, one structural, one electrician); walking at 1.4 m/s on the street graph. Skills, finite kits, travel, work and return time are enforced before storm onset.',
    'The preparation window is assumed accessible and calm; work requires owner access and approved methods before storm onset. It is not authorization or a real work order.',
    'Up to 24 hypothetical 3 kg roof objects are allocated from the loose-debris slider inventory; the remainder is surface litter. Their locations are scenario placements, not observations.',
]


def make_model(parameters, preparation_minutes=120):
    """Use committed numerical core without depending on another chat's working files."""
    from types import SimpleNamespace
    from .benefit import load_baseline
    from .simulation import potential_wind
    w,_,_,_=load_baseline()
    old=w.p;w.p=SimpleNamespace(**{**vars(old),**parameters,'response_mode':'baseline'})
    w.u,w.v=potential_wind(w.walls,w.p.wind_mps,w.p.wind_from_deg,12)
    w.clog*=w.p.blockage_pct/max(old.blockage_pct,1e-9)
    for item in w.parcels:item['kg']*=w.p.debris_kg/max(old.debris_kg,1e-9)
    with np.load(ROOT/'data/model-inputs.npz') as f:rain=f['runoff_weights']*w.p.rain_mm_h/1000/3600*w.p.runoff_coefficient
    pilot=None
    if parameters.get('coastal_case','manual')!='manual':
        pilot=json.loads((ROOT/'data/coastal/pilot.json').read_text())
    w.dynamics={'rain':rain,'tide_base':json.loads((ROOT/'data/manifest.json').read_text())['tide_base_navd88_m'],
                'coastal':pilot,'coastal_case':parameters.get('coastal_case','manual')}
    return Model(risk_assets.load(),w,preparation_minutes)


def boundary(pilot,case,seconds,added_rise=0):
    profile=next(c for c in pilot['cases'] if c['id']==case)
    return float(np.interp(seconds,profile['times_seconds'],profile['water_level_m']))+min(seconds/1800,1)*added_rise


class Model:
    def __init__(self, assets, world, preparation_minutes=120):
        self.a=assets; self.world=world; self.minutes=preparation_minutes
        self.buildings=assets['buildings'];self.trees=assets['trees'];self.roads=assets['roads'];self.stations=assets['substations']
        self.B=np.array([b['xy'] for b in self.buildings]);self.T=np.array([t['xy'] for t in self.trees])
        self.bid={b['id']:i for i,b in enumerate(self.buildings)};self.tid={t['id']:i for i,t in enumerate(self.trees)}
        self.rid={r['id']:i for i,r in enumerate(self.roads)}
        self.btree=cKDTree(self.B);self.road_shapes=[LineString(r['xy']) for r in self.roads]
        self.nodes=[];lookup={};edges=[];self.road_ends=[]
        for r in self.roads:
            pair=[]
            for xy in (r['xy'][0],r['xy'][-1]):
                key=tuple(round(v/2)*2 for v in xy)
                if key not in lookup:lookup[key]=len(self.nodes);self.nodes.append(xy)
                pair.append(lookup[key])
            self.road_ends.append(pair);edges.append((pair[0],pair[1],max(1,r['length_m'])))
        rows=[];cols=[];data=[]
        for u,v,length in edges:rows.extend((u,v));cols.extend((v,u));data.extend((length,length))
        self.graph=csr_matrix((data,(rows,cols)),shape=(len(self.nodes),len(self.nodes)))
        self.ntree=cKDTree(self.nodes);self.bnodes=self.ntree.query(self.B)[1]
        # Largest road component and highest terrain node give a fixed, real-geometry staging proxy.
        _,labels=connected_components(self.graph,directed=False)
        component=np.argmax(np.bincount(labels))
        candidates=np.where(labels==component)[0]
        self.home=int(max(candidates,key=lambda n:self.world.z[self.cell(self.nodes[n])]))
        self.baseline_access=labels[self.bnodes]==labels[self.home]
        self.distances=dijkstra(self.graph,directed=False)
        self.objects=[]
        # Explicit hypothetical inventory; never described as image-detected objects.
        object_count=min(24,math.ceil(max(0,self.world.p.debris_kg)/3))
        for j,b in enumerate(self.buildings[::max(1,len(self.buildings)//24)][:object_count]):
            self.objects.append(dict(id=f'O{j+1}',building_id=b['id'],xy=b['xy'],position=b['position'],
                height_m=min(b['height_m'],20),mass_kg=min(3.,self.world.p.debris_kg/max(1,object_count)),area_m2=.5))
        self.roof_mass=sum(o['mass_kg'] for o in self.objects)
        self.oid={o['id']:i for i,o in enumerate(self.objects)}
        self.targets={}
        backup_sites=[b for b in self.buildings if int(hashlib.sha256(b['id'].encode()).hexdigest()[:4],16)%4==0]
        # Unit fixtures can explicitly declare a ready connection.
        backup_sites.extend(b for b in self.buildings if b.get('backup_ready') and b not in backup_sites)
        for kind,items in [('secure_loose',self.objects),('prune_tree',self.trees),('brace_structure',[b for b in self.buildings if b['height_m']<20]),('protect_entrance',self.buildings),('backup_power',backup_sites),('close_route',self.roads)]:
            for item in items:
                xy=item['xy'] if kind!='close_route' else item['xy'][0]
                self.targets[(kind,item['id'])]={'item':item,'node':int(self.ntree.query(xy)[1])}
        self._cellsB=tuple(np.array([b['cell'] for b in self.buildings]).T)
        self._cellsR=tuple(np.array([r['cell'] for r in self.roads]).T)
        self._cellsS=tuple(np.array([s['cell'] for s in self.stations]).T) if self.stations else None
        self.overhead=[transform(risk_assets.TO,shape(l['geometry'])) for l in assets['power_lines'] if l['installation']=='overhead']

    def cell(self,xy):
        x0,y0=self.world.lon[0,0],self.world.lat[0,0]
        # Find nearest actual grid cell; used only for a few graph staging nodes.
        lo,la=risk_assets.FROM(*xy)
        i=int(np.argmin((self.world.lon-lo)**2+(self.world.lat-la)**2))
        return np.unravel_index(i,self.world.z.shape)

    def execute(self, actions):
        clocks=np.zeros(len(CREWS));positions=np.full(len(CREWS),self.home);stock=dict(LIMITS)
        done=[];skipped=[];seen=set()
        for action in actions:
            kind,target=action['kind'],action['target']
            if (kind,target) not in self.targets:raise ValueError('Unknown or incompatible action target')
            if (kind,target) in seen:raise ValueError('Duplicate preventive action')
            seen.add((kind,target));role,duration,kit=WORK[kind];node=self.targets[(kind,target)]['node']
            options=[]
            for c,r in enumerate(CREWS):
                travel=self.distances[positions[c],node]/1.4/60
                back=self.distances[node,self.home]/1.4/60
                if r==role and np.isfinite(travel+back) and clocks[c]+travel+duration+back<=self.minutes:
                    options.append((clocks[c]+travel+duration,c,travel,back))
            if not options or stock[kind]<=0:
                skipped.append({**action,'reason':'Insufficient time, accessible route, qualified crew or equipment'});continue
            finish,c,travel,back=min(options);clocks[c]=finish;positions[c]=node;stock[kind]-=1
            done.append({**action,'crew':c+1,'role':role,'work_minutes':duration,'travel_minutes':round(travel,2),
                         'finish_minutes_before_storm':round(self.minutes-finish,2),'kit':kit})
        for c in range(len(CREWS)):clocks[c]+=self.distances[positions[c],self.home]/1.4/60
        return dict(completed=done,skipped=skipped,crew_minutes=clocks.tolist(),stock_remaining=stock)

    def candidates(self, peak):
        p=self.world.p;theta=math.radians(p.wind_from_deg)
        result={}
        for kind in KINDS:
            values=[]
            for (k,key),t in self.targets.items():
                if k!=kind:continue
                item=t['item'];node=t['node'];distance=self.distances[self.home,node]
                if not np.isfinite(distance):continue
                if kind=='secure_loose':score=item['height_m'];basis='Hypothetical unsecured roof object'
                elif kind=='prune_tree':score=item['height_m']*item['crown_m'];basis='Mapped species/DBH; condition unobserved'
                elif kind=='brace_structure':score=.613*p.gust_mps**2/math.sqrt(item['area_m2']);basis='Conditional low-rise roof/wall connection capacity'
                elif kind in ('protect_entrance','backup_power'):
                    depth=float(peak[tuple(item['cell'])]);rating=.6 if kind=='protect_entrance' else .8
                    threshold=.15 if kind=='protect_entrance' else .45
                    score=depth if threshold<depth<rating else 0.
                    basis=f'Predicted depth {depth:.3f} m; intervention rating {rating:g} m; service threshold {threshold:g} m'
                else:score=float(peak[tuple(item['cell'])])+.01*item['length_m'];basis='Mapped street; modeled flood and exposure'
                values.append(dict(id=key,position=item['position'],priority=round(score,3),basis=basis,
                    outbound_walk_minutes=round(distance/1.4/60,2),work_minutes=WORK[kind][1],required_skill=WORK[kind][0]))
            result[kind]=sorted(values,key=lambda t:t['priority'],reverse=True)[:16]
        return result

    def run(self, actions, capacity_case=1., record=False):
        receipt=self.execute(actions);done={(a['kind'],a['target']) for a in receipt['completed']}
        # Exogenous latent samples depend only on the fixed world, never on the plan.
        rng=np.random.default_rng(20261025);nb=len(self.buildings);nt=len(self.trees)
        glass_cap=rng.uniform(1200,2400,(nb,4))*capacity_case
        structure_cap=rng.uniform(500,1800,nb)*capacity_case
        dry_anchorage_gust=rng.uniform(35,55,nt)
        w=copy.copy(self.world);w.p=copy.copy(w.p);w.clog=w.clog.copy();w.parcels=copy.deepcopy(w.parcels)
        for parcel in w.parcels:parcel['kg']*=max(0,w.p.debris_kg-self.roof_mass)/max(w.p.debris_kg,1e-9)
        w.crews=[];w.events=[];w.frames=[];w.forecasting=True;w.storm_hold=True
        # Entrance barriers, braces and backup are finite local assets.
        barrier=np.array([.6 if ('protect_entrance',b['id']) in done else 0 for b in self.buildings])
        brace=np.array([20000/b['area_m2'] if ('brace_structure',b['id']) in done else 0 for b in self.buildings])
        backup=np.array([('backup_power',b['id']) in done for b in self.buildings])
        closed=np.array([('close_route',r['id']) in done for r in self.roads])
        treefailed=np.zeros(nt,bool);structfailed=np.zeros(nb,bool);glassfailed=np.zeros((nb,4),bool);objectreleased=np.zeros(len(self.objects),bool)
        stationfailed=np.zeros(len(self.stations),bool);linehit=False
        h=np.zeros_like(w.z);pilot=w.dynamics.get('coastal');case=w.dynamics.get('coastal_case','manual')
        if pilot:
            tide0=boundary(pilot,case,0)
        else:tide0=w.dynamics['tide_base']
        h[w.ocean]=np.maximum(tide0-w.z[w.ocean],0);budget=Budget(initial_m3=float(h.sum()*144))
        flowwalls=w.walls.copy();peak=h.copy();flooded=np.zeros(nb,bool);interior_depth=np.zeros(nb)
        metrics=dict(route_hazard_minutes=0.,isolated_building_minutes=0.,unserved_load_hours=0.,interior_water_m3=0.)
        events=[];frames=[];flood_frames=[];tree_road=np.zeros(len(self.roads),bool);rubble_road=np.zeros(len(self.roads),bool)
        bangles=np.array([b['angle_rad'] for b in self.buildings]);road_mid=np.array([r['xy'][len(r['xy'])//2] for r in self.roads]);roadtree=cKDTree(road_mid)
        theta=math.radians(p.wind_from_deg if (p:=w.p) else 0);direction=np.array([-math.sin(theta),-math.cos(theta)])
        for step in range(3601):
            if step%5==0:w.step(step,h);rates=w.rates()
            if step%120==0:
                gust=p.gust_mps*(.6+.4*math.sin(math.pi*min(step,3600)/3600))
                q=.613*gust**2
                normals=np.stack([bangles+i*math.pi/2 for i in range(4)],axis=1)
                coeff=.8+1.2*np.maximum(0,np.cos(normals-(math.pi/2-theta)))**2
                newglass=(q*coeff>glass_cap)&~glassfailed;glassfailed|=newglass
                roofcoeff=1.4+glassfailed.any(axis=1)*.5
                newstruct=(q*roofcoeff>structure_cap+brace)&~structfailed&np.array([b['height_m']<20 for b in self.buildings])
                for i in np.where(newstruct)[0]:
                    structfailed[i]=True;b=self.buildings[i];events.append(dict(time_seconds=step,kind='partial_collapse',id=b['id'],position=b['position']))
                    near=roadtree.query_ball_point(b['xy'],max(12,math.sqrt(b['area_m2'])/2))
                    rubble_road[near]=True
                    for j in near:flowwalls[tuple(self.roads[j]['cell'])]=True
                for i,t in enumerate(self.trees):
                    if treefailed[i]:continue
                    original_area=math.pi*(t['crown_m']/2)**2
                    crown_area=original_area
                    if ('prune_tree',t['id']) in done:crown_area*=.75
                    # Explicit capacity scenario: do not transfer UK conifer pull coefficients
                    # or a universal stem-break threshold to uninspected urban trees.
                    streamlining=(max(gust,10)/10)**(-.7)
                    moment=q*.5*streamlining*crown_area*.6*t['height_m']
                    saturation=min(1,p.rain_mm_h/85+float(h[tuple(t['cell'])]))
                    critical=dry_anchorage_gust[i]
                    root_capacity=.613*critical**2*.5*(critical/10)**(-.7)*original_area*.6*t['height_m']*(1-.4*saturation)*capacity_case
                    if moment<=root_capacity:continue
                    treefailed[i]=True;events.append(dict(time_seconds=step,kind='tree_fall',id=t['id'],position=t['position']))
                    line=LineString([t['xy'],(np.array(t['xy'])+direction*t['height_m']).tolist()])
                    for j in roadtree.query_ball_point(t['xy'],t['height_m']+25):
                        if self.road_shapes[j].distance(line)<3:tree_road[j]=True
                    for j in self.btree.query_ball_point(t['xy'],t['height_m']+10):
                        if line.distance(Point(self.B[j]))<max(3,math.sqrt(self.buildings[j]['area_m2'])/3):glassfailed[j,0]=True
                    if any(line.distance(power)<3 for power in self.overhead):linehit=True
                    # Finite new leaf inventory; explicitly accounted separately from initial litter.
                    idx=int(w.drain_tree.query([t['cell'][1],t['cell'][0]])[1]);w.clog[idx]+=min(2.,crown_area*.03)
                for i,o in enumerate(self.objects):
                    if objectreleased[i] or ('secure_loose',o['id']) in done:continue
                    if q*1.2*o['area_m2']<=.5*o['mass_kg']*9.81:continue
                    objectreleased[i]=True;end=np.array(o['xy'])+direction*min(80,gust*o['height_m']/6)
                    events.append(dict(time_seconds=step,kind='windborne_object',id=o['id'],position=list(risk_assets.FROM(*end))))
                    distance,j=self.btree.query(end)
                    if distance<30 and .5*o['mass_kg']*(gust*.5)**2>150:glassfailed[j,0]=True
                    for j in roadtree.query_ball_point(end,15):rubble_road[j]=True
                waterB=h[self._cellsB];above=np.maximum(0,waterB-.15)
                # Rated barrier overtops; above rating it provides no dry-proofing credit.
                enter=(waterB>.15)&~((barrier>0)&(waterB<=barrier))
                flooded|=enter;interior_depth=np.maximum(interior_depth,np.where(enter,above,0))
                if self.stations:
                    heights=h[self._cellsS]
                    stationfailed|=heights>(.45*capacity_case)
                failednames={s['name'] for i,s in enumerate(self.stations) if stationfailed[i] and s.get('name')}
                out=np.array([b.get('substation') in failednames for b in self.buildings])
                # Conditional local service-panel inundation, not an observed utility outage.
                out|=waterB>(.45*capacity_case)
                # CEC transmission-to-distribution topology is unavailable; do not invent cascading customers.
                # 20 kW backup for an assumed 10 kW load; raised pad may itself flood.
                backup_running=backup&(waterB<.8)&~structfailed
                served=~out|backup_running
                roadwater=h[self._cellsR];danger=tree_road|rubble_road|(roadwater>.15)
                inaccessible=tree_road|rubble_road|(roadwater>.3)|closed
                rr=[];cc=[]
                for j,(u,v) in enumerate(self.road_ends):
                    if not inaccessible[j]:rr.extend((u,v));cc.extend((v,u))
                graph=csr_matrix((np.ones(len(rr)),(rr,cc)),shape=self.graph.shape)
                _,labels=connected_components(graph,directed=False)
                isolated=(labels[self.bnodes]!=labels[self.home])&self.baseline_access
                if step<3600:
                    metrics['route_hazard_minutes']+=float((danger&~closed).sum())*2
                    metrics['isolated_building_minutes']+=float(isolated.sum())*2
                    metrics['unserved_load_hours']+=float((~served).sum())/30
                if record:
                    flood_frames.append(h.astype('float32').copy())
                    frames.append(dict(time_seconds=step,tree_ids=[self.trees[i]['id'] for i in np.where(treefailed)[0]],
                        structure_ids=[self.buildings[i]['id'] for i in np.where(structfailed)[0]],
                        flooded_ids=[self.buildings[i]['id'] for i in np.where(enter)[0]],
                        unpowered_ids=[self.buildings[i]['id'] for i in np.where(~served)[0]],
                        blocked_route_ids=[self.roads[i]['id'] for i in np.where(inaccessible)[0]]))
                rates=w.rates()
            if step==3600:break
            tide=boundary(pilot,case,step,p.coastal_rise_m) if pilot else tide0+min(step/1800,1)*p.coastal_rise_m
            advance_water(h,w.z,flowwalls,w.ocean,w.dynamics['rain'],rates,tide,12,1,budget,p.roughness)
            np.maximum(peak,h,out=peak)
        residual=float(h.sum()*144)-budget.initial_m3-budget.rain_m3+budget.drained_m3-budget.coastal_exchange_m3
        if not np.isfinite(h).all() or abs(residual)>.01:raise ArithmeticError('Multi-hazard water balance failed')
        glass_area=np.array([b['perimeter_m']*b['height_m']*.35/4 for b in self.buildings])
        metrics.update(trees_failed=int(treefailed.sum()),partial_collapses=int(structfailed.sum()),
            damaged_envelope_m2=float((glassfailed*glass_area[:,None]).sum()),flooded_entrances=int(flooded.sum()),
            interior_water_m3=float(sum(interior_depth[i]*b['area_m2'] for i,b in enumerate(self.buildings))),
            failed_substations=int(stationfailed.sum()),crew_minutes=float(sum(receipt['crew_minutes'])),
            completed_actions=len(receipt['completed']),mass_balance_error_m3=residual)
        return dict(metrics=metrics,receipt=receipt,frames=frames,events=events,peak=peak,
                    flood=np.stack(flood_frames) if record else None)
