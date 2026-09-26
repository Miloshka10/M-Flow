from flask import (
    Flask,
    request,
    redirect,
    session,
    render_template_string,
    flash,
    jsonify
)
import sqlite3
import os
from datetime import date
from functools import wraps
from werkzeug.security import (
    check_password_hash,
    generate_password_hash
)
from html import escape


app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("M_FLOW_SECRET_KEY", "m-flow-local-secret"),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

DATABASE_PATH = os.environ.get(
    "M_FLOW_DATABASE",
    os.path.join(app.root_path, "database.db")
)


# =========================================================
# DATABASE
# =========================================================

def get_db():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def prepare_database():

    conn = get_db()

    conn.executescript(
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
        """
    )

    project_columns = {
        column["name"]
        for column in conn.execute("PRAGMA table_info(projects)").fetchall()
    }

    if "owner_id" not in project_columns:
        conn.execute("ALTER TABLE projects ADD COLUMN owner_id INTEGER")

    columns = conn.execute(
        "PRAGMA table_info(tasks)"
    ).fetchall()

    column_names = [
        column["name"]
        for column in columns
    ]

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

    for username, role in (("milosh", "student"), ("uchitel", "teacher")):
        conn.execute(
            """
            INSERT OR IGNORE INTO users (username, password, role)
            VALUES (?, ?, ?)
            """,
            (username, generate_password_hash("1234"), role)
        )

    conn.commit()
    conn.close()


prepare_database()


# =========================================================
# AUTH
# =========================================================

def login_required(func):

    @wraps(func)
    def wrapper(*args, **kwargs):

        if "user_id" not in session or get_current_user() is None:
            session.clear()
            return redirect("/login")

        return func(*args, **kwargs)

    return wrapper


def get_current_user():

    if "user_id" not in session:
        return None

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

    user = get_current_user()

    if user is None:
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

def get_projects():

    user = get_current_user()

    if user is None:
        return []

    conn = get_db()

    projects = conn.execute(
        """
        SELECT DISTINCT
            projects.id,
            projects.name
        FROM projects
        LEFT JOIN project_members
            ON project_members.project_id = projects.id
        WHERE projects.owner_id = ?
        OR project_members.user_id = ?
        ORDER BY projects.id DESC
        """,
        (
            user["id"],
            user["id"]
        )
    ).fetchall()

    conn.close()

    return projects


def is_valid_date(value):

    if not value:
        return True

    try:
        date.fromisoformat(value)
    except ValueError:
        return False

    return True


def add_project(name):

    user = get_current_user()

    if user is None:
        return None

    conn = get_db()

    cursor = conn.execute(
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

    project_id = cursor.lastrowid

    conn.execute(
        """
        INSERT OR IGNORE INTO project_members
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

    return project_id


def get_project_name(project_id):

    user = get_current_user()

    if user is None:
        return None

    conn = get_db()

    project = conn.execute(
        """
        SELECT projects.name
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

    if project is None:
        return None

    return project["name"]


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

    if priority not in allowed_priorities:
        priority = "normal"

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


def toggle_task(project_id, task_id):

    conn = get_db()

    task = conn.execute(
        """
        SELECT status
        FROM tasks
        WHERE id = ?
        AND project_id = ?
        """,
        (
            task_id,
            project_id
        )
    ).fetchone()

    if task is not None:

        if task["status"] == "done":

            conn.execute(
                """
                UPDATE tasks
                SET done = 0,
                    status = 'todo'
                WHERE id = ?
                AND project_id = ?
                """,
                (
                    task_id,
                    project_id
                )
            )

        else:

            conn.execute(
                """
                UPDATE tasks
                SET done = 1,
                    status = 'done'
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

    if status not in allowed_statuses:
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

    if user is None:
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

    if priority not in allowed_priorities:
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
        FROM project_members
        JOIN users
            ON users.id = project_members.user_id
        WHERE project_members.project_id = ?
        ORDER BY
            CASE
                WHEN users.role = 'teacher' THEN 0
                ELSE 1
            END,
            users.username
        """,
        (project_id,)
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

PAGE_STYLE = """
<style>
@import url('/static/mflow-theme.css');

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    background: #f5f7fb;
    color: #172033;
    font-family: Inter, Arial, sans-serif;
}

a {
    color: inherit;
    text-decoration: none;
}

button,
input,
select {
    font-family: inherit;
}

.container {
    width: min(1150px, calc(100% - 40px));
    margin: 0 auto;
}


/* HEADER */

.main-header {
    height: 78px;
    display: flex;
    align-items: center;
    justify-content: space-between;
}

.brand {
    font-size: 27px;
    font-weight: 800;
    letter-spacing: -1px;
    color: #1f2937;
}

.brand span {
    color: #2563eb;
}

.account {
    display: flex;
    align-items: center;
    gap: 12px;
}

.account-text {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
}

.account-text strong {
    font-size: 14px;
}

.account-text small {
    margin-top: 3px;
    color: #8a94a6;
    font-size: 12px;
}

.avatar {
    width: 40px;
    height: 40px;
    border-radius: 12px;
    background: #2563eb;
    color: white;
    display: flex;
    justify-content: center;
    align-items: center;
    font-weight: 700;
}

.logout {
    margin-left: 5px;
    color: #8a94a6;
    font-size: 13px;
}

.logout:hover {
    color: #2563eb;
}


/* HERO */

.hero {
    margin-top: 28px;
    padding: 38px;
    border-radius: 24px;
    background: linear-gradient(
        135deg,
        #1d4ed8,
        #4f46e5
    );
    color: white;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 30px;
    box-shadow:
        0 18px 40px
        rgba(37, 99, 235, 0.18);
}

.eyebrow {
    display: block;
    margin-bottom: 10px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1.5px;
    color: rgba(255,255,255,0.65);
}

.hero h1 {
    margin: 0;
    font-size: 36px;
    letter-spacing: -1px;
    color: white;
}

.hero p {
    margin: 10px 0 0;
    color: rgba(255,255,255,0.78);
    font-size: 15px;
}

.overall-progress {
    min-width: 230px;
}

.overall-progress span {
    display: block;
    margin-bottom: 5px;
    color: rgba(255,255,255,0.7);
    font-size: 12px;
}

.overall-progress strong {
    display: block;
    margin-bottom: 10px;
    font-size: 30px;
}

.main-progress {
    width: 100%;
    height: 8px;
    background: rgba(255,255,255,0.2);
    border-radius: 20px;
    overflow: hidden;
}

.main-progress div {
    height: 100%;
    background: white;
    border-radius: 20px;
}


/* STATS */

.stats-line {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    margin: 22px 0 38px;
    background: white;
    border: 1px solid #e7ebf2;
    border-radius: 18px;
    overflow: hidden;
}

.stats-line > div {
    padding: 20px 25px;
    border-right: 1px solid #edf0f5;
}

.stats-line > div:last-child {
    border-right: none;
}

.stats-line strong {
    display: block;
    font-size: 25px;
}

.stats-line span {
    display: block;
    margin-top: 4px;
    color: #8a94a6;
    font-size: 13px;
}


/* SECTIONS */

.section-title {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 18px;
}

.section-title h2 {
    margin: 0;
    font-size: 23px;
}

.section-title p {
    margin: 5px 0 0;
    color: #8a94a6;
    font-size: 13px;
}


/* BUTTONS */

button {
    border: none;
    border-radius: 10px;
    padding: 10px 15px;
    background: #2563eb;
    color: white;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    transition: 0.18s ease;
}

button:hover {
    background: #1d4ed8;
    transform: translateY(-1px);
}

button.secondary {
    background: #eef2f7;
    color: #344054;
}

button.secondary:hover {
    background: #e2e8f0;
}


/* INPUTS */

input,
.priority-select {
    width: 100%;
    padding: 11px 13px;
    border: 1px solid #dce2eb;
    border-radius: 10px;
    background: white;
    color: #172033;
    outline: none;
}

input:focus,
.priority-select:focus {
    border-color: #2563eb;
    box-shadow:
        0 0 0 3px
        rgba(37,99,235,0.1);
}


/* PROJECT */

.new-project {
    display: none;
    padding: 15px;
    margin-bottom: 15px;
    background: white;
    border: 1px solid #e3e8f0;
    border-radius: 14px;
}

.new-project.show {
    display: flex;
    gap: 8px;
}

.new-project input {
    flex: 1;
}

.projects-list {
    display: flex;
    flex-direction: column;
    gap: 10px;
}

.project-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 17px 19px;
    background: white;
    border: 1px solid #e5e9f0;
    border-radius: 15px;
    transition: 0.18s ease;
}

