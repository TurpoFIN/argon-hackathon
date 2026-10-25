"""Write 3D layer files for a finished run: python -m loop.layers data/prep-loop/<run>/<model>

Re-simulates the run's no-preparation control, its rule baseline and its best round with the same
storm, forecast and mode. The simulation is deterministic, so these match the logged results.
"""
import json
import sys
from pathlib import Path

from .judge import OUT, Judge
from backend import multihazard, risk_assets


class DestructionJudge(Judge):
    def write_layers(self, label, plan, drawn, sim, ledger, runs):
        super().write_layers(label, plan, drawn, sim, ledger, runs)
        model = multihazard.Model(risk_assets.load(), sim["world"])
        payload = multihazard.destruction(model, sim["flood"], [m["time_seconds"] for m in sim["frame_metrics"]])
        payload["label"] = label
        text = json.dumps(payload, separators=(",", ":"))
        (self.run_dir / f"{label}_destruction.json").write_text(text)
        (self.run_dir / "destruction.json").write_text(text)


def forcing_file(folder: Path, log):
    if not log.get("forcing"):
        return None
    for candidate in [folder.parent / "forcing.json", *sorted(OUT.glob("forcing-*.json")), *sorted(OUT.glob("*/forcing.json"))]:
        if candidate.is_file() and json.loads(candidate.read_text()).get("issued") == log["forcing"]["issued"]:
            return str(candidate)
    raise SystemExit("forcing file for this run not found")


def main():
    folder = Path(sys.argv[1]).resolve()
    key = folder.name
    log = json.loads((folder / f"{key}.json").read_text())
    judge = DestructionJudge(folder, forcing=forcing_file(folder, log), rain_mm_h=log["parameters"]["rain_mm_h"], mode=log.get("mode", "doorways"))
    plans = {"control": log["control"]["plan"]}
    if log.get("greedy_heuristic"):
        plans["greedy"] = log["greedy_heuristic"]["plan"]
    scored = [r for r in log["rounds"] if "ledger" in r]
    if scored:
        best = min(scored, key=lambda r: r["ledger"]["total_usd"])
        plans[f"{key}_round{best['round']}"] = best["plan"]
    for label, plan in plans.items():
        entry = judge.evaluate(plan, label, label)
        print(f"{label}: ${entry['ledger']['total_usd']:,.0f} -> {label}_layers.json", flush=True)


if __name__ == "__main__":
    main()
