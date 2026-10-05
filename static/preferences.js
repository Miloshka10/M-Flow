/* Apply appearance before first paint; no account data is stored. */
(() => {
  'use strict';
  const key = 'mflow-preferences-v1';
  const system = window.matchMedia('(prefers-color-scheme: dark)');
  let preferences;
  function load() {
    preferences = {theme: 'system', animations: true};
    try {
      const saved = JSON.parse(localStorage.getItem(key) || 'null');
      if (saved && ['system', 'light', 'dark'].includes(saved.theme)) preferences.theme = saved.theme;
      if (saved && typeof saved.animations === 'boolean') preferences.animations = saved.animations;
    } catch (_) { /* Invalid or blocked storage falls back to defaults. */ }
  }
  load();
  function apply() {
    document.documentElement.dataset.theme = preferences.theme === 'system' ? (system.matches ? 'dark' : 'light') : preferences.theme;
    document.documentElement.dataset.animations = preferences.animations ? 'on' : 'off';
  }
  apply();
  system.addEventListener('change', apply);
  function syncForm() {
    const theme = document.getElementById('site-theme');
    if (!theme) return;
    theme.value = preferences.theme;
    document.getElementById('site-animations').checked = preferences.animations;
  }
  function refresh() { load(); apply(); syncForm(); }
  window.addEventListener('pageshow', event => { if (event.persisted) refresh(); });
  window.addEventListener('storage', event => { if (event.key === key || event.key === null) refresh(); });
  document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('site-preferences');
    if (!form) return;
    const theme = document.getElementById('site-theme');
    const animations = document.getElementById('site-animations');
    const status = document.getElementById('preferences-status');
    function save() {
      apply();
      try { localStorage.setItem(key, JSON.stringify(preferences)); status.textContent = 'Настройки сохранены в этом браузере.'; }
      catch (_) { status.textContent = 'Настройки применены, но браузер не разрешает их сохранить.'; }
    }
    syncForm();
    form.addEventListener('change', () => { preferences = {theme: theme.value, animations: animations.checked}; save(); });
    document.getElementById('reset-preferences').addEventListener('click', () => { preferences = {theme: 'system', animations: true}; syncForm(); save(); });
  });
})();
