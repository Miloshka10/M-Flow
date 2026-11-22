"""Voluntary classroom rosters. Group membership never grants project access."""
from interface import icon
from navigation import stage_result_target
import hashlib
import re
import secrets
import time
from collections import defaultdict
from datetime import datetime, timezone
from html import escape
from urllib.parse import urlencode

from database import PostgresConnection, execute_schema_script, form_database_id, insert_id
from deadlines import format_timestamp
from grading import load_scores, summarize
from group_assignments import assignment_date, assignment_for, initialize_group_assignments, render_assignment, save_assignment
from project_stages import STAGES, stage_label, stage_needs_revision, get_stages
from project_rules import create_owned_project, has_project_teacher
from project_workspace import next_step
from video_defense import defense_for
from teacher_queue import queue_url, review_url, sent_date
from teacher_supervision import consented_connection
from teamwork import hidden_token

CODE_SECONDS = 7 * 86400
MAX_GROUPS = 50
MAX_MEMBERS = 300
MAX_STUDENT_GROUPS = 20
PAGE_SIZE = 30
CODE_ERROR = 'Код группы недоступен. Проверьте его или попросите учителя выдать новый.'
VIEWS = {'all': 'Все ученики', 'review': 'Ждут проверки', 'revision': 'Дорабатывают', 'unlinked': 'Нужно подключение',
         'no_project': 'Без проекта', 'stale': 'Работы без обновлений 14 дней'}
GROUP_CSS = '<link rel="stylesheet" href="/static/learning-groups.css?v=20261114-review">'


def initialize_groups(conn):
    execute_schema_script(conn, """
        CREATE TABLE IF NOT EXISTS learning_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER NOT NULL REFERENCES users(id),
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
            revision INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (CAST(CURRENT_TIMESTAMP AS TEXT))
        );
        CREATE INDEX IF NOT EXISTS idx_learning_groups_teacher ON learning_groups(teacher_id,archived,id);
        CREATE TABLE IF NOT EXISTS learning_group_members (
            group_id INTEGER NOT NULL REFERENCES learning_groups(id),
            student_id INTEGER NOT NULL REFERENCES users(id),
            project_id INTEGER REFERENCES projects(id),
            joined_at TEXT NOT NULL DEFAULT (CAST(CURRENT_TIMESTAMP AS TEXT)),
            PRIMARY KEY(group_id,student_id)
        );
        CREATE INDEX IF NOT EXISTS idx_learning_group_student ON learning_group_members(student_id,group_id);
        CREATE INDEX IF NOT EXISTS idx_learning_group_project ON learning_group_members(project_id) WHERE project_id IS NOT NULL;
        CREATE TABLE IF NOT EXISTS learning_group_invitations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL REFERENCES learning_groups(id),
            code_hash TEXT NOT NULL UNIQUE,
            expires INTEGER NOT NULL,
            revoked INTEGER NOT NULL DEFAULT 0 CHECK(revoked IN (0,1))
        );
        CREATE INDEX IF NOT EXISTS idx_learning_group_invite ON learning_group_invitations(group_id,revoked,expires);
    """)
    initialize_group_assignments(conn)


def _lock(conn, group_id=None, user_id=None):
    if isinstance(conn, PostgresConnection):
        if group_id is not None:
            conn.execute('SELECT id FROM learning_groups WHERE id=? FOR UPDATE', (group_id,))
        if user_id is not None:
            conn.execute('SELECT id FROM users WHERE id=? FOR UPDATE', (user_id,))
    else:
        conn.execute('BEGIN IMMEDIATE')


def group_for(conn, user, group_id, teacher_only=False):
    row = conn.execute('''SELECT g.*,u.username AS teacher_name FROM learning_groups g
        JOIN users u ON u.id=g.teacher_id AND u.role='teacher' WHERE g.id=? AND (
        g.teacher_id=? OR EXISTS (SELECT 1 FROM learning_group_members m
            WHERE m.group_id=g.id AND m.student_id=?))''', (group_id, user['id'], user['id'])).fetchone()
    if row is None or (teacher_only and (user['role'] != 'teacher' or row['teacher_id'] != user['id'])):
        raise PermissionError('Учебная группа недоступна.')
    return dict(row)


def _fields(form):
    name, description = form.get('name', '').strip(), form.get('description', '').strip()
    if not name or len(name) > 120 or len(description) > 600:
        raise ValueError('Укажите название до 120 символов и описание до 600 символов.')
    return name, description


