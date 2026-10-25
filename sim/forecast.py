"""Fetch and cache the small live forcing payload used by ``forecast_live``.

The cache is intentionally plain JSON so the demo can run without network access.  A TimesFM
adapter can be used when ``timesfm3`` is installed; this box does not ship that optional
dependency, so the default is the documented NOAA harmonic-tide plus observed-surge persistence
fallback.  No simulator code imports a forecasting package at import time.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "reference" / "storm" / "forecast_live.json"
STATION = "9414290"
COOPS = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
NWS_HEADERS = {"User-Agent": "StormSim SF hackathon (stormsim@example.com)", "Accept": "application/geo+json"}
QUANTILES = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
STEP = timedelta(minutes=6)


def _get(url: str, timeout: float = 20.0):
    req = urllib.request.Request(url, headers=NWS_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _coops(product: str, begin: datetime, end: datetime, **extra):
    params = {"station": STATION, "product": product, "begin_date": begin.strftime("%Y%m%d %H:%M"),
              "end_date": end.strftime("%Y%m%d %H:%M"), "time_zone": "gmt", "units": "metric",
              "format": "json", "application": "stormsim-sf", **extra}
    url = COOPS + "?" + urllib.parse.urlencode(params)
    payload = _get(url, timeout=30)
    rows = payload.get("data") or payload.get("predictions")
    if not rows:
        raise RuntimeError(f"NOAA {product}: {payload.get('error', payload)}")
    return url, rows


def _series(rows, key: str):
    out = []
    for row in rows:
        try:
            if row.get(key) in (None, "", "M"):
                continue
            out.append((_parse_time(row["t"]), float(row[key])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _interp(points, times, default=0.0):
    if not points:
        return np.full(len(times), default, dtype=float)
    x = np.array([p[0].timestamp() for p in points], dtype=float)
    y = np.array([p[1] for p in points], dtype=float)
    return np.interp([t.timestamp() for t in times], x, y)


def _interval(value: str):
    """Return start and duration for an RFC3339/ISO-8601 interval."""
    start, _, duration = value.partition("/")
    m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?", duration)
    if not m:
        return _parse_time(start), timedelta(hours=1)
    days, hours, minutes = (int(x or 0) for x in m.groups())
    return _parse_time(start), timedelta(days=days, hours=hours, minutes=minutes)


def _weather_values(grid, key: str, conversion=1.0):
    values = []
    for item in (grid.get("properties", {}).get(key, {}).get("values", []) or []):
        try:
            if item.get("value") is None:
                continue
            start, duration = _interval(item["validTime"])
            value = item["value"]
            if isinstance(value, list):
                value = value[0]
            values.append((start, start + duration, float(value) * conversion))
        except (KeyError, TypeError, ValueError):
            continue
    return values


def _weather_at(values, times, default=0.0):
    result = []
    for t in times:
        matches = [v for start, end, v in values if start <= t < end]
        result.append(matches[0] if matches else default)
    return np.asarray(result, dtype=float)


def _alerts():
    url = "https://api.weather.gov/alerts/active?point=37.7749,-122.4194"
    data = _get(url)
    alerts = []
    for feature in data.get("features", []):
        p = feature.get("properties", {})
        alerts.append({k: p.get(k) for k in ("event", "headline", "onset", "ends", "severity", "areaDesc",
                                             "description", "instruction", "senderName")})
    return url, alerts


def _grid_forecast():
    point_url = "https://api.weather.gov/points/37.7749,-122.4194"
    point = _get(point_url)
    grid_url = point["properties"]["forecastGridData"]
    grid = _get(grid_url)
    props = grid.get("properties", {})
    # NWS wind-gust unit is normally km/h.  Preserve the source unit conversion explicitly.
    gust_unit = props.get("windGust", {}).get("uom", "")
    gust_factor = 1 / 3.6 if "km_h" in gust_unit else (0.44704 if "mi_h" in gust_unit else 1.0)
    qpf_unit = props.get("quantitativePrecipitation", {}).get("uom", "")
    qpf_factor = 0.1 if "kg_m" in qpf_unit else 1.0  # NWS kg/m2 is mm; tenths are common.
    return point_url, grid_url, {
        "gust": _weather_values(grid, "windGust", gust_factor),
        "qpf": _weather_values(grid, "quantitativePrecipitation", qpf_factor),
    }


def _persistence_payload():
    """Fetch NOAA observations and produce a forecast without optional ML dependencies."""
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    begin = now - timedelta(days=30)
    wl_url, wl_rows = _coops("water_level", begin, now, datum="NAVD")
    last = max(t for t, _ in _series(wl_rows, "v"))
    future = [last + STEP * (i + 1) for i in range(480)]
    wind_url, wind_rows = _coops("wind", begin, last)
    tide_url, tide_rows = _coops("predictions", last, future[-1], datum="NAVD", interval="6")
    observed = _series(wl_rows, "v")
    predicted = _series(tide_rows, "v")
    # Keep the most recent residual and let uncertainty grow over the two-day horizon.
    residual = float(observed[-1][1] - _interp(predicted, [last], observed[-1][1])[0]) if predicted else 0.0
    tide = _interp(predicted, future, observed[-1][1])
    spread = np.linspace(0.025, 0.18, len(future))
    water_q = np.stack([tide + residual + (q - 0.5) * 2.0 * spread for q in QUANTILES], axis=1)
    speed = _interp(_series(wind_rows, "s"), [last], 8.0)[0]
    gust_points = _series(wind_rows, "g")
    gust = _interp(gust_points, [last], max(speed * 1.4, 10.0))[0] if gust_points else max(speed * 1.4, 10.0)
    # A short persistence tail is more honest than inventing a weather model forecast.
    speed_path = max(0.0, speed) * np.exp(-np.arange(len(future)) / 240.0) + 7.0 * (1 - np.exp(-np.arange(len(future)) / 240.0))
    gust_path = np.maximum(speed_path * 1.35, gust * np.exp(-np.arange(len(future)) / 180.0) + 7.0)
    wind_q = np.stack([np.maximum(0.0, speed_path + (q - 0.5) * 3.0) for q in QUANTILES], axis=1)
    gust_q = np.stack([np.maximum(0.0, gust_path + (q - 0.5) * 5.0) for q in QUANTILES], axis=1)
    direction = _series(wind_rows, "d")
    wind_from = float(_interp(direction, [last], 200.0)[0]) if direction else 200.0
    sources = {"water_level": wl_url, "wind": wind_url, "tide_prediction": tide_url}
    fetched = _iso(now)
    try:
        alert_url, alerts = _alerts()
        sources["nws_alerts"] = alert_url
    except Exception as exc:
        alerts = []
        sources["nws_alerts"] = "https://api.weather.gov/alerts/active?point=37.7749,-122.4194"
        alert_error = f"{type(exc).__name__}: {exc}"
    try:
        point_url, grid_url, weather = _grid_forecast()
        sources.update({"nws_point": point_url, "nws_gridpoint": grid_url})
        qpf = _weather_at(weather["qpf"], future, 0.0)
        grid_gust = _weather_at(weather["gust"], future, 0.0)
        if grid_gust.max() > 0:
            gust_q = np.stack([np.maximum(0.0, grid_gust + (q - 0.5) * 4.0) for q in QUANTILES], axis=1)
        qpf_source = "NWS MTR gridpoint quantitativePrecipitation"
    except Exception as exc:
        qpf = np.zeros(len(future), dtype=float)
        qpf_source = "unavailable"
        weather_error = f"{type(exc).__name__}: {exc}"
    payload = {
        "schema": "stormsim.forecast.v1", "station": STATION, "issued": _iso(last), "fetched_at": fetched,
        "model": "NOAA harmonic tide + observed surge persistence (TimesFM unavailable on CPU)",
        "fallback": {"used": True, "reason": "timesfm3 optional dependency is not installed; deterministic persistence fallback",
                     "method": "NOAA harmonic prediction plus latest observed residual; wind persistence; NWS QPF when available"},
        "context_days": 30, "horizon_hours": 48, "step_minutes": 6, "quantiles": QUANTILES,
        "wind_from_deg": round(wind_from, 1), "sources": sources, "alerts": alerts,
        "forecast": {"t": [_iso(t) for t in future], "tide_prediction": tide.round(3).tolist(),
                     "water_level_q": water_q.round(3).tolist(), "wind_speed_q": wind_q.round(2).tolist(),
                     "gust_q": gust_q.round(2).tolist(), "qpf_mm": qpf.round(3).tolist()},
        "history": {"t": [_iso(t) for t, _ in observed[-480:]], "water_level": [round(v, 3) for _, v in observed[-480:]]},
        "notes": ["Rain forcing is the NWS MTR gridpoint QPF where available, with an Atlas 14 100-year floor in the sim.",
                  "P10/P50/P90 paths are persistence uncertainty bands, not a TimesFM posterior.",
                  f"QPF source: {qpf_source}"],
    }
    for key in ("alert_error", "weather_error"):
        if key in locals():
            payload["notes"].append(f"{key}: {locals()[key]}")
    return payload


def load_or_create(refresh: bool = False) -> dict:
    existing = None
    if CACHE.exists():
        try:
            existing = json.loads(CACHE.read_text())
            if existing.get("schema") == "stormsim.forecast.v1" and not refresh:
                return _decorate(existing, "cached")
        except (OSError, ValueError):
            existing = None
    try:
        payload = _persistence_payload()
    except Exception as exc:
        if existing and existing.get("schema") == "stormsim.forecast.v1":
            # Never destroy a known-good stage cache because a transient NOAA/NWS request failed.
            out = dict(existing)
            out["cache_status"] = "cached (refresh failed)"
            out["refresh_error"] = f"{type(exc).__name__}: {exc}"
            return _decorate(out, out["cache_status"])
        # Offline demo fallback: use a stable tide and wind envelope.  The provenance calls it
        # out so an offline run is never mistaken for a live fetch.
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        times = [now + STEP * (i + 1) for i in range(480)]
        tide = 1.2 + 0.45 * np.sin(np.arange(480) * 2 * math.pi / 124.2)
        payload = {"schema": "stormsim.forecast.v1", "station": STATION, "issued": _iso(now),
                   "fetched_at": _iso(now), "model": "offline synthetic tide fallback",
                   "fallback": {"used": True, "reason": f"network fetch failed: {type(exc).__name__}: {exc}"},
                   "horizon_hours": 48, "step_minutes": 6, "quantiles": QUANTILES, "wind_from_deg": 200.0,
                   "sources": {"noaa": COOPS}, "alerts": [],
                   "forecast": {"t": [_iso(t) for t in times],
                                "tide_prediction": tide.round(3).tolist(),
                                "water_level_q": np.stack([tide + (q - .5) * .2 for q in QUANTILES], axis=1).round(3).tolist(),
                                "wind_speed_q": np.full((480, 9), 8.0).tolist(), "gust_q": np.full((480, 9), 11.0).tolist(),
                                "qpf_mm": np.zeros(480).tolist()}, "notes": ["OFFLINE: no live inputs were reachable"]}
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(payload, indent=1) + "\n")
    return _decorate(payload, "live fetched" if not payload.get("fallback", {}).get("reason", "").startswith("network") else "offline fallback")


def _decorate(payload: dict, cache_status: str) -> dict:
    """Add stage-friendly freshness and countdown fields without changing the tracked cache."""
    out = dict(payload)
    out["cache_status"] = cache_status
    out["provenance"] = {"issued": out.get("issued"), "fetched_at": out.get("fetched_at"),
                          "sources": out.get("sources", {}), "model": out.get("model"),
                          "fallback": out.get("fallback", {}), "cache_status": cache_status}
    times = out.get("forecast", {}).get("t", [])
    if times:
        try:
            arrival = _parse_time(times[0])
            now = datetime.now(timezone.utc)
            out["arrival"] = {"at": _iso(arrival), "countdown_seconds": max(0, int((arrival - now).total_seconds()))}
        except (TypeError, ValueError):
            pass
    return out


def main():
    global CACHE
    parser = argparse.ArgumentParser(description="Fetch/cache StormSim SF live forecast inputs")
    parser.add_argument("--refresh", action="store_true", help="fetch NOAA/NWS inputs again")
    parser.add_argument("--out", type=Path, default=CACHE, help="cache path (default: tracked storm cache)")
    args = parser.parse_args()
    if args.out != CACHE:
        CACHE = args.out
    payload = load_or_create(refresh=args.refresh)
    print(json.dumps({"cache": str(CACHE), "issued": payload.get("issued"), "fetched_at": payload.get("fetched_at"),
                      "model": payload.get("model"), "alerts": len(payload.get("alerts", [])),
                      "points": len(payload.get("forecast", {}).get("t", []))}, indent=1))


if __name__ == "__main__":
    main()
