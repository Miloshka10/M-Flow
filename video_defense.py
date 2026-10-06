"""Запись защиты проекта по ссылке: черновик, проверка, доработка."""

from html import escape
from urllib.parse import urlsplit
from teamwork import hidden_token
from project_rules import has_project_teacher, TEACHER_REQUIRED

DEFENSE_STATES = {"draft": "Черновик", "submitted": "На проверке", "accepted": "Защита принята", "returned": "Нужна доработка"}


def initialize_defenses(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS project_defenses (
        project_id INTEGER PRIMARY KEY REFERENCES projects(id),
        video_url TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        state TEXT NOT NULL CHECK(state IN ('draft','submitted','accepted','returned')),
        teacher_comment TEXT NOT NULL DEFAULT '',
        author_id INTEGER REFERENCES users(id),
        reviewer_id INTEGER REFERENCES users(id),
        revision INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")


def defense_for(conn, project_id):
    row = conn.execute("""SELECT d.*,a.username AS author_name,r.username AS reviewer_name
        FROM project_defenses d LEFT JOIN users a ON a.id=d.author_id LEFT JOIN users r ON r.id=d.reviewer_id
        WHERE project_id=?""", (project_id,)).fetchone()
    return dict(row) if row else dict(project_id=project_id, video_url="", description="", state="draft", teacher_comment="",
                                     author_id=None, reviewer_id=None, author_name=None, reviewer_name=None, revision=0, updated_at="")


def change_defense(conn, project_id, user, form):
    conn.execute("BEGIN IMMEDIATE")
    try:
        current = defense_for(conn, project_id)
        if form.get("revision") != str(current["revision"]):
            raise ValueError("Запись защиты уже изменена другим участником. Обновите страницу.")
        action = form.get("action")
        if user["role"] == "student":
            if action not in ("save", "submit") or current["state"] not in ("draft", "returned"):
                raise ValueError("Запись можно менять только в черновике или после возврата учителем.")
            url = form.get("video_url", "").strip()
            description = form.get("description", "").strip()
            if len(url) > 2000 or len(description) > 2000:
                raise ValueError("Ссылка и описание должны быть не длиннее 2000 символов каждое.")
            if url:
                try:
                    parsed = urlsplit(url)
                    valid = parsed.scheme in ("http", "https") and bool(parsed.hostname) and not parsed.username and not parsed.password and not any(c.isspace() for c in url)
                except ValueError:
                    valid = False
                if not valid:
                    raise ValueError("Укажите полную ссылку на видео с https:// или http://, без логина и пароля в адресе.")
            if action == "submit" and not url:
                raise ValueError("Добавьте ссылку на запись перед отправкой учителю.")
            if action == "submit" and not has_project_teacher(conn, project_id):
                raise ValueError(TEACHER_REQUIRED)
            current.update(video_url=url, description=description, author_id=user["id"],
                           state="submitted" if action == "submit" else current["state"])
        elif user["role"] == "teacher":
            if action not in ("accept", "return") or current["state"] != "submitted":
                raise ValueError("Учитель может проверять только отправленную запись защиты.")
            comment = form.get("teacher_comment", "").strip()
            if len(comment) > 2000 or (action == "return" and not comment):
                raise ValueError("При возврате укажите, что исправить. Комментарий — до 2000 символов.")
            current.update(teacher_comment=comment, reviewer_id=user["id"], state="accepted" if action == "accept" else "returned")
        else:
            raise ValueError("Недопустимая роль участника.")
        conn.execute("""INSERT INTO project_defenses(project_id,video_url,description,state,teacher_comment,author_id,reviewer_id,revision)
            VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET video_url=excluded.video_url,
            description=excluded.description,state=excluded.state,teacher_comment=excluded.teacher_comment,
            author_id=excluded.author_id,reviewer_id=excluded.reviewer_id,revision=excluded.revision,updated_at=CURRENT_TIMESTAMP""",
            (project_id,current['video_url'],current['description'],current['state'],current['teacher_comment'],current['author_id'],current['reviewer_id'],current['revision']+1))
        conn.commit()
        return {"save": "Черновик записи сохранён.", "submit": "Запись защиты отправлена учителю.",
                "accept": "Видеозащита принята.", "return": "Запись возвращена на доработку."}[action]
    except Exception:
        conn.rollback()
        raise


def render_defense(record, user, token, form=None, has_teacher=True):
    values = form or record
    body = f'<section class="card"><h2>{DEFENSE_STATES[record["state"]]}</h2><p>Одна запись защиты на проект. Черновик и проверка доступны только участникам проекта, включая учителя.</p>'
    if record['video_url']:
        body += f'<p><a class="teacher-primary-link" href="{escape(record["video_url"],quote=True)}" target="_blank" rel="noopener noreferrer">Открыть запись защиты ↗</a></p>'
    if record['description']:
        body += f'<p class="team-text">{escape(record["description"])}</p>'
    if record['author_name']:
        body += f'<p class="t-muted">Автор: {escape(record["author_name"])} · Обновлено: {escape(record["updated_at"])}</p>'
    if record['reviewer_name']:
        body += f'<p>Проверил: {escape(record["reviewer_name"])}</p>'
    if record['teacher_comment']:
        body += f'<p class="stage-comment team-text">Комментарий учителя: {escape(record["teacher_comment"])}</p>'
    prefix = f'<form method="post" class="team-profile-form">{hidden_token(token)}<input type="hidden" name="revision" value="{record["revision"]}">'
    if user['role'] == 'student' and record['state'] in ('draft','returned'):
        body += prefix + f'''<label>Ссылка на запись видео<input name="video_url" type="url" maxlength="2000" placeholder="https://…" value="{escape(values.get('video_url',''),quote=True)}"></label>
            <label>Описание выступления<textarea name="description" rows="4" maxlength="2000">{escape(values.get('description',''))}</textarea></label>
            <p>Подойдёт ссылка на видео или облачный диск. Проверьте права просмотра: учитель должен иметь доступ. M-Flow не загружает видео и не проверяет его доступность автоматически.</p>
            <div class="team-actions"><button class="secondary" name="action" value="save">Сохранить черновик</button><button name="action" value="submit" {"disabled" if not has_teacher else ""}>Отправить защиту учителю</button></div></form>'''
    elif user['role'] == 'teacher' and record['state'] == 'submitted':
        body += prefix + f'''<label>Комментарий учителя<textarea name="teacher_comment" rows="4" maxlength="2000">{escape(values.get('teacher_comment',''))}</textarea></label>
            <div class="team-actions"><button name="action" value="accept">Принять защиту</button><button class="secondary" name="action" value="return">Вернуть на доработку</button></div></form>'''
    elif record['state'] == 'submitted':
        body += '<p>Запись ожидает проверки учителя.</p>'
    elif user['role'] == 'teacher' and record['state'] in ('draft','returned'):
        body += '<p>Ожидается отправка записи учениками.</p>'
    if user['role'] == 'student' and not has_teacher:
        body += f'<p role="alert">{TEACHER_REQUIRED}</p>'
    grading_hint = 'Оценить участников' if user['role'] == 'teacher' else 'Посмотреть свои оценки'
    return body + f'</section><p>Видеозащита не заменяет пять этапов и не выставляет итоговые баллы автоматически. {grading_hint} можно в разделе <a href="/project/{record["project_id"]}/assessment">«Оценивание»</a>.</p>'
