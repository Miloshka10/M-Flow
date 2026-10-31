/* Email capabilities stay out of HTTP URLs and referrers. Ordinary forms still
   accept the code copied from the same email when JavaScript is unavailable. */
(() => {
  'use strict';
  const form = document.querySelector('[data-email-token-form]');
  if (!form) return;
  const token = new URLSearchParams(location.hash.slice(1)).get('token') || '';
  if (location.hash) history.replaceState(null, '', location.pathname + location.search);
  if (!/^[A-Za-z0-9_-]{43}$/.test(token)) return;
  const input = form.querySelector('[name="token"]');
  input.value = token;
  input.type = 'hidden';
  form.querySelector('[data-token-label]').hidden = true;
})();
