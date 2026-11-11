"""Incremental Linear-style presentation. Reads only; existing forms own all writes."""
from contextlib import closing
from html import escape
import json

from flask import request
from interface import APPEARANCE_VERSION, appearance_assets, icon, theme_button
from deadlines import format_timestamp
from material_links import safe_web_url
from project_stages import get_stages, stages_from_rows, stage_summary, stage_needs_revision
from work_history import load_history, EVENTS

NAMES = ('Тема', 'Работа 1', 'Работа 2', 'Работа 3', 'Документы')


def stage_model(stages):
    _, current = stage_summary(stages)
    return [dict(name=NAMES[i], status='locked' if current and s['number'] > current['number'] else
                 'returned' if stage_needs_revision(s) else 'progress' if s['state'] == 'todo' else s['state'])
            for i, s in enumerate(stages)]


def status_name(status):
    return {'locked': 'Пока закрыт', 'done': 'Этап принят', 'review': 'На проверке',
            'returned': 'На доработке', 'progress': 'В работе'}[status]


def document(title, body):
    return (f'<!doctype html><html lang="ru" data-theme="dark"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{escape(title)} — M-Flow</title>{appearance_assets()}'
            f'<link rel="stylesheet" href="/static/linear-ui.css?v={APPEARANCE_VERSION}">'
            f'<script type="module" src="/static/linear-ui.js?v={APPEARANCE_VERSION}"></script></head>'
            f'<body data-linear-view><a class="skip-link" href="#linear-main">Перейти к содержанию</a>{body}</body></html>')


def sidebar(user, projects, token, pid=None, stages=None, selected=0):
    links = ''.join(f'<a class="lx-nav-row" href="/next/project/{p["id"]}" '
                    f'data-active="{str(p["id"] == pid).lower()}">{icon("folder")}<span>{escape(p["name"])}</span></a>' for p in projects)
    steps = ''
    if stages:
        steps = '<div class="lx-nav-group"><span class="lx-nav-label">Этапы проекта</span>' + ''.join(
            f'<a class="lx-nav-row" href="?stage={i+1}" data-stage-link="{i}" aria-label="Этап {i+1}: {s["name"]}" '
            f'aria-current="{"step" if i == selected else "false"}" {"aria-disabled=true tabindex=-1" if s["status"] == "locked" else ""}>'
            f'{icon("check" if s["status"] == "done" else "stages")}<span>{s["name"]}</span><small>{i+1}</small></a>'
            for i, s in enumerate(stages)) + '</div>'
    home = '/teacher' if user['role'] == 'teacher' else '/'
    content = (f'<div class="lx-sidebar-top"><a class="lx-workspace-switch" href="/next"><span class="lx-workspace-mark">M</span>M-Flow</a></div>'
               '<div class="lx-sidebar-scroll"><nav class="lx-nav-rows lx-nav-primary" aria-label="Разделы">'
               f'<a class="lx-nav-row" href="/next">{icon("folder")}<span>Проекты</span></a>'
               f'<a class="lx-nav-row" href="{"/teacher#review-queue" if user["role"] == "teacher" else "/teams"}">{icon("review" if user["role"] == "teacher" else "team")}<span>{"Все проверки" if user["role"] == "teacher" else "Команды"}</span></a>'
               f'<a class="lx-nav-row" href="/groups">{icon("groups")}<span>Учебные группы</span></a></nav>{steps}'
               f'<div class="lx-nav-group"><span class="lx-nav-label">Мои проекты</span>{links or "<p class=lw-muted>Проектов пока нет</p>"}</div></div>'
               f'<div class="lx-sidebar-bottom"><a class="lx-nav-row" href="{home}">{icon("home")} Рабочий стол</a>'
               f'<a class="lx-sidebar-account" href="/profile"><span class="lx-avatar">{escape(user["username"][:2].upper())}</span><span>{escape(user["username"])}</span></a>'
               f'<form action="/logout" method="post"><input type="hidden" name="csrf_token" value="{escape(token, quote=True)}"><button class="lx-nav-row" type="submit">Выйти</button></form></div>')
    return (f'<aside class="lx-sidebar" aria-label="Навигация">{content}</aside>'
            '<button class="ln-menu-button lx-icon-button" data-open-nav aria-label="Открыть навигацию">' + icon('menu') + '</button>'
            f'<dialog class="ln-nav-dialog" aria-label="Навигация"><button class="ln-close lx-icon-button" data-close-nav aria-label="Закрыть навигацию">{icon("close")}</button>{content}</dialog>')


