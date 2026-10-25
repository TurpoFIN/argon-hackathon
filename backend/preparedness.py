"""Advance preparation experiments. Every alternative gets the same entire storm.

Historical footage constrains hypothetical access; it never supplies water depths
or drain condition. Crews prepare for 30 minutes, then hold at their staging sites.
"""
import copy
import math
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from scipy.ndimage import label, maximum_filter
from .coupled import route
from .simulation import advance_water, Budget
from . import argon, planning, coastal, robustness

PREPARATION_SECONDS = 1800
STORM_SECONDS = 3600


def fork(world):
    branch = copy.copy(world)
    for field in ('parcels', 'crews', 'priority', 'applied_evidence'):
        setattr(branch, field, copy.deepcopy(getattr(world, field)))
    branch.clog = world.clog.copy()
    branch.blocked = world.blocked.copy()
    branch.events, branch.frames, branch.plans = [], [], []
    branch.forecasting = True
    branch.storm_hold = True
    return branch


def apply_evidence(world):
    for item in world.evidence:
        if not item.get('included', True) or item.get('status') != 'ready':
            continue
        world.applied_evidence.append(item['id'])
        supported = [h for h in item['interpretation']['hazards']
                     if h['confidence'] >= .7 and h['kind'] in item['expected_hazards']]
        if supported:
            lng, lat = item['center']
            distance = np.hypot((world.lon-lng)*111320*np.cos(np.deg2rad(lat)), (world.lat-lat)*111320)
            world.blocked |= distance <= item['radius_m']
        world.event(-PREPARATION_SECONDS, 'planning_evidence', evidence_id=item['id'], applied=bool(supported),
                    reason=item['interpretation']['summary'])


def prepare(world, plan):
    """Execute travel and finite clearing BEFORE t=0, without releasing storm debris."""
    by_id = {d['id']: i for i, d in enumerate(world.drains)}
    clocks = [0. for _ in world.crews]
    actions = []
    dry = np.zeros_like(world.z)

    def travel(crew_index, target_id, staging=False):
        crew = world.crews[crew_index]
        target = world.drains[by_id[target_id]]['cell']
        path = route((round(crew['y']), round(crew['x'])), target, world.blocked, dry)
        if not path:
            actions.append(dict(crew=crew['id'], kind='inaccessible', drain_id=target_id,
                                time_seconds=round(clocks[crew_index]-PREPARATION_SECONDS, 2)))
            return False
        cells = min(len(path)-1, max(0, math.floor((PREPARATION_SECONDS-clocks[crew_index])*1.4/world.cell+1e-9)))
        crew['y'], crew['x'] = map(float, path[cells])
        clocks[crew_index] += cells*world.cell/1.4
        reached = cells == len(path)-1
        actions.append(dict(crew=crew['id'], kind='stage' if staging else 'travel', drain_id=target_id,
                            reached=reached, time_seconds=round(clocks[crew_index]-PREPARATION_SECONDS, 2)))
        return reached

    for order, drain_id in enumerate(plan['drain_ids']):
        if not world.crews: break
        idx = by_id[drain_id]
        # Reserve the route to each crew's final staging site before committing work.
        # Clearing is optional within the budget; staging must not be silently consumed.
        options = []
        for ci,crew in enumerate(world.crews):
            path = route((round(crew['y']),round(crew['x'])),world.drains[idx]['cell'],world.blocked,dry)
            stage_ids = plan.get('stage_ids', [])
            onward = route(world.drains[idx]['cell'],world.drains[by_id[stage_ids[ci]]]['cell'],world.blocked,dry) if stage_ids else [world.drains[idx]['cell']]
            if not path or not onward: continue
            available = PREPARATION_SECONDS-clocks[ci]-(len(path)+len(onward)-2)*world.cell/1.4
            if available>0: options.append((clocks[ci]+len(path)*world.cell/1.4,ci,available))
        if not options:
            actions.append(dict(crew='Preparation team',kind='clearing_skipped_for_staging',drain_id=drain_id,time_seconds=-PREPARATION_SECONDS))
            continue
        _,ci,available = min(options)
        if not travel(ci, drain_id): continue
        duration = min(float(world.clog[idx])/.025, available)
        removed = min(float(world.clog[idx]), .025*duration)
        world.clog[idx] -= removed
        world.cleared_kg += removed
        clocks[ci] += duration
        actions.append(dict(crew=world.crews[ci]['id'], kind='preventive_clearing', drain_id=drain_id,
                            cleared_kg=round(removed, 6), completed=bool(world.clog[idx]<.02),
                            time_seconds=round(clocks[ci]-PREPARATION_SECONDS, 2)))
    for ci, stage_id in enumerate(plan.get('stage_ids', [])):
        travel(ci, stage_id, staging=True)
    for crew in world.crews:
        crew.update(state='staged', target=None, path=[], progress=0., source='preparation')
    world.events.extend({'time_seconds':a['time_seconds'], **{k:v for k,v in a.items() if k!='time_seconds'}} for a in actions)
    return dict(budget_seconds_per_crew=PREPARATION_SECONDS, crew_seconds=[round(t,2) for t in clocks],
                actions=actions, cleared_kg=round(world.cleared_kg, 6),
                stage_positions=[dict(id=c['id'], position=world.crew_point(c)) for c in world.crews],
                requested_stage_ids=plan.get('stage_ids', []),
                reached_staging=sum(a['kind']=='stage' and a['reached'] for a in actions))


