const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname,'../static/form-guard.js'),'utf8');
function setup(force = false) {
  const docEvents = {}, winEvents = {};
  const forms = [0,1].map((n) => ({dataset:{draftUnsaved:force && n===0?'1':'0'},events:{},
    fields:[{name:'result',type:'textarea',value:'saved'},{name:'csrf',type:'hidden',value:'token'},{name:'password',type:'password',value:'private'}],
    querySelectorAll() {return this.fields;}, prepend(note) {this.note=note;},
    addEventListener(name, fn) {this.events[name]=fn;}}));
  vm.runInNewContext(source, {document:{querySelectorAll:()=>forms,
    createElement:()=>({setAttribute(){}}),addEventListener:(n,f)=>docEvents[n]=f},
    window:{addEventListener:(n,f)=>winEvents[n]=f}});
  docEvents.DOMContentLoaded();
  const leaving = () => {const event={preventDefault(){this.prevented=true;}};winEvents.beforeunload(event);return event.prevented===true;};
  const submit = (target, blocked = false) => docEvents.submit({target,defaultPrevented:blocked});
  return {forms,leaving,submit,restore:()=>winEvents.pageshow()};
}
const ui=setup();
assert.equal(ui.leaving(),false);
ui.forms[0].fields[1].value='refreshed-token'; ui.forms[0].fields[2].value='another-password';
assert.equal(ui.leaving(),false,'Tokens/password fields do not create draft state');
ui.forms[0].fields[0].value='edited'; ui.forms[0].events.input();
assert.equal(ui.forms[0].note.hidden,false);
assert.equal(ui.leaving(),true,'Leaving edited work asks the browser to warn');
ui.forms[0].fields[0].value='saved'; ui.forms[0].events.input();
assert.equal(ui.forms[0].note.hidden,true); assert.equal(ui.leaving(),false,'Reverting removes the warning');
ui.forms[0].fields[0].value='edited'; ui.submit(ui.forms[0],true);
assert.equal(ui.leaving(),true,'Cancelled submit does not bypass the warning');
ui.submit(ui.forms[0]); assert.equal(ui.leaving(),false,'Saving the form is not blocked');
ui.restore(); assert.equal(ui.leaving(),true,'Back/Forward restores protection');
ui.submit({}); assert.equal(ui.leaving(),true,'An unrelated POST must not discard edited work');
ui.forms[1].fields[0].value='other edit'; ui.submit(ui.forms[0]);
assert.equal(ui.leaving(),true,'Submitting one form does not discard another form');
assert.equal(setup(true).leaving(),true,'Server-rejected text is guarded before further edits');
console.log('Form guard: edits, revert, submit, blocked submit, history, multiple forms and rejected input passed');
