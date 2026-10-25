"""Rebuild saved destruction only, optionally comparing a captured prior adapter.

Run as a module. The active scenario reuses flood.bin. Saved plans reuse their
recorded forcing and actions, running only the primary water case used by the
existing layer exporter. No planning calls or image/video exports are made.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault('MPLCONFIGDIR', str(ROOT/'data/building-upgrade/matplotlib'))

from backend import destruction_adapter, destruction_web, preparedness, risk_assets, scenarios
from loop.judge import Judge, PumpedWorld, PUMP_M3S
from loop.layers import forcing_file


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summary(result):
    return dict(buildings_by_ds=result['summary']['buildings_by_ds'],
                building_dollar_lines=[line for line in result['ledger']['lines']
                    if line['cause'] in ('wind_roof_cover', 'wind_structure', 'wind_contents', 'debris_glazing')],
                buildings_priced=len(result['ledger']['by_building']),
                total_usd=result['ledger']['total_usd'],
                trees_into_buildings=result['summary']['trees_into_buildings'],
                roads_blocked=result['summary']['roads_blocked'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--receipt', type=Path, default=ROOT/'evidence/building-destruction-comparison.json')
    args = parser.parse_args()
    assets = risk_assets.load()
    old_assets, old_inputs = None, None
    if args.baseline:
        spec = importlib.util.spec_from_file_location('backend._prior_adapter', args.baseline/'baseline_adapter.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        old_inputs = module.Inputs
        old_assets = json.loads((args.baseline/'baseline_assets.json').read_text())
        old_assets['destruction_inventory'] = assets['destruction_inventory']
    receipt = dict(adapter_sha256=digest(ROOT/'backend/destruction_adapter.py'),
                   engine_sha256=digest(ROOT/'backend/destruction.py'),
                   inventory_sha256=digest(ROOT/'data/risk/assets.json'), cases=[])

    def publish(world, frames, times, folder, label=None, actions=(), evidence=None):
        record = dict(path=str(folder.relative_to(ROOT)), label=label, input=evidence)
        if old_inputs:
            old = old_inputs(old_assets, world)
            _, result = old.run(frames, times, actions)
            record['before'] = summary(result)
        inputs = destruction_adapter.Inputs(assets, world)
        storm, result = inputs.run(frames, times, actions)
        payload = destruction_web.export(storm, label or 'Shared weather', result, inputs)
        if label:
            payload['label'] = label
        prefix = f'{label}_' if label else ''
        target = folder/f'{prefix}destruction.json'
        target.write_text(json.dumps(payload, separators=(',', ':'), allow_nan=False))
        (folder/f'{prefix}destruction_street_trees.json').write_text(json.dumps(
            destruction_web.export_street_trees(inputs), separators=(',', ':'), allow_nan=False))
        reread = json.loads(target.read_text())
        assert reread['summary'] == result['summary']
        assert not any('without assessor values' in note for note in reread['unpriced'])
        record.update(after=summary(result), collision_shape=inputs.grid.shape,
                      collision_cell_m=inputs.grid.dx, water_shape=world.z.shape,
                      output_sha256=digest(target))
        receipt['cases'].append(record)
        args.receipt.write_text(json.dumps(receipt, indent=2)+'\n')
        print(f"{record['path']}/{label or 'active'}: {record.get('before', {}).get('buildings_by_ds')} -> {record['after']['buildings_by_ds']}", flush=True)

    state = json.loads((ROOT/'data/scenario-state.json').read_text())
    folder = ROOT/'data' if state['scenario_id'] == 'baseline' else ROOT/'data/scenarios'/state['scenario_id']
    manifest = json.loads((folder/'manifest.json').read_text())
    world, _ = scenarios.create_world(scenarios.Parameters(**manifest['parameters']))
    times = manifest['times_seconds']
    flood_path = folder/'flood.bin'
    flood_hash = digest(flood_path)
    frames = np.fromfile(flood_path, dtype=np.float32).reshape(len(times), *world.z.shape)
    publish(world, frames, times, folder, evidence=dict(flood_sha256=flood_hash))
    assert digest(flood_path) == flood_hash
    for root in (ROOT/'data/prep-loop/demo6', ROOT/'data/prep-loop/demo6check'):
        for folder in sorted(p for p in root.iterdir() if p.is_dir() and (p/f'{p.name}.json').is_file()):
            log_path = folder/f'{folder.name}.json'
            log = json.loads(log_path.read_text())
            judge = Judge(folder, forcing=forcing_file(folder, log), rain_mm_h=log['parameters']['rain_mm_h'],
                          mode=log.get('mode', 'doorways'), replay_context={'candidates': log['candidates']})
            plans = {key: log[key]['plan'] for key in ('control', 'codex_heuristic') if key in log}
            if log.get('greedy_heuristic'):
                plans['greedy'] = log['greedy_heuristic']['plan']
            plans.update({f"{folder.name}_round{entry['round']}": entry['plan']
                          for entry in log['rounds'] if 'ledger' in entry})
            for label, plan in plans.items():
                base = judge.base
                if plan.get('barriers') or plan.get('pumps'):
                    base = copy.copy(base)
                    base.z = base.z.copy()
                    for barrier in plan.get('barriers', []):
                        for cell in judge.barrier_cells(barrier):
                            base.z[cell] += barrier['height_ft'] * .3048
                    if plan.get('pumps'):
                        base.__class__ = PumpedWorld
                        base.pump_rates = np.zeros_like(base.z)
                        for pump in plan['pumps']:
                            base.pump_rates[judge.pump_cell(pump)] += PUMP_M3S / base.cell ** 2
                sim = preparedness.simulate(base, plan)
                publish(sim['world'], sim['flood'], [m['time_seconds'] for m in sim['frame_metrics']],
                        folder, label, plan.get('actions', ()), dict(log_sha256=digest(log_path),
                        flood_sha256=hashlib.sha256(np.asarray(sim['flood']).tobytes()).hexdigest()))


if __name__ == '__main__':
    main()
