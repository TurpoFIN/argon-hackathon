"""Run the dec31_2022 replay once on the real grid and cache it (validation input).
Usage: .venv/bin/python -m validation.replay_run [--threads 16]"""
import hashlib, json, os, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("NUMBA_NUM_THREADS", "16")
import numpy as np
from sim import water
from sim.storm import load_storm

CACHE = os.path.join(ROOT, "validation", "cache")


def params_key(storm="dec31_2022"):
    p = water.default_params()
    src = open(os.path.join(ROOT, "sim", "water.py"), "rb").read()
    h = hashlib.sha1(json.dumps({k: getattr(p, k) for k in p.__dataclass_fields__}, sort_keys=True,
                                default=str).encode() + src).hexdigest()[:10]
    return f"{storm}_{h}"


def _meta_params(path):
    try:
        z = np.load(path)
        return json.loads(bytes(z["_meta"]).decode()).get("params", {})
    except Exception:
        return None


def cached(storm_name="dec31_2022"):
    """(path, is_current). Candidates: our cache and the water agent's runs/dev_water/water_<storm>.npz.
    'current' = the result's stored solver parameters equal today's data/reference/water_params.json
    (the source-hash key is only a fast path). Prefers current, then newest."""
    import glob
    cur = os.path.join(CACHE, f"water_{params_key(storm_name)}.npz")
    if os.path.exists(cur):
        return cur, True
    cands = [o for o in glob.glob(os.path.join(CACHE, f"water_{storm_name}_*.npz")) if not o.endswith(".tmp.npz")]
    dev = os.path.join(ROOT, "runs", "dev_water", f"water_{storm_name}.npz")
    if os.path.exists(dev):
        cands.append(dev)
    if not cands:
        return None, False
    p = water.default_params()
    want = {k: getattr(p, k) for k in p.__dataclass_fields__}
    def match(path):
        mp = _meta_params(path) or {}
        return all(mp.get(k) == v for k, v in want.items() if k in mp and v is not None)
    cands.sort(key=os.path.getmtime, reverse=True)
    for c in cands:
        if match(c):
            return c, True
    return cands[0], False


def run(storm_name="dec31_2022", threads=16):
    os.makedirs(CACHE, exist_ok=True)
    key = params_key(storm_name)
    path = os.path.join(CACHE, f"water_{key}.npz")
    if os.path.exists(path):
        return path
    grid = water.load_grid()
    storm = load_storm(storm_name)
    res = water.run_water(grid, storm, threads=threads, keep_frames=True)
    water.save_result(res, path + ".tmp.npz")
    os.replace(path + ".tmp.npz", path)
    json.dump({"key": key, "path": path, "runtime_s": res.runtime_s, "when": time.time()},
              open(os.path.join(CACHE, f"water_{storm_name}_latest.json"), "w"))
    return path


if __name__ == "__main__":
    th = int(sys.argv[sys.argv.index("--threads") + 1]) if "--threads" in sys.argv else 16
    print(run(threads=th))
