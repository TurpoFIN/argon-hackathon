"""Per-action counterfactuals driven by the selected prep run's forcing and field.

These are local, explicitly extrapolated mechanism calculations. They do not
rewrite the run's flood-loss ledger, and are not an isolated full-city solve.
"""
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path

from scipy.spatial import cKDTree
from . import consequence_scenario, risk_assets

ROOT = Path(__file__).resolve().parents[1]
NAMES = {'wind':'Wind','trees':'Falling trees','structure':'Structural collapse',
         'flood':'Flooding','power':'Power loss','routes':'Blocked routes'}


def metres(a,b):
    return math.hypot((a[0]-b[0])*88000,(a[1]-b[1])*111320)


class Field:
    def __init__(self, layers):
        self.minutes=layers.get('frame_minutes') or [0,60]
        self.rows=layers.get('flood_frames') or [[x,y,[0,z]] for x,y,z in layers.get('flood',[])]
        self.tree=cKDTree([[x*88000,y*111320] for x,y,_ in self.rows]) if self.rows else None
    def at(self, point):
        if self.tree is None:return [0.0]*len(self.minutes)
        distance,index=self.tree.query([point[0]*88000,point[1]*111320])
        return self.rows[int(index)][2] if distance<=26 else [0.0]*len(self.minutes)


def local_drain(depths,minutes,lps,area):
    """Same incoming depth increments; only the added local sink changes."""
    out=[max(0,float(depths[0]))]
    for k in range(1,len(minutes)):
        incoming=float(depths[k])-float(depths[k-1])
        out.append(max(0,min(float(depths[k]),out[-1]+incoming-lps/1000*(minutes[k]-minutes[k-1])*60/area)))
    return out


def states(op,weather,depths,minutes,*,recorded=False,control=None,cell_m=12):
    before=[];after=[]
    reduced=local_drain(depths,minutes,6.3 if op=='backup-pump' else .25*1000 if op=='portable-pump' else weather.get('drain_capacity_lps',6)*weather.get('blockage_pct',65)/100,cell_m**2)
    for k,minute in enumerate(minutes):
        water=float(depths[k]);fraction=minute/60
        pair=[]
        for done in (False,True):
            gust=float(weather.get('gust_mps',0)) if minute else 0
            w={**weather,'gust_mps':gust}
            base=consequence_scenario.state(op,w,done) if op!='portable-pump' else {}
            row={'water_m':round(water,3),'broken_panes':0,'rain_inside_l':0,'tree_fallen':False,
                 'collapse_m2':0,'pump_on':False,'lane_open':True,'wet_contents':0,'restricted':False}
            if op in ('secure-items','latch-windows'):
                row.update(broken_panes=base['broken_panes'],rain_inside_l=round(base['rain_inside_l']*fraction),loose_released=base.get('loose_released',0))
                row['summary']=f"{row['broken_panes']} broken panes · {row['rain_inside_l']} L rain indoors"
            elif op=='prune-defect':
                q=.613*gust**2*(.65 if done else 1)
                capacity=.613*26**2*(1 if water>.1 else 1.2)
                row.update(tree_fallen=q>capacity,lane_open=q<=capacity)
                row['summary']='Tree across road' if row['tree_fallen'] else 'Tree standing · road open'
            elif op=='engineered-support':
                area=12 if .613*gust**2>.613*26**2*(1.7 if done else 1) else 0
                row.update(collapse_m2=area,lane_open=not area)
                row['summary']=f'{area} m² façade collapse' if area else 'Façade stays in place'
            elif op in ('fit-barrier','sandbag-wall','move-contents'):
                barrier=.6 if op=='fit-barrier' else .4
                inside=max(0,water-.15)
                if done and op!='move-contents' and water<=barrier:inside=0
                wet=10 if inside>(1 if done and op=='move-contents' else .05) else 0
                row.update(water_m=round(inside,3),wet_contents=wet,barrier=bool(done and op!='move-contents'))
                row['summary']=f'{round(inside*100)} cm entry water · {wet} boxes wet'
            elif op in ('clear-inlet','portable-pump','backup-pump'):
                power=gust<22 and water<.3
                pump_on=done and (op=='portable-pump' or (op=='backup-pump' and (power or water<1)))
                active=done and (op!='backup-pump' or pump_on)
                local=reduced[k] if active else water
                if op=='portable-pump' and recorded:
                    # The saved run already includes this pump. Its removal is
                    # approximated locally, bounded by the no-preparation field.
                    local=water if done else max(water,min(float(control[k]),water+250/1000*minute*60/(cell_m**2)))
                if op=='clear-inlet' and recorded:local=water  # saved field cannot isolate this drain's marginal benefit
                row.update(water_m=round(local,3),pump_on=pump_on and (done or op=='backup-pump'))
                suffix=(' · pump running' if row['pump_on'] else ' · pump off') if op in ('portable-pump','backup-pump') else ''
                row['summary']=f'{round(local*100)} cm local water'+suffix
            else:
                # Clearing loose material cannot remove floodwater or new storm debris.
                debris=0 if done and op=='clear-route' else 2.4
                new_debris=.4 if gust>=22 else 0
                width=max(0,3-max(debris,new_debris))
                passable=water<.3 and width>=2
                restricted=done and op=='install-cordon' and not passable
                row.update(lane_open=passable,restricted=restricted,lane_width_m=round(width,1))
                row['summary']='Flooded road · entry blocked by cordon' if restricted else 'Road impassable · entry uncontrolled' if not passable else 'Road passable'
            pair.append(row)
        before.append(pair[0]);after.append(pair[1])
    return before,after


