import unittest
from unittest.mock import patch
from backend import preventive_work as w,prestorm


class PreventiveWorkTests(unittest.TestCase):
    def setUp(self):
        self.e=prestorm.evidence();self.s=prestorm.initial({},self.e)
        self.s['plan']['resources']['lead_minutes']=120
        self.o=prestorm.sites({},self.e);self.p={'clearing_crews':2,'gust_mps':28}

    def ctx(self,profile='basic',minutes=120,on_site=True):
        ctx=w.context(self.s,self.p,self.o,self.e,w.Settings(profile=profile,minutes=minutes))
        if on_site:
            # Resource-boundary cases isolate work at one staging point. Travel has its own test.
            for c in ctx['resources']['crews']:c['position']=[-122.4,37.8]
            for c in ctx['candidates']:
                c['site']['position']=[-122.4,37.8]
                c['feasibility']=w.evaluate(c,ctx['resources'])
        return ctx

    def result(self,ctx,operations,crews):
        picks=[]
        for operation,crew in zip(operations,crews):
            c=next(c for c in ctx['candidates'] if c['operation_id']==operation and c['id'] not in {x.action_id for x in picks})
            picks.append(w.Selection(action_id=c['id'],crew_id=crew,why='A specific physical work package, with bounded resources.'))
        return w.Comparison(selected=picks,conclusion='The physical packages fit only when their resources and conditions hold.',why_not_do_nothing='Doing nothing leaves the addressed physical mechanisms unchanged.',remaining_gaps='No local loss estimate or field completion is inferred from this comparison.')

    def test_baseline_has_no_invented_loss_or_protection(self):
        p=w.public(self.s,self.p,self.o,self.e)
        self.assertEqual(p['baseline']['labor_minutes'],0)
        self.assertIn('Not estimated',p['baseline']['storm_loss'])
        self.assertEqual(p['totals']['jobs'],0)

    def test_two_simultaneous_crews_use_upper_bound_and_person_minutes(self):
        ctx=self.ctx(minutes=30);r=self.result(ctx,['clear-inlet','secure-items'],['general-1','general-2'])
        jobs=w.schedule(r,ctx)
        self.assertEqual([(j['start'],j['finish']) for j in jobs],[(0,25),(0,30)])
        self.assertEqual(sum(j['labor_minutes'] for j in jobs),110)
        with self.assertRaisesRegex(ValueError,'exceeds'):
            w.schedule(self.result(ctx,['clear-inlet','secure-items'],['general-1','general-1']),ctx)

    def test_partial_sandbag_wall_never_counts_as_completed(self):
        ctx=self.ctx('staged',30)
        c=next(c for c in ctx['candidates'] if c['operation_id']=='sandbag-wall')
        self.assertFalse(c['feasibility']['fits'])
        self.assertEqual(c['minimum_minutes'],70)
        with self.assertRaisesRegex(ValueError,'does not fit'):
            w.schedule(self.result(ctx,['sandbag-wall'],['general-1']),ctx)
        ctx=self.ctx('staged',110)
        self.assertEqual(w.schedule(self.result(ctx,['sandbag-wall'],['general-1']),ctx)[0]['finish'],110)

    def test_qualified_electrician_and_equipment_are_both_required(self):
        ctx=self.ctx('staged',120)
        with self.assertRaisesRegex(ValueError,'qualified crew'):
            w.schedule(self.result(ctx,['backup-pump'],['general-1']),ctx)
        self.assertEqual(w.schedule(self.result(ctx,['backup-pump'],['electrician-1']),ctx)[0]['finish'],80)
        self.s['plan']['resources']['electrician']=0
        c=next(c for c in self.ctx('staged',120)['candidates'] if c['operation_id']=='backup-pump')
        self.assertFalse(c['feasibility']['fits'])

    def test_zero_general_crews_and_clear_sites_are_preserved(self):
        self.s['plan']['resources']['general']=0
        ctx=self.ctx()
        self.assertFalse(any(c['feasibility']['fits'] for c in ctx['candidates']))
        self.s['plan']['tasks']['wind'].update(condition='clear',note='2026-10-25 operator: no current concern')
        self.assertNotIn('secure-items@glass-0',{c['id'] for c in self.ctx()['candidates']})

    def test_structural_and_traffic_approvals_not_created_by_equipment(self):
        ctx=self.ctx('specialists',480)
        for op in ('engineered-support','install-cordon'):
            c=next(c for c in ctx['candidates'] if c['operation_id']==op)
            self.assertFalse(c['feasibility']['fits'])
            self.assertIn('Design/authority not established',c['feasibility']['reasons'])

    def test_recorded_design_prerequisites_can_unlock_qualified_work(self):
        self.s['plan']['tasks']['structure'].update(condition='concern',note='2026-10-25 engineer: support design and all listed prerequisites confirmed',prerequisites_confirmed=True)
        ctx=self.ctx('specialists',480)
        c=next(c for c in ctx['candidates'] if c['operation_id']=='engineered-support')
        self.assertTrue(c['feasibility']['fits'])
        job=w.schedule(self.result(ctx,['engineered-support'],['building-1']),ctx)[0]
        self.assertEqual(job['finish'],420)

    def test_consumable_stock_is_not_double_booked(self):
        ctx=self.ctx('staged',120)
        ctx['resources']['equipment']['tie_down']=10
        with self.assertRaisesRegex(ValueError,'stock exhausted'):
            w.schedule(self.result(ctx,['secure-items','secure-items'],['general-1','general-2']),ctx)

    def test_future_booking_does_not_delay_an_independent_crew(self):
        ctx=self.ctx(minutes=120)
        result=self.result(ctx,['clear-inlet','secure-items','secure-items'],['general-1','general-1','general-2'])
        jobs=w.schedule(result,ctx)
        self.assertEqual([(j['start'],j['finish']) for j in jobs],[(0,25),(25,55),(0,30)])

    def test_plan_stales_on_weather_and_keeps_honest_evidence(self):
        ctx=self.ctx();result=self.result(ctx,['clear-inlet'],['general-1'])
        with patch.object(w.argon,'generate',return_value=(result,{'generated_at':'test','result_sha256':'test'})):
            saved=w.generate(self.s,self.p,self.o,self.e,w.Settings(minutes=120))
        self.s['work_comparison']=saved
        self.assertTrue(w.public(self.s,self.p,self.o,self.e)['current'])
        self.assertFalse(w.public(self.s,{**self.p,'gust_mps':35},self.o,self.e)['current'])
        rows={r['id']:r for r in w.catalog()['operations']}
        self.assertIn('observational',rows['prune-defect']['evidence'].lower())
        self.assertIn('not a',rows['sandbag-wall']['evidence'])

    def test_travel_is_not_free_and_changes_feasibility(self):
        ctx=self.ctx(on_site=False)
        candidate=next(c for c in ctx['candidates'] if c['operation_id']=='clear-inlet')
        for crew in ctx['resources']['crews']:crew['position']=[-122.4,37.78]
        candidate['feasibility']=w.evaluate(candidate,ctx['resources'])
        result=self.result(ctx,['clear-inlet'],['general-1'])
        job=w.schedule(result,ctx)[0]
        self.assertGreater(job['travel_minutes'],0)
        self.assertEqual(job['finish'],job['travel_minutes']+candidate['maximum_minutes'])
        ctx['resources']['minutes']=30
        self.assertFalse(w.evaluate(candidate,ctx['resources'])['fits'])


if __name__=='__main__':unittest.main()
