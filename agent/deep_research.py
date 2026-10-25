"""Deep Research agent report on SF atmospheric-river preparedness (cited) -> agent/pack_cache/."""
import json, sys, time
from pathlib import Path
from google import genai

OUT = Path(__file__).resolve().parent / "pack_cache"
PROMPT = """Research report for a San Francisco storm-planning cell preparing for an El Nino atmospheric-river storm (late October 2026) hitting the Mission/SoMa/Mission Bay/Downtown area (tile 37.758-37.795 N, -122.425 to -122.385 W, incl. the Mission Creek / 17th & Folsom low basin).
Cover, with citations (URLs) for every number:
1. Past events: 31 Dec 2022 atmospheric river in SF (rainfall totals, 311 flooding/sewage/tree reports, damage $, where it flooded), Jan 2023 storms, 2014 and 2017 storms; the 17th & Folsom flooding history.
2. SF's own plans: SF Hazards & Climate Resilience Plan, SFPUC 100-Year Storm Flood Risk Map, SF Emergency Response Plan and its flood / severe-storm / tsunami annexes, Folsom Area Stormwater Improvement Project (tunnel/storage size, cost, schedule), Sewer System Improvement Program, Adopt-a-Drain, free sandbag program, floodwater grant program, backwater valves.
3. Unit costs (2024-2026 USD, cited): floodwalls per linear m by height, deployable flood barriers/gates, temporary barriers (AquaDam, HESCO), sandbags (filled, per bag and per linear m), stormwater pump stations per m3/s, storage tanks/cisterns per m3, deep storage/relief tunnels per m3/s and per km, permeable pavement per m2, bioswales/rain gardens per m2, green roofs per m2, inlet upgrades/new catch basins, building dry/wet floodproofing per building, backwater valves, tree pruning/removal/replacement/bracing per tree, wind screens; emergency ops: shelter operations per person-night, meals/water per person-day (FEMA), evacuation buses, crews per hour, generators.
4. Emergency-response doctrine: FEMA/CalOES/SF DEM shelter standards (sq ft/person), evacuation timing for vulnerable residents, FEMA BCA methodology (BCR >= 1, value of statistical life per DOT 2024/2025), DEFRA FD2321 flood hazard to people.
5. Tree failure in storms: SF street tree species most prone to failure, saturated soil uprooting, wind thresholds.
Output a structured markdown report with a cost table (item, unit, low, typical, high, source URL) and a numbered source list."""

def main():
    c = genai.Client()
    it = c.interactions.create(agent="deep-research-preview-04-2026", input=PROMPT, background=True)
    print("started", it.id, flush=True)
    (OUT / "deep_research_id.txt").write_text(it.id)
    while True:
        time.sleep(20)
        it = c.interactions.get(it.id)
        print("status", it.status, flush=True)
        if it.status in ("completed", "failed", "cancelled", "incomplete"):
            break
    txt = it.output_text or ""
    (OUT / "deep_research_sf_ar.md").write_text(txt)
    print("done", it.status, len(txt))

if __name__ == "__main__":
    main()
