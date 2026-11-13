"""Shared workflow rules, independent of the project's creator role."""

from database import insert_id


def create_owned_project(conn, owner_id, name):
    """Create the project and its owner membership within the caller's transaction."""
    name = name.strip()
    if not name:
        raise ValueError('Введите название проекта.')
    if len(name) > 80:
        raise ValueError('Название проекта должно быть не длиннее 80 символов.')
    project_id = insert_id(conn, 'INSERT INTO projects(name,owner_id) VALUES(?,?)', (name, owner_id))
    conn.execute('INSERT INTO project_members(project_id,user_id) VALUES(?,?)', (project_id, owner_id))
    return project_id


def has_project_teacher(conn, project_id):
    return conn.execute("""SELECT 1 FROM users u WHERE u.role='teacher' AND (
        u.id=(SELECT owner_id FROM projects WHERE id=?) OR EXISTS (
            SELECT 1 FROM project_members m WHERE m.project_id=? AND m.user_id=u.id
        )) LIMIT 1""", (project_id, project_id)).fetchone() is not None


TEACHER_REQUIRED = "В проекте нет руководителя. Владелец принимает приглашение учителя в разделе «Руководитель проекта», затем учитель подтверждает сопровождение. Черновики доступны; отправка на проверку — после подключения."
