import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import postgres_restore_check as restore
from postgres_backup import BackupError


class RestoreGuardTests(unittest.TestCase):
    def test_old_and_notification_archives_have_compatible_manifest(self):
        old = dict(counts={table: 0 for table in restore.REQUIRED_TABLES})
        self.assertEqual(restore.manifest_tables(old), restore.REQUIRED_TABLES)
        new = dict(counts={**old['counts'], 'notifications': 3})
        self.assertEqual(restore.manifest_tables(new), restore.REQUIRED_TABLES | {'notifications'})
        with self.assertRaises(BackupError):
            restore.manifest_tables(dict(counts={**old['counts'], 'unknown_table': 1}))

    def test_row_hash_manifest_requires_all_hashes_when_format_is_present(self):
        hashes = {table: 'a' * 64 for table in restore.REQUIRED_TABLES}
        self.assertIsNone(restore.manifest_row_hashes({}, restore.REQUIRED_TABLES))
        manifest = dict(row_sha256_format=restore.ROW_HASH_FORMAT, row_sha256=hashes)
        self.assertEqual(restore.manifest_row_hashes(manifest, restore.REQUIRED_TABLES), hashes)
        for invalid in (
            dict(row_sha256_format=restore.ROW_HASH_FORMAT),
            dict(manifest, row_sha256_format='unknown'),
            dict(manifest, row_sha256={}),
            dict(manifest, row_sha256={**hashes, 'users': 'invalid'}),
        ):
            with self.subTest(manifest=invalid), self.assertRaises(BackupError):
                restore.manifest_row_hashes(invalid, restore.REQUIRED_TABLES)

    def test_restore_rejects_altered_rows_with_matching_counts(self):
        tables = sorted(restore.REQUIRED_TABLES)
        expected_hashes = dict.fromkeys(tables, 'a' * 64)
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / 'database.dump'
            archive.write_bytes(b'archive')
            manifest = dict(sha256=restore.file_hash(archive), counts=dict.fromkeys(tables, 1),
                            row_sha256_format=restore.ROW_HASH_FORMAT, row_sha256=expected_hashes)
            archive.with_name('verification.json').write_text(json.dumps(manifest), encoding='utf-8')
            with (patch.object(restore, 'test_environment', return_value={}),
                  patch.object(restore, 'tool_path'),
                  patch.object(restore, 'archive_check'),
                  patch.object(restore, 'run_private'),
                  patch.object(restore, 'require_empty'),
                  patch.object(restore.psycopg, 'connect') as connect,
                  patch.object(restore, 'table_row_hashes',
                               return_value={**expected_hashes, 'users': 'b' * 64})):
                conn = connect.return_value.__enter__.return_value
                conn.execute.return_value.__iter__.return_value = iter((table,) for table in tables)
                conn.execute.return_value.fetchone.return_value = (1,)
                with self.assertRaisesRegex(BackupError, 'Содержимое записей отличается'):
                    restore.restore_verified_archive('unused-url', archive)
            self.assertFalse(archive.with_name('restore_verified.json').exists())

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
