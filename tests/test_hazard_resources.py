"""Paired real-engine boundaries for the operational resource profiles."""
import copy
import unittest

import numpy as np

from backend import hazard_workplans as hw, workplan
from test_multihazard import fixture
from test_destruction_replay import strike_model


def candidate(kind,target,point):
    op,hazard,label,role,people,minutes,eq=hw.OPERATIONS[kind]
    return dict(id=op+':'+target,kind=kind,target=target,operation_id=op,hazard=hazard,label=label,
        role=role,people=people,minutes=[minutes,minutes],equipment=eq,site={'position':point},conditions=[],
        exclusive_group='tree:'+target if hazard=='trees' else None)


def replay_inputs(model,depth=0):
    return np.full((31,*model.world.z.shape),depth),np.arange(0,3601,120)


class ResourceHazards(unittest.TestCase):
    def test_specialists_are_explicit_and_cannot_fit_rapid_profile(self):
        m=fixture();r=hw.ResourceModel(m.a,m.world)
        p=hw.resource_profile(r,'rapid');a=candidate('brace_structure','B1',r.buildings[0]['position'])
        result=hw.evaluate(r,*replay_inputs(r),[a['id']],[a],p)
        self.assertFalse(result['action_ids']);self.assertEqual(len(result['execution']['skipped']),1)
        full=hw.resource_profile(r,'specialists')
        self.assertEqual(sum(c['people'] for c in full['crews']),14)
        self.assertEqual(full['minutes'],480)

    def test_removal_changes_real_tree_and_cascade_while_forcing_is_unchanged(self):
        original=strike_model();m=hw.ResourceModel(original.a,original.world);frames,times=replay_inputs(m)
        a=candidate('remove_tree','T1',m.trees[0]['position']);p=hw.resource_profile(m,'specialists')
        base=hw.evaluate(m,frames,times,[],[a],p);treated=hw.evaluate(m,frames,times,[a['id']],[a],p)
        self.assertGreater(base['metrics']['trees_failed'],treated['metrics']['trees_failed'])
        self.assertGreater(base['metrics']['tree_strikes'],treated['metrics']['tree_strikes'])
        np.testing.assert_array_equal(frames,0)
        self.assertEqual(m.world.p.gust_mps,80)
        self.assertEqual(treated['action_ids'],[a['id']])

    def test_generator_only_restores_critical_load_and_keeps_public_outage(self):
        original=strike_model();m=hw.ResourceModel(original.a,original.world);frames,times=replay_inputs(m)
        a=candidate('backup_power','B1',m.buildings[0]['position']);p=hw.resource_profile(m,'staged')
        base=hw.evaluate(m,frames,times,[],[a],p);backed=hw.evaluate(m,frames,times,[a['id']],[a],p)
        self.assertTrue(backed['action_ids'])
        self.assertEqual(base['destruction']['outage'],backed['destruction']['outage'])
        self.assertGreater(base['metrics']['unserved_load_hours'],backed['metrics']['unserved_load_hours'])
        self.assertEqual(backed['execution']['stock_remaining']['generator_20kw'],1)
        self.assertEqual(backed['execution']['stock_remaining']['fuel_2h'],1)

    def test_entrance_device_keeps_water_field_but_changes_ingress(self):
        f=fixture();m=hw.ResourceModel(f.a,f.world);frames,times=replay_inputs(m,.4)
        a=candidate('protect_entrance','B1',m.buildings[0]['position']);p=hw.resource_profile(m,'staged')
        base=hw.evaluate(m,frames,times,[],[a],p);dry=hw.evaluate(m,frames,times,[a['id']],[a],p)
        self.assertGreater(base['metrics']['interior_water_m3'],dry['metrics']['interior_water_m3'])
        self.assertEqual(dry['metrics']['flooded_entrances'],0)
        np.testing.assert_array_equal(frames,.4)

    def test_overtopped_barrier_receives_no_ingress_credit(self):
        f=fixture();m=hw.ResourceModel(f.a,f.world);frames,times=replay_inputs(m,.8)
        a=candidate('protect_entrance','B1',m.buildings[0]['position']);p=hw.resource_profile(m,'staged')
        base=hw.evaluate(m,frames,times,[],[a],p);wet=hw.evaluate(m,frames,times,[a['id']],[a],p)
        self.assertEqual(base['metrics']['interior_water_m3'],wet['metrics']['interior_water_m3'])

    def test_closure_reduces_exposure_without_claiming_less_damage(self):
        f=fixture();m=hw.ResourceModel(f.a,f.world);frames,times=replay_inputs(m,.4)
        a=candidate('close_route','R1',m.roads[0]['position']);p=hw.resource_profile(m,'staged')
        base=hw.evaluate(m,frames,times,[],[a],p);closed=hw.evaluate(m,frames,times,[a['id']],[a],p)
        self.assertLess(closed['metrics']['route_hazard_minutes'],base['metrics']['route_hazard_minutes'])
        self.assertEqual(base['metrics']['trees_failed'],closed['metrics']['trees_failed'])
        self.assertEqual(base['metrics']['partial_collapses'],closed['metrics']['partial_collapses'])
        self.assertEqual(base['metrics']['interior_water_m3'],closed['metrics']['interior_water_m3'])

    def test_tree_treatments_cannot_spend_twice_on_same_tree(self):
        m=strike_model();p=hw.resource_profile(hw.ResourceModel(m.a,m.world),'specialists')
        a=candidate('prune_tree','T1',m.trees[0]['position']);b=candidate('remove_tree','T1',m.trees[0]['position'])
        for c in (a,b):c['minutes']=[10,10]
        receipt=workplan.schedule([a['id'],b['id']],[a,b],p,lambda a,b:0)
        self.assertEqual(len(receipt['completed']),1)
        self.assertEqual(receipt['skipped'][0]['reason'],'Conflicting treatment of the same asset')

    def test_staged_profile_cannot_claim_tree_work(self):
        m=strike_model();r=hw.ResourceModel(m.a,m.world)
        a=candidate('prune_tree','T1',r.trees[0]['position'])
        receipt=workplan.schedule([a['id']],[a],hw.resource_profile(r,'staged'),lambda a,b:0)
        self.assertFalse(receipt['completed'])

    def test_roof_work_can_avoid_ds4_while_major_damage_remains(self):
        observed=False
        for gust in range(40,81,2):
            f=fixture(gust=gust);m=hw.ResourceModel(f.a,f.world);frames,times=replay_inputs(m)
            a=candidate('brace_structure','B1',m.buildings[0]['position']);p=hw.resource_profile(m,'specialists')
            base=hw.evaluate(m,frames,times,[],[a],p);treated=hw.evaluate(m,frames,times,[a['id']],[a],p)
            if base['metrics']['destroyed_buildings']>treated['metrics']['destroyed_buildings'] and base['metrics']['partial_collapses']==treated['metrics']['partial_collapses']:
                observed=True
                self.assertEqual(base['metrics']['destroyed_buildings']-treated['metrics']['destroyed_buildings'],1)
                break
        self.assertTrue(observed,'DS4-to-DS3 benefit must not disappear into a DS3/4 combined counter')


if __name__=='__main__':unittest.main()
