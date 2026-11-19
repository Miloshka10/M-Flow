"""A bounded view of the existing teacher overview, not a new workflow/state store."""
from datetime import datetime, timezone

PAGE_SIZE = 20


def sent_date(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        return (dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt).astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


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
                   href=f'/project/{pid}/stages#stage-{stage["number"]}-result', action='Проверить этап', body='',
                   previous_decision=bool(stage.get('reviewed_by')),
                   history_href=f'/project/{pid}/history?work=stage-{stage["number"]}')
    defense = project['defense']
    if defense.get('revision') and defense['state'] == 'submitted':
        yield dict(common, key=f'defense-{pid}', kind='defense', title='Видеозащита',
                   author=defense.get('author_name'), sent=defense.get('updated_at'), revision=defense['revision'],
                   requires_decision=True, href=f'/project/{pid}/defense', action='Проверить видеозащиту', body='')
    for entry in project['reports']:
        report = entry['report']
        yield dict(common, key=f'report-{pid}-{report["id"]}', kind='report',
                   title='Отчёт: ' + entry['task_title'], author=report['username'], sent=report['created_at'],
                   revision=None, requires_decision=False, href=f'/project/{pid}#task-{entry["task_id"]}',
                   action='Открыть задачу и отчёт', body=report.get('body', ''))


def matches_query(item, query):
    return not query or query in ' '.join(str(item.get(k) or '') for k in
        ('project_name', 'title', 'author', 'team')).casefold()


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
