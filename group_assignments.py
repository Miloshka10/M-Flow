"""Class assignments reuse project submissions and require separate teacher consent."""
from collections import Counter
from html import escape
import time

from database import PostgresConnection, execute_schema_script, form_database_id, insert_id
from deadlines import deadline_info, format_timestamp, parse_deadline
from interface import icon
from learning_groups import _lock, group_for
from material_links import safe_web_url
from navigation import bounded_page, page_links
from project_rules import create_owned_project, has_project_teacher
from project_stages import get_stages
from project_workspace import next_step
from teacher_supervision import consented_connection
from teamwork import hidden_token
from video_defense import defense_for

PAGE_SIZE = 12
ROSTER_SIZE = 30
PROGRESS = {
    'not_started': 'Не начал', 'awaiting_teacher': 'Нужно согласовать сопровождение',
    'working': 'В работе', 'review': 'Ждёт проверки', 'returned': 'На доработке',
    'accepted': 'Этапы и защита приняты',
}


def initialize_assignments(conn):
    execute_schema_script(conn, """
        CREATE TABLE IF NOT EXISTS group_assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL REFERENCES learning_groups(id),
            title TEXT NOT NULL,
            goal TEXT NOT NULL,
            due_on TEXT NOT NULL,
            materials TEXT NOT NULL DEFAULT '',
            material_url TEXT NOT NULL DEFAULT '',
            revision INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (CAST(CURRENT_TIMESTAMP AS TEXT))
        );
        CREATE INDEX IF NOT EXISTS idx_group_assignments_group ON group_assignments(group_id,id);
        CREATE TABLE IF NOT EXISTS group_assignment_projects (
            assignment_id INTEGER NOT NULL REFERENCES group_assignments(id),
            student_id INTEGER NOT NULL REFERENCES users(id),
            project_id INTEGER NOT NULL UNIQUE REFERENCES projects(id),
            linked_at TEXT NOT NULL DEFAULT (CAST(CURRENT_TIMESTAMP AS TEXT)),
            PRIMARY KEY (assignment_id,student_id)
        );
    """)


def assignments_for(conn, group_id, limit, offset=0):
    return conn.execute("""SELECT id,title,due_on FROM group_assignments
        WHERE group_id=? ORDER BY id DESC LIMIT ? OFFSET ?""", (group_id, limit, offset)).fetchall()


def assignment_for(conn, group_id, assignment_id):
    assignment = conn.execute('SELECT * FROM group_assignments WHERE group_id=? AND id=?',
                              (group_id, assignment_id)).fetchone()
    if assignment is None:
        raise PermissionError('Задание недоступно в этой группе.')
    return assignment


def assignment_fields(form):
    fields = tuple(form.get(key, '').strip() for key in ('title', 'goal', 'due_on', 'materials', 'material_url'))
    title, goal, due_on, materials, url = fields
    if not title or len(title) > 120 or not goal or len(goal) > 2000:
        raise ValueError('Укажите название до 120 символов и цель до 2000 символов.')
    if parse_deadline(due_on) is None:
        raise ValueError('Укажите корректную дату сдачи.')
    if len(materials) > 2000 or (url and not safe_web_url(url)):
        raise ValueError('Материалы должны быть до 2000 символов. Используйте ссылку HTTP или HTTPS.')
    return fields


def save_assignment(conn, user, group_id, form, assignment_id=None):
    _lock(conn, group_id=group_id)
    group = group_for(conn, user, group_id, teacher_only=True)
    if group['archived']:
        raise ValueError('Для изменения заданий восстановите группу из архива.')
    fields = assignment_fields(form)
    if assignment_id is None:
        assignment_id = insert_id(conn, """INSERT INTO group_assignments
            (group_id,title,goal,due_on,materials,material_url) VALUES (?,?,?,?,?,?)""", (group_id, *fields))
    else:
        assignment = assignment_for(conn, group_id, assignment_id)
        if form.get('revision') != str(assignment['revision']):
            raise ValueError('Задание изменилось. Откройте свежую версию и повторите правку.')
        conn.execute("""UPDATE group_assignments SET title=?,goal=?,due_on=?,materials=?,material_url=?,
            revision=revision+1 WHERE group_id=? AND id=?""", (*fields, group_id, assignment_id))
    conn.commit()
    return assignment_id


