"""StormSim SF storm forcing (owned by damage/storm agent).

`load_storm(name) -> Storm`. All raw inputs are archived in data/reference/storm/
(fetched 25.10.2026 from NOAA CO-OPS, Iowa Environmental Mesonet, NOAA HDSC PFDS).

Conventions
- t[i] = i * dt_frame_s, i = 0..n (n+1 = 73 frame times incl. t=duration, same as water.py frames).
- rain_mm_h[i] is the mean rate over [t[i], t[i]+dt) (mass-exact block; last value 0); use
  `storm.rain_at(t)` (step) inside solvers. gust/mean/tide are instantaneous at t[i];
  use `gust_at`, `tide_at` (linear).
- gust_ms / mean_ms: 10 m open-exposure reference (airport-type anemometer).
  The LBM wind field adds street-canyon / hilltop speed-up on top.
- wind_dir_deg: scalar meteorological FROM direction used for the steady LBM field
  (dominant direction during peak gusts). wind_dir_t: per-frame series (FROM, deg).
- tide_m: still-water level at the bay boundary, m NAVD88 (NOAA 9414290).

Scenarios
- dec31_2022: real 31.12.2022 atmospheric river, 07:45-13:45 PST (6 h around the peak).
- elnino_ar_2026: "incoming El Nino AR": NOAA Atlas 14 0.2%-AEP nested 6-h core, peak
  gust 26 m/s ref (exposed sites 30-35 m/s after LBM speed-up), this winter's highest
  king tide (24.12.2026) + 0.6 m surge (1983 El Nino class).
- atlas14_100yr: SF planning design storm (1%-AEP nested 6 h) on MHHW + 0.3 m.
"""
from __future__ import annotations

import csv
import json
import math
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = os.path.join(ROOT, "data", "reference", "storm")
IN2MM = 25.4
KT2MS = 0.514444
MPH2MS = 0.44704

SRC = {
    "sfoc1": "Iowa Environmental Mesonet, SF Downtown gauge SFOC1 (CA_DCP) hourly accumulated precip 31.12.2022, "
             "https://mesonet.agron.iastate.edu/api/1/obhistory.json?station=SFOC1&network=CA_DCP&date=2022-12-31&full=1 "
             "(archived data/reference/storm/iem_sfoc1_downtown_20221231.json)",
    "nws_546": "NWS Bay Area, 1.1.2023: SF Downtown 5.46 in on 31.12.2022, 2nd-wettest day in 170+ yr record "
               "(https://x.com/NWSBayArea/status/1609513375184326656); NWS MTR event page "
               "https://www.weather.gov/mtr/AtmosphericRivers_12_2022-01_2023",
    "cnrfc": "NOAA CNRFC storm summary Late Dec 2022-Jan 2023, https://www.cnrfc.noaa.gov/storm_summaries/dec2022Jan2023storms.php",
    "ksfo": "Iowa Environmental Mesonet ASOS archive, KSFO hourly METAR 31.12.2022 (sknt, gust, peak_wind_gust, drct), "
            "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station=SFO (archived iem_ksfo_asos_20221231_local.csv)",
    "tide22": "NOAA CO-OPS 9414290 San Francisco, verified water level (6-min) and predictions 31.12.2022, datum NAVD88, "
              "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter (archived noaa_9414290_*_20221231.json)",
    "datums": "NOAA CO-OPS 9414290 datums (epoch 1983-2001): MHHW 1.798 m NAVD88, HAT 2.224 m, record 2.658 m NAVD88 "
              "on 27.1.1983 (El Nino storm), https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations/9414290/datums.json",
    "kt26": "NOAA CO-OPS 9414290 tide predictions Nov 2026-Feb 2027 (hilo): highest = 2.222 m NAVD88 at 10:36 PST 24.12.2026 "
            "(archived noaa_9414290_hilo_predictions_navd_2026-11_2027-02.json, 6-min curve noaa_9414290_predictions_6min_navd_20261224.json)",
    "atlas14": "NOAA Atlas 14 Vol. 6 v2 PFDS, partial-duration depths at 37.7765N 122.4050W (SoMa/Mission Creek), "
               "https://hdsc.nws.noaa.gov/pfds/ (archived noaa_atlas14_pfds_sf_37.7765_-122.405_mm.csv)",
    "arkstorm": "USGS ARkStorm scenario (Porter et al. 2011, USGS Open-File Report 2010-1312): federal planning scenario "
                "for an extreme California atmospheric-river sequence, https://pubs.usgs.gov/of/2010/1312/",
    "arkstorm2": "Huang & Swain 2022, Science Advances 8(32): 'Climate change is increasing the risk of a California megaflood' "
                 "(ARkStorm 2.0; warming raises extreme AR runoff/precip), https://doi.org/10.1126/sciadv.abq0995",
    "wind_mar23": "21.3.2023 bomb cyclone: 77 mph (34 m/s) gust at Pier 1 San Francisco, 88 mph Richmond (NWS MTR / SF Chronicle); "
                  "4.1.2023: SFO gust 54 mph (24 m/s) (SF Chronicle live updates, https://www.sfchronicle.com/weather/article/california-storm-flooding-17692589.php)",
    "surge": "Surge choice: 27.1.1983 El Nino storm set the SF record 2.658 m NAVD88 (NOAA 9414290 datums) roughly 0.6 m above "
             "predicted tide; NOAA/NWS 1982-83 & 1997-98 El Nino SF Bay residuals 0.3-0.6 m; "
             "California OPC 2024 Sea Level Rise Guidance (https://opc.ca.gov/risk-assessment/) for SF Bay planning levels",
    "nws_now": "NWS API https://api.weather.gov/alerts/active (snapshot data/raw/nws_alerts_now.json, 25.10.2026)",
}


