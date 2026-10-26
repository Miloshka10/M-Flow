"""Bounded, read-only projection of existing results on the student's home page."""
from interface import icon
from collections import defaultdict
from html import escape
from urllib.parse import urlencode

from deadlines import MOSCOW
from grading import MAX_SCORE, load_scores, summarize
from project_stages import STAGES, stages_from_rows, get_stages, stage_summary, stage_needs_revision
from video_defense import defense_for
from project_workspace import next_step
from teacher_queue import sent_date

PAGE_SIZE = 12
STATES = {'all': 'Все результаты', 'revision': 'Нужна доработка', 'review': 'На проверке',
          'accepted': 'Принято', 'draft': 'Ещё не отправлено', 'grade': 'Личные оценки'}


def load_project_states(conn, user):
    """One read of small state fields; never fetch work text or personal grades for cards."""
    rows = conn.execute('''SELECT p.id, s.number, s.state, s.reviewed_by, d.state AS defense_state
        FROM projects p LEFT JOIN project_stages s ON s.project_id=p.id
        LEFT JOIN project_defenses d ON d.project_id=p.id
        WHERE p.owner_id=? OR EXISTS
            (SELECT 1 FROM project_members m WHERE m.project_id=p.id AND m.user_id=?)
        ORDER BY p.id, s.number''', (user['id'], user['id'])).fetchall()
    grouped = defaultdict(list)
    for row in rows:
        grouped[row['id']].append(dict(row))
    states = {}
    for pid, project_rows in grouped.items():
        done, current = stage_summary(stages_from_rows(r for r in project_rows if r['number'] is not None))
        if current:
            revision = stage_needs_revision(current)
            label = ('Нужна доработка' if revision else 'На проверке' if current['state'] == 'review'
                     else 'Выбор темы' if current['number'] == 1 and current['state'] == 'todo'
                     else 'В работе' if current['state'] == 'progress' else 'Можно начинать')
            tone = 'revision' if revision else 'review' if current['state'] == 'review' else 'work'
            label = f'Этап {current["number"]} · {label}'
        else:
            state = project_rows[0]['defense_state'] or 'draft'
            label, tone = {'accepted': ('Защита принята', 'accepted'),
                           'submitted': ('Защита на проверке', 'review'),
                           'returned': ('Доработать защиту', 'revision')}.get(state, ('Готов к защите', 'work'))
        states[pid] = dict(label=label, tone=tone, stages_done=done)
    return states


def load_focus(conn, user, project):
    """Read one project after rechecking membership, including personal grade visibility."""
    row = conn.execute('''SELECT p.owner_id,
        EXISTS (SELECT 1 FROM users u WHERE u.role='teacher' AND (u.id=p.owner_id OR EXISTS
            (SELECT 1 FROM project_members m WHERE m.project_id=p.id AND m.user_id=u.id))) AS has_teacher,
        EXISTS (SELECT 1 FROM project_assessments a WHERE a.project_id=p.id AND a.student_id=? AND a.state='published') AS published
        FROM projects p WHERE p.id=? AND
        (p.owner_id=? OR EXISTS (SELECT 1 FROM project_members m WHERE m.project_id=p.id AND m.user_id=?))''',
        (user['id'], project['id'], user['id'], user['id'])).fetchone()
    if row is None:
        raise PermissionError('Доступ к выбранному проекту изменился. Обновите страницу.')
    stages = get_stages(conn, project['id'])
    defense = defense_for(conn, project['id'])
    step = next_step(stages, 'student', row['owner_id'] == user['id'],
                     bool(row['has_teacher']), defense, bool(row['published']))
    _, current = stage_summary(stages)
    needs_revision = bool(current and stage_needs_revision(current))
    defense_returned = step['key'] == 'defense-prepare' and defense['state'] == 'returned'
    comment = current['teacher_comment'] if needs_revision else defense['teacher_comment'] if defense_returned else ''
    if (needs_revision or defense_returned) and not (comment or '').strip():
        comment = 'Комментарий не указан. Уточни у учителя, что нужно исправить.'
    return dict(project=project, step=step, comment=comment)

