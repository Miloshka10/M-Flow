"""Presentation helpers. No database access or workflow decisions."""
from html import escape
from interface import icon


def initials(name):
    return ''.join(word[0] for word in str(name).split()[:2]).upper() or '—'


def stage_track(accepted):
    return ('<div class="ds-stage-track" aria-hidden="true">'
            + ''.join('<span class="accepted"></span>' if n < accepted else '<span></span>' for n in range(5)) + '</div>')


def compact_stage_route(stages, project_id):
    """The real five-stage state, independent of task counts and grades."""
    current = next((s['number'] for s in stages if s['state'] != 'done'), None)
    accepted = sum(s['state'] == 'done' for s in stages)
    items = []
    for stage in stages:
        number = stage['number']
        done = stage['state'] == 'done'
        active = number == current
        label = ('Принято' if done else 'На проверке' if active and stage['state'] == 'review'
                 else 'Нужна доработка' if active and stage.get('reviewed_by') and stage['state'] == 'progress'
                 else 'Текущий этап' if active else 'Впереди')
        title = 'Выбор темы' if number == 1 else 'Документация' if number == 5 else f'Работа · {number-1}'
        items.append(f'<li class="{"done" if done else "current" if active else "future"}"'
                     + (' aria-current="step"' if active else '')
                     + f'><span class="v3-route-dot" aria-hidden="true">{icon("check") if done else f"{number:02d}"}</span>'
                     + f'<a href="/project/{int(project_id)}/stages?view_stage={number}#stage-{number}" aria-label="Этап {number}. {title}. {label}">'
                     + f'<strong>{title}</strong><small>{label}</small></a></li>')
    return (f'<aside class="v3-route-aside" aria-label="Путь проекта"><div class="v3-route-title">'
            f'<span>Путь проекта</span><span>Принято {accepted} из 5 этапов</span></div>'
            f'<ol class="v3-mini-route">{"".join(items)}</ol>'
            '<p class="v3-route-footer">Решение по этапу принимает руководитель.</p></aside>')

def cabinet_shortcuts(teacher=False):
    items = ([
        ('Проверки', 'Работы и видеозащиты учеников', '/teacher/review', 'review'),
        ('Проекты учеников', 'Состояние и сроки проектов', '#teacher-projects', 'stages'),
        ('Учебные группы', 'Классы и участники', '/groups', 'groups'),
        ('Руководство', 'Приглашения и согласования', '/supervision', 'team'),
    ] if teacher else [
        ('Мои проекты', 'Этапы и задачи проекта', '#my-projects', 'stages'),
        ('Учебные группы', 'Твои классы и учителя', '/groups', 'groups'),
        ('Команды и люди', 'Участники и приглашения', '/teams', 'team'),
        ('Мои результаты', 'Решения учителя и оценки', '#student-results', 'review'),
    ])
    return '<nav class="cabinet-shortcuts ds-home-only" aria-label="Быстрый переход">' + ''.join(
        f'<a class="cabinet-shortcut" href="{href}"><div><strong>{escape(title)}</strong><p>{escape(description)}</p></div>'
        f'<span class="cabinet-shortcut-arrow">{icon("arrow")}</span>'
        f'<img src="/static/illustrations/cabinet/{art}.png" alt="" width="316" height="198" loading="lazy"></a>'
        for title, description, href, art in items
    ) + '</nav>'
