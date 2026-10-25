export type CameraInputMode='trackpad'|'mouse';
export type CameraMotion={panX:number;panY:number;yaw:number;pitch:number;zoom:number};
export type WheelPacket={deltaX:number;deltaY:number;deltaMode:number;ctrlKey:boolean;shiftKey:boolean};
export const emptyMotion=():CameraMotion=>({panX:0,panY:0,yaw:0,pitch:0,zoom:0});
export const clamp=(value:number,min:number,max:number)=>Math.max(min,Math.min(max,value));
export const heading=(value:number)=>((value%360)+360)%360;

// Translate in the horizontal camera plane. Panning must not retarget a roof
// picked by the cursor: doing so lets SceneView change camera height and zoom.
export function panOffset(dx:number,dy:number,scale:number,yaw:number,tilt:number,latitude:number){
  const angle=yaw*Math.PI/180,metresPerPixel=scale*.0254/96;
  const forward=-dy*metresPerPixel/Math.max(.1,Math.cos(tilt*Math.PI/180));
  const right=dx*metresPerPixel;
  const east=right*Math.cos(angle)+forward*Math.sin(angle);
  const north=-right*Math.sin(angle)+forward*Math.cos(angle);
  return {longitude:east/(111320*Math.max(.1,Math.cos(latitude*Math.PI/180))),latitude:north/111320};
}

// Chromium represents trackpad pinch as a Ctrl-wheel packet. Keep it separate
// from scrolling so a pinch never pans the map or zooms the browser page.
export function wheelMotion(event:WheelPacket,mode:CameraInputMode,pageHeight:number):CameraMotion|null{
  if(mode==='mouse'&&!event.ctrlKey&&!event.shiftKey)return null;
  const unit=event.deltaMode===1?16:event.deltaMode===2?pageHeight:1;
  const dx=clamp(event.deltaX*unit,-500,500),dy=clamp(event.deltaY*unit,-500,500);
  const motion=emptyMotion();
  if(event.ctrlKey)motion.zoom=dy*.01;
  else if(event.shiftKey){motion.yaw=dx*.18;motion.pitch=dy*.18;}
  else{motion.panX=dx;motion.panY=dy;}
  return motion;
}
