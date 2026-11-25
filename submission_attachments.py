"""Private originals tied to immutable stage submissions."""
from hashlib import sha256
from html import escape
import unicodedata

from database import PostgresConnection, execute_schema

FILE_COUNT = 4
FILE_SIZE = 2 * 1024 * 1024
TOTAL_SIZE = 6 * 1024 * 1024
REQUEST_SIZE = TOTAL_SIZE + 1024 * 1024
LIMIT_TEXT = 'До 4 файлов, по 2 МБ на файл и 6 МБ суммарно.'


def initialize_attachments(conn):
    binary = 'BYTEA' if isinstance(conn, PostgresConnection) else 'BLOB'
    execute_schema(conn, f"""CREATE TABLE IF NOT EXISTS submission_attachments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        history_id INTEGER NOT NULL REFERENCES work_history(id),
        filename TEXT NOT NULL,
        size INTEGER NOT NULL CHECK(size > 0 AND size <= {FILE_SIZE}),
        sha256 TEXT NOT NULL CHECK(length(sha256)=64),
        content {binary} NOT NULL CHECK(length(content)=size),
        UNIQUE(history_id,filename)
    )""")


def read_uploads(files):
    uploads = [upload for upload in files if upload.filename]
    if len(uploads) > FILE_COUNT:
        raise ValueError(LIMIT_TEXT)
    names, results, total = set(), [], 0
    for upload in uploads:
        name = unicodedata.normalize('NFC', upload.filename.strip())
        if (not name or name in ('.', '..') or len(name) > 150
                or any(char in '/\\:' or unicodedata.category(char).startswith('C') for char in name)):
            raise ValueError('Переименуйте файл: нужно обычное имя без пути, до 150 символов.')
        if name.casefold() in names:
            raise ValueError('Имена вложений в одной сдаче должны различаться.')
        names.add(name.casefold())
        content = upload.read(min(FILE_SIZE, TOTAL_SIZE - total) + 1)
        total += len(content)
        if len(content) > FILE_SIZE or total > TOTAL_SIZE:
            raise ValueError(LIMIT_TEXT)
        if not content:
            raise ValueError('В списке вложений есть пустой файл.')
        results.append((name, len(content), sha256(content).hexdigest(), content))
    return results


def save_attachments(conn, history_id, uploads):
    conn.executemany("""INSERT INTO submission_attachments(history_id,filename,size,sha256,content)
        VALUES(?,?,?,?,?)""", [(history_id, *file) for file in uploads])


def attachment_metadata(conn, history_ids):
    if not history_ids:
        return {}
    marks = ','.join('?' for _ in history_ids)
    groups = {}
    for row in conn.execute(f"""SELECT id,history_id,filename,size,sha256 FROM submission_attachments
        WHERE history_id IN ({marks}) ORDER BY id""", tuple(history_ids)):
        groups.setdefault(row['history_id'], []).append(row)
    return groups


def readable_attachment(conn, project_id, attachment_id, user_id):
    return conn.execute("""SELECT a.filename,a.content
        FROM submission_attachments a JOIN work_history h ON h.id=a.history_id
        JOIN projects p ON p.id=h.project_id
        WHERE p.id=? AND a.id=? AND h.kind='stage' AND h.event='submit'
        AND (p.owner_id=? OR EXISTS (
            SELECT 1 FROM project_members pm WHERE pm.project_id=p.id AND pm.user_id=?))""",
        (project_id, attachment_id, user_id, user_id)).fetchone()


def render_attachments(project_id, files, *, other_files=None, previous=False):
    if not files:
        return '<p class="t-muted">В этой версии файлов нет.</p>' if other_files else ''
    other = {row['filename']: row['sha256'] for row in (other_files or ())}
    items = []
    for row in files:
        path = f'/project/{project_id}/attachments/{row["id"]}'
        name = row['filename']
        size = f'{(row["size"] + 1023) // 1024} КБ' if row['size'] >= 1024 else f'{row["size"]} Б'
        state = ''
        if other_files is not None:
            if name not in other:
                state = ' · Удалён' if previous else ' · Добавлен'
            elif row['sha256'] != other[name]:
                state = ' · Изменён'
            else:
                state = ' · Без изменений'
        items.append(f'<li><a class="action-link" href="{path}">{escape(name)}</a> <small>{size}{state}</small></li>')
    return '<p><strong>Файлы сдачи</strong></p><ul class="submission-attachments">' + ''.join(items) + '</ul>'
