"""Water solver verification against analytic solutions (tests 1, 3, 4, 5).

All tests run the PRODUCTION numba kernels of sim/water.py through validation/swe_driver.py.
"""
from __future__ import annotations

import json
import os
import time

import numpy as np
from scipy.optimize import brentq

from validation.swe_driver import SWE
from sim import water as Wm

G = 9.81
REF_C = "#555555"
SIM_C = "#2b6cb0"
BAD_C = "#b91c1c"


def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False})
    return plt


def _verdict(ok):
    return "PASS" if ok else "FAIL"


# --------------------------------------------------------------------------------------------- 1
def test_lake_at_rest(out_dir):
    rng = np.random.default_rng(1)
    ny, nx, dx = 120, 120, 4.0
    yy, xx = np.mgrid[0:ny, 0:nx] * dx
    z = 1.0 + 0.8 * np.sin(xx / 37.0) * np.cos(yy / 23.0) + 0.3 * rng.standard_normal((ny, nx))
    z[50:60, 50:60] += 3.0                      # emergent island: wet/dry fronts
    solid = np.zeros((ny, nx), bool)
    solid[80:90, 20:35] = True                  # a building
    eta0 = 2.0
    h0 = np.maximum(eta0 - z, 0.0)
    h0[solid] = 0
    s = SWE(z, dx, n=0.015, h0=h0, solid=solid)
    v0 = s.volume()
    s.run_to(3600.0)
    u, v = s.velocity()
    sp = np.hypot(u, v)
    wet = (s.h > 0.01) & ~solid
    deta = float(np.abs((s.h + z)[wet] - eta0).max())
    umax = float(sp.max())
    # 1b: the same physics must also be STABLE: a 1 mm bump in a 0.8 m flat pool has to decay (2D)
    zp = np.full((21, 40), 0.2)
    zp[:, :2] = 3
    zp[:, -2:] = 3
    hp = np.maximum(1.0 - zp, 0)
    hp[10, 20] += 1e-3
    sp_ = SWE(zp, dx, n=0.03, h0=hp)
    rng_t = []
    for _ in range(4):
        sp_.run_to(sp_.t + 300.0)
        e = (sp_.h + zp)[sp_.h > 0.01]
        rng_t.append(float(e.max() - e.min()))
    stable = max(rng_t) < 2e-3
    ok = umax < 1e-3 and deta < 1e-3 and stable
    # same pool with alpha 0.6 for contrast (shows the fix for BUGS.md B1)
    alt = []
    if sp_.p.alpha > 0.6:
        pa = Wm.default_params()
        pa.alpha = 0.6
        sa = SWE(zp, dx, n=0.03, h0=hp, params=pa)
        for _ in range(4):
            sa.run_to(sa.t + 300.0)
            e = (sa.h + zp)[sa.h > 0.01]
            alt.append(float(e.max() - e.min()))
    hll_rng, hll_lake = [], None
    if _has_hll():
        sh = SWE(zp, dx, n=0.03, h0=hp, params=_params("hll"))
        for _ in range(4):
            sh.run_to(sh.t + 300.0)
            e = (sh.h + zp)[sh.h > 0.01]
            hll_rng.append(float(e.max() - e.min()))
        sl = SWE(z, dx, n=0.015, h0=h0, solid=solid, params=_params("hll"))
        sl.run_to(1800.0)
        ul, vl = sl.velocity()
        hll_lake = float(np.hypot(ul, vl).max())
    plt = _plt()
    fig, ax = plt.subplots(1, 3, figsize=(12.5, 3.6), dpi=130, gridspec_kw={"width_ratios": [1.3, 1, 1]})
    xs = xx[55]
    ax[0].fill_between(xs, z.min() - 0.2, z[55], color="#b7a27a", label="bumpy bed + island")
    ax[0].plot(xs, np.where(s.h[55] > 0, z[55] + s.h[55], np.nan), color=SIM_C, lw=2, label="water surface after 1 h")
    ax[0].axhline(eta0, color=REF_C, ls="--", lw=1, label="exact: flat at 2.0 m")
    ax[0].set_xlabel("x, m")
    ax[0].set_ylabel("elevation, m")
    ax[0].legend(fontsize=7, loc="lower left")
    ax[0].set_title(f"1a: max |u| {umax:.1e} m/s, max |Δη| {deta:.1e} m", fontsize=9)
    im = ax[1].imshow(np.where(wet, sp, np.nan), origin="lower", cmap="viridis", vmin=0, vmax=max(umax, 1e-9))
    plt.colorbar(im, ax=ax[1], label="|u| m/s")
    ax[1].set_title("1a: speed after 1 h (machine zero)", fontsize=9)
    tt = [5, 10, 15, 20]
    ax[2].semilogy(tt, np.maximum(rng_t, 1e-12), "-o", color=BAD_C if not stable else SIM_C, label=f"production alpha {sp_.p.alpha}")
    if alt:
        ax[2].semilogy(tt, np.maximum(alt, 1e-12), "-s", color="#16a34a", label="alpha 0.6 (proposed fix)")
    if hll_rng:
        ax[2].semilogy(tt, np.maximum(hll_rng, 1e-12), "-^", color="#d97706", label="HLL full-SWE option")
    ax[2].axhline(1e-3, color=REF_C, ls="--", lw=1, label="initial 1 mm bump")
    ax[2].set_xlabel("minutes")
    ax[2].set_ylabel("surface range in pool, m")
    ax[2].set_title("1b: 1 mm bump in a 0.8 m pool must decay", fontsize=9)
    ax[2].legend(fontsize=7)
    fig.suptitle(f"Test 1 lake at rest (well-balanced) and stability: {_verdict(ok)}"
                 + ("" if stable else " (1b unstable at alpha 0.7: validation/BUGS.md B1)"), fontsize=10)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "t01_lake_at_rest.png"))
    plt.close(fig)
    return {"id": "1", "name": "Lake at rest over bumpy terrain + island + building (well-balanced)",
            "reference": "exact: u = 0, η = const (Audusse et al. 2004 well-balanced property)",
            "result": f"max |u| {umax:.1e} m/s, max |Δη| {deta:.1e} m after 1 h; 1b perturbed pool surface range "
                      f"{max(rng_t):.1e} m (alpha {sp_.p.alpha})"
                      + (f"; HLL option: lake max |u| {hll_lake:.1e} m/s, pool range {max(hll_rng):.1e} m" if hll_rng else ""),
            "tolerance": "|u| < 1e-3 m/s, |Δη| < 1 mm; 1b: 1 mm bump stays < 2 mm", "pass": bool(ok),
            "plot": "t01_lake_at_rest.png",
            "metrics": {"max_speed_ms": umax, "max_deta_m": deta, "volume_drift_m3": s.volume() - v0,
                        "steps": s.nsteps, "perturbed_pool_range_m_every_300s": rng_t, "alpha": sp_.p.alpha,
                        "same_with_alpha_0.6": alt, "hll_pool_range_m": hll_rng, "hll_lake_max_speed_ms": hll_lake},
            "notes": "Wet/dry fronts around the island and a solid building included. 1b exposes a CFL instability "
                     "at alpha = 0.7 (validation/BUGS.md B1); alpha <= 0.6 is stable."}


