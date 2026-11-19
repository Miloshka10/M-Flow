"""Teacher review workspace over existing submissions; no additional workflow store.

Routes authorize the teacher/project before calling these bounded readers. Decisions
use the original stage/defense handlers, including their write lock and revision.
"""
from datetime import datetime, timezone
from html import escape
import json
import re
import time
from urllib.parse import urlencode

from deadlines import format_timestamp
from grading import CRITERIA, MAX_SCORE
from interface import icon
from material_links import safe_web_url
from teacher_queue import entries, queue_page, sent_date
from teacher_overview import project_scope

KINDS = {'all': 'Все на проверке', 'stage': 'Этапы', 'defense': 'Видеозащиты', 'report': 'Отчёты — информация'}
SORTS = {'oldest': 'Сначала ранние', 'newest': 'Сначала новые'}
WORK_KEY = re.compile(r'(?:stage-([1-9][0-9]{0,18})-([1-5])|defense-([1-9][0-9]{0,18})|report-([1-9][0-9]{0,18})-([1-9][0-9]{0,18}))\Z')


def work_project(key):
    match = WORK_KEY.fullmatch(key)
    if not match:
        raise ValueError('Некорректная ссылка на работу. Откройте её из списка.')
    return int(match[1] or match[3] or match[4])


def center_url(params=None, work=None, page=None, detail=False):
    values = {k: v for k, v in (params or {}).items() if v not in ('', None)}
    if page is not None:
        values['page'] = page
    if work:
        values['work'] = work
    return '/teacher/review' + ('?' + urlencode(values) if values else '') + ('#review-detail' if detail else '')


def submission_index(conn, teacher_id, group_id=None):
    """One query, current submission metadata only, scoped before transfer from DB."""
    scope, parameters = project_scope(teacher_id, group_id)
    rows = conn.execute(scope + '''
        SELECT h.project_id,h.kind,h.number,h.attempt,h.recorded_at
        FROM work_history h JOIN visible_projects v ON v.id=h.project_id
        JOIN project_stages s ON s.project_id=h.project_id AND s.number=h.number AND s.revision=h.revision
        WHERE h.kind='stage' AND h.event='submit' AND s.state='review'
        UNION ALL
        SELECT h.project_id,h.kind,h.number,h.attempt,h.recorded_at
        FROM work_history h JOIN visible_projects v ON v.id=h.project_id
        JOIN project_defenses d ON d.project_id=h.project_id AND d.revision=h.revision
        WHERE h.kind='defense' AND h.event='submit' AND d.state='submitted' ''', parameters)
    return {(f'stage-{r["project_id"]}-{r["number"]}' if r['kind'] == 'stage' else f'defense-{r["project_id"]}'): dict(r) for r in rows}


def inbox(projects, metadata, kind, sort, page, query='', project_id=None):
    # Reuse existing chronological, bounded pagination. Reports are deliberately
    # separate: they have no accept/return workflow and must not inflate the queue.
    filtered = []
    for p in projects:
        if project_id is not None and p['id'] != project_id:
            continue
        copy = dict(p)
        for field, key in (('current_stage', f'stage-{p["id"]}-{p["current_stage"]["number"]}' if p['current_stage'] else ''),
                           ('defense', f'defense-{p["id"]}')):
            if key in metadata:
                copy[field] = dict(p[field], updated_at=metadata[key]['recorded_at'])
        filtered.append(copy)
    # queue_page's all includes informational reports; the center's all does not.
    # Filter before pagination, rather than dropping rows from an already cut page.
    if kind == 'all':
        copies = [dict(p, reports=[]) for p in filtered]
        result = queue_page(copies, kind, sort, page, query)
    else:
        result = queue_page(filtered, kind, sort, page, query)
    for item in result['items']:
        item['submission'] = metadata.get(item['key'])
        if item['submission']:
            item['date'] = sent_date(item['submission']['recorded_at'])
    return result


