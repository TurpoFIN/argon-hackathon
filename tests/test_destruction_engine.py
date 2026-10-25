"""Original destruction acceptance cases, on a small explicit city and the host adapter.

The old 2022/311 calibration-run and cached LBM checks require the other city's
inputs. Here the archived calibration constants and the host wind hook are checked.
"""
import copy
import json
import unittest
from types import SimpleNamespace

import numpy as np

from backend import destruction as D
from backend.destruction_adapter import Inputs
from backend.destruction_web import export, export_street_trees
from test_multihazard import fixture


def city(gust=50, plan=None, forced=False):
    t=np.arange(0,3601,120,dtype=float)
    storm=SimpleNamespace(name='test',t=t,gust_ms=gust*(.6+.4*np.sin(np.pi*t/3600)),
        mean_ms=np.full(31,gust*.7),wind_dir_deg=270.,wind_dir_t=np.full(31,270.),
        dt_frame_s=120.,rain_mm_h=np.full(31,32.))
    grid=SimpleNamespace(x0=0.,y0=0.,dx=1.,bldg_id=np.full((100,100),-1),bldg_h=np.zeros((100,100)),cover=np.zeros((100,100)))
    grid.bldg_id[36:45,58:67]=0;grid.bldg_h[36:45,58:67]=12
    buildings=[dict(id=0,x=62.,y=40.,height_m=12.,floors=4,area_m2=81.,footprint=[[58,36],[67,36],[67,45],[58,45],[58,36]])]
    trees=[dict(id=0,x=35.,y=20.,height_m=25.,dbh_cm=60.,crown_m=8.,species='Quercus agrifolia'),
           dict(id=1,x=48.,y=40.,height_m=20.,dbh_cm=30.,crown_m=6.,species='Platanus')]
    poles=[dict(id=i,x=x,y=20.,spans=[j for j in range(2) if i in (j,j+1)]) for i,x in enumerate((40.,60.,80.))]
    spans=[dict(id=i,line_id=0,a=[40.+i*20,20.],b=[60.+i*20,20.],pole_a=i,pole_b=i+1,length_m=20.) for i in range(2)]
    assets=dict(power_poles=poles,power_spans=spans,power_lines=[dict(id=0,spans=[0,1])],
        ocs_spans=[dict(id=0,a=[40,20],b=[60,20],section=0,street='Test',mode='trolleybus',length_m=20)],
        streetlights=[dict(id=0,x=60,y=25)],signals=[dict(id=0,x=62,y=28)],shelters=[dict(id=0,x=52,y=40)],masts=[])
    failures=[dict(id=i,fail_t=600.,fall_dir_deg=90.,mode='uproot') for i in range(2)] if forced else None
    return D.run_destruction(storm,plan=plan,assets=assets,buildings=buildings,trees=trees,cars=[],grid=grid,tree_result=failures),storm


