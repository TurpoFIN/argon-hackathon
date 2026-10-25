"""Screenshots of a plan for the model to watch.

1. Headless Chrome of the web app (`http://127.0.0.1:8765/?run=<id>&shot=N`) when web/dist exists and the
   server is up (STORMSIM_WEB_SHOTS=0 disables). Blank captures are discarded.
2. Fallback: matplotlib top-down map of the plan objects (plan_objects.json, local metres from grid SW)
   over terrain + the run's max water depth.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
SERVER = os.environ.get("STORMSIM_SERVER", "http://127.0.0.1:8765")
CHROME = shutil.which("google-chrome") or shutil.which("chromium")
N_SHOTS = int(os.environ.get("STORMSIM_SHOTS", "3"))


def _server_up() -> bool:
    try:
        urllib.request.urlopen(SERVER + "/api/health", timeout=2)
        return (REPO / "web" / "dist" / "index.html").exists()
    except Exception:
        return False


def chrome_shots(run_id: str, out_dir: Path, n: int = N_SHOTS) -> list[str]:
    if not CHROME or os.environ.get("STORMSIM_WEB_SHOTS") == "0" or not _server_up():
        return []
    shots = []
    for i in range(n):
        p = out_dir / f"shot_{i}.png"
        cmd = [CHROME, "--headless=new", "--no-sandbox", "--hide-scrollbars", "--window-size=1280,720",
               "--enable-unsafe-webgpu", "--enable-features=Vulkan", "--use-angle=swiftshader",
               "--virtual-time-budget=20000", f"--screenshot={p}", f"{SERVER}/?run={run_id}&shot={i}"]
        try:
            subprocess.run(cmd, capture_output=True, timeout=60, cwd="/tmp")
        except Exception:
            continue
        if p.exists() and p.stat().st_size > 20000:
            try:
                from PIL import Image
                a = np.asarray(Image.open(p).convert("L"), dtype=np.float32)
                if a.std() < 4:
                    continue
            except Exception:
                pass
            shots.append(str(p))
    return shots


COLORS = {"wall": "#d9d9d9", "floodwall": "#d9d9d9", "gate": "#ffb000", "sandbags": "#c8a165", "pump": "#00c8ff",
          "storage": "#3c8cff", "tunnel": "#7a5cff", "shelter": "#39d353", "evacuate": "#ff4d6d", "depot": "#ffd166",
          "crew": "#f78c6b", "close_road": "#ff0000", "permeable": "#7fdb7f", "bioswale": "#4caf50"}


def matplotlib_shot(run_dir: Path, objects: list | None, out: Path) -> str | None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from agent.tools import grid, max_depth
    meta, g = grid()
    z = g["z"]
    fig = plt.figure(figsize=(12.8, 7.2), dpi=100, facecolor="#0d1117")
    ax = fig.add_axes([0.0, 0.0, 0.72, 1.0])
    ax.set_facecolor("#0d1117")
    ext = [0, meta["nx"] * meta["dx"], 0, meta["ny"] * meta["dx"]]
    gy, gx = np.gradient(z)
    shade = np.clip(0.6 + 0.4 * (gx - gy) / (np.abs(gx - gy).max() + 1e-6) * 6, 0, 1)
    shade[g["cover"] == 3] = 0.15
    bh = g["bldg_h"] > 0
    shade[bh] = 0.75
    ax.imshow(shade, origin="lower", cmap="gray", extent=ext, vmin=0, vmax=1)
    r = max_depth(str(run_dir))
    if r is not None:
        md, fdx, _, _ = r
        m = np.ma.masked_less(md, 0.05)
        ax.imshow(m, origin="lower", cmap="Blues", extent=[0, md.shape[1] * fdx, 0, md.shape[0] * fdx],
                  vmin=0, vmax=1.5, alpha=0.85)
    legend = {}
    for o in objects or []:
        geo = o.get("geometry") or {}
        pts = np.asarray(geo.get("coords") or [], float)
        if pts.size == 0:
            continue
        pts = pts.reshape(-1, pts.shape[-1])[:, :2]
        c = COLORS.get(o.get("type"), (o.get("style") or {}).get("color") or "#ff66ff")
        kind = geo.get("kind", "point")
        if kind == "polyline":
            ax.plot(pts[:, 0], pts[:, 1], color=c, lw=2.5)
        elif kind in ("polygon", "mesh"):
            ax.fill(pts[:, 0], pts[:, 1], color=c, alpha=0.45, lw=1.5, ec=c)
        else:
            ax.scatter(pts[:, 0], pts[:, 1], s=60, color=c, edgecolor="k", zorder=5)
        legend[o.get("type")] = c
        if o.get("label") and len(objects) < 60:
            ax.annotate(str(o["label"])[:24], pts[0], color="w", fontsize=7, xytext=(3, 3), textcoords="offset points")
    ax.set_xlim(ext[0], ext[1])
    ax.set_ylim(ext[2], ext[3])
    ax.axis("off")
    tx = fig.add_axes([0.73, 0.0, 0.27, 1.0])
    tx.axis("off")
    res = {}
    try:
        res = json.loads((Path(run_dir) / "result.json").read_text())
    except Exception:
        pass
    y = 0.97
    step = 0.034

    def line(txt, color="w", square=None):
        nonlocal y
        if square:
            tx.text(0.02, y, "■", va="top", color=square, fontsize=11, family="monospace")
        tx.text(0.09 if square else 0.02, y, txt, va="top", color=color, fontsize=11, family="monospace")
        y -= step
    line(f"Plan objects: {len(objects or [])}")
    y -= step / 2
    for t, c in list(legend.items())[:20]:
        n = sum(1 for o in objects if o.get("type") == t)
        line(f"{t} × {n}", square=c)
    led = res.get("ledger") or {}
    y -= step / 2
    if led:
        line(f"Damage: ${float(led.get('total_usd') or 0)/1e6:,.1f}M")
    pc = res.get("plan_cost") or {}
    if isinstance(pc, dict) and pc.get("total_usd") is not None:
        line(f"Plan cost: ${float(pc['total_usd'])/1e6:,.1f}M")
    line("water = max depth (blue)", color="#8ab4f8")
    fig.savefig(out, facecolor=fig.get_facecolor())
    plt.close(fig)
    return str(out)


def plan_screenshots(run_dir, objects=None) -> list[str]:
    rd = Path(run_dir)
    out_dir = rd / "screens"
    out_dir.mkdir(exist_ok=True)
    shots = chrome_shots(rd.name, out_dir)
    if shots:
        return shots
    p = matplotlib_shot(rd, objects, out_dir / "plan_map.png")
    return [p] if p else []
