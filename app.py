from flask import (
    Flask,
    request,
    redirect,
    session,
    flash,
    jsonify,
    get_flashed_messages,
    g,
    has_request_context
)
import os
from contextlib import closing
from database import DATABASE_ERRORS
from datetime import date, timedelta
from functools import wraps
from werkzeug.security import (
    check_password_hash,
    generate_password_hash
)
from html import escape
import json
import secrets
from collections import defaultdict
from project_rules import has_project_teacher
from dashboard_view import student_dashboard
from project_workspace import render_workspace, task_deadline
from grading import CRITERIA, MAX_SCORE, RUBRIC_VERSION, parse_scores, summarize, load_scores
from project_stages import initialize_stages, get_stages, stages_from_rows, stage_summary, change_stage, render_stages, STATE_LABELS
from teamwork import (initialize_teamwork, profile_for, save_profile, directory, invite_student,
                      respond_to_invitation, change_team, render_profile as render_skill_profile,
                      render_directory, render_team)
from video_defense import initialize_defenses, defense_for, change_defense, render_defense, DEFENSE_STATES
from database import (DatabaseSettings, DatabaseUnavailable, INTEGRITY_ERRORS,
                      PostgresConnection, connect_database, execute_schema_script,
                      table_columns, insert_id, begin_project_write, initialization_settings)


app = Flask(__name__)
DEBUG_MODE = os.environ.get("M_FLOW_DEBUG") == "1"

SECRET_KEY = os.environ.get("M_FLOW_SECRET_KEY")

if not SECRET_KEY:
    if DEBUG_MODE:
        SECRET_KEY = "m-flow-dev-only-key"
    else:
        raise RuntimeError(
            "Задайте переменную окружения M_FLOW_SECRET_KEY "
            "(длинная случайная строка)."
        )

app.config.update(
    SECRET_KEY=SECRET_KEY,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=not DEBUG_MODE,
    MAX_CONTENT_LENGTH=1024 * 1024,
)


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def csrf_input():
    return f'<input type="hidden" name="csrf_token" value="{escape(csrf_token(), quote=True)}">'


def logout_form():
    return f'<form method="post" action="/logout" class="logout-form">{csrf_input()}<button type="submit" class="logout">Выйти</button></form>'


@app.before_request
def protect_mutations():
    if request.method in ("GET", "HEAD", "OPTIONS") or request.endpoint is None:
        return
    if request.method not in request.url_rule.methods:
        return
    public_auth = {"login", "register", "teacher_login", "teacher_register"}
    if request.endpoint not in public_auth and get_current_user() is None:
        # Expired authentication is reported before CSRF; no mutation can run.
        session.clear()
        if request.is_json:
            return jsonify(success=False, error="Сессия завершена. Войдите в аккаунт заново."), 401
        return redirect("/teacher/login" if request.path.startswith("/teacher") else "/login")
    expected = session.get("csrf_token")
    submitted = request.headers.get("X-CSRF-Token") if request.is_json else request.form.get("csrf_token")
    if not (isinstance(expected, str) and isinstance(submitted, str)
            and 0 < len(submitted) <= 128
            and secrets.compare_digest(expected.encode(), submitted.encode())):
        message = "Форма устарела или сессия изменилась. Обновите страницу и повторите действие."
        if request.is_json:
            return jsonify(success=False, error=message, code="csrf_failed"), 400
        return render_error_page("Форма устарела", message, 400)


@app.after_request
def protect_private_page_cache(response):
    if response.mimetype == "text/html" or (request.method not in ("GET", "HEAD", "OPTIONS")):
        response.headers["Cache-Control"] = "no-store"
    return response


def render_page(html):
    # Страницы собираются f-строками с экранированием через escape().
    # Отдаём их как есть: пропускать через Jinja нельзя, иначе
    # {{ ... }} из пользовательских данных будет выполнен как код.
    if '<main' in html and 'id="main-content"' not in html:
        html = html.replace('<main', '<main id="main-content"', 1)
    skip_target = 'main-content' if '<main' in html else 'page-content'
    return ('<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>M-Flow — проекты и задачи</title>'
            f'<meta name="csrf-token" content="{escape(csrf_token(), quote=True)}">'
        '<link rel="stylesheet" href="/static/mflow-theme.css?v=20261005-mobile">'
        '<link rel="stylesheet" href="/static/mobile.css?v=20261006-scrollbar">'
        '<link rel="stylesheet" href="/static/motion.css?v=20261005-controls">'
        '<link rel="stylesheet" href="/static/preferences.css?v=20261005">'
        '<link rel="stylesheet" href="/static/controls.css?v=20261006-csrf">'
        '<script src="/static/preferences.js?v=20261005"></script>'
        '<script src="/static/motion.js?v=20261005-controls"></script>'
        '<link rel="stylesheet" href="/static/product-design.css?v=20261006">'
        '<script src="/static/dashboard.js?v=20261006" defer></script>'
            f'</head><body id="page-content"><a class="skip-link" href="#{skip_target}">К содержимому</a>' + html + '</body></html>')


def teacher_nav(user):
    team_link = '<a href="/teams" class="logout">Команды</a><a href="/settings" class="logout">Настройки</a>'
    if user["role"] == "teacher":
        return '<a href="/teacher" class="logout">Кабинет учителя</a>' + team_link
    return team_link

DATABASE_SETTINGS = DatabaseSettings.from_environment(app.root_path)
DATABASE_PATH = DATABASE_SETTINGS.path


# =========================================================
# DATABASE
# =========================================================

def get_db():
    return connect_database(DATABASE_SETTINGS)


@app.route('/health', methods=['GET'])
def health():
    """Readiness only: no user table reads or connection details."""
    try:
        with closing(get_db()) as conn:
            available = conn.execute('SELECT 1').fetchone()[0] == 1
    except DATABASE_ERRORS:
        available = False
    response = jsonify(status='ok' if available else 'unavailable')
    response.status_code = 200 if available else 503
    response.headers['Cache-Control'] = 'no-store'
    return response


def valid_database_id(value):
    return type(value) is int and 0 < value <= 9223372036854775807


@app.before_request
def validate_route_ids():
    if any(type(value) is int and not valid_database_id(value)
           for value in (request.view_args or {}).values()):
        if request.is_json:
            return jsonify(success=False, error="Некорректный номер записи."), 400
        return render_error_page("Некорректный адрес", "Проверьте номер проекта или задачи в адресе.", 400)


