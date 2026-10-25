"""Render a StormSim run (runs/<id>/frames) into a legible MP4.

Left: top-down map (north up). Right: oblique heightfield view from the south.
Header: label, sim time, rain, gust, running damage, counters. Bottom: rain/gust strip + legend.

    render_run(run_dir, out_path=None, grid_dir="data/grid", duration_s=24.0, fps=12, workers=16) -> str
    python -m sim.render_video runs/<id> [--out x.mp4] [--grid data/grid] [--duration 24] [--fps 12]
"""
from __future__ import annotations

import argparse
import json
import math
import multiprocessing as mp
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:
    from numba import njit
except Exception:  # pragma: no cover
    njit = None

REPO = Path(__file__).resolve().parent.parent
W_OUT, H_OUT = 1280, 720
HEADER_H = 58
PANEL_Y0, PANEL_Y1 = 64, 636
STRIP_Y0, STRIP_Y1 = 644, 714
MARGIN = 10
BG = (10, 13, 19)
PANEL_BG = (17, 21, 29)
TXT = (232, 236, 242)
TXT_DIM = (140, 150, 166)
ACCENT = (255, 176, 46)
RAIN_C = (88, 170, 255)
GUST_C = (255, 176, 46)
TREE_LIVE = np.array([64, 168, 84], np.float32)
TREE_DEAD = (255, 54, 54)
CAR_OK = (205, 210, 220)
CAR_MOVED = (255, 140, 0)
EX = 2.5            # vertical exaggeration in oblique view
BEX = 1.3           # building height exaggeration in oblique view
WEX = 2.0           # extra exaggeration of water depth in oblique view
WATER_MIN = 0.03    # m, below this water is not drawn

# depth colour ramp (m) and alpha
_D_ST = np.array([0.0, 0.05, 0.15, 0.3, 0.6, 1.0, 1.5])
_D_COL = np.array([[150, 225, 255], [120, 208, 255], [64, 166, 248], [38, 118, 232],
                   [28, 74, 205], [36, 38, 168], [104, 24, 150]], np.float32)
_A_ST = np.array([0.0, WATER_MIN, 0.05, 0.15, 0.3, 1.0])
_A_V = np.array([0.0, 0.0, 0.42, 0.68, 0.84, 0.94])
_LUT_N = 512
_LUT_D = np.linspace(0, 1.5, _LUT_N)
WATER_LUT = np.stack([np.interp(_LUT_D, _D_ST, _D_COL[:, c]) for c in range(3)], 1).astype(np.float32)
ALPHA_LUT = np.interp(_LUT_D, _A_ST, _A_V).astype(np.float32)

_S: dict = {}  # shared state for forked workers


# ----------------------------------------------------------------------------- helpers
def _font(size, bold=False):
    try:
        import matplotlib
        p = Path(matplotlib.get_data_path()) / "fonts" / "ttf" / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
        return ImageFont.truetype(str(p), size)
    except Exception:
        try:
            return ImageFont.truetype("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", size)
        except Exception:
            return ImageFont.load_default()


def _resolve(p):
    p = Path(p)
    return p if p.is_absolute() else (REPO / p)


def _jload(p, default=None):
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return default


def _money(v):
    v = float(v or 0)
    if abs(v) >= 1e9:
        return f"${v / 1e9:.2f}B"
    if abs(v) >= 1e6:
        return f"${v / 1e6:.1f}M"
    if abs(v) >= 1e3:
        return f"${v / 1e3:.0f}k"
    return f"${v:.0f}"


def _hhmm(s):
    s = max(0, int(round(s)))
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}"


def _dmg_color(usd):
    f = np.clip((np.log10(np.maximum(usd, 1.0)) - 3.0) / 3.0, 0, 1)
    st = np.array([0.0, 0.5, 1.0])
    cols = np.array([[250, 222, 72], [246, 134, 40], [222, 34, 34]], np.float32)
    return np.stack([np.interp(f, st, cols[:, c]) for c in range(3)], -1).astype(np.float32)


def _hillshade(s, dx, az=315.0, alt=45.0):
    gy, gx = np.gradient(s.astype(np.float32), dx)  # rows = north (row 0 south)
    a, b = math.radians(az), math.radians(alt)
    L = np.array([math.sin(a) * math.cos(b), math.cos(a) * math.cos(b), math.sin(b)], np.float32)
    n = np.sqrt(gx * gx + gy * gy + 1.0)
    return np.clip((-gx * L[0] - gy * L[1] + L[2]) / n, 0, 1)


def _fit(a, ny, nx, fill):
    """crop/pad 2D array (row 0 south) to (ny, nx)."""
    out = np.full((ny, nx), fill, a.dtype)
    h, w = min(ny, a.shape[0]), min(nx, a.shape[1])
    out[:h, :w] = a[:h, :w]
    return out


# ----------------------------------------------------------------------------- raycaster
def _raycast_py(col, hgt, out, s, D, Hc, horizon, fogc, x_off):
    H, W = hgt.shape
    ph, pw = out.shape[0], out.shape[1]
    for i in range(pw):
        ybuf = ph
        iyf = 0.0
        while iyf < H - 1:
            z = D + iyf
            step = max(0.5, 0.8 * z / D)
            ixf = W * 0.5 + (i - pw * 0.5 - x_off) * z / (s * D)
            if 0.0 <= ixf < W:
                iy = int(iyf)
                ix = int(ixf)
                h = hgt[iy, ix]
                k = s * D / z
                y = horizon + (Hc - h) * k
                yt = int(y)
                if yt < ybuf:
                    yflat = horizon + (Hc - h) * s * D / max(z - step, 1.0) + 1.0
                    fog = 0.5 * iyf / H
                    y0 = yt if yt > 0 else 0
                    for yy in range(y0, ybuf):
                        sh = 1.0 if yy <= yflat else 0.6 - 0.15 * min(1.0, (yy - yflat) / 40.0)
                        for c in range(3):
                            out[yy, i, c] = col[iy, ix, c] * sh * (1.0 - fog) + fogc[c] * fog
                    ybuf = y0
                    if ybuf <= 0:
                        break
            iyf += step
    return out


