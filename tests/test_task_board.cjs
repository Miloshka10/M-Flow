const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname,'../static/task-board.js'),'utf8');
class Element {
  constructor(tag='div') {this.tagName=tag.toUpperCase();this.dataset={};this.attrs={};this.listeners={};this.children=[];this.hidden=false;this.classes=new Set();this.classList={add:n=>this.classes.add(n),remove:n=>this.classes.delete(n)};}
  setAttribute(k,v){this.attrs[k]=v;}
  addEventListener(k,f){this.listeners[k]=f;}
  append(...els){this.children.push(...els);}
  after(el){this.afterElement=el;}
  focus(){this.focused=true;}
  showModal(){this.open=true;}
  close(){this.open=false;this.listeners.close?.();}
  getBoundingClientRect(){return {left:800,right:1360,top:0,bottom:900};}
}
function setup({mobile=false,hash='',supported=true,path='/project/1'}={}){
  const docRoot=new Element(), board=new Element(), columnField=new Element('input');
  board.dataset={column:'todo',openCreate:'0',projectUrl:'/project/1',boardLocation:'/project/1?stage=2'};
  const columns=['todo','progress','done'].map(status=>{const e=new Element();e.dataset.status=status;return e;});
  const tabs=columns.map(col=>{const tab=new Element('a');tab.dataset.column=col.dataset.status;tab.count=new Element('span');tab.querySelector=()=>tab.count;return tab;});
  const quick=new Element('a');quick.href='https://preview.test/project/1?due=week#task-board';
  const returnField=new Element('input');returnField.value='/project/1?stage=2#task-7';
  const summary=new Element('summary'),content=new Element(),heading=new Element('h2'),card=new Element();card.id='task-7';
  card.closest=()=>columns[1];card.querySelector=()=>summary;content.querySelector=()=>heading;
  const details=new Element('details');details.closest=()=>card;details.querySelector=s=>s.includes('summary')?summary:content;
  const addLink=new Element('a'),create=new Element('details'),createSummary=new Element('summary'),createContent=new Element();
  create.closest=()=>null;create.querySelector=s=>s.includes('summary')?createSummary:createContent;createContent.querySelector=()=>new Element('h2');
  board.querySelectorAll=s=>s.includes('.board-columns')?tabs:s==='.kanban-column'?columns:s.includes('return_to')?[quick,returnField]:s==='.board-disclosure'?[details,create]:s.includes('task-create')?[addLink]:[];
  board.querySelector=s=>s.includes('input')?columnField:s.includes('kanban-count')?{textContent:'3'}:s==='.board-add'?addLink:null;
  const media={matches:mobile,addEventListener(k,f){this.change=f;}};
  const windowListeners={};
  const loc=new URL('https://preview.test'+path+'?stage=2'+hash);
  const context={URL,HTMLDialogElement:supported?Element:undefined,
    document:{documentElement:docRoot,querySelector:()=>board,createElement:t=>new Element(t),addEventListener(){}},
    window:{location:loc,matchMedia:()=>media,addEventListener:(k,f)=>windowListeners[k]=f},
    history:{replaceState(_,__,url){const next=new URL(url,loc);loc.href=next.href;}}};
  vm.createContext(context);vm.runInContext(source,context);context.setupTaskBoard();
  return {context,board,docRoot,columns,tabs,quick,returnField,summary,details,card,loc,media,windowListeners,addLink,create};
}
const desktop=setup();
assert.ok(desktop.columns.every(c=>!c.hidden));
assert.equal(desktop.summary.attrs['aria-haspopup'],'dialog');
desktop.summary.listeners.click({preventDefault(){}});
const dialog=desktop.details.afterElement;
assert.equal(dialog.open,true);
assert.equal(desktop.summary.attrs['aria-expanded'],'true');
assert.ok(desktop.docRoot.classes.has('board-dialog-open'));
assert.equal(desktop.loc.hash,'#task-7');
dialog.listeners.click({target:dialog,clientX:1000,clientY:100});
assert.equal(dialog.open,true,'A click inside the panel does not close it');
dialog.listeners.click({target:dialog,clientX:300,clientY:100});
assert.equal(dialog.open,false);
assert.equal(desktop.summary.focused,true);
assert.equal(desktop.summary.attrs['aria-expanded'],'false');
assert.ok(!desktop.docRoot.classes.has('board-dialog-open'));
assert.equal(desktop.loc.hash,'#task-board');
desktop.summary.listeners.click({preventDefault(){}});
dialog.closest=()=>desktop.card;
desktop.media.matches=true;desktop.media.change();
assert.deepEqual(desktop.columns.map(c=>c.hidden),[true,false,true],'Resizing an open desktop panel must keep its column visible on mobile');
assert.equal(dialog.open,true);
dialog.close();
const mobile=setup({mobile:true,hash:'#task-7'});
assert.equal(mobile.details.afterElement.open,true);
assert.deepEqual(mobile.columns.map(c=>c.hidden),[true,false,true]);
assert.equal(mobile.loc.searchParams.get('column'),'progress');
assert.match(mobile.returnField.value,/stage=2&column=progress#task-7/);
assert.match(mobile.quick.href,/due=week&column=progress/);
mobile.context.window.beforeBoardTaskMove(mobile.card);
assert.equal(mobile.details.afterElement.open,false);
mobile.context.window.boardTaskSaved(mobile.card,'done');
assert.deepEqual(mobile.columns.map(c=>c.hidden),[true,true,false]);
assert.equal(mobile.tabs[2].attrs['aria-current'],'true');
assert.equal(mobile.tabs[2].count.textContent,'3');
mobile.media.matches=false;mobile.media.change();
assert.ok(mobile.columns.every(c=>!c.hidden),'Resize reveals all desktop columns');
mobile.addLink.listeners.click({preventDefault(){}});
assert.equal(mobile.create.afterElement.open,true);
mobile.create.afterElement.close();
assert.equal(mobile.addLink.focused,true);
const fallback=setup({supported:false});
assert.ok(!fallback.board.classes.has('board-enhanced'),'Unsupported dialogs keep native details and forms');
const invalidForm=setup({path:'/project/1/add',hash:'#task-create'});
assert.equal(invalidForm.loc.pathname,'/project/1','Validation errors normalize the POST URL before later navigation');
assert.equal(invalidForm.loc.searchParams.get('stage'),'2','Validation errors preserve active filters in the normalized URL');
console.log('Task board checks passed: drawers, outside close, focus, hash links, mobile columns, counts, URL state, fallback.');
