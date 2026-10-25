from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import threading
from functools import lru_cache

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from . import prestorm, risk_assets

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
                     plan=body.plan.model_dump(), review=old.get('review'))
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
