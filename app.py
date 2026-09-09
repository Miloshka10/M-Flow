from flask import Flask, request, redirect
import sqlite3

app = Flask(__name__)

def get_projects():
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM projects")
    rows = cursor.fetchall()
    conn.close()
    return rows

def add_project(name):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO projects (name) VALUES (?)", (name,))
    conn.commit()
    conn.close()

def get_project_name(project_id):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM projects WHERE id = ?", (project_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else "Проект"

def get_tasks(project_id):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, done, deadline FROM tasks WHERE project_id = ?", (project_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def add_task(project_id, title, deadline):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO tasks (project_id, title, done, deadline) VALUES (?, ?, ?, ?)", (project_id, title, 0, deadline))
    conn.commit()
    conn.close()

def toggle_task(task_id):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT done FROM tasks WHERE id = ?", (task_id,))
    current = cursor.fetchone()[0]
    new_value = 0 if current == 1 else 1
    cursor.execute("UPDATE tasks SET done = ? WHERE id = ?", (new_value, task_id))
    conn.commit()
    conn.close()

def delete_task(task_id):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()

PAGE_STYLE = """
<style>
    body { font-family: Arial, sans-serif; max-width: 500px; margin: 50px auto; background: #f4f6f8; color: #222; }
    h1 { color: #2d6a4f; }
    ul { list-style: none; padding: 0; }
    li { display: flex; justify-content: space-between; align-items: center; background: white; padding: 12px 16px; margin-bottom: 8px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
    li.done span { text-decoration: line-through; color: #888; }
    small { color: #999; margin-left: 6px; }
    a { font-size: 13px; color: #2d6a4f; text-decoration: none; margin-left: 10px; }
    a.back { display: inline-block; margin-bottom: 15px; }
    form { margin-top: 20px; display: flex; gap: 8px; }
    input { flex: 1; padding: 10px; border: 1px solid #ccc; border-radius: 6px; }
    button { padding: 10px 16px; background: #2d6a4f; color: white; border: none; border-radius: 6px; cursor: pointer; }
</style>
"""

@app.route("/")
def home():
    projects = get_projects()
    items = ""
    for project_id, name in projects:
        items += f'<li><span>{name}</span><a href="/project/{project_id}">открыть</a></li>'

    html = f"""
    <html><head><title>M-Flow</title>{PAGE_STYLE}</head>
    <body>
        <h1>M-Flow — мои проекты</h1>
        <ul>{items}</ul>
        <form method="POST" action="/add_project">
            <input type="text" name="name" placeholder="Название нового проекта" required>
            <button type="submit">Создать</button>
        </form>
    </body></html>
    """
    return html

@app.route("/add_project", methods=["POST"])
def add_project_route():
    name = request.form.get("name")
    if name:
        add_project(name)
    return redirect("/")

@app.route("/project/<int:project_id>")
def project_page(project_id):
    project_name = get_project_name(project_id)
    tasks = get_tasks(project_id)

    task_items = ""
    for task_id, title, done, deadline in tasks:
        status = "✅" if done else "⬜"
        css_class = "done" if done else ""
        deadline_text = f'<small>до {deadline}</small>' if deadline else ""
        task_items += f'''
        <li class="{css_class}">
            <span>{status} {title} {deadline_text}</span>
            <span>
                <a href="/project/{project_id}/toggle/{task_id}">переключить</a>
                <a href="/project/{project_id}/delete/{task_id}">удалить</a>
            </span>
        </li>
        '''

    html = f"""
    <html><head><title>M-Flow</title>{PAGE_STYLE}</head>
    <body>
        <a class="back" href="/">← Все проекты</a>
        <h1>{project_name}</h1>
        <ul>{task_items}</ul>
        <form method="POST" action="/project/{project_id}/add">
            <input type="text" name="title" placeholder="Новая задача" required>
            <input type="date" name="deadline">
            <button type="submit">Добавить</button>
        </form>
    </body></html>
    """
    return html

@app.route("/project/<int:project_id>/add", methods=["POST"])
def add_task_route(project_id):
    title = request.form.get("title")
    deadline = request.form.get("deadline")
    if title:
        add_task(project_id, title, deadline)
    return redirect(f"/project/{project_id}")

@app.route("/project/<int:project_id>/toggle/<int:task_id>")
def toggle_route(project_id, task_id):
    toggle_task(task_id)
    return redirect(f"/project/{project_id}")

@app.route("/project/<int:project_id>/delete/<int:task_id>")
def delete_route(project_id, task_id):
    delete_task(task_id)
    return redirect(f"/project/{project_id}")

if __name__ == "__main__":
    app.run(debug=True)