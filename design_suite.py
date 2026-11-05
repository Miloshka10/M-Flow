"""Presentation helpers. No database access or workflow decisions."""
from html import escape
from interface import icon


def initials(name):
    return ''.join(word[0] for word in str(name).split()[:2]).upper() or '—'


def project_cover(name, project_id):
    tone = ('sage', 'blue', 'gold')[int(project_id) % 3]
    word = str(name).split()[0] if str(name).split() else 'Проект'
    return f'<div class="ds-cover ds-{tone}" aria-hidden="true"><span>{escape(word[:24].upper())}</span></div>'


def stage_track(accepted):
    return (f'<div class="ds-stage-track" role="img" aria-label="Принято {accepted} из 5 этапов">'
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
