"""Privacy guards of the client-side pg_dump helper; no remote DB access."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import postgres_backup as backup
from database_backup import BackupError, REQUIRED_TABLES


class PostgresBackupTests(unittest.TestCase):
    def test_password_is_environment_only_and_url_is_decoded(self):
        env = backup.connection_environment("postgresql://owner:a%40b%25c@ep-test.example.com/neondb?sslmode=require&channel_binding=require")
        self.assertEqual(env['PGPASSWORD'], 'a@b%c')
        self.assertEqual(env['PGHOST'], 'ep-test.example.com')
        self.assertEqual(env['PGCHANNELBINDING'], 'require')
        self.assertNotIn('DATABASE_URL', {k: v for k, v in env.items() if v == 'a@b%c'})

    def test_pooled_url_is_refused(self):
        with self.assertRaises(BackupError):
            backup.connection_environment("postgresql://owner:secret@ep-test-pooler.example.com/neondb?sslmode=require")

    def test_missing_password_is_refused(self):
        with self.assertRaises(BackupError):
            backup.connection_environment("postgresql://owner@ep-test.example.com/neondb?sslmode=require")

    def test_inherited_pg_overrides_do_not_redirect_backup(self):
        with patch.dict(os.environ, PGHOST='wrong-host', PGSERVICE='wrong-service', PGOPTIONS='wrong-options'):
            env = backup.connection_environment("postgresql://owner:secret@ep-test.example.com/neondb?sslmode=require")
        self.assertEqual(env['PGHOST'], 'ep-test.example.com')
        self.assertNotIn('PGSERVICE', env)
        self.assertNotIn('PGOPTIONS', env)

    def test_repository_destination_is_refused_before_creation(self):
        target = backup.ROOT / 'private-copy'
        with self.assertRaises(BackupError):
            backup.private_directory(target)
        self.assertFalse(target.exists())

    def test_native_failure_does_not_expose_driver_stderr(self):
        result = Mock(returncode=1, stdout=b'', stderr=b'password=private-password secret report')
        with patch.object(backup.subprocess, 'run', return_value=result):
            with self.assertRaises(BackupError) as raised:
                backup.run_private(['pg_dump'], {'PGPASSWORD': 'private-password'})
        self.assertNotIn('private-password', str(raised.exception))
        self.assertNotIn('secret report', str(raised.exception))

    def test_archive_check_requires_every_table(self):
        toc = '\n'.join(f'1; 1259 123 TABLE public {table} owner' for table in REQUIRED_TABLES).encode()
        with patch.object(backup, 'run_private', return_value=toc):
            backup.archive_check(Path('fake.dump'), 'pg_restore')
        with patch.object(backup, 'run_private', return_value=b'1; 1259 123 TABLE DATA public users owner'):
            with self.assertRaises(BackupError):
                backup.archive_check(Path('fake.dump'), 'pg_restore')

    def test_sha256_reads_file_without_logging_contents(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'test.dump'
            path.write_bytes(b'abc')
            self.assertEqual(backup.file_hash(path), 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')

    def test_new_private_directory_accepts_backup_destination(self):
        with tempfile.TemporaryDirectory() as base:
            directory = backup.private_directory(base)
            self.assertTrue(directory.is_dir())
            self.assertEqual(list(directory.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