class DestructionEngineTests(unittest.TestCase):
    def test_deterministic_bit_identical(self):
        self.assertEqual(D.result_hash(city()[0]),D.result_hash(city()[0]))

    def test_calm_storm_breaks_nothing(self):
        r,_=city(gust=0)
        for key in ('trees_failed','buildings_damaged','power_poles_down'):
            self.assertEqual(r['summary'][key],0)
        self.assertEqual(r['ledger']['total_usd'],0)

    def test_bigger_storm_breaks_more(self):
        a,_=city(gust=30);b,_=city(gust=80)
        for key in ('trees_failed','buildings_damaged','power_spans_down','ocs_spans_down','signals_damaged'):
            self.assertGreaterEqual(b['summary'][key],a['summary'][key],key)
        self.assertGreater(b['ledger']['total_usd'],a['ledger']['total_usd'])

    def test_power_cascade_tree_to_outage_to_dark_signals(self):
        r,_=city(gust=0,forced=True)
        self.assertGreater(r['summary']['power_spans_down'],0)
        self.assertGreater(r['summary']['customers_out'],0)
        self.assertGreater(r['summary']['signals_dark'],0)
        self.assertGreater(r['summary']['streetlights_dark'],0)
        self.assertGreater(r['ledger']['by_cause']['power_outage'],0)
        self.assertEqual(r['power_poles'][0]['cause'],'tree')
        for line in r['power_lines_out']:
            self.assertLessEqual(min(s['t'] for s in r['power_spans'] if s['line_id']==line['id']),line['t'])

    def test_undergrounding_removes_line_exposure(self):
        r,_=city(gust=0,forced=True,plan={'undergrounded_lines':[0]})
        self.assertEqual(r['summary']['power_lines_out'],0)

    def test_removing_failed_trees_prevents_their_strikes(self):
        a,_=city(gust=80)
        ids=[f['id'] for f in a['trees']]
        self.assertTrue(ids)
        b,_=city(gust=80,plan={'tree_actions':{i:'remove' for i in ids}})
        self.assertEqual(b['trees'],[])
        self.assertEqual(b['tree_strikes'],[])
        self.assertFalse(any(x.get('tree_id') in ids for x in b['power_spans']))

    def test_events_sorted_and_timeline_monotone(self):
        r,_=city(gust=80)
        ts=[e['t'] for e in r['events']]
        self.assertEqual(ts,sorted(ts))
        for values in r['timeline'].values():self.assertTrue(np.all(np.diff(values)>=0))

    def test_debris_lands_downwind(self):
        r,st=city(gust=80)
        self.assertTrue(r['debris'])
        for d in r['debris']:
            dx,dy=d['x1']-d['x0'],d['y1']-d['y0']
            if np.hypot(dx,dy)<2:continue
            self.assertLess(abs((np.degrees(np.arctan2(dx,dy))-90+180)%360-180),75)

    def test_merge_ledger_adds_without_touching_damage_causes(self):
        r,_=city(gust=80)
        ledger=dict(total_usd=1000.,by_cause={'flood_structure':1000.},by_building={},lines=[],sources=[])
        D.merge_ledger(ledger,r)
        self.assertEqual(ledger['by_cause']['flood_structure'],1000)
        self.assertAlmostEqual(ledger['total_usd'],1000+r['ledger']['total_usd'],places=2)

    def test_every_priced_line_has_source_and_values_remain_unpriced(self):
        r,_=city(gust=80)
        for line in r['ledger']['lines']:
            if line['usd'] is not None and line['usd']>0:self.assertTrue(line['source'] and not line['source'].startswith('unpriced'))
        self.assertTrue(r['ledger']['unpriced'])
        self.assertIsNone(r['ledger']['by_cause']['wind_structure'])

    def test_incidents_and_service_drop(self):
        r,_=city(gust=0,forced=True)
        self.assertTrue(r['road_blocks']);self.assertTrue(r['tree_strikes'])
        self.assertTrue(all(x['tree_id'] in {0,1} for x in r['tree_strikes']))
        self.assertTrue(all(x['length_m']>=3 for x in r['road_blocks'] if x['cause']!='power_wire'))

    def test_host_grid_orientation_wind_and_exact_export(self):
        model=fixture(gust=20);inputs=Inputs(model.a,model.world)
        b=inputs.buildings[0];ix,iy=inputs.grid.cell(b['x'],b['y'])
        self.assertEqual((int(iy),int(ix)),(3,4))
        for row in (1,6):
            x,y=inputs.grid.xy.reshape((*model.world.z.shape,2))[row,2]
            ix,iy=inputs.grid.cell(x,y);self.assertEqual(int(iy),row)
        st,r=inputs.run(np.zeros((31,*model.world.z.shape)),range(0,3601,120))
        self.assertEqual(st.gust_ms[15],model.world.p.gust_mps)
        self.assertIn('potential_wind',r['wind_source'])
        self.assertTrue(any('no velocities' in x for x in r['notes']))
        payload=export(st,'test',r,inputs)
        self.assertTrue({'trees','buildings','poles','spans','ocs','lights','signals','shelters','debris','road_blocks','tree_strikes','outage','customers_t','timeline','summary','ledger_total_usd','by_cause','heights','gust','dir_from','t','masts'}<=payload.keys())
        json.dumps(payload,allow_nan=False)
        self.assertEqual(len(export_street_trees(inputs)['trees']),len(model.trees))

    def test_archived_calibration_and_explicit_action_mapping(self):
        p=D.params()['tree_fallback']
        self.assertGreater(p['strength_scale'],0);self.assertGreater(p['load_scale'],0)
        model=fixture();i=Inputs(model.a,model.world)
        mapped=i.plan([dict(kind='brace_structure',target='B1'),dict(kind='backup_power',target='B1'),dict(kind='close_route',target='R1')])
        self.assertNotEqual(D.plan_hash(mapped),D.plan_hash({}))
        self.assertEqual(mapped['roof_retrofit'],[0]);self.assertEqual(mapped['backup_power'],[0]);self.assertEqual(mapped['closed_routes'],['R1'])


if __name__=='__main__':unittest.main()
