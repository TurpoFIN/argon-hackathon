"""Measure the current wind model's scope; ask Argon to critique a broader design.

This does not introduce uncalibrated damage, casualty, or service-loss estimates.
Run from the repository root: .venv/bin/python scripts/audit_wind_scope.py
"""
import copy
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from backend import argon
from backend.benefit import ROOT, load_baseline, run
from backend.simulation import potential_wind


class ActionFamily(BaseModel):
    model_config = ConfigDict(extra='forbid')
    hazard_chain: str
    preventive_actions: list[str] = Field(min_length=1, max_length=5)
    measured_outcome: str
    essential_missing_inputs: list[str] = Field(min_length=1, max_length=5)
    realistic_lead_time: str


class Redesign(BaseModel):
    model_config = ConfigDict(extra='forbid')
    diagnosis: str
    families: list[ActionFamily] = Field(min_length=4, max_length=6)
    first_integrated_experiment: str
    falsification_tests: list[str] = Field(min_length=3, max_length=8)
    claims_we_cannot_make: list[str] = Field(min_length=3, max_length=8)


class RefinedDesign(BaseModel):
    model_config = ConfigDict(extra='forbid')
    historical_lesson: str
    corrected_priorities: list[str] = Field(min_length=3, max_length=7)
    first_slice: str
    matched_experiment: str
    rejection_tests: list[str] = Field(min_length=3, max_length=7)
    unresolved_inputs: list[str] = Field(min_length=2, max_length=6)


class StructuralReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mechanisms: list[str] = Field(min_length=3, max_length=5)
    feasible_actions_and_horizons: list[str] = Field(min_length=3, max_length=5)
    required_inputs: list[str] = Field(min_length=3, max_length=5)
    acceptance_tests: list[str] = Field(min_length=3, max_length=5)
    limitations: str


SOURCES = [
    {'url': 'https://www.weather.gov/mob/wind_mitigation',
     'fact': 'NWS identifies pre-storm tree maintenance, removing loose objects, protecting windows, securing doors and maintaining roofs. This is action evidence, not a quantified effectiveness model.'},
    {'url': 'https://sanfrancisco.granicus.com/TranscriptViewer.php?clip_id=44497&view_id=87',
     'fact': 'SF Building Inspection Commission, 20 Sep 2023: investigation and recommendations concerning March 2023 high-rise glass failures.'},
    {'url': 'https://www.wje.com/knowledge/webinars/detail/wind-events-and-glass-breakage-lessons-learned-in-san-francisco',
     'fact': 'Engineering investigation of two March 2023 SF wind events, glazing failures and facade inspections. Generic residential shutter advice is not a high-rise repair model.'},
    {'url': 'https://nvlpubs.nist.gov/nistpubs/gcr/2023/NIST.GCR.23-045-upd1.pdf',
     'fact': 'NIST performance-based wind design workshop identifies windborne debris and water infiltration after envelope failure as coupled research needs. Search-index excerpt, not a full reviewed technical specification.'},
    {'url': 'https://onesanfrancisco.org/sites/default/files/inline-files/2025%20HCR_CalOES_FEMA_Mar10.pdf',
     'fact': 'SF 2025 hazard plan describes March 21 2023 fatalities, tree failures, glass failures and transport disruption. Search-index excerpt; full 40 MB document not downloaded.'},
]


def array_hash(fields):
    h = hashlib.sha256()
    for name in ('peak', 'flood_duration', 'final'):
        h.update(name.encode())
        h.update(np.ascontiguousarray(fields[name]).tobytes())
    return h.hexdigest()


