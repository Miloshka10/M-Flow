"""Confirmed recovery email, single-use capabilities, and bounded async mail.

No network in request/DB locks for password recovery; unknown addresses use the
same queued path. Tokens travel in URL fragments, are stored only as HMACs, and
are bound to the password version and email revision. No automatic login.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from email.message import EmailMessage
import json
import os
import re
import secrets
import smtplib
import ssl
import threading
import time
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request, HTTPSHandler, HTTPRedirectHandler, build_opener

from werkzeug.security import check_password_hash, generate_password_hash
from account_names import login_user
from auth_security import begin_auth_write, fingerprint, _cleanup, private_database_error, MAX_COUNTERS
from database import execute_schema, DatabaseUnavailable

TOKEN_SECONDS = 20 * 60
VERIFY_SECONDS = 24 * 60 * 60
TOKEN_LIMIT = 20000
GENERIC_REQUEST = 'Если логин и подтверждённый адрес совпадают, на почту придёт ссылка для восстановления. Проверьте также папку «Спам».'
INVALID_TOKEN = 'Ссылка недействительна или её срок истёк. Запросите новое письмо.'
TOKEN = re.compile(r'[A-Za-z0-9_-]{43}\Z')


def public_origin(value):
    try:
        # urlsplit drops empty query/fragment delimiters and leading controls.
        # Validate the original text before appending a recovery URL to it.
        if any(c in '\\?#' or c.isspace() or ord(c) < 32 or 127 <= ord(c) <= 159 for c in value):
            return ''
        origin = value.rstrip('/')
        parsed = urlsplit(origin)
        if (parsed.scheme != 'https' or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.path or parsed.query or parsed.fragment or parsed.netloc.endswith(':')
                or (parsed.port is not None and not 1 <= parsed.port <= 65535)):
            return ''
        return origin
    except ValueError:
        return ''


def normalize_email(value):
    value = value.strip()
    if len(value) > 254 or value.count('@') != 1 or any(c.isspace() for c in value):
        raise ValueError('Укажите полный адрес электронной почты.')
    local, domain = value.rsplit('@', 1)
    try:
        domain = domain.encode('idna').decode('ascii').lower()
    except UnicodeError:
        raise ValueError('Проверьте адрес электронной почты.') from None
    if (not 1 <= len(local) <= 64 or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+", local)
            or local.startswith('.') or local.endswith('.') or '..' in local
            or '.' not in domain or len(domain) > 253
            or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', p) for p in domain.split('.'))):
        raise ValueError('Укажите обычный адрес почты, например name@example.org.')
    result = local.lower() + '@' + domain
    if len(result) > 254:
        raise ValueError('Адрес электронной почты слишком длинный.')
    return result


@dataclass(frozen=True, repr=False)
class MailSettings:
    host: str
    port: int
    user: str
    password: str
    sender: str
    origin: str
    mode: str
    provider: str = 'smtp'
    api_key: str = ''

    @classmethod
    def from_environment(cls):
        try:
            origin = public_origin(os.environ.get('M_FLOW_PUBLIC_URL', ''))
            sender = normalize_email(os.environ.get('M_FLOW_MAIL_FROM', ''))
            provider = os.environ.get('M_FLOW_MAIL_PROVIDER', 'smtp').strip().lower()
            if not origin:
                return None
            if provider == 'brevo':
                api_key = os.environ.get('M_FLOW_MAIL_API_KEY', '')
                if (not 1 <= len(api_key) <= 512 or not api_key.isascii()
                        or not api_key.isprintable() or any(c.isspace() for c in api_key)):
                    return None
                return cls('', 0, '', '', sender, origin, '', provider, api_key)
            if provider != 'smtp':
                return None
            host = os.environ.get('M_FLOW_MAIL_HOST', '').strip()
            mode = os.environ.get('M_FLOW_MAIL_TLS', 'starttls')
            port = int(os.environ.get('M_FLOW_MAIL_PORT', '587' if mode == 'starttls' else '465'))
            if (not origin or not host
                    or any(c.isspace() for c in host) or mode not in ('starttls', 'ssl') or not 1 <= port <= 65535):
                return None
            user, password = os.environ.get('M_FLOW_MAIL_USER', ''), os.environ.get('M_FLOW_MAIL_PASSWORD', '')
            if not user or not password or '\r' in user or '\n' in user:
                return None
            return cls(host, port, user, password, sender, origin, mode)
        except (ValueError, UnicodeError):
            return None


class _NoMailRedirect(HTTPRedirectHandler):
    """Do not forward a provider credential or recovery token to another URL."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send_brevo(settings, recipient, subject, body):
    """HTTPS on the provider's fixed endpoint, without retries or response logging.

    A 201 response means accepted by the provider, not delivery to an inbox.
    No contact list, tracking script, webhook or marketing subscription is created.
    """
    payload = dict(sender=dict(email=settings.sender, name='M-Flow'),
                   to=[dict(email=normalize_email(recipient))], subject=subject, textContent=body)
    request = Request('https://api.brevo.com/v3/smtp/email',
                      data=json.dumps(payload, ensure_ascii=False).encode('utf-8'), method='POST',
                      headers={'api-key': settings.api_key, 'Content-Type': 'application/json',
                               'Accept': 'application/json'})
    opener = build_opener(HTTPSHandler(context=ssl.create_default_context()), _NoMailRedirect())
    try:
        with opener.open(request, timeout=15) as response:
            if response.status != 201:
                raise ValueError('Unexpected mail response')
            data = response.read(16385)
            if len(data) > 16384:
                raise ValueError('Oversized mail response')
            result = json.loads(data)
            if not isinstance(result, dict) or not isinstance(result.get('messageId'), str) or not result['messageId'].strip():
                raise ValueError('Missing mail receipt')
    except (HTTPError, URLError, OSError, ValueError, UnicodeError) as exc:
        if isinstance(exc, HTTPError):
            exc.close()
        raise DatabaseUnavailable('Почтовый сервис не подтвердил приём письма. Повторите запрос позже.') from None