@dataclass
class Storm:
    name: str
    title: str
    duration_s: float
    dt_frame_s: float
    t: np.ndarray
    rain_mm_h: np.ndarray
    gust_ms: np.ndarray
    mean_ms: np.ndarray
    wind_dir_deg: float
    tide_m: np.ndarray
    sources: list
    wind_dir_t: np.ndarray = None
    start_local: str = ""
    antecedent_rain_mm: float = 0.0   # rain in the ~24-30 h before the window (soil wetness init)
    description: str = ""
    stats: dict = field(default_factory=dict)
    alerts: list = field(default_factory=list)

    @property
    def n_frames(self) -> int:
        return len(self.t)

    def _idx(self, ts):
        return np.clip((np.asarray(ts, float) // self.dt_frame_s).astype(int), 0, self.n_frames - 1)

    def rain_at(self, ts):
        """mm/h, block-constant (mass exact)."""
        return self.rain_mm_h[self._idx(ts)]

    def gust_at(self, ts):
        return np.interp(ts, self.t, self.gust_ms)

    def mean_at(self, ts):
        return np.interp(ts, self.t, self.mean_ms)

    def tide_at(self, ts):
        return np.interp(ts, self.t, self.tide_m)

    def total_rain_mm(self) -> float:
        return float(np.sum(self.rain_mm_h) * self.dt_frame_s / 3600.0)

    def to_json(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, np.ndarray):
                d[k] = [round(float(x), 4) for x in v]
        d["total_rain_mm"] = round(self.total_rain_mm(), 2)
        d["n_frames"] = self.n_frames
        return d


# ---------------------------------------------------------------- helpers
def _ref(name):
    return os.path.join(REF, name)


def _parse_local(s):
    return datetime.strptime(s.replace("T", " ")[:16], "%Y-%m-%d %H:%M")


def _hours(ts, t0):
    return np.array([(x - t0).total_seconds() for x in ts])


def _pchip_cum(x, y, xq):
    """Monotone (Fritsch-Carlson) cubic interpolation of a cumulative curve."""
    try:
        from scipy.interpolate import PchipInterpolator
        return PchipInterpolator(x, y)(xq)
    except Exception:  # pragma: no cover
        return np.interp(xq, x, y)


def _blocks_from_cum(cum_fn, t_edges):
    c = cum_fn(t_edges)
    r = np.maximum(np.diff(c), 0) / (np.diff(t_edges) / 3600.0)  # mm/h
    return np.append(r, 0.0)  # value at t=duration: window over


def _frames(duration_s, dt):
    n = int(round(duration_s / dt))
    return np.arange(n + 1) * dt, np.arange(n + 1) * dt  # frame times 0..duration (n+1, like water.py), block edges


def _circmean(deg, w):
    r = np.deg2rad(deg)
    return float(np.rad2deg(math.atan2(np.sum(w * np.sin(r)), np.sum(w * np.cos(r)))) % 360)


def _load_alerts():
    p = os.path.join(ROOT, "data", "raw", "nws_alerts_now.json")
    out = []
    try:
        d = json.load(open(p))
        for f in d.get("features", []):
            pr = f["properties"]
            out.append({k: pr.get(k) for k in ("event", "headline", "onset", "ends", "severity", "areaDesc",
                                                 "description", "instruction", "senderName")})
    except Exception:
        pass
    return out


# ---------------------------------------------------------------- dec31_2022
def _dec31_2022(dt=300.0):
    t0 = datetime(2022, 12, 31, 7, 45)
    dur = 6 * 3600.0
    # Rain: SFOC1 accumulated precip (inches), hourly at :43
    obs = json.load(open(_ref("iem_sfoc1_downtown_20221231.json")))["data"]
    pts = [(_parse_local(r["local_valid"]), float(r["PCIRGZZ"])) for r in obs if r.get("PCIRGZZ") is not None]
    ts = _hours([p[0] for p in pts], t0)
    cum_mm = (np.array([p[1] for p in pts]) - pts[0][1]) * IN2MM
    tq, edges = _frames(dur, dt)
    rain = _blocks_from_cum(lambda q: _pchip_cum(ts, cum_mm, q), edges)
    day_total_in = pts[-1][1] - pts[0][1]
    before = float(np.interp(0.0, ts, cum_mm))
    hourly_in = np.diff([p[1] for p in pts])
    # Wind: KSFO hourly METAR
    rows = list(csv.DictReader(open(_ref("iem_ksfo_asos_20221231_local.csv"))))
    wt, gust, mean, drct = [], [], [], []
    for r in rows:
        sk = float(r["sknt"]) if r["sknt"] not in ("M", "") else 0.0
        g = [float(r[k]) for k in ("gust", "peak_wind_gust") if r[k] not in ("M", "")]
        wt.append(_parse_local(r["valid"]))
        mean.append(sk * KT2MS)
        gust.append(max(g + [1.4 * sk]) * KT2MS)  # 1.4 = ASOS gust factor when no gust is reported
        drct.append(float(r["drct"]) if r["drct"] not in ("M", "") else np.nan)
    wts = _hours(wt, t0)
    gust_ms = np.interp(tq, wts, gust)
    mean_ms = np.interp(tq, wts, mean)
    dr = np.array(drct)
    ok = ~np.isnan(dr)
    # direction series: nearest ob (avoid interpolating through 0/360)
    idx = np.abs(wts[ok][None, :] - tq[:, None]).argmin(1)
    wdir_t = dr[ok][idx]
    win = (wts >= -1800) & (wts <= dur + 1800) & ok & (np.array(mean) > 4)
    wdir = _circmean(dr[win], np.array(gust)[win] ** 2)
    # Tide: verified 6-min water level, NAVD88
    wl = json.load(open(_ref("noaa_9414290_water_level_6min_navd_20221231.json")))["data"]
    tt = _hours([_parse_local(r["t"]) for r in wl], t0)
    tv = np.array([float(r["v"]) for r in wl])
    tide = np.interp(tq, tt, tv)
    pr = json.load(open(_ref("noaa_9414290_predictions_navd_20221231.json")))["predictions"]
    pt = _hours([_parse_local(r["t"]) for r in pr], t0)
    surge = tide - np.interp(tq, pt, [float(r["v"]) for r in pr])
    s = Storm(
        name="dec31_2022", title="Replay: New Year's Eve atmospheric river, 31.12.2022",
        duration_s=dur, dt_frame_s=dt, t=tq, rain_mm_h=rain, gust_ms=gust_ms, mean_ms=mean_ms,
        wind_dir_deg=round(wdir, 1), tide_m=tide, wind_dir_t=wdir_t,
        start_local="2022-12-31T07:45-08:00",
        antecedent_rain_mm=round(before + 0.81 * IN2MM, 1),
        description=("Observed forcing for the 6 h around the peak of the 31.12.2022 AR at SF Downtown: "
                     f"{day_total_in:.2f} in fell 00:43-15:43 PST at SFOC1 (NWS daily 5.46 in, 2nd-wettest day on record). "
                     "Rain = SF Downtown gauge (hourly, monotone-interpolated to 5 min, hourly totals preserved). "
                     "Wind = KSFO ASOS (nearest official anemometer; peak gust 35 kt). Tide = NOAA 9414290 verified, NAVD88: "
                     "the 2.09 m morning high tide (06:12, ~0.27 m above prediction) was ebbing as the 3 h burst (0.81/0.85/0.97 in/h) arrived."),
        sources=[SRC["sfoc1"], SRC["nws_546"], SRC["cnrfc"], SRC["ksfo"], SRC["tide22"], SRC["datums"], SRC["atlas14"]],
    )
    pf = _atlas14()
    six_h = s.total_rain_mm()
    s.stats = {
        "window_rain_mm": round(six_h, 1),
        "peak_hour_mm": round(float(hourly_in.max() * IN2MM), 1),
        "peak_5min_rate_mm_h": round(float(rain.max()), 1),
        "peak_gust_ms": round(float(gust_ms.max()), 1),
        "peak_tide_m_navd": round(float(tide.max()), 3),
        "max_surge_m": round(float(surge.max()), 3),
        "atlas14_6h_ari_years": _ari_for(pf, "6-hr", six_h),
        "atlas14_1h_ari_years": _ari_for(pf, "60-min", float(hourly_in.max() * IN2MM)),
        "sfoc1_hourly_in_ending_0843_1343": [round(float(x), 2) for x in hourly_in[7:13]],
    }
    return s


# ---------------------------------------------------------------- Atlas 14 design storms
_DUR_MIN = {"5-min": 5, "10-min": 10, "15-min": 15, "30-min": 30, "60-min": 60, "2-hr": 120, "3-hr": 180,
            "6-hr": 360, "12-hr": 720, "24-hr": 1440, "2-day": 2880}


def _atlas14():
    out = {}
    ari = None
    for line in open(_ref("noaa_atlas14_pfds_sf_37.7765_-122.405_mm.csv")):
        parts = [p.strip() for p in line.strip().split(",")]
        if parts[0].startswith("by duration"):
            ari = [int(x) for x in parts[1:] if x]
        elif parts[0].rstrip(":") in _DUR_MIN and ari and "depth" not in out.get("_done", ""):
            key = parts[0].rstrip(":")
            if key in out:
                continue
            out[key] = dict(zip(ari, [float(x) for x in parts[1:len(ari) + 1]]))
    return out


def _ari_for(pf, dur, depth):
    row = pf[dur]
    aris = sorted(row)
    d = [row[a] for a in aris]
    if depth <= d[0]:
        return f"<{aris[0]}"
    if depth >= d[-1]:
        return f">{aris[-1]}"
    return round(float(np.exp(np.interp(depth, d, np.log(aris)))), 0)


def _nested_hyetograph(pf, ari, dur_s, dt, peak_frac):
    """Alternating-block nested storm from Atlas 14 DDF (log-log interpolation)."""
    mins = np.array([_DUR_MIN[k] for k in pf if k in _DUR_MIN and _DUR_MIN[k] <= 1440], float)
    keys = [k for k in pf if k in _DUR_MIN and _DUR_MIN[k] <= 1440]
    dep = np.array([pf[k][ari] for k in keys], float)
    n = int(round(dur_s / dt))
    dmin = np.arange(1, n + 1) * dt / 60.0
    D = np.exp(np.interp(np.log(dmin), np.log(mins), np.log(dep)))
    inc = np.diff(np.concatenate([[0.0], D]))  # block depths, descending-ish
    order = np.argsort(-inc)
    blocks = np.zeros(n)
    pk = int(round(peak_frac * (n - 1)))
    pos = [pk]
    l, r = pk - 1, pk + 1
    while len(pos) < n:  # alternate right/left around peak
        if r < n:
            pos.append(r); r += 1
        if l >= 0 and len(pos) < n:
            pos.append(l); l -= 1
    for k, p in zip(order, pos):
        blocks[p] = inc[k]
    return np.append(blocks / (dt / 3600.0), 0.0), float(D[-1])


def _design(name, ari, peak_gust, wind_dir, tide_kind, dt=300.0):
    dur = 6 * 3600.0
    pf = _atlas14()
    tq, edges = _frames(dur, dt)
    rain, core = _nested_hyetograph(pf, ari, dur, dt, peak_frac=0.58)
    # Wind: pre-frontal AR jet, gust peak ~40 min before the rain burst (cold-front passage)
    tp = 0.48 * dur
    g = 9.0 + (peak_gust - 9.0) * np.exp(-0.5 * ((tq - tp) / (1.25 * 3600)) ** 2)
    mean = g / 1.45  # open-terrain gust factor (ESDU / ASCE 7 ~1.4-1.5 at 10 m)
    wdir_t = np.where(tq < 0.62 * dur, wind_dir, wind_dir + 60.0) % 360  # veer to W/NW behind the front
    sources = [SRC["atlas14"], SRC["arkstorm"], SRC["arkstorm2"], SRC["wind_mar23"], SRC["datums"]]
    if tide_kind == "king+surge":
        pr = json.load(open(_ref("noaa_9414290_predictions_6min_navd_20261224.json")))["predictions"]
        t0 = datetime(2026, 12, 24, 10, 36) - timedelta(seconds=0.58 * dur)
        pt = _hours([_parse_local(r["t"]) for r in pr], t0)
        astro = np.interp(tq, pt, [float(r["v"]) for r in pr])
        surge = 0.35 + 0.25 * np.exp(-0.5 * ((tq - 0.58 * dur) / (2.0 * 3600)) ** 2)  # peaks at +0.60 m
        tide = astro + surge
        start = t0.strftime("%Y-%m-%dT%H:%M-08:00")
        sources += [SRC["kt26"], SRC["surge"], SRC["nws_now"]]
    else:
        mhhw = 1.798
        tide = mhhw + 0.3 * np.cos(2 * np.pi * (tq - 0.58 * dur) / (12.42 * 3600))
        start = ""
        surge = np.zeros_like(tq)
    D24 = pf["24-hr"][ari]
    s = Storm(name=name, title="", duration_s=dur, dt_frame_s=dt, t=tq, rain_mm_h=rain, gust_ms=g, mean_ms=mean,
              wind_dir_deg=float(wind_dir), tide_m=tide, wind_dir_t=wdir_t, start_local=start,
              antecedent_rain_mm=round(D24 - core, 1), sources=sources)
    s.stats = {
        "window_rain_mm": round(s.total_rain_mm(), 1), "atlas14_ari_years": ari,
        "atlas14_6h_mm": pf["6-hr"][ari], "atlas14_1h_mm": pf["60-min"][ari], "atlas14_24h_mm": D24,
        "peak_5min_rate_mm_h": round(float(rain.max()), 1), "peak_gust_ms": round(float(g.max()), 1),
        "peak_tide_m_navd": round(float(tide.max()), 3), "max_surge_m": round(float(surge.max()), 3),
    }
    return s


def _elnino(dt=300.0):
    s = _design("elnino_ar_2026", 500, peak_gust=26.0, wind_dir=200.0, tide_kind="king+surge", dt=dt)
    s.title = "Incoming El Nino atmospheric river (scenario), king tide 24.12.2026"
    s.description = (
        "Scenario storm, deliberately larger than 31.12.2022 at every duration: NOAA Atlas 14 0.2%-AEP (500-yr) nested "
        f"6-h core ({s.stats['atlas14_6h_mm']:.0f} mm in 6 h, {s.stats['atlas14_1h_mm']:.0f} mm in the peak hour, "
        f"5-min burst {s.stats['peak_5min_rate_mm_h']:.0f} mm/h) after {s.antecedent_rain_mm:.0f} mm of antecedent AR rain "
        f"(24-h total {s.stats['atlas14_24h_mm']:.0f} mm = Atlas 14 500-yr 24-h). 31.12.2022 itself was ~200-yr at 6 h downtown, "
        "so a 500-yr core is the ARkStorm-style stress test (USGS ARkStorm; Huang & Swain 2022 show warming raises such storms). "
        "SSW pre-frontal jet, 10 m reference gust 26 m/s (58 mph; SFO saw 24 m/s on 4.1.2023) so exposed sites reach the "
        "30-35 m/s seen at Pier 1 on 21.3.2023 after LBM speed-up; veers W behind the front. Bay level: this winter's highest "
        "predicted tide (2.22 m NAVD88, 24.12.2026 10:36) + 0.6 m surge, a 1983-El-Nino-class residual -> 2.82 m NAVD88, "
        "0.16 m above the 1983 record.")
    s.alerts = _load_alerts()
    return s


def _atlas100(dt=300.0):
    s = _design("atlas14_100yr", 100, peak_gust=20.0, wind_dir=200.0, tide_kind="mhhw", dt=dt)
    s.title = "SF planning design storm: Atlas 14 100-yr nested 6 h on MHHW+0.3 m"
    s.description = "NOAA Atlas 14 1%-AEP nested 6-h storm (SFPUC 100-yr flood map basis), MHHW tide +/-0.3 m, 20 m/s gust."
    return s


SCENARIOS = {"dec31_2022": _dec31_2022, "elnino_ar_2026": _elnino, "atlas14_100yr": _atlas100}


def load_storm(name: str = "elnino_ar_2026", dt_frame_s: float = 300.0) -> Storm:
    if name not in SCENARIOS:
        raise KeyError(f"unknown storm {name!r}; choose {list(SCENARIOS)}")
    return SCENARIOS[name](dt_frame_s)


if __name__ == "__main__":
    import sys
    for nm in (sys.argv[1:] or list(SCENARIOS)):
        s = load_storm(nm)
        print(nm, "frames", s.n_frames, "rain mm", round(s.total_rain_mm(), 1), "dir", s.wind_dir_deg,
              "antecedent", s.antecedent_rain_mm)
        print("  stats", s.stats)
        print("  rain", np.round(s.rain_mm_h[::6], 1).tolist())
        print("  gust", np.round(s.gust_ms[::6], 1).tolist())
        print("  tide", np.round(s.tide_m[::6], 2).tolist())
