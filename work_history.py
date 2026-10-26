"""Append-only snapshots of submissions and decisions, inside the project lock.

No initialization backfill or writes on GET. Legacy state is labelled explicitly;
it cannot establish the content or timestamp of an already overwritten attempt.
"""
from html import escape
from urllib.parse import urlencode, urlsplit

from database import execute_schema
from deadlines import format_timestamp
from interface import icon

PAGE_SIZE = 12
WORKS = {'all': 'Все работы', **{f'stage-{n}': f'Этап {n}' for n in range(1, 6)}, 'defense': 'Видеозащита'}
EVENTS = {'legacy': 'Состояние до включения истории', 'submit': 'Отправлено на проверку',
          'return': 'Возвращено на доработку', 'accept': 'Принято учителем'}


def initialize_history(conn):
    execute_schema(conn, '''CREATE TABLE IF NOT EXISTS work_history_sources (
        project_id INTEGER NOT NULL REFERENCES projects(id),
        kind TEXT NOT NULL CHECK(kind IN ('stage','defense')),
        number INTEGER NOT NULL,
        PRIMARY KEY(project_id,kind,number),
        CHECK((kind='stage' AND number BETWEEN 1 AND 5) OR (kind='defense' AND number=0))
    )''')
    execute_schema(conn, '''CREATE TABLE IF NOT EXISTS work_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id),
        kind TEXT NOT NULL CHECK(kind IN ('stage','defense')),
        number INTEGER NOT NULL,
        revision INTEGER NOT NULL,
        event TEXT NOT NULL CHECK(event IN ('legacy','submit','accept','return')),
        attempt INTEGER NOT NULL CHECK(attempt>=0),
        state TEXT NOT NULL,
        result TEXT NOT NULL DEFAULT '',
        resource_url TEXT NOT NULL DEFAULT '',
        comment TEXT NOT NULL DEFAULT '',
        author_id INTEGER REFERENCES users(id), author_name TEXT NOT NULL DEFAULT '',
        actor_id INTEGER REFERENCES users(id), actor_name TEXT NOT NULL DEFAULT '',
        reviewer_id INTEGER REFERENCES users(id), reviewer_name TEXT NOT NULL DEFAULT '',
        source_updated_at TEXT NOT NULL DEFAULT '',
        recorded_at TEXT NOT NULL DEFAULT (CAST(CURRENT_TIMESTAMP AS TEXT)),
        UNIQUE(project_id,kind,number,revision),
        CHECK((kind='stage' AND number BETWEEN 1 AND 5) OR (kind='defense' AND number=0))
    )''')
    execute_schema(conn, 'CREATE INDEX IF NOT EXISTS idx_work_history_project ON work_history(project_id,id)')
    execute_schema(conn, 'CREATE INDEX IF NOT EXISTS idx_work_history_item ON work_history(project_id,kind,number,id)')


def _snapshot(current, kind):
    return dict(revision=current['revision'], state=current['state'],
                result=current.get('result', '') if kind == 'stage' else current.get('description', ''),
                resource_url=current.get('presentation_url', '') if kind == 'stage' else current.get('video_url', ''),
                comment=current.get('teacher_comment', ''),
                author_id=current.get('submitted_by') if kind == 'stage' else current.get('author_id'),
                reviewer_id=current.get('reviewed_by') if kind == 'stage' else current.get('reviewer_id'),
                source_updated_at=current.get('updated_at', ''))


