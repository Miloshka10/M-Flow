/* Progressive enhancement: navigation and forms work without this script. */
(() => {
  'use strict';
  const key = 'mflow-auth-motion';
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  const authPaths = new Set(['/login', '/register', '/teacher/login', '/teacher/register']);
  // Store only a short-lived destination/direction, never values from a form.
  try {
    const entry = JSON.parse(sessionStorage.getItem(key) || 'null');
    sessionStorage.removeItem(key);
    if (!reduced.matches && entry && entry.path === location.pathname &&
        authPaths.has(entry.path) && Date.now() - entry.time >= 0 &&
        Date.now() - entry.time < 5000 && ['next', 'prev'].includes(entry.direction)) {
      document.documentElement.dataset.mfEntry = entry.direction;
    }
  } catch (_) { /* Blocked storage must not block navigation. */ }

  let pending = false;
  let timer;
  function reset() {
    pending = false;
    clearTimeout(timer);
    delete document.documentElement.dataset.mfEntry;
    document.querySelectorAll('.mf-leave-next, .mf-leave-prev').forEach(el =>
      el.classList.remove('mf-leave-next', 'mf-leave-prev'));
    document.querySelectorAll('[data-pending-role]').forEach(el => delete el.dataset.pendingRole);
  }
  // Restore usable forms on Back/Forward, including the browser's page cache.
  window.addEventListener('pageshow', event => { if (event.persisted) reset(); });
  window.addEventListener('pagehide', reset);
  document.addEventListener('animationend', event => {
    if (event.animationName.startsWith('mf-enter-')) delete document.documentElement.dataset.mfEntry;
  });
  document.addEventListener('click', event => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey ||
        event.shiftKey || event.altKey || reduced.matches) return;
    const link = event.target.closest('.auth-role-switch a, .auth-tab');
    if (!link || link.hasAttribute('download') || (link.target && link.target !== '_self')) return;
    const destination = new URL(link.href, location.href);
    if (destination.origin !== location.origin || !authPaths.has(destination.pathname)) return;
    if (pending) { event.preventDefault(); return; }
    if (destination.pathname === location.pathname) return;
    const panel = document.querySelector('.auth-form-inner');
    if (!panel) return;
    event.preventDefault();
    pending = true;
    const switcher = link.closest('.auth-role-switch');
    const teacher = destination.pathname.startsWith('/teacher/');
    const direction = switcher ? (teacher ? 'next' : 'prev') :
      (destination.pathname.endsWith('/register') ? 'next' : 'prev');
    if (switcher) switcher.dataset.pendingRole = teacher ? 'teacher' : 'student';
    delete document.documentElement.dataset.mfEntry;
    panel.classList.add('mf-leave-' + direction);
    timer = window.setTimeout(() => {
      try { sessionStorage.setItem(key, JSON.stringify({path: destination.pathname, direction, time: Date.now()})); }
      catch (_) { /* Ordinary navigation still works. */ }
      location.assign(destination.href);
    }, 120);
  });
})();