def link_project(conn, user, group_id, assignment_id, form):
    _lock(conn, group_id=group_id)
    group = group_for(conn, user, group_id)
    if user['role'] != 'student' or group['archived']:
        raise PermissionError('Начать проект может ученик действующей группы.')
    assignment_for(conn, group_id, assignment_id)
    action = form.get('action')
    existing = conn.execute("""SELECT project_id FROM group_assignment_projects
        WHERE assignment_id=? AND student_id=?""", (assignment_id, user['id'])).fetchone()
    if action == 'create_project' and not existing:
        project_id = create_owned_project(conn, user['id'], form.get('project_name', ''))
    elif action == 'link_project' and not existing:
        project_id = form_database_id(form.get('project_id'))
    elif action == 'propose' and existing:
        project_id = existing['project_id']
    else:
        raise ValueError('Обновите страницу. Задание уже связано с проектом или действие недоступно.')
    if isinstance(conn, PostgresConnection):
        conn.execute('SELECT id FROM projects WHERE id=? FOR UPDATE', (project_id,))
    if not conn.execute('SELECT 1 FROM projects WHERE id=? AND owner_id=?', (project_id, user['id'])).fetchone():
        raise PermissionError('Для задания можно выбрать только свой проект.')
    if action == 'propose':
        consented_connection(conn, user, project_id, group['teacher_id'])
    else:
        if conn.execute('SELECT 1 FROM group_assignment_projects WHERE project_id=?', (project_id,)).fetchone():
            raise ValueError('Этот проект уже связан с другим заданием.')
        conn.execute("""INSERT INTO group_assignment_projects(assignment_id,student_id,project_id)
            VALUES(?,?,?)""", (assignment_id, user['id'], project_id))
    conn.commit()
    return project_id


def progress_rows(conn, group, assignment, user):
    teacher = user['role'] == 'teacher'
    # Roster names and linked project titles are visible to the group teacher.
    # Submission metadata is loaded only for projects the viewer can open.
    rows = [dict(row) for row in conn.execute("""SELECT u.username, p.id AS project_id, p.name AS project_name,
            CASE WHEN p.id IS NOT NULL AND EXISTS (SELECT 1 FROM project_members pm
                WHERE pm.project_id=p.id AND pm.user_id=?) THEN 1 ELSE 0 END AS connected,
            ap.linked_at
        FROM learning_group_members m JOIN users u ON u.id=m.student_id
        LEFT JOIN group_assignment_projects ap ON ap.student_id=m.student_id AND ap.assignment_id=?
        LEFT JOIN projects p ON p.id=ap.project_id AND p.owner_id=m.student_id
        WHERE m.group_id=? AND (?=1 OR m.student_id=?) ORDER BY u.username,u.id""",
        (group['teacher_id'], assignment['id'], group['id'], int(teacher), user['id']))]
    ids = [row['project_id'] for row in rows if row['project_id'] and (not teacher or row['connected'])]
    stages, defenses, first = {}, {}, {}
    if ids:
        marks = ','.join('?' for _ in ids)
        for stage in conn.execute(f"""SELECT project_id,number,state,reviewed_by
            FROM project_stages WHERE project_id IN ({marks}) ORDER BY number""", ids):
            stages.setdefault(stage['project_id'], []).append(stage)
        defenses = {r['project_id']: r['state'] for r in conn.execute(
            f'SELECT project_id,state FROM project_defenses WHERE project_id IN ({marks})', ids)}
        first = {r['project_id']: r['first_at'] for r in conn.execute(f"""SELECT project_id,MIN(recorded_at) AS first_at
            FROM work_history WHERE project_id IN ({marks}) AND kind='stage' AND event='submit' GROUP BY project_id""", ids)}
    for row in rows:
        pid = row['project_id']
        current = next((dict(stage) for stage in stages.get(pid, []) if stage['state'] != 'done'), {})
        defense = defenses.get(pid)
        row['status'] = (
            'not_started' if not pid else
            'awaiting_teacher' if teacher and not row['connected'] else
            'review' if current.get('state') == 'review' or defense == 'submitted' else
            'returned' if (current.get('state') == 'progress' and current['reviewed_by']) or defense == 'returned' else
            'accepted' if len(stages.get(pid, [])) == 5 and not current and defense == 'accepted' else
            'working'
        )
        row['work'] = (f'stage-{pid}-{current["number"]}' if current.get('state') == 'review' else
                       f'defense-{pid}' if defense == 'submitted' else '')
        row['first_at'] = first.get(pid)
    return rows


