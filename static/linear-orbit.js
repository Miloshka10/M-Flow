const TAU = Math.PI * 2;
const PHI = (1 + Math.sqrt(5)) / 2;
const palettes = {
  dark: {grid: '#6c7180', reference: '#555b6b', track: '#afb7c9', selected: '#a8a5ec',
    node: '#d7dfef', active: '#b8b4ff', halo: '#aaa4ff', core: '#f0f1ff',
    layer: '#9b99d9', shell: '#dce1f4'},
  light: {grid: '#4e5164', reference: '#606483', track: '#555a77', selected: '#5050a9',
    node: '#414760', active: '#4745aa', halo: '#4846ad', core: '#242649',
    layer: '#63609e', shell: '#444678'},
};
const vertices = [[0,1,PHI],[0,-1,PHI],[0,1,-PHI],[0,-1,-PHI],[1,PHI,0],[-1,PHI,0],[1,-PHI,0],[-1,-PHI,0],[PHI,0,1],[-PHI,0,1],[PHI,0,-1],[-PHI,0,-1]].map(v => v.map(n => n / Math.hypot(...v)));
const edges = vertices.flatMap((a,i) => vertices.flatMap((b,j) => j > i && Math.hypot(...a.map((v,k) => v-b[k])) < 1.1 ? [[i,j]] : []));
const connected=(a,b)=>edges.some(([x,y])=>x===Math.min(a,b)&&y===Math.max(a,b));
const faces=vertices.flatMap((_,a)=>vertices.flatMap((_,b)=>vertices.flatMap((_,c)=>a<b&&b<c&&connected(a,b)&&connected(b,c)&&connected(a,c)?[[a,b,c]]:[])));
function rotation(ax, ay, az = 0) {
  const sinX = Math.sin(ax), cosX = Math.cos(ax);
  const sinY = Math.sin(ay), cosY = Math.cos(ay);
  const sinZ = Math.sin(az), cosZ = Math.cos(az);
  return ([x, y, z]) => {
    const y1 = y * cosX - z * sinX, z1 = y * sinX + z * cosX;
    const x2 = x * cosY + z1 * sinY, z2 = -x * sinY + z1 * cosY;
    return [x2 * cosZ - y1 * sinZ, x2 * sinZ + y1 * cosZ, z2];
  };
}

function sampleRing(segments) {
  return Array.from({length: segments + 1}, (_, step) => {
    const angle = step / segments * TAU;
    return [Math.cos(angle), Math.sin(angle), 0];
  });
}

const referenceRing = sampleRing(128).map(rotation(1.15, .1));
const stageRing = sampleRing(144);
const dashedOrbit = [2, 5];

export function clampOrbitView({yaw,pitch}) {
  return {yaw:Number.isFinite(yaw)?((yaw+Math.PI)%(Math.PI*2)+Math.PI*2)%(Math.PI*2)-Math.PI:0,pitch:Math.max(-.95,Math.min(.95,Number.isFinite(pitch)?pitch:0))};
}

export function observeOrbitAppearance(onChange) {
  const root = document.documentElement;
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const sync = () => onChange({
    theme: root.dataset.theme,
    reduced: reducedMotion.matches || root.dataset.animations === 'off',
  });
  const observer = new MutationObserver(sync);
  observer.observe(root, {attributes: true, attributeFilter: ['data-theme', 'data-animations']});
  reducedMotion.addEventListener('change', sync);
  sync();
  return () => {
    observer.disconnect();
    reducedMotion.removeEventListener('change', sync);
  };
}