def create_group(conn, user, form):
    if user['role'] != 'teacher':
        raise PermissionError('Создавать учебные группы может учитель.')
    name, description = _fields(form)
    try:
        _lock(conn, user_id=user['id'])
        if conn.execute('SELECT COUNT(*) FROM learning_groups WHERE teacher_id=?', (user['id'],)).fetchone()[0] >= MAX_GROUPS:
            raise ValueError('Достигнут предел: 50 учебных групп на учителя.')
        group_id = insert_id(conn, 'INSERT INTO learning_groups(teacher_id,name,description) VALUES(?,?,?)',
                             (user['id'], name, description))
        conn.commit()
        return group_id
    except Exception:
        conn.rollback()
        raise


def preview_code(conn, user, code):
    if user['role'] != 'student':
        raise PermissionError('Вступить по коду может ученик.')
    if not re.fullmatch(r'[A-Za-z0-9_-]{32}', code or ''):
        raise ValueError(CODE_ERROR)
    row = conn.execute('''SELECT g.*,u.username AS teacher_name,i.id AS invitation_id,i.expires FROM learning_group_invitations i
        JOIN learning_groups g ON g.id=i.group_id JOIN users u ON u.id=g.teacher_id
        WHERE i.code_hash=? AND i.revoked=0 AND i.expires>? AND g.archived=0 AND u.role='teacher' ''',
        (hashlib.sha256(code.encode()).hexdigest(), int(time.time()))).fetchone()
    if row is None:
        raise ValueError(CODE_ERROR)
    return dict(row)


def join_group(conn, user, code):
    # Discover the ID, then lock and recheck expiration/revocation inside the write.
    initial = preview_code(conn, user, code)
    try:
        _lock(conn, group_id=initial['id'], user_id=user['id'])
        group = preview_code(conn, user, code)
        if conn.execute('SELECT 1 FROM learning_group_members WHERE group_id=? AND student_id=?', (group['id'], user['id'])).fetchone():
            conn.commit()
            return group['id']
        if conn.execute('SELECT COUNT(*) FROM learning_group_members WHERE group_id=?', (group['id'],)).fetchone()[0] >= MAX_MEMBERS:
            raise ValueError('Группа заполнена: не больше 300 учеников.')
        if conn.execute('SELECT COUNT(*) FROM learning_group_members WHERE student_id=?', (user['id'],)).fetchone()[0] >= MAX_STUDENT_GROUPS:
            raise ValueError('Вы уже состоите в 20 учебных группах.')
        conn.execute('INSERT INTO learning_group_members(group_id,student_id) VALUES(?,?)', (group['id'], user['id']))
        conn.commit()
        return group['id']
    except Exception:
        conn.rollback()
        raise


