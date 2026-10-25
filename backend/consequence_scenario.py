"""Illustrative, paired consequences — explicitly not measured site predictions.

Mapped positions are real. Asset condition, inventories and capacities below are
scenario assumptions. The same forcing and assets are reused in both branches;
only completion of the named preventive operation changes.
"""
from functools import lru_cache
import hashlib
import json
import math
from . import risk_assets
from shapely.geometry import Point, shape

VERSION='illustrative-consequences-1'
ASSUMPTIONS={
    'storm_minutes':60, 'loose_objects':10, 'open_windows':4,
    'pane_area_m2':1.8, 'door_sill_m':.15, 'barrier_m':.6,
    'sandbag_m':.4, 'catchment_m2':1200, 'ponding_area_m2':200,
    'assumed_ingress_catchment_m2':220, 'assumed_exposed_floor_m2':80, 'pump_lps':6.3,
    'contents_count':10, 'contents_height_m':.05, 'raised_height_m':1,
    'tree_wet_failure_gust_mps':26, 'pruned_wind_load_fraction':.65,
    'weak_facade_failure_gust_mps':26, 'braced_capacity_multiplier':1.7,
    'facade_section_m2':12, 'lane_width_m':3, 'outage_gust_mps':22,
}


def distance(a,b):
    return math.hypot((a[0]-b[0])*math.cos(math.radians(a[1])),a[1]-b[1])*111320


@lru_cache(maxsize=64)
def placement(lon,lat):
    """Position the explanatory cutaway on the adjacent mapped street."""
    try:
        assets=risk_assets.load()
    except (FileNotFoundError,OSError):
        return dict(position=[lon,lat],source_position=[lon,lat],heading=0,tree_height_m=12,asset_basis='Site location; illustrative local geometry')
    origin=[lon,lat]
    building=min(assets['buildings'],key=lambda b:shape(b['geometry']).distance(Point(lon,lat)),default=None)
    tree=min(assets['trees'],key=lambda t:distance(origin,t['position']),default=None)
    best=(float('inf'),origin,0,None)
    scale=111320*math.cos(math.radians(lat))
    for road in assets['roads']:
        coords=road['geometry']['coordinates']
        for a,b in zip(coords,coords[1:]):
            ax,ay=(a[0]-lon)*scale,(a[1]-lat)*111320
            dx,dy=(b[0]-a[0])*scale,(b[1]-a[1])*111320
            t=max(0,min(1,-(ax*dx+ay*dy)/max(dx*dx+dy*dy,1e-12)))
            x,y=ax+t*dx,ay+t*dy
            if math.hypot(x,y)<best[0]:best=(math.hypot(x,y),[lon+x/scale,lat+y/111320],math.degrees(math.atan2(dx,dy)),road['id'])
    return dict(position=best[1],source_position=origin,heading=best[2],road_id=best[3],
                tree_height_m=round(min(18,max(8,tree['height_m'] if tree else 12)),1),
                tree_id=tree['id'] if tree else None,
                building_id=building['id'] if building else None,
                building_position=building['position'] if building else origin,
                building_geometry=building['geometry'] if building else None,
                building_height_m=building['height_m'] if building else None,
                asset_basis='Mapped building footprint and street geometry. Indoor layout, ingress and equipment are hypothetical; no basement is asserted.')


