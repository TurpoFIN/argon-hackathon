"""Shared consequences on saved depths, with independent strike boundary cases."""
import copy
import json
import unittest
from unittest.mock import patch

import numpy as np

from backend.multihazard import Model, destruction
from backend.risk_assets import FROM
from test_multihazard import fixture


def strike_model(kind='power', offset=0., reach=0.):
    base=fixture(gust=80)
    assets=copy.deepcopy(base.a)
    b=assets['buildings'][0]
    x,y=b['xy']
    b['height_m']=25.  # Isolate tree damage from the low-rise collapse model.
    b['geometry']={'type':'Polygon','coordinates':[[list(FROM(x+dx,y+dy)) for dx,dy in [(-5,-5),(5,-5),(5,5),(-5,5),(-5,-5)]]]}
    tx=x-10-reach
    assets['trees']=[dict(id='T1',xy=[tx,y],position=list(FROM(tx,y)),cell=b['cell'],height_m=20.,crown_m=8.,species='Test tree')]
    assets['power_lines']=[dict(id='OSM1',installation='overhead',kind=kind,
        geometry={'type':'LineString','coordinates':[list(FROM(x-reach,y+offset-5)),list(FROM(x-reach,y+offset+5))]})]
    return Model(assets,base.world)


def depths(model):
    return np.zeros((31,*model.world.z.shape)),list(range(0,3601,120))


class DestructionReplayTests(unittest.TestCase):
    def test_deterministic_and_does_not_step_water_or_mutate_inputs(self):
        model=strike_model();frames,times=depths(model);clog=model.world.clog.copy()
        with patch('backend.multihazard.advance_water',side_effect=AssertionError('Replay stepped water')):
            a=model.replay(frames,times);b=model.replay(frames,times)
        self.assertEqual(a['frames'],b['frames']);self.assertEqual(a['events'],b['events']);self.assertEqual(a['metrics'],b['metrics'])
        np.testing.assert_array_equal(frames,0);np.testing.assert_array_equal(model.world.clog,clog)
        self.assertNotIn('flood',a);self.assertIsNone(a['metrics']['mass_balance_error_m3'])

    def test_power_conductor_strike_disconnects_building(self):
        model=strike_model();result=model.replay(*depths(model))
        event=next(e for e in result['events'] if e['kind']=='line_down')
        self.assertEqual((event['id'],event['line_kind'],event['tree_id']),('OSM1','power','T1'))
        self.assertEqual(len(event['position']),2)
        frame=next(f for f in result['frames'] if f['time_seconds']==event['time_seconds'])
        self.assertIn('B1',frame['unpowered_ids']);self.assertEqual(frame['down_line_ids'],['OSM1'])
        self.assertGreater(result['metrics']['unserved_load_hours'],0)
        self.assertEqual(result['metrics']['lines_down'],1)

    def test_tree_strike_and_fall_metadata(self):
        model=strike_model();result=model.replay(*depths(model))
        event=next(e for e in result['events'] if e['kind']=='tree_strike')
        self.assertEqual((event['tree_id'],event['building_id']),('T1','B1'))
        fall=next(e for e in result['events'] if e['kind']=='tree_fall')
        self.assertEqual((fall['height_m'],fall['species']),(20.,'Test tree'))
        self.assertEqual(fall['fall_dir_deg'],result['destruction']['trees'][0]['fall_dir_deg'])
        self.assertEqual(result['frames'][-1]['struck_building_ids'],['B1'])
        self.assertEqual(result['metrics']['tree_strikes'],1)

    def test_trolley_strike_does_not_disconnect_power(self):
        model=strike_model('trolley');result=model.replay(*depths(model))
        self.assertEqual(result['metrics']['trolley_lines_down'],1)
        self.assertEqual(result['frames'][-1]['unpowered_ids'],[])
        self.assertEqual(result['metrics']['unserved_load_hours'],0)

    def test_service_reach_and_strike_distance(self):
        for reach,expected in [(34.9,['B1']),(35.1,[])]:
            with self.subTest(reach=reach):
                model=strike_model(reach=reach);result=model.replay(*depths(model))
                self.assertEqual(result['frames'][-1]['unpowered_ids'],expected)
        for offset,expected in [(10.4,1),(10.6,0)]:  # Crown radius 4 m + 1.5 m contact allowance, with seeded bearing.
            with self.subTest(offset=offset):
                model=strike_model(offset=offset);result=model.replay(*depths(model))
                self.assertEqual(result['metrics']['lines_down'],expected)

    def test_service_drop_and_backup_with_nearer_unstruck_conductor(self):
        model=strike_model();assets=copy.deepcopy(model.a);x,y=assets['buildings'][0]['xy']
        assets['power_lines'].append(dict(id='OSM2',kind='power',installation='overhead',geometry={
            'type':'LineString','coordinates':[list(FROM(x+1,y-1)),list(FROM(x+1,y+1))]}))
        # Move the struck conductor away; the nearer, unstruck one supplies the building.
        assets['power_lines'][0]['geometry']['coordinates']=[list(FROM(x-9,y-5)),list(FROM(x-9,y+5))]
        assets['power_lines'][1]['geometry']['coordinates']=[list(FROM(x,y+8)),list(FROM(x,y+10))]
        nearest=Model(assets,model.world);result=nearest.replay(*depths(nearest))
        self.assertEqual(result['frames'][-1]['down_line_ids'],['OSM1'])
        # The exact engine also tears the struck building's individual service drop.
        self.assertEqual(result['destruction']['outage']['service_drops_torn'],1)
        self.assertEqual(result['frames'][-1]['unpowered_ids'],['B1'])
        result=model.replay(*depths(model),actions=[dict(kind='backup_power',target='B1')])
        self.assertEqual(result['frames'][-1]['unpowered_ids'],[])

    def test_run_existing_metrics_and_shared_frames(self):
        model=fixture(gust=60);run=model.run([],record=True)
        old={'route_hazard_minutes','isolated_building_minutes','unserved_load_hours','interior_water_m3',
             'trees_failed','partial_collapses','damaged_envelope_m2','flooded_entrances',
             'failed_substations','crew_minutes','completed_actions','mass_balance_error_m3'}
        self.assertTrue(old<=run['metrics'].keys())
        replay=model.replay(run['flood'],[f['time_seconds'] for f in run['frames']])
        self.assertEqual(run['events'],replay['events']);self.assertEqual(run['frames'],replay['frames'])
        for key in old-{'mass_balance_error_m3'}:self.assertEqual(run['metrics'][key],replay['metrics'][key])

    def test_artifact_preserves_positional_export_and_full_inventory(self):
        model=strike_model();artifact=destruction(model,*depths(model))
        json.dumps(artifact,allow_nan=False)
        self.assertEqual(artifact['t'],list(range(0,3601,120)))
        self.assertEqual(len(artifact['spans']),1)
        self.assertEqual(len(artifact['trees'][0]),11)
        self.assertTrue(artifact['tree_strikes'][0][5])
        self.assertEqual(artifact['summary']['trees_failed'],1)

    def test_invalid_timeline(self):
        model=strike_model();frames,times=depths(model)
        with self.assertRaises(ValueError):model.replay(frames,times[:-1])
        with self.assertRaises(ValueError):model.replay(frames,[0]*31)


