import { createOrbitScene, observeOrbitAppearance } from './linear-orbit.js?v=20261111-orbit';

const statusName = s => ({done:'Этап принят',review:'На проверке',returned:'На доработке',progress:'В работе',locked:'Пока закрыт'}[s]);
const figure = document.querySelector('[data-orbit]');
let scene, stages, initialStage, currentStage, currentTab;
const validTabs = ['result', 'tasks', 'history'];

function availableStage(index) {
  return Number.isInteger(index) && stages[index] && stages[index].status !== 'locked';
}

function rememberSelection(key, value) {
  const url = new URL(location.href);
  url.searchParams.set(key, value);
  history.pushState(null, '', url);
}

function selectStage(index, navigate = true) {
  if (!availableStage(index) || (navigate && index === currentStage)) return;
  currentStage = index;
  document.querySelectorAll('[data-stage-panel],[data-stage-info],[data-stage-quick]').forEach(el => {
    el.hidden = Number(el.dataset.stagePanel ?? el.dataset.stageInfo ?? el.dataset.stageQuick) !== index;
  });
  document.querySelectorAll('[data-stage-link]').forEach(el => el.setAttribute('aria-current', Number(el.dataset.stageLink) === index ? 'step' : 'false'));
  document.querySelector('[data-stage-caption]').textContent = stages[index].name;
  document.querySelector('[data-stage-status]').textContent = statusName(stages[index].status);
  document.querySelectorAll('[data-tab]').forEach(link => {
    const url = new URL(link.href, location.href);
    url.searchParams.set('stage', index + 1);
    link.href = url.pathname + url.search;
  });
  scene.update({selected:index});
  if (navigate) rememberSelection('stage', index + 1);
}

function selectTab(name, navigate = true) {
  if (!validTabs.includes(name)) name = 'result';
  if (navigate && name === currentTab) return;
  currentTab = name;
  document.querySelectorAll('[data-tab-panel]').forEach(el => { el.hidden = el.dataset.tabPanel !== name; });
  document.querySelectorAll('[data-tab]').forEach(link => {
    if (link.dataset.tab === name) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  document.querySelectorAll('a[data-stage-link]').forEach(link => {
    const url = new URL(link.href, location.href);
    url.searchParams.set('tab', name);
    link.href = url.pathname + url.search;
  });
  if (navigate) rememberSelection('tab', name);
}

function restoreView() {
  const params = new URL(location.href).searchParams;
  const value = params.get('stage');
  const index = /^[1-5]$/.test(value) ? Number(value) - 1 : initialStage;
  selectStage(availableStage(index) ? index : initialStage, false);
  selectTab(params.get('tab'), false);
}

if(figure){
  stages=JSON.parse(figure.dataset.stages);
  initialStage = currentStage = Number(figure.dataset.selected);
  const accepted=stages.findIndex(s=>s.status!=='done');
  const completed=accepted<0?5:accepted;
  const active=completed<5&&stages[completed].status!=='locked'?completed:-1;
  const pause=figure.querySelector('[data-pause-orbit]'),reset=figure.querySelector('[data-reset-orbit]');
  const nodes=[...figure.querySelectorAll('[data-node]')],detail=figure.querySelector('[data-orbit-detail]');
  let paused = false;
  let orbitAppearance;
  scene=createOrbitScene(figure.querySelector('canvas'),{accepted:completed,active,selected:currentStage,seed:Number(figure.dataset.seed),paused,hovered:-1},{
    onProject(points){nodes.forEach(node=>{const p=points.find(p=>p.index===Number(node.dataset.node));node.hidden=!p;if(p){node.style.transform=`translate(${p.x-18}px,${p.y-18}px)`;node.style.zIndex=String(Math.round(p.depth)+500);}});},
    onViewChange(changed){reset.disabled=!changed;}
  });
  const hover=index=>{scene.update({hovered:index});detail.hidden=index<0;detail.textContent=index>=0?`${stages[index].name} · ${statusName(stages[index].status)}`:'';};
  nodes.forEach(node=>{const i=Number(node.dataset.node);node.addEventListener('pointerenter',()=>hover(i));node.addEventListener('pointerleave',()=>hover(-1));node.addEventListener('focus',()=>hover(i));node.addEventListener('blur',()=>hover(-1));node.addEventListener('click',()=>selectStage(i));});
  reset.addEventListener('click',()=>scene.reset());
  const motion = () => {
    const {theme, reduced} = orbitAppearance;
    pause.disabled = reduced;
    pause.setAttribute('aria-pressed', String(paused || reduced));
    pause.setAttribute('aria-label', reduced ? 'Движение отключено в настройках' :
      paused ? 'Включить движение структуры' : 'Приостановить движение структуры');
    pause.textContent = paused || reduced ? 'Движение' : 'Пауза';
    scene.update({theme, paused, reduced});
  };
  pause.addEventListener('click', () => { paused = !paused; motion(); });
  const stopAppearance = observeOrbitAppearance(appearance => {
    orbitAppearance = appearance;
    motion();
  });
  document.querySelectorAll('[data-stage-link]').forEach(el=>el.addEventListener('click',e=>{if(e.metaKey||e.ctrlKey||e.shiftKey||e.altKey)return;e.preventDefault();selectStage(Number(el.dataset.stageLink));}));
  document.querySelectorAll('[data-tab]').forEach(el=>el.addEventListener('click',e=>{if(e.metaKey||e.ctrlKey||e.shiftKey||e.altKey)return;e.preventDefault();selectTab(el.dataset.tab);}));
  addEventListener('popstate', restoreView);
  restoreView();
  // Keep native back/forward caching usable; only dispose when the document is discarded.
  addEventListener('pagehide', event => {
    if (!event.persisted) {
      stopAppearance();
      scene.dispose();
    }
  });
}

const dialog=document.querySelector('.ln-nav-dialog');
const opener=document.querySelector('[data-open-nav]');
opener?.addEventListener('click',()=>dialog.showModal());
dialog?.querySelector('[data-close-nav]').addEventListener('click',()=>dialog.close());
dialog?.addEventListener('click',e=>{if(e.target===dialog)dialog.close();if(e.target.closest('[data-stage-link]'))dialog.close();});
dialog?.addEventListener('close',()=>opener.focus({preventScroll:true}));

const search = document.querySelector('[data-project-search]');
if (search) {
  const projects = [...document.querySelectorAll('[data-project-name]')];
  const empty = document.querySelector('[data-search-empty]');
  search.addEventListener('input', () => {
    const query = search.value.trim().toLowerCase();
    for (const project of projects) project.hidden = !project.dataset.projectName.includes(query);
    empty.hidden = projects.some(project => !project.hidden);
  });
}
