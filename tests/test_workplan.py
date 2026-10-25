import unittest
from pydantic import ValidationError
from backend import workplan


def action(identity,role='general',minutes=20,equipment=None):
    return dict(id=identity,operation_id='latch-windows',role=role,people=2,minutes=[minutes,minutes],
                equipment=equipment or {},site={'position':[0,0]},conditions=['Owner access'])


class WorkPlanTest(unittest.TestCase):
    def setUp(self):
        self.res=dict(minutes=30,crews=[dict(id='A',role='general',people=2,position=[0,0]),
                                      dict(id='B',role='general',people=2,position=[0,0])],equipment={'kit':1,'fitted_barrier':1})

    def test_upper_time_and_return_are_enforced(self):
        row=action('a',minutes=25);row['site']['position']=[1,1]
        receipt=workplan.schedule(['a'],[row],self.res,lambda a,b:3 if a!=b else 0)
        self.assertEqual(receipt['completed'],[])
        self.assertEqual(len(receipt['skipped']),1)

    def test_no_specialist_is_invented(self):
        row=action('a',role='arborist',minutes=10)
        receipt=workplan.schedule(['a'],[row],self.res)
        self.assertFalse(receipt['completed'])

    def test_two_parallel_crews(self):
        rows=[action('a'),action('b')]
        receipt=workplan.schedule(['a','b'],rows,self.res)
        self.assertEqual([r['crew_id'] for r in receipt['completed']],['A','B'])
        self.assertEqual(receipt['crew_minutes'],{'A':20,'B':20})

    def test_shared_reusable_tool_cannot_be_double_booked(self):
        rows=[action('a',equipment={'kit':1}),action('b',equipment={'kit':1})]
        receipt=workplan.schedule(['a','b'],rows,self.res)
        self.assertEqual(len(receipt['completed']),1)

    def test_consumed_device_is_not_reused(self):
        rows=[action('a',minutes=5,equipment={'fitted_barrier':1}),action('b',minutes=5,equipment={'fitted_barrier':1})]
        receipt=workplan.schedule(['a','b'],rows,self.res)
        self.assertEqual(len(receipt['completed']),1)
        self.assertEqual(receipt['stock_remaining']['fitted_barrier'],0)

    def test_unknown_duplicate_ids_rejected(self):
        with self.assertRaises(ValueError):workplan.schedule(['missing'],[],self.res)
        with self.assertRaises(ValueError):workplan.schedule(['a','a'],[action('a')],self.res)

    def test_skipped_work_cannot_cross_simulation_boundary(self):
        a=action('fit-barrier:42',role='building');a['operation_id']='fit-barrier'
        b=action('latch-windows:9')
        receipt=workplan.schedule([a['id'],b['id']],[a,b],self.res)
        plan=workplan.compiled(receipt,[a,b],'test')
        self.assertEqual(plan['action_ids'],[b['id']])
        self.assertEqual(plan['doorway_fd_ids'],[])
        self.assertEqual(plan['pumps'],[])

    def test_selected_devices_compile_to_physics(self):
        a=action('fit-barrier:42');a['operation_id']='fit-barrier'
        b=action('portable-pump:0');b['operation_id']='portable-pump'
        receipt=workplan.schedule([a['id'],b['id']],[a,b],self.res)
        plan=workplan.compiled(receipt,[a,b],'test')
        self.assertEqual(plan['doorway_fd_ids'],[42])
        self.assertEqual(plan['pumps'],[{'lon':0,'lat':0}])
        self.assertEqual(set(plan['action_ids']),{a['id'],b['id']})

    def test_renaming_identical_alternatives_does_not_count_as_search(self):
        with self.assertRaises(ValidationError):
            workplan.Alternatives(plans=[dict(name=n,action_ids=['a'],rationale='test') for n in ['one','two']])


if __name__=='__main__':unittest.main()
