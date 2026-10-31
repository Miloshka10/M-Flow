"""Unified presentation of existing stages and tasks. No writes or permission grants."""
from html import escape
from urllib.parse import urlencode

from interface import icon
from project_stages import render_stages, stage_label, stage_summary, STAGE_DESCRIPTIONS
from project_workspace import next_step

SHORT_NAMES = ('Тема проекта', 'Работа · 1', 'Работа · 2', 'Работа · 3', 'Документация')


def render_project_work(pid, user, owner, members, stages, defense, assessments,
                        tasks, all_tasks, render_task, filters, create, state,
                        token, submitted=None, error='', unsaved=''):
    completed, current = stage_summary(stages)
    current_number = current['number'] if current else 5
    selected = int(state.get('view_stage') or (state.get('stage') if state.get('stage') in ('1', '2', '3', '4', '5') else current_number))
    has_teacher = any(m['role'] == 'teacher' for m in members)
    base = f'/project/{pid}'

    def location(number, anchor=None):
        args = {k: v for k, v in state.items() if v and k != 'column'}
        args['view_stage'] = str(number)
        return escape(base + '?' + urlencode(args) + '#' + (anchor or f'stage-{number}'), quote=True)

    route = ''.join(f'''<a class="pw-route-step {'pw-accepted' if s['state'] == 'done' else ''}"
        href="{location(s['number'])}" data-work-stage="{s['number']}" aria-controls="stage-{s['number']}"
        aria-label="Этап {s['number']}. {escape(s['title'], quote=True)}. {escape(stage_label(s, current_number), quote=True)}"
        {'aria-current="step"' if s['number'] == selected else ''}>
        <span class="pw-route-number">{icon('check') if s['state'] == 'done' else str(s['number']).zfill(2)}</span>
        <strong>{SHORT_NAMES[s['number']-1]}</strong><small>{escape(stage_label(s, current_number))}</small></a>''' for s in stages)

    def task_list(rows, number):
        active = [t for t in rows if t['status'] != 'done']
        done = [t for t in rows if t['status'] == 'done']
        total = sum(t['stage_number'] == number for t in all_tasks)
        html = f'<p class="pw-task-count">Показано {len(rows)} из {total} {"общих задач" if number is None else "задач этапа"} · выполнено {sum(t["status"] == "done" and t["stage_number"] == number for t in all_tasks)} из {total}</p>'
        if active:
            html += '<div class="pw-task-list">' + ''.join(render_task(t) for t in active) + '</div>'
        elif not done:
            html += f'''<div class="pw-empty"><h3>{'Нет задач по этим условиям' if total else 'Начните с небольшого дела' if owner else 'Задачи пока не добавлены'}</h3>
                <p>{'Измените фильтры или переключите этап.' if total else 'Добавьте задачи и распределите работу команды.' if owner else 'Владелец проекта может добавить задачи этого этапа.'}</p></div>'''
        else:
            html += '<p class="pw-task-note">Все показанные задачи выполнены. Результат этапа проверяет учитель.</p>'
        if done:
            html += f'<details class="pw-done"><summary>Выполненные · {len(done)}</summary><div class="pw-task-list">' + ''.join(render_task(t) for t in done) + '</div></details>'
        return html

    panels = []
    for s in stages:
        number = s['number']
        kind = 'accepted' if s['state'] == 'done' else 'returned' if s['state'] == 'progress' and s.get('reviewed_by') else s['state']
        if current and number != current['number']:
            title = 'Этап принят. Результат сохранён.' if s['state'] == 'done' else f'Результат откроется после принятия этапа {number-1}'
            text = 'Можно посмотреть работу и решение учителя.' if s['state'] == 'done' else 'Задачи будущего этапа можно планировать уже сейчас.'
            action, target = 'К текущему этапу', location(current['number'])
            key = 'accepted' if s['state'] == 'done' else 'locked'
        else:
            step = next_step(stages, user['role'], owner, has_teacher, defense, bool(assessments))
            title, text, action, key = step['title'], step['text'], step['action'], step['key']
            raw = step['target']
            target = (location(number, f'stage-{number}-result') if raw and raw.startswith('stages#') else
                      location(number, f'stage-{number}-tasks') if raw == '#task-board' else
                      escape(raw if raw and raw.startswith('/') else f'{base}/{raw}', quote=True) if raw else '')
        rows = [t for t in tasks if t['stage_number'] == number]
        values = submitted if submitted is not None and submitted.get('number') == str(number) else None
        result = render_stages(stages, user, token, values, has_teacher, pid,
                               only_number=number, compact=True, return_to=location(number).replace('&amp;', '&'))
        panels.append(f'''<section class="pw-stage pw-stage-{kind}" id="stage-{number}" data-work-panel="{number}">
            <header class="pw-stage-heading"><span class="eyebrow">ЭТАП {number} / 5{' · ТЕКУЩИЙ' if current and number == current['number'] else ''}</span>
            <h2>{escape(s['title'])}</h2><p>{STAGE_DESCRIPTIONS[number]}</p></header>
            <div class="pw-mobile-views" role="group" aria-label="Задачи и результат этапа {number}">
            <button type="button" data-work-view-button="tasks" aria-pressed="true" aria-controls="stage-{number}-tasks">Задачи</button>
            <button type="button" data-work-view-button="result" aria-pressed="false" aria-controls="stage-{number}-result">Результат этапа</button></div>
            <div class="pw-grid"><section class="pw-tasks" id="stage-{number}-tasks"><div class="pw-task-heading"><h3>Задачи этапа</h3>
            {f'<a class="board-add action-link" href="#task-create">{icon("plus")} Задача</a>' if owner else ''}</div>
            <div data-work-tools-slot></div>{task_list(rows, number)}<div data-unassigned-slot></div></section>
            <aside class="pw-result" id="stage-{number}-result" tabindex="-1" aria-label="Результат этапа {number}">
            {f'<p class="error" role="alert">{escape(error)}</p>{unsaved}' if error and (number == current_number) else ''}{result}
            <details class="pw-next pw-{kind}" data-next-step="{key}"><summary>{escape(title)}</summary>
            <p>{escape(text)}</p>{f'<a class="action-link" href="{target}">{escape(action)} {icon("arrow")}</a>' if target else ''}</details></aside></div></section>''')
    general = [t for t in tasks if t['stage_number'] is None]
    unassigned = f'''<details class="pw-unassigned" id="tasks-without-stage"><summary>Без этапа · {len(general)}</summary>
        <p>Общие дела проекта. Владелец может привязать их к этапу.</p>{task_list(general, None)}</details>'''
    return f'''<div class="pw-route-caption"><h2>Путь проекта</h2><span>Принято {completed} из 5 этапов</span></div>
        <nav class="pw-route" aria-label="Пять этапов проекта">{route}</nav>
        <div class="pw-tools" data-work-tools {'hidden' if not all_tasks else ''}>{filters}<p class="board-filter-summary">Показано {len(tasks)} из {len(all_tasks)} задач во всём проекте. Выберите нужный этап выше.</p>{'<p role="status">Нет задач по выбранным условиям. Измените фильтры или нажмите «Сбросить».</p>' if not tasks and all_tasks else ''}</div>
        {create}<p id="board-status" role="status" aria-live="polite" hidden></p>{''.join(panels)}{unassigned}
        <div class="pw-finish"><div><h3>Видеозащита и итоговая оценка</h3><p>Отдельный шаг завершения. Доступен на любом этапе.</p></div>
        <a class="action-link" href="{base}/defense">Защита и результат {icon('arrow')}</a></div>''', selected
