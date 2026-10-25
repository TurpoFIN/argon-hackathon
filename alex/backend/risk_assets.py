"""Bounded public asset cut, with original power-line provenance and missingness."""
from datetime import datetime, timezone
import hashlib
import json
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
BBOX = [-122.406, 37.786, -122.386, 37.808]
TO = Transformer.from_crs(4326, 32610, always_xy=True).transform
FROM = Transformer.from_crs(32610, 4326, always_xy=True).transform


def fetch(name, url):
    FOLDER.mkdir(exist_ok=True)
    path = FOLDER / name
    if not path.exists():
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent':'ArgonPreparedness/1.0'}), timeout=60) as r:
            data = r.read(12_000_001)
        if len(data) > 12_000_000: raise ValueError('Asset cut exceeds 12 MB: '+name)
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
        coverage=dict(buildings=len(bldgs),trees=len(trees),trees_without_usable_dbh=missing,roads=len(roads),
                      distribution_feeders=len(pgelines),transmission_lines=len(lines)-len(pgelines),substations=len(stations),
                      overhead_lines=sum(l['installation']=='overhead' for l in lines),unknown_installation=sum(l['installation']=='unknown' for l in lines)))
    (FOLDER/'assets.json').write_text(json.dumps(result,separators=(',',':')))
    print(json.dumps(result['coverage'],indent=2),flush=True)
    return result


def load():
    return json.loads((FOLDER/'assets.json').read_text())


if __name__=='__main__':prepare()
