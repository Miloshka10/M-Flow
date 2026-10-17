/* User-visible disclosure behavior, including pages without a header. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/navigation.js'), 'utf8');
function panel() {
  const handlers = {}, attrs = {};
  const trigger = {setAttribute: (key, value) => {attrs[key] = value;}, focus: () => {trigger.focused = true;}};
  return {open: false, handlers, attrs, trigger,
    querySelector: () => trigger,
    addEventListener: (key, fn) => {handlers[key] = fn;},
    contains: node => node === trigger};
}
function setup(panels) {
  const handlers = {};
  let resized;
  const document = {
    addEventListener: (key, fn) => {handlers[key] = fn;},
    querySelectorAll: () => panels, querySelector: () => null,
  };
  const window = {matchMedia: () => ({addEventListener: (_, fn) => {resized = fn;}}), addEventListener: () => {}};
  vm.runInNewContext(source, {document, window});
  handlers.DOMContentLoaded();
  return {handlers, resize: () => resized()};
}
const account = panel(), notifications = panel();
const app = setup([account, notifications]);
assert.equal(account.attrs['aria-expanded'], 'false');
account.open = true; account.handlers.toggle();
assert.equal(account.attrs['aria-expanded'], 'true');
app.handlers.click({target: account.trigger});
assert.equal(account.open, true);
app.handlers.click({target: {}});
assert.equal(account.open, false);
assert.equal(account.attrs['aria-expanded'], 'false');
account.open = true; account.handlers.toggle();
let prevented = false;
app.handlers.keydown({key: 'Escape', preventDefault: () => {prevented = true;}});
assert.equal(account.open, false);
assert.equal(account.trigger.focused, true);
assert.equal(prevented, true);
account.open = true; account.handlers.toggle();
notifications.open = true; notifications.handlers.toggle();
assert.equal(account.open, false);
assert.equal(notifications.open, true);
app.handlers.focusin({target: {}});
assert.equal(notifications.open, false);
account.open = true; account.handlers.toggle();
app.resize();
assert.equal(account.open, false);
assert.doesNotThrow(() => setup([]));
console.log('Navigation checks passed: disclosure state, Escape/focus, outside click, focus leaving, exclusive panels, viewport changes.');
