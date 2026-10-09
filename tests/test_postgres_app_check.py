from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

import postgres_app_check as check
from postgres_backup import BackupError


class AppCheckTests(unittest.TestCase):
    def test_bounded_url_keeps_credentials_private_and_targets_test_database(self):
        from urllib.parse import urlsplit, parse_qs
        url = check.bounded_url('postgresql://owner:secret@ep-test.example.com/mflow_restore_check?sslmode=require')
        self.assertEqual(urlsplit(url).path, '/mflow_restore_check')
        self.assertNotIn('options', parse_qs(urlsplit(url).query))

    def test_connection_error_category_never_returns_secrets(self):
        import psycopg
        error = psycopg.OperationalError('timeout expired password=secret-private-url')
        self.assertEqual(check.connection_failure_kind(error), 'connection_timeout')

    def test_stream_discards_arbitrary_logs_and_returns_only_digest(self):
        process = Mock()
        process.stdout = io.BytesIO(b'password=secret\n{"step":"accounts"}\n' +
                                    json.dumps({'fingerprint': 'a' * 64}).encode() + b'\n')
        process.wait.return_value = 0
        process.poll.return_value = 0
        with patch.object(check.subprocess, 'Popen', return_value=process), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(check.run_phase('create', {}, lambda event: None), 'a' * 64)
        self.assertNotIn('secret', output.getvalue())

    def test_stream_sanitizes_error_details(self):
        process = Mock()
        process.stdout = io.BytesIO(b'{"step":"stages","error":"database","details":"secret record"}\n')
        process.wait.return_value = 1
        process.poll.return_value = 1
        with patch.object(check.subprocess, 'Popen', return_value=process), redirect_stdout(io.StringIO()):
            with self.assertRaises(BackupError) as caught:
                check.run_phase('create', {}, lambda event: None)
        self.assertIn('database', str(caught.exception))
        self.assertNotIn('secret', str(caught.exception))

    def test_phase_refuses_production_before_app_import(self):
        with patch.dict(os.environ, DATABASE_URL='postgresql://owner:secret@ep-test.example.com/neondb?sslmode=require'):
            with self.assertRaises(BackupError):
                check.phase('create')

    def test_functional_scenarios_on_disposable_sqlite(self):
        # Verify request payloads locally; actual Neon run remains separate.
        with tempfile.TemporaryDirectory() as folder:
            env = dict(DATABASE_URL='unused', M_FLOW_DATABASE_BACKEND='sqlite',
                       M_FLOW_DATABASE=str(Path(folder) / 'test.db'), M_FLOW_DEBUG='0',
                       M_FLOW_SECRET_KEY='test-only-random-key', M_FLOW_TEACHER_USER='',
                       M_FLOW_TEACHER_PASSWORD='', MF_CHECK_PREFIX='test_smoke',
                       MF_CHECK_PASSWORD='test-smoke-password')
            previous = sys.modules.pop('app', None)
            try:
                with patch.dict(os.environ, env), patch.object(check, 'test_environment'), \
                        patch.object(check, 'fingerprint', return_value='a' * 64):
                    with redirect_stdout(io.StringIO()) as output:
                        check.phase('create')
                    self.assertEqual(json.loads(output.getvalue().splitlines()[-1])['fingerprint'], 'a' * 64)
                    os.environ['MF_CHECK_FINGERPRINT'] = 'a' * 64
                    with redirect_stdout(io.StringIO()):
                        check.phase('verify')
            finally:
                sys.modules.pop('app', None)
                if previous is not None:
                    sys.modules['app'] = previous
