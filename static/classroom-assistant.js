document.addEventListener('DOMContentLoaded', () => {
  const add = (container, tag, text) => {
    const item = document.createElement(tag);
    item.textContent = text;
    container.append(item);
    return item;
  };
  document.querySelectorAll('[data-stage-advice]').forEach(panel => {
    const form = panel.closest('form');
    const button = panel.querySelector('[data-request-advice]');
    const status = panel.querySelector('[data-advice-status]');
    const output = panel.querySelector('[data-advice-result]');
    const editor = form.elements.result;
    panel.hidden = false;
    editor.addEventListener('input', () => {
      if (!output.hidden) status.textContent = 'Текст изменился. Запросите совет для новой версии.';
      output.hidden = true;
    });
    button.addEventListener('click', async () => {
      const result = editor.value;
      if (!result.trim()) {
        status.textContent = 'Сначала добавьте описание результата.';
        editor.focus();
        return;
      }
      output.hidden = true;
      button.disabled = true;
      status.textContent = 'Ollama читает черновик…';
      try {
        const response = await fetch(panel.dataset.stageAdvice, {
          method: 'POST',
          headers: {'Content-Type': 'application/json', 'X-CSRF-Token': form.elements.csrf_token.value},
          body: JSON.stringify({result, revision: form.elements.revision.value})
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Не удалось получить совет.');
        if (editor.value !== result) {
          status.textContent = 'Текст изменился. Запросите совет для новой версии.';
          return;
        }
        output.replaceChildren();
        add(output, 'h3', 'Совет Ollama');
        add(output, 'p', data.summary);
        add(output, 'h4', 'Проверка критериев');
        const checks = add(output, 'ul', '');
        for (const check of data.checks) {
          const row = add(checks, 'li', '');
          add(row, 'strong', check.criterion);
          add(row, 'p', check.status);
          if (check.evidence) add(row, 'blockquote', check.evidence);
        }
        add(output, 'h4', 'Что доработать');
        const suggestions = add(output, 'ol', '');
        data.suggestions.forEach(text => add(suggestions, 'li', text));
        status.textContent = 'Совет готов.';
        output.hidden = false;
      } catch (error) {
        status.textContent = error instanceof SyntaxError ? 'Не удалось получить совет. Обновите страницу.' : error.message;
      } finally {
        button.disabled = false;
      }
    });
  });
  document.querySelectorAll('[data-ai-review-form]').forEach(form => {
    form.addEventListener('submit', () => {
      form.querySelector('[data-ai-progress]').textContent = 'Ollama анализирует материалы…';
    });
    window.addEventListener('pageshow', () => {
      form.querySelector('[data-ai-progress]').textContent = '';
    });
  });
});
