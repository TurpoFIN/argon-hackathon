"""Storm forcing from TimesFM 3: forecast the San Francisco tide gauge's water level and wind.

Targets (6-minute NOAA CO-OPS 9414290 data, last 30 days): water level (m NAVD88), wind speed and
wind gust (m/s). Past-and-future covariate: NOAA's harmonic tide prediction, which is known ahead.
Output: 9 quantile paths per target over the horizon, and P10/P50/P90 storm scenarios for the sim.

Runs in the isolated TimesFM venv (loop/README.md):
  .venv/bin/python -m loop.forecast --out data/prep-loop/forcing-live.json
  .venv/bin/python -m loop.forecast --issued 2023-01-04T18:00Z --out data/prep-loop/forcing-2023-01-04.json
"""
import argparse
import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

STATION = "9414290"
API = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
STEP = timedelta(minutes=6)
CONTEXT_DAYS = 30
QUANTILES = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
# Three-point approximation of the forecast distribution: P10/P50/P90 weighted 0.3/0.4/0.3
# (Swanson's rule, a standard decision-analysis discretization).
SCENARIOS = [("P10", 0, 0.3), ("P50", 4, 0.4), ("P90", 8, 0.3)]


def coops(product, begin, end, **extra):
    params = {"station": STATION, "product": product, "begin_date": begin.strftime("%Y%m%d %H:%M"),
              "end_date": end.strftime("%Y%m%d %H:%M"), "time_zone": "gmt", "units": "metric", "format": "json",
              "application": "argon-hackathon-prep-loop", **extra}
    url = API + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "argon-hackathon"}), timeout=90) as r:
        payload = json.load(r)
    rows = payload.get("data") or payload.get("predictions")
    if not rows:
        raise RuntimeError(f"NOAA {product}: {payload.get('error', payload)}")
    return url, rows


