"""Inventory derivation and real engine collisions on independent small cases."""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from scipy.ndimage import distance_transform_edt
from shapely import contains_xy
from shapely.geometry import box, mapping
from shapely.ops import transform

from backend import building_inventory as inventory, destruction as engine, risk_assets
from backend.destruction_adapter import Inputs
from test_multihazard import fixture


def feature(poly, **props):
    return dict(type='Feature', geometry=mapping(transform(risk_assets.FROM, poly)), properties=props)


def building(poly, bid='B1', height=12.):
    p = poly.representative_point()
    return dict(id=bid, geometry=mapping(transform(risk_assets.FROM, poly)),
                xy=[p.x, p.y], area_m2=poly.area, height_m=height)


def parcel(x, y, **props):
    return dict(type='Feature', geometry=dict(type='Point', coordinates=list(risk_assets.FROM(x, y))), properties=props)


class BuildingInventoryTests(unittest.TestCase):
    def test_large_assessor_download_is_cached_with_provenance(self):
        raw = b'{"features":[]}' + b' ' * 12_000_000
        with tempfile.TemporaryDirectory(dir=risk_assets.ROOT) as folder:
            with patch.object(risk_assets, 'FOLDER', Path(folder)):
                with patch('urllib.request.urlopen', return_value=io.BytesIO(raw)) as fetch:
                    rows, source = risk_assets.fetch('roll.json', 'https://data.sf.gov/roll', max_bytes=24_000_000)
                    cached, again = risk_assets.fetch('roll.json', 'https://data.sf.gov/roll', max_bytes=24_000_000)
                self.assertEqual(fetch.call_count, 1)
                self.assertEqual(rows, cached)
                self.assertEqual(source, again)
                self.assertEqual(len(source['sha256']), 64)

    def test_joined_values_and_ids_geometry_unchanged(self):
        poly = box(552300, 4183400, 552340, 4183430)
        b = building(poly)
        before = copy.deepcopy(b)
        coverage = inventory.enrich([b], [feature(poly, sf16_bldgid='1', hgt_median_m=10,
            hgt_maxcm=1400, gnd_min_m=2.5)], [parcel(552310, 4183410, use_code='COMO',
            property_area=1000, number_of_stories=20, year_property_built=2018,
            assessed_improvement_value=2500000, assessed_fixtures_value=50000)])
        self.assertEqual((b['id'], b['geometry']), (before['id'], before['geometry']))
        self.assertEqual(coverage['buildings_assessor_joined'], 1)
        self.assertEqual((b['height_m'], b['height_max_m'], b['height_source']), (78., 78., 'assessor_stories'))
        self.assertEqual((b['floors'], b['occupancy'], b['year_built'], b['use_code']), (20, 'COM4', 2018, 'COMO'))
        self.assertEqual(b['assessed_improvement_usd'], 2550000)
        self.assertEqual(b['n_parcels'], 1)
        self.assertEqual(b['ground_z'], 2.5)
        self.assertAlmostEqual(b['floor_area_m2'], 24000., places=1)
        self.assertAlmostEqual(b['value_structure'], 144000000, delta=1)
        self.assertEqual(b['value_contents'], b['value_structure'])
        self.assertEqual((b['first_floor_m'], b['ground_retail']), (0., False))
        self.assertTrue(all(k in b for k in ('inventory_basis', 'floor_area_m2', 'height_max_m')))

    def test_nearest_25m_join_and_60m_occupancy_only(self):
        poly = box(552300, 4183400, 552310, 4183410)
        full = [feature(poly, sf16_bldgid='1', hgt_median_m=9)]
        for distance, joined, occ in ((24.9, 1, 'COM1'), (25.1, 0, 'COM1'), (59.9, 0, 'COM1'), (60.1, 0, 'RES3')):
            # Occupancy's 60 m query is to the centroid, while join is to the footprint.
            x = 552310 + distance if distance < 30 else 552305 + distance
            b = building(poly)
            inventory.enrich([b], full, [parcel(x, 4183405, use_code='COMR',
                property_area=20, number_of_stories=900, year_property_built=1950, assessed_improvement_value=10)])
            self.assertEqual(b['n_parcels'], joined)
            self.assertEqual(b['occupancy'], occ)
            self.assertEqual(b['year_built'], 1950 if joined else None)
            self.assertLess(b['floors'], 900)
            self.assertEqual(b['assessed_improvement_usd'], 10 if joined else 0)

    def test_height_fallback_and_missing_full_record(self):
        poly = box(552300, 4183400, 552310, 4183410)
        b = building(poly)
        inventory.enrich([b], [feature(poly, sf16_bldgid='1', hgt_median_m=1, hgt_maxcm=1000)], [])
        self.assertEqual(b['height_m'], 8.)
        self.assertEqual(b['floors'], 3)
        self.assertEqual(b['first_floor_m'], .3)
        missing = building(poly, bid='Bmissing', height=37.25)
        inventory.enrich([missing], [], [])
        self.assertEqual(missing['height_m'], 37.25)
        self.assertEqual(missing['height_source'], 'existing_height')
        self.assertIn('existing_height_fallback', missing['inventory_basis'])
        self.assertEqual(missing['occupancy'], 'COM4')
        self.assertGreater(missing['value_structure'], 0)
        world = fixture().world
        inventory.ground_fallback([missing], world.z, world.lon, world.lat)
        self.assertEqual(missing['ground_z'], 1.)

    def test_known_tower_and_weighted_occupancy_year_median(self):
        x, y, height, name = inventory.KNOWN_TOWERS[0]
        poly = box(x-20, y-20, x+20, y+20)
        b = building(poly)
        inventory.enrich([b], [feature(poly, sf16_bldgid='1', hgt_median_m=5)], [
            parcel(x, y, use_code='COMO', property_area=1000, year_property_built=2017),
            parcel(x+1, y, use_code='MRES', property_area=100, year_property_built=2019)])
        self.assertEqual((b['height_m'], b['height_source']), (height, f'known:{name}'))
        self.assertEqual(b['occupancy'], 'COM4')
        self.assertEqual(b['year_built'], 2018)
        self.assertEqual(b['n_parcels'], 2)

    def test_enriched_fields_reach_engine_and_priced_lines(self):
        model = fixture(gust=100)
        b = model.a['buildings'][0]
        b.update(occupancy='COM4', floors=7, year_built=2018, value_structure=3e6,
                 value_contents=1e6, first_floor_m=0., floor_area_m2=700.)
        inputs = Inputs(model.a, model.world)
        for key in ('occupancy', 'floors', 'year_built', 'value_structure', 'value_contents', 'first_floor_m', 'floor_area_m2', 'height_m'):
            self.assertEqual(inputs.buildings[0][key], b[key])
        _, result = inputs.run(np.zeros((31, *model.world.z.shape)), np.arange(0, 3601, 120))
        self.assertGreater(result['ledger']['by_cause']['wind_structure'], 0)
        self.assertGreater(result['ledger']['by_cause']['wind_contents'], 0)
        self.assertFalse(any('without assessor values' in v for v in result['ledger']['unpriced']))
        json.dumps(result, allow_nan=False)


