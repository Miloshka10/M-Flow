"""Authentication scenarios inherited by both disposable SQLite and PostgreSQL suites."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import subprocess
import sys
from unittest.mock import patch

import auth_security as security
from database import DatabaseUnavailable


class AuthSecurityCases:
    def counters(self):
        with closing(self.module.get_db()) as conn:
            return [tuple(row) for row in conn.execute('SELECT bucket,hits,expires FROM auth_limits ORDER BY bucket')]

    def attempt(self, name='student-one', source='192.0.2.1', browser='browser', action='login'):
        return security.reserve_attempt(self.module.get_db, self.module.SECRET_KEY, action, name, source, browser)

    def test_auth_limit_both_roles_shared_across_role_routes_and_fixed_expiry(self):
        for path, name in (('/login', 'student-one'), ('/teacher/login', 'teacher-one')):
            with patch('auth_security.time.time', return_value=200000):
                for _ in range(security.LOGIN_PAIR_LIMIT):
                    response = self.post(path, data=dict(username=name, password='wrong-password'))
                    self.assertEqual(response.status_code, 200)
                    self.assertIn('Неверный логин или пароль.', response.text)
                rows = self.counters()
                alternate = '/teacher/login' if path == '/login' else '/login'
                limited = self.post(alternate, data=dict(username=name, password='wrong-password'))
                self.assertEqual(limited.status_code, 429)
                self.assertEqual(limited.headers['Retry-After'], '60')
                self.assertIn('Повторите через 60 сек.', limited.text)
                self.assertEqual(rows, self.counters())
            with patch('auth_security.time.time', return_value=200030):
                limited = self.post(path, data=dict(username=name, password='test-password'))
                self.assertEqual(limited.status_code, 429)
                self.assertEqual(limited.headers['Retry-After'], '30')
            with patch('auth_security.time.time', return_value=200060):
                response = self.post(path, data=dict(username=name, password='test-password'))
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.location, '/teacher' if path.startswith('/teacher') else '/')

    def test_auth_unknown_and_known_have_same_error_and_hash_verification(self):
        with patch.object(self.module, 'check_password_hash', wraps=self.module.check_password_hash) as check:
            for name in ('student-one', 'absent-account'):
                response = self.post('/login', data=dict(username=name, password='wrong-password'))
                self.assertEqual(response.status_code, 200)
                self.assertIn('Неверный логин или пароль.', response.text)
                self.assertNotIn('аккаунт учителя', response.text)
            self.assertEqual(check.call_count, 2)
            self.assertEqual(check.call_args_list[1].args[0], self.module.DUMMY_PASSWORD_HASH)
        for _ in range(7):
            self.post('/login', data=dict(username='absent-account', password='wrong-password'))
        self.assertEqual(self.post('/login', data=dict(username='absent-account', password='wrong-password')).status_code, 429)

    def test_auth_account_pressure_never_locks_out_valid_owner_at_other_source(self):
        with patch('auth_security.time.time', return_value=200000):
            for n in range(30):
                self.assertEqual(self.attempt(source=f'192.0.2.{n+1}')[0], 0)
            # Even without reliable client IP, correct credentials bypass account pressure.
            failed = self.post('/login', data=dict(username='student-one', password='wrong-password'))
            self.assertEqual(failed.status_code, 429)
            valid = self.post('/login', data=dict(username='student-one', password='test-password'))
            self.assertEqual(valid.status_code, 302)

    def test_auth_shared_source_tightens_individual_limits_without_school_lockout(self):
        with patch('auth_security.time.time', return_value=200000):
            for n in range(300):
                self.attempt(name=f'unknown-{n}', source='127.0.0.1')
            for path, name in (('/login', 'student-one'), ('/teacher/login', 'teacher-one')):
                self.assertEqual(self.post(path, data=dict(username=name, password='test-password')).status_code, 302)
            for _ in range(3):
                self.assertEqual(self.attempt(name='new-unknown', source='127.0.0.1')[0], 0)
            self.assertEqual(self.attempt(name='new-unknown', source='127.0.0.1')[0], 60)
            self.assertEqual(self.attempt(name='different-student', source='127.0.0.1')[0], 0)

    def test_auth_users_and_sources_are_independent_and_cookies_do_not_bypass_pair(self):
        with patch('auth_security.time.time', return_value=200000):
            for _ in range(8):
                self.attempt()
            self.assertEqual(self.attempt(browser='new-cookie')[0], 60)
            self.assertEqual(self.attempt(name='teacher-one')[0], 0)
            self.assertEqual(self.attempt(source='192.0.2.2')[0], 0)

    def test_auth_registration_both_roles_limits_invalid_forms_before_creating_users(self):
        before = self.database_snapshot()
        with patch('auth_security.time.time', return_value=200000):
            for role in ('student', 'teacher'):
                path = '/teacher/register' if role == 'teacher' else '/register'
                name = 'new-' + role
                for _ in range(3):
                    self.assertEqual(self.post(path, data=dict(username=name, password='short')).status_code, 200)
                response = self.post(path, data=dict(username=name, password='test-password', repeat_password='test-password'))
                self.assertEqual(response.status_code, 429)
                self.assertEqual(response.headers['Retry-After'], '600')
            self.assertEqual(self.database_snapshot(), before)
        with patch('auth_security.time.time', return_value=200600):
            for role in ('student', 'teacher'):
                path = '/teacher/register' if role == 'teacher' else '/register'
                response = self.post(path, data=dict(username='new-' + role, password='test-password', repeat_password='test-password'))
                self.assertEqual(response.status_code, 302)
                self.assertEqual(self.client.get(response.location).status_code, 200)

    def test_auth_registration_browser_budget_and_shared_source_adaptation(self):
        with patch('auth_security.time.time', return_value=200000):
            for n in range(10):
                self.assertEqual(self.attempt(name=f'new-{n}', action='register')[0], 0)
            self.assertEqual(self.attempt(name='another', action='register')[0], 600)
            self.assertEqual(self.attempt(name='another', browser='other-student', action='register')[0], 0)
            for n in range(120):
                self.attempt(name=f'source-{n}', browser=f'other-{n}', action='register')
            for n in range(3):
                self.assertEqual(self.attempt(name=f'new-pupil-{n}', browser='new-pupil', action='register')[0], 0)
            self.assertEqual(self.attempt(name='new-pupil-4', browser='new-pupil', action='register')[0], 600)
            self.assertEqual(self.attempt(name='next-pupil', browser='next-pupil', action='register')[0], 0)

    def test_auth_successful_registration_and_logout_do_not_reset_browser_budget(self):
        with patch('auth_security.time.time', return_value=200000):
            browser = None
            for n in range(10):
                path = '/register' if n % 2 else '/teacher/register'
                response = self.post(path, data=dict(username=f'new-account-{n}', password='test-password', repeat_password='test-password'))
                self.assertEqual(response.status_code, 302)
                with self.client.session_transaction() as session:
                    if browser is None:
                        browser = session['auth_browser']
                    self.assertEqual(session['auth_browser'], browser)
                self.assertEqual(self.post('/logout').status_code, 302)
                with self.client.session_transaction() as session:
                    self.assertEqual(session['auth_browser'], browser)
                    self.assertNotIn('user_id', session)
                    self.assertNotIn('auth_token', session)
            response = self.post('/register', data=dict(username='new-account-11', password='test-password', repeat_password='test-password'))
            self.assertEqual(response.status_code, 429)
            with closing(self.module.get_db()) as conn:
                self.assertIsNone(conn.execute("SELECT id FROM users WHERE username='new-account-11'").fetchone())

    def test_auth_proxy_headers_default_ignored_and_explicit_trust_stops_at_untrusted_hop(self):
        self.assertEqual(security.client_source('192.0.2.1', '198.51.100.1'), '192.0.2.1')
        nets = security.proxy_networks('10.0.0.0/24')
        self.assertEqual(security.client_source('10.0.0.2', '203.0.113.99, 192.0.2.1', nets), '192.0.2.1')
        self.assertEqual(security.client_source('192.0.2.1', '203.0.113.99', nets), '192.0.2.1')
        for value in ('garbage', '192.0.2.1, not-an-ip', 'x' * 1025):
            self.assertEqual(security.client_source('10.0.0.2', value, nets), '10.0.0.2')
        self.assertEqual(security.client_source('::ffff:192.0.2.1', ''), '192.0.2.1')
        for value in ('0.0.0.0/0', '::/0', 'not-a-network'):
            with self.assertRaises(RuntimeError):
                security.proxy_networks(value)
        with patch.object(self.module, 'TRUSTED_AUTH_PROXIES', ()):
            for n in range(8):
                self.assertEqual(self.post('/login', data=dict(username='student-one', password='wrong'),
                    headers={'X-Forwarded-For': f'192.0.2.{n+1}', 'Forwarded': 'for=198.51.100.1', 'True-Client-IP': '198.51.100.1'}).status_code, 200)
            self.assertEqual(self.post('/login', data=dict(username='student-one', password='wrong'),
                                      headers={'X-Forwarded-For': '192.0.2.100'}).status_code, 429)

    def test_auth_counter_atomicity_under_concurrent_connections(self):
        with patch('auth_security.time.time', return_value=200000):
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda _: self.attempt()[0], range(12)))
            self.assertEqual(results.count(0), 8)
            self.assertEqual(results.count(60), 4)

    def test_auth_trusted_proxy_configuration_is_used_by_request_handler(self):
        nets = security.proxy_networks('10.0.0.0/24')
        with patch.object(self.module, 'TRUSTED_AUTH_PROXIES', nets), patch('auth_security.time.time', return_value=200000):
            for n in range(8):
                response = self.post('/login', data=dict(username='student-one', password='wrong'),
                    environ_overrides={'REMOTE_ADDR': '10.0.0.2'},
                    headers={'X-Forwarded-For': f'203.0.113.{n+1}, 192.0.2.1'})
                self.assertEqual(response.status_code, 200)
            response = self.post('/login', data=dict(username='student-one', password='wrong'),
                environ_overrides={'REMOTE_ADDR': '10.0.0.2'}, headers={'X-Forwarded-For': '203.0.113.100, 192.0.2.1'})
            self.assertEqual(response.status_code, 429)
            other = self.post('/login', data=dict(username='student-one', password='test-password'),
                environ_overrides={'REMOTE_ADDR': '10.0.0.2'}, headers={'X-Forwarded-For': '192.0.2.2'})
            self.assertEqual(other.status_code, 302)

    def test_auth_database_failure_fails_closed_without_private_error(self):
        import sqlite3
        with patch.object(self.module, 'get_db', side_effect=sqlite3.OperationalError('private-password:db-path')):
            response = self.post('/login', data=dict(username='private-user', password='private-password'))
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private-password', response.text)
        self.assertNotIn('db-path', response.text)

    def test_auth_long_input_bounded_hash_and_no_secret_form_logging(self):
        with patch.object(self.module, 'check_password_hash', wraps=self.module.check_password_hash) as check, \
                patch.object(self.module.app.logger, 'error') as error, \
                patch.object(self.module.app.logger, 'warning') as warning, \
                patch.object(self.module.app.logger, 'info') as info:
            response = self.post('/login', data=dict(username='private-user' * 50, password='private-password' * 100))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(check.call_args.args[1], '')
        self.assertNotIn('private-password', response.text + repr(self.counters()))
        for logger in (error, warning, info):
            logger.assert_not_called()

    def test_auth_logout_preserves_other_device_session(self):
        first = security.new_session(self.module.get_db, self.module.SECRET_KEY, 3)
        second = security.new_session(self.module.get_db, self.module.SECRET_KEY, 3)
        self.sign_in(3)
        security.revoke_session(self.module.get_db, self.module.SECRET_KEY, first)
        with closing(self.module.get_db()) as conn:
            self.assertIsNotNone(conn.execute('SELECT 1 FROM auth_sessions WHERE token_hash=?',
                (security.fingerprint(self.module.SECRET_KEY, 'session', second),)).fetchone())
        self.assertEqual(self.client.get('/').status_code, 200)

    def test_auth_gets_and_rejected_csrf_do_not_create_or_refresh_auth_records(self):
        self.sign_in(3)
        with closing(self.module.get_db()) as conn:
            before = [tuple(r) for r in conn.execute('SELECT * FROM auth_sessions ORDER BY token_hash')]
        for path in ('/login', '/teacher/login', '/register', '/teacher/register', '/', '/project/2'):
            self.assertEqual(self.client.get(path).status_code, 200)
        for path in ('/login', '/teacher/login', '/register', '/teacher/register'):
            self.assertEqual(self.client.post(path, data={'username': 'student-one', 'password': 'wrong'}).status_code, 400)
        self.assertEqual(self.counters(), [])
        with closing(self.module.get_db()) as conn:
            self.assertEqual([tuple(r) for r in conn.execute('SELECT * FROM auth_sessions ORDER BY token_hash')], before)

    def test_auth_repeat_initialization_preserves_counters_sessions_and_user_data(self):
        self.attempt()
        token = security.new_session(self.module.get_db, self.module.SECRET_KEY, 3)
        before, counters = self.database_snapshot(), self.counters()
        with closing(self.module.get_db()) as conn:
            sessions = [tuple(r) for r in conn.execute('SELECT * FROM auth_sessions')]
        with patch.dict(self.module.os.environ, M_FLOW_DEBUG='0', M_FLOW_TEACHER_USER='', M_FLOW_TEACHER_PASSWORD=''):
            self.module.prepare_database()
            self.module.prepare_database()
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(self.counters(), counters)
        with closing(self.module.get_db()) as conn:
            self.assertEqual([tuple(r) for r in conn.execute('SELECT * FROM auth_sessions')], sessions)
        self.assertNotIn(token, repr(sessions))
        self.assertNotIn('student-one', repr(counters))
        self.assertNotIn('192.0.2.1', repr(counters))

    def test_auth_expired_cleanup_and_capacity_do_not_evict_live_protection(self):
        with closing(self.module.get_db()) as conn:
            conn.executemany('INSERT INTO auth_limits(bucket,hits,expires) VALUES(?,1,?)', [('old-' + str(n), 10) for n in range(5)])
            conn.commit()
        with patch('auth_security.time.time', return_value=20), patch.object(security, 'MAX_COUNTERS', 3):
            self.assertEqual(self.attempt()[0], 0)
            before = self.counters()
            with self.assertRaises(DatabaseUnavailable):
                self.attempt(name='another-user')
            self.assertEqual(self.counters(), before)
            self.assertEqual(len(before), 3)

    def test_auth_session_rotation_logout_replay_expiry_and_csrf(self):
        for path, name in (('/login', 'student-one'), ('/teacher/login', 'teacher-one')):
            self.client.get(path)
            with self.client.session_transaction() as session:
                old_csrf = session['csrf_token']
                session['untrusted-extra'] = 'remove-me'
            self.assertEqual(self.post(path, data=dict(username=name, password='test-password')).status_code, 302)
            with self.client.session_transaction() as session:
                self.assertNotEqual(session['csrf_token'], old_csrf)
                self.assertNotIn('untrusted-extra', session)
                token = session['auth_token']
            cookie = self.client.get_cookie('session').value
            self.assertEqual(self.client.post('/logout', data={'csrf_token': old_csrf}).status_code, 400)
            self.assertEqual(self.post('/logout').status_code, 302)
            self.assertEqual(self.client.get('/').status_code, 302)
            self.client.set_cookie('session', cookie)
            self.assertEqual(self.client.get('/').status_code, 302)
            with closing(self.module.get_db()) as conn:
                self.assertIsNone(conn.execute('SELECT 1 FROM auth_sessions WHERE token_hash=?',
                    (security.fingerprint(self.module.SECRET_KEY, 'session', token),)).fetchone())
        self.assertEqual(self.post('/login', data=dict(username='student-one', password='test-password')).status_code, 302)
        with closing(self.module.get_db()) as conn:
            conn.execute('UPDATE auth_sessions SET expires=0')
            conn.commit()
        self.assertEqual(self.client.get('/').status_code, 302)

    def test_auth_relogin_revokes_previous_token_and_legacy_cookies_do_not_authenticate(self):
        self.post('/login', data=dict(username='student-one', password='test-password'))
        old_cookie = self.client.get_cookie('session').value
        self.post('/login', data=dict(username='student-one', password='test-password'))
        current_cookie = self.client.get_cookie('session').value
        self.client.set_cookie('session', old_cookie)
        self.assertEqual(self.client.get('/').status_code, 302)
        self.client.set_cookie('session', current_cookie)
        self.assertEqual(self.client.get('/').status_code, 200)
        with self.client.session_transaction() as session:
            session.pop('auth_token')
        self.assertEqual(self.client.get('/').status_code, 302)

    def test_auth_production_cookie_flags_and_expired_session_json(self):
        with patch.dict(self.module.app.config, SESSION_COOKIE_SECURE=True):
            response = self.client.get('/login', base_url='https://localhost')
            cookie = response.headers['Set-Cookie']
            for flag in ('Secure', 'HttpOnly', 'SameSite=Lax', 'Path=/'):
                self.assertIn(flag, cookie)
        self.assertEqual(int(self.module.app.permanent_session_lifetime.total_seconds()), security.SESSION_SECONDS)
        self.sign_in(3)
        with closing(self.module.get_db()) as conn:
            conn.execute('DELETE FROM auth_sessions')
            conn.commit()
        response = self.post('/project/2/status', json=dict(task_id=3, status='todo'))
        self.assertEqual(response.status_code, 401)

    def test_auth_registered_account_is_atomic_when_session_creation_fails(self):
        before = self.database_snapshot()
        with patch.object(security, 'MAX_SESSIONS', 0):
            response = self.post('/register', data=dict(username='atomic-student', password='test-password', repeat_password='test-password'))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.database_snapshot(), before)

    def test_auth_another_process_observes_sqlite_limit(self):
        if self.module.DATABASE_SETTINGS.backend != 'sqlite':
            self.skipTest('SQLite process check; PostgreSQL uses shared concurrent connections in CI')
        for _ in range(8):
            self.attempt()
        script = """import sys
from auth_security import reserve_attempt
from database import DatabaseSettings,connect_database
settings=DatabaseSettings('sqlite',sys.argv[1])
retry,_=reserve_attempt(lambda:connect_database(settings),sys.argv[2],'login','student-one','192.0.2.1','browser')
assert retry>0
print('shared-limit-ok')
"""
        result = subprocess.run([sys.executable, '-c', script, self.module.DATABASE_SETTINGS.path, self.module.SECRET_KEY],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), 'shared-limit-ok')