def simulate(base, plan, horizon=STORM_SECONDS, coastal_case=None):
    world = fork(base)
    preparation = prepare(world, plan)
    h = np.zeros_like(world.z)
    tide_base = world.dynamics['tide_base']
    pilot = world.dynamics.get('coastal')
    active_case = coastal_case or world.dynamics.get('coastal_case','manual')
    if pilot:
        tide_base=coastal.boundary(pilot,active_case,0)
    h[world.ocean] = np.maximum(tide_base-world.z[world.ocean], 0)
    budget = Budget(initial_m3=float(h.sum()*world.cell**2))
    land = ~world.ocean & ~world.walls
    peak = h.copy()
    frames, metrics = [], []
    exposure, crew_exposure, max_area, max_depth = 0., 0., 0, 0.
    rates = world.rates()
    for step in range(horizon+1):
        if step % 60 == 0: base.check_cancelled()
        if step % 30 == 0: world.snapshot(step)
        if step % 120 == 0:
            frames.append(h.astype('float32').copy())
            metrics.append(dict(time_seconds=step, max_depth_m=float(h[land].max()),
                flooded_area_m2=int((h[land]>.05).sum())*world.cell**2,
                water_volume_m3=float(h[land].sum()*world.cell**2)))
        if step == horizon: break
        if step % 5 == 0:
            world.step(step, h)
            rates = world.rates()
        tide = (coastal.boundary(pilot,active_case,step,world.p.coastal_rise_m) if pilot else
                tide_base + min(step/1800, 1)*world.p.coastal_rise_m)
        advance_water(h, world.z, world.walls, world.ocean, world.dynamics['rain'], rates,
                      tide, world.cell, 1, budget, world.p.roughness)
        np.maximum(peak, h, out=peak)
        area = int((h[land]>.05).sum())*world.cell**2
        exposure += area/60
        max_area = max(max_area, area)
        max_depth = max(max_depth, float(h[land].max()))
        crew_exposure += sum(h[round(c['y']),round(c['x'])]>.3 or world.blocked[round(c['y']),round(c['x'])]
                             for c in world.crews)/60
    residual = float(h.sum()*world.cell**2)-budget.initial_m3-budget.rain_m3+budget.drained_m3-budget.coastal_exchange_m3
    if not np.isfinite(h).all() or abs(residual)>.01:
        raise ArithmeticError('Preparation experiment water budget failed')
    components, _ = label(~world.blocked & (h<=.3))
    crew_components = {components[round(c['y']),round(c['x'])] for c in world.crews}-{0}
    reachable = sum(components[d['cell']] in crew_components for d in world.drains)
    result = dict(name=plan['name'], drain_ids=plan['drain_ids'], stage_ids=plan.get('stage_ids', []),coastal_case=active_case,
        horizon_seconds=horizon, flood_exposure_m2_min=round(exposure, 3),
        peak_flooded_area_m2=max_area, max_depth_m=round(max_depth, 6),
        water_volume_m3=round(float(h[land].sum()*world.cell**2), 6),
        cleared_kg=round(world.cleared_kg,6), crew_exposure_minutes=round(crew_exposure,3),
        reachable_drains=int(reachable), blocked_drains=int((world.blockage()>=.5).sum()),
        completed_clearings=sum(a.get('completed',False) for a in preparation['actions']),
        preparation=preparation, mass_balance_error_m3=residual)
    world.export()  # The independent finite-debris budget must also balance.
    return dict(result=result, world=world, flood=np.stack(frames), frame_metrics=metrics,
                water_budget=vars(budget), final_volume_m3=float(h.sum()*world.cell**2), peak=peak)