def refine():
    """Review new forensic evidence without repeating the numerical trials."""
    path = ROOT / 'evidence/wind-scope-audit.json'
    report = json.loads(path.read_text())
    source = {
        'url': 'https://media.api.sf.gov/documents/BIC_Meeting_092023_-_Item_9.pdf',
        'fact': 'Official SF BIC presentation, 20 Sep 2023, pages 4-8: 31 broken panes investigated in seven buildings; 30 had likely causes identifiable and mitigable before the storms. Thermal stress, debris impact, window hardware and contamination; wind loads very low probability. This is not a measured 97% benefit for a new intervention.'}
    corrections = [
        '900902 m3 net coastal exchange includes fixed ocean-cell storage. It is not land inflow or proof of coastal dominance. Prior source-isolation tests were rain-dominated.',
        'The no-loose-debris cases jointly change wind AND loose material relative to the original. Do not claim a separately tested zero-wind-with-debris effect.',
        'Do not compare a 24-hour four-crew intervention to a 30-minute two-crew baseline and call the difference action effectiveness. Equal budgets AND equal preparation horizons within each comparison.',
        '18 m/s is not an established safe cutoff here. Equipment-specific rated limits, competent work method and local operating rules are missing inputs.',
        'A closure need not reduce exposure or increase delays in every world. A stronger gust need not break a sound component. Do not hard-code desirable treatment effects or force a tradeoff that has not occurred.',
        'Temporary film is not a generic valid facade repair. The WJE source describes its failures. Inspection alone changes information; only completed, appropriate remedial action changes physical condition.',
        'The main people implementation uses storm-only population randomness despite a stale docstring. Audit actual dependencies rather than reporting the comment as a bug.'
    ]
    report['sources'].append(source)
    report['review_corrections'] = corrections
    prompt = (
        'Refine your proposed redesign using the new primary forensic evidence and mandatory numerical corrections. '
        'This is advance preparedness for the current Embarcadero area. Choose the first useful cross-hazard slice, not a full-city mega-project. '
        'Separate conditions observed historically, current unknown condition, and explicitly conditional scenarios. '
        'No arbitrary reduction factors, universal safety cutoffs, guaranteed failure counts, or unvalidated casualty/savings numbers. '
        'An inspection is an information action with cost and possibly no actionable finding. How does it compete with known preventive work? '
        'Keep all portfolio comparisons resource- and horizon-matched and preserve optional no-action outcomes. '
        'Return concise, concrete priorities and rejection tests.\n' + json.dumps({
            'new_primary_evidence': source, 'mandatory_corrections': corrections,
            'earlier_design': report['argon_review'], 'measured_findings': report['findings']}))
    refined, receipt = argon.generate(RefinedDesign, prompt, thinking='medium', max_tokens=3800, timeout=120)
    report['argon_refined_design'] = refined.model_dump()
    report['argon_refinement_receipt'] = {'model': argon.MODEL, **receipt}
    path.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'refined_design': report['argon_refined_design'],
                      'receipt': report['argon_refinement_receipt']}, indent=2), flush=True)


def review_structures():
    """Incorporate the user's explicit structural-collapse requirement."""
    path = ROOT / 'evidence/wind-scope-audit.json'
    report = json.loads(path.read_text())
    sources = [
        'https://basc.pnnl.gov/resource-guides/continuous-load-path-provided-connections-roof-through-wall-foundation',
        'https://www.fema.gov/sites/default/files/2020-08/fema55_voli_combined.pdf',
        'https://www.safework.nsw.gov.au/hazards-a-z/high-winds-in-construction',
    ]
    prompt = (
        'The user explicitly adds: "And structures to collapse" to the advance-preparedness scope. '
        'Review this requirement concisely. Cover roofs, load-bearing walls, partial/total building collapse and temporary structures, '
        'not just glazing. Established source mechanisms: PNNL continuous roof-wall-foundation load paths; FEMA P55 erosion/scour '
        'undermining and wind load-path failure; SafeWork NSW scaffold collapse and structure/equipment-specific wind limits. '
        'These source summaries establish mechanisms, not collapse predictions for an SF high-rise. '
        'Couple wind suction/lateral loads, internal pressure after an opening, flood forces and loss of support. '
        'Keep nonstructural damage separate. Define required condition/capacity evidence, feasible preventive actions and lead times, '
        'shared debris/flow/access consequences and falsification tests. No invented probabilities, wind cutoffs, '
        'damage discounts, or assumption that the current wind/water solver provides structural response, waves or scour. '
        'A sound structure need not fail. Exclusion/relocation reduces exposure, never physical collapse. '
        'This is a design addendum, not an implemented structural model.\n' + json.dumps({'sources': sources}))
    result, receipt = argon.generate(StructuralReview, prompt, thinking='medium', max_tokens=2800, timeout=120)
    report['structural_addendum'] = {'user_requirement': 'Include structures collapsing',
        'sources': sources, 'review': result.model_dump(),
        'receipt': {'model': argon.MODEL, **receipt}, 'runtime_implemented': False}
    path.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report['structural_addendum'], indent=2), flush=True)


