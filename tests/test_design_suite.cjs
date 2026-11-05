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
const heading = element({dataset:{homeTitle:'Привет, ученик.'},focus(){this.focused=true;}});
const context = element();
const createLink=element(),createPanel=element({open:false,scrollIntoView(){}}),createName=element({focus(){this.focused=true;}});
const main = element({dataset: {suiteView: 'home'},
  querySelector: selector => ({'#dashboard-title':heading,'.ds-project-tools': tools, '.home-details': results, '[data-project-empty]': empty})[selector],
  querySelectorAll: () => cards});
const location = {hash: '#my-projects'};
const document = {title:'Привет, ученик. — M-Flow',body: {hasAttribute: () => true, dataset: {}},
  querySelector: selector => selector === '[data-suite-dashboard]' ? main : selector==='[data-shell-location]'?context:null,
  getElementById:id=>id==='project-create'?createPanel:createName,
  querySelectorAll: selector => selector==='[data-open-project]'?[createLink]:[]};
const window = run(document, location);
assert.equal(main.dataset.suiteView, 'projects');
assert.equal(heading.textContent,'Мои проекты');
assert.equal(document.title,'Мои проекты — M-Flow');
assert.equal(context.textContent,'Мои проекты');
assert.equal(tools.hidden, false);
search.value = '  ТЕПЛИЦА  '; search.handlers.input();
assert.deepEqual(cards.map(card => card.hidden), [false, true]);
filter.value = 'review'; filter.handlers.change();
assert.equal(empty.hidden, false);
search.value = ''; search.handlers.input();
assert.deepEqual(cards.map(card => card.hidden), [true, false]);
location.hash = '#student-results'; window.handlers.hashchange();
assert.equal(main.dataset.suiteView, 'results'); assert.equal(results.open, true);
assert.equal(heading.textContent,'Мои результаты');assert.equal(heading.focused,true);
assert.equal(context.textContent,'Мои результаты');
location.hash = ''; window.handlers.popstate();
assert.equal(main.dataset.suiteView, 'home');
assert.equal(heading.textContent,'Привет, ученик.');
assert.equal(document.title,'Привет, ученик. — M-Flow');
heading.focused=false;
createLink.handlers.click({});
assert.equal(location.hash,'#project-create');assert.equal(createPanel.open,true);
assert.equal(main.dataset.suiteView,'projects');assert.equal(heading.textContent,'Мои проекты');
assert.equal(context.textContent,'Мои проекты');assert.equal(createName.focused,true);
window.handlers.hashchange();
assert.equal(heading.focused,false,'The hash event must keep focus in the visible create field');
location.hash='';window.handlers.popstate();
assert.equal(main.dataset.suiteView,'home');
createLink.handlers.click({ctrlKey:true});
assert.equal(location.hash,'','Modified links keep native navigation');
main.querySelectorAll=()=>[];
run(document);
assert.equal(tools.hidden,true,'An empty project list must not show useless filters');

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
