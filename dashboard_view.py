"""Presentation only: no database, roles or request mutations."""
from html import escape
from deadlines import deadline_text, TIMEZONE_LABEL, business_today
from interface import project_art


def student_dashboard(user, projects, token, header, messages, results='', focus=None, results_open=False, groups=()):
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
        hero = f'''{switch}<div class="ui-home-grid"><section class="home-focus" aria-labelledby="focus-name">
            <div class="ui-project-cover"><span class="eyebrow">ТВОЙ ТЕКУЩИЙ ПРОЕКТ</span><h2 id="focus-name"><a href="{base}">{escape(p['name'])}</a></h2>
            <span class="ui-cover-meta">От идеи до своего результата</span>{project_art()}</div><div class="ui-focus-body">
            <div class="home-next" data-home-step="{step['key']}"><span class="eyebrow">СЛЕДУЮЩИЙ ШАГ</span>
            <h2>{step['title']}</h2><p>{step['text']}</p>{comment}<a class="btn" href="{url}">{step['action'] or 'Открыть проект'} →</a></div>
            <a class="focus-overview" href="{base}">Обзор проекта →</a></div></section>
            <section class="ui-agenda" aria-labelledby="upcoming-title"><h2 id="upcoming-title">На ближайшее время</h2>{upcoming_html}<p class="ui-agenda-foot">Твои задачи и задачи без ответственного · {TIMEZONE_LABEL}<br>Показано {min(6,len(upcoming))} из {len(upcoming)}.</p></section></div>'''
    else:
        hero = f'''<section class="home-focus home-welcome"><div class="ui-hello-empty">
            <span class="eyebrow">ТВОЯ ИДЕЯ. ТВОЙ ПРОЕКТ.</span><h2>Начни свой проект.</h2>
            <p>Есть код от учителя? Вступи в учебную группу. Готов начать работу? Создай свой проект.</p>
            <div class="welcome-actions"><a class="btn" href="/groups#group-code">Ввести код группы</a>
            <a class="action-link" href="#project-create" data-open-project>Создать проект</a></div>
            <a class="focus-overview" href="/teams">Найти команду →</a>{project_art()}</div></section>
            <div class="ui-welcome-steps"><div><span class="eyebrow">01 / НАЧАЛО</span><h3>Найди свою тему</h3><p>Сформулируй идею и согласуй её с руководителем.</p></div>
            <div><span class="eyebrow">02 / РАБОТА</span><h3>Пробуй и улучшай</h3><p>Распредели задачи в команде, сдавай этапы и получай обратную связь.</p></div>
            <div><span class="eyebrow">03 / РЕЗУЛЬТАТ</span><h3>Покажи, что получилось</h3><p>Подготовь документацию, презентацию и запись защиты.</p></div></div>'''
    secondary = f'''<details class="home-details" {"open" if results_open else ""}><summary>Результаты и обратная связь</summary>{results}</details>'''
    projects_html = f'''<section class="workspace-projects" id="my-projects"><div class="section-title"><div><h2>Мои проекты</h2>
        <p>Выбери проект, чтобы увидеть следующий шаг.</p></div><a class="action-link" href="/teams">Найти команду →</a></div>
        <div class="projects-list project-grid">{''.join(cards)}</div>{create}</section>''' if projects else f'<section class="workspace-projects">{create}</section>'
    group_cards = ''.join(f'''<a class="ui-group-teaser" href="/groups/{group['id']}"><span class="eyebrow">УЧЕБНАЯ ГРУППА</span><h3>{escape(group['name'])}</h3>
        <p>{escape(group['teacher_name'])}</p><span>{'В архиве' if group['archived'] else 'Открыть группу'} →</span></a>''' for group in list(groups)[:3])
    groups_html = f'<aside class="ui-home-groups"><div class="section-title"><h2>Учебные группы</h2></div>{group_cards}</aside>' if group_cards else '''<aside class="ui-home-groups"><div class="section-title"><h2>Учебные группы</h2></div><div class="ui-group-teaser"><span class="eyebrow">ВМЕСТЕ С КЛАССОМ</span><h3>Есть код от учителя?</h3><p>Присоединись к группе и выбери свой проект.</p><a class="action-link" href="/groups#group-code">Ввести код группы →</a></div></aside>'''
    lower = f'<div class="ui-home-lower">{projects_html}{groups_html}</div>' if projects else projects_html
    today = business_today()
    months = ('января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря')
    heading = f'<div class="ui-page-heading"><div><h1>Привет, {escape(user["username"])}.</h1><p>Сегодня — ещё один шаг к результату.</p></div><div class="ui-date"><strong>{today.day}</strong>{months[today.month-1]}</div></div>'
    return f'''<div class="container workspace student-workspace">{header}<main id="main-content" class="simple-home">
        {messages}{heading}{hero}{lower}{secondary}
        <footer class="workspace-footer"><span>M-Flow <small>by Minich</small></span></footer></main></div>'''