def state(operation,weather,completed=False):
    a=ASSUMPTIONS
    gust=max(0,float(weather.get('gust_mps',0)))
    rain=max(0,float(weather.get('rain_mm_h',0)))
    runoff=max(0,min(1,float(weather.get('runoff_coefficient',.9))))
    duration=a['storm_minutes']*60
    q=.613*gust**2
    result=dict(broken_panes=0,rain_inside_l=0,water_cm=0,wet_contents=0,
                tree_fallen=False,collapse_m2=0,pump_on=False,grid_on=gust<a['outage_gust_mps'],
                lane_open=True,lane_width_m=a['lane_width_m'],exposed_passages=0)
    if operation in ('secure-items','latch-windows'):
        release=sum(gust>v for v in range(18,38,2))
        if completed and operation=='secure-items':release=sum(gust>v for v in range(46,66,2))
        impact=min(6,release//2)
        open_count=0 if completed and operation=='latch-windows' else a['open_windows']
        hardware=int(open_count>0 and q>400)
        direct=sum(q*1.3>v for v in (800,950,1100,1300))
        broken=min(10,impact+hardware+direct)
        result.update(broken_panes=broken,rain_inside_l=round(rain*(.6*open_count+.5*broken)),loose_released=release)
        summary=f'{broken} broken '+('pane' if broken==1 else 'panes')+f' · {result["rain_inside_l"]} L rain indoors'
    elif operation=='prune-defect':
        # Wind force scales with V². Pruning changes projected crown area only.
        load=q*(a['pruned_wind_load_fraction'] if completed else 1)
        capacity=.613*a['tree_wet_failure_gust_mps']**2*(1.2 if rain<30 else 1)
        fallen=load>capacity
        result.update(tree_fallen=fallen,lane_open=not fallen,lane_width_m=0 if fallen else 3)
        summary='Tree falls across road' if fallen else 'Tree stays standing · road open'
    elif operation=='engineered-support':
        capacity=.613*a['weak_facade_failure_gust_mps']**2*(a['braced_capacity_multiplier'] if completed else 1)
        area=a['facade_section_m2'] if q>capacity else 0
        result.update(collapse_m2=area,lane_open=not bool(area),lane_width_m=0 if area else 3)
        summary=f'{area} m² façade section collapses' if area else 'Façade section stays in place'
    elif operation=='backup-pump':
        inflow=rain/1000/3600*a['assumed_ingress_catchment_m2']*runoff
        on=result['grid_on'] or completed
        retained=max(0,(inflow-(a['pump_lps']/1000 if on else 0))*duration)
        cm=round(retained/a['assumed_exposed_floor_m2']*100)
        result.update(water_cm=cm,pump_on=on,wet_contents=a['contents_count'] if cm/100>a['contents_height_m'] else 0)
        summary=(f'{cm} cm indoor water' if cm else 'No accumulated indoor water')+' · pump '+('running' if on else 'off')
    elif operation in ('fit-barrier','sandbag-wall','clear-inlet','move-contents'):
        blockage=max(0,min(1,float(weather.get('blockage_pct',20))/100))
        if completed and operation=='clear-inlet':blockage=0
        volume=rain/1000*a['catchment_m2']*runoff
        drained=max(0,float(weather.get('drain_capacity_lps',12)))/1000*(1-blockage)*duration
        street=max(0,(volume-drained)/a['ponding_area_m2'])
        top=a['barrier_m'] if operation=='fit-barrier' else a['sandbag_m']
        protected=completed and operation in ('fit-barrier','sandbag-wall') and street<=top
        inside=0 if protected else max(0,street-a['door_sill_m'])
        cm=round(inside*100)
        contents_height=a['raised_height_m'] if completed and operation=='move-contents' else a['contents_height_m']
        wet=a['contents_count'] if inside>contents_height else 0
        result.update(water_cm=cm,wet_contents=wet,street_water_cm=round(street*100),barrier=protected)
        summary=f'{wet} contents boxes flooded' if operation=='move-contents' else f'{cm} cm water inside · {wet} boxes flooded'
    else:
        new_debris=0 if gust<22 else .4 if gust<36 else 3
        original=0 if gust<18 else 2.4
        obstruction=max(new_debris,0 if completed and operation=='clear-route' else original)
        width=round(max(0,3-obstruction),1)
        open_lane=width>=2
        exposed=20 if not open_lane and not (completed and operation=='install-cordon') else 0
        result.update(lane_open=open_lane,lane_width_m=width,exposed_passages=exposed)
        summary=(f'{exposed} passages through hazard' if operation=='install-cordon' else f'{width:g} m usable lane · '+('open' if open_lane else 'blocked'))
    return dict(summary=summary,**result)


@lru_cache(maxsize=512)
def _comparison(operation,site_json,weather_json):
    site=json.loads(site_json);weather=json.loads(weather_json)
    before=state(operation,weather,False);after=state(operation,weather,True)
    fingerprint=hashlib.sha256(weather_json.encode()).hexdigest()
    harm_keys=('broken_panes','rain_inside_l','water_cm','wet_contents','tree_fallen','collapse_m2','lane_width_m','exposed_passages')
    return dict(model=VERSION,basis='Illustrative extrapolation',validated=False,
                storm_before=fingerprint,storm_after=fingerprint,
                before=before,after=after,changed=any(before[k]!=after[k] for k in harm_keys),
                assumptions=ASSUMPTIONS,placement=placement(*site['position'][:2]))


def compare(work,weather):
    return _comparison(work['operation_id'],json.dumps(work['site'],sort_keys=True),json.dumps(weather,sort_keys=True))
