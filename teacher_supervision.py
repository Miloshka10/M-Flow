"""Consent-based teacher onboarding. Membership remains the source of project access."""
import hashlib
import json
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from html import escape

from database import PostgresConnection, execute_schema_script, begin_project_write, form_database_id, insert_id
from notifications import notify_project
from teamwork import hidden_token

CODE_SECONDS = 86400
REQUEST_SECONDS = 7 * 86400
CODE_ERROR = 'Приглашение недоступно: проверьте код или попросите учителя выдать новый.'


def initialize_supervision(conn):
    execute_schema_script(conn, """
        CREATE TABLE IF NOT EXISTS teacher_invitations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER NOT NULL REFERENCES users(id),
            code_hash TEXT NOT NULL UNIQUE,
            state TEXT NOT NULL CHECK(state IN ('pending','used','cancelled','declined')),
            expires INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_teacher_invitations_owner ON teacher_invitations(teacher_id,state,expires);
        CREATE TABLE IF NOT EXISTS supervision_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL REFERENCES projects(id),
            owner_id INTEGER NOT NULL REFERENCES users(id),
            target_id INTEGER REFERENCES users(id),
            invitation_id INTEGER UNIQUE REFERENCES teacher_invitations(id),
            kind TEXT NOT NULL CHECK(kind IN ('connect','change','end')),
            state TEXT NOT NULL CHECK(state IN ('pending','completed','declined','cancelled')),
            previous_json TEXT NOT NULL,
            approvals_json TEXT NOT NULL DEFAULT '[]',
            owner_approved INTEGER NOT NULL CHECK(owner_approved IN (0,1)),
            revision INTEGER NOT NULL DEFAULT 0,
            expires INTEGER NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_supervision_pending ON supervision_requests(project_id) WHERE state='pending';
        CREATE INDEX IF NOT EXISTS idx_supervision_target ON supervision_requests(target_id,state);
    """)


def teacher_ids(conn, project_id):
    return [r['id'] for r in conn.execute("""SELECT u.id FROM users u WHERE u.role='teacher' AND (
        u.id=(SELECT owner_id FROM projects WHERE id=?) OR EXISTS (
        SELECT 1 FROM project_members m WHERE m.project_id=? AND m.user_id=u.id)) ORDER BY u.id""", (project_id, project_id))]


def participant(conn, project_id, user_id):
    return conn.execute('''SELECT 1 FROM projects p WHERE p.id=? AND (p.owner_id=? OR EXISTS (
        SELECT 1 FROM project_members m WHERE m.project_id=p.id AND m.user_id=?))''', (project_id, user_id, user_id)).fetchone() is not None


def has_pending_review(conn, project_id):
    return conn.execute("""SELECT 1 FROM project_stages WHERE project_id=? AND state='review'
        UNION ALL SELECT 1 FROM project_defenses WHERE project_id=? AND state='submitted' LIMIT 1""", (project_id, project_id)).fetchone() is not None


def _invite(conn, code, lock=False):
    if not re.fullmatch(r'[A-Za-z0-9_-]{32}', code or ''):
        raise ValueError(CODE_ERROR)
    suffix = ' FOR UPDATE OF i' if lock and isinstance(conn, PostgresConnection) else ''
    row = conn.execute('''SELECT i.*,u.username AS teacher_name FROM teacher_invitations i
        JOIN users u ON u.id=i.teacher_id WHERE i.code_hash=? AND u.role='teacher' ''' + suffix,
        (hashlib.sha256(code.encode()).hexdigest(),)).fetchone()
    if row is None or row['state'] != 'pending' or row['expires'] <= int(time.time()):
        raise ValueError(CODE_ERROR)
    return row


