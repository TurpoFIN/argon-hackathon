"""Sandbox child: lock itself down (rlimits, Landlock FS+TCP, seccomp), then run plan/query code.

Run as: python -I server/_jail.py --mode plan|query --payload P --out OUT --tmp TMP --repo REPO
Writes OUT/_sandbox_result.json. Never imports anything from the repo before locking down.
"""
import argparse
import ctypes
import io
import json
import os
import resource
import struct
import sys
import traceback
import contextlib

libc = ctypes.CDLL(None, use_errno=True)
libc.syscall.restype = ctypes.c_long
libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]

PR_SET_NO_NEW_PRIVS = 38
PR_SET_SECCOMP = 22
SECCOMP_MODE_FILTER = 2


def no_new_privs():
    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prctl(NO_NEW_PRIVS) failed")


# ---------------------------------------------------------------- Landlock
SYS_create, SYS_add, SYS_restrict = 444, 445, 446
FS_EXECUTE, FS_WRITE_FILE, FS_READ_FILE, FS_READ_DIR = 1, 2, 4, 8
FS_REMOVE_DIR, FS_REMOVE_FILE, FS_MAKE_CHAR, FS_MAKE_DIR = 16, 32, 64, 128
FS_MAKE_REG, FS_MAKE_SOCK, FS_MAKE_FIFO, FS_MAKE_BLOCK, FS_MAKE_SYM = 256, 512, 1024, 2048, 4096
FS_REFER, FS_TRUNCATE = 1 << 13, 1 << 14
NET_BIND_TCP, NET_CONNECT_TCP = 1, 2


def landlock(ro_paths, rw_paths, ro_files=(), rw_files=()):
    abi = libc.syscall(SYS_create, None, 0, 1)
    if abi < 1:
        raise OSError("landlock unavailable")
    fs_all = (1 << 13) - 1
    if abi >= 2:
        fs_all |= FS_REFER
    if abi >= 3:
        fs_all |= FS_TRUNCATE
    net = (NET_BIND_TCP | NET_CONNECT_TCP) if abi >= 4 else 0
    if abi >= 4:
        attr = struct.pack("QQ", fs_all, net)
    else:
        attr = struct.pack("Q", fs_all)
    buf = ctypes.create_string_buffer(attr, len(attr))
    fd = libc.syscall(SYS_create, buf, len(attr), 0)
    if fd < 0:
        raise OSError(ctypes.get_errno(), "landlock_create_ruleset")
    ro = FS_EXECUTE | FS_READ_FILE | FS_READ_DIR
    file_ro = FS_EXECUTE | FS_READ_FILE
    rw = fs_all
    file_rw = FS_READ_FILE | FS_WRITE_FILE | (FS_TRUNCATE if abi >= 3 else 0)

    def add(path, access):
        try:
            pfd = os.open(path, os.O_PATH | os.O_CLOEXEC)
        except FileNotFoundError:
            return
        try:
            rule = ctypes.create_string_buffer(struct.pack("=Qi", access, pfd), 12)
            if libc.syscall(SYS_add, fd, 1, rule, 0) != 0:
                raise OSError(ctypes.get_errno(), f"landlock_add_rule {path}")
        finally:
            os.close(pfd)

    for p in ro_paths:
        add(p, ro)
    for p in ro_files:
        add(p, file_ro)
    for p in rw_paths:
        add(p, rw)
    for p in rw_files:
        add(p, file_rw)
    no_new_privs()
    if libc.syscall(SYS_restrict, fd, 0) != 0:
        raise OSError(ctypes.get_errno(), "landlock_restrict_self")
    os.close(fd)
    return abi


# ---------------------------------------------------------------- seccomp
class SockFilter(ctypes.Structure):
    _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte), ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint)]


class SockFprog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(SockFilter))]


def seccomp():
    LD, JEQ, JGE, RET = 0x20, 0x15, 0x35, 0x06
    ALLOW, KILL = 0x7FFF0000, 0x80000000
    EPERM = 0x00050000 | 1
    EACCES = 0x00050000 | 13
    blocked = [59, 322,  # execve, execveat
               425, 426, 427,  # io_uring_*
               101, 310, 311,  # ptrace, process_vm_readv/writev
               321, 272, 165, 166, 155,  # bpf, unshare, mount, umount2, pivot_root
               250, 248, 249]  # keyctl, add_key, request_key
    prog = [(LD, 0, 0, 4), (JEQ, 1, 0, 0xC000003E), (RET, 0, 0, KILL), (LD, 0, 0, 0),
            (JGE, 0, 1, 0x40000000), (RET, 0, 0, EPERM)]
    for nr in blocked:
        prog += [(JEQ, 0, 1, nr), (RET, 0, 0, EPERM)]
    prog += [(JEQ, 0, 4, 41), (LD, 0, 0, 16), (JEQ, 0, 1, 1), (RET, 0, 0, ALLOW), (RET, 0, 0, EACCES),
             (RET, 0, 0, ALLOW)]
    arr = (SockFilter * len(prog))(*[SockFilter(*p) for p in prog])
    fprog = SockFprog(len(prog), arr)
    no_new_privs()
    if libc.prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, ctypes.addressof(fprog), 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "seccomp failed")


