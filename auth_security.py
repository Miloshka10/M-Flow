"""Shared, bounded authentication counters and revocable sessions (no form logging)."""
from contextlib import closing, nullcontext
import hashlib
import hmac
import ipaddress
import json
import secrets
import time
from functools import wraps

from database import PostgresConnection, execute_schema, DatabaseUnavailable, DATABASE_ERRORS


def private_database_error(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except DATABASE_ERRORS:
            raise DatabaseUnavailable('Защита входа временно недоступна. Повторите попытку позже.') from None
    return wrapped

LOGIN_PAIR_LIMIT = 8
LOGIN_WINDOW = 60
MAX_COUNTERS = 20000
MAX_SESSIONS = 20000
SESSION_SECONDS = 12 * 60 * 60


def initialize_auth(conn):
    execute_schema(conn, """CREATE TABLE IF NOT EXISTS auth_limits (
        bucket TEXT PRIMARY KEY, hits INTEGER NOT NULL, expires INTEGER NOT NULL)""")
    execute_schema(conn, """CREATE TABLE IF NOT EXISTS auth_sessions (
        token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
        expires INTEGER NOT NULL, FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE)""")
    for table in ('auth_limits', 'auth_sessions'):
        conn.execute(f'CREATE INDEX IF NOT EXISTS idx_{table}_expires ON {table}(expires)')


def fingerprint(secret, *parts):
    data = json.dumps(parts, ensure_ascii=True, separators=(',', ':')).encode()
    return hmac.new(secret.encode(), data, hashlib.sha256).hexdigest()


def proxy_networks(value):
    try:
        networks = tuple(ipaddress.ip_network(v.strip()) for v in value.split(',') if v.strip())
        if any(n.prefixlen == 0 for n in networks):
            raise ValueError()
        return networks
    except ValueError:
        raise RuntimeError('Некорректные доверенные сети прокси M_FLOW_TRUSTED_PROXY_CIDRS.') from None


def _address(value):
    address = ipaddress.ip_address(value)
    return address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped else address


def client_source(peer, forwarded, trusted=()):
    """Walk from socket peer right-to-left, only through explicitly trusted proxies."""
    try:
        current = _address(peer)
    except (ValueError, TypeError):
        return 'unknown-peer'
    if not trusted or not any(current in net for net in trusted):
        return str(current)
    if not forwarded or len(forwarded) > 1024:
        return str(current)
    try:
        addresses = [_address(v.strip()) for v in forwarded.split(',')]
        if len(addresses) > 16:
            return str(current)
    except ValueError:
        return str(current)
    for address in reversed(addresses):
        if not any(current in net for net in trusted):
            break
        current = address
    return str(current)


def begin_auth_write(conn):
    if isinstance(conn, PostgresConnection):
        conn.execute('SELECT pg_advisory_xact_lock(?)', (674930211,))
    else:
        conn.execute('BEGIN IMMEDIATE')


def _cleanup(conn, table, now):
    key = 'bucket' if table == 'auth_limits' else 'token_hash'
    conn.execute(f'''DELETE FROM {table} WHERE {key} IN
        (SELECT {key} FROM {table} WHERE expires<=? ORDER BY expires LIMIT 200)''', (now,))


@private_database_error
def reserve_attempt(get_db, secret, action, username, source, browser):
    """Reserve before hashing. Blocked requests never extend a fixed window.

    Account/source login limits are soft: valid credentials remain usable from
    another source. Shared-IP bursts tighten individual limits, not the whole IP.
    Returns (retry seconds, account/source soft pressure).
    """
    now = int(time.time())
    if action not in ('login', 'register'):
        raise ValueError('Неизвестное действие авторизации.')
    keys = {kind: fingerprint(secret, action, kind, *parts) for kind, parts in (
        ('account', (username[:256],)), ('pair', (username[:256], source)),
        ('source', (source,)), ('browser', (source, browser)))}
    with closing(get_db()) as conn:
        begin_auth_write(conn)
        _cleanup(conn, 'auth_limits', now)
        rows = {}
        present = set()
        for kind, key in keys.items():
            row = conn.execute('SELECT hits,expires FROM auth_limits WHERE bucket=?', (key,)).fetchone()
            if row is not None:
                present.add(kind)
            rows[kind] = dict(hits=row['hits'], expires=row['expires']) if row and row['expires'] > now else dict(hits=0, expires=now)
        pressure = rows['source']['hits'] >= (300 if action == 'login' else 120)
        if action == 'login':
            limits = {'pair': (3 if pressure else LOGIN_PAIR_LIMIT, LOGIN_WINDOW),
                      'account': (30, 300), 'source': (300, 300)}
            hard = ('pair',)
        else:
            limits = {'pair': (3, 600), 'account': (6, 600),
                      'source': (120, 600), 'browser': (3 if pressure else 10, 600)}
            hard = ('pair', 'account', 'browser')
        retry = max((rows[k]['expires'] - now for k in hard if rows[k]['hits'] >= limits[k][0]), default=0)
        if retry:
            conn.commit()
            return retry, 0
        missing = sum(k not in present for k in limits)
        count = conn.execute('SELECT COUNT(*) FROM auth_limits').fetchone()[0]
        if count + missing > MAX_COUNTERS:
            raise DatabaseUnavailable('Защита входа временно занята. Повторите попытку позже.')
        soft_retry = max((rows[k]['expires'] - now for k in ('account', 'source')
                          if rows[k]['hits'] >= limits[k][0]), default=0) if action == 'login' else 0
        for kind, (limit, window) in limits.items():
            row = rows[kind]
            # Saturate soft counters, but keep their original expiry.
            conn.execute('''INSERT INTO auth_limits(bucket,hits,expires) VALUES(?,?,?)
                ON CONFLICT(bucket) DO UPDATE SET hits=excluded.hits,expires=excluded.expires''',
                (keys[kind], min(row['hits'] + 1, limit), row['expires'] if row['hits'] else now + window))
        conn.commit()
        return 0, soft_retry


@private_database_error
def new_session(get_db, secret, user_id, previous=None, connection=None):
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    with (closing(get_db()) if connection is None else nullcontext(connection)) as conn:
        if connection is None:
            begin_auth_write(conn)
        _cleanup(conn, 'auth_sessions', now)
        if previous:
            conn.execute('DELETE FROM auth_sessions WHERE token_hash=?', (fingerprint(secret, 'session', previous),))
        if conn.execute('SELECT COUNT(*) FROM auth_sessions').fetchone()[0] >= MAX_SESSIONS:
            raise DatabaseUnavailable('Сессии временно недоступны. Повторите попытку позже.')
        conn.execute('INSERT INTO auth_sessions(token_hash,user_id,expires) VALUES(?,?,?)',
                     (fingerprint(secret, 'session', token), user_id, now + SESSION_SECONDS))
        if connection is None:
            conn.commit()
    return token


@private_database_error
def revoke_session(get_db, secret, token):
    if isinstance(token, str):
        with closing(get_db()) as conn:
            conn.execute('DELETE FROM auth_sessions WHERE token_hash=?', (fingerprint(secret, 'session', token),))
            conn.commit()