def context_for(conn, item, record):
    """At most one prior submission and one decision, never another stage/draft."""
    params = (item['project_id'], item['kind'], record.get('number', 0), record['revision'])
    previous = conn.execute('''SELECT * FROM work_history WHERE project_id=? AND kind=? AND number=?
        AND revision<? AND event='submit' ORDER BY revision DESC LIMIT 1''', params).fetchone()
    decision = conn.execute('''SELECT * FROM work_history WHERE project_id=? AND kind=? AND number=?
        AND revision<? AND event='return' ORDER BY revision DESC LIMIT 1''', params).fetchone()
    return dict(previous=dict(previous) if previous else None, decision=dict(decision) if decision else None)


def connections_for(conn, teacher_id):
    # Same visibility as supervision hub; this does not authorize project reads.
    rows = conn.execute('''SELECT r.id,r.approvals_json,p.name,owner.username AS owner_name
        FROM supervision_requests r JOIN projects p ON p.id=r.project_id JOIN users owner ON owner.id=p.owner_id
        WHERE r.state='pending' AND r.expires>? AND r.owner_id=p.owner_id
        AND ((r.target_id=? AND r.owner_approved=1) OR EXISTS (
            SELECT 1 FROM project_members m WHERE m.project_id=p.id AND m.user_id=?))
        ORDER BY r.id DESC LIMIT 31''', (int(time.time()), teacher_id, teacher_id)).fetchall()
    actionable = [r for r in rows[:30] if teacher_id not in json.loads(r['approvals_json'])]
    return actionable[:3], len(rows) > 30


def wait_label(value, now=None):
    date = sent_date(value)
    if not date:
        return 'Дата отправки неизвестна'
    now = now or datetime.now(timezone.utc)
    if date > now:
        return 'Проверьте дату отправки'
    days = (now - date).days
    if days == 0:
        return 'Ожидает меньше суток'
    return f'Ожидает {days} дн.'


def material(url, label):
    if not url:
        return ''
    if not safe_web_url(url):
        return '<p class="rc-hint">Сохранённая ссылка имеет неподдерживаемый формат.</p>'
    return f'<a class="action-link rc-material" href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{escape(label)} {icon("external")}</a>'


def render_inbox(result, params, selected, explicit):
    html = f'<aside class="rc-inbox" id="review-inbox" tabindex="-1" aria-labelledby="inbox-title" data-mobile-hidden="{str(explicit).lower()}"><div class="rc-inbox-head"><h2 id="inbox-title">{"Отчёты" if params["kind"] == "report" else "Ждут решения"}</h2><span>{result["total"]}</span></div>'
    if not result['items']:
        html += '<div class="rc-empty">' + icon('check') + '<h3>Здесь пока нет работ</h3><p>Новые сдачи появятся после отправки учениками. Попробуйте изменить фильтры.</p><a class="action-link" href="/teacher/review">Сбросить фильтры</a></div>'
    html += '<ul class="rc-list">'
    for item in result['items']:
        meta = item.get('submission')
        repeat = meta and meta['attempt'] > 1
        badge = 'Отчёт' if item['kind'] == 'report' else 'Повторная сдача' if repeat else 'Видеозащита' if item['kind'] == 'defense' else 'Этап'
        date = meta['recorded_at'] if meta else item['sent']
        wait = (wait_label(date) if meta else 'Время отправки неизвестно') if item['requires_decision'] else 'Решение не требуется'
        href = escape(center_url(params, item['key'], detail=True), quote=True)
        html += f'''<li><a class="rc-work" data-work-key="{item['key']}" href="{href}" {('aria-current="true"' if selected and selected['key'] == item['key'] else '')}>
            <strong class="rc-work-project">{escape(item['project_name'])}</strong>
            <span class="rc-work-top"><span class="rc-badge">{badge}</span><span class="rc-wait">{wait}</span></span>
            <span class="rc-work-title">{escape(item['title'])}</span>
            <span class="rc-work-meta">{escape(item['author'] or 'Автор не указан')}</span></a></li>'''
    html += '</ul>'
    if result['pages'] > 1:
        html += '<nav class="rc-pagination" aria-label="Страницы работ">'
        if result['page'] > 1:
            html += f'<a href="{escape(center_url(params, page=result["page"]-1), quote=True)}">{icon("arrow-left")} Назад</a>'
        html += f'<span>{result["page"]} / {result["pages"]}</span>'
        if result['page'] < result['pages']:
            html += f'<a href="{escape(center_url(params, page=result["page"]+1), quote=True)}">Далее {icon("arrow")}</a>'
        html += '</nav>'
    return html + '</aside>'