def send_mail(settings, recipient, subject, body):
    if settings.provider == 'brevo':
        return send_brevo(settings, recipient, subject, body)
    if settings.provider == 'smtp':
        return send_smtp(settings, recipient, subject, body)
    raise DatabaseUnavailable('Способ отправки почты не поддерживается.')


def send_smtp(settings, recipient, subject, body):
    message = EmailMessage()
    message['From'], message['To'], message['Subject'] = settings.sender, recipient, subject
    message.set_content(body)
    context = ssl.create_default_context()
    if settings.mode == 'ssl':
        connection = smtplib.SMTP_SSL(settings.host, settings.port, timeout=15, context=context)
    else:
        connection = smtplib.SMTP(settings.host, settings.port, timeout=15)
    with connection as smtp:
        if settings.mode == 'starttls':
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
        smtp.login(settings.user, settings.password)
        smtp.send_message(message)


class MailDispatcher:
    """Bounded, event-driven queue; no polling, extra service or hidden retries.

In-flight mail can be lost on process restart: the user may request it again.
Never log exceptions that can contain SMTP credentials or recipient addresses.
"""
    def __init__(self, on_error, sender=send_mail):
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='mflow-mail')
        self._slots = threading.BoundedSemaphore(16)
        self.sender, self.on_error = sender, on_error

    def submit(self, job):
        if not self._slots.acquire(blocking=False):
            raise DatabaseUnavailable('Отправка писем временно занята. Попробуйте позже.')
        def run():
            try:
                job(self.sender)
            except Exception:
                self.on_error()
            finally:
                self._slots.release()
        try:
            self._pool.submit(run)
        except Exception:
            self._slots.release()
            raise DatabaseUnavailable('Отправка писем временно недоступна.') from None


def initialize_mail(conn):
    execute_schema(conn, '''CREATE TABLE IF NOT EXISTS account_emails (
        user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        email TEXT NOT NULL DEFAULT '', pending_email TEXT NOT NULL DEFAULT '',
        revision INTEGER NOT NULL DEFAULT 0,
        old_approved INTEGER NOT NULL DEFAULT 0, new_approved INTEGER NOT NULL DEFAULT 0
    )''')
    execute_schema(conn, '''CREATE TABLE IF NOT EXISTS account_email_tokens (
        token_hash TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        purpose TEXT NOT NULL CHECK(purpose IN ('verify_new','approve_old','reset')),
        email TEXT NOT NULL, version TEXT NOT NULL, expires INTEGER NOT NULL
    )''')
    execute_schema(conn, 'CREATE INDEX IF NOT EXISTS idx_account_email_tokens_expiry ON account_email_tokens(expires)')
    execute_schema(conn, 'CREATE INDEX IF NOT EXISTS idx_account_email_tokens_user ON account_email_tokens(user_id)')


def email_for(conn, user_id):
    row = conn.execute('SELECT * FROM account_emails WHERE user_id=?', (user_id,)).fetchone()
    return dict(row) if row else dict(user_id=user_id, email='', pending_email='', revision=0, old_approved=0, new_approved=0)


