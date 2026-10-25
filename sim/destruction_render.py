"""Top-down render of a destruction result (for the model's watch loop and for humans): damaged buildings by
damage state, downed power and trolley wire, de-energised sections, failed trees with fall direction, roof-debris
flights, damaged signals, shelters and streetlights."""
from __future__ import annotations

import json
import os

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRID = os.path.join(ROOT, "data", "grid")


def render_png(res, path, assets=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection

    meta = json.load(open(os.path.join(GRID, "meta.json")))
    z = np.load(os.path.join(GRID, "city.npz"))
    assets = assets or json.load(open(os.path.join(GRID, "assets.json")))
    trees = {t["id"]: t for t in json.load(open(os.path.join(GRID, "trees.json")))}
    x0, y0, dx = meta["x0"], meta["y0"], meta["dx"]
    ny, nx = z["bldg_id"].shape
    ext = [0, nx * dx, 0, ny * dx]

    fig, ax = plt.subplots(figsize=(13, 15), dpi=110)
    fig.patch.set_facecolor("#111")
    ax.set_facecolor("#1b1b1b")
    base = np.where(z["cover"] == 3, 0.08, np.where(z["bldg_id"] >= 0, 0.42, 0.2))
    ax.imshow(base, origin="lower", extent=ext, cmap="gray", vmin=0, vmax=1, interpolation="nearest")
    ds = np.zeros(int(z["bldg_id"].max()) + 2, np.int8)
    for b in res["buildings"]:
        ds[b["id"]] = b["ds"]
    bid = z["bldg_id"]
    dmap = np.where(bid >= 0, ds[np.maximum(bid, 0)], 0).astype(float)
    dmap[dmap == 0] = np.nan
    ax.imshow(dmap, origin="lower", extent=ext, cmap=matplotlib.colors.ListedColormap(["#f2b200", "#ff6d00", "#e8143c", "#a0003c"]),
              vmin=0.5, vmax=4.5, interpolation="nearest")
    out_b = set(res["outage"]["buildings_out"])
    if out_b:
        om = np.isin(bid, list(out_b)).astype(float)
        om[om == 0] = np.nan
        ax.imshow(om, origin="lower", extent=ext, cmap=matplotlib.colors.ListedColormap(["#ad5dff"]), alpha=0.55, interpolation="nearest")

    def segs(items, ids=None):
        return [[(s["a"][0] - x0, s["a"][1] - y0), (s["b"][0] - x0, s["b"][1] - y0)] for i, s in enumerate(items) if ids is None or i in ids]

    ocs_out = {o["id"] for o in res["ocs_out"]}
    ocs_down = {o["id"] for o in res["ocs_spans"]}
    ax.add_collection(LineCollection(segs(assets["ocs_spans"]), colors="#00d1ef", linewidths=0.35, alpha=0.45))
    ax.add_collection(LineCollection(segs(assets["ocs_spans"], ocs_out), colors="#ad5dff", linewidths=1.6, alpha=0.9))
    ax.add_collection(LineCollection(segs(assets["ocs_spans"], ocs_down), colors="#ff2d55", linewidths=3.0))
    span_down = {s["id"] for s in res["power_spans"]}
    ax.add_collection(LineCollection(segs(assets["power_spans"]), colors="#e0e0e0", linewidths=0.7, alpha=0.8))
    ax.add_collection(LineCollection(segs(assets["power_spans"], span_down), colors="#ff2d55", linewidths=3.0))
    pp = assets["power_poles"]
    ax.scatter([p["x"] - x0 for p in pp], [p["y"] - y0 for p in pp], s=2, c="#e0e0e0", lw=0)
    pd = [pp[p["id"]] for p in res["power_poles"]]
    if pd:
        ax.scatter([p["x"] - x0 for p in pd], [p["y"] - y0 for p in pd], s=40, marker="x", c="#ff2d55", lw=1.5)

    tl = []
    for f in res["trees"]:
        t = trees.get(f["id"])
        if t:
            a = np.radians(f["fall_dir_deg"])
            L = t.get("height_m") or 6.0
            tl.append([(t["x"] - x0, t["y"] - y0), (t["x"] - x0 + np.sin(a) * L, t["y"] - y0 + np.cos(a) * L)])
    ax.add_collection(LineCollection(tl, colors="#00c769", linewidths=1.6))
    dl = [[(d["x0"] - x0, d["y0"] - y0), (d["x1"] - x0, d["y1"] - y0)] for d in res["debris"]]
    ax.add_collection(LineCollection(dl, colors="#ffd166", linewidths=0.8, alpha=0.9))
    hits = [d for d in res["debris"] if d["hit"] >= 0]
    if hits:
        ax.scatter([d["x1"] - x0 for d in hits], [d["y1"] - y0 for d in hits], s=12, c="#ffd166", marker="o", lw=0)
    sg = assets["signals"]
    bad = [sg[s["id"]] for s in res["signals"] if s["head_t"] is not None or s["struct_t"] is not None]
    dark = [sg[s["id"]] for s in res["signals"] if s["dark_t"] is not None or s["flooded"]]
    ax.scatter([s["x"] - x0 for s in sg], [s["y"] - y0 for s in sg], s=5, c="#00c769", lw=0, alpha=0.6)
    if bad:
        ax.scatter([s["x"] - x0 for s in bad], [s["y"] - y0 for s in bad], s=36, c="#ff6d00", marker="^", lw=0)
    if dark:
        ax.scatter([s["x"] - x0 for s in dark], [s["y"] - y0 for s in dark], s=36, facecolors="none", edgecolors="#ad5dff", marker="o", lw=1.2)
    sh = assets["shelters"]
    shd = [sh[s["id"]] for s in res["shelters"]]
    if shd:
        ax.scatter([s["x"] - x0 for s in shd], [s["y"] - y0 for s in shd], s=36, c="#f644b9", marker="s", lw=0)
    li = assets["streetlights"]
    ld = [li[s["id"]] for s in res["streetlights"] if s["t"] is not None]
    if ld:
        ax.scatter([s["x"] - x0 for s in ld], [s["y"] - y0 for s in ld], s=30, c="#ffffff", marker="v", lw=0)

    rb = res.get("road_blocks") or []
    if rb:
        ax.scatter([r["x"] - x0 for r in rb], [r["y"] - y0 for r in rb], s=46, marker="X", c="#ff2d55", edgecolors="#ffffff", linewidths=0.6, zorder=6)
    ts = res.get("tree_strikes") or []
    if ts:
        ax.scatter([r["x"] - x0 for r in ts], [r["y"] - y0 for r in ts], s=40, marker="D", c="#f644b9", edgecolors="#ffffff", linewidths=0.5, zorder=6)
    S = res["summary"]
    L = res["ledger"]
    ax.set_xlim(ext[0], ext[1]); ax.set_ylim(ext[2], ext[3])
    ax.set_aspect("equal")
    ax.tick_params(colors="#888")
    ax.set_xlabel("m east of tile SW corner (UTM 10N)", color="#aaa")
    ax.set_ylabel("m north", color="#aaa")
    title = (f"StormSim SF destruction | storm {res['storm']} | wind: {res['wind_source']}\n"
             f"{S['trees_failed']} trees down | {S['buildings_damaged']} buildings damaged {S['buildings_by_ds']} | "
             f"{S['windows_broken_by_debris']:.0f} windows broken by roof debris\n"
             f"power: {S['power_poles_down']} poles, {S['power_spans_down']} spans down, {S['customers_out']} customers out | "
             f"Muni wire: {S['ocs_spans_down']} spans down, {S['ocs_route_km_out']} km de-energised\n"
             f"{S['signals_damaged']} signals damaged, {S['signals_dark']} dark | {S['shelters_smashed']} shelters | "
             f"{S['streetlights_down']} streetlights down | {S.get('roads_blocked', 0)} roads blocked | "
             f"{S.get('trees_into_buildings', 0)} trees into buildings | destruction ledger ${L['total_usd'] / 1e6:.2f}M")
    ax.set_title(title, color="#eee", fontsize=10, loc="left")
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    leg = [Patch(color=c, label=l) for c, l in (("#f2b200", "building DS1 minor"), ("#ff6d00", "DS2 moderate"), ("#e8143c", "DS3 severe"),
                                                 ("#a0003c", "DS4 destroyed"), ("#ad5dff", "de-energised / without power"))]
    leg += [Line2D([], [], color="#00c769", lw=2, label="failed tree (fall direction, length = height)"),
            Line2D([], [], color="#ff2d55", lw=3, label="wire down (power / trolley)"),
            Line2D([], [], color="#00d1ef", lw=1, label="Muni overhead contact wire"),
            Line2D([], [], color="#e0e0e0", lw=1, label="PG&E overhead distribution (OSM)"),
            Line2D([], [], color="#ffd166", lw=1, label="roof-debris flight (dot = facade hit)"),
            Line2D([], [], color="#ff6d00", marker="^", lw=0, label="signal heads / mast arm damaged"),
            Line2D([], [], color="#f644b9", marker="s", lw=0, label="bus shelter smashed"),
            Line2D([], [], color="#ff2d55", marker="X", lw=0, markeredgecolor="#fff", label="road blocked (fallen tree / pole / light / live wire)"),
            Line2D([], [], color="#f644b9", marker="D", lw=0, markeredgecolor="#fff", label="tree fell into a building")]
    ax.legend(handles=leg, loc="lower right", fontsize=8, facecolor="#222", edgecolor="#444", labelcolor="#ddd")
    fig.tight_layout()
    fig.savefig(path, facecolor=fig.get_facecolor())
    plt.close(fig)
    return path
