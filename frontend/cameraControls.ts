import {clamp,emptyMotion,heading,panOffset,wheelMotion,type CameraInputMode,type CameraMotion} from './cameraInput';

type Camera={heading:number;tilt:number;position:{z:number;longitude:number;latitude:number};clone():Camera};
export type CameraView={
  camera:Camera;center:unknown;scale:number;width:number;height:number;
  container:HTMLElement;navigation:{mouseWheelZoomEnabled:boolean};
  goTo(target:unknown,options?:{animate:boolean}):Promise<unknown>;
};
const mounted=new WeakSet<object>();

export function installCameraControls(view:CameraView):void{
  if(mounted.has(view))return;mounted.add(view);
  const home=view.camera.clone();
  let mode:CameraInputMode='trackpad';
  try{if(localStorage.getItem('camera-input-mode')==='mouse')mode='mouse';}catch{/* Use trackpad controls. */}
  view.navigation.mouseWheelZoomEnabled=mode==='mouse';
  const style=document.createElement('style');style.textContent=`
    .camera-controls{position:fixed;left:12px;bottom:60px;z-index:31;font:12px/1.4 system-ui;color:var(--foreground,#eee)}
    .camera-controls>summary{display:block;list-style:none;width:max-content;cursor:pointer;padding:5px 10px;border:1px solid var(--border,#555);border-radius:6px;background:var(--card,#10151a)}
    .camera-controls>summary::-webkit-details-marker{display:none}.camera-controls[open]>summary{background:var(--muted,#30343a)}
    .camera-control-panel{position:absolute;bottom:36px;left:0;width:252px;padding:12px;background:var(--card,#10151a);border:1px solid var(--border,#555);border-radius:6px;box-shadow:0 5px 20px #0003}
    .camera-controls label{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px}.camera-controls output{font-variant-numeric:tabular-nums}
    .camera-controls input[type=range]{display:block;width:100%;margin:0 0 12px;accent-color:var(--foreground,#eee)}
    .camera-controls button,.camera-controls select{font:inherit;color:inherit;background:var(--card,#10151a);border:1px solid var(--border,#555);border-radius:5px;min-height:28px;padding:3px 8px;cursor:pointer}
    .camera-control-actions{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:10px}.camera-control-actions button{flex:1}.camera-control-help{font-size:11px;color:var(--muted-foreground,#abb1ba);margin:8px 0 0;line-height:1.6}
    .camera-controls :is(summary,button,select,input):focus-visible{outline:2px solid var(--foreground,#fff);outline-offset:2px}
  `;document.head.append(style);
  const panel=document.createElement('details');panel.className='camera-controls';
  panel.innerHTML=`<summary aria-label="Camera controls">Camera</summary><div class="camera-control-panel">
    <label for="camera-yaw">Yaw <output id="camera-yaw-value"></output></label><input id="camera-yaw" aria-label="Camera yaw" type="range" min="0" max="359" step="1">
    <label for="camera-pitch">Pitch <output id="camera-pitch-value"></output></label><input id="camera-pitch" aria-label="Camera pitch" type="range" min="-90" max="-5" step="1">
    <div class="camera-control-actions"><button type="button" data-camera="north" title="Face north">North</button><button type="button" data-camera="top" title="Look straight down">Top</button><button type="button" data-camera="home" title="Restore the starting view">Reset</button></div>
    <div class="camera-control-actions"><button type="button" data-camera="out" aria-label="Zoom out">−</button><button type="button" data-camera="in" aria-label="Zoom in">+</button><output id="camera-altitude" aria-label="Camera altitude"></output></div>
    <label>Input <select aria-label="Camera input mode"><option value="trackpad">Trackpad</option><option value="mouse">Mouse</option></select></label>
    <p class="camera-control-help"></p></div>`;document.body.append(panel);
  const yaw=panel.querySelector<HTMLInputElement>('#camera-yaw')!,pitch=panel.querySelector<HTMLInputElement>('#camera-pitch')!;
  const input=panel.querySelector<HTMLSelectElement>('select')!;
  const help=panel.querySelector<HTMLElement>('.camera-control-help')!;
  let pending=emptyMotion(),frame=0;
  function sync(){
    const h=heading(view.camera.heading),p=view.camera.tilt-90;
    if(document.activeElement!==yaw)yaw.value=String(Math.round(h)%360);
    if(document.activeElement!==pitch)pitch.value=String(Math.round(p));
    panel.querySelector('output#camera-yaw-value')!.textContent=`${Math.round(h)%360}°`;
    panel.querySelector('output#camera-pitch-value')!.textContent=`${Math.round(p)}°`;
    panel.querySelector('output#camera-altitude')!.textContent=`${Math.round(view.camera.position.z).toLocaleString()} m`;
    panel.dataset.yaw=String(h);panel.dataset.pitch=String(p);panel.dataset.scale=String(view.scale);
  }
  function move(target:unknown){void view.goTo(target,{animate:false}).then(sync).catch(error=>{if(error?.name!=='AbortError')console.warn('Camera movement unavailable',error);});}
  function flush(){
    frame=0;const motion=pending;pending=emptyMotion();
    const change:Record<string,unknown>={};
    if(motion.panX||motion.panY){
      const camera=view.camera.clone(),position=camera.position;
      const offset=panOffset(motion.panX,motion.panY,view.scale,camera.heading,camera.tilt,position.latitude);
      position.longitude=heading(position.longitude+offset.longitude+180)-180;
      position.latitude=clamp(position.latitude+offset.latitude,-84,84);
      view.camera=camera;sync();
    }
    if(motion.zoom)change.scale=clamp(view.scale*Math.exp(clamp(motion.zoom,-2,2)),400,20000000);
    if(motion.yaw)change.heading=heading(view.camera.heading+motion.yaw);
    if(motion.pitch)change.tilt=clamp(view.camera.tilt+motion.pitch,0,85);
    if(Object.keys(change).length)move(change);
  }
  function queue(motion:CameraMotion){for(const key of Object.keys(motion) as (keyof CameraMotion)[])pending[key]+=motion[key];if(!frame)frame=requestAnimationFrame(flush);}
  function updateMode(){
    view.navigation.mouseWheelZoomEnabled=mode==='mouse';input.value=mode;
    help.textContent=mode==='trackpad'?'Two fingers: pan · Pinch: zoom\nShift + scroll: orbit · Arrows: pan':'Drag: pan · Right-drag: orbit\nWheel: zoom · Shift + arrows: orbit';help.style.whiteSpace='pre-line';
  }
  input.onchange=()=>{mode=input.value as CameraInputMode;try{localStorage.setItem('camera-input-mode',mode);}catch{/* Keep this session choice. */}updateMode();};updateMode();
  yaw.oninput=()=>{pending=emptyMotion();move({heading:Number(yaw.value)});};
  pitch.oninput=()=>{pending=emptyMotion();move({tilt:Number(pitch.value)+90});};
  panel.querySelectorAll<HTMLButtonElement>('button[data-camera]').forEach(button=>button.onclick=()=>{
    pending=emptyMotion();
    switch(button.dataset.camera){
      case 'home':move(home.clone());break;
      case 'north':move({heading:0});break;
      case 'top':move({tilt:0});break;
      case 'in':queue({...emptyMotion(),zoom:-.3});break;
      case 'out':queue({...emptyMotion(),zoom:.3});break;
    }
  });
  view.container.addEventListener('wheel',event=>{
    if(event.target instanceof Element&&event.target.closest('button,input,select,textarea,a,[role=dialog]'))return;
    const motion=wheelMotion(event,mode,view.height);if(!motion)return;
    event.preventDefault();event.stopImmediatePropagation();queue(motion);
  },{capture:true,passive:false});
  view.container.addEventListener('keydown',event=>{
    if(event.ctrlKey||event.metaKey||event.altKey)return;
    if(event.target instanceof Element&&event.target.closest('input,textarea,select,[contenteditable=true]'))return;
    const motion=emptyMotion();const amount=event.shiftKey?5:45;
    if(event.key==='Home'){event.preventDefault();event.stopImmediatePropagation();move(home.clone());return;}
    if(event.key==='+'||event.key==='=')motion.zoom=-.3;
    else if(event.key==='-')motion.zoom=.3;
    else if(event.key==='ArrowLeft'){if(event.shiftKey)motion.yaw=-amount;else motion.panX=-amount;}
    else if(event.key==='ArrowRight'){if(event.shiftKey)motion.yaw=amount;else motion.panX=amount;}
    else if(event.key==='ArrowUp'){if(event.shiftKey)motion.pitch=-amount;else motion.panY=-amount;}
    else if(event.key==='ArrowDown'){if(event.shiftKey)motion.pitch=amount;else motion.panY=amount;}
    else return;
    event.preventDefault();event.stopImmediatePropagation();queue(motion);
  },{capture:true});
  let watchFrame=0;
  function watch(){sync();if(panel.open)watchFrame=requestAnimationFrame(watch);}
  panel.addEventListener('toggle',()=>{cancelAnimationFrame(watchFrame);if(panel.open)watch();});
  panel.addEventListener('keydown',event=>{if(event.key==='Escape'){panel.open=false;panel.querySelector('summary')!.focus();}});
  sync();
}
