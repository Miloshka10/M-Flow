"""The mail QA launcher must stay isolated even with production-like environment."""
from contextlib import closing, redirect_stdout
import getpass
import io
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from account_mail import MailSettings
from database import DatabaseUnavailable
from preview_account_mail import (PreviewDelivery, MAX_LETTERS, hidden, preview,
                                  provider_settings, PreviewSetupError, run)


class MailPreviewTests(unittest.TestCase):
    def test_setup_retries_only_invalid_fields_trims_paste_and_never_echoes_values(self):
        secret = 'private-setup-marker'
        entries = [' Sender@Example.Org\n', 'service bad-' + secret, '\tservice_valid \r\n',
                   ' template_valid ', 'public-valid', '\x16' + secret, ' ' + secret + ' ',
                   'not-an-address-' + secret, ' Owner@Example.Org ', '']
        output = io.StringIO()
        with patch('preview_account_mail.hidden', side_effect=entries) as read, \
             patch('builtins.input', return_value='ОТПРАВКА'), redirect_stdout(output):
            settings, recipients = provider_settings()
        self.assertEqual(settings.sender, 'sender@example.org')
        self.assertEqual(settings.service_id, 'service_valid')
        self.assertEqual(settings.template_id, 'template_valid')
        self.assertEqual(settings.api_key, secret)
        self.assertEqual(recipients, {'owner@example.org'})
        prompts = [call.args[0] for call in read.call_args_list]
        self.assertEqual(prompts.count('Service ID: '), 2)
        self.assertEqual(prompts.count('Private Key: '), 2)
        self.assertEqual(prompts.count('Public Key: '), 1)
        self.assertEqual(prompts.count('Ваш тестовый адрес получателя: '), 2)
        for value in (secret, 'sender@example.org', 'Owner@Example.Org', 'public-valid', 'service_valid'):
            self.assertNotIn(value, output.getvalue())
        self.assertIn('Не принято поле «Service ID»', output.getvalue())
        self.assertIn('Не принято поле «Private Key»', output.getvalue())
        self.assertIn('Действительность ключей и доставку ещё нужно проверить', output.getvalue())

    def test_setup_error_is_explained_but_unknown_exception_values_are_redacted(self):
        for error, expected in ((PreviewSetupError('Скрытый ввод недоступен.'), 'Скрытый ввод недоступен.'),
                                (RuntimeError('private-setup-marker'), 'LOCAL_START')):
            output = io.StringIO()
            with self.subTest(kind=type(error).__name__), patch('preview_account_mail.main', side_effect=error), redirect_stdout(output):
                self.assertEqual(run(), 1)
            self.assertIn(expected, output.getvalue())
            self.assertNotIn('private-setup-marker', output.getvalue())

    def test_getpass_fallback_and_eof_are_explained_without_exposing_the_exception(self):
        for error in (getpass.GetPassWarning('private-setup-marker'), EOFError('private-setup-marker')):
            with patch('sys.stdin.isatty', return_value=True), patch('getpass.getpass', side_effect=error):
                with self.assertRaises(PreviewSetupError) as caught:
                    hidden('Private Key: ')
            self.assertNotIn('private-setup-marker', str(caught.exception))
            self.assertIn('PowerShell', str(caught.exception))

    def test_delivery_allowlist_and_attempt_cap_hold_after_provider_failure(self):
        sender = Mock(side_effect=DatabaseUnavailable('provider failed'))
        delivery = PreviewDelivery(['owner@example.org'], real=True, sender=sender)
        for _ in range(MAX_LETTERS):
            with self.assertRaises(DatabaseUnavailable):
                delivery(None, 'owner@example.org', 'test', 'test')
        with self.assertRaises(DatabaseUnavailable):
            delivery(None, 'owner@example.org', 'test', 'test')
        with self.assertRaises(DatabaseUnavailable):
            delivery(None, 'someone-else@example.org', 'test', 'test')
        self.assertEqual(sender.call_count, MAX_LETTERS)
        self.assertEqual(delivery.captured(), [])
        other = PreviewDelivery(['owner@example.org'], real=True, sender=sender)
        with self.assertRaises(DatabaseUnavailable):
            other(None, 'outsider@example.org', 'test', 'test')
        self.assertEqual(sender.call_count, MAX_LETTERS)

    def test_noninteractive_secret_entry_cannot_fall_back_to_echo(self):
        with patch('sys.stdin', io.StringIO('private-data')), patch('getpass.getpass') as getpass:
            with self.assertRaises(ValueError):
                hidden('key')
            getpass.assert_not_called()

    def test_capture_uses_only_new_sqlite_binds_loopback_and_cleans_up(self):
        inherited = dict(DATABASE_URL='postgresql://never-connect.invalid/production',
                         M_FLOW_DATABASE='/never-open/production.sqlite3',
                         M_FLOW_DATABASE_BACKEND='postgres', M_FLOW_DEBUG='1',
                         M_FLOW_TEACHER_USER='must-not-be-created', M_FLOW_TEACHER_PASSWORD='unused',
                         M_FLOW_MAIL_PROVIDER='emailjs', M_FLOW_MAIL_API_KEY='must-not-be-used',
                         M_FLOW_PUBLIC_URL='https://production.invalid')
        with patch.dict(os.environ, inherited), patch('emailjs_transport.build_opener') as network:
            with preview() as (module, server, password, delivery):
                database = Path(module.DATABASE_SETTINGS.path)
                self.assertEqual(module.DATABASE_SETTINGS.backend, 'sqlite')
                self.assertEqual(Path(module.app.root_path), Path(__file__).resolve().parents[1])
                self.assertNotEqual(str(database), inherited['M_FLOW_DATABASE'])
                self.assertTrue(database.exists())
                self.assertFalse(module.DEBUG_MODE)
                self.assertEqual(server.server_address[0], '127.0.0.1')
                with closing(module.get_db()) as conn:
                    names = {row['username'] for row in conn.execute('SELECT username FROM users').fetchall()}
                self.assertEqual(names, {'mail-student', 'mail-teacher'})
                origin = f'http://127.0.0.1:{server.server_port}'
                client = module.app.test_client()
                with client.get('/static/account-mail.js', base_url=origin) as asset:
                    self.assertEqual(asset.status_code, 200)
                self.assertEqual(client.get('/login', base_url='http://attacker.invalid').status_code, 403)
                self.assertEqual(client.get('/login', base_url=origin, environ_overrides={'REMOTE_ADDR': '192.0.2.1'}).status_code, 403)
                self.assertEqual(client.post('/login', base_url=origin, data={'username': 'mail-student', 'password': password}).status_code, 400)
                token = 'A' * 43
                delivery(module.app.config['MAIL_SETTINGS'], 'first@example.org', '<script>title</script>',
                         '<img src=x onerror=alert(1)>\n' + origin + '/email/confirm#token=' + token)
                page = client.get('/__qa_mailbox', base_url=origin)
                self.assertEqual(page.status_code, 200)
                self.assertIn('&lt;script&gt;title&lt;/script&gt;', page.text)
                self.assertNotIn('<img src=x', page.text)
                self.assertIn('/email/confirm#token=' + token, page.text)
            self.assertFalse(database.exists())
            self.assertEqual(os.environ['DATABASE_URL'], inherited['DATABASE_URL'])
            self.assertEqual(os.environ['M_FLOW_MAIL_API_KEY'], inherited['M_FLOW_MAIL_API_KEY'])
            network.assert_not_called()

    def test_real_mode_has_no_mailbox_and_loopback_origin_cannot_leak_to_environment(self):
        settings = MailSettings('', 0, '', '', 'sender@example.org', 'https://approved.invalid', '',
                                'emailjs', 'private-key', 'public-key', 'service_test', 'template_test')
        with patch('emailjs_transport.build_opener') as network:
            with preview(settings, ['owner@example.org']) as (module, server, _, delivery):
                origin = f'http://127.0.0.1:{server.server_port}'
                self.assertEqual(module.app.config['MAIL_SETTINGS'].origin, origin)
                self.assertEqual(module.app.test_client().get('/__qa_mailbox', base_url=origin).status_code, 404)
                self.assertEqual(delivery.recipients, {'owner@example.org'})
                self.assertNotIn('M_FLOW_MAIL_API_KEY', os.environ)
                self.assertNotIn('M_FLOW_PUBLIC_URL', os.environ)
                self.assertFalse(module.app.debug)
            network.assert_not_called()


if __name__ == '__main__':
    unittest.main()