def create_invite(conn, user):
    if user['role'] != 'teacher':
        raise PermissionError('Приглашения создаёт учитель.')
    try:
        if isinstance(conn, PostgresConnection):
            conn.execute('SELECT id FROM users WHERE id=? FOR UPDATE', (user['id'],))
        else:
            conn.execute('BEGIN IMMEDIATE')
        if conn.execute("SELECT COUNT(*) FROM teacher_invitations WHERE teacher_id=? AND state='pending' AND expires>?", (user['id'], int(time.time()))).fetchone()[0] >= 20:
            raise ValueError('Уже есть 20 действующих приглашений. Отзовите ненужные или дождитесь окончания их срока.')
        code = secrets.token_urlsafe(24)
        insert_id(conn, "INSERT INTO teacher_invitations(teacher_id,code_hash,state,expires) VALUES(?,?,'pending',?)",
                  (user['id'], hashlib.sha256(code.encode()).hexdigest(), int(time.time()) + CODE_SECONDS))
        conn.commit()
        return code
    except Exception:
        conn.rollback()
        raise


def _project(conn, project_id):
    row = conn.execute("SELECT p.*,u.role AS owner_role FROM projects p JOIN users u ON u.id=p.owner_id WHERE p.id=?", (project_id,)).fetchone()
    if row is None or row['owner_role'] != 'student':
        raise PermissionError('Подключение доступно для проекта ученика-владельца. Старый проект сначала нужно передать ученику.')
    return row


def _new_request(conn, project, target, invitation=None, owner_approved=1, approvals=()):
    now = int(time.time())
    conn.execute("UPDATE supervision_requests SET state='cancelled',revision=revision+1 WHERE project_id=? AND state='pending' AND expires<=?", (project['id'], now))
    if conn.execute("SELECT 1 FROM supervision_requests WHERE project_id=? AND state='pending'", (project['id'],)).fetchone():
        raise ValueError('В проекте уже есть запрос. Завершите или отмените его перед новым подключением.')
    previous = teacher_ids(conn, project['id'])
    if target in previous:
        raise ValueError('Этот учитель уже руководит проектом.')
    if target is None and not previous:
        raise ValueError('В проекте нет действующего руководителя.')
    request_id = insert_id(conn, '''INSERT INTO supervision_requests
        (project_id,owner_id,target_id,invitation_id,kind,state,previous_json,approvals_json,owner_approved,expires)
        VALUES(?,?,?,?,?,'pending',?,?,?,?)''', (project['id'], project['owner_id'], target, invitation,
        'end' if target is None else 'change' if previous else 'connect', json.dumps(previous), json.dumps(list(approvals)), owner_approved, now + REQUEST_SECONDS))
    notify_project(conn, project['id'], 'student', f'supervision:{request_id}:created',
                   'Запрос по руководителю проекта', 'Подключение или смена требуют согласований. Пока доступ не изменён.', f'/project/{project["id"]}')
    notify_project(conn, project['id'], 'teacher', f'supervision:{request_id}:created',
                   'Запрос по руководителю проекта', 'Откройте раздел руководства для подтверждения или отказа.', f'/project/{project["id"]}')
    return request_id


def request_connection(conn, user, code, project_id):
    if user['role'] != 'student':
        raise PermissionError('Приглашение принимает ученик-владелец проекта.')
    begin_project_write(conn, project_id)
    try:
        project = _project(conn, project_id)
        if project['owner_id'] != user['id']:
            raise PermissionError('Подтвердить подключение может только владелец этого проекта, не другой участник команды.')
        invite = _invite(conn, code, lock=True)
        request_id = _new_request(conn, project, invite['teacher_id'], invite['id'])
        conn.execute("UPDATE teacher_invitations SET state='used' WHERE id=?", (invite['id'],))
        conn.commit()
        return request_id
    except Exception:
        conn.rollback()
        raise


def consented_connection(conn, user, project_id, teacher_id):
    """Caller holds the project write lock and a verified classroom membership.

    Reuse the same request/approval rules; this does not grant project membership.
    The caller commits the group selection and request together.
    """
    if user['role'] != 'student':
        raise PermissionError('Проект предлагает ученик-владелец.')
    project = _project(conn, project_id)
    if project['owner_id'] != user['id']:
        raise PermissionError('Подтвердить проект может только его владелец.')
    if conn.execute("SELECT 1 FROM users WHERE id=? AND role='teacher'", (teacher_id,)).fetchone() is None:
        raise PermissionError('Учитель недоступен.')
    return _new_request(conn, project, teacher_id)