def orbit(stages, selected, pid):
    accepted = next((i for i, s in enumerate(stages) if s['status'] != 'done'), len(stages))
    data = escape(json.dumps(stages, ensure_ascii=False), quote=True)
    nodes = ''.join(f'<button type="button" class="po-node-hit" data-node="{i}" hidden aria-label="Узел этапа «{s["name"]}»"></button>' for i, s in enumerate(stages))
    steps = ''.join(f'<button type="button" data-stage-link="{i}" {"disabled" if s["status"] == "locked" else ""} aria-current="{"step" if i == selected else "false"}"><span class="po-step-mark">{icon("check") if s["status"] == "done" else str(i+1).zfill(2)}</span><span>{s["name"]}</span></button>' for i, s in enumerate(stages))
    return (f'<figure class="po-figure" data-orbit data-stages="{data}" data-selected="{selected}" data-seed="{pid}">'
            '<div class="po-topline"><span>Структура проекта</span><div>'
            f'<button class="po-motion" data-reset-orbit aria-label="Сбросить поворот структуры" disabled>{icon("return")}</button>'
            '<button class="po-motion" data-pause-orbit aria-label="Приостановить движение структуры" aria-pressed="false">Пауза</button></div></div>'
            f'<div class="po-scene" role="group" aria-label="Структура проекта: принято {accepted} из 5 этапов">'
            '<canvas tabindex="0" role="img" aria-label="Орбитальная структура. Перетаскивайте для поворота; стрелки — повернуть, Home — сбросить вид."></canvas>'
            f'{nodes}<div class="po-hover-detail" data-orbit-detail hidden role="status"></div>'
            f'<span class="po-coordinate po-coordinate-left" aria-hidden="true">M—{pid:03}</span><span class="po-coordinate po-coordinate-right" aria-hidden="true">{accepted:02} / 05</span></div>'
            f'<figcaption class="po-caption"><span class="po-progress-copy"><span class="po-status-dot"></span>{"Проект собран" if accepted == 5 else f"Принято {accepted} из 5 этапов"}</span><span>Потяни, чтобы повернуть</span></figcaption>'
            f'<nav class="po-stages" aria-label="Этапы структуры">{steps}</nav></figure>')


