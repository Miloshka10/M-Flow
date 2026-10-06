/* One save path for mouse, touch and keyboard. Move cards only after server success. */
function updateKanbanCounts() {
  document.querySelectorAll('.kanban-column').forEach(column => {
    const cards = column.querySelector('.kanban-cards');
    const count = cards.querySelectorAll('.task-card').length;
    column.querySelector('.kanban-count').textContent = count;
    const empty = cards.querySelector('.kanban-empty');
    if (count && empty) empty.remove();
    if (!count && !empty) {
      const message = document.createElement('div');
      message.className = 'kanban-empty'; message.textContent = 'Пока нет задач'; cards.appendChild(message);
    }
  });
}
async function saveTask(card, endpoint, value) {
  if (card.dataset.saving === '1') throw new Error('Дождитесь сохранения задачи.');
  card.dataset.saving = '1';
  const controls = card.querySelectorAll('select');
  controls.forEach(control => control.disabled = true);
  try {
    const response = await fetch(window.location.pathname + '/' + endpoint, {
      method: 'POST', headers: {'Content-Type': 'application/json',
        'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]')?.content || ''},
      body: JSON.stringify({task_id: Number(card.dataset.taskId), [endpoint]: value})
    });
    if (response.redirected || response.status === 401) throw new Error('Сессия завершена. Войдите в аккаунт заново.');
    let result;
    try { result = await response.json(); }
    catch (_) { throw new Error('Сервер вернул неожиданный ответ. Повторите попытку позже.'); }
    if (!response.ok || !result.success) throw new Error(result.error || 'Не удалось сохранить задачу.');
    return result;
  } catch (error) {
    if (error instanceof TypeError) throw new Error('Не удалось связаться с сервером. Проверьте соединение и повторите попытку.');
    throw error;
  } finally {
    delete card.dataset.saving;
    controls.forEach(control => control.disabled = false);
  }
}
function updateProjectMetrics(metrics, deadlineText) {
  if (!metrics) return;
  for (const state of ['todo', 'progress', 'done', 'overdue']) {
    const count = document.querySelector('.metric-' + state + ' strong');
    if (count) count.textContent = metrics[state];
  }
  const percent = document.querySelector('[data-project-progress]');
  const fill = document.querySelector('[data-project-progress-fill]');
  const text = document.querySelector('[data-project-progress-text]');
  if (percent) percent.textContent = metrics.percent + '%';
  if (fill) fill.style.width = metrics.percent + '%';
  if (text) text.textContent = metrics.done + ' из ' + metrics.total + ' задач выполнено';
  const summary = document.querySelector('[data-workspace-task-summary]');
  if (summary) summary.textContent = metrics.done + ' из ' + metrics.total + ' · просрочено ' + metrics.overdue;
  const deadline = document.querySelector('[data-task-deadline]');
  if (deadline && deadlineText) deadline.textContent = deadlineText;
}
async function moveTask(card, target) {
  const previous = card.closest('.kanban-column').dataset.status;
  const select = card.querySelector('.task-status-select');
  if (previous === target) return;
  try {
    const result = await saveTask(card, 'status', target);
    document.querySelector('.kanban-column[data-status="' + target + '"] .kanban-cards').appendChild(card);
    if (select) select.value = target;
    card.classList.toggle('done-card', target === 'done');
    const deadline = card.querySelector('.task-deadline');
    if (deadline) {
      const now = new Date();
      const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
      const raw = card.dataset.deadline;
      const due = /^\d{4}-\d{2}-\d{2}$/.test(raw || '') ? new Date(raw + 'T00:00:00') : null;
      const days = due ? Math.round((due - today) / 86400000) : null;
      deadline.classList.toggle('deadline-overdue', target !== 'done' && days !== null && days < 0);
      deadline.classList.toggle('deadline-soon', target !== 'done' && days !== null && days >= 0 && days <= 2);
    }
    updateKanbanCounts(); updateProjectMetrics(result.metrics, result.deadline_text);
    (result.students || []).forEach(student => {
      const progress = document.querySelector('[data-student-progress="' + student.id + '"]');
      if (progress) progress.textContent = student.done + ' из ' + student.total + ' задач · ' + (student.total ? Math.round(student.done / student.total * 100) : 0) + '%';
    });
    const message = document.querySelector('#board-status');
    if (message) { message.hidden = false; message.textContent = 'Статус задачи сохранён.'; }
  } catch (error) {
    if (select) select.value = previous;
    alert(error.message || 'Проверьте соединение и повторите попытку.');
  }
}
function changeTaskStatus(select) { return moveTask(select.closest('.task-card'), select.value); }
async function changePriority(select) {
  const previous = select.dataset.savedValue;
  try { await saveTask(select.closest('.task-card'), 'priority', select.value); location.reload(); }
  catch (error) { select.value = previous; alert(error.message || 'Проверьте соединение и повторите попытку.'); }
}
function setupKanban() {
  document.querySelectorAll('.task-priority-select').forEach(select => select.dataset.savedValue = select.value);
  document.querySelectorAll('.task-card').forEach(card => {
    card.addEventListener('dragstart', event => {
      if (card.draggable !== true || card.dataset.saving === '1' || event.target.closest('select,button,input,textarea,a')) {
        event.preventDefault(); return;
      }
      card.classList.add('dragging');
    });
    card.addEventListener('dragend', () => {
      card.classList.remove('dragging');
      document.querySelectorAll('.drag-over').forEach(column => column.classList.remove('drag-over'));
    });
  });
  document.querySelectorAll('.kanban-column').forEach(column => {
    column.addEventListener('dragover', event => { event.preventDefault(); column.classList.add('drag-over'); });
    column.addEventListener('dragleave', event => { if (!column.contains(event.relatedTarget)) column.classList.remove('drag-over'); });
    column.addEventListener('drop', event => {
      event.preventDefault(); column.classList.remove('drag-over');
      const card = document.querySelector('.dragging');
      if (card && card.dataset.saving !== '1') moveTask(card, column.dataset.status);
    });
  });
  updateKanbanCounts();
}
document.addEventListener('DOMContentLoaded', setupKanban);
