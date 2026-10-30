/* Progressive enhancement: ordinary POST forms also work without JavaScript. */
(() => {
  'use strict';
  const page = document.querySelector('.auth-page');
  if (!page) return;
  page.querySelectorAll('.auth-password-toggle').forEach(button => {
    const input = document.getElementById(button.getAttribute('aria-controls'));
    if (!input) return;
    const subject = input.name === 'repeat_password' ? 'повтор пароля' : 'пароль';
    function conceal() {
      input.type = 'password';
      button.textContent = 'Показать';
      button.setAttribute('aria-label', `Показать ${subject}`);
      button.setAttribute('aria-pressed', 'false');
    }
    conceal();
    button.hidden = false;
    input.parentElement.classList.add('has-toggle');
    button.addEventListener('click', () => {
      if (input.type === 'text') conceal();
      else {
        input.type = 'text';
        button.textContent = 'Скрыть';
        button.setAttribute('aria-label', `Скрыть ${subject}`);
        button.setAttribute('aria-pressed', 'true');
      }
    });
    // A page restored with Back must not leave a previously revealed password visible.
    window.addEventListener('pagehide', conceal);
    window.addEventListener('pageshow', conceal);
  });
  const error = page.querySelector('[aria-invalid="true"], .auth-error');
  if (error) error.focus();

  const form = page.querySelector('form');
  if (!form) return;
  const submit = form.querySelector('[data-pending-label]');
  const status = page.querySelector('.auth-submit-status');
  if (!submit || !status) return;
  const label = submit.textContent;
  const fields = [...form.querySelectorAll('input:not([type="hidden"])')];
  const readonly = fields.map(field => field.readOnly);
  const toggles = [...form.querySelectorAll('.auth-password-toggle')];
  function resetSubmit() {
    delete page.dataset.authPending;
    form.removeAttribute('aria-busy');
    submit.disabled = false;
    submit.textContent = label;
    fields.forEach((field, index) => { field.readOnly = readonly[index]; });
    toggles.forEach(button => { button.disabled = false; });
    status.textContent = '';
  }
  form.addEventListener('submit', event => {
    if (event.defaultPrevented) return;
    if (page.dataset.authPending === 'true') { event.preventDefault(); return; }
    // Native validation has already succeeded. Keep inputs enabled so the browser
    // still sends their values and the CSRF token in the ordinary POST request.
    page.dataset.authPending = 'true';
    form.setAttribute('aria-busy', 'true');
    submit.textContent = submit.dataset.pendingLabel;
    submit.disabled = true;
    fields.forEach(field => { field.readOnly = true; });
    toggles.forEach(button => { button.disabled = true; });
    status.textContent = submit.dataset.pendingLabel;
  });
  page.addEventListener('click', event => {
    if (page.dataset.authPending === 'true' && event.target.closest('a')) event.preventDefault();
  });
  window.addEventListener('pagehide', resetSubmit);
  window.addEventListener('pageshow', resetSubmit);
})();
