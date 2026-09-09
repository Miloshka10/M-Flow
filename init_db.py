import sqlite3

conn = sqlite3.connect("database.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    deadline TEXT,
    FOREIGN KEY (project_id) REFERENCES projects (id)
)
""")

# Создаём один проект для начала, с прежними тестовыми задачами
cursor.execute("INSERT INTO projects (name) VALUES (?)", ("Мой первый проект",))
project_id = cursor.lastrowid

cursor.execute("INSERT INTO tasks (project_id, title, done, deadline) VALUES (?, ?, ?, ?)", (project_id, "Придумать название проекта", 1, "2026-09-10"))
cursor.execute("INSERT INTO tasks (project_id, title, done, deadline) VALUES (?, ?, ?, ?)", (project_id, "Установить Flask", 1, "2026-09-12"))
cursor.execute("INSERT INTO tasks (project_id, title, done, deadline) VALUES (?, ?, ?, ?)", (project_id, "Сделать список задач", 0, "2026-09-20"))

conn.commit()
conn.close()

print("База данных создана и заполнена!")