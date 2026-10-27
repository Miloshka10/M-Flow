"""Small rendering and validation helpers for the project task board."""
from html import escape
from urllib.parse import urlencode, urlsplit

from database import execute_schema, table_columns
from deadlines import DEADLINE_FILTERS, TASK_SORTS
from project_stages import STAGES

PRIORITIES = {'low':'Низкий', 'normal':'Обычный', 'high':'Высокий', 'urgent':'Срочный'}


def initialize_task_board(conn):
    columns = table_columns(conn, 'tasks')
    if 'description' not in columns:
        execute_schema(conn, "ALTER TABLE tasks ADD COLUMN description TEXT NOT NULL DEFAULT ''")
    if 'stage_number' not in columns:
        execute_schema(conn, 'ALTER TABLE tasks ADD COLUMN stage_number INTEGER CHECK(stage_number BETWEEN 1 AND 5)')


def task_fields(form, previous=None):
    previous = previous or {}
    description = form.get('description', previous.get('description', '')).strip()
    if len(description) > 3000:
        raise ValueError('Описание задачи — до 3000 символов.')
    raw = form.get('stage_number', previous.get('stage_number'))
    if raw in (None, ''):
        number = None
    elif str(raw) in ('1','2','3','4','5'):
        number = int(raw)
    else:
        raise ValueError('Выберите один из пяти этапов или «Без этапа».')
    return description, number


def board_return(project_id, value, default=None):
    base = f'/project/{project_id}'
    if not isinstance(value, str) or len(value) > 2000 or '\\' in value or any(ord(c) < 32 for c in value):
        return default or base + '#task-board'
    try:
        parsed = urlsplit(value)
    except ValueError:
        return default or base + '#task-board'
    if parsed.scheme or parsed.netloc or parsed.path != base:
        return default or base + '#task-board'
    return value


def options(values, current):
    return ''.join(f'<option value="{escape(str(key),quote=True)}" {"selected" if str(current or "") == str(key) else ""}>{escape(label)}</option>' for key,label in values.items())


def stage_options(current):
    return options({'':'Без этапа', **{str(n): f'Этап {n} · {title}' for n,title in STAGES}}, current)


def task_edit_fields(task, members):
    return f'''<label>Название задачи<input name="title" required maxlength="160" value="{escape(task['title'],quote=True)}"></label>
        <label>Описание задачи<textarea name="description" maxlength="3000" rows="5">{escape(task['description'])}</textarea></label>
        <label>Дедлайн задачи<input name="deadline" type="date" value="{escape(task['deadline'] or '',quote=True)}"></label>
        <label>Ответственный за задачу<select name="assignee_id">{options({'':'Не назначать', **{str(m['id']):m['username'] for m in members if m['role']=='student'}}, task['assignee_id'])}</select></label>
        <label>Этап проекта<select name="stage_number">{stage_options(task['stage_number'])}</select></label>
        <p class="t-muted">Связь с этапом помогает организовать работу. Выполнение задачи не означает принятие этапа учителем.</p>'''


def create_form(pid, members, token, return_to, values=None, error=''):
    task = dict(title='',description='',deadline='',assignee_id=None,stage_number=None)
    task.update(values or {})
    priority = task.get('priority','normal')
    return f'''<details class="board-disclosure task-create" id="task-create"><summary data-ui-icon="plus">Добавить задачу</summary>
        <div class="task-detail-content"><h2>Добавить задачу</h2><p>Опишите конкретный результат, назначьте ответственного и срок.</p>
        {f'<p role="alert">{escape(error)}</p>' if error else ''}
        <form action="/project/{pid}/add" method="post" class="add-task-form board-task-form">
        {token}<input type="hidden" name="return_to" value="{escape(return_to,quote=True)}">
        {task_edit_fields(task, members)}
        <label>Приоритет новой задачи<select name="priority">{options(PRIORITIES,priority)}</select></label>
        <button type="submit" data-ui-icon="plus">Добавить</button></form></div></details>'''


def render_filters(pid, user, members, state):
    base = f'/project/{pid}'
    def link(**updates):
        args = dict(state)
        args.update(updates)
        return escape(base + '?' + urlencode({k:v for k,v in args.items() if v not in ('',None)}) + '#task-board',quote=True)
    quick = [('Все',dict(mine='',due=''))]
    if user['role'] == 'student':
        quick.append(('Мои',dict(mine='1',due='')))
    quick += [('Скоро срок',dict(mine='',due='week')), ('Просрочены',dict(mine='',due='overdue'))]
    quick_html = ''.join(f'<a href="{link(**args)}" {"aria-current=\"true\"" if all(str(state.get(k) or "") == v for k,v in args.items()) else ""}>{label}</a>' for label,args in quick)
    chips = []
    labels = {'q':'Поиск', 'priority':'Приоритет', 'due':'Срок', 'mine':'Мои задачи', 'assignee':'Исполнитель', 'stage':'Этап'}
    people = {str(m['id']):m['username'] for m in members if m['role']=='student'}
    values = {'priority':PRIORITIES, 'due':DEADLINE_FILTERS, 'assignee':{'none':'Не назначен',**people},
              'stage':{'none':'Без этапа',**{str(n):str(n) for n in range(1,6)}}}
    for key,label in labels.items():
        if state.get(key):
            value = values.get(key,{}).get(state[key], state[key]) if key != 'mine' else ''
            chips.append(f'<span>{label}{": " + escape(value) if value else ""}</span>')
    advanced = any(state.get(k) for k in ('priority','assignee','stage')) or state['sort'] != 'priority'
    return f'''<form method="get" class="task-filters board-filters" action="{base}#task-board">
        <div class="board-search"><input type="search" name="q" value="{escape(state['q'],quote=True)}" placeholder="Найти задачу…" aria-label="Поиск по названию задачи"><button type="submit" data-ui-icon="search">Найти</button></div>
        {f'<input type="hidden" name="mine" value="{state["mine"]}">' if user['role']=='student' else ''}<input type="hidden" name="column" value="{state['column']}"><input type="hidden" name="view_stage" value="{escape(state.get('view_stage', ''), quote=True)}">
        <details class="board-advanced" {"open" if advanced else ""}><summary data-ui-icon="filter">Фильтры</summary><div>
        <label>Приоритет<select name="priority" aria-label="Фильтр приоритета">{options({'':'Любой приоритет',**PRIORITIES},state['priority'])}</select></label>
        <label>Срок<select name="due" aria-label="Фильтр срока">{options(DEADLINE_FILTERS,state['due'])}</select></label>
        <label>Исполнитель<select name="assignee" aria-label="Фильтр исполнителя">{options({'':'Все исполнители','none':'Не назначен',**people},state['assignee'])}</select></label>
        <label>Этап<select name="stage" aria-label="Фильтр этапа">{options({'':'Все этапы','none':'Без этапа',**{str(n):f'Этап {n}' for n in range(1,6)}},state['stage'])}</select></label>
        <label>Порядок<select name="sort" aria-label="Сортировка задач">{options(TASK_SORTS,state['sort'])}</select></label><button data-ui-icon="filter">Применить</button></div></details>
        </form><nav class="board-quick" aria-label="Быстрые фильтры задач">{quick_html}</nav>
        <div class="board-active">{''.join(chips)}{f'<a class="filter-reset" href="{base}#task-board">Сбросить</a>' if chips or state['sort'] != 'priority' else ''}</div>'''
