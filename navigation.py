"""Shared project links; callers must check project access before rendering."""
from interface import icon


def project_navigation(project_id, active='overview'):
    base = f'/project/{project_id}'
    links = [('overview', base, 'Обзор'), ('stages', base + '/stages', 'Этапы'),
             ('tasks', base + '#task-board', 'Задачи'), ('team', base + '/team', 'Команда'),
             ('finish', base + '/defense', 'Защита и результат')]
    section = 'finish' if active in ('defense', 'assessment') else active
    nav = '<nav class="project-navigation" aria-label="Разделы проекта">' + ''.join(
        f'<a href="{href}" data-project-tab="{key}"' + (' aria-current="page"' if key == section else '')
        + f'>{icon("video" if key == "finish" else key)}<span>{label}</span></a>' for key, href, label in links)
    # History remains reachable from every project section, including old URLs.
    nav += f'<a class="ui-project-history" href="{base}/history"' + (' aria-current="page"' if active == 'history' else '') + f'>{icon("history")}<span>История решений</span></a></nav>'
    if section == 'finish':
        nav += '<nav class="ui-finish-navigation" aria-label="Завершение проекта">' + ''.join(
            f'<a href="{base}/{key}"' + (' aria-current="page"' if active == key else '') + f'>{icon("video" if key == "defense" else "assessment")}{label}</a>'
            for key,label in [('defense','Видеозащита'),('assessment','Оценивание')]) + '</nav>'
    return nav