.project-row:hover {
    border-color: #cbd7f8;
    box-shadow:
        0 8px 22px
        rgba(30,41,59,0.07);
    transform: translateY(-1px);
}

.project-info {
    display: flex;
    align-items: center;
    gap: 13px;
}

.project-symbol {
    width: 40px;
    height: 40px;
    border-radius: 11px;
    background: #eef4ff;
    color: #2563eb;
    display: flex;
    justify-content: center;
    align-items: center;
    font-weight: 800;
}

.project-info h3 {
    margin: 0 0 4px;
    font-size: 15px;
}

.project-info span {
    color: #8a94a6;
    font-size: 12px;
}

.project-status {
    display: flex;
    align-items: center;
    gap: 12px;
}

.project-status > span {
    min-width: 38px;
    color: #667085;
    font-size: 12px;
    text-align: right;
}

.project-status b {
    color: #9aa4b5;
    font-size: 18px;
}

.mini-progress {
    width: 100px;
    height: 6px;
    background: #e9edf4;
    border-radius: 20px;
    overflow: hidden;
}

.mini-progress div {
    height: 100%;
    background: #2563eb;
    border-radius: 20px;
}


/* EMPTY */

.empty {
    padding: 55px 20px;
    text-align: center;
    background: white;
    border: 1px dashed #d5dce7;
    border-radius: 17px;
}

