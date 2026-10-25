import type {Constructor} from './types';
import type {WorkCandidate,ConsequenceState} from './preventiveWork';

type Layer={removeAll():void;addMany(items:unknown[]):void};
type MapHost={add(layer:unknown):void;ground?:{queryElevation(point:unknown,options:unknown):Promise<{geometry:{z:number}}>};layers?:{find(predicate:(l:{type:string;filter:unknown;visible:boolean})=>boolean):{type:string;filter:unknown;visible:boolean}|undefined}};
type Building={id:string;position:number[];height_m:number;geometry:{type:string;coordinates:number[][][][]}};

/** A geolocated cutaway, never presented as observed damage or a photo reconstruction. */
export async function consequenceScene(map:MapHost,is3d:boolean){
  const [GraphicsLayer,Graphic,Point]=await new Promise<Constructor[]>((resolve,reject)=>window.require(['esri/layers/GraphicsLayer','esri/Graphic','esri/geometry/Point'],(...m)=>resolve(m as Constructor[]),reject));
  const layer=new GraphicsLayer({title:'Illustrative storm consequences',elevationInfo:{mode:'absolute-height'},listMode:'hide'}) as Layer;
  const buildings=map.layers?.find(l=>l.type==='scene');
  const mappedBuildings:Building[]=await fetch('/api/risk/assets').then(r=>r.ok?r.json():{buildings:[]}).then(r=>r.buildings);
  map.add(layer);
  let key='';
  const ground=new Map<string,number>();
  async function anchor(work:WorkCandidate){
    const placement=work.scenario?.placement;
    const indoor=['backup-pump','fit-barrier','sandbag-wall','clear-inlet','move-contents'].includes(work.operation_id);
    const [lon,lat]=(indoor?placement?.building_position:undefined)??placement?.position??work.site.position;
    const groundKey=lon+','+lat;
    if(!ground.has(groundKey)){
      const sample=is3d&&map.ground?await map.ground.queryElevation(new Point({longitude:lon,latitude:lat,spatialReference:{wkid:4326}}),{demResolution:'finest-contiguous'}):null;
      ground.set(groundKey,Number.isFinite(sample?.geometry.z)?sample!.geometry.z:0);
    }
    return [lon,lat,ground.get(groundKey)!+.2];
  }
  return {anchor,async draw(work:WorkCandidate|undefined,mode:'before'|'after',detail=false){
    document.documentElement.dataset.modelDetail=String(detail);
    if(!detail){key='';layer.removeAll();if(buildings)buildings.visible=true;return;}
    if(!work?.scenario){layer.removeAll();return;}
    const scenario=work.scenario,state:ConsequenceState=scenario[mode];
    const stamp=JSON.stringify([work.id,scenario.storm_before,mode,state]);if(stamp===key)return;key=stamp;
    const indoor=['backup-pump','fit-barrier','sandbag-wall','clear-inlet','move-contents'].includes(work.operation_id);
    const [lon,lat,altitude]=await anchor(work),angle=scenario.placement.heading*Math.PI/180;
    if(key!==stamp)return;
    document.documentElement.dataset.consequenceGround=String(altitude);
    const items:unknown[]=[];
    const footprint=scenario.placement.building_geometry;
    const rings=footprint?.coordinates.flat();
    if(buildings){buildings.filter=null;buildings.visible=!indoor;}
    const xy=(x:number,y:number,z=0)=>[lon+(x*Math.cos(angle)+y*Math.sin(angle))/(111320*Math.cos(lat*Math.PI/180)),lat+(-x*Math.sin(angle)+y*Math.cos(angle))/111320,altitude+z];
    const point=(x:number,y:number,z=0)=>{const [longitude,latitude,height]=xy(x,y,z);return {type:'point',longitude,latitude,z:height,spatialReference:{wkid:4326}};};
    const add=(geometry:unknown,symbol:unknown,kind:string)=>items.push(new Graphic({geometry,symbol,attributes:{kind,mode,action:work.id,basis:'Illustrative extrapolation'}}));
    if(indoor&&is3d)for(const b of mappedBuildings){
      const distance=Math.hypot((b.position[0]-lon)*88000,(b.position[1]-lat)*111320);
      if(distance<5||distance>150)continue;
      // A horizontal display cut exposes ground level; footprints remain sourced.
      add({type:'polygon',rings:b.geometry.coordinates.flat().map(r=>r.map(p=>[p[0],p[1],altitude])),hasZ:true,spatialReference:{wkid:4326}},{type:'polygon-3d',symbolLayers:[{type:'extrude',size:Math.min(8,b.height_m),material:{color:[105,118,119,.42]}}]},'mapped-context-cutaway');
    }
    const box=(x:number,y:number,z:number,w:number,d:number,h:number,color:unknown,kind:string,primitive='cube')=>add(point(x,y,z),is3d?{type:'point-3d',symbolLayers:[{type:'object',resource:{primitive},width:w,depth:d,height:h,heading:scenario.placement.heading,anchor:'bottom',material:{color}}]}:{type:'simple-marker',style:primitive==='sphere'?'circle':'square',size:Math.max(5,w*3),color,outline:{color:'#182025',width:.5}},kind);
    const path=(coords:number[][],width:number,color:unknown,kind:string)=>add({type:'polyline',paths:[coords.map(p=>xy(p[0],p[1],p[2]??.05))],hasZ:true,spatialReference:{wkid:4326}},is3d?{type:'line-3d',symbolLayers:[{type:'path',profile:'circle',width,material:{color}}]}:{type:'simple-line',width:Math.max(1,width*3),color},kind);
    const slab=(x:number,y:number,w:number,d:number,h:number,color:unknown,kind:string)=>{
      const ring=[[-w/2,-d/2],[w/2,-d/2],[w/2,d/2],[-w/2,d/2],[-w/2,-d/2]].map(([a,b])=>xy(x+a,y+b,.02));
      add({type:'polygon',rings:[ring],hasZ:true,spatialReference:{wkid:4326}},is3d?{type:'polygon-3d',symbolLayers:[{type:'extrude',size:h,material:{color}}]}:{type:'simple-fill',color,outline:{color:'#7c8d91',width:1}},kind);
    };
    const floor=()=>{
      if(rings){
        add({type:'polygon',rings:rings.map(r=>r.map(p=>[p[0],p[1],altitude])),hasZ:true,spatialReference:{wkid:4326}},is3d?{type:'polygon-3d',symbolLayers:[{type:'extrude',size:.1,material:{color:'#c3c6bf'}}]}:{type:'simple-fill',color:'#c3c6bf',outline:{color:'#53656c',width:2}},'mapped-building-footprint');
        for(const ring of rings)add({type:'polyline',paths:[ring.map(p=>[p[0],p[1],altitude+.2])],hasZ:true,spatialReference:{wkid:4326}},is3d?{type:'line-3d',symbolLayers:[{type:'path',profile:'quad',width:.2,height:1,material:{color:'#84938b'}}]}:{type:'simple-line',width:2,color:'#84938b'},'mapped-building-outline');
      }
      // No basement or room layout is inferred. This is the explicitly assumed
      // 80 m² exposure area, shown inside the real mapped building footprint.
      if(state.water_cm>0)slab(0,0,10,8,state.water_cm/100,[37,151,203,.8],'assumed-flooded-area');
    };
    if(['backup-pump','fit-barrier','sandbag-wall','clear-inlet','move-contents'].includes(work.operation_id)){
      floor();
      if(work.operation_id==='move-contents'){
        const raised=mode==='after';
        if(raised)box(2,1,.05,3.5,2,.9,'#5c6b6f','assumed-storage-shelf');
        for(let i=0;i<10;i++)box(.6+(i%5)*.55,.5+Math.floor(i/5)*.7,raised?1:.13,.45,.55,.45,i<state.wet_contents?'#80503e':'#b78b54',i<state.wet_contents?'assumed-flooded-contents':'assumed-dry-contents');
      }
      if(mode==='after'&&['fit-barrier','sandbag-wall'].includes(work.operation_id))box(0,-4.3,0,4,.3,work.operation_id==='fit-barrier'?.6:.4,'#d2b76e','entrance-barrier');
      if(work.operation_id==='backup-pump'){
        box(-3,-2,.12,.8,.7,.75,state.pump_on?'#55b3a1':'#4a4a48',state.pump_on?'running-pump':'stopped-pump');
        if(mode==='after')box(-5,-5,0,1.5,.9,1,'#d0ac53','backup-supply');
        path([[-3,-2,.4],[-4,-3,.4],[-7,-5,.3]],.16,state.pump_on?'#3fd3ee':'#54595a',state.pump_on?'pumped-discharge':'inactive-pipe');
      }
    }else if(['secure-items','latch-windows'].includes(work.operation_id)){
      slab(0,0,12,7,.12,'#979d99','building-floor');
      box(0,2.6,0,12,.4,5,'#555f61','facade');
      for(let i=0;i<10;i++){
        const x=-4.8+(i%5)*2.35,z=.7+Math.floor(i/5)*2;
        const broken=i<state.broken_panes;
        box(x,2.31,z,1.8,.12,1.5,broken?'#151a1d':'#73bdcf',broken?'broken-window':'intact-window');
        if(broken)for(let s=0;s<3;s++)box(x+(s-1)*.45,-1-(i%2),.14,.3,.35,.05,'#b6dbe1','glass-fragment');
      }
      const released=state.loose_released??0;
      for(let i=0;i<Math.min(6,released);i++)box(-4+i*1.5,-3-(i%2),.13,.6,.75,.35,'#aa7546','wind-debris');
      if(state.rain_inside_l)slab(0,0,10,4,.04,[45,149,198,.65],'indoor-rainwater');
    }else if(work.operation_id==='prune-defect'){
      slab(0,0,7,26,.08,'#545e62','road');
      const height=scenario.placement.tree_height_m;
      const tip=state.tree_fallen?[height*.65,-height*.4,1.5]:[-3,0,height];
      path([[-3,0,.2],tip],.55,'#746047',state.tree_fallen?'fallen-trunk':'standing-trunk');
      const crown=mode==='after'?4:6;
      box(tip[0],tip[1],tip[2]-1,crown,crown,4,'#567a55',state.tree_fallen?'fallen-crown':'standing-crown','sphere');
      if(state.lane_open)path([[1,-10,.16],[1,10,.16]],.15,'#63c7b2','passable-lane');
    }else if(work.operation_id==='engineered-support'){
      slab(0,0,12,8,.1,'#8a8f8a','structural-base');
      if(state.collapse_m2){
        for(let i=0;i<9;i++)box(-4+(i%3)*2.8,-3+Math.floor(i/3)*2,.12,2,1.3,.4+(i%3)*.3,'#c09a79','collapsed-masonry');
      }else{
        box(0,1.8,0,8,.5,4,'#c2b5a3','standing-facade');
        if(mode==='after')for(const x of [-3,0,3])path([[x,-3,.1],[x,1.5,3.7]],.2,'#62b8a4','structural-brace');
      }
    }else{
      slab(0,0,6,28,.08,'#555d60','road');
      for(const x of [-2.6,2.6])path([[x,-13,.12],[x,13,.12]],.08,'#c7c5a7','road-edge');
      if(!state.lane_open)for(let i=0;i<4;i++)path([[-2,-2+i,.4],[1.7,1+i,.4]],.4,'#897451','route-debris');
      else path([[0,-12,.15],[0,12,.15]],.2,'#63c7b2','passable-lane');
      if(work.operation_id==='install-cordon'&&mode==='after')for(const y of [-6,6])box(0,y,0,5,.3,1,'#ddaa48','access-cordon');
    }
    layer.removeAll();layer.addMany(items);
    document.documentElement.dataset.consequenceMode=mode;
    document.documentElement.dataset.consequenceAction=work.id;
    document.documentElement.dataset.consequenceObjects=String(items.length);
    document.documentElement.dataset.consequenceSummary=state.summary;
  }};
}
