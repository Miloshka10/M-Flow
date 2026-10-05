// Run with: node tests/test_motion.cjs (no dependencies).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/motion.js'), 'utf8');

function setup({reduced = false, blocked = false, animations = 'on', entry = null} = {}) {
  const events = {}, windowEvents = {}, timers = [];
  const classes = new Set();
  const panel = {classList: {add: c => classes.add(c), remove: (...cs) => cs.forEach(c => classes.delete(c))}};
  const switcher = {dataset: {}};
  const root = {dataset: {animations}};
  let stored = entry && JSON.stringify(entry), destination;
  const location = {pathname: '/login', href: 'https://mflow.test/login', origin: 'https://mflow.test', assign: url => destination = url};
  const storage = {
    getItem: () => { if (blocked) throw Error('blocked'); return stored; },
    removeItem: () => { if (blocked) throw Error('blocked'); stored = null; },
    setItem: (_, value) => { if (blocked) throw Error('blocked'); stored = value; }
  };
  const document = {
    documentElement: root,
    addEventListener: (name, fn) => events[name] = fn,
    querySelector: () => panel,
    querySelectorAll: selector => selector === '[data-pending-role]' ? [switcher] : [panel]
  };
  const window = {
    matchMedia: () => ({matches: reduced}),
    addEventListener: (name, fn) => windowEvents[name] = fn,
    setTimeout: fn => { timers.push(fn); return timers.length; }
  };
  vm.runInNewContext(source, {window, document, location, sessionStorage: storage, URL, Date, clearTimeout() {}});
  function click(url, extras = {}) {
    const link = url && {href: url, target: '', hasAttribute: () => false, closest: () => switcher};
    const event = {button: 0, target: {closest: () => link}, preventDefault() { this.prevented = true; }, ...extras};
    events.click(event);
    return event;
  }
  return {click, root, classes, switcher, timers, windowEvents,
    destination: () => destination, stored: () => stored};
}

const normal = setup();
assert.equal(normal.click('https://mflow.test/teacher/login').prevented, true);
assert.equal(normal.switcher.dataset.pendingRole, 'teacher');
assert.equal(normal.classes.size, 0, 'The form must never move or fade');
normal.click('https://mflow.test/teacher/register');
assert.equal(normal.click('https://mflow.test/login').prevented, true);
assert.equal(normal.timers.length, 1, 'Repeated presses must not create extra navigations');
normal.timers[0]();
assert.equal(normal.destination(), 'https://mflow.test/teacher/login');
assert.equal(normal.stored(), null, 'Role animation must not use storage');
normal.windowEvents.pageshow({persisted: true});
assert.equal(normal.classes.size, 0, 'Back/Forward must restore visible, usable form');
assert.equal(normal.switcher.dataset.pendingRole, undefined);

const reduced = setup({reduced: true});
assert.equal(reduced.click('https://mflow.test/teacher/login').prevented, undefined);
assert.equal(reduced.timers.length, 0);
for (const modifiers of [{ctrlKey: true}, {metaKey: true}, {shiftKey: true}, {altKey: true}, {button: 1}]) {
  assert.equal(setup().click('https://mflow.test/teacher/login', modifiers).prevented, undefined);
}
assert.equal(setup().click('https://other.test/teacher/login').prevented, undefined);
assert.equal(setup().click('https://mflow.test/teams').prevented, undefined);
assert.equal(setup().click(null).prevented, undefined, 'Form buttons must not be intercepted');

const blocked = setup({blocked: true});
blocked.click('https://mflow.test/teacher/login');
blocked.timers[0]();
assert.equal(blocked.destination(), 'https://mflow.test/teacher/login');
assert.equal(setup({animations: 'off'}).click('https://mflow.test/teacher/login').prevented, undefined);
assert.equal(setup({entry: {path: '/login', direction: 'prev', time: Date.now() - 6000}}).root.dataset.mfEntry, undefined);
assert.equal(setup({entry: {path: '/register', direction: 'next', time: Date.now()}}).root.dataset.mfEntry, undefined);
assert.equal(setup({reduced: true, entry: {path: '/login', direction: 'prev', time: Date.now()}}).root.dataset.mfEntry, undefined);
console.log('Motion checks passed: highlight-only, navigation, motion settings, modifiers, cache recovery.');
