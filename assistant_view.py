"""Presentation for local assistance and comparison of submitted sources."""

from html import escape
from local_assistant import FILE_LIMIT, IGNORED_FOLDERS, SOURCE_FILE_COUNT, TEXT_EXTENSIONS, TEXT_LIMIT, ZIP_LIMIT

FILE_STATUS = {'added': 'Добавлен', 'removed': 'Удалён', 'changed': 'Изменён'}


def draft_panel(project_id, number):
    return f'''<section class="local-assistant" data-local-assistant data-stage-advice="/project/{project_id}/stages/{number}/advice" hidden>
        <button class="secondary" type="button" data-request-advice>Получить совет по доработке</button>
        <p role="status" data-advice-status></p><div data-advice-result hidden></div></section>'''


def source_picker():
    extensions = ' '.join(sorted(TEXT_EXTENSIONS))
    omitted = ' '.join(sorted(IGNORED_FOLDERS))
    return f'''<fieldset class="source-picker" data-local-assistant data-source-picker data-types="{extensions}"
        data-omitted="{omitted}" data-file-limit="{FILE_LIMIT}" data-total-limit="{TEXT_LIMIT}"
        data-count-limit="{SOURCE_FILE_COUNT}" data-archive-limit="{ZIP_LIMIT}">
        <legend>Версия проекта</legend>
        <p>При отправке можно приложить исходники. Учитель увидит изменения между сдачами.</p>
        <label data-folder-field hidden>Папка с исходниками<input type="file" webkitdirectory multiple data-source-folder></label>
        <details><summary>Приложить ZIP с исходниками</summary>
        <label>ZIP с исходниками<input type="file" name="source_archive" accept=".zip,application/zip"></label></details>
        <p class="local-assistant-hint">До 60 текстовых файлов, до 64 КБ на файл и 384 КБ вместе.</p>
        <p role="status" data-source-status></p><button class="secondary" type="button" data-clear-sources hidden>Убрать вложение</button>
        </fieldset>'''


def model_report(result):
    checks = ''.join(f'<li><strong>{escape(row["criterion"])}</strong><p>{escape(row["status"])}</p>'
                     + (f'<blockquote>{escape(row["evidence"])}</blockquote>' if row['evidence'] else '') + '</li>'
                     for row in result['checks'])
    suggestions = ''.join(f'<li>{escape(text)}</li>' for text in result['suggestions'])
    changes = ''.join(f'<li><strong>{escape(row["description"])}</strong><p>{escape(row["source"])}</p>'
                      f'<blockquote>{escape(row["quote"])}</blockquote></li>' for row in result['changes'])
    change_section = f'<h3>Что изменилось по доступным данным</h3><ul>{changes}</ul>' if changes else ''
    return f'''<section class="local-assistant-panel"><h2>Разбор Ollama</h2><p>{escape(result['summary'])}</p>
        <h3>Проверка критериев</h3><ul>{checks}</ul>{change_section}
        <h3>Что проверить дальше</h3><ol>{suggestions}</ol></section>'''


def analysis_page(stage, versions, terms, label, archives, result, token, errors=(), *,
                  has_current_sources=False, has_previous_sources=False):
    current = versions[0]
    previous = versions[1] if len(versions) > 1 else None
    assignment = f'<p>Задание · {escape(terms["title"])}</p>' if terms else ''
    versions_label = f'Отправки №{previous["attempt"]} и №{current["attempt"]}' if previous else (
        f'Отправка №{current["attempt"]}' if current['attempt'] else 'Сохранённый результат')
    if has_current_sources:
        note = 'К предыдущей сдаче исходники не приложены.' if previous else 'Это первая сдача с приложенными исходниками.'
    elif has_previous_sources:
        note = 'К текущей сдаче исходники не приложены.'
    else:
        note = 'К этим отправкам исходники не приложены. Доступно сравнение описаний.'
    files = f'<p class="local-assistant-hint">{note}</p>'
    if archives:
        rows = ''.join(f'<tr><th scope="row">{escape(row["path"])}</th><td>{FILE_STATUS[row["kind"]]}</td>'
                       f'<td>+{row["added"]} / −{row["removed"]}</td></tr>' for row in archives['files'])
        table = (f'<div class="local-assistant-table"><table><thead><tr><th scope="col">Файл</th><th scope="col">Статус</th>'
                 f'<th scope="col">Строки</th></tr></thead><tbody>{rows}</tbody></table></div>') if rows else ''
        ignored = f' · пропущено файлов: {archives["ignored"]}' if archives['ignored'] else ''
        declarations = ''.join(f'<li>{FILE_STATUS[row["kind"]]} <code>{escape(row["name"])}()</code> в {escape(row["path"])}</li>'
                               for row in archives['declarations'])
        functions = f'<h3>Объявления функций Python</h3><ul>{declarations}</ul>' if declarations else ''
        files = f'''<section class="local-assistant-panel"><h2>Сравнение исходников</h2>
            <p>Исходники взяты из двух сдач.</p>
            <p data-archive-verdict><strong>{escape(archives['status'])}</strong></p>
            <p>Сопоставлено путей: {archives['compared']}{ignored} · строки +{archives['added']} / −{archives['removed']}</p>
            {table}{functions}</section>'''
    messages = ''.join(f'<p class="error" role="alert">{escape(error)}</p>' for error in errors)
    report = model_report(result) if result else ''
    return f'''<div class="local-assistant-page" data-local-assistant>
        <section class="local-assistant-panel"><h2>{escape(stage)}</h2>{assignment}
        <p>{versions_label}</p><p><strong>{escape(label)}</strong></p>
        {files if not archives else ''}
        <form method="post" data-ai-review-form>
        <input type="hidden" name="csrf_token" value="{escape(token, quote=True)}">
        <input type="hidden" name="revision" value="{current['revision']}">
        <button type="submit">Запустить анализ</button><p role="status" data-ai-progress></p></form></section>
        {messages}{files if archives else ''}{report}</div>'''