def project_page(user, projects, project, stages, tasks, history, members, token, selected):
    pid, name = project['id'], project['name']
    model = stage_model(stages)
    initial_tab = request.args.get('tab', 'result')
    if initial_tab not in ('result', 'tasks', 'history'):
        initial_tab = 'result'
    panels, properties, quick_actions = [], [], []
    for i, stage in enumerate(stages):
        status = model[i]['status']
        # A teacher does not receive an unsubmitted draft in this presentation.
        visible = status != 'locked' and (user['role'] == 'student' or status in ('review', 'done'))
        text = stage['result'] if visible else ''
        resource = (f'<a class="lw-result-link" href="{escape(stage["presentation_url"], quote=True)}" target="_blank" rel="noopener noreferrer">Презентация {icon("external")}</a>'
                    if visible and safe_web_url(stage['presentation_url']) else '')
        panels.append(f'<article class="lw-result" data-stage-panel="{i}" {"hidden" if i != selected else ""}><h2>{NAMES[i]}</h2><p>{escape(text) if text else "Этап пока закрыт." if status == "locked" else "Результат ещё не отправлен." if user["role"] == "teacher" else "Добавьте результат в редакторе этапа."}</p>{resource}</article>')
        action = ''
        if status != 'locked':
            action = f'<a class="lw-button {"lw-button-primary" if status in ("progress", "returned", "review") else ""}" href="/project/{pid}/stages?view_stage={i+1}#stage-{i+1}-result">{"Проверить результат" if user["role"] == "teacher" and status == "review" else "Доработать" if status == "returned" and user["role"] == "student" else "Открыть редактор" if user["role"] == "student" and status == "progress" else "Открыть этап"}</a>'
        if (user['role'] == 'student' and status in ('progress', 'returned')) or (user['role'] == 'teacher' and status == 'review'):
            quick_label = 'Проверить' if user['role'] == 'teacher' else 'Доработать' if status == 'returned' else 'Редактор'
            quick_actions.append(f'<a class="lw-button lw-button-primary ln-compact-action" data-stage-quick="{i}" {"hidden" if i != selected else ""} href="/project/{pid}/stages?view_stage={i+1}#stage-{i+1}-result">{quick_label}</a>')
        comment = escape(stage['teacher_comment'] or ('Результат у учителя.' if status == 'review' else 'Здесь появится обратная связь учителя.'))
        properties.append(f'<div data-stage-info="{i}" {"hidden" if i != selected else ""}><h2 class="ln-inspector-title">Сведения</h2><dl class="lw-properties"><div><dt>Статус</dt><dd>{status_name(status)}</dd></div><div><dt>Этап</dt><dd>{i+1} / 5 · {NAMES[i]}</dd></div><div><dt>Обновлено</dt><dd>{format_timestamp(stage["updated_at"])}</dd></div></dl><section class="lw-review-panel"><div class="lw-review-panel-header">Обратная связь</div><div class="lw-review-panel-body"><p>{comment}</p>{action}</div></section></div>')
    task_rows = ''.join(f'<a class="ln-task" href="/project/{pid}?view_stage={t["stage_number"] or selected+1}#task-{t["id"]}">{icon("check" if t["status"] == "done" else "tasks")}<span>{escape(t["title"])}<small>{escape(t["assignee_name"] or "Не назначен")}</small></span><small>{escape({"done":"Готово","progress":"В работе","todo":"К выполнению"}.get(t["status"],t["status"]))}</small></a>' for t in tasks)
    events = ''.join(f'<article class="ln-event"><span>{icon("history")}</span><div><strong>{escape(EVENTS.get(row["event"], "Обновление"))}</strong><p>{escape(row["actor_name"] or row["author_name"] or "Участник проекта")} · {format_timestamp(row["recorded_at"])}</p>{"<p>"+escape(row["comment"])+"</p>" if row["comment"] else ""}</div></article>' for row in history['items'])
    people = ''.join(f'<p>{escape(m["username"])}<small>{"Учитель" if m["role"] == "teacher" else "Ученик"}</small></p>' for m in members)
    return document(name, f'''<div class="lx-app-shell lw-shell">{sidebar(user, projects, token, pid, model, selected)}
      <main class="lx-app-main lw-view" id="linear-main"><header class="lx-app-topbar lw-topbar">{icon('folder')}<span class="lw-id">MFL-{pid:03}</span><span class="lw-breadcrumb-title">{escape(name)}</span>{theme_button()}</header>
      <div class="lw-viewbar"><nav class="lw-tabs" aria-label="Раздел проекта"><a href="?stage={selected+1}&tab=result" data-tab="result" aria-current="{'page' if initial_tab == 'result' else 'false'}">Результат</a><a href="?stage={selected+1}&tab=tasks" data-tab="tasks" aria-current="{'page' if initial_tab == 'tasks' else 'false'}">Задачи</a><a href="?stage={selected+1}&tab=history" data-tab="history" aria-current="{'page' if initial_tab == 'history' else 'false'}">История</a></nav><div class="lw-toolbar-actions">{"".join(quick_actions)}<a class="lw-button ln-team-link" href="/project/{pid}/team">Команда</a></div></div>
      <div class="lw-body"><section class="lw-content"><div class="lw-title-block"><h1>{escape(name)}</h1><p class="lw-stage-context"><span data-stage-caption>{NAMES[selected]}</span><span>·</span><span data-stage-status>{status_name(model[selected]['status'])}</span></p></div>
      <section data-tab-panel="result" {"hidden" if initial_tab != "result" else ""}>{''.join(panels)}{orbit(model, selected, pid)}<a class="lw-result-link" href="/project/{pid}/preparation">Видеозащита и итоговая оценка {icon('arrow')}</a></section>
      <section data-tab-panel="tasks" {"hidden" if initial_tab != "tasks" else ""}><div class="lw-section-title"><h2>Задачи команды</h2><a href="/project/{pid}#task-board">Доска задач {icon('arrow')}</a></div>{task_rows or '<p class="lw-muted">Задач пока нет.</p>'}</section>
      <section data-tab-panel="history" {"hidden" if initial_tab != "history" else ""}><div class="lw-section-title"><h2>Последние события</h2><a href="/project/{pid}/history">Вся история {icon('arrow')}</a></div>{events or '<p class="lw-muted">Здесь появятся отправки и решения учителя.</p>'}</section></section>
      <aside class="lw-inspector ln-inspector" aria-label="Сведения и обратная связь">{''.join(properties)}<details class="ln-people"><summary>Команда · {len(members)}</summary>{people}</details></aside></div></main></div>''')


