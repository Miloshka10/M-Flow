/* Apply appearance before first paint; no account data is stored. */
(() => {
  'use strict';
  const key = 'mflow-preferences-v1';
  const system = window.matchMedia('(prefers-color-scheme: dark)');
  const defaults = {theme: 'dark', animations: true};
  const root = document.documentElement;
  let toggles = [];
  let preferences;

  function load() {
    preferences = {...defaults};
    try {
      const saved = JSON.parse(localStorage.getItem(key) || 'null');
      if (saved && ['system', 'light', 'dark'].includes(saved.theme)) preferences.theme = saved.theme;
      if (saved && typeof saved.animations === 'boolean') preferences.animations = saved.animations;
    } catch (_) { /* Invalid or blocked storage falls back to defaults. */ }
  }
  load();

  function apply() {
    const theme = preferences.theme === 'system' ? (system.matches ? 'dark' : 'light') : preferences.theme;
    const animations = preferences.animations ? 'on' : 'off';
    if (root.dataset.theme !== theme) root.dataset.theme = theme;
    if (root.dataset.animations !== animations) root.dataset.animations = animations;
    const label = theme === 'dark' ? 'Включить светлую тему' : 'Включить тёмную тему';
    for (const toggle of toggles) {
      toggle.setAttribute('aria-label', label);
      toggle.title = label;
      toggle.hidden = false;
    }
  }
  apply();
  system.addEventListener('change', apply);
  function syncForm() {
    const theme = document.getElementById('site-theme');
    if (!theme) return;
    theme.value = preferences.theme;
    document.getElementById('site-animations').checked = preferences.animations;
  }
  function save() {
    apply();
    syncForm();
    const status = document.getElementById('preferences-status');
    let message = 'Настройки сохранены в этом браузере.';
    try { localStorage.setItem(key, JSON.stringify(preferences)); }
    catch (_) { message = 'Настройки применены, но браузер не разрешает их сохранить.'; }
    if (status) status.textContent = message;
  }

  function refresh() { load(); apply(); syncForm(); }
  window.addEventListener('pageshow', event => { if (event.persisted) refresh(); });
  window.addEventListener('storage', event => { if (event.key === key || event.key === null) refresh(); });
  document.addEventListener('DOMContentLoaded', () => {
    toggles = document.querySelectorAll('[data-theme-toggle]');
    for (const toggle of toggles) {
      toggle.addEventListener('click', () => {
        preferences.theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
        save();
      });
    }
    apply();
    syncForm();
    const form = document.getElementById('site-preferences');
    if (!form) return;
    form.addEventListener('submit', event => event.preventDefault());
    const theme = document.getElementById('site-theme');
    const animations = document.getElementById('site-animations');
    form.addEventListener('change', () => { preferences = {theme: theme.value, animations: animations.checked}; save(); });
    document.getElementById('reset-preferences').addEventListener('click', () => { preferences = {...defaults}; save(); });
  });
})();
