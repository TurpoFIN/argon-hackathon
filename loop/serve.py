"""Live demo server: python -m loop.serve [--port 5182]

GET  /                     newest run's page (rebuilt from the logs on every request)
GET  /runs/<run>/...       a run's page, renders and videos
GET  /api/status           running jobs and known runs
POST /api/run              start Argon and Flash on one new run (both see the same storm)
"""
import argparse
import json
import subprocess
import sys
import threading
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .judge import OUT, ROOT
from . import report

JOBS = {}
LOCK = threading.Lock()


def runs():
    """Run names, newest model log first (page rebuilds don't count as activity)."""
    newest = {}
    for log in OUT.glob("*/*/*.json"):
        if log.stem in ("argon", "flash"):
            run = log.parent.parent.name
            newest[run] = max(newest.get(run, 0), log.stat().st_mtime)
    return sorted(newest, key=newest.get, reverse=True)


def running():
    with LOCK:
        return {key: job["run"] for key, job in JOBS.items() if job["process"].poll() is None}


FORCING = OUT / "forcing-2023-01-04.json"  # TimesFM 3 backtest of the January 2023 storm (loop.forecast)


def start(rounds):
    if running():
        return None
    run = "live-" + datetime.now().strftime("%H%M%S")
    forcing = ["--forcing", str(FORCING)] if FORCING.is_file() else []
    with LOCK:
        for model in ("argon", "flash"):
            log = OUT / run / f"{model}.out"
            log.parent.mkdir(parents=True, exist_ok=True)
            process = subprocess.Popen([sys.executable, "-u", "-m", "loop.judge", "--model", model, "--rounds", str(rounds),
                                        "--run", f"{run}/{model}", *forcing], cwd=ROOT, stdout=log.open("w"), stderr=subprocess.STDOUT)
            JOBS[model] = {"run": run, "process": process}
    return run


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(OUT), **kwargs)

    def log_message(self, *args):
        pass

    def send_json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/status":
            return self.send_json({"running": running(), "runs": runs()})
        if path in ("/", "/index.html"):
            names = runs()
            if not names:
                return self.send_json({"error": "no runs yet; POST /api/run"}, 404)
            self.send_response(302)
            self.send_header("Location", f"/runs/{names[0]}/index.html")
            return self.end_headers()
        if path.startswith("/runs/"):
            parts = path.split("/")
            if len(parts) >= 4 and parts[3] == "index.html" and (OUT / parts[2]).is_dir():
                try:
                    report.build(OUT / parts[2])
                except SystemExit:
                    pass  # no logs yet: serve whatever exists
            self.path = "/" + "/".join(parts[2:])
            return super().do_GET()
        self.send_error(404)

    def do_POST(self):
        if self.path.split("?")[0] != "/api/run":
            return self.send_error(404)
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        rounds = max(1, min(5, int(body.get("rounds", 3))))
        run = start(rounds)
        if run is None:
            return self.send_json({"error": "a run is already in progress", "running": running()}, 409)
        self.send_json({"run": run, "page": f"/runs/{run}/index.html"}, 202)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5182)
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Prep loop: http://localhost:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
