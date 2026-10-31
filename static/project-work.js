/* Progressive layout only. All writes and state decisions remain on the server. */
document.addEventListener('DOMContentLoaded', () => {
  'use strict';
  const board = document.querySelector('[data-workspace="1"]');
  if (!board) return;
  board.classList.add('pw-enhanced');
  const panels = [...board.querySelectorAll('[data-work-panel]')];
  const tools = board.querySelector('[data-work-tools]');
  const general = board.querySelector('.pw-unassigned');
  let selected = board.dataset.selectedStage || '1';
  function setView(panel, view) {
    panel.dataset.workView = view;
    panel.querySelectorAll('[data-work-view-button]').forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.workViewButton === view));
    });
  }
  panels.forEach(panel => {
    setView(panel, 'tasks');
    panel.querySelectorAll('[data-work-view-button]').forEach(button => {
      button.addEventListener('click', () => setView(panel, button.dataset.workViewButton));
    });
  });
  function show(number) {
    if (!panels.some(p => p.dataset.workPanel === number)) return;
    selected = number;
    const panel = panels.find(p => p.dataset.workPanel === selected);
    panels.forEach(p => p.hidden = p !== panel);
    board.querySelectorAll('[data-work-stage]').forEach(link => {
      if (link.dataset.workStage === selected) link.setAttribute('aria-current', 'step');
      else link.removeAttribute('aria-current');
    });
    panel.querySelector('[data-work-tools-slot]').append(tools);
    panel.querySelector('[data-unassigned-slot]').append(general);
    const view = tools.querySelector('input[name="view_stage"]');
    if (view) view.value = selected;
    tools.querySelectorAll('a[href]').forEach(link => {
      const url = new URL(link.href, location.href);
      if (url.pathname !== board.dataset.projectUrl) return;
      url.searchParams.set('view_stage', selected);
      link.href = url.pathname + url.search + url.hash;
    });
    // Preserve the viewed stage in existing task POST return URLs.
    board.querySelectorAll('input[name="return_to"]').forEach(input => {
      const url = new URL(input.value, location.href);
      url.searchParams.set('view_stage', selected);
      input.value = url.pathname + url.search + url.hash;
    });
    const stageField = board.querySelector('.task-create select[name="stage_number"]') || board.querySelector('#drawer-task-create select[name="stage_number"]');
    if (stageField && board.dataset.openCreate !== '1') stageField.value = selected;
  }
  function fromHash() {
    const id = location.hash.slice(1);
    const stage = /^stage-([1-5])(?:-|$)/.exec(id);
    const card = /^task-\d+$/.test(id) ? document.getElementById(id) : null;
    if (stage) selected = stage[1];
    else if (card?.dataset.taskStage !== 'none' && card?.dataset.taskStage) selected = card.dataset.taskStage;
    show(selected);
    const panel = panels.find(p => p.dataset.workPanel === selected);
    if (stage && /-result$/.test(id)) setView(panel, 'result');
    else if (card || (stage && /-tasks$/.test(id))) setView(panel, 'tasks');
    if (card) {
      for (let node = card.parentElement; node && node !== board; node = node.parentElement) {
        if (node.tagName === 'DETAILS') node.open = true;
      }
    }
    const target = stage ? document.getElementById(id) : null;
    if (target && target !== panels.find(p => p.dataset.workPanel === selected)) {
      target.scrollIntoView({block: 'center'}); target.focus({preventScroll: true});
      const editor = target.querySelector('.pw-editor>summary[aria-controls]');
      if (editor && !document.getElementById(editor.getAttribute('aria-controls')).open) editor.click();
    }
  }
  board.querySelectorAll('[data-work-stage]').forEach(link => link.addEventListener('click', event => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const url = new URL(link.href);
    // Keep current filter state; only the viewed stage changes.
    const current = new URL(location.href);
    current.searchParams.set('view_stage', link.dataset.workStage);
    current.hash = url.hash;
    event.preventDefault(); history.pushState(null, '', current.pathname + current.search + current.hash);
    fromHash(); window.dispatchEvent(new Event('hashchange'));
  }));
  window.addEventListener('hashchange', fromHash);
  window.addEventListener('popstate', fromHash);
  fromHash();

  if (typeof HTMLDialogElement === 'undefined') return;
  board.querySelectorAll('.pw-editor').forEach(details => {
    const trigger = details.querySelector('summary'), content = details.querySelector('.pw-editor-content');
    const wasOpen = details.open;
    const dialog = document.createElement('dialog'); dialog.className = 'task-drawer pw-result-drawer';
    const number = details.closest('[data-work-panel]').dataset.workPanel;
    dialog.id = 'result-editor-' + number;
    const title = content.querySelector('h2'); title.id = 'result-editor-title-' + number;
    dialog.setAttribute('aria-labelledby', title.id);
    const close = document.createElement('button'); close.type = 'button'; close.className = 'drawer-close'; close.textContent = 'Закрыть';
    const top = document.createElement('div'); top.className = 'drawer-top'; top.append(close);
    dialog.append(top, content); details.after(dialog); details.open = false;
    trigger.setAttribute('aria-haspopup', 'dialog'); trigger.setAttribute('aria-controls', dialog.id); trigger.setAttribute('aria-expanded', 'false');
    function open() {dialog.showModal(); trigger.setAttribute('aria-expanded', 'true'); document.documentElement.classList.add('board-dialog-open'); close.focus();}
    trigger.addEventListener('click', event => {event.preventDefault(); open();});
    close.addEventListener('click', () => dialog.close());
    dialog.addEventListener('click', event => {
      if (event.target !== dialog) return;
      const rect = dialog.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
    });
    dialog.addEventListener('close', () => {
      trigger.setAttribute('aria-expanded', 'false'); document.documentElement.classList.remove('board-dialog-open');
      const url = new URL(location.href); url.hash = 'stage-' + number; history.replaceState(null, '', url.pathname + url.search + url.hash);
      trigger.focus({preventScroll: true});
    });
    dialog.addEventListener('keydown', event => {
      if (event.key !== 'Tab') return;
      const targets = [...dialog.querySelectorAll('button:not(:disabled),input:not([type="hidden"]):not(:disabled),textarea:not(:disabled)')].filter(el => el.getClientRects().length);
      const first = targets[0], last = targets[targets.length - 1];
      if (event.shiftKey && document.activeElement === first) {event.preventDefault(); last.focus();}
      else if (!event.shiftKey && document.activeElement === last) {event.preventDefault(); first.focus();}
    });
    if (wasOpen) {show(number); open();}
  });
  // Direct links may load a new document; its result dialog must exist first.
  fromHash();
});
