"""Unicode-aware account names without renaming or merging legacy accounts.

Registration calls name_taken under the existing shared auth write lock. A
short projected name scan avoids a destructive uniqueness migration when old
accounts collide. Login preserves exact legacy names and rejects ambiguous aliases.
"""
import unicodedata


def name_key(value):
    return unicodedata.normalize('NFKC', value).strip().casefold()


def name_taken(conn, username):
    key = name_key(username)
    return any(name_key(row['username']) == key for row in conn.execute('SELECT username FROM users'))


def login_user(conn, username):
    exact = conn.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
    if exact is not None:
        return exact
    key = name_key(username)
    matches = [row['id'] for row in conn.execute('SELECT id,username FROM users') if name_key(row['username']) == key]
    return conn.execute('SELECT * FROM users WHERE id=?', (matches[0],)).fetchone() if len(matches) == 1 else None


def name_audit(conn):
    counts = {}
    for row in conn.execute('SELECT username FROM users'):
        key = name_key(row['username'])
        counts[key] = counts.get(key, 0) + 1
    collisions = [n for n in counts.values() if n > 1]
    return dict(accounts=sum(counts.values()), collision_groups=len(collisions),
                accounts_in_collisions=sum(collisions))