.empty h3 {
    margin: 0 0 7px;
}

.empty p {
    margin: 0;
    color: #8a94a6;
    font-size: 13px;
}


/* PROJECT PAGE */

.project-header {
    margin-top: 25px;
    margin-bottom: 25px;
}

.project-header h1 {
    margin: 8px 0 0;
    font-size: 32px;
    letter-spacing: -1px;
}

.back {
    color: #667085;
    font-size: 13px;
}

.back:hover {
    color: #2563eb;
}


/* CARDS */

.card {
    background: white;
    border: 1px solid #e5e9f0;
    border-radius: 17px;
    padding: 24px;
    margin: 18px 0;
}

.card h2 {
    margin-top: 0;
    font-size: 20px;
}

.card p {
    color: #667085;
}


/* PROGRESS */

.project-progress {
    width: 100%;
    height: 9px;
    margin-top: 15px;
    background: #e9edf4;
    border-radius: 20px;
    overflow: hidden;
}

.project-progress div {
    height: 100%;
    background: #2563eb;
    border-radius: 20px;
}


/* KANBAN */

.kanban-wrapper {
    margin-top: 25px;
}

.kanban {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 18px;
    align-items: start;
}

.kanban-column {
    background: #eef1f6;
    border-radius: 18px;
    padding: 14px;
    min-height: 350px;
}

.kanban-column.drag-over {
    background: #e4ebfb;
    outline: 2px dashed #2563eb;
    outline-offset: -2px;
}

.kanban-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 4px 4px 13px;
}

.kanban-title {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    font-weight: 700;
}

.kanban-dot {
    width: 9px;
    height: 9px;
    border-radius: 50%;
}

.dot-todo {
    background: #94a3b8;
}

.dot-progress {
    background: #f59e0b;
}

.dot-done {
    background: #22c55e;
}

.kanban-count {
    min-width: 25px;
    height: 25px;
    padding: 0 7px;
    border-radius: 8px;
    background: white;
    color: #667085;
    display: flex;
    justify-content: center;
    align-items: center;
    font-size: 11px;
    font-weight: 700;
}

.kanban-cards {
    min-height: 280px;
}

.task-card {
    position: relative;
    background: white;
    border: 1px solid #e5e9f0;
    border-radius: 14px;
    padding: 15px;
    margin-bottom: 10px;
    cursor: grab;
    box-shadow: 0 4px 12px rgba(15,23,42,0.04);
    transition: 0.18s ease;
}

.task-card:hover {
    box-shadow: 0 8px 20px rgba(15,23,42,0.08);
    transform: translateY(-1px);
}

.task-card:active {
    cursor: grabbing;
}

.task-card.dragging {
    opacity: 0.45;
    transform: rotate(2deg);
}

.task-title {
    margin-bottom: 10px;
    font-size: 14px;
    font-weight: 650;
    line-height: 1.45;
    word-break: break-word;
}

.task-card.done-card .task-title {
    color: #98a2b3;
    text-decoration: line-through;
}


/* PRIORITY */

.task-priority {
    display: inline-flex;
    align-items: center;
    margin-bottom: 10px;
    padding: 5px 8px;
    border-radius: 7px;
    background: #f5f7fb;
    color: #667085;
    font-size: 11px;
}

.task-priority-select {
    width: 100%;
    margin-bottom: 10px;
    padding: 7px 9px;
    border: 1px solid #e1e6ef;
    border-radius: 8px;
    background: #f8fafc;
    color: #475467;
    font-size: 11px;
    outline: none;
    cursor: pointer;
}

.task-priority-select:focus {
    border-color: #2563eb;
}

.add-task-priority {
    min-width: 180px;
}


/* DEADLINE */

.task-deadline {
    display: inline-flex;
    align-items: center;
    padding: 5px 8px;
    border-radius: 7px;
    background: #f5f7fb;
    color: #667085;
    font-size: 11px;
}

.task-footer {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
}

.delete-task {
    border: none;
    background: transparent;
    color: #98a2b3;
    padding: 4px 6px;
    font-size: 11px;
}

.delete-task:hover {
    color: #ef4444;
    background: #fff1f2;
    transform: none;
}