def invitation_action(conn, user, form):
    try:
        if form.get('action') == 'decline':
            if user['role'] != 'student':
                raise PermissionError('Отказаться от приглашения может ученик.')
            if not isinstance(conn, PostgresConnection):
                conn.execute('BEGIN IMMEDIATE')
            row = _invite(conn, form.get('code', '').strip(), lock=True)
            conn.execute("UPDATE teacher_invitations SET state='declined' WHERE id=?", (row['id'],))
        else:
            if user['role'] != 'teacher':
                raise PermissionError('Отзывать приглашение может его учитель.')
            cur = conn.execute("UPDATE teacher_invitations SET state='cancelled' WHERE id=? AND teacher_id=? AND state='pending'",
                               (form_database_id(form.get('invitation_id')), user['id']))
            if not cur.rowcount:
                raise PermissionError('Приглашение недоступно или уже обработано.')
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def request_end(conn, user, project_id):
    begin_project_write(conn, project_id)
    try:
        project = _project(conn, project_id)
        owner = user['role'] == 'student' and project['owner_id'] == user['id']
        teachers = teacher_ids(conn, project_id)
        if not owner and not (user['role'] == 'teacher' and user['id'] in teachers):
            raise PermissionError('Запрос доступен владельцу или действующему учителю проекта.')
        _new_request(conn, project, None, owner_approved=int(owner), approvals=() if owner else (user['id'],))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def respond_request(conn, user, form):
    request_id = form_database_id(form.get('request_id'))
    initial = conn.execute('SELECT project_id FROM supervision_requests WHERE id=?', (request_id,)).fetchone()
    if initial is None:
        raise PermissionError('Запрос недоступен.')
    begin_project_write(conn, initial['project_id'])
    try:
        row = conn.execute('SELECT * FROM supervision_requests WHERE id=?', (request_id,)).fetchone()
        project = _project(conn, row['project_id'])
        previous = json.loads(row['previous_json'])
        current = teacher_ids(conn, row['project_id'])
        owner = user['role'] == 'student' and user['id'] == project['owner_id']
        teacher = user['role'] == 'teacher' and (user['id'] in current or (user['id'] == row['target_id'] and row['owner_approved']))
        if row['owner_id'] != project['owner_id'] or not (owner or teacher):
            raise PermissionError('Запрос недоступен. Его может подтвердить только владелец или участвующий в согласовании учитель.')
        if row['state'] != 'pending' or row['expires'] <= int(time.time()):
            raise ValueError('Запрос завершён или истёк. Обновите страницу; доступ повторно не меняется.')
        if form.get('revision') != str(row['revision']) or current != previous:
            raise ValueError('Запрос или состав руководителей изменился. Обновите страницу перед решением.')
        action = form.get('action')
        if action in ('cancel', 'reject'):
            if action == 'cancel' and not owner:
                raise PermissionError('Отменяет запрос владелец; учитель может отказаться.')
            conn.execute('UPDATE supervision_requests SET state=?,revision=revision+1 WHERE id=?', ('cancelled' if action == 'cancel' else 'declined', request_id))
            notify_project(conn, row['project_id'], 'student', f'supervision:{request_id}:closed', 'Запрос по руководителю закрыт', 'Доступ и действующий руководитель не изменены.', f'/project/{row["project_id"]}')
            conn.commit()
            return 'Запрос закрыт. Доступ не изменён.'
        if action != 'approve':
            raise ValueError('Выберите подтверждение или отказ.')
        approvals = set(json.loads(row['approvals_json']))
        owner_approved = row['owner_approved'] or owner
        if teacher:
            approvals.add(user['id'])
        required = set(previous) | ({row['target_id']} if row['target_id'] is not None else set())
        complete = owner_approved and required.issubset(approvals)
        if not complete and sorted(approvals) == json.loads(row['approvals_json']) and int(owner_approved) == row['owner_approved']:
            conn.commit()
            return 'Ваше согласие уже получено. Дождитесь остальных участников.'
        if complete:
            # Initial onboarding may recover legacy orphaned submissions. Only
            # replacement/end must keep existing reviewers until their work ends.
            if previous and has_pending_review(conn, row['project_id']):
                raise ValueError('Сначала завершите проверку отправленных этапов и видеозащиты. Затем подтвердите запрос снова.')
            if row['target_id'] is not None:
                target = conn.execute("SELECT 1 FROM users WHERE id=? AND role='teacher'", (row['target_id'],)).fetchone()
                if target is None:
                    raise ValueError('Новый учитель недоступен. Отмените запрос.')
                conn.execute('INSERT INTO project_members(project_id,user_id) VALUES(?,?) ON CONFLICT(project_id,user_id) DO NOTHING', (row['project_id'], row['target_id']))
            for old_id in previous:
                conn.execute('DELETE FROM team_member_roles WHERE project_id=? AND user_id=?', (row['project_id'], old_id))
                conn.execute('DELETE FROM project_members WHERE project_id=? AND user_id=?', (row['project_id'], old_id))
            for role in ('student', 'teacher'):
                notify_project(conn, row['project_id'], role, f'supervision:{request_id}:completed',
                               'Руководство проектом обновлено', 'Согласование завершено. Решения и оценки прежних учителей сохранены.', f'/project/{row["project_id"]}')
        conn.execute('''UPDATE supervision_requests SET owner_approved=?,approvals_json=?,state=?,revision=revision+1 WHERE id=?''',
                     (int(owner_approved), json.dumps(sorted(approvals)), 'completed' if complete else 'pending', request_id))
        conn.commit()
        return 'Согласование завершено. Руководство обновлено.' if complete else 'Согласие сохранено. Ожидаются остальные участники согласования.'
    except Exception:
        conn.rollback()
        raise


