import unittest
from unittest.mock import Mock, patch
import postgres_restore_check as restore
from postgres_backup import BackupError


class RestoreGuardTests(unittest.TestCase):
    def test_production_url_rejected_before_connection(self):
        with patch.object(restore.psycopg, 'connect') as connect:
            with self.assertRaises(BackupError):
                restore.restore_check('postgresql://owner:secret@ep-test.example.com/neondb?sslmode=require', 'fake.dump')
            connect.assert_not_called()

    def test_pooled_url_rejected(self):
        with self.assertRaises(BackupError):
            restore.test_environment('postgresql://owner:secret@ep-test-pooler.example.com/mflow_restore_check?sslmode=require')

    def test_test_database_url_accepted(self):
        env = restore.test_environment('postgresql://owner:secret@ep-test.example.com/mflow_restore_check?sslmode=require')
        self.assertEqual(env['PGDATABASE'], 'mflow_restore_check')

    def test_server_database_identity_checked(self):
        conn = Mock()
        conn.execute.return_value.fetchone.return_value = ('neondb',)
        with self.assertRaises(BackupError):
            restore.require_empty(conn)
        self.assertEqual(conn.execute.call_count, 1)

    def test_existing_objects_rejected_without_delete(self):
        conn = Mock()
        conn.execute.return_value.fetchone.side_effect = [('mflow_restore_check',), (1,)]
        with self.assertRaises(BackupError):
            restore.require_empty(conn)
        self.assertTrue(all(call.args[0].lstrip().startswith('SELECT') for call in conn.execute.call_args_list))

    def test_empty_database_accepted(self):
        conn = Mock()
        conn.execute.return_value.fetchone.side_effect = [('mflow_restore_check',), (0,)]
        restore.require_empty(conn)
