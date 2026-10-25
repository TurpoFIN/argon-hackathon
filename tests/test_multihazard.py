"""Independent small boundary cases, not claims of field calibration."""
import unittest
from types import SimpleNamespace
import numpy as np
from scipy.spatial import cKDTree
from backend.multihazard import Model
from backend.risk_assets import FROM


def fixture(gust=0.,tide=1.0,minutes=120):
    n=8;x=552300+np.arange(n)*12;y=4183500-np.arange(n)*12
    xx,yy=np.meshgrid(x,y);lo,la=FROM(xx,yy)
    ocean=np.zeros((n,n),bool);ocean[:,0]=True
    p=SimpleNamespace(wind_mps=gust*.7,gust_mps=gust,wind_from_deg=270.,coastal_rise_m=0.,rain_mm_h=0.,roughness=.035,debris_kg=0.)
    w=SimpleNamespace(z=np.ones((n,n)),walls=np.zeros((n,n),bool),ocean=ocean,lon=lo,lat=la,p=p,
        clog=np.zeros(1),parcels=[],crews=[],dynamics={'rain':np.zeros((n,n)),'tide_base':tide},
        step=lambda *args:None,rates=lambda:np.zeros((n,n)),drain_tree=cKDTree([[4,4]]))
    a=[float(x[1]),float(y[3])];b=[float(x[4]),float(y[3])];c=[float(x[6]),float(y[3])]
    roads=[dict(id='R1',name='Test street',xy=[a,b],length_m=36.,cell=[3,2],position=list(FROM(*a)),geometry={'type':'LineString','coordinates':[list(FROM(*a)),list(FROM(*b))]}),
           dict(id='R2',name='Test street',xy=[b,c],length_m=24.,cell=[3,5],position=list(FROM(*b)),geometry={'type':'LineString','coordinates':[list(FROM(*b)),list(FROM(*c))]})]
    building=dict(id='B1',xy=b,position=list(FROM(*b)),cell=[3,4],area_m2=100.,perimeter_m=40.,height_m=8.,angle_rad=0.,feeder='F1',substation=None,backup_ready=True)
    assets={'buildings':[building],'trees':[],'roads':roads,'substations':[],'power_lines':[]}
    return Model(assets,w,minutes)


class MultiHazardTests(unittest.TestCase):
    def test_calm_sound_case_and_wind_response(self):
        calm=fixture().run([])['metrics'];wind=fixture(gust=80).run([])['metrics']
        self.assertEqual(calm['partial_collapses'],0)
        self.assertEqual(calm['damaged_envelope_m2'],0)
        self.assertGreater(wind['partial_collapses'],0)
        self.assertGreater(wind['damaged_envelope_m2'],0)
        self.assertLess(abs(wind['mass_balance_error_m3']),.01)

    def test_barrier_and_backup_change_distinct_consequences(self):
        model=fixture(tide=1.45)
        base=model.run([])['metrics']
        protected=model.run([{'kind':'protect_entrance','target':'B1'},{'kind':'backup_power','target':'B1'}])['metrics']
        self.assertGreater(base['flooded_entrances'],0)
        self.assertEqual(protected['flooded_entrances'],0)
        self.assertGreater(base['unserved_load_hours'],0)
        self.assertEqual(protected['unserved_load_hours'],0)
        self.assertEqual(base['partial_collapses'],protected['partial_collapses'])

    def test_work_budget_and_type_boundary(self):
        model=fixture(minutes=30)
        outcome=model.execute([{'kind':'brace_structure','target':'B1'}])
        self.assertEqual(outcome['completed'],[])
        self.assertEqual(len(outcome['skipped']),1)
        with self.assertRaises(ValueError):model.execute([{'kind':'prune_tree','target':'B1'}])
        with self.assertRaises(ValueError):model.execute([{'kind':'close_route','target':'R1'}]*2)

    def test_barrier_can_be_overtopped(self):
        m=fixture(tide=1.7)
        r=m.run([{'kind':'protect_entrance','target':'B1'}])['metrics']
        self.assertGreater(r['flooded_entrances'],0)

    def test_cordon_does_not_reduce_physical_damage(self):
        model=fixture(gust=60)
        base=model.run([])['metrics']
        closed=model.run([{'kind':'close_route','target':'R1'}])['metrics']
        self.assertEqual(base['partial_collapses'],closed['partial_collapses'])
        self.assertEqual(base['damaged_envelope_m2'],closed['damaged_envelope_m2'])
        self.assertLessEqual(closed['route_hazard_minutes'],base['route_hazard_minutes'])

    def test_added_anchorage_can_prevent_a_marginal_failure(self):
        avoided=False
        for gust in range(40,81,2):
            m=fixture(gust=gust)
            base=m.run([])['metrics']['partial_collapses']
            braced=m.run([{'kind':'brace_structure','target':'B1'}])['metrics']['partial_collapses']
            self.assertLessEqual(braced,base)
            if braced<base:
                avoided=True;break
        self.assertTrue(avoided,'Anchorage must affect a case near the load/capacity boundary')


if __name__=='__main__':unittest.main()
