"""Presentation only: no database, roles or request mutations."""
from html import escape

from group_assignments import assignment_url
from deadlines import parse_deadline
from datetime import timedelta
from deadlines import business_today, deadline_text
from interface import icon
from design_suite import stage_track
from grading import MAX_SCORE, load_scores, summarize
from project_stages import stage_needs_revision
from project_work import SHORT_NAMES
from navigation import stage_result_target


def home_project_url(project_id):
    return f'/?project={project_id}#home-project'


def render_due_tasks(rows):
    visible = ''.join(
        f'''<li><a href="/project/{project['id']}#task-{task['id']}"><span><strong>{escape(task['title'])}</strong>
        <small>{escape(project['name'])}</small></span><span class="due-label {task['deadline_info']['css']}">
        {escape(deadline_text(task['deadline_info']))}</span></a></li>''' for project, task in rows[:6])
    count = f'<p class="student-home-note">Показано 6 из {len(rows)} задач.</p>' if len(rows) > 6 else ''
    return f'<ul class="upcoming-list">{visible}</ul>{count}'


def render_week(projects, today):
    days = [[] for _ in range(7)]
    overdue, later = [], []
    upcoming = sorted(((project, task) for project in projects for task in project.get('deadlines', [])),
                      key=lambda pair: (pair[1]['deadline_info']['date'], pair[1]['id']))
    for pair in upcoming:
        offset = pair[1]['deadline_info']['days']
        if offset < 0:
            overdue.append(pair)
        elif offset < len(days):
            days[offset].append(pair)
        else:
            later.append(pair)
    weekdays = ('Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс')
    selected = next((offset for offset, rows in enumerate(days) if rows), 0)
    week = []
    for offset, rows in enumerate(days):
        date = today + timedelta(days=offset)
        weekday = weekdays[date.weekday()]
        target = f'student-week-{offset}'
        count = len(rows)
        label = f'{weekday}, {date:%d.%m} · задач: {count}' + (' · сегодня' if not offset else '')
        tasks = render_due_tasks(rows) if rows else '<p class="student-home-note">Нет задач со сроком на этот день.</p>' if upcoming else ''
        week.append(f'''<input class="student-week-choice" type="radio" id="{target}" name="home-week-day"
            value="{date:%Y-%m-%d}" aria-label="{label}" aria-controls="{target}-tasks"
            {'checked' if offset == selected else ''}>
            <label for="{target}" class="{'student-week-today' if not offset else ''}">
                <span>{weekday}</span><strong>{date.day}</strong>{f'<small>{count}</small>' if count else ''}
            </label>
            <div class="student-week-panel" id="{target}-tasks">{f'<h4>{weekday}, {date:%d.%m}</h4>{tasks}' if tasks else ''}</div>''')
    late = f'<div class="student-week-overdue"><h4>Просрочено</h4>{render_due_tasks(overdue)}</div>' if overdue else ''
    future = f'<details class="student-week-later"><summary>После этой недели · {len(later)}</summary>{render_due_tasks(later)}</details>' if later else ''
    empty = '<p class="student-home-note">Нет незавершённых задач со сроками.</p>' if not upcoming else ''
    return f'''<section aria-labelledby="upcoming-title"><h3 id="upcoming-title">Ближайшие 7 дней</h3>
        {late}{empty}<div class="student-week" role="group" aria-label="Дни недели">{''.join(week)}</div>
        {future}</section>'''


def render_project_finish(focus, base):
    scores = []
    for assessment in focus['assessments']:
        try:
            total, grade = summarize(load_scores(assessment))
        except (ValueError, TypeError, AttributeError):
            continue
        if grade is not None:
            scores.append(f'<li><strong>{total} / {MAX_SCORE}</strong><span>{escape(assessment["teacher_name"])}</span></li>')
    score_list = f'<ul class="student-finish-scores" aria-label="Опубликованные баллы">{"".join(scores)}</ul>' if scores else ''
    return f'''<section class="home-focus student-home-focus student-finish ds-home-only" id="home-project"
        tabindex="-1" aria-labelledby="focus-name" data-home-step="completed">
        <div class="student-finish-cover">
            <span class="student-finish-mark" aria-hidden="true">{icon('check')}</span>
            <p class="student-finish-state">Проект завершён</p>
            <h2 id="focus-name">{escape(focus['project']['name'])}</h2>
            {score_list}
        </div>
        <nav aria-label="Итоги проекта" class="student-finish-links">
            <a class="btn" href="{base}/assessment">Посмотреть свои оценки {icon('arrow')}</a>
            <a class="action-link" href="{base}/stages#stage-5-result">Итоговая работа</a>
            <a class="action-link" href="{base}/defense">Видеозащита</a>
        </nav>
        {render_project_path(focus['stages'], None)}
        </section>'''


