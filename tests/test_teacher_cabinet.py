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
        for table in ("project_stages", "project_assessments", "task_updates", "tasks", "project_members", "projects", "users"):
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
            session["assessment_csrf"] = "test-form-token"
            session["stages_csrf"] = "test-stage-token"

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

    def stage_post(self, user_id, number=1, revision=0, action="submit", **fields):
        self.sign_in(user_id)
        data = dict(csrf_token="test-stage-token", number=str(number), revision=str(revision),
                    action=action, result="Результат этапа", **fields)
        return self.client.post("/project/1/stages", data=data)

    def stage_rows(self):
        conn = self.module.get_db()
        from project_stages import get_stages
        rows = get_stages(conn, 1)
        conn.close()
        return rows

    def test_five_stages_exist_without_changing_existing_project(self):
        self.sign_in(3)
        response = self.client.get("/project/1/stages")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text.count('class="stage-number"'), 5)
        self.assertIn("Выбор темы и согласование", response.text)
        self.assertIn("Подготовка документации", response.text)
        self.assertEqual([row["state"] for row in self.stage_rows()], ["todo"] * 5)
        conn = self.module.get_db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM project_stages").fetchone()[0], 0)
        conn.close()
        self.assertIn("/project/1/stages", self.client.get("/project/1").text)

    def test_auth_design_form_precedes_brand_and_preserves_controls(self):
        for path in ("/login", "/register", "/teacher/login", "/teacher/register"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.text.startswith('<!doctype html><html lang="ru">'))
            self.assertEqual(response.text.count('name="viewport"'), 1)
            self.assertEqual(response.text.count('/static/mflow-theme.css'), 1)
            self.assertLess(response.text.index('class="auth-form-side"'), response.text.index('class="auth-hero"'))
            self.assertIn('by Minich', response.text)
            self.assertIn('name="username"', response.text)
            self.assertIn('type="password"', response.text)
            self.assertIn('aria-label="Выбор роли"', response.text)
            if path.endswith("register"):
                self.assertIn('name="repeat_password"', response.text)

    def test_all_workspaces_use_same_stylesheet(self):
        self.sign_in(1)
        for path in ("/", "/teacher", "/profile", "/project/1", "/project/1/stages", "/project/1/assessment"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.text.count('/static/mflow-theme.css'), 1)
            self.assertEqual(response.text.count('name="viewport"'), 1)
            self.assertNotIn('@import', response.text)
            self.assertNotIn('<style>', response.text)

    def test_theme_supports_reduced_motion_and_accessible_focus(self):
        with self.client.get('/static/mflow-theme.css') as response:
            css = response.get_data(as_text=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn('prefers-reduced-motion: reduce', css)
            self.assertIn('forced-colors: active', css)
            self.assertIn(':focus-visible', css)
            self.assertIn('wordmark-shimmer', css)
            self.assertNotIn('#ffcc00', css)

    def test_all_five_stages_can_be_completed(self):
        for number in range(1, 6):
            fields = {"presentation_url": "https://example.com/slides"} if number == 5 else {}
            self.assertEqual(self.stage_post(3, number=number, **fields).status_code, 302)
            self.assertEqual(self.stage_post(1, number=number, revision=1, action="accept").status_code, 302)
        self.assertEqual([row["state"] for row in self.stage_rows()], ["done"] * 5)
        response = self.client.get("/project/1/stages")
        self.assertIn("Принято 5 из 5 этапов · 100%", response.text)
        self.assertIn("Открыть презентацию", response.text)
        self.assertEqual(self.stage_post(3, number=5, revision=2).status_code, 400)

    def test_stage_order_and_role_permissions(self):
        self.assertEqual(self.stage_post(3, number=2).status_code, 400)
        self.assertEqual(self.stage_post(1).status_code, 400)
        self.assertEqual(self.stage_post(3, action="accept").status_code, 400)
        self.assertEqual(self.stage_post(3).status_code, 302)
        self.assertEqual(self.stage_post(3, revision=1, action="save").status_code, 400)
        self.assertEqual(self.stage_post(3, revision=1, action="accept").status_code, 400)
        self.assertEqual(self.stage_post(3, number=2).status_code, 400)
        self.assertEqual(self.stage_rows()[0]["state"], "review")

    def test_return_to_revision_requires_comment(self):
        self.stage_post(3)
        self.assertEqual(self.stage_post(1, revision=1, action="return").status_code, 400)
        self.assertEqual(self.stage_post(1, revision=1, action="return", teacher_comment="Уточните тему").status_code, 302)
        self.assertEqual(self.stage_rows()[0]["state"], "progress")
        self.sign_in(3)
        self.assertIn("Уточните тему", self.client.get("/project/1/stages").text)
        self.assertEqual(self.stage_post(3, revision=2).status_code, 302)
        self.assertEqual(self.stage_post(1, revision=3, action="accept").status_code, 302)

    def test_stages_reject_stale_forms(self):
        self.assertEqual(self.stage_post(3, action="save").status_code, 302)
        self.assertEqual(self.stage_post(3, action="submit").status_code, 400)
        self.assertEqual(self.stage_rows()[0]["state"], "progress")
        self.assertEqual(self.stage_post(3, revision=1).status_code, 302)

    def test_stages_access_and_csrf(self):
        self.sign_in(2)
        self.assertEqual(self.client.get("/project/1/stages").status_code, 403)
        self.assertEqual(self.stage_post(2).status_code, 403)
        self.sign_in(3)
        self.assertEqual(self.client.get("/project/3/stages").status_code, 403)
        self.assertEqual(self.client.post("/project/1/stages", data={"action": "submit"}).status_code, 400)
        self.client.get("/logout")
        self.assertEqual(self.client.get("/project/1/stages").status_code, 302)

    def test_last_stage_requires_documentation_and_safe_presentation_url(self):
        for number in range(1, 5):
            self.stage_post(3, number=number)
            self.stage_post(1, number=number, revision=1, action="accept")
        self.assertEqual(self.stage_post(3, number=5).status_code, 400)
        for url in ("javascript:alert(1)", "https://", "https://[broken", "https://example.com/ bad"):
            self.assertEqual(self.stage_post(3, number=5, presentation_url=url).status_code, 400)
        self.sign_in(3)
        data = dict(csrf_token="test-stage-token", number="5", revision="0", action="submit",
                    result="", presentation_url="https://example.com/slides")
        self.assertEqual(self.client.post("/project/1/stages", data=data).status_code, 400)
        self.assertEqual(self.stage_rows()[4]["state"], "todo")

    def test_stage_content_is_escaped_and_draft_can_be_empty(self):
        self.sign_in(3)
        data = dict(csrf_token="test-stage-token", number="1", revision="0", action="save", result="")
        self.assertEqual(self.client.post("/project/1/stages", data=data).status_code, 302)
        data.update(revision="1", action="submit")
        self.assertEqual(self.client.post("/project/1/stages", data=data).status_code, 400)
        data["result"] = "<script>alert('topic')</script>"
        self.assertEqual(self.client.post("/project/1/stages", data=data).status_code, 302)
        self.sign_in(1)
        response = self.client.get("/project/1/stages")
        self.assertIn("&lt;script&gt;", response.text)
        self.assertNotIn("<script>alert", response.text)

    def test_stages_schema_creation_is_idempotent(self):
        self.stage_post(3)
        from project_stages import initialize_stages
        conn = self.module.get_db()
        initialize_stages(conn)
        initialize_stages(conn)
        conn.commit()
        conn.close()
        self.assertEqual(self.stage_rows()[0]["state"], "review")

    def assessment_data(self, action="published"):
        from grading import CRITERIA
        return dict(student_id="3", student_name="Иванов Иван", note="Хорошая работа", csrf_token="test-form-token",
                    action=action, **{f"criterion_{key}": str(maximum) for key, _, maximum in CRITERIA})

    def test_assessment_publication_and_student_visibility(self):
        self.sign_in(1)
        page = self.client.get("/project/1/assessment")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.text.count('<select name="criterion_'), 12)
        self.assertIn('name="csrf_token"', page.text)
        for asset in ("assessment.css", "assessment.js"):
            with self.client.get(f"/static/{asset}") as response:
                self.assertEqual(response.status_code, 200)
        response = self.client.post("/project/1/assessment", data=self.assessment_data())
        self.assertEqual(response.status_code, 302)
        self.sign_in(3)
        response = self.client.get("/project/1/assessment")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Итого: 45 / 45 · Оценка: 5", response.text)
        self.assertIn("Хорошая работа", response.text)
        self.assertNotIn('name="criterion_', response.text)
        self.assertEqual(self.client.post("/project/1/assessment", data=self.assessment_data()).status_code, 403)

    def test_draft_is_private_and_can_be_incomplete(self):
        self.sign_in(1)
        data = self.assessment_data("draft")
        data["criterion_1"] = ""
        data["criterion_2"] = "0"
        self.assertEqual(self.client.post("/project/1/assessment", data=data).status_code, 302)
        conn = self.module.get_db()
        row = conn.execute("SELECT * FROM project_assessments").fetchone()
        from grading import load_scores, summarize
        scores = load_scores(row)
        self.assertIsNone(scores["1"])
        self.assertEqual(scores["2"], 0)
        self.assertIsNone(summarize(scores)[1])
        conn.close()
        self.sign_in(3)
        response = self.client.get("/project/1/assessment")
        self.assertIn("Оценка ещё не опубликована", response.text)
        self.assertNotIn("Хорошая работа", response.text)

    def test_assessment_validation_does_not_write(self):
        self.sign_in(1)
        for bad in ("", "4", "-1", "1.5", "abc"):
            data = self.assessment_data()
            data["criterion_1"] = bad
            self.assertEqual(self.client.post("/project/1/assessment", data=data).status_code, 400)
        data = self.assessment_data()
        data["student_id"] = "2"
        self.assertEqual(self.client.post("/project/1/assessment", data=data).status_code, 400)
        conn = self.module.get_db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM project_assessments").fetchone()[0], 0)
        conn.close()

    def test_teacher_requires_project_access_but_can_grade_as_member(self):
        self.sign_in(2)
        self.assertEqual(self.client.get("/project/1/assessment").status_code, 403)
        self.assertEqual(self.client.post("/project/1/assessment", data=self.assessment_data()).status_code, 403)
        self.sign_in(1)
        self.assertEqual(self.client.post("/project/2/assessment", data=self.assessment_data()).status_code, 302)

    def test_student_cannot_view_another_students_assessment(self):
        conn = self.module.get_db()
        conn.execute("INSERT INTO users(id,username,password,role) VALUES(4,'student-two',?,'student')", (self.password,))
        conn.execute("INSERT INTO project_members(project_id,user_id) VALUES(1,4)")
        conn.commit()
        conn.close()
        self.sign_in(1)
        self.client.post("/project/1/assessment", data=self.assessment_data())
        self.sign_in(4)
        response = self.client.get("/project/1/assessment?student_id=3")
        self.assertIn("Оценка ещё не опубликована", response.text)
        self.assertNotIn("Иванов Иван", response.text)

    def test_assessment_update_escapes_content_and_does_not_duplicate(self):
        self.sign_in(1)
        data = self.assessment_data()
        self.client.post("/project/1/assessment", data=data)
        data["note"] = "<script>alert(1)</script>"
        data["criterion_1"] = "0"
        self.assertEqual(self.client.post("/project/1/assessment", data=data).status_code, 302)
        conn = self.module.get_db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM project_assessments").fetchone()[0], 1)
        conn.close()
        self.sign_in(3)
        response = self.client.get("/project/1/assessment")
        self.assertIn("&lt;script&gt;", response.text)
        self.assertNotIn("<script>alert", response.text)
        self.assertIn("Итого: 42 / 45", response.text)

    def test_rubric_grade_boundaries(self):
        from grading import CRITERIA, MAX_SCORE, summarize
        self.assertEqual(MAX_SCORE, 45)
        for total, expected in ((0, 2), (20, 2), (21, 3), (29, 3), (30, 4), (38, 4), (39, 5), (45, 5)):
            remaining = total
            scores = {}
            for key, _, maximum in CRITERIA:
                scores[key] = min(remaining, maximum)
                remaining -= scores[key]
            self.assertEqual(summarize(scores), (total, expected))

    def test_assessment_rejects_post_without_form_token(self):
        self.sign_in(1)
        data = self.assessment_data()
        del data["csrf_token"]
        self.assertEqual(self.client.post("/project/1/assessment", data=data).status_code, 400)
        data["csrf_token"] = "неверный токен"
        self.assertEqual(self.client.post("/project/1/assessment", data=data).status_code, 400)
        conn = self.module.get_db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM project_assessments").fetchone()[0], 0)
        conn.close()

    def test_assessments_by_different_teachers_are_independent(self):
        conn = self.module.get_db()
        conn.execute("INSERT INTO project_members(project_id,user_id) VALUES(1,2)")
        conn.commit()
        conn.close()
        self.sign_in(1)
        self.client.post("/project/1/assessment", data=self.assessment_data())
        self.sign_in(2)
        data = self.assessment_data("draft")
        data["note"] = "Приватный черновик второго учителя"
        self.client.post("/project/1/assessment", data=data)
        conn = self.module.get_db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM project_assessments").fetchone()[0], 2)
        conn.close()
        self.sign_in(1)
        self.assertNotIn("Приватный черновик второго учителя", self.client.get("/project/1/assessment").text)
        self.sign_in(3)
        response = self.client.get("/project/1/assessment")
        self.assertIn("teacher-one", response.text)
        self.assertNotIn("teacher-two", response.text)
        self.assertNotIn("Приватный черновик", response.text)

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
        response = self.client.post("/teacher/login", data={"username": "teacher-one", "password": "test-password"})
        self.assertEqual(response.location, "/teacher")
        response = self.client.post("/teacher/projects", data={"name": "New project"})
        self.assertEqual(response.status_code, 302)
        conn = self.module.get_db()
        project = conn.execute("SELECT id, owner_id FROM projects WHERE name = 'New project'").fetchone()
        self.assertEqual(project["owner_id"], 1)
        self.assertIsNotNone(conn.execute("SELECT 1 FROM project_members WHERE project_id=? AND user_id=1", (project["id"],)).fetchone())
        conn.close()
        self.assertEqual(self.client.get(response.location).status_code, 200)

    def test_teacher_registration_supports_custom_login(self):
        response = self.client.post("/teacher/register", data={
            "username": "my-new-teacher", "password": "my-password-123",
            "repeat_password": "my-password-123"})
        self.assertEqual(response.location, "/teacher")
        self.assertEqual(self.client.get("/teacher").status_code, 200)
        conn = self.module.get_db()
        user = conn.execute("SELECT role FROM users WHERE username=?", ("my-new-teacher",)).fetchone()
        self.assertEqual(user["role"], "teacher")
        conn.close()
        self.assertEqual(self.client.get("/logout").location, "/teacher/login")
        response = self.client.post("/teacher/login", data={
            "username": "my-new-teacher", "password": "my-password-123"})
        self.assertEqual(response.location, "/teacher")

    def test_registration_role_is_selected_by_route(self):
        response = self.client.post("/register", data={
            "username": "new-student", "password": "my-password-123",
            "repeat_password": "my-password-123", "role": "teacher"})
        self.assertEqual(response.location, "/")
        self.assertEqual(self.client.get("/teacher").status_code, 403)
        conn = self.module.get_db()
        self.assertEqual(conn.execute("SELECT role FROM users WHERE username='new-student'").fetchone()[0], "student")
        conn.close()

    def test_wrong_login_portal_does_not_sign_in(self):
        for path, username in (("/login", "teacher-one"), ("/teacher/login", "student-one")):
            response = self.client.post(path, data={"username": username, "password": "test-password"})
            self.assertEqual(response.status_code, 200)
            self.assertIn("Это аккаунт", response.text)
            with self.client.session_transaction() as session:
                self.assertNotIn("user_id", session)

    def test_teacher_registration_cannot_replace_existing_student(self):
        response = self.client.post("/teacher/register", data={
            "username": "student-one", "password": "my-password-123",
            "repeat_password": "my-password-123"})
        self.assertIn("Такой логин уже занят", response.text)
        conn = self.module.get_db()
        self.assertEqual(conn.execute("SELECT role FROM users WHERE id=3").fetchone()[0], "student")
        conn.close()

    def test_mismatched_passwords_do_not_create_account(self):
        response = self.client.post("/teacher/register", data={
            "username": "mismatch", "password": "my-password-123", "repeat_password": "different-123"})
        self.assertIn("Пароли не совпадают", response.text)
        conn = self.module.get_db()
        self.assertIsNone(conn.execute("SELECT id FROM users WHERE username='mismatch'").fetchone())
        conn.close()

    def test_anonymous_teacher_is_sent_to_teacher_login(self):
        self.assertEqual(self.client.get("/teacher").location, "/teacher/login")
        for path in ("/login", "/register", "/teacher/login", "/teacher/register"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn("Я учитель", response.text)
            self.assertIn("Я ученик", response.text)


if __name__ == "__main__":
    unittest.main()
