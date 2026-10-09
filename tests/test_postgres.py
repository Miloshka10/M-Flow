"""The full role/CSRF suite on a disposable LOCAL PostgreSQL schema only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import uuid

import psycopg
from psycopg import sql
from werkzeug.security import generate_password_hash

import test_teacher_cabinet as fixtures
import database


TEST_URL = os.environ.get("M_FLOW_TEST_POSTGRES_URL", "")
TABLES = ("project_defenses", "team_member_roles", "team_invitations", "student_profiles",
          "project_stages", "project_assessments", "task_updates", "tasks", "project_members", "projects", "users")


@unittest.skipUnless(TEST_URL, "Set M_FLOW_TEST_POSTGRES_URL for a disposable local PostgreSQL server")
class PostgresCabinetTests(fixtures.TeacherCabinetTests):
    @classmethod
    def setUpClass(cls):
        parsed = urlsplit(TEST_URL)
        if parsed.hostname not in ("127.0.0.1", "localhost", "::1") or parsed.path != "/mflow_test":
            raise RuntimeError("PostgreSQL tests refuse remote/production databases. Use localhost/mflow_test.")
        cls.schema = "mflow_test_" + uuid.uuid4().hex
        with psycopg.connect(TEST_URL, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(cls.schema)))
        query = dict(parse_qsl(parsed.query))
        query["options"] = "-csearch_path=" + cls.schema
        cls.test_url = urlunsplit(parsed._replace(query=urlencode(query)))
        cls.env = patch.dict(os.environ, {
            "DATABASE_URL": cls.test_url, "DATABASE_URL_UNPOOLED": cls.test_url,
            "M_FLOW_DATABASE_BACKEND": "postgres", "M_FLOW_DEBUG": "0",
            "M_FLOW_SECRET_KEY": "disposable-postgres-test-key",
            "M_FLOW_TEACHER_USER": "", "M_FLOW_TEACHER_PASSWORD": "",
        })
        cls.env.start()
        try:
            spec = importlib.util.spec_from_file_location("postgres_test_app", Path(__file__).resolve().parents[1] / "app.py")
            cls.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.module)
            cls.module.app.config.update(TESTING=True)
            cls.password = generate_password_hash("test-password")
        except Exception:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        # Only the randomly named schema created by this test class, never public.
        if not cls.schema.startswith("mflow_test_") or len(cls.schema) != 43:
            raise RuntimeError("Unexpected test schema; cleanup refused.")
        with psycopg.connect(TEST_URL, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(cls.schema)))

    def setUp(self):
        super().setUp()
        # Existing fixtures use explicit IDs; align PostgreSQL identity sequences.
        with closing(self.module.get_db()) as conn:
            for table in ("users", "projects", "tasks", "task_updates"):
                conn.execute(f"SELECT setval(pg_get_serial_sequence(?, 'id'), COALESCE(MAX(id),1), MAX(id) IS NOT NULL) FROM {table}", (table,))
            conn.commit()

    def database_snapshot(self):
        with closing(self.module.get_db()) as conn:
            return tuple((table, tuple(sorted((tuple(row) for row in conn.execute(f"SELECT * FROM {table}")), key=repr)))
                         for table in TABLES)

    def test_repeat_initialization_preserves_postgres_records(self):
        self.stage_post(3)
        self.sign_in(1)
        self.post('/project/1/assessment', data=self.assessment_data())
        before = self.database_snapshot()
        self.module.prepare_database()
        self.module.prepare_database()
        self.assertEqual(self.database_snapshot(), before)

    def test_failed_connection_returns_503_and_does_not_expose_secret(self):
        self.sign_in(3)
        with patch.object(self.module, "get_db", side_effect=database.DatabaseUnavailable("private connection detail")):
            response = self.client.get("/")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private connection detail", response.text)

    def test_concurrent_stage_submission_has_one_winner(self):
        from project_stages import change_stage
        form = dict(number="1", revision="0", action="submit", result="Параллельный результат")

        def submit():
            with closing(self.module.get_db()) as conn:
                try:
                    change_stage(conn, 2, dict(id=3, role="student"), form)
                    return "saved"
                except ValueError:
                    return "stale"

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(lambda _: submit(), range(2))), ["saved", "stale"])

    def test_concurrent_defense_submission_has_one_winner(self):
        from video_defense import change_defense
        form = dict(revision="0", action="submit", video_url="https://example.com/video", description="Защита")

        def submit():
            with closing(self.module.get_db()) as conn:
                try:
                    change_defense(conn, 2, dict(id=3, role="student"), form)
                    return "saved"
                except ValueError:
                    return "stale"

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(lambda _: submit(), range(2))), ["saved", "stale"])


if __name__ == "__main__":
    unittest.main()
