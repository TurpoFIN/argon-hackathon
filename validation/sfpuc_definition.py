"""Like-for-like comparison with SFPUC's own flood-map definition.

SFPUC: the 100-Year Storm Flood Risk Map shows "deep and contiguous flooding": "flooding that is at least
6 inches deep, spanning an area at least the size of half an average city block" (SFPUC 100-Year Flood Risk
Map Information Sheet and FAQs, Dec 2024; SFPUC Flood Maps page). SFPUC gives no square-foot figure, so the
average block is measured independently of any flood result: the mean area of street-centreline-bounded
blocks inside the tile (DataSF street centrelines, data/raw/extra/streets.geojson) = 12,880 m2 -> half = 6,440 m2.
Scoring: open-land 4 m cells (buildings and bay excluded), 8-connected flooded regions >= that area.
Nothing here is tuned; thresholds are fixed by the definition above.
"""
import json
import os
import sys

import numpy as np
from scipy import ndimage
from scipy.stats import rankdata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from sim import water as W  # noqa: E402

DEPTH_M = 6 * 0.0254
HALF_BLOCK_M2 = 0.5 * 12880.0


def score(md, g, depth=0.15, min_area=0.0):
    land = (g.cover != 3) & (g.bldg_id < 0)
    pred = (md > depth) & land
    if min_area > 0:
        lab, n = ndimage.label(pred, structure=np.ones((3, 3)))
        size = np.bincount(lab.ravel()) * g.dx ** 2
        keep = size >= min_area
        keep[0] = False
        pred = keep[lab]
    obs = (g.flood100 > 0) & land
    h, m, f = int((pred & obs).sum()), int((~pred & obs).sum()), int((pred & ~obs).sum())
    sc = md[land].astype(float)
    lab_ = obs[land]
    r = rankdata(sc)
    n1, n0 = lab_.sum(), (~lab_).sum()
    auc = float((r[lab_].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))
    return {"CSI": round(h / (h + m + f), 3), "POD": round(h / (h + m), 3), "FAR": round(f / max(h + f, 1), 3),
            "AUC_maxdepth": round(auc, 3), "pred_km2": round(pred.sum() * g.dx ** 2 / 1e6, 3),
            "obs_km2": round(obs.sum() * g.dx ** 2 / 1e6, 3)}


if __name__ == "__main__":
    g = W.load_grid()
    rep = W.load_result(os.path.join(ROOT, "runs", "dev_water", "water_dec31_2022.npz")).max_depth
    atl = np.load(os.path.join(ROOT, "validation", "cache", "atlas14_100yr_maxdepth.npz"))["max_depth"]
    out = {"definition": "SFPUC: >= 6 in deep, contiguous >= half an average city block (6,440 m2 measured)",
           "a_replay_our_threshold_0.15m": score(rep, g, 0.15),
           "b_replay_sfpuc_definition": score(rep, g, DEPTH_M, HALF_BLOCK_M2),
           "c_atlas14_100yr_sfpuc_definition": score(atl, g, DEPTH_M, HALF_BLOCK_M2),
           "c2_atlas14_100yr_our_threshold_0.15m": score(atl, g, 0.15)}
    json.dump(out, open(os.path.join(ROOT, "validation", "out", "sfpuc_definition.json"), "w"), indent=1)
    print(json.dumps(out, indent=1))
