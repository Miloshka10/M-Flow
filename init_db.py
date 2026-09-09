import sqlite3
from werkzeug.security import generate_password_hash

conn = sqlite3.connect("database.db")
cursor = conn.cursor()

# Пользователи
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password TEXT NOT NULL,
    role TEXT NOT NULL
)
""")

# Проекты
cursor.execute("""
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    owner_id INTEGER,
    FOREIGN KEY (owner_id) REFERENCES users (id)
)
""")

# Если база была создана старой версией M-Flow,
# добавляем owner_id.
cursor.execute("PRAGMA table_info(projects)")
project_columns = [row[1] for row in cursor.fetchall()]

if "owner_id" not in project_columns:
    cursor.execute("ALTER TABLE projects ADD COLUMN owner_id INTEGER")

# Задачи
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

# Участники проектов
cursor.execute("""
CREATE TABLE IF NOT EXISTS project_members (
    project_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    PRIMARY KEY (project_id, user_id),
    FOREIGN KEY (project_id) REFERENCES projects (id),
    FOREIGN KEY (user_id) REFERENCES users (id)
)
""")

# Создаём тестового ученика
cursor.execute("SELECT id FROM users WHERE username = ?", ("milosh",))
student = cursor.fetchone()

if student is None:
    cursor.execute(
        "INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
        ("milosh", generate_password_hash("1234"), "student"),
    )
    student_id = cursor.lastrowid
else:
    student_id = student[0]

# Создаём тестового учителя
cursor.execute("SELECT id FROM users WHERE username = ?", ("uchitel",))
teacher = cursor.fetchone()

if teacher is None:
    cursor.execute(
        "INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
        ("uchitel", generate_password_hash("1234"), "teacher"),
    )

# Старые проекты получают владельца
cursor.execute(
    "UPDATE projects SET owner_id = ? WHERE owner_id IS NULL",
    (student_id,),
)

# Добавляем владельцев старых проектов в список участников
cursor.execute("""
INSERT OR IGNORE INTO project_members (project_id, user_id)
SELECT id, owner_id
FROM projects
WHERE owner_id IS NOT NULL
""")

# Если проектов ещё нет — создаём стартовый
cursor.execute("SELECT id FROM projects LIMIT 1")
project = cursor.fetchone()

if project is None:
    cursor.execute(
        "INSERT INTO projects (name, owner_id) VALUES (?, ?)",
        ("Мой первый проект", student_id),
    )

    project_id = cursor.lastrowid

    cursor.execute(
        "INSERT OR IGNORE INTO project_members (project_id, user_id) VALUES (?, ?)",
        (project_id, student_id),
    )

    tasks = [
        (project_id, "Придумать название проекта", 1, "2026-09-10"),
        (project_id, "Установить Flask", 1, "2026-09-12"),
        (project_id, "Сделать список задач", 0, "2026-09-20"),
    ]

    cursor.executemany(
        "INSERT INTO tasks (project_id, title, done, deadline) VALUES (?, ?, ?, ?)",
        tasks,
    )

conn.commit()
conn.close()

print("База данных создана и обновлена!")