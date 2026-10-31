const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../static/account-mail.js'),'utf8');
function run(hash,withForm=true){
  const input={value:'',type:'password'},label={hidden:false},replaced=[];
  const form={querySelector:s=>s==='[name="token"]'?input:label};
  vm.runInNewContext(source,{document:{querySelector:()=>withForm?form:null},location:{hash,pathname:'/reset-password',search:''},
    history:{replaceState:(...args)=>replaced.push(args)},URLSearchParams});
  return {input,label,replaced};
}
let ui=run('#token='+'a'.repeat(43));
assert.equal(ui.input.value,'a'.repeat(43));assert.equal(ui.input.type,'hidden');assert.equal(ui.label.hidden,true);
assert.equal(ui.replaced[0][2],'/reset-password','Capability must be removed from browser history URL');
for(const hash of ['', '#token=bad', '#token='+encodeURIComponent('<script>')]){
  ui=run(hash);assert.equal(ui.input.value,'');assert.equal(ui.label.hidden,false);assert.equal(ui.input.type,'password');
}
assert.equal(run('#token='+'b'.repeat(43),false).replaced.length,0,'Other pages must remain untouched');
console.log('Account mail: fragment capability, URL cleanup, invalid token and no-JS fallback passed');