def assignment_cards(group_id, assignments):
    cards = []
    for assignment in assignments:
        due = deadline_info(assignment['due_on'])['display_date']
        cards.append(f'<li><a href="/groups/{group_id}/assignments/{assignment["id"]}">{escape(assignment["title"])}</a> · срок {escape(due)}</li>')
    return '<ul>' + ''.join(cards) + '</ul>' if cards else '<p>Заданий пока нет.</p>'


def group_assignment_section(conn, group):
    gid = group['id']
    return (f'<section class="group-panel"><h2>Задания группы</h2>{assignment_cards(gid, assignments_for(conn, gid, 3))}'
            f'<a href="/groups/{gid}/assignments">Открыть задания группы {icon("arrow")}</a></section>')


def assignment_form(token, values, editing=False):
    def value(field):
        return escape(str(values.get(field, '')), quote=True)
    return f'''<form method="post" class="group-form">{hidden_token(token)}
        <input type="hidden" name="action" value="save">
        <input type="hidden" name="revision" value="{value("revision")}">
        <label>Название задания<input name="title" maxlength="120" required value="{value("title")}"></label>
        <label>Цель<textarea name="goal" maxlength="2000" rows="4" required>{value("goal")}</textarea></label>
        <label>Срок сдачи<input type="date" name="due_on" required value="{value("due_on")}"></label>
        <label>Материалы и инструкции<textarea name="materials" rows="3" maxlength="2000">{value("materials")}</textarea></label>
        <label>Ссылка на материалы<input type="url" name="material_url" maxlength="2000" value="{value("material_url")}"></label>
        <button data-ui-icon="save">{"Сохранить задание" if editing else "Выдать задание"}</button></form>'''


STYLES = '<link rel="stylesheet" href="/static/learning-groups.css?v=20261119-assignments">'


def render_assignments(conn, user, group, token, page, submitted=None):
    base = f'/groups/{group["id"]}/assignments'
    rows = assignments_for(conn, group['id'], PAGE_SIZE + 1, (page - 1) * PAGE_SIZE)
    body = STYLES + f'<section class="group-panel">{assignment_cards(group["id"], rows[:PAGE_SIZE])}'
    body += page_links(base, page, len(rows) > PAGE_SIZE) + '</section>'
    if user['role'] == 'teacher' and not group['archived']:
        opened = ' open' if submitted is not None or not rows else ''
        body += (f'<details class="group-panel"{opened}><summary>Новое задание</summary>'
                 + assignment_form(token, submitted or {}) + '</details>')
    return body


def student_assignment(conn, user, group, assignment, row, token):
    if row and row['project_id']:
        pid = row['project_id']
        base = f'/project/{pid}'
        active_teacher = has_project_teacher(conn, pid)
        published = conn.execute("""SELECT 1 FROM project_assessments
            WHERE project_id=? AND student_id=? AND state='published' LIMIT 1""", (pid, user['id'])).fetchone() is not None
        step = next_step(get_stages(conn, pid), 'student', True, active_teacher, defense_for(conn, pid), published)
        target = step['target']
        if not target:
            target = base
        elif not target.startswith('/'):
            target = base + (target if target.startswith('#') else '/' + target)
        pending = conn.execute("""SELECT 1 FROM supervision_requests WHERE project_id=? AND owner_id=?
            AND target_id=? AND state='pending' AND expires>?""",
            (pid, user['id'], group['teacher_id'], int(time.time()))).fetchone()
        proposal = ''
        if not row['connected'] and not group['archived']:
            proposal = (f'<form method="post">{hidden_token(token)}<button name="action" value="propose">Предложить сопровождение учителю группы</button></form>'
                        if not pending else '<p>Запрос отправлен. <a href="/supervision">Открыть согласования</a>.</p>')
        next_link = f'<a href="{escape(target, quote=True)}">{escape(step["action"] or "Открыть проект")}</a>'
        if step['key'] == 'teacher-missing' and proposal:
            step.update(title='Согласуй сопровождение проекта',
                        text='После подтверждения учителем можно отправить работу на проверку.')
            next_link = ''
        return f'''<section class="group-panel"><h2>Следующий шаг</h2><h3>{escape(step["title"])}</h3>
            <p>{escape(step["text"])}</p><div class="group-actions">{next_link}
            <a href="{base}">Обзор проекта</a>{proposal}</div></section>'''
    if group['archived']:
        return '<p>Группа в архиве. Начать проект по заданию уже нельзя.</p>'
    projects = conn.execute("""SELECT p.id,p.name FROM projects p WHERE p.owner_id=? AND NOT EXISTS
        (SELECT 1 FROM group_assignment_projects ap WHERE ap.project_id=p.id) ORDER BY p.id DESC""", (user['id'],)).fetchall()
    options = ''.join(f'<option value="{p["id"]}">{escape(p["name"])}</option>' for p in projects)
    link = f'''<form method="post" class="group-form">{hidden_token(token)}
        <label>Готовый проект<select name="project_id" required><option value="">Выберите проект</option>{options}</select></label>
        <button name="action" value="link_project">Привязать проект</button></form>''' if options else ''
    return f'''<section class="group-panel"><h2>Начать работу</h2><form method="post" class="group-form">{hidden_token(token)}
        <label>Название проекта<input name="project_name" value="{escape(assignment["title"][:80], quote=True)}" required maxlength="80"></label>
        <button name="action" value="create_project">Создать проект по заданию</button></form>{link}
        <p>Учитель увидит название проекта. Доступ к работам появится после согласования сопровождения.</p></section>'''