def prepare_database():
    conn = connect_database(initialization_settings(DATABASE_SETTINGS))
    try:
        _prepare_database(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _prepare_database(conn):

    if isinstance(conn, PostgresConnection):
        # Serialize startup DDL across gunicorn workers in the same transaction.
        conn.execute("SELECT pg_advisory_xact_lock(?)", (674930210,))

    execute_schema_script(conn,
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('teacher', 'student'))
        );

        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            owner_id INTEGER,
            FOREIGN KEY (owner_id) REFERENCES users (id)
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0,
            deadline TEXT,
            status TEXT NOT NULL DEFAULT 'todo',
            priority TEXT NOT NULL DEFAULT 'normal',
            assignee_id INTEGER,
            FOREIGN KEY (project_id) REFERENCES projects (id),
            FOREIGN KEY (assignee_id) REFERENCES users (id)
        );

        CREATE TABLE IF NOT EXISTS project_members (
            project_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            PRIMARY KEY (project_id, user_id),
            FOREIGN KEY (project_id) REFERENCES projects (id),
            FOREIGN KEY (user_id) REFERENCES users (id)
        );

        CREATE TABLE IF NOT EXISTS task_updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (CAST(CURRENT_TIMESTAMP AS TEXT)),
            FOREIGN KEY (task_id) REFERENCES tasks (id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users (id)
        );

        CREATE TABLE IF NOT EXISTS project_assessments (
            project_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            teacher_id INTEGER NOT NULL,
            student_name TEXT NOT NULL,
            rubric_version TEXT NOT NULL,
            scores_json TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL CHECK(state IN ('draft', 'published')),
            updated_at TEXT NOT NULL DEFAULT (CAST(CURRENT_TIMESTAMP AS TEXT)),
            PRIMARY KEY (project_id, student_id, teacher_id),
            FOREIGN KEY (project_id) REFERENCES projects(id),
            FOREIGN KEY (student_id) REFERENCES users(id),
            FOREIGN KEY (teacher_id) REFERENCES users(id)
        );
        """
    )

    initialize_stages(conn)
    initialize_teamwork(conn)
    initialize_defenses(conn)

    project_columns = table_columns(conn, "projects")

    if "owner_id" not in project_columns:
        conn.execute("ALTER TABLE projects ADD COLUMN owner_id INTEGER")

    column_names = table_columns(conn, "tasks")

    if "status" not in column_names:

        conn.execute(
            """
            ALTER TABLE tasks
            ADD COLUMN status TEXT NOT NULL DEFAULT 'todo'
            """
        )

        conn.execute(
            """
            UPDATE tasks
            SET status = 'done'
            WHERE done = 1
            """
        )

    if "priority" not in column_names:

        conn.execute(
            """
            ALTER TABLE tasks
            ADD COLUMN priority TEXT NOT NULL DEFAULT 'normal'
            """
        )

    if "assignee_id" not in column_names:

        conn.execute(
            """
            ALTER TABLE tasks
            ADD COLUMN assignee_id INTEGER
            """
        )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tasks_project_assignee
        ON tasks(project_id, assignee_id)
        """
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_project_members_user
        ON project_members(user_id)
        """
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_task_updates_task_created
        ON task_updates(task_id, created_at DESC)
        """
    )

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_task_updates_task_id
        ON task_updates(task_id, id DESC)
    """)

    if os.environ.get("M_FLOW_DEBUG") == "1":
        for username, role in (("milosh", "student"), ("uchitel", "teacher")):
            if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                continue
            conn.execute(
                """
                INSERT INTO users (username, password, role)
                VALUES (?, ?, ?)
                ON CONFLICT(username) DO NOTHING
                """,
                (username, generate_password_hash("1234"), role)
            )

    # Учитель для рабочего сайта: создаётся из переменных окружения.
    teacher_name = os.environ.get("M_FLOW_TEACHER_USER", "").strip()
    teacher_password = os.environ.get("M_FLOW_TEACHER_PASSWORD", "")

    if (teacher_name and len(teacher_password) >= 8
            and not conn.execute("SELECT 1 FROM users WHERE username = ?", (teacher_name,)).fetchone()):
        conn.execute(
            """
            INSERT INTO users (username, password, role)
            VALUES (?, ?, 'teacher')
            ON CONFLICT(username) DO NOTHING
            """,
            (teacher_name, generate_password_hash(teacher_password))
        )

prepare_database()


@app.errorhandler(DatabaseUnavailable)
def database_unavailable(error):
    message = "База данных временно недоступна. Повторите попытку позже; новая локальная база не создаётся."
    if request.is_json:
        return jsonify(success=False, error=message), 503
    return render_error_page("База данных недоступна", message, 503)


# =========================================================
# AUTH
# =========================================================

def login_required(func):

    @wraps(func)
    def wrapper(*args, **kwargs):

        if "user_id" not in session or get_current_user() is None:
            session.clear()
            if request.is_json:
                return jsonify(success=False, error="Сессия завершена. Войдите в аккаунт заново."), 401
            return redirect("/teacher/login" if request.path.startswith("/teacher") else "/login")

        return func(*args, **kwargs)

    return wrapper


def get_current_user():

    if "user_id" not in session:
        return None

    # Cache only within this request, never across accounts or requests.
    user_id = session["user_id"]
    if has_request_context() and getattr(g, "current_user_id", None) == user_id:
        return g.current_user

    conn = get_db()

    user = conn.execute(
        """
        SELECT id, username, role
        FROM users
        WHERE id = ?
        """,
        (session["user_id"],)
    ).fetchone()

    conn.close()

    if has_request_context():
        g.current_user_id = user_id
        g.current_user = user

    return user


# =========================================================
# PROJECT ACCESS
# =========================================================

def user_has_project_access(project_id):

    user = get_current_user()

    if user is None:
        return False

    conn = get_db()

    project = conn.execute(
        """
        SELECT id
        FROM projects
        WHERE id = ?
        AND (
            owner_id = ?
            OR EXISTS (
                SELECT 1
                FROM project_members
                WHERE project_members.project_id = projects.id
                AND project_members.user_id = ?
            )
        )
        """,
        (
            project_id,
            user["id"],
            user["id"]
        )
    ).fetchone()

    conn.close()

    return project is not None


def is_project_owner(project_id):
    """Only student owners manage projects; legacy teacher ownership is read-only."""

    user = get_current_user()

    if user is None or user['role'] != 'student':
        return False

    conn = get_db()

    project = conn.execute(
        """
        SELECT id
        FROM projects
        WHERE id = ?
        AND owner_id = ?
        """,
        (
            project_id,
            user["id"]
        )
    ).fetchone()

    conn.close()

    return project is not None


# =========================================================
# PROJECTS
# =========================================================

def get_projects(with_counts=False):

    user = get_current_user()

    if user is None:
        return []

    conn = get_db()

    columns = ""
    join = ""
    params = [user["id"], user["id"]]
    if with_counts:
        columns = """, COUNT(t.id) AS total,
            COALESCE(SUM(CASE WHEN t.status='done' THEN 1 ELSE 0 END), 0) AS done,
            COALESCE(SUM(CASE WHEN t.status='progress' THEN 1 ELSE 0 END), 0) AS active,
            COALESCE(SUM(CASE WHEN t.status!='done' AND t.deadline!='' AND t.deadline<? THEN 1 ELSE 0 END), 0) AS overdue"""
        join = "LEFT JOIN tasks t ON t.project_id=projects.id"
        params.insert(0, date.today().isoformat())
    projects = conn.execute(f"""
        SELECT projects.id, projects.name {columns}
        FROM projects {join}
        WHERE projects.owner_id=? OR EXISTS (
            SELECT 1 FROM project_members m
            WHERE m.project_id=projects.id AND m.user_id=?
        )
        GROUP BY projects.id ORDER BY projects.id DESC
        """, params).fetchall()

    conn.close()

    return projects


def is_valid_date(value):

    if not value:
        return True

    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return False

    return value == parsed.isoformat()


def add_project(name):

    user = get_current_user()

    if user is None:
        return None

    conn = get_db()

    project_id = insert_id(conn,
        """
        INSERT INTO projects
        (name, owner_id)
        VALUES (?, ?)
        """,
        (
            name,
            user["id"]
        )
    )

    conn.execute(
        """
        INSERT INTO project_members
        (project_id, user_id)
        VALUES (?, ?)
        ON CONFLICT(project_id, user_id) DO NOTHING
        """,
        (
            project_id,
            user["id"]
        )
    )

    conn.commit()
    conn.close()

    return project_id


def get_project_details(project_id):

    user = get_current_user()

    if user is None:
        return None

    conn = get_db()

    project = conn.execute(
        """
        SELECT projects.id, projects.name, projects.owner_id
        FROM projects
        WHERE projects.id = ?
        AND (
            projects.owner_id = ?
            OR EXISTS (
                SELECT 1
                FROM project_members
                WHERE project_members.project_id = projects.id
                AND project_members.user_id = ?
            )
        )
        """,
        (
            project_id,
            user["id"],
            user["id"]
        )
    ).fetchone()

    conn.close()

    return project


def get_project_name(project_id):
    project = get_project_details(project_id)
    return project['name'] if project is not None else None


# =========================================================
# TASKS
# =========================================================

def get_tasks(project_id):

    conn = get_db()

    tasks = conn.execute(
        """
        SELECT
            tasks.id,
            tasks.title,
            tasks.done,
            tasks.deadline,
            tasks.status,
            tasks.priority,
            tasks.assignee_id,
            users.username AS assignee_name
        FROM tasks
        LEFT JOIN users
            ON users.id = tasks.assignee_id
        WHERE tasks.project_id = ?
        ORDER BY
            CASE priority
                WHEN 'urgent' THEN 1
                WHEN 'high' THEN 2
                WHEN 'normal' THEN 3
                WHEN 'low' THEN 4
                ELSE 5
            END,
            tasks.id
        """,
        (project_id,)
    ).fetchall()

    conn.close()

    return tasks


def add_task(
    project_id,
    title,
    deadline,
    priority,
    assignee_id
):

    allowed_priorities = {
        "low",
        "normal",
        "high",
        "urgent"
    }

    if not isinstance(priority, str) or priority not in allowed_priorities:
        priority = "normal"

    if assignee_id is not None and not valid_database_id(assignee_id):
        assignee_id = None

    if assignee_id is not None:
        conn = get_db()
        member = conn.execute(
            """
            SELECT 1
            FROM project_members
            JOIN users ON users.id = project_members.user_id
            WHERE project_members.project_id = ?
            AND project_members.user_id = ?
            AND users.role = 'student'
            """,
            (project_id, assignee_id)
        ).fetchone()
        conn.close()

        if member is None:
            assignee_id = None

    conn = get_db()

    conn.execute(
        """
        INSERT INTO tasks
        (
            project_id,
            title,
            deadline,
            done,
            status,
            priority,
            assignee_id
        )
        VALUES (?, ?, ?, 0, 'todo', ?, ?)
        """,
        (
            project_id,
            title,
            deadline,
            priority,
            assignee_id
        )
    )

    conn.commit()
    conn.close()


def delete_task(project_id, task_id):

    conn = get_db()

    conn.execute(
        """
        DELETE FROM tasks
        WHERE id = ?
        AND project_id = ?
        """,
        (
            task_id,
            project_id
        )
    )

    conn.commit()
    conn.close()


def add_task_update(project_id, task_id, user_id, body):

    conn = get_db()

    task = conn.execute(
        """
        SELECT id
        FROM tasks
        WHERE id = ? AND project_id = ?
        """,
        (task_id, project_id)
    ).fetchone()

    if task is None:
        conn.close()
        return False

    conn.execute(
        """
        INSERT INTO task_updates (task_id, user_id, body)
        VALUES (?, ?, ?)
        """,
        (task_id, user_id, body)
    )

    conn.commit()
    conn.close()
    return True


def get_task_update_history(project_id):

    conn = get_db()
    rows = conn.execute(
        """
        SELECT task_updates.task_id, task_updates.body,
               task_updates.created_at, users.username
        FROM task_updates
        JOIN tasks ON tasks.id = task_updates.task_id
        JOIN users ON users.id = task_updates.user_id
        WHERE tasks.project_id = ?
        ORDER BY task_updates.id DESC
        """,
        (project_id,)
    ).fetchall()
    conn.close()

    history = {}
    for row in rows:
        history.setdefault(row["task_id"], []).append(row)

    return history


def update_task_status(
    project_id,
    task_id,
    status
):

    allowed_statuses = {
        "todo",
        "progress",
        "done"
    }

    if not isinstance(status, str) or status not in allowed_statuses:
        return False

    conn = get_db()

    task = conn.execute(
        """
        SELECT id
        FROM tasks
        WHERE id = ?
        AND project_id = ?
        """,
        (
            task_id,
            project_id
        )
    ).fetchone()

    if task is None:

        conn.close()
        return False

    done = 1 if status == "done" else 0

    conn.execute(
        """
        UPDATE tasks
        SET status = ?,
            done = ?
        WHERE id = ?
        AND project_id = ?
        """,
        (
            status,
            done,
            task_id,
            project_id
        )
    )

    conn.commit()
    conn.close()

    return True


def user_can_update_task(project_id, task_id):

    user = get_current_user()

    if user is None or user['role'] != 'student':
        return False

    conn = get_db()

    task = conn.execute(
        """
        SELECT tasks.id
        FROM tasks
        JOIN projects ON projects.id = tasks.project_id
        WHERE tasks.id = ?
        AND tasks.project_id = ?
        AND (
            projects.owner_id = ?
            OR tasks.assignee_id = ?
        )
        """,
        (task_id, project_id, user["id"], user["id"])
    ).fetchone()

    conn.close()

    return task is not None


def update_task_priority(
    project_id,
    task_id,
    priority
):

    allowed_priorities = {
        "low",
        "normal",
        "high",
        "urgent"
    }

    if not isinstance(priority, str) or priority not in allowed_priorities:
        return False

    conn = get_db()

    task = conn.execute(
        """
        SELECT id
        FROM tasks
        WHERE id = ?
        AND project_id = ?
        """,
        (
            task_id,
            project_id
        )
    ).fetchone()

    if task is None:

        conn.close()
        return False

    conn.execute(
        """
        UPDATE tasks
        SET priority = ?
        WHERE id = ?
        AND project_id = ?
        """,
        (
            priority,
            task_id,
            project_id
        )
    )

    conn.commit()
    conn.close()

    return True


# =========================================================
# MEMBERS
# =========================================================

def get_project_members(project_id):

    conn = get_db()

    members = conn.execute(
        """
        SELECT
            users.id,
            users.username,
            users.role
        FROM users
        WHERE users.id = (SELECT owner_id FROM projects WHERE id = ?)
        OR EXISTS (SELECT 1 FROM project_members m WHERE m.project_id = ? AND m.user_id = users.id)
        ORDER BY
            CASE
                WHEN users.role = 'teacher' THEN 0
                ELSE 1
            END,
            users.username
        """,
        (project_id, project_id)
    ).fetchall()

    conn.close()

    return members


def get_student_progress(project_id):

    conn = get_db()

    students = conn.execute(
        """
        SELECT
            users.id,
            users.username,
            COUNT(tasks.id) AS task_count,
            COALESCE(SUM(CASE WHEN tasks.status = 'done' THEN 1 ELSE 0 END), 0)
                AS completed_count
        FROM project_members
        JOIN users
            ON users.id = project_members.user_id
        LEFT JOIN tasks
            ON tasks.project_id = project_members.project_id
            AND tasks.assignee_id = users.id
        WHERE project_members.project_id = ?
        AND users.role = 'student'
        GROUP BY users.id, users.username
        ORDER BY users.username
        """,
        (project_id,)
    ).fetchall()

    conn.close()

    return students


# =========================================================
# STYLE + JAVASCRIPT
# =========================================================

PAGE_STYLE = '<script src="/static/kanban.js?v=20261009-polish" defer></script>'


# =========================================================
# LOGIN
# =========================================================

def render_auth_page(mode, error=None, role="student"):
    is_login = mode == "login"
    is_teacher = role == "teacher"
    prefix = "/teacher" if is_teacher else ""
    role_title = "Для учителей" if is_teacher else "Для учеников"
    student_url = f'/{mode}'
    teacher_url = f'/teacher/{mode}'
    username = escape(request.form.get("username", "")[:30])

    error_html = (
        f'<div class="error" role="alert">{escape(error)}</div>'
        if error
        else ""
    )

    if is_login:
        form_fields = f"""
            <label for="auth-username">Логин</label>
            <input id="auth-username" name="username" value="{username}" autocomplete="username" maxlength="30" required>
            <label for="auth-password">Пароль</label>
            <input id="auth-password" name="password" type="password" autocomplete="current-password" required>
        """
        submit_label = "Войти"
        hero_title = "Проекты класса под контролем" if is_teacher else "Твои проекты и задачи"
        hero_text = (
            "Следите за прогрессом учеников, проверяйте этапы и читайте отчёты."
            if is_teacher else "Работай над задачами, следи за дедлайнами и рассказывай о прогрессе."
        )
    else:
        form_fields = f"""
            <label for="auth-username">Придумайте логин</label>
            <input id="auth-username" name="username" value="{username}" minlength="3" maxlength="30" autocomplete="username" required>
            <label for="auth-password">Пароль</label>
            <input id="auth-password" name="password" type="password" minlength="8" maxlength="256" autocomplete="new-password" required>
            <small class="auth-hint">Минимум 8 символов</small>
            <label for="auth-repeat-password">Повторите пароль</label>
            <input id="auth-repeat-password" name="repeat_password" type="password" minlength="8" maxlength="256" autocomplete="new-password" required>
        """
        submit_label = "Зарегистрироваться"
        hero_title = "Ваш кабинет учителя" if is_teacher else "Начни работу с M-Flow"
        hero_text = (
            "Создайте аккаунт учителя и сообщите ученикам свой логин. Они добавят вас в проекты для проверки результатов."
            if is_teacher else "Создай аккаунт ученика, начни проект и добавь учителя по его логину для проверки результатов."
        )

    login_active = "active" if is_login else ""
    register_active = "" if is_login else "active"

    return render_page(
        PAGE_STYLE
        + f"""
        <div class="auth-page auth-role-{role}">
            <div class="auth-card">
                <div class="auth-form-side">
                    <div class="auth-form-inner">
                    <a href="{prefix}/login" class="brand auth-form-brand" aria-label="M-Flow — вход">M<span>-</span>Flow</a>
                    <h1>{"С возвращением" if is_login else "Создать аккаунт"}</h1>
                    <p class="auth-intro">{role_title} · {"Продолжите работу над проектами." if is_login else "Всё для работы над школьным проектом."}</p>
                    <nav class="auth-role-switch" aria-label="Выбор роли">
                        <a href="{student_url}" class="{"active" if not is_teacher else ""}" {"aria-current='page'" if not is_teacher else ""}>Я ученик</a>
                        <a href="{teacher_url}" class="{"active" if is_teacher else ""}" {"aria-current='page'" if is_teacher else ""}>Я учитель</a>
                    </nav>
                    <div class="auth-tabs">
                        <a href="{prefix}/login" class="auth-tab {login_active}" {"aria-current='page'" if is_login else ""}>Вход</a>
                        <a href="{prefix}/register" class="auth-tab {register_active}" {"aria-current='page'" if not is_login else ""}>Регистрация</a>
                    </div>
                    {error_html}
                    <form method="post">
                        {csrf_input()}
                        {form_fields}
                        <button type="submit">{submit_label}</button>
                    </form>
                    <p class="auth-footer">{"Проверяйте этапы, видеозащиты и отчёты учеников в одном кабинете." if is_teacher else "Создайте проект и добавьте учителя по его логину."}<br><a href="/settings">Настройки оформления</a></p>
                    </div>
                </div>
                <aside class="auth-hero" aria-label="О проекте M-Flow">
                    <div class="auth-hero-top">Рабочее пространство школьных проектов</div>
                    <div class="auth-hero-content">
                        <span class="auth-product-tag">ДЛЯ БОЛЬШИХ ИДЕЙ</span>
                        <h2 class="auth-logo auth-shimmer">M-Flow</h2>
                        <div class="auth-byline auth-shimmer">by Minich</div>
                        <img class="auth-art" src="/static/project-kit.svg" alt="" width="480" height="360">
                        <h3>{hero_title}</h3>
                        <p>{hero_text}</p>
                    </div>
                    <div class="auth-hero-footer"><span>01 / Тема</span><span>02–04 / Работа</span><span>05 / Документы</span></div>
                </aside>
            </div>
        </div>
        """
    )


def login_for_role(role):
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE username = ?",
            (username,)
        ).fetchone()

        conn.close()
        if user is None or not check_password_hash(user["password"], password):
            return render_auth_page("login", error="Неверный логин или пароль.", role=role)

        if user["role"] != role:
            role_name = "учителя" if user["role"] == "teacher" else "ученика"
            switch_name = "Я учитель" if user["role"] == "teacher" else "Я ученик"
            return render_auth_page(
                "login", error=f'Это аккаунт {role_name}. Выберите «{switch_name}» для входа.', role=role
            )

        session.clear()
        session["user_id"] = user["id"]
        return redirect("/teacher" if user["role"] == "teacher" else "/")

    return render_auth_page("login", role=role)


def register_for_role(role):
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        repeat_password = request.form.get("repeat_password", "")

        if not username or not password:
            return render_auth_page("register", error="Заполните все поля.", role=role)

        if len(username) < 3 or len(username) > 30:
            return render_auth_page("register", error="Логин должен быть от 3 до 30 символов.", role=role)

        if not 8 <= len(password) <= 256:
            return render_auth_page("register", error="Пароль должен быть от 8 до 256 символов.", role=role)

        if password != repeat_password:
            return render_auth_page("register", error="Пароли не совпадают.", role=role)

        conn = get_db()

        existing = conn.execute(
            "SELECT id FROM users WHERE username = ?",
            (username,)
        ).fetchone()

        if existing is not None:
            conn.close()
            return render_auth_page("register", error="Такой логин уже занят. Выберите другой или войдите в существующий аккаунт.", role=role)

        try:
            new_user_id = insert_id(conn,
                "INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
                (username, generate_password_hash(password), role)
            )
            conn.commit()
        except INTEGRITY_ERRORS:
            conn.rollback()
            conn.close()
            return render_auth_page("register", error="Такой логин уже занят.", role=role)
        conn.close()

        session.clear()
        session["user_id"] = new_user_id
        return redirect("/teacher" if role == "teacher" else "/")

    return render_auth_page("register", role=role)


@app.route("/login", methods=["GET", "POST"])
def login():
    return login_for_role("student")


@app.route("/register", methods=["GET", "POST"])
def register():
    return register_for_role("student")


@app.route("/teacher/login", methods=["GET", "POST"])
def teacher_login():
    return login_for_role("teacher")


@app.route("/teacher/register", methods=["GET", "POST"])
def teacher_register():
    return register_for_role("teacher")


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = get_current_user()
    message = ""
    error = ""

    if request.method == "POST":
        old_password = request.form.get("old_password", "")
        new_password = request.form.get("new_password", "")
        repeat_password = request.form.get("repeat_password", "")

        conn = get_db()
        row = conn.execute(
            "SELECT password FROM users WHERE id = ?",
            (user["id"],)
        ).fetchone()
        conn.close()

        if row is None or not check_password_hash(row["password"], old_password):
            error = "Старый пароль введён неверно."
        elif len(new_password) < 8:
            error = "Новый пароль слишком короткий (минимум 8 символов)."
        elif len(new_password) > 256:
            error = "Новый пароль должен быть не длиннее 256 символов."
        elif new_password != repeat_password:
            error = "Новые пароли не совпадают."
        else:
            conn = get_db()
            conn.execute(
                "UPDATE users SET password = ? WHERE id = ?",
                (generate_password_hash(new_password), user["id"])
            )
            conn.commit()
            conn.close()
            message = "Пароль изменён."
            session.pop("csrf_token", None)

    role_name = "Учитель" if user["role"] == "teacher" else "Ученик"
    projects_count = len(get_projects())
    skills_link = '<p><a class="teacher-primary-link" href="/skills">Мой класс, направление и навыки</a></p>' if user["role"] == "student" else ''

    message_html = f'<div class="ok">{message}</div>' if message else ""
    error_html = f'<div class="error">{error}</div>' if error else ""

    return render_page(
        PAGE_STYLE
        + f"""
        <div class="container">
            <div class="profile-card">
                <a class="back" href="/">← На главную</a>
                <h1>Профиль</h1>
                <dl>
                    <dt>Логин</dt>
                    <dd>{escape(user["username"])}</dd>
                    <dt>Роль</dt>
                    <dd>{role_name}</dd>
                    <dt>Проектов</dt>
                    <dd>{projects_count}</dd>
                </dl>
                {skills_link}
                <p><a class="back" href="/settings">Настройки оформления и анимаций →</a></p>
                <h3>Смена пароля</h3>
                {message_html}
                {error_html}
                <form method="post">
                    {csrf_input()}
                    <label for="old-password">Старый пароль</label>
                    <input id="old-password" type="password" name="old_password" autocomplete="current-password" required>
                    <label for="new-password">Новый пароль</label>
                    <input id="new-password" type="password" name="new_password" autocomplete="new-password" minlength="8" maxlength="256" required>
                    <label for="repeat-password">Повторите новый пароль</label>
                    <input id="repeat-password" type="password" name="repeat_password" autocomplete="new-password" minlength="8" maxlength="256" required>
                    <button type="submit">Сменить пароль</button>
                </form>
            </div>
        </div>
        """
    )


# =========================================================
# LOGOUT
# =========================================================

@app.route("/logout", methods=["POST"])
@login_required
def logout():

    user = get_current_user()
    target = "/teacher/login" if user is not None and user["role"] == "teacher" else "/login"
    session.clear()

    return redirect(target)


# =========================================================
# MAIN PAGE
# =========================================================

@app.route("/")
@login_required
def index():
    user = get_current_user()
    if user['role'] == 'teacher':
        return teacher_cabinet()
    projects = get_projects(with_counts=True)
    messages = ''.join(f'<div class="{escape(category, quote=True)}" role="status">{escape(message)}</div>'
                       for category, message in get_flashed_messages(with_categories=True))
    return render_page(PAGE_STYLE + student_dashboard(user, projects, csrf_token(), render_header(user), messages))


# =========================================================
# CREATE PROJECT
# =========================================================

@app.route(
    "/add_project",
    methods=["POST"]
)
@login_required
def create_project():
    if get_current_user()['role'] != 'student':
        return render_error_page('Нет доступа', 'Проекты создают ученики. Учитель проверяет результаты в кабинете.', 403)

    name = request.form.get(
        "name",
        ""
    ).strip()


    if not name:

        flash(
            "Введите название проекта.",
            "error"
        )

        return redirect("/")

    if len(name) > 80:

        flash(
            "Название проекта должно быть не длиннее 80 символов.",
            "error"
        )

        return redirect("/")


    add_project(name)

    flash(
        "Проект создан!",
        "success"
    )

    return redirect("/")


# =========================================================
# PROJECT PAGE
# =========================================================

@app.route(
    "/project/<int:project_id>"
)
@login_required
def project(project_id):

    details = get_project_details(project_id)
    if details is None:
        return render_error_page('Нет доступа', 'Проект не найден или вы не участвуете в нём.', 403)
    project_name = details['name']
    current_user = get_current_user()
    all_tasks = get_tasks(project_id)
    task_update_history = get_task_update_history(project_id)
    members = get_project_members(project_id)
    student_counts = defaultdict(lambda: [0, 0])
    for task in all_tasks:
        counts = student_counts[task['assignee_id']]
        counts[0] += 1
        counts[1] += task['status'] == 'done'
    student_progress = [dict(id=member['id'], username=member['username'],
                             task_count=student_counts[member['id']][0],
                             completed_count=student_counts[member['id']][1])
                        for member in members if member['role'] == 'student'] if current_user['role'] == 'teacher' else []
    owner = current_user['role'] == 'student' and details['owner_id'] == current_user['id']
    stage_conn = get_db()
    try:
        stages = get_stages(stage_conn, project_id)
        defense = defense_for(stage_conn, project_id)
        assessment_column = 'teacher_id' if current_user['role'] == 'teacher' else 'student_id'
        published_assessments = stage_conn.execute(f'''SELECT a.scores_json, u.username AS teacher_name
            FROM project_assessments a JOIN users u ON u.id=a.teacher_id
            WHERE a.project_id=? AND a.state='published' AND a.{assessment_column}=?
            ORDER BY a.updated_at DESC, a.teacher_id''', (project_id, current_user['id'])).fetchall()
    finally:
        stage_conn.close()

    # ---------- фильтры (поиск, приоритет, только мои) ----------

    search_query = request.args.get("q", "").strip()[:100]
    priority_filter = request.args.get("priority", "")
    if priority_filter not in ("low", "normal", "high", "urgent"):
        priority_filter = ""
    only_mine = current_user['role'] == 'student' and request.args.get("mine") == "1"

    tasks = list(all_tasks)

    if search_query:
        normalized_query = search_query.lower()
        tasks = [
            task for task in tasks
            if normalized_query in task["title"].lower()
        ]

    if priority_filter in ("low", "normal", "high", "urgent"):
        tasks = [
            task for task in tasks
            if (task["priority"] or "normal") == priority_filter
        ]

    if only_mine:
        tasks = [
            task for task in tasks
            if task["assignee_id"] == current_user["id"]
        ]

    filters_active = bool(search_query or priority_filter or only_mine)

    hot_items = ""

    for task in all_tasks:
        if not task["deadline"] or task["status"] == "done":
            continue

        try:
            days_left = (
                date.fromisoformat(task["deadline"])
                - date.today()
            ).days
        except ValueError:
            continue

        if days_left > 3:
            continue

        if days_left < 0:
            label = f"просрочено на {-days_left} дн."
            css = "overdue"
        elif days_left == 0:
            label = "сегодня"
            css = ""
        else:
            label = f"через {days_left} дн."
            css = ""

        hot_items += (
            f'<li class="{css}">{escape(task["title"])} — '
            f'{escape(task["deadline"])} ({label})</li>'
        )

    hot_html = (
        f'<div class="hot-strip"><strong>Горящие дедлайны</strong>'
        f'<ul>{hot_items}</ul></div>'
        if hot_items
        else ""
    )

    def selected(value):
        return "selected" if priority_filter == value else ""

    mine_checked = "checked" if only_mine else ""
    mine_filter = f'<label><input type="checkbox" name="mine" value="1" {mine_checked}> Только мои</label>' if current_user['role'] == 'student' else ''
    reset_link = (
        f'<a class="filter-reset" href="/project/{project_id}">Сбросить</a>'
        if filters_active
        else ""
    )

    filters_html = f"""
    <form method="get" class="task-filters">
        <input type="search" name="q" value="{escape(search_query)}" placeholder="Поиск по названию задачи" aria-label="Поиск по названию задачи">
        <select name="priority" aria-label="Фильтр приоритета">
            <option value="">Любой приоритет</option>
            <option value="low" {selected("low")}>Низкий</option>
            <option value="normal" {selected("normal")}>Обычный</option>
            <option value="high" {selected("high")}>Высокий</option>
            <option value="urgent" {selected("urgent")}>Срочный</option>
        </select>
        {mine_filter}
        <button type="submit">Найти</button>
        {reset_link}
    </form>
    """


    todo_tasks = [
        task
        for task in tasks
        if task["status"] == "todo"
    ]

    progress_tasks = [
        task
        for task in tasks
        if task["status"] == "progress"
    ]

    done_tasks = [
        task
        for task in tasks
        if task["status"] == "done"
    ]


    total_tasks = len(all_tasks)
    completed_tasks = sum(1 for task in all_tasks if task["status"] == "done")
    todo_count = sum(1 for task in all_tasks if task["status"] == "todo")
    progress_count = sum(1 for task in all_tasks if task["status"] == "progress")
    overdue_count = sum(
        1
        for task in all_tasks
        if task["deadline"]
        and task["deadline"] < date.today().isoformat()
        and task["status"] != "done"
    )


    progress = (
        round(
            completed_tasks
            / total_tasks
            * 100
        )
        if total_tasks
        else 0
    )


    # =====================================================
    # TASK CARD
    # =====================================================

    def render_task_card(task):

        deadline = (
            task["deadline"]
            if task["deadline"]
            else "Без срока"
        )

        done_class = (
            "done-card"
            if task["status"] == "done"
            else ""
        )


        priority_names = {
            "low": "Низкий",
            "normal": "Обычный",
            "high": "Высокий",
            "urgent": "Срочный"
        }


        priority = (
            task["priority"]
            or "normal"
        )


        priority_name = priority_names.get(
            priority,
            "Обычный"
        )


        # ВАЖНО:
        # Все значения заранее считаются здесь.
        # Благодаря этому внутри f-string нет
        # сложных выражений.

        task_id = task["id"]

        # Access to this project has already been checked; use loaded card data.
        # Mutation routes still perform their own database permission checks.
        can_update = current_user['role'] == 'student' and (owner or task["assignee_id"] == current_user["id"])
        draggable = "true" if can_update else "false"
        assignee_name = escape(task["assignee_name"] or "Не назначена")

        updates = task_update_history.get(task_id, [])
        latest_update_html = ""
        if updates:
            latest_update = updates[0]
            update_author = escape(latest_update["username"] or "Участник")
            update_timestamp = escape(latest_update["created_at"] or "")
            update_body = escape(latest_update["body"])

            history_items = "".join(
                f'''<article class="task-report-entry">
                    <div class="task-report-head">
                        <strong>{escape(update["username"])}</strong>
                        <span>{escape(update["created_at"])}</span>
                    </div>
                    <p>{escape(update["body"])}</p>
                </article>'''
                for update in updates
            )

            latest_update_html = f"""
                <div class="task-report">
                    <div class="task-report-head">
                        <strong>Последний отчёт · {update_author}</strong>
                        <span>{update_timestamp}</span>
                    </div>
                    <p>{update_body}</p>
                    <details class="task-report-history">
                        <summary>История отчётов ({len(updates)})</summary>
                        <div>{history_items}</div>
                    </details>
                </div>
            """

        report_form = ""
        if current_user["role"] == "student" and task["assignee_id"] == current_user["id"]:
            report_form = f"""
                <details class="task-report-compose">
                    <summary>Добавить отчёт</summary>
                    <form method="post" action="/project/{project_id}/task/{task_id}/update">
                        {csrf_input()}
                        <textarea name="body" maxlength="500" rows="3" required
                            placeholder="Что уже сделано? Что мешает двигаться дальше?"></textarea>
                        <button type="submit">Сохранить отчёт</button>
                    </form>
                </details>
            """

        delete_control = f"""
            <form action="/project/{project_id}/delete/{task_id}" method="post">
                {csrf_input()}
                <button type="submit" class="delete-task" title="Удалить задачу">Удалить</button>
            </form>
        """ if owner else ""

        task_title = escape(
            task["title"]
        )

        safe_deadline = escape(
            deadline
        )

        deadline_class = ""

        if task["deadline"] and task["status"] != "done":
            try:
                days_left = (
                    date.fromisoformat(task["deadline"])
                    - date.today()
                ).days

                if days_left < 0:
                    deadline_class = "deadline-overdue"
                elif days_left <= 2:
                    deadline_class = "deadline-soon"
            except ValueError:
                pass

        low_selected = (
            "selected"
            if priority == "low"
            else ""
        )

        normal_selected = (
            "selected"
            if priority == "normal"
            else ""
        )

        high_selected = (
            "selected"
            if priority == "high"
            else ""
        )

        urgent_selected = (
            "selected"
            if priority == "urgent"
            else ""
        )

        status_options = ''.join(
            f'<option value="{value}" {"selected" if task["status"] == value else ""}>{label}</option>'
            for value, label in (("todo", "К выполнению"), ("progress", "В работе"), ("done", "Готово"))
        )
        status_control = f'''<label class="task-status-control" for="task-status-{task_id}">Статус задачи
            <select id="task-status-{task_id}" class="task-status-select" onchange="changeTaskStatus(this)">
                {status_options}
            </select></label>''' if can_update else ''

        priority_control = f"""
            <select class="task-priority-select" data-task-id="{task_id}" aria-label="Приоритет задачи: {task_title}" onchange="changePriority(this)">
                <option value="low" {low_selected}>Низкий</option>
                <option value="normal" {normal_selected}>Обычный</option>
                <option value="high" {high_selected}>Высокий</option>
                <option value="urgent" {urgent_selected}>Срочный</option>
            </select>
        """ if owner else ""


        return f"""
        <div
            class="task-card {done_class}"
            id="task-{task_id}"
            draggable="{draggable}"
            data-task-id="{task_id}"
            data-deadline="{escape(task['deadline'] or '', quote=True)}"
            data-priority="{escape(priority, quote=True)}"
        >

            <div class="task-title">
                {task_title}
            </div>


            <div class="task-priority priority-{escape(priority)}">
                {priority_name}
            </div>


            {priority_control}

            {status_control}

            {latest_update_html}

            {report_form}


            <div class="task-footer">

                <span class="task-deadline {deadline_class}">
                    {safe_deadline}
                </span>

                <span class="role">{assignee_name}</span>

                {delete_control}

            </div>

        </div>
        """


    # =====================================================
    # KANBAN COLUMN
    # =====================================================

    def render_column(
        title,
        status,
        dot_class,
        task_list
    ):

        cards = ""

        for task in task_list:

            cards += render_task_card(
                task
            )


        if not cards:

            cards = """
            <div class="kanban-empty">
                Пока нет задач
            </div>
            """


        task_count = len(task_list)


        return f"""
        <div
            class="kanban-column"
            data-status="{status}"
        >

            <div class="kanban-header">

                <div class="kanban-title">

                    <span
                        class="kanban-dot {dot_class}"
                    ></span>

                    {title}

                </div>

                <span class="kanban-count">
                    {task_count}
                </span>

            </div>

            <div class="kanban-cards">

                {cards}

            </div>

        </div>
        """


    kanban_html = f"""

    <div class="kanban">

        {render_column(
            "Новые",
            "todo",
            "dot-todo",
            todo_tasks
        )}

        {render_column(
            "В работе",
            "progress",
            "dot-progress",
            progress_tasks
        )}

        {render_column(
            "Готово",
            "done",
            "dot-done",
            done_tasks
        )}

    </div>

    """


    # =====================================================
    # MEMBERS HTML
    # =====================================================

    members_html = ""


    for member in members:

        member_username = escape(
            member["username"]
        )

        role_name = (
            "Учитель"
            if member["role"] == "teacher"
            else "Ученик"
        )


        remove_button = ""


        if (
            owner
            and member["id"]
            != current_user["id"]
        ):

            member_id = member["id"]

            remove_button = f"""
            <form
                action="/project/{project_id}/remove_member/{member_id}"
                method="post"
            >
                {csrf_input()}

                <button
                    type="submit"
                    class="secondary"
                >
                    Удалить
                </button>

            </form>
            """


        members_html += f"""
        <div class="member">

            <b>
                {member_username}
            </b>

            <span class="role">
                {role_name}
            </span>

            {remove_button}

        </div>
        """

    student_options = '<option value="">Не назначать</option>'

    for member in members:
        if member["role"] == "student":
            student_options += (
                f'<option value="{member["id"]}">'
                f'{escape(member["username"])}'
                f'</option>'
            )

    student_progress_html = ""

    if current_user["role"] == "teacher":
        for student in student_progress:
            task_count = student["task_count"]
            completed_count = student["completed_count"]
            student_percent = (
                round(completed_count / task_count * 100)
                if task_count else 0
            )
            student_progress_html += f"""
            <div class="member">
                <b>{escape(student['username'])}</b>
                <span class="role" data-student-progress="{student['id']}">{completed_count} из {task_count} задач · {student_percent}%</span>
            </div>
            """

        if not student_progress_html:
            student_progress_html = "<p>В проекте пока нет учеников. Владелец проекта формирует команду.</p>"

    teacher_dashboard = f"""
    <div class="card">
        <h2>Прогресс учеников</h2>
        {student_progress_html}
    </div>
    """ if current_user["role"] == "teacher" else ""

    add_task_section = f"""
    <div class="card">
        <h2>Добавить задачу</h2>
        <form action="/project/{project_id}/add" method="post" class="add-task-form">
            {csrf_input()}
            <input name="title" placeholder="Название задачи" aria-label="Название задачи" required>
            <input name="deadline" type="date" aria-label="Дедлайн задачи">
            <select name="priority" class="priority-select add-task-priority" aria-label="Приоритет новой задачи">
                <option value="low">Низкий</option>
                <option value="normal" selected>Обычный</option>
                <option value="high">Высокий</option>
                <option value="urgent">Срочный</option>
            </select>
            <select name="assignee_id" class="priority-select" aria-label="Ответственный за задачу">
                {student_options}
            </select>
            <button type="submit">Добавить</button>
        </form>
    </div>
    """ if owner else ""


    # =====================================================
    # OWNER SECTION
    # =====================================================

    owner_section = ""


    if owner:

        owner_section = f"""
        <div class="card" id="project-members">

            <h2>
                Добавить участника
            </h2>

            <form
                action="/project/{project_id}/add_member"
                method="post"
                class="member-add-form"
            >
                {csrf_input()}

                <input
                    name="username"
                    placeholder="Логин пользователя"
                    aria-label="Логин участника"
                    required
                >

                <button type="submit">
                    Добавить
                </button>

            </form>

            <p>
                Только владелец проекта
                может добавлять участников.
            </p>

        </div>
        """


    # =====================================================
    # MESSAGES
    # =====================================================

    messages = ""


    for message in session.pop(
        "_flashes",
        []
    ):

        category, text = message

        messages += f"""
        <div class="{category}">
            {escape(text)}
        </div>
        """


    workspace_html = render_workspace(project_id, project_name, current_user, owner, members,
                                      stages, defense, published_assessments, all_tasks,
                                      completed_tasks, progress, overdue_count)
    return render_page(
        PAGE_STYLE
        + f"""
        <link rel="stylesheet" href="/static/project-workspace.css?v=20261006-route">
        <div class="container">
            {render_header(current_user)}
            <main class="project-workspace">
                <a href="/" class="back">← Все проекты</a>
                {messages}
                {workspace_html}


                <section class="project-metrics">

                    <div class="metric-card metric-todo">
                        <span>Новые</span>
                        <strong>{todo_count}</strong>
                    </div>

                    <div class="metric-card metric-progress">
                        <span>В работе</span>
                        <strong>{progress_count}</strong>
                    </div>

                    <div class="metric-card metric-done">
                        <span>Готово</span>
                        <strong>{completed_tasks}</strong>
                    </div>

                    <div class="metric-card metric-overdue">
                        <span>Просрочено</span>
                        <strong>{overdue_count}</strong>
                    </div>

                </section>


                <div class="kanban-wrapper" id="task-board">

                    <div class="section-title">

                        <div>

                            <h2>
                                Доска задач
                            </h2>

                            <p>
                                {"Здесь показаны задачи учеников. Учитель проверяет этапы, видеозащиту и оценки." if current_user['role'] == 'teacher' else "Меняй статус в карточке или перетаскивай задачи между колонками"}
                            </p>

                        </div>

                    </div>

                    {hot_html}

                    {filters_html}

                    <p id="board-status" role="status" aria-live="polite" hidden></p>

                    {kanban_html}

                </div>

                {teacher_dashboard}

                {add_task_section}


                <div class="card">

                    <h2>
                        Участники
                    </h2>

                    {
                        members_html
                        if members_html
                        else
                        "<p>Участников пока нет.</p>"
                    }

                </div>


                {owner_section}

            </main>

        </div>
        """
    )


# =========================================================
# ADD MEMBER
# =========================================================

@app.route(
    "/project/<int:project_id>/add_member",
    methods=["POST"]
)
@login_required
def add_member(project_id):

    if not is_project_owner(project_id):

        return (
            "Только владелец проекта может "
            "добавлять участников.",
            403
        )


    username = request.form.get(
        "username",
        ""
    ).strip()


    if not username:

        flash(
            "Введите логин пользователя.",
            "error"
        )

        return redirect(
            f"/project/{project_id}"
        )


    conn = get_db()


    user = conn.execute(
        """
        SELECT id
        FROM users
        WHERE username = ?
        """,
        (username,)
    ).fetchone()


    if user is None:

        conn.close()

        flash(
            f"Пользователь {username} не найден.",
            "error"
        )

        return redirect(
            f"/project/{project_id}"
        )


    current_user = get_current_user()


    if user["id"] == current_user["id"]:

        conn.close()

        flash(
            "Вы уже являетесь владельцем этого проекта.",
            "error"
        )

        return redirect(
            f"/project/{project_id}"
        )


    existing = conn.execute(
        """
        SELECT 1
        FROM project_members
        WHERE project_id = ?
        AND user_id = ?
        """,
        (
            project_id,
            user["id"]
        )
    ).fetchone()


    if existing is not None:

        conn.close()

        flash(
            "Этот пользователь уже является "
            "участником проекта.",
            "error"
        )

        return redirect(
            f"/project/{project_id}"
        )


    conn.execute(
        """
        INSERT INTO project_members
        (project_id, user_id)
        VALUES (?, ?)
        """,
        (
            project_id,
            user["id"]
        )
    )


    conn.commit()
    conn.close()


    flash(
        f"Пользователь {username} "
        f"добавлен в проект.",
        "success"
    )


    return redirect(
        f"/project/{project_id}"
    )


# =========================================================
# REMOVE MEMBER
# =========================================================

@app.route(
    "/project/<int:project_id>/remove_member/<int:user_id>",
    methods=["POST"]
)
@login_required
def remove_member(
    project_id,
    user_id
):

    if not is_project_owner(project_id):

        return (
            "Только владелец проекта может "
            "удалять участников.",
            403
        )


    current_user = get_current_user()


    if user_id == current_user["id"]:

        flash(
            "Владельца проекта нельзя удалить.",
            "error"
        )

        return redirect(
            f"/project/{project_id}"
        )


    conn = get_db()


    removed = conn.execute(
        """
        DELETE FROM project_members
        WHERE project_id = ?
        AND user_id = ?
        """,
        (
            project_id,
            user_id
        )
    )

    if not removed.rowcount:
        conn.close()
        flash('Этот пользователь не является участником проекта.', 'error')
        return redirect(f'/project/{project_id}')
    conn.execute('DELETE FROM team_member_roles WHERE project_id=? AND user_id=?', (project_id, user_id))
    conn.execute("UPDATE team_invitations SET state='cancelled',updated_at=CAST(CURRENT_TIMESTAMP AS TEXT) WHERE project_id=? AND invitee_id=? AND state='pending'", (project_id, user_id))
    conn.execute(
        """
        UPDATE tasks
        SET assignee_id = NULL
        WHERE project_id = ?
        AND assignee_id = ?
        """,
        (project_id, user_id)
    )


    conn.commit()
    conn.close()


    flash(
        "Участник удалён из проекта.",
        "success"
    )


    return redirect(
        f"/project/{project_id}"
    )


# =========================================================
# ADD TASK
# =========================================================

@app.route(
    "/project/<int:project_id>/add",
    methods=["POST"]
)
@login_required
def add_project_task(project_id):

    if not is_project_owner(project_id):

        return (
            "Проект не найден или у вас нет доступа.",
            403
        )


    title = request.form.get(
        "title",
        ""
    ).strip()


    deadline = request.form.get(
        "deadline",
        ""
    ).strip()


    priority = request.form.get(
        "priority",
        "normal"
    )

    assignee_id = request.form.get(
        "assignee_id",
        type=int
    )


    if title:

        if len(title) > 160:

            flash(
                "Название задачи должно быть не длиннее 160 символов.",
                "error"
            )

            return redirect(f"/project/{project_id}")

        if not is_valid_date(deadline):

            flash(
                "Укажите корректную дату дедлайна.",
                "error"
            )

            return redirect(f"/project/{project_id}")

        add_task(
            project_id,
            title,
            deadline,
            priority,
            assignee_id
        )


    return redirect(
        f"/project/{project_id}"
    )


# =========================================================
# CHANGE TASK STATUS
# =========================================================

@app.route(
    "/project/<int:project_id>/status",
    methods=["POST"]
)
@login_required
def change_task_status(project_id):

    if not user_has_project_access(project_id):

        return jsonify({
            "success": False,
            "error": "Нет доступа"
        }), 403


    data = request.get_json(
        silent=True
    )


    if not isinstance(data, dict) or not data:

        return jsonify({
            "success": False,
            "error": "Нет данных"
        }), 400


    task_id = data.get("task_id")
    status = data.get("status")


    if not valid_database_id(task_id) or not isinstance(status, str):

        return jsonify({
            "success": False,
            "error": "Неверный task_id"
        }), 400

    if not user_can_update_task(project_id, task_id):

        return jsonify({
            "success": False,
            "error": "Вы можете менять статус только своих задач"
        }), 403


    success = update_task_status(
        project_id,
        task_id,
        status
    )


    if not success:

        return jsonify({
            "success": False,
            "error": "Не удалось изменить статус"
        }), 400


    tasks = get_tasks(project_id)
    counts = {state: sum(task['status'] == state for task in tasks) for state in ('todo', 'progress', 'done')}
    counts['total'] = len(tasks)
    counts['overdue'] = sum(task['status'] != 'done' and bool(task['deadline']) and
                            is_valid_date(task['deadline']) and task['deadline'] < date.today().isoformat()
                            for task in tasks)
    counts['percent'] = percent(counts['done'], counts['total'])
    students = [dict(id=student['id'], done=student['completed_count'], total=student['task_count'])
                for student in get_student_progress(project_id)]
    return jsonify(success=True, metrics=counts, students=students, deadline_text=task_deadline(tasks))


@app.route(
    "/project/<int:project_id>/task/<int:task_id>/update",
    methods=["POST"]
)
@login_required
def submit_task_update(project_id, task_id):

    user = get_current_user()

    if user["role"] != "student" or not user_has_project_access(project_id):
        return (
            "Отчёт может отправить только ученик, которому назначена задача.",
            403
        )

    body = request.form.get("body", "").strip()

    if not body:
        flash("Напишите короткий отчёт перед отправкой.", "error")
        return redirect(f"/project/{project_id}")

    if len(body) > 500:
        flash("Отчёт должен быть не длиннее 500 символов.", "error")
        return redirect(f"/project/{project_id}")

    conn = get_db()
    assigned = conn.execute(
        "SELECT 1 FROM tasks WHERE id = ? AND project_id = ? AND assignee_id = ?",
        (task_id, project_id, user["id"])
    ).fetchone()
    conn.close()

    if assigned is None:
        return (
            "Отчёт можно отправить только по назначенной вам задаче.",
            403
        )

    add_task_update(project_id, task_id, user["id"], body)
    flash("Отчёт сохранён в истории задачи.", "success")
    return redirect(f"/project/{project_id}")


# =========================================================
# CHANGE TASK PRIORITY
# =========================================================

@app.route(
    "/project/<int:project_id>/priority",
    methods=["POST"]
)
@login_required
def change_task_priority(project_id):

    if not is_project_owner(project_id):

        return jsonify({
            "success": False,
            "error": "Нет доступа"
        }), 403


    data = request.get_json(
        silent=True
    )


    if not isinstance(data, dict) or not data:

        return jsonify({
            "success": False,
            "error": "Нет данных"
        }), 400


    task_id = data.get(
        "task_id"
    )

    priority = data.get(
        "priority"
    )


    if not valid_database_id(task_id) or not isinstance(priority, str):

        return jsonify({
            "success": False,
            "error": "Неверный task_id"
        }), 400


    success = update_task_priority(
        project_id,
        task_id,
        priority
    )


    if not success:

        return jsonify({
            "success": False,
            "error": "Не удалось изменить приоритет"
        }), 400


    return jsonify({
        "success": True
    })


# =========================================================
# DELETE TASK
# =========================================================

@app.route(
    "/project/<int:project_id>/delete/<int:task_id>",
    methods=["POST"]
)
@login_required
def delete_project_task(
    project_id,
    task_id
):

    if not is_project_owner(project_id):

        return (
            "Проект не найден или у вас нет доступа.",
            403
        )


    delete_task(
        project_id,
        task_id
    )


    return redirect(
        f"/project/{project_id}"
    )


# =========================================================
# TEACHER CABINET
# =========================================================

TEACHER_STYLE = ""


def percent(done, total):
    return round(done / total * 100) if total else 0


def render_header(user):
    nav = teacher_nav(user)
    return f"""
    <header class="main-header">
        <a href="/" class="brand">M<span>-</span>Flow</a>
        <div class="account">
            <div class="account-text">
                <strong>{escape(user["username"])}</strong>
                <small>{"Учитель" if user["role"] == "teacher" else "Ученик"}</small>
            </div>
            <div class="avatar">{escape(user["username"][0].upper())}</div>
            {nav}
            <a href="/profile" class="logout">Профиль</a>
            {logout_form()}
        </div>
    </header>
    """


@app.route('/settings')
def site_settings():
    user = get_current_user()
    header = render_header(user) if user else '<header class="main-header"><a class="brand" href="/login">M<span>-</span>Flow</a><a class="back" href="/login">← К входу</a></header>'
    return render_page(f'''<div class="container">{header}<main class="settings-page">
        <h1>Настройки сайта</h1>
        <p class="settings-intro">Оформление сохраняется в этом браузере и действует на всех страницах. Настройки аккаунта остаются в профиле.</p>
        <section class="card settings-card" aria-labelledby="appearance-heading">
            <h2 id="appearance-heading">Оформление</h2>
            <form id="site-preferences" onsubmit="return false">
                <label for="site-theme">Тема сайта</label>
                <select id="site-theme" name="theme">
                    <option value="system">Как в системе</option><option value="light">Светлая</option><option value="dark">Тёмная</option>
                </select>
                <p class="settings-hint">Системная тема автоматически подстраивается под настройки устройства.</p>
                <label class="settings-checkbox" for="site-animations"><input id="site-animations" name="animations" type="checkbox" checked>Плавные анимации интерфейса</label>
                <p class="settings-hint">Если на устройстве включено уменьшение движения, анимации отключены независимо от этого переключателя.</p>
                <button type="button" class="secondary" id="reset-preferences">Сбросить настройки</button>
                <p id="preferences-status" role="status" aria-live="polite">Изменения применяются сразу.</p>
                <noscript><p class="error">Для изменения и сохранения оформления включите JavaScript в браузере.</p></noscript>
            </form>
        </section></main></div>''')


def collaboration_token():
    return csrf_token()


def render_collaboration_page(user, title, body, project_id=None, error="", status=200):
    messages = ''.join(f'<div class="success" role="status">{escape(message)}</div>' for message in get_flashed_messages())
    back = f'<a class="back" href="/project/{project_id}">← К задачам проекта</a>' if project_id else '<a class="back" href="/">← К моим проектам</a>'
    error_html = f'<div class="error" role="alert">{escape(error)}</div>' if error else ''
    return render_page(PAGE_STYLE + f'''<link rel="stylesheet" href="/static/teamwork.css"><div class="container">{render_header(user)}
        <main class="team-page">{back}<h1>{escape(title)}</h1>{messages}{error_html}{body}</main></div>'''), status


@app.route('/skills', methods=['GET','POST'])
@login_required
def skill_profile():
    user = get_current_user()
    if user['role'] != 'student':
        return render_error_page('Нет доступа', 'Учебный профиль предназначен для учеников.', 403)
    token = collaboration_token()
    conn = get_db()
    error, status = '', 200
    try:
        if request.method == 'POST':
            try:
                save_profile(conn, user['id'], request.form)
                flash('Учебный профиль сохранён.')
                return redirect('/skills')
            except ValueError as exc:
                error, status = str(exc), 400
        body = render_skill_profile(profile_for(conn, user['id']), token, request.form if error else None)
    finally:
        conn.close()
    return render_collaboration_page(user, 'Мои навыки и направление', body, error=error, status=status)


@app.route('/teams')
@login_required
def teams_directory():
    user = get_current_user()
    token = collaboration_token()
    filters = {key: request.args.get(key,'').strip()[:100] for key in ('q','skill','direction','class_name')}
    conn = get_db()
    try:
        candidates = directory(conn,user['id'],query=filters['q'],direction=filters['direction'],class_name=filters['class_name'],skill=filters['skill'])
        projects = conn.execute("SELECT p.id,p.name FROM projects p JOIN users u ON u.id=p.owner_id WHERE p.owner_id=? AND u.role='student' ORDER BY p.id DESC",(user['id'],)).fetchall()
        invitations = conn.execute("""SELECT i.*,p.name AS project_name,u.username AS sender_name FROM team_invitations i
            JOIN projects p ON p.id=i.project_id JOIN users u ON u.id=i.sender_id
            WHERE i.invitee_id=? AND i.state='pending' ORDER BY i.id DESC""",(user['id'],)).fetchall()
        body = render_directory(candidates,projects,invitations,token,filters,user['role']=='student')
    finally:
        conn.close()
    return render_collaboration_page(user,'Команды и участники',body)


@app.route('/teams/invite', methods=['POST'])
@login_required
def send_team_invitation():
    user = get_current_user()
    if user['role'] != 'student':
        return render_error_page('Нет доступа', 'Команду формирует ученик-владелец проекта.', 403)
    conn = get_db()
    try:
        try:
            invite_student(conn,user['id'],request.form)
        except ValueError as exc:
            return render_collaboration_page(user,'Приглашение не отправлено','<a class="back" href="/teams">← В каталог участников</a>',error=str(exc),status=400)
    finally:
        conn.close()
    flash('Приглашение отправлено. Ученик получит доступ к проекту после принятия.')
    return redirect('/teams')


@app.route('/teams/invitations/<int:invitation_id>/respond',methods=['POST'])
@login_required
def answer_team_invitation(invitation_id):
    user = get_current_user()
    if user['role'] != 'student':
        return render_error_page('Нет доступа','Приглашения в команды адресованы ученикам.',403)
    conn = get_db()
    try:
        try:
            pid = respond_to_invitation(conn,invitation_id,user['id'],request.form.get('action'))
        except PermissionError as exc:
            return render_error_page('Нет доступа',str(exc),403)
        except ValueError as exc:
            return render_collaboration_page(user,'Приглашение не обработано','<a class="back" href="/teams">← К приглашениям</a>',error=str(exc),status=400)
    finally:
        conn.close()
    accepted = request.form.get('action') == 'accept'
    flash('Вы присоединились к команде проекта.' if accepted else 'Приглашение отклонено.')
    return redirect(f'/project/{pid}/team' if accepted else '/teams')


@app.route('/project/<int:project_id>/team',methods=['GET','POST'])
@login_required
def project_team(project_id):
    user = get_current_user()
    name = get_project_name(project_id)
    if name is None:
        return render_error_page('Нет доступа','Вы не участвуете в этом проекте.',403)
    owner = is_project_owner(project_id)
    if request.method == 'POST' and not owner:
        return render_error_page('Нет доступа','Управлять составом и ролями может только владелец проекта.',403)
    token = collaboration_token()
    conn = get_db()
    error, status = '',200
    try:
        if request.method == 'POST':
            try:
                change_team(conn,project_id,request.form)
                flash('Команда обновлена.')
                return redirect(f'/project/{project_id}/team')
            except ValueError as exc:
                conn.rollback()
                error,status = str(exc),400
        body = render_team(conn,project_id,owner,token)
    finally:
        conn.close()
    return render_collaboration_page(user,f'Команда · {name}',body,project_id,error,status)


@app.route('/project/<int:project_id>/defense',methods=['GET','POST'])
@login_required
def project_video_defense(project_id):
    user = get_current_user()
    name = get_project_name(project_id)
    if name is None:
        return render_error_page('Нет доступа','Вы не участвуете в этом проекте.',403)
    token = collaboration_token()
    conn = get_db()
    error,status,submitted = '',200,None
    try:
        if request.method == 'POST':
            try:
                flash(change_defense(conn,project_id,user,request.form))
                return redirect(f'/project/{project_id}/defense')
            except ValueError as exc:
                error,status = str(exc),400
        record = defense_for(conn,project_id)
        if error and request.form.get('revision') == str(record['revision']):
            submitted = request.form
        body = render_defense(record,user,token,submitted,has_teacher=has_project_teacher(conn, project_id))
    finally:
        conn.close()
    return render_collaboration_page(user,f'Видеозащита · {name}',body,project_id,error,status)


@app.route("/project/<int:project_id>/stages", methods=["GET", "POST"])
@login_required
def project_stage_page(project_id):
    user = get_current_user()
    project_name = get_project_name(project_id)
    if project_name is None:
        return render_error_page("Нет доступа", "Вы не участвуете в этом проекте.", 403)
    token = csrf_token()
    conn = get_db()
    error, status, submitted = "", 200, None
    try:
        if request.method == "POST":
            try:
                message = change_stage(conn, project_id, user, request.form)
                flash(message)
                return redirect(f"/project/{project_id}/stages")
            except ValueError as exc:
                error, status, submitted = str(exc), 400, request.form
        stages = get_stages(conn, project_id)
        has_teacher = has_project_teacher(conn, project_id)
        _, current = stage_summary(stages)
        if submitted is not None and (current is None or submitted.get("number") != str(current["number"])
                                      or submitted.get("revision") != str(current["revision"])):
            submitted = None
    finally:
        conn.close()
    messages = "".join(f'<p role="status">{escape(message)}</p>' for message in get_flashed_messages())
    return render_page(PAGE_STYLE + f'''
        <link rel="stylesheet" href="/static/project-stages.css"><link rel="stylesheet" href="/static/project-workspace.css?v=20261006-route"><div class="container">{render_header(user)}
        <main class="stages-page"><a class="back" href="/project/{project_id}">← К задачам проекта</a>
        <h1>Пять этапов · {escape(project_name)}</h1>
        <p>Ученики сохраняют результат и отправляют его учителю. После принятия открывается следующий этап.
        Этапы общие для участников проекта; их прохождение не меняет статусы задач и итоговые баллы.</p>
        {messages}<p role="alert">{escape(error)}</p>{render_stages(stages, user, token, submitted, has_teacher=has_teacher)}</main></div>'''), status


@app.route("/project/<int:project_id>/assessment", methods=["GET", "POST"])
@login_required
def project_assessment(project_id):
    user = get_current_user()
    project_name = get_project_name(project_id)
    if project_name is None:
        return render_error_page("Нет доступа", "Вы не участвуете в этом проекте.", 403)
    is_teacher = user["role"] == "teacher"
    if request.method == "POST" and not is_teacher:
        return render_error_page("Нет доступа", "Оценивать проекты может только учитель.", 403)
    token = csrf_token()
    conn = get_db()
    students = conn.execute("""
        SELECT id, username FROM users WHERE role = 'student' AND (
            id IN (SELECT user_id FROM project_members WHERE project_id = ?)
            OR id = (SELECT owner_id FROM projects WHERE id = ?)) ORDER BY username
        """, (project_id, project_id)).fetchall()
    error = ""
    status = 200
    content = ""
    if is_teacher and students:
        raw_id = request.form.get("student_id") if request.method == "POST" else request.args.get("student_id", str(students[0]["id"]))
        selected = next((student for student in students if str(student["id"]) == raw_id), None)
        if selected is None:
            conn.close()
            return render_error_page("Ученик не найден", "Выберите ученика этого проекта.", 400)
        row = conn.execute("SELECT * FROM project_assessments WHERE project_id=? AND student_id=? AND teacher_id=?",
                           (project_id, selected["id"], user["id"])).fetchone()
        scores = load_scores(row)
        student_name = row["student_name"] if row else selected["username"]
        note = row["note"] if row else ""
        if request.method == "POST":
            student_name = request.form.get("student_name", "").strip()
            note = request.form.get("note", "").strip()
            action = request.form.get("action", "")
            try:
                if action not in ("draft", "published"):
                    raise ValueError("Выберите сохранение черновика или публикацию.")
                if not student_name or len(student_name) > 100:
                    raise ValueError("Укажите ФИО ученика (до 100 символов).")
                if len(note) > 1000:
                    raise ValueError("Примечание должно содержать не больше 1000 символов.")
                scores = parse_scores(request.form, publish=action == "published")
                conn.execute("""INSERT INTO project_assessments
                    (project_id, student_id, teacher_id, student_name, rubric_version, scores_json, note, state)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project_id, student_id, teacher_id) DO UPDATE SET
                    student_name=excluded.student_name, rubric_version=excluded.rubric_version,
                    scores_json=excluded.scores_json, note=excluded.note, state=excluded.state,
                    updated_at=CAST(CURRENT_TIMESTAMP AS TEXT)""",
                    (project_id, selected["id"], user["id"], student_name, RUBRIC_VERSION,
                     json.dumps(scores), note, action))
                conn.commit()
                conn.close()
                flash("Оценка опубликована." if action == "published" else "Черновик сохранён. Ученику он не виден.")
                return redirect(f"/project/{project_id}/assessment?student_id={selected['id']}")
            except ValueError as exc:
                error = str(exc)
                status = 400
                # Keep valid entered values without treating missing criteria as zero.
                scores = {key: int(request.form[f"criterion_{key}"]) if request.form.get(f"criterion_{key}") in
                          {str(v) for v in range(maximum + 1)} else None for key, _, maximum in CRITERIA}
        links = " ".join(f'<a class="assessment-person" href="?student_id={s["id"]}" aria-current="{str(s["id"] == selected["id"]).lower()}">{escape(s["username"])}</a>' for s in students)
        fields = ""
        for key, label, maximum in CRITERIA:
            options = '<option value="">Не оценено</option>' + "".join(
                f'<option value="{v}" {"selected" if scores.get(key) == v else ""}>{v}</option>' for v in range(maximum + 1))
            fields += f'<tr><td data-label="Критерий">{key}. {escape(label)}</td><td data-label="Максимум">{maximum}</td><td data-label="Баллы"><select name="criterion_{key}" aria-label="{escape(label)}">{options}</select></td></tr>'
        total, grade = summarize(scores)
        state = ("Опубликована" if row["state"] == "published" else "Черновик") if row else "Ещё не сохранена"
        saved = f'{state} · {escape(row["updated_at"])}' if row else state
        content = f"""<nav class="assessment-people">{links}</nav><section class="card">
            <h2>Оценивание: {escape(selected['username'])}</h2><p>{saved}</p>
            <form method="post" id="assessment-form">
                <input type="hidden" name="student_id" value="{selected['id']}">
                <input type="hidden" name="csrf_token" value="{token}">
                <label>ФИО ученика<input name="student_name" maxlength="100" required value="{escape(student_name, quote=True)}"></label>
                <div class="assessment-table"><table><thead><tr><th>Критерий</th><th>Максимум</th><th>Баллы</th></tr></thead><tbody>{fields}</tbody></table></div>
                <p id="assessment-total" aria-live="polite">Итого: {total} / {MAX_SCORE} · Оценка: {grade if grade is not None else 'не все критерии заполнены'}</p>
                <label>Примечание учителя<textarea name="note" maxlength="1000" rows="4">{escape(note)}</textarea></label>
                <p>Черновик виден только вам. Публикация открывает оценку ученику. Сохранение опубликованной оценки как черновика снова скроет её.</p>
                <div class="assessment-actions"><button name="action" value="draft" type="submit">Сохранить черновик</button><button name="action" value="published" type="submit">Опубликовать оценку</button></div>
            </form></section>"""
    elif is_teacher:
        if request.method == "POST":
            conn.close()
            return render_error_page("Ученик не найден", "В проекте нет учеников для оценивания.", 400)
        content = '<section class="card"><h2>Пока нет учеников</h2><p>Добавьте учеников в участники проекта, чтобы выставлять им баллы.</p></section>'
    else:
        rows = conn.execute("""SELECT a.*, u.username AS teacher_name FROM project_assessments a
            JOIN users u ON u.id=a.teacher_id WHERE a.project_id=? AND a.student_id=? AND a.state='published'
            ORDER BY a.updated_at DESC, a.teacher_id""", (project_id, user["id"])).fetchall()
        for row in rows:
            scores = load_scores(row)
            total, grade = summarize(scores)
            fields = "".join(f'<tr><td data-label="Критерий">{escape(label)}</td><td data-label="Баллы">{scores.get(key)} / {maximum}</td></tr>' for key, label, maximum in CRITERIA)
            content += f'''<section class="card"><h2>{escape(row['student_name'])}</h2>
                <p>Учитель: {escape(row['teacher_name'])} · {escape(row['updated_at'])}</p>
                <div class="assessment-table"><table><thead><tr><th>Критерий</th><th>Баллы</th></tr></thead><tbody>{fields}</tbody></table></div>
                <h3>Итого: {total} / {MAX_SCORE} · Оценка: {grade}</h3><p class="assessment-note">{escape(row['note'])}</p></section>'''
        if not rows:
            content = '<section class="card"><h2>Оценка ещё не опубликована</h2><p>Здесь появятся ваши баллы и примечание учителя.</p></section>'
    conn.close()
    messages = "".join(f'<p role="status">{escape(message)}</p>' for message in get_flashed_messages())
    return render_page(PAGE_STYLE + f'''<link rel="stylesheet" href="/static/assessment.css">
        <div class="app">{render_header(user)}<main class="main-content assessment-page">
        <a class="back" href="/project/{project_id}">← К задачам проекта</a>
        <h1>Оценивание · {escape(project_name)}</h1>
        <p>12 критериев · максимум 45 баллов. Шкала: «5» — 39–45, «4» — 30–38, «3» — 21–29, «2» — 0–20.</p>
        {messages}<p role="alert">{escape(error)}</p>{content}</main></div>
        <script src="/static/assessment.js"></script>'''), status


def build_teacher_overview(teacher):

    conn = get_db()
    projects = conn.execute(
        """
        SELECT id, name, owner_id
        FROM projects
        WHERE owner_id = ? OR EXISTS (
            SELECT 1 FROM project_members
            WHERE project_members.project_id = projects.id
            AND project_members.user_id = ?
        )
        ORDER BY id DESC
        """,
        (teacher["id"], teacher["id"])
    ).fetchall()
    # A fixed number of queries for all accessible projects, not per project.
    # Repeat the access scope in SQL so private projects cannot enter a batch.
    scope = """WITH visible_projects AS (
        SELECT id FROM projects WHERE owner_id=? OR EXISTS (
            SELECT 1 FROM project_members m
            WHERE m.project_id=projects.id AND m.user_id=?
        )
    ) """
    params = (teacher["id"], teacher["id"])

    def grouped(sql):
        result = defaultdict(list)
        for row in conn.execute(scope + sql, params):
            result[row["project_id"]].append(row)
        return result

    try:
        tasks_by_project = grouped("""
            SELECT t.*, u.username AS assignee_name FROM tasks t
            JOIN visible_projects v ON v.id=t.project_id
            LEFT JOIN users u ON u.id=t.assignee_id
            ORDER BY CASE t.priority WHEN 'urgent' THEN 1 WHEN 'high' THEN 2
                WHEN 'normal' THEN 3 WHEN 'low' THEN 4 ELSE 5 END, t.id""")
        students_by_project = grouped("""
            SELECT m.project_id, u.id, u.username, u.role FROM project_members m
            JOIN visible_projects v ON v.id=m.project_id
            JOIN users u ON u.id=m.user_id WHERE u.role='student'
            ORDER BY u.username""")
        stages_by_project = grouped("""
            SELECT s.*, author.username AS author_name, reviewer.username AS reviewer_name
            FROM project_stages s JOIN visible_projects v ON v.id=s.project_id
            LEFT JOIN users author ON author.id=s.submitted_by
            LEFT JOIN users reviewer ON reviewer.id=s.reviewed_by""")
        defenses_by_project = grouped("""
            SELECT d.project_id, d.state, d.revision FROM project_defenses d
            JOIN visible_projects v ON v.id=d.project_id""")
        reports_by_project = grouped("""
            SELECT t.project_id, r.task_id, r.body, r.created_at, u.username
            FROM tasks t JOIN visible_projects v ON v.id=t.project_id
            JOIN task_updates r ON r.id=(
                SELECT MAX(latest.id) FROM task_updates latest WHERE latest.task_id=t.id
            )
            JOIN users u ON u.id=r.user_id ORDER BY r.id DESC""")
    finally:
        conn.close()

    today = date.today()
    overview = []

    for project_row in projects:

        pid = project_row["id"]
        completed_stages, current_stage = stage_summary(stages_from_rows(stages_by_project[pid]))
        video_defense = (defenses_by_project[pid][0] if defenses_by_project[pid]
                         else {"revision": 0})
        needs_review = bool(current_stage and current_stage['state'] == 'review') or bool(video_defense['revision'] and video_defense['state'] == 'submitted')
        tasks = tasks_by_project[pid]
        students = students_by_project[pid]

        stats = {
            student["id"]: {
                "id": student["id"],
                "name": student["username"],
                "total": 0,
                "done": 0,
                "overdue": 0,
            }
            for student in students
        }

        counts = {"todo": 0, "progress": 0, "done": 0, "overdue": 0,
                  "soon": 0, "unassigned": 0}
        attention = []

        for task in tasks:

            status = task["status"] if task["status"] in ("todo", "progress", "done") else "todo"
            counts[status] += 1

            days_left = None

            if task["deadline"]:
                try:
                    days_left = (
                        date.fromisoformat(task["deadline"]) - today
                    ).days
                except ValueError:
                    days_left = None

            is_overdue = (
                status != "done"
                and days_left is not None
                and days_left < 0
            )

            if is_overdue:
                counts["overdue"] += 1
            if status != "done" and days_left is not None and 0 <= days_left <= 3:
                counts["soon"] += 1
            if status != "done" and task["assignee_id"] not in stats:
                counts["unassigned"] += 1

            student_stat = stats.get(task["assignee_id"])

            if student_stat is not None:
                student_stat["total"] += 1
                if status == "done":
                    student_stat["done"] += 1
                if is_overdue:
                    student_stat["overdue"] += 1

            if status == "done":
                continue

            reasons = []

            if is_overdue:
                reasons.append(f"просрочено на {-days_left} дн.")
            elif days_left is not None and days_left <= 3:
                reasons.append(
                    "срок сегодня" if days_left == 0
                    else f"срок через {days_left} дн."
                )

            if task["assignee_id"] not in stats:
                reasons.append("нет ответственного")

            if reasons:
                attention.append({
                    "task": task,
                    "reasons": reasons,
                    "sort": days_left if days_left is not None else 9999,
                })

        attention.sort(key=lambda item: item["sort"])

        task_titles = {task["id"]: task["title"] for task in tasks}
        overview.append({
            "id": pid,
            "completed_stages": completed_stages,
            "defense_state": DEFENSE_STATES[video_defense['state']] if video_defense['revision'] else 'Запись ещё не добавлена',
            "current_stage": current_stage,
            "name": project_row["name"],
            "total": len(tasks),
            "counts": counts,
            "progress": percent(counts["done"], len(tasks)),
            "students": list(stats.values()),
            "attention": attention,
            "can_manage": False,
            "legacy_owner": project_row['owner_id'] == teacher['id'],
            "needs_review": needs_review,
            "health": (
                "overdue" if counts["overdue"] else
                "attention" if counts["soon"] or counts["unassigned"] or needs_review else
                "done" if tasks and counts["done"] == len(tasks) else
                "empty" if not tasks else "normal"
            ),
            "reports": [
                {"task_id": report["task_id"], "task_title": task_titles[report["task_id"]], "report": report}
                for report in reports_by_project[pid]
            ],
        })

    return overview


def render_attention_task(project_id, item):
    task = item["task"]
    return f'''
        <div class="t-edit">
            <span class="t-title">{escape(task["title"])}</span>
            <span class="t-reason">{escape(" · ".join(item["reasons"]))}</span>
            <a class="back" href="/project/{project_id}#task-{task['id']}">Открыть задачу →</a>
        </div>'''


def render_teacher_project(project):

    counts = project["counts"]
    health_labels = {"overdue": "Есть просрочки", "attention": "Требует внимания",
                     "done": "Задачи завершены", "normal": "Всё по плану", "empty": "Нет задач"}
    health = project["health"]
    stage = project["current_stage"]
    stage_note = (f'Текущий этап {stage["number"]}: {escape(stage["title"])} · {STATE_LABELS[stage["state"]]}'
                  if stage else 'Все пять этапов приняты учителем')
    access_note = '<span class="t-muted">Проверка результатов · задачами и командой управляет ученик-владелец</span>'
    if project['legacy_owner']:
        options = ''.join(f'<option value="{student["id"]}">{escape(student["name"])}</option>' for student in project['students'])
        access_note += ('<p>Этот старый проект создан учителем. Передайте управление ученику: задания и результаты сохранятся, вы останетесь учителем проекта.</p>'
                       + f'<form method="post" action="/project/{project["id"]}/transfer">{csrf_input()}<label>Ученик-владелец<select name="student_id"><option value="">Выберите ученика</option>{options}</select></label><label>Или логин ученика<input name="student_username" maxlength="30" placeholder="Если ученика пока нет в проекте"></label><button type="submit">Передать проект ученику</button></form>')
    overdue_chip = (
        f'<span class="t-chip bad">Просрочено: {counts["overdue"]}</span>'
        if counts["overdue"] else
        '<span class="t-chip">Просроченных нет</span>'
    )

    rows = ""

    for student in project["students"]:

        student_percent = percent(student["done"], student["total"])
        note = (
            "нет задач" if student["total"] == 0
            else f'{student["done"]} из {student["total"]}'
        )
        overdue = (
            f'<span class="t-chip bad">{student["overdue"]}</span>'
            if student["overdue"] else "—"
        )

        rows += f"""
        <tr>
            <td data-label="Ученик"><b>{escape(student["name"])}</b></td>
            <td data-label="Прогресс"><div class="t-bar"><div style="width: {student_percent}%"></div></div></td>
            <td data-label="Выполнено">{note} · {student_percent}%</td>
            <td data-label="Просрочено">{overdue}</td>
        </tr>
        """

    students_html = (
        f"""
        <table class="t-table">
            <tr><th>Ученик</th><th>Прогресс</th><th>Выполнено</th><th>Просрочено</th></tr>
            {rows}
        </table>
        """
        if rows else
        '<p class="t-muted">В проекте пока нет учеников. '
        'Ученик-владелец формирует команду на странице проекта.</p>'
    )

    attention_html = ""

    if project["attention"]:
        items = "".join(
            render_attention_task(project["id"], item)
            for item in project["attention"]
        )
        attention_html = f'<div class="t-sub">Требуют внимания</div>{items}'

    return f"""
    <section class="t-project">
        <div class="t-project-head">
            <h2>{escape(project["name"])}</h2>
            <span class="teacher-health {health}">{health_labels[health]}</span>
            <a href="/project/{project["id"]}" class="teacher-board-link">Открыть доску →</a>
            <a href="/project/{project["id"]}/assessment" class="teacher-board-link">Оценивание →</a>
            <a href="/project/{project["id"]}/stages" class="teacher-board-link">Этапы: {project["completed_stages"]}/5 приняты →</a>
            <a href="/project/{project["id"]}/team" class="teacher-board-link">Команда →</a>
            <a href="/project/{project["id"]}/defense" class="teacher-board-link">Видеозащита →</a>
        </div>
        {access_note}
        <p class="t-muted">{stage_note}</p>
        <p class="t-muted">Видеозащита: {project['defense_state']}</p>
        <div class="t-chips">
            <span class="t-chip">Новые: {counts["todo"]}</span>
            <span class="t-chip">В работе: {counts["progress"]}</span>
            <span class="t-chip">Готово: {counts["done"]}</span>
            {overdue_chip}
            <span class="t-chip">Без ответственного: {counts["unassigned"]}</span>
        </div>
        <div class="t-bar"><div style="width: {project["progress"]}%"></div></div>
        <p class="t-muted">{counts["done"]} из {project["total"]} задач · {project["progress"]}%</p>
        <details class="teacher-students"><summary>Прогресс учеников ({len(project["students"])})</summary>
            <div class="teacher-table-scroll">{students_html}</div>
        </details>
        {attention_html}
    </section>
    """


@app.route("/teacher")
@login_required
def teacher_cabinet():

    user = get_current_user()

    if user["role"] != "teacher":
        return render_error_page(
            "Нет доступа",
            "Кабинет доступен только учителям.",
            403
        )

    overview = build_teacher_overview(user)

    query = request.args.get("q", "").strip()[:100]
    focus = request.args.get("focus", "all")
    if focus not in ("all", "overdue", "attention", "done"):
        focus = "all"
    visible_projects = [
        project for project in overview
        if (not query or query.casefold() in project["name"].casefold()
            or any(query.casefold() in student["name"].casefold()
                   for student in project["students"]))
        and (focus == "all" or (focus != 'done' and project["health"] == focus)
             or focus == 'done' and project['total'] > 0 and project['counts']['done'] == project['total']
             or focus == "attention" and bool(project["attention"]))
    ]

    total_tasks = sum(p["total"] for p in overview)
    done_tasks = sum(p["counts"]["done"] for p in overview)
    overdue_tasks = sum(p["counts"]["overdue"] for p in overview)
    soon_tasks = sum(p["counts"]["soon"] for p in overview)
    unassigned_tasks = sum(p["counts"]["unassigned"] for p in overview)
    student_ids = {
        s["id"] for p in overview for s in p["students"]
    }

    messages = ""

    for category, text in session.pop("_flashes", []):
        messages += f'<div class="{category}">{escape(text)}</div>'

    projects_html = "".join(
        render_teacher_project(project) for project in visible_projects
    ) or ("""
        <div class="empty">
            <h3>Ничего не найдено</h3>
            <p>Измените запрос или <a href="/teacher">сбросьте фильтры</a>.</p>
        </div>
    """ if overview else """
        <div class="empty">
            <h3>Ваше рабочее пространство готово</h3>
            <p>Попросите ученика создать проект и добавить ваш логин в участники. Здесь появятся его результаты для проверки.</p>
        </div>
    """)

    focus_options = "".join(
        f'<option value="{value}" {"selected" if focus == value else ""}>{label}</option>'
        for value, label in (("all", "Все проекты"), ("overdue", "Есть просрочки"),
                             ("attention", "Требуют внимания"), ("done", "Все задачи выполнены"))
    )
    reports = sorted(
        [(project, entry) for project in visible_projects for entry in project["reports"]],
        key=lambda pair: pair[1]["report"]["created_at"], reverse=True
    )[:6]
    reports_html = "".join(
        f'''<article class="teacher-report-item">
            <div class="teacher-report-meta"><strong>{escape(entry["report"]["username"])}</strong>
                <time>{escape(entry["report"]["created_at"])} UTC</time></div>
            <a href="/project/{project["id"]}#task-{entry["task_id"]}">{escape(project["name"])} · {escape(entry["task_title"])}</a>
            <p>{escape(entry["report"]["body"])}</p>
        </article>'''
        for project, entry in reports
    ) or '<p class="t-muted">Здесь появятся отчёты учеников по назначенным задачам.</p>'

    attention_items = sorted(
        [(project, item) for project in visible_projects for item in project["attention"]],
        key=lambda pair: pair[1]["sort"]
    )[:6]
    attention_html = "".join(
        f'''<a class="teacher-attention-item" href="/project/{project["id"]}#task-{item["task"]["id"]}">
            <strong>{escape(item["task"]["title"])}</strong>
            <span>{escape(project["name"])} · {escape(" · ".join(item["reasons"]))}</span>
        </a>'''
        for project, item in attention_items
    ) or '<p class="t-muted">В выбранных проектах нет задач, требующих внимания.</p>'
    review_links = ''.join(
        (f'<a class="teacher-attention-item" href="/project/{p["id"]}/stages"><strong>Этап ожидает проверки</strong><span>{escape(p["name"])}</span></a>' if p['current_stage'] and p['current_stage']['state'] == 'review' else '')
        + (f'<a class="teacher-attention-item" href="/project/{p["id"]}/defense"><strong>Видеозащита ожидает проверки</strong><span>{escape(p["name"])}</span></a>' if p['defense_state'] == DEFENSE_STATES['submitted'] else '')
        for p in visible_projects if p['needs_review'])
    if review_links:
        attention_html = review_links + (attention_html if attention_items else '')

    overdue_class = "warn" if overdue_tasks else ""
    review_count = sum(bool(p['current_stage'] and p['current_stage']['state'] == 'review')
                       + bool(p['defense_state'] == DEFENSE_STATES['submitted']) for p in overview)

    return render_page(
        PAGE_STYLE
        + TEACHER_STYLE
        + f"""
        <div class="container workspace teacher-workspace">
            {render_header(user)}
            <main>
                <div class="workspace-intro"><span>РАБОЧЕЕ ПРОСТРАНСТВО УЧИТЕЛЯ</span><span>M-Flow / проверка</span></div>
                <section class="teacher-hero workspace-hero">
                    <div class="hero-copy"><span class="hero-kicker">КОНТРОЛЬ ПРОЕКТОВ</span>
                        <h1>Кабинет учителя</h1>
                        <p>{escape(user["username"])}, всё важное — перед вами.<br>Помогайте ученикам двигаться от идеи к результату.</p>
                        <a class="btn" href="#review-queue">Перейти к проверке <span aria-hidden="true">↗</span></a>
                    </div>
                    <img class="hero-art" src="/static/project-kit.svg" alt="" width="480" height="360">
                    <div class="review-counter"><strong>{review_count}</strong><span>на проверке<br>этапы и видеозащиты</span></div>
                </section>
                {messages}
                <div class="t-stats">
                    <div class="t-stat"><strong>{len(overview)}</strong><span>Проектов</span></div>
                    <div class="t-stat"><strong>{len(student_ids)}</strong><span>Учеников</span></div>
                    <div class="t-stat"><strong>{total_tasks}</strong><span>Всего задач</span></div>
                    <div class="t-stat"><strong>{percent(done_tasks, total_tasks)}%</strong><span>Выполнено</span></div>
                    <div class="t-stat {overdue_class}"><strong>{overdue_tasks}</strong><span>Просрочено</span></div>
                </div>
                <div class="teacher-signal-strip">
                    <span>В ближайшие 3 дня: <b>{soon_tasks}</b></span>
                    <span>Без ответственного: <b>{unassigned_tasks}</b></span>
                </div>
                <form class="teacher-filters" method="get">
                    <input type="search" name="q" value="{escape(query)}"
                        aria-label="Поиск проекта или ученика" placeholder="Найти проект или ученика">
                    <select name="focus" aria-label="Состояние проектов">{focus_options}</select>
                    <button type="submit">Показать</button>
                    <a href="/teacher" class="back">Сбросить</a>
                </form>
                <div class="teacher-dashboard-grid">
                    <section class="teacher-panel review-panel" id="review-queue"><span class="eyebrow">СЛЕДУЮЩИЙ ШАГ</span><h2>Требуют внимания</h2>{attention_html}</section>
                    <section class="teacher-panel"><h2>Последние отчёты</h2>{reports_html}</section>
                </div>
                <div class="section-title"><h2>Проекты</h2>
                    <span class="t-muted">Показано {len(visible_projects)} из {len(overview)}</span></div>
                {projects_html}
                <section class="teacher-panel"><h2>Как подключиться к проекту</h2><p>Проект создаёт ученик. Сообщите ему свой логин: <strong>{escape(user['username'])}</strong>. Ученик-владелец добавит вас на доске проекта. Вы сможете проверять этапы, видеозащиту и выставлять оценки.</p></section>
                <footer class="workspace-footer"><span>M-Flow <small>by Minich</small></span><a href="/settings">Настройки оформления ↗</a></footer>
            </main>
        </div>
        """
    )


@app.route("/teacher/projects", methods=["POST"])
@login_required
def create_teacher_project():
    return render_error_page('Нет доступа', 'Проекты создают ученики. Попросите ученика добавить вас в свой проект.', 403)


@app.route('/project/<int:project_id>/transfer', methods=['POST'])
@login_required
def transfer_legacy_project(project_id):
    user = get_current_user()
    if user['role'] != 'teacher':
        return render_error_page('Нет доступа', 'Передача доступна только учителю-владельцу старого проекта.', 403)
    student_id = request.form.get('student_id', type=int)
    username = request.form.get('student_username', '').strip()
    if student_id is not None and not valid_database_id(student_id):
        return render_error_page('Некорректный ученик', 'Выберите ученика проекта.', 400)
    if len(username) > 30 or bool(student_id) == bool(username):
        return render_error_page('Выберите ученика', 'Выберите ученика из списка или укажите один логин, но не оба.', 400)
    conn = get_db()
    try:
        begin_project_write(conn, project_id)
        owned = conn.execute('SELECT id FROM projects WHERE id=? AND owner_id=?', (project_id, user['id'])).fetchone()
        if owned is None:
            return render_error_page('Нет доступа', 'Проект уже передан или принадлежит другому владельцу.', 403)
        if username:
            student = conn.execute("SELECT id FROM users WHERE username=? AND role='student'", (username,)).fetchone()
        else:
            student = conn.execute("SELECT u.id FROM users u JOIN project_members m ON m.user_id=u.id WHERE m.project_id=? AND u.id=? AND u.role='student'", (project_id, student_id)).fetchone()
        if student is None:
            return render_error_page('Ученик не найден', 'Выберите ученика проекта или существующий логин ученика.', 400)
        conn.executemany('INSERT INTO project_members(project_id,user_id) VALUES(?,?) ON CONFLICT(project_id,user_id) DO NOTHING', [(project_id, user['id']), (project_id, student['id'])])
        conn.execute('UPDATE projects SET owner_id=? WHERE id=?', (student['id'], project_id))
        conn.execute("UPDATE team_invitations SET state='cancelled',updated_at=CAST(CURRENT_TIMESTAMP AS TEXT) WHERE project_id=? AND state='pending'", (project_id,))
        conn.commit()
    finally:
        conn.close()
    flash('Проект передан ученику. Вы остались учителем проекта; задачи и результаты сохранены.')
    return redirect('/teacher')


@app.route(
    "/project/<int:project_id>/edit/<int:task_id>",
    methods=["POST"]
)
@login_required
def edit_project_task(project_id, task_id):

    if not is_project_owner(project_id):
        return (
            "Проект не найден или у вас нет доступа.",
            403
        )

    target = (
        "/teacher"
        if request.form.get("next") == "/teacher"
        else f"/project/{project_id}"
    )

    title = request.form.get("title", "").strip()
    deadline = request.form.get("deadline", "").strip()
    assignee_id = request.form.get("assignee_id", type=int)
    if assignee_id is not None and not valid_database_id(assignee_id):
        flash("Укажите корректного ответственного.", "error")
        return redirect(target)

    if len(title) > 160:
        flash("Название задачи должно быть не длиннее 160 символов.", "error")
        return redirect(target)

    if not is_valid_date(deadline):
        flash("Укажите корректную дату дедлайна.", "error")
        return redirect(target)

    conn = get_db()

    task = conn.execute(
        "SELECT title FROM tasks WHERE id = ? AND project_id = ?",
        (task_id, project_id)
    ).fetchone()

    if task is None:
        conn.close()
        flash("Задача не найдена.", "error")
        return redirect(target)

    if assignee_id is not None:
        member = conn.execute(
            """
            SELECT 1
            FROM project_members
            JOIN users ON users.id = project_members.user_id
            WHERE project_members.project_id = ?
            AND project_members.user_id = ?
            AND users.role = 'student'
            """,
            (project_id, assignee_id)
        ).fetchone()

        if member is None:
            assignee_id = None

    conn.execute(
        """
        UPDATE tasks
        SET title = ?, deadline = ?, assignee_id = ?
        WHERE id = ? AND project_id = ?
        """,
        (
            title or task["title"],
            deadline or None,
            assignee_id,
            task_id,
            project_id
        )
    )


    conn.commit()
    conn.close()

    flash("Задача обновлена.", "success")

    return redirect(target)


# =========================================================
# ERROR PAGES
# =========================================================

def render_error_page(title, message, status_code):

    return render_page(
        PAGE_STYLE
        + f"""
        <div class="login-page">
            <div class="login-box">
                <div class="login-logo">M<span>-</span>Flow</div>
                <span class="eyebrow" style="color:#b7791f">{status_code}</span>
                <h1>{escape(title)}</h1>
                <p>{escape(message)}</p>
                <a href="/" class="back">← Вернуться на главную</a>
            </div>
        </div>
        """,
    ), status_code


@app.errorhandler(404)
def page_not_found(error):

    return render_error_page(
        "Страница не найдена",
        "Проверьте адрес или вернитесь в своё рабочее пространство.",
        404
    )


@app.errorhandler(500)
def internal_server_error(error):

    app.logger.exception("Необработанная ошибка M-Flow: %s", error)

    return render_error_page(
        "Что-то пошло не так",
        "Мы уже зафиксировали ошибку. Обновите страницу или вернитесь на главную.",
        500
    )


@app.errorhandler(413)
def request_too_large(error):
    if request.is_json:
        return jsonify(success=False, error="Слишком большой запрос."), 413
    return render_error_page("Слишком много данных", "Сократите текст или ссылку и повторите отправку.", 413)


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    app.run(
        debug=os.environ.get("M_FLOW_DEBUG") == "1"
    )
