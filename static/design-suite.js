/* Presentation only. All forms, permissions, revisions and POST routes stay server-owned. */
document.addEventListener('DOMContentLoaded', () => {
  'use strict';
  if (!document.body.hasAttribute('data-design-suite')) return;
  document.documentElement.classList.add('ds-enhanced');
  const main = document.querySelector('[data-suite-dashboard]');
  if (main) {
    const initial = main.dataset.suiteView;
    function view() {
      const hash = location.hash.slice(1);
      const next = hash === 'my-projects' || hash === 'project-create' ? 'projects'
        : hash === 'student-results' ? 'results' : initial;
      main.dataset.suiteView = next;
      if (next === 'results') main.querySelector('.home-details').open = true;
    }
    view();
    window.addEventListener('hashchange', view);
    window.addEventListener('popstate', view);
    document.querySelectorAll('[data-open-project]').forEach(link => link.addEventListener('click', () => {
      main.dataset.suiteView = 'projects';
      document.getElementById('project-create').scrollIntoView({block: 'center'});
    }));
    const tools = main.querySelector('.ds-project-tools');
    tools.hidden = false;
    const search = tools.querySelector('[data-project-search]');
    const filter = tools.querySelector('[data-project-filter]');
    const cards = [...main.querySelectorAll('.ds-project-card')];
    function applyFilter() {
      const query = search.value.trim().toLocaleLowerCase('ru');
      cards.forEach(card => {
        card.hidden = !card.dataset.projectName.toLocaleLowerCase('ru').includes(query)
          || filter.value !== 'all' && card.dataset.projectTone !== filter.value;
      });
      main.querySelector('[data-project-empty]').hidden = cards.some(card => !card.hidden);
    }
    search.addEventListener('input', applyFilter);
    filter.addEventListener('change', applyFilter);
  }

  const error = [...document.querySelectorAll('[role="alert"],.error')].some(n => n.textContent.trim());
  function disclose(nodes, label, open = error) {
    if (!nodes.length) return;
    const details = document.createElement('details');
    details.className = 'ds-guide';
    details.open = open;
    const summary = document.createElement('summary');
    summary.textContent = label;
    details.append(summary);
    nodes[0].before(details);
    nodes.forEach(node => details.append(node));
    return details;
  }
  const points = [...document.querySelectorAll('.prep-point')];
  if (points.length) {
    const host = document.querySelector('.prep-form') || points[0].parentElement;
    const nav = document.createElement('div');
    nav.className = 'ds-prep-tabs';
    nav.setAttribute('role', 'group');
    nav.setAttribute('aria-label', 'Части выступления');
    let selected = 0;
    const buttons = points.map((point, index) => {
      point.id = 'prep-section-' + index;
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = String(index + 1).padStart(2, '0') + ' · ' + point.querySelector('h3').textContent;
      button.setAttribute('aria-controls', point.id);
      button.addEventListener('click', () => select(index));
      nav.append(button);
      return button;
    });
    function select(index) {
      selected = index;
      points.forEach((point, i) => {
        point.hidden = i !== selected;
        buttons[i].setAttribute('aria-pressed', String(i === selected));
      });
    }
    host.before(nav);
    select(0);
    host.addEventListener('invalid', event => {
      const index = points.findIndex(p => p.contains(event.target));
      if (index >= 0) select(index);
    }, true);
    disclose([...document.querySelectorAll('.prep-meta,.prep-guidance')], 'О сохранении плана');
    document.querySelectorAll('.prep-point').forEach(point => {
      disclose([...point.querySelectorAll('.prep-hint')], 'Подсказка');
    });
    disclose([...document.querySelectorAll('.prep-intro')], 'О подготовке');
  }
  document.querySelectorAll('.groups-intro:not(.group-identity)').forEach(node => disclose([node], 'О группе и доступе'));
  if (document.body.dataset.designPage === 'learning_groups_page') {
    const form = document.querySelector('form.group-form input[value="create"]')?.closest('.group-panel');
    if (form) disclose([form], 'Новая учебная группа');
  }
  if (document.body.dataset.designPage === 'project_team') {
    document.querySelectorAll('.team-person .team-skills').forEach(node => disclose([node], 'Навыки'));
  }
  // Keep the four decision-relevant columns visible; additional data remains accessible.
  const table = document.querySelector('.group-table');
  if (table) {
    const headers = [...table.querySelectorAll('thead th')];
    if (headers.length === 7) {
      headers[4].textContent = 'Подробности';
      headers[5].remove(); headers[6].remove();
      table.querySelectorAll('tbody tr').forEach(row => {
        const cells = [...row.children];
        if (cells.length !== 7) return;
        const details = document.createElement('details');
        details.className = 'ds-roster-details';
        const summary = document.createElement('summary');
        summary.textContent = 'Открыть';
        details.append(summary);
        for (let index = 4; index < 7; index++) {
          const section = document.createElement('div');
          const label = document.createElement('strong');
          label.textContent = cells[index].dataset.label;
          section.append(label);
          while (cells[index].firstChild) section.append(cells[index].firstChild);
          details.append(section);
        }
        cells[4].dataset.label = 'Подробности';
        cells[4].append(details);
        cells[5].remove(); cells[6].remove();
      });
    }
  }
});