export function createOrbitScene(element, initial, callbacks={}) {
  const ctx=element.getContext('2d');
  if(!ctx)return {update(){},reset(){},dispose(){}};
  let config={...initial},clock=0,pointer=[0,0],view={yaw:0,pitch:0},drag=null;
    let width=0,height=0,frame=0,last=0,visible=true,destroyed=false;
    let spinX=0,spinY=0,built=config.accepted;
    const seed=initial.seed||0;
    const orbits = Array.from({length: 5}, (_, index) => {
      const orient = rotation(.65 + index * .49, .2 + index * .56, index * .64 + seed * .12);
      return {orient, track: stageRing.map(orient)};
    });
    const accepted=config.accepted,active=config.active;
    const reveal=Array.from({length:5},(_,i)=>i<accepted+(active>=0?1:0)?1:0);
    const line = (points,color,opacity,lineWidth=.7,dashed=false) => {
      ctx.beginPath();ctx.strokeStyle=color;ctx.globalAlpha=opacity;ctx.lineWidth=lineWidth;
      if (dashed) ctx.setLineDash(dashedOrbit);
      points.forEach((p,i)=>i ? ctx.lineTo(p[0],p[1]) : ctx.moveTo(p[0],p[1]));
      ctx.stroke();
      if (dashed) ctx.setLineDash([]);
    };
    const crystal = (project,position,radius,angle,color,alpha) => {
      const orient = rotation(angle * .6, angle, .25);
      const points = vertices.map(([x, y, z]) => {
        const [a, b, c] = orient([x * radius, y * radius, z * radius]);
        return project([a + position[0], b + position[1], c + position[2]]);
      });
      if(radius>20) [...faces].sort((a,b)=>a.reduce((s,i)=>s+points[i][2],0)-b.reduce((s,i)=>s+points[i][2],0)).forEach(face=>{
        ctx.beginPath();face.forEach((i,n)=>n?ctx.lineTo(points[i][0],points[i][1]):ctx.moveTo(points[i][0],points[i][1]));ctx.closePath();ctx.globalAlpha=alpha*.035;ctx.fillStyle=color;ctx.fill();
      });
      edges.forEach(([a,b])=>line([points[a],points[b]],color,alpha*(.35+(points[a][2]+200)/650),.75));
      points.forEach(p=>{ctx.globalAlpha=alpha*.85;ctx.fillStyle=color;ctx.beginPath();ctx.arc(p[0],p[1],Math.max(.7,radius*.022*p[3]),0,TAU);ctx.fill();});
    };
    const draw = () => {
      if (!width || !height) return;
      const c=config,t=clock,scale=Math.min(width/470,height/400,1.3);
      const palette = c.theme === 'light' ? palettes.light : palettes.dark;
      ctx.clearRect(0,0,width,height);
      const easing=c.paused||c.reduced?1:.065;
      built+=(c.accepted-built)*easing;
      spinX+=(pointer[0]*.12-spinX)*.055;spinY+=(pointer[1]*.09-spinY)*.055;
      // Project state drives geometry. Locked stages contribute no orbit or satellites.
      const layers=c.accepted+(c.active>=0?1:0);
      for(let i=layers;i<reveal.length;i++) reveal[i]=0;
      const target=Math.max(1,layers);
      const spread=scale*(.80+Math.min(target,5)*.04);
      const centerSize=(25+built*10)*scale;
      const orientView = rotation(.28 + spinY + view.pitch, -.2 + spinX + view.yaw, -.16);
      const project = point => {
        const [x, y, depth] = orientView(point);
        const perspective = 620 / (620 - depth);
        return [width / 2 + x * perspective, height / 2 + y * perspective, depth, perspective];
      };
      const projectRing = (points, radius) => points.map(([x, y, z]) => project([x * radius, y * radius, z * radius]));
      const origin=project([0,0,0]);
      line([[width/2-12, height/2],[width/2+12,height/2]],palette.grid,.11);
      line([[width/2,height/2-12],[width/2,height/2+12]],palette.grid,.11);
      // A quiet external reference ring stays fixed; the built structure moves within it.
      line(projectRing(referenceRing, 208 * spread),palette.reference,.2,.55);
      const bodies=[];
      for (let i=0;i<layers;i++) {
        reveal[i]+=(1-reveal[i])*easing;
        const birth=reveal[i];
        const complete=i<c.accepted,focused=i===c.selected||i===c.hovered;
        const radius=(79+i*26)*spread*(.75+birth*.25);
        const {orient, track} = orbits[i];
        const visiblePoints = Math.max(2, Math.ceil(track.length * birth));
        line(projectRing(track.slice(0, visiblePoints), radius),focused?palette.selected:palette.track,(complete ? (focused ? .58 : .35) : .28)*birth,focused?1:.75,!complete);
        const count=complete?3+i:1;
        for (let n=0;n<count;n++) {
          const angle=t*(i%2 ? -.07:.055)/(1+i*.11)+n/count*TAU+i*1.79+seed*.43;
          const position=orient([Math.cos(angle)*radius,Math.sin(angle)*radius,0]);
          const p=project(position),color=focused||!complete?palette.active:palette.node;
          if(n===0 && complete) line([origin,p],color,.08,.6);
          bodies.push({position,p,color,complete,focused,size:(n===0?12+i*1.5:3.2+i*.35)*scale*birth,angle,birth,index:i,node:n});
        }
      }
      bodies.sort((a,b)=>a.p[2]-b.p[2]);
      callbacks.onProject?.(bodies.filter(b=>b.node===0).map(b=>({index:b.index,x:b.p[0],y:b.p[1],depth:b.p[2]})));
      const paintBody = b => {
        if(b.size>7*scale) crystal(project,b.position,b.size,t*.15+b.angle,b.color,(b.complete?.65:.55)*b.birth);
        else { ctx.fillStyle=b.color;ctx.globalAlpha=(b.complete?.75:.65)*b.birth;ctx.beginPath();ctx.arc(b.p[0],b.p[1],Math.max(1.3,b.size*b.p[3]*.5),0,TAU);ctx.fill(); }
        if(b.focused) {
          const pulse=c.reduced?0:(Math.sin(t*1.5)+1)*.5;
          ctx.globalAlpha=.12+pulse*.09;ctx.strokeStyle=palette.halo;ctx.lineWidth=.7;ctx.beginPath();ctx.arc(b.p[0],b.p[1],(b.size+7+pulse*3)*b.p[3],0,TAU);ctx.stroke();
        }
      };
      bodies.filter(b=>b.p[2]<0).forEach(paintBody);
      // Nested polyhedra form the nucleus; accepted stages add a new crystalline shell.
      for(let i=c.accepted;i>=0;i--) {
        const r=centerSize*(.52+i*.105);
        crystal(project,[0,0,0],r,t*.075+i*.31+seed*.4,i===0?palette.core:palette.layer,i===0?.85:.29);
      }
      crystal(project,[0,0,0],centerSize,t*.075+seed*.4,palette.shell,.8);
      bodies.filter(b=>b.p[2]>=0).forEach(paintBody);
      ctx.globalAlpha=1;
    };
    const moving=()=>!config.paused&&!config.reduced&&visible&&!document.hidden&&!drag&&!(config.hovered>=0);
    const tick = now => {
      frame=0;
      if(destroyed) return;
      if(moving()) clock+=Math.min((now-(last||now))/1000,.06);
      last=now;draw();
      if(moving()) frame=requestAnimationFrame(tick);
    };
    const refresh = () => {cancelAnimationFrame(frame);frame=0;last=0;draw();if(moving())frame=requestAnimationFrame(tick);};
    const resize=new ResizeObserver(entries=>{const rect=entries[0].contentRect;width=rect.width;height=rect.height;const dpr=Math.min(window.devicePixelRatio||1,2);element.width=Math.round(width*dpr);element.height=Math.round(height*dpr);ctx.setTransform(dpr,0,0,dpr,0,0);refresh();});
    const observer=new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;refresh();},{threshold:.01});
    resize.observe(element);observer.observe(element);document.addEventListener('visibilitychange',refresh);
    const setView=(next)=>{view=clampOrbitView(next);callbacks.onViewChange?.(Math.abs(view.yaw)+Math.abs(view.pitch)>.001);refresh();};
    const down=e=>{if(e.button!==0)return;drag={id:e.pointerId,x:e.clientX,y:e.clientY};element.setPointerCapture(e.pointerId);element.dataset.dragging='true';refresh();};
    const move=e=>{const r=element.getBoundingClientRect();pointer=[(e.clientX-r.left)/r.width-.5,(e.clientY-r.top)/r.height-.5];if(drag&&e.pointerId===drag.id){setView({yaw:view.yaw+(e.clientX-drag.x)*.009,pitch:view.pitch+(e.clientY-drag.y)*.009});drag.x=e.clientX;drag.y=e.clientY;}else if(!moving())draw();};
    const up=e=>{if(!drag||e.pointerId!==drag.id)return;drag=null;delete element.dataset.dragging;if(element.hasPointerCapture(e.pointerId))element.releasePointerCapture(e.pointerId);refresh();};
    const leave=()=>{pointer=[0,0];};
    const key=e=>{const offsets={ArrowLeft:[-.14,0],ArrowRight:[.14,0],ArrowUp:[0,-.14],ArrowDown:[0,.14]};if(offsets[e.key]){e.preventDefault();const [x,y]=offsets[e.key];setView({yaw:view.yaw+x,pitch:view.pitch+y});}else if(e.key==='Home'){e.preventDefault();setView({yaw:0,pitch:0});}};
    element.addEventListener('pointerdown',down);element.addEventListener('pointermove',move);element.addEventListener('pointerup',up);element.addEventListener('pointercancel',up);element.addEventListener('pointerleave',leave);element.addEventListener('keydown',key);
    return {update(next){config={...config,...next};refresh();},reset(){pointer=[0,0];spinX=spinY=0;setView({yaw:0,pitch:0});},dispose(){destroyed=true;cancelAnimationFrame(frame);resize.disconnect();observer.disconnect();document.removeEventListener('visibilitychange',refresh);element.removeEventListener('pointerdown',down);element.removeEventListener('pointermove',move);element.removeEventListener('pointerup',up);element.removeEventListener('pointercancel',up);element.removeEventListener('pointerleave',leave);element.removeEventListener('keydown',key);}};
}
