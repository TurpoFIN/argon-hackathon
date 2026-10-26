"""Bounded public asset cut, with original power-line provenance and missingness."""
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import urllib.parse
import urllib.request

import numpy as np
from pyproj import Transformer
from scipy.ndimage import distance_transform_edt
from shapely.geometry import shape, mapping, box, LineString
from shapely.ops import transform

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'data/risk'
PGE = 'https://services2.arcgis.com/mJaJSax0KPHoCNB6/ArcGIS/rest/services/DRPComplianceRelProd/FeatureServer'
CEC = 'https://services3.arcgis.com/bWPjFyq029ChCGur/arcgis/rest/services/Transmission_Line/FeatureServer/2'
BBOX = [-122.486, 37.786, -122.386, 37.812]  # Presidio to North Beach and the Embarcadero
TO = Transformer.from_crs(4326, 32610, always_xy=True).transform
FROM = Transformer.from_crs(32610, 4326, always_xy=True).transform


def fetch(name, url, max_bytes=12_000_000):
    FOLDER.mkdir(exist_ok=True)
    path = FOLDER / name
    if not path.exists():
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent':'ArgonPreparedness/1.0'}), timeout=60) as r:
            data = r.read(max_bytes + 1)
        if len(data) > max_bytes: raise ValueError('Asset cut exceeds byte limit: '+name)
        path.write_bytes(data)
    raw = path.read_bytes()
    return json.loads(raw), {'url':url, 'file':name, 'sha256':hashlib.sha256(raw).hexdigest()}


def arc(name, endpoint, fields):
    params = dict(f='geojson', where='1=1', geometry=','.join(map(str,BBOX)),
                  geometryType='esriGeometryEnvelope', inSR=4326, outSR=4326,
                  spatialRel='esriSpatialRelIntersects', outFields=fields, resultRecordCount=2000)
    data, source = fetch(name, endpoint+'/query?'+urllib.parse.urlencode(params))
    if data.get('error'): raise ValueError(str(data['error']))
    if data.get('exceededTransferLimit'): raise ValueError('Truncated asset query: '+name)
    return data['features'], source


