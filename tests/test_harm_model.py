"""Consequence checks distinguish source mechanism reproduction from calibration."""
import unittest
import hashlib
import json
from pydantic import ValidationError
from backend.harm_model import Range,Site,overtopped,ingress,series_system,pump_service,water_control,compare
R=lambda a,b=None:Range(low=a,high=a if b is None else b)

class HarmTests(unittest.TestCase):
    def test_sandy_wave_overtopping_regime(self):
        # FEMA P-942 pp4-22/23: 42-inch gates, stillwater near top,
        # another 1-2 ft of waves. This checks that mean-water-only protection
        # is rejected; it does NOT fit a local damage curve.
        self.assertTrue(overtopped(R(42*.0254),R(.3048,.6096),R(42*.0254)))
        self.assertFalse(overtopped(R(.2),R(0),R(.6)))
        self.assertIsNone(overtopped(R(.2,.8),R(0),R(.6)))

    def test_sandy_series_dependency_regime(self):
        # FEMA P-942: submerged supporting components defeat raised generators.
        self.assertFalse(series_system([True,False,True,True]))
        self.assertIsNone(series_system([True,None,True]))
        s=Site(source_ref='FEMA P-942 mechanism case',generator_available=True,grid_available=False,
          transfer_available=False,fuel_available=True,distribution_available=True,
          pump_available=True,outlet_available=True)
        self.assertFalse(pump_service(s,True))

    def test_positive_flood_ingress_change_needs_all_paths(self):
        s=Site(source_ref='Synthetic boundary test, not storm validation',vertical_datum='shared test datum',water_m=R(.4),wave_crest_m=R(0),
          entry_m=R(.1),barrier_top_m=R(.6),barrier_complete=True,barrier_within_tested_conditions=True,all_paths_protected=True,leakage_control_verified=True)
        self.assertTrue(ingress(s,False));self.assertFalse(ingress(s,True))
        self.assertIsNone(ingress(s.model_copy(update={'all_paths_protected':None}),True))
        self.assertIsNone(ingress(s.model_copy(update={'barrier_within_tested_conditions':None}),True))
        self.assertTrue(ingress(s.model_copy(update={'wave_crest_m':R(.4)}),True))

    def test_missing_data_is_not_no_harm_or_no_difference(self):
        work={'id':'x','operation_id':'fit-barrier','site':{'id':'local'}}
        result=compare(work,{}, {'rain_mm_h':85,'gust_mps':28})
        self.assertIsNone(result['before']);self.assertIsNone(result['after']);self.assertIsNone(result['improvement'])
        self.assertEqual(result['support'],'unavailable');self.assertFalse(result['field_calibrated'])
        self.assertEqual(result['storm_before'],result['storm_after'])

    def test_only_treatment_changes_in_a_pair(self):
        work={'id':'x','operation_id':'fit-barrier','site':{'id':'local'}}
        storm={'rain_mm_h':85}
        fingerprint=hashlib.sha256(json.dumps(storm,sort_keys=True).encode()).hexdigest()
        data={'local':{'source_ref':'Explicit conditional unit-test inputs','storm_fingerprint':fingerprint,'vertical_datum':'shared test datum','water_m':{'low':.4,'high':.4},
          'wave_crest_m':{'low':0,'high':0},'entry_m':{'low':.1,'high':.1},'barrier_top_m':{'low':.6,'high':.6},
          'barrier_complete':True,'barrier_within_tested_conditions':True,'all_paths_protected':True,'leakage_control_verified':True}}
        r=compare(work,data,{'rain_mm_h':85});self.assertEqual(r['improvement'],1);self.assertEqual(r['support'],'conditional')
        self.assertEqual(compare(work,data,{'rain_mm_h':85},False)['improvement'],0)
        # Barrier never repairs glazing or structural vulnerability.
        self.assertEqual(compare({'id':'x','operation_id':'latch-windows','site':{'id':'local'}},data,{})['improvement'],None)
        stale=compare(work,data,{'rain_mm_h':120})
        self.assertIsNone(stale['improvement'])
        self.assertIn('hazard inputs tied to the current storm',stale['missing'])

    def test_missing_datum_and_undated_measurements_are_rejected(self):
        with self.assertRaises(ValidationError):Site(source_ref='Unreferenced elevation',water_m=R(.5))
        with self.assertRaises(ValidationError):Site(source_ref='Claimed measured input',provenance='measured')

    def test_available_backup_is_not_enough_without_inflow_and_duty(self):
        s=Site(source_ref='Synthetic power capacity test, not storm validation',grid_available=False,
          generator_available=True,transfer_available=True,fuel_available=True,distribution_available=True,
          backup_kw=R(20),pump_kw=R(10),pump_available=True,outlet_available=True)
        self.assertTrue(pump_service(s,True));self.assertIsNone(water_control(s,True))

if __name__=='__main__':unittest.main()
