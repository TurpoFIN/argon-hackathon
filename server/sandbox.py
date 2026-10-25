"""Execute model-written plan / query code in an isolated subprocess.

Backends (auto-detected, best first):
  1. bwrap   - bubblewrap: ro-bind /usr + python + repo, tmpfs /tmp, --unshare-all (no net),
               clean env. Needs unprivileged user namespaces (blocked by Ubuntu 24.04 AppArmor
               `apparmor_restrict_unprivileged_userns=1` unless a bwrap AppArmor profile exists).
  2. landlock - unprivileged, kernel-enforced: Landlock FS rules (read-only /usr, python, repo;
               read-write only a private tmp dir + the output dir) + Landlock TCP bind/connect deny
               + seccomp filter (socket() only AF_UNIX, no execve/io_uring/ptrace) + rlimits,
               clean env (no secrets), own process group, wall-clock timeout.

Public API:
  run_plan(plan_code, out_dir=None, timeout=90) -> dict
      {ok, out_dir, export: path|None, plan_log, stdout, error, backend, secs}
      The edited city is written to out_dir (city export via CityEditor.export/save if present,
      otherwise generic: arrays -> edited_city.npz, JSON-able attrs -> edited_city.json).
  run_query(expr, timeout=60, context=None) -> dict {ok, result (str), stdout, error}
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = REPO / ".venv" / "bin" / "python"
PY_REAL = Path(os.path.realpath(PY))
PY_HOME = PY_REAL.parent.parent  # uv cpython install dir
JAIL = Path(__file__).resolve().parent / "_jail.py"
SANDBOX_ROOT = Path(os.environ.get("STORMSIM_SANDBOX_DIR", "/tmp/stormsim_sandbox"))

_backend_cache: str | None = None


def _clean_env(tmp: Path) -> dict:
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp),
        "TMPDIR": str(tmp),
        "PYTHONPATH": str(REPO),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "MPLCONFIGDIR": str(tmp),
        "NUMBA_CACHE_DIR": str(tmp / "numba"),
        "NUMBA_NUM_THREADS": "4",
        "OMP_NUM_THREADS": "4",
        "OPENBLAS_NUM_THREADS": "4",
        "LANG": "C.UTF-8",
    }


def _bwrap_cmd(tmp: Path, out: Path, argv: list[str]) -> list[str]:
    cmd = [
        "/usr/bin/bwrap",
        "--ro-bind", "/usr", "/usr",
        "--symlink", "usr/bin", "/bin",
        "--symlink", "usr/lib", "/lib",
        "--symlink", "usr/lib64", "/lib64",
        "--ro-bind", "/etc/ld.so.cache", "/etc/ld.so.cache",
        "--ro-bind", str(PY_HOME), str(PY_HOME),
        "--ro-bind", str(REPO), str(REPO),
        "--proc", "/proc", "--dev", "/dev",
        "--tmpfs", "/tmp",
        "--bind", str(tmp), str(tmp),
        "--bind", str(out), str(out),
        "--unshare-all", "--die-with-parent", "--new-session",
        "--clearenv",
    ]
    for k, v in _clean_env(tmp).items():
        cmd += ["--setenv", k, v]
    cmd += ["--chdir", str(tmp)]
    return cmd + argv


def detect_backend() -> str:
    global _backend_cache
    if _backend_cache:
        return _backend_cache
    forced = os.environ.get("STORMSIM_SANDBOX")
    if forced in ("bwrap", "landlock"):
        _backend_cache = forced
        return forced
    SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=SANDBOX_ROOT))
    try:
        r = subprocess.run(_bwrap_cmd(tmp, tmp, [str(PY), "-c", "print('ok')"]),
                           capture_output=True, text=True, timeout=20)
        _backend_cache = "bwrap" if r.returncode == 0 and "ok" in r.stdout else "landlock"
    except Exception:
        _backend_cache = "landlock"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return _backend_cache


def _run(mode: str, payload: dict, out_dir: Path | None, timeout: float) -> dict:
    SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f"{mode}_", dir=SANDBOX_ROOT))
    (tmp / "numba").mkdir()
    out = Path(out_dir) if out_dir else tmp / "out"
    out.mkdir(parents=True, exist_ok=True)
    (tmp / "payload.json").write_text(json.dumps(payload))
    root = tmp / "root"
    (root / "agent").mkdir(parents=True)
    for d in ("sim", "data"):
        os.symlink(REPO / d, root / d)
    for f in ("__init__.py", "citylib.py"):
        shutil.copy(REPO / "agent" / f, root / "agent" / f)
    backend = detect_backend()
    argv = [str(PY), "-I", str(JAIL), "--mode", mode, "--payload", str(tmp / "payload.json"),
            "--out", str(out), "--tmp", str(tmp), "--repo", str(REPO), "--cpu", str(int(timeout) + 5)]
    if backend == "bwrap":
        cmd = _bwrap_cmd(tmp, out, argv + ["--no-landlock"])
        env = None
    else:
        cmd = argv + ["--landlock"]
        env = _clean_env(tmp)
    t0 = time.time()
    try:
        p = subprocess.Popen(cmd, cwd=str(tmp), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, start_new_session=True)
        try:
            so, se = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, 9)
            so, se = p.communicate()
            return {"ok": False, "error": f"Timed out after {timeout:.0f}s (sandbox killed).",
                    "stdout": (so or "")[-3000:], "backend": backend, "secs": round(time.time() - t0, 2),
                    "out_dir": str(out)}
    except Exception as e:  # pragma: no cover
        return {"ok": False, "error": f"sandbox launch failed: {e}", "backend": backend, "out_dir": str(out)}
    res_path = out / "_sandbox_result.json"
    if res_path.exists():
        try:
            res = json.loads(res_path.read_text())
        except Exception as e:
            res = {"ok": False, "error": f"bad result file: {e}"}
    else:
        res = {"ok": False, "error": f"sandbox exited {p.returncode} without result. stderr: {se[-2000:]}"}
    res.setdefault("stdout", (so or "")[-3000:])
    if not res.get("ok") and se and "stderr" not in res:
        res["stderr"] = se[-2000:]
    res["backend"] = backend
    res["secs"] = round(time.time() - t0, 2)
    res["out_dir"] = str(out)
    if not out_dir:
        res["_tmp"] = str(tmp)
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    return res


def run_plan(plan_code: str, out_dir: str | Path | None = None, timeout: float = 90) -> dict:
    """Run `def apply(city)` in the sandbox; edited city + plan log land in out_dir."""
    if out_dir is None:
        out_dir = SANDBOX_ROOT / f"plan_{uuid.uuid4().hex[:10]}"
    return _run("plan", {"code": plan_code}, Path(out_dir), timeout)


def run_query(expr: str, timeout: float = 60, context: dict | None = None) -> dict:
    """Evaluate a read-only python expression (or snippet setting `result`) against the city."""
    res = _run("query", {"code": expr, "context": context or {}}, None, timeout)
    tmp = res.pop("_tmp", None)
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    return res


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    print("backend:", detect_backend())
    tests = {
        "ok": "def apply(city):\n    city.log('hello from plan')\n",
        "net": "def apply(city):\n    import socket\n    s=socket.create_connection(('1.1.1.1',80),timeout=3)\n",
        "udp": "def apply(city):\n    import socket\n    s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.sendto(b'x',('8.8.8.8',53))\n",
        "secret": "def apply(city):\n    print(open('/home/ubuntu/.bashrc').read()[:50])\n",
        "env": "def apply(city):\n    import os\n    city.log(str(sorted(os.environ)))\n",
        "exec": "def apply(city):\n    import subprocess\n    subprocess.run(['/usr/bin/curl','-s','https://example.com'])\n",
        "write_repo": "def apply(city):\n    open('" + str(REPO / 'pwned.txt') + "','w').write('x')\n",
        "loop": "def apply(city):\n    while True: pass\n",
    }
    for name, code in tests.items():
        r = run_plan(code, timeout=8 if name == "loop" else 60)
        print(f"{name:10s} ok={r.get('ok')} secs={r.get('secs')} log={r.get('plan_log')} err={(r.get('error') or '')[-160:]!r}")
    print("query:", run_query("type(city).__name__")["result"] if True else None)
