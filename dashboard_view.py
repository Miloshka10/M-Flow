"""Presentation only: no database, roles or request mutations."""
from html import escape
from deadlines import deadline_text, TIMEZONE_LABEL
from interface import icon
from design_suite import stage_track, compact_stage_route
from navigation import stage_result_target
from ui_components import state_badge


def student_dashboard(user, projects, token, header, messages, results='', focus=None, results_open=False, groups=(), project_states=None):
    project_states = project_states or {}
    active_groups = [group for group in groups if not group['archived']]
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
            <div class="ds-card-body">
            <div class="ds-card-title"><h3>{escape(p['name'])}</h3>{state_badge(state['label'], state['tone'])}{overdue}</div>
            <div class="ds-card-progress">{stage_track(state['stages_done'])}<span class="ds-stage-count" aria-hidden="true">Принято {state['stages_done']} из 5 этапов</span></div>
            <span class="ds-card-open" aria-hidden="true">{icon('arrow')}</span></div></a>
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
    group_cards = ''.join(f'<a class="ui-group-teaser" href="/groups/{g["id"]}"><h3>{escape(g["name"])}</h3><p>{escape(g["teacher_name"])}</p></a>' for g in list(groups)[:3])
    if not group_cards:
        group_cards = '<p class="t-muted">Присоединитесь к учебной группе по коду учителя.</p><a class="action-link" href="/groups#group-code">Ввести код группы</a>'
    group_widget = f'<details class="ds-home-groups v3-home-group ds-home-only" open><summary>Учебные группы</summary>{group_cards}<a class="action-link" href="/groups">Все группы {icon("arrow")}</a></details>'
    if focus:
        p, step = focus['project'], focus['step']
        state = project_states.get(p['id'], dict(label='В работе', tone='work', stages_done=0))
        base, target = f'/project/{p["id"]}', step['target']
        target = stage_result_target(target)
        if target == '/supervision':
            target = f'/supervision?project={p["id"]}'
        url = base + target if target and target.startswith('#') else target if target and target.startswith('/') else f'{base}/{target}' if target else base
        if step['key'] == 'teacher-missing':
            linked_group = next((group for group in active_groups if group.get('linked_project_id') == p['id']), None)
            if linked_group:
                url = f'/groups/{linked_group["id"]}'
                step = dict(step, text='Запрос руководителю и его статус доступны в учебной группе.', action='Открыть учебную группу')
        options = ''.join(f'<option value="{item["id"]}" {"selected" if item["id"] == p["id"] else ""}>{escape(item["name"])}</option>' for item in projects)
        switch = (f'<form class="focus-switch ds-home-only" method="get" action="/"><label for="current-project">Текущий проект</label>'
                  f'<select id="current-project" name="project">{options}</select><button class="secondary">Выбрать</button></form>') if len(projects) > 1 else ''
        feedback = (f'<aside class="focus-feedback ds-feedback"><h3>Замечание учителя</h3>'
                    f'{"<small>" + escape(focus.get("reviewer", "")) + "</small>" if focus.get("reviewer") else ""}'
                    f'<p>{escape(focus["comment"])}</p></aside>') if focus['comment'] else ''
        stages = focus.get('stages', [])
        route = compact_stage_route(stages, p['id']) if stages else f'<div class="v3-route-aside">{stage_track(state["stages_done"])}</div>'
        hero = f'''{switch}<section class="home-focus ds-feature ds-home-only" aria-labelledby="focus-name">
            <div class="ds-feature-main"><div class="v3-focus-heading"><span class="eyebrow">ТЕКУЩИЙ ПРОЕКТ</span>
            {state_badge(state['label'], state.get('tone', 'work'))}</div><h2 id="focus-name"><a href="{base}">{escape(p['name'])}</a></h2>
            <div class="home-next" data-home-step="{step['key']}"><div class="v3-next-title"><h3>{step['title']}</h3></div>
            <p class="work-next-copy">{step['text']}</p>
            {feedback}
            <div class="ui-actions work-next-actions"><a class="btn" href="{url}">{step['action'] or 'Открыть проект'} {icon('arrow')}</a>
            <a class="action-link" href="#my-projects">Все проекты</a></div></div></div>
            {route}</section>
            <div class="ds-home-columns ds-home-only"><section class="ui-agenda" aria-labelledby="upcoming-title">
            <h2 id="upcoming-title">Ближайшие задачи</h2><p class="work-section-copy">Ваши задачи и задачи без ответственного во всех проектах.</p>{upcoming_html}
            <details class="ds-guide"><summary>О сроках</summary><p>Твои задачи и задачи без ответственного · {TIMEZONE_LABEL}.
            Показано {min(6,len(upcoming))} из {len(upcoming)}.</p></details></section>{group_widget}</div>'''
    else:
        if active_groups:
            group_url = f'/groups/{active_groups[0]["id"]}' if len(active_groups) == 1 else '/groups'
            group_action = 'Продолжить в группе' if len(active_groups) == 1 else 'Выбрать группу'
            hint = f'Вы уже в группе «{escape(active_groups[0]["name"])}».' if len(active_groups) == 1 else 'Выберите, для какой группы создать проект.'
            independent = '<a class="action-link" href="#project-create" data-open-project>Создать проект вне группы</a>'
        else:
            group_url, group_action = '/groups#group-code', 'Ввести код группы'
            hint = 'Если учитель создал группу, введите её код. Для своей идеи можно сразу создать проект.'
            independent = '<a class="btn secondary" href="#project-create" data-open-project>Создать проект</a>'
        hero = f'''<section class="home-focus home-welcome ds-home-only"><div class="ui-hello-empty">
            <h2>Здесь начнётся ваш проект</h2><p>{hint}</p>
            <div class="welcome-actions"><a class="btn" href="{group_url}">{group_action}</a>
            {independent}</div>
            <a class="action-link" href="/teams#team-invitations">Приглашения в команды</a></div></section>'''
    projects_html = f'''<section class="workspace-projects" id="my-projects"><div class="section-title"><h2>Мои проекты</h2>
        <a class="action-link" href="#project-create" data-open-project>{icon('plus')} Новый проект</a></div>
        <nav class="project-people-links" aria-label="Участники и приглашения"><a class="action-link" href="/teams#people">Найти участников</a>
        <a class="action-link" href="/teams#team-invitations">Приглашения в команды</a></nav>
        <div class="ds-project-tools" hidden><label>Поиск проектов<input type="search" data-project-search placeholder="Найти проект…"></label>
        <label>Состояние<select data-project-filter><option value="all">Все</option><option value="work">В работе</option>
        <option value="review">На проверке</option><option value="revision">Нужна доработка</option><option value="accepted">Защита принята</option></select></label></div>
        <div class="projects-list project-grid">{''.join(cards)}</div><p data-project-empty hidden role="status">Проекты не найдены.</p>
        {create}</section>'''
    groups_html = group_widget if not focus and groups else ''
    secondary = f'<details class="home-details" {"open" if results_open else ""}><summary data-ui-icon="results">Мои результаты</summary>{results}</details>'
    return f'''<div class="container workspace student-workspace">{header}<main id="main-content" class="simple-home work-home" data-work-screens="home" data-suite-dashboard data-suite-view="{'results' if results_open else 'home'}">
        {messages}<div class="ui-page-heading"><h1 id="dashboard-title" tabindex="-1" data-home-title="Привет, {escape(user["username"], quote=True)}.">Привет, {escape(user["username"])}.</h1></div>
        {hero}{projects_html}{groups_html}{secondary}</main></div>'''
