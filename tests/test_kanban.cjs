const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/kanban.js'), 'utf8');
function setup(response) {
  const column = {dataset: {status: 'todo'}};
  const select = {value: 'done', disabled: false};
  const priority = {value: 'high', disabled: false, dataset: {savedValue: 'normal'}};
  const classes = new Set(), alerts = [];
  const card = {dataset: {taskId: '1', deadline: ''}, closest: () => column,
    querySelectorAll: () => [select, priority],
    querySelector: selector => selector === '.task-status-select' ? select : null,
    classList: {toggle: (name, on) => on ? classes.add(name) : classes.delete(name)}};
  priority.closest = () => card;
  const elements = {
    '[data-project-progress]': {}, '[data-project-progress-fill]': {style: {}},
    '[data-project-progress-text]': {}, '#board-status': {hidden: true},
    '[data-student-progress="3"]': {}
  };
  elements['meta[name="csrf-token"]'] = {content: 'test-csrf-token'};
  const requests = [];
  const context = {Date, alert: message => alerts.push(message),
    window: {location: {pathname: '/project/1'}}, location: {reload() {}},
    fetch: async (url, options) => { requests.push({url, options}); if (response instanceof Error) throw response; return response; },
    document: {addEventListener() {}, querySelectorAll: () => [],
      querySelector: selector => selector.includes('.kanban-column[data-status=') ?
        {appendChild: () => column.dataset.status = 'done'} : elements[selector] || null}
  };
  vm.createContext(context); vm.runInContext(source, context);
  return {context, card, select, priority, column, classes, alerts, elements, requests};
}
(async () => {
  const success = setup({ok: true, status: 200, json: async () => ({success: true,
    metrics: {percent: 50, done: 1, total: 2}, students: [{id: 3, done: 1, total: 1}]})});
  await success.context.moveTask(success.card, 'done');
  assert.equal(success.column.dataset.status, 'done');
  assert.equal(success.elements['[data-project-progress]'].textContent, '50%');
  assert.equal(success.elements['[data-student-progress="3"]'].textContent, '1 из 1 задач · 100%');
  assert.equal(success.select.disabled, false);
  assert.equal(success.requests[0].options.headers['X-CSRF-Token'], 'test-csrf-token');
  for (const response of [new Error('offline'), {ok: false, status: 403, json: async () => ({success: false, error: 'Нет доступа'})},
                          {ok: false, status: 400, json: async () => ({success: false, error: 'Форма устарела'})},
                          {ok: false, status: 401}, {ok: true, redirected: true}]) {
    const failure = setup(response);
    await failure.context.moveTask(failure.card, 'done');
    assert.equal(failure.column.dataset.status, 'todo', 'Failed save must not move the card');
    assert.equal(failure.select.value, 'todo');
    assert.equal(failure.select.disabled, false);
    assert.equal(failure.card.dataset.saving, undefined);
    assert.equal(failure.alerts.length, 1);
  }
  const priorityFailure = setup(new Error('offline'));
  await priorityFailure.context.changePriority(priorityFailure.priority);
  assert.equal(priorityFailure.priority.value, 'normal');
  assert.equal(priorityFailure.priority.disabled, false);
  assert.equal(priorityFailure.requests[0].options.headers['X-CSRF-Token'], 'test-csrf-token');
  console.log('Kanban checks passed: save, live metrics, student progress, network errors, denied access, expired session.');
})().catch(error => { console.error(error); process.exitCode = 1; });
