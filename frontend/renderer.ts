import {mat4} from 'gl-matrix';
import type {Manifest, RenderHost, RenderNodeConstructor, SceneView, WebGLCoordinates} from './types';

export interface StormRenderer {node:RenderHost; update(manifest:Manifest,grid:Float32Array,flood:Float32Array):void;}

const WATER_VERTEX = `#version 300 es
precision highp float;
layout(location=0) in vec3 position;
layout(location=1) in vec3 up;
layout(location=2) in float depth;
uniform mat4 viewMatrix;
uniform mat4 projection;
uniform float time;
out float waterDepth;
out vec3 local;
void main() {
  waterDepth=depth;
  local=position;
  float ripple=0.018*sin(position.x*0.21+position.y*0.13-time*3.0);
  gl_Position=projection*viewMatrix*vec4(position+up*(max(depth,0.0)+0.12+ripple),1.0);
}`;
const WATER_FRAGMENT = `#version 300 es
precision highp float;
in float waterDepth;
in vec3 local;
uniform float time;
out vec4 color;
void main() {
  if(waterDepth<0.025) discard;
  float deep=smoothstep(0.03,1.8,waterDepth);
  float ripples=0.04*sin(local.x*0.38+local.y*0.26-time*4.0);
  vec3 shallow=vec3(0.12,0.55,0.66);
  vec3 dark=vec3(0.025,0.22,0.36);
  color=vec4(mix(shallow,dark,deep)+ripples,0.42+0.36*deep);
}`;
const WIND_VERTEX = `#version 300 es
precision highp float;
layout(location=0) in vec3 position;
layout(location=1) in float opacity;
layout(location=2) in vec3 opposite;
layout(location=3) in float side;
uniform mat4 viewMatrix;
uniform mat4 projection;
uniform vec2 viewport;
out float alpha;
void main(){
  alpha=opacity;
  vec4 p=projection*viewMatrix*vec4(position,1.0);
  vec4 q=projection*viewMatrix*vec4(opposite,1.0);
  vec2 direction=normalize(q.xy/q.w-p.xy/p.w+vec2(0.000001));
  p.xy+=vec2(-direction.y,direction.x)*side*1.35*2.0/viewport*p.w;
  gl_Position=p;
}`;
const WIND_FRAGMENT = `#version 300 es
precision highp float;
in float alpha;
out vec4 color;
void main(){color=vec4(0.79,0.90,0.96,alpha);}`;

function shader(gl: WebGL2RenderingContext, type: number, source: string): WebGLShader {
  const s=gl.createShader(type)!;
  gl.shaderSource(s,source); gl.compileShader(s);
  if(!gl.getShaderParameter(s,gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s) ?? 'Shader compilation failed');
  return s;
}
function program(gl: WebGL2RenderingContext, vertex: string, fragment: string): WebGLProgram {
  const p=gl.createProgram()!;
  const v=shader(gl,gl.VERTEX_SHADER,vertex),f=shader(gl,gl.FRAGMENT_SHADER,fragment);
  gl.attachShader(p,v); gl.attachShader(p,f); gl.linkProgram(p);
  gl.deleteShader(v); gl.deleteShader(f);
  if(!gl.getProgramParameter(p,gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p) ?? 'Shader linking failed');
  return p;
}
function attribute(gl: WebGL2RenderingContext, slot: number, width: number, data: Float32Array, dynamic=false): WebGLBuffer {
  const b=gl.createBuffer()!;
  gl.bindBuffer(gl.ARRAY_BUFFER,b); gl.bufferData(gl.ARRAY_BUFFER,data,dynamic?gl.DYNAMIC_DRAW:gl.STATIC_DRAW);
  gl.enableVertexAttribArray(slot); gl.vertexAttribPointer(slot,width,gl.FLOAT,false,0,0);
  return b;
}
interface Particle {x:number;y:number;z:number;age:number;life:number;}

