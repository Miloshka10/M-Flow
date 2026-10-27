// Run with node tests/test_auth_ui.cjs. Tests actual enhancement behavior, no dependencies.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/auth.js'), 'utf8');

function setup({error = false, page = true} = {}) {
  const events = {};
  const inputs = ['password', 'repeat_password'].map(name => ({name, type:'password', value:'unchanged-test-value',
    parentElement:{classList:{add(c) { this.added = c; }}}}));
  const buttons = inputs.map((input, i) => ({hidden:true, attrs:{'aria-controls':`input-${i}`}, listeners:{},
    getAttribute(k) { return this.attrs[k]; }, setAttribute(k,v) { this.attrs[k] = v; },
    addEventListener(k,fn) { this.listeners[k] = fn; }}));
  const invalid = {focus() { this.focused = true; }};
  vm.runInNewContext(source, {
    document:{querySelector:() => page ? {querySelectorAll:() => buttons, querySelector:() => error ? invalid : null} : null,
      getElementById:id => inputs[Number(id.split('-')[1])]},
    window:{addEventListener(k,fn) { (events[k] ||= []).push(fn); }}
  });
  return {inputs,buttons,invalid,events};
}
const ui = setup({error:true});
assert.equal(ui.invalid.focused, true, 'Server validation moves focus to the described error');
ui.buttons.forEach((button,i) => {
  assert.equal(button.hidden, false);
  assert.equal(ui.inputs[i].parentElement.classList.added, 'has-toggle');
  button.listeners.click();
  assert.equal(ui.inputs[i].type, 'text');
  assert.equal(button.attrs['aria-pressed'], 'true');
  assert.equal(button.textContent, 'Скрыть');
  assert.match(button.attrs['aria-label'], i ? /повтор пароля/ : /пароль/);
  button.listeners.click();
  assert.equal(ui.inputs[i].type, 'password');
  assert.equal(button.attrs['aria-pressed'], 'false');
  assert.equal(ui.inputs[i].value, 'unchanged-test-value', 'Revealing never changes the submitted value');
  button.listeners.click();
});
ui.events.pagehide.forEach(fn => fn());
assert.ok(ui.inputs.every(input => input.type === 'password'), 'Leaving conceals both fields');
ui.buttons[0].listeners.click();
ui.events.pageshow.forEach(fn => fn());
assert.ok(ui.inputs.every(input => input.type === 'password'), 'Restoring Back/Forward also conceals');
assert.doesNotThrow(() => setup({page:false}));
console.log('Auth UI: password visibility, values, accessibility, error focus and history restoration passed');
