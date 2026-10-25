"""Small input boundary between the shared weather world and StormSim destruction.

The nearest-cell index is built in UTM once. It preserves the host's southward
rows; no north-up raster arithmetic or second wind/water solver is used.
"""
from types import SimpleNamespace

import numpy as np
from scipy.spatial import cKDTree
from shapely import contains_xy
from shapely.geometry import shape, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import transform

from . import destruction as engine, risk_assets


class Grid:
    def __init__(self, world, buildings):
        x, y = risk_assets.TO(world.lon, world.lat)
        self.xy = np.column_stack((x.ravel(), y.ravel()))
        self.lookup = cKDTree(self.xy)
        self.shape = world.z.shape
        self.dx = float(getattr(world, 'cell', 12))
        self.x0, self.y0 = float(x.min()), float(y.min())
        self.bldg_id = np.full(self.shape, -1, dtype=int)
        self.bldg_h = np.zeros(self.shape, dtype=float)
        for b in buildings:
            poly = b['_polygon']
            minx, miny, maxx, maxy = poly.bounds
            candidates = (x >= minx) & (x <= maxx) & (y >= miny) & (y <= maxy)
            mask = np.zeros(self.shape, bool)
            mask[candidates] = contains_xy(poly, x[candidates], y[candidates])
            self.bldg_id[mask] = b['id']
            self.bldg_h[mask] = b['height_m']
        self.cover = np.where(world.ocean, 3, np.where((self.bldg_id >= 0) | world.walls, 1, 0))

    def cell(self, x, y):
        xx, yy = np.broadcast_arrays(x, y)
        ids = self.lookup.query(np.column_stack((xx.ravel(), yy.ravel())))[1]
        iy, ix = np.unravel_index(ids, self.shape)
        return ix.reshape(xx.shape), iy.reshape(xx.shape)


class Wind:
    def __init__(self, world, grid):
        self.world, self.grid = world, grid
        u = getattr(world, 'u', None)
        v = getattr(world, 'v', None)
        if u is None or v is None:
            # Small callers without a prepared field use the same host solver.
            from .simulation import potential_wind
            u, v = potential_wind(world.walls, world.p.wind_mps, world.p.wind_from_deg, grid.dx)
        self.amplification = np.hypot(u, v) / world.p.wind_mps if world.p.wind_mps > 0 else np.ones_like(world.z)

    def speedup(self, x, y, z):
        ix, iy = self.grid.cell(x, y)
        p = engine.params()['wind_profile']
        def kz(height, exposure):
            return 2.01 * (np.maximum(height, p['z_min_m']) / exposure['zg_m']) ** (2 / exposure['alpha'])
        profile = np.sqrt(kz(z, p['exposure_B']) / kz(10., p['exposure_C']))
        return self.amplification[iy, ix] * profile