def _version(secret, user, email):
    return fingerprint(secret, 'email-version', user['id'], user['password'], email['email'], email['pending_email'], email['revision'])


@private_database_error
def reserve_mail(get_db, secret, identity, source, browser, purpose):
    now = int(time.time())
    buckets = [(fingerprint(secret, 'mail', purpose, 'identity', identity), 3),
               (fingerprint(secret, 'mail', purpose, 'browser', source, browser), 10)]
    with closing(get_db()) as conn:
        begin_auth_write(conn)
        _cleanup(conn, 'auth_limits', now)
        rows = []
        for key, limit in buckets:
            row = conn.execute('SELECT hits,expires FROM auth_limits WHERE bucket=?', (key,)).fetchone()
            rows.append((key, limit, row if row and row['expires'] > now else None))
        retry = max((r['expires'] - now for _, limit, r in rows if r and r['hits'] >= limit), default=0)
        if retry:
            conn.rollback()
            return retry
        if conn.execute('SELECT COUNT(*) FROM auth_limits').fetchone()[0] + 2 > MAX_COUNTERS:
            raise DatabaseUnavailable('Защита восстановления временно занята.')
        for key, _, row in rows:
            conn.execute('''INSERT INTO auth_limits(bucket,hits,expires) VALUES(?,?,?)
                ON CONFLICT(bucket) DO UPDATE SET hits=excluded.hits,expires=excluded.expires''',
                         (key, row['hits'] + 1 if row else 1, row['expires'] if row else now + 900))
        conn.commit()
    return 0


def _issue(conn, secret, user, email, purpose, recipient):
    now = int(time.time())
    conn.execute('''DELETE FROM account_email_tokens WHERE token_hash IN
        (SELECT token_hash FROM account_email_tokens WHERE expires<=? ORDER BY expires LIMIT 200)''', (now,))
    if conn.execute('SELECT COUNT(*) FROM account_email_tokens').fetchone()[0] >= TOKEN_LIMIT:
        raise DatabaseUnavailable('Восстановление временно недоступно.')
    raw = secrets.token_urlsafe(32)
    conn.execute('INSERT INTO account_email_tokens(token_hash,user_id,purpose,email,version,expires) VALUES(?,?,?,?,?,?)',
                 (fingerprint(secret, 'email-token', raw), user['id'], purpose, recipient, _version(secret, user, email),
                  now + (TOKEN_SECONDS if purpose == 'reset' else VERIFY_SECONDS)))
    return raw


def _display_name(username):
    return ''.join(c if c.isprintable() else ' ' for c in username)[:128]


def _letter(settings, raw, purpose, username):
    reset = purpose == 'reset'
    url = settings.origin + ('/reset-password' if reset else '/email/confirm') + '#token=' + raw
    subject = 'Восстановление доступа к M-Flow' if reset else 'Подтвердите почту M-Flow' if purpose == 'verify_new' else 'Смена почты M-Flow: подтвердите со старого адреса'
    note = ('Ссылка действует 20 минут. После смены пароля старые сеансы будут завершены.' if reset else
            'Ссылка действует 24 часа. При смене адреса нужны подтверждения со старого и нового ящиков.')
    return subject, f'{subject}\nАккаунт: {_display_name(username)}\n\n{url}\n\n{note}\nЕсли ссылка не открывается, скопируйте код из письма в форму:\n{raw}\n\nЕсли вы не запрашивали это действие, проигнорируйте письмо. Пароль в письме никогда не запрашивается.'


def schedule_reset(get_db, secret, settings, dispatcher, username, address):
    """Always queue a job, including an unknown username/email combination."""
    def job(send):
        raw = None
        with closing(get_db()) as conn:
            try:
                begin_auth_write(conn)
                user = login_user(conn, username)
                if user is not None:
                    email = email_for(conn, user['id'])
                    if email['email'] and email['email'] == address:
                        raw = _issue(conn, secret, user, email, 'reset', address)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        if raw:
            subject, body = _letter(settings, raw, 'reset', user['username'])
            send(settings, address, subject, body)
    dispatcher.submit(job)