def render_detail(item, record, context, params, token, explicit=False, error='', submitted=None):
    back = f'<a class="action-link rc-mobile-back" href="{escape(center_url(params), quote=True)}">{icon("arrow-left")} К списку работ</a>'
    html = f'<section class="rc-detail" id="review-detail" tabindex="-1" aria-labelledby="detail-title" data-mobile-hidden="{str(not explicit).lower()}">{back}'
    if not item:
        explanation = ('Результат мог быть проверен другим учителем. Откройте актуальный список; история решений сохранена.' if explicit else
                       'Работ по выбранным условиям пока нет. Новые результаты появятся после отправки учениками.')
        return html + '<div class="rc-empty">' + icon('review') + '<h2 id="detail-title">' + ('Работа уже не в очереди' if explicit else 'Всё готово к новой проверке') + f'</h2><p>{explanation}</p><a class="action-link" href="/teacher/review">Открыть список</a></div></section>'
    pid = item['project_id']
    work = f'stage-{record["number"]}' if item['kind'] == 'stage' else 'defense'
    html += f'''<div class="rc-detail-head"><span class="eyebrow">{"ИНФОРМАЦИОННЫЙ ОТЧЁТ" if item['kind'] == 'report' else "ПРОВЕРКА РЕЗУЛЬТАТА"}</span>
        <h2 id="detail-title">{escape(item['project_name'])}</h2><p>{escape(item['title'])}</p></div>
        <div class="rc-detail-links"><a class="action-link" href="/project/{pid}">{icon('folder')} Проект</a>
        <a class="action-link" href="{item['href']}">Открыть в проекте {icon('arrow')}</a></div>'''
    if item['kind'] == 'report':
        return html + f'<p class="rc-hint">Отчёт о задаче — информация о ходе работы. Его прочтение не принимает этап и не выставляет оценку.</p><p class="rc-hint">{escape(item["author"] or "Автор не указан")} · {format_timestamp(item["sent"])}</p><p class="rc-result">{escape(item["body"])}</p></section>'
    meta = item.get('submission')
    date = meta['recorded_at'] if meta else item['sent']
    attempt = f'Отправка №{meta["attempt"]}' if meta and meta['attempt'] else 'Номер отправки неизвестен'
    html += f'<p class="rc-hint"><strong>{attempt}</strong></p>'
    html += f'<p class="rc-hint">Автор: {escape(item["author"] or "не указан")} · {"Отправлено" if meta else "Обновлено"}: {format_timestamp(date)}{(" · " + wait_label(date)) if meta else ""}</p>'
    if not meta:
        html += '<p class="rc-hint">История этой отправки не записана: показана дата последнего обновления, точное время ожидания неизвестно.</p>'
    result = record.get('result', '') if item['kind'] == 'stage' else record.get('description', '')
    url = record.get('presentation_url', '') if item['kind'] == 'stage' else record.get('video_url', '')
    previous = context.get('previous')
    html += '<div class="rc-section"><h3>' + ('Сравнение отправок' if previous else 'Текущая работа') + '</h3>'
    if previous:
        html += f'<div class="rc-comparison"><article class="rc-version" data-version="previous"><h4>Предыдущая сдача</h4><small>{format_timestamp(previous["recorded_at"])}</small><p class="rc-result">{escape(previous["result"] or "Описание не указано.")}</p>{material(previous["resource_url"], "Материал прошлой сдачи")}</article><article class="rc-version" data-version="current"><h4>Новая сдача</h4><small>{format_timestamp(date)}</small><p class="rc-result">{escape(result or "Описание не указано.")}</p>{material(url, "Открыть презентацию" if item["kind"] == "stage" else "Смотреть видеозащиту")}</article></div>'
    else:
        html += f'<p class="rc-result" data-version="current">{escape(result or "Описание не указано.")}</p>{material(url, "Открыть презентацию" if item["kind"] == "stage" else "Смотреть видеозащиту")}<p class="rc-hint">Более ранних отправок в истории нет. Черновики не считаются сдачами.</p>'
    html += '</div>'
    decision = context.get('decision')
    comment = decision['comment'] if decision else record.get('teacher_comment')
    if comment:
        html += f'<aside class="rc-comment"><h3>Замечание к предыдущей сдаче</h3><p class="rc-result">{escape(comment)}</p><p class="rc-hint">Новая версия ещё не проверена.{(" Решение: " + escape(decision["actor_name"]) + " · " + format_timestamp(decision["recorded_at"])) if decision else " Ранее сохранённое замечание; точная история неизвестна."}</p></aside>'
    html += f'<div class="rc-detail-links"><a class="action-link" href="/project/{pid}/history?work={work}">{icon("history")} Вся история этой работы</a></div>'
    criteria = ''.join(f'<li>{escape(label)} <span>до {maximum}</span></li>' for _, label, maximum in CRITERIA)
    html += f'<details class="rc-history"><summary>Критерии итоговой оценки</summary><p class="rc-hint">Ориентир для обратной связи. Итоговые баллы каждого ученика выставляются отдельно, максимум {MAX_SCORE}.</p><ul class="rc-criteria">{criteria}</ul><a class="action-link" href="/project/{pid}/assessment">Перейти к личным оценкам {icon("arrow")}</a></details>'
    editable = record['state'] in ('review', 'submitted')
    same_version = submitted is None or submitted.get('revision') == str(record['revision'])
    if error:
        html += f'<p class="error" role="alert">{escape(error)}</p>'
    if submitted is not None and (not editable or not same_version):
        html += f'<aside class="rc-comment"><h3>Ваш комментарий не сохранён</h3><p class="rc-result">{escape(submitted.get("teacher_comment", ""))}</p><p>Скопируйте его при необходимости. Повторное решение для изменённой работы не отправлено.</p></aside>'
    if editable:
        value = submitted.get('teacher_comment', '') if submitted is not None and same_version else ''
        html += f'''<form class="rc-decision" method="post" action="{escape(center_url(params, item['key'], detail=True), quote=True)}" data-guard-draft data-draft-unsaved="{int(bool(value))}">
            <h3>Ваше решение</h3><input type="hidden" name="csrf_token" value="{escape(token, quote=True)}"><input type="hidden" name="revision" value="{record['revision']}">'''
        if item['kind'] == 'stage':
            html += f'<input type="hidden" name="number" value="{record["number"]}">'
        html += f'''<label for="review-comment">Комментарий ученикам</label><p class="rc-hint" id="comment-hint">Для возврата напишите, что исправить. Для принятия комментарий необязателен. До 2000 символов.</p>
            <textarea id="review-comment" name="teacher_comment" rows="4" maxlength="2000" aria-describedby="comment-hint" placeholder="Что получилось и что нужно улучшить">{escape(value)}</textarea>
            <div class="rc-decision-actions"><button name="action" value="accept" data-ui-icon="check">Принять {"этап" if item['kind'] == 'stage' else 'видеозащиту'}</button><button class="secondary" name="action" value="return" data-ui-icon="return">На доработку</button></div>
            <p class="rc-hint">Решение сохраняется в истории, ученики получат уведомление. Личные оценки не изменятся.</p></form>'''
    else:
        html += '<p class="rc-hint">Работа уже не ожидает решения. Обновите очередь.</p>'
    return html + '</section>'


