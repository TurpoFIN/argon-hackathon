import unittest
import numpy as np
from backend.simulation import Budget, advance_water, potential_wind


class WaterTests(unittest.TestCase):
    def test_closed_domain_conserves_and_stays_positive(self):
        rng = np.random.default_rng(4)
        z = rng.uniform(0, 3, (16, 20))
        h = rng.uniform(0, 0.4, z.shape)
        wall = np.zeros(z.shape, bool)
        wall[5:10, 8:12] = True
        before = h.sum()
        for _ in range(100):
            advance_water(h, z, wall, np.zeros_like(wall), np.zeros_like(h), np.zeros_like(h), 0, 12, 1, Budget())
        self.assertAlmostEqual(float(h.sum()), float(before), places=9)
        self.assertGreaterEqual(float(h.min()), 0)

    def test_lake_at_rest_has_no_spurious_flow(self):
        z = np.tile(np.linspace(0, 1, 12), (10, 1))
        h = 2 - z
        initial = h.copy()
        advance_water(h, z, np.zeros(z.shape, bool), np.zeros(z.shape, bool), np.zeros_like(h), np.zeros_like(h), 0, 12, 1, Budget())
        np.testing.assert_allclose(h, initial, atol=1e-12)

    def test_coastal_exchange_and_sinks_account_exactly(self):
        h = np.zeros((10, 10))
        z = np.zeros_like(h)
        ocean = np.zeros(h.shape, bool)
        ocean[:, -1] = True
        budget = Budget()
        for _ in range(50):
            advance_water(h, z, np.zeros_like(ocean), ocean, np.full_like(h, 0.001), np.full_like(h, 0.0002), 0.5, 12, 1, budget)
        self.assertAlmostEqual(float(h.sum()*144), budget.rain_m3-budget.drained_m3+budget.coastal_exchange_m3, places=8)

    def test_building_barrier_blocks_transport(self):
        h = np.zeros((8, 8)); h[:, :3] = 1
        barrier = np.zeros_like(h, dtype=bool); barrier[:, 3] = True
        for _ in range(30):
            advance_water(h, np.zeros_like(h), barrier, np.zeros_like(barrier), np.zeros_like(h), np.zeros_like(h), 0, 12, 1, Budget())
        self.assertEqual(float(h[:, 4:].sum()), 0)


class WindTests(unittest.TestCase):
    def test_uniform_unobstructed_wind_and_meteorological_direction(self):
        u,v=potential_wind(np.zeros((20,20),bool), 10, 90, 12, iterations=100)
        np.testing.assert_allclose(u[2:-2,2:-2], -10, atol=1e-10)
        np.testing.assert_allclose(v[2:-2,2:-2], 0, atol=1e-10)

    def test_obstacles_are_impermeable(self):
        mask=np.zeros((25,25),bool);mask[8:16,10:14]=True
        u,v=potential_wind(mask, 10, 135, 12, iterations=250)
        self.assertTrue(np.isfinite(u).all() and np.isfinite(v).all())
        self.assertEqual(float(np.abs(u[mask]).sum()+np.abs(v[mask]).sum()),0)


if __name__ == '__main__':
    unittest.main()