def change_group(conn, user, group_id, form):
    action = form.get('action')
    code = None
    try:
        _lock(conn, group_id=group_id)
        group = group_for(conn, user, group_id)
        if action == 'leave':
            if user['role'] != 'student':
                raise PermissionError('Выход доступен ученику группы.')
            conn.execute('DELETE FROM learning_group_members WHERE group_id=? AND student_id=?', (group_id, user['id']))
        elif action == 'save_assignment':
            if user['role'] != 'teacher' or group['teacher_id'] != user['id'] or group['archived']:
                raise PermissionError('Публиковать задание может учитель действующей группы.')
            save_assignment(conn, group_id, form)
        elif action in ('select', 'propose', 'create_project'):
            if user['role'] != 'student' or group['archived']:
                raise PermissionError('Выбирать проект может ученик действующей группы.')
            member = conn.execute('''SELECT p.id AS current_project FROM learning_group_members m
                LEFT JOIN projects p ON p.id=m.project_id AND p.owner_id=m.student_id
                WHERE m.group_id=? AND m.student_id=?''', (group_id, user['id'])).fetchone()
            if member is None:
                raise PermissionError('Вы больше не состоите в группе.')
            if action == 'create_project':
                if member['current_project'] is not None:
                    raise ValueError('В группе уже выбран проект. Уберите привязку перед созданием нового.')
                pid = create_owned_project(conn, user['id'], form.get('name', ''))
            else:
                raw = form.get('project_id', '')
                pid = form_database_id(raw) if raw else None
            if action == 'propose' and pid is None:
                raise ValueError('Выберите собственный проект для сопровождения.')
            if pid is not None and action != 'create_project':
                if isinstance(conn, PostgresConnection):
                    conn.execute('SELECT id FROM projects WHERE id=? FOR UPDATE', (pid,))
                project = conn.execute('SELECT id FROM projects WHERE id=? AND owner_id=?', (pid, user['id'])).fetchone()
                if project is None:
                    raise PermissionError('Выбрать проект для группы может только его ученик-владелец.')
                if action == 'propose':
                    consented_connection(conn, user, pid, group['teacher_id'])
            conn.execute('UPDATE learning_group_members SET project_id=? WHERE group_id=? AND student_id=?', (pid, group_id, user['id']))
        else:
            group_for(conn, user, group_id, teacher_only=True)
            if form.get('revision') != str(group['revision']):
                raise ValueError('Группа изменилась. Обновите страницу перед действием.')
            if action == 'edit':
                name, description = _fields(form)
                conn.execute('UPDATE learning_groups SET name=?,description=?,revision=revision+1 WHERE id=?', (name, description, group_id))
            elif action in ('archive', 'restore'):
                conn.execute('UPDATE learning_groups SET archived=?,revision=revision+1 WHERE id=?', (int(action == 'archive'), group_id))
                conn.execute('UPDATE learning_group_invitations SET revoked=1 WHERE group_id=?', (group_id,))
            elif action in ('invite', 'revoke'):
                if group['archived']:
                    raise ValueError('Сначала восстановите группу из архива.')
                conn.execute('UPDATE learning_group_invitations SET revoked=1 WHERE group_id=?', (group_id,))
                if action == 'invite':
                    code = secrets.token_urlsafe(24)
                    conn.execute('INSERT INTO learning_group_invitations(group_id,code_hash,expires) VALUES(?,?,?)',
                                 (group_id, hashlib.sha256(code.encode()).hexdigest(), int(time.time()) + CODE_SECONDS))
                conn.execute('UPDATE learning_groups SET revision=revision+1 WHERE id=?', (group_id,))
            elif action == 'remove':
                sid = form_database_id(form.get('student_id'))
                if not conn.execute('DELETE FROM learning_group_members WHERE group_id=? AND student_id=?', (group_id, sid)).rowcount:
                    raise ValueError('Ученик уже вышел из группы. Обновите список.')
                conn.execute('UPDATE learning_groups SET revision=revision+1 WHERE id=?', (group_id,))
            else:
                raise ValueError('Выберите действие в актуальной форме.')
        conn.commit()
        return code
    except Exception:
        conn.rollback()
        raise


def own_groups(conn, user):
    return [dict(r) for r in conn.execute('''SELECT g.*,u.username AS teacher_name,p.id AS linked_project_id,
        a.title AS assignment_title,a.due_date AS assignment_due,
        (SELECT COUNT(*) FROM learning_group_members m WHERE m.group_id=g.id) AS members
        FROM learning_groups g JOIN users u ON u.id=g.teacher_id
        LEFT JOIN learning_group_members member ON member.group_id=g.id AND member.student_id=?
        LEFT JOIN projects p ON p.id=member.project_id AND p.owner_id=member.student_id
        LEFT JOIN learning_group_assignments a ON a.group_id=g.id
        WHERE g.teacher_id=? OR member.student_id IS NOT NULL
        ORDER BY g.archived,g.id DESC LIMIT 50''', (user['id'], user['id']))]


def project_assignment_groups(conn, user, project_id):
    if user['role'] == 'teacher':
        access = '''g.teacher_id=? AND EXISTS (
            SELECT 1 FROM project_members pm WHERE pm.project_id=p.id AND pm.user_id=g.teacher_id)'''
    else:
        access = 'p.owner_id=?'
    return [dict(row) for row in conn.execute(f'''SELECT g.id,g.name,a.title AS assignment_title,a.due_date AS assignment_due
        FROM learning_group_members m JOIN learning_groups g ON g.id=m.group_id AND g.archived=0
        JOIN projects p ON p.id=m.project_id AND p.owner_id=m.student_id
        JOIN learning_group_assignments a ON a.group_id=g.id
        WHERE p.id=? AND {access} ORDER BY a.due_date,g.id LIMIT 20''', (project_id, user['id']))]


def teacher_group_projects(conn, teacher_id):
    groups = defaultdict(set)
    for row in conn.execute('''SELECT g.id,m.project_id FROM learning_groups g
        JOIN learning_group_members m ON m.group_id=g.id
        JOIN projects p ON p.id=m.project_id AND p.owner_id=m.student_id
        JOIN project_members pm ON pm.project_id=p.id AND pm.user_id=g.teacher_id
        WHERE g.teacher_id=?''', (teacher_id,)):
        groups[row['id']].add(row['project_id'])
    return groups