# Access is checked here, not inferred from a filter or a notification. There is
# one current row per stage/defense; old notifications never manufacture results.
RESULTS_SQL = """WITH visible AS (
    SELECT p.id, p.name, p.owner_id FROM projects p
    WHERE (p.owner_id=? OR EXISTS (SELECT 1 FROM project_members m WHERE m.project_id=p.id AND m.user_id=?))
    AND (?=0 OR p.id=?)
), results AS (
    SELECT p.id AS project_id, p.name AS project_name, 'stage' AS kind, s.number AS item_id,
        CASE WHEN s.state='done' THEN 'accepted' WHEN s.state='review' THEN 'review'
             WHEN s.state='progress' AND s.reviewed_by IS NOT NULL THEN 'revision' ELSE 'draft' END AS category,
        s.updated_at, s.teacher_comment AS comment, '' AS scores_json, u.username AS teacher_name
    FROM visible p JOIN project_stages s ON s.project_id=p.id LEFT JOIN users u ON u.id=s.reviewed_by
    UNION ALL
    SELECT p.id, p.name, 'defense', 0,
        CASE d.state WHEN 'accepted' THEN 'accepted' WHEN 'submitted' THEN 'review'
             WHEN 'returned' THEN 'revision' ELSE 'draft' END,
        d.updated_at, d.teacher_comment, '', u.username
    FROM visible p JOIN project_defenses d ON d.project_id=p.id LEFT JOIN users u ON u.id=d.reviewer_id
    UNION ALL
    SELECT p.id, p.name, 'grade', a.teacher_id, 'grade', a.updated_at, a.note, a.scores_json, u.username
    FROM visible p JOIN project_assessments a ON a.project_id=p.id JOIN users u ON u.id=a.teacher_id
    WHERE a.student_id=? AND a.state='published'
) """


def parameters(args, projects):
    """Invalid values fail closed for project scope; other controls normalize."""
    raw = args.get('result_project', '')
    if raw and (not raw.isascii() or not raw.isdigit() or len(raw) > 18 or int(raw) < 1):
        raise ValueError('Некорректный фильтр проекта. Сбросьте фильтры результатов.')
    project = int(raw) if raw else 0
    if project and project not in {p['id'] for p in projects}:
        raise PermissionError('Проект не найден или доступ к нему изменился.')
    state = args.get('result_state', 'all')
    if state not in STATES:
        state = 'all'
    raw_page = args.get('result_page', '1')
    page = int(raw_page) if raw_page.isascii() and raw_page.isdigit() and len(raw_page) <= 9 else 1
    return dict(project=project, state=state, page=max(1, page))


def load_results(conn, user, params):
    uid = user['id']
    scope = (uid, uid, params['project'], params['project'], uid)
    counts = {r['category']: r['amount'] for r in conn.execute(
        RESULTS_SQL + 'SELECT category, COUNT(*) AS amount FROM results GROUP BY category', scope)}
    total = sum(counts.values()) if params['state'] == 'all' else counts.get(params['state'], 0)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(params['page'], pages)
    # Dates are stored as TEXT and may contain legacy malformed values. Order by
    # stable IDs within state groups rather than pretending those are valid dates.
    items = [dict(r) for r in conn.execute(RESULTS_SQL + """SELECT * FROM results
        WHERE (?='all' OR category=?)
        ORDER BY CASE category WHEN 'revision' THEN 0 WHEN 'review' THEN 1
            WHEN 'draft' THEN 2 WHEN 'accepted' THEN 3 ELSE 4 END, project_id DESC, kind, item_id
        LIMIT ? OFFSET ?""", scope + (params['state'], params['state'], PAGE_SIZE, (page-1)*PAGE_SIZE))]
    ids = sorted({r['project_id'] for r in items})
    steps = {}
    if ids:
        placeholders = ','.join('?' for _ in ids)
        project_rows = conn.execute(f"""SELECT p.id, p.owner_id,
            EXISTS (SELECT 1 FROM users u WHERE u.role='teacher' AND (u.id=p.owner_id OR EXISTS
                (SELECT 1 FROM project_members m WHERE m.project_id=p.id AND m.user_id=u.id))) AS has_teacher,
            EXISTS (SELECT 1 FROM project_assessments a WHERE a.project_id=p.id AND a.student_id=? AND a.state='published') AS published
            FROM projects p WHERE p.id IN ({placeholders})""", (uid, *ids)).fetchall()
        stage_rows = defaultdict(list)
        # At most five stages per displayed project. Pass the real values to
        # next_step: SQL TRIM does not share Python's Unicode whitespace rules.
        for row in conn.execute(f"""SELECT project_id, number, state, reviewed_by, result, presentation_url
            FROM project_stages WHERE project_id IN ({placeholders})""", ids):
            stage_rows[row['project_id']].append(row)
        defenses = {r['project_id']: dict(r) for r in conn.execute(
            f'SELECT project_id,state FROM project_defenses WHERE project_id IN ({placeholders})', ids)}
        for project in project_rows:
            pid = project['id']
            steps[pid] = next_step(stages_from_rows(stage_rows[pid]), 'student', project['owner_id'] == uid,
                                   bool(project['has_teacher']), defenses.get(pid, {'state': 'draft'}), bool(project['published']))
    return dict(items=items, counts=counts, total=total, pages=pages, page=page, steps=steps)


