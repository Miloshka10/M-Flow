import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const source=readFileSync(new URL('../static/linear-orbit.js',import.meta.url),'utf8');
const {clampOrbitView,createOrbitScene}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));

for(const yaw of [-1e8,-Math.PI,0,Math.PI,1e8]){
  const view=clampOrbitView({yaw,pitch:100});
  assert.ok(view.yaw>=-Math.PI&&view.yaw<=Math.PI);
  assert.equal(view.pitch,.95);
}
assert.deepEqual(clampOrbitView({yaw:NaN,pitch:Infinity}),{yaw:0,pitch:0});
assert.equal(clampOrbitView({yaw:0,pitch:-100}).pitch,-.95);
// Exercise the shared renderer's input/lifecycle contract without a browser GPU.
globalThis.window={devicePixelRatio:1};
globalThis.document={hidden:false,addEventListener(){},removeEventListener(){}};
globalThis.requestAnimationFrame=()=>1;
globalThis.cancelAnimationFrame=()=>{};
let disconnected=0;
globalThis.ResizeObserver=class{constructor(fn){this.fn=fn;}observe(){this.fn([{contentRect:{width:600,height:340}}]);}disconnect(){disconnected++;}};
globalThis.IntersectionObserver=class{constructor(fn){this.fn=fn;}observe(){this.fn([{isIntersecting:true}]);}disconnect(){disconnected++;}};
const listeners=new Map(),captures=new Set();
const noop=()=>{};
const context=Object.fromEntries(['beginPath','stroke','setLineDash','lineTo','moveTo','fill','arc','closePath','clearRect','setTransform'].map(k=>[k,noop]));
const canvas={dataset:{},getContext:()=>context,addEventListener:(n,f)=>listeners.set(n,f),removeEventListener:n=>listeners.delete(n),getBoundingClientRect:()=>({left:0,top:0,width:600,height:340}),setPointerCapture:i=>captures.add(i),hasPointerCapture:i=>captures.has(i),releasePointerCapture:i=>captures.delete(i)};
let changed=false,projected=[];
const scene=createOrbitScene(canvas,{accepted:2,active:2,selected:2,seed:1,paused:true,reduced:false,hovered:-1},{onProject:p=>projected=p,onViewChange:v=>changed=v});
assert.equal(projected.length,3,'only completed and current stages have interactive satellites');
listeners.get('pointerdown')({button:0,pointerId:7,clientX:100,clientY:100});
listeners.get('pointermove')({pointerId:7,clientX:180,clientY:130});
assert.equal(changed,true,'drag rotates the view even while automatic motion is paused');
assert.equal(canvas.dataset.dragging,'true');
listeners.get('pointerup')({pointerId:7});
assert.equal(captures.size,0);
assert.equal(canvas.dataset.dragging,undefined);
scene.reset();assert.equal(changed,false);
listeners.get('keydown')({key:'ArrowRight',preventDefault(){}});assert.equal(changed,true);
listeners.get('keydown')({key:'Home',preventDefault(){}});assert.equal(changed,false);
scene.dispose();assert.equal(listeners.size,0);assert.equal(disconnected,2);
console.log('Orbit checks passed: bounded rotation, drag, paused input, keyboard, reset, projection and cleanup.');
