"""Account invariants inherited by isolated SQLite and PostgreSQL suites."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import re
from unittest.mock import patch
from types import SimpleNamespace
import os
from werkzeug.security import check_password_hash, generate_password_hash

import account_mail
from account_names import name_audit


class Mailbox:
    def __init__(self):
        self.jobs, self.letters = [], []

    def submit(self, job):
        self.jobs.append(job)

    def flush(self):
        while self.jobs:
            self.jobs.pop(0)(lambda settings, recipient, subject, body: self.letters.append((recipient, subject, body)))

    def token(self, index=-1):
        return re.search(r'#token=([A-Za-z0-9_-]{43})', self.letters[index][2])[1]


class AccountRecoveryCases:
    def test_recovery_bootstrap_cannot_create_case_alias_or_replace_existing_role(self):
        before=self.database_snapshot()
        with patch.dict(os.environ,M_FLOW_DEBUG='0',M_FLOW_TEACHER_USER='STUDENT-ONE',M_FLOW_TEACHER_PASSWORD='bootstrap-password'):
            self.module.prepare_database()
        self.assertEqual(self.database_snapshot(),before)

    def mail_setup(self):
        box = Mailbox()
        settings = account_mail.MailSettings('smtp.invalid', 587, 'test-user', 'test-only-password', 'sender@example.org', 'https://mflow.example.org', 'starttls')
        config = patch.dict(self.module.app.config, MAIL_SETTINGS=settings, MAIL_DISPATCHER=box)
        config.start()
        self.addCleanup(config.stop)
        return box

    def attach_email(self, box, address='student@example.org', user=3):
        self.sign_in(user)
        result = self.post('/profile/email', data=dict(email=address, password='test-password'))
        self.assertEqual(result.status_code, 200)
        box.flush()
        raw = box.token()
        self.assertEqual(self.post('/email/confirm', data={'token':raw}).status_code, 200)
        return raw

    def reset_link(self, box, name='student-one', email='student@example.org'):
        response = self.post('/forgot-password', data=dict(username=name, email=email))
        self.assertEqual(response.status_code, 200)
        box.flush()
        return box.token()

    def test_recovery_names_block_case_and_unicode_aliases_without_renaming(self):
        for name in ('STUDENT-ONE', 'ｓｔｕｄｅｎｔ-one'):
            before = self.database_snapshot()
            response = self.post('/register', data=dict(username=name, password='test-password', repeat_password='test-password'))
            self.assertIn('Такой логин уже занят', response.text)
            self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(self.post('/login', data=dict(username='STUDENT-ONE', password='test-password')).status_code, 302)
        with closing(self.module.get_db()) as conn:
            self.assertEqual(conn.execute('SELECT username FROM users WHERE id=3').fetchone()['username'], 'student-one')

    def test_recovery_legacy_name_collisions_keep_exact_accounts_and_reject_ambiguous_alias(self):
        with closing(self.module.get_db()) as conn:
            conn.execute("INSERT INTO users(id,username,password,role) VALUES(9,'Student-One',?,'student')", (generate_password_hash('other-password'),))
            conn.commit()
            self.assertEqual(name_audit(conn), dict(accounts=4, collision_groups=1, accounts_in_collisions=2))
        self.assertEqual(self.post('/login', data=dict(username='Student-One', password='other-password')).status_code, 302)
        with self.client.session_transaction() as sess:
            self.assertEqual(sess['user_id'], 9)
        self.assertEqual(self.post('/login', data=dict(username='STUDENT-ONE', password='test-password')).status_code, 200)
        self.assertEqual(self.post('/login', data=dict(username='student-one', password='test-password')).status_code, 302)

    def test_recovery_case_aliases_share_login_limit(self):
        for n in range(8):
            self.assertEqual(self.post('/login', data=dict(username='STUDENT-ONE' if n % 2 else 'student-one', password='incorrect-password')).status_code, 200)
        self.assertEqual(self.post('/login', data=dict(username='Student-One', password='incorrect-password')).status_code, 429)

    def test_recovery_concurrent_registration_allows_only_one_normalized_name(self):
        def create(name):
            with self.module.app.test_client() as client:
                client.get('/register')
                with client.session_transaction() as sess:
                    token = sess['csrf_token']
                return client.post('/register', data=dict(username=name, password='test-password', repeat_password='test-password', csrf_token=token)).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(create, ('New-Pupil', 'new-pupil'))), [200,302])

    def test_recovery_unconfigured_mail_fails_closed_without_mutation(self):
        with patch.dict(self.module.app.config, MAIL_SETTINGS=None):
            self.sign_in(3)
            before = self.database_snapshot()
            self.assertIn('пока не подключено', self.client.get('/forgot-password').text)
            self.assertEqual(self.post('/forgot-password', data=dict(username='student-one', email='student@example.org')).status_code, 503)
            self.assertEqual(self.post('/profile/email', data=dict(email='student@example.org', password='test-password')).status_code, 503)
            self.assertEqual(self.database_snapshot(), before)

    def test_recovery_email_needs_current_password_and_never_saves_plain_tokens(self):
        box = self.mail_setup()
        self.sign_in(3)
        before = self.database_snapshot()
        invalid = self.post('/profile/email', data=dict(email='student@example.org', password='bad-password'))
        self.assertEqual(invalid.status_code, 400)
        self.assertIn('value="student@example.org"', invalid.text)
        self.assertNotIn('bad-password', invalid.text)
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(box.jobs, [])
        self.post('/profile/email', data=dict(email='student@example.org', password='test-password'))
        box.flush()
        with closing(self.module.get_db()) as conn:
            self.assertEqual(account_mail.email_for(conn,3)['email'], '')
            self.assertNotIn(box.token(), '\n'.join(conn.iterdump()) if hasattr(conn,'iterdump') else str([dict(r) for r in conn.execute('SELECT * FROM account_email_tokens')]))
        self.post('/forgot-password', data=dict(username='student-one', email='student@example.org'))
        box.flush()
        self.assertEqual(len(box.letters), 1, 'Unconfirmed mail cannot reset a password')

    def test_recovery_confirmation_is_post_csrf_single_use_and_not_login(self):
        box = self.mail_setup()
        self.sign_in(3)
        self.post('/profile/email', data=dict(email='student@example.org', password='test-password'))
        box.flush()
        raw = box.token()
        before = self.database_snapshot()
        self.assertEqual(self.client.get('/email/confirm').status_code, 200)
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(self.client.post('/email/confirm', data={'token':raw}).status_code, 400)
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(self.post('/email/confirm', data={'token':raw}).status_code, 200)
        before = self.database_snapshot()
        self.assertEqual(self.post('/email/confirm', data={'token':raw}).status_code, 400)
        self.assertEqual(self.database_snapshot(), before)

    def test_recovery_existing_email_requires_both_mailboxes_for_change(self):
        box = self.mail_setup()
        self.attach_email(box)
        self.post('/profile/email', data=dict(email='new@example.org', password='test-password'))
        box.flush()
        self.assertEqual([m[0] for m in box.letters[-2:]], ['new@example.org','student@example.org'])
        new, old = box.token(-2), box.token(-1)
        self.post('/email/confirm', data={'token':new})
        with closing(self.module.get_db()) as conn:
            self.assertEqual(account_mail.email_for(conn,3)['email'], 'student@example.org')
        self.post('/email/confirm', data={'token':old})
        with closing(self.module.get_db()) as conn:
            self.assertEqual(account_mail.email_for(conn,3)['email'], 'new@example.org')

    def test_recovery_reset_revokes_sessions_and_keeps_projects_history_and_role(self):
        box = self.mail_setup()
        self.attach_email(box)
        self.stage_post(3)
        raw = self.reset_link(box)
        with self.client.session_transaction() as sess:
            old_cookie = dict(sess)
        with closing(self.module.get_db()) as conn:
            before = [tuple(r) for r in conn.execute('SELECT * FROM work_history')]
        result = self.post('/reset-password', data=dict(token=raw, password='new-test-password', repeat_password='new-test-password'))
        self.assertEqual(result.status_code, 200)
        self.assertIn('Пароль изменён', result.text)
        with self.client.session_transaction() as sess:
            self.assertNotIn('user_id', sess)
        with closing(self.module.get_db()) as conn:
            row = conn.execute('SELECT * FROM users WHERE id=3').fetchone()
            self.assertTrue(check_password_hash(row['password'],'new-test-password'))
            self.assertEqual(row['role'],'student')
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM auth_sessions WHERE user_id=3').fetchone()[0],0)
            self.assertEqual([tuple(r) for r in conn.execute('SELECT * FROM work_history')],before)
        with self.client.session_transaction() as sess:
            sess.update(old_cookie)
        self.assertEqual(self.client.get('/project/1').status_code,302)
        self.assertEqual(self.post('/reset-password', data=dict(token=raw,password='replayed-password',repeat_password='replayed-password')).status_code,400)
        box.flush()
        self.assertIn('Пароль M-Flow изменён', box.letters[-1][1])

    def test_recovery_requests_do_not_disclose_known_or_unknown_account_and_do_not_send_in_request(self):
        box = self.mail_setup()
        self.attach_email(box)
        count = len(box.letters)
        known = self.post('/forgot-password', data=dict(username='STUDENT-ONE', email='student@example.org'))
        unknown = self.post('/forgot-password', data=dict(username='unknown-person', email='unknown@example.org'))
        self.assertEqual(known.status_code, unknown.status_code)
        self.assertIn(account_mail.GENERIC_REQUEST,known.text)
        self.assertIn(account_mail.GENERIC_REQUEST,unknown.text)
        self.assertEqual(len(box.jobs),2)
        self.assertEqual(len(box.letters),count)
        box.flush()
        self.assertEqual(len(box.letters),count+1)
        self.assertIn('Аккаунт: student-one',box.letters[-1][2])
        self.assertNotIn('student@example.org',known.text)
        self.assertNotIn(box.token(), known.text)

    def test_recovery_expiry_wrong_purpose_password_change_and_token_versions(self):
        box = self.mail_setup()
        verify = self.attach_email(box)
        raw = self.reset_link(box)
        before = self.database_snapshot()
        data = dict(password='new-test-password', repeat_password='new-test-password')
        self.assertEqual(self.post('/reset-password',data=dict(data,token=verify)).status_code,400)
        self.assertEqual(self.post('/email/confirm',data={'token':raw}).status_code,400)
        with patch('account_mail.time',SimpleNamespace(time=lambda:9999999999)):
            with self.assertRaises(ValueError):
                account_mail.reset_password(self.module.get_db,self.module.SECRET_KEY,raw,'new-test-password','new-test-password')
        self.assertEqual(self.database_snapshot(),before)
        self.post('/profile',data=dict(old_password='test-password',new_password='changed-password',repeat_password='changed-password'))
        self.assertEqual(self.post('/reset-password',data=dict(data,token=raw)).status_code,400)

    def test_recovery_invalid_password_keeps_token_usable_and_passwords_out_of_html(self):
        box = self.mail_setup()
        self.attach_email(box)
        raw = self.reset_link(box)
        before = self.database_snapshot()
        result = self.post('/reset-password',data=dict(token=raw,password='private-password',repeat_password='different-secret'))
        self.assertEqual(result.status_code,400)
        self.assertNotIn('private-password',result.text)
        self.assertNotIn('different-secret',result.text)
        self.assertEqual(self.database_snapshot(),before)
        self.assertEqual(self.post('/reset-password',data=dict(token=raw,password='final-password',repeat_password='final-password')).status_code,200)

    def test_recovery_concurrent_reset_consumes_capability_once(self):
        box=self.mail_setup()
        self.attach_email(box)
        raw=self.reset_link(box)
        def reset(_):
            with self.module.app.test_client() as client:
                client.get('/reset-password')
                with client.session_transaction() as sess:
                    csrf=sess['csrf_token']
                return client.post('/reset-password',data=dict(token=raw,password='race-new-password',repeat_password='race-new-password',csrf_token=csrf)).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(reset,range(2))),[200,400])

    def test_recovery_replaced_pending_address_invalidates_previous_confirmation(self):
        box=self.mail_setup()
        self.sign_in(3)
        self.post('/profile/email',data=dict(email='first@example.org',password='test-password'))
        box.flush()
        old=box.token()
        self.post('/profile/email',data=dict(email='second@example.org',password='test-password'))
        box.flush()
        self.assertEqual(self.post('/email/confirm',data={'token':old}).status_code,400)
        self.assertEqual(self.post('/email/confirm',data={'token':box.token()}).status_code,200)
        with closing(self.module.get_db()) as conn:
            self.assertEqual(account_mail.email_for(conn,3)['email'],'second@example.org')

    def test_recovery_csrf_on_all_new_forms_and_unrelated_teacher_cannot_change_email(self):
        box = self.mail_setup()
        self.attach_email(box)
        raw = self.reset_link(box)
        self.sign_in(1)
        before = self.database_snapshot()
        for path,data in (('/forgot-password',dict(username='student-one',email='student@example.org')),
                          ('/reset-password',dict(token=raw,password='other-password',repeat_password='other-password')),
                          ('/email/confirm',dict(token=raw)),('/profile/email',dict(user_id='3',email='thief@example.org',password='test-password'))):
            self.assertEqual(self.client.post(path,data=data).status_code,400)
        self.assertEqual(self.database_snapshot(),before)
        self.post('/profile/email',data=dict(user_id='3',email='teacher@example.org',password='test-password'))
        with closing(self.module.get_db()) as conn:
            self.assertEqual(account_mail.email_for(conn,3)['email'],'student@example.org')
            self.assertEqual(account_mail.email_for(conn,1)['pending_email'],'teacher@example.org')

    def test_recovery_email_and_token_requests_have_fixed_budgets(self):
        self.mail_setup()
        for _ in range(3):
            self.assertEqual(self.post('/forgot-password',data=dict(username='unknown',email='same@example.org')).status_code,200)
        limited=self.post('/forgot-password',data=dict(username='unknown',email='same@example.org'))
        self.assertEqual(limited.status_code,429)
        self.assertIn('Retry-After',limited.headers)

    def test_recovery_pages_are_read_only_private_referrers_and_no_fabricated_operator(self):
        self.mail_setup()
        before=self.database_snapshot()
        for path in ('/forgot-password','/reset-password','/email/confirm'):
            result=self.client.get(path)
            self.assertEqual(result.status_code,200)
            self.assertEqual(result.headers['Referrer-Policy'],'no-referrer')
            self.assertEqual(result.headers['Cache-Control'],'no-store')
            self.assertIn('/static/account-mail.js',result.text)
        self.assertEqual(self.database_snapshot(),before)
        self.assertIn('не заменяет заполненную политику',self.client.get('/data').text)
        with patch.dict(self.module.app.config,PUBLIC_ORIGIN='https://mflow.example.org'):
            canonical=self.client.get('/login',base_url='http://mflow.example.org')
            self.assertEqual(canonical.headers['Strict-Transport-Security'],'max-age=31536000')
            spoofed=self.client.get('/login',headers={'X-Forwarded-Proto':'https'})
            self.assertNotIn('Strict-Transport-Security',spoofed.headers)
