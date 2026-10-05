const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/preferences.js'), 'utf8');
function setup({saved = null, dark = false, blocked = false} = {}) {
  const events = {}, windowEvents = {}, root = {dataset: {}};
  let stored = saved;
  const system = {matches: dark, addEventListener: (_, fn) => system.changed = fn};
  const elements = {
    'site-preferences': {addEventListener: (_, fn) => elements.change = fn},
    'site-theme': {}, 'site-animations': {}, 'preferences-status': {},
    'reset-preferences': {addEventListener: (_, fn) => elements.reset = fn}
  };
  vm.runInNewContext(source, {
    window: {matchMedia: () => system, addEventListener: (name, fn) => windowEvents[name] = fn},
    document: {documentElement: root, getElementById: id => elements[id], addEventListener: (name, fn) => events[name] = fn},
    localStorage: {
      getItem: () => { if (blocked) throw Error('blocked'); return stored; },
      setItem: (_, value) => { if (blocked) throw Error('blocked'); stored = value; }
    }
  });
  events.DOMContentLoaded();
  return {root, system, elements, windowEvents, stored: () => stored, updateStorage: value => stored = value};
}
const defaults = setup();
assert.equal(defaults.root.dataset.theme, 'light');
assert.equal(defaults.root.dataset.animations, 'on');
defaults.system.matches = true; defaults.system.changed();
assert.equal(defaults.root.dataset.theme, 'dark');
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
assert.equal(explicit.elements['site-theme'].value, 'system');
assert.equal(explicit.elements['site-animations'].checked, true);
for (const saved of ['invalid json', JSON.stringify({theme: 'invalid', animations: 'false'}), 'null']) {
  assert.equal(setup({saved}).root.dataset.theme, 'light');
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