_raycast = njit(cache=False, nogil=True)(_raycast_py) if njit is not None else _raycast_py


# ----------------------------------------------------------------------------- setup
def _setup(run_dir: Path, grid_dir: Path, duration_s: float, fps: int):
    fr = run_dir / "frames"
    man = _jload(fr / "manifest.json", {}) or {}
    files = man.get("files") or {}
    if isinstance(files, list):
        files = {}
    dpath = fr / (files.get("depth") if isinstance(files.get("depth"), str) else "depth.u16")
    if not dpath.exists():
        dpath = fr / "depth.u16"
    nx8, ny8 = int(man["nx"]), int(man["ny"])
    dx8 = float(man.get("dx_frame", man.get("dx", 8.0)))
    raw = np.fromfile(dpath, dtype=np.uint16)
    nf = raw.size // (nx8 * ny8)
    nf = min(nf, int(man.get("n_frames", nf)))
    depth = raw[: nf * ny8 * nx8].reshape(nf, ny8, nx8)
    t = np.asarray(man.get("t") or np.arange(nf) * 60.0, np.float64)[:nf]
    if t.size < nf:
        t = np.arange(nf) * (t[1] - t[0] if t.size > 1 else 60.0)
    rain = np.asarray(man.get("rain") or man.get("rain_mm_h") or np.zeros(nf), np.float64)
    gust = np.asarray(man.get("gust") or man.get("gust_ms") or np.zeros(nf), np.float64)
    rain = np.interp(np.arange(nf), np.linspace(0, nf - 1, max(rain.size, 1)), rain) if rain.size != nf else rain
    gust = np.interp(np.arange(nf), np.linspace(0, nf - 1, max(gust.size, 1)), gust) if gust.size != nf else gust

    # ---- grid
    meta = _jload(grid_dir / "meta.json") if grid_dir else None
    city = None
    if meta and (grid_dir / "city.npz").exists():
        try:
            city = np.load(grid_dir / "city.npz")
        except Exception:
            city = None
    has_grid = city is not None and "z" in city
    fx0 = man.get("x0", meta.get("x0") if meta else None)
    fy0 = man.get("y0", meta.get("y0") if meta else None)
    if has_grid:
        dr = float(meta.get("dx", 4.0))
        f = max(1, int(round(dx8 / dr)))
        dr = dx8 / f
        gx0, gy0 = float(meta.get("x0", 0)), float(meta.get("y0", 0))
        ox = int(round(((fx0 if fx0 is not None else gx0) - gx0) / dr))
        oy = int(round(((fy0 if fy0 is not None else gy0) - gy0) / dr))
        Hr, Wr = ny8 * f, nx8 * f
        # cut the grid window that matches the frame extent
        def win(a, fill):
            a = a[max(oy, 0):, max(ox, 0):]
            b = _fit(a, Hr - max(-oy, 0), Wr - max(-ox, 0), fill)
            out = np.full((Hr, Wr), fill, a.dtype)
            out[max(-oy, 0):, max(-ox, 0):] = b
            return out
        z = win(np.asarray(city["z"], np.float32), np.float32(np.nan))
        zfill = np.nanpercentile(z, 2) if np.isfinite(z).any() else 0.0
        z = np.where(np.isfinite(z), z, zfill).astype(np.float32)
        bid = win(np.asarray(city["bldg_id"], np.int32), np.int32(-1)) if "bldg_id" in city else np.full((Hr, Wr), -1, np.int32)
        bh = win(np.asarray(city["bldg_h"], np.float32), np.float32(0)) if "bldg_h" in city else np.zeros((Hr, Wr), np.float32)
        cover = win(np.asarray(city["cover"], np.uint8), np.uint8(255)) if "cover" in city else None
        X0, Y0 = gx0 + ox * dr, gy0 + oy * dr
    else:
        f, dr = 1, dx8
        Hr, Wr = ny8, nx8
        z = np.zeros((Hr, Wr), np.float32)
        bid = np.full((Hr, Wr), -1, np.int32)
        bh = np.zeros((Hr, Wr), np.float32)
        cover = None
        X0, Y0 = (fx0 or 0.0), (fy0 or 0.0)
    bh = np.where(bid >= 0, np.maximum(bh, 3.0), 0).astype(np.float32)
    isb = bid >= 0

    # ---- static base colours (row 0 south)
    base = np.empty((Hr, Wr, 3), np.float32)
    if has_grid:
        land = z > -0.5
        lo, hi = (np.percentile(z[land], [2, 98]) if land.any() else (0.0, 1.0))
        zn = np.clip((z - lo) / max(hi - lo, 1e-3), 0, 1)
        g = 44 + 40 * zn
        base[..., 0], base[..., 1], base[..., 2] = g, g + 2, g + 7
        if cover is not None:
            m = cover == 2
            base[m] *= np.array([0.88, 1.12, 0.84], np.float32)
            m = cover == 0
            base[m] *= 0.82
            bay = cover == 3
        else:
            bay = z < -1.0
        hs = _hillshade(z + bh, dr)
        base *= (0.55 + 0.6 * hs)[..., None]
        base[bay] = (16, 27, 45)
        lum = 128 + 80 * np.clip(bh / 60.0, 0, 1)
        bshade = 0.78 + 0.32 * hs
        for c, off in enumerate((0, 0, 5)):
            base[..., c] = np.where(isb, (lum + off) * bshade, base[..., c])
        edge = np.zeros_like(isb)
        edge[1:, :] |= bid[1:, :] != bid[:-1, :]
        edge[:-1, :] |= bid[:-1, :] != bid[1:, :]
        edge[:, 1:] |= bid[:, 1:] != bid[:, :-1]
        edge[:, :-1] |= bid[:, :-1] != bid[:, 1:]
        base[edge & isb] *= 0.68
    else:
        base[:] = (36, 40, 47)
        step = max(1, int(round(500.0 / dr)))
        base[::step, :] = (52, 57, 66)
        base[:, ::step] = (52, 57, 66)
        bay = np.zeros((Hr, Wr), bool)
    base = np.clip(base, 0, 255)

    zmin = float(np.percentile(z, 1)) if has_grid else 0.0
    zrel = np.maximum(z - zmin, -2.0)
    h_static = ((zrel * EX + bh * BEX) / dr).astype(np.float32)

    # ---- buildings & damage
    dmg = _jload(fr / "damage.json", {}) or {}
    result = _jload(run_dir / "result.json", {}) or {}
    ledger = result.get("ledger") if isinstance(result.get("ledger"), dict) else {}
    by_b = dmg.get("by_building") or (ledger or {}).get("by_building") or {}
    total_usd = float(dmg.get("total_usd") or (ledger or {}).get("total_usd") or result.get("total_usd") or 0.0)
    blds = _jload(grid_dir / "buildings.json", []) if has_grid else []
    id2idx = {}
    if isinstance(blds, list):
        for i, b in enumerate(blds):
            if isinstance(b, dict) and "id" in b:
                id2idx[str(b["id"])] = i
    nb = int(max(bid.max() + 1, len(blds) if isinstance(blds, list) else 0, 1))
    usd_b = np.zeros(nb, np.float64)
    for k_, v in (by_b.items() if isinstance(by_b, dict) else []):
        i = id2idx.get(str(k_))
        if i is None:
            try:
                i = int(k_)
            except Exception:
                continue
        if 0 <= i < nb:
            usd_b[i] += float(v or 0)
    if total_usd <= 0:
        total_usd = float(usd_b.sum())
    other_usd = max(0.0, total_usd - float(usd_b[: bid.max() + 1].sum() if bid.max() >= 0 else 0.0))

    # global ramps
    thr = 100  # mm
    flooded = (depth > thr).reshape(nf, -1).sum(1).astype(np.float64)
    fl_cm = np.maximum.accumulate(flooded)
    ramp_flood = fl_cm / fl_cm[-1] if fl_cm[-1] > 0 else np.linspace(0, 1, nf)
    gp = np.cumsum(np.maximum(gust, 0) ** 3)
    ramp_wind = gp / gp[-1] if gp[-1] > 0 else np.linspace(0, 1, nf)

    # per-building flood exposure per source frame (cummax of neighbourhood depth)
    bfrac = None
    if isb.any() and usd_b.any():
        from scipy.ndimage import maximum_filter
        iyR, ixR = np.nonzero(isb)
        c8 = (np.clip(iyR // f, 0, ny8 - 1) * nx8 + np.clip(ixR // f, 0, nx8 - 1)).astype(np.int64)
        pair = np.unique(bid[iyR, ixR].astype(np.int64) * (ny8 * nx8) + c8)
        pb = pair // (ny8 * nx8)
        pc = pair % (ny8 * nx8)
        starts = np.r_[0, np.nonzero(np.diff(pb))[0] + 1]
        ub = pb[starts]
        md = maximum_filter(depth, size=(1, 3, 3)).reshape(nf, -1)
        vals = md[:, pc]
        bmax = np.maximum.reduceat(vals, starts, axis=1).astype(np.float32)  # nf x len(ub)
        bcum = np.maximum.accumulate(bmax, axis=0)
        fin = bcum[-1]
        fr_ = np.where(fin[None, :] > 150, np.clip((bcum - 100.0) / np.maximum(fin[None, :] - 100.0, 1), 0, 1),
                       ramp_wind[:, None])
        bfrac = np.zeros((nf, nb), np.float32)
        bfrac[:, ub] = fr_
        nob = np.setdiff1d(np.arange(nb), ub)
        bfrac[:, nob] = ramp_wind[:, None]

    # ---- trees
    ft = _jload(fr / "trees.json", []) or []
    gtrees = _jload(grid_dir / "trees.json", []) if has_grid or (grid_dir / "trees.json").exists() else []
    pos = {}
    for tr in gtrees if isinstance(gtrees, list) else []:
        if isinstance(tr, dict) and "x" in tr and "y" in tr:
            pos[str(tr.get("id"))] = (float(tr["x"]), float(tr["y"]), float(tr.get("height_m") or 10.0))
    tx, ty, tf, tdir, th = [], [], [], [], []
    seen = set()
    for tr in ft if isinstance(ft, list) else []:
        if not isinstance(tr, dict):
            continue
        key = str(tr.get("id"))
        p = pos.get(key)
        if p is None and "x" in tr and "y" in tr:
            p = (float(tr["x"]), float(tr["y"]), float(tr.get("height_m") or 10.0))
        if p is None:
            continue
        seen.add(key)
        tx.append(p[0]); ty.append(p[1]); th.append(p[2])
        ftv = tr.get("fail_t")
        tf.append(float(ftv) if ftv is not None else np.inf)
        tdir.append(float(tr.get("fall_dir_deg") or 0.0))
    for key, p in pos.items():  # trees with no frame entry: live
        if key not in seen:
            tx.append(p[0]); ty.append(p[1]); th.append(p[2]); tf.append(np.inf); tdir.append(0.0)
    tx, ty = np.array(tx, np.float64), np.array(ty, np.float64)
    trees = dict(ix=(tx - X0) / dr, iy=(ty - Y0) / dr, fail=np.array(tf), dir=np.array(tdir), h=np.array(th))
    inb = (trees["ix"] >= 0) & (trees["ix"] < Wr) & (trees["iy"] >= 0) & (trees["iy"] < Hr)
    trees = {k: v[inb] for k, v in trees.items()} if len(tx) else {k: np.zeros(0) for k in ("ix", "iy", "fail", "dir", "h")}
    n_trees_total = int(len(trees["ix"]))

    # ---- cars / debris
    def load_parts(name, ncomp):
        p = fr / name
        if not p.exists():
            return None
        a = np.fromfile(p, dtype=np.float32)
        if a.size == 0 or a.size % (nf * ncomp):
            nfa = a.size // ncomp
            return None if nfa == 0 else None
        return a.reshape(nf, -1, ncomp)
    cars = load_parts("cars.f32", 4)
    debris = load_parts("debris.f32", 3)
    car_moved = None
    if cars is not None and cars.shape[1]:
        disp = np.hypot(cars[:, :, 0] - cars[0:1, :, 0], cars[:, :, 1] - cars[0:1, :, 1])
        lift = cars[:, :, 2] - cars[0:1, :, 2]
        car_moved = np.maximum.accumulate((disp > 3.0) | (lift > 0.25), axis=0)

    # ---- timing
    n_out = max(2, int(round(duration_s * fps)))
    n_hold = min(int(round(1.5 * fps)), n_out // 6)
    n_play = n_out - n_hold
    t_out = np.r_[np.linspace(t[0], t[-1], n_play), np.full(n_hold, t[-1])]
    fidx = np.interp(t_out, t, np.arange(nf))

    # ---- layout
    pw_all = W_OUT - 3 * MARGIN
    ph = PANEL_Y1 - PANEL_Y0
    mw = int(round(ph * Wr / Hr))
    mh = ph
    if mw > int(pw_all * 0.48):
        mw = int(pw_all * 0.48)
        mh = int(round(mw * Hr / Wr))
    map_x0 = MARGIN
    map_y0 = PANEL_Y0 + (ph - mh) // 2
    ob_x0 = MARGIN * 2 + mw
    ob_w = W_OUT - MARGIN - ob_x0
    ob_h = ph
    base_disp = Image.fromarray(np.flipud(base).astype(np.uint8)).resize((mw, mh), Image.LANCZOS)
    sx, sy = mw / Wr, mh / Hr
    # panel pixel -> source cell maps
    pxx = np.minimum((np.arange(mw) + 0.5) / sx, Wr - 1).astype(np.int64)
    pyy = np.minimum((Hr - (np.arange(mh) + 0.5) / sy), Hr - 1).astype(np.int64)
    py8 = np.clip(pyy // f, 0, ny8 - 1)
    px8 = np.clip(pxx // f, 0, nx8 - 1)
    bid_disp = bid[pyy[:, None], pxx[None, :]]
    bay_disp = bay[pyy[:, None], pxx[None, :]]
    bay8 = np.zeros((ny8, nx8), bool)
    if bay.any():
        bay8 = _fit(bay[: (Hr // f) * f, : (Wr // f) * f].reshape(Hr // f, f, Wr // f, f).mean((1, 3)) > 0.5, ny8, nx8, False)
    land8 = ~bay8

    # oblique camera
    s_cam = ob_w * 0.97 / Wr
    D = 1.05 * Hr
    band = 0.74 * ob_h
    Hc = band / (s_cam * (1.0 - D / (D + Hr)))
    horizon = ob_h - 6 - Hc * s_cam
    sky = np.zeros((ob_h, ob_w, 3), np.float32)
    yy = np.linspace(0, 1, ob_h)[:, None]
    for c, (a, b) in enumerate(((12, 40), (16, 48), (26, 62))):
        sky[..., c] = a + (b - a) * np.clip(yy / 0.45, 0, 1)
    fogc = np.array([40, 48, 62], np.float32)

    # static strip image
    strip = _make_strip(t, rain, gust, W_OUT, STRIP_Y1 - STRIP_Y0)

    label = str(result.get("label") or man.get("label") or run_dir.name)
    plan_cost = None
    pc = result.get("plan_cost")
    if isinstance(pc, dict):
        plan_cost = pc.get("total_usd")
    elif isinstance(pc, (int, float)):
        plan_cost = pc
    if plan_cost is None and isinstance(result.get("plan"), dict):
        plan_cost = result["plan"].get("cost_usd") or result["plan"].get("total_usd")

    _S.update(dict(bay=bay, bay_disp=bay_disp, land8=land8, depth=depth, t=t, rain=rain, gust=gust, nf=nf, nx8=nx8, ny8=ny8, f=f, dr=dr, Hr=Hr, Wr=Wr,
                   z=z, zrel=zrel, bid=bid, isb=isb, base=base, h_static=h_static, has_grid=has_grid,
                   usd_b=usd_b, other_usd=other_usd, total_usd=total_usd, bfrac=bfrac, ramp_flood=ramp_flood,
                   ramp_wind=ramp_wind, trees=trees, n_trees_total=n_trees_total, cars=cars, car_moved=car_moved,
                   debris=debris, X0=X0, Y0=Y0, t_out=t_out, fidx=fidx, n_out=n_out, n_play=n_play,
                   mw=mw, mh=mh, map_x0=map_x0, map_y0=map_y0, ob_x0=ob_x0, ob_w=ob_w, ob_h=ob_h,
                   base_disp=np.asarray(base_disp, np.float32), sx=sx, sy=sy, py8=py8, px8=px8, bid_disp=bid_disp,
                   s_cam=s_cam, D=D, Hc=Hc, horizon=horizon, sky=sky, fogc=fogc, strip=strip, label=label,
                   plan_cost=plan_cost, dx8=dx8))
    return n_out


def _make_strip(t, rain, gust, W, H):
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    f9 = _font(11)
    x0, x1 = MARGIN + 4, 860
    y0, y1 = 16, H - 6
    d.rounded_rectangle([MARGIN, 0, x1 + 6, H - 1], 6, fill=PANEL_BG)
    tt = (t - t[0]) / max(t[-1] - t[0], 1)
    X = x0 + tt * (x1 - x0)
    rmax = max(float(rain.max()), 1e-6)
    gmax = max(float(gust.max()), 1e-6)
    pr = [(x0, y1)] + [(float(x), float(y1 - (y1 - y0) * r / rmax)) for x, r in zip(X, rain)] + [(x1, y1)]
    d.polygon(pr, fill=(36, 74, 120))
    d.line(pr[1:-1], fill=RAIN_C, width=1)
    pg = [(float(x), float(y1 - (y1 - y0) * g / gmax)) for x, g in zip(X, gust)]
    d.line(pg, fill=GUST_C, width=2)
    d.text((x0 + 2, 2), f"RAIN mm/h (peak {rain.max():.0f})", font=f9, fill=RAIN_C)
    d.text((x0 + 190, 2), f"GUST m/s (peak {gust.max():.0f})", font=f9, fill=GUST_C)
    dur = t[-1] - t[0]
    for h in range(0, int(dur // 3600) + 1):
        xx = x0 + (h * 3600) / max(dur, 1) * (x1 - x0)
        d.line([(xx, y1 - 3), (xx, y1)], fill=TXT_DIM)
        d.text((xx + 2, y1 - 13), f"{h}h", font=f9, fill=TXT_DIM)
    # legend (right side)
    lx = x1 + 16
    d.rounded_rectangle([lx - 4, 0, W - MARGIN, H - 1], 6, fill=PANEL_BG)
    d.text((lx + 4, 3), "WATER DEPTH", font=f9, fill=TXT_DIM)
    bx0, bx1, by0, by1 = lx + 92, W - MARGIN - 12, 5, 15
    for i in range(bx1 - bx0):
        dd = 0.05 + (1.5 - 0.05) * i / (bx1 - bx0 - 1)
        c = WATER_LUT[min(int(dd / 1.5 * (_LUT_N - 1)), _LUT_N - 1)]
        d.line([(bx0 + i, by0), (bx0 + i, by1)], fill=tuple(int(v) for v in c))
    for v in (0.05, 0.5, 1.0, 1.5):
        xx = bx0 + (v - 0.05) / 1.45 * (bx1 - bx0 - 1)
        d.line([(xx, by1), (xx, by1 + 3)], fill=TXT_DIM)
        lab = f"{v:g} m"
        d.text((min(xx - 10, bx1 - d.textlength(lab, font=f9)), by1 + 3), lab, font=f9, fill=TXT_DIM)
    yl = 40
    xx = lx + 4
    d.ellipse([xx, yl + 4, xx + 5, yl + 9], fill=tuple(int(v) for v in TREE_LIVE))
    d.text((xx + 9, yl), "tree", font=f9, fill=TXT)
    xx += 44
    d.line([(xx, yl + 2), (xx + 7, yl + 9)], fill=TREE_DEAD, width=2)
    d.line([(xx, yl + 9), (xx + 7, yl + 2)], fill=TREE_DEAD, width=2)
    d.text((xx + 11, yl), "tree failed", font=f9, fill=TXT)
    xx += 82
    d.rectangle([xx, yl + 3, xx + 5, yl + 8], fill=CAR_OK)
    d.rectangle([xx + 9, yl + 3, xx + 14, yl + 8], fill=CAR_MOVED)
    d.text((xx + 18, yl), "car / moved", font=f9, fill=TXT)
    xx += 92
    for i in range(30):
        c = _dmg_color(10 ** (3 + 3 * i / 29))
        d.line([(xx + i, yl + 2), (xx + i, yl + 10)], fill=tuple(int(v) for v in c))
    d.text((xx + 34, yl), "bldg damage $1k-1M", font=f9, fill=TXT)
    return np.asarray(im)


# ----------------------------------------------------------------------------- per-frame
def _render(k):
    S = _S
    fk = float(S["fidx"][k])
    tk = float(S["t_out"][k])
    nf = S["nf"]
    i0 = int(math.floor(fk)); i1 = min(i0 + 1, nf - 1); a = fk - i0
    d8 = (S["depth"][i0].astype(np.float32) * (1 - a) + S["depth"][i1].astype(np.float32) * a) * 1e-3
    f = S["f"]; Hr, Wr = S["Hr"], S["Wr"]
    dR = d8 if f == 1 else np.repeat(np.repeat(d8, f, 0), f, 1)
    dR = dR[:Hr, :Wr]
    isb = S["isb"]
    # building damage now
    usd_b = S["usd_b"]
    if S["bfrac"] is not None:
        bf = S["bfrac"][i0] * (1 - a) + S["bfrac"][i1] * a
        bd = usd_b * bf
    else:
        bd = np.zeros_like(usd_b)
    rf = S["ramp_flood"][i0] * (1 - a) + S["ramp_flood"][i1] * a
    rw = S["ramp_wind"][i0] * (1 - a) + S["ramp_wind"][i1] * a
    if S["bfrac"] is not None:
        dmg_now = float(bd.sum()) + S["other_usd"] * 0.5 * (rf + rw)
    else:
        dmg_now = S["total_usd"] * (0.65 * rf + 0.35 * rw)
    bd_ext = np.r_[0.0, bd]  # index bid+1
    bcol = _dmg_color(bd_ext)
    bmask_lut = bd_ext > 500

    # ---------- top-down
    td = S["base_disp"].copy()
    dd = d8[S["py8"][:, None], S["px8"][None, :]]
    bdisp = S["bid_disp"]
    m = bmask_lut[bdisp + 1]
    if m.any():
        td[m] = td[m] * 0.3 + bcol[bdisp[m] + 1] * 0.7
    li = np.minimum((dd * ((_LUT_N - 1) / 1.5)).astype(np.int32), _LUT_N - 1)
    al = ALPHA_LUT[li]
    al[(bdisp >= 0) | S["bay_disp"]] = 0
    td = td * (1 - al[..., None]) + WATER_LUT[li] * al[..., None]

    tr = S["trees"]
    failed = tr["fail"] <= tk
    sx, sy = S["sx"], S["sy"]
    if len(tr["ix"]):
        live = ~failed
        px = np.clip((tr["ix"][live] * sx).astype(np.int64), 0, S["mw"] - 1)
        py = np.clip(((Hr - tr["iy"][live]) * sy).astype(np.int64), 0, S["mh"] - 1)
        td[py, px] = td[py, px] * 0.35 + TREE_LIVE * 0.65

    # ---------- oblique composite (source res)
    comp = S["base"].copy()
    mR = bmask_lut[S["bid"] + 1]
    if mR.any():
        comp[mR] = comp[mR] * 0.3 + bcol[S["bid"][mR] + 1] * 0.7
    liR = np.minimum((dR * ((_LUT_N - 1) / 1.5)).astype(np.int32), _LUT_N - 1)
    aR = ALPHA_LUT[liR]
    aR[isb | S["bay"]] = 0
    comp = comp * (1 - aR[..., None]) + WATER_LUT[liR] * aR[..., None]
    hgt = S["h_static"].copy()
    wm = (dR > WATER_MIN) & ~isb & ~S["bay"]
    hgt[wm] = ((S["zrel"][wm] + dR[wm] * WEX) * EX / S["dr"])
    if len(tr["ix"]):
        ti = np.clip(tr["iy"].astype(np.int64), 0, Hr - 1); tj = np.clip(tr["ix"].astype(np.int64), 0, Wr - 1)
        lv = ~failed
        comp[ti[lv], tj[lv]] = comp[ti[lv], tj[lv]] * 0.3 + TREE_LIVE * 0.7
        hgt[ti[lv], tj[lv]] += 4.0 / S["dr"]
    ob = S["sky"].copy()
    _raycast(comp.astype(np.float32), hgt.astype(np.float32), ob, S["s_cam"], S["D"], S["Hc"], S["horizon"], S["fogc"], 0.0)

    # ---------- assemble
    frame = np.empty((H_OUT, W_OUT, 3), np.uint8)
    frame[:] = BG
    frame[S["map_y0"]:S["map_y0"] + S["mh"], S["map_x0"]:S["map_x0"] + S["mw"]] = np.clip(td, 0, 255).astype(np.uint8)
    frame[PANEL_Y0:PANEL_Y0 + S["ob_h"], S["ob_x0"]:S["ob_x0"] + S["ob_w"]] = np.clip(ob, 0, 255).astype(np.uint8)
    frame[STRIP_Y0:STRIP_Y1] = S["strip"]
    im = Image.fromarray(frame)
    d = ImageDraw.Draw(im)
    mx0, my0 = S["map_x0"], S["map_y0"]
    ox0, oy0 = S["ob_x0"], PANEL_Y0

    def proj(ix, iy, h):
        zc = S["D"] + iy
        kk = S["s_cam"] * S["D"] / zc
        return ox0 + S["ob_w"] * 0.5 + (ix - Wr * 0.5) * kk, oy0 + S["horizon"] + (S["Hc"] - h) * kk

    # failed trees
    n_failed = int(failed.sum())
    if n_failed:
        idx = np.nonzero(failed)[0]
        for j in idx:
            x = mx0 + tr["ix"][j] * sx
            y = my0 + (Hr - tr["iy"][j]) * sy
            ang = math.radians(tr["dir"][j])
            L = max(3.0, tr["h"][j] / S["dr"] * sx)
            age = tk - tr["fail"][j]
            prog = min(1.0, age / 300.0)
            d.line([(x, y), (x + math.sin(ang) * L * prog, y - math.cos(ang) * L * prog)], fill=(255, 150, 60), width=1)
            r = 2.2
            d.line([(x - r, y - r), (x + r, y + r)], fill=TREE_DEAD, width=2)
            d.line([(x - r, y + r), (x + r, y - r)], fill=TREE_DEAD, width=2)
            if age < 600:
                rr = 4 + 9 * (1 - age / 600)
                d.ellipse([x - rr, y - rr, x + rr, y + rr], outline=(255, 90, 90), width=1)
            iyc = min(int(tr["iy"][j]), Hr - 1); ixc = min(int(tr["ix"][j]), Wr - 1)
            px_, py_ = proj(tr["ix"][j], tr["iy"][j], hgt[iyc, ixc])
            if ox0 <= px_ < ox0 + S["ob_w"] and oy0 <= py_ < oy0 + S["ob_h"]:
                d.line([(px_ - 2, py_ - 2), (px_ + 2, py_ + 2)], fill=TREE_DEAD, width=2)
                d.line([(px_ - 2, py_ + 2), (px_ + 2, py_ - 2)], fill=TREE_DEAD, width=2)

    # cars
    n_moved = 0
    cars = S["cars"]
    if cars is not None and cars.shape[1]:
        c = cars[i0] * (1 - a) + cars[i1] * a
        mv = S["car_moved"][i0] if a < 0.5 else S["car_moved"][i1]
        n_moved = int(mv.sum())
        cix = (c[:, 0] - S["X0"]) / S["dr"]; ciy = (c[:, 1] - S["Y0"]) / S["dr"]
        ok = (cix >= 0) & (cix < Wr) & (ciy >= 0) & (ciy < Hr)
        for j in np.nonzero(ok & ~mv)[0]:
            x = mx0 + cix[j] * sx; y = my0 + (Hr - ciy[j]) * sy
            d.rectangle([x - 1, y - 1, x + 1, y + 1], fill=CAR_OK)
        for j in np.nonzero(ok & mv)[0]:
            x = mx0 + cix[j] * sx; y = my0 + (Hr - ciy[j]) * sy
            d.rectangle([x - 2, y - 2, x + 2, y + 2], fill=CAR_MOVED, outline=(40, 20, 0))
            px_, py_ = proj(cix[j], ciy[j], hgt[min(int(ciy[j]), Hr - 1), min(int(cix[j]), Wr - 1)] + 0.5)
            if ox0 <= px_ < ox0 + S["ob_w"] and oy0 <= py_ < oy0 + S["ob_h"]:
                d.rectangle([px_ - 2, py_ - 2, px_ + 2, py_ + 1], fill=CAR_MOVED)
    deb = S["debris"]
    if deb is not None and deb.shape[1]:
        p = deb[i0] * (1 - a) + deb[i1] * a
        mvd = (np.hypot(p[:, 0] - deb[0, :, 0], p[:, 1] - deb[0, :, 1]) > 1.0) | (p[:, 2] - deb[0, :, 2] > 0.5)
        for j in np.nonzero(mvd)[0]:
            x = mx0 + (p[j, 0] - S["X0"]) / S["dr"] * sx; y = my0 + (Hr - (p[j, 1] - S["Y0"]) / S["dr"]) * sy
            if mx0 <= x < mx0 + S["mw"] and my0 <= y < my0 + S["mh"]:
                d.rectangle([x - 1, y - 1, x, y], fill=(235, 205, 150))

    _overlay(d, im, k, tk, i0, i1, a, d8, dmg_now, n_failed, n_moved)
    return np.asarray(im).tobytes()


_FONTS = None


def _overlay(d, im, k, tk, i0, i1, a, d8, dmg_now, n_failed, n_moved):
    global _FONTS
    if _FONTS is None:
        _FONTS = (_font(12), _font(17, True), _font(10), _font(15, True))
    S = _S
    t = S["t"]
    fT, fB, fS, fL = _FONTS
    # header
    d.rectangle([0, 0, W_OUT, HEADER_H], fill=(16, 20, 28))
    d.line([(0, HEADER_H), (W_OUT, HEADER_H)], fill=(44, 52, 66))
    d.text((MARGIN + 2, 6), "STORMSIM SF", font=fS, fill=ACCENT)
    lab = S["label"]
    while len(lab) > 8 and d.textlength(lab, font=fL) > 420:
        lab = lab[:-4] + "..."
    d.text((MARGIN + 2, 19), lab, font=fL, fill=TXT)
    sub = f"final damage {_money(S['total_usd'])}"
    if S["plan_cost"] is not None:
        sub = f"plan cost {_money(S['plan_cost'])}  |  " + sub
    d.text((MARGIN + 2, 39), sub, font=fT, fill=TXT_DIM)
    rain = S["rain"][i0] * (1 - a) + S["rain"][i1] * a
    gust = S["gust"][i0] * (1 - a) + S["gust"][i1] * a
    cell_km2 = (S["dx8"] ** 2) / 1e6
    dl = d8[S["land8"]]
    fl = float((dl > 0.15).sum()) * cell_km2
    maxd = float(dl.max()) if dl.size else 0.0
    tiles = [("SIM TIME", "T+" + _hhmm(tk - t[0]), TXT), ("RAIN", f"{rain:.0f} mm/h", RAIN_C),
             ("GUST", f"{gust:.0f} m/s", GUST_C), ("DAMAGE", _money(dmg_now), (255, 96, 80)),
             ("TREES DOWN", f"{n_failed}", TREE_DEAD), ("CARS MOVED", f"{n_moved}", CAR_MOVED),
             ("FLOODED >15cm", f"{fl:.2f} km\u00b2", TXT), ("MAX DEPTH", f"{maxd:.2f} m", TXT)]
    tmpl = ["T+00:00", "000 mm/h", "00 m/s", "$000.0M", "0000", "0000", "0.00 km\u00b2", "0.00 m"]
    widths = [max(d.textlength(v, font=fB), d.textlength(c, font=fS)) + 22 for (c, _, _), v in zip(tiles, tmpl)]
    x = W_OUT - MARGIN - sum(widths) + 14
    for (cap, val, col), w in zip(tiles, widths):
        d.line([(x - 9, 10), (x - 9, HEADER_H - 10)], fill=(44, 52, 66))
        d.text((x, 9), cap, font=fS, fill=TXT_DIM)
        d.text((x, 25), val, font=fB, fill=col)
        x += w
    # panel captions
    mx0, my0, mw, mh = S["map_x0"], S["map_y0"], S["mw"], S["mh"]
    _tag(d, (mx0 + 6, my0 + 6), "TOP-DOWN  |  north up", fT)
    _tag(d, (S["ob_x0"] + 6, PANEL_Y0 + 6), "OBLIQUE  |  from south, looking north (terrain x%g, buildings x%g)" % (EX, BEX), fT)
    # north arrow + scale bar
    nxp, nyp = mx0 + mw - 18, my0 + 12
    d.polygon([(nxp, nyp), (nxp - 6, nyp + 14), (nxp + 6, nyp + 14)], fill=TXT)
    d.text((nxp - 4, nyp + 15), "N", font=fS, fill=TXT)
    px_per_m = S["sx"] / S["dr"]
    L = 500 if mw / px_per_m > 1500 else 200
    bl = L * px_per_m
    by = my0 + mh - 14
    d.rectangle([mx0 + 8, by - 12, mx0 + 16 + bl, by + 6], fill=(10, 13, 19))
    d.line([(mx0 + 12, by), (mx0 + 12 + bl, by)], fill=TXT, width=2)
    d.text((mx0 + 14, by - 12), f"{L} m", font=fS, fill=TXT)
    # cursor in strip
    x0, x1 = MARGIN + 4, 860
    tt = (tk - t[0]) / max(t[-1] - t[0], 1)
    cx = x0 + tt * (x1 - x0)
    d.line([(cx, STRIP_Y0 + 2), (cx, STRIP_Y1 - 2)], fill=(255, 255, 255), width=2)
    if k >= S["n_play"]:
        msg = f"END OF STORM  |  total damage {_money(S['total_usd'])}  |  trees down {n_failed}  |  cars moved {n_moved}"
        tw = d.textlength(msg, font=fL)
        bx = (W_OUT - tw) / 2
        d.rounded_rectangle([bx - 14, PANEL_Y0 + 40, bx + tw + 14, PANEL_Y0 + 72], 8, fill=(120, 24, 24))
        d.text((bx, PANEL_Y0 + 47), msg, font=fL, fill=(255, 255, 255))


def _tag(d, xy, text, font):
    x, y = xy
    w = d.textlength(text, font=font)
    d.rounded_rectangle([x - 4, y - 3, x + w + 5, y + 15], 4, fill=(10, 13, 19))
    d.text((x, y - 1), text, font=font, fill=TXT)


# ----------------------------------------------------------------------------- driver
def _ffmpeg_exe():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def render_run(run_dir, out_path=None, grid_dir="data/grid", duration_s=24.0, fps=12, workers=16) -> str:
    t0 = time.time()
    run_dir = _resolve(run_dir)
    grid_dir = _resolve(grid_dir) if grid_dir else Path("/nonexistent")
    out_path = _resolve(out_path) if out_path else run_dir / "video.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n_out = _setup(run_dir, grid_dir, float(duration_s), int(fps))
    # compile raycaster before forking
    _raycast(np.zeros((2, 2, 3), np.float32), np.zeros((2, 2), np.float32), np.zeros((2, 2, 3), np.float32),
             1.0, 1.0, 1.0, 0.0, np.zeros(3, np.float32), 0.0)
    tmp = out_path.with_suffix(".tmp.mp4")
    cmd = [_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W_OUT}x{H_OUT}",
           "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", str(tmp)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    workers = max(1, min(int(workers), 16, os.cpu_count() or 1))
    try:
        if workers > 1:
            ctx = mp.get_context("fork")
            with ctx.Pool(workers) as pool:
                for buf in pool.imap(_render, range(n_out), chunksize=2):
                    proc.stdin.write(buf)
        else:
            for k in range(n_out):
                proc.stdin.write(_render(k))
    finally:
        proc.stdin.close()
        rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"ffmpeg failed ({rc})")
    os.replace(tmp, out_path)
    print(f"[render_video] {out_path} ({n_out} frames, {out_path.stat().st_size / 1e6:.1f} MB, {time.time() - t0:.1f}s)",
          file=sys.stderr)
    return str(out_path)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Render a StormSim run to MP4")
    ap.add_argument("run_dir")
    ap.add_argument("--out", default=None)
    ap.add_argument("--grid", default="data/grid")
    ap.add_argument("--duration", type=float, default=24.0)
    ap.add_argument("--fps", type=int, default=12)
    ap.add_argument("--workers", type=int, default=16)
    a = ap.parse_args(argv)
    print(render_run(a.run_dir, a.out, a.grid, a.duration, a.fps, a.workers))


if __name__ == "__main__":
    main()
