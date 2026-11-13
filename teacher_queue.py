"""A bounded view of the existing teacher overview, not a new workflow/state store."""
from interface import icon
from datetime import datetime, timedelta, timezone
from html import escape
from urllib.parse import urlencode

from deadlines import MOSCOW
from group_assignments import assignment_date

WORK_KINDS = {'all': 'Все работы', 'stage': 'Этапы', 'defense': 'Видеозащиты', 'report': 'Отчёты — информация'}
WORK_SORTS = {'oldest': 'Сначала ранние', 'newest': 'Сначала новые'}
QUEUE_DEFAULTS = {'q': '', 'focus': 'all', 'kind': 'all', 'sort': 'oldest', 'page': 1, 'group': None, 'project': None}
PROJECT_FOCUS = {
    'all': 'Все проекты', 'overdue': 'Просрочены задачи учеников', 'review': 'Ожидают моей проверки',
    'attention': 'Требуют внимания', 'done': 'Все задачи выполнены',
}
TEACHER_QUEUE_CSS = '<link rel="stylesheet" href="/static/teacher-queue.css?v=20261114-review">'
PAGE_SIZE = 20


def sent_date(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        return (dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt).astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def queue_filters(args):
    filters = {'q': args.get('q', '').strip()[:100]}
    for key, options in (('focus', PROJECT_FOCUS), ('kind', WORK_KINDS), ('sort', WORK_SORTS)):
        value = args.get(key)
        filters[key] = value if value in options else QUEUE_DEFAULTS[key]
    try:
        filters['page'] = max(1, int(args.get('page', '1')))
    except ValueError:
        filters['page'] = 1
    return filters


def entries(project):
    pid = project['id']
    names = [s['name'] for s in project['students']]
    team = ', '.join(names[:3]) + (f' и ещё {len(names)-3}' if len(names) > 3 else '')
    common = dict(project_id=pid, project_name=project['name'], team=team)
    stage = project['current_stage']
    if stage and stage['state'] == 'review':
        yield dict(common, key=f'stage-{pid}-{stage["number"]}', kind='stage',
                   title=f'Этап {stage["number"]}: {stage["title"]}', author=stage.get('author_name'),
                   sent=stage.get('updated_at'), revision=stage['revision'], requires_decision=True,
                   work=f'stage-{stage["number"]}', action='Проверить этап', body='',
                   previous_decision=bool(stage.get('reviewed_by')),
                   history_href=f'/project/{pid}/history?work=stage-{stage["number"]}')
    defense = project['defense']
    if defense.get('revision') and defense['state'] == 'submitted':
        yield dict(common, key=f'defense-{pid}', kind='defense', title='Видеозащита',
                   author=defense.get('author_name'), sent=defense.get('updated_at'), revision=defense['revision'],
                   requires_decision=True, work='defense', action='Проверить видеозащиту', body='')
    for entry in project['reports']:
        report = entry['report']
        yield dict(common, key=f'report-{pid}-{report["id"]}', kind='report',
                   title='Отчёт: ' + entry['task_title'], author=report['username'], sent=report['created_at'],
                   revision=None, requires_decision=False, href=f'/project/{pid}#task-{entry["task_id"]}',
                   action='Открыть задачу и отчёт', body=report['body'])


def matches_query(item, query):
    return not query or query in ' '.join(str(item.get(k) or '') for k in
        ('project_name', 'title', 'author', 'team')).casefold()


def project_matches(project, query):
    return (not query or query in project['name'].casefold()
            or any(query in s['name'].casefold() for s in project['students'])
            or any(matches_query(item, query) for item in entries(project)))


def queue_page(projects, kind='all', sort='oldest', page=1, query=''):
    items = [item for p in projects for item in entries(p)
             if (kind == 'all' or item['kind'] == kind) and matches_query(item, query)]
    for item in items:
        item['date'] = sent_date(item['sent'])

    def key(item):
        dt = item['date']
        stamp = (dt.toordinal(), dt.hour, dt.minute, dt.second, dt.microsecond) if dt else (0,)*5
        if sort == 'newest':
            stamp = tuple(-n for n in stamp)
        # Decisions first, chronological order inside each group, unknown dates last.
        return (not item['requires_decision'], dt is None, stamp, item['project_id'], item['key'])

    items.sort(key=key)
    total = len(items)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(max(1, page), pages)
    start = (page - 1) * PAGE_SIZE
    return dict(items=items[start:start + PAGE_SIZE], total=total, page=page, pages=pages,
                decisions=sum(i['requires_decision'] for i in items), start=start)


def _queue_query(params):
    return urlencode({key: params[key] for key, default in QUEUE_DEFAULTS.items()
                      if key in params and params[key] not in (None, default)})


def queue_url(params, page=None):
    query = _queue_query(params if page is None else dict(params, page=page))
    return '/teacher' + ('?' + query if query else '') + '#review-queue'


def review_url(project_id, work, params=None):
    query = _queue_query(params or {})
    return f'/teacher/review/{project_id}/{work}' + ('?' + query if query else '')


def render_classrooms(groups, project_ids, decisions):
    active = [group for group in groups if not group['archived']]
    if not active:
        return ''
    cards = []
    for group in active:
        group_id = group['id']
        count = sum(decisions.get(pid, 0) for pid in project_ids.get(group_id, ()))
        assignment = ''
        if group['assignment_title']:
            assignment = f'<span>{escape(group["assignment_title"])} · {assignment_date(group["assignment_due"])}</span>'
        cards.append((count, f'''<article class="classroom-queue-card">
            <a href="/groups/{group_id}"><strong>{escape(group['name'])}</strong></a>
            {assignment}
            <a class="action-link" href="/teacher?group={group_id}#review-queue">На проверке: {count} {icon('arrow')}</a>
            </article>'''))
    cards.sort(key=lambda card: -card[0])
    return f'''<section class="classroom-queue" aria-labelledby="classroom-queue-title">
        <div><h2 id="classroom-queue-title">Учебные группы</h2><a class="action-link" href="/groups">Все группы {icon('arrow')}</a></div>
        <div class="classroom-queue-grid">{"".join(card for _, card in cards[:3])}</div>
        </section>'''


def render_queue(result, params, has_projects=True, *, groups=(), group_projects=None):
    group_id = params.get('group')
    group_projects = group_projects or {}
    now = datetime.now(timezone.utc)
    html = ''
    previous_group = None
    first_decision = next((item['key'] for item in result['items'] if item['requires_decision']), None)
    for item in result['items']:
        group = 'Требуется решение' if item['requires_decision'] else 'Отчёты — информация'
        if group != previous_group:
            html += f'<h3>{group}</h3>'
            previous_group = group
        date = item['date']
        try:
            local = date.astimezone(MOSCOW) if date else None
        except OverflowError:
            local = None
        date_html = (f'<time datetime="{date.isoformat()}">Отправлено: {local:%d.%m.%Y %H:%M} МСК</time>'
                     if local else 'Дата отправки не указана')
        waiting = ''
        if item['requires_decision'] and local and date <= now:
            hours = (now - date) // timedelta(hours=1)
            elapsed = f'{hours // 24} д.' if hours >= 24 else f'{hours} ч.' if hours else 'меньше часа'
            waiting = f'<strong class="queue-wait">Ожидание: {elapsed}</strong>'
        label = {'stage': 'Этап ожидает проверки', 'defense': 'Видеозащита ожидает проверки',
                 'report': 'Информационный отчёт · решение не требуется'}[item['kind']]
        author = escape(item['author'] or 'не указан')
        team = f'<p class="t-muted">Команда: {escape(item["team"])}</p>' if item['team'] else ''
        body = f'<p class="teacher-queue-body">{escape(item["body"])}</p>' if item['body'] else ''
        material = (f'<details class="ds-queue-material"><summary>{"Отчёт и команда" if item["kind"] == "report" else "Команда проекта"}</summary>{team}{body}</details>'
                    if team or body else '')
        previous = '<p class="work-previous-decision">Есть предыдущее решение</p>' if item.get('previous_decision') else ''
        history = (f'<a class="action-link work-review-history" href="{item["history_href"]}">История этапа</a>'
                   if item.get('previous_decision') else '')
        target = (review_url(item['project_id'], item['work'], dict(params, page=result['page']))
                  if item['requires_decision'] else item['href'])
        classes = [group['name'] for group in groups if item['project_id'] in group_projects.get(group['id'], ())
                   and (group_id == group['id'] if group_id else not group['archived'])]
        scope = escape(classes[0]) + (f' · ещё {len(classes) - 1}' if len(classes) > 1 else '') if classes else ''
        html += f'''<article class="teacher-attention-item teacher-queue-item" data-work-kind="{item['kind']}"
            data-work-key="{item['key']}"><span class="v3-submission-mark" aria-hidden="true">{icon('video' if item['kind'] == 'defense' else 'review')}</span>
            <div class="v3-submission-main"><strong class="v3-submission-label">{label}</strong><h4>{escape(item['project_name'])}</h4>
            <p class="v3-submission-type">{escape(item['title'])}</p>
            <p class="t-muted">Автор: {author}{f' · {scope}' if scope else ''}</p>{previous}{material}</div>
            <div class="v3-submission-side"><p class="queue-timing">{waiting}{date_html}</p>
            <a class="{'btn' if item['key'] == first_decision else 'action-link'}" href="{escape(target, quote=True)}">{item['action']} {icon('arrow')}</a>{history}</div></article>'''
    if not result['items']:
        filtered = bool(params.get('q') or params.get('project') or params.get('focus', 'all') != 'all' or params.get('kind', 'all') != 'all')
        if filtered:
            html += (f'<p class="t-muted">Нет работ по выбранным условиям. '
                     f'<a class="action-link" href="{queue_url(dict(group=group_id))}">Сбросить фильтры</a></p>')
        elif has_projects or group_id:
            html += '<div class="work-empty"><h3>Сейчас нет работ для проверки</h3><p>Новые сдачи появятся здесь после отправки учениками.</p></div>'
        else:
            html += '<div class="work-empty"><h3>Здесь появятся работы учеников</h3><p>Подключите первый проект, чтобы начать проверять сдачи.</p></div>'
    if result['pages'] > 1:
        links = ''
        if result['page'] > 1:
            links += f'<a class="action-link" href="{escape(queue_url(params, result["page"]-1), quote=True)}">{icon('arrow-left')} Предыдущая</a>'
        links += f'<span>Страница {result["page"]} из {result["pages"]}</span>'
        if result['page'] < result['pages']:
            links += f'<a class="action-link" href="{escape(queue_url(params, result["page"]+1), quote=True)}">Следующая {icon('arrow')}</a>'
        html += f'<nav class="teacher-queue-pages" aria-label="Страницы очереди">{links}</nav>'
    return html
