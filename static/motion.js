/* Animate only the selected role highlight, never the page or form. */
(() => {
  'use strict';
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  const authPaths = new Set(['/login', '/register', '/teacher/login', '/teacher/register']);

  let pending = false;
  let timer;
  function reset() {
    pending = false;
    clearTimeout(timer);
    document.querySelectorAll('[data-pending-role]').forEach(el => delete el.dataset.pendingRole);
  }
  // Restore usable forms on Back/Forward, including the browser's page cache.
  window.addEventListener('pageshow', event => { if (event.persisted) reset(); });
  window.addEventListener('pagehide', reset);
  document.addEventListener('click', event => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey ||
        event.shiftKey || event.altKey || reduced.matches || document.documentElement.dataset.animations === 'off') return;
    const link = event.target.closest('.auth-role-switch a');
    if (!link || link.hasAttribute('download') || (link.target && link.target !== '_self')) return;
    const destination = new URL(link.href, location.href);
    if (destination.origin !== location.origin || !authPaths.has(destination.pathname)) return;
    if (pending) { event.preventDefault(); return; }
    if (destination.pathname === location.pathname) return;
    const switcher = link.closest('.auth-role-switch');
    if (!switcher) return;
    event.preventDefault();
    pending = true;
    switcher.dataset.pendingRole = destination.pathname.startsWith('/teacher/') ? 'teacher' : 'student';
    timer = window.setTimeout(() => location.assign(destination.href), 220);
  });
})();
