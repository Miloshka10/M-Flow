import os
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import traceback
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

from account_mail import (MailSettings, MailDispatcher, normalize_email, send_smtp,
                          send_brevo, send_mail, public_origin, _NoMailRedirect)
from check_account_setup import configuration_report
from database import DatabaseUnavailable


class AccountMailUnits(unittest.TestCase):
    def test_origin_rejects_broken_recovery_link_prefixes_for_both_transports_and_preflight(self):
        invalid = ('https://example.org#', 'https://example.org?', 'https://@example.org',
                   'https://example.org\\other', '\x00https://example.org', 'https://exa\x00mple.org',
                   'https://example.org\x7f', 'https://example.org\x80', 'https://example.org:',
                   'https://[::1]:')
        common = dict(M_FLOW_MAIL_HOST='smtp.example.org', M_FLOW_MAIL_USER='test-user',
                      M_FLOW_MAIL_PASSWORD='test-password', M_FLOW_MAIL_FROM='sender@example.org',
                      M_FLOW_MAIL_API_KEY='test-api-key')
        for origin in invalid:
            with self.subTest(origin=repr(origin)):
                self.assertEqual(public_origin(origin), '')
                if '\x00' in origin:
                    continue  # The OS itself refuses NUL in environment values.
                for provider in ('smtp', 'brevo'):
                    with patch.dict(os.environ, dict(common, M_FLOW_MAIL_PROVIDER=provider,
                                                    M_FLOW_PUBLIC_URL=origin), clear=True):
                        self.assertIsNone(MailSettings.from_environment())
                        report = configuration_report()
                        self.assertFalse(report['checks']['public_https_origin_configured'])
                        self.assertFalse(report['checks']['mail_configured'])
        for origin in ('https://m-flow-4tjw.onrender.com', 'https://example.org:8443',
                       'https://[::1]', 'https://[2001:db8::1]:8443', 'https://пример.рф'):
            with self.subTest(origin=origin):
                self.assertEqual(public_origin(origin + '/'), origin)
                self.assertEqual(public_origin(origin), origin)

    def brevo_settings(self):
        return MailSettings('', 0, '', '', 'sender@example.org', 'https://mflow.example.org', '',
                            'brevo', 'private-test-api-key')

    def test_brevo_configuration_is_explicit_and_never_falls_back_to_smtp(self):
        good = dict(M_FLOW_MAIL_PROVIDER='brevo', M_FLOW_PUBLIC_URL='https://mflow.example.org',
                    M_FLOW_MAIL_FROM='sender@example.org', M_FLOW_MAIL_API_KEY='private-test-api-key')
        with patch.dict(os.environ, good, clear=True):
            settings = MailSettings.from_environment()
            self.assertEqual(settings.provider, 'brevo')
            self.assertEqual(settings.password, '')
            self.assertNotIn('private-test-api-key', repr(settings))
        for key, value in (('M_FLOW_MAIL_API_KEY', ''), ('M_FLOW_MAIL_API_KEY', 'secret\nheader'),
                           ('M_FLOW_MAIL_API_KEY', ' secret '), ('M_FLOW_MAIL_PROVIDER', 'typo'),
                           ('M_FLOW_PUBLIC_URL', 'http://mflow.example.org'),
                           ('M_FLOW_MAIL_FROM', 'sender@example.org\r\nBcc:other@example.org')):
            with self.subTest(key=key), patch.dict(os.environ, dict(good, **{key: value}), clear=True):
                self.assertIsNone(MailSettings.from_environment())

    def test_brevo_uses_fixed_https_verified_tls_plain_text_and_no_contact_import(self):
        received = []
        class Response(io.BytesIO):
            status = 201
        class Opener:
            def open(self, request, timeout):
                received.append((request, timeout))
                return Response(b'{"messageId":"accepted-by-provider"}')
        with patch('account_mail.build_opener', return_value=Opener()) as build:
            send_brevo(self.brevo_settings(), 'PUPIL@example.org', 'Подтвердите почту', 'Код и ссылка')
        handlers = build.call_args.args
        self.assertTrue(handlers[0]._context.check_hostname)
        self.assertTrue(handlers[0]._context.verify_mode)
        self.assertIsInstance(handlers[1], _NoMailRedirect)
        request, timeout = received[0]
        self.assertEqual(request.full_url, 'https://api.brevo.com/v3/smtp/email')
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(timeout, 15)
        self.assertEqual(request.get_header('Api-key'), 'private-test-api-key')
        payload = json.loads(request.data)
        self.assertEqual(payload, dict(sender=dict(email='sender@example.org', name='M-Flow'),
            to=[dict(email='pupil@example.org')], subject='Подтвердите почту', textContent='Код и ссылка'))

    def test_brevo_rejects_redirects_bad_receipts_timeouts_and_never_exposes_provider_body(self):
        class Response(io.BytesIO):
            def __init__(self, status, body):
                super().__init__(body)
                self.status = status
        cases = [(200, b'{"messageId":"not-created"}'), (201, b'not-json'), (201, b'[]'),
                 (201, b'{"messageId":""}'), (201, b'{"messageId":123}'), (201, b'x'*16385),
                 URLError('private-provider-response'), TimeoutError('private-provider-response')]
        for case in cases:
            response = None if isinstance(case, Exception) else Response(*case)
            opener = unittest.mock.Mock()
            if isinstance(case, Exception):
                opener.open.side_effect = case
            else:
                opener.open.return_value = response
            with self.subTest(case=type(case).__name__), patch('account_mail.build_opener', return_value=opener):
                try:
                    send_brevo(self.brevo_settings(), 'pupil@example.org', 'Subject', 'secret-token-in-body')
                except DatabaseUnavailable as exc:
                    displayed = ''.join(traceback.format_exception(exc))
                    self.assertNotIn('private-provider-response', displayed)
                    self.assertNotIn('private-test-api-key', displayed)
                    self.assertNotIn('secret-token-in-body', str(exc))
                else:
                    self.fail('A mail provider failure must fail closed')
            self.assertEqual(opener.open.call_count, 1, 'No automatic resends')
            if response:
                self.assertTrue(response.closed)
        error_body = io.BytesIO(b'private-provider-response')
        error = HTTPError('https://api.brevo.com/v3/smtp/email', 302, 'redirect',
                          {'Location': 'https://other.invalid'}, error_body)
        with patch('account_mail.build_opener') as build:
            build.return_value.open.side_effect = error
            with self.assertRaises(DatabaseUnavailable):
                send_brevo(self.brevo_settings(), 'pupil@example.org', 'Subject', 'Body')
        self.assertTrue(error_body.closed)
        request = Request('https://api.brevo.com/v3/smtp/email', headers={'api-key': 'test-secret'}, data=b'body')
        for status in (301, 302, 303, 307, 308):
            self.assertIsNone(_NoMailRedirect().redirect_request(request, None, status, '', {}, 'https://other.invalid'))

    def test_dispatcher_selects_only_the_explicit_transport(self):
        smtp = MailSettings('smtp.invalid', 587, 'user', 'secret', 'sender@example.org', 'https://mflow.example.org', 'starttls')
        with patch('account_mail.send_brevo') as brevo_send, patch('account_mail.send_smtp') as smtp_send:
            send_mail(self.brevo_settings(), 'pupil@example.org', 'Subject', 'Body')
            brevo_send.assert_called_once()
            smtp_send.assert_not_called()
            send_mail(smtp, 'pupil@example.org', 'Subject', 'Body')
            smtp_send.assert_called_once()
            invalid = MailSettings('', 0, '', '', 'sender@example.org', 'https://mflow.example.org', '', 'typo')
            with self.assertRaises(DatabaseUnavailable):
                send_mail(invalid, 'pupil@example.org', 'Subject', 'Body')
            self.assertEqual(brevo_send.call_count, 1)
            self.assertEqual(smtp_send.call_count, 1)

    def test_setup_check_is_secret_free_and_does_not_open_app_database_or_send_mail(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            settings = dict(M_FLOW_MAIL_PROVIDER='brevo', M_FLOW_PUBLIC_URL='https://mflow.example.org',
                M_FLOW_MAIL_FROM='private-sender@example.org', M_FLOW_MAIL_API_KEY='private-api-key',
                M_FLOW_TEACHER_CODE='private-enrollment-code-123456', M_FLOW_OPERATOR_NAME='private-operator',
                M_FLOW_SUPPORT_EMAIL='private-support@example.org', M_FLOW_DATABASE=str(Path(folder)/'never-create.db'))
            env = dict(os.environ, **settings)
            result = subprocess.run([sys.executable, str(root/'check_account_setup.py')], env=env,
                                    cwd=folder, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertTrue(report['configuration_only'])
            self.assertIn('real_test_delivery', report['external_checks_required'])
            for value in (settings['M_FLOW_MAIL_FROM'], settings['M_FLOW_MAIL_API_KEY'], settings['M_FLOW_TEACHER_CODE'],
                          settings['M_FLOW_OPERATOR_NAME'], settings['M_FLOW_SUPPORT_EMAIL']):
                self.assertNotIn(value, result.stdout + result.stderr)
            self.assertFalse(Path(settings['M_FLOW_DATABASE']).exists())
            with patch.dict(os.environ, dict(settings, M_FLOW_TEACHER_CODE=' '*32), clear=True):
                self.assertFalse(configuration_report()['checks']['teacher_registration_code_configured'])
        with patch.dict(os.environ, {'M_FLOW_MAIL_PROVIDER': 'private-mistyped-key'}, clear=True):
            report = configuration_report()
            self.assertFalse(report['configuration_complete'])
            self.assertEqual(report['mail_provider'], 'unsupported')
            self.assertNotIn('private-mistyped-key', repr(report))

    def test_email_validation_blocks_headers_and_normalizes_without_provider_alias_rules(self):
        self.assertEqual(normalize_email(' Name+project@EXAMPLE.ORG '),'name+project@example.org')
        self.assertNotEqual(normalize_email('n.ame@example.org'),normalize_email('name@example.org'))
        for address in ('a@example.org\r\nBcc:attacker@example.org','a@@example.org','.name@example.org',
                        'name..x@example.org','name@-bad.example','name@example..org','name@example','name@', 'a'*65+'@example.org'):
            with self.subTest(address=address), self.assertRaises(ValueError):
                normalize_email(address)

    def test_settings_fail_closed_without_complete_encrypted_smtp_and_explicit_https_origin(self):
        good=dict(M_FLOW_PUBLIC_URL='https://mflow.example.org', M_FLOW_MAIL_HOST='smtp.example.org',
                  M_FLOW_MAIL_USER='sender',M_FLOW_MAIL_PASSWORD='smtp-test-secret',M_FLOW_MAIL_FROM='sender@example.org')
        with patch.dict(os.environ,good,clear=True):
            config=MailSettings.from_environment()
            self.assertIsNotNone(config)
            self.assertNotIn('smtp-test-secret',repr(config))
            self.assertEqual(config.mode,'starttls')
        for key,value in (('M_FLOW_PUBLIC_URL','http://mflow.example.org'),('M_FLOW_PUBLIC_URL','https://u:p@mflow.example.org'),
                          ('M_FLOW_PUBLIC_URL','https://mflow.example.org/path'),('M_FLOW_MAIL_HOST',''),
                          ('M_FLOW_MAIL_PASSWORD',''),('M_FLOW_MAIL_TLS','none'),('M_FLOW_MAIL_FROM','bad\n@example.org')):
            with self.subTest(key=key,value=value),patch.dict(os.environ,dict(good,**{key:value}),clear=True):
                self.assertIsNone(MailSettings.from_environment())

    def test_smtp_negotiates_tls_before_authentication_and_uses_verified_context(self):
        events=[]
        class SMTP:
            def __init__(self,*args,**kwargs): events.append('connect')
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def ehlo(self): events.append('ehlo')
            def starttls(self,context):
                self_test.assertTrue(context.check_hostname)
                events.append('tls')
            def login(self,*args): events.append('login')
            def send_message(self,msg):
                self_test.assertEqual(msg['To'],'pupil@example.org')
                events.append('send')
        self_test=self
        settings=MailSettings('smtp.example.org',587,'user','secret','sender@example.org','https://mflow.example.org','starttls')
        with patch('account_mail.smtplib.SMTP',SMTP):
            send_smtp(settings,'pupil@example.org','Subject','Body')
        self.assertEqual(events,['connect','ehlo','tls','ehlo','login','send'])

    def test_dispatcher_is_bounded_and_errors_are_reported_without_raw_exception(self):
        release=threading.Event()
        failed=threading.Event()
        dispatcher=MailDispatcher(on_error=failed.set)
        try:
            for _ in range(16):
                dispatcher.submit(lambda send: release.wait(5))
            with self.assertRaises(DatabaseUnavailable):
                dispatcher.submit(lambda send: None)
            release.set()
        finally:
            release.set()
            dispatcher._pool.shutdown(wait=True)
        dispatcher=MailDispatcher(on_error=failed.set)
        try:
            def bad(send): raise RuntimeError('sensitive SMTP diagnostic')
            dispatcher.submit(bad)
            self.assertTrue(failed.wait(5))
        finally:
            dispatcher._pool.shutdown(wait=True)