def on_grid(rows, field, grid):
    """Values on the 6-minute grid; gaps are linearly interpolated (and reported)."""
    index = {datetime.strptime(r["t"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc): r.get(field, "") for r in rows}
    values = np.array([float(index[t]) if index.get(t, "") not in ("", None) else np.nan for t in grid])
    missing = int(np.isnan(values).sum())
    if missing:
        good = ~np.isnan(values)
        values = np.interp(np.arange(len(values)), np.flatnonzero(good), values[good])
    return values.astype(np.float32), missing


def circular_mean_deg(degrees, weights):
    radians = np.radians(degrees)
    return float(np.degrees(math.atan2(np.sum(np.sin(radians) * weights), np.sum(np.cos(radians) * weights))) % 360)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--issued", help="forecast issue time, UTC (default: latest observation)")
    parser.add_argument("--horizon-hours", type=float, default=48)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    issued = datetime.strptime(args.issued, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc) if args.issued else now
    begin = issued - timedelta(days=CONTEXT_DAYS)
    horizon = int(args.horizon_hours * 60 / 6)
    after = issued + STEP * horizon

    wl_url, wl_rows = coops("water_level", begin, issued, datum="NAVD")
    last = max(datetime.strptime(r["t"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc) for r in wl_rows if r.get("v"))
    grid = [last - STEP * k for k in range(CONTEXT_DAYS * 240 - 1, -1, -1)]
    future = [last + STEP * (k + 1) for k in range(horizon)]
    wind_url, wind_rows = coops("wind", begin, last)
    tide_url, tide_rows = coops("predictions", begin, last + STEP * horizon, datum="NAVD", interval="6")

    water, gaps_w = on_grid(wl_rows, "v", grid)
    speed, gaps_s = on_grid(wind_rows, "s", grid)
    gust, gaps_g = on_grid(wind_rows, "g", grid)
    direction, _ = on_grid(wind_rows, "d", grid)
    tide, gaps_t = on_grid(tide_rows, "v", grid + future)

    from timesfm3.mlx import TimesFM3Forecaster
    model = TimesFM3Forecaster.from_pretrained("google/timesfm-3.0-pytorch")
    out = model.predict(np.stack([water, speed, gust]), horizon=horizon,
                        past_future_covariates=tide[None, :], return_quantiles=True)
    q = np.asarray(out.quantiles)  # (3 targets, horizon, 9)
    q[1:] = np.maximum(q[1:], 0.0)  # wind cannot be negative
    q = np.sort(q, axis=-1)  # enforce monotone quantiles

    recent = slice(-60, None)  # last 6 hours
    wind_from = circular_mean_deg(direction[recent], np.maximum(speed[recent], 0.1))
    scenarios = []
    for name, k, weight in SCENARIOS:
        peak = int(np.argmax(q[0, :, k]))
        gust_peak = float(q[2, :, k].max())
        scenarios.append({
            "name": name, "quantile": QUANTILES[k], "weight": weight,
            "peak_water_level_navd88_m": round(float(q[0, peak, k]), 3),
            "peak_time": future[peak].strftime("%Y-%m-%dT%H:%MZ"),
            "wind_mps": round(float(q[1, :, k].max()), 2), "gust_mps": round(max(gust_peak, float(q[1, :, k].max())), 2),
            "wind_from_deg": round(wind_from, 1),
        })

    observed = None
    if after <= now:  # backtest: compare with what actually happened
        _, obs_rows = coops("water_level", last + STEP, after, datum="NAVD")
        _, obs_wind = coops("wind", last + STEP, after)
        levels = [(float(r["v"]), r["t"]) for r in obs_rows if r.get("v")]
        gusts = [float(r["g"]) for r in obs_wind if r.get("g")]
        top = max(levels)
        observed = {"peak_water_level_navd88_m": top[0], "peak_time": top[1], "peak_gust_mps": max(gusts) if gusts else None,
                    "water_level_within_p10_p90": bool(scenarios[0]["peak_water_level_navd88_m"] <= top[0] <= scenarios[2]["peak_water_level_navd88_m"])}

    payload = {
        "model": "google/timesfm-3.0-pytorch (TimesFM 3.0, MLX backend)",
        "model_license": "TimesFM 3.0 weights: timesfm-non-commercial-license-v1.0 (non-commercial, non-production use).",
        "station": STATION, "issued": last.strftime("%Y-%m-%dT%H:%MZ"), "context_days": CONTEXT_DAYS,
        "horizon_hours": args.horizon_hours, "step_minutes": 6, "quantiles": QUANTILES,
        "sources": {"water_level": wl_url, "wind": wind_url, "tide_prediction": tide_url},
        "gaps_interpolated": {"water_level": gaps_w, "wind_speed": gaps_s, "gust": gaps_g, "tide_prediction": gaps_t},
        "history": {"t": [t.strftime("%Y-%m-%dT%H:%MZ") for t in grid[-480:]], "water_level": water[-480:].round(3).tolist(),
                    "gust": gust[-480:].round(2).tolist()},
        "forecast": {"t": [t.strftime("%Y-%m-%dT%H:%MZ") for t in future], "tide_prediction": tide[len(grid):].round(3).tolist(),
                     "water_level_q": q[0].round(3).tolist(), "wind_speed_q": q[1].round(2).tolist(), "gust_q": q[2].round(2).tolist()},
        "scenarios": scenarios, "observed": observed,
        "notes": ["Rain is not forecast here; the simulator keeps its Atlas 14 design rain in every scenario.",
                  "Scenario peaks are the maxima of each quantile path over the horizon, not quantiles of the maximum.",
                  "Wind direction is the speed-weighted mean of the last 6 observed hours.",
                  "Scenario weights 0.3/0.4/0.3 follow Swanson's rule for P10/P50/P90."],
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(payload, indent=1))
    print(json.dumps({"issued": payload["issued"], "scenarios": scenarios, "observed": observed}, indent=1))


if __name__ == "__main__":
    main()
