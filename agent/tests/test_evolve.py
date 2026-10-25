"""Tests for agent/evolve.py: diff application, parsing, strategy descriptors, and a full evolution run with a
fake LLM client on the mock sim (no API calls). Run: STORMSIM_MOCK=1 .venv/bin/python -m pytest agent/tests -q"""
import json
import os
import re
from types import SimpleNamespace

import pytest

os.environ.setdefault("STORMSIM_MOCK", "1")

from agent import evolve as ev  # noqa: E402

PARENT = """def apply(city):
    city.storage(1966, 2466, volume_m3=15000, intake_m3s=3.0)
    city.pump(1986, 2466, m3s=2.5)
    city.wall([(10, 10), (20, 10)], 1.2)
"""


def test_apply_diff_exact_and_whitespace_tolerant():
    d = "NOTE: bigger vault\n<<<<<<< SEARCH\n    city.storage(1966, 2466, volume_m3=15000, intake_m3s=3.0)\n=======\n" \
        "    city.storage(1966, 2466, volume_m3=25000, intake_m3s=4.0)\n>>>>>>> REPLACE\n"
    child, n, probs = ev.apply_diff(PARENT, d)
    assert n == 1 and not probs and "volume_m3=25000" in child and "pump" in child
    d2 = "<<<<<<< SEARCH\n    city.pump(1986, 2466, m3s=2.5)   \n=======\n    city.pump(1986, 2466, m3s=4)\n>>>>>>> REPLACE"
    child2, n2, _ = ev.apply_diff(PARENT, d2)
    assert n2 == 1 and "m3s=4)" in child2
    bad, n3, probs3 = ev.apply_diff(PARENT, "<<<<<<< SEARCH\nnot there\n=======\nx\n>>>>>>> REPLACE")
    assert bad is None and n3 == 0 and probs3


def test_extract_code_and_note_and_json():
    t = "NOTE: try pumps\n```python\ndef apply(city):\n    city.pump(1, 2, m3s=3)\n```"
    assert ev.extract_code(t).startswith("def apply") and ev.first_line(t) == "try pumps"
    assert ev.parse_json('blah ```json\n{"insights": ["a"], "directions": []}\n``` x') == {"insights": ["a"], "directions": []}
    assert ev.check_code("def apply(city):\n    pass\n") is None
    assert "apply" in ev.check_code("x = 1\n")
    assert "SyntaxError" in ev.check_code("def apply(city)\n")


def test_strategy_and_bands():
    items = [{"category": "grey_infrastructure", "usd": 30e6}, {"category": "green_infrastructure", "usd": 5e6}]
    assert ev.strategy_of(items)[0] == "grey"
    assert ev.strategy_of([{"category": "trees", "usd": 1}, {"category": "operations", "usd": 1}])[0] == "mixed"
    assert ev.strategy_of([{"what": "dry floodproofing 4 buildings", "usd": 3}])[0] == "building"
    assert [ev.cost_band(x, 50e6) for x in (1e6, 12e6, 30e6, 45e6)] == [0, 1, 2, 3]


def test_tweak_sites():
    sites = ev.Evolver.tweak_sites(PARENT)
    keys = sorted(k for _, _, k in sites)
    assert keys == ["intake_m3s", "m3s", "size", "volume_m3"]


class FakeInteractions:
    """Stands in for client.interactions: answers each prompt kind like a model would."""

    def __init__(self):
        self.calls = []

    def create(self, model, input, **kw):
        self.calls.append((model, input[:80]))
        if "Return ONLY a JSON object" in input:
            ids = re.findall(r"^(c\d{3}) ", input, re.M)
            out = json.dumps({"insights": ["storage pays back", "walls are wasted"],
                              "directions": [{"parent": ids[0] if ids else "", "directive": "add a pump at the basin"}] * 4,
                              "crossover": {"parents": ids[:2], "idea": "merge"}})
        elif "Improve the PARENT plan" in input:
            m = re.search(r"# PARENT\n.*?```python\n(.*?)```", input, re.S)
            first = m.group(1).splitlines()[0] if m else "def apply(city):"
            out = (f"NOTE: add a pump\n<<<<<<< SEARCH\n{first}\n=======\n{first}\n    city.pump(2000, 2400, m3s=1.5)\n"
                   ">>>>>>> REPLACE")
        else:  # seeds: pairs of identical plans, so deduplication is exercised too
            self.n_seed = getattr(self, "n_seed", 0) + 1
            vol = 8000 + 4000 * (self.n_seed // 2)
            out = f"NOTE: seed\n```python\ndef apply(city):\n    city.storage(1966, 2466, volume_m3={vol}, intake_m3s=2.0)\n```"
        return SimpleNamespace(output_text=out, usage=SimpleNamespace(total_input_tokens=100, total_output_tokens=50))


def test_full_run_with_fake_llm(tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "OUT", tmp_path)
    from agent.events import new_job
    client = SimpleNamespace(interactions=FakeInteractions())
    job = new_job("evolve-test", {})
    e = ev.Evolver(job, generations=1, population=5, sim_workers=3, use_llm=True, import_seeds=False, client=client)
    e.api_doc = "(api docs omitted in test)"
    final = e.run()
    st = json.loads((ev.OUT / job.id / "state.json").read_text())
    assert st["status"] == "awaiting_approval" and final.get("best_id")
    ops = {c["op"] for c in st["candidates"]}
    assert {"seed", "mutate", "crossover", "tweak"} <= ops, ops
    assert any(c["status"] == "duplicate" for c in st["candidates"])  # identical seeds are deduplicated
    assert st["generations"][-1]["insights"] == ["storage pays back", "walls are wasted"]
    assert st["archive"]["cells"], "archive must hold elites"
    assert st["usage"]["gemini-4-argon"]["calls"] >= 1