class Inputs:
    def __init__(self, assets, world):
        self.world = world
        self.buildings = []
        for i, b in enumerate(assets['buildings']):
            if b.get('geometry'):
                poly = transform(risk_assets.TO, shape(b['geometry']))
            else:
                x, y = b['xy']; r = np.sqrt(b['area_m2']) / 2
                poly = Polygon([(x-r,y-r),(x+r,y-r),(x+r,y+r),(x-r,y+r)])
            parts = list(poly.geoms) if poly.geom_type == 'MultiPolygon' else [poly]
            exterior = max(parts, key=lambda g: g.area).exterior
            rings = [list(map(list, ring.coords)) for part in parts
                     for ring in [orient(part, sign=-1).exterior, *orient(part, sign=-1).interiors]]
            self.buildings.append(dict(id=i, source_id=b['id'], x=b['xy'][0], y=b['xy'][1],
                height_m=b['height_m'], floors=max(1, round(b['height_m']/3)), area_m2=b['area_m2'],
                footprint=list(map(list, exterior.coords)), footprint_rings=rings, _polygon=poly))
        self.trees = [dict(id=i, source_id=t['id'], x=t['xy'][0], y=t['xy'][1],
            dbh_cm=t.get('dbh_m', .15)*100, height_m=t['height_m'], crown_m=t['crown_m'],
            species=t.get('species'), wood_mor_mpa=t.get('wood_mor_mpa'), street=t.get('street'))
            for i, t in enumerate(assets['trees'])]
        self.grid = Grid(world, self.buildings)
        self.wind = Wind(world, self.grid)
        self.assets = assets.get('destruction_inventory')
        if self.assets is None:
            # Explicit in-memory inventories (tests/importers) use their own conductor geometry.
            # The real risk_assets.load path always attaches the complete cached OSM/SFMTA cut.
            self.assets = inventory_from_lines(assets.get('power_lines', []))
        self.building_ids = {b['source_id']: b['id'] for b in self.buildings}
        self.tree_ids = {t['source_id']: t['id'] for t in self.trees}

    def plan(self, actions):
        out = dict(tree_actions={}, roof_retrofit=[], backup_power=[], closed_routes=[])
        for a in actions:
            kind, target = a['kind'], a['target']
            if kind == 'prune_tree': out['tree_actions'][self.tree_ids[target]] = 'prune'
            elif kind == 'brace_structure': out['roof_retrofit'].append(self.building_ids[target])
            elif kind == 'backup_power': out['backup_power'].append(self.building_ids[target])
            elif kind == 'close_route': out['closed_routes'].append(target)
        return out

    def run(self, frames, times, actions=()):
        times = np.asarray(times, float)
        p = self.world.p
        storm = SimpleNamespace(name='shared-weather', t=times, dt_frame_s=120.,
            gust_ms=p.gust_mps*(.6+.4*np.sin(np.pi*times/3600)),
            mean_ms=np.full(len(times), p.wind_mps), wind_dir_deg=p.wind_from_deg,
            wind_dir_t=np.full(len(times), p.wind_from_deg), rain_mm_h=np.full(len(times), p.rain_mm_h),
            wind=self.wind, depth_frames=np.asarray(frames))
        result = engine.run_destruction(storm, plan=self.plan(actions), wind=self.wind,
            water=engine.water_inputs(storm), assets=self.assets, buildings=self.buildings,
            trees=self.trees, cars=[], grid=self.grid)
        result['wind_source'] = 'Host potential_wind u,v / mean wind × ASCE 7 exposure-B height profile'
        result['notes'].extend([
            'Building occupancy inferred by original height/class rules; assessor structure and contents values unavailable.',
            'No parked-car inventory: falling-object car strikes skipped.',
            'Roadway mask: non-building, non-ocean land at least 6 m from rasterized buildings.',
        ])
        return storm, result


def inventory_from_lines(lines):
    """Translate explicit conductor fixtures without inventing poles or customer topology."""
    out = {k: [] for k in ('power_poles','power_spans','power_lines','ocs_spans','streetlights','signals','shelters','masts')}
    for line in lines:
        if line.get('installation') != 'overhead': continue
        geom = transform(risk_assets.TO, shape(line['geometry']))
        parts = list(geom.geoms) if hasattr(geom, 'geoms') else [geom]
        spans = []
        lid = len(out['power_lines'])
        for part in parts:
            for a,b in zip(list(part.coords)[:-1], list(part.coords)[1:]):
                if line.get('kind') == 'trolley':
                    for c,d in risk_assets._split(a,b,30):
                        out['ocs_spans'].append(dict(id=len(out['ocs_spans']), a=c,b=d,section=lid,
                            street=line.get('name',''),mode='trolleybus',length_m=float(np.linalg.norm(np.subtract(d,c))),source_id=line['id']))
                else:
                    sid=len(out['power_spans']);spans.append(sid)
                    out['power_spans'].append(dict(id=sid,a=a,b=b,line_id=lid,pole_a=None,pole_b=None,
                        length_m=float(np.linalg.norm(np.subtract(b,a))),source_id=line['id']))
        if spans: out['power_lines'].append(dict(id=lid,spans=spans,source_id=line['id']))
    return out
