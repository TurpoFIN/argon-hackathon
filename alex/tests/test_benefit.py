import copy
import json
import unittest
import numpy as np
import test_coupled
from backend import benefit


class BenefitAuditTests(unittest.TestCase):
    setUp=test_coupled.CoupledTests.setUp
    world=test_coupled.CoupledTests.world

    def test_no_ocean_rain_only_matches_control_and_coastal_only_stays_dry(self):
        world=self.world(blockage_pct=80,debris_kg=0)
        rain=np.full_like(self.z,.00003)
        baseline,_=benefit.run(world,rain,-10)
        rain_only,_=benefit.run(world,rain,-10,'rainfall_isolated')
        coast_only,_=benefit.run(world,rain,-10,'coastal_inflow_isolated')
        self.assertEqual(baseline['flood_exposure_m2_min'],rain_only['flood_exposure_m2_min'])
        self.assertEqual(coast_only['flood_exposure_m2_min'],0)
        self.assertGreater(baseline['flood_exposure_m2_min'],0)

    def test_clog_ceiling_preserves_source_and_increases_sink(self):
        world=self.world(blockage_pct=80,debris_kg=0)
        clog=world.clog.copy();parcels=copy.deepcopy(world.parcels)
        baseline,_=benefit.run(world,np.full_like(self.z,.00003),-10)
        clean,_=benefit.run(world,np.full_like(self.z,.00003),-10,'all_initial_clogs_cleared')
        self.assertGreater(clean['gravity_and_pumped_m3'],baseline['gravity_and_pumped_m3'])
        self.assertLess(clean['final_land_water_m3'],baseline['final_land_water_m3'])
        self.assertLess(abs(clean['balance_error_m3']),.01)
        np.testing.assert_array_equal(world.clog,clog);self.assertEqual(world.parcels,parcels)

    def test_pump_is_a_finite_sink_with_a_balanced_water_budget(self):
        world=self.world(debris_kg=0)
        rain=np.full_like(self.z,.00003)
        baseline,_=benefit.run(world,rain,-10)
        pumped,_=benefit.run(world,rain,-10,pump_sites=[(3,3)],pump_lps=100)
        self.assertGreater(pumped['gravity_and_pumped_m3'],baseline['gravity_and_pumped_m3'])
        self.assertLessEqual(pumped['gravity_and_pumped_m3']-baseline['gravity_and_pumped_m3'],.1*3600)
        self.assertGreaterEqual(pumped['final_land_water_m3'],0)
        self.assertLess(abs(pumped['balance_error_m3']),.01)

class PreventiveActionTests(unittest.TestCase):
    setUp=test_coupled.CoupledTests.setUp
    world=test_coupled.CoupledTests.world

    def test_preparation_conserves_removed_clog_and_obeys_work_budget(self):
        from backend.preventive_actions import ActionPlan,implement
        world=self.world(blockage_pct=80,debris_kg=0,clearing_crews=2)
        world.evidence=[]
        fields={'peak':np.full_like(self.z,.12),'flood_duration':np.full_like(self.z,3000.)}
        original=world.clog.copy()
        prepared,pumps,receipt=implement(world,fields,ActionPlan(name='test',strategy='shallow_drain_clearing',crews=2,lead_minutes=30,pump_count=0,pump_lps=100))
        json.dumps(receipt)  # Preserve the actual report serialization boundary.
        self.assertGreater(receipt['removed_clog_kg'],0)
        self.assertAlmostEqual(float((original-prepared.clog).sum()),receipt['removed_clog_kg'])
        self.assertTrue(all(t<=1800 for t in receipt['crew_seconds']))
        self.assertEqual(pumps,[])
        np.testing.assert_array_equal(original,world.clog)
        self.assertTrue(all(a['completed_before_storm_seconds']>=0 for a in receipt['actions']))

    def test_pump_setup_and_insufficient_sites_are_reported(self):
        from backend.preventive_actions import ActionPlan,implement
        world=self.world(blockage_pct=80,debris_kg=0,clearing_crews=2);world.evidence=[]
        fields={'peak':np.full_like(self.z,.12),'flood_duration':np.full_like(self.z,3000.)}
        prepared,pumps,receipt=implement(world,fields,ActionPlan(name='pump',strategy='temporary_pumps',crews=2,lead_minutes=30,pump_count=2,pump_lps=100))
        json.dumps(receipt)
        self.assertEqual(receipt['installed_pumps'],1)  # One real inlet in this fixture, never fabricate another.
        self.assertEqual(len(pumps),1)
        self.assertEqual(receipt['removed_clog_kg'],0)
        self.assertEqual(receipt['actions'][0]['work_seconds'],600)
        self.assertTrue(all(t<=1800 for t in receipt['crew_seconds']))
