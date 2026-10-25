"""Frozen TimesFM / NOAA hindcast pilot. No training and no future observations in inputs."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import time
import urllib.parse
import urllib.request

import numpy as np

ROOT = Path(__file__).resolve().parents[1] / 'data' / 'coastal'
MODEL = 'google/timesfm-2.5-200m-pytorch'
MODEL_REVISION = '1d952420fba87f3c6dee4f240de0f1a0fbc790e3'
CONTEXT = 336
HORIZON = 24
AS_OF = '2023-03-21 00:00'
STATION = '9414290'


def timestamp(value):
    return datetime.strptime(value, '%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc)


def fetch_noaa():
    ROOT.mkdir(parents=True, exist_ok=True)
    params = dict(station=STATION, begin_date='20221125', end_date='20230325',
                  datum='NAVD', units='metric', time_zone='gmt', format='json', application='Embarcadero_Preparedness_Pilot')
    def fetch(product):
        query = {**params, 'product':product}
        if product == 'predictions': query['interval']='h'
        url = 'https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?' + urllib.parse.urlencode(query)
        path = ROOT / (product+'.json')
        if path.exists(): data=path.read_bytes()
        else:
            with urllib.request.urlopen(url, timeout=40) as response: data=response.read(2_000_000)
            decoded=json.loads(data)
            if 'error' in decoded: raise ValueError(decoded['error']['message'])
            path.write_bytes(data)
        return json.loads(data), dict(url=url, sha256=hashlib.sha256(data).hexdigest())
    with ThreadPoolExecutor(max_workers=2) as executor:
        (water,water_source),(tide,tide_source)=list(executor.map(fetch,['hourly_height','predictions']))
    observed={row['t']:float(row['v']) for row in water['data'] if row.get('v') not in ('',None)}
    predicted={row['t']:float(row['v']) for row in tide['predictions'] if row.get('v') not in ('',None)}
    keys=sorted(observed.keys() & predicted.keys())
    for a,b in zip(keys,keys[1:]):
        if timestamp(b)-timestamp(a)!=timedelta(hours=1):
            raise ValueError(f'NOAA hourly gap at {a}; pilot does not interpolate missing storm observations')
    values=np.array([[observed[t],predicted[t]] for t in keys],dtype=np.float64)
    if not np.isfinite(values).all(): raise ValueError('Non-finite NOAA values')
    return keys,values,dict(station=STATION,name='San Francisco',datum='NAVD88',units='m',timezone='UTC',
        observations=water_source,predictions=tide_source,paired_hours=len(keys))


def make_window(keys, values, cutoff):
    index=keys.index(cutoff)
    if index+1<CONTEXT or index+HORIZON>=len(keys): raise ValueError('Insufficient context or verification horizon')
    residual=values[:,0]-values[:,1]
    return residual[index-CONTEXT+1:index+1].copy(), values[index+1:index+HORIZON+1].copy(), index


def score(point,truth):
    error=point-truth
    return dict(mae_m=float(np.abs(error).mean()),rmse_m=float(np.sqrt((error**2).mean())),
        peak_height_error_m=float(np.abs(point.max(axis=1)-truth.max(axis=1)).mean()),
        peak_timing_error_hours=float(np.abs(point.argmax(axis=1)-truth.argmax(axis=1)).mean()))


def calibrate(low, high, truth):
    if not (low.shape==high.shape==truth.shape) or low.ndim!=2 or not len(low):
        raise ValueError('Calibration requires paired nonempty trajectories')
    misses=np.maximum(np.maximum(low-truth,truth-high),0).max(axis=1)
    rank=min(len(low)-1,math.ceil((len(low)+1)*.8)-1)
    return float(np.sort(misses)[rank])


def build_pilot():
    """Run predeclared calibration/evaluation dates once, then save immutable inputs/outputs."""
    keys,values,sources=fetch_noaa()
    # Earlier calibration windows do not overlap any evaluation future horizon.
    calibration=[f'2022-12-{day:02} 00:00' for day in range(15,28)]
    evaluation=[f'2022-12-{day:02} 00:00' for day in range(30,32)] + [f'2023-01-{day:02} 00:00' for day in range(1,10)] + [f'2023-03-{day:02} 00:00' for day in range(17,25)]
    origins=calibration+evaluation+[AS_OF]
    windows=[make_window(keys,values,origin) for origin in origins]
    assert timestamp(calibration[-1])+timedelta(hours=HORIZON)<timestamp(evaluation[0])
    import torch
    import timesfm
    revision=MODEL_REVISION
    torch.set_num_threads(min(torch.get_num_threads(),6))
    started=time.monotonic()
    model=timesfm.TimesFM_2p5_200M_torch.from_pretrained(MODEL,revision=revision,torch_compile=False)
    model.compile(timesfm.ForecastConfig(max_context=CONTEXT,max_horizon=HORIZON,
        per_core_batch_size=8,normalize_inputs=True,use_continuous_quantile_head=True,
        force_flip_invariance=True,infer_is_positive=False,fix_quantile_crossing=True))
    points,quantiles=model.forecast(horizon=HORIZON,inputs=[w[0] for w in windows])
    points,quantiles=np.asarray(points),np.asarray(quantiles)
    if points.shape!=(len(windows),HORIZON) or quantiles.shape!=(len(windows),HORIZON,10):
        raise ValueError('Unexpected TimesFM forecast contract')
    if not np.isfinite(points).all() or not np.isfinite(quantiles).all(): raise ValueError('Non-finite forecast')
    truth=np.stack([w[1][:,0] for w in windows]);tides=np.stack([w[1][:,1] for w in windows])
    predicted=points+tides;low=quantiles[:,:,1]+tides;high=quantiles[:,:,9]+tides
    if np.any(low>high): raise ValueError('Crossed forecast quantiles')
    n=len(calibration)
    # One score per day = maximum interval miss across the 24h trajectory.
    # Widen, never shrink. These are empirically calibrated stress envelopes,
    # not a guarantee of local flood probabilities or joint weather samples.
    widening=calibrate(low[:n],high[:n],truth[:n])
    lo,hi=low-widening,high+widening
    test=slice(n,n+len(evaluation))
    persistence=tides+np.array([w[0][-1] for w in windows])[:,None]
    metrics=[]
    for horizon in (6,12,24):
        target=truth[test,:horizon]
        row=dict(horizon_hours=horizon,origins=len(evaluation),
            timesfm=score(predicted[test,:horizon],target),
            persistence=score(persistence[test,:horizon],target),tide_only=score(tides[test,:horizon],target))
        row['raw_interval_coverage']=float(((target>=low[test,:horizon])&(target<=high[test,:horizon])).mean())
        row['calibrated_interval_coverage']=float(((target>=lo[test,:horizon])&(target<=hi[test,:horizon])).mean())
        row['calibrated_mean_width_m']=float((hi[test,:horizon]-lo[test,:horizon]).mean())
        metrics.append(row)
    origin=windows[-1][2]
    # Choose the exercise hour solely from the forecast central curve, never truth.
    central=np.r_[values[origin,0],predicted[-1]]
    lower=np.r_[values[origin,0],lo[-1]];upper=np.r_[values[origin,0],hi[-1]]
    peak=int(np.argmax(central[1:]))+1
    begin=max(0,peak-1)
    cases=[dict(id=name,label=label,times_seconds=[0,3600],water_level_m=[float(v[begin]),float(v[begin+1])])
        for name,label,v in [('lower','Lower envelope',lower),('central','Central forecast',central),('upper','Upper envelope',upper)]]
    result=dict(version=1,status='ready',model=MODEL,model_revision=revision,package_version='2.0.2',
        device=str(model.model.device),inference_seconds=round(time.monotonic()-started,3),sources=sources,
        context_hours=CONTEXT,forecast_hours=HORIZON,as_of=AS_OF+' UTC',
        calibration_origins=calibration,evaluation_origins=evaluation,metrics=metrics,
        widening_m=widening,exercise_start=(timestamp(AS_OF)+timedelta(hours=begin)).isoformat(),
        exercise_lead_hours=begin,cases=cases,
        forecast=dict(timestamps=keys[origin:origin+HORIZON+1],central_m=central.tolist(),
            lower_m=lower.tolist(),upper_m=upper.tolist(),tide_m=values[origin:origin+HORIZON+1,1].tolist()),
        backtests=[dict(as_of=origins[i]+' UTC',truth_m=truth[i].tolist(),forecast_m=predicted[i].tolist(),
            lower_m=lo[i].tolist(),upper_m=hi[i].tolist(),tide_m=tides[i].tolist(),persistence_m=persistence[i].tolist())
            for i in range(n,n+len(evaluation))],
        limitations=[
            'Historical hindcast, not a current weather forecast; no training or fine-tuning.',
            'Frozen TimesFM 2.5 forecasts station residual above NOAA astronomical tide; station is near the Golden Gate, not an Embarcadero street-depth sensor.',
            'Downloaded TimesFM 2.5 weights are Apache-2.0. This pilot does not use TimesFM 3.0.',
            'Earlier calibration days and disjoint evaluation days; historical data may overlap model pretraining. Not proof of unseen-event generalization.',
            'Verified historical gauge observations may differ from preliminary readings available at the time; this is not an archived operational-forecast replay.',
            'Empirically widened marginal envelopes are stress cases, not sampled joint trajectories or flood probabilities.',
            'One-hour exercise window is selected using only the central forecast peak. Rain and wind remain user scenario assumptions.',
        ])
    ROOT.mkdir(parents=True,exist_ok=True)
    temporary=ROOT/'pilot.tmp';temporary.write_text(json.dumps(result,indent=2));temporary.replace(ROOT/'pilot.json')
    return result


def load_pilot():
    path=ROOT/'pilot.json'
    if not path.exists(): return None
    result=json.loads(path.read_text())
    if result.get('version')!=1 or result.get('status')!='ready': raise ValueError('Unsupported coastal pilot artifact')
    if {c['id'] for c in result['cases']}!={'lower','central','upper'}: raise ValueError('Missing coastal cases')
    for case in result['cases']:
        if case['times_seconds']!=[0,3600] or len(case['water_level_m'])!=2 or not np.isfinite(case['water_level_m']).all():
            raise ValueError('Invalid coastal boundary')
    return result


def boundary(pilot,case,seconds,added_rise=0.):
    profile=next(c for c in pilot['cases'] if c['id']==case)
    return float(np.interp(seconds,profile['times_seconds'],profile['water_level_m'])) + min(seconds/1800,1)*added_rise
