import type {Constructor,WorldData,WorldFrame,WorldCrew} from './types';

export function frameAt(world:WorldData,seconds:number):WorldFrame {
  return world.frames[Math.min(world.frames.length-1,Math.max(0,Math.floor(seconds/30)))];
}

interface GraphicLayer {removeAll():void;addMany(graphics:unknown[]):void;}
interface MapHost {add(layer:unknown):void;}
function crewPosition(crew:WorldCrew,elapsed:number):number[] {
  let position=crew.position,distance=crew.state==='traveling'?elapsed*1.4:0;
  for(const target of crew.route){
    const dx=(target[0]-position[0])*111320*Math.cos(position[1]*Math.PI/180),dy=(target[1]-position[1])*111320;
    const length=Math.hypot(dx,dy);
    if(length>distance)return position.map((v,i)=>v+(target[i]-v)*distance/length);
    distance-=length;position=target;
  }
  return position;
}
export async function worldLayer(map:MapHost,is3d:boolean):Promise<{set(data:WorldData|undefined):void;draw(seconds:number):void}> {
  const [Layer,Graphic]=await new Promise<Constructor[]>((resolve,reject)=>window.require(['esri/layers/GraphicsLayer','esri/Graphic'],(...m)=>resolve(m as Constructor[]),reject));
  const layer=new Layer({title:'Drains, debris and clearing crews',elevationInfo:{mode:'relative-to-ground',offset:.8},listMode:'hide'}) as GraphicLayer;
  map.add(layer);
  let world:WorldData|undefined,last=-1;
  function point(position:number[],symbol:unknown,attributes:unknown):unknown {
    return new Graphic({geometry:{type:'point',longitude:position[0],latitude:position[1],spatialReference:{wkid:4326}},symbol,attributes});
  }
  function marker(color:string,size:number,style='circle'):unknown {
    return {type:'simple-marker',style,color,size,outline:{color:'#172126',width:.7}};
  }
  return {set(data){world=data;last=-1;layer.removeAll();},draw(seconds){
    if(!world)return;
    const key=Math.floor(seconds/2);if(key===last)return;last=key;
    const frame=frameAt(world,seconds);
    const graphics:unknown[]=[], targets=new Set(frame.crews.map(c=>c.target));
    if(world.perception.bbox){
      const [w,s,e,n]=world.perception.bbox;
      for(const zone of world.perception.zones??[]){
        const [t,l,b,r]=zone.box,left=w+(e-w)*l/1000,right=w+(e-w)*r/1000,top=n-(n-s)*t/1000,bottom=n-(n-s)*b/1000;
        graphics.push(new Graphic({geometry:{type:'polygon',rings:[[[left,top],[right,top],[right,bottom],[left,bottom],[left,top]]],spatialReference:{wkid:4326}},symbol:{type:'simple-fill',color:[83,176,144,.05],outline:{color:[83,176,144,.65],width:1,style:'dash'}},attributes:{kind:'candidate source',evidence:zone.evidence}}));
      }
    }
    const visible=world.drains.map((d,i)=>({d,i,block:frame.blockage[i]})).filter(v=>v.block>=40||targets.has(v.d.id)).sort((a,b)=>b.block-a.block).slice(0,160);
    for(const {d,block} of visible)graphics.push(point([d.lon,d.lat],marker(block>=70?'#df694b':'#e8b355',is3d?6:7,'square'),{kind:'drain',id:d.id,blockage:block}));
    for(const parcel of frame.debris)graphics.push(point(parcel.position,marker('#dcb769',is3d?3:3),{kind:'debris',id:parcel.id}));
    for(const crew of frame.crews){
      const pos=crewPosition(crew,seconds-frame.time_seconds);
      if(crew.route.length>1)graphics.push(new Graphic({geometry:{type:'polyline',paths:[[crew.position,...crew.route]],spatialReference:{wkid:4326}},symbol:{type:'simple-line',color:[73,195,202,.7],width:2,style:'dash'}}));
      graphics.push(point(pos,marker(crew.state==='clearing'?'#70d09b':'#51c3d1',is3d?11:12,'diamond'),{kind:'crew',id:crew.id,state:crew.state}));
    }
    layer.removeAll();layer.addMany(graphics);
    document.documentElement.dataset.worldFrame=String(Math.floor(seconds/30));
    document.documentElement.dataset.worldDrains=String(visible.length);
    document.documentElement.dataset.worldCrews=String(frame.crews.length);
    document.documentElement.dataset.worldDebris=String(frame.debris.length);
  }};
}
