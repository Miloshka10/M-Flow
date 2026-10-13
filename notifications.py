"""Transactional project notifications; no polling, external delivery or backfill."""
from html import escape
from database import execute_schema


def initialize_notifications(conn):
    execute_schema(conn, """CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        recipient_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        event_key TEXT NOT NULL,
        title TEXT NOT NULL,
        message TEXT NOT NULL DEFAULT '',
        target TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (CAST(CURRENT_TIMESTAMP AS TEXT)),
        read_at TEXT,
        UNIQUE(recipient_id, event_key)
    )""")
    conn.execute('CREATE INDEX IF NOT EXISTS idx_notifications_recipient ON notifications(recipient_id, id DESC)')


def project_recipients(conn, project_id, role):
    return [row['id'] for row in conn.execute("""SELECT u.id FROM users u
        WHERE u.role=? AND (u.id=(SELECT owner_id FROM projects WHERE id=?)
            OR EXISTS (SELECT 1 FROM project_members m WHERE m.project_id=? AND m.user_id=u.id))""",
        (role, project_id, project_id))]


def notify(conn, project_id, event_key, title, message, target, recipients):
    """Must run before the caller's commit, never commit independently."""
    if not target.startswith(f'/project/{project_id}') or len(message) > 2200:
        raise ValueError('Некорректное уведомление.')
    conn.executemany("""INSERT INTO notifications
        (recipient_id,project_id,event_key,title,message,target) VALUES (?,?,?,?,?,?)
        ON CONFLICT(recipient_id,event_key) DO NOTHING""",
        [(uid, project_id, event_key, title, message, target) for uid in set(recipients)])


def notify_project(conn, project_id, role, event_key, title, message, target):
    notify(conn, project_id, event_key, title, message, target,
           project_recipients(conn, project_id, role))


# Re-check membership on every read/mutation: removed members must not see old data.
VISIBLE = """n.recipient_id=? AND EXISTS (SELECT 1 FROM projects p WHERE p.id=n.project_id
    AND (p.owner_id=n.recipient_id OR EXISTS (SELECT 1 FROM project_members m
        WHERE m.project_id=p.id AND m.user_id=n.recipient_id)))"""


def notification_feed(conn, user_id, before=None, limit=12):
    count = conn.execute(f'SELECT COUNT(*) FROM notifications n WHERE {VISIBLE} AND n.read_at IS NULL',
                         (user_id,)).fetchone()[0]
    extra = ' AND n.id<?' if before is not None else ''
    rows = conn.execute(f'''SELECT n.*, p.name AS project_name FROM notifications n
        JOIN projects p ON p.id=n.project_id WHERE {VISIBLE}{extra}
        ORDER BY n.id DESC LIMIT ?''',
        (user_id, before, limit) if before is not None else (user_id, limit)).fetchall()
    return count, rows


def mark_read(conn, user_id, notification_id=None):
    extra = ' AND n.id=?' if notification_id is not None else ''
    params = (user_id, notification_id) if notification_id is not None else (user_id,)
    # Portable subquery rather than UPDATE alias syntax differences.
    ids = f'SELECT n.id FROM notifications n WHERE {VISIBLE}{extra}'
    if notification_id is not None and not conn.execute(ids, params).fetchone():
        return False
    conn.execute(f'''UPDATE notifications SET read_at=CAST(CURRENT_TIMESTAMP AS TEXT)
        WHERE read_at IS NULL AND id IN ({ids})''', params)
    conn.commit()
    return True


def render_items(rows, token):
    if not rows:
        return '<p class="notification-empty">Пока нет уведомлений. Здесь появятся новые сдачи и результаты проверок.</p>'
    items = []
    for row in rows:
        unread = row['read_at'] is None
        button = (f'<form method="post" action="/notifications/{row["id"]}/read">{token}'
                  '<button class="secondary" type="submit">Отметить прочитанным</button></form>') if unread else '<small>Прочитано</small>'
        items.append(f'''<li class="notification-item {'is-unread' if unread else ''}">
            <a href="{escape(row['target'], quote=True)}"><strong>{escape(row['title'])}</strong>
            <span>{escape(row['project_name'])}</span><span>{escape(row['message'])}</span></a>
            <div class="notification-meta"><time>{escape(row['created_at'])} UTC</time>{button}</div></li>''')
    return '<ul class="notification-list">' + ''.join(items) + '</ul>'


def render_bell(count, rows, token):
    badge = f'<span class="notification-count" aria-hidden="true">{count if count < 100 else "99+"}</span>' if count else ''
    return f'''<details class="notification-bell"><summary aria-label="Уведомления: непрочитанных {count}">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/></svg>{badge}</summary>
        <section class="notification-panel" aria-label="Последние уведомления"><h2>Уведомления</h2>
        {render_items(rows, token)}<a class="action-link" href="/notifications">Все уведомления</a>
        <form method="post" action="/notifications/read-all">{token}<button class="secondary" type="submit">Прочитать все</button></form>
        </section></details>'''