def render_center(projects, result, selected, record, context, params, token, connections, explicit=False, error='', submitted=None, groups=()):
    counts = {kind: sum(item['kind'] == kind for p in projects for item in entries(p)) for kind in ('stage', 'defense')}
    stats = ''
    tab_params = {key: params.get(key) for key in ('group', 'q', 'project', 'sort')}
    if tab_params['sort'] == 'oldest':
        tab_params.pop('sort')
    group_id = params.get('group')
    group = next((g for g in groups if g['id'] == group_id), None)
    group_context = (f'<p class="rc-hint">Класс: <a href="/groups/{group["id"]}">{escape(group["name"])}</a></p>'
                     if group else '')
    for kind, number, label in (('all', sum(counts.values()), 'Все'), ('stage', counts['stage'], 'Этапы'), ('defense', counts['defense'], 'Видеозащиты')):
        stats += f'<a class="rc-stat" href="{escape(center_url(dict(tab_params, kind=kind)), quote=True)}" {("aria-current=\"true\"" if params["kind"] == kind else "")}>{label} <strong>{number}</strong></a>'
    options = lambda items, value: ''.join(f'<option value="{escape(str(k), quote=True)}" {("selected" if str(k) == str(value) else "")}>{escape(v)}</option>' for k, v in items)
    project_options = options([('', 'Все проекты')] + [(p['id'], p['name']) for p in projects], params.get('project', ''))
    group_options = options([('', 'Все классы')] + [(g['id'], g['name'] + (' · архив' if g['archived'] else ''))
                            for g in groups], group_id or '')
    class_filter = (f'<label><span id="review-class-label">Класс</span>'
                    f'<select name="group" aria-labelledby="review-class-label">{group_options}</select></label>'
                    if groups else '')
    filtered = bool(params['q'] or params.get('project') or group or params['sort'] != 'oldest')
    html = f'''<div class="rc-heading"><h1>Центр проверки</h1><a class="back" href="/teacher">{icon('arrow-left')} В кабинет</a></div>
        {group_context}<div class="rc-toolbar"><nav class="rc-stats" aria-label="Работы на проверке">{stats}</nav>
        <a class="action-link" href="{escape(center_url(dict(tab_params, kind='report')), quote=True)}" {'aria-current="true"' if params['kind'] == 'report' else ''}>Отчёты о задачах</a></div>
        <details class="rc-filter-panel" {'open' if filtered else ''}><summary data-ui-icon="filter">Поиск и фильтры{' · применены' if filtered else ''}</summary><form class="rc-filters" method="get"><label>Поиск<input type="search" name="q" value="{escape(params['q'], quote=True)}" placeholder="Проект, ученик или работа"></label>
        {class_filter}<label><span id="review-project-label">Проект</span><select name="project" aria-labelledby="review-project-label">{project_options}</select></label><label>Работы<select name="kind">{options(KINDS.items(), params['kind'])}</select></label><label>Порядок<select name="sort">{options(SORTS.items(), params['sort'])}</select></label><button data-ui-icon="filter">Показать</button><a class="action-link" href="/teacher/review">Сбросить</a></form></details>
        <div class="rc-layout">{render_inbox(result, params, selected, explicit)}{render_detail(selected, record, context, params, token, explicit, error, submitted)}</div>'''
    requests, more = connections
    html += '<section class="rc-connections"><h2>Согласование руководства</h2>'
    for row in requests:
        html += f'<p><strong>{escape(row["name"])}</strong> · {escape(row["owner_name"])} <a class="action-link" href="/supervision#request-{row["id"]}">Рассмотреть {icon("arrow")}</a></p>'
    if not requests:
        html += '<p class="rc-hint">В показанных запросах вашего решения сейчас не требуется.</p>'
    if more:
        html += '<p class="rc-hint">Показана часть последних запросов. Все согласования доступны по ссылке ниже.</p>'
    return html + f'<a class="action-link" href="/supervision">Все согласования {icon("arrow")}</a><a class="action-link" href="/groups">{icon("groups")} Обзор учебных групп</a></section>'
