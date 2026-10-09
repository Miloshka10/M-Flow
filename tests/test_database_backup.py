"""Persistence and recovery checks on disposable databases, never real users."""

from contextlib import closing, redirect_stderr, redirect_stdout
import importlib.util
import io
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import database_backup as backup


ROOT = Path(__file__).resolve().parents[1]


class DatabaseBackupTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.directory = Path(self.folder.name)
        self.database = self.directory / "working.db"
        self.output = self.directory / "snapshot.sqlite3"
        self.env = patch.dict(os.environ, {
            "M_FLOW_DATABASE": str(self.database), "M_FLOW_DEBUG": "0", "M_FLOW_DATABASE_BACKEND": "sqlite",
            "M_FLOW_SECRET_KEY": "disposable-backup-test-key",
            "M_FLOW_TEACHER_USER": "", "M_FLOW_TEACHER_PASSWORD": "",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        spec = importlib.util.spec_from_file_location("backup_test_app", ROOT / "app.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        with closing(self.module.get_db()) as conn:
            conn.executemany("INSERT INTO users(id,username,password,role) VALUES(?,?,?,?)", [
                (1, "uchitel", "existing-private-hash", "teacher"),
                (2, "milosh", "another-private-hash", "student"),
                (3, "student-invitee", "private-hash-3", "student"),
            ])
            conn.executemany("INSERT INTO projects(id,name,owner_id) VALUES(?,?,2)",
                             [(1, "Робот — тест"), (2, "Исследование — тест")])
            conn.executemany("INSERT INTO project_members(project_id,user_id) VALUES(?,?)",
                             [(1, 1), (1, 2), (2, 1), (2, 2)])
            conn.execute("""INSERT INTO tasks(id,project_id,title,done,status,deadline,priority,assignee_id)
                VALUES(1,1,'Задача — тест',1,'done','2027-01-01','high',2)""")
            conn.execute("""INSERT INTO task_updates(id,task_id,user_id,body,created_at)
                VALUES(1,1,2,'Закрытый отчёт — тест','2026-10-09 12:00:00')""")
            conn.executemany("""INSERT INTO project_stages(project_id,number,state,result,
                presentation_url,teacher_comment,submitted_by,reviewed_by,revision,updated_at)
                VALUES(1,?,'done',?,'https://example.com/slides','Принято — тест',2,1,3,'2026-10-09 12:00:00')""",
                [(number, f"Результат этапа {number}") for number in range(1, 6)])
            conn.execute("""INSERT INTO project_defenses(project_id,video_url,description,state,
                teacher_comment,author_id,reviewer_id,revision,updated_at)
                VALUES(1,'https://example.com/video','Выступление — тест','accepted',
                'Принято — тест',2,1,4,'2026-10-09 12:00:00')""")
            conn.executemany("""INSERT INTO project_assessments(project_id,student_id,teacher_id,
                student_name,rubric_version,scores_json,note,state,updated_at)
                VALUES(?,2,1,'Ученик — тест','original-rubric','[1,2,3]','Закрытое примечание — тест',?,'2026-10-09 12:00:00')""",
                [(1, "published"), (2, "draft")])
            conn.execute("""INSERT INTO student_profiles(user_id,class_name,direction,skills_json,bio,discoverable)
                VALUES(2,'10 ИТ','ИТ','["Python"]','Закрытый профиль — тест',0)""")
            conn.execute("""INSERT INTO team_member_roles(project_id,user_id,role_text)
                VALUES(1,2,'Разработчик')""")
            conn.executemany("""INSERT INTO team_invitations(project_id,sender_id,invitee_id,
                role_text,message,state,updated_at) VALUES(1,2,?,'Инженер','Приглашение — тест',?,'2026-10-09 12:00:00')""",
                [(2, "accepted"), (3, "pending")])
            conn.execute("PRAGMA user_version=7")
            conn.commit()

    def snapshot(self, path):
        with closing(sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True)) as conn:
            schema = conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
            rows = {name: sorted(conn.execute('SELECT * FROM "' + name.replace('"', '""') + '"').fetchall(), key=repr)
                    for kind, name, _, _ in schema if kind == "table"}
            return schema, rows, conn.execute("PRAGMA user_version").fetchone()

    def test_repeated_initialization_preserves_every_table_and_password(self):
        before = self.snapshot(self.database)
        # Even an already configured bootstrap teacher must not reset its account.
        with patch.dict(os.environ, M_FLOW_TEACHER_USER="uchitel", M_FLOW_TEACHER_PASSWORD="not-a-real-password"):
            self.module.prepare_database()
            self.module.prepare_database()
        self.assertEqual(self.snapshot(self.database), before)
        self.assertEqual(self.module.DATABASE_PATH, str(self.database))

    def test_new_process_start_and_legacy_initializer_preserve_all_records(self):
        before = self.snapshot(self.database)
        for command in ([sys.executable, "-c", "import app"], [sys.executable, "init_db.py"]):
            result = subprocess.run(command, cwd=ROOT, env=dict(os.environ), capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.snapshot(self.database), before)

    def test_backup_and_restore_include_all_data_schema_and_metadata(self):
        before = self.snapshot(self.database)
        backup.create_backup(self.output)
        restored = self.directory / "restored.sqlite3"
        backup.restore_backup(self.output, restored)
        backup.verify_database(restored)
        self.assertEqual(self.snapshot(self.output), before)
        self.assertEqual(self.snapshot(restored), before)
        self.assertEqual(self.snapshot(self.database), before)
        self.assertFalse(list(self.directory.glob(".mflow-snapshot-*")))

    def test_live_wal_contains_committed_but_not_uncommitted_changes(self):
        with closing(sqlite3.connect(self.database)) as writer:
            self.assertEqual(writer.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("INSERT INTO task_updates(task_id,user_id,body) VALUES(1,2,'Committed in WAL')")
            writer.commit()
            self.assertTrue(Path(str(self.database) + "-wal").exists())
            # The main file alone has not received this WAL commit.
            with closing(sqlite3.connect(self.database.as_uri() + "?immutable=1", uri=True)) as main_only:
                self.assertEqual(main_only.execute("SELECT COUNT(*) FROM task_updates").fetchone()[0], 1)
            writer.execute("INSERT INTO task_updates(task_id,user_id,body) VALUES(1,2,'Uncommitted')")
            backup.create_backup(self.output)
            with closing(sqlite3.connect(self.output)) as copy:
                self.assertEqual(copy.execute("SELECT COUNT(*) FROM task_updates").fetchone()[0], 2)
                self.assertIsNone(copy.execute("SELECT 1 FROM task_updates WHERE body='Uncommitted'").fetchone())
            writer.rollback()
            restored = self.directory / "wal-restored.sqlite3"
            backup.restore_backup(self.output, restored)
            self.assertEqual(self.snapshot(restored), self.snapshot(self.database))

    def test_writer_can_commit_during_incremental_backup(self):
        with closing(sqlite3.connect(self.database)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE future_data(id INTEGER PRIMARY KEY,body TEXT)")
            conn.executemany("INSERT INTO future_data(body) VALUES(?)", [("x" * 4096,)] * 600)
            conn.commit()
        written = []
        database = self.database

        class ConcurrentConnection(sqlite3.Connection):
            def backup(self, target, **kwargs):
                original_progress = kwargs["progress"]

                def progress(status, remaining, total):
                    if status == sqlite3.SQLITE_OK and remaining and not written:
                        with closing(sqlite3.connect(database)) as writer:
                            writer.execute("INSERT INTO task_updates(task_id,user_id,body) VALUES(1,2,'During backup')")
                            writer.commit()
                        written.append(True)
                    original_progress(status, remaining, total)

                kwargs["progress"] = progress
                return super().backup(target, **kwargs)

        def concurrent_read(path, timeout=5):
            return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True,
                                   timeout=timeout, factory=ConcurrentConnection)

        with patch.object(backup, "_read_only", side_effect=concurrent_read):
            backup.create_backup(self.output)
        self.assertEqual(written, [True])
        self.assertEqual(self.snapshot(self.output), self.snapshot(self.database))

    def test_missing_source_is_not_created(self):
        missing = self.directory / "missing.db"
        with self.assertRaises(FileNotFoundError):
            backup.create_backup(self.output, missing)
        self.assertFalse(missing.exists())
        self.assertFalse(self.output.exists())

    def test_existing_destination_and_source_cannot_be_overwritten(self):
        before = self.database.read_bytes()
        for operation in (backup.create_backup, lambda output: backup.restore_backup(self.database, output)):
            with self.assertRaises(backup.BackupError):
                operation(self.database)
        self.assertEqual(self.database.read_bytes(), before)
        self.output.write_bytes(b"previous backup")
        with self.assertRaises(backup.BackupError):
            backup.create_backup(self.output)
        self.assertEqual(self.output.read_bytes(), b"previous backup")

    def test_restore_cannot_create_missing_configured_working_database(self):
        active = self.directory / "missing-active.db"
        with patch.dict(os.environ, M_FLOW_DATABASE=str(active)):
            with self.assertRaises(backup.BackupError):
                backup.restore_backup(self.database, active)
        self.assertFalse(active.exists())

    def test_repository_and_public_destinations_are_rejected(self):
        for destination in (ROOT / "backup.sqlite3", ROOT / "static" / "backup.sqlite3"):
            with self.assertRaises(backup.BackupError):
                backup.create_backup(destination)
            self.assertFalse(destination.exists())
        for name in ("public", "other-repository"):
            folder = self.directory / name
            folder.mkdir(mode=0o700)
            if name == "other-repository":
                (folder / ".git").mkdir()
            with self.assertRaises(backup.BackupError):
                backup.create_backup(folder / "snapshot.sqlite3")

    def test_symlink_into_repository_is_rejected(self):
        link = self.directory / "repo-link"
        try:
            link.symlink_to(ROOT, target_is_directory=True)
        except OSError:
            self.skipTest("Creating symlinks requires extra Windows permissions")
        with self.assertRaises(backup.BackupError):
            backup.create_backup(link / "backup.sqlite3")

    @unittest.skipUnless(os.name == "posix", "POSIX permissions checked on Linux")
    def test_private_permissions_are_enforced(self):
        unsafe = self.directory / "shared"
        unsafe.mkdir(mode=0o755)
        with self.assertRaises(backup.BackupError):
            backup.create_backup(unsafe / "snapshot.sqlite3")
        backup.create_backup(self.output)
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)

    def test_invalid_database_is_not_published_and_temporary_files_are_removed(self):
        invalid = self.directory / "invalid.db"
        invalid.write_bytes(b"private invalid database content")
        with self.assertRaises(sqlite3.DatabaseError):
            backup.create_backup(self.output, invalid)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.directory.glob(".mflow-snapshot-*")))

    def test_incomplete_schema_and_broken_relations_are_rejected_without_repair(self):
        with closing(sqlite3.connect(self.database)) as conn:
            conn.execute("UPDATE tasks SET assignee_id=999")
            conn.commit()
        before = self.snapshot(self.database)
        with self.assertRaises(backup.BackupError):
            backup.create_backup(self.output)
        self.assertEqual(self.snapshot(self.database), before)
        self.assertFalse(self.output.exists())
        empty = self.directory / "empty.db"
        with closing(sqlite3.connect(empty)) as conn:
            conn.execute("CREATE TABLE something(id INTEGER)")
        with self.assertRaises(backup.BackupError):
            backup.create_backup(self.output, empty)

    def test_timeout_does_not_leave_partial_backup(self):
        with patch.object(backup.time, "monotonic", side_effect=[0, 2]):
            with self.assertRaises(backup.BackupError):
                backup.create_backup(self.output, timeout=1)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.directory.glob(".mflow-snapshot-*")))

    def test_invalid_timeouts_are_rejected(self):
        for timeout in (0, -1, float("nan"), float("inf")):
            with self.subTest(timeout=timeout), self.assertRaises(backup.BackupError):
                backup.create_backup(self.output, timeout=timeout)
        self.assertFalse(self.output.exists())

    def test_publish_race_does_not_replace_another_file(self):
        def competing_publish(source, target):
            target.write_bytes(b"other process")
            raise FileExistsError()
        with patch.object(backup.os, "link", side_effect=competing_publish):
            with self.assertRaises(FileExistsError):
                backup.create_backup(self.output)
        self.assertEqual(self.output.read_bytes(), b"other process")
        self.assertFalse(list(self.directory.glob(".mflow-snapshot-*")))

    def test_cli_success_and_errors_do_not_log_records_or_paths(self):
        for arguments, expected in (
            (["backup", "--output", str(self.output)], 0),
            (["verify", "--source", str(self.output)], 0),
            (["restore", "--source", str(self.output), "--output", str(self.database)], 1),
            (["verify", "--source", str(self.directory / "missing.db")], 1),
        ):
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(backup.main(arguments), expected)
            logged = stdout.getvalue() + stderr.getvalue()
            for private in ("existing-private-hash", "Закрытый отчёт", "Закрытое примечание", str(self.directory)):
                self.assertNotIn(private, logged)

    def test_cli_does_not_import_app_or_require_its_secret(self):
        env = dict(os.environ)
        env.pop("M_FLOW_SECRET_KEY", None)
        result = subprocess.run([sys.executable, "database_backup.py", "backup", "--output", str(self.output)],
                                cwd=ROOT, env=env, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(self.output), self.snapshot(self.database))

    def test_database_path_matches_application_without_changing_it(self):
        self.assertEqual(backup.configured_database(), Path(self.module.DATABASE_PATH))
        with patch.dict(os.environ):
            os.environ.pop("M_FLOW_DATABASE", None)
            self.assertEqual(backup.configured_database(), ROOT / "database.db")
        with patch.dict(os.environ, M_FLOW_DATABASE="relative.db"):
            self.assertEqual(backup.configured_database(), Path("relative.db"))

    def test_backup_files_are_ignored_by_git(self):
        paths = ["database.db", "data.sqlite3-wal", "data.sqlite3-shm", "private.bak",
                 "private.backup", "private.dump", "backups/data.zip", "private-backups/data.json",
                 "restore-check/data.sql", ".mflow-snapshot-test.sqlite3"]
        result = subprocess.run(["git", "check-ignore", "--stdin", "-z"], input=("\0".join(paths) + "\0").encode(),
                                cwd=ROOT, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(set(result.stdout.decode().rstrip("\0").split("\0")), set(paths))


if __name__ == "__main__":
    unittest.main()