def load_pack(root,run,model,label,reference=None):
    folder=Path(root)/run/model
    assets_path=risk_assets.FOLDER/'assets.json'
    files=[folder/f'{model}.json',folder/f'{label}_layers.json',folder/'control_layers.json']
    review=ROOT/'evidence/viewer-action-review.json'
    if review.is_file():files.append(review)
    binding=folder/'workplan.json'
    frozen=False
    if binding.is_file():
        files.append(binding)
        frozen=json.loads(binding.read_text()).get('version')=='six-hazard-resources-1'
    if not frozen:files.append(assets_path)
    return _pack(str(root),run,model,label,tuple(p.stat().st_mtime_ns for p in files),reference)


@lru_cache(maxsize=12)
def _pack(root,run,model,label,versions,reference=None):
    folder=Path(root)/run/model
    log=json.loads((folder/f'{model}.json').read_text())
    binding_path=folder/'workplan.json'
    if binding_path.is_file():
        binding=json.loads(binding_path.read_text())
        if binding.get('version')=='six-hazard-resources-1':
            from .hazard_workplans import public
            return public(binding,label,reference)
    layers=json.loads((folder/f'{label}_layers.json').read_text())
    control=json.loads((folder/'control_layers.json').read_text())
    weather=dict(log['parameters'])
    p50=next((s for s in (log.get('forcing') or {}).get('scenarios',[]) if s['name']=='P50'),{})
    weather.update({k:p50[k] for k in ('wind_mps','gust_mps','wind_from_deg') if k in p50})
    storm_id=hashlib.sha256(json.dumps({'parameters':weather,'forcing':log.get('forcing')},sort_keys=True).encode()).hexdigest()
    assets=json.loads((risk_assets.FOLDER/'assets.json').read_text());field=Field(layers);basefield=Field(control)
    operations=json.loads((ROOT/'evidence/preventive-work.json').read_text())['operations']
    byop={o['id']:o for o in operations}
    byop['portable-pump']={**byop['backup-pump'],'id':'portable-pump','hazard':'flood','label':'Deploy portable pump at recorded cell','role':'pump crew','people':2,'minutes':[30,60],'overhead':[10,20],
        'equipment':{'pump':1},'evidence':'The run uses a 250 L/s sink at this 12 m cell. Removing one pump is estimated by local flow balance; it is not a full-city rerun.','source_ids':[],
        'conditions':['Pump, crew and safe discharge available. Fuel and power availability are not recorded.']}
    building_index=cKDTree([[b['position'][0]*88000,b['position'][1]*111320] for b in assets['buildings']])
    def site(point,label,kind='building',asset=None):
        idx=int(building_index.query([point[0]*88000,point[1]*111320])[1]);b=assets['buildings'][idx]
        road=min(assets['roads'],key=lambda r:metres(point,r['position']))
        if label.startswith('Building '):label=road['name']+' vicinity · '+label
        return {'position':point,'label':label,'kind':kind,'building_height_m':b['height_m'],'display_height_m':max((x['height_m'] for x in assets['buildings'] if metres(point,x['position'])<45),default=b['height_m']),'building_geometry':b['geometry'],
                'heading':math.degrees(b.get('angle_rad',0)),'height_m':(asset or {}).get('height_m',12),
                'road_geometry':(asset or {}).get('geometry') if kind=='road' else None}
    rows=[];seen=set()
    def add(op,identity,point,title,recorded=False,kind='building',asset=None):
        key=f'{op}:{identity}'
        if key in seen:return
        seen.add(key);o=byop[op];s=site(point,title,kind,asset)
        d=field.at(point);b=basefield.at(point)
        before,after=states(op,weather,d,field.minutes,recorded=recorded,control=b,cell_m=layers['cell_m'])
        changed=any(x!=y for x,y in zip(before,after))
        harmkeys=['water_m','broken_panes','rain_inside_l','tree_fallen','collapse_m2','wet_contents','lane_open','restricted']
        helps=any(any(x.get(k)!=y.get(k) for k in harmkeys) for x,y in zip(before,after))
        power=[]
        if o['hazard']=='power':
            from shapely.geometry import Point, shape
            nearest=min(assets['power_lines'],key=lambda line:shape(line['geometry']).distance(Point(*point)),default=None)
            if nearest:power=[nearest]
        rows.append({'id':key,'operation_id':op,'hazard':o['hazard'],'label':o['label'],'site':s,'recorded':recorded,
                     'people':o['people'],'role':o['role'],'minutes':[o['minutes'][i]+o['overhead'][i] for i in (0,1)],
                     'power_lines':power,'equipment':o['equipment'],'conditions':o['conditions'],'evidence':o['evidence'],'source_ids':o['source_ids'],
                     'basis':'Extrapolated local mechanism, same P50 field and forcing','changed':changed,'helps':helps,
                     'effect_known':not(op=='clear-inlet' and recorded),'before':before,'after':after,'peak_before':before[-1]['summary'],'peak_after':after[-1]['summary']})
    # Preserve every job in the selected run, not one representative per hazard.
    plan=layers.get('plan',{})
    all_buildings={str(b['fd_id']):b for b in control['buildings']}
    for fid in plan.get('doorway_fd_ids',[]):
        if str(fid) in all_buildings:
            b=all_buildings[str(fid)];add('fit-barrier',fid,[b['lon'],b['lat']],f"Building {fid}",True)
    for i,p in enumerate(layers.get('pumps',[])):add('portable-pump',i,p,f'Pump cell {i+1}',True,'pump')
    for i,p in enumerate(layers.get('drains_cleared',[])):add('clear-inlet',i,p,f'Cleared inlet {i+1}',True,'inlet')
    # Full operation catalogue at multiple exposed mapped locations. This is a
    # bounded candidate cut, not an assertion that every asset needs treatment.
    exposed=sorted(control['buildings'],key=lambda b:b.get('usd',0),reverse=True)[:12]
    for b in exposed:
        point=[b['lon'],b['lat']]
        for op in ('latch-windows','secure-items','engineered-support','fit-barrier','sandbag-wall','move-contents','backup-pump'):
            add(op,b['fd_id'],point,f"Building {b['fd_id']}")
    trees=sorted(assets['trees'],key=lambda t:max(field.at(t['position'])),reverse=True)[:12]
    for t in trees:add('prune-defect',t['id'],t['position'],t['species']+' · '+t['id'],False,'tree',t)
    roads=sorted(assets['roads'],key=lambda r:max(field.at(r['position'])),reverse=True)[:12]
    for r in roads:
        for op in ('clear-route','install-cordon'):add(op,r['id'],r['position'],r['name'],False,'road',r)
    raw=ROOT/'data/raw/jtgq-b7c5.json'
    if raw.is_file():
        drains=json.loads(raw.read_text())
        for i,d in enumerate(drains):
            point=d.get('location',{}).get('coordinates',[0,0])
            if max(field.at(point))>.1:add('clear-inlet',d.get('dpw_node_id',i),point,'Inlet '+str(d.get('dpw_node_id',i)),False,'inlet')
            if sum(r['operation_id']=='clear-inlet' and not r['recorded'] for r in rows)>=12:break
    sources=json.loads((ROOT/'evidence/preventive-work.json').read_text())['sources']
    evidence_path=ROOT/'evidence/planning-sources.json'
    if evidence_path.is_file():sources+=json.loads(evidence_path.read_text()).get('sources',[])
    review=None
    review_path=ROOT/'evidence/viewer-action-review.json'
    if review_path.is_file():
        candidate=json.loads(review_path.read_text())
        if candidate.get('storm_id')==storm_id:review=candidate
    result={'review':review,'run':run,'model':model,'plan':label,'storm_id':storm_id,'weather':weather,'minutes':field.minutes,
            'hazards':NAMES,'actions':rows,'sources':sources,'power_sources':assets['sources'],
            'scope':'All saved plan jobs plus the full work catalogue at up to 12 exposed mapped locations per type. Additional effects are extrapolated, not included in the saved flood-loss total.'}
    binding=folder/'workplan.json'
    if binding.is_file():
        saved=json.loads(binding.read_text())
        if label in saved['entries']:
            from .workplan import annotate
            return annotate(result,saved,label)
    # For archived runs, only original recorded jobs are the model's plan.
    for a in rows:a.update(selected=a['recorded'],applied=a['recorded'],not_selected_reason='Not selected in this archived flood plan')
    return result


def summary(pack):
    return {**pack,'actions':[{k:v for k,v in r.items() if k not in ('before','after')} for r in pack['actions']]}
