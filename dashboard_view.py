"""Presentation only: no database, roles or request mutations."""
from html import escape
from deadlines import deadline_text, TIMEZONE_LABEL
from interface import icon
from design_suite import project_cover, stage_track


def student_dashboard(user, projects, token, header, messages, results='', focus=None, results_open=False, groups=(), project_states=None):
    project_states = project_states or {}
    selected_id = focus['project']['id'] if focus else None
    cards = []
    for p in projects:
        percent = round(p['done'] / p['total'] * 100) if p['total'] else 0
        state = project_states.get(p['id'], dict(label='В работе', tone='work', stages_done=0))
        selected = ' aria-current="true"' if p['id'] == selected_id else ''
        details = f'{p["done"]} из {p["total"]} задач выполнено · {percent}%' if p['total'] else 'Задачи ещё не добавлены'
        overdue = f'<span class="tile-alert">Просрочено: {p["overdue"]}</span>' if p['overdue'] else ''
        cards.append(f'''<article class="ds-project-card" data-project-name="{escape(p['name'], quote=True)}" data-project-tone="{state['tone']}">
            <a class="project-row project-tile" href="/project/{p['id']}"{selected}>
            {project_cover(p['name'], p['id'])}<div class="ds-card-body">
            <span class="ui-state {state['tone']}">{escape(state['label'])}</span><h3>{escape(p['name'])}</h3>
            {stage_track(state['stages_done'])}<span class="ds-stage-count">Принято {state['stages_done']} из 5 этапов</span>{overdue}
            <span class="ds-card-open">Открыть проект {icon('arrow')}</span></div></a>
            <details class="ds-card-details"><summary>Задачи</summary><p>{details}</p></details></article>''')
    upcoming = sorted([(p, task) for p in projects for task in p.get('deadlines', [])],
                      key=lambda pair: (pair[1]['deadline_info']['date'], pair[1]['id']))
    upcoming_rows = ''.join(
        f'''<li><a href="/project/{p['id']}#task-{task['id']}"><span><strong>{escape(task['title'])}</strong>
        <small>{escape(p['name'])}</small></span><span class="due-label {task['deadline_info']['css']}">
        {escape(deadline_text(task['deadline_info']))}</span></a></li>''' for p, task in upcoming[:6])
    upcoming_html = f'<ul class="upcoming-list">{upcoming_rows}</ul>' if upcoming else '<p class="t-muted">Нет незавершённых задач со сроками.</p>'
    create = f'''<details id="project-create" class="project-create"><summary>{icon('plus')} Новый проект</summary>
        <form id="project-form" class="new-project show" action="/add_project" method="post">
        <input type="hidden" name="csrf_token" value="{escape(token, quote=True)}"><label for="project-name">Название проекта</label>
        <input id="project-name" name="name" placeholder="Название проекта" maxlength="80" required>
        <button type="submit" data-ui-icon="plus">Создать</button></form></details>'''
    if focus:
        p, step = focus['project'], focus['step']
        state = project_states.get(p['id'], dict(label='В работе', stages_done=0))
        base, target = f'/project/{p["id"]}', step['target']
        url = base + target if target and target.startswith('#') else target if target and target.startswith('/') else f'{base}/{target}' if target else base
        options = ''.join(f'<option value="{item["id"]}" {"selected" if item["id"] == p["id"] else ""}>{escape(item["name"])}</option>' for item in projects)
        switch = (f'<form class="focus-switch ds-home-only" method="get" action="/"><label for="current-project">Текущий проект</label>'
                  f'<select id="current-project" name="project">{options}</select><button class="secondary">Выбрать</button></form>') if len(projects) > 1 else ''
        feedback = (f'<aside class="focus-feedback ds-feedback"><h2>Замечание учителя</h2><p>{escape(focus["comment"])}</p>'
                    f'<a class="action-link" href="{url}">Открыть замечание {icon("arrow")}</a></aside>') if focus['comment'] else ''
        hero = f'''{switch}<section class="home-focus ds-feature ds-home-only" aria-labelledby="focus-name">
            <div class="ds-feature-main"><span class="eyebrow">ТЕКУЩИЙ ПРОЕКТ</span><h2 id="focus-name">{escape(p['name'])}</h2>
            <div class="home-next" data-home-step="{step['key']}"><h3>{step['title']}</h3>
            <details class="ds-guide"><summary>Что делать дальше</summary><p>{step['text']}</p></details>
            <a class="btn" href="{url}">{step['action'] or 'Открыть проект'} {icon('arrow')}</a>
            <a class="action-link" href="#my-projects">Все проекты</a></div></div>
            <div class="ds-feature-art">{project_cover(p['name'], p['id'])}{stage_track(state['stages_done'])}
            <span>Принято {state['stages_done']} из 5 этапов</span></div></section>
            <div class="ds-home-columns ds-home-only"><section class="ui-agenda" aria-labelledby="upcoming-title">
            <h2 id="upcoming-title">Нужен твой шаг</h2>{upcoming_html}
            <details class="ds-guide"><summary>О сроках</summary><p>Твои задачи и задачи без ответственного · {TIMEZONE_LABEL}.
            Показано {min(6,len(upcoming))} из {len(upcoming)}.</p></details></section>{feedback}</div>'''
    else:
        hero = '''<section class="home-focus home-welcome ds-home-only"><div class="ui-hello-empty">
            <h2>Начни свой проект.</h2><div class="welcome-actions"><a class="btn" href="#project-create" data-open-project>Создать проект</a>
            <a class="action-link" href="/groups#group-code">Ввести код группы</a></div></div></section>'''
    projects_html = f'''<section class="workspace-projects" id="my-projects"><div class="section-title"><h2>Мои проекты</h2>
        <a class="action-link" href="#project-create" data-open-project>{icon('plus')} Новый проект</a></div>
        <div class="ds-project-tools" hidden><label>Поиск проектов<input type="search" data-project-search placeholder="Найти проект…"></label>
        <label>Состояние<select data-project-filter><option value="all">Все</option><option value="work">В работе</option>
        <option value="review">На проверке</option><option value="revision">Нужна доработка</option><option value="accepted">Защита принята</option></select></label></div>
        <div class="projects-list project-grid">{''.join(cards)}</div><p data-project-empty hidden role="status">Проекты не найдены.</p>
        {create}</section>'''
    group_cards = ''.join(f'<a class="ui-group-teaser" href="/groups/{g["id"]}"><h3>{escape(g["name"])}</h3><p>{escape(g["teacher_name"])}</p></a>' for g in list(groups)[:3])
    if not group_cards:
        group_cards = '<a href="/groups#group-code">Ввести код группы</a>'
    groups_html = f'<details class="ds-home-groups ds-home-only"><summary>Учебные группы</summary>{group_cards}</details>'
    secondary = f'<details class="home-details" {"open" if results_open else ""}><summary data-ui-icon="results">Мои результаты</summary>{results}</details>'
    return f'''<div class="container workspace student-workspace">{header}<main id="main-content" class="simple-home" data-suite-dashboard data-suite-view="{'results' if results_open else 'home'}">
        {messages}<div class="ui-page-heading ds-home-only"><h1>Привет, {escape(user["username"])}.</h1></div>
        {hero}{projects_html}{groups_html}{secondary}</main></div>'''
