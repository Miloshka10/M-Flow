/* Shared shell only. Server-rendered links/forms remain usable without JS. */
document.addEventListener('DOMContentLoaded', () => {
  'use strict';
  document.documentElement.classList.add('ui-shell-enhanced');
  const drawer = document.querySelector('.ui-mobile-menu');
  const mobile = window.matchMedia('(max-width: 800px)');
  const initialNav = new Map([...document.querySelectorAll('[data-nav-key]')].map(link => [link, link.getAttribute('aria-current')]));
  let inerted = [], beforeOverflow = '', managingBackground = false;
  function restoreBackground() {
    inerted.forEach(([node, previous]) => { node.inert = previous; });
    inerted = [];
    if (managingBackground) document.body.style.overflow = beforeOverflow;
    managingBackground = false;
  }
  function syncDrawer() {
    if (!drawer) return;
    restoreBackground();
    if (!drawer.open || !mobile.matches) return;
    beforeOverflow = document.body.style.overflow;
    managingBackground = true;
    // Inert siblings along the path, never an ancestor of the open navigation.
    for (let node = drawer; node.parentElement; node = node.parentElement) {
      [...node.parentElement.children].filter(sibling => sibling !== node).forEach(sibling => {
        inerted.push([sibling, Boolean(sibling.inert)]); sibling.inert = true;
      });
      if (node.parentElement === document.body) break;
    }
    document.body.style.overflow = 'hidden';
    drawer.querySelector('[data-close-navigation]').focus();
  }
  if (drawer) {
    const panel = drawer.querySelector('.ui-mobile-panel');
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-modal', 'true');
    drawer.addEventListener('toggle', syncDrawer);
    drawer.querySelectorAll('[data-close-navigation]').forEach(button => button.addEventListener('click', () => {
      drawer.open = false; restoreBackground(); drawer.querySelector('summary').focus();
    }));
    document.addEventListener('keydown', event => {
      if (!drawer.open || !mobile.matches || event.key !== 'Tab') return;
      const targets = [...drawer.querySelectorAll('.ui-mobile-panel a,.ui-mobile-panel button')].filter(el => el.getClientRects().length);
      const first = targets[0], last = targets[targets.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    });
    mobile.addEventListener('change', () => {
      const hadFocus = drawer.contains(document.activeElement) || managingBackground;
      drawer.open = false; restoreBackground();
      if (!mobile.matches && hadFocus) {
        (document.querySelector('.ui-sidebar [aria-current]') || document.querySelector('.ui-sidebar .brand'))?.focus();
      } else if (mobile.matches && document.activeElement.closest('.ui-sidebar')) {
        drawer.querySelector('summary').focus();
      }
    });
  }
  function revealHash() {
    let id;
    try { id = decodeURIComponent(location.hash.slice(1)); } catch (_) { return; }
    const target = document.getElementById(id);
    if (target) {
      if (target.tagName === 'DETAILS') target.open = true;
      for (let node = target.parentElement; node; node = node.parentElement) {
        if (node.tagName === 'DETAILS') node.open = true;
      }
    }
    // The board retains its original URL, filters and forms. Hide the overview
    // only after JS is ready; the full page is available as a no-JS fallback.
    const workspace = document.querySelector('[data-project-views]');
    if (workspace) {
      const board = workspace.querySelector('#task-board');
      const onTasks = /^task-|^task-board$/.test(id) || Boolean(board && (board.dataset.openCreate === '1' || board.dataset.boardRefresh === '1'));
      const onTeam = id === 'project-members';
      workspace.classList.add('ui-project-views');
      workspace.dataset.projectView = onTeam ? 'team' : onTasks ? 'tasks' : 'overview';
      workspace.querySelectorAll('[data-project-tab]').forEach(link => {
        if (link.dataset.projectTab === (onTeam ? 'team' : onTasks ? 'tasks' : 'overview')) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
      });
      // Reopened validation forms must remain visible, including filtered POSTs.
      if (onTasks && target) target.scrollIntoView({block: 'start'});
    } else if (target) target.scrollIntoView({block:'start'});
    const activeKey = id === 'student-results' ? 'results' : id === 'my-projects' || id === 'teacher-projects' ? 'projects' : id === 'review-queue' ? 'review' : null;
    initialNav.forEach((original, link) => {
      const current = activeKey ? link.dataset.navKey === activeKey : Boolean(original);
      if (current) link.setAttribute('aria-current', original || 'page'); else link.removeAttribute('aria-current');
    });
  }
  revealHash(); window.addEventListener('hashchange', revealHash);
  document.addEventListener('click', event => {
    const link = event.target.closest('a[href]');
    if (link && drawer && drawer.contains(link)) { drawer.open = false; restoreBackground(); }
  });
});
