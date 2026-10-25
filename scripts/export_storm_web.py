"""Export the storm forcing of every scenario in sim/storm.py for the browser (cinema mode).

Writes data/web/storm_<name>.json with the frame times, rain, gusts, mean wind, wind direction and
tide exactly as the solvers use them, plus the scenario's sources. Run: python3 scripts/export_storm_web.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from sim.storm import SCENARIOS, load_storm  # noqa: E402

KEEP = ["name", "title", "duration_s", "dt_frame_s", "t", "rain_mm_h", "gust_ms", "mean_ms", "wind_dir_deg",
        "wind_dir_t", "tide_m", "start_local", "total_rain_mm", "sources"]

out_dir = os.path.join(ROOT, "data", "web")
os.makedirs(out_dir, exist_ok=True)
for name in SCENARIOS:
    storm = load_storm(name).to_json()
    path = os.path.join(out_dir, f"storm_{name}.json")
    with open(path, "w") as f:
        json.dump({k: storm.get(k) for k in KEEP}, f, separators=(",", ":"))
    print(path, f"peak rain {max(storm['rain_mm_h']):.1f} mm/h, peak gust {max(storm['gust_ms']):.1f} m/s")