@private_database_error
def request_email(get_db, secret, settings, dispatcher, user_id, address, password):
    with closing(get_db()) as conn:
        user = conn.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        if user is None or len(password) > 256 or not check_password_hash(user['password'], password):
            raise ValueError('Текущий пароль введён неверно.')
        previous_password = user['password']
        begin_auth_write(conn)
        try:
            user = conn.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
            if user is None or not secrets.compare_digest(user['password'], previous_password):
                raise ValueError('Пароль уже изменён. Войдите в аккаунт заново.')
            current = email_for(conn, user_id)
            if current['email'] == address:
                raise ValueError('Этот адрес уже подтверждён.')
            conn.execute('''INSERT INTO account_emails(user_id,pending_email,revision,old_approved)
                VALUES(?,?,1,1) ON CONFLICT(user_id) DO UPDATE SET pending_email=excluded.pending_email,
                revision=account_emails.revision+1,old_approved=?,new_approved=0''',
                         (user_id, address, int(not current['email'])))
            current = email_for(conn, user_id)
            conn.execute("DELETE FROM account_email_tokens WHERE user_id=? AND purpose!='reset'", (user_id,))
            letters = [(address, *_letter(settings, _issue(conn, secret, user, current, 'verify_new', address), 'verify_new', user['username']))]
            if current['email']:
                letters.append((current['email'], *_letter(settings, _issue(conn, secret, user, current, 'approve_old', current['email']), 'approve_old', user['username'])))
            # Reserve queue space before commit; sending starts only after commit.
            ready = threading.Event()
            committed = []
            def job(send):
                if not ready.wait(20):
                    raise DatabaseUnavailable('Письмо не дождалось завершения операции.')
                if committed:
                    for recipient, subject, body in letters:
                        send(settings, recipient, subject, body)
            dispatcher.submit(job)
            conn.commit()
            committed.append(True)
            ready.set()
        except Exception:
            conn.rollback()
            if 'ready' in locals():
                ready.set()
            raise


def _token(conn, secret, raw, purposes):
    if not TOKEN.fullmatch(raw):
        raise ValueError(INVALID_TOKEN)
    row = conn.execute('SELECT * FROM account_email_tokens WHERE token_hash=?',
                       (fingerprint(secret, 'email-token', raw),)).fetchone()
    if row is None or row['purpose'] not in purposes or row['expires'] <= int(time.time()):
        raise ValueError(INVALID_TOKEN)
    user = conn.execute('SELECT * FROM users WHERE id=?', (row['user_id'],)).fetchone()
    email = email_for(conn, row['user_id'])
    if user is None or not secrets.compare_digest(row['version'], _version(secret, user, email)):
        raise ValueError(INVALID_TOKEN)
    return dict(row), dict(user), email


@private_database_error
def confirm_email(get_db, secret, raw):
    with closing(get_db()) as conn:
        begin_auth_write(conn)
        try:
            row, user, email = _token(conn, secret, raw, ('verify_new', 'approve_old'))
            column = 'new_approved' if row['purpose'] == 'verify_new' else 'old_approved'
            conn.execute(f'UPDATE account_emails SET {column}=1 WHERE user_id=?', (user['id'],))
            conn.execute('DELETE FROM account_email_tokens WHERE token_hash=?', (row['token_hash'],))
            email = email_for(conn, user['id'])
            complete = bool(email['old_approved'] and email['new_approved'])
            if complete:
                conn.execute("UPDATE account_emails SET email=pending_email,pending_email='',revision=revision+1 WHERE user_id=?", (user['id'],))
                conn.execute('DELETE FROM account_email_tokens WHERE user_id=?', (user['id'],))
            conn.commit()
            return complete
        except Exception:
            conn.rollback()
            raise


@private_database_error
def reset_password(get_db, secret, raw, password, repeat):
    if not 8 <= len(password) <= 256:
        raise ValueError('Пароль должен быть от 8 до 256 символов.')
    if password != repeat:
        raise ValueError('Пароли не совпадают.')
    # Validate before expensive hashing, and again under the write lock.
    with closing(get_db()) as conn:
        _token(conn, secret, raw, ('reset',))
    hashed = generate_password_hash(password)
    with closing(get_db()) as conn:
        begin_auth_write(conn)
        try:
            row, user, email = _token(conn, secret, raw, ('reset',))
            conn.execute('UPDATE users SET password=? WHERE id=?', (hashed, user['id']))
            conn.execute('DELETE FROM auth_sessions WHERE user_id=?', (user['id'],))
            conn.execute('DELETE FROM account_email_tokens WHERE user_id=?', (user['id'],))
            conn.execute("UPDATE account_emails SET pending_email='',revision=revision+1,old_approved=0,new_approved=0 WHERE user_id=?", (user['id'],))
            conn.commit()
            return dict(id=user['id'], role=user['role'], email=email['email'], display_name=_display_name(user['username']))
        except Exception:
            conn.rollback()
            raise
