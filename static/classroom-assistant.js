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
      status.textContent = `${panel.dataset.aiEngine} читает черновик…`;
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
        add(output, 'h3', `Совет ${panel.dataset.aiEngine}`);
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
  document.querySelectorAll('[data-source-picker]').forEach(picker => {
    const form = picker.closest('form');
    const folder = picker.querySelector('[data-source-folder]');
    const zip = form.elements.source_archive;
    const status = picker.querySelector('[data-source-status]');
    const clear = picker.querySelector('[data-clear-sources]');
    const extensions = new Set(picker.dataset.types.split(' '));
    const omitted = new Set(picker.dataset.omitted.split(' '));
    let files = [], issue = '', attaching = false;
    if ('webkitdirectory' in folder) picker.querySelector('[data-folder-field]').hidden = false;

    function update() {
      files = [...folder.files].map(file => ({file, path: file.webkitRelativePath.split('/').slice(1).join('/')}))
        .filter(({path}) => {
          const parts = path.split('/');
          const name = parts.pop();
          return name.includes('.') && !path.split('/').some(part => part.startsWith('.')) &&
            !parts.some(part => omitted.has(part.toLowerCase())) && extensions.has(name.split('.').pop().toLowerCase());
        });
      const total = files.reduce((bytes, {file}) => bytes + file.size, 0);
      issue = '';
      if (folder.files.length && !files.length) issue = 'В этой папке нет поддерживаемых текстовых файлов.';
      else if (files.length > Number(picker.dataset.countLimit) || total > Number(picker.dataset.totalLimit) ||
               files.some(({file}) => file.size > Number(picker.dataset.fileLimit))) {
        issue = 'Выберите папку поменьше или подготовьте ZIP с нужными исходниками.';
      } else if (zip.files[0] && zip.files[0].size > Number(picker.dataset.archiveLimit)) {
        issue = 'ZIP должен быть не больше 384 КБ.';
      }
      clear.hidden = !(folder.files.length || zip.files.length);
      status.textContent = issue || (files.length ? `К сдаче будут приложены файлы: ${files.length}.`
        : zip.files.length ? `К сдаче будет приложен ${zip.files[0].name}.` : '');
    }
    folder.addEventListener('change', () => { if (folder.files.length) zip.value = ''; update(); });
    zip.addEventListener('change', () => { if (zip.files.length) folder.value = ''; update(); });
    clear.addEventListener('click', () => {
      folder.value = '';
      zip.value = '';
      folder.dispatchEvent(new Event('change', {bubbles: true}));
    });
    form.addEventListener('submit', event => {
      attaching = event.submitter?.value === 'submit';
      update();
      if (attaching && issue) {
        event.preventDefault();
        (folder.files.length ? folder : zip).focus();
      }
    });
    form.addEventListener('formdata', event => {
      if (!attaching) event.formData.delete('source_archive');
      else for (const {file, path} of files) event.formData.append('source_files', file, path);
    });
    update();
  });
  document.querySelectorAll('[data-ai-review-form]').forEach(form => {
    form.addEventListener('submit', () => {
      form.querySelector('[data-ai-progress]').textContent = `${form.dataset.aiEngine} анализирует материалы…`;
    });
    window.addEventListener('pageshow', () => {
      form.querySelector('[data-ai-progress]').textContent = '';
    });
  });
});
