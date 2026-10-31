const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/design-suite.js'), 'utf8');

function element(props = {}) {
  return Object.assign({
    dataset: {}, handlers: {}, children: [], hidden: false,
    addEventListener(name, callback) { this.handlers[name] = callback; },
    setAttribute(name, value) { this[name] = value; },
    append(child) { this.children.push(child); },
    before() {}, querySelectorAll() { return []; }
  }, props);
}
function run(document, location = {hash: ''}) {
  const window = element();
  document.addEventListener = (_, callback) => { document.ready = callback; };
  document.documentElement = {classList: {add() {}}};
  document.createElement = () => element();
  vm.runInNewContext(source, {document, window, location});
  document.ready();
  return window;
}

// Anonymous pages must not gain authenticated behavior.
run({body: {hasAttribute: () => false}, querySelector() { throw Error('Anonymous page queried'); }});

// Hash navigation, searching and status filtering must work together.
const search = element({value: ''}), filter = element({value: 'all'});
const cards = [element({dataset: {projectName: 'Умная теплица', projectTone: 'revision'}}),
  element({dataset: {projectName: 'Карта школы', projectTone: 'review'}})];
const empty = element(), results = element({open: false});
const tools = element({hidden: true, querySelector: selector => selector.includes('search') ? search : filter});
const main = element({dataset: {suiteView: 'home'},
  querySelector: selector => ({'.ds-project-tools': tools, '.home-details': results, '[data-project-empty]': empty})[selector],
  querySelectorAll: () => cards});
const location = {hash: '#my-projects'};
const document = {body: {hasAttribute: () => true, dataset: {}},
  querySelector: selector => selector === '[data-suite-dashboard]' ? main : null,
  querySelectorAll: () => []};
const window = run(document, location);
assert.equal(main.dataset.suiteView, 'projects');
assert.equal(tools.hidden, false);
search.value = '  ТЕПЛИЦА  '; search.handlers.input();
assert.deepEqual(cards.map(card => card.hidden), [false, true]);
filter.value = 'review'; filter.handlers.change();
assert.equal(empty.hidden, false);
search.value = ''; search.handlers.input();
assert.deepEqual(cards.map(card => card.hidden), [true, false]);
location.hash = '#student-results'; window.handlers.hashchange();
assert.equal(main.dataset.suiteView, 'results'); assert.equal(results.open, true);
location.hash = ''; window.handlers.popstate();
assert.equal(main.dataset.suiteView, 'home');

// Switching preparation parts preserves every original field in the secured form.
const fields = Array.from({length: 5}, (_, index) => element({name: 'part_' + index, value: 'Saved text ' + index}));
const points = fields.map((field, index) => element({children: [field],
  querySelector: () => ({textContent: 'Part ' + index}), contains: target => target === field}));
let nav;
const form = element({children: points, before: node => { nav = node; }});
run({body: {hasAttribute: () => true, dataset: {}},
  querySelector: selector => selector === '.prep-form' ? form : null,
  querySelectorAll: selector => selector === '.prep-point' ? points : []});
assert.equal(nav.children.length, 5);
nav.children[3].handlers.click();
assert.deepEqual(points.map(point => point.hidden), [true, true, true, false, true]);
assert.equal(nav.children[3]['aria-pressed'], 'true');
fields[3].value = 'Unsaved edit';
nav.children[0].handlers.click(); nav.children[3].handlers.click();
assert.equal(fields[3].value, 'Unsaved edit');
assert.equal(form.children, points);
assert.deepEqual(points.flatMap(point => point.children), fields);
form.handlers.invalid({target: fields[4]});
assert.equal(points[4].hidden, false);
assert.equal(nav.children[4]['aria-pressed'], 'true');
assert.ok(nav.children.every(button => button.type === 'button'));
console.log('Design suite checks passed: navigation, combined filters, original form fields and validation.');
