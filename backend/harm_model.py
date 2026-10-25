"""Paired component consequences; missing measurements never become zero loss.

These mechanisms do not calibrate San Francisco loss probabilities. Numerical
site predictions require referenced measurements; scenario inputs are labelled
conditional. The storm and all non-treatment inputs are identical in each pair.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT=Path(__file__).resolve().parents[1]

class Range(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True,allow_inf_nan=False)
    low:float
    high:float
    @model_validator(mode='after')
    def ordered(self):
        if self.low>self.high:raise ValueError('Measurement bounds are reversed')
        return self

class Site(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True,allow_inf_nan=False)
    source_ref:str=Field(min_length=8)
    provenance:Literal['measured','conditional']='conditional'
    # Hazard levels/outages apply to one forcing case, not every future storm.
    storm_fingerprint:str|None=None
    vertical_datum:str|None=None
    observed_at:str|None=None
    observer:str|None=None
    # All elevations use one documented local datum. No default sill or rating.
    water_m:Range|None=None
    wave_crest_m:Range|None=None
    entry_m:Range|None=None
    barrier_top_m:Range|None=None
    barrier_complete:bool|None=None
    barrier_within_tested_conditions:bool|None=None
    all_paths_protected:bool|None=None
    leakage_control_verified:bool|None=None
    interior_water_m:Range|None=None
    original_contents_m:Range|None=None
    raised_contents_m:Range|None=None
    grid_available:bool|None=None
    generator_available:bool|None=None
    transfer_available:bool|None=None
    fuel_available:bool|None=None
    distribution_available:bool|None=None
    pump_available:bool|None=None
    backup_kw:Range|None=None
    pump_kw:Range|None=None
    inflow_lps:Range|None=None
    pump_lps:Range|None=None
    outlet_available:bool|None=None

    @model_validator(mode='after')
    def provenance_and_datum(self):
        elevations=(self.water_m,self.entry_m,self.barrier_top_m,self.interior_water_m,self.original_contents_m,self.raised_contents_m)
        if any(v is not None for v in elevations) and not self.vertical_datum:
            raise ValueError('State the common vertical datum for water and asset elevations')
        if self.provenance=='measured' and not (self.observed_at and self.observer):
            raise ValueError('Measured inputs require observation date and observer')
        for r in (self.backup_kw,self.pump_kw,self.inflow_lps,self.pump_lps,self.wave_crest_m):
            if r is not None and r.low<0:raise ValueError('Capacity, flow and wave crest ranges cannot be negative')
        return self


def overtopped(water:Range|None,wave:Range|None,top:Range|None):
    if water is None or wave is None or top is None:return None
    if water.low+wave.low>top.high:return True
    if water.high+wave.high<=top.low:return False
    return None


def ingress(site:Site,protected:bool):
    w,e=site.water_m,site.entry_m
    if w is None or e is None or site.wave_crest_m is None:return None
    peak=Range(low=w.low+site.wave_crest_m.low,high=w.high+site.wave_crest_m.high)
    if peak.high<=e.low:return False
    if not protected or site.barrier_complete is False:
        return True if peak.low>e.high else None
    overflow=overtopped(w,site.wave_crest_m,site.barrier_top_m)
    if overflow is True:return True
    if overflow is False and site.barrier_complete is True and site.barrier_within_tested_conditions is True and site.all_paths_protected is True and site.leakage_control_verified is True:return False
    return None


def contents(site:Site,raised:bool,entry:bool|None):
    if entry is False:return False
    level=site.raised_contents_m if raised else site.original_contents_m
    water=site.interior_water_m
    if level is None or water is None:return None
    if water.low>level.high:return True
    if water.high<=level.low:return False
    return None


def series_system(parts:list[bool|None]):
    if False in parts:return False
    if all(x is True for x in parts):return True
    return None


def pump_service(site:Site,backup:bool):
    # A generator alone cannot bypass a flooded switch, fuel pump or blocked outlet.
    supply=series_system([site.grid_available,site.distribution_available])
    if backup and supply is not True:
        capacity=None
        if site.backup_kw and site.pump_kw:
            if site.backup_kw.low>=site.pump_kw.high:capacity=True
            elif site.backup_kw.high<site.pump_kw.low:capacity=False
        supply=series_system([site.generator_available,site.transfer_available,site.fuel_available,site.distribution_available,capacity])
    return series_system([supply,site.pump_available,site.outlet_available])


def water_control(site:Site,service:bool|None):
    if service is False:
        if site.inflow_lps and site.inflow_lps.low>0:return False
        return None
    if service is not True or site.inflow_lps is None or site.pump_lps is None:return None
    if site.pump_lps.low>=site.inflow_lps.high:return True
    if site.pump_lps.high<site.inflow_lps.low:return False
    return None


def outcomes(site:Site,operations:set[str]):
    entry=ingress(site,bool(operations&{'fit-barrier','sandbag-wall'}))
    pumping=pump_service(site,'backup-pump' in operations)
    return {'water_entry':entry,'contents_inundated':contents(site,'move-contents' in operations,entry),
            'pump_service':pumping,'water_control':water_control(site,pumping),
            'glazing_failure':None,'tree_failure':None,'structural_failure':None,'route_impassable':None}

MISSING={
 'fit-barrier':['water and wave elevations','entry elevation','barrier rating and completeness','all other entry paths and leakage'],
 'sandbag-wall':['water and wave elevations','entry elevation','completed barrier rating','other entry paths and leakage'],
 'move-contents':['interior flood level','original and destination storage elevations'],
 'backup-pump':['grid outage scenario','complete power/fuel/transfer chain','pump duty and inflow','safe operating discharge'],
 'clear-inlet':['surveyed inlet/sewer connectivity','measured inlet capacity','observed blockage and independent flood depths'],
 'secure-items':['observed loose-object inventory','impact trajectories and glazing resistance'],
 'latch-windows':['current opening condition','tested glazing and envelope resistance'],
 'prune-defect':['arborist defect and capacity assessment','applicable intervention effect evidence'],
 'engineered-support':['structural system and capacity','site-specific engineered intervention'],
 'clear-route':['surveyed obstruction','verified usable route network and storm hazards'],
 'install-cordon':['verified hazard footprint','authorized closure and alternative access'],
}
KEY={'fit-barrier':'water_entry','sandbag-wall':'water_entry','move-contents':'contents_inundated','backup-pump':'water_control',
     'latch-windows':'glazing_failure','secure-items':'glazing_failure','prune-defect':'tree_failure','engineered-support':'structural_failure',
     'clear-route':'route_impassable','install-cordon':'route_impassable','clear-inlet':'water_entry'}
BAD_IS_TRUE={'water_entry','contents_inundated','glazing_failure','tree_failure','structural_failure','route_impassable'}


def compare(work,measurements,storm,selected=True):
    original=deepcopy(measurements)
    fingerprint=hashlib.sha256(json.dumps(storm,sort_keys=True).encode()).hexdigest()
    op=work['operation_id'];raw=measurements.get(work['site']['id'])
    recorded=Site.model_validate(raw) if raw else None
    site=recorded if recorded and recorded.storm_fingerprint==fingerprint else None
    before=outcomes(site,set()) if site else {k:None for k in BAD_IS_TRUE|{'pump_service','water_control'}}
    after=outcomes(site,{op} if selected else set()) if site else before.copy()
    key=KEY[op];b,a=before[key],after[key]
    if b is None or a is None:change=None
    else:change=int(b)-int(a) if key in BAD_IS_TRUE else int(a)-int(b)
    # Hydraulic response to an inlet/closure is not inferred from completing work.
    if op in ('clear-inlet','clear-route','install-cordon'):change=None;b=None;a=None
    assert measurements==original
    return {'metric':key,'before':b,'after':a,'improvement':change,
            'support':site.provenance if site and change is not None else 'unavailable',
            'source_ref':recorded.source_ref if recorded else None,'missing':((['hazard inputs tied to the current storm'] if recorded and site is None else [])+MISSING[op]) if change is None else [],
            'storm_before':fingerprint,'storm_after':fingerprint,'field_calibrated':False,
            'limitation':'Mechanism calculation only; local damage probabilities and total losses are not calibrated.'}


def load_measurements():
    p=ROOT/'data/harm/site-measurements.json'
    if not p.exists():return {}
    raw=json.loads(p.read_text())
    for value in raw.values():Site.model_validate(value)
    return raw


def annotate(candidates,selected,storm):
    measurements=load_measurements();ids={w['id'] for w in selected}
    def add(w):return {**w,'harm':compare(w,measurements,storm,w['id'] in ids)}
    return [add(w) for w in candidates],[add(w) for w in selected]
