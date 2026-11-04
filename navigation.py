"""Shared project links; callers must check project access before rendering."""
from interface import icon
import re


def project_list_url(role, project_id=None):
    if role == 'teacher':
        return '/teacher#teacher-projects'
    return (f'/?project={int(project_id)}' if project_id else '/') + '#my-projects'


def stage_result_target(target):
    """Normalize a result action; ordinary stage selection keeps its own URL."""
    return target + '-result' if target and re.fullmatch(r'(?:/project/[0-9]+/)?stages#stage-[1-5]', target) else target


def project_navigation(project_id, active='overview'):
    base = f'/project/{project_id}'
    links = [('overview', base, 'Обзор'), ('tasks', base + '/stages', 'Работа над проектом'), ('team', base + '/team', 'Команда'),
             ('finish', base + '/preparation', 'Защита и результат')]
    section = 'finish' if active in ('preparation', 'defense', 'assessment') else 'tasks' if active == 'stages' else active
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
            for key,label in [('preparation','Подготовка'),('defense','Видеозащита'),('assessment','Оценивание')]) + '</nav>'
    return nav
