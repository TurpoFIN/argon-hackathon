#!/usr/bin/env bash
# Remote smoke test: no-plan elnino_ar_2026 on Daytona and on Fly (in parallel, via drun / sim/remote.py),
# then compare each ledger_hash with the local no-plan baseline in runs/ (if one exists) and print latencies.
# Usage: scripts/remote_smoke.sh [storm] [backends...]   e.g. scripts/remote_smoke.sh elnino_ar_2026 daytona fly
set -euo pipefail
cd "$(dirname "$0")/.."
STORM="${1:-elnino_ar_2026}"; shift || true
BACKENDS=("${@:-daytona fly}"); BACKENDS=(${BACKENDS[@]})
LOG=/tmp/stormsim-logs; mkdir -p "$LOG"
pids=()
for b in "${BACKENDS[@]}"; do
  nice -n 10 .venv/bin/python -m sim.remote "$STORM" --label "remote-smoke-$b" --backend "$b" > "$LOG/smoke_$b.json" 2> "$LOG/smoke_$b.err" &
  pids+=($!)
done
rc=0
for p in "${pids[@]}"; do wait "$p" || rc=1; done
.venv/bin/python - "$STORM" "${BACKENDS[@]}" <<'EOF'
import json, sys
from pathlib import Path
storm, backends = sys.argv[1], sys.argv[2:]
runs = Path("runs")
local = None
for rp in sorted(runs.glob("*/result.json"), key=lambda p: p.stat().st_mtime, reverse=True):
    try:
        r = json.loads(rp.read_text())
    except Exception:
        continue
    if r.get("storm") == storm and not r.get("mock") and r.get("has_plan") is False and not r.get("remote") \
            and "truncated" not in " ".join(r.get("notes") or []):
        local = r
        break
print(f"local baseline: {local['run_id'] + ' ' + local['ledger_hash'][:16] if local else 'none found'}")
ok = True
for b in backends:
    out = Path(f"/tmp/stormsim-logs/smoke_{b}.json").read_text().strip().splitlines()
    if not out:
        print(f"{b}: FAILED (see /tmp/stormsim-logs/smoke_{b}.err)"); ok = False; continue
    j = json.loads(out[-1])
    same = None if local is None else (j["ledger_hash"] == local["ledger_hash"])
    ok &= same is not False
    print(f"{b}: {j['run_dir']} hash {j['ledger_hash'][:16]} total ${j['total_usd']:,.0f} "
          f"match_local={same} wall {j['wall_s']} s phases {j['remote']['phases_s']}")
sys.exit(0 if ok else 1)
EOF
