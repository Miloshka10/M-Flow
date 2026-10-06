"""Shared workflow rules, independent of the project's creator role."""


def has_project_teacher(conn, project_id):
    return conn.execute("""SELECT 1 FROM users u WHERE u.role='teacher' AND (
        u.id=(SELECT owner_id FROM projects WHERE id=?) OR EXISTS (
            SELECT 1 FROM project_members m WHERE m.project_id=? AND m.user_id=u.id
        )) LIMIT 1""", (project_id, project_id)).fetchone() is not None


TEACHER_REQUIRED = "В проекте нет учителя. Владелец должен добавить учителя по логину на доске проекта. Черновик можно сохранить, отправка на проверку станет доступна после добавления учителя."