def prepare():
    manifest = json.loads((ROOT/'data/manifest.json').read_text())
    g = manifest['grid']; dx = g['cell_m']; x0 = g['origin_easting']; y0 = g['origin_northing']
    with np.load(ROOT/'data/model-inputs.npz') as f:
        walls=f['buildings']; ocean=f['ocean']
    nearest=distance_transform_edt(walls|ocean,return_distances=False,return_indices=True)
    def cell(x,y):
        j=max(0,min(g['nx']-1,int((x-x0)//dx)));i=max(0,min(g['ny']-1,int((y0-y)//dx)))
        return [int(nearest[0,i,j]),int(nearest[1,i,j])]
    def xy(point): return list(map(float,TO(*point)))
    sources=[]; bounds=box(*BBOX)
    bldgs=[]
    for b in json.loads((ROOT/'data/raw/ynuv-fyni.json').read_text()):
        geom=shape(b['shape']); p=transform(TO,geom); center=p.representative_point()
        if p.area<30: continue
        rr=p.minimum_rotated_rectangle; c=list(rr.exterior.coords); edge=np.array(c[1])-c[0]
        bldgs.append(dict(id='B'+str(b['sf16_bldgid']),xy=[center.x,center.y],position=list(FROM(center.x,center.y)),
            cell=cell(center.x,center.y),area_m2=p.area,perimeter_m=p.length,height_m=float(b['hgt_median_m']),
            angle_rad=float(np.arctan2(edge[1],edge[0])),geometry=mapping(geom)))
    from . import building_inventory
    full, roll, inventory_sources = building_inventory.load_sources()
    inventory_coverage = building_inventory.enrich(bldgs, full, roll)
    with np.load(ROOT/'data/model-inputs.npz') as f:
        building_inventory.ground_fallback(bldgs, f['ground'], f['lon'], f['lat'])
    sources.extend(inventory_sources)
    sources.append({'name':'DataSF building footprints','url':manifest['sources']['buildings'],'basis':'Mapped geometry and older measured height; structural condition is not recorded.'})
    q={'$where':f'latitude between {BBOX[1]} and {BBOX[3]} and longitude between {BBOX[0]} and {BBOX[2]}',
       '$select':'treeid,species,mapdbh,latitude,longitude,legalstatus,data_as_of','$limit':10000}
    rows,src=fetch('trees.json','https://data.sfgov.org/resource/tkzw-k3nq.json?'+urllib.parse.urlencode(q));src['name']='DataSF street trees';sources.append(src)
    trees=[];missing=0
    for r in rows:
        if any(s in r.get('species','').lower() for s in ('stump','vacant','planting site','no tree')):
            missing+=1;continue
        try: dbh=float(r.get('mapdbh',''))*.0254
        except (ValueError,TypeError): missing+=1;continue
        if not .02<=dbh<=2: missing+=1;continue
        position=[float(r['longitude']),float(r['latitude'])];x,y=xy(position)
        # Documented scenario allometry, not a surveyed height or condition.
        height=1.37+22.63*(1-np.exp(-.03*dbh*100))
        trees.append(dict(id='T'+r['treeid'],xy=[x,y],position=position,cell=cell(x,y),dbh_m=dbh,
                          height_m=float(height),crown_m=float(.42*(dbh*100)**.8),species=r.get('species','unrecorded')))
    q={'$where':f"active = true AND intersects(line,'POLYGON(({BBOX[0]} {BBOX[1]},{BBOX[2]} {BBOX[1]},{BBOX[2]} {BBOX[3]},{BBOX[0]} {BBOX[3]},{BBOX[0]} {BBOX[1]}))')",
       '$select':'cnn,streetname,line,f_node_cnn,t_node_cnn','$limit':10000}
    rows,src=fetch('streets.json','https://data.sfgov.org/resource/3psu-pn9h.json?'+urllib.parse.urlencode(q));src['name']='DataSF active street centerlines';sources.append(src)
    roads=[]
    for r in rows:
        geom=shape(r['line']).intersection(bounds)
        parts=list(geom.geoms) if hasattr(geom,'geoms') else [geom]
        for k,line in enumerate(parts):
            if line.geom_type!='LineString':continue
            p=transform(TO,line);m=p.interpolate(.5,normalized=True)
            roads.append(dict(id='R'+r['cnn']+'-'+str(k),name=r.get('streetname','Street'),geometry=mapping(line),
                xy=list(map(list,p.coords)),length_m=p.length,cell=cell(m.x,m.y),position=list(FROM(m.x,m.y))))
    lines=[]
    rows,src=arc('pge-feeders.geojson',PGE+'/2','OBJECTID,Substation,FeederID,Feeder_Name,Nominal_Voltage,Last_Update_On_Map');src['name']='PG&E GRIP distribution feeders';sources.append(src)
    for f in rows:
        prop=f['properties'];geom=shape(f['geometry']).intersection(bounds)
        if geom.is_empty:continue
        lines.append(dict(id='P'+str(prop['OBJECTID']),feeder=str(prop['FeederID']),name=prop.get('Feeder_Name'),
            substation=prop.get('Substation'),installation='unknown',voltage_kv=prop.get('Nominal_Voltage'),
            geometry=mapping(geom),source='PG&E GRIP'))
    rows,src=arc('cec-lines.geojson',CEC,'OBJECTID,Name,Type,kV,Owner,Status');src['name']='CEC transmission lines';sources.append(src)
    for f in rows:
        prop=f['properties'];geom=shape(f['geometry']).intersection(bounds)
        if geom.is_empty:continue
        lines.append(dict(id='C'+str(prop['OBJECTID']),feeder=None,name=prop.get('Name'),substation=None,
            installation={'OH':'overhead','UG':'underground','UW':'underwater'}.get(prop.get('Type'),'unknown'),
            voltage_kv=prop.get('kV'),geometry=mapping(geom),source='CEC'))
    transmission_count=len(lines)-sum(l['source']=='PG&E GRIP' for l in lines)
    bbox=','.join(map(str,(BBOX[1],BBOX[0],BBOX[3],BBOX[2])))
    query=f'[out:json];(way["power"~"^(line|minor_line)$"]({bbox});node["power"~"^(pole|tower)$"]({bbox});way["trolley_wire"="yes"]({bbox});way["railway"~"^(tram|light_rail)$"]["electrified"="contact_line"]({bbox}););out body;>;out skel qt;'
    osm,src=fetch('osm-overhead.json','https://overpass-api.de/api/interpreter?'+urllib.parse.urlencode({'data':query}))
    src['name']='OpenStreetMap overhead conductors';src['license']='© OpenStreetMap contributors, ODbL';sources.append(src)
    nodes={e['id']:[e['lon'],e['lat']] for e in osm['elements'] if e['type']=='node' and 'lon' in e}
    for e in osm['elements']:
        if e['type']!='way':continue
        tags=e.get('tags',{})
        if tags.get('power') in ('line','minor_line'):kind='power'
        elif tags.get('trolley_wire')=='yes' or (tags.get('railway') in ('tram','light_rail') and tags.get('electrified')=='contact_line'):kind='trolley'
        else:continue
        coordinates=[nodes[n] for n in e['nodes'] if n in nodes]
        if len(coordinates)!=len(e['nodes']) or len(coordinates)<2:raise ValueError('Incomplete OSM conductor geometry')
        lines.append(dict(id='OSM'+str(e['id']),installation='overhead',kind=kind,
            geometry=mapping(LineString(coordinates)),source='© OpenStreetMap contributors, ODbL'))
    rows,src=arc('pge-substations.geojson',PGE+'/0','OBJECTID,SubstationName,SubstationID,Voltage_kV');src['name']='PG&E GRIP substations';sources.append(src)
    stations=[]
    for f in rows:
        prop=f['properties'];point=shape(f['geometry']);x,y=TO(point.x,point.y)
        stations.append(dict(id='S'+str(prop['OBJECTID']),name=prop.get('SubstationName'),position=[point.x,point.y],xy=[x,y],cell=cell(x,y)))
    # Assignment is explicit proximity assumption, never asserted as a customer connection.
    pgelines=[l for l in lines if l['source']=='PG&E GRIP']
    pgshapes=[transform(TO,shape(l['geometry'])) for l in pgelines]
    for b in bldgs:
        p=shape({'type':'Point','coordinates':b['xy']})
        idx=min(range(len(pgelines)),key=lambda i:pgshapes[i].distance(p)) if pgelines else None
        b['feeder']=pgelines[idx]['feeder'] if idx is not None else None
        b['substation']=pgelines[idx]['substation'] if idx is not None else None
        b['supply_basis']='Nearest mapped feeder; customer connection unverified'
    result=dict(version=1,generated_at=datetime.now(timezone.utc).isoformat(),bbox=BBOX,
        buildings=bldgs,trees=trees,roads=roads,power_lines=lines,substations=stations,sources=sources,
        coverage=dict(**inventory_coverage,buildings=len(bldgs),trees=len(trees),trees_without_usable_dbh=missing,roads=len(roads),
                      distribution_feeders=len(pgelines),transmission_lines=transmission_count,substations=len(stations),
                      overhead_power_lines=sum(l['installation']=='overhead' and l.get('kind','power')=='power' for l in lines),
                      trolley_wire_ways=sum(l.get('kind')=='trolley' for l in lines),
                      overhead_lines=sum(l['installation']=='overhead' for l in lines),unknown_installation=sum(l['installation']=='unknown' for l in lines)))
    (FOLDER/'assets.json').write_text(json.dumps(result,separators=(',',':')))
    print(json.dumps(result['coverage'],indent=2),flush=True)
    return result


def load():
    assets = json.loads((FOLDER/'assets.json').read_text())
    assets['destruction_inventory'] = build_destruction_inventory()
    return assets


def _inside(meta, x, y):
    lon, lat = FROM(x, y)
    return BBOX[0] <= lon <= BBOX[2] and BBOX[1] <= lat <= BBOX[3]


def _pt(meta, kind, i, x, y, **kw):
    ix, iy = meta.cell(x, y) if meta is not None else (None, None)
    return dict(id=i, kind=kind, x=round(x, 2), y=round(y, 2), ix=ix, iy=iy, **kw)


def _split(p, q, max_len):
    n = max(1, int(math.ceil(math.hypot(q[0]-p[0], q[1]-p[1]) / max_len)))
    return [((p[0]+(q[0]-p[0])*k/n, p[1]+(q[1]-p[1])*k/n),
             (p[0]+(q[0]-p[0])*(k+1)/n, p[1]+(q[1]-p[1])*(k+1)/n)) for k in range(n)]


OCS_MAX_SPAN_M = 30.0


def build_destruction_inventory(grid=None):
    """Original inventory construction, using this app's bounded fetch/cache layer."""
    bbox = ','.join(map(str, (BBOX[1], BBOX[0], BBOX[3], BBOX[2])))
    queries = [
        ('osm-overhead.json', f'[out:json];(way["power"~"^(line|minor_line)$"]({bbox});node["power"~"^(pole|tower)$"]({bbox});way["trolley_wire"="yes"]({bbox});way["railway"~"^(tram|light_rail)$"]["electrified"="contact_line"]({bbox}););out body;>;out skel qt;'),
        ('osm-street-assets.json', f'[out:json];(node["highway"="street_lamp"]({bbox});node["highway"="bus_stop"]["shelter"="yes"]({bbox});node["amenity"="shelter"]({bbox});node["man_made"~"^(mast|tower)$"]({bbox}););out body;'),
    ]
    data = []; sources = []
    for name, query in queries:
        rows, source = fetch(name, 'https://overpass-api.de/api/interpreter?'+urllib.parse.urlencode({'data':query}))
        source['license'] = '© OpenStreetMap contributors, ODbL'
        data.append(rows); sources.append(source)
    query = {'$where':f'within_box(shape,{BBOX[3]},{BBOX[0]},{BBOX[1]},{BBOX[2]})', '$limit':5000}
    signal_rows, source = fetch('datasf-traffic-signals.json', 'https://data.sfgov.org/resource/ybh5-27n2.json?'+urllib.parse.urlencode(query))
    source['license'] = 'DataSF Open Data; SFMTA Traffic Signals'
    sources.append(source)
    osm = {'elements': data[0]['elements'] + data[1]['elements']}
    meta = grid
    nodes = {}
    for e in osm["elements"]:   # "out body; >; out skel" repeats tagged nodes without tags: keep the tagged copy
        if e["type"] == "node" and (e.get("tags") or e["id"] not in nodes):
            nodes[e["id"]] = e
    utm = {nid: TO(n["lon"], n["lat"]) for nid, n in nodes.items()}
    tagged = [e for e in osm["elements"] if e.get("tags") and (e["type"] != "node" or nodes[e["id"]] is e)]

    # ---------------- power: poles, lines (OSM ways), spans between consecutive line nodes
    pole_nodes = sorted(e["id"] for e in tagged if e["type"] == "node" and e["tags"].get("power") in ("pole", "tower"))
    pole_idx = {}
    poles = []
    for nid in pole_nodes:
        x, y = utm[nid]
        if not _inside(meta, x, y):
            continue
        t = nodes[nid]["tags"]
        pole_idx[nid] = len(poles)
        poles.append(_pt(meta, "power_pole", len(poles), x, y, osm_id=nid, material=t.get("material", "wood"),
                         operator=t.get("operator:short") or t.get("operator") or "", tower=t.get("power") == "tower",
                         spans=[]))
    lines, spans = [], []
    for w in sorted((e for e in tagged if e["type"] == "way" and e["tags"].get("power") in ("line", "minor_line")),
                    key=lambda e: e["id"]):
        if w["tags"].get("layer", "0").startswith("-"):
            continue   # mapped as underground
        lid = len(lines)
        span_ids = []
        for a, b in zip(w["nodes"][:-1], w["nodes"][1:]):
            if a not in utm or b not in utm:
                continue
            pa, pb = utm[a], utm[b]
            if not (_inside(meta, *pa) or _inside(meta, *pb)):
                continue
            sid = len(spans)
            ia, ib = pole_idx.get(a), pole_idx.get(b)
            spans.append(dict(id=sid, kind="power_span", line_id=lid, a=[round(pa[0], 2), round(pa[1], 2)],
                              b=[round(pb[0], 2), round(pb[1], 2)], pole_a=ia, pole_b=ib,
                              length_m=round(math.hypot(pb[0] - pa[0], pb[1] - pa[1]), 2)))
            for ip in (ia, ib):
                if ip is not None:
                    poles[ip]["spans"].append(sid)
            span_ids.append(sid)
        if span_ids:
            lines.append(dict(id=lid, osm_id=w["id"], kind=w["tags"]["power"],
                              operator=w["tags"].get("operator:short") or w["tags"].get("operator") or "",
                              spans=span_ids))

    # ---------------- Muni overhead contact system: trolleybus wire streets + electrified tram/light rail
    ocs = []
    sections = {}
    for w in sorted((e for e in tagged if e["type"] == "way" and (
            e["tags"].get("trolley_wire") == "yes" or
            (e["tags"].get("railway") in ("tram", "light_rail") and e["tags"].get("electrified") == "contact_line"))),
            key=lambda e: e["id"]):
        t = w["tags"]
        mode = "trolleybus" if t.get("trolley_wire") == "yes" else t["railway"]
        name = t.get("name") or f"way {w['id']}"
        sec = sections.setdefault((mode, name), len(sections))
        for a, b in zip(w["nodes"][:-1], w["nodes"][1:]):
            if a not in utm or b not in utm:
                continue
            for p, q in _split(utm[a], utm[b], OCS_MAX_SPAN_M):
                if not (_inside(meta, *p) or _inside(meta, *q)):
                    continue
                ocs.append(dict(id=len(ocs), kind="ocs_span", mode=mode, street=name, section=sec, osm_id=w["id"],
                                a=[round(p[0], 2), round(p[1], 2)], b=[round(q[0], 2), round(q[1], 2)],
                                length_m=round(math.hypot(q[0] - p[0], q[1] - p[1]), 2)))

    # ---------------- streetlights, shelters, masts (OSM points)
    lights, shelters, masts = [], [], []
    for e in sorted((e for e in tagged if e["type"] == "node"), key=lambda e: e["id"]):
        t = e["tags"]
        x, y = utm[e["id"]]
        if not _inside(meta, x, y):
            continue
        if t.get("highway") == "street_lamp":
            mount = t.get("lamp_mount", "straight_mast")
            lights.append(_pt(meta, "streetlight", len(lights), x, y, osm_id=e["id"], mount=mount,
                              high_mast=mount == "high_mast"))
        elif (t.get("highway") == "bus_stop" and t.get("shelter") == "yes") or t.get("amenity") == "shelter":
            shelters.append(_pt(meta, "shelter", len(shelters), x, y, osm_id=e["id"], name=t.get("name", "")))
        elif t.get("man_made") in ("mast", "tower") and t.get("tower:type") != "bell_tower" and t.get("location") != "roof":
            masts.append(_pt(meta, "mast", len(masts), x, y, osm_id=e["id"], tower_type=t.get("tower:type", ""),
                             lattice=t.get("tower:construction") == "lattice"))

    # ---------------- traffic signals (SFMTA official inventory)
    signals = []
    for r in sorted(signal_rows, key=lambda r: int(r["objectid"])):
        if r.get("type") not in ("SIGNAL", "FLASHER", "BEACON") or not r.get("shape"):
            continue
        lon, lat = r["shape"]["coordinates"]
        x, y = TO(lon, lat)
        if _inside(meta, x, y):
            signals.append(_pt(meta, "signal", len(signals), x, y, sfmta_id=int(r["objectid"]), type=r["type"],
                               street1=r.get("street1", ""), street2=r.get("street2", "")))

    out = dict(power_poles=poles, power_lines=lines, power_spans=spans, ocs_spans=ocs,
               ocs_sections=[dict(id=v, mode=k[0], street=k[1]) for k, v in sorted(sections.items(), key=lambda kv: kv[1])],
               streetlights=lights, signals=signals, shelters=shelters, masts=masts,
               sources={
                   'osm': sources[0], 'street_assets': sources[1], 'signals': sources[2],
                   'coverage': 'Mapped objects only; OSM counts are a floor on real stock.'})
    return out


if __name__=='__main__':prepare()