class DestructionPublicationTests(unittest.TestCase):
    def test_optional_artifact_failure_preserves_scenario(self):
        import tempfile
        from pathlib import Path
        import test_coupled
        from backend import preparedness, scenarios
        fixture_case=test_coupled.CoupledTests();fixture_case.setUp()
        self.addCleanup(fixture_case.doCleanups)
        world=fixture_case.world(debris_kg=0)
        world.dynamics={'rain':np.zeros_like(world.z),'tide_base':-10}
        sim=preparedness.simulate(world,dict(name='control',drain_ids=[],stage_ids=[]),horizon=120)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            np.savez(root/'model-inputs.npz',height=np.zeros_like(world.z))
            source=dict(wind={},metrics={},assumptions=[])
            with patch.object(scenarios,'DATA',root),patch.object(scenarios,'create_world',return_value=(world,source)),patch.object(scenarios,'study',return_value=sim),patch.object(scenarios.risk_assets,'load',side_effect=ValueError('unavailable asset cache')):
                scenarios.calculate(world.p,root/'result')
            manifest=json.loads((root/'result/manifest.json').read_text())
            self.assertEqual(manifest['destruction_error'],'ValueError: unavailable asset cache')
            self.assertTrue((root/'result/flood.bin').is_file())
            self.assertTrue((root/'result/world.json').is_file())


if __name__=='__main__':unittest.main()
