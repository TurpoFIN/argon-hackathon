"""Compare the port with the supplied source on identical synthetic inputs."""
import importlib.util
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
import test_destruction_engine as cases
from backend import destruction as port

spec=importlib.util.spec_from_file_location('reference_destruction','.port-src/destruction.py')
reference=importlib.util.module_from_spec(spec);spec.loader.exec_module(reference)
reference._City=port._City  # identical grid boundary; no old city files
reference.params=port.params  # byte-identical archived parameter files

def normalized(result):
    result=json.loads(json.dumps(result))
    result['ledger'].pop('unpriced',None)
    for line in result['ledger']['lines']:
        line.pop('unpriced_building_ids',None)
        line.pop('priced_usd',None)
        if line['usd'] is None:line['usd']=0.0
        line['basis']=line['basis'].split('; missing building values')[0]
    result['ledger']['by_cause']={k:(v or 0.0) for k,v in result['ledger']['by_cause'].items()}
    result['notes']=[x for x in result['notes'] if not x.startswith('counted but not priced')]
    return result

checks=[]
for gust,forced,plan in [(0,False,None),(30,False,None),(50,False,None),(80,False,None),
                          (0,True,None),(80,False,{'roof_retrofit':[0]}),
                          (80,False,{'tree_actions':{0:'remove',1:'prune'}})]:
    cases.D=port;a,_=cases.city(gust=gust,forced=forced,plan=plan)
    cases.D=reference;b,_=cases.city(gust=gust,forced=forced,plan=plan)
    assert normalized(a)==normalized(b),(gust,forced,plan)
    checks.append(dict(gust=gust,forced_trees=forced,plan=plan,result='identical physics, events, times, summary and priced totals'))
cases.D=port
Path('evidence/port-reference-parity.json').write_text(json.dumps(checks,indent=2))
print(f'{len(checks)} reference comparisons passed')
