import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys
import sqlite3
from datetime import date, timedelta
from werkzeug.security import generate_password_hash
from unittest.mock import patch
from html.parser import HTMLParser


class FormTokens(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.forms, self.current, self.meta = [], None, None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta' and attrs.get('name') == 'csrf-token':
            self.meta = attrs.get('content')
        if tag == 'form':
            self.current = dict(method=attrs.get('method', 'get').lower(),
                                action=attrs.get('action'), tokens=[])
            self.forms.append(self.current)
        if tag == 'input' and self.current is not None and attrs.get('name') == 'csrf_token':
            self.current['tokens'].append(attrs.get('value'))

    def handle_endtag(self, tag):
        if tag == 'form':
            self.current = None


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
        for table in ("project_defenses", "team_member_roles", "team_invitations", "student_profiles", "project_stages", "project_assessments", "task_updates", "tasks", "project_members", "projects", "users"):
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
            session["csrf_token"] = "test-form-token"

    def post(self, path, **kwargs):
        """Functional tests send the same token as a real form/AJAX client.

        CSRF rejection tests deliberately use self.client.post directly.
        """
        with self.client.session_transaction() as session:
            token = session.get('csrf_token')
        if token is None:
            self.client.get('/login')
            with self.client.session_transaction() as session:
                token = session['csrf_token']
        if 'json' in kwargs:
            kwargs.setdefault('headers', {})['X-CSRF-Token'] = token
        else:
            kwargs.setdefault('data', {}).setdefault('csrf_token', token)
        return self.client.post(path, **kwargs)

    def count_page_queries(self, path):
        statements = []
        original = self.module.get_db

        def traced_db():
            conn = original()
            conn.set_trace_callback(lambda sql: statements.append(sql)
                                    if sql.lstrip().upper().startswith(('SELECT', 'WITH')) else None)
            return conn

        with patch.object(self.module, 'get_db', traced_db):
            response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        return len(statements)

    def database_snapshot(self):
        conn = self.module.get_db()
        try:
            return tuple(conn.iterdump())
        finally:
            conn.close()

    def test_every_post_route_rejects_missing_and_invalid_csrf_without_mutation(self):
        adapter = self.module.app.url_map.bind('localhost')
        routes = [rule for rule in self.module.app.url_map.iter_rules() if 'POST' in rule.methods]
        self.assertGreaterEqual(len(routes), 20)
        for user_id in (1, 3):
            self.sign_in(user_id)
            before = self.database_snapshot()
            for rule in routes:
                path = adapter.build(rule.endpoint, {arg: 1 for arg in rule.arguments})
                for token in (None, '', 'wrong', 'неверный', 'x' * 129):
                    with self.subTest(user=user_id, path=path, token=token):
                        if path.endswith(('/status', '/priority')):
                            headers = {} if token is None else {'X-CSRF-Token': token}
                            response = self.client.post(path, json={'task_id': 1, 'status': 'done', 'priority': 'high'}, headers=headers)
                            self.assertEqual(response.json['code'], 'csrf_failed')
                        else:
                            data = {} if token is None else {'csrf_token': token}
                            response = self.client.post(path, data=data)
                            self.assertIn('Обновите страницу', response.text)
                        self.assertEqual(response.status_code, 400)
            self.assertEqual(self.database_snapshot(), before)
            with self.client.session_transaction() as session:
                self.assertEqual(session['user_id'], user_id)

    def test_all_rendered_post_forms_have_one_shared_token(self):
        paths = ('/', '/teacher', '/project/1', '/profile', '/settings', '/skills', '/teams',
                 '/project/1/team', '/project/1/defense', '/project/1/stages', '/project/1/assessment')
        for user_id in (1, 3):
            self.sign_in(user_id)
            for path in paths:
                page = self.client.get(path)
                if page.status_code == 403:
                    continue
                with self.subTest(user=user_id, path=path):
                    self.assertEqual(page.status_code, 200)
                    parsed = FormTokens(page.text)
                    self.assertEqual(parsed.meta, 'test-form-token')
                    self.assertNotIn('href="/logout"', page.text)
                    for form in parsed.forms:
                        self.assertEqual(form['tokens'], [parsed.meta] if form['method'] == 'post' else [])
                    if path != '/profile':  # Profile has a back link, not a header.
                        self.assertTrue(any(f['action'] == '/logout' for f in parsed.forms))
        for path in ('/login', '/register', '/teacher/login', '/teacher/register'):
            page = FormTokens(self.client.get(path).text)
            self.assertTrue(page.meta)
            self.assertEqual(page.forms[0]['tokens'], [page.meta])

    def test_logout_is_post_only_and_invalid_tokens_leave_session_active(self):
        self.sign_in(1)
        self.assertEqual(self.client.get('/logout').status_code, 405)
        self.assertEqual(self.client.post('/logout').status_code, 400)
        self.assertEqual(self.client.post('/logout', data={'csrf_token': 'wrong'}).status_code, 400)
        with self.client.session_transaction() as session:
            self.assertEqual(session['user_id'], 1)
        self.assertEqual(self.post('/logout').location, '/teacher/login')
        with self.client.session_transaction() as session:
            self.assertNotIn('user_id', session)
            self.assertNotIn('csrf_token', session)

    def test_csrf_token_is_bound_to_session_and_rotated_on_login(self):
        first = self.module.app.test_client()
        second = self.module.app.test_client()
        token = FormTokens(first.get('/login').text).meta
        other = FormTokens(second.get('/login').text).meta
        self.assertNotEqual(token, other)
        payload = dict(username='student-one', password='test-password')
        self.assertEqual(second.post('/login', data=dict(payload, csrf_token=token)).status_code, 400)
        response = first.post('/login', data=dict(payload, csrf_token=token))
        self.assertEqual(response.location, '/')
        fresh = FormTokens(first.get('/').text).meta
        self.assertNotEqual(token, fresh)
        before = self.database_snapshot()
        self.assertEqual(first.post('/project/1/status', json={'task_id': 1, 'status': 'done'},
                                    headers={'X-CSRF-Token': token}).status_code, 400)
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(first.post('/project/1/status', json={'task_id': 1, 'status': 'done'},
                                    headers={'X-CSRF-Token': fresh}).status_code, 200)
        with first.session_transaction() as session:
            session.clear()
        expired = first.post('/project/1/status', json={'task_id': 1, 'status': 'todo'}, headers={'X-CSRF-Token': fresh})
        self.assertEqual(expired.status_code, 401)
        self.assertIn('Сессия завершена', expired.json['error'])

    def test_anonymous_auth_posts_also_require_csrf(self):
        before = self.database_snapshot()
        for path in ('/login', '/teacher/login', '/register', '/teacher/register'):
            for token in (None, 'wrong'):
                client = self.module.app.test_client()
                data = dict(username='unwanted-account', password='test-password', repeat_password='test-password')
                if token is not None:
                    data['csrf_token'] = token
                self.assertEqual(client.post(path, data=data).status_code, 400)
                with client.session_transaction() as session:
                    self.assertNotIn('user_id', session)
        self.assertEqual(self.database_snapshot(), before)

    def test_teacher_cannot_manage_or_submit_student_work_even_for_legacy_owned_project(self):
        self.sign_in(1)
        before = self.database_snapshot()
        for path in ('/add_project', '/teacher/projects', '/project/1/add', '/project/1/delete/1',
                     '/project/1/edit/1', '/project/1/add_member', '/project/1/remove_member/3',
                     '/project/1/task/1/update', '/project/1/team', '/teams/invite'):
            with self.subTest(path=path):
                self.assertEqual(self.post(path, data={'title': 'Denied', 'action': 'role'}).status_code, 403)
        for endpoint, value in (('status', 'done'), ('priority', 'high')):
            self.assertEqual(self.post('/project/1/' + endpoint, json={'task_id': 1, endpoint: value}).status_code, 403)
        self.assertEqual(self.stage_post(1, action='submit').status_code, 400)
        self.assertEqual(self.defense_post(user_id=1, action='submit').status_code, 400)
        self.assertEqual(self.database_snapshot(), before)
        page = self.client.get('/project/1?mine=1').text
        self.assertIn('Overdue task', page)
        self.assertNotIn('Только мои', page)
        self.assertNotIn('id="task-status-', page)
        self.assertNotIn('Добавить задачу', page)
        self.assertIn('Прогресс учеников', page)

    def test_legacy_transfer_is_explicit_atomic_and_preserves_results(self):
        self.sign_in(1)
        conn = self.module.get_db()
        tasks = [tuple(r) for r in conn.execute('SELECT * FROM tasks ORDER BY id')]
        reports = [tuple(r) for r in conn.execute('SELECT * FROM task_updates ORDER BY id')]
        conn.close()
        before = self.database_snapshot()
        self.client.get('/teacher')
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(self.client.post('/project/1/transfer', data={'student_id': '3'}).status_code, 400)
        for fields in ({'student_id': '2'}, {'student_id': str(2**80)}, {'student_id': '3', 'student_username': 'student-one'}, {'student_username': 'missing'}):
            self.assertEqual(self.post('/project/1/transfer', data=fields).status_code, 400)
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(self.post('/project/1/transfer', data={'student_id': '3'}).status_code, 302)
        conn = self.module.get_db()
        self.assertEqual(conn.execute('SELECT owner_id FROM projects WHERE id=1').fetchone()[0], 3)
        self.assertEqual([tuple(r) for r in conn.execute('SELECT * FROM tasks ORDER BY id')], tasks)
        self.assertEqual([tuple(r) for r in conn.execute('SELECT * FROM task_updates ORDER BY id')], reports)
        conn.close()
        self.assertIn('Owned project', self.client.get('/teacher').text)
        self.assertEqual(self.post('/project/1/transfer', data={'student_id': '3'}).status_code, 403)
        self.sign_in(2)
        self.assertEqual(self.post('/project/1/transfer', data={'student_id': '3'}).status_code, 403)
        self.sign_in(3)
        self.assertEqual(self.post('/project/1/transfer', data={'student_id': '3'}).status_code, 403)
        self.assertIn('Добавить задачу', self.client.get('/project/1').text)

    def test_legacy_project_without_students_can_be_transferred_by_login(self):
        self.sign_in(2)
        self.assertEqual(self.post('/project/3/transfer', data={'student_username': 'student-one'}).status_code, 302)
        self.assertIn('Private project', self.client.get('/teacher').text)
        self.sign_in(3)
        self.assertIn('Private project', self.client.get('/').text)

    def test_student_can_draft_without_teacher_but_cannot_submit_until_teacher_joins(self):
        conn = self.module.get_db()
        conn.execute('DELETE FROM project_members WHERE project_id=2 AND user_id=1')
        conn.commit()
        conn.close()
        self.sign_in(3)
        for path in ('/project/2/stages', '/project/2/defense'):
            self.assertIn('В проекте нет учителя', self.client.get(path).text)
        stage = dict(number='1', revision='0', action='submit', result='Тема')
        defense = dict(revision='0', action='submit', video_url='https://example.com/video')
        self.assertEqual(self.post('/project/2/stages', data=dict(stage)).status_code, 400)
        self.assertEqual(self.post('/project/2/defense', data=dict(defense)).status_code, 400)
        self.assertEqual(self.post('/project/2/stages', data=dict(stage, action='save')).status_code, 302)
        self.assertEqual(self.post('/project/2/defense', data=dict(defense, action='save')).status_code, 302)
        self.assertEqual(self.post('/project/2/add_member', data={'username': 'teacher-one'}).status_code, 302)
        self.assertEqual(self.post('/project/2/stages', data=dict(stage, revision='1')).status_code, 302)
        self.assertEqual(self.post('/project/2/defense', data=dict(defense, revision='1')).status_code, 302)
        self.sign_in(1)
        page = self.client.get('/teacher?focus=attention').text
        self.assertIn('Этап ожидает проверки', page)
        self.assertIn('Видеозащита ожидает проверки', page)
        # Completing tasks is not the same as passing project review.
        self.assertIn('Observed project', self.client.get('/teacher?focus=done').text)

    def test_removal_cancels_pending_invitation_and_does_not_restore_old_role(self):
        self.add_candidate()
        self.invitation_post(project_id=2)
        self.sign_in(3)
        self.post('/project/2/add_member', data={'username': 'candidate-engineer'})
        self.post('/project/2/team', data={'action': 'role', 'student_id': '4', 'role_text': 'Инженер'})
        self.assertEqual(self.post('/project/2/remove_member/4').status_code, 302)
        conn = self.module.get_db()
        self.assertIsNone(conn.execute('SELECT * FROM team_member_roles WHERE project_id=2 AND user_id=4').fetchone())
        self.assertEqual(conn.execute('SELECT state FROM team_invitations WHERE project_id=2 AND invitee_id=4').fetchone()[0], 'cancelled')
        conn.close()
        self.assertEqual(self.invitation_answer(4).status_code, 400)
        self.sign_in(3)
        self.post('/project/2/add_member', data={'username': 'candidate-engineer'})
        self.assertNotIn('Роль: Инженер', self.client.get('/project/2/team').text)

    def test_valid_csrf_does_not_grant_access_to_foreign_tasks(self):
        self.sign_in(3)
        before = self.database_snapshot()
        for path, data in (('/project/1/delete/2', {}), ('/project/1/add_member', {'username': 'teacher-two'}),
                           ('/project/3/add', {'title': 'Denied'}), ('/project/1/task/2/update', {'body': 'Denied'})):
            self.assertEqual(self.post(path, data=data).status_code, 403)
        self.assertEqual(self.post('/project/1/priority', json={'task_id': 1, 'priority': 'high'}).status_code, 403)
        self.assertEqual(self.post('/project/1/status', json={'task_id': 2, 'status': 'done'}).status_code, 403)
        self.assertEqual(self.database_snapshot(), before)

    def test_successful_password_change_rotates_csrf_and_login_still_works(self):
        self.sign_in(3)
        response = self.post('/profile', data=dict(old_password='test-password', new_password='new-test-password', repeat_password='new-test-password'))
        self.assertIn('Пароль изменён', response.text)
        token = FormTokens(response.text).meta
        self.assertNotEqual(token, 'test-form-token')
        self.assertEqual(self.client.post('/logout', data={'csrf_token': 'test-form-token'}).status_code, 400)
        self.assertEqual(self.client.post('/logout', data={'csrf_token': token}).status_code, 302)
        self.assertEqual(self.post('/login', data=dict(username='student-one', password='new-test-password')).location, '/')

    def test_token_pages_are_not_cached_but_static_assets_keep_normal_caching(self):
        self.assertEqual(self.client.get('/login').headers['Cache-Control'], 'no-store')
        self.sign_in(1)
        self.assertEqual(self.client.get('/teacher').headers['Cache-Control'], 'no-store')
        with self.client.get('/static/kanban.js') as response:
            self.assertNotIn('no-store', response.headers.get('Cache-Control', ''))

    def test_legacy_mutation_scenario_accepts_valid_form_and_ajax_tokens(self):
        self.sign_in(3)
        created = self.post('/add_project', data={'name': 'Protected project'})
        self.assertEqual(created.status_code, 302)
        conn = self.module.get_db()
        pid = conn.execute("SELECT id FROM projects WHERE name='Protected project'").fetchone()[0]
        conn.close()
        base = f'/project/{pid}'
        self.assertEqual(self.post(base + '/add_member', data={'username': 'teacher-two'}).status_code, 302)
        self.assertEqual(self.post(base + '/add', data={'title': 'Protected task', 'assignee_id': '3'}).status_code, 302)
        conn = self.module.get_db()
        task_id = conn.execute("SELECT id FROM tasks WHERE title='Protected task'").fetchone()[0]
        conn.close()
        self.assertEqual(self.post(base + f'/edit/{task_id}', data={'title': 'Updated task', 'assignee_id': '3'}).status_code, 302)
        self.assertEqual(self.post(base + '/priority', json={'task_id': task_id, 'priority': 'high'}).status_code, 200)
        self.sign_in(3)
        self.assertEqual(self.post(base + '/status', json={'task_id': task_id, 'status': 'done'}).status_code, 200)
        self.assertEqual(self.post(base + f'/task/{task_id}/update', data={'body': 'Protected report'}).status_code, 302)
        conn = self.module.get_db()
        row = conn.execute('SELECT title,status,priority FROM tasks WHERE id=?', (task_id,)).fetchone()
        self.assertEqual(tuple(row), ('Updated task', 'done', 'high'))
        self.assertEqual(conn.execute('SELECT body FROM task_updates WHERE task_id=?', (task_id,)).fetchone()[0], 'Protected report')
        conn.close()
        self.sign_in(3)
        self.assertEqual(self.post(base + '/remove_member/2').status_code, 302)
        self.assertEqual(self.post(base + f'/delete/{task_id}').status_code, 302)
        conn = self.module.get_db()
        self.assertIsNone(conn.execute('SELECT id FROM tasks WHERE id=?', (task_id,)).fetchone())
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM task_updates WHERE task_id=?', (task_id,)).fetchone()[0], 0)
        conn.close()

    def test_page_query_counts_do_not_grow_with_projects_or_cards(self):
        self.sign_in(1)
        paths = ('/', '/teacher', '/project/1')
        before = {path: self.count_page_queries(path) for path in paths}
        conn = self.module.get_db()
        for pid in range(10, 30):
            conn.execute('INSERT INTO projects(id,name,owner_id) VALUES(?,?,1)', (pid, f'Project {pid}'))
            conn.executemany('INSERT INTO project_members VALUES(?,?)', [(pid, 1), (pid, 3)])
            conn.executemany('INSERT INTO tasks(project_id,title,assignee_id) VALUES(?,?,3)',
                             [(pid, f'Task {n}') for n in range(20)])
        conn.executemany('INSERT INTO tasks(project_id,title,assignee_id) VALUES(1,?,3)',
                         [(f'Card {n}',) for n in range(30)])
        conn.commit()
        conn.close()
        for path, budget in zip(paths, (7, 7, 8)):
            with self.subTest(path=path):
                self.assertEqual(self.count_page_queries(path), before[path])
                self.assertLessEqual(before[path], budget)

    def test_current_user_cache_is_request_scoped_and_tracks_session_changes(self):
        with self.module.app.test_request_context('/'):
            self.module.session['user_id'] = 1
            with patch.object(self.module, 'get_db', wraps=self.module.get_db) as db:
                self.assertEqual(self.module.get_current_user()['id'], 1)
                self.assertEqual(self.module.get_current_user()['id'], 1)
                self.assertEqual(db.call_count, 1)
                self.module.session['user_id'] = 2
                self.assertEqual(self.module.get_current_user()['id'], 2)
                self.assertEqual(db.call_count, 2)
                self.module.session.clear()
                self.assertIsNone(self.module.get_current_user())
        self.sign_in(1)
        self.assertNotIn('Private project', self.client.get('/teacher').text)
        self.sign_in(2)
        page = self.client.get('/teacher').text
        self.assertIn('Private project', page)
        self.assertNotIn('Owned project', page)
        conn = self.module.get_db()
        conn.execute("UPDATE users SET role='student' WHERE id=2")
        conn.commit()
        conn.close()
        self.assertEqual(self.client.get('/teacher').status_code, 403)

    def test_batched_overview_loads_only_latest_authorized_reports(self):
        conn = self.module.get_db()
        conn.execute("INSERT INTO task_updates(task_id,user_id,body,created_at) VALUES(1,3,'Latest report','2000-01-01')")
        conn.execute("INSERT INTO tasks(id,project_id,title,assignee_id) VALUES(9,3,'Private task',2)")
        conn.execute("INSERT INTO task_updates(task_id,user_id,body) VALUES(9,2,'Private report')")
        conn.commit()
        teacher = conn.execute('SELECT * FROM users WHERE id=1').fetchone()
        conn.close()
        overview = self.module.build_teacher_overview(teacher)
        reports = [item for project in overview for item in project['reports']]
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]['report']['body'], 'Latest report')
        self.sign_in(1)
        # The project board still shows full history, unlike the summary cabinet.
        board = self.client.get('/project/1').text
        self.assertIn('Latest report', board)
        self.assertIn('alert', board)

    def test_home_aggregates_preserve_empty_and_overdue_project_counts(self):
        conn = self.module.get_db()
        conn.execute("INSERT INTO projects(id,name,owner_id) VALUES(4,'Empty',1)")
        conn.commit()
        conn.close()
        with self.module.app.test_request_context('/'):
            self.module.session['user_id'] = 1
            projects = {p['id']: dict(p) for p in self.module.get_projects(with_counts=True)}
        self.assertEqual((projects[1]['total'], projects[1]['done'], projects[1]['active'], projects[1]['overdue']), (2, 0, 1, 1))
        self.assertEqual((projects[2]['total'], projects[2]['done'], projects[2]['overdue']), (1, 1, 0))
        self.assertEqual((projects[4]['total'], projects[4]['done'], projects[4]['active'], projects[4]['overdue']), (0, 0, 0, 0))
        self.assertNotIn(3, projects)

    def test_mobile_styles_are_loaded_on_auth_and_application_pages(self):
        for path in ('/login', '/register', '/teacher/login', '/teacher/register'):
            page = self.client.get(path)
            self.assertEqual(page.text.count('/static/mobile.css'), 1)
        self.sign_in(1)
        for path in ('/', '/teacher', '/project/1', '/project/1/assessment',
                     '/project/1/stages', '/project/1/team', '/project/1/defense', '/teams', '/profile'):
            page = self.client.get(path)
            self.assertEqual(page.status_code, 200)
            self.assertEqual(page.text.count('/static/mobile.css'), 1)
        with self.client.get('/static/mobile.css') as response:
            self.assertEqual(response.status_code, 200)
            self.assertIn('safe-area-inset', response.text)

    def test_mobile_page_scrollbar_hiding_is_scoped_and_keeps_accessible_scroll_areas(self):
        with self.client.get('/static/mobile.css') as response:
            css = response.text
        self.assertIn('@media (max-width: 800px) and (forced-colors: none)', css)
        self.assertIn('html { scrollbar-width: none; }', css)
        self.assertIn('html::-webkit-scrollbar, body::-webkit-scrollbar', css)
        self.assertNotIn('*::-webkit-scrollbar', css)
        self.assertNotIn('body { overflow: hidden', css)
        self.assertNotIn('html { overflow: hidden', css)
        self.assertIn('mobile.css?v=20261006-scrollbar', self.client.get('/login').text)

    def test_product_design_assets_and_semantic_forms_are_shared(self):
        for path in ('/login', '/register', '/teacher/login', '/teacher/register'):
            page = self.client.get(path).text
            self.assertEqual(page.count('/static/product-design.css'), 1)
            self.assertIn('width="480" height="360"', page)
            self.assertIn('К содержимому', page)
        self.sign_in(3)
        page = self.client.get('/').text
        self.assertIn('class="project-row project-tile"', page)
        self.assertIn('<details id="project-create"', page)
        self.assertIn('for="project-name"', page)
        self.assertEqual(len(FormTokens(page).forms), 2)  # Logout and project create.
        for path in ('/project/1', '/project/1/stages', '/project/1/defense', '/profile', '/settings'):
            self.assertIn('/static/product-design.css', self.client.get(path).text)

    def test_redesign_shows_real_totals_and_empty_state_without_writing_database(self):
        self.sign_in(3)
        before = self.database_snapshot()
        page = self.client.get('/').text
        self.assertIn('1 из 3 задач выполнено', page)
        self.assertIn('33%', page)
        self.assertNotIn('Private project', page)
        self.assertEqual(self.database_snapshot(), before)
        self.add_candidate()
        self.sign_in(4)
        page = self.client.get('/').text
        self.assertIn('Пока чистый лист.', page)
        self.assertIn('0 из 0 задач выполнено', page)
        self.assertNotIn('project-row project-tile', page)

    def test_teacher_review_counter_uses_submitted_results_not_task_completion(self):
        self.stage_post(3)
        self.defense_post()
        self.sign_in(1)
        page = self.client.get('/teacher').text
        self.assertIn('class="review-counter"><strong>2</strong>', page)
        self.assertIn('href="#review-queue"', page)
        self.assertNotIn('project-create', page)
        self.assertIn('Этап ожидает проверки', page)
        self.assertIn('Видеозащита ожидает проверки', page)

    def test_product_assets_remain_lightweight_local_and_accessible(self):
        with self.client.get('/static/product-design.css') as response:
            self.assertIn('html[data-theme="dark"]', response.text)
            self.assertIn('prefers-reduced-motion', response.text)
            self.assertIn('forced-colors', response.text)
            self.assertIn(':focus-visible', response.text)
            self.assertNotIn('@import', response.text)
        with self.client.get('/static/project-kit.svg') as response:
            from xml.etree import ElementTree
            self.assertLess(len(response.data), 5000)
            self.assertEqual(ElementTree.fromstring(response.data).attrib['viewBox'], '0 0 480 360')
            self.assertNotIn('<script', response.text)

    def test_touch_status_control_respects_task_permissions_and_saves(self):
        self.sign_in(3)
        page = self.client.get('/project/1')
        self.assertIn('id="task-status-1"', page.text)
        self.assertNotIn('id="task-status-2"', page.text)
        response = self.post('/project/1/status', json={'task_id': 1, 'status': 'done'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.post('/project/1/status', json={'task_id': 2, 'status': 'done'}).status_code, 403)
        self.assertEqual(self.post('/project/3/status', json={'task_id': 1, 'status': 'done'}).status_code, 403)
        conn = self.module.get_db()
        self.assertEqual(conn.execute('SELECT status FROM tasks WHERE id=1').fetchone()[0], 'done')
        conn.close()
        self.assertEqual(self.post('/project/1/status', json={'task_id': 1, 'status': 'invalid'}).status_code, 400)

    def test_mobile_tables_include_field_labels(self):
        self.sign_in(1)
        assessment = self.client.get('/project/1/assessment').text
        for label in ('Критерий', 'Максимум', 'Баллы'):
            self.assertIn(f'data-label="{label}"', assessment)
        cabinet = self.client.get('/teacher').text
        for label in ('Ученик', 'Прогресс', 'Выполнено', 'Просрочено'):
            self.assertIn(f'data-label="{label}"', cabinet)

    def test_malformed_task_api_requests_do_not_raise_server_errors(self):
        conn = self.module.get_db()
        conn.execute('UPDATE projects SET owner_id=3 WHERE id=1')
        conn.commit()
        conn.close()
        self.sign_in(3)
        for endpoint, field in (('status', 'status'), ('priority', 'priority')):
            for payload in ([1], 'bad', 1, {'task_id': True, field: 'done'},
                            {'task_id': 2**80, field: 'done'},
                            {'task_id': 1, field: []}, {'task_id': 1, field: {}}):
                with self.subTest(endpoint=endpoint, payload=payload):
                    response = self.post('/project/1/' + endpoint, json=payload)
                    self.assertEqual(response.status_code, 400)
                    self.assertFalse(response.json['success'])

    def test_huge_route_ids_return_validation_error(self):
        self.sign_in(1)
        for path in (f'/project/{2**80}', '/project/0', f'/teams/invitations/{2**80}/respond'):
            response = self.post(path) if path.endswith('respond') else self.client.get(path)
            self.assertEqual(response.status_code, 400)

    def test_anonymous_task_api_returns_json_not_login_html(self):
        response = self.post('/project/1/status', json={'task_id': 1, 'status': 'done'})
        self.assertEqual(response.status_code, 401)
        self.assertFalse(response.json['success'])

    def test_status_response_has_full_metrics_even_with_filtered_board(self):
        self.sign_in(3)
        response = self.post('/project/1/status?q=Overdue', json={'task_id': 1, 'status': 'done'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['metrics'], dict(todo=0, progress=1, done=1, total=2, overdue=0, percent=50))
        self.assertEqual(response.json['students'], [dict(id=3, done=1, total=1)])

    def test_oversized_request_is_rejected(self):
        response = self.post('/register', data={'username': 'x' * (1024 * 1024 + 1)})
        self.assertEqual(response.status_code, 413)
        self.assertIn('Слишком много данных', response.text)

    def test_legacy_initializer_uses_configured_database_without_demo_seed(self):
        with tempfile.TemporaryDirectory() as folder:
            database = str(Path(folder) / 'production.db')
            env = dict(os.environ, M_FLOW_DEBUG='0', M_FLOW_SECRET_KEY='initializer-test-only', M_FLOW_DATABASE=database)
            result = subprocess.run([sys.executable, 'init_db.py'], cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            conn = sqlite3.connect(database)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM users').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM projects').fetchone()[0], 0)
            self.assertIsNotNone(conn.execute("SELECT name FROM sqlite_master WHERE name='project_defenses'").fetchone())
            conn.close()

    def test_profile_rejects_oversized_new_password(self):
        self.sign_in(1)
        response = self.post('/profile', data=dict(old_password='test-password', new_password='x' * 257, repeat_password='x' * 257))
        self.assertEqual(response.status_code, 200)
        self.assertIn('не длиннее 256', response.text)
        conn = self.module.get_db()
        self.assertEqual(conn.execute('SELECT password FROM users WHERE id=1').fetchone()[0], self.password)
        conn.close()

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
        data = dict(csrf_token="test-form-token", number=str(number), revision=str(revision),
                    action=action, result="Результат этапа", **fields)
        return self.post("/project/1/stages", data=data)

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

    def test_motion_assets_and_auth_links_preserve_normal_navigation(self):
        for path in ('/login', '/register', '/teacher/login', '/teacher/register'):
            page = self.client.get(path)
            self.assertEqual(page.text.count('/static/motion.css'), 1)
            self.assertEqual(page.text.count('/static/motion.js'), 1)
            mode = 'register' if path.endswith('register') else 'login'
            self.assertIn(f'href="/{mode}"', page.text)
            self.assertIn(f'href="/teacher/{mode}"', page.text)
            self.assertIn('<form method="post">', page.text)
        self.sign_in(1)
        self.assertIn('/static/motion.js', self.client.get('/teacher').text)
        with self.client.get('/static/motion.css') as response:
            self.assertEqual(response.status_code, 200)
            self.assertIn('prefers-reduced-motion: reduce', response.text)
            self.assertIn('data-pending-role="teacher"', response.text)
            self.assertNotIn('mf-enter-', response.text)
            self.assertNotIn('mf-leave-', response.text)
        with self.client.get('/static/motion.js') as response:
            self.assertEqual(response.status_code, 200)
            self.assertIn('reduced.matches', response.text)
            self.assertIn("window.addEventListener('pageshow'", response.text)

    def test_site_settings_available_with_and_without_account(self):
        for user_id in (None, 1, 3):
            if user_id is not None:
                self.sign_in(user_id)
            page = self.client.get('/settings')
            self.assertEqual(page.status_code, 200)
            self.assertIn('id="site-theme"', page.text)
            self.assertIn('id="site-animations"', page.text)
            self.assertIn('id="reset-preferences"', page.text)
            self.assertEqual(page.text.count('/static/preferences.js'), 1)
        self.assertIn('/settings', self.client.get('/profile').text)
        for asset in ('preferences.js', 'preferences.css'):
            with self.client.get('/static/' + asset) as response:
                self.assertEqual(response.status_code, 200)

    def test_outlined_actions_loaded_on_all_major_pages(self):
        for path in ('/login', '/register', '/settings'):
            self.assertEqual(self.client.get(path).text.count('/static/controls.css'), 1)
        self.sign_in(1)
        for path in ('/', '/teacher', '/profile', '/project/1', '/project/1/stages',
                     '/project/1/assessment', '/project/1/team', '/project/1/defense'):
            page = self.client.get(path)
            self.assertEqual(page.status_code, 200)
            self.assertEqual(page.text.count('/static/controls.css'), 1)
        with self.client.get('/static/controls.css') as response:
            self.assertEqual(response.status_code, 200)
            self.assertIn('.account .logout', response.text)
            self.assertIn('var(--mf-line)', response.text)
            self.assertIn('min-height: 44px', response.text)

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
        self.post("/logout")
        self.assertEqual(self.client.get("/project/1/stages").status_code, 302)

    def test_last_stage_requires_documentation_and_safe_presentation_url(self):
        for number in range(1, 5):
            self.stage_post(3, number=number)
            self.stage_post(1, number=number, revision=1, action="accept")
        self.assertEqual(self.stage_post(3, number=5).status_code, 400)
        for url in ("javascript:alert(1)", "https://", "https://[broken", "https://example.com/ bad"):
            self.assertEqual(self.stage_post(3, number=5, presentation_url=url).status_code, 400)
        self.sign_in(3)
        data = dict(csrf_token="test-form-token", number="5", revision="0", action="submit",
                    result="", presentation_url="https://example.com/slides")
        self.assertEqual(self.post("/project/1/stages", data=data).status_code, 400)
        self.assertEqual(self.stage_rows()[4]["state"], "todo")

    def test_stage_content_is_escaped_and_draft_can_be_empty(self):
        self.sign_in(3)
        data = dict(csrf_token="test-form-token", number="1", revision="0", action="save", result="")
        self.assertEqual(self.post("/project/1/stages", data=data).status_code, 302)
        data.update(revision="1", action="submit")
        self.assertEqual(self.post("/project/1/stages", data=data).status_code, 400)
        data["result"] = "<script>alert('topic')</script>"
        self.assertEqual(self.post("/project/1/stages", data=data).status_code, 302)
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

    def add_candidate(self, discoverable=1):
        conn = self.module.get_db()
        conn.execute("INSERT INTO users(id,username,password,role) VALUES(4,'candidate-engineer',?,'student')",(self.password,))
        conn.execute("""INSERT INTO student_profiles(user_id,class_name,direction,skills_json,bio,discoverable)
            VALUES(4,'11Б','Инженерное',?, 'Собираю прототипы',?)""", ('["3D-моделирование", "Arduino"]', discoverable))
        conn.commit()
        conn.close()

    def invitation_post(self, user_id=3, project_id=2, student_id=4):
        self.sign_in(user_id)
        return self.post('/teams/invite',data=dict(csrf_token='test-form-token',project_id=str(project_id),
                                student_id=str(student_id),role_text='Инженер прототипа',message='Давай сделаем общий проект'))

    def invitation_id(self):
        conn = self.module.get_db()
        iid = conn.execute('SELECT id FROM team_invitations').fetchone()[0]
        conn.close()
        return iid

    def invitation_answer(self,user_id,action='accept'):
        self.sign_in(user_id)
        return self.post(f'/teams/invitations/{self.invitation_id()}/respond',
                               data=dict(csrf_token='test-form-token',action=action))

    def test_student_profile_saves_skills_without_changing_another_user(self):
        self.add_candidate()
        self.sign_in(3)
        data=dict(csrf_token='test-form-token',user_id='4',class_name='10А',direction='ИТ',
                  skills='Python, python,  Презентации ',bio='Люблю код',discoverable='1')
        self.assertEqual(self.post('/skills',data=data).status_code,302)
        conn = self.module.get_db()
        from teamwork import profile_for
        profile = profile_for(conn,3)
        self.assertEqual(profile['skills_json'],'["Python", "Презентации"]')
        self.assertEqual(profile_for(conn,4)['class_name'],'11Б')
        conn.close()
        self.sign_in(1)
        self.assertEqual(self.post('/skills',data=data).status_code,403)

    def test_profile_visibility_requires_explicit_opt_in(self):
        self.add_candidate(discoverable=0)
        self.sign_in(3)
        self.assertNotIn('candidate-engineer',self.client.get('/teams').text)
        self.assertEqual(self.invitation_post().status_code,400)
        self.sign_in(4)
        data=dict(csrf_token='test-form-token',class_name='11Б',direction='Инженерное',skills='Arduino',discoverable='1')
        self.assertEqual(self.post('/skills',data=data).status_code,302)
        self.sign_in(3)
        self.assertIn('candidate-engineer',self.client.get('/teams').text)
        self.sign_in(4)
        data.pop('discoverable')
        self.post('/skills',data=data)
        self.sign_in(3)
        self.assertNotIn('candidate-engineer',self.client.get('/teams').text)

    def test_incomplete_or_excessive_skills_profile_is_rejected(self):
        self.sign_in(3)
        data=dict(csrf_token='test-form-token',discoverable='1')
        self.assertEqual(self.post('/skills',data=data).status_code,400)
        for fields in ({'class_name':'x'*31},{'skills':','.join(f'skill{i}' for i in range(13))},{'skills':'x'*41},{'bio':'x'*501}):
            self.assertEqual(self.post('/skills',data=dict(csrf_token='test-form-token',**fields)).status_code,400)

    def test_directory_filters_skills_direction_and_class(self):
        self.add_candidate()
        self.sign_in(3)
        for query in ('?skill=arduino','?direction=инженер','?class_name=11Б','?q=прототип'):
            self.assertIn('candidate-engineer',self.client.get('/teams'+query).text)
        for query in ('?skill=Python','?direction=ИТ','?class_name=10А'):
            self.assertNotIn('candidate-engineer',self.client.get('/teams'+query).text)

    def test_cross_class_team_requires_invitation_acceptance(self):
        self.add_candidate()
        self.assertEqual(self.invitation_post().status_code,302)
        self.sign_in(4)
        self.assertEqual(self.client.get('/project/2/team').status_code,403)
        self.assertIn('Observed project',self.client.get('/teams').text)
        self.assertEqual(self.invitation_answer(4).status_code,302)
        page = self.client.get('/project/2/team')
        self.assertEqual(page.status_code,200)
        self.assertIn('Инженер прототипа',page.text)
        self.assertIn('11Б',page.text)
        self.assertEqual(self.client.get('/project/2').status_code,200)
        self.sign_in(3)
        self.assertIn('candidate-engineer',self.client.get('/project/2').text)

    def test_invites_require_owner_and_discoverable_student(self):
        self.add_candidate()
        self.assertEqual(self.invitation_post(project_id=1).status_code,400)
        self.assertEqual(self.invitation_post(user_id=1,project_id=1,student_id=2).status_code,403)
        self.assertEqual(self.invitation_post(user_id=1,project_id=1,student_id=3).status_code,403)
        self.assertEqual(self.invitation_post(student_id=3).status_code,400)

    def test_invitation_cannot_be_answered_by_another_student_or_twice(self):
        self.add_candidate()
        self.invitation_post()
        self.assertEqual(self.invitation_post().status_code,400)
        self.assertEqual(self.invitation_answer(3).status_code,403)
        self.assertEqual(self.invitation_answer(1).status_code,403)
        self.assertEqual(self.invitation_answer(4).status_code,302)
        self.assertEqual(self.invitation_answer(4).status_code,400)
        conn = self.module.get_db()
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM project_members WHERE project_id=2 AND user_id=4').fetchone()[0],1)
        conn.close()

    def test_declining_or_cancelling_invitation_does_not_grant_access(self):
        self.add_candidate()
        self.invitation_post()
        self.assertEqual(self.invitation_answer(4,'decline').status_code,302)
        self.assertEqual(self.client.get('/project/2/team').status_code,403)
        self.invitation_post()
        self.sign_in(3)
        self.assertEqual(self.post('/project/2/team',data=dict(csrf_token='test-form-token',action='cancel',invitation_id=self.invitation_id())).status_code,302)
        self.assertEqual(self.invitation_answer(4).status_code,400)
        self.assertEqual(self.client.get('/project/2/team').status_code,403)

    def test_team_roles_are_owner_only_and_removed_with_membership(self):
        self.add_candidate()
        self.sign_in(3)
        data=dict(csrf_token='test-form-token',action='role',student_id='3',role_text='Разработчик')
        self.assertEqual(self.post('/project/1/team',data=data).status_code,403)
        self.sign_in(1)
        self.assertEqual(self.post('/project/1/transfer', data={'student_username': 'candidate-engineer'}).status_code, 302)
        self.sign_in(4)
        self.assertEqual(self.post('/project/1/team',data=data).status_code,302)
        self.assertIn('Разработчик',self.client.get('/project/1/team').text)
        data['student_id']='2'
        self.assertEqual(self.post('/project/1/team',data=data).status_code,400)
        conn = self.module.get_db()
        conn.execute('DELETE FROM project_members WHERE project_id=1 AND user_id=3')
        conn.commit()
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM team_member_roles').fetchone()[0],0)
        conn.close()

    def defense_post(self,user_id=3,revision=0,action='submit',url='https://example.com/defense',comment='',description='Защита проекта'):
        self.sign_in(user_id)
        return self.post('/project/1/defense',data=dict(csrf_token='test-form-token',revision=str(revision),
                                action=action,video_url=url,description=description,teacher_comment=comment))

    def test_video_defense_submission_return_and_acceptance(self):
        self.assertEqual(self.defense_post().status_code,302)
        self.assertEqual(self.defense_post(user_id=1,revision=1,action='return').status_code,400)
        self.assertEqual(self.defense_post(user_id=1,revision=1,action='return',comment='Улучшите звук').status_code,302)
        self.sign_in(3)
        self.assertIn('Улучшите звук',self.client.get('/project/1/defense').text)
        self.assertEqual(self.defense_post(revision=2,url='https://example.com/new-video').status_code,302)
        self.assertEqual(self.defense_post(user_id=1,revision=3,action='accept',comment='Принято').status_code,302)
        self.assertIn('Защита принята',self.client.get('/project/1/defense').text)
        self.assertIn('Видеозащита: Защита принята',self.client.get('/teacher').text)
        self.assertEqual(self.defense_post(revision=4,action='save').status_code,400)

    def test_video_defense_roles_and_project_access(self):
        self.assertEqual(self.defense_post(user_id=1).status_code,400)
        self.assertEqual(self.defense_post(user_id=2).status_code,403)
        self.assertEqual(self.client.get('/project/1/defense').status_code,403)
        self.assertEqual(self.defense_post(action='accept').status_code,400)
        self.defense_post()
        self.assertEqual(self.defense_post(revision=1,action='save').status_code,400)
        self.assertEqual(self.defense_post(revision=1,action='accept').status_code,400)

    def test_video_defense_validates_urls_and_handles_stale_tabs(self):
        for url in ('','javascript:alert(1)','https://','https://[bad','https://example.com/ bad','https://user:secret@example.com'):
            self.assertEqual(self.defense_post(url=url).status_code,400)
        self.assertEqual(self.defense_post(action='save',url='').status_code,302)
        self.assertEqual(self.defense_post().status_code,400)
        self.assertEqual(self.defense_post(revision=1).status_code,302)

    def test_team_profile_and_video_content_are_escaped(self):
        self.add_candidate()
        conn = self.module.get_db()
        conn.execute("UPDATE student_profiles SET bio='<script>alert(1)</script>' WHERE user_id=4")
        conn.commit()
        conn.close()
        self.sign_in(3)
        self.assertIn('&lt;script&gt;',self.client.get('/teams').text)
        self.defense_post(description='<script>alert(2)</script>')
        page = self.client.get('/project/1/defense').text
        self.assertIn('&lt;script&gt;',page)
        self.assertNotIn('<script>alert',page)
        self.assertNotIn('<iframe',page)

    def test_collaboration_forms_require_csrf(self):
        self.sign_in(3)
        for url in ('/skills','/teams/invite','/project/2/team','/project/1/defense'):
            self.assertEqual(self.client.post(url,data={}).status_code,400)

    def test_collaboration_pages_follow_shared_design(self):
        self.sign_in(3)
        for url in ('/skills','/teams','/project/1/team','/project/1/defense'):
            response=self.client.get(url)
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.text.count('/static/mflow-theme.css'),1)
            self.assertIn('/static/teamwork.css',response.text)

    def test_hidden_profile_can_still_accept_existing_invitation(self):
        self.add_candidate()
        self.invitation_post()
        conn=self.module.get_db()
        conn.execute('UPDATE student_profiles SET discoverable=0 WHERE user_id=4')
        conn.commit()
        conn.close()
        self.assertEqual(self.invitation_answer(4).status_code,302)
        self.sign_in(3)
        page=self.client.get('/project/2/team').text
        self.assertIn('candidate-engineer',page)
        self.assertNotIn('3D-моделирование',page)

    def test_collaboration_schema_repeated_initialization_preserves_data(self):
        self.add_candidate()
        self.invitation_post()
        self.defense_post()
        from teamwork import initialize_teamwork
        from video_defense import initialize_defenses
        conn=self.module.get_db()
        initialize_teamwork(conn)
        initialize_defenses(conn)
        conn.commit()
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM team_invitations').fetchone()[0],1)
        self.assertEqual(conn.execute('SELECT state FROM project_defenses WHERE project_id=1').fetchone()[0],'submitted')
        conn.close()

    def test_assessment_publication_and_student_visibility(self):
        self.sign_in(1)
        page = self.client.get("/project/1/assessment")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.text.count('<select name="criterion_'), 12)
        self.assertIn('name="csrf_token"', page.text)
        for asset in ("assessment.css", "assessment.js"):
            with self.client.get(f"/static/{asset}") as response:
                self.assertEqual(response.status_code, 200)
        response = self.post("/project/1/assessment", data=self.assessment_data())
        self.assertEqual(response.status_code, 302)
        self.sign_in(3)
        response = self.client.get("/project/1/assessment")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Итого: 45 / 45 · Оценка: 5", response.text)
        self.assertIn("Хорошая работа", response.text)
        self.assertNotIn('name="criterion_', response.text)
        self.assertEqual(self.post("/project/1/assessment", data=self.assessment_data()).status_code, 403)

    def test_draft_is_private_and_can_be_incomplete(self):
        self.sign_in(1)
        data = self.assessment_data("draft")
        data["criterion_1"] = ""
        data["criterion_2"] = "0"
        self.assertEqual(self.post("/project/1/assessment", data=data).status_code, 302)
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
            self.assertEqual(self.post("/project/1/assessment", data=data).status_code, 400)
        data = self.assessment_data()
        data["student_id"] = "2"
        self.assertEqual(self.post("/project/1/assessment", data=data).status_code, 400)
        conn = self.module.get_db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM project_assessments").fetchone()[0], 0)
        conn.close()

    def test_teacher_requires_project_access_but_can_grade_as_member(self):
        self.sign_in(2)
        self.assertEqual(self.client.get("/project/1/assessment").status_code, 403)
        self.assertEqual(self.post("/project/1/assessment", data=self.assessment_data()).status_code, 403)
        self.sign_in(1)
        self.assertEqual(self.post("/project/2/assessment", data=self.assessment_data()).status_code, 302)

    def test_student_cannot_view_another_students_assessment(self):
        conn = self.module.get_db()
        conn.execute("INSERT INTO users(id,username,password,role) VALUES(4,'student-two',?,'student')", (self.password,))
        conn.execute("INSERT INTO project_members(project_id,user_id) VALUES(1,4)")
        conn.commit()
        conn.close()
        self.sign_in(1)
        self.post("/project/1/assessment", data=self.assessment_data())
        self.sign_in(4)
        response = self.client.get("/project/1/assessment?student_id=3")
        self.assertIn("Оценка ещё не опубликована", response.text)
        self.assertNotIn("Иванов Иван", response.text)

    def test_assessment_update_escapes_content_and_does_not_duplicate(self):
        self.sign_in(1)
        data = self.assessment_data()
        self.post("/project/1/assessment", data=data)
        data["note"] = "<script>alert(1)</script>"
        data["criterion_1"] = "0"
        self.assertEqual(self.post("/project/1/assessment", data=data).status_code, 302)
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
        self.post("/project/1/assessment", data=self.assessment_data())
        self.sign_in(2)
        data = self.assessment_data("draft")
        data["note"] = "Приватный черновик второго учителя"
        self.post("/project/1/assessment", data=data)
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
        self.assertEqual(self.post("/teacher/projects", data={"name": "Denied"}).status_code, 403)

    def test_member_teacher_cannot_edit_observed_project(self):
        self.sign_in(1)
        response = self.post("/project/2/edit/3", data={"title": "Denied"})
        self.assertEqual(response.status_code, 403)

    def test_teacher_cannot_create_project_and_login_lands_in_cabinet(self):
        response = self.post("/teacher/login", data={"username": "teacher-one", "password": "test-password"})
        self.assertEqual(response.location, "/teacher")
        response = self.post("/teacher/projects", data={"name": "New project"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.post('/add_project', data={'name': 'New project'}).status_code, 403)
        conn = self.module.get_db()
        project = conn.execute("SELECT id, owner_id FROM projects WHERE name = 'New project'").fetchone()
        self.assertIsNone(project)
        conn.close()
        self.assertIn('Кабинет учителя', self.client.get('/').text)
        self.assertNotIn('+ Создать проект', self.client.get('/teacher').text)

    def test_teacher_registration_supports_custom_login(self):
        response = self.post("/teacher/register", data={
            "username": "my-new-teacher", "password": "my-password-123",
            "repeat_password": "my-password-123"})
        self.assertEqual(response.location, "/teacher")
        self.assertEqual(self.client.get("/teacher").status_code, 200)
        conn = self.module.get_db()
        user = conn.execute("SELECT role FROM users WHERE username=?", ("my-new-teacher",)).fetchone()
        self.assertEqual(user["role"], "teacher")
        conn.close()
        self.assertEqual(self.post("/logout").location, "/teacher/login")
        response = self.post("/teacher/login", data={
            "username": "my-new-teacher", "password": "my-password-123"})
        self.assertEqual(response.location, "/teacher")

    def test_registration_role_is_selected_by_route(self):
        response = self.post("/register", data={
            "username": "new-student", "password": "my-password-123",
            "repeat_password": "my-password-123", "role": "teacher"})
        self.assertEqual(response.location, "/")
        self.assertEqual(self.client.get("/teacher").status_code, 403)
        conn = self.module.get_db()
        self.assertEqual(conn.execute("SELECT role FROM users WHERE username='new-student'").fetchone()[0], "student")
        conn.close()

    def test_wrong_login_portal_does_not_sign_in(self):
        for path, username in (("/login", "teacher-one"), ("/teacher/login", "student-one")):
            response = self.post(path, data={"username": username, "password": "test-password"})
            self.assertEqual(response.status_code, 200)
            self.assertIn("Это аккаунт", response.text)
            with self.client.session_transaction() as session:
                self.assertNotIn("user_id", session)

    def test_teacher_registration_cannot_replace_existing_student(self):
        response = self.post("/teacher/register", data={
            "username": "student-one", "password": "my-password-123",
            "repeat_password": "my-password-123"})
        self.assertIn("Такой логин уже занят", response.text)
        conn = self.module.get_db()
        self.assertEqual(conn.execute("SELECT role FROM users WHERE id=3").fetchone()[0], "student")
        conn.close()

    def test_mismatched_passwords_do_not_create_account(self):
        response = self.post("/teacher/register", data={
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
