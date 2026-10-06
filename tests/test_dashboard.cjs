const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/dashboard.js'), 'utf8');
let ready, clicked, focused = 0, scroll;
const panel = {open: false, scrollIntoView: options => { scroll = options; }};
const context = {document: {
  addEventListener: (_, callback) => { ready = callback; },
  getElementById: id => id === 'project-create' ? panel : {focus: () => { focused++; }},
  querySelectorAll: () => [{addEventListener: (_, callback) => { clicked = callback; }}]
}};
vm.runInNewContext(source, context); ready();
let prevented = false;
clicked({preventDefault: () => { prevented = true; }});
assert.equal(panel.open, true); assert.equal(focused, 1); assert.equal(prevented, true);
assert.equal(scroll.behavior, 'auto');
context.document.getElementById = () => null;
assert.doesNotThrow(() => ready());
console.log('Dashboard checks passed: project form opening, focus, reduced-motion-safe scrolling, other pages.');
