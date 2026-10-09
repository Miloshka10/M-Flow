from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import postgres_app_check as check
from postgres_backup import BackupError


class AppCheckTests(unittest.TestCase):
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
                        patch.object(check, 'fingerprint', return_value='synthetic-hash'):
                    with redirect_stdout(io.StringIO()) as output:
                        check.phase('create')
                    self.assertEqual(json.loads(output.getvalue())['fingerprint'], 'synthetic-hash')
                    os.environ['MF_CHECK_FINGERPRINT'] = 'synthetic-hash'
                    with redirect_stdout(io.StringIO()):
                        check.phase('verify')
            finally:
                sys.modules.pop('app', None)
                if previous is not None:
                    sys.modules['app'] = previous