.kanban-empty {
    padding: 30px 10px;
    text-align: center;
    color: #98a2b3;
    font-size: 12px;
}


/* ADD TASK */

.add-task-form {
    display: grid;
    grid-template-columns: 1fr 180px 180px auto;
    gap: 8px;
}


/* MEMBERS */

.member {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    padding: 13px 0;
    border-bottom: 1px solid #edf0f5;
}

.member:last-child {
    border-bottom: none;
}

.role {
    margin-left: auto;
    color: #98a2b3;
    font-size: 13px;
}


/* MESSAGES */

.error,
.success {
    padding: 12px 15px;
    margin: 15px 0;
    border-radius: 10px;
    font-size: 13px;
}

.error {
    background: #fff1f2;
    color: #be123c;
    border: 1px solid #fecdd3;
}

.success {
    background: #ecfdf3;
    color: #027a48;
    border: 1px solid #abefc6;
}


/* LOGIN / REGISTER */

.auth-page {
    min-height: 100vh;
    display: flex;
    justify-content: center;
    align-items: center;
    padding: 20px;
    background: #171717;
}

.auth-card {
    width: 100%;
    max-width: 860px;
    display: grid;
    grid-template-columns: 1fr 1fr;
    background: white;
    border-radius: 28px;
    overflow: hidden;
    box-shadow: 0 30px 70px rgba(0,0,0,.35);
}

.auth-hero {
    background: #171717;
    color: white;
    padding: 48px 40px;
    display: flex;
    flex-direction: column;
    justify-content: center;
    position: relative;
    overflow: hidden;
}

.auth-hero::after {
    content: "";
    position: absolute;
    width: 220px;
    height: 220px;
    right: -60px;
    top: -80px;
    background: #ffcc00;
    border-radius: 50%;
    opacity: .9;
}

.auth-hero-content { position: relative; z-index: 1; }

.auth-logo {
    font-size: 26px;
    font-weight: 800;
    margin-bottom: 18px;
}