def preview_invite(conn, code):
    row = _invite(conn, code)
    return row['teacher_name']


def render_hub(conn, user, token, before=None, preview=None, code=None):
    now = int(time.time())
    teacher = user['role'] == 'teacher'
    body = '<p>Проект создаёт ученик. Учитель подключается только по согласованию с владельцем конкретного проекта. Существующие связи сохранены.</p>'
    if teacher:
        body += f'<section class="card"><h2>Пригласить ученика</h2><form method="post">{hidden_token(token)}<button name="action" value="create">Создать одноразовый код</button></form><p>Код действует 24 часа и показывается один раз. Передайте его ученику лично; не публикуйте в общем доступе.</p>'
        if code:
            body += f'<p role="status">Код создан. Скопируйте его сейчас:</p><label>Код приглашения<input readonly autocomplete="off" value="{escape(code, quote=True)}"></label>'
        invitations = conn.execute("SELECT id,expires FROM teacher_invitations WHERE teacher_id=? AND state='pending' AND expires>? ORDER BY id DESC LIMIT 20", (user['id'], now)).fetchall()
        for invitation in invitations:
            expires = datetime.fromtimestamp(invitation['expires'], timezone(timedelta(hours=3))).strftime('%d.%m.%Y %H:%M МСК')
            body += f'<form method="post" class="team-actions">{hidden_token(token)}<input type="hidden" name="invitation_id" value="{invitation["id"]}"><span>Приглашение №{invitation["id"]} · до {expires}</span><button class="secondary" name="action" value="revoke">Отозвать</button></form>'
        body += '</section>'
    else:
        body += f'<section class="card"><h2>Приглашение учителя</h2><form method="post" class="team-profile-form">{hidden_token(token)}<label>Код от учителя<input name="code" maxlength="32" autocomplete="off" required></label><button name="action" value="preview">Проверить приглашение</button></form>'
        if preview:
            projects = conn.execute('SELECT id,name FROM projects WHERE owner_id=? ORDER BY id DESC', (user['id'],)).fetchall()
            options = ''.join(f'<option value="{p["id"]}">{escape(p["name"])}</option>' for p in projects)
            body += f'<h3>Учитель: {escape(preview[0])}</h3><p>Учитель увидит только выбранный проект и ваш логин после подтверждения. Если руководитель уже есть, будет создан запрос на смену, а не добавлен второй.</p><form method="post" class="team-profile-form">{hidden_token(token)}<input type="hidden" name="code" value="{escape(preview[1], quote=True)}">'
            if projects:
                body += f'<label>Мой проект<select name="project_id">{options}</select></label><button name="action" value="request">Подтвердить проект</button> '
            else:
                body += '<p>Сначала создайте свой проект. Подтверждать подключение в чужую команду может только её владелец.</p>'
            body += '<button class="secondary" name="action" value="decline">Отказаться</button></form>'
        body += '</section>'
    access = "(p.owner_id=? OR r.target_id=?)" if not teacher else "((r.target_id=? AND r.owner_approved=1) OR EXISTS(SELECT 1 FROM project_members m WHERE m.project_id=p.id AND m.user_id=?))"
    rows = conn.execute(f'''SELECT r.*,p.name,owner.username AS owner_name,target.username AS target_name FROM supervision_requests r
        JOIN projects p ON p.id=r.project_id JOIN users owner ON owner.id=p.owner_id LEFT JOIN users target ON target.id=r.target_id
        WHERE r.state='pending' AND r.expires>? AND r.owner_id=p.owner_id AND {access}
        {'AND r.id<?' if before else ''} ORDER BY r.id DESC LIMIT 31''',
        (now, user['id'], user['id'], before) if before else (now, user['id'], user['id'])).fetchall()
    body += '<section class="card"><h2>Согласования</h2><p>При смене или завершении нужны согласия владельца, всех текущих учителей и нового руководителя (при смене). Пока согласования не завершены, доступ не меняется. Если учитель недоступен или есть спор, решение необходимо согласовать вне сайта; обхода согласия и административной роли здесь нет.</p>'
    owned = conn.execute("""SELECT p.id,p.name FROM projects p WHERE p.owner_id=? AND EXISTS (
        SELECT 1 FROM project_members m JOIN users u ON u.id=m.user_id WHERE m.project_id=p.id AND u.role='teacher') ORDER BY p.id DESC""", (user['id'],)).fetchall() if not teacher else conn.execute("""SELECT p.id,p.name FROM projects p JOIN users owner ON owner.id=p.owner_id
        WHERE owner.role='student' AND EXISTS(SELECT 1 FROM project_members m WHERE m.project_id=p.id AND m.user_id=?) ORDER BY p.id DESC""", (user['id'],)).fetchall()
    if owned:
        options = ''.join(f'<option value="{p["id"]}">{escape(p["name"])}</option>' for p in owned)
        body += f'<details><summary>Запросить завершение сопровождения</summary><form method="post" class="team-profile-form">{hidden_token(token)}<label>Проект<select name="project_id">{options}</select></label><button class="secondary" name="action" value="end">Создать запрос завершения</button></form><p>Окончание требует всех согласий. Для смены примите код нового учителя вместо завершения.</p></details>'
    if not rows:
        body += '<p>Ожидающих согласований нет. Для нового подключения передайте или примите код учителя.</p>'
    for row in rows[:30]:
        approvals = json.loads(row['approvals_json'])
        required = json.loads(row['previous_json']) + ([row['target_id']] if row['target_id'] is not None else [])
        label = {'connect':'Подключение руководителя','change':'Смена руководителя','end':'Завершение сопровождения'}[row['kind']]
        approved = user['id'] in approvals if teacher else row['owner_approved']
        body += f'<article class="team-invite"><h3>{label} · {escape(row["name"])}</h3><p>Владелец: {escape(row["owner_name"])} · Новый учитель: {escape(row["target_name"] or "не назначается")}</p><p>Согласие владельца: {"получено" if row["owner_approved"] else "ожидается"}. Согласия учителей: {len(approvals)} из {len(required)}.</p>'
        if approved:
            body += '<p>Ваше согласие получено. Ожидаются остальные участники согласования.</p>'
        body += f'<form method="post" class="team-actions">{hidden_token(token)}<input type="hidden" name="request_id" value="{row["id"]}"><input type="hidden" name="revision" value="{row["revision"]}"><button name="action" value="approve" {"disabled" if approved else ""}>Подтвердить</button><button class="secondary" name="action" value="reject">Отказаться</button>'
        if not teacher:
            body += '<button class="secondary" name="action" value="cancel">Отменить запрос</button>'
        body += '</form></article>'
    if len(rows) > 30:
        body += f'<a href="/supervision?before={rows[29]["id"]}">Следующие согласования →</a>'
    return body + '</section>'
