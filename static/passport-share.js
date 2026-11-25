const share = document.querySelector('[data-passport-share]');
if (share) {
  const button = share.querySelector('button');
  const fallback = share.querySelector('label');
  const field = fallback.querySelector('input');
  const status = share.querySelector('[role="status"]');
  const url = new URL(share.dataset.passportShare, window.location.origin).href;
  button.hidden = false;
  button.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(url);
      fallback.hidden = true;
      status.textContent = 'Ссылка скопирована.';
    } catch {
      field.value = url;
      fallback.hidden = false;
      field.focus();
      field.select();
      status.textContent = 'Скопируйте выделенную ссылку.';
    }
  });
}
