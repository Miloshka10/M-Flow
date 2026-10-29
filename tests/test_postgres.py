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
TABLES = ("project_defense_plans", "work_history", "work_history_sources", "learning_group_invitations", "learning_group_members", "learning_groups", "supervision_requests", "teacher_invitations", "notifications", "project_defenses", "team_member_roles", "team_invitations", "student_profiles",
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

    def test_concurrent_teacher_change_and_submission_use_same_project_lock(self):
        from project_stages import change_stage
        from teacher_supervision import respond_request
        from threading import Barrier
        code = self.supervision_code()
        self.assertEqual(self.supervision_request(code).status_code, 302)
        self.assertEqual(self.supervision_reply(2).status_code, 302)
        row = self.supervision_row()
        barrier = Barrier(2)

        def action(which):
            with closing(self.module.get_db()) as conn:
                barrier.wait(timeout=10)
                try:
                    if which == 'submit':
                        change_stage(conn, 2, {'id':3,'role':'student'}, dict(number='1',revision='0',action='submit',result='Concurrent submission'))
                    else:
                        respond_request(conn, {'id':1,'role':'teacher'}, dict(request_id=str(row['id']),revision=str(row['revision']),action='approve'))
                    return 'ok'
                except ValueError:
                    return 'blocked'
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(action, ('submit','change')))
        self.assertEqual(outcomes[0], 'ok')
        with closing(self.module.get_db()) as conn:
            completed = conn.execute('SELECT state FROM supervision_requests WHERE id=?', (row['id'],)).fetchone()[0] == 'completed'
            expected_teacher = 2 if completed else 1
            recipients = [r[0] for r in conn.execute("SELECT recipient_id FROM notifications WHERE event_key='stage:2:1:1'")]
            self.assertEqual(recipients, [expected_teacher])
            self.assertEqual(outcomes[1], 'ok' if completed else 'blocked')

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
        self.assertEqual([row['recipient_id'] for row in self.notification_rows()], [1])
        with closing(self.module.get_db()) as conn:
            events = list(conn.execute("SELECT event,attempt,revision FROM work_history WHERE project_id=2"))
            self.assertEqual([tuple(row) for row in events], [('submit', 1, 1)])

    def test_concurrent_group_join_is_idempotent_and_capacity_is_enforced(self):
        import learning_groups
        gid = self.group_create(teacher=2)
        code = self.group_code(gid, teacher=2)
        def join(uid, invite):
            with closing(self.module.get_db()) as conn:
                try:
                    return learning_groups.join_group(conn, dict(id=uid, role='student'), invite)
                except ValueError:
                    return 'full'
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(lambda _: join(3, code), range(2))), [gid, gid])
        with closing(self.module.get_db()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM learning_group_members WHERE group_id=?', (gid,)).fetchone()[0], 1)
        self.add_candidate()
        other = self.group_create(teacher=2)
        other_code = self.group_code(other, teacher=2)
        with patch('learning_groups.MAX_MEMBERS', 1), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda uid: join(uid, other_code), (3, 4)))
        self.assertEqual(results.count('full'), 1)
        self.assertEqual(results.count(other), 1)
        self.sign_in(2)
        self.assertEqual(self.client.get('/project/2').status_code, 403)

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
        self.assertEqual([row['recipient_id'] for row in self.notification_rows()], [1])

    def test_concurrent_report_retry_creates_one_report_and_notification(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(lambda _: self.module.add_task_update(1, 1, 3, 'Одинаковый отчёт'), range(2))),
                             [True, True])
        with closing(self.module.get_db()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM task_updates WHERE body='Одинаковый отчёт'").fetchone()[0], 1)
        self.assertEqual(len(self.notification_rows()), 1)

    def test_concurrent_assessment_retry_creates_one_notification(self):
        token = self.module.new_session(self.module.get_db, self.module.SECRET_KEY, 1)
        def publish(_):
            client = self.module.app.test_client()
            with client.session_transaction() as session:
                session['user_id'] = 1
                session['auth_token'] = token
                session['csrf_token'] = 'test-form-token'
            data = self.assessment_data()
            page = client.get('/project/2/assessment?student_id=3').text
            import re
            data['assessment_version'] = re.search(r'name="assessment_version" value="([^"]+)"', page).group(1)
            return client.post('/project/2/assessment', data=data).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(publish, range(2))), [302, 302])
        self.assertEqual([row['recipient_id'] for row in self.notification_rows()], [3])


if __name__ == "__main__":
    unittest.main()
