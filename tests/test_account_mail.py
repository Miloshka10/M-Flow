import os
import threading
import unittest
from unittest.mock import patch

from account_mail import MailSettings, MailDispatcher, normalize_email, send_smtp
from database import DatabaseUnavailable


class AccountMailUnits(unittest.TestCase):
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
