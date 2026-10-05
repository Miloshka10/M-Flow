"""Профили навыков и добровольные межклассные команды проектов."""

import json
from html import escape


def initialize_teamwork(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS student_profiles (
            user_id INTEGER PRIMARY KEY REFERENCES users(id),
            class_name TEXT NOT NULL DEFAULT '',
            direction TEXT NOT NULL DEFAULT '',
            skills_json TEXT NOT NULL DEFAULT '[]',
            bio TEXT NOT NULL DEFAULT '',
            discoverable INTEGER NOT NULL DEFAULT 0 CHECK(discoverable IN (0,1))
        );
        CREATE TABLE IF NOT EXISTS team_invitations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL REFERENCES projects(id),
            sender_id INTEGER NOT NULL REFERENCES users(id),
            invitee_id INTEGER NOT NULL REFERENCES users(id),
            role_text TEXT NOT NULL DEFAULT '',
            message TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL CHECK(state IN ('pending','accepted','declined','cancelled')),
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, invitee_id)
        );
        CREATE INDEX IF NOT EXISTS idx_invitations_recipient ON team_invitations(invitee_id,state);
        CREATE TABLE IF NOT EXISTS team_member_roles (
            project_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            role_text TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(project_id,user_id),
            FOREIGN KEY(project_id,user_id) REFERENCES project_members(project_id,user_id) ON DELETE CASCADE
        );
    """)


def profile_for(conn, user_id):
    row = conn.execute("SELECT * FROM student_profiles WHERE user_id=?", (user_id,)).fetchone()
    return dict(row) if row else dict(user_id=user_id, class_name="", direction="", skills_json="[]", bio="", discoverable=0)


def save_profile(conn, user_id, form):
    class_name, direction, bio = (form.get(key, "").strip() for key in ("class_name", "direction", "bio"))
    if len(class_name) > 30 or len(direction) > 60 or len(bio) > 500:
        raise ValueError("Класс — до 30 символов, направление — до 60, описание — до 500.")
    raw = form.get("skills", "")
    if len(raw) > 600:
        raise ValueError("Список навыков слишком длинный.")
    skills = []
    for skill in raw.split(","):
        skill = " ".join(skill.split())
        if not skill:
            continue
        if len(skill) > 40:
            raise ValueError("Каждый навык должен быть не длиннее 40 символов.")
        if skill.casefold() not in {s.casefold() for s in skills}:
            skills.append(skill)
    if len(skills) > 12:
        raise ValueError("Укажите не больше 12 навыков через запятую.")
    discoverable = int(form.get("discoverable") == "1")
    if discoverable and (not class_name or not direction or not skills):
        raise ValueError("Для каталога заполните класс, направление и хотя бы один навык.")
    conn.execute("""INSERT INTO student_profiles(user_id,class_name,direction,skills_json,bio,discoverable)
        VALUES (?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET class_name=excluded.class_name,
        direction=excluded.direction, skills_json=excluded.skills_json, bio=excluded.bio,
        discoverable=excluded.discoverable""", (user_id, class_name, direction, json.dumps(skills, ensure_ascii=False), bio, discoverable))
    conn.commit()


def directory(conn, current_id, query="", direction="", class_name="", skill=""):
    rows = conn.execute("""SELECT p.*, u.username FROM student_profiles p JOIN users u ON u.id=p.user_id
        WHERE p.discoverable=1 AND u.role='student' AND u.id<>? ORDER BY u.username""", (current_id,)).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        skills = json.loads(item["skills_json"])
        haystack = " ".join([item["username"], item["class_name"], item["direction"], item["bio"], *skills]).casefold()
        if query.casefold() not in haystack or direction.casefold() not in item["direction"].casefold() or class_name.casefold() not in item["class_name"].casefold():
            continue
        if skill and not any(skill.casefold() in s.casefold() for s in skills):
            continue
        item["skills"] = skills
        result.append(item)
    return result


def invite_student(conn, sender_id, form):
    conn.execute("BEGIN IMMEDIATE")
    try:
        pid, uid = form.get("project_id", ""), form.get("student_id", "")
        project = conn.execute("SELECT * FROM projects WHERE id=? AND owner_id=?", (pid, sender_id)).fetchone()
        target = conn.execute("""SELECT u.id FROM users u JOIN student_profiles p ON p.user_id=u.id
            WHERE u.id=? AND u.role='student' AND p.discoverable=1""", (uid,)).fetchone()
        if project is None:
            raise ValueError("Приглашать можно только в проект, которым вы владеете.")
        if target is None or target["id"] == sender_id:
            raise ValueError("Ученик недоступен для приглашения в каталоге.")
        uid = target["id"]
        pid = project["id"]
        if project["owner_id"] == uid or conn.execute("SELECT 1 FROM project_members WHERE project_id=? AND user_id=?", (pid, uid)).fetchone():
            raise ValueError("Ученик уже состоит в этой команде.")
        existing = conn.execute("SELECT state FROM team_invitations WHERE project_id=? AND invitee_id=?", (pid, uid)).fetchone()
        if existing and existing["state"] == "pending":
            raise ValueError("Приглашение уже отправлено. Дождитесь ответа ученика.")
        role, message = form.get("role_text", "").strip(), form.get("message", "").strip()
        if len(role) > 100 or len(message) > 500:
            raise ValueError("Роль — до 100 символов, сообщение — до 500.")
        conn.execute("""INSERT INTO team_invitations(project_id,sender_id,invitee_id,role_text,message,state)
            VALUES (?,?,?,?,?,'pending') ON CONFLICT(project_id,invitee_id) DO UPDATE SET
            sender_id=excluded.sender_id, role_text=excluded.role_text, message=excluded.message,
            state='pending', updated_at=CURRENT_TIMESTAMP""", (pid, sender_id, uid, role, message))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def respond_to_invitation(conn, invitation_id, user_id, action):
    conn.execute("BEGIN IMMEDIATE")
    try:
        invitation = conn.execute("SELECT * FROM team_invitations WHERE id=? AND invitee_id=?", (invitation_id, user_id)).fetchone()
        if invitation is None:
            raise PermissionError("Это приглашение адресовано другому ученику.")
        if invitation["state"] != "pending" or action not in ("accept", "decline"):
            raise ValueError("Приглашение уже обработано или выбран неверный ответ.")
        owner = conn.execute("SELECT owner_id FROM projects WHERE id=?", (invitation["project_id"],)).fetchone()
        if owner is None or owner["owner_id"] != invitation["sender_id"]:
            raise ValueError("Приглашение устарело: владелец проекта изменился.")
        if action == "accept":
            conn.execute("INSERT OR IGNORE INTO project_members(project_id,user_id) VALUES (?,?)", (invitation["project_id"], user_id))
            conn.execute("""INSERT INTO team_member_roles(project_id,user_id,role_text) VALUES (?,?,?)
                ON CONFLICT(project_id,user_id) DO UPDATE SET role_text=excluded.role_text""", (invitation["project_id"], user_id, invitation["role_text"]))
        conn.execute("UPDATE team_invitations SET state=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                     ("accepted" if action == "accept" else "declined", invitation_id))
        conn.commit()
        return invitation["project_id"]
    except Exception:
        conn.rollback()
        raise


def change_team(conn, project_id, form):
    action = form.get("action")
    if action == "cancel":
        cur = conn.execute("UPDATE team_invitations SET state='cancelled',updated_at=CURRENT_TIMESTAMP WHERE id=? AND project_id=? AND state='pending'",
                           (form.get("invitation_id", ""), project_id))
        if not cur.rowcount:
            raise ValueError("Приглашение уже обработано или не относится к проекту.")
    elif action == "role":
        uid, role = form.get("student_id", ""), form.get("role_text", "").strip()
        member = conn.execute("""SELECT 1 FROM project_members m JOIN users u ON u.id=m.user_id
            WHERE m.project_id=? AND m.user_id=? AND u.role='student'""", (project_id, uid)).fetchone()
        if not member or len(role) > 100:
            raise ValueError("Выберите ученика команды и укажите роль до 100 символов.")
        conn.execute("""INSERT INTO team_member_roles(project_id,user_id,role_text) VALUES (?,?,?)
            ON CONFLICT(project_id,user_id) DO UPDATE SET role_text=excluded.role_text""", (project_id, uid, role))
    else:
        raise ValueError("Неизвестное действие с командой.")
    conn.commit()


def hidden_token(token):
    return f'<input type="hidden" name="csrf_token" value="{escape(token, quote=True)}">'


def render_profile(profile, token, form=None):
    values = form or {**profile, "skills": ", ".join(json.loads(profile["skills_json"]))}
    checked = form.get("discoverable") == "1" if form is not None else bool(profile["discoverable"])
    return f'''<section class="card"><h2>Учебный профиль</h2><p>Например, ученик ИТ-класса может отвечать за код, а ученик инженерного — за конструкцию.</p>
        <form method="post" class="team-profile-form">{hidden_token(token)}
        <label>Класс<input name="class_name" maxlength="30" placeholder="Например, 10А" value="{escape(values.get('class_name',''), quote=True)}"></label>
        <label>Направление<input name="direction" maxlength="60" placeholder="ИТ, инженерное, гуманитарное…" value="{escape(values.get('direction',''), quote=True)}"></label>
        <label>Навыки через запятую<input name="skills" maxlength="600" placeholder="Python, 3D-моделирование, презентации" value="{escape(values.get('skills',''), quote=True)}"></label>
        <p>До 12 навыков. Укажите то, с чем готовы помогать команде.</p>
        <label>О себе<textarea name="bio" maxlength="500" rows="3" placeholder="Что интересно делать в проекте">{escape(values.get('bio',''))}</textarea></label>
        <label class="team-checkbox"><input type="checkbox" name="discoverable" value="1" {'checked' if checked else ''}>Показывать мой профиль в каталоге участников</label>
        <p>Если включить каталог, вошедшие пользователи увидят логин, класс, направление, навыки и описание. При отключении профиль исчезнет из каталога; отправленные вам приглашения останутся.</p>
        <button>Сохранить профиль</button></form></section>'''


def render_directory(candidates, projects, invitations, token, filters, is_student):
    body = '<div class="team-toolbar"><a class="teacher-primary-link" href="/skills">Мои навыки</a></div>' if is_student else ''
    if invitations:
        body += '<section class="card"><h2>Приглашения в команды</h2>'
        for item in invitations:
            body += f'''<article class="team-invite"><h3>{escape(item['project_name'])}</h3><p>От {escape(item['sender_name'])} · Роль: {escape(item['role_text'] or 'Обсудите с командой')}</p>
                <p class="team-text">{escape(item['message'])}</p><form method="post" action="/teams/invitations/{item['id']}/respond" class="team-actions">{hidden_token(token)}
                <button name="action" value="accept">Принять приглашение</button><button class="secondary" name="action" value="decline">Отказаться</button></form></article>'''
        body += '</section>'
    body += f'''<form method="get" class="team-filters"><input name="q" aria-label="Поиск учеников" placeholder="Логин, навык или интерес" value="{escape(filters['q'], quote=True)}">
        <input name="skill" aria-label="Навык" placeholder="Навык: например Python" value="{escape(filters['skill'], quote=True)}">
        <input name="direction" aria-label="Направление" placeholder="Направление" value="{escape(filters['direction'], quote=True)}">
        <input name="class_name" aria-label="Класс" placeholder="Класс (необязательно)" value="{escape(filters['class_name'], quote=True)}">
        <button>Найти</button><a class="back" href="/teams">Сбросить</a></form><p>Найдено участников: {len(candidates)}. Участники могут быть из разных классов и направлений.</p><div class="team-directory">'''
    for item in candidates:
        tags = ''.join(f'<span class="team-skill">{escape(skill)}</span>' for skill in item['skills'])
        body += f'''<article class="card team-person"><h2>{escape(item['username'])}</h2><p>{escape(item['class_name'])} · {escape(item['direction'])}</p><div class="team-skills">{tags}</div><p class="team-text">{escape(item['bio'])}</p>'''
        if projects:
            options = ''.join(f'<option value="{project["id"]}">{escape(project["name"])}</option>' for project in projects)
            body += f'''<details><summary>Пригласить в проект</summary><form method="post" action="/teams/invite" class="team-invite-form">{hidden_token(token)}
                <input type="hidden" name="student_id" value="{item['user_id']}"><label>Проект<select name="project_id">{options}</select></label>
                <label>Роль в команде<input name="role_text" maxlength="100" placeholder="Например, разработчик"></label>
                <label>Сообщение<textarea name="message" maxlength="500" rows="2"></textarea></label><button>Отправить приглашение</button></form></details>'''
        body += '</article>'
    body += '</div>'
    if not candidates:
        body += '<section class="empty"><h3>Участники пока не найдены</h3><p>Измените фильтры или попросите учеников заполнить навыки и включить показ в каталоге.</p></section>'
    if not projects:
        body += '<p>Для приглашения участников создайте свой проект. Уже существующие проекты можно открыть с главной страницы.</p>'
    return body


def render_team(conn, project_id, owner, token):
    members = conn.execute("""SELECT u.id,u.username,u.role,p.class_name,p.direction,p.skills_json,p.discoverable,r.role_text
        FROM users u LEFT JOIN student_profiles p ON p.user_id=u.id
        LEFT JOIN team_member_roles r ON r.project_id=? AND r.user_id=u.id
        WHERE u.id IN (SELECT user_id FROM project_members WHERE project_id=?)
        OR u.id=(SELECT owner_id FROM projects WHERE id=?) ORDER BY u.role,u.username""", (project_id, project_id, project_id)).fetchall()
    body = '<p>Команда общая для проекта: классы и направления не ограничивают участие.</p><a class="teacher-primary-link" href="/teams">Подобрать участников по навыкам</a><div class="team-directory">'
    for member in members:
        body += f'<section class="card team-person"><h2>{escape(member["username"])}</h2><p>{"Учитель" if member["role"] == "teacher" else "Ученик"}</p>'
        if member['discoverable']:
            body += f'<p>{escape(member["class_name"])} · {escape(member["direction"])}</p><div class="team-skills">' + ''.join(f'<span class="team-skill">{escape(s)}</span>' for s in json.loads(member['skills_json'])) + '</div>'
        body += f'<p>Роль: {escape(member["role_text"] or "Пока не указана")}</p>'
        if owner and member['role'] == 'student' and conn.execute('SELECT 1 FROM project_members WHERE project_id=? AND user_id=?', (project_id, member['id'])).fetchone():
            body += f'''<form method="post" class="team-invite-form">{hidden_token(token)}<input type="hidden" name="action" value="role"><input type="hidden" name="student_id" value="{member['id']}">
                <label>Роль в команде<input name="role_text" maxlength="100" value="{escape(member['role_text'] or '',quote=True)}"></label><button class="secondary">Сохранить роль</button></form>'''
        body += '</section>'
    body += '</div>'
    if owner:
        pending = conn.execute("""SELECT i.*,u.username FROM team_invitations i JOIN users u ON u.id=i.invitee_id
            WHERE i.project_id=? AND i.state='pending' ORDER BY i.id DESC""", (project_id,)).fetchall()
        body += '<section class="card"><h2>Отправленные приглашения</h2>'
        for invitation in pending:
            body += f'''<article class="team-invite"><strong>{escape(invitation['username'])}</strong><p>{escape(invitation['role_text'])} · Ожидается ответ</p><form method="post">{hidden_token(token)}
                <input type="hidden" name="invitation_id" value="{invitation['id']}"><button class="secondary" name="action" value="cancel">Отменить приглашение</button></form></article>'''
        if not pending:
            body += '<p>Нет приглашений, ожидающих ответа.</p>'
        body += '<p>Удаление участников доступно на доске проекта.</p></section>'
    return body
