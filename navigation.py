"""Shared project links; callers must check project access before rendering."""


def project_navigation(project_id, active='overview'):
    base = f'/project/{project_id}'
    links = [('overview', base, 'Обзор'), ('tasks', base + '#task-board', 'Задачи'),
             ('stages', base + '/stages', 'Этапы'), ('team', base + '/team', 'Команда')]
    return '<nav class="project-navigation" aria-label="Разделы проекта">' + ''.join(
        f'<a href="{href}" data-project-tab="{key}"' + (' aria-current="page"' if key == active else '')
        + f'>{label}</a>' for key, href, label in links) + '</nav>'
