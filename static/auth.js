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
})();
