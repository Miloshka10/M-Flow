/* Progressive enhancement: native details/forms work if dialogs or JS are unavailable. */
function setupTaskBoard() {
  const board = document.querySelector('#task-board');
  if (!board || typeof HTMLDialogElement === 'undefined') return;
  board.classList.add('board-enhanced');
  const media = window.matchMedia('(max-width: 800px)');
  const tabs = Array.from(board.querySelectorAll('.board-columns [data-column]'));
  const drawers = new Map();
  const projectPath = board.dataset.projectUrl || window.location.pathname;
  if (window.location.pathname !== projectPath) {
    const canonical = new URL(board.dataset.boardLocation || projectPath, window.location.href);
    canonical.hash = window.location.hash;
    history.replaceState(null, '', canonical);
  }
  let active = null;
  let column = board.dataset.column || 'todo';
  function selectColumn(value, updateURL = true) {
    column = value;
    board.dataset.column = value;
    board.querySelectorAll('.kanban-column').forEach(el => el.hidden = media.matches && el.dataset.status !== value);
    tabs.forEach(tab => tab.setAttribute('aria-current', String(tab.dataset.column === value)));
    const field = board.querySelector('input[name="column"]');
    if (field) field.value = value;
    board.querySelectorAll('.board-quick a, input[name="return_to"]').forEach(el => {
      const url = new URL(el.tagName === 'A' ? el.href : el.value, window.location.href);
      if (url.pathname !== projectPath || url.origin !== window.location.origin) return;
      url.searchParams.set('column', value);
      if (el.tagName === 'A') el.href = url.pathname + url.search + url.hash;
      else el.value = url.pathname + url.search + url.hash;
    });
    if (updateURL) {
      const url = new URL(window.location.href);
      url.pathname = projectPath;
      url.searchParams.set('column', value);
      history.replaceState(null, '', url);
    }
  }
  tabs.forEach(tab => tab.addEventListener('click', event => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault(); selectColumn(tab.dataset.column);
  }));
  media.addEventListener('change', () => {
    const card = media.matches && active ? active.closest('.task-card') : null;
    selectColumn(card ? card.closest('.kanban-column').dataset.status : column, !!card);
  });
  selectColumn(column, false);
  function setHash(hash) {
    const url = new URL(window.location.href);
    url.pathname = projectPath;
    url.hash = hash;
    history.replaceState(null, '', url);
  }
  board.querySelectorAll('.board-disclosure').forEach(details => {
    const summary = details.querySelector(':scope > summary');
    const content = details.querySelector(':scope > .task-detail-content');
    if (!summary || !content) return;
    const card = details.closest('.task-card');
    const id = card ? card.id : 'task-create';
    const dialog = document.createElement('dialog');
    dialog.className = 'task-drawer'; dialog.id = 'drawer-' + id;
    const heading = content.querySelector('h2');
    heading.id = 'drawer-title-' + id;
    dialog.setAttribute('aria-labelledby', heading.id);
    const top = document.createElement('div'); top.className = 'drawer-top';
    const label = document.createElement('span'); label.textContent = card ? 'ЗАДАЧА ПРОЕКТА' : 'НОВАЯ ЗАДАЧА';
    const close = document.createElement('button'); close.type = 'button'; close.className = 'drawer-close'; close.textContent = 'Закрыть';
    close.addEventListener('click', () => dialog.close());
    top.append(label, close); dialog.append(top, content);
    details.after(dialog);
    summary.setAttribute('aria-haspopup', 'dialog'); summary.setAttribute('aria-controls', dialog.id); summary.setAttribute('aria-expanded', 'false');
    const open = () => {
      if (active === dialog) return;
      if (active) active.close();
      if (card && media.matches) selectColumn(card.closest('.kanban-column').dataset.status);
      active = dialog; dialog.showModal(); summary.setAttribute('aria-expanded','true');
      document.documentElement.classList.add('board-dialog-open'); setHash(id);
      dialog.scrollTop = 0;
    };
    summary.addEventListener('click', event => {event.preventDefault(); open();});
    dialog.addEventListener('click', event => {
      if (event.target !== dialog) return;
      const rect = dialog.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
    });
    dialog.addEventListener('close', () => {
      summary.setAttribute('aria-expanded','false');
      if (active !== dialog) return;
      active = null; document.documentElement.classList.remove('board-dialog-open');
      setHash('task-board');
      const trigger = card ? summary : board.querySelector('.board-add') || summary;
      trigger.focus({preventScroll:true});
    });
    drawers.set(id, {open, dialog, card});
  });
  const fromHash = () => {
    const hash = window.location.hash.slice(1);
    const target = drawers.get(hash);
    if (target) target.open();
    else if (active) active.close();
  };
  board.querySelectorAll('a[href="#task-create"]').forEach(link => link.addEventListener('click', event => {
    event.preventDefault(); drawers.get('task-create')?.open();
  }));
  window.addEventListener('hashchange', fromHash);
  // Close before moving a card: moving an open native dialog would detach it from the top layer.
  window.beforeBoardTaskMove = card => {
    const entry = drawers.get(card.id);
    if (entry?.dialog.open) entry.dialog.close();
  };
  window.boardTaskSaved = (card, target) => {
    if (media.matches) selectColumn(target);
    tabs.forEach(tab => {
      const count = board.querySelector('.kanban-column[data-status="' + tab.dataset.column + '"] .kanban-count');
      const label = tab.querySelector('[data-column-count]');
      if (count && label) label.textContent = count.textContent;
    });
    card.querySelector('.task-title')?.focus({preventScroll:true});
  };
  if (board.dataset.openCreate === '1') drawers.get('task-create')?.open();
  else fromHash();
}
document.addEventListener('DOMContentLoaded', setupTaskBoard);
