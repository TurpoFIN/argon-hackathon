"""Tests 2 and 6: mass conservation over the real-grid dec31_2022 replay and real-world validation
against the SFPUC 100-year flood map and the 311 reports of 31.12.2022."""
from __future__ import annotations

import json
import os

import numpy as np
from scipy import ndimage

from sim import water as W
from sim.storm import load_storm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THR = 0.15          # m, "flooded" (same threshold as sim.water.validate)
RADII = (0, 15, 30, 50, 100, 150, 200)
N_PERM = 10000


def _disk(r_cells):
    yy, xx = np.mgrid[-r_cells:r_cells + 1, -r_cells:r_cells + 1]
    return (xx ** 2 + yy ** 2) <= r_cells ** 2


def mass_balance(res, grid, storm):
    V = res.volumes
    A = grid.dx ** 2
    # independent recomputation of the rain input: integrate storm.rain_at over the run on a 1 s grid
    t = np.arange(0.0, float(storm.duration_s), 1.0) + 0.5
    P_m = float(np.sum(storm.rain_at(t)) / 3.6e6)          # m of rain per unit area
    bay = W._bay_mask(grid.cover, grid.bldg_id, grid.z.astype(np.float64), res.params["intertidal_z"])
    n_cells = int((~bay).sum())                            # land + roofs (incl. courtyards)
    rain_indep = P_m * n_cells * A
    surf_end_indep = float(np.sum(res.depth[-1].astype(np.float64))) * A   # final frame (float16-stored)
    inflow = V["rain_total"] - V["roof_retained"] + V["dry_weather_flow"] + V["upstream_sewer_inflow"] + V["bay_to_land"]
    outflow = (V["infiltration"] + V["sewer_to_treatment"] + V["sewer_cso_to_bay"] + V["tunnel_to_bay"]
               + V["plan_storage"] + V["pumped_to_bay"] + V["edge_outflow"])
    closure = float(V["mass_error_m3"])   # (storage_end - storage_start) - (inflow - outflow), solver ledger
    err_pct = 100 * abs(closure) / max(V["rain_total"] + abs(V["bay_to_land"]), 1e-9)
    return {
        "rain_in_m3_solver": V["rain_total"], "rain_in_m3_independent": rain_indep,
        "rain_rel_diff_pct": 100 * (V["rain_total"] - rain_indep) / rain_indep,
        "surface_end_m3_solver": V["surface_end"], "surface_end_m3_from_frames": surf_end_indep,
        "surface_rel_diff_pct": 100 * (V["surface_end"] - surf_end_indep) / max(surf_end_indep, 1.0),
        "terms_m3": {k: round(float(v), 1) for k, v in V.items()},
        "closure_error_m3": float(closure), "closure_error_pct": float(err_pct),
    }


