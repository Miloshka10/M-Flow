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
import mimetypes
from contextlib import closing
from database import DATABASE_ERRORS
from functools import wraps
from werkzeug.security import (
    check_password_hash,
    generate_password_hash
)
from html import escape
import json
import secrets
import time
from urllib.parse import urlencode, urlsplit, parse_qsl
from datetime import timedelta
from auth_security import (initialize_auth, reserve_attempt, client_source, proxy_networks,
                           new_session, revoke_session, fingerprint, SESSION_SECONDS, begin_auth_write)
from collections import defaultdict
from deadlines import business_today, deadline_info, deadline_text, parse_deadline, urgent_tasks, TIMEZONE_LABEL, format_timestamp
from deadlines import DEADLINE_FILTERS, TASK_SORTS, matches_deadline, task_sort_key
from notifications import (initialize_notifications, notify, notify_project,
                           notification_feed, mark_read, render_bell, render_items)
from project_rules import has_project_teacher
from teacher_supervision import (initialize_supervision, create_invite, preview_invite, request_connection,
                                invitation_action, request_end, respond_request, render_hub, participant)
from auth_security import reserve_supervision_attempt
from learning_groups import (initialize_groups, create_group, preview_code as preview_group_code,
                             join_group, change_group, own_groups, group_for, roster,
                             render_groups, render_group)
from teacher_queue import WORK_KINDS, WORK_SORTS, project_matches, queue_page, render_queue
from dashboard_view import student_dashboard
from graphics import illustration
from project_workspace import render_workspace, task_deadline
from navigation import project_navigation
from interface import shell_header, project_art, document_title, icon
from student_results import parameters as result_parameters, load_results, render_results, load_focus, load_project_states
from grading import CRITERIA, MAX_SCORE, RUBRIC_VERSION, parse_scores, summarize, load_scores
from project_stages import initialize_stages, get_stages, stages_from_rows, stage_summary, change_stage, render_stages, STATE_LABELS
from teamwork import (initialize_teamwork, profile_for, save_profile, directory, invite_student,
                      respond_to_invitation, change_team, render_profile as render_skill_profile,
                      render_directory, render_team)
from video_defense import initialize_defenses, defense_for, change_defense, render_defense, DEFENSE_STATES
from work_history import initialize_history, history_parameters, load_history, render_history
from task_board import initialize_task_board, task_fields, board_return, render_filters, create_form, task_edit_fields
from database import (DatabaseSettings, DatabaseUnavailable, INTEGRITY_ERRORS,
                      PostgresConnection, connect_database, execute_schema_script,
                      table_columns, insert_id, begin_project_write, initialization_settings, form_database_id)


# Windows may not have a system MIME registration for WebP.
mimetypes.add_type('image/webp', '.webp')
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
    PERMANENT_SESSION_LIFETIME=timedelta(seconds=SESSION_SECONDS),
    MAX_CONTENT_LENGTH=1024 * 1024,
)
TRUSTED_AUTH_PROXIES = proxy_networks(os.environ.get('M_FLOW_TRUSTED_PROXY_CIDRS', ''))
# Unknown usernames still do one password verification, rather than returning early.
DUMMY_PASSWORD_HASH = generate_password_hash(secrets.token_urlsafe(32))


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def csrf_input():
    return f'<input type="hidden" name="csrf_token" value="{escape(csrf_token(), quote=True)}">'


