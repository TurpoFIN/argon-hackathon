import unittest
from backend.viewer_actions import states,local_drain

WEATHER={'rain_mm_h':32,'gust_mps':7.88,'wind_mps':5.45,'blockage_pct':65,'drain_capacity_lps':6}
class ActionMechanisms(unittest.TestCase):
    def test_no_tree_or_collapse_benefit_is_invented_in_low_wind(self):
        for op,key in [('prune-defect','tree_fallen'),('engineered-support','collapse_m2')]:
            b,a=states(op,WEATHER,[0,.2],[0,60])
            self.assertEqual([r[key] for r in b],[r[key] for r in a])
            self.assertFalse(b[-1][key])
    def test_real_wind_change_changes_failure_not_treatment_forcing(self):
        w={**WEATHER,'gust_mps':28}
        b,a=states('prune-defect',w,[0,.2],[0,60])
        self.assertTrue(b[-1]['tree_fallen']);self.assertFalse(a[-1]['tree_fallen'])
        self.assertEqual(w['gust_mps'],28)
    def test_backup_cannot_bypass_flooded_supporting_equipment(self):
        b,a=states('backup-pump',WEATHER,[0,1.2],[0,60])
        self.assertFalse(a[-1]['pump_on']);self.assertEqual(a[-1]['water_m'],b[-1]['water_m'])
    def test_undeployed_backup_pump_is_off_even_with_grid_power(self):
        b,a=states('backup-pump',WEATHER,[0,.24],[0,60])
        self.assertFalse(b[-1]['pump_on']);self.assertTrue(a[-1]['pump_on'])
    def test_backup_works_for_an_assumed_protected_chain(self):
        b,a=states('backup-pump',WEATHER,[0,.5],[0,60])
        self.assertFalse(b[-1]['pump_on']);self.assertTrue(a[-1]['pump_on'])
        self.assertLess(a[-1]['water_m'],b[-1]['water_m'])
    def test_clearance_does_not_drain_flooded_roads(self):
        b,a=states('clear-route',WEATHER,[0,.5],[0,60])
        self.assertFalse(a[-1]['lane_open']);self.assertEqual(a[-1]['water_m'],b[-1]['water_m'])
    def test_barrier_overtopping_does_not_protect_contents(self):
        b,a=states('fit-barrier',WEATHER,[0,.8],[0,60])
        self.assertEqual(a[-1]['wet_contents'],b[-1]['wet_contents'])
    def test_added_sink_preserves_nonnegative_water_and_zero_capacity_identity(self):
        depths=[0,.1,.4,.2,0];minutes=[0,15,30,45,60]
        self.assertEqual(local_drain(depths,minutes,0,144),depths)
        for before,after in zip(depths,local_drain(depths,minutes,6,144)):
            self.assertGreaterEqual(after,0);self.assertLessEqual(after,before)
