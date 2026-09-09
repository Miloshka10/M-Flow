from flask import Flask, request, redirect
import sqlite3

app = Flask(__name__)

def get_tasks():
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, done, deadline FROM tasks")
    rows = cursor.fetchall()
    conn.close()
    return rows

def add_task(title, deadline):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO tasks (title, done, deadline) VALUES (?, ?, ?)", (title, 0, deadline))
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

@app.route("/")
def home():
    tasks = get_tasks()

    task_items = ""
    for task_id, title, done, deadline in tasks:
        status = "✅" if done else "⬜"
        css_class = "done" if done else ""
        deadline_text = f'<small>до {deadline}</small>' if deadline else ""
        task_items += f'''
        <li class="{css_class}">
            <span>{status} {title} {deadline_text}</span>
            <span class="actions">
                <a href="/toggle/{task_id}">переключить</a>
                <a href="/delete/{task_id}">удалить</a>
            </span>
        </li>
        '''

    html = f"""
    <html>
    <head>
        <title>M-Flow</title>
        <style>
            body {{ font-family: Arial, sans-serif; max-width: 500px; margin: 50px auto; background: #f4f6f8; color: #222; }}
            h1 {{ color: #2d6a4f; }}
            ul {{ list-style: none; padding: 0; }}
            li {{ display: flex; justify-content: space-between; align-items: center; background: white; padding: 12px 16px; margin-bottom: 8px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
            li.done span {{ text-decoration: line-through; color: #888; }}
            small {{ color: #999; margin-left: 6px; }}
            .actions a {{ font-size: 13px; color: #2d6a4f; text-decoration: none; margin-left: 10px; }}
            form {{ margin-top: 20px; display: flex; gap: 8px; }}
            input {{ flex: 1; padding: 10px; border: 1px solid #ccc; border-radius: 6px; }}
            button {{ padding: 10px 16px; background: #2d6a4f; color: white; border: none; border-radius: 6px; cursor: pointer; }}
        </style>
    </head>
    <body>
        <h1>M-Flow — мои задачи</h1>
        <ul>{task_items}</ul>
        <form method="POST" action="/add">
            <input type="text" name="title" placeholder="Новая задача" required>
            <input type="date" name="deadline">
            <button type="submit">Добавить</button>
        </form>
    </body>
    </html>
    """
    return html

@app.route("/add", methods=["POST"])
def add():
    title = request.form.get("title")
    deadline = request.form.get("deadline")
    if title:
        add_task(title, deadline)
    return redirect("/")

@app.route("/toggle/<int:task_id>")
def toggle(task_id):
    toggle_task(task_id)
    return redirect("/")

@app.route("/delete/<int:task_id>")
def delete(task_id):
    delete_task(task_id)
    return redirect("/")

if __name__ == "__main__":
    app.run(debug=True)