def result_url(params, page):
    values = dict(result_page=page)
    if params.get('home_project'):
        values['project'] = params['home_project']
    if params['project']:
        values['result_project'] = params['project']
    if params['state'] != 'all':
        values['result_state'] = params['state']
    return '/?' + urlencode(values) + '#student-results'


def result_date(item):
    dt = sent_date(item['updated_at'])
    try:
        local = dt.astimezone(MOSCOW) if dt else None
    except OverflowError:
        local = None
    if not local:
        return 'Дата не указана'
    label = ('Отправлено' if item['category'] == 'review' else 'Решение учителя' if item['category'] == 'accepted'
             else 'Обновление опубликованной оценки' if item['kind'] == 'grade' else 'Последнее обновление')
    return f'<time datetime="{dt.isoformat()}">{label}: {local:%d.%m.%Y %H:%M} МСК</time>'


def render_results(result, params, projects):
    focus_field = f'<input type="hidden" name="project" value="{params["home_project"]}">' if params.get('home_project') else ''
    reset = f'/?project={params["home_project"]}#student-results' if params.get('home_project') else '/#student-results'
    options = '<option value="">Все проекты</option>' + ''.join(
        f'<option value="{p["id"]}" {"selected" if params["project"] == p["id"] else ""}>{escape(p["name"])}</option>' for p in projects)
    states = ''.join(f'<option value="{key}" {"selected" if params["state"] == key else ""}>{label}</option>' for key, label in STATES.items())
    counts = result['counts']
    html = f'''<section id="student-results" class="student-results" aria-labelledby="results-title">
        <div class="section-title"><div><span class="eyebrow">ОБРАТНАЯ СВЯЗЬ И СЛЕДУЮЩИЙ ШАГ</span>
        <h2 id="results-title">Центр результатов</h2><p>Сначала то, что нужно исправить. Только актуальные результаты и твои опубликованные оценки.</p></div></div>
        <p class="t-muted">Доработать: {counts.get('revision', 0)} · На проверке: {counts.get('review', 0)} · Принято: {counts.get('accepted', 0)} · Личных оценок: {counts.get('grade', 0)}</p>
        <form class="results-filters" method="get" action="/#student-results">
            {focus_field}
            <label>Проект<select name="result_project">{options}</select></label>
            <label>Состояние<select name="result_state">{states}</select></label>
            <button type="submit">Показать</button><a class="action-link" href="{reset}">Сбросить</a></form>
        <details class="results-help"><summary>О датах и состояниях</summary><p class="t-muted">Даты: Москва (UTC+3). У этапа после правок известна дата последнего обновления, а не отдельная дата прежней проверки. Отчёты находятся в задачах и не получают статус принятого этапа.</p></details>'''
    if not projects:
        html += f'<div class="results-empty"><h3>Пока нет проектов</h3><p>Создай проект — здесь появятся результаты и обратная связь учителя.</p><a class="action-link" href="#project-create" data-open-project>Создать первый проект {icon('arrow')}</a></div>'
    elif not result['total']:
        if params['state'] != 'all':
            html += '<div class="results-empty"><h3>Нет результатов по выбранным условиям</h3><p>Попробуй другое состояние или сбрось фильтры.</p></div>'
        else:
            html += '<div class="results-empty"><h3>Ещё ничего не отправлено</h3><p>Начни с темы проекта. Сводка проекта подскажет следующий шаг, а сохранённые черновики появятся здесь.</p>'
            available = [p for p in projects if not params['project'] or p['id'] == params['project']]
            html += ''.join(f'<a class="action-link" href="/project/{p["id"]}">Начать маршрут: {escape(p["name"])} {icon('arrow')}</a>' for p in available[:3])
            if len(available) > 3:
                html += '<p>Остальные проекты — в разделе «Мои проекты» ниже.</p>'
            html += '</div>'
    else:
        if not counts.get('revision') and not counts.get('review') and counts.get('accepted'):
            html += '<p class="results-confirmation">Все отправленные работы проверены. Это не завершает оставшиеся этапы и не публикует оценку автоматически.</p>'
        elif set(counts) <= {'draft'}:
            html += '<p class="t-muted">Ещё ничего не отправлено: сохранение черновика не отправляет работу учителю.</p>'
        html += f'<p class="t-muted">Найдено: {result["total"]}. Показано: {len(result["items"])}.</p><div class="results-list">'
        for item in result['items']:
            pid, category, kind = item['project_id'], item['category'], item['kind']
            base = f'/project/{pid}'
            title = f'Этап {item["item_id"]}: {dict(STAGES)[item["item_id"]]}' if kind == 'stage' else 'Видеозащита' if kind == 'defense' else 'Твоя личная оценка'
            href = f'{base}/stages#stage-{item["item_id"]}' if kind == 'stage' else f'{base}/defense' if kind == 'defense' else f'{base}/assessment'
            label = 'Опубликовано' if kind == 'grade' else 'Принято' if category == 'accepted' else STATES[category]
            comment = item['comment'] or ''
            feedback = ''
            if category == 'revision':
                feedback = '<strong>Что исправить</strong><p class="result-comment">' + escape(comment or 'Комментарий не указан. Уточни у учителя, что нужно исправить.') + '</p>'
            elif category == 'review':
                feedback = '<p>Новая сдача ожидает решения. Пока её нельзя редактировать.</p>'
                if comment:
                    feedback += '<details><summary>Комментарий к предыдущей сдаче — не решение по новой версии</summary><p class="result-comment">' + escape(comment) + '</p></details>'
            elif category == 'accepted':
                feedback = '<p class="result-comment">' + escape(comment or 'Принято без комментария учителя.') + '</p>'
            elif category == 'draft':
                feedback = '<p>Черновик ещё не отправлен учителю.</p>'
            else:
                try:
                    scores = load_scores(item)
                    if not isinstance(scores, dict):
                        raise ValueError('Invalid score structure')
                    total, grade = summarize(scores)
                    feedback = f'<p><strong>{total} / {MAX_SCORE} · оценка {grade if grade is not None else "не определена"}</strong></p>'
                except (ValueError, TypeError):
                    feedback = '<p>Оценка опубликована. Подробности в разделе оценивания.</p>'
                if comment:
                    feedback += '<p class="result-comment">' + escape(comment) + '</p>'
            teacher = f'<p class="t-muted">Учитель: {escape(item["teacher_name"])}</p>' if item['teacher_name'] and category != 'review' else ''
            step = result['steps'][pid]
            target = step['target']
            step_href = (base + target if target.startswith('#') else target if target.startswith('/') else f'{base}/{target}') if target else base
            step_link = (f'<a class="action-link" href="{step_href}">{step["action"] or "Открыть сводку проекта"} {icon('arrow')}</a>'
                         if step_href != href else '')
            action = 'Перейти к доработке' if category == 'revision' else 'Открыть результат' if kind != 'grade' else 'Посмотреть критерии и баллы'
            html += f'''<article class="result-item result-{category}" data-result-key="{kind}-{pid}-{item['item_id']}" data-result-state="{category}">
                <div class="result-heading"><span class="result-state">{label}</span><a href="{base}">{escape(item['project_name'])} {icon('arrow')}</a></div>
                <h3>{escape(title)}</h3><p class="t-muted">{result_date(item)}</p>{teacher}{feedback}
                <a class="action-link" href="{href}">{action} {icon('arrow')}</a>
                <div class="result-next"><span class="eyebrow">СЛЕДУЮЩИЙ ШАГ ПРОЕКТА</span><strong>{step['title']}</strong><p>{step['text']}</p>{step_link}</div></article>'''
        html += '</div>'
    if result['pages'] > 1:
        html += '<nav class="results-pages" aria-label="Страницы результатов">'
        if result['page'] > 1:
            html += f'<a class="action-link" href="{escape(result_url(params, result["page"]-1), quote=True)}">{icon('arrow-left')} Предыдущая</a>'
        html += f'<span>Страница {result["page"]} из {result["pages"]}</span>'
        if result['page'] < result['pages']:
            html += f'<a class="action-link" href="{escape(result_url(params, result["page"]+1), quote=True)}">Следующая {icon('arrow')}</a>'
        html += '</nav>'
    return html + '</section>'
