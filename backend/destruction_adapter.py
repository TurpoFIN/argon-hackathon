"""Small input boundary between the shared weather world and StormSim destruction.

Collision cells run south to north at 4 m. A precomputed nearest-cell map reads
the host's southward 12 m water rows without running another water solver.
"""
from types import SimpleNamespace

import numpy as np
from scipy.ndimage import distance_transform_edt
from scipy.spatial import cKDTree
from rasterio.features import rasterize
from rasterio.transform import from_origin
from shapely.geometry import shape, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import transform

from . import destruction as engine, risk_assets


class Grid:
    """South-up 4 m collision cells, with one nearest-host index per cell."""
    def __init__(self, world, buildings):
        x, y = risk_assets.TO(world.lon, world.lat)
        self.host_shape = world.z.shape
        self.host_dx = float(getattr(world, 'cell', 12))
        self.lookup = cKDTree(np.column_stack((x.ravel(), y.ravel())))
        self.dx = 4.0
        self.x0 = float(np.floor((x.min() - self.host_dx / 2) / self.dx) * self.dx)
        self.y0 = float(np.floor((y.min() - self.host_dx / 2) / self.dx) * self.dx)
        nx = int(np.ceil((x.max() + self.host_dx / 2 - self.x0) / self.dx))
        ny = int(np.ceil((y.max() + self.host_dx / 2 - self.y0) / self.dx))
        self.shape = (ny, nx)
        xx, yy = np.meshgrid(self.x0 + (np.arange(nx) + .5) * self.dx,
                             self.y0 + (np.arange(ny) + .5) * self.dx)
        self.xy = np.column_stack((xx.ravel(), yy.ravel()))
        self.water_index = self.lookup.query(self.xy)[1].reshape(self.shape)
        ordered = sorted(buildings, key=lambda b: b['height_m'])
        if ordered:
            self.bldg_id = rasterize([(b['_polygon'], b['id']) for b in ordered],
                out_shape=self.shape, transform=from_origin(self.x0, self.y0 + ny * self.dx, self.dx, self.dx),
                fill=-1, dtype='int32')[::-1].copy()
        else:
            self.bldg_id = np.full(self.shape, -1, dtype=np.int32)
        heights = np.zeros(max((b['id'] for b in buildings), default=-1) + 2, dtype=np.float32)
        for b in buildings:
            heights[b['id'] + 1] = b['height_m']
        self.bldg_h = heights[self.bldg_id + 1]
        # Match prep.py: tiny footprints get their representative cell if free.
        have = set(np.unique(self.bldg_id))
        for b in buildings:
            if (b['id'] not in have and self.x0 <= b['x'] < self.x0 + nx * self.dx
                    and self.y0 <= b['y'] < self.y0 + ny * self.dx):
                ix, iy = self.cell(b['x'], b['y'])
                if self.bldg_id[iy, ix] < 0:
                    self.bldg_id[iy, ix], self.bldg_h[iy, ix] = b['id'], b['height_m']
        self.cover = np.where(self.bldg_id >= 0, 1, np.where(self.from_host(world.ocean), 3, 0))

    def cell(self, x, y):
        xx, yy = np.broadcast_arrays(x, y)
        ix = np.clip(np.floor((xx - self.x0) / self.dx).astype(int), 0, self.shape[1] - 1)
        iy = np.clip(np.floor((yy - self.y0) / self.dx).astype(int), 0, self.shape[0] - 1)
        return ix, iy

    def host_cell(self, x, y):
        xx, yy = np.broadcast_arrays(x, y)
        ids = self.lookup.query(np.column_stack((xx.ravel(), yy.ravel())))[1]
        iy, ix = np.unravel_index(ids, self.host_shape)
        return ix.reshape(xx.shape), iy.reshape(xx.shape)

    def from_host(self, values):
        return np.asarray(values).reshape(-1)[self.water_index]

    def water(self, storm):
        summary = engine.water_inputs(storm)
        summary.max_depth = self.from_host(summary.max_depth)
        summary.k_max_depth = self.from_host(summary.k_max_depth)
        return summary


class Wind:
    def __init__(self, world, grid):
        self.world, self.grid = world, grid
        u = getattr(world, 'u', None)
        v = getattr(world, 'v', None)
        if u is None or v is None:
            # Small callers without a prepared field use the same host solver.
            from .simulation import potential_wind
            u, v = potential_wind(world.walls, world.p.wind_mps, world.p.wind_from_deg, grid.host_dx)
        self.amplification = np.hypot(u, v) / world.p.wind_mps if world.p.wind_mps > 0 else np.ones_like(world.z)
        # The 2-D field is solid (about zero) inside building cells. Below the roofs, a point there reads the nearest
        # open cell (the street beside it); at or above the local roof the air is unobstructed (speed-up 1).
        walls = np.asarray(world.walls, bool)
        nearest = distance_transform_edt(walls, return_distances=False, return_indices=True)
        self.street = self.amplification[tuple(nearest)]
        self.walls = walls
        self.roof_host = np.zeros(walls.size, np.float32)
        np.maximum.at(self.roof_host, grid.water_index.ravel(), grid.bldg_h.ravel())
        self.roof_host = self.roof_host.reshape(walls.shape)

    def speedup(self, x, y, z):
        ix, iy = self.grid.host_cell(x, y)
        p = engine.params()['wind_profile']
        def kz(height, exposure):
            return 2.01 * (np.maximum(height, p['z_min_m']) / exposure['zg_m']) ** (2 / exposure['alpha'])
        profile = np.sqrt(kz(z, p['exposure_B']) / kz(10., p['exposure_C']))
        cx, cy = self.grid.cell(x, y)
        roof = np.where(self.grid.bldg_h[cy, cx] > 0, self.grid.bldg_h[cy, cx], self.roof_host[iy, ix])
        above = (roof > 0) & (np.asarray(z, float) >= roof)
        inside = self.walls[iy, ix]
        return np.where(above, 1., np.where(inside, self.street[iy, ix], self.amplification[iy, ix])) * profile


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
            record = {k: b[k] for k in ('occupancy', 'floors', 'year_built', 'value_structure',
                'value_contents', 'first_floor_m', 'floor_area_m2', 'height_max_m', 'height_source',
                'ground_z', 'use_code', 'n_parcels', 'assessed_improvement_usd', 'ground_retail',
                'inventory_basis') if k in b}
            record.update(dict(id=i, source_id=b['id'], x=b['xy'][0], y=b['xy'][1],
                height_m=b['height_m'], area_m2=b['area_m2'],
                footprint=list(map(list, exterior.coords)), footprint_rings=rings, _polygon=poly))
            record.setdefault('floors', max(1, round(b['height_m']/3)))
            self.buildings.append(record)
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
            water=self.grid.water(storm), assets=self.assets, buildings=self.buildings,
            trees=self.trees, cars=[], grid=self.grid)
        result['wind_source'] = ('Host potential_wind u,v / mean wind × ASCE 7 exposure-B height profile; '
                                 'undisturbed (speed-up 1) at or above the local roof, nearest street cell inside building cells')
        result['notes'].extend([
            'Building replacement values: floor area × StormSim Hazus/RLB occupancy cost tables; assessed improvements are recorded separately.',
            'No parked-car inventory: falling-object car strikes skipped.',
            'Roadway mask: non-building land at least 6 m from buildings on a 4 m UTM raster; saved water remains on its host grid.',
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
