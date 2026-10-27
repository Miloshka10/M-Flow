/* Regressions for stage routing after task forms have moved into dialogs. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname,'../static/project-work.js'),'utf8');
function setup(validation=false) {
  const handlers={}, routeHandlers={};
  const location=new URL('https://preview.test/project/1?mine=1&view_stage=2#stage-2');
  const stageField={value:'2'}, view={value:'2'}, returnField={value:'/project/1?mine=1#task-board'};
  const quick={href:'https://preview.test/project/1?mine=1#task-board'};
  const tools={querySelector:()=>view,querySelectorAll:()=>[quick]};
  const general={};
  const panels=[1,2,3,4,5].map(n=>({dataset:{workPanel:String(n)},hidden:false,querySelector:()=>({append(){}})}));
  const links=panels.map(p=>({dataset:{workStage:p.dataset.workPanel},href:'https://preview.test/project/1#stage-'+p.dataset.workPanel,attrs:{},setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this.attrs[k];},addEventListener(k,f){routeHandlers[this.dataset.workStage]=f;}}));
  const board={dataset:{projectUrl:'/project/1',selectedStage:'2',openCreate:validation?'1':'0'},
    querySelector(s){if(s==='[data-work-tools]')return tools;if(s==='.pw-unassigned')return general;if(s.includes('#drawer-task-create'))return stageField;return null;},
    querySelectorAll(s){return s==='[data-work-panel]'?panels:s==='[data-work-stage]'?links:s==='input[name="return_to"]'?[returnField]:[];}};
  const document={querySelector:()=>board,getElementById:()=>null,addEventListener:(k,f)=>handlers[k]=f};
  const window={addEventListener:(k,f)=>handlers[k]=f,dispatchEvent:e=>handlers[e.type]?.()};
  const history={pushState(_,__,url){location.href=new URL(url,location).href;}};
  vm.runInNewContext(source,{document,window,location,history,URL,Event:class{constructor(type){this.type=type;}}});
  handlers.DOMContentLoaded();
  return {stageField,view,quick,returnField,panels,links,location,select(n){routeHandlers[n]({preventDefault(){}});}};
}
const app=setup();app.select('5');
assert.equal(app.stageField.value,'5','The create form in its dialog follows the selected stage');
assert.equal(app.view.value,'5');
assert.equal(new URL(app.quick.href,'https://preview.test').searchParams.get('view_stage'),'5','Quick filters retain the selected stage');
assert.equal(new URL(app.returnField.value,'https://preview.test').searchParams.get('view_stage'),'5');
assert.equal(app.panels.filter(p=>!p.hidden)[0].dataset.workPanel,'5');
assert.equal(app.links[4].attrs['aria-current'],'step');
assert.equal(app.location.hash,'#stage-5');
const recovering=setup(true);recovering.select('5');
assert.equal(recovering.stageField.value,'2','Validation recovery never replaces the submitted task stage');
console.log('Project work checks passed: stage selection, moved create form, filters, return URLs and validation recovery.');
