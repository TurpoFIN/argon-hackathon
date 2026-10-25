// State-machine contract test only: ArcGIS constructor doubles do not prove rendered visuals.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';

class Item {constructor(props={}) {Object.assign(this,props);}}
class Layer extends Item {
  constructor(props){super(props);this.graphics=[];this.visible=true;}
  add(g){this.graphics.push(g);}
  removeAll(){this.graphics=[];}
  destroy(){}
  async applyEdits({updateFeatures}){
    for(const f of updateFeatures){const original=this.source.find(g=>g.attributes.oid===f.attributes.oid);if(original)original.attributes=f.attributes;}
    return {};
  }
}
const el=()=>({innerHTML:'',textContent:'',handlers:{},addEventListener(k,f){this.handlers[k]=f;}});
const stats=el(),note=el(),feed=el(),clock=el(),hide=el();
const panel={hidden:false,querySelector:s=>({'.destruction-stats':stats,'.destruction-note':note,'.destruction-feed':feed,'.destruction-clock':clock,'[data-hide]':hide})[s],querySelectorAll:()=>[]};
const layers=[],map={add:l=>layers.push(l),addMany:ls=>layers.push(...ls),remove:l=>layers.splice(layers.indexOf(l),1)};
const mesh={spatialReference:{isWebMercator:false}},view={goTo:async()=>{}};
let raf;
const context={window:{},document:{documentElement:{dataset:{}}},performance:{now:()=>0},requestAnimationFrame:cb=>{raf=cb;},console,Map,Set};
context.require=(names,callback)=>callback(Layer,Item,Item,Item,Item,Item,Item,Item,Layer,Item,{geographicToWebMercator:x=>x});
vm.createContext(context);
vm.runInContext(fs.readFileSync('loop/destruction-layer.js','utf8'),context);
const api=await context.window.createDestructionLayer({view,map,buildings:mesh,panel});
const base='data/scenarios/cd31da7e0001/';
const data=JSON.parse(fs.readFileSync(base+'destruction.json'));
const trees=JSON.parse(fs.readFileSync(base+'destruction_street_trees.json'));
await api.set(data,trees);
api.setTime(3600);
raf(2000);
await new Promise(resolve=>setImmediate(resolve));
const standing=layers.find(l=>l.title==='Street trees (3D)');
assert.equal(standing.source.length,trees.trees.length);
assert.equal(standing.source.filter(g=>g.attributes.tilt===-88).length,data.trees.length);
assert(stats.innerHTML.includes(String(data.trees.length)));
const collapsed=data.buildings.filter(b=>b[2][3]!==null).length;
assert.equal(mesh.filter?.geometries.length??0,collapsed);
for(const geometry of mesh.filter?.geometries??[]){
  const ring=geometry.rings[0];
  const area=ring.reduce((sum,p,i)=>{const q=ring[(i+1)%ring.length];return sum+p[0]*q[1]-q[0]*p[1];},0);
  assert(area<0,'SceneFilter exterior must be clockwise');
}
assert.deepEqual(Object.keys(context.window),['createDestructionLayer']);
hide.handlers.click({currentTarget:hide});
assert.equal(mesh.filter,null);
assert.equal(standing.visible,false);
hide.handlers.click({currentTarget:hide});
assert.equal(standing.visible,true);
api.setTime(-1);
raf(3000);
await new Promise(resolve=>setImmediate(resolve));
assert.equal(mesh.filter,null);
assert(standing.source.every(g=>g.attributes.tilt===0));
assert(feed.innerHTML.includes('None yet'));
api.setTime(3600,true);
raf(5000);
await new Promise(resolve=>setImmediate(resolve));
assert.equal(standing.source.filter(g=>g.attributes.tilt===-88).length,data.trees.length);
api.setTime(-1);
await api.set(undefined,undefined);
assert.equal(panel.hidden,true);
assert.equal(mesh.filter,null);
assert(!layers.some(l=>l.title==='Street trees (3D)'));
const html=fs.readFileSync('loop/viewer3d.html','utf8');
assert.equal((html.match(/id="toggle"/g)||[]).length,1);
assert(!html.includes('destruction-play'));
new vm.Script(html.match(/<script>([\s\S]*?)<\/script>/)[1]);
console.log(JSON.stringify({standingTrees:trees.trees.length,failedTrees:data.trees.length,collapsed,
  sourceStateMachine:'forward playback, rewind, hide/show, mesh reset and plan clear passed',rendered:false}));
