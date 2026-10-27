/* Regression checks for view selection, validation recovery and drawer focus. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/interface.js'), 'utf8');

function setup({hash='', board={}, withDrawer=false, withWorkspace=true, overflow='scroll'}={}) {
  const handlers = {}, mediaHandlers = {};
  let document;
  function node(tag='DIV') {
    return {tagName:tag,children:[],dataset:{},inert:false,attrs:{},handlers:{},parentElement:null,
      style:{}, classList:{add() {}}, getClientRects:()=>[{}],
      focus() { document.activeElement = this; },
      scrollIntoView() { this.scrolled = true; },
      setAttribute(key,value) {this.attrs[key]=value;},removeAttribute(key) {delete this.attrs[key];},
      getAttribute(key) {return this.attrs[key] ?? null;},
      addEventListener(key,fn) {this.handlers[key]=fn;},
      append(...children) {children.forEach(child=>{child.parentElement=this;this.children.push(child);});},
      contains(other) {return this===other||this.children.some(child=>child.contains(other));},
      querySelector:()=>null,querySelectorAll:()=>[],closest:()=>null};
  }
  const body=node('BODY'), container=node(), header=node('HEADER'), main=node('MAIN'), drawer=node('DETAILS');
  const summary=node('SUMMARY'), panel=node(), close=node('BUTTON'), link=node('A'), backdrop=node('BUTTON'), outside=node(), desktopLink=node('A');
  body.style.overflow=overflow;body.append(container,outside);container.append(header,main);header.append(drawer);drawer.append(summary,panel,backdrop);panel.append(close,link);
  drawer.open=false;drawer.querySelector=s=>s==='summary'?summary:s==='.ui-mobile-panel'?panel:close;
  drawer.querySelectorAll=s=>s==='[data-close-navigation]'?[close,backdrop]:s==='.ui-mobile-panel a,.ui-mobile-panel button'?[close,link]:[summary,close,link,backdrop];
  const target=node(), disclosure=node('DETAILS'); main.append(disclosure);disclosure.append(target);
  const tabs=['overview','tasks','team'].map(key=>{const n=node('A');n.dataset.projectTab=key;return n;});
  const boardNode=node();boardNode.dataset=board;
  main.querySelector=()=>boardNode;main.querySelectorAll=()=>tabs;
  const location={hash};
  const nav=['home','projects','results'].map(key=>{const n=node('A');n.dataset.navKey=key;return n;});
  nav[0].setAttribute('aria-current','page');
  document={body,documentElement:{classList:{add() {}}},activeElement:summary,
    addEventListener(key,fn) {handlers[key]=fn;},
    querySelector(s) {return s==='.ui-sidebar [aria-current]'?desktopLink:s==='.ui-mobile-menu'?(withDrawer?drawer:null):s==='[data-project-views]'?(withWorkspace?main:null):null;},
    querySelectorAll:()=>nav,getElementById:()=>location.hash?target:null};
  const media={matches:true,addEventListener:(name,fn)=>{mediaHandlers[name]=fn;}};
  const window={matchMedia:()=>media,addEventListener:(key,fn)=>{handlers['window:'+key]=fn;}};
  vm.runInNewContext(source,{document,window,location,Boolean,decodeURIComponent,URLSearchParams});
  handlers.DOMContentLoaded();
  return {document,body,main,outside,drawer,panel,summary,close,backdrop,link,target,disclosure,tabs,media,mediaHandlers,handlers,location,desktopLink,nav};
}

for (const board of [{openCreate:'1'},{boardRefresh:'1'}]) {
  const app=setup({board});
  assert.equal(app.main.dataset.projectView,'tasks','Validation and filtered pages must expose the board');
  assert.equal(app.tabs[1].attrs['aria-current'],'page');
}
const board=setup({hash:'#task-4'});
assert.equal(board.main.dataset.projectView,'tasks');
assert.equal(board.disclosure.open,true,'The linked result must not remain inside a closed disclosure');
assert.equal(board.target.scrolled,true);
for(const hash of ['#stage-1','#stage-3-result','#tasks-without-stage']) {
  assert.equal(setup({hash,board:{workspace:'1'}}).main.dataset.projectView,'tasks','Old and new stage links must expose the unified workspace');
}
assert.equal(setup({board:{workspace:'1',boardRefresh:'1'}}).main.dataset.projectView,'overview','The overview stays the default even though task saves use server refresh');
assert.equal(setup({board:{workspace:'1',workActive:'1'}}).main.dataset.projectView,'tasks','Server-normalized filters expose the unified workspace');
board.location.hash='#project-members';board.handlers['window:hashchange']();
assert.equal(board.main.dataset.projectView,'team','Legacy team controls must remain reachable');
assert.equal(board.tabs[2].attrs['aria-current'],'page');
board.location.hash='';board.handlers['window:hashchange']();
assert.equal(board.main.dataset.projectView,'overview');
assert.equal(board.tabs[0].attrs['aria-current'],'page');
board.location.hash='#%E0%A4%A';
assert.doesNotThrow(()=>board.handlers['window:hashchange']());
const results=setup({hash:'#student-results',withWorkspace:false});
assert.equal(results.disclosure.open,true);
assert.equal(results.target.scrolled,true);
assert.equal(results.nav[2].attrs['aria-current'],'page');
results.location.hash='';results.handlers['window:hashchange']();
assert.equal(results.nav[0].attrs['aria-current'],'page','Back from a fragment must restore the page navigation');
assert.equal(results.nav[2].attrs['aria-current'],undefined);
results.location.hash='#my-projects';results.handlers['window:hashchange']();
assert.equal(results.nav[1].attrs['aria-current'],'page');

const drawer=setup({withDrawer:true,withWorkspace:false});
assert.equal(drawer.panel.attrs.role,'dialog');
assert.equal(drawer.panel.attrs['aria-modal'],'true');
drawer.outside.inert=true;
drawer.drawer.open=true;drawer.drawer.handlers.toggle();
assert.equal(drawer.main.inert,true);
assert.equal(drawer.body.style.overflow,'hidden');
assert.equal(drawer.document.activeElement,drawer.close);
drawer.document.activeElement=drawer.close;
let prevented=false;
drawer.handlers.keydown({key:'Tab',shiftKey:true,preventDefault(){prevented=true;}});
assert.equal(prevented,true);assert.equal(drawer.document.activeElement,drawer.link,'Shift+Tab must stay in the visible panel, not on its backdrop or covered trigger');
drawer.handlers.keydown({key:'Tab',shiftKey:false,preventDefault(){}});
assert.equal(drawer.document.activeElement,drawer.close);
drawer.backdrop.handlers.click();
assert.equal(drawer.drawer.open,false);
assert.equal(drawer.main.inert,false);
assert.equal(drawer.outside.inert,true,'Pre-existing inert state must survive closing the navigation');
assert.equal(drawer.body.style.overflow,'scroll');
assert.equal(drawer.document.activeElement,drawer.summary);
drawer.drawer.open=true;drawer.drawer.handlers.toggle();
drawer.media.matches=false;drawer.mediaHandlers.change();
assert.equal(drawer.main.inert,false);
assert.equal(drawer.body.style.overflow,'scroll');
assert.equal(drawer.document.activeElement,drawer.desktopLink,'Resizing must move focus out of the now-hidden mobile menu');
assert.doesNotThrow(()=>setup({withDrawer:false,withWorkspace:false}));
console.log('Interface checks passed: deep links, filters/validation, legacy team controls, disclosures, focus and inert restoration.');