.auth-logo span { color: #ffcc00; }

.auth-hero h2 {
    font-size: 24px;
    margin: 0 0 10px;
}

.auth-hero p {
    color: rgba(255,255,255,.65);
    font-size: 14px;
    line-height: 1.5;
}

.auth-form-side {
    padding: 48px 40px;
}

.auth-tabs {
    display: flex;
    gap: 6px;
    margin-bottom: 28px;
    background: #f3f3f1;
    padding: 5px;
    border-radius: 12px;
}

.auth-tab {
    flex: 1;
    text-align: center;
    padding: 10px;
    border-radius: 9px;
    text-decoration: none;
    color: #777;
    font-weight: 600;
    font-size: 14px;
}

.auth-tab.active {
    background: #171717;
    color: white;
}

.auth-form-side input {
    width: 100%;
    margin-bottom: 12px;
    padding: 13px 14px;
    border-radius: 12px;
    border: 1px solid #e6e6e2;
}

.auth-form-side button {
    width: 100%;
    padding: 13px;
    margin-top: 6px;
    border-radius: 12px;
    border: 0;
    background: #171717;
    color: white;
    font-weight: 700;
    cursor: pointer;
}

.auth-form-side button:hover { background: #ffcc00; color: #171717; }

@media (max-width: 700px) {
    .auth-card { grid-template-columns: 1fr; }
    .auth-hero { padding: 32px 28px; }
}


/* MOBILE */

@media (max-width: 1050px) {

    .add-task-form {
        grid-template-columns:
            1fr 150px 150px auto;
    }
}

@media (max-width: 850px) {

    .kanban {
        grid-template-columns: 1fr;
    }

    .kanban-column {
        min-height: auto;
    }

    .kanban-cards {
        min-height: 80px;
    }

    .add-task-form {
        grid-template-columns: 1fr;
    }

    .add-task-priority {
        min-width: 0;
    }
}

@media (max-width: 700px) {

    .container {
        width: min(100% - 28px, 1150px);
    }

    .hero {
        flex-direction: column;
        align-items: flex-start;
        padding: 28px;
    }

    .overall-progress {
        width: 100%;
    }

    .hero h1 {
        font-size: 29px;
    }

    .stats-line {
        grid-template-columns: 1fr;
    }

    .stats-line > div {
        border-right: none;
        border-bottom: 1px solid #edf0f5;
    }

    .stats-line > div:last-child {
        border-bottom: none;
    }

    .section-title {
        align-items: flex-start;
        gap: 15px;
    }

    .project-row {
        gap: 15px;
    }

    .project-status {
        gap: 6px;
    }

    .mini-progress {
        display: none;
    }

    .account-text {
        display: none;
    }

    .member {
        flex-wrap: wrap;
    }

    .role {
        margin-left: 0;
    }

    .new-project.show {
        flex-direction: column;
    }
}

</style>


<script>


// =========================================================
// KANBAN
// =========================================================

function setupKanban() {

    const cards =
        document.querySelectorAll(".task-card");

    const columns =
        document.querySelectorAll(".kanban-column");


    cards.forEach(card => {

        card.addEventListener(
            "dragstart",
            () => {

                card.classList.add("dragging");

            }
        );


        card.addEventListener(
            "dragend",
            () => {

                card.classList.remove("dragging");

                columns.forEach(column => {

                    column.classList.remove(
                        "drag-over"
                    );

                });

            }
        );

    });


    columns.forEach(column => {

        column.addEventListener(
            "dragover",
            event => {

                event.preventDefault();

                column.classList.add(
                    "drag-over"
                );

            }
        );


        column.addEventListener(
            "dragleave",
            event => {

                if (
                    !column.contains(
                        event.relatedTarget
                    )
                ) {

                    column.classList.remove(
                        "drag-over"
                    );

                }

            }
        );


        column.addEventListener(
            "drop",
            async event => {

                event.preventDefault();

                column.classList.remove(
                    "drag-over"
                );


                const card =
                    document.querySelector(
                        ".dragging"
                    );


                if (!card) {
                    return;
                }


                const taskId =
                    card.dataset.taskId;

                const newStatus =
                    column.dataset.status;


                const cardsContainer =
                    column.querySelector(
                        ".kanban-cards"
                    );


                cardsContainer.appendChild(
                    card
                );


                const response =
                    await fetch(
                        window.location.pathname
                        + "/status",
                        {
                            method: "POST",

                            headers: {
                                "Content-Type":
                                    "application/json"
                            },

                            body: JSON.stringify({
                                task_id:
                                    Number(taskId),

                                status:
                                    newStatus
                            })
                        }
                    );


                if (!response.ok) {

                    alert(
                        "Не удалось изменить статус"
                    );

                    location.reload();

                    return;
                }


                updateKanbanCounts();

            }
        );

    });


    updateKanbanCounts();
}


// =========================================================
// CHANGE PRIORITY
// =========================================================

async function changePriority(select) {

    const taskId =
        Number(select.dataset.taskId);

    const priority =
        select.value;


    const response =
        await fetch(
            window.location.pathname
            + "/priority",
            {
                method: "POST",

                headers: {
                    "Content-Type":
                        "application/json"
                },

                body: JSON.stringify({
                    task_id: taskId,
                    priority: priority
                })
            }
        );


    if (!response.ok) {

        alert(
            "Не удалось изменить приоритет"
        );

        location.reload();

        return;
    }


    location.reload();
}


// =========================================================
// KANBAN COUNTS
// =========================================================

function updateKanbanCounts() {

    const columns =
        document.querySelectorAll(
            ".kanban-column"
        );


    columns.forEach(column => {

        const count =
            column.querySelectorAll(
                ".task-card"
            ).length;


        const counter =
            column.querySelector(
                ".kanban-count"
            );


        if (counter) {

            counter.textContent =
                count;

        }


        const cardsContainer =
            column.querySelector(
                ".kanban-cards"
            );


        const empty =
            cardsContainer.querySelector(
                ".kanban-empty"
            );


        if (
            count === 0
            && !empty
        ) {

            const placeholder =
                document.createElement(
                    "div"
                );

            placeholder.className =
                "kanban-empty";

            placeholder.textContent =
                "Перетащи задачу сюда";


            cardsContainer.appendChild(
                placeholder
            );

        }


        if (
            count > 0
            && empty
        ) {

            empty.remove();

        }

    });
}


document.addEventListener(
    "DOMContentLoaded",
    setupKanban
);

</script>
"""


# =========================================================
# LOGIN
# =========================================================

def render_auth_page(mode, error=None):
    is_login = mode == "login"

    error_html = f'<div class="error">{escape(error)}</div>' if error else ""

    if is_login:
        form_fields = """
            <input name="username" placeholder="Логин" required>
            <input name="password" type="password" placeholder="Пароль" required>
        """
        submit_label = "Войти"
        hero_title = "С возвращением"
        hero_text = "Заходи в своё рабочее пространство — задачи и дедлайны на месте."
    else:
        form_fields = """
            <input name="username" placeholder="Придумай логин" required>
            <input name="password" type="password" placeholder="Придумай пароль" required>
        """
        submit_label = "Зарегистрироваться"
        hero_title = "Начни работу с M-Flow"
        hero_text = "Создай аккаунт ученика, чтобы вести свои проекты и задачи."

    return render_template_string(
        PAGE_STYLE
        + f"""
        <div class="auth-page">
            <div class="auth-card">
                <div class="auth-hero">
                    <div class="auth-hero-content">
                        <div class="auth-logo">M<span>-</span>Flow</div>
                        <h2>{hero_title}</h2>
                        <p>{hero_text}</p>
                    </div>
                </div>
                <div class="auth-form-side">
                    <div class="auth-tabs">
                        <a href="/login" class="auth-tab {'active' if is_login else ''}">Вход</a>
                        <a href="/register" class="auth-tab {'active' if not is_login else ''}">Регистрация</a>
                    </div>
                    {error_html}
                    <form method="post">
                        {form_fields}
                        <button type="submit">{submit_label}</button>
                    </form>
                </div>
            </div>
        </div>
        """
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE username = ?",
            (username,)
        ).fetchone()

        if user is None:
            conn.close()
            return render_auth_page("login", error="Неверный логин или пароль.")

        stored_password = user["password"]

        try:
            password_ok = check_password_hash(stored_password, password)
        except (ValueError, TypeError):
            password_ok = (stored_password == password)

        if not password_ok:
            conn.close()
            return render_auth_page("login", error="Неверный логин или пароль.")

        if stored_password == password:
            new_password = generate_password_hash(password)
            conn.execute(
                "UPDATE users SET password = ? WHERE id = ?",
                (new_password, user["id"])
            )
            conn.commit()

        conn.close()
        session["user_id"] = user["id"]
        return redirect("/")

    return render_auth_page("login")

# =========================================================
# LOGOUT
# =========================================================
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            return render_auth_page("register", error="Заполни оба поля.")

        conn = get_db()

        existing = conn.execute(
            "SELECT id FROM users WHERE username = ?",
            (username,)
        ).fetchone()

        if existing is not None:
            conn.close()
            return render_auth_page("register", error="Такой логин уже занят.")

        cursor = conn.execute(
            "INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
            (username, generate_password_hash(password), "student")
        )

        conn.commit()
        new_user_id = cursor.lastrowid
        conn.close()

        session["user_id"] = new_user_id
        return redirect("/")

    return render_auth_page("register")

@app.route("/logout")
def logout():

    session.clear()

    return redirect("/login")


# =========================================================
# MAIN PAGE
# =========================================================

@app.route("/")
@login_required
def index():

    user = get_current_user()
    projects = get_projects()

    total_projects = len(projects)
    total_tasks = 0
    completed_tasks = 0
    active_tasks = 0
    overdue_tasks = 0
    today = date.today().isoformat()

    projects_html = ""


    for project in projects:

        project_id = project["id"]
        project_name = escape(
            project["name"]
        )

        tasks = get_tasks(
            project_id
        )

        project_total = len(tasks)

        project_done = sum(
            task["status"] == "done"
            for task in tasks
        )

        total_tasks += project_total
        completed_tasks += project_done
        active_tasks += sum(
            task["status"] == "progress"
            for task in tasks
        )
        overdue_tasks += sum(
             1
            for task in tasks
            if task["deadline"]
            and task["deadline"] < today
            and task["status"] != "done"
        )


        if project_total:

            project_progress = round(
                project_done
                / project_total
                * 100
            )

        else:

            project_progress = 0


        projects_html += f"""
        <a
            class="project-row"
            href="/project/{project_id}"
        >

            <div class="project-info">

                <div class="project-symbol">
                    M
                </div>

                <div>

                    <h3>
                        {project_name}
                    </h3>

                    <span>
                        {project_done}
                        из
                        {project_total}
                        задач выполнено
                    </span>

                </div>

            </div>

            <div class="project-status">

                <div class="mini-progress">

                    <div
                        style="width: {project_progress}%"
                    ></div>

                </div>

                <span>
                    {project_progress}%
                </span>

                <b>→</b>

            </div>

        </a>
        """


    progress = (
        round(
            completed_tasks
            / total_tasks
            * 100
        )
        if total_tasks
        else 0
    )


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


    if projects_html:

        project_content = projects_html

    else:

        project_content = """
        <div class="empty">

            <h3>
                Проектов пока нет
            </h3>

            <p>
                Создай первый проект,
                чтобы начать работу.
            </p>

        </div>
        """


    role_name = (
        "Учитель"
        if user["role"] == "teacher"
        else "Ученик"
    )

    username = escape(
        user["username"]
    )

    avatar = escape(
        user["username"][0].upper()
    )


    return render_template_string(
        PAGE_STYLE
        + f"""

        <div class="container">

            <header class="main-header">

                <a
                    href="/"
                    class="brand"
                >
                    M<span>-</span>Flow
                </a>

                <div class="account">

                    <div class="account-text">

                        <strong>
                            {username}
                        </strong>

                        <small>
                            {role_name}
                        </small>

                    </div>

                    <div class="avatar">
                        {avatar}
                    </div>

                    <a
                        href="/logout"
                        class="logout"
                    >
                        Выйти
                    </a>

                </div>

            </header>


            <main>

                <section class="hero">

                    <div>

                        <span class="eyebrow">
                            РАБОЧЕЕ ПРОСТРАНСТВО
                        </span>

                        <h1>
                            Привет,
                            {username}
                            👋
                        </h1>

                        <p>
                            Все школьные проекты
                            в одном месте.
                        </p>

                    </div>

                    <div class="overall-progress">

                        <span>
                            Общий прогресс
                        </span>

                        <strong>
                            {progress}%
                        </strong>

                        <div class="main-progress">

                            <div
                                style="width: {progress}%"
                            ></div>

                        </div>

                    </div>

                </section>


                {messages}


                <section class="stats-line">

                    <div>

                        <strong>
                            {total_projects}
                        </strong>

                        <span>
                            Проектов
                        </span>

                    </div>

                    <div>

                        <strong>
                            {total_tasks}
                        </strong>

                        <span>
                            Всего задач
                        </span>

                    </div>

                    <div>

                        <strong>
                            {completed_tasks}
                        </strong>

                        <span>
                            Выполнено
                        </span>

                    </div>

                    <div>

                        <strong>
                            {overdue_tasks}
                        </strong>

                        <span>
                            Просрочено
                        </span>

                    </div>

                </section>


                <section class="focus-strip">

                    <div class="focus-icon">◌</div>

                    <div>
                        <strong>Фокус на сегодня</strong>
                        <span>В работе: {active_tasks} · Просрочено: {overdue_tasks}</span>
                    </div>

                    <div class="focus-mark">M</div>

                </section>


                <section>

                    <div class="section-title">

                        <div>

                            <h2>
                                Мои проекты
                            </h2>

                            <p>
                                Проекты, к которым
                                у тебя есть доступ
                            </p>

                        </div>

                        <button
                            onclick="
                                document
                                .getElementById('project-form')
                                .classList
                                .toggle('show')
                            "
                        >
                            + Новый проект
                        </button>

                    </div>


                    <form
                        id="project-form"
                        class="new-project"
                        action="/add_project"
                        method="post"
                    >

                        <input
                            name="name"
                            placeholder="Название проекта"
                            required
                        >

                        <button type="submit">
                            Создать
                        </button>

                    </form>


                    <div class="projects-list">

                        {project_content}

                    </div>

                </section>

            </main>

        </div>
        """
    )


# =========================================================
# CREATE PROJECT
# =========================================================

@app.route(
    "/add_project",
    methods=["POST"]
)
@login_required
def create_project():

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

    project_name = get_project_name(
        project_id
    )


    if project_name is None:

        return (
            "Проект не найден или у вас нет доступа.",
            403
        )


    tasks = get_tasks(
        project_id
    )

    members = get_project_members(
        project_id
    )

    student_progress = get_student_progress(
        project_id
    )

    owner = is_project_owner(
        project_id
    )

    current_user = get_current_user()


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


    total_tasks = len(tasks)
    completed_tasks = len(done_tasks)
    todo_count = len(todo_tasks)
    progress_count = len(progress_tasks)
    overdue_count = sum(
         1
         for task in tasks
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


        priority_icons = {
            "low": "🟢",
            "normal": "🔵",
            "high": "🟠",
            "urgent": "🔴"
        }


        priority = (
            task["priority"]
            or "normal"
        )


        priority_name = priority_names.get(
            priority,
            "Обычный"
        )


        priority_icon = priority_icons.get(
            priority,
            "🔵"
        )


        # ВАЖНО:
        # Все значения заранее считаются здесь.
        # Благодаря этому внутри f-string нет
        # сложных выражений.

        task_id = task["id"]

        can_update = user_can_update_task(project_id, task_id)
        draggable = "true" if can_update else "false"
        assignee_name = escape(task["assignee_name"] or "Не назначена")

        delete_control = f"""
            <form action="/project/{project_id}/delete/{task_id}" method="post">
                <button type="submit" class="delete-task" title="Удалить задачу">Удалить</button>
            </form>
        """ if owner else ""

        task_title = escape(
            task["title"]
        )

        safe_deadline = escape(
            deadline
        )

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

        priority_control = f"""
            <select class="task-priority-select" data-task-id="{task_id}" onchange="changePriority(this)">
                <option value="low" {low_selected}>🟢 Низкий</option>
                <option value="normal" {normal_selected}>🔵 Обычный</option>
                <option value="high" {high_selected}>🟠 Высокий</option>
                <option value="urgent" {urgent_selected}>🔴 Срочный</option>
            </select>
        """ if owner else ""


        return f"""
        <div
            class="task-card {done_class}"
            draggable="{draggable}"
            data-task-id="{task_id}"
        >

            <div class="task-title">
                {task_title}
            </div>


            <div class="task-priority">
                {priority_icon}
                {priority_name}
            </div>


            {priority_control}


            <div class="task-footer">

                <span class="task-deadline">
                    📅 {safe_deadline}
                </span>

                <span class="role">👤 {assignee_name}</span>

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
                Перетащи задачу сюда
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

    if owner and current_user["role"] == "teacher":
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
                <span class="role">{completed_count} из {task_count} задач · {student_percent}%</span>
            </div>
            """

        if not student_progress_html:
            student_progress_html = "<p>Добавьте учеников в проект, чтобы видеть их прогресс.</p>"

    teacher_dashboard = f"""
    <div class="card">
        <h2>Прогресс учеников</h2>
        {student_progress_html}
    </div>
    """ if owner and current_user["role"] == "teacher" else ""

    add_task_section = f"""
    <div class="card">
        <h2>Добавить задачу</h2>
        <form action="/project/{project_id}/add" method="post" class="add-task-form">
            <input name="title" placeholder="Название задачи" required>
            <input name="deadline" type="date">
            <select name="priority" class="priority-select add-task-priority">
                <option value="low">🟢 Низкий</option>
                <option value="normal" selected>🔵 Обычный</option>
                <option value="high">🟠 Высокий</option>
                <option value="urgent">🔴 Срочный</option>
            </select>
            <select name="assignee_id" class="priority-select">
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
        <div class="card">

            <h2>
                Добавить участника
            </h2>

            <form
                action="/project/{project_id}/add_member"
                method="post"
                style="
                    display:flex;
                    gap:8px;
                "
            >

                <input
                    name="username"
                    placeholder="Логин пользователя"
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


    # =====================================================
    # CURRENT USER DATA
    # =====================================================

    current_username = escape(
        current_user["username"]
    )


    current_role = (
        "Учитель"
        if current_user["role"] == "teacher"
        else "Ученик"
    )


    current_avatar = escape(
        current_user["username"][0].upper()
    )


    safe_project_name = escape(
        project_name
    )


    # =====================================================
    # PROJECT PAGE
    # =====================================================

    return render_template_string(
        PAGE_STYLE
        + f"""

        <div class="container">

            <header class="main-header">

                <a
                    href="/"
                    class="brand"
                >
                    M<span>-</span>Flow
                </a>

                <div class="account">

                    <div class="account-text">

                        <strong>
                            {current_username}
                        </strong>

                        <small>
                            {current_role}
                        </small>

                    </div>

                    <div class="avatar">
                        {current_avatar}
                    </div>

                    <a
                        href="/logout"
                        class="logout"
                    >
                        Выйти
                    </a>

                </div>

            </header>


            <main>

                <div class="project-header">

                    <a
                        href="/"
                        class="back"
                    >
                        ← Все проекты
                    </a>

                    <h1>
                        {safe_project_name}
                    </h1>

                </div>


                {messages}


                <div class="card">

                    <div class="section-title">

                        <div>

                            <h2>
                                Прогресс проекта
                            </h2>

                            <p>
                                {completed_tasks}
                                из
                                {total_tasks}
                                задач выполнено
                            </p>

                        </div>

                        <strong
                            style="
                                font-size: 28px;
                            "
                        >
                            {progress}%
                        </strong>

                    </div>


                    <div class="project-progress">

                        <div
                            style="
                                width: {progress}%;
                            "
                        ></div>

                    </div>

                </div>


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


                <div class="kanban-wrapper">

                    <div class="section-title">

                        <div>

                            <h2>
                                Доска задач
                            </h2>

                            <p>
                                Перетаскивай задачи
                                между колонками
                            </p>

                        </div>

                    </div>

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


    conn.execute(
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


    if not data:

        return jsonify({
            "success": False,
            "error": "Нет данных"
        }), 400


    task_id = data.get("task_id")
    status = data.get("status")


    if not isinstance(
        task_id,
        int
    ):

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


    return jsonify({
        "success": True
    })


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


    if not data:

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


    if not isinstance(
        task_id,
        int
    ):

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
# OLD TOGGLE ROUTE
# =========================================================

@app.route(
    "/project/<int:project_id>/toggle/<int:task_id>",
    methods=["POST"]
)
@login_required
def toggle_project_task(
    project_id,
    task_id
):

    if not user_has_project_access(
        project_id
    ):

        return (
            "Проект не найден или у вас нет доступа.",
            403
        )


    toggle_task(
        project_id,
        task_id
    )


    return redirect(
        f"/project/{project_id}"
    )


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
# ERROR PAGES
# =========================================================

def render_error_page(title, message, status_code):

    return render_template_string(
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


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    app.run(
        debug=os.environ.get("M_FLOW_DEBUG") == "1"
    )