def skill(res, grid, rng):
    land = (grid.cover != 3) & (grid.bldg_id < 0)
    md = res.max_depth
    pred = (md > THR) & land
    obs = (grid.flood100 > 0) & land
    hits, miss, fa = int((pred & obs).sum()), int((~pred & obs).sum()), int((pred & ~obs).sum())
    cn = int((~pred & ~obs & land).sum())
    out = {"flood100": {"POD": hits / max(hits + miss, 1), "FAR": fa / max(hits + fa, 1),
                        "CSI": hits / max(hits + miss + fa, 1), "pred_km2": pred.sum() * 16e-6,
                        "obs_km2": obs.sum() * 16e-6, "hits": hits, "misses": miss, "false_alarms": fa}}
    # Heidke-like baseline: random map with the same flooded area (expected CSI by chance)
    fp, fo = pred.sum() / land.sum(), obs.sum() / land.sum()
    eh = fp * fo * land.sum()
    out["flood100"]["CSI_random_same_area"] = eh / (pred.sum() + obs.sum() - eh)
    # elevation-only baseline (bathtub): flood the lowest cells with the same total area
    zl = grid.z[land]
    zthr = np.sort(zl)[int(pred.sum())]
    bath = land & (grid.z < zthr)
    hb = int((bath & obs).sum())
    out["flood100"]["CSI_lowest_ground_same_area"] = hb / max(int(bath.sum()) + int(obs.sum()) - hb, 1)
    # ROC AUC of max depth as a score for flood100 membership (threshold-free)
    from scipy.stats import rankdata
    sc = md[land].astype(np.float64)
    lab = obs[land]
    r = rankdata(sc)
    n1, n0 = lab.sum(), (~lab).sum()
    out["flood100"]["AUC_maxdepth"] = float((r[lab].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))
    r2 = rankdata(-grid.z[land].astype(np.float64))
    out["flood100"]["AUC_low_ground_baseline"] = float((r2[lab].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))

    # 311 reports (flooding + sewage) vs simulated flooding
    pts = [q for q in grid.validation.get("311_2022_12_31", []) if q.get("kind") in ("flooding", "sewage")]
    ix, iy = grid.to_ix([q["x"] for q in pts], [q["y"] for q in pts])
    # chance: random open-land (street/yard) cells, the places reports are geolocated to
    ly, lx = np.nonzero(land)
    dist = ndimage.distance_transform_edt(~pred) * grid.dx         # m to nearest simulated flooded cell
    dist_obs = ndimage.distance_transform_edt(~obs) * grid.dx
    d_rep = dist[iy, ix]
    d_rep_obs = dist_obs[iy, ix]
    curves = {"radius_m": list(RADII), "reports": [], "random_mean": [], "random_p95": [], "p_value": [],
              "sfpuc_map_reports": [], "sfpuc_map_random": []}
    n = len(pts)
    sel = rng.integers(0, len(ly), size=(N_PERM, n))
    d_rand = dist[ly[sel], lx[sel]]
    d_rand_obs = dist_obs[ly[sel], lx[sel]]
    for R in RADII:
        f = float((d_rep <= R + 1e-6).mean())
        fr = (d_rand <= R + 1e-6).mean(1)
        curves["reports"].append(f)
        curves["random_mean"].append(float(fr.mean()))
        curves["random_p95"].append(float(np.percentile(fr, 95)))
        curves["p_value"].append(float(((fr >= f).sum() + 1) / (N_PERM + 1)))
        curves["sfpuc_map_reports"].append(float((d_rep_obs <= R + 1e-6).mean()))
        curves["sfpuc_map_random"].append(float((d_rand_obs <= R + 1e-6).mean(1).mean()))
    # depth score at reports vs random land (AUC = P(score at a report > score at a random cell))
    mdx = ndimage.maximum_filter(np.where(land, md, 0.0), footprint=_disk(int(round(30 / grid.dx))))
    s_rep = mdx[iy, ix]
    s_rand = mdx[ly, lx]
    from scipy.stats import mannwhitneyu
    u = mannwhitneyu(s_rep, s_rand, alternative="greater")
    auc = float(u.statistic / (len(s_rep) * len(s_rand)))
    zl_rand = grid.z[ly, lx]
    u2 = mannwhitneyu(-grid.z[iy, ix], -zl_rand, alternative="greater")
    out["311"] = {"n_reports": n, "n_flooding": sum(q["kind"] == "flooding" for q in pts),
                  "n_sewage": sum(q["kind"] == "sewage" for q in pts), "curves": curves,
                  "median_dist_report_m": float(np.median(d_rep)),
                  "median_dist_random_m": float(np.median(dist[ly, lx])),
                  "AUC_maxdepth30m": auc, "AUC_p_value": float(u.pvalue),
                  "AUC_low_ground_baseline": float(u2.statistic / (len(s_rep) * len(s_rand)))}
    return out, pred, obs, pts