def render_project_path(stages, current):
    steps = []
    for stage, name in zip(stages, SHORT_NAMES):
        active = current is not None and stage['number'] == current['number']
        if stage['state'] == 'done':
            tone, label = 'accepted', 'Принят'
        elif not active:
            tone, label = 'locked', 'Закрыт'
        elif stage_needs_revision(stage):
            tone, label = 'revision', 'Вернули'
        elif stage['state'] == 'review':
            tone, label = 'review', 'На проверке'
        else:
            tone, label = 'work', 'В работе' if stage['state'] == 'progress' else 'Можно начать'
        marker = icon('check') if tone == 'accepted' else stage['number']
        aria = ' aria-current="step"' if active else ''
        steps.append(f'''<li class="student-path-step {tone}"{aria}>
            <span class="student-path-marker" aria-hidden="true">{marker}</span>
            <strong>{escape(name)}</strong><small>{label}</small></li>''')
    return f'''<section class="student-home-path" aria-labelledby="student-path-title">
        <h3 id="student-path-title">Путь проекта</h3><ol>{''.join(steps)}</ol></section>'''


def render_home_focus(focus):
    project, step, current = focus['project'], focus['step'], focus['current']
    key = step['key']
    base, url = f'/project/{project["id"]}', step['target']
    if key == 'assessment' and focus['assessments']:
        return render_project_finish(focus, base)
    url = stage_result_target(url)
    if url == '/supervision':
        url = f'/supervision?project={project["id"]}'
    if url and not url.startswith('/'):
        url = base + url if url.startswith('#') else f'{base}/{url}'
    title, action = step['title'], step['action']
    note = ''
    secondary_url, secondary_label = base, 'Обзор проекта'
    if key == 'teacher-missing':
        note = ('Прими приглашение учителя, чтобы отправить результат.' if action == 'Принять приглашение'
                else 'Попроси владельца проекта принять приглашение учителя.')
    elif key == 'revise':
        title, action = 'Нужна доработка', 'Доработать результат'
    elif key == 'ready':
        title, action = 'Черновик готов', 'Отправить учителю'
    elif key == 'await-review':
        title, action = 'На проверке', None
        note = 'Учитель примет этап или вернёт его с замечанием.'
        secondary_url = f'{base}/stages#stage-{current["number"]}-result'
        secondary_label = 'Открыть отправленный результат'
    elif key == 'defense-wait':
        title, action = 'Видеозащита на проверке', None
        note = 'Дождись решения учителя по защите.'
        secondary_url, secondary_label = f'{base}/defense', 'Посмотреть видеозащиту'
    elif key == 'assessment':
        title = 'Видеозащита принята'
        action = None
        note = 'Опубликованной оценки пока нет.'

    status_tone = ('review' if key in ('await-review', 'defense-wait') else 'revision' if focus['comment']
                   else 'accepted' if key == 'assessment' else '')
    reviewer = ' · ' + escape(focus['reviewer']) if focus['reviewer'] else ''
    feedback = (f'<div class="student-home-feedback"><strong>Замечание учителя{reviewer}</strong>'
                f'<p>{escape(focus["comment"])}</p></div>' if focus['comment'] else '')
    primary = f'<a class="btn" href="{escape(url, quote=True)}">{escape(action)} {icon("arrow")}</a>' if action and url else ''
    secondary = f'<a class="action-link" href="{secondary_url}">{secondary_label} {icon("arrow")}</a>'
    stage_context = f'<span class="student-home-stage">Этап {current["number"]} из {len(focus["stages"])}</span>' if current else ''
    return f'''<section class="home-focus student-home-focus ds-home-only" id="home-project" tabindex="-1" aria-labelledby="focus-name">
        <div class="student-home-current student-home-brief" data-home-step="{key}">
            <div class="student-home-identity">
                <h2 id="focus-name">{escape(project['name'])}</h2>
                {stage_context}<h3 class="student-home-status {status_tone}">{escape(title)}</h3>
            </div>
            <div class="student-home-next">
                {f'<p class="student-home-note">{escape(note)}</p>' if note else ''}
                {feedback}<div class="student-home-actions">{primary}{secondary}</div>
            </div>
        </div>
        {render_project_path(focus['stages'], current)}
        </section>'''


def render_home_assignments(assignments):
    if not assignments:
        return ''
    cards = []
    for item in assignments:
        due = parse_deadline(item['due_on'])
        date = f'<time datetime="{due.isoformat()}">{due:%d.%m.%Y}</time>' if due else ''
        label = 'Проект связан' if item['project_id'] else 'Нужно начать'
        cards.append(f'<li><a href="{assignment_url(item)}"><strong>{escape(item["title"])}</strong>'
                     f'<span>{escape(item["group_name"])} · {label}</span>{date}</a></li>')
    return '<section class="home-assignments ds-home-only" aria-labelledby="home-assignments-title"><h2 id="home-assignments-title">Задания групп</h2><ul>' + ''.join(cards) + '</ul></section>'