def limits(cpu_s):
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 2))
    resource.setrlimit(resource.RLIMIT_AS, (32 << 30, 32 << 30))
    resource.setrlimit(resource.RLIMIT_FSIZE, (4 << 30, 4 << 30))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def user_tb(fname):
    et, ev, tb = sys.exc_info()
    frames = [f for f in traceback.extract_tb(tb)]
    keep = [f for f in frames if f.filename == fname] or frames[-2:]
    lines = []
    for f in keep:
        lines.append(f"  {f.filename} line {f.lineno}, in {f.name}: {f.line or ''}")
    # also last library frame (where it actually blew up) for helper misuse
    if frames and frames[-1].filename != fname:
        f = frames[-1]
        lines.append(f"  (in {os.path.basename(f.filename)}:{f.lineno} {f.name}: {f.line or ''})")
    return "Traceback (most recent call last):\n" + "\n".join(lines) + f"\n{et.__name__}: {ev}"


def jsonable(x, depth=0):
    try:
        json.dumps(x)
        return x
    except Exception:
        pass
    try:
        import numpy as np
        if isinstance(x, np.ndarray):
            if x.size <= 200:
                return x.tolist()
            return f"<ndarray shape={x.shape} dtype={x.dtype} min={np.nanmin(x):.4g} max={np.nanmax(x):.4g} mean={np.nanmean(x):.4g}>"
        if isinstance(x, np.generic):
            return x.item()
    except Exception:
        pass
    if isinstance(x, dict) and depth < 3:
        return {str(k): jsonable(v, depth + 1) for k, v in list(x.items())[:200]}
    if isinstance(x, (list, tuple, set)) and depth < 3:
        return [jsonable(v, depth + 1) for v in list(x)[:200]]
    return repr(x)[:4000]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True)
    ap.add_argument("--payload", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tmp", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--cpu", type=int, default=120)
    ap.add_argument("--landlock", action="store_true")
    ap.add_argument("--no-landlock", action="store_true")
    a = ap.parse_args()
    payload = json.load(open(a.payload))
    out_file = os.path.join(a.out, "_sandbox_result.json")
    result = {"ok": False}
    try:
        limits(a.cpu)
        pyhome = os.path.dirname(os.path.dirname(os.path.realpath(sys.executable)))
        if a.landlock:
            result["landlock_abi"] = landlock(
                # only the locked simulator, the city data and the venv: NOT agent/, server/, runs/ (other plans)
                ro_paths=["/usr", pyhome, os.path.join(a.repo, ".venv"), os.path.join(a.repo, "sim"),
                          os.path.join(a.repo, "data"), "/proc", "/sys/devices/system/cpu"],
                rw_paths=[a.tmp, a.out],
                ro_files=["/etc/ld.so.cache", "/etc/localtime", "/dev/zero", "/dev/urandom", "/dev/random"],
                rw_files=["/dev/null"],
            )
        seccomp()
        result["locked"] = True
    except Exception as e:
        result["error"] = f"sandbox lockdown failed: {e}"
        json.dump(result, open(out_file, "w"))
        sys.exit(3)

    sys.path.insert(0, os.path.join(a.tmp, "root"))  # private root: sim/ data/ symlinks + agent/citylib.py copy
    os.chdir(a.tmp)
    buf = io.StringIO()
    code = payload.get("code", "")
    try:
        import numpy as np
        from agent.citylib import load_city, export_city, plan_log_of, exec_plan_in
        if a.mode == "plan":
            with contextlib.redirect_stdout(buf):
                city, log, err = exec_plan_in(code)
            printed = (log or {}).get("printed") if isinstance(log, dict) else None
            out_txt = (buf.getvalue() + ("\n".join(printed) if printed else ""))[-3000:]
            if err:
                result.update(ok=False, error=err[-4000:], stdout=out_txt)
                json.dump(result, open(out_file, "w"), default=str)
                return
            info = export_city(city, a.out)
            result.update(ok=True, export=info, plan_log=jsonable(log), stdout=out_txt)
        else:
            with contextlib.redirect_stdout(buf):
                city = load_city()
            fname = "<query>"
            ns = {"city": city, "np": np, "numpy": np, "json": json}
            ns.update(payload.get("context") or {})
            try:
                with contextlib.redirect_stdout(buf):
                    try:
                        val = eval(compile(code, fname, "eval"), ns)
                    except SyntaxError:
                        exec(compile(code, fname, "exec"), ns)
                        val = ns.get("result", None)
            except BaseException:
                result.update(ok=False, error=user_tb(fname), stdout=buf.getvalue()[-3000:])
                json.dump(result, open(out_file, "w"))
                return
            r = jsonable(val)
            s = r if isinstance(r, str) else json.dumps(r, default=str)
            result.update(ok=True, result=s[:8000], truncated=len(s) > 8000, stdout=buf.getvalue()[-3000:])
    except BaseException:
        result.update(ok=False, error="internal: " + traceback.format_exc()[-3000:], stdout=buf.getvalue()[-2000:])
    json.dump(result, open(out_file, "w"), default=str)


if __name__ == "__main__":
    main()
