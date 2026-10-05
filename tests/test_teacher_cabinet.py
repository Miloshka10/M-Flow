import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from datetime import date, timedelta
from werkzeug.security import generate_password_hash


class TeacherCabinetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.previous_env = {key: os.environ.get(key) for key in (
            "M_FLOW_DEBUG", "M_FLOW_DATABASE", "M_FLOW_SECRET_KEY")}
        os.environ.update(M_FLOW_DEBUG="1", M_FLOW_SECRET_KEY="test-only-key",
                          M_FLOW_DATABASE=str(Path(cls.temp.name) / "test.db"))
        spec = importlib.util.spec_from_file_location(
            "teacher_test_app", Path(__file__).resolve().parents[1] / "app.py")
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.app.config.update(TESTING=True)
        cls.password = generate_password_hash("test-password")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()
        for key, value in cls.previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def setUp(self):
        conn = self.module.get_db()
        for table in ("task_updates", "tasks", "project_members", "projects", "users"):
            conn.execute(f"DELETE FROM {table}")
        conn.executemany("INSERT INTO users(id, username, password, role) VALUES(?, ?, ?, ?)", [
            (1, "teacher-one", self.password, "teacher"),
            (2, "teacher-two", self.password, "teacher"),
            (3, "student-one", self.password, "student")])
        conn.executemany("INSERT INTO projects(id, name, owner_id) VALUES(?, ?, ?)", [
            (1, "Owned project", 1), (2, "Observed project", 3), (3, "Private project", 2)])
        conn.executemany("INSERT INTO project_members(project_id, user_id) VALUES(?, ?)",
                         [(1, 1), (1, 3), (2, 1), (2, 3), (3, 2)])
        conn.executemany("INSERT INTO tasks(id, project_id, title, deadline, status, assignee_id) VALUES(?, ?, ?, ?, ?, ?)", [
            (1, 1, "Overdue task", (date.today() - timedelta(days=2)).isoformat(), "todo", 3),
            (2, 1, "Unassigned task", None, "progress", None),
            (3, 2, "Completed task", None, "done", 3)])
        conn.execute("INSERT INTO task_updates(task_id, user_id, body) VALUES(1, 3, ?)",
                     ("<script>alert('report')</script>",))
        conn.commit()
        conn.close()
        self.client = self.module.app.test_client()

    def sign_in(self, user_id):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id

    def test_access_is_limited_to_teacher_projects(self):
        self.sign_in(1)
        response = self.client.get("/teacher")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Owned project", response.text)
        self.assertIn("Observed project", response.text)
        self.assertNotIn("Private project", response.text)
        self.assertIn("&lt;script&gt;", response.text)
        with self.module.app.test_request_context():
            overview = self.module.build_teacher_overview({"id": 1})
        projects = {p["id"]: p for p in overview}
        self.assertEqual(projects[1]["counts"]["overdue"], 1)
        self.assertEqual(projects[1]["counts"]["unassigned"], 1)
        self.assertFalse(projects[2]["can_manage"])
        self.assertEqual(projects[2]["health"], "done")

    def test_filters_preserve_the_global_totals(self):
        self.sign_in(1)
        response = self.client.get("/teacher?focus=overdue&q=student-one")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Owned project", response.text)
        self.assertNotIn("Observed project", response.text)
        self.assertIn("Показано 1 из 2", response.text)
        self.assertIn("Ничего не найдено", self.client.get("/teacher?q=missing").text)

    def test_student_cannot_open_or_create_in_cabinet(self):
        self.sign_in(3)
        self.assertEqual(self.client.get("/teacher").status_code, 403)
        self.assertEqual(self.client.post("/teacher/projects", data={"name": "Denied"}).status_code, 403)

    def test_member_teacher_cannot_edit_observed_project(self):
        self.sign_in(1)
        response = self.client.post("/project/2/edit/3", data={"title": "Denied"})
        self.assertEqual(response.status_code, 403)

    def test_teacher_can_create_project_and_login_lands_in_cabinet(self):
        response = self.client.post("/login", data={"username": "teacher-one", "password": "test-password"})
        self.assertEqual(response.location, "/teacher")
        response = self.client.post("/teacher/projects", data={"name": "New project"})
        self.assertEqual(response.status_code, 302)
        conn = self.module.get_db()
        project = conn.execute("SELECT id, owner_id FROM projects WHERE name = 'New project'").fetchone()
        self.assertEqual(project["owner_id"], 1)
        self.assertIsNotNone(conn.execute("SELECT 1 FROM project_members WHERE project_id=? AND user_id=1", (project["id"],)).fetchone())
        conn.close()
        self.assertEqual(self.client.get(response.location).status_code, 200)


if __name__ == "__main__":
    unittest.main()
