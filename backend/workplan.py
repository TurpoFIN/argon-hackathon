"""One action-ID contract for Argon, scheduling, simulation and the viewer.

The model orders real candidate IDs. Python owns feasibility and consequences.
Historical flood rounds remain archived results, never silently reinterpreted.
"""
import copy
import hashlib
import json
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .viewer_actions import metres

VERSION = 'action-workplan-1'
CONSUMED = {'tie_down', 'fitted_barrier', 'empty_sandbag', 'sand_fill', 'pump',
            'backup_circuit', 'designed_support', 'traffic_set', 'generator_20kw', 'fuel_2h'}


class Proposal(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=100)
    action_ids: list[str] = Field(max_length=48)
    rationale: str = Field(min_length=1, max_length=1600)


class Alternatives(BaseModel):
    model_config = ConfigDict(extra='forbid')
    plans: list[Proposal] = Field(min_length=2, max_length=3)

    @model_validator(mode='after')
    def distinct(self):
        if len({tuple(p.action_ids) for p in self.plans})!=len(self.plans):
            raise ValueError('Alternatives must contain different ordered action IDs')
        return self


class Decision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    selected_plan: str
    reasons: list[str] = Field(min_length=1, max_length=8)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def resources(world, minutes=30):
    """Keep the run's recorded general crews/window; staged stock is assumed.

    No arborist, engineer, electrician or traffic crew is invented. Their work
    remains a visible alternative that cannot be selected under this roster.
    """
    return dict(minutes=minutes,
        crews=[dict(id=c['id'], role='general', people=2, position=world.crew_point(c)) for c in world.crews],
        equipment=dict(handtool_kit=2, tie_down=20, cart=1, fitted_barrier=1,
                       empty_sandbag=100, sand_fill=1, pump=8),
        basis='Scenario stock, not verified availability: two hand-tool kits, 20 ties, one cart, one pre-fitted barrier, 100 bags/fill and eight pumps. Crew count and positions come from the saved world.',
        travel_basis='Grid walking route at 1.4 m/s, including return to starting location; dry pre-storm access is assumed.')


def schedule(ids, candidates, res, travel=None):
    """Deterministic finite crews, upper work times, stock and reusable tools.

    travel(a,b) is injected from the actual simulation grid. Tests can supply a
    bounded distance oracle. Returns skipped jobs rather than granting credit.
    """
    rows={a['id']:a for a in candidates}
    if len(ids)!=len(set(ids)): raise ValueError('Duplicate action ID')
    if set(ids)-rows.keys(): raise ValueError('Unknown action ID')
    travel=travel or (lambda a,b: metres(a,b)*1.35/1.4/60)
    clocks={c['id']:0. for c in res['crews']}
    positions={c['id']:c['position'] for c in res['crews']}
    stock=dict(res['equipment'])
    tools={k:[[] for _ in range(q)] for k,q in stock.items() if k not in CONSUMED}
    done=[]; skipped=[]; groups=set()
    for identity in ids:
        a=rows[identity];role='general' if a['role']=='pump crew' else a['role']
        if a.get('exclusive_group') and a['exclusive_group'] in groups:
            skipped.append(dict(id=identity,reason='Conflicting treatment of the same asset'));continue
        if any(stock.get(k,0)<q for k,q in a['equipment'].items()):
            skipped.append(dict(id=identity,reason='Equipment unavailable'));continue
        options=[]
        for crew in res['crews']:
            key=crew['id']
            if role!=crew['role'] or a['people']>crew['people']: continue
            walking=travel(positions[key],a['site']['position'])
            back=travel(a['site']['position'],crew['position'])
            duration=walking+a['minutes'][1];start=clocks[key]
            if duration==float('inf') or back==float('inf'):continue
            while True:
                allocations=[];bump=None
                for k,q in a['equipment'].items():
                    if k in CONSUMED:continue
                    free=[];conflicts=[]
                    for i,bookings in enumerate(tools.get(k,[])):
                        overlap=[e for b,e in bookings if b<start+duration and e>start]
                        if overlap:conflicts.extend(overlap)
                        else:free.append(i)
                    if len(free)<q:
                        bump=min(conflicts) if conflicts else float('inf');break
                    allocations.append((k,free[:q]))
                if bump is None:break
                start=bump
                if start==float('inf'):break
            finish=start+duration
            if finish+back<=res['minutes']:
                options.append((finish,key,start,walking,back,allocations))
        if not options:
            skipped.append(dict(id=identity,reason='No qualified crew / accessible route / time including return'));continue
        finish,key,start,walking,back,allocations=min(options)
        for k,q in a['equipment'].items():
            if k in CONSUMED:stock[k]-=q
        for k,slots in allocations:
            for i in slots:tools[k][i].append((start,finish))
        clocks[key]=finish;positions[key]=a['site']['position']
        if a.get('exclusive_group'):groups.add(a['exclusive_group'])
        done.append(dict(id=identity,priority=len(done)+1,crew_id=key,start=round(start,2),finish=round(finish,2),
                         travel_minutes=round(walking,2),return_minutes=round(back,2),work_minutes=a['minutes'][1],
                         conditions=a.get('conditions',[]),status='Planned completion; site conditions need inspection'))
    for crew in res['crews']:
        key=crew['id'];clocks[key]+=travel(positions[key],crew['position'])
    return dict(completed=done,skipped=skipped,crew_minutes={k:round(v,2) for k,v in clocks.items()},stock_remaining=stock)