# --------------------------------------------------------------------------------------------- 3
def stoker(x, t, hl, hr):
    cl = np.sqrt(G * hl)
    xi = x / t
    if hr <= 0:
        return np.where(xi < -cl, hl, np.where(xi < 2 * cl, (2 * cl - xi) ** 2 / (9 * G), 0.0)), None
    f = lambda hm: 2 * (cl - np.sqrt(G * hm)) - (hm - hr) * np.sqrt(G / 2 * (hm + hr) / (hm * hr))
    hm = brentq(f, hr, hl)
    cm = np.sqrt(G * hm)
    um = 2 * (cl - cm)
    S = um * hm / (hm - hr)
    h = np.where(xi < -cl, hl, np.where(xi < um - cm, (2 * cl - xi) ** 2 / (9 * G), np.where(xi < S, hm, hr)))
    return h, {"h_m": hm, "u_m": um, "shock_speed": S, "Fr_m": um / cm}


def _has_hll():
    return "scheme" in Wm.WaterParams.__dataclass_fields__ and hasattr(Wm, "_hll_flux")


def _params(scheme):
    p = Wm.default_params()
    if scheme != "inertial":
        p.scheme = scheme
    return p


def _dam_case(scheme, hr, dx=4.0, T=40.0, hl=1.0):
    nx = int(1200 / dx)
    x = (np.arange(nx) + 0.5) * dx - 600
    h0 = np.repeat(np.where(x < 0, hl, hr)[None, :], 3, 0)
    s = SWE(np.zeros((3, nx)), dx, n=0.0, h0=h0, params=_params(scheme))
    s.run_to(T)
    ref, info = stoker(x, T, hl, hr)
    sim = s.h[1]
    reg = (x > -np.sqrt(G * hl) * T - 20) & (x < 2 * np.sqrt(G * hl) * T + 20)
    err = float(np.abs(sim - ref)[reg].mean() / hl)
    thr = (hr + 0.5 * (info["h_m"] - hr)) if info else 0.01
    xf_sim = float(x[np.nonzero(sim > thr)[0].max()])
    # dry bed: compare where the analytic Ritter profile itself is 1 cm deep (its tip is 0 depth)
    xf_ref = float(info["shock_speed"] * T) if info else float((2 * np.sqrt(G * hl) - 3 * np.sqrt(G * 0.01)) * T)
    return {"scheme": scheme, "hr": hr, "x": x, "sim": sim, "ref": ref, "mean_abs_err_over_h1": err,
            "front_sim_m": xf_sim, "front_ref_m": xf_ref, "front_rel_err": (xf_sim - xf_ref) / xf_ref,
            "Fr_middle": (info or {}).get("Fr_m", 2.0), "steps": s.nsteps}


