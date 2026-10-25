type Time=number|null;
type Wire=[number,number,number,number,Time,Time,...number[]];
type Tree=[number,number,number,number,number,number,number,string,string,string,number];
export interface Destruction {
  t:number[];gust:number[];dir_from:number[];footprint_rings?:Record<string,number[][]>;
  buildings:[number,number,Time[],number,number[]][];
  outage:[number,number,number,number[]][];
  trees:Tree[];poles:[number,number,Time,Time][];spans:Wire[];ocs:Wire[];
  lights:[number,number,number,Time,Time,Time,boolean,Time][];
  signals:[number,number,Time,Time,Time,boolean,Time][];
  shelters:[number,number,Time][];masts:[number,number,number,Time,Time][];
  debris:[number,number,number,number,number,number,number,number,number][];
  road_blocks:[number,number,number,string,string,number][];
  tree_strikes:[number,number,number,number,number,number[],number][];
  customers_t:number[];ledger_total_usd:number;unpriced:string[];
}
type Project=(position:number[])=>{x:number;y:number}|null;
export async function destructionLayer(focus:(position:number[])=>void,project:Project):Promise<{set(data:Destruction|undefined):void;draw(seconds:number,context:CanvasRenderingContext2D):void}> {
  let data:Destruction|undefined,last=-1;
  const counts=document.querySelector<HTMLElement>('#destruction-counts')!;
  const feed=document.querySelector<HTMLElement>('#destruction-events')!;
  // Resolve the existing light/dark tokens, including oklch, through the browser.
  function color(token:string):string {
    const probe=document.createElement('span');probe.style.color=`var(${token})`;document.body.append(probe);
    const value=getComputedStyle(probe).color;probe.remove();
    const context=document.createElement('canvas').getContext('2d')!;
    context.fillStyle=value;context.fillRect(0,0,1,1);
    const [r,g,b,a]=context.getImageData(0,0,1,1).data;
    return `rgba(${r},${g},${b},${a/255})`;
  }
  const red=color('--danger-500'),violet=color('--chart-6'),magenta=color('--chart-3'),green=color('--chart-7');
  const yellow=color('--chart-2'),orange=color('--chart-5'),cyan=color('--chart-4'),white=color('--gray-50');
  const ds=[yellow,orange,red,color('--danger-700')];
  let ctx:CanvasRenderingContext2D;
  function path(points:number[][],close=false):boolean {
    const screen=points.map(project);
    if(screen.some(p=>!p))return false;
    ctx.beginPath();screen.forEach((p,i)=>{if(i)ctx.lineTo(p!.x,p!.y);else ctx.moveTo(p!.x,p!.y);});
    if(close)ctx.closePath();return true;
  }
  function line(points:number[][],color:string,width=1,opacity=1):void {
    if(!path(points))return;
    ctx.strokeStyle=color;ctx.lineWidth=width;ctx.globalAlpha=opacity;ctx.stroke();ctx.globalAlpha=1;
  }
  function point(x:number,y:number,color:string,style='circle',size=7,ring=false,angle=0):void {
    const p=project([x,y]);if(!p)return;
    ctx.save();ctx.translate(p.x,p.y);ctx.rotate(angle*Math.PI/180);
    const r=size/2;ctx.beginPath();
    if(style==='x'){ctx.moveTo(-r,-r);ctx.lineTo(r,r);ctx.moveTo(-r,r);ctx.lineTo(r,-r);}
    else if(style==='triangle'){ctx.moveTo(0,-r);ctx.lineTo(r,r);ctx.lineTo(-r,r);ctx.closePath();}
    else if(style==='diamond'){ctx.moveTo(0,-r);ctx.lineTo(r,0);ctx.lineTo(0,r);ctx.lineTo(-r,0);ctx.closePath();}
    else if(style==='square')ctx.rect(-r,-r,size,size);
    else ctx.arc(0,0,r,0,Math.PI*2);
    if(!ring&&style!=='x'){ctx.fillStyle=color;ctx.fill();}
    ctx.strokeStyle=ring||style==='x'?color:white;ctx.lineWidth=ring||style==='x'?1.5:.5;ctx.stroke();ctx.restore();
  }
  function polygon(id:number,flat:number[],color:string,outline=color):void {
    const rings=data?.footprint_rings?.[id]??[flat];
    ctx.beginPath();
    for(const coords of rings){
      for(let i=0;i<coords.length;i+=2){const p=project([coords[i],coords[i+1]]);if(!p)continue;if(i)ctx.lineTo(p.x,p.y);else ctx.moveTo(p.x,p.y);}
      ctx.closePath();
    }
    ctx.fillStyle=color;ctx.fill('evenodd');ctx.strokeStyle=outline;ctx.lineWidth=.7;ctx.stroke();
  }
  return {set(value){
    if(value&&!Array.isArray(value.t)){data=undefined;counts.hidden=false;counts.textContent='Destruction data needs recalculation';feed.hidden=true;return;}
    data=value;last=-1;counts.hidden=feed.hidden=!value;counts.textContent='';feed.replaceChildren();},draw(seconds,context){
    ctx=context;
    if(!data)return;
    let k=-1;for(let i=0;i<data.t.length;i++)if(data.t[i]<=seconds)k=i;
    const changed=k!==last;last=k;if(k<0){counts.textContent='Storm has not started';return;}
    const d=data,t=d.t[k],on=(v:Time)=>v!==null&&v<=t;
    ctx.save();
    const damaged=d.buildings.filter(b=>b[2].some(on));
    for(const b of damaged){const state=b[2].filter(on).length;polygon(b[0],b[4],ds[state-1]);}
    for(const b of d.outage)if(on(b[1]))polygon(b[0],b[3],violet.replace(/,[^,]+\)$/,',0.55)'),violet);
    for(const [wires,base] of [[d.spans,white],[d.ocs,cyan]] as [Wire[],string][]){
      for(const w of wires){const down=on(w[4]),out=on(w[5]);line([[w[0],w[1]],[w[2],w[3]]],down?red:out?violet:base,down?3:out?1.6:.6,down||out?1:.45);}
    }
    for(const p of d.poles)point(p[0],p[1],on(p[2])?red:white,on(p[2])?'x':'circle',on(p[2])?9:2);
    const fallen=d.trees.filter(v=>on(v[5]));
    for(const v of fallen){const a=v[6]*Math.PI/180;line([[v[0],v[1]],[v[0]+Math.sin(a)*v[2]/(111320*Math.cos(v[1]*Math.PI/180)),v[1]+Math.cos(a)*v[2]/111320]],green,1.6);}
    for(const v of d.debris)if(on(v[6])){line([[v[0],v[1]],[v[3],v[4]]],yellow,.8);if(v[8]>=0&&on(v[7]))point(v[3],v[4],yellow,'circle',4);}
    let badSignals=0,darkSignals=0;
    for(const v of d.signals){point(v[0],v[1],green,'circle',2);if(on(v[2])||on(v[3])){badSignals++;point(v[0],v[1],orange,'triangle',8);}if(on(v[4])||(v[5]&&on(v[6]))){darkSignals++;point(v[0],v[1],violet,'circle',9,true);}}
    for(const v of d.shelters)if(on(v[2]))point(v[0],v[1],magenta,'square',8);
    for(const v of d.lights)if(on(v[3]))point(v[0],v[1],white,'triangle',7,false,180);
    for(const v of d.masts??[])if(on(v[3]))point(v[0],v[1],white,'x',9);
    const blocks=d.road_blocks.filter(v=>on(v[2])),strikes=d.tree_strikes.filter(v=>on(v[2]));
    for(const v of blocks)point(v[0],v[1],red,'x',12);
    for(const v of strikes)point(v[0],v[1],magenta,'diamond',9);
    ctx.restore();
    if(!changed)return;
    const downWires=[...d.spans,...d.ocs].filter(v=>on(v[4])).length;
    counts.textContent=`${fallen.length} trees down · ${damaged.length} buildings damaged · ${downWires} spans down · ${d.customers_t[k]} customers without power · ${badSignals} signals damaged / ${darkSignals} dark · ${d.shelters.filter(v=>on(v[2])).length} shelters smashed · ${d.lights.filter(v=>on(v[3])).length} lights down · ${blocks.length} road blocks · ${strikes.length} tree strikes`;
    const incidents=[...blocks.map(v=>({time:v[2],position:v.slice(0,2),text:`${v[3].replaceAll('_',' ')} blocks ${v[4]||'roadway'} (${Math.round(v[5])} m)`})),...strikes.map(v=>({time:v[2],position:v.slice(0,2),text:`Tree into building #${v[3]}`}))].sort((a,b)=>b.time-a.time).slice(0,6);
    feed.replaceChildren();
    for(const incident of incidents){const button=document.createElement('button');button.type='button';button.className='quiet';button.textContent=`${Math.floor(incident.time/60)} min: ${incident.text}`;button.onclick=()=>focus?.(incident.position as number[]);feed.append(button);}
    if(!incidents.length)feed.textContent='No destruction incidents yet';
    if(d.unpriced?.length){const note=document.createElement('span');note.textContent=`Unpriced: ${d.unpriced.join('; ')}`;feed.append(note);}
    document.documentElement.dataset.destructionFrame=String(k);
  }};
}
