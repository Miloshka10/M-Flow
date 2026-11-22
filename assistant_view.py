"""Presentation for local assistance and independent file comparison."""

from html import escape

FILE_STATUS = {'added': 'Добавлен', 'removed': 'Удалён', 'changed': 'Изменён'}


def draft_panel(project_id, number):
    return f'''<section class="local-assistant" data-local-assistant data-stage-advice="/project/{project_id}/stages/{number}/advice" hidden>
        <button class="secondary" type="button" data-request-advice>Получить совет по доработке</button>
        <p role="status" data-advice-status></p><div data-advice-result hidden></div></section>'''


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


def analysis_page(stage, versions, terms, label, archives, result, token, errors=()):
    current = versions[0]
    previous = versions[1] if len(versions) > 1 else None
    assignment = f'<p>Задание · {escape(terms["title"])}</p>' if terms else ''
    versions_label = f'Отправки №{previous["attempt"]} и №{current["attempt"]}' if previous else (
        f'Отправка №{current["attempt"]}' if current['attempt'] else 'Сохранённый результат')
    files = ''
    if archives:
        rows = ''.join(f'<tr><th scope="row">{escape(row["path"])}</th><td>{FILE_STATUS[row["kind"]]}</td>'
                       f'<td>+{row["added"]} / −{row["removed"]}</td></tr>' for row in archives['files'])
        table = (f'<div class="local-assistant-table"><table><thead><tr><th scope="col">Файл</th><th scope="col">Статус</th>'
                 f'<th scope="col">Строки</th></tr></thead><tbody>{rows}</tbody></table></div>') if rows else ''
        ignored = f' · пропущено записей в двух ZIP: {archives["ignored"]}' if archives['ignored'] else ''
        declarations = ''.join(f'<li>{FILE_STATUS[row["kind"]]} <code>{escape(row["name"])}()</code> в {escape(row["path"])}</li>'
                               for row in archives['declarations'])
        functions = f'<h3>Объявления функций Python</h3><ul>{declarations}</ul>' if declarations else ''
        files = f'''<section class="local-assistant-panel"><h2>Сравнение исходников</h2>
            <p data-archive-verdict><strong>{escape(archives['status'])}</strong></p>
            <p>Сопоставлено путей: {archives['compared']}{ignored} · строки +{archives['added']} / −{archives['removed']}</p>
            {table}{functions}</section>'''
    messages = ''.join(f'<p class="error" role="alert">{escape(error)}</p>' for error in errors)
    report = model_report(result) if result else ''
    return f'''<div class="local-assistant-page" data-local-assistant>
        <section class="local-assistant-panel"><h2>{escape(stage)}</h2>{assignment}
        <p>{versions_label}</p><p><strong>{escape(label)}</strong></p>
        <p>Чтобы проверить изменения исходников, приложите два ZIP с одинаковой структурой папок.</p>
        <form method="post" enctype="multipart/form-data" data-ai-review-form>
        <input type="hidden" name="csrf_token" value="{escape(token, quote=True)}">
        <input type="hidden" name="revision" value="{current['revision']}">
        <div class="local-assistant-files"><label>Предыдущий ZIP<input type="file" name="before" accept=".zip,application/zip"></label>
        <label>Текущий ZIP<input type="file" name="after" accept=".zip,application/zip"></label></div>
        <p class="local-assistant-hint">До 384 КБ на архив. Сравниваются текстовые файлы. Ссылки из сдач не открываются.</p>
        <button type="submit">Запустить анализ</button><p role="status" data-ai-progress></p></form></section>
        {messages}{files}{report}</div>'''
