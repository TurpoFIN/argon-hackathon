from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[1]
app = FastAPI(title="Embarcadero storm prototype", docs_url=None, redoc_url=None)


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