def teacher_progress(group, assignment, rows, view, page):
    counts = Counter(row['status'] for row in rows)
    base = f'/groups/{group["id"]}/assignments/{assignment["id"]}'
    def filter_link(key, label, count):
        current = ' aria-current="page"' if key == view else ''
        href = base if key == 'all' else f'{base}?view={key}'
        return f'<a href="{href}"{current}>{label}: {count}</a>'

    groups = filter_link('all', 'Все', len(rows))
    groups += ''.join(filter_link(key, label, counts[key]) for key, label in PROGRESS.items())
    filtered = [row for row in rows if view == 'all' or row['status'] == view]
    start = (page - 1) * ROSTER_SIZE
    students = []
    for row in filtered[start:start + ROSTER_SIZE]:
        name = escape(row['project_name'] or 'Проект не выбран')
        if row['connected']:
            name = f'<a href="/project/{row["project_id"]}">{name}</a>'
        work = f' · <a href="/teacher/review?work={row["work"]}">Открыть сдачу</a>' if row['work'] else ''
        first = f' · Первая сдача: {escape(format_timestamp(row["first_at"]))}' if row['first_at'] else ''
        students.append(f'<li><strong>{escape(row["username"])}</strong> · {name} · {PROGRESS[row["status"]]}{work}{first}</li>')
    roster = '<ul>' + ''.join(students) + '</ul>' if students else '<p>Здесь пока нет учеников.</p>'
    return (f'<section class="group-panel"><h2>Прогресс класса</h2><nav class="group-actions" aria-label="Статус работы">'
            f'{groups}</nav>{roster}'
            + page_links(base, page, len(filtered) > start + ROSTER_SIZE, {'view': view}) + '</section>')


def render_assignment(conn, user, group, assignment, token, args, submitted=None):
    rows = progress_rows(conn, group, assignment, user)
    due = deadline_info(assignment['due_on'])
    body = STYLES + f'''<section class="group-panel"><h2>Цель</h2><p class="group-text">{escape(assignment["goal"])}</p>
        <p>Срок сдачи · {escape(due["display_date"])} (МСК)</p>'''
    if assignment['materials'] or assignment['material_url']:
        body += f'<h2>Материалы</h2><p class="group-text">{escape(assignment["materials"])}</p>'
        if safe_web_url(assignment['material_url']):
            body += f'<a href="{escape(assignment["material_url"], quote=True)}" rel="noopener noreferrer" target="_blank">Открыть материалы</a>'
    body += '</section>'
    if user['role'] == 'student':
        return body + student_assignment(conn, user, group, assignment, rows[0] if rows else None, token)
    view = args.get('view', 'all')
    view = view if view in PROGRESS else 'all'
    body += teacher_progress(group, assignment, rows, view, bounded_page(args.get('page', '1')))
    if not group['archived']:
        opened = ' open' if submitted is not None else ''
        body += (f'<details class="group-panel"{opened}><summary>Изменить задание</summary>'
                 + assignment_form(token, dict(assignment) if submitted is None else submitted, editing=True) + '</details>')
    return body
