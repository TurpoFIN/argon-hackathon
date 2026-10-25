import copy
import unittest
from backend import consequence_scenario as c


class ConsequenceScenarioTests(unittest.TestCase):
    def setUp(self):
        self.storm=dict(gust_mps=28,rain_mm_h=85,runoff_coefficient=.9,drain_capacity_lps=12,blockage_pct=20)

    def test_each_hazard_has_a_physical_consequence_and_intervention(self):
        for operation,metric in [('secure-items','broken_panes'),('prune-defect','tree_fallen'),
                                 ('engineered-support','collapse_m2'),('fit-barrier','water_cm'),
                                 ('backup-pump','water_cm'),('clear-route','lane_width_m')]:
            with self.subTest(operation=operation):
                before=c.state(operation,self.storm);after=c.state(operation,self.storm,True)
                self.assertNotEqual(before[metric],after[metric])
                self.assertNotEqual(before['summary'],after['summary'])

    def test_no_storm_does_not_manufacture_prevented_damage(self):
        calm={**self.storm,'gust_mps':0,'rain_mm_h':0}
        for op in ['secure-items','prune-defect','engineered-support','fit-barrier','backup-pump','clear-route']:
            self.assertEqual(c.state(op,calm)['summary'],c.state(op,calm,True)['summary'])

    def test_stronger_storm_can_overwhelm_work(self):
        severe={**self.storm,'gust_mps':55,'rain_mm_h':250}
        self.assertTrue(c.state('prune-defect',severe,True)['tree_fallen'])
        self.assertGreater(c.state('engineered-support',severe,True)['collapse_m2'],0)
        self.assertGreater(c.state('fit-barrier',severe,True)['water_cm'],0)
        self.assertGreater(c.state('backup-pump',severe,True)['water_cm'],0)
        self.assertFalse(c.state('clear-route',severe,True)['lane_open'])

    def test_pair_keeps_forcing_and_provenance(self):
        weather=copy.deepcopy(self.storm)
        work={'operation_id':'backup-pump','site':{'id':'test','position':[-122.3973,37.794]}}
        pair=c.compare(work,weather)
        self.assertEqual(weather,self.storm)
        self.assertEqual(pair['storm_before'],pair['storm_after'])
        self.assertEqual(pair['basis'],'Illustrative extrapolation')
        self.assertFalse(pair['validated'])
        self.assertFalse(pair['before']['grid_on'])
        self.assertFalse(pair['after']['grid_on'])
        self.assertTrue(pair['after']['pump_on'])
        self.assertNotIn('basement',pair['before']['summary'].lower())
        self.assertIn('layout',pair['placement']['asset_basis'])
        self.assertIsNotNone(pair['placement']['building_geometry'])

    def test_lifting_contents_does_not_drain_room(self):
        before=c.state('move-contents',self.storm);after=c.state('move-contents',self.storm,True)
        self.assertEqual(before['water_cm'],after['water_cm'])
        self.assertGreater(before['wet_contents'],after['wet_contents'])

    def test_closed_route_reduces_exposure_not_obstruction(self):
        before=c.state('install-cordon',self.storm);after=c.state('install-cordon',self.storm,True)
        self.assertEqual(before['lane_width_m'],after['lane_width_m'])
        self.assertGreater(before['exposed_passages'],after['exposed_passages'])


if __name__=='__main__':unittest.main()
