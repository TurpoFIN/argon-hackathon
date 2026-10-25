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
from types import SimpleNamespace

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra, connected_components
from scipy.spatial import cKDTree
from shapely.geometry import LineString, Point, shape
from shapely.ops import transform

from . import risk_assets
from .destruction_adapter import Inputs
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
    'Three matched capacity cases (0.65, 1.0, 1.35) vary the retained glass and flood-equipment thresholds, not event probabilities. Destruction keeps its archived fragilities; latent samples and weather are reused for every plan.',
    'Tree, building and overhead-network failures use the shared StormSim destruction engine and fixed city seed. DS3 and DS4 count as partial collapse in these metrics.',
    'Wind uses the host potential-flow speedup and exposure-B height profile. Tree snap/uproot uses the archived calibration; dimensions remain DBH-based allometry.',
    'The overhead inventory is OSM; nearest-span service connections and fused sections are engine assumptions, not verified utility topology.',
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
        self.lines=[l for l in assets['power_lines'] if l['installation']=='overhead']
        self.destruction_inputs=Inputs(assets,world)
        self.line_index={line['id']:i for i,line in enumerate(self.lines)}

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

    def _start(self, actions, capacity_case):
        receipt=self.execute(actions);done={(a['kind'],a['target']) for a in receipt['completed']}
        # Exogenous latent samples depend only on the fixed world, never on the plan.
        rng=np.random.default_rng(20261025);nb=len(self.buildings);nt=len(self.trees)
        glass_cap=rng.uniform(1200,2400,(nb,4))*capacity_case
        w=copy.copy(self.world);w.p=copy.copy(w.p);w.clog=w.clog.copy();w.parcels=copy.deepcopy(w.parcels)
        for parcel in w.parcels:parcel['kg']*=max(0,w.p.debris_kg-self.roof_mass)/max(w.p.debris_kg,1e-9)
        w.crews=[];w.events=[];w.frames=[];w.forecasting=True;w.storm_hold=True
        # Entrance barriers, braces and backup are finite local assets.
        barrier=np.array([.6 if ('protect_entrance',b['id']) in done else 0 for b in self.buildings])
        backup=np.array([('backup_power',b['id']) in done for b in self.buildings])
        closed=np.array([('close_route',r['id']) in done for r in self.roads])
        treefailed=np.zeros(nt,bool);structfailed=np.zeros(nb,bool);glassfailed=np.zeros((nb,4),bool);objectreleased=np.zeros(len(self.objects),bool)
        stationfailed=np.zeros(len(self.stations),bool);linedown=np.zeros(len(self.lines),bool);struck=np.zeros(nb,bool)
        flooded=np.zeros(nb,bool);interior_depth=np.zeros(nb)
        metrics=dict(route_hazard_minutes=0.,isolated_building_minutes=0.,unserved_load_hours=0.,interior_water_m3=0.)
        events=[];frames=[];tree_road=np.zeros(len(self.roads),bool);rubble_road=np.zeros(len(self.roads),bool)
        bangles=np.array([b['angle_rad'] for b in self.buildings]);road_mid=np.array([r['xy'][len(r['xy'])//2] for r in self.roads]);roadtree=cKDTree(road_mid)
        destruction_result=None
        theta=math.radians(p.wind_from_deg if (p:=w.p) else 0);direction=np.array([-math.sin(theta),-math.cos(theta)])
        return SimpleNamespace(**{k:v for k,v in locals().items() if k!='self'})

    def _consequences(self, s, h, step, duration, record=True, flowwalls=None):
        gust=s.p.gust_mps*(.6+.4*math.sin(math.pi*min(step,3600)/3600))
        q=.613*gust**2
        normals=np.stack([s.bangles+i*math.pi/2 for i in range(4)],axis=1)
        coeff=.8+1.2*np.maximum(0,np.cos(normals-(math.pi/2-s.theta)))**2
        newglass=(q*coeff>s.glass_cap)&~s.glassfailed;s.glassfailed|=newglass
        if s.destruction_result is None:
            _,s.destruction_result=self.destruction_inputs.run(
                np.zeros((31,*s.w.z.shape)),np.arange(0,3601,120),s.receipt['completed'])
        result=s.destruction_result
        # Project the engine's time-stamped failures into this model's existing metrics and graph.
        for failure in result['buildings']:
            i=failure['id'];at=failure['ds_t'][2]
            if at is not None and at<=step and not s.structfailed[i]:
                s.structfailed[i]=True;b=self.buildings[i]
                s.events.append(dict(time_seconds=at,kind='partial_collapse',id=b['id'],position=b['position']))
                # Preserve this model's rubble/transport consequence, triggered only by engine DS3+.
                near=s.roadtree.query_ball_point(b['xy'],max(12,math.sqrt(b['area_m2'])/2))
                s.rubble_road[near]=True
                if flowwalls is not None:
                    for j in near:flowwalls[tuple(self.roads[j]['cell'])]=True
        for failure in result['trees']:
            i=failure['id']
            if failure['fail_t']>step or s.treefailed[i]:continue
            s.treefailed[i]=True;t=self.trees[i]
            s.events.append(dict(time_seconds=failure['fail_t'],kind='tree_fall',id=t['id'],position=t['position'],
                fall_dir_deg=failure['fall_dir_deg'],height_m=t['height_m'],species=t.get('species','unrecorded')))
            idx=int(s.w.drain_tree.query([t['cell'][1],t['cell'][0]])[1])
            crown_area=math.pi*(t['crown_m']/2)**2
            if ('prune_tree',t['id']) in s.done:crown_area*=.75
            s.w.clog[idx]+=min(2.,crown_area*.03)
        for strike in result['tree_strikes']:
            i=strike['bldg_id']
            if strike['t']>step or s.struck[i]:continue
            s.struck[i]=True;s.glassfailed[i,0]=True
            s.events.append(dict(time_seconds=strike['t'],kind='tree_strike',tree_id=self.trees[strike['tree_id']]['id'],
                building_id=self.buildings[i]['id'],position=self.buildings[i]['position']))
        inventory=self.destruction_inputs.assets
        for kind,failures,spans in [('power',result['power_spans'],inventory['power_spans']),
                                    ('trolley',result['ocs_spans'],inventory['ocs_spans'])]:
            for failure in failures:
                if failure['t']>step:continue
                span=spans[failure['id']]
                if kind=='power':
                    original=inventory['power_lines'][span['line_id']]
                else:original=span
                source=original.get('source_id','OSM'+str(original.get('osm_id')))
                j=self.line_index.get(source)
                if j is None or s.linedown[j]:continue
                s.linedown[j]=True
                event=dict(time_seconds=failure['t'],kind='line_down',id=source,line_kind=kind,
                    position=list(risk_assets.FROM(*np.mean([span['a'],span['b']],axis=0))))
                if failure.get('tree_id') is not None and failure['tree_id']>=0:
                    event['tree_id']=self.trees[failure['tree_id']]['id']
                s.events.append(event)
        for block in result['road_blocks']:
            if block['t']>step:continue
            point=Point(block['x'],block['y'])
            for j in s.roadtree.query_ball_point([block['x'],block['y']],block['length_m']+25):
                if self.road_shapes[j].distance(point)<=max(6,block['length_m']/2):
                    s.tree_road[j]=True
        for i,o in enumerate(self.objects):
            if s.objectreleased[i] or ('secure_loose',o['id']) in s.done:continue
            if q*1.2*o['area_m2']<=.5*o['mass_kg']*9.81:continue
            s.objectreleased[i]=True;end=np.array(o['xy'])+s.direction*min(80,gust*o['height_m']/6)
            s.events.append(dict(time_seconds=step,kind='windborne_object',id=o['id'],position=list(risk_assets.FROM(*end))))
            distance,j=self.btree.query(end)
            if distance<30 and .5*o['mass_kg']*(gust*.5)**2>150:s.glassfailed[j,0]=True
            for j in s.roadtree.query_ball_point(end,15):s.rubble_road[j]=True
        waterB=h[self._cellsB];above=np.maximum(0,waterB-.15)
        # Rated barrier overtops; above rating it provides no dry-proofing credit.
        enter=(waterB>.15)&~((s.barrier>0)&(waterB<=s.barrier))
        s.flooded|=enter;s.interior_depth=np.maximum(s.interior_depth,np.where(enter,above,0))
        if self.stations:
            heights=h[self._cellsS]
            s.stationfailed|=heights>(.45*s.capacity_case)
        failednames={station['name'] for i,station in enumerate(self.stations) if s.stationfailed[i] and station.get('name')}
        out=np.array([b.get('substation') in failednames for b in self.buildings])
        # Conditional local service-panel inundation, not an observed utility outage.
        out|=waterB>(.45*s.capacity_case)
        out|=np.array([any(bid==i and at<=step for bid,at in result['outage']['buildings_out_t']) for i in range(len(self.buildings))])
        # CEC transmission-to-distribution topology is unavailable; do not invent cascading customers.
        # 20 kW backup for an assumed 10 kW load; raised pad may itself flood.
        backup_running=s.backup&(waterB<.8)&~s.structfailed
        served=~out|backup_running
        roadwater=h[self._cellsR];danger=s.tree_road|s.rubble_road|(roadwater>.15)
        inaccessible=s.tree_road|s.rubble_road|(roadwater>.3)|s.closed
        rr=[];cc=[]
        for j,(u,v) in enumerate(self.road_ends):
            if not inaccessible[j]:rr.extend((u,v));cc.extend((v,u))
        graph=csr_matrix((np.ones(len(rr)),(rr,cc)),shape=self.graph.shape)
        _,labels=connected_components(graph,directed=False)
        isolated=(labels[self.bnodes]!=labels[self.home])&self.baseline_access
        if duration>0:
            s.metrics['route_hazard_minutes']+=float((danger&~s.closed).sum())*2*(duration/120)
            s.metrics['isolated_building_minutes']+=float(isolated.sum())*2*(duration/120)
            s.metrics['unserved_load_hours']+=float((~served).sum())/30*(duration/120)
        if record:
            s.frames.append(dict(time_seconds=step,down_line_ids=[self.lines[i]['id'] for i in np.where(s.linedown)[0]],
                struck_building_ids=[self.buildings[i]['id'] for i in np.where(s.struck)[0]],tree_ids=[self.trees[i]['id'] for i in np.where(s.treefailed)[0]],
                structure_ids=[self.buildings[i]['id'] for i in np.where(s.structfailed)[0]],
                flooded_ids=[self.buildings[i]['id'] for i in np.where(enter)[0]],
                unpowered_ids=[self.buildings[i]['id'] for i in np.where(~served)[0]],
                blocked_route_ids=[self.roads[i]['id'] for i in np.where(inaccessible)[0]]))

    def _result(self, s, peak, residual):
        glass_area=np.array([b['perimeter_m']*b['height_m']*.35/4 for b in self.buildings])
        s.metrics.update(lines_down=int(s.linedown.sum()),
            trolley_lines_down=sum(bool(s.linedown[i]) for i,l in enumerate(self.lines) if l.get('kind')=='trolley'),
            tree_strikes=sum(e['kind']=='tree_strike' for e in s.events),trees_failed=int(s.treefailed.sum()),partial_collapses=int(s.structfailed.sum()),
            damaged_envelope_m2=float((s.glassfailed*glass_area[:,None]).sum()),flooded_entrances=int(s.flooded.sum()),
            interior_water_m3=float(sum(s.interior_depth[i]*b['area_m2'] for i,b in enumerate(self.buildings))),
            failed_substations=int(s.stationfailed.sum()),crew_minutes=float(sum(s.receipt['crew_minutes'])),
            completed_actions=len(s.receipt['completed']),mass_balance_error_m3=residual)
        s.events.sort(key=lambda e:(e['time_seconds'],e['kind'],str(e.get('id',''))))
        return dict(metrics=s.metrics,receipt=s.receipt,frames=s.frames,events=s.events,peak=peak,destruction=s.destruction_result)

    def replay(self, frames, frame_seconds, actions=(), capacity_case=1.):
        """Replay the shared consequences; rubble does not alter water in replay.

        Input depths and times belong to the existing water simulation. Its mass
        balance is not recomputed here; mass_balance_error_m3 is unavailable.
        """
        if len(frames)!=len(frame_seconds) or not len(frames):raise ValueError('Depth frames and times must match')
        times=np.asarray(frame_seconds,float)
        if not np.isfinite(times).all() or times[0]<0 or np.any(np.diff(times)<=0):raise ValueError('Frame times must increase')
        s=self._start(actions,capacity_case);peak=np.zeros_like(s.w.z)
        _,s.destruction_result=self.destruction_inputs.run(frames,times,s.receipt['completed'])
        for i,(h,step) in enumerate(zip(frames,times)):
            if h.shape!=s.w.z.shape or not np.isfinite(h).all():raise ValueError('Invalid depth frame')
            duration=float(times[i+1]-step) if i+1<len(times) else 0.
            self._consequences(s,h,float(step),duration)
            np.maximum(peak,h,out=peak)
        return self._result(s,peak,None)

    def run(self, actions, capacity_case=1., record=False):
        s=self._start(actions,capacity_case);w=s.w;p=s.p
        h=np.zeros_like(w.z);pilot=w.dynamics.get('coastal');case=w.dynamics.get('coastal_case','manual')
        if pilot:
            tide0=boundary(pilot,case,0)
        else:tide0=w.dynamics['tide_base']
        h[w.ocean]=np.maximum(tide0-w.z[w.ocean],0);budget=Budget(initial_m3=float(h.sum()*144))
        flowwalls=w.walls.copy();peak=h.copy();flood_frames=[]
        for step in range(3601):
            if step%5==0:w.step(step,h);rates=w.rates()
            if step%120==0:
                self._consequences(s,h,step,120 if step<3600 else 0,record,flowwalls)
                flood_frames.append(h.astype('float32').copy())
                rates=w.rates()
            if step==3600:break
            tide=boundary(pilot,case,step,p.coastal_rise_m) if pilot else tide0+min(step/1800,1)*p.coastal_rise_m
            advance_water(h,w.z,flowwalls,w.ocean,w.dynamics['rain'],rates,tide,12,1,budget,p.roughness)
            np.maximum(peak,h,out=peak)
        residual=float(h.sum()*144)-budget.initial_m3-budget.rain_m3+budget.drained_m3-budget.coastal_exchange_m3
        if not np.isfinite(h).all() or abs(residual)>.01:raise ArithmeticError('Multi-hazard water balance failed')
        _,s.destruction_result=self.destruction_inputs.run(flood_frames,np.arange(0,3601,120),s.receipt['completed'])
        return {**self._result(s,peak,residual), 'flood':np.stack(flood_frames) if record else None}


def destruction(model, frames, frame_seconds, actions=()):
    """The exact positional scene export, shared with the two publishers."""
    from .destruction_web import export
    receipt=model.execute(actions)
    storm,result=model.destruction_inputs.run(frames,frame_seconds,receipt['completed'])
    return export(storm,'Shared weather',result,model.destruction_inputs)
