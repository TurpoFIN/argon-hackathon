import json
import tempfile
import unittest
import os
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
import numpy as np
from pyproj import Transformer
from backend.coupled import World, route
from backend.scenarios import Parameters
from backend.argon import Zone, DispatchPlan
from backend import argon
from pydantic import ValidationError


class CoupledTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'raw').mkdir()
        transform=Transformer.from_crs(4326,32610,always_xy=True)
        west,north=transform.transform(-122.4,37.8)
        (self.root/'manifest.json').write_text(json.dumps({'grid':{'origin_easting':west,'origin_northing':north}}))
        y,x=np.indices((7,7));inverse=Transformer.from_crs(32610,4326,always_xy=True)
        self.lon,self.lat=inverse.transform(west+(x+.5)*12,north-(y+.5)*12)
        (self.root/'raw/jtgq-b7c5.json').write_text(json.dumps([{'dpw_node_id':'real-fixture-1','drain_type':'inlet','location':{'coordinates':[self.lon[3,3],self.lat[3,3]]}}]))
        self.z=np.ones((7,7));self.empty=np.zeros((7,7),bool);self.zero=np.zeros((7,7))

    def world(self,**params):
        values={'response_mode':'baseline','blockage_pct':0,'debris_kg':12,'clearing_crews':0,**params}
        return World(self.root,Parameters(**values),self.z,self.empty,self.empty,self.lon,self.lat,self.zero,self.zero)

    def test_initial_blockage_matches_slider_including_fully_closed(self):
        for percent in (0,20,65,99.9,100):
            with self.subTest(percent=percent):
                world=self.world(blockage_pct=percent)
                self.assertAlmostEqual(world.blockage()[0]*100,percent)
                self.assertAlmostEqual(world.rates()[3,3],world.p.drain_capacity_lps/1000/144*(1-percent/100))

    def test_route_respects_buildings_and_deep_water(self):
        blocked=self.empty.copy();blocked[3,:]=True
        self.assertEqual(route((0,0),(6,6),blocked,self.zero),[])
        blocked[3,3]=False
        path=route((0,0),(6,6),blocked,self.zero)
        self.assertIn((3,3),path)
        h=self.zero.copy();h[3,3]=.31
        self.assertEqual(route((0,0),(6,6),blocked,h),[])

    def test_debris_decreases_actual_drain_rate_and_conserves_mass(self):
        world=self.world()
        original=world.rates()[3,3]
        world.parcels=[{'id':0,'x':3.,'y':3.,'kg':12.,'release':0,'active':False,'done':False}]
        world.step(5,self.zero)
        self.assertLess(world.rates()[3,3],original/10)
        self.assertEqual(world.deposited_kg,12)
        self.assertAlmostEqual(world.export()['budget']['balance_error_kg'],0)

    def test_crew_cannot_clear_before_arrival_and_stops_at_flood(self):
        world=self.world();world.clog[0]=3.;world.initial_clog_kg=3.
        world.parcels=[]
        world.crews=[{'id':'crew','x':2.,'y':3.,'target':0,'path':[(3,3)],'progress':0.,'state':'traveling','source':'baseline'}]
        world.step(5,self.zero);self.assertEqual(world.cleared_kg,0)
        world.step(10,self.zero)
        self.assertAlmostEqual(world.cleared_kg,(10-12/1.4)*.025)
        world.crews[0].update(x=2.,path=[(3,3)],progress=0.)
        h=self.zero.copy();h[3,3]=.4
        before=world.cleared_kg;world.step(15,h)
        self.assertEqual(world.cleared_kg,before)
        self.assertIsNone(world.crews[0]['target'])
        self.assertAlmostEqual(world.export()['budget']['balance_error_kg'],0)

    def test_schema_rejects_unbounded_perception(self):
        with self.assertRaises(ValidationError):Zone(box=[0,0,1001,500],surface='vegetation',evidence='image')
        with self.assertRaises(ValidationError):DispatchPlan(drain_ids=[],reason='')

    def test_unrecognized_or_duplicate_model_targets_are_rejected(self):
        for ids in [['not-an-asset'], ['known','known']]:
            with self.subTest(ids=ids),patch.object(argon,'generate',return_value=(DispatchPlan(drain_ids=ids,reason='Test'),{})):
                with self.assertRaises(ValueError):argon.plan([{'id':'known'}],[],600)

    def test_blank_key_does_not_mask_selected_vault_secret(self):
        (self.root/'.env').write_text('GEMINI_API_KEY=\nGEMINI_HSEC_SECRET=test-vault-key\n')
        with patch.object(argon,'ROOT',self.root),patch.object(argon,'_vault_cache',{}),patch.dict(os.environ,{},clear=True),patch.object(argon.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='test-secret-value')) as read:
            self.assertEqual(argon.api_key(),'test-secret-value')
            self.assertEqual(argon.api_key(),'test-secret-value')
            read.assert_called_once()
            self.assertNotIn('test-secret-value',(self.root/'.env').read_text())

    def test_argon_ranking_is_executed_through_real_routes(self):
        world=self.world();world.p.response_mode='argon';world.clog[0]=3.
        world.crews=[{'id':'crew','x':2.,'y':3.,'target':None,'path':[],'progress':0.,'state':'idle','source':'baseline'}]
        world.planner=lambda *args:{'drain_ids':['real-fixture-1'],'reason':'Adjacent reachable blocked inlet','interaction_id':'test'}
        with patch('backend.argon.api_key',return_value='test'):
            world.dispatch(600,self.zero)
        self.assertEqual(world.crews[0]['source'],'argon')
        self.assertEqual(world.crews[0]['path'],[(3,3)])
        self.assertEqual(world.events[-1]['planner'],'argon')


if __name__=='__main__':unittest.main()
