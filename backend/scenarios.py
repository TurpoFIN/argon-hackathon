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


def calculate(parameters: Parameters, destination: Path):
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
    tide_base = source["tide_base_navd88_m"]
    h = np.zeros_like(z)
    h[ocean] = np.maximum(tide_base-z[ocean], 0)
    budget = Budget(initial_m3=float(h.sum()*dx**2))
    frames, metrics = [], []
    for step in range(3601):
        if step % 120 == 0:
            frames.append(h.astype("float32").copy())
            land_depth = h[~ocean & ~walls]
            metrics.append({"time_seconds": step, "max_depth_m": float(land_depth.max()),
                "flooded_area_m2": int((land_depth > 0.05).sum())*dx**2,
                "water_volume_m3": float(land_depth.sum()*dx**2)})
        if step == 3600:
            break
        tide = tide_base + min(step/1800, 1)*p.coastal_rise_m
        advance_water(h, z, walls, ocean, rain, drains, tide, dx, 1, budget, p.roughness)
    residual = float(h.sum()*dx**2) - budget.initial_m3 - budget.rain_m3 + budget.drained_m3 - budget.coastal_exchange_m3
    if abs(residual) > 0.01 or not np.isfinite(h).all():
        raise ArithmeticError("Scenario failed the water-budget check")
    destination.mkdir(parents=True, exist_ok=False)
    np.stack([lon, lat, z, height, u, v, ocean.astype(float)]).astype("float32").tofile(destination / "grid.bin")
    np.stack(frames).tofile(destination / "flood.bin")
    source.update({"parameters": p.model_dump(), "generated_at": datetime.now(timezone.utc).isoformat(),
        "rainfall_mm_per_hour": p.rain_mm_h, "frame_metrics": metrics,
        "wind": {**source["wind"], "mean_mps": p.wind_mps, "gust_mps": p.gust_mps, "from_deg": p.wind_from_deg},
        "metrics": {**source["metrics"], "wet_land_cells": int(((h > .05) & ~ocean & ~walls).sum()),
            "max_land_depth_m": float(h[~ocean & ~walls].max()), "water_budget": vars(budget),
            "final_volume_m3": float(h.sum()*dx**2), "mass_balance_error_m3": residual},
        "assumptions": [
            "User-configured compound scenario, not a forecast or historical replay.",
            f"Uniform {p.rain_mm_h:g} mm/h rain; runoff coefficient {p.runoff_coefficient:g}; Manning roughness {p.roughness:g}.",
            f"Added coastal rise {p.coastal_rise_m:g} m ramps over 30 minutes above observed 2.368 m NAVD88 baseline.",
            f"Assumed drain capacity {p.drain_capacity_lps:g} L/s with {p.blockage_pct:g}% blockage; no connected sewer network.",
            "Wind is diagnostic 2-D potential flow. Gust envelope and rooftop blend are illustrative; no structural damage prediction.",
            *source["assumptions"][-3:]]})
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

    def submit(self, parameters: Parameters):
        with self.lock:
            if not (DATA / "model-inputs.npz").exists():
                raise FileNotFoundError("Run data preparation before changing parameters")
            values = parameters.model_dump()
            if values == self.state["requested_parameters"] and self.state["status"] != "failed":
                return self.snapshot()
            self._request_version += 1
            self.state["request_version"] = self._request_version
            self.state["requested_parameters"] = values
            if values == self.state["parameters"]:
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
                calculate(parameters, folder)
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
