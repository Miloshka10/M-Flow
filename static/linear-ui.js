import { createOrbitScene } from './linear-orbit.js?v=20261110-orbit-1';

const statusName = s => ({done:'Этап принят',review:'На проверке',returned:'На доработке',progress:'В работе',locked:'Пока закрыт'}[s]);
const figure = document.querySelector('[data-orbit]');
let scene, stages, currentStage;

function selectStage(index, navigate = true) {
  if (!stages || !Number.isInteger(index) || !stages[index] || stages[index].status === 'locked') return;
  currentStage = index;
  document.querySelectorAll('[data-stage-panel],[data-stage-info],[data-stage-quick]').forEach(el => {
    el.hidden = Number(el.dataset.stagePanel ?? el.dataset.stageInfo ?? el.dataset.stageQuick) !== index;
  });
  document.querySelectorAll('[data-stage-link]').forEach(el => el.setAttribute('aria-current', Number(el.dataset.stageLink) === index ? 'step' : 'false'));
  document.querySelector('[data-stage-caption]').textContent = stages[index].name;
  document.querySelector('[data-stage-status]').textContent = statusName(stages[index].status);
  document.querySelectorAll('[data-tab]').forEach(el=>{const url=new URL(el.href,location.href);url.searchParams.set('stage',index+1);el.href=url.pathname+url.search;});
  scene.update({selected:index});
  if (navigate) { const url=new URL(location.href);url.searchParams.set('stage',index+1);history.pushState(null,'',url); }
}

function selectTab(name, navigate = true) {
  if (!['result','tasks','history'].includes(name)) name='result';
  document.querySelectorAll('[data-tab-panel]').forEach(el => el.hidden=el.dataset.tabPanel!==name);
  document.querySelectorAll('[data-tab]').forEach(el => {if(el.dataset.tab===name)el.setAttribute('aria-current','page');else el.removeAttribute('aria-current');});
  if(navigate){const url=new URL(location.href);url.searchParams.set('tab',name);history.pushState(null,'',url);}
}

if(figure){
  stages=JSON.parse(figure.dataset.stages);
  currentStage=Number(figure.dataset.selected);
  const accepted=stages.findIndex(s=>s.status!=='done');
  const completed=accepted<0?5:accepted;
  const active=completed<5&&stages[completed].status!=='locked'?completed:-1;
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');
  const pause=figure.querySelector('[data-pause-orbit]'),reset=figure.querySelector('[data-reset-orbit]');
  const nodes=[...figure.querySelectorAll('[data-node]')],detail=figure.querySelector('[data-orbit-detail]');
  let paused=false;
  scene=createOrbitScene(figure.querySelector('canvas'),{accepted:completed,active,selected:currentStage,seed:Number(figure.dataset.seed),paused,reduced:reduced.matches,hovered:-1},{
    onProject(points){nodes.forEach(node=>{const p=points.find(p=>p.index===Number(node.dataset.node));node.hidden=!p;if(p){node.style.transform=`translate(${p.x-18}px,${p.y-18}px)`;node.style.zIndex=String(Math.round(p.depth)+500);}});},
    onViewChange(changed){reset.disabled=!changed;}
  });
  const hover=index=>{scene.update({hovered:index});detail.hidden=index<0;detail.textContent=index>=0?`${stages[index].name} · ${statusName(stages[index].status)}`:'';};
  nodes.forEach(node=>{const i=Number(node.dataset.node);node.addEventListener('pointerenter',()=>hover(i));node.addEventListener('pointerleave',()=>hover(-1));node.addEventListener('focus',()=>hover(i));node.addEventListener('blur',()=>hover(-1));node.addEventListener('click',()=>selectStage(i));});
  reset.addEventListener('click',()=>scene.reset());
  const motion=()=>{pause.disabled=reduced.matches;pause.setAttribute('aria-pressed',String(paused||reduced.matches));pause.setAttribute('aria-label',reduced.matches?'Движение отключено в настройках устройства':paused?'Включить движение структуры':'Приостановить движение структуры');pause.textContent=paused||reduced.matches?'Движение':'Пауза';scene.update({paused,reduced:reduced.matches});};
  pause.addEventListener('click',()=>{paused=!paused;motion();});reduced.addEventListener('change',motion);motion();
  document.querySelectorAll('[data-stage-link]').forEach(el=>el.addEventListener('click',e=>{if(e.metaKey||e.ctrlKey||e.shiftKey||e.altKey)return;e.preventDefault();selectStage(Number(el.dataset.stageLink));}));
  document.querySelectorAll('[data-tab]').forEach(el=>el.addEventListener('click',e=>{if(e.metaKey||e.ctrlKey||e.shiftKey||e.altKey)return;e.preventDefault();selectTab(el.dataset.tab);}));
  const fromURL=()=>{const p=new URL(location.href).searchParams;const n=p.get('stage');selectStage(n?Number(n)-1:Number(figure.dataset.selected),false);selectTab(p.get('tab')||'result',false);};
  addEventListener('popstate',fromURL);fromURL();
  // Keep native back/forward caching usable; only dispose when the document is discarded.
  addEventListener('pagehide',e=>{if(!e.persisted)scene.dispose();});
}

const dialog=document.querySelector('.ln-nav-dialog');
const opener=document.querySelector('[data-open-nav]');
opener?.addEventListener('click',()=>dialog.showModal());
dialog?.querySelector('[data-close-nav]').addEventListener('click',()=>dialog.close());
dialog?.addEventListener('click',e=>{if(e.target===dialog)dialog.close();if(e.target.closest('[data-stage-link]'))dialog.close();});
dialog?.addEventListener('close',()=>opener.focus({preventScroll:true}));

const search=document.querySelector('[data-project-search]');
search?.addEventListener('input',()=>{let count=0;const q=search.value.trim().toLowerCase();document.querySelectorAll('[data-project-name]').forEach(el=>{el.hidden=!el.dataset.projectName.includes(q);if(!el.hidden)count++;});document.querySelector('[data-search-empty]').hidden=count>0;});
