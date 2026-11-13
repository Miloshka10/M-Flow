"""Teacher view for one stage submission or video defense."""
from html import escape

from deadlines import format_timestamp
from interface import icon
from material_links import safe_web_url
from teamwork import hidden_token
from ui_components import state_badge


def render_review(project_id, work, record, submission, previous, token, submitted=None):
    defense = work == 'defense'
    title = 'Видеозащита' if defense else f'Этап {record["number"]} · {record["title"]}'
    base = f'/project/{project_id}'
    source = base + ('/defense' if defense else f'/stages?view_stage={record["number"]}#{work}-result')
    source_label = 'Открыть видеозащиту в проекте' if defense else 'Открыть этап в проекте'
    links = f'''<nav class="review-related" aria-label="Связанные разделы">
        <a class="action-link" href="{base}/history?work={work}">{icon("history")} История отправок</a>
        <a class="action-link" href="{source}">{source_label} {icon("arrow")}</a></nav>'''
    if record['state'] not in ('review', 'submitted'):
        message = ('Эта работа уже принята.' if record['state'] in ('done', 'accepted')
                   else 'Работа сейчас не ожидает решения. Новая отправка появится в очереди.')
        return f'<section class="card review-closed"><h2>{title}</h2><p>{message}</p>{links}</section>'

    author = escape(record['author_name'] or 'не указан')
    date = format_timestamp(submission['recorded_at'] if submission else record['updated_at'])
    attempt_label = f'Отправка №{submission["attempt"]}' if submission else 'Номер отправки не сохранён'
    result = record['description'] if defense else record['result']
    resource = record['video_url'] if defense else record['presentation_url']
    if safe_web_url(resource):
        resource_label = 'Открыть запись защиты' if defense else 'Открыть презентацию'
        media = f'<a class="action-link review-material" href="{escape(resource, quote=True)}" target="_blank" rel="noopener noreferrer">{resource_label} {icon("external")}</a>'
    elif resource:
        media = '<p class="t-muted">Не удалось открыть сохранённую ссылку.</p>'
    else:
        media = ''
    result_html = f'<p class="review-text">{escape(result)}</p>' if result else ''
    if not result_html and not media:
        result_html = '<p class="t-muted">Текст результата отсутствует.</p>'

    feedback = ''
    if previous:
        credit = ' · '.join(escape(value) for value in (
            f'Отправка №{previous["attempt"]}' if previous['attempt'] else 'Номер отправки неизвестен',
            previous['actor_name'], format_timestamp(previous['recorded_at'])) if value)
        feedback = f'''<aside class="review-previous" aria-labelledby="previous-review-title">
            <h3 id="previous-review-title">Замечание к предыдущей отправке</h3>
            <p class="review-meta">{credit}</p><p class="review-text">{escape(previous["comment"])}</p></aside>'''
    elif record['teacher_comment']:
        feedback = f'''<aside class="review-previous" aria-labelledby="previous-review-title">
            <h3 id="previous-review-title">Сохранённое замечание</h3>
            <p class="review-meta">Номер предыдущей отправки неизвестен</p>
            <p class="review-text">{escape(record["teacher_comment"])}</p></aside>'''

    confirm = 'Принять защиту' if defense else 'Согласовать тему' if record['number'] == 1 else 'Принять этап'
    comment = escape(submitted.get('teacher_comment', '')[:2000]) if submitted is not None else ''
    number_field = '' if defense else f'<input type="hidden" name="number" value="{record["number"]}">'
    return f'''<div class="review-layout">
        <section class="card review-content" aria-labelledby="review-work-title">
            <header class="review-work-heading">
                <h2 id="review-work-title">{title}</h2>{state_badge("На проверке", "review")}
            </header>
            <p class="review-meta"><strong>{attempt_label}</strong><span>Автор: {author}</span><span>{date}</span></p>
            {result_html}{media}{feedback}
        </section>
        <section class="card review-decision" aria-labelledby="review-decision-title">
            <h2 id="review-decision-title">Решение</h2>
            <form method="post" data-guard-draft data-draft-unsaved="{int(submitted is not None)}">
                {hidden_token(token)}{number_field}<input type="hidden" name="revision" value="{record["revision"]}">
                <label for="review-comment">Комментарий учителя</label>
                <textarea name="teacher_comment" id="review-comment" rows="4" maxlength="2000"
                    aria-describedby="review-comment-hint">{comment}</textarea>
                <p id="review-comment-hint" class="review-meta">При возврате укажите, что исправить.</p>
                <div class="review-buttons">
                    <button name="action" value="accept" data-ui-icon="check">{confirm}</button>
                    <button name="action" value="return" class="secondary" data-ui-icon="return">Вернуть на доработку</button>
                </div>
            </form>
        </section>
        {links}
    </div>'''