export function installStormRenderer(view: SceneView, Node: RenderNodeConstructor, coordinates: WebGLCoordinates,
  manifest: Manifest, grid: Float32Array, flood: Float32Array, sampled: number[][], timeline?:()=>number, playing?:()=>boolean): StormRenderer {
  const {nx,ny,cell_m:cell}=manifest.grid, count=nx*ny;
  const height=grid.subarray(count*3,count*4), u=grid.subarray(count*4,count*5), v=grid.subarray(count*5,count*6);
  const ocean=grid.subarray(count*6,count*7);
  const positions=new Float32Array(count*3), normals=new Float32Array(count*3), depth=new Float32Array(count);
  const geographic=new Float64Array(count*3), world=new Float64Array(count*3);
  sampled.forEach((p,i)=>geographic.set(p,i*3));
  coordinates.toRenderCoordinates(view,geographic,0,{wkid:4326},world,0,count);
  const middle=Math.floor(count/2), origin=new Float32Array(world.slice(middle*3,middle*3+3));
  for(let i=0;i<count;i++) {
    const base=i*3, mag=Math.hypot(world[base],world[base+1],world[base+2]);
    for(let axis=0;axis<3;axis++) {positions[base+axis]=world[base+axis]-origin[axis]; normals[base+axis]=world[base+axis]/mag;}
  }
  const triangles:number[]=[];
  for(let y=0;y<ny-1;y++) for(let x=0;x<nx-1;x++) {
    const a=y*nx+x,b=a+1,c=a+nx,d=c+1;
    if([a,b,c,d].some(i=>!Number.isFinite(height[i])||height[i]>2)) continue;
    if([a,b,c,d].every(i=>ocean[i]>0.5)) continue;
    triangles.push(a,c,b,b,c,d);
  }
  const index=new Uint32Array(triangles);
  const lon=sampled[middle][0]*Math.PI/180, lat=sampled[middle][1]*Math.PI/180;
  const east=[-Math.sin(lon),Math.cos(lon),0], north=[-Math.sin(lat)*Math.cos(lon),-Math.sin(lat)*Math.sin(lon),Math.cos(lat)];
  let seed=4321;
  const random=():number=>{seed=(seed*1664525+1013904223)>>>0;return seed/4294967296;};
  const particles:Particle[]=Array.from({length:1800},()=>({x:random()*(nx-1),y:random()*(ny-1),z:12+random()*135,age:random()*6,life:3+random()*5}));
  const windPositions=new Float32Array(particles.length*18),windOpposite=new Float32Array(particles.length*18);
  const windAlpha=new Float32Array(particles.length*6),windSide=new Float32Array(particles.length*6);
  const ends=[0,1,0,0,1,1],sides=[-1,1,1,1,1,-1];
  for(let i=0;i<particles.length;i++)windSide.set(sides,i*6);
  const viewMatrix=new Float64Array(16), gpuView=new Float32Array(16);
  let glReady=false,waterProgram:WebGLProgram,windProgram:WebGLProgram,waterVAO:WebGLVertexArrayObject,windVAO:WebGLVertexArrayObject;
  let depthBuffer:WebGLBuffer,windPositionBuffer:WebGLBuffer,windOppositeBuffer:WebGLBuffer,windAlphaBuffer:WebGLBuffer;
  let last=performance.now(),elapsed=0, lastSurface=-1,lastHydroTime=-1;
  const initialize=(gl:WebGL2RenderingContext):void=>{
    waterProgram=program(gl,WATER_VERTEX,WATER_FRAGMENT); windProgram=program(gl,WIND_VERTEX,WIND_FRAGMENT);
    waterVAO=gl.createVertexArray()!;gl.bindVertexArray(waterVAO);
    attribute(gl,0,3,positions);attribute(gl,1,3,normals);depthBuffer=attribute(gl,2,1,depth,true);
    const ibo=gl.createBuffer()!;gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER,ibo);gl.bufferData(gl.ELEMENT_ARRAY_BUFFER,index,gl.STATIC_DRAW);
    windVAO=gl.createVertexArray()!;gl.bindVertexArray(windVAO);
    windPositionBuffer=attribute(gl,0,3,windPositions,true);windAlphaBuffer=attribute(gl,1,1,windAlpha,true);
    windOppositeBuffer=attribute(gl,2,3,windOpposite,true);attribute(gl,3,1,windSide);
    gl.bindVertexArray(null);glReady=true;
  };
  const StormNode=Node.createSubclass({
    initialize(this:RenderHost) {this.consumes.required.push('composite-color');this.produces='composite-color';},
    render(this:RenderHost,inputs?:Array<{name:string;getAttachment(attachment:number):unknown}>) {
      this.resetWebGLState();
      const gl=this.gl;
      // Cinema mode (cinema.js) grades the photoreal city first; the flood surface and wind then draw on top.
      const host=this as unknown as {acquireOutputFramebuffer():{attachDepth(depth:unknown):void};bindRenderTarget():unknown;resetWebGLState():void};
      const cinema=(window as unknown as {sfCinema?:{state:{active:boolean};post(node:unknown,input:unknown):void}}).sfCinema;
      const input=inputs?.find(i=>i.name==='composite-color');
      let output;
      if(cinema?.state.active&&input) {
        output=host.acquireOutputFramebuffer();
        cinema.post(this,input);
        output.attachDepth(input.getAttachment(gl.DEPTH_STENCIL_ATTACHMENT));
        const fbo=gl.getParameter(gl.FRAMEBUFFER_BINDING),vp=gl.getParameter(gl.VIEWPORT);
        host.resetWebGLState();gl.bindFramebuffer(gl.FRAMEBUFFER,fbo);gl.viewport(vp[0],vp[1],vp[2],vp[3]);
      } else output=this.bindRenderTarget();
      if(document.body.classList.contains('planning-mode')&&!document.body.classList.contains('plan-ui-hidden')&&!document.body.classList.contains('research-mode'))return output;
      if(!glReady) initialize(gl);
      const now=performance.now(),dt=playing?.()===false?0:Math.min((now-last)/1000,0.05);last=now;elapsed+=dt;
      // One forward 60-minute scenario, looped for viewing. Starts at 35 minutes.
      const simulationSeconds=timeline?.()??(2100+elapsed*15)%manifest.times_seconds.at(-1)!;
      const frame=Math.min(Math.floor(simulationSeconds/120),manifest.times_seconds.length-2),blend=(simulationSeconds%120)/120;
      if(elapsed-lastSurface>0.08||Math.abs(simulationSeconds-lastHydroTime)>.05) {
        let wet=0;
        for(let i=0;i<count;i++) {
          depth[i]=ocean[i]>0.5?-1:flood[frame*count+i]*(1-blend)+flood[(frame+1)*count+i]*blend;
          if(depth[i]>0.025) wet++;
        }
        gl.bindBuffer(gl.ARRAY_BUFFER,depthBuffer);gl.bufferSubData(gl.ARRAY_BUFFER,0,depth);lastSurface=elapsed;lastHydroTime=simulationSeconds;
        window.storm.wetVertices=wet;
      }
      // Illustrative gust envelope, bounded by the observed mean/peak gust.
      // This is animation forcing, not a reconstruction of sub-minute weather.
      const gustPulse=Math.pow(0.5+0.5*Math.sin(elapsed*0.75),3);
      const theta=manifest.wind.from_deg*Math.PI/180;
      const mean=manifest.wind.mean_mps,instant=mean+(manifest.wind.gust_mps-mean)*gustPulse;
      const freeU=-(mean||instant)*Math.sin(theta),freeV=-(mean||instant)*Math.cos(theta);
      const gustFactor=mean>0?instant/mean:1;
      for(let i=0;i<particles.length;i++) {
        const p=particles[i]; p.age+=dt;
        if(p.age>p.life||p.x<1||p.x>=nx-2||p.y<1||p.y>=ny-2) {
          p.x=1+random()*(nx-3);p.y=1+random()*(ny-3);p.z=10+random()*130;p.age=0;p.life=4+random()*5;
        }
        const j=Math.floor(p.y)*nx+Math.floor(p.x),above=Math.min(p.z/100,1);
        const ux=(mean>0?u[j]*(1-above)+freeU*above:freeU)*gustFactor,vy=(mean>0?v[j]*(1-above)+freeV*above:freeV)*gustFactor;
        p.x+=ux*dt*2/cell;p.y-=vy*dt*2/cell;
        if(Number.isFinite(height[j])&&height[j]>0&&p.z<height[j]+5) p.z=height[j]+8;
        const tail=2.2+0.6*Math.sin(elapsed*2+i),fade=Math.min(p.age,1,p.life-p.age)*0.55;
        const column=Math.min(nx-2,Math.max(0,Math.floor(p.x))),row=Math.min(ny-2,Math.max(0,Math.floor(p.y)));
        const positionIndex=row*nx+column,fractionalX=p.x-column,fractionalY=p.y-row;
        for(let k=0;k<6;k++) {
          for(let axis=0;axis<3;axis++) {
            const head=positions[positionIndex*3+axis]+east[axis]*fractionalX*cell-north[axis]*fractionalY*cell+normals[positionIndex*3+axis]*p.z;
            const offset=tail*(east[axis]*ux+north[axis]*vy);
            windPositions[i*18+k*3+axis]=head-(ends[k]===0?offset:0);
            windOpposite[i*18+k*3+axis]=head-(ends[k]===1?offset:0);
          }
          windAlpha[i*6+k]=Math.max(0,fade)*(ends[k]===1?1:0.08);
        }
      }
      mat4.translate(viewMatrix,this.camera.viewMatrix,origin);gpuView.set(viewMatrix);
      gl.enable(gl.DEPTH_TEST);gl.depthFunc(gl.LEQUAL);gl.depthMask(false);gl.disable(gl.CULL_FACE);
      gl.enable(gl.BLEND);gl.blendFunc(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA);
      gl.useProgram(waterProgram);gl.bindVertexArray(waterVAO);
      gl.uniformMatrix4fv(gl.getUniformLocation(waterProgram,'viewMatrix'),false,gpuView);
      gl.uniformMatrix4fv(gl.getUniformLocation(waterProgram,'projection'),false,this.camera.projectionMatrix);
      gl.uniform1f(gl.getUniformLocation(waterProgram,'time'),elapsed);
      gl.drawElements(gl.TRIANGLES,index.length,gl.UNSIGNED_INT,0);
      gl.useProgram(windProgram);gl.bindVertexArray(windVAO);
      gl.bindBuffer(gl.ARRAY_BUFFER,windPositionBuffer);gl.bufferSubData(gl.ARRAY_BUFFER,0,windPositions);
      gl.bindBuffer(gl.ARRAY_BUFFER,windAlphaBuffer);gl.bufferSubData(gl.ARRAY_BUFFER,0,windAlpha);
      gl.bindBuffer(gl.ARRAY_BUFFER,windOppositeBuffer);gl.bufferSubData(gl.ARRAY_BUFFER,0,windOpposite);
      gl.uniformMatrix4fv(gl.getUniformLocation(windProgram,'viewMatrix'),false,gpuView);
      gl.uniformMatrix4fv(gl.getUniformLocation(windProgram,'projection'),false,this.camera.projectionMatrix);
      gl.uniform2f(gl.getUniformLocation(windProgram,'viewport'),gl.drawingBufferWidth,gl.drawingBufferHeight);
      gl.drawArrays(gl.TRIANGLES,0,particles.length*6);
      gl.bindVertexArray(null);this.resetWebGLState();
      window.storm.frame=frame;window.storm.elapsedSeconds=simulationSeconds;window.storm.renderedFrames++;
      window.storm.windSegments=particles.length;
      {
        document.documentElement.dataset.stormFrame=String(frame);
        document.documentElement.dataset.stormRenderedFrames=String(window.storm.renderedFrames);
        document.documentElement.dataset.stormWetVertices=String(window.storm.wetVertices);
        document.documentElement.dataset.stormWindSegments=String(particles.length);
      }
      if(!document.hidden&&playing?.()!==false)this.requestRender();
      return output;
    }
  });
  const node=new StormNode({view});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden){last=performance.now();node.requestRender();}});
  return {node,update(nextManifest,nextGrid,nextFlood){
    manifest=nextManifest;flood=nextFlood;
    u.set(nextGrid.subarray(count*4,count*5));v.set(nextGrid.subarray(count*5,count*6));
    lastSurface=-1;lastHydroTime=-1;window.storm.manifest=manifest;node.requestRender();
  }};
}
