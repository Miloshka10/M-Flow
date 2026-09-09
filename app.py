from flask import Flask, request, redirect
import sqlite3

app = Flask(__name__)


def get_db():
    return sqlite3.connect("database.db")


def get_projects():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM projects ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    return rows


def add_project(name):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO projects (name) VALUES (?)", (name.strip(),))
    conn.commit()
    conn.close()


def get_project_name(project_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM projects WHERE id = ?", (project_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None


def get_tasks(project_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, title, done, deadline FROM tasks "
        "WHERE project_id = ? ORDER BY done ASC, deadline ASC, id DESC",
        (project_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return rows


def add_task(project_id, title, deadline):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO tasks (project_id, title, done, deadline) VALUES (?, ?, ?, ?)",
        (project_id, title.strip(), 0, deadline or None),
    )
    conn.commit()
    conn.close()


def toggle_task(task_id, project_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT done FROM tasks WHERE id = ? AND project_id = ?",
        (task_id, project_id),
    )
    row = cursor.fetchone()
    if row is None:
        conn.close()
        return

    new_value = 0 if row[0] == 1 else 1
    cursor.execute(
        "UPDATE tasks SET done = ? WHERE id = ? AND project_id = ?",
        (new_value, task_id, project_id),
    )
    conn.commit()
    conn.close()


def delete_task(task_id, project_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM tasks WHERE id = ? AND project_id = ?",
        (task_id, project_id),
    )
    conn.commit()
    conn.close()


PAGE_STYLE = """
<style>
    * { box-sizing: border-box; }
    body {
        font-family: Arial, sans-serif;
        max-width: 700px;
        margin: 40px auto;
        padding: 0 20px;
        background: #f4f6f8;
        color: #222;
    }
    h1 { color: #2d6a4f; margin-bottom: 8px; }
    .subtitle { color: #777; margin-top: 0; }
    ul { list-style: none; padding: 0; }
    li {
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 12px;
        background: white;
        padding: 14px 16px;
        margin-bottom: 10px;
        border-radius: 10px;
        box-shadow: 0 1px 4px rgba(0,0,0,0.08);
    }
    li.done span.task-title { text-decoration: line-through; color: #888; }
    .task-main { flex: 1; }
    .task-title { font-weight: 500; }
    small { color: #999; margin-left: 8px; }
    a { color: #2d6a4f; text-decoration: none; margin-left: 8px; }
    a:hover { text-decoration: underline; }
    a.back { display: inline-block; margin: 0 0 15px 0; }
    form { margin-top: 20px; display: flex; gap: 8px; }
    input {
        flex: 1;
        padding: 11px;
        border: 1px solid #ccc;
        border-radius: 7px;
        font-size: 14px;
    }
    button {
        padding: 11px 17px;
        background: #2d6a4f;
        color: white;
        border: none;
        border-radius: 7px;
        cursor: pointer;
    }
    button:hover { background: #24583f; }
    .stats {
        background: white;
        padding: 14px 16px;
        border-radius: 10px;
        margin: 18px 0;
        box-shadow: 0 1px 4px rgba(0,0,0,0.08);
    }
    .project-link { margin-left: auto; }
    @media (max-width: 600px) {
        form { flex-direction: column; }
        li { align-items: flex-start; flex-direction: column; }
        .project-link { margin-left: 0; }
    }
</style>
"""


@app.route("/")
def home():
    projects = get_projects()
    items = ""
    for project_id, name in projects:
        items += f'''
        <li>
            <span>{name}</span>
            <a class="project-link" href="/project/{project_id}">Открыть →</a>
        </li>
        '''

    if not items:
        items = "<li>Пока нет проектов. Создай первый ниже.</li>"

    html = f"""
    <html><head><title>M-Flow</title>{PAGE_STYLE}</head>
    <body>
        <h1>M-Flow</h1>
        <p class="subtitle">Управление школьными проектами</p>
        <ul>{items}</ul>
        <form method="POST" action="/add_project">
            <input type="text" name="name" placeholder="Название нового проекта" maxlength="100" required>
            <button type="submit">Создать проект</button>
        </form>
    </body></html>
    """
    return html


@app.route("/add_project", methods=["POST"])
def add_project_route():
    name = request.form.get("name", "")
    if name.strip():
        add_project(name)
    return redirect("/")


@app.route("/project/<int:project_id>")
def project_page(project_id):
    project_name = get_project_name(project_id)
    if project_name is None:
        return "Проект не найден", 404

    tasks = get_tasks(project_id)
    completed = sum(1 for _, _, done, _ in tasks if done)
    total = len(tasks)
    progress = round(completed / total * 100) if total else 0

    task_items = ""
    for task_id, title, done, deadline in tasks:
        status = "✅" if done else "⬜"
        css_class = "done" if done else ""
        deadline_text = f'<small>до {deadline}</small>' if deadline else ""
        task_items += f'''
        <li class="{css_class}">
            <div class="task-main">
                <span class="task-title">{status} {title}</span>{deadline_text}
            </div>
            <div>
                <a href="/project/{project_id}/toggle/{task_id}">Переключить</a>
                <a href="/project/{project_id}/delete/{task_id}">Удалить</a>
            </div>
        </li>
        '''

    if not task_items:
        task_items = "<li>В этом проекте пока нет задач.</li>"

    html = f"""
    <html><head><title>{project_name} — M-Flow</title>{PAGE_STYLE}</head>
    <body>
        <a class="back" href="/">← Все проекты</a>
        <h1>{project_name}</h1>
        <div class="stats">
            <strong>Прогресс: {completed} из {total} задач ({progress}%)</strong>
        </div>
        <ul>{task_items}</ul>
        <form method="POST" action="/project/{project_id}/add">
            <input type="text" name="title" placeholder="Новая задача" maxlength="200" required>
            <input type="date" name="deadline">
            <button type="submit">Добавить</button>
        </form>
    </body></html>
    """
    return html


@app.route("/project/<int:project_id>/add", methods=["POST"])
def add_task_route(project_id):
    title = request.form.get("title", "")
    deadline = request.form.get("deadline", "")
    if title.strip() and get_project_name(project_id) is not None:
        add_task(project_id, title, deadline)
    return redirect(f"/project/{project_id}")


@app.route("/project/<int:project_id>/toggle/<int:task_id>")
def toggle_route(project_id, task_id):
    toggle_task(task_id, project_id)
    return redirect(f"/project/{project_id}")


@app.route("/project/<int:project_id>/delete/<int:task_id>")
def delete_route(project_id, task_id):
    delete_task(task_id, project_id)
    return redirect(f"/project/{project_id}")


if __name__ == "__main__":
    app.run(debug=True)
