"""Optional production view, using only disposable fixture databases."""
from contextlib import closing
from html import unescape
import json
import re
import unittest
import os
import test_postgres as pg_fixtures
import test_teacher_cabinet as fixtures


class LinearViewTests(unittest.TestCase):
    fixture_type = fixtures.TeacherCabinetTests
    @classmethod
    def setUpClass(cls):
        cls.fixture_type.setUpClass()
        cls.module = cls.fixture_type.module

    @classmethod
    def tearDownClass(cls):
        cls.fixture_type.tearDownClass()

    def setUp(self):
        self.fixture = self.fixture_type()
        self.fixture.setUp()
        self.client = self.fixture.client

    def sign_in(self, uid=3):
        self.fixture.sign_in(uid)

    def model(self, text):
        return json.loads(unescape(re.search(r'data-stages="([^"]*)"', text)[1]))

    def test_login_and_membership_are_required(self):
        for path in ('/next', '/next/project/2'):
            self.assertEqual(self.client.get(path).status_code, 302)
        self.sign_in(2)
        self.assertEqual(self.client.get('/next/project/2').status_code, 403)
        self.assertNotIn('data-orbit', self.client.get('/next/project/99999').text)

    def test_gets_are_read_only_and_show_actual_projects(self):
        self.sign_in()
        with closing(self.module.get_db()) as conn:
            conn.execute("UPDATE projects SET name='Настоящий проект' WHERE id=2")
            conn.commit()
        before = self.fixture.database_snapshot()
        for path in ('/next', '/next/project/2', '/next/project/2?stage=5'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn('Настоящий проект', response.text)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertEqual(self.fixture.database_snapshot(), before)

    def test_teacher_does_not_receive_unsent_draft(self):
        self.sign_in()
        self.assertEqual(self.fixture.post('/project/2/stages', data=dict(number='1', revision='0', action='save', result='Частный черновик ученика')).status_code, 302)
        self.assertIn('Частный черновик ученика', self.client.get('/next/project/2').text)
        self.sign_in(1)
        self.assertNotIn('Частный черновик ученика', self.client.get('/next/project/2').text)

    def test_acceptance_changes_real_orbit_progress(self):
        self.sign_in()
        self.assertEqual(self.fixture.post('/project/2/stages', data=dict(number='1', revision='0', action='submit', result='Результат исследования')).status_code, 302)
        self.sign_in(1)
        before = self.client.get('/next/project/2').text
        self.assertEqual(self.model(before)[0]['status'], 'review')
        self.assertIn('Принято 0 из 5', before)
        self.assertEqual(self.fixture.post('/project/2/stages', data=dict(number='1', revision='1', action='accept')).status_code, 302)
        after = self.client.get('/next/project/2').text
        self.assertEqual([s['status'] for s in self.model(after)], ['done', 'progress', 'locked', 'locked', 'locked'])
        self.assertIn('Принято 1 из 5', after)

    def test_task_and_history_links_work_without_javascript(self):
        self.sign_in()
        for tab in ('tasks', 'history'):
            page = self.client.get('/next/project/2?tab=' + tab).text
            self.assertIn(f'<section data-tab-panel="{tab}" >', page)
            self.assertIn('<section data-tab-panel="result" hidden>', page)

    def test_new_routes_cannot_mutate_data(self):
        self.sign_in()
        before = self.fixture.database_snapshot()
        for path in ('/next', '/next/project/2'):
            self.assertEqual(self.fixture.post(path, data=dict(action='accept')).status_code, 405)
        self.assertEqual(self.fixture.database_snapshot(), before)

    def test_user_text_is_escaped_and_future_selection_is_clamped(self):
        self.sign_in()
        with closing(self.module.get_db()) as conn:
            conn.execute('UPDATE projects SET name=? WHERE id=2', ('<script>alert("project")</script>',))
            conn.commit()
        for value in ('5', 'bad', '-100', '999999999999999999999999'):
            page = self.client.get('/next/project/2?stage=' + value).text
            self.assertNotIn('<script>alert', page)
            self.assertIn('&lt;script&gt;', page)
            self.assertIn('data-selected="0"', page)

    def test_existing_forms_and_csrf_are_used_without_role_switching(self):
        self.sign_in()
        page = self.client.get('/next/project/2').text
        self.assertIn('/project/2/stages?view_stage=1#stage-1-result', page)
        self.assertIn('name="csrf_token" value="test-form-token"', page)
        self.assertNotIn('Я учитель', page)
        self.assertNotIn('Алексей Морозов', page)
        self.assertEqual(self.client.post('/logout').status_code, 400)



@unittest.skipUnless(os.environ.get("M_FLOW_TEST_POSTGRES_URL"), "Requires disposable PostgreSQL")
class PostgresLinearViewTests(LinearViewTests):
    fixture_type = pg_fixtures.PostgresCabinetTests
