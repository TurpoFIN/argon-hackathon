import copy
import unittest
from unittest.mock import patch
from pydantic import ValidationError
from backend import prestorm


class PrestormTests(unittest.TestCase):
    def setUp(self):
        self.catalog = prestorm.evidence()
        self.state = prestorm.initial({}, self.catalog)

    def test_missing_inputs_are_not_zero_or_feasible(self):
        rows, reviewed = prestorm.schedule(self.state, self.catalog, {})
        self.assertFalse(reviewed)
        self.assertEqual(len(rows), 6)
        self.assertTrue(all(row['finish'] is None for row in rows))
        self.assertTrue(all('enter time' in row['status'] for row in rows))

    def test_shared_specialist_capacity_and_deadline(self):
        self.state['plan']['resources'].update(lead_minutes=60, building=1)
        for h in ('wind', 'structure'):
            self.state['plan']['tasks'][h]['minutes'] = 40
        rows, _ = prestorm.schedule(self.state, self.catalog, {})
        self.assertEqual(rows[0]['finish'], 40)
        self.assertEqual(rows[2]['status'], 'Does not fit before the storm')
        self.state['plan']['resources']['lead_minutes'] = 80
        rows, _ = prestorm.schedule(self.state, self.catalog, {})
        self.assertEqual(rows[2]['finish'], 80)  # equality is feasible
        self.assertEqual(rows[2]['start'], 40)

    def test_no_specialist_does_not_become_general_crew(self):
        self.state['plan']['resources'].update(lead_minutes=60, general=10, arborist=0)
        self.state['plan']['tasks']['trees']['minutes'] = 20
        rows, _ = prestorm.schedule(self.state, self.catalog, {})
        self.assertEqual(rows[1]['status'], 'Unscheduled: no qualified team available')

    def test_concern_is_not_permission_for_mitigation(self):
        t=self.state['plan']['tasks']['power']
        t.update(condition='concern', note='2026-10-25 operator: transfer switch below marked flood level', minutes=30)
        self.state['plan']['resources'].update(lead_minutes=60,electrician=1)
        rows, _ = prestorm.schedule(self.state, self.catalog, {})
        self.assertTrue(rows[4]['status'].startswith('Hold mitigation'))
        t['prerequisites_confirmed']=True
        rows, _ = prestorm.schedule(self.state, self.catalog, {})
        self.assertEqual(rows[4]['status'],'Preparation scheduled')

    def test_current_condition_requires_observation(self):
        with self.assertRaises(ValidationError):
            prestorm.Task(site_id='glass-0',condition='concern')
        with self.assertRaises(ValidationError):
            prestorm.Task(site_id='glass-0',prerequisites_confirmed=True)

    def test_review_becomes_stale_after_weather_or_input_change(self):
        self.state['review']=dict(order=list(reversed(prestorm.HAZARDS)),fingerprint=prestorm.fingerprint(self.state,{'gust_mps':28}))
        _,valid=prestorm.schedule(self.state,self.catalog,{'gust_mps':28})
        self.assertTrue(valid)
        _,valid=prestorm.schedule(self.state,self.catalog,{'gust_mps':29})
        self.assertFalse(valid)
        self.state['plan']['tasks']['wind']['site_id']='glass-1'
        _,valid=prestorm.schedule(self.state,self.catalog,{'gust_mps':28})
        self.assertFalse(valid)

    def test_local_incidents_remain_reports_and_quantities_not_transferred(self):
        self.assertEqual(self.catalog['validation']['quantified_local_benefits'],0)
        options=prestorm.sites({},self.catalog)
        self.assertEqual(set(options),set(prestorm.HAZARDS))
        self.assertTrue(all(options[h] for h in prestorm.HAZARDS))
        self.assertTrue(all('not' in s['basis'] for s in options['flood']))
        self.assertEqual({h['id'] for h in self.catalog['hazards']},set(prestorm.HAZARDS))
        source_ids={s['id'] for s in self.catalog['sources']}
        self.assertTrue(all(set(h['source_ids'])<=source_ids for h in self.catalog['hazards']))

    def test_unknown_site_missing_hazard_and_duplicate_review_rejected(self):
        plan=prestorm.Plan.model_validate(self.state['plan']);plan.tasks['wind'].site_id='invented'
        with self.assertRaises(ValueError):prestorm.validate(plan,prestorm.sites({},self.catalog))
        del plan.tasks['wind']
        with self.assertRaises(ValueError):prestorm.validate(plan,prestorm.sites({},self.catalog))
        with self.assertRaises(ValidationError):prestorm.Review(order=['wind']*6,reasons={})

    def test_argon_cannot_assign_history_to_an_unobserved_inventory_site(self):
        a={'buildings':[dict(id='B1',position=[-122.4,37.8])]}
        self.state['plan']['tasks']['wind']['site_id']='B1'
        reasons={h:'inspection_gap' for h in prestorm.HAZARDS};reasons['wind']='historical_incident'
        response=prestorm.Review(order=list(prestorm.HAZARDS),reasons=reasons)
        with patch.object(prestorm.argon,'generate',return_value=(response,{})):
            with self.assertRaisesRegex(ValueError,'No relevant historical incident'):
                prestorm.review_plan(self.state,{},prestorm.sites(a,self.catalog))


if __name__=='__main__':unittest.main()
