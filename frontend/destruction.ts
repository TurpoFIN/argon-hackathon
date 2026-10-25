import type {Constructor} from './types';

type Frame={time_seconds:number;tree_ids:string[];down_line_ids:string[];structure_ids:string[];unpowered_ids:string[];blocked_route_ids:string[];struck_building_ids:string[]};
type Event={kind:string;id?:string;time_seconds:number;fall_dir_deg?:number};
export interface Destruction {
  frames:Frame[];events:Event[];
  geometry:{trees:Record<string,number[]>;buildings:Record<string,{position:number[];height_m:number;geometry:{type:string;coordinates:number[][][]|number[][][][]}}>;
    lines:Record<string,{kind:string;coordinates:number[][]|number[][][]}>;roads:Record<string,number[][]>};
}
interface Layer {removeAll():void;addMany(graphics:unknown[]):void;}
export async function destructionLayer(map:{add(layer:unknown):void}):Promise<{set(data:Destruction|undefined):void;draw(seconds:number):void}> {
  const [GraphicsLayer,Graphic]=await new Promise<Constructor[]>((resolve,reject)=>window.require(['esri/layers/GraphicsLayer','esri/Graphic'],(...m)=>resolve(m as Constructor[]),reject));
  const layer=new GraphicsLayer({title:'Storm destruction',listMode:'hide'}) as Layer;map.add(layer);
  let data:Destruction|undefined,last=-1;
  let falls=new Map<string,Event>();
  const counts=document.querySelector<HTMLElement>('#destruction-counts')!;
  // Resolve the page's palette through the browser, including oklch tokens.
  function color(token:string):string {
    const probe=document.createElement('span');probe.style.color=`var(${token})`;document.body.append(probe);
    const value=getComputedStyle(probe).color;probe.remove();
    const context=document.createElement('canvas').getContext('2d')!;
    context.fillStyle=value;context.fillRect(0,0,1,1);
    const [r,g,b,a]=context.getImageData(0,0,1,1).data;
    return `rgba(${r},${g},${b},${a/255})`;
  }
  const red=color('--danger-500'),violet=color('--chart-6'),magenta=color('--chart-3'),tree=color('--chart-5');
  function line(paths:number[][][],color:string,dash=false):unknown {
    return new Graphic({geometry:{type:'polyline',paths,spatialReference:{wkid:4326}},symbol:{type:'simple-line',color,width:2.5,style:dash?'dash':'solid'}});
  }
  return {set(value){data=value;last=-1;falls=new Map(value?.events.filter(e=>e.kind==='tree_fall'&&e.id).map(e=>[e.id!,e]));layer.removeAll();counts.hidden=!value;counts.textContent='';},draw(seconds){
    if(!data)return;
    let k=-1;for(let i=0;i<data.frames.length;i++)if(data.frames[i].time_seconds<=seconds)k=i;
    if(k===last)return;last=k;layer.removeAll();if(k<0){counts.textContent='Storm has not started';return;}
    const frame=data.frames[k],g=data.geometry,graphics:unknown[]=[];
    for(const id of frame.tree_ids){
      const t=g.trees[id],event=falls.get(id);if(!t||event?.fall_dir_deg===undefined)continue;
      const a=event.fall_dir_deg*Math.PI/180,dx=Math.sin(a)*t[2],dy=Math.cos(a)*t[2];
      graphics.push(line([[[t[0],t[1]],[t[0]+dx/(111320*Math.cos(t[1]*Math.PI/180)),t[1]+dy/111320]]],tree));
    }
    for(const id of frame.down_line_ids){const l=g.lines[id];if(l){const paths=typeof l.coordinates[0][0]==='number'?[l.coordinates as number[][]]:l.coordinates as number[][][];graphics.push(line(paths,red,l.kind==='trolley'));}}
    for(const [ids,color] of [[frame.unpowered_ids,violet],[frame.structure_ids,red],[frame.struck_building_ids,magenta]] as [string[],string][]){
      for(const id of ids){const b=g.buildings[id];if(!b)continue;
        const rings=b.geometry.type==='MultiPolygon'?(b.geometry.coordinates as number[][][][]).flat():b.geometry.coordinates;
        graphics.push(new Graphic({geometry:{type:'polygon',rings,spatialReference:{wkid:4326}},symbol:{type:'simple-fill',color:'transparent',outline:{color,width:2}}}));
        graphics.push(new Graphic({geometry:{type:'point',longitude:b.position[0],latitude:b.position[1],spatialReference:{wkid:4326}},symbol:{type:'simple-marker',style:'diamond',color,size:7,outline:{width:0}}}));
      }
    }
    for(const id of frame.blocked_route_ids)if(g.roads[id])graphics.push(line([g.roads[id]],red));
    layer.addMany(graphics);
    counts.textContent=`${frame.tree_ids.length} trees down · ${frame.down_line_ids.length} lines down · ${frame.unpowered_ids.length} buildings without power · ${frame.blocked_route_ids.length} routes blocked · ${data.events.filter(e=>e.kind==='tree_strike'&&e.time_seconds<=seconds).length} trees into buildings`;
    document.documentElement.dataset.destructionFrame=String(k);
  }};
}
