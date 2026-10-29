/* Warn before leaving edited work. No storage, autosave or request interception. */
document.addEventListener('DOMContentLoaded', () => {
  'use strict';
  const forms = [...document.querySelectorAll('[data-guard-draft]')];
  if (!forms.length) return;
  let submitting = null;
  const snapshot = form => JSON.stringify([...form.querySelectorAll('input,textarea,select')]
    .filter(field => !['hidden','submit','button','password'].includes(field.type) && !field.disabled)
    .map(field => [field.name, field.type === 'checkbox' || field.type === 'radio' ? field.checked : field.value]));
  const records = forms.map(form => {
    const baseline = snapshot(form);
    const note = document.createElement('p');
    note.className = 'form-unsaved-note'; note.textContent = 'Есть несохранённые изменения.';
    note.setAttribute('role', 'status'); note.hidden = true; form.prepend(note);
    const dirty = () => form.dataset.draftUnsaved === '1' || snapshot(form) !== baseline;
    const update = () => { note.hidden = !dirty(); };
    form.addEventListener('input', update); form.addEventListener('change', update); update();
    return {form, dirty, update};
  });
  document.addEventListener('submit', event => {
    if (!event.defaultPrevented && forms.includes(event.target)) submitting = event.target;
  });
  window.addEventListener('beforeunload', event => {
    if (!records.some(record => record.form !== submitting && record.dirty())) return;
    submitting = null; event.preventDefault(); event.returnValue = '';
  });
  window.addEventListener('pageshow', () => { submitting = null; records.forEach(record => record.update()); });
});
