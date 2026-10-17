"""Presentation only: no database, roles or request mutations."""
from html import escape
from deadlines import deadline_text, TIMEZONE_LABEL
from graphics import illustration


def student_dashboard(user, projects, token, header, messages, results='', focus=None, results_open=False):
    cards = []
    for p in projects:
        percent = round(p['done'] / p['total'] * 100) if p['total'] else 0
        caption = 'Пока нет задач' if not p['total'] else 'Все задачи выполнены' if p['done'] == p['total'] else 'Работа продолжается'
        deadline = f'<span class="tile-alert">Просрочено: {p["overdue"]}</span>' if p['overdue'] else ''
        cards.append(f'''<a class="project-row project-tile" href="/?project={p['id']}">
            <div class="tile-top"><span class="tile-folder" aria-hidden="true">↗</span><span>{caption}</span>{deadline}</div>
            <h3>{escape(p['name'])}</h3><div class="tile-bottom"><span>{p['done']} из {p['total']} задач выполнено</span><strong>{percent}%</strong></div>
            <div class="mini-progress"><div style="width:{percent}%"></div></div></a>''')
    upcoming = sorted([(p, task) for p in projects for task in p.get('deadlines', [])],
                      key=lambda pair: (pair[1]['deadline_info']['date'], pair[1]['id']))
    upcoming_rows = ''.join(
        f'''<li><a href="/project/{p['id']}#task-{task['id']}"><span><strong>{escape(task['title'])}</strong>
            <small>{escape(p['name'])}</small></span><span class="due-label {task['deadline_info']['css']}">
            {escape(deadline_text(task['deadline_info']))}</span></a></li>'''
        for p, task in upcoming[:6])
    upcoming_html = (f'<ul class="upcoming-list">{upcoming_rows}</ul>' if upcoming else
                     '<p class="t-muted">Нет незавершённых задач со сроками.</p>')
    create = f'''<details id="project-create" class="project-create"><summary>+ Новый проект</summary>
        <form id="project-form" class="new-project show" action="/add_project" method="post">
        <input type="hidden" name="csrf_token" value="{escape(token, quote=True)}"><label for="project-name">Название проекта</label>
        <input id="project-name" name="name" placeholder="Название проекта" maxlength="80" required><button type="submit">Создать</button></form></details>'''
    if focus:
        p, step = focus['project'], focus['step']
        base = f'/project/{p["id"]}'
        target = step['target']
        url = base + target if target and target.startswith('#') else target if target and target.startswith('/') else f'{base}/{target}' if target else base
        options = ''.join(f'<option value="{item["id"]}" {"selected" if item["id"] == p["id"] else ""}>{escape(item["name"])}</option>' for item in projects)
        switch = f'''<form class="focus-switch" method="get" action="/"><label for="current-project">Текущий проект</label>
            <select id="current-project" name="project">{options}</select><button class="secondary">Выбрать</button></form>''' if len(projects) > 1 else '<span class="eyebrow">ТВОЙ ПРОЕКТ</span>'
        comment = f'<aside class="focus-feedback"><strong>Замечание учителя</strong><p tabindex="0">{escape(focus["comment"])}</p></aside>' if focus['comment'] else ''
        hero = f'''<section class="home-focus" aria-labelledby="focus-name">{switch}
            <div class="home-focus-layout"><div><h1 id="focus-name"><a href="{base}">{escape(p['name'])}</a></h1>
            <div class="home-next" data-home-step="{step['key']}"><span class="eyebrow">СЛЕДУЮЩИЙ ШАГ</span>
            <h2>{step['title']}</h2><p>{step['text']}</p>{comment}<a class="btn" href="{url}">{step['action'] or 'Открыть проект'} →</a></div>
            <a class="focus-overview" href="{base}">Обзор проекта →</a></div>{illustration('student')}</div></section>'''
    else:
        hero = f'''<section class="home-focus home-welcome"><div class="home-focus-layout"><div>
            <span class="eyebrow">ПРИВЕТ, {escape(user['username'])}</span><h1>Начни свой проект.</h1>
            <p>Есть код от учителя? Вступи в учебную группу. Готов начать работу? Создай свой проект.</p>
            <div class="welcome-actions"><a class="btn" href="/groups#group-code">Ввести код группы</a>
            <a class="action-link" href="#project-create" data-open-project>Создать проект</a></div>
            <a class="focus-overview" href="/teams">Найти команду →</a></div>{illustration('student')}</div></section>'''
    secondary = f'''<details class="home-details" {"open" if results_open else ""}><summary>Результаты и обратная связь</summary>{results}</details>
        <details class="home-details"><summary>Ближайшие дела · {len(upcoming)}</summary><section class="upcoming-panel" aria-labelledby="upcoming-title">
        <h2 id="upcoming-title">Ближайшие дела</h2><p class="t-muted">Показано {min(6, len(upcoming))} из {len(upcoming)}</p><p>Твои задачи и задачи команды без ответственного · {TIMEZONE_LABEL}</p>{upcoming_html}</section></details>''' if projects else ''
    projects_html = f'''<section class="workspace-projects" id="my-projects"><div class="section-title"><div><h2>Мои проекты</h2>
        <p>Выбери проект, чтобы увидеть следующий шаг.</p></div><a class="action-link" href="/teams">Найти команду →</a></div>
        <div class="projects-list project-grid">{''.join(cards)}</div>{create}</section>''' if projects else f'<section class="workspace-projects">{create}</section>'
    return f'''<div class="container workspace student-workspace">{header}<main id="main-content" class="simple-home">
        {messages}{hero}{projects_html}{secondary}
        <footer class="workspace-footer"><span>M-Flow <small>by Minich</small></span></footer></main></div>'''