def teacher_review_group(conn, teacher_id, project_id, raw_group_id):
    if not raw_group_id:
        return None
    try:
        group_id = form_database_id(raw_group_id)
    except ValueError:
        return None
    return conn.execute('''SELECT g.id,g.name,EXISTS (
        SELECT 1 FROM learning_group_members m JOIN projects p ON p.id=m.project_id AND p.owner_id=m.student_id
        WHERE m.group_id=g.id AND p.id=? AND EXISTS (SELECT 1 FROM project_members pm
            WHERE pm.project_id=p.id AND pm.user_id=g.teacher_id)
        ) AS contains_project FROM learning_groups g WHERE g.id=? AND g.teacher_id=?''',
        (project_id, group_id, teacher_id)).fetchone()


def roster(conn, user, group, args):
    """Bounded at 300 members, batched metadata only; no student work or notes."""
    teacher = user['role'] == 'teacher' and user['id'] == group['teacher_id']
    if not teacher:
        args = {}  # Student pages always show only their own current selection.
    view, query, raw_page = args.get('view', 'all'), args.get('q', '').strip()[:100], args.get('page', '1')
    if view not in VIEWS or not raw_page.isascii() or not raw_page.isdigit() or len(raw_page) > 6 or int(raw_page) < 1:
        raise ValueError('Некорректный фильтр группы. Сбросьте условия.')
    rows = [dict(r) for r in conn.execute('''SELECT m.student_id,m.project_id,u.username,p.name AS project_name,
        CASE WHEN p.id IS NOT NULL AND (p.owner_id=? OR EXISTS (SELECT 1 FROM project_members pm
            WHERE pm.project_id=p.id AND pm.user_id=?)) THEN 1 ELSE 0 END AS accessible
        FROM learning_group_members m JOIN users u ON u.id=m.student_id AND u.role='student'
        LEFT JOIN projects p ON p.id=m.project_id AND p.owner_id=m.student_id
        WHERE m.group_id=? AND (?=1 OR m.student_id=?) ORDER BY u.username,u.id LIMIT 300''',
        (group['teacher_id'], group['teacher_id'], group['id'], int(teacher), user['id']))]
    for row in rows:
        row['teacher_connected'] = bool(row['accessible'])
        if not teacher:
            # Students can always inspect their own selected project. The teacher
            # still receives metadata only for lawfully connected projects.
            row['accessible'] = int(bool(row['project_name']))
    ids = sorted({r['project_id'] for r in rows if r['accessible']})
    stages, defenses, grades, activity = defaultdict(dict), {}, {}, defaultdict(list)
    if ids:
        marks = ','.join('?' for _ in ids)
        for r in conn.execute(f'SELECT project_id,number,state,reviewed_by,updated_at FROM project_stages WHERE project_id IN ({marks})', ids):
            stages[r['project_id']][r['number']] = dict(r)
            activity[r['project_id']].append(r['updated_at'])
        for r in conn.execute(f'SELECT project_id,state,updated_at FROM project_defenses WHERE project_id IN ({marks})', ids):
            defenses[r['project_id']] = r['state']
            activity[r['project_id']].append(r['updated_at'])
        for r in conn.execute(f'''SELECT project_id,student_id,scores_json,state FROM project_assessments
            WHERE project_id IN ({marks}) AND teacher_id=? AND (?=1 OR (student_id=? AND state='published'))''', (*ids, group['teacher_id'], int(teacher), user['id'])):
            grades[(r['project_id'], r['student_id'])] = dict(r)
        for r in conn.execute(f'''SELECT t.project_id,MAX(tu.created_at) AS updated FROM task_updates tu
            JOIN tasks t ON t.id=tu.task_id WHERE t.project_id IN ({marks}) GROUP BY t.project_id''', ids):
            activity[r['project_id']].append(r['updated'])
    selected_ids = sorted({r['project_id'] for r in rows if r['project_name']})
    pending = set()
    if teacher and selected_ids:
        marks = ','.join('?' for _ in selected_ids)
        pending = {r['project_id'] for r in conn.execute(f'''SELECT project_id FROM supervision_requests
            WHERE project_id IN ({marks}) AND target_id=? AND owner_approved=1 AND state='pending' AND expires>?''',
            (*selected_ids, group['teacher_id'], int(time.time())))}
    for row in rows:
        pid = row['project_id']
        row.update(completed=0, pending=0, stage='', grade='', updated='', stale=False,
                   current_number=None, stage_review=False, revision_target='', proposed=pid in pending)
        if row['accessible']:
            stored = stages[pid]
            row['completed'] = sum(s['state'] == 'done' for s in stored.values())
            current = next((n for n, _ in STAGES if stored.get(n, {}).get('state') != 'done'), None)
            row['current_number'] = current
            row['stage_review'] = any(s['state'] == 'review' for s in stored.values())
            row['stage'] = 'Все пять этапов приняты' if current is None else f'Этап {current} · ' + stage_label(stored.get(current, {'state': 'todo', 'reviewed_by': None}))
            row['pending'] = sum(s['state'] == 'review' for s in stored.values()) + int(defenses.get(pid) == 'submitted')
            if current in stored and stage_needs_revision(stored[current]):
                row['revision_target'] = f'/project/{pid}/stages#stage-{current}-result'
            elif defenses.get(pid) == 'returned':
                row['revision_target'] = f'/project/{pid}/defense'
            grade = grades.get((pid, row['student_id']))
            if grade:
                try:
                    scores = load_scores(grade)
                    if not isinstance(scores, dict):
                        raise ValueError('Invalid stored scores')
                    total, mark = summarize(scores)
                    row['grade'] = f'{mark} · {total}/45' if mark is not None else 'Опубликована'
                except (ValueError, TypeError):
                    row['grade'] = 'Опубликована'
                if grade['state'] != 'published':
                    row['grade'] = 'Черновик оценки'
            dates = [sent_date(d) for d in activity[pid]]
            row['updated'] = max((d for d in dates if d), default=None)
            row['stale'] = bool(row['updated'] and (datetime.now(timezone.utc) - row['updated']).days >= 14)
    counts = dict(total=len(rows), review=sum(r['pending'] > 0 for r in rows),
                  revision=sum(bool(r['revision_target']) for r in rows),
                  no_project=sum(not r['project_name'] for r in rows),
                  unlinked=sum(bool(r['project_name']) and not r['accessible'] for r in rows))
    rows = [r for r in rows if query.casefold() in r['username'].casefold()
            and (view == 'all' or view == 'review' and r['pending'] > 0
                 or view == 'revision' and r['revision_target']
                 or view == 'no_project' and not r['project_name']
                 or view == 'unlinked' and r['project_name'] and not r['teacher_connected']
                 or view == 'stale' and r['stale'])]
    pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(int(raw_page), pages)
    return dict(rows=rows[(page-1)*PAGE_SIZE:page*PAGE_SIZE], counts=counts, filtered=len(rows),
                page=page, pages=pages, query=query, view=view, teacher=teacher)


