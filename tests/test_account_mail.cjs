const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../static/account-mail.js'),'utf8');
function run(hash,withForm=true,withError=false,withPasswordError=false){
  const input={value:'',type:'password'},label={hidden:false},replaced=[];
  let focused=0;
  const error={focus:()=>focused++};
  const form={querySelector:s=>s==='[name="token"]'?input:label};
  const nodes=new Map([['[data-account-email-error]',withError?error:null],
    ['[data-account-password-error]',withPasswordError?error:null],['[data-email-token-form]',withForm?form:null]]);
  vm.runInNewContext(source,{document:{querySelector:s=>s.split(',').map(part=>nodes.get(part.trim())).find(Boolean)||null},location:{hash,pathname:'/reset-password',search:''},
    history:{replaceState:(...args)=>replaced.push(args)},URLSearchParams});
  return {input,label,replaced,focused};
}
let ui=run('#token='+'a'.repeat(43));
assert.equal(ui.input.value,'a'.repeat(43));assert.equal(ui.input.type,'hidden');assert.equal(ui.label.hidden,true);
assert.equal(ui.replaced[0][2],'/reset-password','Capability must be removed from browser history URL');
for(const hash of ['', '#token=bad', '#token='+encodeURIComponent('<script>')]){
  ui=run(hash);assert.equal(ui.input.value,'');assert.equal(ui.label.hidden,false);assert.equal(ui.input.type,'password');
}
assert.equal(run('#token='+'b'.repeat(43),false).replaced.length,0,'Other pages must remain untouched');
ui=run('',false,true);
assert.equal(ui.focused,1,'A rejected email change must focus its error even without a token form');
assert.equal(ui.replaced.length,0,'Email profile errors must not modify the URL');
ui=run('',false,false,true);
assert.equal(ui.focused,1,'A rejected profile password change must focus its error');
assert.equal(ui.replaced.length,0,'Password profile errors must not modify the URL');
assert.equal(run('',false,false).focused,0,'Ordinary pages must not move keyboard focus');
console.log('Account mail: fragment capability, URL cleanup, invalid token, no-JS fallback and account error focus passed');
