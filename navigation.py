"""Shared project links; callers must check project access before rendering."""
from interface import icon
import re
from html import escape
from urllib.parse import urlencode


def bounded_page(value):
    return int(value) if value.isascii() and value.isdigit() and 0 < len(value) <= 6 and int(value) > 0 else 1


def page_links(base, page, more, params=None, fragment=''):
    def link(number, label):
        query = escape(urlencode(dict(params or {}, page=number)), quote=True)
        return f'<a href="{base}?{query}{fragment}">{label}</a>'
    links = []
    if page > 1:
        links.append(link(page - 1, 'Предыдущая страница'))
    if more:
        links.append(link(page + 1, 'Следующая страница'))
    return f'<nav class="page-links" aria-label="Страницы">{"".join(links)}</nav>' if links else ''


def project_list_url(role, project_id=None):
    if role == 'teacher':
        return '/teacher#teacher-projects'
    return (f'/?project={int(project_id)}' if project_id else '/') + '#my-projects'


def stage_result_target(target):
    """Normalize a result action; ordinary stage selection keeps its own URL."""
    return target + '-result' if target and re.fullmatch(r'(?:/project/[0-9]+/)?stages#stage-[1-5]', target) else target


def stage_feedback_url(project_id, number, decision_id):
    return f'/project/{project_id}/history?work=stage-{number}&entry={decision_id}#history-event-{decision_id}'


def project_navigation(project_id, active='overview'):
    base = f'/project/{project_id}'
    links = [('overview', base, 'Обзор'), ('tasks', base + '/stages', 'Работа над проектом'), ('team', base + '/team', 'Команда'),
             ('finish', base + '/preparation', 'Защита и результат')]
    section = 'finish' if active in ('preparation', 'defense', 'assessment', 'passport') else 'tasks' if active == 'stages' else 'overview' if active == 'criteria' else active
    history = (f'<div class="project-context-actions"><a class="ui-project-history" href="{base}/history"'
               + (' aria-current="page"' if active == 'history' else '')
               + f'>{icon("history")}<span>История работ и решений</span></a></div>')
    nav = history + '<nav class="project-navigation" aria-label="Разделы проекта">' + ''.join(
        f'<a href="{href}" data-project-tab="{key}"' + (' aria-current="page"' if key == section else '')
        + f'>{icon("video" if key == "finish" else key)}<span>{label}</span></a>' for key, href, label in links)
    # History remains reachable from every project section, including old URLs.
    nav += '</nav>'
    if section == 'finish':
        nav += '<nav class="ui-finish-navigation" aria-label="Завершение проекта">' + ''.join(
            f'<a href="{base}/{key}"' + (' aria-current="page"' if active == key else '') + f'>{icon("tasks" if key == "preparation" else "video" if key == "defense" else "assessment")}{label}</a>'
            for key,label in [('passport','Паспорт проекта'),('preparation','Подготовка'),('defense','Видеозащита'),('assessment','Оценивание')]) + '</nav>'
    return nav
