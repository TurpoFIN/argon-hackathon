from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from .scenarios import Parameters, PlaybackUpdate, store
from . import argon

ROOT = Path(__file__).resolve().parents[1]
app = FastAPI(title="Embarcadero storm prototype", docs_url=None, redoc_url=None)
from .risk_api import router as risk_router
app.include_router(risk_router)


@app.get("/api/engine")
def engine():
    return {"model": argon.MODEL, "configured": bool(argon.api_key())}


@app.post("/api/engine/rebuild", status_code=202)
def rebuild():
    with store.lock:
        return store.submit(Parameters(**store.snapshot()['requested_parameters']), force=True)


@app.get("/api/state")
def state():
    return store.snapshot()


@app.post("/api/scenarios", status_code=202)
def create_scenario(parameters: Parameters):
    try:
        return store.submit(parameters)
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(503, str(error)) from error


@app.post("/api/playback")
def playback(update: PlaybackUpdate):
    return store.playback(update)


@app.get("/api/results/{scenario_id}/{filename}")
def result(scenario_id: str, filename: str):
    if filename not in {"manifest.json", "grid.bin", "flood.bin", "world.json"}:
        raise HTTPException(404)
    if scenario_id != "baseline" and (len(scenario_id) != 12 or any(c not in "0123456789abcdef" for c in scenario_id)):
        raise HTTPException(404)
    folder = ROOT / "data" if scenario_id == "baseline" else ROOT / "data/scenarios" / scenario_id
    path = folder / filename
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, headers={"Cache-Control": "no-cache"})


@app.get("/analysis")
def analysis():
    return FileResponse(ROOT / "dist/analysis.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/manifest")
def manifest():
    path = ROOT / "data/manifest.json"
    if not path.exists():
        raise HTTPException(503, "Run python -m backend.prepare before starting the viewer")
    return FileResponse(path, headers={"Cache-Control": "no-cache"})


@app.get("/api/{name}.bin")
def binary(name: str):
    if name not in {"grid", "flood"}:
        raise HTTPException(404)
    return FileResponse(ROOT / f"data/{name}.bin", media_type="application/octet-stream")


app.mount("/", StaticFiles(directory=ROOT / "dist", html=True), name="viewer")
