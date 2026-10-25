"""Serve the existing preparation-run viewer without replacing its UI or results."""
import json
import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

ROOT = Path(__file__).resolve().parents[1]
RUNS = Path(os.environ.get("STORM_PREP_RUNS_DIR", ROOT / "data/prep-loop"))
router = APIRouter()
NAME = re.compile(r"^[A-Za-z0-9_-]+$")


def available_runs(root=RUNS):
    """Only offer completed, viewable comparisons, newest result first."""
    found = []
    for path in root.glob("*/*/*.json"):
        if path.stem not in ("argon", "flash") or path.parent.name != path.stem:
            continue
        try:
            log = json.loads(path.read_text())
            rounds = [r["round"] for r in log.get("rounds", [])
                      if r.get("ledger") and (path.parent / f"{path.stem}_round{r['round']}_layers.json").is_file()]
            if rounds and (path.parent / "control_layers.json").is_file():
                found.append({"run": path.parent.parent.name, "model": path.stem,
                              "rounds": rounds, "updated": path.stat().st_mtime})
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(found, key=lambda row: row["updated"], reverse=True)


def viewer():
    return FileResponse(ROOT / "loop/viewer3d.html", headers={"Cache-Control": "no-cache"})


@router.get("/3d")
def preparation_viewer():
    return viewer()


@router.get("/api/prep-runs")
def preparation_runs():
    return {"runs": available_runs()}


@router.api_route("/runs/{run}/{model}/{filename}", methods=["GET", "HEAD"])
def preparation_result(run: str, model: str, filename: str):
    if not NAME.fullmatch(run) or model not in ("argon", "flash"):
        raise HTTPException(404)
    if not re.fullmatch(r"[A-Za-z0-9_-]+\.(json|mp4|png)", filename):
        raise HTTPException(404)
    path = RUNS / run / model / filename
    if not path.is_file() or not path.resolve().is_relative_to(RUNS.resolve()):
        raise HTTPException(404)
    return FileResponse(path, headers={"Cache-Control": "no-cache"})


@router.get("/api/prep-runs/{run}/{model}/{label}/actions")
def action_catalog(run: str, model: str, label: str):
    if not NAME.fullmatch(run) or model not in ("argon", "flash") or not NAME.fullmatch(label):
        raise HTTPException(404)
    from .viewer_actions import load_pack, summary
    try:
        return summary(load_pack(RUNS, run, model, label))
    except FileNotFoundError:
        raise HTTPException(404, "Run or action evidence is not available")


@router.get("/api/prep-runs/{run}/{model}/{label}/actions/{action_id}")
def action_detail(run: str, model: str, label: str, action_id: str):
    if not NAME.fullmatch(run) or model not in ("argon", "flash") or not NAME.fullmatch(label):
        raise HTTPException(404)
    from .viewer_actions import load_pack
    try:
        pack = load_pack(RUNS, run, model, label)
    except FileNotFoundError:
        raise HTTPException(404)
    found = next((row for row in pack['actions'] if row['id'] == action_id), None)
    if not found:
        raise HTTPException(404)
    return {**found, 'storm_id': pack['storm_id'], 'minutes_axis': pack['minutes'], 'weather': pack['weather']}
