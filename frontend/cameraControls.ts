import {clamp,emptyMotion,heading,panOffset,wheelMotion,zoomPosition,type CameraInputMode,type CameraMotion} from './cameraInput';

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
  let pending=emptyMotion(),frame=0;
  function move(target:unknown){void view.goTo(target,{animate:false}).catch(error=>{if(error?.name!=='AbortError')console.warn('Camera movement unavailable',error);});}
  function flush(){
    frame=0;const motion=pending;pending=emptyMotion();
    const change:Record<string,unknown>={};
    if(motion.panX||motion.panY||motion.zoom){
      const camera=view.camera.clone(),position=camera.position;
      if(motion.panX||motion.panY){
        const offset=panOffset(motion.panX,motion.panY,view.scale,camera.heading,camera.tilt,position.latitude);
        position.longitude=heading(position.longitude+offset.longitude+180)-180;
        position.latitude=clamp(position.latitude+offset.latitude,-84,84);
      }
      if(motion.zoom)Object.assign(position,zoomPosition(position,camera.heading,camera.tilt,motion.zoom));
      view.camera=camera;
    }
    if(motion.yaw)change.heading=heading(view.camera.heading+motion.yaw);
    if(motion.pitch)change.tilt=clamp(view.camera.tilt+motion.pitch,0,85);
    if(Object.keys(change).length)move(change);
  }
  function queue(motion:CameraMotion){for(const key of Object.keys(motion) as (keyof CameraMotion)[])pending[key]+=motion[key];if(!frame)frame=requestAnimationFrame(flush);}
  view.container.addEventListener('wheel',event=>{
    if(event.target instanceof Element&&event.target.closest('button,input,select,textarea,a,[role=dialog]'))return;
    const motion=wheelMotion(event,mode,view.height);if(!motion)return;
    event.preventDefault();event.stopImmediatePropagation();queue(motion);
  },{capture:true,passive:false});
  view.container.addEventListener('keydown',event=>{
    if(event.ctrlKey||event.metaKey||event.altKey)return;
    if(event.target instanceof Element&&event.target.closest('input,textarea,select,[contenteditable=true]'))return;
    const motion=emptyMotion();const amount=event.shiftKey?5:45;
    if(event.key==='Home'||event.key.toLowerCase()==='n'||event.key.toLowerCase()==='t'){
      event.preventDefault();event.stopImmediatePropagation();pending=emptyMotion();
      move(event.key==='Home'?home.clone():event.key.toLowerCase()==='n'?{heading:0}:{tilt:0});return;
    }
    if(event.key==='+'||event.key==='=')motion.zoom=-.3;
    else if(event.key==='-')motion.zoom=.3;
    else if(event.key==='ArrowLeft'){if(event.shiftKey)motion.yaw=-amount;else motion.panX=-amount;}
    else if(event.key==='ArrowRight'){if(event.shiftKey)motion.yaw=amount;else motion.panX=amount;}
    else if(event.key==='ArrowUp'){if(event.shiftKey)motion.pitch=-amount;else motion.panY=-amount;}
    else if(event.key==='ArrowDown'){if(event.shiftKey)motion.pitch=amount;else motion.panY=amount;}
    else return;
    event.preventDefault();event.stopImmediatePropagation();queue(motion);
  },{capture:true});
}
