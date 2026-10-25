"""Actual Argon tool-driven portfolios; paired physical worlds and saved receipts."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from . import argon
from .multihazard import make_model, KINDS, WORK, CREWS, LIMITS, ASSUMPTIONS

ROOT=Path(__file__).resolve().parents[1]
PATH=ROOT/'data/risk/report.json'
METRICS=('damaged_envelope_m2','trees_failed','partial_collapses','flooded_entrances',
         'unserved_load_hours','route_hazard_minutes','isolated_building_minutes','interior_water_m3')


class Action(BaseModel):
    model_config=ConfigDict(extra='forbid')
    kind:Literal['secure_loose','prune_tree','brace_structure','protect_entrance','backup_power','close_route']
    target:str


class Portfolio(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name:str=Field(min_length=2,max_length=80)
    actions:list[Action]=Field(max_length=24)


class Experiment(BaseModel):
    model_config=ConfigDict(extra='forbid')
    hypothesis:str=Field(max_length=1400)
    plans:list[Portfolio]=Field(min_length=2,max_length=9)


class Selection(BaseModel):
    model_config=ConfigDict(extra='forbid')
    selected_plan:str
    reason:str=Field(max_length=1800)
    tradeoffs:list[str]=Field(max_length=6)


def signature(parameters,minutes):
    h=hashlib.sha256(json.dumps([parameters,minutes],sort_keys=True).encode())
    for file in ('backend/multihazard.py','backend/risk_experiments.py','data/risk/assets.json'):
        h.update((ROOT/file).read_bytes())
    return h.hexdigest()[:16]


def build(parameters,scenario_id,minutes=120,progress=lambda text:None):
    from google import genai
    m=make_model(parameters,minutes)
    progress('Measuring the no-preparation world')
    first=m.run([],record=True)
    candidates=m.candidates(first['peak'])
    prompt=('Design matched ADVANCE PREPARATION experiments. Call evaluate_portfolios exactly once with eight or nine plans: '
        'one no-action control, single-family plans for each available action kind, and one or two combined portfolios. '
        'Use short human-readable plan names, without underscores or numeric prefixes. Use only supplied IDs. Single-family plans must contain only that kind. Each plan has the SAME available time, skills and stock. '
        'Travel, work, return to staging and stock are enforced; unfinished work receives no credit. '
        'Choose several reachable targets per family and combine useful work. Do not use inspection as a free repair. '
        'This is a conditional, uncalibrated model on real mapped geography, not observed damage. '
        'Three capacity sensitivity cases reuse exactly the same weather and latent strengths across plans. '
        'After receiving results, choose one tested plan; explain measured gains, unchanged metrics and harms. '
        'No overall percent benefit: damage, access and exposure are separate. Do not claim prevented injuries or real-world savings. '
        'A closure changes exposure/access, not structural strength. PG&E line installation is unknown; do not call it overhead.\n'+
        json.dumps(dict(parameters=parameters,preparation_minutes=minutes,crews=list(CREWS),work=WORK,
                        stock=LIMITS,candidates=candidates,assumptions=ASSUMPTIONS)))
    tool=dict(type='function',name='evaluate_portfolios',description='Execute feasible work then test the requested portfolios in matched multi-hazard worlds.',parameters=Experiment.model_json_schema())
    history=[{'type':'user_input','content':[{'type':'text','text':prompt}]}]
    rows=[];replays={};identity=signature(parameters,minutes)
    replay_dir=PATH.parent/identity;replay_dir.mkdir(parents=True,exist_ok=True)
    with genai.Client(api_key=argon.api_key()) as client:
        progress('Argon is choosing preventive-action experiments')
        initial=client.interactions.create(model=argon.MODEL,store=False,input=history,tools=[tool],
            generation_config={'thinking_level':'medium','max_output_tokens':9000},timeout=120)
        calls=[s for s in initial.steps or [] if s.type=='function_call']
        if len(calls)!=1 or calls[0].name!='evaluate_portfolios':raise ValueError('Argon did not request the required experiments')
        call=calls[0];experiment=Experiment.model_validate(call.arguments)
        (PATH.parent/'pending.json').write_text(json.dumps({'tool_call_id':call.id,'request':experiment.model_dump(),'parameters':parameters},indent=2))
        plans=experiment.plans
        if len({p.name for p in plans})!=len(plans):raise ValueError('Plan names must be distinct')
        controls=[p for p in plans if not p.actions]
        if len(controls)!=1:raise ValueError('Exactly one no-action control is required')
        for kind in KINDS:
            if not candidates[kind]:continue
            if not any(p.actions and all(a.kind==kind for a in p.actions) for p in plans):
                raise ValueError('Missing single-family experiment: '+kind)
        baseline_cases={}
        for pi,plan in enumerate(sorted(plans,key=lambda p:bool(p.actions))):
            actions=[a.model_dump() for a in plan.actions]
            allowed={(k,c['id']) for k,items in candidates.items() for c in items}
            if any((a['kind'],a['target']) not in allowed for a in actions):raise ValueError('Unknown candidate chosen by Argon')
            cases=[];center=None
            for factor in (.65,1.,1.35):
                progress(f'Testing {plan.name} · capacity case {factor:g}')
                if not actions and factor==1.:result=first
                else:result=m.run(actions,factor,record=factor==1.)
                cases.append(dict(capacity_factor=factor,**result['metrics']))
                if not actions:baseline_cases[factor]=result['metrics']
                if factor==1.:center=result
            metrics={k:dict(mean=float(np.mean([c[k] for c in cases])),low=min(c[k] for c in cases),high=max(c[k] for c in cases)) for k in METRICS}
            changes={k:dict(mean=float(np.mean([baseline_cases[c['capacity_factor']][k]-c[k] for c in cases])),
                low=min(baseline_cases[c['capacity_factor']][k]-c[k] for c in cases),
                high=max(baseline_cases[c['capacity_factor']][k]-c[k] for c in cases)) for k in METRICS}
            rows.append(dict(id=str(pi),name=plan.name,actions=actions,metrics=metrics,avoided=changes,cases=cases,receipt=center['receipt']))
            replays[str(pi)]=dict(frames=center['frames'],events=center['events'])
            center['flood'].tofile(replay_dir/(str(pi)+'.f32'))
        concise=[{k:r[k] for k in ('name','metrics','avoided')}|{'completed_actions':len(r['receipt']['completed']),'skipped_actions':len(r['receipt']['skipped'])} for r in rows]
        history.extend(s.model_dump(exclude_none=True) for s in initial.steps)
        history.append(dict(type='function_result',name=call.name,call_id=call.id,result=[dict(type='text',text=json.dumps(concise))]))
        progress('Argon is reviewing the measured tradeoffs')
        final=client.interactions.create(model=argon.MODEL,store=False,input=history,
            generation_config={'thinking_level':'medium','max_output_tokens':4000},timeout=120,
            response_format={'type':'text','mime_type':'application/json','schema':Selection.model_json_schema()})
        choice=Selection.model_validate_json(final.output_text)
        if choice.selected_plan not in {r['name'] for r in rows}:raise ValueError('Argon selected an untested plan')
    selected=next(r for r in rows if r['name']==choice.selected_plan)
    report=dict(version=1,signature=identity,generated_at=datetime.now(timezone.utc).isoformat(),
        scenario_id=scenario_id,parameters=parameters,preparation_minutes=minutes,crews=list(CREWS),stock=LIMITS,
        selected_id=selected['id'],selection=choice.model_dump(),hypothesis=experiment.hypothesis,rows=rows,replays=replays,
        coverage=m.a['coverage'],sources=m.a['sources'],assumptions=ASSUMPTIONS,
        source_types={'geometry':'Public GIS','condition':'Conditional scenarios; not surveyed','demand':'One modeled 10 kW load per building; not utility customer data'},
        argon_receipt=dict(model=argon.MODEL,tool_call_id=call.id,prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
                           result_sha256=hashlib.sha256(final.output_text.encode()).hexdigest()))
    PATH.parent.mkdir(exist_ok=True)
    tmp=PATH.with_suffix('.tmp');tmp.write_text(json.dumps(report,separators=(',',':')));tmp.replace(PATH)
    progress('Ready')
    return report


if __name__=='__main__':
    state=json.loads((ROOT/'data/scenario-state.json').read_text())
    result=build(state['parameters'],state['scenario_id'],progress=lambda x:print(x,flush=True))
    (ROOT/'evidence/multihazard-results.json').write_text(json.dumps({k:v for k,v in result.items() if k!='replays'},indent=2))
    print(json.dumps(dict(selected=result['selection'],coverage=result['coverage']),indent=2),flush=True)