def test_dam_break(out_dir):
    """Both water schemes side by side: local-inertial (production default) and full-SWE HLL."""
    T, hl = 40.0, 1.0
    schemes = ["inertial"] + (["hll"] if _has_hll() else [])
    cases = {sc: [_dam_case(sc, hr) for hr in (0.5, 0.1, 0.0)] for sc in schemes}
    conv = {}
    for sc in schemes:   # grid refinement, subcritical case
        conv[sc] = [{"dx_m": d, "mean_abs_err_over_h1": _dam_case(sc, 0.5, dx=d)["mean_abs_err_over_h1"]}
                    for d in (4.0, 2.0, 1.0)]
    crit = lambda c: c["mean_abs_err_over_h1"] < 0.05 and abs(c["front_rel_err"]) < 0.10
    verdict = {sc: crit(cases[sc][0]) for sc in schemes}
    verdict_all = {sc: all(crit(c) for c in cases[sc]) for sc in schemes}
    prod = Wm.default_params()
    prod_scheme = str(getattr(prod, "scheme", "inertial")).lower()
    # pass = the scheme the product runs with passes the subcritical case
    ok = verdict.get(prod_scheme, verdict["inertial"])
    plt = _plt()
    fig, axs = plt.subplots(len(schemes), 3, figsize=(12, 3.0 * len(schemes) + 0.4), dpi=130, sharey=True,
                            squeeze=False)
    for r_, sc in enumerate(schemes):
        for ax, c in zip(axs[r_], cases[sc]):
            lab = "Stoker (wet bed)" if c["hr"] > 0 else "Ritter (dry bed)"
            ax.plot(c["x"], c["ref"], color=REF_C, ls="--", lw=1.3, label=f"{lab} analytic")
            ax.plot(c["x"], c["sim"], color=SIM_C if crit(c) else BAD_C, lw=1.8,
                    label=f"StormSim {'local-inertial' if sc == 'inertial' else 'HLL full SWE'}")
            ax.set_xlim(-300, 450)
            ax.set_title(f"{sc}: h_r/h_l {c['hr']:.1f} (Fr {c['Fr_middle']:.2f}): err {100*c['mean_abs_err_over_h1']:.1f} % h_l, "
                         f"front {100*c['front_rel_err']:+.0f} %", fontsize=8)
            ax.legend(fontsize=6.5, loc="upper right")
        axs[r_][0].set_ylabel("depth, m (t = 40 s)")
    for ax in axs[-1]:
        ax.set_xlabel("x from dam, m")
    fig.suptitle("Test 3 dam break (frictionless, dx 4 m): " + "; ".join(
        f"{sc} {'all 3 cases PASS' if verdict_all[sc] else ('subcritical PASS' if verdict[sc] else 'subcritical FAIL')}"
        for sc in schemes) + " (tolerance: depth err < 5 % h_l, bore within 10 %)", fontsize=9.5)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "t03_dam_break.png"))
    plt.close(fig)
    m = {sc: {f"hr_{c['hr']}": {k: c[k] for k in ("mean_abs_err_over_h1", "front_sim_m", "front_ref_m",
                                                    "front_rel_err", "Fr_middle", "steps")} for c in cases[sc]}
         for sc in schemes}
    m["grid_refinement_hr_0.5"] = conv
    m["production_scheme"] = prod_scheme
    res = "; ".join(f"{sc}: " + ", ".join(f"h_r/h_l {c['hr']:.1f} err {100*c['mean_abs_err_over_h1']:.1f} %/front "
                                         f"{100*c['front_rel_err']:+.0f} %" for c in cases[sc]) for sc in schemes)
    return {"id": "3", "name": "1D dam break vs Stoker (wet bed) / Ritter (dry bed), both water schemes",
            "reference": "Stoker 1957 / Ritter 1892 analytic; local-inertial expected weaker at high Froude "
                         "(Bates 2010; de Almeida & Bates 2013 WRR 49)",
            "result": res,
            "tolerance": "mean |err| < 5 % of h_l and bore position within 10 %; verdict on the production scheme "
                         f"('{prod_scheme}'), subcritical case h_r/h_l = 0.5",
            "pass": bool(ok), "plot": "t03_dam_break.png", "metrics": m,
            "notes": "Local-inertial drops the advection term, so it converges to a slightly different PDE: its "
                     "error does not shrink with dx (" + ", ".join(f"dx {c['dx_m']:g} m {100*c['mean_abs_err_over_h1']:.2f} %"
                                                                    for c in conv["inertial"]) + ") and grows with "
                     "Froude; the dry-bed front is far too slow. "
                     + ("The full-SWE HLL option (Audusse 2004 hydrostatic reconstruction) fixes this: "
                        + ", ".join(f"dx {c['dx_m']:g} m {100*c['mean_abs_err_over_h1']:.2f} %" for c in conv["hll"])
                        + " on the same case, and it captures the dry-bed front. " if "hll" in conv else "")
                     + "Urban ponding (the damage driver) is low-Froude."}