def action_catalog(pack, reference_pack, world=None, control_layers=None):
    """Baseline candidates plus every archived placement, without crediting it.

    Old pump sites are useful candidates. Their old completion and loss savings
    are not inherited by the newly scheduled work plan.
    """
    rows={a['id']:copy.deepcopy(a) for a in reference_pack['actions']}
    rows.update({a['id']:copy.deepcopy(a) for a in pack['actions']})
    for a in rows.values():
        a['recorded']=False
        if a['operation_id']=='portable-pump':a['role']='general'
    if world is not None and control_layers is not None:
        # The old top-loss excerpt omitted feasible jobs near the actual crews.
        # Expand from the full NSI/real-drain inventory, then apply travel/time
        # constraints. Do not mistake a display excerpt for the planning domain.
        starts=[world.crew_point(c) for c in world.crews]
        from . import risk_assets
        assets=json.loads((risk_assets.FOLDER/'assets.json').read_text())
        normalized={}
        for a in rows.values():
            if a['operation_id']=='clear-inlet':
                d=min(world.drains,key=lambda x:metres(a['site']['position'],[x['lon'],x['lat']]))
                a['id']='clear-inlet:'+str(d['id'])
                a['site']['position']=[d['lon'],d['lat']]
            normalized[a['id']]=a
        rows=normalized
        latch=next(a for a in rows.values() if a['operation_id']=='latch-windows')
        inlet=next(a for a in rows.values() if a['operation_id']=='clear-inlet')
        for b in control_layers['buildings']:
            point=[b['lon'],b['lat']]
            if min(metres(point,p) for p in starts)>500:continue
            identity='latch-windows:'+str(b['fd_id'])
            if identity in rows:continue
            a=copy.deepcopy(latch)
            a.update(id=identity,site={**a['site'],'position':point,'label':'NSI building '+str(b['fd_id'])})
            # The new site must receive its own nearest mapped footprint, not
            # the template building's geometry or height.
            building=min(assets['buildings'],key=lambda x:metres(point,x['position']))
            road=min(assets['roads'],key=lambda x:metres(point,x['position']))
            a['site'].update(building_geometry=building['geometry'],building_height_m=building['height_m'],
                display_height_m=building['height_m'],heading=building.get('angle_rad',0)*180/3.141592653589793,
                label=road['name']+' vicinity · NSI '+str(b['fd_id']))
            rows[identity]=a
        for d in world.drains:
            point=[d['lon'],d['lat']]
            if min(metres(point,p) for p in starts)>250:continue
            identity='clear-inlet:'+str(d['id'])
            if identity not in rows:
                a=copy.deepcopy(inlet);a.update(id=identity,site={**a['site'],'position':point,'label':'Inlet '+str(d['id'])})
                rows[identity]=a
    return list(rows.values())


def compiled(receipt, candidates, name):
    """Only completed IDs cross the physics boundary. No free legacy work."""
    rows={a['id']:a for a in candidates}
    out=dict(name=name,drain_ids=[],stage_ids=[],pumps=[],barriers=[],doorway_fd_ids=[],
             action_ids=[r['id'] for r in receipt['completed']],actions=[])
    mapping={'prune-defect':'prune_tree','engineered-support':'brace_structure',
             'backup-pump':'backup_power','install-cordon':'close_route'}
    for job in receipt['completed']:
        a=rows[job['id']];op=a['operation_id'];target=a['id'].split(':',1)[1]
        if op=='fit-barrier':out['doorway_fd_ids'].append(int(target))
        elif op=='portable-pump':out['pumps'].append(dict(zip(('lon','lat'),a['site']['position'])))
        elif op in mapping:out['actions'].append(dict(kind=mapping[op],target=target))
    return out


def annotate(pack, binding, label):
    """The viewer consumes the exact saved execution, never a fresh AI review."""
    if binding['storm_id']!=pack['storm_id']:raise ValueError('Work plan storm mismatch; rebuild required')
    entry=binding['entries'][label]
    applied={r['id']:r for r in entry['receipt']['completed']}
    chosen=binding['selected_label'];main=binding['entries'][chosen]
    selected={r['id']:r for r in main['receipt']['completed']}
    candidates=copy.deepcopy(binding['candidates'])
    for a in candidates:
        identity=a['id'];a.update(recorded=identity in applied,selected=identity in selected,applied=identity in applied,
                                assignment=selected.get(identity),ablation_label=binding['ablations'].get(identity),plan_label=chosen)
        a.update(binding['comparisons'].get(identity,{}))
        if identity not in selected:
            trial=schedule([identity],candidates,binding['resources'])
            a['not_selected_reason']=trial['skipped'][0]['reason'] if trial['skipped'] else 'Not chosen by Argon after comparing tested alternatives'
    return {**pack,'actions':candidates,'execution':entry['receipt'],'work_plan':dict(label=chosen,name=main['name'],
        model=binding['model'],reasons=binding['decision']['reasons'],resources=binding['resources'],
        selected_ids=list(selected),applied_ids=list(applied),current_label=label,alternatives=binding['tested'],
        baseline_label='control',input_digest=binding['input_digest']),
        'scope':'One Argon decision → scheduled action IDs → full water solve and matched consequences → 3D. Site applicability and local harm remain extrapolated.'}