def _append(conn, pid, kind, number, event, attempt, data, actor=None):
    ids = sorted({value for value in (data.get('author_id'), data.get('reviewer_id'), actor) if value is not None})
    names = {row['id']: row['username'] for row in conn.execute(
        'SELECT id,username FROM users WHERE id IN (' + ','.join('?' for _ in ids) + ')', ids)} if ids else {}
    conn.execute('''INSERT INTO work_history(project_id,kind,number,revision,event,attempt,state,
        result,resource_url,comment,author_id,author_name,actor_id,actor_name,reviewer_id,reviewer_name,source_updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (pid,kind,number,data['revision'],event,attempt,data['state'],data['result'],data['resource_url'],
         data['comment'],data['author_id'],names.get(data['author_id'], ''),actor,names.get(actor, ''),
         data['reviewer_id'],names.get(data['reviewer_id'], ''),data['source_updated_at']))


def before_change(conn, pid, kind, number, current):
    """Call only after validation, under the existing project write lock."""
    if conn.execute('SELECT 1 FROM work_history_sources WHERE project_id=? AND kind=? AND number=?',
                    (pid,kind,number)).fetchone():
        return
    conn.execute('INSERT INTO work_history_sources(project_id,kind,number) VALUES(?,?,?)', (pid,kind,number))
    if current['revision']:
        _append(conn, pid, kind, number, 'legacy', 0, _snapshot(current, kind))


def record_change(conn, pid, kind, number, action, current, user):
    if action == 'save':
        return
    data = _snapshot(current, kind)
    data['revision'] += 1
    if action == 'submit':
        # A previous return comment never becomes a decision on this new attempt.
        data.update(comment='', reviewer_id=None)
    attempt = conn.execute('SELECT COALESCE(MAX(attempt),0) AS n FROM work_history WHERE project_id=? AND kind=? AND number=?',
                           (pid,kind,number)).fetchone()['n'] + (action == 'submit')
    _append(conn, pid, kind, number, action, attempt, data, user['id'])


def history_parameters(args):
    work = args.get('work', 'all')
    if work not in WORKS:
        raise ValueError('Неизвестный раздел истории.')
    raw = args.get('page', '1')
    if not raw.isascii() or not raw.isdigit() or len(raw) > 9 or int(raw) < 1:
        raise ValueError('Некорректная страница истории.')
    return work, int(raw)


def load_history(conn, pid, work, page):
    # Access must be checked by the route. Content reads are bounded to 12 events
    # and at most six untracked old work records, without lazy writes or N+1.
    kind, number = ('defense', 0) if work == 'defense' else ('stage', int(work[-1])) if work != 'all' else ('', 0)
    where = 'project_id=?' + (' AND kind=? AND number=?' if kind else '')
    params = (pid,kind,number) if kind else (pid,)
    total = conn.execute('SELECT COUNT(*) AS n FROM work_history WHERE ' + where, params).fetchone()['n']
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, pages)
    rows = [dict(row) for row in conn.execute('SELECT * FROM work_history WHERE ' + where +
        ' ORDER BY id DESC LIMIT ? OFFSET ?', params + (PAGE_SIZE, (page-1)*PAGE_SIZE))]
    legacy = []
    if kind != 'defense':
        legacy += [dict(row) for row in conn.execute('''SELECT s.*,'stage' AS kind,
            a.username AS author_name,r.username AS reviewer_name FROM project_stages s
            LEFT JOIN users a ON a.id=s.submitted_by LEFT JOIN users r ON r.id=s.reviewed_by
            WHERE s.project_id=? AND (?=0 OR s.number=?) AND NOT EXISTS
            (SELECT 1 FROM work_history_sources h WHERE h.project_id=s.project_id AND h.kind='stage' AND h.number=s.number)
            ORDER BY s.number''', (pid,number,number))]
    if kind != 'stage':
        legacy += [dict(row) for row in conn.execute('''SELECT d.*,0 AS number,'defense' AS kind,
            a.username AS author_name,r.username AS reviewer_name FROM project_defenses d
            LEFT JOIN users a ON a.id=d.author_id LEFT JOIN users r ON r.id=d.reviewer_id
            WHERE d.project_id=? AND NOT EXISTS
            (SELECT 1 FROM work_history_sources h WHERE h.project_id=d.project_id AND h.kind='defense' AND h.number=0)''', (pid,))]
    snapshots = [dict(_snapshot(row, row['kind']), kind=row['kind'], number=row['number'], event='legacy', attempt=0,
                      author_name=row.get('author_name') or '', reviewer_name=row.get('reviewer_name') or '', actor_name='') for row in legacy]
    return dict(items=rows, legacy=snapshots, total=total, page=page, pages=pages)


def _safe_url(value):
    try:
        parsed = urlsplit(value)
        return parsed.scheme in ('https','http') and bool(parsed.hostname) and not parsed.username and not parsed.password and not any(c.isspace() for c in value)
    except ValueError:
        return False


def _render_event(row):
    old = row['event'] == 'legacy'
    work = f'Этап {row["number"]}' if row['kind'] == 'stage' else 'Видеозащита'
    attempt = f' · Отправка №{row["attempt"]}' if row['attempt'] else ''
    date = ('Последнее известное обновление: ' + format_timestamp(row['source_updated_at'])) if old else format_timestamp(row['recorded_at'])
    actor = f'<p class="t-muted">{escape(row["actor_name"])}</p>' if row['actor_name'] else ''
    author = f'<p class="t-muted">Автор записи: {escape(row["author_name"])}</p>' if row['author_name'] else ''
    reviewer = f'<p class="t-muted">Учитель сохранённого решения: {escape(row["reviewer_name"])}</p>' if old and row['reviewer_name'] else ''
    comment = f'<aside class="history-comment"><strong>{"Сохранённый комментарий" if old else "Решение учителя"}</strong><p tabindex="0" aria-label="Комментарий учителя; длинный текст можно прокрутить">{escape(row["comment"])}</p></aside>' if row['comment'] else ''
    if row['event'] == 'accept' and not row['comment']:
        comment = '<p class="t-muted">Принято без комментария.</p>'
    resource = ''
    if row['resource_url']:
        url = row['resource_url']
        resource = (f'<a class="action-link" href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">'
                    + ('Презентация этой версии' if row['kind'] == 'stage' else 'Запись этой версии') + f' {icon("external")}</a>') if _safe_url(url) else '<p>Сохранённая ссылка некорректна; переход отключён.</p>'
    explanation = (f'<p class="history-notice">Это доступное состояние старой записи, включая возможный черновик и прежнее замечание. '
        f'Точная дата отправки и содержание прошлых попыток неизвестны. Состояние: {escape({"review":"На проверке","submitted":"На проверке","done":"Принято","accepted":"Принято","progress":"В работе или на доработке","returned":"На доработке","draft":"Черновик"}.get(row["state"], row["state"]))}.</p>') if old else ''
    return f'''<article class="history-event" data-history-event="{row['event']}"><span class="eyebrow">{work}{attempt}</span>
        <h2>{EVENTS[row['event']]}</h2><p class="t-muted">{date}</p>{actor}{author}{reviewer}{explanation}
        <details><summary data-ui-icon="history">Текст и ссылка этой записи</summary><p class="history-text" tabindex="0">{escape(row['result'] or 'Текст не указан.')}</p>{resource}</details>{comment}</article>'''


def render_history(pid, data, work):
    options = ''.join(f'<option value="{key}" {"selected" if key == work else ""}>{label}</option>' for key,label in WORKS.items())
    html = f'''<link rel="stylesheet" href="/static/work-history.css?v=20261019"><p class="history-intro">Здесь сохраняются отправленные версии и решения учителя. Изменения черновиков не записываются.
        Номера отправок учитывают только новые сдачи после включения истории. Ранее перезаписанные версии восстановить нельзя.</p>
        <form class="history-filters" method="get"><label>Работа<select name="work">{options}</select></label><button data-ui-icon="filter">Показать</button></form>
        <p class="t-muted">Записей: {data['total']} · Страница {data['page']} из {data['pages']}. Новые события сверху.</p>'''
    html += ''.join(_render_event(row) for row in data['items']) or '<p>Новых отправок и решений пока нет. Они появятся после отправки работы учителю.</p>'
    if data['pages'] > 1:
        html += '<nav class="history-pages" aria-label="Страницы истории">'
        for page, label in ((data['page']-1, 'Новее'), (data['page']+1, 'Ранее')):
            if 1 <= page <= data['pages']:
                html += f'<a class="action-link" href="/project/{pid}/history?{escape(urlencode(dict(work=work,page=page)),quote=True)}">{icon("arrow-left" if page < data["page"] else "arrow")} {label}</a>'
        html += '</nav>'
    if data['legacy']:
        html += '<section class="history-legacy"><h2>Ранее сохранённые работы</h2><p>Текущие старые записи показаны без восстановления неизвестной истории.</p>'
        html += ''.join(_render_event(row) for row in data['legacy']) + '</section>'
    return html