class CollisionGridTests(unittest.TestCase):
    def inputs(self):
        model = fixture(gust=0)
        # Six metres wide, between both neighbouring 12 m sample columns.
        poly = box(552349, 4183460, 552355, 4183465)
        model.a['buildings'] = [building(poly)]
        return Inputs(model.a, model.world), poly

    def test_four_metre_footprint_ids_road_mask_and_host_water_mapping(self):
        inputs, poly = self.inputs()
        grid = inputs.grid
        self.assertEqual(grid.dx, 4.)
        self.assertGreater(grid.shape[0], inputs.world.z.shape[0])
        xy = grid.xy.reshape(*grid.shape, 2)
        inside = contains_xy(poly, xy[..., 0], xy[..., 1])
        self.assertGreater(inside.sum(), 0)
        self.assertTrue(np.all(grid.bldg_id[inside] == 0))
        city = engine._City(grid)
        expected = (grid.cover == 0) & (distance_transform_edt(grid.bldg_id < 0) * 4 >= 6)
        np.testing.assert_array_equal(city.roadway, expected)
        depths = np.arange(inputs.world.z.size).reshape(inputs.world.z.shape)
        frames = np.stack([depths, depths + 1])
        storm = SimpleNamespace(depth_frames=frames, t=np.array([0., 120.]))
        water = grid.water(storm)
        ix, iy = grid.host_cell(xy[..., 0], xy[..., 1])
        np.testing.assert_array_equal(water.max_depth, depths[iy, ix] + 1)
        np.testing.assert_array_equal(water.k_max_depth, 1)
        np.testing.assert_array_equal(storm.depth_frames, frames)

    def test_taller_overlap_wins_and_tiny_footprint_is_stamped(self):
        inputs, poly = self.inputs()
        tall = building(poly, 'B2', 40)
        small = building(box(552365, 4183421, 552365.5, 4183421.5), 'B3')
        assets = {**inputs.world.__dict__.get('assets', {}), 'buildings': [tall, building(poly), small], 'trees': []}
        grid = Inputs(assets, inputs.world).grid
        ix, iy = grid.cell(*tall['xy'])
        self.assertEqual(grid.bldg_id[iy, ix], 0)
        self.assertEqual(grid.bldg_h[iy, ix], 40)
        ix, iy = grid.cell(*small['xy'])
        self.assertEqual(grid.bldg_id[iy, ix], 2)

    def test_tree_hits_narrow_building_missed_by_twelve_metre_grid(self):
        inputs, poly = self.inputs()
        world = inputs.world
        x, y = risk_assets.TO(world.lon, world.lat)
        coarse_id = np.where(contains_xy(poly, x, y), 0, -1)
        self.assertFalse(np.any(coarse_id >= 0))
        coarse = SimpleNamespace(bldg_id=coarse_id, bldg_h=np.zeros_like(x),
            cover=np.where(world.ocean, 3, 0), dx=12., x0=x.min(), y0=y.min(), cell=inputs.grid.host_cell)
        storm = SimpleNamespace(name='test', t=np.array([0., 120., 240.]),
            gust_ms=np.zeros(3), mean_ms=np.zeros(3), wind_dir_deg=270.,
            wind_dir_t=np.full(3, 270.), dt_frame_s=120., rain_mm_h=np.zeros(3))
        trees = [dict(id=0, x=552333., y=4183462., height_m=28., dbh_cm=40., crown_m=4.)]
        results = []
        for grid in (coarse, inputs.grid):
            results.append(engine.run_destruction(storm, grid=grid, assets=inputs.assets,
                buildings=inputs.buildings, trees=trees, cars=[],
                tree_result=[dict(id=0, fail_t=120., fall_dir_deg=90., mode='uproot')]))
        self.assertEqual(results[0]['tree_strikes'], [])
        self.assertEqual(len(results[1]['tree_strikes']), 1)
        self.assertEqual(results[1]['tree_strikes'][0]['bldg_id'], 0)


if __name__ == '__main__':
    unittest.main()
