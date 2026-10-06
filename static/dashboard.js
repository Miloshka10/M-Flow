/* Native details remains usable without JavaScript. No new requests or storage. */
document.addEventListener('DOMContentLoaded', () => {
  const panel = document.getElementById('project-create');
  if (!panel) return;
  document.querySelectorAll('[data-open-project]').forEach(link => {
    link.addEventListener('click', event => {
      event.preventDefault();
      panel.open = true;
      document.getElementById('project-name').focus({preventScroll: true});
      panel.scrollIntoView({block: 'center', behavior: 'auto'});
    });
  });
});