def student_dashboard(projects, token, header, messages, results='', focus=None, results_open=False, groups=(), project_states=None, today=None, assignments=()):
    project_states = project_states or {}
    selected_id = focus['project']['id'] if focus else None
    cards = []
    for p in projects:
        percent = round(p['done'] / p['total'] * 100) if p['total'] else 0
        state = project_states.get(p['id'], dict(label='В работе', tone='work', stages_done=0))
        selected = ' aria-current="true"' if p['id'] == selected_id else ''
        details = f'{p["done"]} из {p["total"]} задач выполнено · {percent}%' if p['total'] else 'Задачи ещё не добавлены'
        overdue = f' <span class="tile-alert">· Просрочено: {p["overdue"]}</span>' if p['overdue'] else ''
        cards.append(f'''<article class="ds-project-card" data-project-name="{escape(p['name'], quote=True)}" data-project-tone="{state['tone']}">
            <a class="project-row project-tile" href="{home_project_url(p['id'])}" data-project-url="/project/{p['id']}"{selected}>
                <div class="ds-project-identity"><span class="tile-folder">{icon('folder')}</span>
                    <div><h3>{escape(p['name'])}</h3><span class="ds-project-meta">{details}{overdue}</span></div>
                </div>
                <span class="ui-state {state['tone']}">{escape(state['label'])}</span>
                <div class="ds-row-progress">{stage_track(state['stages_done'])}<span class="ds-stage-count">Принято {state['stages_done']} из 5 этапов</span></div>
                <span class="ds-card-open" aria-hidden="true">{icon('arrow')}</span>
            </a></article>''')
    create = f'''<details id="project-create" class="project-create"><summary>{icon('plus')} Новый проект</summary>
        <form id="project-form" class="new-project show" action="/add_project" method="post">
        <input type="hidden" name="csrf_token" value="{escape(token, quote=True)}"><label for="project-name">Название проекта</label>
        <input id="project-name" name="name" placeholder="Название проекта" maxlength="80" required>
        <button type="submit" data-ui-icon="plus">Создать</button></form></details>'''
    agenda = ''
    if focus:
        hero = render_home_focus(focus)
        other_projects = []
        for tone in ('revision', 'review'):
            for p in projects:
                state = project_states.get(p['id'], {})
                if p['id'] != selected_id and state.get('tone') == tone:
                    other_projects.append(f'''<li><a href="{home_project_url(p['id'])}"><strong>{escape(p['name'])}</strong>
                        <span class="ui-state {tone}">{escape(state['label'])}</span></a></li>''')
        reviews = (f'<ul class="upcoming-list student-home-reviews">{"".join(other_projects)}</ul>'
                   if other_projects else '')
        agenda = f'''<section class="ui-agenda student-home-agenda ds-home-only" aria-labelledby="attention-title">
            <h2 id="attention-title">Состояние и сроки</h2>{reviews}
            {render_week(projects, today or business_today())}</section>'''
    else:
        hero = '''<section class="home-focus student-home-focus ds-home-only" aria-labelledby="empty-home-title">
            <div class="student-home-current"><h2 id="empty-home-title">У тебя пока нет проектов</h2>
            <div class="student-home-actions"><a class="btn" href="#project-create" data-open-project>Создать проект</a>
            <a class="action-link" href="/groups#group-code">Ввести код группы</a></div></div></section>'''
    projects_html = f'''<section class="workspace-projects" id="my-projects" aria-label="Мои проекты"><div class="ds-project-head"><h2>Мои проекты</h2>{create}</div>
        <nav class="project-people-links" aria-label="Участники и приглашения">
        <a class="action-link" href="/teams#people">Найти участников</a>
        <a class="action-link" href="/teams#team-invitations">Приглашения в команды</a></nav>
        <div class="ds-project-tools" hidden><label>Поиск проектов<input type="search" data-project-search placeholder="Найти проект…"></label>
        <label>Состояние<select data-project-filter><option value="all">Все</option><option value="work">В работе</option>
        <option value="review">На проверке</option><option value="revision">Нужна доработка</option><option value="accepted">Защита принята</option></select></label></div>
        <div class="ds-project-columns" aria-hidden="true"><span>Проект</span><span>Состояние</span><span>Этапы</span></div>
        <div class="projects-list project-grid">{''.join(cards)}</div><p data-project-empty hidden role="status">Проекты не найдены.</p>
        </section>'''
    group_cards = ''.join(f'<a class="ui-group-teaser" href="/groups/{g["id"]}"><h3>{escape(g["name"])}</h3><p>{escape(g["teacher_name"])}</p></a>' for g in list(groups)[:3])
    groups_html = (f'<details class="ds-home-groups ds-home-only"><summary>Учебные группы</summary>{group_cards}</details>'
                   if group_cards else '')
    secondary = f'<details class="home-details" {"open" if results_open else ""}><summary data-ui-icon="results">Мои результаты</summary>{results}</details>'
    return f'''<div class="container workspace student-workspace">{header}<main id="main-content" class="simple-home" data-suite-dashboard data-suite-view="{'results' if results_open else 'home'}">
        <h1 id="dashboard-title" tabindex="-1" data-home-title="Главная">{'Мои результаты' if results_open else 'Главная'}</h1>{messages}
        {render_home_assignments(assignments)}{hero}{agenda}{projects_html}{groups_html}{secondary}</main></div>'''
