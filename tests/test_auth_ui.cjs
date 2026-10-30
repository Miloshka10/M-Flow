// Run with node tests/test_auth_ui.cjs. Tests actual enhancement behavior, no dependencies.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/auth.js'), 'utf8');

function setup({error = false, page = true} = {}) {
  const events = {};
  const inputs = ['password', 'repeat_password'].map(name => ({name, type:'password', readOnly:false, value:'unchanged-test-value',
    parentElement:{classList:{add(c) { this.added = c; }}}}));
  const buttons = inputs.map((input, i) => ({hidden:true, attrs:{'aria-controls':`input-${i}`}, listeners:{},
    getAttribute(k) { return this.attrs[k]; }, setAttribute(k,v) { this.attrs[k] = v; },
    addEventListener(k,fn) { this.listeners[k] = fn; }}));
  const invalid = {focus() { this.focused = true; }};
  const submit = {textContent:'Создать аккаунт', disabled:false, dataset:{pendingLabel:'Создаём аккаунт…'}};
  const status = {textContent:''};
  const form = {attrs:{}, listeners:{},
    querySelector() { return submit; },
    querySelectorAll(selector) { return selector === '.auth-password-toggle' ? buttons : inputs; },
    setAttribute(k,v) { this.attrs[k] = v; }, removeAttribute(k) { delete this.attrs[k]; },
    addEventListener(k,fn) { this.listeners[k] = fn; }};
  const element = {dataset:{}, listeners:{},querySelectorAll:() => buttons,
    querySelector(selector) { return selector === 'form' ? form : selector === '.auth-submit-status' ? status : error ? invalid : null; },
    addEventListener(k,fn) { this.listeners[k] = fn; }};
  vm.runInNewContext(source, {
    document:{querySelector:() => page ? element : null,
      getElementById:id => inputs[Number(id.split('-')[1])]},
    window:{addEventListener(k,fn) { (events[k] ||= []).push(fn); }}
  });
  return {inputs,buttons,invalid,events,submit,status,form,element};
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
const event = () => ({defaultPrevented:false,preventDefault() { this.defaultPrevented = true; }});
const cancelled = event(); cancelled.defaultPrevented = true;
ui.form.listeners.submit(cancelled);
assert.equal(ui.submit.disabled, false, 'An already-cancelled submit must stay editable');
const first = event(); ui.form.listeners.submit(first);
assert.equal(first.defaultPrevented, false, 'First submit remains an ordinary browser POST');
assert.equal(ui.form.attrs['aria-busy'], 'true');
assert.equal(ui.submit.textContent, 'Создаём аккаунт…');
assert.equal(ui.status.textContent, 'Создаём аккаунт…');
assert.equal(ui.submit.disabled, true);
assert.ok(ui.inputs.every(input => input.readOnly && !input.disabled && input.value === 'unchanged-test-value'),
  'Inputs remain successful form controls; pending state must not lose passwords');
assert.ok(ui.buttons.every(button => button.disabled));
const duplicate = event(); ui.form.listeners.submit(duplicate);
assert.equal(duplicate.defaultPrevented, true, 'Repeated submits are suppressed');
const navigation = {...event(),target:{closest:() => ({})}}; ui.element.listeners.click(navigation);
assert.equal(navigation.defaultPrevented, true, 'Role/navigation links cannot interrupt a pending POST');
ui.events.pagehide.forEach(fn => fn());
ui.events.pageshow.forEach(fn => fn());
assert.equal(ui.submit.disabled, false);
assert.equal(ui.submit.textContent, 'Создать аккаунт');
assert.equal(ui.form.attrs['aria-busy'], undefined);
assert.equal(ui.status.textContent, '');
assert.ok(ui.inputs.every(input => !input.readOnly && input.type === 'password'));
assert.ok(ui.buttons.every(button => !button.disabled));
const retry = event(); ui.form.listeners.submit(retry);
assert.equal(retry.defaultPrevented, false, 'Back/Forward restores a usable submit');
assert.doesNotThrow(() => setup({page:false}));
console.log('Auth UI: visibility, values, accessibility, error focus, ordinary POST, pending/duplicate submit and history restoration passed');