def plot_map(res, grid, pred, obs, pts, path, sk):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
    ext = [0, grid.nx * grid.dx / 1000, 0, grid.ny * grid.dx / 1000]
    fig, ax = plt.subplots(figsize=(8.6, 9.6), dpi=130)
    zz = np.where(grid.cover == 3, np.nan, grid.z)
    ax.imshow(zz, origin="lower", extent=ext, cmap="Greys_r", vmin=-2, vmax=np.nanpercentile(zz, 99), alpha=0.45)
    ax.imshow(np.ma.masked_where(grid.bldg_id < 0, np.ones_like(grid.z)), origin="lower", extent=ext,
              cmap=ListedColormap(["#d9d9d9"]), alpha=0.9)
    ax.imshow(np.ma.masked_where(grid.cover != 3, np.ones_like(grid.z)), origin="lower", extent=ext,
              cmap=ListedColormap(["#c6dbef"]))
    cat = np.zeros(grid.z.shape, np.int8)
    cat[pred & obs] = 1
    cat[pred & ~obs] = 2
    cat[~pred & obs] = 3
    ax.imshow(np.ma.masked_where(cat == 0, cat), origin="lower", extent=ext, interpolation="nearest",
              cmap=ListedColormap(["#1a7f37", "#2b6cb0", "#d97706"]), vmin=1, vmax=3)
    for kind, mk, col in (("flooding", "o", "#b91c1c"), ("sewage", "s", "#4a1d0a")):
        P = [q for q in pts if q["kind"] == kind]
        ax.scatter([(q["x"] - grid.x0) / 1000 for q in P], [(q["y"] - grid.y0) / 1000 for q in P], s=14,
                   marker=mk, facecolors="none", edgecolors=col, linewidths=0.9, label=f"311 {kind} report ({len(P)})")
    for c, lab in (("#1a7f37", "sim floods & SFPUC 100-yr zone (hit)"), ("#2b6cb0", "sim floods, outside SFPUC zone"),
                   ("#d97706", "SFPUC zone, sim dry (miss)")):
        ax.fill_between([], [], color=c, label=lab)
    f = sk["flood100"]
    ax.set_title(f"dec31_2022 replay vs SFPUC 100-yr flood zone + 311 reports\n"
                 f"CSI {f['CSI']:.2f} (random same-area {f['CSI_random_same_area']:.2f}), POD {f['POD']:.2f}, "
                 f"FAR {f['FAR']:.2f}; max depth > {THR} m", fontsize=10)
    ax.set_xlabel("km east")
    ax.set_ylabel("km north")
    ax.legend(loc="upper left", fontsize=7.5, framealpha=0.9)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def plot_curves(sk, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    c = sk["311"]["curves"]
    R = c["radius_m"]
    fig, ax = plt.subplots(figsize=(7, 4.4), dpi=130)
    ax.plot(R, c["reports"], "-o", color="#b91c1c", lw=2, label=f"311 reports (n={sk['311']['n_reports']}) vs sim")
    ax.plot(R, c["random_mean"], "--", color="#555", lw=1.5, label="random street cells vs sim (mean)")
    ax.fill_between(R, 0, c["random_p95"], color="#999", alpha=0.18, label="random: 95th percentile")
    ax.plot(R, c["sfpuc_map_reports"], "-s", color="#1a7f37", lw=1.2, ms=4, label="311 reports vs SFPUC map (for reference)")
    ax.plot(R, c["sfpuc_map_random"], ":", color="#1a7f37", lw=1.2, label="random vs SFPUC map")
    ax.set_xlabel("distance to simulated flooding (> 0.15 m), m")
    ax.set_ylabel("fraction of points within distance")
    ax.set_ylim(0, 1.02)
    i30 = R.index(30)
    sig = c["p_value"][i30] < 0.01
    ax.set_title(("311 flood/sewage reports lie closer to simulated flooding than chance\n" if sig else
                  "311 reports vs simulated flooding: NOT significantly better than chance at p < 0.01\n")
                 + f"within 30 m: {c['reports'][i30]:.0%} vs {c['random_mean'][i30]:.0%} random (p = {c['p_value'][i30]:.4f}); "
                 f"depth AUC {sk['311']['AUC_maxdepth30m']:.2f}", fontsize=9.5)
    ax.legend(fontsize=7.5, loc="lower right")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def plot_mass(mb, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    T = mb["terms_m3"]
    ins = [("rain", T["rain_total"] - T["roof_retained"], "#2b6cb0"),
           ("dry-weather sewage", T["dry_weather_flow"], "#6b46c1"),
           ("bay -> land", max(T["bay_to_land"], 0), "#0891b2")]
    outs = [("infiltration", T["infiltration"], "#15803d"), ("sewer -> treatment", T["sewer_to_treatment"], "#a16207"),
            ("CSO -> bay", T["sewer_cso_to_bay"], "#b91c1c"), ("tile-edge outflow", T["edge_outflow"], "#64748b"),
            ("land -> bay", max(-T["bay_to_land"], 0), "#94a3b8"), ("on streets at end", T["surface_end"], "#0ea5e9"),
            ("in sewer at end", T["sewer_end"], "#7c3aed")]
    fig, ax = plt.subplots(figsize=(8, 3.4), dpi=130)
    for row, items in ((1, ins), (0, outs)):
        left = 0.0
        for k, v, c in items:
            ax.barh(row, v / 1e3, left=left, color=c, edgecolor="white", label=f"{k}: {v/1e3:,.0f}k m³")
            left += v / 1e3
    ax.set_yticks([0, 1], ["out + stored", "in"])
    ax.set_xlabel("volume, thousand m³")
    ax.legend(fontsize=6.5, ncol=2, loc="upper center", bbox_to_anchor=(0.5, -0.25), frameon=False)
    ax.set_title(f"Water balance, real 4 m SF grid, dec31_2022 replay: closure {mb['closure_error_m3']:.1e} m³ "
                 f"({mb['closure_error_pct']:.1e} % of rain)\nrain vs independent hyetograph integral: "
                 f"{mb['rain_rel_diff_pct']:+.4f} %; final storage vs saved depth frame: {mb['surface_rel_diff_pct']:+.3f} %",
                 fontsize=8.5)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def run(out_dir, res_path=None):
    from validation.replay_run import run as replay
    res_path = res_path or replay()
    res = W.load_result(res_path)
    grid = W.load_grid()
    storm = load_storm("dec31_2022")
    rng = np.random.default_rng(20221231)
    mb = mass_balance(res, grid, storm)
    sk, pred, obs, pts = skill(res, grid, rng)
    plot_mass(mb, os.path.join(out_dir, "t02_mass_balance.png"))
    plot_map(res, grid, pred, obs, pts, os.path.join(out_dir, "t06_replay_map.png"), sk)
    plot_curves(sk, os.path.join(out_dir, "t06_311_distance.png"))
    f, s3 = sk["flood100"], sk["311"]
    i30 = s3["curves"]["radius_m"].index(30)
    tests = [
        {"id": "2", "name": "Mass conservation, real grid, full dec31_2022 storm",
         "reference": "rain in = surface + sewer storage + infiltration + treatment + CSO + edge + bay exchange",
         "result": f"closure {mb['closure_error_pct']:.1e} % of {mb['rain_in_m3_independent']/1e6:.2f} Mm³ rain; "
                   f"rain vs independent integral {mb['rain_rel_diff_pct']:+.3f} %; surface vs frames {mb['surface_rel_diff_pct']:+.2f} %",
         "tolerance": "< 1 % each",
         "pass": bool(mb["closure_error_pct"] < 1 and abs(mb["rain_rel_diff_pct"]) < 1 and abs(mb["surface_rel_diff_pct"]) < 1),
         "plot": "t02_mass_balance.png", "metrics": mb,
         "notes": "Closure uses the solver's own flux ledger; rain input and final surface storage are re-derived "
                  "independently (hyetograph integral x land area; sum of the saved final depth frame)."},
        {"id": "6a", "name": "Replay vs SFPUC 100-year storm flood zone",
         "reference": "SFPUC 100-Year Storm Flood Risk Map (DataSF jzu3-4yxp), SFPUC's own 1D-2D model",
         "result": f"CSI {f['CSI']:.2f} (random same-area {f['CSI_random_same_area']:.2f}, lowest-ground {f['CSI_lowest_ground_same_area']:.2f}); "
                   f"POD {f['POD']:.2f}, FAR {f['FAR']:.2f}; AUC {f['AUC_maxdepth']:.2f}",
         "tolerance": "CSI > 2x random same-area map and AUC > 0.7",
         "pass": bool(f["CSI"] > 2 * f["CSI_random_same_area"] and f["AUC_maxdepth"] > 0.7),
         "plot": "t06_replay_map.png", "metrics": f,
         "notes": "The SFPUC zone is SFPUC's own model of a 100-year design storm, not an observation. The replay "
                  "forcing (SFOC1 gauge: 88 mm in 6 h, 67 mm in 3 h) is ~200-yr (6 h) / ~300-yr (3 h) by NOAA Atlas 14 "
                  "at SF, so the 100-yr zone should mostly flood (POD is the key number) and some extra flooding "
                  "outside it is expected. Cells are 4 m open land (buildings and bay excluded)."},
        {"id": "6b", "name": "Replay vs 311 flooding + sewage reports, 31.12.2022",
         "reference": f"{s3['n_reports']} SF 311 reports in the tile (DataSF vw6y-z8j6), random street-cell placement baseline",
         "result": f"{s3['curves']['reports'][i30]:.0%} within 30 m of sim flooding vs {s3['curves']['random_mean'][i30]:.0%} random "
                   f"(p = {s3['curves']['p_value'][i30]:.4f}); depth AUC {s3['AUC_maxdepth30m']:.2f} (p = {s3['AUC_p_value']:.1e})",
         "tolerance": "permutation p < 0.01 at 30 m",
         "pass": bool(s3["curves"]["p_value"][i30] < 0.01),
         "plot": "t06_311_distance.png", "metrics": s3,
         "notes": "311 points are geocoded to street addresses (not the exact flooded spot) and reflect where "
                  "people report, so a hit radius is needed. 10,000 random placements give the null distribution."},
    ]
    return tests


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ROOT)
    out = os.path.join(ROOT, "validation", "out")
    os.makedirs(out, exist_ok=True)
    print(json.dumps(run(out, sys.argv[1] if len(sys.argv) > 1 else None), indent=1, default=float))