def main():
    output = ROOT / 'evidence/wind-scope-audit.json'
    world, rain, tide, baseline = load_baseline()
    cases = [
        ('original', 20., 28., 168., True),
        ('gust_only_50', 20., 50., 168., True),
        ('strong_reversed_mean', 35., 50., 348., True),
        ('no_loose_debris_calm', 0., 0., 168., False),
        ('no_loose_debris_strong', 35., 50., 348., False),
    ]
    report = {'purpose': 'Scope audit, not a multi-hazard effectiveness claim',
              'source_scenario': baseline['source_scenario'], 'sources': SOURCES,
              'cases': [], 'baseline_parameters': baseline['parameters']}
    for name, mean, gust, direction, debris in cases:
        w = copy.copy(world)
        w.p = copy.copy(world.p)
        w.p.wind_mps, w.p.gust_mps, w.p.wind_from_deg = mean, gust, direction
        w.u, w.v = potential_wind(w.walls, mean, direction, 12)
        metrics, fields = run(w, rain, tide, case=name,
                              parcels=None if debris else [])
        metrics.update(mean_mps=mean, gust_mps=gust, direction_from_deg=direction,
                       loose_debris=debris, field_sha256=array_hash(fields))
        report['cases'].append(metrics)
        output.write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(metrics), flush=True)
    a, b, c, d, e = report['cases']
    assert a['field_sha256'] == b['field_sha256'], 'Gust unexpectedly changed current physics; inspect before interpreting.'
    assert d['field_sha256'] == e['field_sha256'], 'A non-debris wind coupling now exists; re-audit the source.'
    assert abs(a['flood_exposure_m2_min'] - baseline['control']['flood_exposure_m2_min']) < .1
    report['findings'] = {
        'gust_only_changes_numerical_outcome': False,
        'wind_changes_numerical_water_without_loose_debris': False,
        'strong_reversed_mean_exposure_change_pct':
            (c['flood_exposure_m2_min'] / a['flood_exposure_m2_min'] - 1) * 100,
        'absent_outcomes': ['glazing/roof damage', 'tree failure', 'person exposure/injury',
                           'power loss', 'transport/network disconnection', 'business interruption'],
        'interpretation': 'Absent outcomes are unmodeled, never zero. Wind/debris motion has only a drain-clogging consequence here.'}
    report['implementation_hashes'] = {
        str(p): hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
        for p in ('backend/simulation.py', 'backend/coupled.py', 'backend/planning.py', 'backend/benefit.py')}
    output.write_text(json.dumps(report, indent=2) + '\n')
    context = {
        'measured_audit': report,
        'current_action_contract': '3 plans including no-prep; drain IDs plus one inlet-proxy stage ID per crew; 2 crews, 30 min. Rejects other actions.',
        'user_intent': 'Advance preparedness simulation experiments for an SF urban area. User rejects drain-only scope and asks to think through wind and other material benefits.',
        'parallel_work': 'Coastal uncertainty/backtesting is being added separately. Do not prescribe a duplicate forecast project.',
        'existing_main': 'origin/main at 88cb619 contains a separate SoMa/Mission Bay grid, real street-tree inventory, synthetic parked cars, Hazus flood damage tables, city action/cost API, people and response code. Referenced sim/wind.py and sim/trees.py are not in that commit. Different extent/resolution/history: audit and port contracts, do not blindly merge or claim this powers the Embarcadero app.',
    }
    prompt = (
        'You are Argon, reviewing an urban advance-preparedness simulator. Think through a useful multi-hazard redesign, not another drain optimization. '
        'Use the measured evidence and supplied source summaries. Do not infer casualty counts, failure probabilities, asset conditions, grid topology or savings from absent data. '
        'Separate exposure reduction (closures/relocation) from physical damage reduction. Couple wind, wet soil, debris, envelope failure, rain ingress, power and access. '
        'Distinguish actions feasible in 30 minutes from 24-72 hours and capital projects. Include labor skills, travel, setup, safe cutoff, asset-owner authority, inventories and network displacement costs. '
        'Suggest a smallest integrated experiment where competing actions under equal resources can alter a meaningful metric, with clear requirements and falsifiable tests. '
        'Do not output made-up benefits or recommend arbitrary percent-damage multipliers. Give a concrete, candid bounded design.\n' + json.dumps(context)
    )
    proposal, receipt = argon.generate(Redesign, prompt, thinking='medium', max_tokens=6500, timeout=120)
    report['argon_review'] = proposal.model_dump()
    report['argon_receipt'] = {'model': argon.MODEL, **receipt}
    output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'argon_receipt': report['argon_receipt'], 'findings': report['findings'],
                      'argon_review': report['argon_review']}, indent=2), flush=True)


if __name__ == '__main__':
    if sys.argv[1:] == ['--refine']:
        refine()
    elif sys.argv[1:] == ['--structures']:
        review_structures()
    elif not sys.argv[1:]:
        main()
    else:
        raise SystemExit('usage: audit_wind_scope.py [--refine | --structures]')
