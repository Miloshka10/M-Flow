(() => {
  const form = document.getElementById('assessment-form');
  if (!form) return;
  const selects = [...form.querySelectorAll('select')];
  function updateTotal() {
    const total = selects.reduce((sum, select) => sum + Number(select.value || 0), 0);
    const complete = selects.every(select => select.value !== '');
    const grade = total >= 39 ? 5 : total >= 30 ? 4 : total >= 21 ? 3 : 2;
    document.getElementById('assessment-total').textContent =
      `Итого: ${total} / 45 · Оценка: ${complete ? grade : 'не все критерии заполнены'}`;
  }
  form.addEventListener('change', updateTotal);
  updateTotal();
})();
