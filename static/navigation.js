/* Native disclosures also work without JS. Tab follows ordinary links. */
document.addEventListener('DOMContentLoaded', () => {
  const panels = [...document.querySelectorAll('.main-header details')];
  const summary = panel => panel.querySelector('summary');
  function close(panel, restore = false) {
    panel.open = false;
    summary(panel).setAttribute('aria-expanded', 'false');
    if (restore) summary(panel).focus();
  }
  panels.forEach(panel => {
    summary(panel).setAttribute('aria-expanded', String(panel.open));
    panel.addEventListener('toggle', () => {
      summary(panel).setAttribute('aria-expanded', String(panel.open));
      if (panel.open) panels.filter(other => other !== panel).forEach(other => close(other));
    });
  });
  document.addEventListener('click', event => {
    panels.filter(panel => panel.open && !panel.contains(event.target)).forEach(panel => close(panel));
  });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    panels.filter(panel => panel.open).forEach(panel => { close(panel, true); event.preventDefault(); });
  });
  document.addEventListener('focusin', event => {
    panels.filter(panel => panel.open && !panel.contains(event.target)).forEach(panel => close(panel));
  });
  window.matchMedia('(max-width: 800px)').addEventListener('change', () => panels.forEach(panel => close(panel)));
  // Both links share the board URL; its fragment identifies the tasks section.
  function syncProjectTab() {
    const overview = document.querySelector('[data-project-tab="overview"]');
    const tasks = document.querySelector('[data-project-tab="tasks"]');
    if (!overview || !tasks || ![overview, tasks].some(link => link.hasAttribute('aria-current'))) return;
    const onTasks = location.hash.startsWith('#task-');
    overview.toggleAttribute('aria-current', !onTasks);
    tasks.toggleAttribute('aria-current', onTasks);
    (onTasks ? tasks : overview).setAttribute('aria-current', 'page');
  }
  syncProjectTab();
  window.addEventListener('hashchange', syncProjectTab);
});
