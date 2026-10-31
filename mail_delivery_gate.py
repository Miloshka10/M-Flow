"""Coordinate EmailJS's send rate across workers using existing auth storage.

Only a keyed account fingerprint and a short lease are stored. No idle worker,
new schema, recipient data or network operation inside a DB transaction.
"""
from contextlib import closing, contextmanager
import secrets
import time

from auth_security import begin_auth_write, fingerprint, private_database_error, _cleanup, MAX_COUNTERS
from database import DatabaseUnavailable

LEASE_SECONDS = 30  # HTTP send timeout is 15 seconds.
COOLDOWN_SECONDS = 2  # Integer timestamps still leave >=1 second after completion.
MAX_WAIT_SECONDS = 45


@private_database_error
def claim_delivery(get_db, key):
    now = int(time.time())
    with closing(get_db()) as conn:
        begin_auth_write(conn)
        _cleanup(conn, 'auth_limits', now)
        row = conn.execute('SELECT hits,expires FROM auth_limits WHERE bucket=?', (key,)).fetchone()
        if row and row['expires'] > now:
            conn.rollback()
            return None, min(1.0, row['expires'] - time.time())
        if not row and conn.execute('SELECT COUNT(*) FROM auth_limits').fetchone()[0] >= MAX_COUNTERS:
            raise DatabaseUnavailable('Отправка писем временно занята.')
        lease = secrets.randbelow(2147483647) + 1
        conn.execute('''INSERT INTO auth_limits(bucket,hits,expires) VALUES(?,?,?)
            ON CONFLICT(bucket) DO UPDATE SET hits=excluded.hits,expires=excluded.expires''',
                     (key, lease, now + LEASE_SECONDS))
        conn.commit()
    return lease, 0


@private_database_error
def finish_delivery(get_db, key, lease):
    with closing(get_db()) as conn:
        begin_auth_write(conn)
        # A late/failed worker must not release another worker's renewed lease.
        conn.execute('UPDATE auth_limits SET hits=0,expires=? WHERE bucket=? AND hits=?',
                     (int(time.time()) + COOLDOWN_SECONDS, key, lease))
        conn.commit()


@contextmanager
def emailjs_gate(get_db, secret, public_key):
    key = fingerprint(secret, 'mail-delivery', 'emailjs', public_key)
    deadline = time.monotonic() + MAX_WAIT_SECONDS
    while True:
        lease, delay = claim_delivery(get_db, key)
        if lease is not None:
            break
        delay = max(.05, delay)
        if time.monotonic() + delay > deadline:
            raise DatabaseUnavailable('Отправка писем временно занята. Повторите запрос позже.')
        time.sleep(delay)  # Only an already queued mail job waits; HTTP does not.
    try:
        yield
    finally:
        finish_delivery(get_db, key, lease)
