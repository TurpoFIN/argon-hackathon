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
    return {"storm": "elnino_ar_2026", "run_id": "test-run", "budget_usd": 50_000_000, "plan_cost": costs,
            "ledger": {"total_usd": 80_000_000, "by_cause": {"flood": 50_000_000, "life_safety": 30_000_000}}}


def test_action_plan_reconciles_cost_damage_and_queues():
    result = scored_result()
    doc = build_action_plan(PLAN, result,
                            baseline={"total_usd": 100_000_000, "by_cause": {"flood": 70_000_000, "life_safety": 30_000_000}})
    assert doc["checks"]["plan_cost_matches_actions"]
    assert doc["checks"]["action_avoided_damage_matches_ledger"]
    assert any(a["site"].get("coords") for a in doc["actions"])
    pump_queue = next(q for q in doc["crew_queues"] if q["crew_type"] == "pump crew")
    assert pump_queue["actions"]


def test_eop_is_self_contained_and_written(tmp_path):
    result = scored_result()
    doc = make_eop(PLAN, result, baseline={"total_usd": 100_000_000, "by_cause": {"flood": 70_000_000, "life_safety": 30_000_000}})
    assert doc["dispatch_status"] == "ready"
    assert not doc["checks"]["flags"]
    page = render_eop_html(doc)
    assert "Emergency Operations Plan" in page
    assert "Mission Creek" in page
    paths = write_eop("job-test", PLAN, result,
                      baseline={"total_usd": 100_000_000, "by_cause": {"flood": 70_000_000, "life_safety": 30_000_000}},
                      out_dir=tmp_path)
    assert paths["eop_json"].endswith("/eop.json")
    saved = json.loads((tmp_path / "eop.json").read_text())
    assert saved["schema_version"] == 1
    assert (tmp_path / "eop.html").read_text().startswith("<!doctype html>")


def test_eop_blocks_over_budget_and_stale_baseline():
    result = scored_result()
    over = dict(result, budget_usd=1)
    doc = make_eop(PLAN, over, baseline={"total_usd": 100_000_000, "by_cause": {"flood": 70_000_000}})
    assert doc["dispatch_status"] == "blocked"
    assert not doc["eligible_for_dispatch"]
    assert any("exceeds job budget" in flag for flag in doc["checks"]["flags"])
    stale = make_eop(PLAN, result, baseline={"total_usd": 33_200_000_000, "by_cause": {"flood": 33_200_000_000}})
    assert stale["dispatch_status"] == "blocked"
    assert any("outside" in flag for flag in stale["baseline_check"]["errors"])


def test_eop_summary_contains_top_ten_actions():
    result = scored_result()
    doc = make_eop(PLAN, result, baseline={"total_usd": 100_000_000, "by_cause": {"flood": 70_000_000, "life_safety": 30_000_000}})
    assert 1 <= len(doc["top_actions"]) <= 10
    assert doc["summary"]["top_action_count"] == len(doc["top_actions"])
    assert "One-page dispatch summary" in render_eop_html(doc)