def study(base, previous=None):
    """Argon chooses pre-storm actions only after exercising the numerical tool."""
    apply_evidence(base)
    control_plan = dict(name='No preparation', drain_ids=[], stage_ids=[])
    pilot=base.dynamics.get('coastal')
    active_case=base.dynamics.get('coastal_case','manual')
    scenario_specs=robustness.live_scenarios(pilot) if pilot else []
    cases=[s['id'] for s in scenario_specs] if pilot else ['manual']
    by_case={s['id']:s for s in scenario_specs}
    def run_cases(plan):
        if len(cases)==1: return {cases[0]:simulate(base,plan,coastal_case=cases[0])}
        # Worlds fork all mutable state. NumPy releases the GIL for numerical
        # kernels, so the independent coastal cases can use separate CPU cores.
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures={case:pool.submit(simulate,robustness.apply_scenario(base,by_case[case]),plan,coastal_case=case) for case in cases}
            return {case:future.result() for case,future in futures.items()}
    controls=run_cases(control_plan)
    control=controls[active_case]
    # Candidate selection sees the worst coastal case, including dry staging checks.
    control['peak']=np.maximum.reduce([r['peak'] for r in controls.values()])
    cache = {((), ()): controls}
    neighborhood_peak = maximum_filter(control['peak'], size=5)
    candidates = []
    for i,d in enumerate(base.drains):
        if base.blocked[d['cell']]: continue
        candidates.append(dict(id=d['id'], blockage_pct=round(float(base.blockage()[i]*100),2),
            elevation_m=round(float(base.z[d['cell']]),2),
            control_peak_depth_m=round(float(neighborhood_peak[d['cell']]),3),
            position=[d['lon'],d['lat']]))
    # Favor wet areas, but retain spatial diversity rather than one unresolved inlet cluster.
    candidates.sort(key=lambda d:d['control_peak_depth_m'], reverse=True)
    selected = []
    for d in candidates:
        if all(math.hypot((d['position'][0]-s['position'][0])*88000,
                          (d['position'][1]-s['position'][1])*111320)>45 for s in selected):
            selected.append(d)
        if len(selected)==8: break
    wet = selected[:]
    dry = [d for d in candidates if d['control_peak_depth_m']<.15]
    dry.sort(key=lambda d:min((math.hypot((d['position'][0]-w['position'][0])*88000,
                             (d['position'][1]-w['position'][1])*111320) for w in wet),default=0))
    for d in dry:
        if all(math.hypot((d['position'][0]-s['position'][0])*88000,
                          (d['position'][1]-s['position'][1])*111320)>45 for s in selected):
            selected.append(d)
        if len(selected)==16: break
    candidates = selected
    for d in candidates:
        target=next(asset['cell'] for asset in base.drains if asset['id']==d['id'])
        d['staging_travel_seconds_by_crew']=[]
        for crew in base.crews:
            path=route((round(crew['y']),round(crew['x'])),target,base.blocked,np.zeros_like(base.z))
            d['staging_travel_seconds_by_crew'].append(round((len(path)-1)*base.cell/1.4,1) if path else None)
    visible = [e for e in base.evidence if e.get('included',True) and e['status']=='ready']
    context = dict(simulation_seconds=-PREPARATION_SECONDS, trigger='Historical planning evidence changed' if previous else 'Initial advance preparation study',
        candidates=candidates, crews=[dict(id=c['id'],position=base.crew_point(c)) for c in base.crews],
        evidence=[{k:v for k,v in e.items() if k not in {'media_url'}} for e in visible],
        previous_decisions=[previous] if previous else [], preparation_seconds=PREPARATION_SECONDS,
        storm_seconds=STORM_SECONDS, control_outcome=control['result'],
        coastal_uncertainty=(dict(model=pilot['model'],as_of=pilot['as_of'],exercise_start=pilot['exercise_start'],
            cases=scenario_specs,backtest=pilot['metrics'],
            control_outcomes=[dict(coastal_case=case,**{k:v for k,v in controls[case]['result'].items()
                if k in {'flood_exposure_m2_min','crew_exposure_minutes','reachable_drains'}}) for case in cases],
            note='Coherent historical error paths and declared weather sensitivities are stress cases, not flood probabilities. All alternatives tested on every case. Display case is not the optimization objective.') if pilot else None),
        assumptions='12m grid; 1.4m/s travel; .025kg/s clearing; reserve final staging travel inside the 30min budget, skipping clearings if necessary. Historical hazard radii are hypothetical access exclusions; candidate drain locations are staging proxies, not surveyed facilities. Crews hold during storm; no reactive dispatch.')

    def evaluate(plan):
        base.check_cancelled()
        signature = (tuple(plan['drain_ids']), tuple(plan.get('stage_ids', [])))
        if signature not in cache: cache[signature] = run_cases(plan)
        runs=cache[signature]
        outcomes=[dict(coastal_case=case,**{k:v for k,v in runs[case]['result'].items()
            if k in {'flood_exposure_m2_min','peak_flooded_area_m2','max_depth_m','water_volume_m3','crew_exposure_minutes','reachable_drains','mass_balance_error_m3'}}) for case in cases]
        return {**runs[active_case]['result'], 'name':plan['name'],'coastal_outcomes':outcomes,
            'worst_flood_exposure_m2_min':max(r['flood_exposure_m2_min'] for r in outcomes),
            'worst_crew_exposure_minutes':max(r['crew_exposure_minutes'] for r in outcomes)}

    provider_error = None
    if base.p.response_mode == 'argon' and argon.api_key() and candidates and base.crews:
        try:
            decision = planning.choose(context, evaluate, base.check_cancelled)
        except Exception as error:
            base.check_cancelled()
            provider_error = type(error).__name__
            decision = None
    else:
        decision = None
    if decision is None:
        ids = [d['id'] for d in candidates]
        stages = []
        for ci in range(len(base.crews)):
            feasible=[d for d in candidates if d['control_peak_depth_m']<.15 and
                      d['staging_travel_seconds_by_crew'][ci] is not None and d['staging_travel_seconds_by_crew'][ci]<=PREPARATION_SECONDS]
            if not feasible:
                stages=[]
                break
            stages.append(min(feasible,key=lambda d:d['staging_travel_seconds_by_crew'][ci])['id'])
        alternatives = [control_plan,
            dict(name='Priority clearing and staging',drain_ids=ids[:8],stage_ids=stages),
            dict(name='Distributed clearing and staging',drain_ids=ids[1:16:2],stage_ids=stages)]
        results = [evaluate(p) for p in alternatives]
        robustness.add_regrets(results)
        best = min(results,key=robustness.objective)
        decision = dict(time_seconds=-PREPARATION_SECONDS,selected_plan=best['name'],drain_ids=best['drain_ids'],stage_ids=best['stage_ids'],
            reason='Deterministic fallback selected the lowest simulated exposure; Argon decision unavailable.' if base.p.response_mode=='argon' else 'Deterministic preparation comparison selected the lowest simulated exposure.',
            experiments=results,evidence_ids=[e['id'] for e in visible],trigger=context['trigger'],provider='baseline',provider_error=provider_error)
    else:
        decision['provider']='argon'
    signature=(tuple(decision['drain_ids']),tuple(decision['stage_ids']))
    chosen=cache[signature][active_case]
    chosen['result']={**chosen['result'], 'name':decision['selected_plan']}
    chosen['world'].events = base.events + chosen['world'].events
    chosen['world'].plans=[decision]
    decision['outcome']={**chosen['result'], 'time_seconds':STORM_SECONDS}
    chosen['world'].planner_status='Argon tested 3 full-storm preparation plans' if decision['provider']=='argon' else decision['reason']
    chosen['world'].preparedness=dict(phase='advance_preparedness', preparation_seconds=PREPARATION_SECONDS,
        storm_seconds=STORM_SECONDS, evidence_ids=decision['evidence_ids'], previous=previous,
        preparation=chosen['result']['preparation'], selected_plan=decision['selected_plan'],
        excluded_cells=int((base.blocked & ~base.walls & ~base.ocean).sum()))
    if pilot:
        chosen['world'].preparedness['coastal']={k:pilot[k] for k in ('model','model_revision','as_of','context_hours','forecast_hours','exercise_start','exercise_lead_hours','metrics','cases','widening_m','limitations')}
        chosen['world'].preparedness['coastal']['selected_case']=active_case
        chosen['world'].preparedness['coastal']['scenarios']=scenario_specs
        chosen['world'].preparedness['coastal']['cases']=[s for s in scenario_specs if s['id'] in ('lower','central','upper')]
        chosen['world'].preparedness['coastal']['limitations']=pilot['limitations']+[
            'Preparation selection uses coherent historical error paths plus wet/windy and mild sensitivity cases. These are not calibrated joint probabilities.',
            'Selection minimizes worst crew exposure, then worst and mean extra flood exposure relative to the best tested preparation in each scenario.']
    return chosen