def render_groups(groups, user, token, preview=None):
    body = GROUP_CSS + '<p class="groups-intro">Учебная группа объединяет класс. Проекты, команды и руководство сохраняют свои правила доступа.</p>'
    if user['role'] == 'teacher':
        body += f'''<section class="group-panel"><h2>Новая учебная группа</h2><form method="post" class="group-form">{hidden_token(token)}
            <input type="hidden" name="action" value="create"><label>Название группы<input name="name" maxlength="120" required placeholder="Например, 10А — индивидуальные проекты"></label>
            <label>Описание для учеников<textarea name="description" maxlength="600" rows="2"></textarea></label><button data-ui-icon="plus">Создать группу</button></form></section>'''
    else:
        body += f'''<section class="group-panel" id="group-code"><h2>Вступить в группу</h2><form method="post" class="group-form">{hidden_token(token)}
            <input type="hidden" name="action" value="preview"><label>Код группы от учителя<input name="code" autocomplete="off" maxlength="32" required></label><button data-ui-icon="review">Проверить код группы</button></form></section>'''
    if preview:
        group, code = preview
        body += f'''<section class="group-panel"><h2>{escape(group['name'])}</h2><p>Учитель: {escape(group['teacher_name'])}</p><p class="group-text">{escape(group['description'])}</p>
            <p>После вступления учитель увидит ваш логин. Вы сами выберете проект; доступ к работам требует отдельного согласования. Другие ученики не увидят ваш проект или оценки через группу.</p>
            <form method="post">{hidden_token(token)}<input type="hidden" name="action" value="join"><input type="hidden" name="code" value="{escape(code, quote=True)}"><button data-ui-icon="check">Подтвердить вступление</button></form></section>'''
    body += '<h2>Мои учебные группы</h2><div class="group-list">'
    for group in groups:
        assignment = (f'<p class="group-list-assignment">{escape(group["assignment_title"])} · {assignment_date(group["assignment_due"])}</p>'
                      if group.get('assignment_title') else '')
        body += f'''<a class="group-panel group-link" href="/groups/{group['id']}"><span>{'В архиве' if group['archived'] else 'Действующая группа'}</span><h3>{escape(group['name'])}</h3>
            {assignment}<p>Учитель: {escape(group['teacher_name'])}</p>{f'<p>Учеников: {group["members"]}</p>' if user['role'] == 'teacher' else ''}</a>'''
    if not groups:
        empty = 'У вас пока нет учебных групп.' if user['role'] == 'teacher' else 'Вы ещё не вступили в учебную группу.'
        body += f'<p>{empty}</p>'
    return body + '</div>'


