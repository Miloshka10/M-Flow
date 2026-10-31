"""All provider calls are intercepted; no email, database or network access."""
import io
import json
import ssl
import traceback
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

from account_mail import _NoMailRedirect
from database import DatabaseUnavailable
from emailjs_transport import send_emailjs


class Response(io.BytesIO):
    def __init__(self, status=200, body=b'OK'):
        super().__init__(body)
        self.status = status


class EmailJsTransportTests(unittest.TestCase):
    def setUp(self):
        self.settings = SimpleNamespace(sender='sender@example.org', origin='https://mflow.example.org',
            provider='emailjs', api_key='private-test-key', public_key='public-test-key',
            service_id='service_test', template_id='template_test')
        self.recipient = 'pupil@example.org'
        self.subject = 'Подтверждение M-Flow'
        self.token = 'A' * 43
        self.action = self.settings.origin + '/email/confirm#token=' + self.token
        self.body = 'Аккаунт: ученик <b>\n\n' + self.action + '\n\nКод из письма:\n' + self.token

    def deliver(self, response):
        opener = Mock()
        opener.open.return_value = response
        with patch('emailjs_transport.build_opener', return_value=opener) as build:
            send_emailjs(self.settings, self.recipient, self.subject, self.body)
        return opener, build

    def test_fixed_https_endpoint_verified_tls_private_auth_and_exact_template_contract(self):
        response = Response()
        opener, build = self.deliver(response)
        handlers = build.call_args.args
        self.assertTrue(handlers[0]._context.check_hostname)
        self.assertEqual(handlers[0]._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertIsInstance(handlers[1], _NoMailRedirect)
        opener.open.assert_called_once()
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.emailjs.com/api/v1.0/email/send')
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.get_header('Content-type'), 'application/json')
        self.assertEqual(opener.open.call_args.kwargs['timeout'], 15)
        self.assertEqual(json.loads(request.data), dict(
            service_id='service_test', template_id='template_test', user_id='public-test-key',
            accessToken='private-test-key', template_params=dict(to_email='pupil@example.org',
                subject=self.subject, message=self.body, action_url=self.action)))
        self.assertTrue(response.closed)

    def test_only_exact_trusted_capability_line_becomes_action_link(self):
        reset = self.settings.origin + '/reset-password#token=' + self.token
        for body, expected in (
            ('Аккаунт: https://phishing.invalid\n\n' + reset, reset),
            ('Аккаунт: ' + self.action, ''),
            ('Пароль изменён.\nАккаунт: https://phishing.invalid', ''),
            ('https://phishing.invalid/email/confirm#token=' + self.token, ''),
            (self.action + '-extra', ''),
            (' ' + self.action, ''),
            (self.action + '\n' + reset, ''),
            (self.action + '\r\nКод из письма', self.action),
        ):
            with self.subTest(body=body):
                self.body = body
                opener, _ = self.deliver(Response())
                params = json.loads(opener.open.call_args.args[0].data)['template_params']
                self.assertEqual(params['action_url'], expected)
                self.assertEqual(params['message'], body, 'The template owns escaping; no double-escaping')

    def test_text_and_json_string_ok_receipts_are_accepted(self):
        for body in (b'OK', b' OK\r\n', b'"OK"', b' "OK"\n'):
            with self.subTest(body=body):
                response = Response(body=body)
                self.deliver(response)
                self.assertTrue(response.closed)

    def test_errors_bad_receipts_and_oversized_responses_are_closed_and_redacted_without_retry(self):
        cases = [(201, b'OK'), (200, b''), (200, b'<html>private-provider-response</html>'),
                 (200, b'{"status":"OK"}'), (200, b'false'), (200, b'null'),
                 (200, b'"NOT OK"'), (200, b'\xff'), (200, b'x' * 4097),
                 (200, b'[' * 1500 + b']' * 1500),
                 URLError('private-provider-response'), TimeoutError('private-provider-response')]
        for case in cases:
            opener = Mock()
            response = None
            if isinstance(case, Exception):
                opener.open.side_effect = case
            else:
                response = Response(*case)
                opener.open.return_value = response
            with self.subTest(case=type(case).__name__), patch('emailjs_transport.build_opener', return_value=opener):
                try:
                    send_emailjs(self.settings, self.recipient, self.subject, self.body)
                except DatabaseUnavailable as exc:
                    displayed = ''.join(traceback.format_exception(exc))
                    for secret in (self.settings.api_key, self.settings.public_key,
                                   self.recipient, self.token, 'private-provider-response'):
                        self.assertNotIn(secret, displayed)
                else:
                    self.fail('A bad response must not be treated as successful acceptance')
            self.assertEqual(opener.open.call_count, 1)
            if response is not None:
                self.assertTrue(response.closed)

    def test_redirects_do_not_forward_private_credentials_or_capabilities(self):
        for status in (301, 302, 303, 307, 308):
            response = io.BytesIO(b'private-provider-response')
            error = HTTPError('https://api.emailjs.com/api/v1.0/email/send', status, 'redirect',
                              {'Location': 'https://other.invalid'}, response)
            with self.subTest(status=status), patch('emailjs_transport.build_opener') as build:
                build.return_value.open.side_effect = error
                with self.assertRaises(DatabaseUnavailable):
                    send_emailjs(self.settings, self.recipient, self.subject, self.body)
                build.return_value.open.assert_called_once()
                handler = build.call_args.args[1]
                request = Request('https://api.emailjs.com/api/v1.0/email/send', data=b'private-payload')
                self.assertIsNone(handler.redirect_request(request, None, status, '', {}, 'https://other.invalid'))
            self.assertTrue(response.closed)

    def test_invalid_recipient_does_not_attempt_provider_request(self):
        with patch('emailjs_transport.build_opener') as build:
            with self.assertRaises(DatabaseUnavailable):
                send_emailjs(self.settings, 'pupil@example.org\r\nBcc:other@example.org', self.subject, self.body)
            build.assert_not_called()


if __name__ == '__main__':
    unittest.main()
