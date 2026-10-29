"""Regressions discovered during the October project-wide review."""
from contextlib import closing
import re
from unittest.mock import patch

from project_stages import stages_from_rows
from project_workspace import next_step
from material_links import safe_web_url
from teamwork import change_team


class QualityReviewCases:
    def assessment_version_from(self, client=None, project=1):
        page = (client or self.client).get(f'/project/{project}/assessment?student_id=3').text
        found = re.search(r'name="assessment_version" value="([^"]+)"', page)
        return found.group(1) if found else ''

    def test_review_password_change_revokes_other_sessions_and_old_cookie(self):
        self.sign_in(3)
        old_cookie = self.client.get_cookie('session').value
        other = self.module.app.test_client()
        other_token = self.module.new_session(self.module.get_db, self.module.SECRET_KEY, 3)
        with other.session_transaction() as session:
            session.update(user_id=3, auth_token=other_token, csrf_token='other-test-token')
        observer = self.module.app.test_client()
        observer_token = self.module.new_session(self.module.get_db, self.module.SECRET_KEY, 1)
        with observer.session_transaction() as session:
            session.update(user_id=1, auth_token=observer_token, csrf_token='observer-test-token')
        self.assertEqual(other.get('/').status_code, 200)
        self.assertIn('Пароль изменён', self.post('/profile', data=dict(old_password='test-password',
            new_password='new-test-password', repeat_password='new-test-password')).text)
        self.assertEqual(self.client.get('/').status_code, 200)
        self.assertEqual(other.get('/').status_code, 302)
        self.assertEqual(observer.get('/teacher').status_code, 200)
        replay = self.module.app.test_client()
        replay.set_cookie('session', old_cookie)
        self.assertEqual(replay.get('/').status_code, 302)

    def test_review_stale_assessment_does_not_overwrite_newer_scores(self):
        self.sign_in(1)
        old = self.assessment_version_from()
        first = dict(self.assessment_data(), assessment_version=old, note='Свежая оценка')
        self.assertEqual(self.post('/project/1/assessment', data=first).status_code, 302)
        before = self.database_snapshot()
        stale = dict(self.assessment_data('draft'), assessment_version=old, note='<b>Старая вкладка</b>')
        response = self.post('/project/1/assessment', data=stale)
        self.assertEqual(response.status_code, 400)
        self.assertIn('&lt;b&gt;Старая вкладка&lt;/b&gt;', response.text)
        self.assertIn('Открыть сохранённую оценку', response.text)
        self.assertEqual(self.database_snapshot(), before)

    def test_review_finished_project_does_not_require_new_supervisor(self):
        stages = stages_from_rows([dict(number=n, state='done') for n in range(1,6)])
        done = next_step(stages, 'student', True, False, dict(state='accepted'), True)
        self.assertEqual(done['key'], 'assessment')
        pending = next_step(stages, 'student', True, False, dict(state='draft'), True)
        self.assertEqual(pending['key'], 'teacher-missing')
        self.sign_in(1)
        self.assertEqual(self.post('/project/2/assessment', data=self.assessment_data()).status_code, 302)
        with closing(self.module.get_db()) as conn:
            conn.executemany("INSERT INTO project_stages(project_id,number,state) VALUES(2,?,'done')", [(n,) for n in range(1,6)])
            conn.execute("INSERT INTO project_defenses(project_id,state) VALUES(2,'accepted')")
            conn.execute('DELETE FROM project_members WHERE project_id=2 AND user_id=1')
            conn.commit()
        self.sign_in(3)
        page = self.client.get('/?project=2').text
        self.assertIn('Этапы и видеозащита приняты', page)
        self.assertNotIn('В проекте пока нет учителя', page)

    def test_review_login_cannot_create_session_for_replaced_password(self):
        with closing(self.module.get_db()) as conn:
            previous = conn.execute('SELECT password FROM users WHERE id=3').fetchone()['password']
            conn.execute("UPDATE users SET password='changed-between-check-and-login' WHERE id=3")
            conn.commit()
            count = conn.execute('SELECT COUNT(*) FROM auth_sessions').fetchone()[0]
        token = self.module.new_session(self.module.get_db, self.module.SECRET_KEY, 3, expected_password=previous)
        self.assertIsNone(token)
        with closing(self.module.get_db()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM auth_sessions').fetchone()[0], count)

    def test_review_password_and_sessions_roll_back_if_new_session_fails(self):
        self.sign_in(3)
        before = self.database_snapshot()
        with closing(self.module.get_db()) as conn:
            sessions = [tuple(r) for r in conn.execute('SELECT * FROM auth_sessions ORDER BY token_hash')]
        with patch('auth_security.new_session', side_effect=self.module.DatabaseUnavailable('private-driver-details')):
            response = self.post('/profile', data=dict(old_password='test-password', new_password='new-test-password', repeat_password='new-test-password'))
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private-driver-details', response.text)
        self.assertEqual(self.database_snapshot(), before)
        with closing(self.module.get_db()) as conn:
            self.assertEqual([tuple(r) for r in conn.execute('SELECT * FROM auth_sessions ORDER BY token_hash')], sessions)
        self.assertEqual(self.client.get('/').status_code, 200)

    def test_review_assessment_requires_version_and_keeps_invalid_input(self):
        self.sign_in(1)
        before = self.database_snapshot()
        for version in ('', 'invalid', 'x' * 64):
            data = dict(self.assessment_data(), assessment_version=version, note='Моё примечание')
            response = self.post('/project/1/assessment', data=data)
            self.assertEqual(response.status_code, 400)
            self.assertIn('Моё примечание', response.text)
            self.assertEqual(self.database_snapshot(), before)

    def test_review_identical_assessment_retry_preserves_timestamp_and_notifications(self):
        self.sign_in(1)
        self.assertEqual(self.post('/project/1/assessment', data=self.assessment_data()).status_code, 302)
        with closing(self.module.get_db()) as conn:
            conn.execute("UPDATE project_assessments SET updated_at='2000-01-01 00:00:00' WHERE project_id=1")
            conn.commit()
        old = self.assessment_version_from()
        before = self.database_snapshot()
        self.assertEqual(self.post('/project/1/assessment', data=dict(self.assessment_data(), assessment_version=old)).status_code, 302)
        self.assertEqual(self.database_snapshot(), before)

    def test_review_team_rechecks_owner_after_project_lock(self):
        self.sign_in(3)
        from teamwork import begin_project_write
        def ownership_changed(conn, project_id):
            conn.execute('UPDATE projects SET owner_id=1 WHERE id=?', (project_id,))
            conn.commit()
            begin_project_write(conn, project_id)
        with patch('teamwork.begin_project_write', side_effect=ownership_changed):
            response = self.post('/project/2/team', data=dict(action='role', student_id='3', role_text='Не сохранять'))
        self.assertEqual(response.status_code, 403)
        with closing(self.module.get_db()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM team_member_roles').fetchone()[0], 0)

    def test_review_materials_reject_credentials_ports_and_unsafe_legacy_links(self):
        invalid = ('https://user:password@example.com/slides', 'https://example.com:wrong/slides',
                   'https://example.com:99999/slides', 'javascript:alert(1)', 'https://example.com/\x00')
        for url in invalid:
            self.assertFalse(safe_web_url(url))
        self.assertTrue(safe_web_url('https://example.com/slides?a=1&b=2'))
        with closing(self.module.get_db()) as conn:
            conn.executemany("INSERT INTO project_stages(project_id,number,state) VALUES(1,?,'done')", [(n,) for n in range(1,5)])
            conn.commit()
        for url in invalid[:3]:
            self.assertEqual(self.stage_post(3, number=5, presentation_url=url).status_code, 400)
        with closing(self.module.get_db()) as conn:
            conn.execute("INSERT INTO project_stages(project_id,number,state,presentation_url) VALUES(1,5,'progress','javascript:alert(1)')")
            conn.execute("INSERT INTO project_defenses(project_id,state,video_url) VALUES(1,'draft','javascript:alert(2)')")
            conn.commit()
        self.sign_in(3)
        for part in ('stages','defense','preparation'):
            page = self.client.get(f'/project/1/{part}').text
            self.assertNotIn('href="javascript:', page)

    def test_review_editable_work_forms_preserve_draft_guard(self):
        for user, path in ((3,'stages'), (3,'defense'), (3,'preparation'), (1,'assessment')):
            self.sign_in(user)
            page = self.client.get(f'/project/1/{path}').text
            self.assertIn('data-guard-draft', page)
            self.assertIn('/static/form-guard.js?v=20261029', page)
