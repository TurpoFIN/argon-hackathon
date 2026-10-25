import json

from agent.action_plan import build_action_plan, make_eop, render_eop_html, write_eop
from sim.city_api import exec_plan
from sim.costs import price_plan


PLAN = '''
def apply(city):
    city.pump(900, 1200, m3s=0.026)
    r = city.response
    r.alert("Mission Creek flood warning", at_s=-3600)
    r.crew("pump", n=1, tasks=[(900, 1200)], start_s=0)
'''


def scored_result():
    editor, _, error = exec_plan(PLAN)
    assert error is None
    costs = price_plan(None, editor, editor)
    return {"storm": "elnino_ar_2026", "run_id": "test-run", "plan_cost": costs,
            "ledger": {"total_usd": 80, "by_cause": {"flood": 50, "life_safety": 30}}}


def test_action_plan_reconciles_cost_damage_and_queues():
    result = scored_result()
    doc = build_action_plan(PLAN, result,
                            baseline={"total_usd": 100, "by_cause": {"flood": 70, "life_safety": 30}})
    assert doc["checks"]["plan_cost_matches_actions"]
    assert doc["checks"]["action_avoided_damage_matches_ledger"]
    assert any(a["site"].get("coords") for a in doc["actions"])
    pump_queue = next(q for q in doc["crew_queues"] if q["crew_type"] == "pump crew")
    assert pump_queue["actions"]


def test_eop_is_self_contained_and_written(tmp_path):
    result = scored_result()
    doc = make_eop(PLAN, result, baseline={"total_usd": 100, "by_cause": {"flood": 70, "life_safety": 30}})
    page = render_eop_html(doc)
    assert "Emergency Operations Plan" in page
    assert "Mission Creek" in page
    paths = write_eop("job-test", PLAN, result,
                      baseline={"total_usd": 100, "by_cause": {"flood": 70, "life_safety": 30}},
                      out_dir=tmp_path)
    assert paths["eop_json"].endswith("/eop.json")
    saved = json.loads((tmp_path / "eop.json").read_text())
    assert saved["schema_version"] == 1
    assert (tmp_path / "eop.html").read_text().startswith("<!doctype html>")