def logout_form():
    return f'<form method="post" action="/logout" class="logout-form">{csrf_input()}<button type="submit" class="logout" data-ui-icon="logout">Выйти</button></form>'


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
    body_class = 'mf-v2 ui-authenticated' if 'data-interface-shell=' in html else 'mf-v2'
    return ('<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{document_title(html)}</title>'
            f'<meta name="csrf-token" content="{escape(csrf_token(), quote=True)}">'
        '<link rel="stylesheet" href="/static/mflow-theme.css?v=20261005-mobile">'
        '<link rel="stylesheet" href="/static/mobile.css?v=20261006-scrollbar">'
        '<link rel="stylesheet" href="/static/motion.css?v=20261005-controls">'
        '<link rel="stylesheet" href="/static/preferences.css?v=20261005">'
        '<link rel="stylesheet" href="/static/controls.css?v=20261006-csrf">'
        '<script src="/static/preferences.js?v=20261005"></script>'
        '<script src="/static/motion.js?v=20261005-controls"></script>'
        '<link rel="stylesheet" href="/static/product-design.css?v=20261017-text-logo">'
        '<link rel="stylesheet" href="/static/graphics.css?v=20261015">'
        '<link rel="stylesheet" href="/static/notifications.css?v=20261013">'
        '<link rel="stylesheet" href="/static/deadlines.css?v=20261013-filters">'
        '<script src="/static/dashboard.js?v=20261006" defer></script>'
        '<link rel="stylesheet" href="/static/navigation.css?v=20261017-fixes">'
        '<script src="/static/navigation.js?v=20261017-fixes" defer></script>'
        '<link rel="stylesheet" href="/static/icon-symbols.css?v=20261026-icons">'
        '<link rel="stylesheet" href="/static/interface.css?v=20261026-icons">'
        '<script src="/static/interface.js?v=20261025-polish" defer></script>'
            f'</head><body id="page-content" class="{body_class}"><a class="skip-link" href="#{skip_target}">К содержимому</a>' + html + '</body></html>')


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
    initialize_history(conn)
    initialize_notifications(conn)
    initialize_auth(conn)
    initialize_supervision(conn)
    initialize_groups(conn)

    project_columns = table_columns(conn, "projects")

    if "owner_id" not in project_columns:
        conn.execute("ALTER TABLE projects ADD COLUMN owner_id INTEGER")

    column_names = table_columns(conn, "tasks")
    initialize_task_board(conn)

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

    if "user_id" not in session or not isinstance(session.get('auth_token'), str):
        return None

    # Cache only within this request, never across accounts or requests.
    user_id = session["user_id"]
    if (has_request_context() and getattr(g, "current_user_id", None) == user_id
            and getattr(g, 'current_user_auth_token', None) == session['auth_token']):
        return g.current_user

    with closing(get_db()) as conn:
        user = conn.execute(
            """
            SELECT id, username, role
            FROM users
            WHERE id = ? AND EXISTS (SELECT 1 FROM auth_sessions s
                WHERE s.user_id=users.id AND s.token_hash=? AND s.expires>?)
            """,
            (session["user_id"], fingerprint(SECRET_KEY, 'session', session['auth_token']), int(time.time()))
        ).fetchone()

    if has_request_context():
        g.current_user_id = user_id
        g.current_user_auth_token = session['auth_token']
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
            0 AS overdue"""
        join = "LEFT JOIN tasks t ON t.project_id=projects.id"
    projects = conn.execute(f"""
        SELECT projects.id, projects.name {columns}
        FROM projects {join}
        WHERE projects.owner_id=? OR EXISTS (
            SELECT 1 FROM project_members m
            WHERE m.project_id=projects.id AND m.user_id=?
        )
        GROUP BY projects.id ORDER BY projects.id DESC
        """, params).fetchall()

    if with_counts:
        # One batched read for valid calendar dates and the student's next tasks.
        # Never compare arbitrary legacy TEXT values lexicographically as dates.
        today = business_today()
        projects = [dict(row, deadlines=[]) for row in projects]
        by_id = {row['id']: row for row in projects}
        dated_tasks = conn.execute("""SELECT t.id,t.project_id,t.title,t.status,t.deadline,t.assignee_id
            FROM tasks t JOIN projects p ON p.id=t.project_id
            WHERE t.status!='done' AND t.deadline IS NOT NULL AND t.deadline!=''
            AND (p.owner_id=? OR EXISTS (SELECT 1 FROM project_members m WHERE m.project_id=p.id AND m.user_id=?))""",
            (user['id'], user['id']))
        for row in dated_tasks:
            info = deadline_info(row['deadline'], row['status'], today)
            project = by_id.get(row['project_id'])
            if project is None or not info['active']:
                continue
            project['overdue'] += info['state'] == 'overdue'
            if row['assignee_id'] in (None, user['id']):
                project['deadlines'].append(dict(row, deadline_info=info))
    conn.close()

    return projects


def is_valid_date(value):

    if not value:
        return True

    return parse_deadline(value) is not None


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
            tasks.description,
            tasks.stage_number,
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


def add_task(project_id, title, deadline, priority, assignee_id, description='', stage_number=None):
    user = get_current_user()
    conn = get_db()
    try:
        begin_project_write(conn, project_id)
        owned = conn.execute('SELECT owner_id FROM projects WHERE id=?', (project_id,)).fetchone()
        if user['role'] != 'student' or owned is None or owned['owner_id'] != user['id']:
            raise ValueError('Владелец проекта изменился. Обновите страницу.')
        if priority not in ('low','normal','high','urgent'):
            priority = 'normal'
        if assignee_id is not None and (not valid_database_id(assignee_id) or not conn.execute(
                "SELECT 1 FROM project_members m JOIN users u ON u.id=m.user_id WHERE m.project_id=? AND u.id=? AND u.role='student'",
                (project_id,assignee_id)).fetchone()):
            assignee_id = None
        task_id = insert_id(conn, "INSERT INTO tasks(project_id,title,deadline,done,status,priority,assignee_id,description,stage_number) VALUES(?,?,?,0,'todo',?,?,?,?)",
                            (project_id,title,deadline or None,priority,assignee_id,description,stage_number))
        conn.commit()
        return task_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_task(project_id, task_id):
    conn = get_db()
    try:
        # Reports use the same project lock: deletion cannot race a new report.
        begin_project_write(conn, project_id)
        removed = conn.execute('''DELETE FROM tasks WHERE id=? AND project_id=?
            AND NOT EXISTS (SELECT 1 FROM task_updates WHERE task_id=tasks.id)''', (task_id, project_id))
        conn.commit()
        return bool(removed.rowcount)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def add_task_update(project_id, task_id, user_id, body):
    conn = get_db()
    try:
        begin_project_write(conn, project_id)
        task = conn.execute('SELECT id FROM tasks WHERE id=? AND project_id=? AND assignee_id=?',
                            (task_id, project_id, user_id)).fetchone()
        if task is None:
            conn.rollback()
            return False
        # An identical repeat of the latest report is not a new report.
        previous = conn.execute('SELECT user_id,body FROM task_updates WHERE task_id=? ORDER BY id DESC LIMIT 1',
                                (task_id,)).fetchone()
        if previous and previous['user_id'] == user_id and previous['body'] == body:
            conn.rollback()
            return True
        update_id = insert_id(conn, 'INSERT INTO task_updates(task_id,user_id,body) VALUES(?,?,?)',
                              (task_id, user_id, body))
        notify_project(conn, project_id, 'teacher', f'report:{update_id}',
                       'Новый отчёт по задаче', '', f'/project/{project_id}#task-{task_id}')
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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

PAGE_STYLE = '<script src="/static/kanban.js?v=20261019-board" defer></script>'


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
            "Создайте аккаунт учителя и пригласите ученика одноразовым кодом в разделе руководства."
            if is_teacher else "Создай аккаунт ученика, начни проект и прими приглашение учителя для его сопровождения."
        )

    login_active = "active" if is_login else ""
    register_active = "" if is_login else "active"

    return render_page(
        PAGE_STYLE
        + f"""
        <div class="auth-page auth-role-{role}">
            <div class="auth-card">
                <main class="auth-form-side">
                    <div class="auth-form-inner">
                    <a href="{prefix}/login" class="brand auth-form-brand" aria-label="M-Flow — вход">M-Flow<span class="ui-brand-dot">.</span></a>
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
                        <button type="submit" data-ui-icon="arrow">{submit_label}</button>
                    </form>
                    <p class="auth-footer">{"Приглашайте учеников и проверяйте результаты в одном кабинете." if is_teacher else "Создайте проект и примите приглашение учителя."}<br><a href="/settings">Настройки оформления</a></p>
                    </div>
                </main>
                <aside class="auth-hero" aria-label="О проекте M-Flow">
                    <div class="auth-hero-top">Рабочее пространство школьных проектов</div>
                    <div class="auth-hero-content">
                        <span class="auth-product-tag">ДЛЯ БОЛЬШИХ ИДЕЙ</span>
                        <h2 class="auth-logo">От идеи<br>к результату.</h2>
                        <div class="auth-byline auth-shimmer">by Minich</div>
                        {project_art()}
                        <h3>{hero_title}</h3>
                        <p>{hero_text}</p>
                    </div>
                    <div class="auth-hero-footer"><span>01 / Тема</span><span>02–04 / Работа</span><span>05 / Документы</span></div>
                </aside>
            </div>
        </div>
        """
    )


def auth_wait(mode, role, seconds):
    seconds = max(1, int(seconds))
    return (render_auth_page(mode, error=f'Слишком много попыток. Повторите через {seconds} сек.', role=role),
            429, {'Retry-After': str(seconds)})


def auth_attempt(action, username):
    if 'auth_browser' not in session:
        session['auth_browser'] = secrets.token_urlsafe(24)
    source = client_source(request.remote_addr, request.headers.get('X-Forwarded-For', ''), TRUSTED_AUTH_PROXIES)
    return reserve_attempt(get_db, SECRET_KEY, action, username, source, session['auth_browser'])


def establish_session(user_id, token):
    browser = session.get('auth_browser')
    session.clear()
    if isinstance(browser, str):
        session['auth_browser'] = browser
    session['user_id'] = user_id
    session['auth_token'] = token
    csrf_token()


def login_for_role(role):
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        retry, soft_retry = auth_attempt('login', username)
        if retry:
            return auth_wait('login', role, retry)

        with closing(get_db()) as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE username = ?",
                (username if len(username) <= 256 else '',)
            ).fetchone()
        valid_password = check_password_hash(user['password'] if user else DUMMY_PASSWORD_HASH,
                                             password if len(password) <= 256 else '')
        if user is None or not valid_password or len(password) > 256:
            if soft_retry:
                return auth_wait('login', role, soft_retry)
            return render_auth_page("login", error="Неверный логин или пароль.", role=role)

        if user["role"] != role:
            role_name = "учителя" if user["role"] == "teacher" else "ученика"
            switch_name = "Я учитель" if user["role"] == "teacher" else "Я ученик"
            return render_auth_page(
                "login", error=f'Это аккаунт {role_name}. Выберите «{switch_name}» для входа.', role=role
            )

        token = new_session(get_db, SECRET_KEY, user['id'], session.get('auth_token'))
        establish_session(user['id'], token)
        return redirect("/teacher" if user["role"] == "teacher" else "/")

    return render_auth_page("login", role=role)


def register_for_role(role):
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        repeat_password = request.form.get("repeat_password", "")
        retry, _ = auth_attempt('register', username)
        if retry:
            return auth_wait('register', role, retry)

        if not username or not password:
            return render_auth_page("register", error="Заполните все поля.", role=role)

        if len(username) < 3 or len(username) > 30:
            return render_auth_page("register", error="Логин должен быть от 3 до 30 символов.", role=role)

        if not 8 <= len(password) <= 256:
            return render_auth_page("register", error="Пароль должен быть от 8 до 256 символов.", role=role)

        if password != repeat_password:
            return render_auth_page("register", error="Пароли не совпадают.", role=role)

        password_hash = generate_password_hash(password)
        with closing(get_db()) as conn:
            try:
                begin_auth_write(conn)
                existing = conn.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()
                if existing is not None:
                    return render_auth_page("register", error="Такой логин уже занят. Выберите другой или войдите в существующий аккаунт.", role=role)
                new_user_id = insert_id(conn,
                    'INSERT INTO users (username, password, role) VALUES (?, ?, ?)',
                    (username, password_hash, role))
                token = new_session(get_db, SECRET_KEY, new_user_id, session.get('auth_token'), connection=conn)
                conn.commit()
            except INTEGRITY_ERRORS:
                conn.rollback()
                return render_auth_page('register', error='Такой логин уже занят.', role=role)
            except DATABASE_ERRORS:
                conn.rollback()
                raise DatabaseUnavailable('Регистрация временно недоступна. Повторите попытку позже.') from None

        establish_session(new_user_id, token)
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
            {render_header(user)}
            <main class="profile-card">
                <a class="back" href="/">{icon('arrow-left')} На главную</a>
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
                <p><a class="back" href="/settings">Настройки оформления и анимаций {icon('arrow')}</a></p>
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
                    <button type="submit" data-ui-icon="settings">Сменить пароль</button>
                </form>
            </main>
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
    revoke_session(get_db, SECRET_KEY, session.get('auth_token'))
    browser = session.get('auth_browser')
    session.clear()
    if isinstance(browser, str):
        session['auth_browser'] = browser

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
    try:
        result_params = result_parameters(request.args, projects)
        selected = result_parameters({'result_project': request.args.get('project', '')}, projects)['project']
        selected = selected or result_params['project'] or (projects[0]['id'] if projects else 0)
        result_params['home_project'] = selected
    except ValueError as error:
        return render_error_page('Некорректный фильтр', str(error), 400)
    except PermissionError as error:
        return render_error_page('Нет доступа', str(error), 403)
    with closing(get_db()) as conn:
        results = render_results(load_results(conn, user, result_params), result_params, projects)
        groups = own_groups(conn, user)
        project_states = load_project_states(conn, user) if projects else {}
        try:
            focus = load_focus(conn, user, next(p for p in projects if p['id'] == selected)) if selected else None
        except PermissionError as error:
            return render_error_page('Нет доступа', str(error), 403)
    messages = ''.join(f'<div class="{escape(category, quote=True)}" role="status">{escape(message)}</div>'
                       for category, message in get_flashed_messages(with_categories=True))
    return render_page(PAGE_STYLE + '<link rel="stylesheet" href="/static/student-results.css?v=20261015">'
                       + student_dashboard(user, projects, csrf_token(), render_header(user), messages, results, focus,
                                           any(key.startswith("result_") for key in request.args), groups=groups,
                                           project_states=project_states))


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

    # One calendar snapshot for filters, labels, metrics and project summary.
    deadline_today = business_today()
    # ---------- фильтры (поиск, приоритет, только мои, срок) ----------

    board_args = request.args
    if request.method == 'POST' and request.path.endswith('/add'):
        try:
            board_args = dict(parse_qsl(urlsplit(board_return(project_id, request.form.get('return_to'))).query, max_num_fields=20))
        except ValueError:
            board_args = {}
    search_query = board_args.get("q", "").strip()[:100]
    priority_filter = board_args.get("priority", "")
    if priority_filter not in ("low", "normal", "high", "urgent"):
        priority_filter = ""
    only_mine = current_user['role'] == 'student' and board_args.get("mine") == "1"
    deadline_filter = board_args.get('due', '')
    if deadline_filter not in DEADLINE_FILTERS:
        deadline_filter = ''
    sort_mode = board_args.get('sort', 'priority')
    if sort_mode not in TASK_SORTS:
        sort_mode = 'priority'

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

    if deadline_filter:
        tasks = [task for task in tasks if matches_deadline(
            deadline_info(task['deadline'], task['status'], deadline_today), deadline_filter)]
    tasks.sort(key=lambda task: task_sort_key(task, sort_mode))
    filters_active = bool(search_query or priority_filter or only_mine or deadline_filter)
    assignee_filter = board_args.get('assignee', '')
    if assignee_filter not in {'none', *[str(m['id']) for m in members if m['role'] == 'student']}:
        assignee_filter = ''
    stage_filter = board_args.get('stage', '')
    if stage_filter not in ('none','1','2','3','4','5'):
        stage_filter = ''
    column = board_args.get('column', 'todo')
    if column not in ('todo','progress','done'):
        column = 'todo'
    if assignee_filter:
        tasks = [t for t in tasks if (t['assignee_id'] is None if assignee_filter == 'none' else str(t['assignee_id']) == assignee_filter)]
    if stage_filter:
        tasks = [t for t in tasks if (t['stage_number'] is None if stage_filter == 'none' else str(t['stage_number']) == stage_filter)]
    filters_active = filters_active or bool(assignee_filter or stage_filter)
    refresh_board = filters_active or sort_mode != 'priority'

    # Every shortcut must point to a card that is visible under these filters.
    near_tasks = urgent_tasks(tasks, deadline_today)
    hot_items = ''.join(f'<li><a href="#task-{item["id"]}">{escape(item["title"])} — {escape(item["text"])}</a></li>'
                        for item in near_tasks)
    hot_html = (f'<div class="hot-strip" data-deadline-strip {"hidden" if not near_tasks else ""}>'
                f'<strong>Задачи с ближайшими сроками</strong><small>{TIMEZONE_LABEL} · не сроки проверки учителем</small>'
                f'<ul data-deadline-list>{hot_items}</ul></div>')

    filter_summary = (f'<p class="board-filter-summary">Показано {len(tasks)} из {len(all_tasks)} задач. '
                      'Показатели проекта учитывают все задачи команды.</p>')
    if filters_active and not tasks:
        filter_summary += '<div class="board-filter-empty" role="status">Нет задач по выбранным условиям. Измените фильтры или нажмите «Сбросить».</div>'
    board_state = dict(q=search_query, priority=priority_filter, mine='1' if only_mine else '',
                       due=deadline_filter, sort=sort_mode, assignee=assignee_filter, stage=stage_filter, column=column)
    filters_html = render_filters(project_id, current_user, members, board_state)
    canonical_filters = {k:v for k,v in board_state.items() if v and not (k == 'sort' and v == 'priority') and not (k == 'column' and v == 'todo')}
    board_location = f'/project/{project_id}' + ('?' + urlencode(canonical_filters) if canonical_filters else '')


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
        if deadline_info(task['deadline'], task['status'], deadline_today)['state'] == 'overdue'
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

        deadline = deadline_info(task['deadline'], task['status'], deadline_today)

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
        assignee_name = escape(task["assignee_name"] or "Не назначен")

        updates = task_update_history.get(task_id, [])
        latest_update_html = ""
        if updates:
            latest_update = updates[0]
            update_author = escape(latest_update["username"] or "Участник")
            update_timestamp = format_timestamp(latest_update["created_at"])
            update_body = escape(latest_update["body"])

            history_items = "".join(
                f'''<article class="task-report-entry">
                    <div class="task-report-head">
                        <strong>{escape(update["username"])}</strong>
                        <span>{format_timestamp(update["created_at"])}</span>
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
                        <summary data-ui-icon="history">История отчётов ({len(updates)})</summary>
                        <div>{history_items}</div>
                    </details>
                </div>
            """

        report_form = ""
        if current_user["role"] == "student" and task["assignee_id"] == current_user["id"]:
            report_form = f"""
                <details class="task-report-compose">
                    <summary data-ui-icon="plus">Добавить отчёт</summary>
                    <form method="post" action="/project/{project_id}/task/{task_id}/update">
                        {csrf_input()}
                        <input type="hidden" name="return_to" value="{escape(board_location + '#task-' + str(task_id),quote=True)}">
                        <textarea name="body" maxlength="500" rows="3" required
                            aria-label="Отчёт по задаче"
                            placeholder="Что уже сделано? Что мешает двигаться дальше?"></textarea>
                        <button type="submit" data-ui-icon="save">Сохранить отчёт</button>
                    </form>
                </details>
            """

        delete_control = f"""
            <form action="/project/{project_id}/delete/{task_id}" method="post">
                {csrf_input()}
                <input type="hidden" name="return_to" value="{escape(board_location + '#task-board',quote=True)}">
                <button type="submit" class="delete-task" title="Удалить задачу" data-ui-icon="trash">Удалить</button>
            </form>
        """ if owner else ""

        task_title = escape(
            task["title"]
        )

        safe_deadline = escape(deadline_text(deadline))
        deadline_class = deadline['css']

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
            <label class="task-priority-control">Приоритет<select class="task-priority-select" data-task-id="{task_id}" aria-label="Приоритет задачи: {task_title}" onchange="changePriority(this)">
                <option value="low" {low_selected}>Низкий</option>
                <option value="normal" {normal_selected}>Обычный</option>
                <option value="high" {high_selected}>Высокий</option>
                <option value="urgent" {urgent_selected}>Срочный</option>
            </select></label>
        """ if owner else ""
        edit_control = f'''<details class="task-edit"><summary data-ui-icon="edit">Редактировать задачу</summary>
            <form method="post" action="/project/{project_id}/edit/{task_id}" class="board-task-form">
            {csrf_input()}<input type="hidden" name="return_to" value="{escape(board_location + '#task-' + str(task_id),quote=True)}">
            {task_edit_fields(task, members)}<button data-ui-icon="save">Сохранить задачу</button></form></details>''' if owner else ''
        description_html = f'<p class="task-description" tabindex="0">{escape(task["description"])}</p>' if task['description'] else '<p class="t-muted">Описание пока не добавлено.</p>'
        stage_html = f'<a class="task-stage" href="/project/{project_id}/stages#stage-{task["stage_number"]}">Этап {task["stage_number"]} {icon('arrow')}</a>' if task['stage_number'] else '<span class="task-stage">Без этапа</span>'


        return f"""
        <div
            class="task-card {done_class}"
            id="task-{task_id}"
            draggable="{draggable}"
            data-task-id="{task_id}"
            data-deadline="{escape(task['deadline'] or '', quote=True)}"
            data-priority="{escape(priority, quote=True)}"
        >

            <details class="board-disclosure task-details" id="task-details-{task_id}">
                <summary class="task-title">{task_title}</summary>
                <div class="task-detail-content"><h2>{task_title}</h2>{description_html}
                    <div class="task-detail-meta"><span>Ответственный: {assignee_name}</span><span class="task-detail-deadline {deadline_class}">{safe_deadline}</span></div>
                    <p>{stage_html}</p><p class="t-muted">Выполнение задачи не означает принятие этапа учителем.</p>
                    {status_control}{priority_control}{edit_control}{latest_update_html}{report_form}{delete_control}
                </div>
            </details>
            <div class="task-priority priority-{escape(priority)}">
                {priority_name}
            </div>


            <div class="task-footer">

                <span class="task-deadline {deadline_class}">
                    {safe_deadline}
                </span>

                <span class="role">{assignee_name}</span>

            </div>
            <div class="task-meta"><span>{'Этап ' + str(task['stage_number']) if task['stage_number'] else 'Без этапа'}</span><span>Отчётов: {len(updates)}</span></div>

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

            cards = f"""
            <div class="kanban-empty">
                {"Нет задач по фильтрам" if filters_active else {"todo":"Новые задачи появятся здесь.","progress":"Перенесите сюда задачу, которую начали.","done":"Здесь будут выполненные задачи."}[status]}
            </div>
            """


        task_count = len(task_list)


        return f"""
        <div
            class="kanban-column"
            id="column-{status}"
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
    mobile_tabs = '<nav class="board-columns" aria-label="Колонки доски">' + ''.join(
        f'<a href="/project/{project_id}?{escape(urlencode(dict(board_state,column=key)),quote=True)}#task-board" data-column="{key}" aria-controls="column-{key}">{label} <span data-column-count="{key}">{len(rows)}</span></a>'
        for key,label,rows in (('todo','Новые',todo_tasks),('progress','В работе',progress_tasks),('done','Готово',done_tasks))) + '</nav>'
    if not all_tasks:
        kanban_html = f'''<section class="board-start"><span class="eyebrow">ОТ ИДЕИ К ПЕРВОМУ ДЕЙСТВИЮ</span><h3>Начните с первой задачи</h3>
            <p>{'Например: «Собрать материалы по теме». Определите результат и того, кто за него отвечает.' if owner else 'Владелец проекта сможет добавить задачи и распределить работу команды.'}</p>
            {f'<a class="btn" href="#task-create">{icon('plus')} Создать задачу</a>' if owner else ''}</section>'''
        mobile_tabs = ''
    elif not tasks:
        kanban_html = ''
        mobile_tabs = ''


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
            and member["role"] == 'student'
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
                 data-ui-icon="trash">
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

    create_values = request.form.to_dict() if request.method == 'POST' and request.path.endswith('/add') else None
    create_error = ' '.join(text for category,text in session.get('_flashes',[]) if category == 'error') if create_values is not None else ''
    add_task_section = create_form(project_id, members, csrf_input(), board_location + '#task-board', create_values, create_error) if owner else ''


    # =====================================================
    # OWNER SECTION
    # =====================================================

    owner_section = ""


    if owner:
        end_request_form = f'<form method="post" action="/supervision">{csrf_input()}<input type="hidden" name="project_id" value="{project_id}"><button class="secondary" name="action" value="end">Запросить завершение сопровождения</button></form>' if any(m['role'] == 'teacher' for m in members) else ''
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
                    placeholder="Логин ученика"
                    aria-label="Логин участника"
                    required
                >

                <button type="submit" data-ui-icon="plus">
                    Добавить
                </button>

            </form>

            <p>
                Только владелец проекта
                может добавлять учеников. Руководитель подключается через приглашение.
            </p>
            <a class="action-link" href="/supervision">Подключение и смена руководителя {icon('arrow')}</a>
            {end_request_form}

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
                                      completed_tasks, progress, overdue_count, today=deadline_today)
    return render_page(
        PAGE_STYLE
        + f"""
        <link rel="stylesheet" href="/static/project-workspace.css?v=20261014-feedback">
        <link rel="stylesheet" href="/static/task-board.css?v=20261019"><script src="/static/task-board.js?v=20261019" defer></script>
        <div class="container">
            {render_header(current_user)}
            <main class="project-workspace" data-project-views>
                <a href="/" class="back">{icon('arrow-left')} Все проекты</a>
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


                <div class="kanban-wrapper" id="task-board" data-project-url="/project/{project_id}" data-board-location="{escape(board_location,quote=True)}" data-board-refresh="{int(refresh_board)}" data-column="{column}" data-open-create="{int(create_values is not None)}">

                    <div class="section-title">

                        <div>

                            <h2>
                                Доска задач <span class="board-total">{total_tasks}</span>
                            </h2>

                            <p>
                                {"Здесь показаны задачи учеников. Учитель проверяет этапы, видеозащиту и оценки." if current_user['role'] == 'teacher' else "От конкретного действия — к готовому результату. Открой задачу или перенеси её в другую колонку."}
                            </p>

                        </div>
                        {f'<a class="btn board-add" href="#task-create">{icon('plus')} Задача</a>' if owner else ''}
                    </div>

                    {filters_html}
                    <details class="board-deadlines"><summary data-ui-icon="calendar">Ближайшие сроки</summary>{hot_html}</details>
                    {filter_summary}

                    <p id="board-status" role="status" aria-live="polite" hidden></p>

                    {add_task_section}{mobile_tabs}{kanban_html}

                </div>

                <div class="ui-project-teacher-details">{teacher_dashboard}</div>



                <div class="card ui-project-team-controls">

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


                <div class="ui-project-team-controls">{owner_section}</div>

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
    begin_project_write(conn, project_id)
    if conn.execute('SELECT 1 FROM projects WHERE id=? AND owner_id=?', (project_id, get_current_user()['id'])).fetchone() is None:
        conn.close()
        return render_error_page('Нет доступа', 'Владелец проекта изменился. Обновите страницу.', 403)

    user = conn.execute(
        """
        SELECT id, role
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


    if user['role'] != 'student':
        conn.close()
        return render_error_page('Нужно приглашение', 'Учителя нельзя добавить по логину. Примите его приглашение в разделе «Руководитель проекта».', 403)

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
    begin_project_write(conn, project_id)
    if conn.execute('SELECT 1 FROM projects WHERE id=? AND owner_id=?', (project_id, current_user['id'])).fetchone() is None:
        conn.close()
        return render_error_page('Нет доступа', 'Владелец проекта изменился. Обновите страницу.', 403)

    target = conn.execute('SELECT role FROM users WHERE id=?', (user_id,)).fetchone()
    if target and target['role'] == 'teacher':
        conn.close()
        return render_error_page('Руководитель защищён', 'Удаление учителя учеником запрещено. Запросите согласованную смену или завершение в разделе «Руководитель проекта».', 403)

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
    target = board_return(project_id, request.form.get('return_to'))
    try:
        description, stage_number = task_fields(request.form)
    except ValueError as exc:
        flash(str(exc), 'error')
        return project(project_id), 400
    if not title or len(title) > 160 or not is_valid_date(deadline):
        flash('Введите название до 160 символов и корректную дату дедлайна.', 'error')
        return project(project_id), 400
    try:
        add_task(project_id, title, deadline, priority, assignee_id, description, stage_number)
    except ValueError as exc:
        return render_error_page('Нет доступа', str(exc), 403)
    flash('Задача создана. Если она не видна, проверьте активные фильтры.', 'success')

    return redirect(target)


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
    today = business_today()
    counts['overdue'] = sum(deadline_info(task['deadline'], task['status'], today)['state'] == 'overdue'
                            for task in tasks)
    counts['percent'] = percent(counts['done'], counts['total'])
    students = [dict(id=student['id'], done=student['completed_count'], total=student['task_count'])
                for student in get_student_progress(project_id)]
    updated_task = next((task for task in tasks if task['id'] == task_id), None)
    info = deadline_info(updated_task['deadline'], updated_task['status'], today) if updated_task else deadline_info(None)
    return jsonify(success=True, metrics=counts, students=students, deadline_text=task_deadline(tasks, today),
                   task_deadline=dict(text=deadline_text(info), css=info['css']), deadline_tasks=urgent_tasks(tasks, today))


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
        return redirect(board_return(project_id, request.form.get('return_to')))

    if len(body) > 500:
        flash("Отчёт должен быть не длиннее 500 символов.", "error")
        return redirect(board_return(project_id, request.form.get('return_to')))

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

    if not add_task_update(project_id, task_id, user["id"], body):
        return render_error_page('Нет доступа', 'Задача удалена или назначена другому участнику.', 403)
    flash("Отчёт сохранён в истории задачи.", "success")
    return redirect(board_return(project_id, request.form.get('return_to')))


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


    deleted = delete_task(
        project_id,
        task_id
    )
    if not deleted:
        flash('Задача не удалена: задачи с отчётами сохраняют историю работы. Исправьте название или статус; если задачи уже нет, обновите страницу.', 'error')


    return redirect(board_return(project_id, request.form.get('return_to')))


# =========================================================
# TEACHER CABINET
# =========================================================

TEACHER_STYLE = ""


def percent(done, total):
    return round(done / total * 100) if total else 0


def render_header(user, notification_data=None):
    if notification_data is None:
        with closing(get_db()) as conn:
            count, rows = notification_feed(conn, user['id'])
    else:
        count, rows = notification_data
    bell = render_bell(count, rows, csrf_input())
    return shell_header(user, request.path, bell, logout_form(), query=request.args)


@app.route('/notifications')
@login_required
def notifications_page():
    user = get_current_user()
    before = request.args.get('before')
    if before is not None:
        try:
            before = int(before)
        except (ValueError, TypeError):
            before = 0
        if not valid_database_id(before):
            return render_error_page('Некорректный адрес', 'Проверьте номер страницы уведомлений.', 400)
    with closing(get_db()) as conn:
        count, rows = notification_feed(conn, user['id'], before, limit=31)
    older = (f'<a class="action-link" href="/notifications?before={rows[29]["id"]}">Более ранние</a>'
             if len(rows) > 30 else '')
    # The first page already contains the latest header items. Reuse only within
    # this response; older pages must still fetch the latest items for the bell.
    header = render_header(user, (count, rows[:12]) if before is None else None)
    return render_page(PAGE_STYLE + f'''<div class="container">{header}
        <main class="notifications-page"><h1>Уведомления</h1><p>Непрочитанных: {count}</p>
        <form method="post" action="/notifications/read-all">{csrf_input()}
        <button class="secondary" type="submit" data-ui-icon="check">Прочитать все</button></form>
        {render_items(rows[:30], csrf_input())}{older}</main></div>''')


@app.route('/notifications/<int:notification_id>/read', methods=['POST'])
@app.route('/notifications/read-all', methods=['POST'])
@login_required
def read_notifications(notification_id=None):
    with closing(get_db()) as conn:
        if not mark_read(conn, get_current_user()['id'], notification_id):
            return render_error_page('Уведомление не найдено', 'Уведомление недоступно вашему аккаунту.', 404)
    return redirect('/notifications')


@app.route('/settings')
def site_settings():
    user = get_current_user()
    header = render_header(user) if user else f'<header class="main-header"><a class="brand" href="/login" aria-label="M-Flow — вход">M-Flow<span class="ui-brand-dot">.</span></a><a class="back" href="/login">{icon('arrow-left')} К входу</a></header>'
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
                <button type="button" class="secondary" id="reset-preferences" data-ui-icon="return">Сбросить настройки</button>
                <p id="preferences-status" role="status" aria-live="polite">Изменения применяются сразу.</p>
                <noscript><p class="error">Для изменения и сохранения оформления включите JavaScript в браузере.</p></noscript>
            </form>
        </section></main></div>''')


def collaboration_token():
    return csrf_token()


def group_attempt(user):
    source = client_source(request.remote_addr, request.headers.get('X-Forwarded-For', ''), TRUSTED_AUTH_PROXIES)
    retry = reserve_supervision_attempt(get_db, SECRET_KEY, user['id'], source, purpose='learning-groups')
    if retry:
        return render_collaboration_page(user, 'Учебные группы', '<a href="/groups">К моим группам</a>',
            error=f'Слишком много попыток. Повторите через {retry} сек.', status=429)[0], 429, {'Retry-After': str(retry)}


@app.route('/groups', methods=['GET', 'POST'])
@login_required
def learning_groups_page():
    user = get_current_user()
    if request.method == 'POST':
        limited = group_attempt(user)
        if limited:
            return limited
    error, status, preview = '', 200, None
    with closing(get_db()) as conn:
        try:
            if request.method == 'POST':
                action = request.form.get('action')
                if action == 'create':
                    gid = create_group(conn, user, request.form)
                    flash('Учебная группа создана. Теперь можно пригласить класс по коду.')
                    return redirect(f'/groups/{gid}')
                if action == 'preview':
                    raw = request.form.get('code', '').strip()
                    preview = (preview_group_code(conn, user, raw), raw)
                elif action == 'join':
                    gid = join_group(conn, user, request.form.get('code', '').strip())
                    flash('Вы в учебной группе. Выберите свой проект; руководство согласуется отдельно.')
                    return redirect(f'/groups/{gid}')
                else:
                    raise ValueError('Выберите действие в актуальной форме.')
        except (ValueError, PermissionError) as exc:
            conn.rollback()
            error, status = str(exc), 403 if isinstance(exc, PermissionError) else 400
        body = render_groups(own_groups(conn, user), user, csrf_token(), preview)
    return render_collaboration_page(user, 'Учебные группы', body, error=error, status=status)


@app.route('/groups/<int:group_id>', methods=['GET', 'POST'])
@login_required
def learning_group_page(group_id):
    user = get_current_user()
    if not valid_database_id(group_id):
        return render_error_page('Некорректный адрес', 'Откройте учебные группы заново.', 400)
    if request.method == 'POST' and request.form.get('action') == 'invite':
        limited = group_attempt(user)
        if limited:
            return limited
    error, status, code = '', 200, None
    with closing(get_db()) as conn:
        try:
            group = group_for(conn, user, group_id)
        except PermissionError as exc:
            return render_error_page('Нет доступа', str(exc), 403)
        try:
            if request.method == 'POST':
                action = request.form.get('action')
                code = change_group(conn, user, group_id, request.form)
                if action == 'leave':
                    flash('Вы вышли из группы. Проект и действующее руководство сохранены.')
                    return redirect('/groups')
                if action != 'invite':
                    flash('Запрос сопровождения создан. Откройте «Руководитель проекта» для согласования.' if action == 'propose'
                          else 'Выбор проекта для группы обновлён. Доступ к работам не изменён.' if action == 'select'
                          else 'Группа обновлена. Проекты и действующее руководство сохранены.')
                    return redirect(f'/groups/{group_id}')
                group = group_for(conn, user, group_id)
            data = roster(conn, user, group, request.args)
        except (ValueError, PermissionError) as exc:
            conn.rollback()
            error, status = str(exc), 403 if isinstance(exc, PermissionError) else 400
            try:
                group = group_for(conn, user, group_id)
            except PermissionError:
                return render_error_page('Нет доступа', 'Вы больше не состоите в учебной группе.', 403)
            data = roster(conn, user, group, {})
        body = render_group(conn, user, group, data, csrf_token(), code)
    return render_collaboration_page(user, group['name'], body, error=error, status=status,
                                     back_url='/groups', back_label='Учебные группы')


@app.route('/supervision', methods=['GET', 'POST'])
@login_required
def supervision_page():
    user = get_current_user()
    before = request.args.get('before', type=int)
    if 'before' in request.args and not valid_database_id(before):
        return render_error_page('Некорректная страница', 'Откройте раздел руководства заново.', 400)
    action = request.form.get('action', '')
    if request.method == 'POST' and action in ('create', 'preview', 'request', 'decline'):
        source = client_source(request.remote_addr, request.headers.get('X-Forwarded-For', ''), TRUSTED_AUTH_PROXIES)
        retry = reserve_supervision_attempt(get_db, SECRET_KEY, user['id'], source)
        if retry:
            return render_collaboration_page(user, 'Руководитель проекта', '<p><a href="/supervision">Вернуться к приглашениям</a></p>',
                error=f'Слишком много попыток. Повторите через {retry} сек.', status=429)[0], 429, {'Retry-After': str(retry)}
    error, status, code, preview = '', 200, None, None
    with closing(get_db()) as conn:
        try:
            if request.method == 'POST':
                if action == 'create':
                    code = create_invite(conn, user)
                elif action == 'preview':
                    if user['role'] != 'student':
                        raise PermissionError('Приглашение принимает ученик-владелец.')
                    raw_code = request.form.get('code', '').strip()
                    preview = (preview_invite(conn, raw_code), raw_code)
                elif action == 'request':
                    request_connection(conn, user, request.form.get('code', '').strip(), form_database_id(request.form.get('project_id')))
                    flash('Проект подтверждён. Учитель должен принять сопровождение; при смене также нужны согласия текущих учителей.')
                    return redirect('/supervision')
                elif action in ('revoke', 'decline'):
                    invitation_action(conn, user, request.form)
                    flash('Приглашение отозвано или отклонено. Доступ к проектам не изменён.')
                    return redirect('/supervision')
                elif action == 'end':
                    request_end(conn, user, form_database_id(request.form.get('project_id')))
                    flash('Запрос завершения создан. До всех согласований руководитель и доступ сохраняются.')
                    return redirect('/supervision')
                elif action in ('approve', 'reject', 'cancel'):
                    flash(respond_request(conn, user, request.form))
                    return redirect('/supervision')
                else:
                    raise ValueError('Выберите действие в актуальной форме.')
        except (PermissionError, ValueError) as exc:
            conn.rollback()
            error, status = str(exc), 403 if isinstance(exc, PermissionError) else 400
        body = render_hub(conn, user, csrf_token(), before=before, preview=preview, code=code)
    return render_collaboration_page(user, 'Руководитель проекта', body, error=error, status=status)


def render_unsaved_result(form, user, target, defense=False):
    """Only escaped, bounded work fields; never tokens or a stale editable form."""
    fields = [('teacher_comment', 'Комментарий учителя', 2000)] if user['role'] == 'teacher' else (
        [('video_url', 'Ссылка на запись', 2000), ('description', 'Описание выступления', 2000)] if defense else
        [('result', 'Результат этапа', 5000), ('presentation_url', 'Ссылка на презентацию', 2000)])
    content = ''
    for key, label, limit in fields:
        value = form.get(key, '')
        if value:
            content += f'<strong>{label}</strong><pre tabindex="0">{escape(value[:limit])}</pre>'
            if len(value) > limit:
                content += f'<p>Показаны первые {limit} символов: превышен допустимый размер поля.</p>'
    if not content:
        return ''
    return f'''<aside class="journey-feedback unsaved-result" aria-label="Несохранённый ввод"><strong>Ваш ввод не сохранён</strong>
        <p>Результат уже изменён или недоступен для редактирования. Скопируйте свой текст перед обновлением страницы,
        сравните с актуальной версией и повторите действие только если оно доступно. Этот текст не подставлен в новую форму.</p>
        <details><summary data-ui-icon="link">Показать текст для копирования</summary>{content}</details>
        <a href="{target}">Открыть актуальную версию {icon('arrow')}</a></aside>'''


def render_collaboration_page(user, title, body, project_id=None, error="", status=200, back_url=None, back_label=None):
    messages = ''.join(f'<div class="success" role="status">{escape(message)}</div>' for message in get_flashed_messages())
    back = f'<a class="back" href="/project/{project_id}">{icon('arrow-left')} К обзору проекта</a>' if project_id else f'<a class="back" href="/">{icon('arrow-left')} К моим проектам</a>'
    if back_url:
        back = f'<a class="back" href="{escape(back_url, quote=True)}">{icon('arrow-left')} {escape(back_label or "Назад")}</a>'
    error_html = f'<div class="error" role="alert">{escape(error)}</div>' if error else ''
    return render_page(PAGE_STYLE + f'''<link rel="stylesheet" href="/static/teamwork.css"><div class="container">{render_header(user)}
        <main class="team-page">{back}<h1>{escape(title)}</h1>{project_navigation(project_id, request.path.rsplit('/', 1)[-1]) if project_id else ""}{messages}{error_html}{body}</main></div>'''), status


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
            return render_collaboration_page(user,'Приглашение не отправлено',f'<a class="back" href="/teams">{icon('arrow-left')} В каталог участников</a>',error=str(exc),status=400)
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
            return render_collaboration_page(user,'Приглашение не обработано',f'<a class="back" href="/teams">{icon('arrow-left')} К приглашениям</a>',error=str(exc),status=400)
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
        if owner:
            body += f'<section class="card"><h2>Управление составом</h2><p>Добавление по логину, удаление участников и управление руководителем.</p><a class="action-link" href="/project/{project_id}#project-members">Открыть управление командой {icon('arrow')}</a></section>'
    finally:
        conn.close()
    return render_collaboration_page(user,f'Команда · {name}',body,project_id,error,status)


@app.route('/project/<int:project_id>/history')
@login_required
def project_work_history(project_id):
    user = get_current_user()
    name = get_project_name(project_id)
    if name is None:
        return render_error_page('Нет доступа', 'Вы не участвуете в этом проекте.', 403)
    try:
        work, page = history_parameters(request.args)
    except ValueError as exc:
        return render_error_page('История работ', str(exc), 400)
    conn = get_db()
    try:
        body = render_history(project_id, load_history(conn, project_id, work, page), work)
    finally:
        conn.close()
    return render_collaboration_page(user, f'История работ · {name}', body, project_id)


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
        editable = record['state'] in ('draft', 'returned') if user['role'] == 'student' else record['state'] == 'submitted'
        if error and editable and request.form.get('revision') == str(record['revision']):
            submitted = request.form
        body = render_defense(record,user,token,submitted,has_teacher=has_project_teacher(conn, project_id))
        if error and submitted is None:
            body = render_unsaved_result(request.form, user, f'/project/{project_id}/defense', defense=True) + body
        body = '<link rel="stylesheet" href="/static/project-workspace.css?v=20261014-feedback">' + body
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
        editable = current is not None and (current['state'] in ('todo', 'progress') if user['role'] == 'student' else current['state'] == 'review')
        if submitted is not None and (current is None or submitted.get("number") != str(current["number"])
                                      or submitted.get("revision") != str(current["revision"]) or not editable):
            submitted = None
    finally:
        conn.close()
    messages = "".join(f'<p role="status">{escape(message)}</p>' for message in get_flashed_messages())
    unsaved = render_unsaved_result(request.form, user, f'/project/{project_id}/stages') if error and submitted is None else ''
    return render_page(PAGE_STYLE + f'''
        <link rel="stylesheet" href="/static/project-stages.css"><link rel="stylesheet" href="/static/project-workspace.css?v=20261014-feedback"><div class="container">{render_header(user)}
        <main class="stages-page"><a class="back" href="/project/{project_id}">{icon('arrow-left')} К обзору проекта</a>
        <h1>Пять этапов · {escape(project_name)}</h1>{project_navigation(project_id, "stages")}
        <p>Ученики сохраняют результат и отправляют его учителю. После принятия открывается следующий этап.
        Этапы общие для участников проекта; их прохождение не меняет статусы задач и итоговые баллы.</p>
        {messages}<p role="alert">{escape(error)}</p>{unsaved}{render_stages(stages, user, token, submitted, has_teacher=has_teacher, project_id=project_id)}</main></div>'''), status


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
                begin_project_write(conn, project_id)
                if not participant(conn, project_id, user['id']):
                    conn.rollback()
                    conn.close()
                    return render_error_page('Нет доступа', 'Сопровождение завершено. Новые оценки в этом проекте недоступны.', 403)
                if not participant(conn, project_id, selected['id']):
                    raise ValueError('Состав проекта изменился. Обновите страницу и выберите действующего участника.')
                # Re-read under the same project lock as other review mutations.
                previous = conn.execute('SELECT * FROM project_assessments WHERE project_id=? AND student_id=? AND teacher_id=?',
                                        (project_id, selected['id'], user['id'])).fetchone()
                changed = (previous is None or previous['state'] != action or previous['note'] != note
                           or previous['student_name'] != student_name or load_scores(previous) != scores
                           or previous['rubric_version'] != RUBRIC_VERSION)
                conn.execute("""INSERT INTO project_assessments
                    (project_id, student_id, teacher_id, student_name, rubric_version, scores_json, note, state)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project_id, student_id, teacher_id) DO UPDATE SET
                    student_name=excluded.student_name, rubric_version=excluded.rubric_version,
                    scores_json=excluded.scores_json, note=excluded.note, state=excluded.state,
                    updated_at=CAST(CURRENT_TIMESTAMP AS TEXT)""",
                    (project_id, selected["id"], user["id"], student_name, RUBRIC_VERSION,
                     json.dumps(scores), note, action))
                if action == 'published' and changed:
                    total, grade = summarize(scores)
                    notify(conn, project_id, 'assessment:' + secrets.token_hex(16),
                           'Оценка проекта опубликована',
                           f'Баллы: {total} / {MAX_SCORE}. Оценка: {grade}.' + (f' {note}' if note else ''),
                           f'/project/{project_id}/assessment', [selected['id']])
                conn.commit()
                conn.close()
                flash("Оценка опубликована." if action == "published" else "Черновик сохранён. Ученику он не виден.")
                return redirect(f"/project/{project_id}/assessment?student_id={selected['id']}")
            except ValueError as exc:
                conn.rollback()
                error = str(exc)
                status = 400
                # Keep valid entered values without treating missing criteria as zero.
                scores = {key: int(request.form[f"criterion_{key}"]) if request.form.get(f"criterion_{key}") in
                          {str(v) for v in range(maximum + 1)} else None for key, _, maximum in CRITERIA}
            except Exception:
                conn.rollback()
                conn.close()
                raise
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
                <div class="assessment-actions"><button name="action" value="draft" type="submit" data-ui-icon="save">Сохранить черновик</button><button name="action" value="published" type="submit" data-ui-icon="check">Опубликовать оценку</button></div>
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
                <h3>Итого: {total} / {MAX_SCORE} · Оценка: {grade}</h3><p class="assessment-note">{escape(row['note'] or 'Учитель опубликовал оценку без примечания.')}</p></section>'''
        if not rows:
            content = f'<section class="card"><h2>Оценка ещё не опубликована</h2><p>Здесь появятся ваши баллы и примечание учителя. Приём этапов и видеозащиты не выставляет баллы автоматически. Дождитесь публикации учителем; оценки других участников здесь не показываются.</p><a href="/project/{project_id}">Посмотреть следующий шаг {icon('arrow')}</a></section>'
    conn.close()
    messages = "".join(f'<p role="status">{escape(message)}</p>' for message in get_flashed_messages())
    return render_page(PAGE_STYLE + f'''<link rel="stylesheet" href="/static/assessment.css">
        <div class="app">{render_header(user)}<main class="main-content assessment-page">
        <a class="back" href="/project/{project_id}">{icon('arrow-left')} К обзору проекта</a>
        <h1>Оценивание · {escape(project_name)}</h1>{project_navigation(project_id, "assessment")}
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
            SELECT d.project_id, d.state, d.revision, d.updated_at, a.username AS author_name
            FROM project_defenses d JOIN visible_projects v ON v.id=d.project_id
            LEFT JOIN users a ON a.id=d.author_id""")
        reports_by_project = grouped("""
            SELECT t.project_id, r.id, r.task_id, r.body, r.created_at, u.username
            FROM tasks t JOIN visible_projects v ON v.id=t.project_id
            JOIN task_updates r ON r.id=(
                SELECT MAX(latest.id) FROM task_updates latest WHERE latest.task_id=t.id
            )
            JOIN users u ON u.id=r.user_id ORDER BY r.id DESC""")
    finally:
        conn.close()

    today = business_today()
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

            due = deadline_info(task['deadline'], status, today)
            days_left = due['days']
            is_overdue = due['state'] == 'overdue'

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
                reasons.append(deadline_text(due))
            elif days_left is not None and days_left <= 3:
                reasons.append(deadline_text(due))

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
            "defense": dict(video_defense),
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
            <a class="back" href="/project/{project_id}#task-{task['id']}">Открыть задачу {icon('arrow')}</a>
        </div>'''


def render_teacher_project(project):

    counts = project["counts"]
    health_labels = {"overdue": "Просрочены задачи", "attention": "Требует внимания",
                     "done": "Задачи завершены", "normal": "Всё по плану", "empty": "Нет задач"}
    health = project["health"]
    stage = project["current_stage"]
    stage_note = (f'Текущий этап {stage["number"]}: {escape(stage["title"])} · {STATE_LABELS[stage["state"]]}'
                  if stage else 'Все пять этапов приняты учителем')
    access_note = '<span class="t-muted">Проверка результатов · задачами и командой управляет ученик-владелец</span>'
    if project['legacy_owner']:
        options = ''.join(f'<option value="{student["id"]}">{escape(student["name"])}</option>' for student in project['students'])
        access_note += ('<p>Этот старый проект создан учителем. Передайте управление ученику: задания и результаты сохранятся, вы останетесь учителем проекта.</p>'
                       + f'<form method="post" action="/project/{project["id"]}/transfer">{csrf_input()}<label>Ученик-владелец<select name="student_id"><option value="">Выберите ученика</option>{options}</select></label><label>Или логин ученика<input name="student_username" maxlength="30" placeholder="Если ученика пока нет в проекте"></label><button type="submit" data-ui-icon="arrow">Передать проект ученику</button></form>')
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
            <a href="/project/{project["id"]}" class="teacher-board-link">Открыть проект {icon('arrow')}</a>
            <a href="/project/{project["id"]}/assessment" class="teacher-board-link">Оценивание {icon('arrow')}</a>
            <a href="/project/{project["id"]}/stages" class="teacher-board-link">Этапы: {project["completed_stages"]}/5 приняты {icon('arrow')}</a>
            <a href="/project/{project["id"]}/team" class="teacher-board-link">Команда {icon('arrow')}</a>
            <a href="/project/{project["id"]}/defense" class="teacher-board-link">Видеозащита {icon('arrow')}</a>
        </div>
        {access_note}
        <p class="t-muted">{stage_note}</p>
        <p class="t-muted">Видеозащита: {project['defense_state']}</p>
        <details class="ui-project-more"><summary>Задачи и прогресс команды</summary><div class="t-chips">
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
        {attention_html}</details>
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
    if focus not in ("all", "overdue", "attention", "done", "review"):
        focus = "all"
    kind = request.args.get('kind', 'all')
    kind = kind if kind in WORK_KINDS else 'all'
    sort = request.args.get('sort', 'oldest')
    sort = sort if sort in WORK_SORTS else 'oldest'
    project_filter = request.args.get('project', '')
    project_id = None
    if project_filter:
        try:
            project_id = int(project_filter)
        except ValueError:
            return render_error_page('Некорректный фильтр', 'Выберите проект из списка.', 400)
        if not valid_database_id(project_id):
            return render_error_page('Некорректный фильтр', 'Выберите проект из списка.', 400)
        if project_id not in {p['id'] for p in overview}:
            return render_error_page('Нет доступа', 'Проект недоступен вашему аккаунту.', 403)
    try:
        page = max(1, int(request.args.get('page', '1')))
    except ValueError:
        page = 1
    query_folded = query.casefold()
    visible_projects = [
        project for project in overview
        if project_matches(project, query_folded)
        and (project_id is None or project['id'] == project_id)
        and (focus == "all" or (focus not in ('done', 'review') and project["health"] == focus)
             or focus == 'done' and project['total'] > 0 and project['counts']['done'] == project['total']
             or focus == 'review' and project['needs_review']
             or focus == "attention" and bool(project["attention"]))
    ]
    queue = queue_page(visible_projects, kind, sort, page, query_folded)
    queue_html = render_queue(queue, dict(q=query, focus=focus, kind=kind,
                                         project=project_id, sort=sort))
    kind_options = ''.join(f'<option value="{v}" {"selected" if kind == v else ""}>{label}</option>'
                           for v, label in WORK_KINDS.items())
    sort_options = ''.join(f'<option value="{v}" {"selected" if sort == v else ""}>{label}</option>'
                           for v, label in WORK_SORTS.items())
    project_options = '<option value="">Все проекты</option>' + ''.join(
        f'<option value="{p["id"]}" {"selected" if project_id == p["id"] else ""}>{escape(p["name"])}</option>'
        for p in overview)

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
            <p>Создайте приглашение в разделе «Руководитель проекта». Владелец подтвердит конкретный проект, затем вы сможете принять его на сопровождение. Здесь появятся доступные вам результаты.</p>
        </div>
    """)

    focus_options = "".join(
        f'<option value="{value}" {"selected" if focus == value else ""}>{label}</option>'
        for value, label in (("all", "Все проекты"), ("overdue", "Просрочены задачи учеников"), ("review", "Ожидают моей проверки"),
                             ("attention", "Требуют внимания"), ("done", "Все задачи выполнены"))
    )
    attention_items = sorted(
        [(project, item) for project in visible_projects for item in project["attention"]
         if focus != 'review' and (focus != 'overdue' or item['task']['status'] != 'done' and item['sort'] < 0)],
        key=lambda pair: pair[1]["sort"]
    )[:6]
    attention_html = "".join(
        f'''<a class="teacher-attention-item" data-urgency="{'overdue' if item['sort'] < 0 else 'upcoming'}" href="/project/{project["id"]}#task-{item["task"]["id"]}">
            <strong>{escape(item["task"]["title"])}</strong>
            <span>{escape(project["name"])} · {escape(" · ".join(item["reasons"]))}</span>
        </a>'''
        for project, item in attention_items
    ) or '<p class="t-muted">В выбранных проектах нет задач, требующих внимания.</p>'
    attention_title = 'Просроченные задачи учеников' if focus == 'overdue' else 'Сроки и назначение задач'

    overdue_class = "warn" if overdue_tasks else ""
    review_count = sum(bool(p['current_stage'] and p['current_stage']['state'] == 'review')
                       + bool(p['defense_state'] == DEFENSE_STATES['submitted']) for p in overview)

    return render_page(
        PAGE_STYLE
        + TEACHER_STYLE
        + '<link rel="stylesheet" href="/static/teacher-queue.css?v=20261014">'
        + f"""
        <div class="container workspace teacher-workspace">
            {render_header(user)}
            <main>
                <div class="workspace-intro"><span>РАБОЧЕЕ ПРОСТРАНСТВО УЧИТЕЛЯ</span><span>M-Flow / проверка</span></div>
                <section class="teacher-hero workspace-hero">
                    <div class="hero-copy"><span class="hero-kicker">РАБОЧЕЕ ПРОСТРАНСТВО УЧИТЕЛЯ</span>
                        <h1>Кабинет учителя</h1>
                        <p>{escape(user["username"])}, всё важное — перед вами. На проверке: {review_count}.</p>
                    </div>
                    <a class="btn" href="/supervision">{icon('link')} Подключить проект</a>
                </section>
                {messages}
                <div class="t-stats">
                    <div class="t-stat"><strong data-review-count="{review_count}">{review_count:02d}</strong><span>На проверке</span></div>
                    <div class="t-stat"><strong>{len(overview)}</strong><span>Проектов</span></div>
                    <div class="t-stat"><strong>{len(student_ids)}</strong><span>Учеников</span></div>
                    <div class="t-stat {overdue_class}"><strong>{overdue_tasks}</strong><span>Просрочено</span></div>
                </div>
                <div class="teacher-signal-strip">
                    <span>Выполнено задач: <b>{done_tasks} из {total_tasks}</b> · {percent(done_tasks, total_tasks)}%</span>
                    <span>В ближайшие 3 дня: <b>{soon_tasks}</b></span>
                    <span>Без ответственного: <b>{unassigned_tasks}</b></span>
                    <span>Сроки задач: {TIMEZONE_LABEL}. Ожидание проверки — не просрочка сдачи.</span>
                </div>
                <details class="ui-teacher-filters" {'open' if query or focus != 'all' or kind != 'all' or sort != 'oldest' or project_id is not None or queue['page'] > 1 else ''}><summary data-ui-icon="filter">Поиск и фильтры работ</summary>
                <form class="teacher-filters" method="get">
                    <input type="search" name="q" value="{escape(query)}"
                        aria-label="Поиск проекта, автора или работы" placeholder="Найти проект, автора или работу">
                    <select name="focus" aria-label="Состояние проектов">{focus_options}</select>
                    <select name="project" aria-label="Проект очереди">{project_options}</select>
                    <select name="kind" aria-label="Тип работы">{kind_options}</select>
                    <select name="sort" aria-label="Порядок отправки работ">{sort_options}</select>
                    <button type="submit" data-ui-icon="filter">Показать</button>
                    <a href="/teacher" class="back">Сбросить</a>
                </form></details>
                <div class="teacher-dashboard-grid">
                    <section class="teacher-panel review-panel" id="review-queue"><span class="eyebrow">СЛЕДУЮЩИЙ ШАГ</span><h2>Очередь работ</h2>{queue_html}</section>
                    <section class="teacher-panel" id="task-attention"><h2>{attention_title}</h2><p class="t-muted">Дедлайны учеников отдельно от ожидания проверки учителем.</p>{attention_html}</section>
                </div>
                <div class="section-title" id="teacher-projects"><h2>Проекты</h2>
                    <span class="t-muted">Показано {len(visible_projects)} из {len(overview)}</span></div>
                {projects_html}
                <section class="teacher-panel"><h2>Как подключиться к проекту</h2><p>Создайте одноразовый код, передайте его ученику. Владелец подтверждает конкретный проект, затем вы берёте его на сопровождение. Другие проекты не открываются автоматически.</p><a class="teacher-primary-link" href="/supervision">Руководитель проекта {icon('arrow')}</a></section>
                <footer class="workspace-footer"><span>M-Flow <small>by Minich</small></span><a href="/settings">{icon('settings')} Настройки оформления</a></footer>
            </main>
        </div>
        """
    )


@app.route("/teacher/projects", methods=["POST"])
@login_required
def create_teacher_project():
    return render_error_page('Нет доступа', 'Проекты создают ученики. Пригласите владельца кодом в разделе «Руководитель проекта».', 403)


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

    target = '/teacher' if request.form.get('next') == '/teacher' else board_return(project_id, request.form.get('return_to'))
    title = request.form.get('title', '').strip()
    deadline = request.form.get('deadline', '').strip()
    assignee_id = request.form.get('assignee_id', type=int)
    if assignee_id is not None and not valid_database_id(assignee_id):
        flash('Укажите корректного ответственного.', 'error')
        return redirect(target)
    if len(title) > 160 or not is_valid_date(deadline):
        flash('Название — до 160 символов; укажите корректную дату дедлайна.', 'error')
        return redirect(target)
    user = get_current_user()
    conn = get_db()
    try:
        begin_project_write(conn, project_id)
        owned = conn.execute('SELECT owner_id FROM projects WHERE id=?', (project_id,)).fetchone()
        if owned is None or owned['owner_id'] != user['id'] or user['role'] != 'student':
            return render_error_page('Нет доступа', 'Владелец проекта изменился.', 403)
        task = conn.execute('SELECT title,description,stage_number FROM tasks WHERE id=? AND project_id=?', (task_id,project_id)).fetchone()
        if task is None:
            flash('Задача не найдена.', 'error')
            return redirect(target)
        try:
            description, stage_number = task_fields(request.form, dict(task))
        except ValueError as exc:
            flash(str(exc), 'error')
            return redirect(target)
        if assignee_id is not None and not conn.execute(
                "SELECT 1 FROM project_members m JOIN users u ON u.id=m.user_id WHERE m.project_id=? AND u.id=? AND u.role='student'",
                (project_id,assignee_id)).fetchone():
            assignee_id = None
        conn.execute('UPDATE tasks SET title=?,deadline=?,assignee_id=?,description=?,stage_number=? WHERE id=? AND project_id=?',
                     (title or task['title'],deadline or None,assignee_id,description,stage_number,task_id,project_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    flash('Задача обновлена.', 'success')
    return redirect(target)


# =========================================================
# ERROR PAGES
# =========================================================

def render_error_page(title, message, status_code):

    return render_page(
        PAGE_STYLE
        + f"""
        <div class="login-page ui-error-page">
            <main class="login-box">
                <a class="brand" href="/" aria-label="M-Flow — на главную">M-Flow<span class="ui-brand-dot">.</span></a>
                <span class="eyebrow">Ошибка {status_code}</span>
                <h1>{escape(title)}</h1>
                <p>{escape(message)}</p>
                <a href="/" class="back">{icon('arrow-left')} Вернуться на главную</a>
            </main>
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
