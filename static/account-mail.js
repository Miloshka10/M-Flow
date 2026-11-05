/* Email capabilities stay out of HTTP URLs and referrers. Ordinary forms still
   accept the code copied from the same email when JavaScript is unavailable. */
(() => {
  'use strict';
  const error = document.querySelector('[data-account-email-error], [data-account-password-error]');
  if (error) error.focus();
  const form = document.querySelector('[data-email-token-form]');
  if (!form) return;
  const input = form.querySelector('[name="token"]');
  const entry = form.querySelector('[data-token-entry]');
  const actions = form.querySelector('[data-token-actions]');
  const submit = form.querySelector('button[type="submit"]');
  const help = form.querySelector('[data-token-help]');
  const token = new URLSearchParams(location.hash.slice(1)).get('token') || '';
  if (location.hash) history.replaceState(null, '', location.pathname + location.search);
  const valid = value => /^[A-Za-z0-9_-]{43}$/.test(value);
  if (valid(token)) {
    input.value = token;
    input.type = 'hidden';
    form.querySelector('[data-token-label]').hidden = true;
    if (entry) entry.hidden = true;
  } else if (entry) {
    entry.open = Boolean(input.value);
  }
  function update() {
    // Format readiness is not verification: expiry and single use stay on the server.
    const ready = valid(input.value);
    if (actions) actions.hidden = !ready;
    if (submit) submit.hidden = !ready;
    if (help) help.hidden = ready;
  }
  input.addEventListener('input', update);
  update();
  window.addEventListener('pageshow', update);
})();
