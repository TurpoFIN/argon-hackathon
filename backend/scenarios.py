"""Shared, bounded local scenario state. Parameters change the actual model."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import json
import shutil
import threading
import time
import uuid

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .simulation import Budget, advance_water, potential_wind
from .coupled import World
from . import argon, footage, coastal, destruction_web, risk_assets
from .preparedness import study
from typing import Literal

DATA = Path(__file__).resolve().parents[1] / "data"


class Parameters(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    rain_mm_h: float = Field(default=32, ge=0, le=150)
    wind_mps: float = Field(default=11.3, ge=0, le=50)
    gust_mps: float = Field(default=19.3, ge=0, le=80)
    wind_from_deg: float = Field(default=138, ge=0, le=360)
    coastal_rise_m: float = Field(default=1, ge=0, le=3)
    drain_capacity_lps: float = Field(default=6, ge=0, le=50)
    blockage_pct: float = Field(default=65, ge=0, le=100)
    runoff_coefficient: float = Field(default=0.9, ge=0, le=1)
    roughness: float = Field(default=0.035, ge=0.01, le=0.2)
    debris_kg: float = Field(default=600, ge=0, le=2000)
    clearing_crews: int = Field(default=2, ge=0, le=4)
    response_mode: Literal['argon', 'baseline'] = 'argon'
    coastal_case: Literal['manual','lower','central','upper'] = 'manual'

    @model_validator(mode="after")
    def valid_gust(self):
        if self.gust_mps < self.wind_mps:
            raise ValueError("Wind gust must be at least the mean wind speed")
        return self


class PlaybackUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    time_seconds: float | None = Field(default=None, ge=0, le=3600)
    playing: bool | None = None
    speed: float | None = Field(default=None, ge=1, le=60)


class SupersededCalculation(Exception):
    """The slider moved again; discard this calculation without publishing it."""


def create_world(parameters: Parameters, should_cancel=lambda: False):
    """Build the same public-data initial world for interactive and matched studies."""
    def check_cancelled():
        if should_cancel():
            raise SupersededCalculation()

    check_cancelled()
    p = parameters
    with np.load(DATA / "model-inputs.npz") as arrays:
        z, height, walls, ocean = [arrays[name] for name in ["ground", "height", "buildings", "ocean"]]
        rain = arrays["runoff_weights"] * p.rain_mm_h / 1000 / 3600 * p.runoff_coefficient
        drains = arrays["drain_counts"] * p.drain_capacity_lps / 1000 * (1-p.blockage_pct/100) / 144
        lon, lat = arrays["lon"], arrays["lat"]
    source = json.loads((DATA / "manifest.json").read_text())
    dx = source["grid"]["cell_m"]
    # Matrices retain the same grid as the static public data in both views.
    u, v = potential_wind(walls, p.wind_mps, p.wind_from_deg, dx)
    check_cancelled()
    perception = {"status": "disabled", "zones": []}
    if p.response_mode == 'argon' or (DATA / 'argon-perception.json').exists():
        try:
            perception = argon.perceive()
        except Exception as error:
            perception = {"status": "failed", "reason": f"Perception failed ({type(error).__name__})", "zones": []}
    check_cancelled()
    tide_base = source["tide_base_navd88_m"]
    evidence = footage.load_evidence()
    pilot = coastal.load_pilot() if p.coastal_case != 'manual' else None
    if p.coastal_case != 'manual' and pilot is None:
        raise ValueError('Coastal pilot unavailable. Run python -m scripts.prepare_coastal or select the manual boundary.')
    check_cancelled()
    world = World(DATA,p,z,walls,ocean,lon,lat,u,v,perception,
                  evidence=evidence, dynamics={'rain':rain,'tide_base':tide_base,'coastal':pilot,'coastal_case':p.coastal_case}, check_cancelled=check_cancelled)
    return world, source


def calculate(parameters: Parameters, destination: Path, should_cancel=lambda: False, previous=None):
    world, source = create_world(parameters, should_cancel)
    p = parameters
    lon, lat, z = world.lon, world.lat, world.z
    with np.load(DATA / 'model-inputs.npz') as arrays:
        height = arrays['height']
    u, v, ocean = world.u, world.v, world.ocean
    dx = world.cell
    pilot = world.dynamics.get('coastal')
    experiment = study(world, previous)
    world = experiment['world']
    frames, metrics = experiment['flood'], experiment['frame_metrics']
    residual = experiment['result']['mass_balance_error_m3']
    destination.mkdir(parents=True, exist_ok=False)
    np.stack([lon, lat, z, height, u, v, ocean.astype(float)]).astype("float32").tofile(destination / "grid.bin")
    np.stack(frames).tofile(destination / "flood.bin")
    (destination / "world.json").write_text(json.dumps(world.export(), separators=(',', ':')))
    source.update({"parameters": p.model_dump(), "engine_version": "advance-preparedness-coastal-v2", "generated_at": datetime.now(timezone.utc).isoformat(),
        "rainfall_mm_per_hour": p.rain_mm_h, "frame_metrics": metrics, "world_file": "world.json",
        "wind": {**source["wind"], "mean_mps": p.wind_mps, "gust_mps": p.gust_mps, "from_deg": p.wind_from_deg},
        "metrics": {**source["metrics"], "wet_land_cells": int(metrics[-1]["flooded_area_m2"] / dx**2),
            "max_land_depth_m": metrics[-1]["max_depth_m"], "water_budget": experiment["water_budget"],
            "final_volume_m3": experiment["final_volume_m3"], "mass_balance_error_m3": residual},
        "assumptions": [
            "User-configured compound scenario, not a forecast or historical replay.",
            f"Uniform {p.rain_mm_h:g} mm/h rain; runoff coefficient {p.runoff_coefficient:g}; Manning roughness {p.roughness:g}.",
            (f"Added coastal rise {p.coastal_rise_m:g} m ramps over 30 minutes above the selected TimesFM/NOAA historical coastal trace." if pilot else f"Added coastal rise {p.coastal_rise_m:g} m ramps over 30 minutes above observed 2.368 m NAVD88 baseline."),
            f"Assumed drain capacity {p.drain_capacity_lps:g} L/s; initial {p.blockage_pct:g}% blockage evolves with debris and clearing; no connected sewer network.",
            "Wind is diagnostic 2-D potential flow. Destruction applies conditional fragilities to the same gust envelope and local speedup.",
            *source["assumptions"][-3:]]})
    try:
        destruction_web.publish(world, risk_assets.load(), frames,
                                [m['time_seconds'] for m in metrics], destination)
    except Exception as error:
        source['destruction_error']=f'{type(error).__name__}: {error}'
    (destination / "manifest.json").write_text(json.dumps(source, indent=2))


class ScenarioStore:
    def __init__(self):
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="storm")
        self.path = DATA / "scenario-state.json"
        if self.path.exists():
            self.state = json.loads(self.path.read_text())
            self.state.update(status="ready", error=None)
        else:
            self.state = {"scenario_id": "baseline", "parameters": Parameters().model_dump(),
                "status": "ready", "error": None, "revision": 1,
                "playback": {"time_seconds": 2100, "playing": True, "speed": 15, "anchor_ms": time.time()*1000}}
        self.state.pop("pending_parameters", None)
        self.state["requested_parameters"] = self.state["parameters"]
        self._pending = None
        self._running = False
        self._request_version = self.state.get("request_version", 0)
        self.state["request_version"] = self._request_version

    def save(self):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state))
        temporary.replace(self.path)

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.state))

    def submit(self, parameters: Parameters, force=False):
        with self.lock:
            if not (DATA / "model-inputs.npz").exists():
                raise FileNotFoundError("Run data preparation before changing parameters")
            values = parameters.model_dump()
            if not force and values == self.state["requested_parameters"] and self.state["status"] != "failed":
                return self.snapshot()
            self._request_version += 1
            self.state["request_version"] = self._request_version
            self.state["requested_parameters"] = values
            if not force and values == self.state["parameters"]:
                # Returning to the displayed scenario cancels any obsolete work.
                self._pending = None
                self.state.update(status="ready", error=None)
                self.state.pop("pending_parameters", None)
                self.save()
                return self.snapshot()
            self._pending = (self._request_version, parameters)
            self.state.update(status="computing", error=None, pending_parameters=values)
            if not self._running:
                self._running = True
                self.executor.submit(self._run)
            return self.snapshot()

    def _run(self):
        while True:
            with self.lock:
                if self._pending is None:
                    self._running = False
                    return
                version, parameters = self._pending
                self._pending = None
            identity = uuid.uuid4().hex[:12]
            folder = DATA / "scenarios" / identity
            try:
                previous = None
                prior_folder = DATA if self.state['scenario_id']=='baseline' else DATA/'scenarios'/self.state['scenario_id']
                prior_world = prior_folder/'world.json'
                if prior_world.exists():
                    prior = json.loads(prior_world.read_text())
                    if prior.get('preparedness') and prior.get('plans'):
                        last = prior['plans'][-1]
                        # Prior actions help revision, but scores from different weather /
                        # boundaries are not comparable to this experiment's outcomes.
                        previous = {k:last[k] for k in ('selected_plan','drain_ids','stage_ids','evidence_ids')}
                        previous['comparison_scope']='Prior actions only; not scored under the current conditions.'
                calculate(parameters, folder, lambda: version != self._request_version, previous)
                with self.lock:
                    if version != self._request_version:
                        # Never publish an intermediate result after newer edits.
                        shutil.rmtree(folder)
                        continue
                    self.state.update(scenario_id=identity, parameters=parameters.model_dump(), status="ready", error=None,
                        revision=self.state["revision"]+1)
                    self.state.pop("pending_parameters", None)
                    # Preserve playback position and camera when parameters change.
                    self.save()
                    history = sorted((DATA / "scenarios").iterdir(), key=lambda p:p.stat().st_mtime, reverse=True)
                    for stale in history[4:]:
                        shutil.rmtree(stale)
            except Exception as error:
                with self.lock:
                    if version == self._request_version:
                        self.state.update(status="failed", error=str(error))
                        self.state.pop("pending_parameters", None)
                if folder.exists():
                    shutil.rmtree(folder)

    def playback(self, update: PlaybackUpdate):
        with self.lock:
            p = self.state["playback"]
            now = time.time()*1000
            current = (p["time_seconds"] + (now-p["anchor_ms"])/1000*p["speed"]*(1 if p["playing"] else 0)) % 3600
            p.update(time_seconds=current, anchor_ms=now)
            p.update(update.model_dump(exclude_none=True))
            self.save()
            return self.snapshot()


store = ScenarioStore()
