import test from 'node:test';
import assert from 'node:assert/strict';
import {clamp,emptyMotion,heading,panOffset,wheelMotion} from '../frontend/cameraInput.ts';
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
