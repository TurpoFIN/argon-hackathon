from concurrent.futures import ThreadPoolExecutor
import json
import threading
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from . import risk_assets, risk_experiments

router=APIRouter(prefix='/api/risk')
executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='multi-hazard')
lock=threading.RLock()
state={'status':'idle','progress':'Ready to compare preventive actions','error':None,'viewed_id':'argon'}


class Run(BaseModel):
    preparation_minutes:int=Field(default=120,ge=30,le=1440)


def report():
    return json.loads(risk_experiments.PATH.read_text()) if risk_experiments.PATH.exists() else None


@router.get('')
def get():
    with lock:
        r=report()
        stale=bool(r and r['signature']!=risk_experiments.signature(r['parameters'],r['preparation_minutes']))
        return {**state,'source_stale':stale,'report':{k:v for k,v in r.items() if k!='replays'} if r else None}


@router.get('/assets')
def assets():
    try:return risk_assets.load()
    except FileNotFoundError:raise HTTPException(503,'Prepare the public risk assets first')


@router.get('/replay/{plan_id}')
def replay(plan_id:str):
    r=report()
    if r is None or plan_id not in r['replays']:raise HTTPException(404,'Replay unavailable')
    return {'signature':r['signature'],'plan_id':plan_id,**r['replays'][plan_id]}


@router.get('/flood/{identity}/{plan_id}')
def flood(identity:str,plan_id:str):
    r=report()
    if r is None or identity!=r['signature'] or plan_id not in r['replays']:raise HTTPException(404)
    return FileResponse(risk_experiments.PATH.parent/identity/(plan_id+'.f32'),media_type='application/octet-stream')


class View(BaseModel):
    plan_id:str|None=None


@router.post('/select')
def select(body:View):
    r=report()
    if body.plan_id is not None and (r is None or body.plan_id not in r['replays']):raise HTTPException(404)
    with lock:state['viewed_id']=body.plan_id
    return dict(viewed_id=body.plan_id)


@router.post('/run',status_code=202)
def run(body:Run):
    with lock:
        if state['status']=='running':return {**state}
        current=json.loads((risk_experiments.ROOT/'data/scenario-state.json').read_text())
        if current['status']!='ready':raise HTTPException(409,'Wait for the storm parameters to finish updating')
        state.update(status='running',progress='Starting matched multi-hazard experiments',error=None)
        def progress(message):
            with lock:state['progress']=message
        def job():
            try:
                risk_experiments.build(current['parameters'],current['scenario_id'],body.preparation_minutes,progress)
                with lock:state.update(status='ready',error=None,viewed_id='argon')
            except Exception as e:
                with lock:state.update(status='failed',error=str(e),progress='Previous completed result retained')
        executor.submit(job)
        return {**state}