def register_linear_views(app, login_required, current_user, get_db, projects_for_user, project_details, tasks_for_project, token):
    @app.get('/next')
    @login_required
    def linear_home():
        user = current_user()
        projects = projects_for_user()
        rows = []
        with closing(get_db()) as conn:
            if projects:
                ids = tuple(p['id'] for p in projects)
                rows = [dict(r) for r in conn.execute('SELECT project_id,number,state FROM project_stages WHERE project_id IN (' + ','.join('?' for _ in ids) + ')', ids)]
        cards = []
        empty_text = 'Проекты появятся после назначения руководителем.' if user['role'] == 'teacher' else 'Создайте проект на рабочем столе или примите приглашение.'
        review_link = '<a class="lw-button" href="/teacher#review-queue">Все проверки</a>' if user['role'] == 'teacher' else ''
        for p in projects:
            stages = stages_from_rows(r for r in rows if r['project_id'] == p['id'])
            accepted, current = stage_summary(stages)
            label = status_name(stage_model(stages)[current['number']-1]['status']) if current else 'Все этапы приняты'
            cards.append(f'<a class="ln-project-row" href="/next/project/{p["id"]}" data-project-name="{escape(p["name"].lower(), quote=True)}">{icon("folder")}<span>{escape(p["name"])}<small>MFL-{p["id"]:03} · {label}</small></span><span class="ln-progress">{accepted} / 5</span>{icon("arrow")}</a>')
        return document('Проекты', f'<div class="lx-app-shell">{sidebar(user, projects, token())}<main class="lx-app-main" id="linear-main"><header class="lx-app-topbar">Обзор проектов{theme_button()}</header><section class="ln-projects"><h1>{"Проекты учеников" if user["role"] == "teacher" else "Мои проекты"}</h1><div class="ln-project-tools"><label>Найти проект<input type="search" data-project-search placeholder="Название проекта…"></label>{review_link}</div><div class="ln-project-list">{"".join(cards) or f"<p class=lw-muted>Проектов пока нет. {empty_text}</p>"}</div><p class="lw-muted" data-search-empty hidden>Ничего не найдено.</p></section></main></div>')

    @app.get('/next/project/<int:project_id>')
    @login_required
    def linear_project(project_id):
        user = current_user()
        project = project_details(project_id) if 0 < project_id < 2**63 else None
        if project is None:
            return document('Нет доступа', f'<main class="ln-access" id="linear-main"><header class="lx-app-topbar"><a href="/next">К проектам</a>{theme_button()}</header><h1>Нет доступа к проекту</h1></main>'), 403
        with closing(get_db()) as conn:
            stages = get_stages(conn, project_id)
            history = load_history(conn, project_id, 'all', 1)
            members = [dict(r) for r in conn.execute('SELECT u.username,u.role FROM users u JOIN project_members m ON m.user_id=u.id WHERE m.project_id=? ORDER BY u.id', (project_id,))]
        _, current = stage_summary(stages)
        try:
            selected = int(request.args.get('stage', current['number'] if current else 5))-1
        except (ValueError, TypeError):
            selected = (current['number'] if current else 5)-1
        selected = max(0, min(4, selected))
        if current and selected >= current['number']:
            selected = current['number']-1
        return project_page(user, projects_for_user(), project, stages, tasks_for_project(project_id), history, members, token(), selected)
