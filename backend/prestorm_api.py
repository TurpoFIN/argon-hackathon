from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import threading
from functools import lru_cache

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from . import prestorm, risk_assets, action_plan, preventive_work

router = APIRouter(prefix='/api/prestorm')
lock = threading.RLock()
executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='evidence-plan')
job = dict(status='idle', error=None)


@lru_cache(maxsize=1)
def inventory():
    return risk_assets.load(), prestorm.evidence()


def context():
    assets, catalog = inventory()
    from .scenarios import store
    return assets, catalog, store.snapshot()['parameters']


@router.get('')
def get():
    try:
        with lock:
            assets, catalog, parameters = context()
            return {**prestorm.public(prestorm.load(assets, catalog), parameters, assets, catalog), 'job': dict(job)}
    except FileNotFoundError:
        raise HTTPException(503, 'The source-backed asset inventory is unavailable. Prepare the public data before planning.')


class Update(BaseModel):
    expected_version: int = Field(ge=0)
    plan: prestorm.Plan


@router.put('')
def update(body: Update):
    with lock:
        assets, catalog, parameters = context()
        old = prestorm.load(assets, catalog)
        if old['version'] != body.expected_version:
            raise HTTPException(409, 'The plan changed in another view. Reload the latest saved plan before saving.')
        try:
            prestorm.validate(body.plan, prestorm.sites(assets, catalog))
        except ValueError as e:
            raise HTTPException(422, str(e))
        state = dict(version=old['version'] + 1, updated_at=datetime.now(timezone.utc).isoformat(),
                     plan=body.plan.model_dump(), review=old.get('review'), action_plan=old.get('action_plan'),
                     work_settings=old.get('work_settings'),work_comparison=old.get('work_comparison'),work_studies=old.get('work_studies',[]))
        prestorm.persist(state)
        return prestorm.public(state, parameters, assets, catalog)


@router.post('/review', status_code=202)
def review():
    with lock:
        if job['status'] == 'running':
            return dict(job)
        assets, catalog, parameters = context()
        state = prestorm.load(assets, catalog)
        job.update(status='running', error=None)

        def run():
            try:
                result = prestorm.review_plan(state, parameters, prestorm.sites(assets, catalog))
                with lock:
                    current = prestorm.load()
                    if current['version'] != state['version']:
                        raise ValueError('Plan changed during review; the new inputs need another review.')
                    current['review'] = result
                    current['version'] += 1
                    prestorm.persist(current)
                    job.update(status='ready', error=None)
            except Exception as e:
                with lock:
                    job.update(status='failed', error=str(e))
        executor.submit(run)
        return dict(job)


@router.post('/generate', status_code=202)
def generate():
    with lock:
        if job['status'] == 'running':
            return dict(job)
        assets, catalog, parameters = context()
        state = prestorm.load(assets, catalog)
        options = prestorm.sites(assets, catalog)
        drains = action_plan.load_drains()
        job.update(status='running', error=None)

        def run():
            try:
                result = action_plan.generate(state, parameters, options, catalog, drains)
                with lock:
                    current = prestorm.load()
                    _, _, latest_parameters = context()
                    latest = action_plan.context(current, latest_parameters, options, catalog, drains)
                    if current['version'] != state['version'] or result['fingerprint'] != action_plan.fingerprint(latest):
                        raise ValueError('Inputs changed while Argon planned. Generate again using the current inputs.')
                    current['action_plan'] = result
                    current['version'] += 1
                    prestorm.persist(current)
                    job.update(status='ready', error=None)
            except Exception as e:
                with lock:
                    job.update(status='failed', error=str(e))
        executor.submit(run)
        return dict(job)


class WorkRequest(BaseModel):
    expected_version:int=Field(ge=0)
    settings:preventive_work.Settings


@router.post('/compare-work',status_code=202)
def compare_work(body:WorkRequest):
    with lock:
        if job['status']=='running':return dict(job)
        assets,catalog,parameters=context()
        state=prestorm.load(assets,catalog)
        if state['version']!=body.expected_version:
            raise HTTPException(409,'The inputs changed in another view. Reload before comparing work.')
        options=prestorm.sites(assets,catalog)
        key=preventive_work.fingerprint(preventive_work.context(state,parameters,options,catalog,body.settings))
        cached=next((s for s in state.get('work_studies',[]) if s['result']['fingerprint']==key),None)
        if cached:
            state.update(work_settings=body.settings.model_dump(),work_comparison=cached['result'],version=state['version']+1)
            prestorm.persist(state);job.update(status='ready',error=None)
            return dict(job)
        job.update(status='running',error=None)
        def run():
            try:
                result=preventive_work.generate(state,parameters,options,catalog,body.settings)
                with lock:
                    current=prestorm.load()
                    _,_,latest_parameters=context()
                    ctx=preventive_work.context(current,latest_parameters,options,catalog,body.settings)
                    if current['version']!=state['version'] or result['fingerprint']!=preventive_work.fingerprint(ctx):
                        raise ValueError('Inputs changed during the work comparison. Run it again with the latest inputs.')
                    settings=body.settings.model_dump()
                    studies=[r for r in current.get('work_studies',[]) if r['settings']!=settings]
                    studies.append(dict(settings=settings,result=result))
                    current.update(work_settings=settings,work_comparison=result,work_studies=studies[-6:],version=current['version']+1)
                    prestorm.persist(current);job.update(status='ready',error=None)
            except Exception as e:
                with lock:job.update(status='failed',error=str(e))
        executor.submit(run)
        return dict(job)
