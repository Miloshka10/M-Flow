import sqlite3

conn = sqlite3.connect("database.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    deadline TEXT
)
""")

cursor.execute("INSERT INTO tasks (title, done, deadline) VALUES (?, ?, ?)", ("Придумать название проекта", 1, "2026-09-10"))
cursor.execute("INSERT INTO tasks (title, done, deadline) VALUES (?, ?, ?)", ("Установить Flask", 1, "2026-09-12"))
cursor.execute("INSERT INTO tasks (title, done, deadline) VALUES (?, ?, ?)", ("Сделать список задач", 0, "2026-09-20"))

conn.commit()
conn.close()

print("База данных создана и заполнена!")