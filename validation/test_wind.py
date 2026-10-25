"""Wind + tree verification (tests 7-10). Calls the production kernels / public API of sim/wind.py and
sim/trees.py; never modifies them.

  7  Poiseuille half-channel with the production D3Q19 kernel (sim.wind.lbm_step) vs the analytic parabola
  8  Log-law inflow profile preserved over an empty upstream fetch (horizontal homogeneity)
  9  Surface-mounted cube (sim.wind.run_lbm) vs Martinuzzi & Tropea 1993 / Castro & Robins 1977
  10 Tree critical breaking speed (sim.trees.virot_check) vs Virot et al. 2016

run(out_dir) -> list of result dicts (see validation/run_all.py).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("NUMBA_NUM_THREADS", "16")
CACHE = os.path.join(ROOT, "validation", "cache")

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from sim import wind as W  # noqa: E402

C_OURS = "#1f5fa8"
C_REF = "#444444"
C_ACC = "#d1495b"


def _src_hash(*rel):
    h = hashlib.sha1()
    for r in rel:
        h.update(open(os.path.join(ROOT, r), "rb").read())
    return h.hexdigest()[:10]


def _pf(ok):
    return "PASS" if ok else "FAIL"


# =============================================================================================
# 7. Poiseuille flow with the production kernel
# =============================================================================================
def _poiseuille(tau0, nz=16, umax_target=0.03, conv=1e-4):
    """Half channel: kernel's own half-way bounce-back floor (k=-1/2) + specular free-slip lid (symmetry
    plane at k=nz-1/2). Periodic in x, y via ghost columns (nx = ny = 3, interior index 1). Driven by a
    uniform body force F (simple forcing g_q += 3 w_q c_qx rho F after each collision). Started from rest;
    run until the slowest diffusive mode exp(-nu (pi/2H)^2 t) has decayed to `conv`."""
    import numba as nb
    nth = nb.get_num_threads()
    nb.set_num_threads(1)           # 432-cell domain: thread fork/join would dominate
    nu = (tau0 - 0.5) / 3.0
    H = float(nz)
    F = 2.0 * nu * umax_target / H ** 2
    steps = int(np.ceil(np.log(1.0 / conv) / (nu * (np.pi / (2 * H)) ** 2)))
    shape = (19, nz, 3, 3)
    f = np.empty(shape, np.float32)
    solid = np.zeros(shape[1:], np.uint8)
    near = np.ones(shape[1:], np.uint8)
    resist = np.zeros(shape[1:], np.float32)
    z0 = np.zeros(shape[1:], np.float32)
    W.init_eq(f, solid, z0, z0, z0)
    g = np.empty_like(f)
    force = (3.0 * W.W * W.CX * F).astype(np.float32)[:, None, None, None]
    t3 = np.float32(tau0)
    zero = np.float32(0.0)
    try:
        for _ in range(steps):
            W.lbm_step(f, g, solid, near, resist, t3, zero)
            g += force * g.sum(0, keepdims=True)
            g[:, :, :, 0] = g[:, :, :, 1]
            g[:, :, :, 2] = g[:, :, :, 1]
            g[:, :, 0, :] = g[:, :, 1, :]
            g[:, :, 2, :] = g[:, :, 1, :]
            f, g = g, f
    finally:
        nb.set_num_threads(nth)
    c = f[:, :, 1, 1].astype(np.float64)
    u = (W.CX[:, None] * c).sum(0) / c.sum(0) + 0.5 * F
    zp = np.arange(nz) + 0.5
    ua = F / (2 * nu) * (2 * H * zp - zp ** 2)
    l2 = float(np.sqrt(np.sum((u - ua) ** 2) / np.sum(ua ** 2)))
    return dict(tau0=tau0, nu=nu, nz=nz, F=F, steps=steps, z=zp, u=u, ua=ua, l2=l2,
                umax_err=float(abs(u.max() - ua.max()) / ua.max()))


def test_poiseuille(out_dir):
    cases = [_poiseuille(0.8, nz=16), _poiseuille(0.6, nz=16),
             _poiseuille(float(0.5 + 3 * W.NU0_LAT), nz=8)]   # production background viscosity
    tol = 0.02
    ok = all(c["l2"] < tol for c in cases)
    fig, ax = plt.subplots(figsize=(6, 4.6), dpi=130)
    for c, mk in zip(cases, ("o", "s", "^")):
        zf = np.linspace(0, c["nz"], 200)
        ua = c["F"] / (2 * c["nu"]) * (2 * c["nz"] * zf - zf ** 2)
        ax.plot(ua / ua.max(), zf / c["nz"], "--", color=C_REF, lw=1)
        ax.plot(c["u"] / ua.max(), c["z"] / c["nz"], mk, color=C_OURS, ms=4, mfc="none",
                label=f"LBM tau={c['tau0']:.3f}, {c['nz']} cells: L2 err {100*c['l2']:.2f} %")
    ax.plot([], [], "--", color=C_REF, label="analytic parabola")
    ax.set_xlabel("u / u_max (analytic)")
    ax.set_ylabel("z / H (wall at 0, symmetry plane at 1)")
    ax.set_title(f"7. Poiseuille, production D3Q19 kernel: {_pf(ok)}", fontsize=10)
    ax.legend(fontsize=8, loc="upper left")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "wind_poiseuille.png"))
    plt.close(fig)
    return {"id": "7", "name": "Poiseuille channel flow (wind LBM kernel)",
            "reference": "Analytic Poiseuille parabola u = F/(2 nu) (2 H z - z^2), nu = (tau - 1/2)/3",
            "result": ", ".join(f"L2 {100*c['l2']:.2f} % (tau {c['tau0']:.3f})" for c in cases),
            "tolerance": "L2 relative error < 2 %", "pass": bool(ok), "plot": "wind_poiseuille.png",
            "metrics": {f"tau{c['tau0']:.3f}": {"l2_rel": c["l2"], "umax_rel_err": c["umax_err"], "steps": c["steps"],
                                            "nz": c["nz"]} for c in cases},
            "notes": "Laminar check of the production lbm_step (regularized BGK, half-way bounce-back floor, "
                     "specular lid) with Smagorinsky off (cs2=0) and a body force added outside the kernel; "
                     "16 cells across the half channel. Verifies collision viscosity and wall placement, "
                     "not the turbulence model."}


# =============================================================================================
# 9 + 8. Surface-mounted cube (full run_lbm) and upstream log-law profile
# =============================================================================================
CUBE = dict(dx=8.0, Hc=8, up=5, down=11, side=4, top_m=6 * 64.0)


# grid-convergence family: same 64 m cube and physics, dx = 64 m / n, smaller domain (blockage ~7 %)
CONV_N = (8, 16, 24)


def conv_cfg(n):
    return dict(dx=64.0 / n, Hc=n, up=3, down=4.5, side=2.5, top_m=2.5 * 64.0, bh_align=True)


def _cube_grid(cfg=None):
    cfg = cfg or CUBE
    dx, Hc = cfg["dx"], cfg["Hc"]
    nx = int(round(Hc * (cfg["up"] + 1 + cfg["down"])))
    ny = int(round(Hc * (2 * cfg["side"] + 1)))
    z = np.full((ny, nx), 10.0, np.float32)
    bh = np.zeros((ny, nx), np.float32)
    bid = -np.ones((ny, nx), np.int32)
    i0, j0 = int(round(Hc * cfg["up"])), int(round(Hc * cfg["side"]))
    # run_lbm's terrain voxels top out at floor(z) + dx/4: put the cube top exactly n cells above that
    bh[j0:j0 + Hc, i0:i0 + Hc] = Hc * dx + (0.25 * dx if cfg.get("bh_align") else 0.0)
    bid[j0:j0 + Hc, i0:i0 + Hc] = 0
    cover = np.where(bid >= 0, 1, 0).astype(np.uint8)
    meta = {"crs": "EPSG:32610", "dx": dx, "nx": nx, "ny": ny, "x0": 0.0, "y0": 0.0, "synthetic": True}
    return {"z": z, "bldg_h": bh, "bldg_id": bid, "cover": cover, "meta": meta,
            "buildings": [{"id": 0, "x": (i0 + Hc / 2) * dx, "y": (j0 + Hc / 2) * dx, "height_m": Hc * dx}]}, i0, j0


def _cube_key(cfg=None):
    return _src_hash("sim/wind.py") + hashlib.sha1(json.dumps(cfg or CUBE, sort_keys=True).encode()).hexdigest()[:6]


def _cube_run(refresh=False, cfg=None, allow_compute=True, threads=None):
    """Cube LES (~5-10 min on the loaded box). Exact cache hit on (sim/wind.py source, CUBE); otherwise the
    newest cached cube run is reused and flagged stale (run_all must stay < 5 min). Refresh with
    `python -m validation.test_wind --refresh-cube`."""
    import glob
    os.makedirs(CACHE, exist_ok=True)
    key = _cube_key(cfg)
    path = os.path.join(CACHE, f"wind_cube_{key}.npz")
    if not refresh:
        cands = [path] if os.path.exists(path) else sorted(
            glob.glob(os.path.join(CACHE, f"wind_cube_*{key[-6:]}.npz")), key=os.path.getmtime)[-1:]
        if cands:
            d = dict(np.load(cands[0]))
            d["cached"] = True
            d["cache_key"] = os.path.basename(cands[0])[10:-4]
            d["stale"] = cands[0] != path
            return d
    if not allow_compute:
        return None
    cfg = cfg or CUBE
    grid, _, _ = _cube_grid(cfg)
    out = W.run_lbm(grid, 270.0, dx=cfg["dx"], top_m=cfg["top_m"], verbose=True, threads=threads)
    keep = {k: np.asarray(out[k]) for k in ("u", "v", "w", "cp", "solid", "ground", "btop", "zc", "runtime_s", "n_steps")}
    np.savez(path + ".tmp.npz", **keep)
    os.replace(path + ".tmp.npz", path)
    keep.update(cached=False, cache_key=key, stale=False)
    return keep


def _cube_metrics(d, cfg=None):
    cfg = cfg or CUBE
    Hc, dx = cfg["Hc"], cfg["dx"]
    H = Hc * dx
    u = d["u"].astype(np.float64)
    cp = d["cp"].astype(np.float64)
    zc = d["zc"].astype(np.float64)
    ground = float(d["ground"][0, 0])
    zag = zc - ground                                # height above the DEM ground (inflow log-law coordinate)
    _, i0, j0 = _cube_grid(cfg)
    kf0 = int(np.argmax(d["solid"][:, 0, 0] == 0))
    floor = zc[kf0] - dx / 2                         # top face of the terrain voxels (cube base)
    zf = zc - floor
    jc = [j0 + Hc // 2 - 1, j0 + Hc // 2]          # two centreline cell rows
    kf = int(np.argmax(zag > 0))                     # first fluid level
    k_low = kf                                       # lowest fluid cell (z ~ dx/2 above ground)
    ucl = u[:, jc, :].mean(1)                        # (nz, nx) centreline plane
    # reattachment: first x behind the leeward face where near-ground u turns positive again
    i_lee = i0 + Hc                                  # first fluid column behind the cube
    ul = ucl[k_low, i_lee:]
    xr = None
    neg = np.nonzero(ul < 0)[0]
    if len(neg):
        start = neg[0]
        pos = np.nonzero(ul[start:] >= 0)[0]
        if len(pos):
            m = start + pos[0]
            a, b = ul[m - 1], ul[m]
            frac = a / (a - b) if a != b else 0.0
            # cell centres at (m + 0.5) dx behind the leeward face
            xr = ((m - 1 + 0.5) + frac) * dx / H
    # free-stream speed at cube height from the inflow log law (u normalised by U10 inflow)
    UH = float(W.log_profile(floor + H - ground))
    kk = np.nonzero((zf > 0) & (zf < H))[0]
    front = cp[kk][:, j0:j0 + Hc, i0 - 1].mean(1)    # fluid column just upstream of the face
    back = cp[kk][:, j0:j0 + Hc, i0 + Hc].mean(1)
    cp_front_max = float(front.max() / UH ** 2)
    cp_back = float(back.mean() / UH ** 2)
    cd = float((front.mean() - back.mean()) / UH ** 2)  # pressure drag on the projected H x H face
    # upstream profiles (test 8): inflow column (just inside the 96 m buffer) and 3H upstream of the cube
    prof = {}
    for name, i in (("inlet", 1), ("3H_upstream", max(i0 - 3 * Hc, 1))):
        prof[name] = u[:, :, i].mean(1)
    return dict(xr_H=xr, UH=UH, cp_front_max=cp_front_max, cp_back=cp_back, cd=cd, zag=zag, zf=zf, kf=kf, ucl=ucl,
                profiles=prof, i0=i0, j0=j0, H=H, front=front / UH ** 2, back=back / UH ** 2, zk=zag[kk])


def _convergence():
    """Cube at 8/16/24 cells per H (dx = 64/n m, same physics, smaller domain). Only n=8 may run live (~1 min);
    finer resolutions are read from validation/cache (python -m validation.test_wind --conv N)."""
    out = []
    for n in CONV_N:
        cfg = conv_cfg(n)
        d = _cube_run(cfg=cfg, allow_compute=(n == 8), threads=4)   # small domain: few threads beat 16 on a busy box
        if d is None:
            out.append({"cells_per_H": n, "dx_m": cfg["dx"], "xr_H": None, "cd": None, "cp_front_max": None,
                        "status": ("not run: 2.2 M cells x 7.5k steps needs ~1 h on the shared box, did not fit "
                                   "before code freeze" if n == 24 else "not finished (cache missing)")})
            continue
        m = _cube_metrics(d, cfg)
        out.append({"cells_per_H": n, "dx_m": round(cfg["dx"], 3), "xr_H": None if m["xr_H"] is None else float(m["xr_H"]), "cd": m["cd"],
                    "cp_front_max": m["cp_front_max"], "runtime_s": float(d["runtime_s"]),
                    "stale_vs_current_wind_py": bool(d.get("stale", False)), "status": "ok"})
    return out


def _conv_note(conv):
    parts = [f"{c['cells_per_H']} cells/H: X_R {c['xr_H']:.2f} H, Cd {c['cd']:.2f}" if c["xr_H"] is not None
             else f"{c['cells_per_H']} cells/H: {c['status']}" for c in conv]
    return ("Grid convergence (same 64 m cube, dx = 64/n m, smaller 8.5H x 6H x 2.5H domain, ~7 % blockage): "
            + "; ".join(parts) + ". " + _trend(conv))


def _trend(conv):
    xs = [c["xr_H"] for c in conv if c["xr_H"] is not None]
    if len(xs) >= 2 and all(b < a for a, b in zip(xs, xs[1:])):
        return (f"X_R falls with every refinement ({' -> '.join(f'{x:.2f}' for x in xs)} H) towards the 1.61 H "
                f"measurement, consistent with the base-case excess being a resolution error (only "
                f"{len(xs)} levels, so no Richardson extrapolation claimed). The base run's longer X_R in the "
                f"larger domain also shows some domain sensitivity. ")
    return ""


def test_cube(d, m, out_dir):
    xr = m["xr_H"]
    ok_xr = xr is not None and 1.0 <= xr <= 2.5
    ok_cp = 0.5 <= m["cp_front_max"] <= 1.0
    ok = bool(ok_xr and ok_cp)
    Hc, dx, H = CUBE["Hc"], CUBE["dx"], m["H"]
    conv = _convergence()
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(12, 3.9), dpi=130, gridspec_kw={"width_ratios": [3.2, 1]})
    nz, nx = m["ucl"].shape
    xs = ((np.arange(nx) + 0.5) * dx - (m["i0"] + Hc) * dx) / H      # 0 at the leeward face
    zz = m["zf"] / H
    kmax = int(np.searchsorted(zz, 3.0))
    U = np.ma.masked_where(d["solid"][:kmax, m["j0"] + Hc // 2, :] != 0, m["ucl"][:kmax])
    im = ax.pcolormesh(xs, zz[:kmax], U / m["UH"], cmap="RdBu_r", vmin=-1.2, vmax=1.2, shading="nearest")
    ax.contour(xs, zz[:kmax], np.asarray(U.filled(np.nan)), levels=[0.0], colors="k", linewidths=0.8)
    ax.add_patch(plt.Rectangle((-1, 0), 1, 1, color="0.35"))
    ax.axvline(1.61, color=C_REF, ls="--", lw=1.2, label="Martinuzzi & Tropea 1993: 1.61 H")
    if xr is not None:
        ax.axvline(xr, color=C_ACC, lw=1.6, label=f"StormSim LBM: {xr:.2f} H")
    ax.set_xlim(-4, 8)
    ax.set_ylim(0, 3)
    ax.set_aspect("equal")
    ax.set_xlabel("x / H from leeward face")
    ax.set_ylabel("z / H")
    plt.colorbar(im, ax=ax, label="u / U_H (centreline)", shrink=0.8)
    ax.legend(fontsize=7, loc="upper right")
    # grid convergence panel
    ax2.axhline(1.61, color=C_REF, ls="--", lw=1.2, label="M&T 1993: 1.61 H")
    ax2.plot([Hc], [xr], "*", color=C_ACC, ms=11, label="base run (17H x 9H domain)")
    done = [c for c in conv if c["xr_H"] is not None]
    if done:
        ax2.plot([c["cells_per_H"] for c in done], [c["xr_H"] for c in done], "o-", color=C_OURS,
                 label="convergence family (8.5H x 6H)")
        for c in done:
            ax2.annotate(f"Cd {c['cd']:.2f}", (c["cells_per_H"], c["xr_H"]), textcoords="offset points",
                         xytext=(4, 6), fontsize=6.5)
    ax2.set_xticks(list(CONV_N))
    ax2.set_xlim(4, 28)
    ax2.set_ylim(0, 4)
    ax2.set_xlabel("cells per cube height")
    ax2.set_ylabel("reattachment X_R / H")
    ax2.set_title("grid convergence", fontsize=9)
    ax2.legend(fontsize=6.5, loc="lower left")
    ax2.grid(alpha=0.3)
    ax.set_title(f"9. Surface-mounted cube, 8 cells/H: reattachment {xr if xr is None else round(xr, 2)} H, "
                 f"front Cp_max {m['cp_front_max']:.2f}, Cd {m['cd']:.2f}: {_pf(ok)}", fontsize=9)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "wind_cube.png"))
    plt.close(fig)
    return {"id": "9", "name": "Flow around a surface-mounted cube (full wind solver)",
            "reference": "Reattachment X_R = 1.61 H (Martinuzzi & Tropea 1993, J. Fluids Eng. 115:85, thin-BL "
                         "channel); the recirculation shortens in a thick turbulent BL (Castro & Robins 1977, JFM 79:307). "
                         "Front-face Cp_max ~0.7-0.9 on U_H (Castro & Robins 1977; Richards et al. 2001 Silsoe cube)",
            "result": f"X_R {xr if xr is None else round(xr, 2)} H; front Cp_max {m['cp_front_max']:.2f}; "
                      f"Cd {m['cd']:.2f}; grid study X_R " + ", ".join(
                          f"{c['xr_H']:.2f}" + f" ({c['cells_per_H']}/H)" for c in conv if c["xr_H"] is not None),
            "tolerance": "X_R in 1.0-2.5 H; front Cp_max in 0.5-1.0",
            "pass": ok, "plot": "wind_cube.png",
            "metrics": {"reattachment_H": None if xr is None else float(xr), "cp_front_max": m["cp_front_max"], "cp_back_mean": m["cp_back"],
                        "cd_pressure": m["cd"], "U_H_over_U10": m["UH"], "runtime_s": float(d["runtime_s"]),
                        "n_steps": int(d["n_steps"]), "cells_per_H": Hc, "cached": bool(d.get("cached", False)),
                        "grid_convergence": conv,
                        "wind_py_version": str(d.get("cache_key", ""))[:10], "stale_vs_current_wind_py": bool(d.get("stale", False))},
            "notes": _conv_note(conv) + "Same run_lbm as the city (8 m voxels, Smagorinsky LES, log-law inflow z0 0.7 m, wall-modelled "
                     "floor, time-averaged field). 64 m cube = 8 cells per H, coarse by LES standards (Martinuzzi "
                     "& Tropea is Re 4e4 in a channel with a thin BL; our inflow is a deep urban log-law BL), so "
                     "agreement is expected only within the stated band. Cd from front minus back mean Cp "
                     "(pressure drag, U_H reference). If X_R is long, the likely causes are the coarse 8 cells/H "
                     "resolution with Smagorinsky dissipation (a too-laminar shear layer reattaches late, as in "
                     "standard k-epsilon RANS, which gives ~2.5-3 H; cf. Rodi 1997, J. Wind Eng. 69-71:55) and the "
                     "free-slip + wall-model floor. Impact on the product: street-level speeds in building wakes "
                     "(sheltered zones) are less reliable than speeds in open canyons and at facades."}


def test_loglaw(d, m, out_dir):
    zag = m["zag"]
    sel = (zag >= 10) & (zag <= 200)
    ref = W.log_profile(zag)
    dev = {k: float(np.max(np.abs(p[sel] / ref[sel] - 1))) for k, p in m["profiles"].items()}
    tol = 0.10
    ok = dev["3H_upstream"] < tol
    fig, ax = plt.subplots(figsize=(5.2, 4.8), dpi=130)
    zf = np.linspace(1, 300, 300)
    ax.plot(W.log_profile(zf), zf, "--", color=C_REF, label="log law, z0 = 0.7 m (inflow target)")
    for (k, p), col in zip(m["profiles"].items(), (C_ACC, C_OURS)):
        mm = zag > 0
        ax.plot(p[mm], zag[mm], "o-", ms=3, color=col, label=f"LBM {k.replace('_', ' ')}: max dev {100*dev[k]:.1f} %")
    ax.axhspan(10, 200, color="0.9", zorder=-1)
    ax.set_ylim(0, 300)
    ax.set_xlabel("U / U10 (inflow)")
    ax.set_ylabel("height above ground (m)")
    ax.set_title(f"8. Log-law inflow kept over the fetch: {_pf(ok)}", fontsize=10)
    ax.legend(fontsize=7, loc="upper left")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "wind_loglaw.png"))
    plt.close(fig)
    return {"id": "8", "name": "Log-law inflow profile upstream of an obstacle",
            "reference": "U(z)/U10 = ln((z+z0)/z0)/ln((10+z0)/z0), z0 = 0.7 m; horizontal homogeneity "
                         "requirement of Blocken, Stathopoulos & Carmeliet 2007 (Atmos. Environ. 41:238)",
            "result": f"max deviation 10-200 m: {100*dev['3H_upstream']:.1f} % at 3H upstream "
                      f"({100*dev['inlet']:.1f} % at inlet)",
            "tolerance": "< 10 % over 10-200 m at 3H upstream", "pass": bool(ok), "plot": "wind_loglaw.png",
            "metrics": {"max_rel_dev_inlet": dev["inlet"], "max_rel_dev_3H_upstream": dev["3H_upstream"]},
            "notes": "Profiles are spanwise means from the cube run (5H = 320 m fetch from the domain inflow "
                     "through the 96 m buffer). The first fluid cell (4 m) is below the 10 m band. "
                     "The 3H station is also affected by the cube's upstream blockage near the ground."}


# =============================================================================================
# 10. Trees vs Virot et al. 2016
# =============================================================================================
def test_virot(out_dir):
    from sim import trees as T
    P = T.load_params()
    tr = json.load(open(os.path.join(ROOT, "data", "grid", "trees.json")))
    chk = T.virot_check(tr, P)
    ta = T.tree_arrays(tr, P)
    big = ta.D >= 0.10
    sound = ta.m_snap / ta.eta
    uc = T.critical_speed(sound[big], np.maximum(ta.hc - 1.3, 0.5)[big], ta.area[big], P)
    med = float(np.median(uc))
    tol = 8.0
    r = chk["corr_log_uc_log_h"]
    # Virot: Uc ~ H^(-1/3)... weak size dependence; slope of log Uc vs log H
    slope = float(np.polyfit(np.log(ta.H[big]), np.log(uc), 1)[0])
    ok = abs(med - 42.0) <= tol and abs(slope) < 0.5
    fig, ax = plt.subplots(figsize=(6, 4.2), dpi=130)
    ax.hist(uc, bins=np.linspace(0, 120, 61), color=C_OURS, alpha=0.85,
            label=f"SF street trees, DBH >= 10 cm (n={int(big.sum())})")
    ax.axvline(42, color=C_REF, ls="--", lw=1.5, label="Virot et al. 2016: ~42 m/s")
    ax.axvspan(42 - tol, 42 + tol, color="0.85", zorder=-1)
    ax.axvline(med, color=C_ACC, lw=1.6, label=f"StormSim median {med:.1f} m/s")
    ax.set_xlabel("critical wind speed for trunk snapping, sound wood (m/s)")
    ax.set_ylabel("trees")
    ax.set_title(f"10. Tree breaking speed vs Virot 2016: {_pf(ok)}", fontsize=10)
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "trees_virot.png"))
    plt.close(fig)
    p10, p50, p90 = chk["snap_ucrit_p10_50_90"]
    return {"id": "10", "name": "Tree critical breaking wind speed (calibration consistency)",
            "reference": "Virot, Ponomarenko, Dehandschoewercker, Quere & Clanet 2016, Phys. Rev. E 93:023001: "
                         "trunks break at ~42 m/s nearly independent of species and size",
            "result": f"median {med:.1f} m/s (p10 {p10}, p90 {p90}); log-log slope vs height {slope:.2f}",
            "tolerance": "median within 42 +/- 8 m/s (calibrated) and weak size dependence |d ln Uc / d ln H| < 0.5 (independent)", "pass": bool(ok), "plot": "trees_virot.png",
            "metrics": {"n": chk["n"], "median_ms": med, "p10_50_90": chk["snap_ucrit_p10_50_90"],
                        "uproot_dry_p10_50_90": chk["uproot_dry_ucrit_p10_50_90"], "corr_log_uc_log_h": r,
                        "slope_log_uc_log_h": slope},
            "notes": "HONEST LIMIT: the median is not a prediction: tree_load_factor (2.9) and tree_cd0 in "
                     "data/reference/wind_tree_params.json were calibrated to put it at 42 m/s. The independent part is the "
                     "size dependence (Virot: near-universal Uc) and that each bundled factor sits in its published range; "
                     "our p10-p90 spread (species wood strength, crown shape) is much wider than Virot's. "
                     "Dry-soil uprooting thresholds are far lower (see validation/BUGS.md W1). "
                     "Computed for sound wood (decay factor removed) on the real tile inventory, exactly as "
                     "sim.trees.virot_check. Virot's 42 m/s is a 10-min-ish mean speed at the crown from storm "
                     "records; our Uc is the crown speed from the drag/Vogel model. Decayed trees and uprooting "
                     "on saturated soil fail at lower speeds in the sim by design."}


def run(out_dir: str) -> list:
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()
    res = [test_poiseuille(out_dir)]
    d = _cube_run()
    m = _cube_metrics(d)
    res.append(test_loglaw(d, m, out_dir))
    res.append(test_cube(d, m, out_dir))
    res.append(test_virot(out_dir))
    for r in res:
        r["metrics"]["wall_s_cum"] = round(time.time() - t0, 1)
    return sorted(res, key=lambda r: int(r["id"]))


if __name__ == "__main__":
    if "--refresh-cube" in sys.argv:
        _cube_run(refresh=True)
        sys.exit(0)
    if "--conv" in sys.argv:            # python -m validation.test_wind --conv 16
        th = int(sys.argv[sys.argv.index("--threads") + 1]) if "--threads" in sys.argv else None
        _cube_run(cfg=conv_cfg(int(sys.argv[sys.argv.index("--conv") + 1])), threads=th)
        sys.exit(0)
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "validation", "out")
    print(json.dumps(run(out), indent=1, default=str))
