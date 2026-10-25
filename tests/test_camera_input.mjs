import test from 'node:test';
import assert from 'node:assert/strict';
import {clamp,emptyMotion,heading,panOffset,wheelMotion,zoomPosition} from '../frontend/cameraInput.ts';
const packet=(extra={})=>({deltaX:0,deltaY:0,deltaMode:0,ctrlKey:false,shiftKey:false,...extra});

test('two-finger scrolling pans both axes without zoom or rotation',()=>{
  assert.deepEqual(wheelMotion(packet({deltaX:24,deltaY:-12}),'trackpad',900),{...emptyMotion(),panX:24,panY:-12});
});
test('pinch changes only zoom, even when shift is held',()=>{
  assert.deepEqual(wheelMotion(packet({deltaX:9,deltaY:-4,ctrlKey:true,shiftKey:true}),'trackpad',900),{...emptyMotion(),zoom:-.04});
});
test('shift-scroll rotates yaw and pitch without translating',()=>{
  const result=wheelMotion(packet({deltaX:20,deltaY:-30,shiftKey:true}),'trackpad',900);
  assert.ok(Math.abs(result.yaw-3.6)<1e-9);assert.ok(Math.abs(result.pitch+5.4)<1e-9);
  assert.equal(result.panX,0);assert.equal(result.panY,0);assert.equal(result.zoom,0);
});
test('ordinary mouse-wheel zoom remains owned by ArcGIS',()=>{
  assert.equal(wheelMotion(packet({deltaY:120}),'mouse',900),null);
  assert.notEqual(wheelMotion(packet({deltaY:3,ctrlKey:true}),'mouse',900),null);
});
test('line and page deltas are normalized and bounded',()=>{
  assert.equal(wheelMotion(packet({deltaY:3,deltaMode:1}),'trackpad',900).panY,48);
  assert.equal(wheelMotion(packet({deltaY:1,deltaMode:2}),'trackpad',900).panY,500);
});
test('headings wrap and limits prevent flipping the camera',()=>{
  assert.equal(heading(-10),350);assert.equal(heading(365),5);
  assert.equal(clamp(-5,0,85),0);assert.equal(clamp(90,0,85),85);
});
test('horizontal pan follows camera axes and has no altitude or zoom component',()=>{
  const east=panOffset(100,0,4000,0,60,37.8);
  assert.ok(east.longitude>0);assert.ok(Math.abs(east.latitude)<1e-12);
  const south=panOffset(100,0,4000,90,60,37.8);
  assert.ok(south.latitude<0);assert.ok(Math.abs(south.longitude)<1e-12);
  const back=panOffset(0,100,4000,0,60,37.8);
  assert.ok(Math.abs(back.longitude)<1e-12);assert.ok(back.latitude<0);
  assert.deepEqual(Object.keys(back).sort(),['latitude','longitude']);
});
test('zoom moves along the viewing direction and the opposite step restores height',()=>{
  const start={longitude:-122.38,latitude:37.8,z:690};
  const closer=zoomPosition(start,0,60,-.3);
  assert.ok(closer.latitude>start.latitude);assert.ok(closer.z<start.z);
  const back=zoomPosition(closer,0,60,.3);
  assert.ok(Math.abs(back.z-start.z)<1e-9);
  assert.ok(Math.abs(back.latitude-start.latitude)<1e-9);
  assert.ok(Math.abs(back.longitude-start.longitude)<1e-9);
});
test('top-down zoom changes height without drifting horizontally',()=>{
  const start={longitude:-122.38,latitude:37.8,z:690};
  const farther=zoomPosition(start,245,0,.3);
  assert.ok(farther.z>start.z);assert.ok(Math.abs(farther.longitude-start.longitude)<1e-9);assert.ok(Math.abs(farther.latitude-start.latitude)<1e-9);
});
test('zoom remains finite at the altitude limits and near the horizon',()=>{
  assert.equal(zoomPosition({longitude:0,latitude:0,z:21},0,85,-2).z,20);
  const farther=zoomPosition({longitude:0,latitude:80,z:4999999},270,85,2);
  assert.equal(farther.z,5000000);assert.ok(Object.values(farther).every(Number.isFinite));
});
