const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/preferences.js'), 'utf8');
function setup({saved = null, dark = false, blocked = false} = {}) {
  const events = {}, windowEvents = {}, root = {dataset: {}};
  let stored = saved;
  const system = {matches: dark, addEventListener: (_, fn) => system.changed = fn};
  const toggle = {
    hidden: true,
    setAttribute(name, value) { this[name] = value; },
    addEventListener(name, fn) { this[name] = fn; }
  };
  const elements = {
    'site-preferences': {addEventListener: (event, fn) => elements[event] = fn},
    'site-theme': {}, 'site-animations': {}, 'preferences-status': {},
    'reset-preferences': {addEventListener: (_, fn) => elements.reset = fn}
  };
  vm.runInNewContext(source, {
    window: {matchMedia: () => system, addEventListener: (name, fn) => windowEvents[name] = fn},
    document: {
      documentElement: root, getElementById: id => elements[id],
      querySelectorAll: () => [toggle], addEventListener: (name, fn) => events[name] = fn
    },
    localStorage: {
      getItem: () => { if (blocked) throw Error('blocked'); return stored; },
      setItem: (_, value) => { if (blocked) throw Error('blocked'); stored = value; }
    }
  });
  events.DOMContentLoaded();
  return {root, system, elements, toggle, windowEvents, stored: () => stored, updateStorage: value => stored = value};
}
const defaults = setup();
let prevented = false;
defaults.elements.submit({preventDefault(){ prevented = true; }});
assert.equal(prevented,true,'Appearance form must not submit account data or navigate');
assert.equal(defaults.root.dataset.theme, 'dark');
assert.equal(defaults.root.dataset.animations, 'on');
assert.equal(defaults.toggle.hidden, false);
defaults.toggle.click();
assert.equal(defaults.root.dataset.theme, 'light');
assert.equal(defaults.toggle['aria-label'], 'Включить тёмную тему');
assert.equal(defaults.elements['site-theme'].value, 'light');
assert.equal(setup({saved: defaults.stored(), dark: true}).root.dataset.theme, 'light');
const followSystem = setup({saved: JSON.stringify({theme: 'system'})});
assert.equal(followSystem.root.dataset.theme, 'light');
followSystem.system.matches = true;
followSystem.system.changed();
assert.equal(followSystem.root.dataset.theme, 'dark');
const explicit = setup({saved: JSON.stringify({theme: 'light', animations: false}), dark: true});
assert.equal(explicit.root.dataset.theme, 'light');
assert.equal(explicit.root.dataset.animations, 'off');
explicit.elements['site-theme'].value = 'dark';
explicit.elements['site-animations'].checked = true;
explicit.elements.change();
assert.equal(explicit.root.dataset.theme, 'dark');
assert.deepEqual(JSON.parse(explicit.stored()), {theme: 'dark', animations: true});
assert.equal(setup({saved: explicit.stored()}).root.dataset.theme, 'dark');
explicit.elements.reset();
assert.equal(explicit.elements['site-theme'].value, 'dark');
assert.equal(explicit.elements['site-animations'].checked, true);
for (const saved of ['invalid json', JSON.stringify({theme: 'invalid', animations: 'false'}), 'null']) {
  assert.equal(setup({saved}).root.dataset.theme, 'dark');
  assert.equal(setup({saved}).root.dataset.animations, 'on');
}
const blocked = setup({blocked: true});
blocked.elements['site-theme'].value = 'dark';
blocked.elements['site-animations'].checked = false;
blocked.elements.change();
assert.equal(blocked.root.dataset.theme, 'dark');
assert.match(blocked.elements['preferences-status'].textContent, /не разрешает/);
const restored = setup();
restored.updateStorage(JSON.stringify({theme: 'dark', animations: false}));
restored.windowEvents.pageshow({persisted: true});
assert.equal(restored.root.dataset.theme, 'dark');
assert.equal(restored.elements['site-theme'].value, 'dark');
restored.updateStorage(JSON.stringify({theme: 'light', animations: true}));
restored.windowEvents.storage({key: 'mflow-preferences-v1'});
assert.equal(restored.root.dataset.theme, 'light');
console.log('Preferences checks passed: theme, system changes, saving, reset, blocked storage, Back/Forward, other tabs.');
