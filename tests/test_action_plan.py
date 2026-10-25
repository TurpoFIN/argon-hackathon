import copy
import unittest
from unittest.mock import patch

from backend import action_plan, prestorm


class ActionPlanTests(unittest.TestCase):
    def setUp(self):
        self.catalog=prestorm.evidence()
        self.state=prestorm.initial({},self.catalog)
        self.options=prestorm.sites({},self.catalog)
        self.parameters={'clearing_crews':2,'gust_mps':28}
        self.ctx=action_plan.context(self.state,self.parameters,self.options,self.catalog,[])
        self.chosen=[c for c in self.ctx['candidates'] if c['role']=='general'][:3]
        self.response=action_plan.ActionPlan(strategy='Start from recorded local report locations and verify current conditions.',
            steps=[action_plan.Choice(candidate_id=c['id'],crew=i%2+1,why='Historical reports identify this specific place for a current check.',why_before_next='This is an initial reconnaissance priority, not a predicted failure.') for i,c in enumerate(self.chosen)],
            coverage={h:'Selected work or a qualified inspection request addresses this hazard.' for h in prestorm.HAZARDS})

    def store_result(self):
        with patch.object(action_plan.argon,'generate',return_value=(self.response,{'generated_at':'2026-10-25','result_sha256':'test'})):
            self.state['action_plan']=action_plan.generate(self.state,self.parameters,self.options,self.catalog,[])

    def test_two_initial_stops_then_queue_without_invented_times(self):
        self.store_result()
        result=action_plan.public(self.state,self.parameters,self.options,self.catalog,[])
        self.assertEqual([s['stage'] for s in result['steps']],['start','start','queued'])
        self.assertEqual([s['assignment'] for s in result['steps']],['Crew 1 · first stop','Crew 2 · first stop','Crew 1 · queue position 2'])
        self.assertTrue(all('finish' not in s for s in result['steps']))

    def test_explicit_zero_overrides_scenario_and_requires_request(self):
        self.state['plan']['resources']['general']=0
        for step in self.response.steps: step.crew=None
        self.store_result()
        result=action_plan.public(self.state,self.parameters,self.options,self.catalog,[])
        self.assertEqual(result['resources']['general'],0)
        self.assertTrue(all(s['stage']=='request' for s in result['steps']))

    def test_operator_changes_weather_and_evidence_invalidate_plan(self):
        self.store_result()
        self.assertTrue(action_plan.public(self.state,self.parameters,self.options,self.catalog,[])['current'])
        for change in ('resources','weather','evidence'):
            state=copy.deepcopy(self.state); parameters=dict(self.parameters); catalog=copy.deepcopy(self.catalog)
            if change=='resources':state['plan']['resources']['general']=1
            if change=='weather':parameters['gust_mps']=30
            if change=='evidence':catalog['sources'][0]['finding']='Revised source finding'
            public=action_plan.public(state,parameters,self.options,catalog,[])
            self.assertFalse(public['current'])
            self.assertEqual(public['steps'],[])

    def test_argon_cannot_invent_an_action(self):
        self.response.steps[0].candidate_id='imaginary-generator-delivery'
        with patch.object(action_plan.argon,'generate',return_value=(self.response,{})):
            with self.assertRaisesRegex(ValueError,'unknown action'):
                action_plan.generate(self.state,self.parameters,self.options,self.catalog,[])

    def test_argon_owns_queue_and_cannot_overallocate_crew_ids(self):
        self.response.steps[2].crew=2
        self.store_result()
        result=action_plan.public(self.state,self.parameters,self.options,self.catalog,[])
        self.assertEqual(result['steps'][2]['assignment'],'Crew 2 · queue position 2')
        self.response.steps[0].crew=3
        with patch.object(action_plan.argon,'generate',return_value=(self.response,{})):
            with self.assertRaisesRegex(ValueError,'available general crew'):
                action_plan.generate(self.state,self.parameters,self.options,self.catalog,[])

    def test_nearest_inlet_is_only_a_spatial_candidate(self):
        site=self.options['flood'][0]
        drain={'dpw_node_id':'real-inlet','location':{'coordinates':site['position']}}
        candidates=action_plan.candidates(self.state,self.options,self.catalog,[drain])
        target=next(c for c in candidates if c['id']=='flood:'+site['id'])
        self.assertEqual(target['site']['nearby_inlet']['distance_m'],0)
        self.assertIn('hydraulic connection',target['site']['nearby_inlet']['basis'])
        self.assertIsNone(target['observation'])
        self.assertIn('confirmed',target['follow_up'])

    def test_verified_clear_site_is_not_reassigned_as_a_concern(self):
        self.state['plan']['tasks']['wind'].update(condition='clear',note='2026-10-25 operator inspection: no current concern')
        choices=action_plan.candidates(self.state,self.options,self.catalog,[])
        self.assertNotIn('wind:'+self.state['plan']['tasks']['wind']['site_id'],{c['id'] for c in choices})

    def test_specialist_catalog_never_creates_capacity(self):
        profile=action_plan.profile(self.state,self.parameters)
        self.assertTrue(all(v is None for v in profile['specialists'].values()))
        self.assertIn('power-service',{r['id'] for r in action_plan.resources()['resources']})


if __name__=='__main__':unittest.main()