# --------------------------------------------------------------------------------------------- 4
def _channel(S0, n, Q, dx=4.0, L=2000.0, Wm_=20.0, theta=None, scheme="inertial"):
    nx = int(L / dx)
    wc = int(Wm_ / dx)
    ny = wc + 2
    x = (np.arange(nx) + 0.5) * dx
    z = np.repeat((S0 * (L - x))[None, :], ny, 0)
    solid = np.zeros((ny, nx), bool)
    solid[0] = solid[-1] = True
    edge = np.zeros((ny, nx), bool)
    edge[:, -1] = True
    src = np.zeros((ny, nx), bool)
    src[1:-1, 0] = True
    p = _params(scheme)
    if theta is not None:
        p.theta = theta
    s = SWE(z, dx, n=n, solid=solid, edge_open=edge, params=p)
    s.run_to(3 * 3600.0, source=(src, Q))
    hs = []
    e0, t0 = s.edge_out, s.t
    for _ in range(10):
        s.run_to(s.t + 60.0, source=(src, Q))
        hs.append(s.h[ny // 2, nx // 3:2 * nx // 3].copy())
    hs = np.array(hs)
    q = Q / Wm_
    hn = (q * n / np.sqrt(S0)) ** 0.6
    return {"S0": S0, "n": n, "q_m2s": q, "h_normal_m": hn, "h_sim_m": float(hs.mean()),
            "err_pct": float(100 * (hs.mean() - hn) / hn), "temporal_std_m": float(hs.std(0).mean()),
            "Fr": float(q / hn / np.sqrt(G * hn)), "outflow_over_inflow": float((s.edge_out - e0) / (s.t - t0) / Q),
            "profile": s.h[ny // 2].copy(), "x": x}


def test_manning(out_dir):
    specs = [(0.002, 0.035, 1.0), (0.002, 0.015, 2.0), (0.001, 0.015, 4.0), (0.004, 0.015, 2.0), (0.01, 0.015, 2.0)]
    schemes = ["inertial"] + (["hll"] if _has_hll() else [])
    allc = {sc: [_channel(*sp_, scheme=sc) for sp_ in specs] for sc in schemes}
    prod_scheme = str(getattr(Wm.default_params(), "scheme", "inertial")).lower()
    okf = lambda cs: all(abs(c["err_pct"]) < 1.0 and c["temporal_std_m"] < 1e-3 for c in cs if c["Fr"] < 0.8)
    verdict = {sc: okf(allc[sc]) for sc in schemes}
    ok = verdict.get(prod_scheme, verdict["inertial"])
    cases = allc["inertial"]
    sub = [c for c in cases if c["Fr"] < 0.8]
    sup = [c for c in cases if c["Fr"] >= 0.8]
    plt = _plt()
    fig, ax = plt.subplots(1, 2, figsize=(10.5, 3.8), dpi=130)
    ax[0].axhspan(-1, 1, color="#16a34a", alpha=0.12, label="±1 % tolerance")
    for sc, col, mk in (("inertial", SIM_C, "o"), ("hll", "#d97706", "s")):
        if sc not in allc:
            continue
        cs = allc[sc]
        ax[0].errorbar([c["Fr"] for c in cs], [c["err_pct"] for c in cs],
                       yerr=[100 * c["temporal_std_m"] / c["h_normal_m"] for c in cs], fmt=mk, color=col, capsize=3,
                       ms=6, label=f"{'local-inertial' if sc == 'inertial' else 'HLL full SWE'} (bar = temporal std)")
    ax[0].axvline(1, color=BAD_C, ls=":", lw=1)
    ax[0].set_xlabel("Froude number of normal flow")
    ax[0].set_ylabel("depth error vs Manning normal depth, %")
    ax[0].legend(fontsize=7)
    c0, c1 = sub[0], sup[-1] if sup else cases[-1]
    for c, col in ((c0, SIM_C), (c1, BAD_C)):
        ax[1].plot(c["x"], c["profile"], color=col, lw=1.4, label=f"local-inertial S={c['S0']} (Fr {c['Fr']:.2f})")
        ax[1].axhline(c["h_normal_m"], color=col, ls="--", lw=1, label=f"Manning h_n = {c['h_normal_m']:.3f} m")
    if "hll" in allc:
        ch = allc["hll"][-1]
        ax[1].plot(ch["x"], ch["profile"], color="#d97706", lw=1.4, label=f"HLL S={ch['S0']} (Fr {ch['Fr']:.2f})")
    ax[1].set_xlabel("x along channel, m")
    ax[1].set_ylabel("depth, m")
    ax[1].legend(fontsize=6.5)
    fig.suptitle("Test 4 steady uniform flow vs Manning: " + "; ".join(
        f"{sc} subcritical {_verdict(verdict[sc])}" for sc in schemes)
        + ". Local-inertial oscillates at Fr ≥ ~0.9; HLL is steady but biased on steep thin flow", fontsize=9)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "t04_manning.png"))
    plt.close(fig)
    m = {sc: [{k: v for k, v in c.items() if k not in ("profile", "x")} for c in allc[sc]] for sc in schemes}
    m["production_scheme"] = prod_scheme
    return {"id": "4", "name": "Steady uniform flow on a sloped channel vs Manning normal depth, both schemes",
            "reference": "Manning: h_n = (q n / √S)^(3/5)",
            "result": "; ".join(f"{sc}: " + ", ".join(f"Fr {c['Fr']:.2f} {c['err_pct']:+.1f} %"
                                                     + (" (oscillating)" if c["temporal_std_m"] > 0.01 else "")
                                                     for c in allc[sc]) for sc in schemes),
            "tolerance": f"subcritical (Fr < 0.8): |err| < 1 % and steady; verdict on production scheme '{prod_scheme}'",
            "pass": bool(ok), "plot": "t04_manning.png", "metrics": m,
            "notes": "Local-inertial: exact (0.0x %) for subcritical flow; at Fr ≥ ~0.9 (steep SF streets, thin sheet "
                     "flow) spurious roll-wave oscillations and a mean depth 15-35 % low (BUGS.md B2). HLL: always "
                     "steady, but first-order hydrostatic reconstruction over-predicts thin flow on steep slopes "
                     "(a known issue when h << slope x dx, Delestre et al. 2012; BUGS.md B4) and is 1.7 % low on the "
                     "rough mild case. Ponding that drives damage is subcritical."}


# --------------------------------------------------------------------------------------------- 5
def test_ea_depressions(out_dir):
    """(a) EA Test 1-type (Néelz & Pender 2013): a disconnected water body behind a crest floods when the
    boundary level rises above the crest and keeps the crest level after recession.
    (b) EA Test 2-type: an inflow volume fills a chain of depressions; static end levels vs level-pool
    (stage-volume) analytic solution computed on the same DEM."""
    dx = 4.0
    # ---- (a)
    nx, ny = 175, 5
    x = (np.arange(nx) + 0.5) * dx
    z = np.interp(x, [0, 60, 200, 260, 400, 600, 700], [8.0, 8.0, 10.0, 10.0, 8.0, 8.0, 11.0])
    z = np.repeat(z[None], ny, 0)
    bay = np.zeros((ny, nx), bool)
    bay[:, :10] = True
    lvl = lambda t: float(np.interp(t, [0, 3600, 7200, 10800], [9.7, 10.35, 10.35, 9.7]))
    s = SWE(z, dx, n=0.03, bay=bay, h0=np.where(bay, 9.7 - z, 0.0))   # EA tests use n = 0.03
    pond = (x > 420) & (x < 600)
    ts, pl = [], []
    while s.t < 12 * 3600:
        s.run_to(s.t + 120.0, tide_fn=lvl)
        ts.append(s.t)
        pl.append(float((s.h[2] + z[2])[pond].mean()))
    pl = np.array(pl)
    ts = np.array(ts)
    peak, final = float(pl.max()), float(pl[-1])
    ok_a = abs(peak - 10.35) < 0.05 and abs(final - 10.0) < 0.02
    # ---- (b)
    nx2, ny2 = 125, 21
    x2 = (np.arange(nx2) + 0.5) * dx
    y2 = (np.arange(ny2) + 0.5) * dx
    prof = np.interp(x2, [0, 20, 30, 110, 120, 125, 200, 205, 210, 290, 295, 300, 380, 385, 390, 480, 490, 500],
                     [3.0, 3.0, 0.2, 0.2, 1.0, 1.0, 0.0, 0.8, 0.8, 0.0, 0.6, 0.6, 0.0, 0.0, 0.0, 0.0, 3.0, 3.0])
    # rebuild with explicit basins: A [30,110] sill 1.0, B sill 0.8, C sill 0.6, D closed
    zb = np.repeat(prof[None], ny2, 0) + 0.4 * ((y2[:, None] - y2.mean()) / y2.mean()) ** 2
    sills = {"A": (1.0, (x2 > 20) & (x2 < 120)), "B": (0.8, (x2 >= 125) & (x2 < 205)),
             "C": (0.6, (x2 >= 210) & (x2 < 295)), "D": (None, (x2 >= 300) & (x2 < 490))}
    sill_z = {k: float(zb[:, np.nonzero(m)[0][-1] + 1].min()) if v is not None else None for k, (v, m) in sills.items()}

    def vol(mask_x, level):
        zz = zb[:, mask_x]
        return float(np.maximum(level - zz, 0).sum() * dx * dx)
    vA, vB, vC = vol(sills["A"][1], sill_z["A"]), vol(sills["B"][1], sill_z["B"]), vol(sills["C"][1], sill_z["C"])
    vDfull = vol(sills["D"][1], sill_z["C"])
    V = vA + vB + vC + 0.5 * vDfull
    lev_D = brentq(lambda L: vol(sills["D"][1], L) - 0.5 * vDfull, float(zb[:, sills["D"][1]].min()) + 1e-6, sill_z["C"])
    src = np.zeros((ny2, nx2), bool)
    src[ny2 // 2 - 1:ny2 // 2 + 2, 8:10] = True
    T_in = 1800.0
    s2 = SWE(zb, dx, n=0.03)
    s2.run_to(T_in, source=(src, V / T_in))
    s2.run_to(12 * 3600.0)
    eta = s2.h + zb
    got = {}
    for k, (v, m) in sills.items():
        wet = (s2.h[:, m] > 0.005)
        got[k] = float(eta[:, m][wet].mean()) if wet.any() else float("nan")
    ref = {"A": sill_z["A"], "B": sill_z["B"], "C": sill_z["C"], "D": lev_D}
    errs = {k: got[k] - ref[k] for k in ref}
    vol_err = (s2.volume() - V) / V
    ok_b = all(abs(e) < 0.02 for e in errs.values()) and abs(vol_err) < 1e-3
    ok = ok_a and ok_b
    plt = _plt()
    fig, ax = plt.subplots(1, 2, figsize=(11, 3.6), dpi=130)
    ax[0].plot(ts / 3600, [lvl(t) for t in ts], color=REF_C, ls="--", lw=1.2, label="boundary (bay) level")
    ax[0].plot(ts / 3600, pl, color=SIM_C, lw=2, label="level in the disconnected pond")
    ax[0].axhline(10.0, color="#b7a27a", lw=1, label="crest 10.0 m (expected final level)")
    ax[0].set_ylim(9.5, 10.5)
    ax[0].set_xlabel("time, h")
    ax[0].set_ylabel("water level, m")
    ax[0].set_title(f"(a) EA Test 1-type: peak {peak:.3f} m (exp 10.35), final {final:.3f} m (exp 10.00)", fontsize=8.5)
    ax[0].legend(fontsize=7)
    j = ny2 // 2
    ax[1].fill_between(x2, -0.2, zb[j], color="#b7a27a")
    ax[1].plot(x2, np.where(s2.h[j] > 0.005, eta[j], np.nan), color=SIM_C, lw=2, label="simulated static level")
    for k, (v, m) in sills.items():
        xs = x2[m]
        ax[1].hlines(ref[k], xs.min(), xs.max(), color=REF_C, ls="--", lw=1.2)
        ax[1].text(xs.mean(), ref[k] + 0.1, f"{k}: {errs[k]*100:+.1f} cm", ha="center", fontsize=7)
    ax[1].plot([], [], color=REF_C, ls="--", label="level-pool analytic (stage-volume)")
    ax[1].set_xlabel("x, m")
    ax[1].set_ylabel("elevation, m")
    ax[1].set_ylim(-0.2, 1.6)
    ax[1].set_title(f"(b) EA Test 2-type depression filling: volume error {vol_err*100:.1e} %", fontsize=8.5)
    ax[1].legend(fontsize=7, loc="upper right")
    fig.suptitle(f"Test 5 Environment Agency 2D benchmark-style tests (Néelz & Pender 2013): {_verdict(ok)}", fontsize=10)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "t05_ea_tests.png"))
    plt.close(fig)
    return {"id": "5", "name": "EA 2D benchmark Test 1/Test 2-type (disconnected water body; filling depressions)",
            "reference": "Néelz & Pender 2013 (EA SC120002) test behaviours; own DEMs, exact level-pool answers",
            "result": f"(a) pond peak {peak:.3f} m vs 10.35, final {final:.3f} m vs 10.00; (b) end levels "
                      + ", ".join(f"{k} {errs[k]*100:+.1f} cm" for k in errs) + f"; volume err {vol_err*100:.1e} %",
            "tolerance": "(a) peak ±5 cm, final ±2 cm; (b) levels ±2 cm, volume ±0.1 %", "pass": bool(ok),
            "plot": "t05_ea_tests.png",
            "metrics": {"a_peak_m": peak, "a_final_m": final, "b_levels_sim": got, "b_levels_ref": ref,
                        "b_volume_err": vol_err},
            "notes": "Same test logic as EA Tests 1 and 2 but on our own DEMs (the official DEMs/reference "
                     "results were not loaded in time); the answers are exact by construction (level pool)."}


def run(out_dir):
    out = []
    for f in (test_lake_at_rest, test_dam_break, test_manning, test_ea_depressions):
        t0 = time.time()
        r = f(out_dir)
        r["runtime_s"] = round(time.time() - t0, 1)
        out.append(r)
    return out


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    od = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
    os.makedirs(od, exist_ok=True)
    which = sys.argv[1:] or ["1", "3", "4", "5"]
    fns = {"1": test_lake_at_rest, "3": test_dam_break, "4": test_manning, "5": test_ea_depressions}
    for w in which:
        t0 = time.time()
        r = fns[w](od)
        print(json.dumps(r, indent=1, default=float), round(time.time() - t0, 1), "s")
