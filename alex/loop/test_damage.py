"""Damage scorer properties on the real NSI structures and the real simulation grid.

Run: env PYTHONPATH=. .venv/bin/python -m loop.test_damage
"""
import numpy as np

from .judge import OUT, argon, build_base
from . import damage


def main():
    base, _, _ = build_base()
    nsi = damage.nsi_structures(argon.BBOX, OUT / "nsi_structures.json")
    exposure = damage.Exposure(base.lon, base.lat, base.walls, base.ocean, nsi)
    land = ~base.ocean
    dry = exposure.score(np.zeros_like(base.z))
    assert dry["total_usd"] == 0 and dry["structures_flooded"] == 0, dry["total_usd"]

    previous = -1.0
    for depth in (0.02, 0.05, 0.3, 0.6, 1.0, 2.0):
        flood = np.where(land, depth, 0.0)
        ledger = exposure.score(flood)
        assert ledger["total_usd"] >= previous, (depth, ledger["total_usd"], previous)
        previous = ledger["total_usd"]
        if depth < damage.WET_M:
            assert ledger["total_usd"] == 0
        print(f"uniform {depth:.2f} m: ${ledger['total_usd']:,.0f}, {ledger['structures_flooded']} flooded")

    everyone = [r["fd_id"] for r in exposure.rows]
    shallow = exposure.score(np.where(land, 0.5, 0.0), everyone, 0.6)
    deep = exposure.score(np.where(land, 0.7, 0.0), everyone, 0.6)
    unprotected_deep = exposure.score(np.where(land, 0.7, 0.0))
    assert shallow["total_usd"] == 0 and shallow["structures_kept_dry_by_doorway_sandbags"] > 0
    assert deep["total_usd"] == unprotected_deep["total_usd"], "protection must not help above its height"
    total_value = float((exposure.value_s + exposure.value_c).sum())
    assert previous < total_value, "damage cannot exceed exposed value"
    print(f"{len(exposure.rows)} structures, exposed value ${total_value:,.0f}; all damage properties hold")


if __name__ == "__main__":
    main()
