const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../static/account-mail.js'),'utf8');
function run(hash,withForm=true,withError=false,withPasswordError=false){
  const input={value:'',type:'password',listeners:{},addEventListener(k,f){this.listeners[k]=f;}},label={hidden:false},replaced=[];
  const entry={hidden:false,open:true},actions={hidden:false},submit={hidden:false},help={hidden:false},events={};
  let focused=0;
  const error={focus:()=>focused++};
  const form={querySelector:s=>({'[name="token"]':input,'[data-token-label]':label,'[data-token-entry]':entry,'[data-token-actions]':actions,'button[type="submit"]':submit,'[data-token-help]':help})[s]};
  const nodes=new Map([['[data-account-email-error]',withError?error:null],
    ['[data-account-password-error]',withPasswordError?error:null],['[data-email-token-form]',withForm?form:null]]);
  vm.runInNewContext(source,{document:{querySelector:s=>s.split(',').map(part=>nodes.get(part.trim())).find(Boolean)||null},location:{hash,pathname:'/reset-password',search:''},
    history:{replaceState:(...args)=>replaced.push(args)},window:{addEventListener(k,f){events[k]=f;}},URLSearchParams});
  return {input,label,replaced,focused,entry,actions,submit,help,events};
}
let ui=run('#token='+'a'.repeat(43));
assert.equal(ui.input.value,'a'.repeat(43));assert.equal(ui.input.type,'hidden');assert.equal(ui.label.hidden,true);
assert.equal(ui.replaced[0][2],'/reset-password','Capability must be removed from browser history URL');
assert.equal(ui.entry.hidden,true);assert.equal(ui.actions.hidden,false);assert.equal(ui.submit.hidden,false);
for(const hash of ['', '#token=bad', '#token='+encodeURIComponent('<script>')]){
  ui=run(hash);assert.equal(ui.input.value,'');assert.equal(ui.label.hidden,false);assert.equal(ui.input.type,'password');
  assert.equal(ui.entry.open,false,'The manual fallback is available without overwhelming the missing-link screen');
  assert.equal(ui.actions.hidden,true);assert.equal(ui.submit.hidden,true);assert.equal(ui.help.hidden,false);
}
ui=run('');ui.input.value='c'.repeat(43);ui.input.listeners.input();
assert.equal(ui.actions.hidden,false,'Manual code entry reveals the ordinary password fields');
assert.equal(ui.submit.hidden,false);assert.equal(ui.help.hidden,true);
ui.input.value='bad';ui.events.pageshow();
assert.equal(ui.actions.hidden,true,'Back/Forward must restore the readiness of the current code');
assert.equal(run('#token='+'b'.repeat(43),false).replaced.length,0,'Other pages must remain untouched');
ui=run('',false,true);
assert.equal(ui.focused,1,'A rejected email change must focus its error even without a token form');
assert.equal(ui.replaced.length,0,'Email profile errors must not modify the URL');
ui=run('',false,false,true);
assert.equal(ui.focused,1,'A rejected profile password change must focus its error');
assert.equal(ui.replaced.length,0,'Password profile errors must not modify the URL');
assert.equal(run('',false,false).focused,0,'Ordinary pages must not move keyboard focus');
console.log('Account mail: fragment capability, URL cleanup, invalid token, no-JS fallback and account error focus passed');