def render_student_project(conn, user, group, row, token):
    archived = bool(group['archived'])
    selected = row['project_id'] if row.get('project_name') else None
    csrf = hidden_token(token)

    def create_form():
        return f'''<form method="post" class="group-form group-project-form">{csrf}
            <input type="hidden" name="action" value="create_project">
            <label>Название проекта<input name="name" maxlength="80" required></label>
            <button data-ui-icon="plus">Создать проект для группы</button></form>'''

    def select_form(projects):
        placeholder = 'Убрать проект из группы' if selected else 'Выберите проект'
        options = f'<option value="">{placeholder}</option>' + ''.join(
            f'<option value="{p["id"]}"{" selected" if p["id"] == selected else ""}>{escape(p["name"])}</option>'
            for p in projects)
        return f'''<form method="post" class="group-form group-project-form">{csrf}
            <input type="hidden" name="action" value="select">
            <label>Мой проект<select name="project_id"{"" if selected else " required"}>{options}</select></label>
            <button data-ui-icon="save">{"Сохранить выбор проекта" if selected else "Добавить проект в группу"}</button></form>'''

    projects = conn.execute('SELECT id,name FROM projects WHERE owner_id=? ORDER BY id DESC', (user['id'],)).fetchall() if not archived else []
    if selected is None:
        if archived:
            return '<p class="group-text">Проект для этой группы не выбран.</p>'
        return f'''<section class="group-panel group-student-project"><h2 id="group-project" tabindex="-1">Добавьте проект в группу</h2>
            <p>Учитель увидит название проекта. Для доступа к работе нужно согласовать руководство.</p>
            {select_form(projects) if projects else create_form()}
            {f'<details class="ds-guide"><summary>Создать новый проект</summary>{create_form()}</details>' if projects else ''}
            </section>'''

    hub_url = f'/supervision?project={selected}'
    project_url = f'/project/{selected}'
    states = get_stages(conn, selected)
    defense = defense_for(conn, selected)
    has_teacher = has_project_teacher(conn, selected)
    published = conn.execute("SELECT 1 FROM project_assessments WHERE project_id=? AND student_id=? AND state='published' LIMIT 1", (selected, user['id'])).fetchone() is not None
    pending = conn.execute("""SELECT kind FROM supervision_requests WHERE project_id=? AND owner_id=?
        AND target_id=? AND state='pending' AND expires>? LIMIT 1""",
        (selected, user['id'], group['teacher_id'], int(time.time()))).fetchone()
    step = next_step(states, 'student', True, has_teacher, defense, published)
    propose = ''
    if not archived and not row['teacher_connected'] and not pending:
        label = 'Предложить смену руководителя' if has_teacher else 'Отправить запрос учителю'
        propose = f'''<form method="post">{csrf}<input type="hidden" name="project_id" value="{selected}">
            <button name="action" value="propose"{' class="secondary"' if has_teacher else ''}>{label} {icon('arrow')}</button></form>'''

    target = stage_result_target(step['target'])
    if not target:
        target = project_url
    elif target == '/supervision':
        target = hub_url
    elif not target.startswith('/'):
        target = project_url + (target if target.startswith('#') else '/' + target)
    primary = f'<a class="btn" href="{escape(target, quote=True)}">{escape(step["action"] or "Открыть проект")} {icon("arrow")}</a>'
    project_link = f'<a href="{project_url}">Открыть проект {icon("arrow")}</a>' if target != project_url else ''
    title, description = escape(step['title']), escape(step['text'])
    if pending:
        title = 'Сопровождение ожидает согласования'
        description = 'Запрос у учителя группы. Пока можно работать над проектом.'
        if pending['kind'] == 'change':
            description += ' Для смены нужно также согласие действующих руководителей.'
        primary = f'<a class="btn" href="{hub_url}">Открыть согласование {icon("arrow")}</a>'
    elif step['key'] == 'teacher-missing' and propose:
        title = 'Согласуй сопровождение проекта'
        description = 'После подтверждения учитель сможет проверять работу. Черновик можно начать уже сейчас.'
        primary, propose = propose, ''

    grade = f'<a href="{project_url}/assessment">Оценка учителя группы: {escape(row["grade"])}</a>' if row['grade'] else ''
    change = f'<details class="ds-guide"><summary>Изменить проект в группе</summary>{select_form(projects)}</details>' if not archived else ''
    return f'''<section class="group-panel group-student-project" aria-label="Мой проект в группе">
        <span class="eyebrow">МОЙ ПРОЕКТ В ГРУППЕ</span><h2 id="group-project" tabindex="-1">{escape(row['project_name'])}</h2>
        <p class="group-project-progress">{row['completed']} из 5 приняты · {escape(row['stage'])}</p>
        <div class="group-project-step"><h3>{title}</h3><p>{description}</p>
        <div class="group-actions">{primary}{project_link}</div></div>
        {grade}{change}
        {f'<details class="ds-guide"><summary>Руководство в группе</summary><p>Для смены нужно согласие действующих руководителей.</p>{propose}</details>' if propose else ''}
        </section>'''


