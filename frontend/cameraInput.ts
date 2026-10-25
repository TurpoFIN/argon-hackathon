export type CameraInputMode='trackpad'|'mouse';
export type CameraMotion={panX:number;panY:number;yaw:number;pitch:number;zoom:number};
export type WheelPacket={deltaX:number;deltaY:number;deltaMode:number;ctrlKey:boolean;shiftKey:boolean};
export const emptyMotion=():CameraMotion=>({panX:0,panY:0,yaw:0,pitch:0,zoom:0});
export const clamp=(value:number,min:number,max:number)=>Math.max(min,Math.min(max,value));
export const heading=(value:number)=>((value%360)+360)%360;

export function zoomPosition(position:{longitude:number;latitude:number;z:number},yaw:number,tilt:number,amount:number){
  const z=clamp(position.z*Math.exp(clamp(amount,-2,2)),20,5000000);
  // Move along the view ray, rather than asking SceneView to infer a new
  // distance from map scale (which depends on the terrain under the centre).
  const distance=(position.z-z)*Math.tan(clamp(tilt,0,85)*Math.PI/180);
  const bearing=yaw*Math.PI/180,lat=position.latitude*Math.PI/180,arc=distance/6378137;
  const nextLat=Math.asin(clamp(Math.sin(lat)*Math.cos(arc)+Math.cos(lat)*Math.sin(arc)*Math.cos(bearing),-1,1));
  const nextLon=position.longitude+Math.atan2(Math.sin(bearing)*Math.sin(arc)*Math.cos(lat),Math.cos(arc)-Math.sin(lat)*Math.sin(nextLat))*180/Math.PI;
  return {longitude:heading(nextLon+180)-180,latitude:nextLat*180/Math.PI,z};
}

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
