"""Extract USACE go-consequences default depth-damage curves into loop/reference/depth_damage.json.

Usage: python loop/build_curves.py <path to occtypes.json> <go-consequences commit sha>
"""
import json
import sys
from pathlib import Path


def central(distribution):
    p = distribution["parameters"]
    return float(p.get("value", p.get("mean", p.get("mostlikely", p.get("mode", 0.0)))))


def curve(component):
    functions = component["damagefunctions"]
    chosen = functions.get("default") or next(iter(functions.values()))
    fn = chosen["damagefunction"]
    return {"source": chosen.get("source"), "depth_ft": fn["xvalues"],
            "damage_pct": [central(d) for d in fn["ydistributions"]]}


source, sha = Path(sys.argv[1]), sys.argv[2]
types = json.loads(source.read_text())["occupancytypes"]
out = {
    "source": f"https://github.com/USACE/go-consequences/blob/{sha}/structures/occtypes.json",
    "notes": "USACE go-consequences default occupancy types (the curves it applies to NSI structures). "
             "Depth is feet of water above the first floor; damage is percent of structure or contents value. "
             "Stochastic distributions are reduced to their central value.",
    "curves": {name: {part: curve(t["componentdamagefunctions"][part])
                      for part in ("structure", "contents") if part in t["componentdamagefunctions"]}
               for name, t in types.items()},
}
target = Path(__file__).resolve().parent / "reference" / "depth_damage.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(out, indent=1))
print(len(out["curves"]), "occupancy types ->", target)