def render_group(conn, user, group, data, token, code=None, assignment_form=None):
    gid, revision = group['id'], group['revision']
    def form(action, content):
        return f'<form method="post">{hidden_token(token)}<input type="hidden" name="revision" value="{revision}"><input type="hidden" name="action" value="{action}">{content}</form>'
    body = GROUP_CSS + f'''<p class="groups-intro group-identity">Учитель: {escape(group['teacher_name'])}{' · В архиве' if group['archived'] else ''}</p><p class="group-text">{escape(group['description'])}</p>'''
    if group['archived']:
        body += '<p class="group-notice">Группа в архиве. Новые вступления и выбор проекта закрыты; существующие проекты и руководство сохранены.</p>'
    assignment = render_assignment(assignment_for(conn, gid), token, teacher=data['teacher'],
                                   archived=group['archived'], submitted=assignment_form)
    if not data['teacher']:
        body += assignment + render_student_project(conn, user, group, data['rows'][0] if data['rows'] else {}, token)
        return body + '<details class="group-panel"><summary>Выход из группы</summary><p>Проект, оценки и руководство сохранятся.</p>' + form('leave', '<button class="secondary" data-ui-icon="logout">Выйти из группы</button>') + '</details>'
    body += f'<a class="btn group-review-entry" href="{queue_url(dict(group=gid))}">Очередь работ группы {icon("arrow")}</a>'
    c = data['counts']
    stats = [('all', c['total']), ('no_project', c['no_project']), ('review', c['review']),
             ('revision', c['revision']), ('unlinked', c['unlinked'])]
    stat_links = []
    for view, count in stats:
        current = ' aria-current="true"' if data['view'] == view and not data['query'] else ''
        stat_links.append(f'<a href="/groups/{gid}?view={view}#group-roster"{current}>'
                          f'<strong>{count}</strong><span>{VIEWS[view]}</span></a>')
    body += f'<nav class="group-stats" aria-label="Фильтры по состоянию проектов">{"".join(stat_links)}</nav>'
    if assignment:
        body += (f'<details id="group-assignment-panel" class="group-panel" {"open" if assignment_form is not None or not c["total"] else ""}>'
                 f'<summary>Задание группы</summary>{assignment}</details>')
    body += f'<details class="group-panel" {"open" if code or not c["total"] else ""}><summary data-ui-icon="link">Приглашение класса</summary><p>Один код можно передать всему классу. Он действует 7 дней, показывается только при создании. Новый код отменяет предыдущий, но сохраняет состав группы.</p><div class="group-actions">'
    if not group['archived']:
        body += form('invite', '<button data-ui-icon="plus">Создать новый код группы</button>') + form('revoke', '<button class="secondary" data-ui-icon="close">Отозвать код</button>')
    body += '</div>'
    active = conn.execute('SELECT expires FROM learning_group_invitations WHERE group_id=? AND revoked=0 AND expires>? ORDER BY id DESC LIMIT 1', (gid, int(time.time()))).fetchone()
    if active:
        expiry = format_timestamp(datetime.fromtimestamp(active['expires'], timezone.utc).isoformat())
        body += f'<p>Код действует до {expiry}.</p>'
    else:
        body += '<p>Действующего кода нет.</p>'
    if code:
        body += f'<label class="group-code">Код группы<input readonly autocomplete="off" value="{escape(code, quote=True)}"></label><p role="status">Скопируйте код сейчас. Восстановить его позже нельзя; можно создать новый.</p>'
    body += '</details>'
    options = ''.join(f'<option value="{key}" {"selected" if key == data["view"] else ""}>{label}</option>' for key, label in VIEWS.items())
    body += f'''<form method="get" class="group-filters"><label>Поиск ученика<input name="q" maxlength="100" value="{escape(data['query'], quote=True)}"></label><label>Состояние учеников<select name="view">{options}</select></label><button data-ui-icon="filter">Показать</button><a href="/groups/{gid}">Сбросить</a></form>'''
    body += f'<section aria-label="Проекты учеников"><h2 id="group-roster" tabindex="-1">Ученики и проекты</h2><p>Показано {len(data["rows"])} из {data["filtered"]}</p><div class="group-table-wrap"><table class="group-table"><thead><tr>'
    columns = ('Ученик', 'Проект', 'Этапы', 'Проверка', 'Моя оценка', 'Последнее изменение', 'Действия')
    body += ''.join(f'<th scope="col">{label}</th>' for label in columns) + '</tr></thead><tbody>'
    for row in data['rows']:
        pid = row['project_id']
        project = escape(row['project_name']) if row['project_name'] else 'Проект не выбран'
        if row['accessible']:
            project = f'<a href="/project/{pid}">{project}</a>'
            stages = f'{row["completed"]} из 5 приняты<br>{escape(row["stage"])}'
            stage_target = f'/project/{pid}/stages' + (f'#stage-{row["current_number"]}-result' if row['current_number'] else '')
            if row['revision_target'] and (data['view'] == 'revision' or not row['pending']):
                review = f'<a class="group-review-link" href="{row["revision_target"]}">Посмотреть замечание</a>'
            elif row['pending']:
                work = f'stage-{row["current_number"]}' if row['stage_review'] and row['current_number'] else 'defense'
                review_target = review_url(pid, work, dict(group=gid))
                review = f'<a class="group-review-link" href="{review_target}">Ожидают решения: {row["pending"]}</a>'
            else:
                review = 'Нет ожидающих проверок'
            actions = f'<a href="{stage_target}">Открыть этапы {icon('arrow')}</a>'
            grade = row['grade'] or 'Не опубликована'
            grade_target = f'/project/{pid}/assessment' + f'?student_id={row["student_id"]}'
            grade = f'<a href="{grade_target}">{grade}</a>'
            updated = format_timestamp(row['updated'].isoformat()) if row['updated'] else 'Нет данных'
            if row['stale']:
                updated += '<br><span class="group-late">Без обновлений 14 дней</span>'
        else:
            stages, grade, updated = '—', '—', '—'
            review = 'Нужно выбрать проект' if not row['project_name'] else 'Ожидается согласование' if row['proposed'] else 'Работы пока недоступны'
            if row['proposed']:
                review = f'<a class="group-review-link" href="/supervision">Рассмотреть подключение {icon("arrow")}</a>'
            actions = '—'
        actions += form('remove', f'<input type="hidden" name="student_id" value="{row["student_id"]}"><button class="secondary" data-ui-icon="trash">Убрать из группы</button>')
        cells = (escape(row['username']), project, stages, review, grade, updated, actions)
        body += '<tr>' + ''.join(f'<td data-label="{label}"><div class="group-cell">{cell}</div></td>' for label, cell in zip(columns, cells)) + '</tr>'
    body += '</tbody></table></div>'
    if not data['rows']:
        empty = 'По этим условиям учеников нет.' if c['total'] else 'Пригласите учеников по коду группы.'
        body += f'<p>{empty}</p>'
    body += '<nav class="group-actions" aria-label="Страницы группы">'
    for page, label in ((data['page']-1, 'Предыдущая страница'), (data['page']+1, 'Следующая страница')):
        if 1 <= page <= data['pages']:
            query = urlencode(dict(page=page, q=data['query'], view=data['view']))
            body += f'<a href="/groups/{gid}?{escape(query, quote=True)}">{icon("arrow-left" if page < data["page"] else "arrow")} {label}</a>'
    body += '</nav></section><p class="groups-intro">Группа показывает выбранные проекты. Членство в группе не открывает работы, личные оценки других учеников или черновики других учителей. Последнее изменение учитывает работы и решения учителя.</p>'
    body += f'''<details class="group-panel"><summary data-ui-icon="settings">Настройки группы</summary><form method="post" class="group-form">{hidden_token(token)}<input type="hidden" name="revision" value="{revision}"><input type="hidden" name="action" value="edit">
        <label>Название группы<input name="name" maxlength="120" required value="{escape(group['name'], quote=True)}"></label><label>Описание для учеников<textarea name="description" maxlength="600" rows="3">{escape(group['description'])}</textarea></label><button data-ui-icon="save">Сохранить настройки группы</button></form>
        {form('restore' if group['archived'] else 'archive', '<button class="secondary" data-ui-icon="archive">' + ('Восстановить группу' if group['archived'] else 'Отправить группу в архив') + '</button>')}</details>'''
    return body
