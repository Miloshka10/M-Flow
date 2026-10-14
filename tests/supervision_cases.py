"""Shared SQLite/PostgreSQL regression cases, synthetic fixtures only."""
import re
import os
from types import SimpleNamespace
from unittest.mock import patch


class SupervisionCases:
    def supervision_code(self, teacher=2):
        self.sign_in(teacher)
        response = self.post('/supervision', data={'action': 'create'})
        self.assertEqual(response.status_code, 200)
        return re.search(r'readonly autocomplete="off" value="([A-Za-z0-9_-]{32})"', response.text).group(1)

    def supervision_empty_project(self):
        conn = self.module.get_db()
        conn.execute("INSERT INTO projects(id,name,owner_id) VALUES(50,'Consent-only project',3)")
        conn.execute('INSERT INTO project_members(project_id,user_id) VALUES(50,3)')
        conn.commit()
        conn.close()
        return 50

    def supervision_request(self, code, project=2, user=3):
        self.sign_in(user)
        return self.post('/supervision', data=dict(action='request', code=code, project_id=str(project)))

    def supervision_row(self):
        conn = self.module.get_db()
        row = dict(conn.execute('SELECT * FROM supervision_requests ORDER BY id DESC LIMIT 1').fetchone())
        conn.close()
        return row

    def supervision_reply(self, user, action='approve', revision=None):
        row = self.supervision_row()
        self.sign_in(user)
        return self.post('/supervision', data=dict(action=action, request_id=str(row['id']),
            revision=str(row['revision'] if revision is None else revision)))

    def test_supervision_new_connection_is_scoped_and_consent_precedes_access(self):
        pid = self.supervision_empty_project()
        code = self.supervision_code()
        conn = self.module.get_db()
        self.assertNotIn(code, conn.execute('SELECT code_hash FROM teacher_invitations').fetchone()[0])
        conn.close()
        self.assertNotIn('Consent-only project', self.client.get('/supervision').text)
        self.assertEqual(self.client.get('/project/50').status_code, 403)
        self.sign_in(3)
        preview = self.post('/supervision', data=dict(action='preview', code=code))
        self.assertIn('Учитель: teacher-two', preview.text)
        self.assertEqual(self.supervision_request(code, pid).status_code, 302)
        self.sign_in(2)
        self.assertIn('Consent-only project', self.client.get('/supervision').text)
        for path in ('/project/50', '/project/50/stages', '/project/50/assessment'):
            self.assertEqual(self.client.get(path).status_code, 403)
        self.assertEqual(self.supervision_reply(2).status_code, 302)
        self.assertEqual(self.supervision_row()['state'], 'completed')
        self.assertEqual(self.client.get('/project/50').status_code, 200)
        self.assertEqual(self.client.get('/project/2').status_code, 403)
        self.assertEqual(self.post('/project/50/stages', data=dict(action='save',number='1',revision='0',result='Teacher cannot submit')).status_code, 400)

    def test_supervision_only_team_owner_can_accept_and_invalid_ids_are_safe(self):
        self.add_candidate()
        conn = self.module.get_db()
        conn.execute('INSERT INTO project_members(project_id,user_id) VALUES(2,4)')
        conn.commit()
        conn.close()
        code = self.supervision_code()
        before = self.database_snapshot()
        self.assertEqual(self.supervision_request(code, user=4).status_code, 403)
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(self.supervision_request(code, project=3).status_code, 403)
        self.assertEqual(self.supervision_request(code, project=2**90).status_code, 400)
        self.assertEqual(self.client.get('/supervision?before=oops').status_code, 400)
        self.sign_in(3)
        self.assertEqual(self.post('/supervision', data={'action':'create'}).status_code, 403)
        self.assertEqual(self.supervision_request(code).status_code, 302)
        self.sign_in(4)
        self.assertNotIn('Смена руководителя · Observed project', self.client.get('/supervision').text)
        self.assertEqual(self.supervision_reply(4).status_code, 403)

    def test_supervision_initial_onboarding_can_recover_legacy_orphaned_reviews(self):
        pid = self.supervision_empty_project()
        conn = self.module.get_db()
        conn.execute("INSERT INTO project_stages(project_id,number,state,result,submitted_by,revision) VALUES(50,1,'review','Legacy submission',3,1)")
        conn.execute("INSERT INTO project_defenses(project_id,state,author_id,revision) VALUES(50,'submitted',3,1)")
        conn.commit()
        conn.close()
        code = self.supervision_code()
        self.assertEqual(self.supervision_request(code, pid).status_code, 302)
        self.assertEqual(self.supervision_reply(2).status_code, 302)
        self.assertIn('Legacy submission', self.client.get('/project/50/stages').text)
        self.assertEqual(self.post('/project/50/stages', data=dict(number='1',revision='1',action='accept')).status_code, 302)
        self.assertEqual(self.post('/project/50/defense', data=dict(revision='1',action='accept')).status_code, 302)

    def test_supervision_empty_teacher_guidance_uses_invitation_not_login(self):
        conn = self.module.get_db()
        conn.execute("INSERT INTO users(id,username,password,role) VALUES(5,'empty-teacher',?,'teacher')", (self.password,))
        conn.commit()
        conn.close()
        self.sign_in(5)
        page = self.client.get('/teacher').text
        self.assertIn('Создайте приглашение в разделе', page)
        self.assertNotIn('добавить ваш логин', page)
        self.assertNotIn('Ученик-владелец добавит вас', page)

    def test_supervision_invite_expiry_revoke_decline_and_errors_do_not_echo_codes(self):
        code = self.supervision_code()
        conn = self.module.get_db()
        iid = conn.execute('SELECT id FROM teacher_invitations').fetchone()[0]
        conn.execute('UPDATE teacher_invitations SET expires=0 WHERE id=?', (iid,))
        conn.commit()
        conn.close()
        self.sign_in(3)
        response = self.post('/supervision', data=dict(action='preview',code=code))
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(code, response.text)
        code = self.supervision_code()
        conn = self.module.get_db()
        iid = conn.execute('SELECT MAX(id) FROM teacher_invitations').fetchone()[0]
        conn.close()
        self.sign_in(1)
        self.assertEqual(self.post('/supervision', data=dict(action='revoke',invitation_id=str(iid))).status_code, 403)
        self.sign_in(2)
        self.assertEqual(self.post('/supervision', data=dict(action='revoke',invitation_id=str(iid))).status_code, 302)
        self.sign_in(3)
        self.assertEqual(self.post('/supervision', data=dict(action='preview',code=code)).status_code, 400)
        code = self.supervision_code()
        self.sign_in(3)
        self.assertEqual(self.post('/supervision', data=dict(action='decline',code=code)).status_code, 302)
        self.sign_in(2)
        self.assertNotIn('student-one', self.client.get('/supervision').text)

    def test_supervision_code_attempts_are_shared_bounded_temporary_and_independent(self):
        self.sign_in(3)
        from auth_security import reserve_supervision_attempt
        with patch('auth_security.time', SimpleNamespace(time=lambda: 10000)):
            for _ in range(10):
                response = self.post('/supervision', data=dict(action='preview',code='invalid'))
                self.assertEqual(response.status_code, 400)
            response = self.post('/supervision', data=dict(action='preview',code='invalid'))
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.headers['Retry-After'], '60')
            self.assertEqual(reserve_supervision_attempt(self.module.get_db, self.module.SECRET_KEY, 4, '127.0.0.1'), 0)
        with patch('auth_security.time', SimpleNamespace(time=lambda: 10061)):
            self.assertEqual(self.post('/supervision', data=dict(action='preview',code='invalid')).status_code, 400)
        conn = self.module.get_db()
        self.assertLessEqual(conn.execute('SELECT MAX(hits) FROM auth_limits').fetchone()[0], 20)
        conn.close()

    def test_supervision_repeat_requests_stale_forms_and_cancel_do_not_duplicate(self):
        pid = self.supervision_empty_project()
        code = self.supervision_code()
        self.assertEqual(self.supervision_request(code, pid).status_code, 302)
        before = self.database_snapshot()
        self.assertEqual(self.supervision_request(code, pid).status_code, 400)
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(self.supervision_reply(3, revision=99).status_code, 400)
        self.assertEqual(self.supervision_reply(3, action='cancel').status_code, 302)
        self.assertEqual(self.supervision_reply(2).status_code, 400)
        self.sign_in(2)
        self.assertNotIn('Consent-only project', self.client.get('/supervision').text)
        self.assertEqual(self.client.get('/project/50').status_code, 403)

    def test_supervision_expired_request_and_changed_teacher_set_cannot_grant_access(self):
        code = self.supervision_code()
        self.supervision_request(code)
        conn = self.module.get_db()
        conn.execute('UPDATE supervision_requests SET expires=0')
        conn.commit()
        conn.close()
        before = self.database_snapshot()
        self.assertEqual(self.supervision_reply(2).status_code, 400)
        self.assertEqual(self.database_snapshot(), before)
        code = self.supervision_code()
        self.assertEqual(self.supervision_request(code).status_code, 302)
        conn = self.module.get_db()
        conn.execute('INSERT INTO project_members(project_id,user_id) VALUES(2,2)')
        conn.commit()
        conn.close()
        before = self.database_snapshot()
        self.assertEqual(self.supervision_reply(2).status_code, 400)
        self.assertEqual(self.database_snapshot(), before)

    def test_supervision_change_keeps_authorship_and_revokes_all_former_access(self):
        self.sign_in(1)
        self.assertEqual(self.post('/project/2/assessment', data=self.assessment_data()).status_code, 302)
        conn = self.module.get_db()
        original = tuple(conn.execute('SELECT * FROM project_assessments WHERE project_id=2').fetchone())
        conn.close()
        code = self.supervision_code()
        self.assertEqual(self.supervision_request(code).status_code, 302)
        self.assertEqual(self.supervision_reply(2).status_code, 302)
        self.sign_in(2)
        self.assertEqual(self.client.get('/project/2').status_code, 403)
        self.sign_in(1)
        before = self.database_snapshot()
        self.assertEqual(self.supervision_reply(1, revision=0).status_code, 400)
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(self.supervision_reply(1).status_code, 302)
        for path in ('/project/2','/project/2/stages','/project/2/defense','/project/2/assessment'):
            self.assertEqual(self.client.get(path).status_code, 403)
        self.assertNotIn('Observed project', self.client.get('/supervision').text)
        self.assertNotIn('Observed project', self.client.get('/notifications').text)
        self.assertEqual(self.post('/project/2/assessment', data=self.assessment_data()).status_code, 403)
        conn = self.module.get_db()
        self.assertEqual(tuple(conn.execute('SELECT * FROM project_assessments WHERE project_id=2').fetchone()), original)
        self.assertEqual([r[0] for r in conn.execute("SELECT m.user_id FROM project_members m JOIN users u ON u.id=m.user_id WHERE m.project_id=2 AND u.role='teacher'")], [2])
        conn.close()
        self.sign_in(3)
        self.assertIn('Иванов Иван', self.client.get('/project/2/assessment').text)
        self.assertEqual(self.supervision_reply(2).status_code, 400)

    def test_supervision_change_waits_for_review_and_all_legacy_teachers(self):
        conn = self.module.get_db()
        conn.execute("INSERT INTO users(id,username,password,role) VALUES(5,'new-supervisor',?,'teacher')", (self.password,))
        conn.execute('INSERT INTO project_members(project_id,user_id) VALUES(2,2)')
        conn.execute("INSERT INTO project_stages(project_id,number,state,result,submitted_by,revision) VALUES(2,1,'review','Submitted topic',3,1)")
        conn.commit()
        conn.close()
        code = self.supervision_code(5)
        self.supervision_request(code)
        self.assertEqual(self.supervision_reply(5).status_code, 302)
        self.assertEqual(self.supervision_reply(1).status_code, 302)
        before = self.database_snapshot()
        self.assertEqual(self.supervision_reply(2).status_code, 400)
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(self.post('/project/2/stages', data=dict(number='1',revision='1',action='accept',teacher_comment='Accepted by previous teacher')).status_code, 302)
        self.assertEqual(self.supervision_reply(2).status_code, 302)
        self.assertEqual(self.supervision_row()['state'], 'completed')
        conn = self.module.get_db()
        self.assertEqual(conn.execute('SELECT reviewed_by FROM project_stages WHERE project_id=2').fetchone()[0], 2)
        conn.close()
        self.sign_in(5)
        self.assertEqual(self.client.get('/project/2').status_code, 200)

    def test_supervision_end_requires_owner_and_is_idempotent(self):
        self.sign_in(1)
        self.assertEqual(self.post('/supervision', data=dict(action='end',project_id='2')).status_code, 302)
        before = self.database_snapshot()
        self.assertEqual(self.supervision_reply(1).status_code, 302)
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(self.client.get('/project/2').status_code, 200)
        self.assertEqual(self.supervision_reply(3).status_code, 302)
        self.sign_in(1)
        self.assertEqual(self.client.get('/project/2').status_code, 403)
        self.sign_in(3)
        self.assertIn('Принять приглашение', self.client.get('/project/2').text)
        self.assertEqual(self.post('/project/2/stages', data=dict(number='1',revision='0',action='save',result='Draft')).status_code, 302)
        self.assertEqual(self.post('/project/2/stages', data=dict(number='1',revision='1',action='submit',result='Draft')).status_code, 400)

    def test_supervision_old_paths_are_closed_reports_preserved_and_csrf_required(self):
        self.sign_in(3)
        before = self.database_snapshot()
        self.assertEqual(self.post('/project/2/add_member', data={'username':'teacher-two'}).status_code, 403)
        self.assertEqual(self.post('/project/2/remove_member/1').status_code, 403)
        self.assertEqual(self.database_snapshot(), before)
        for token in (None, 'bad'):
            for action in ('create','request','approve','end'):
                data = dict(action=action,project_id='2')
                if token is not None:
                    data['csrf_token'] = token
                self.assertEqual(self.client.post('/supervision', data=data).status_code, 400)
        self.assertEqual(self.database_snapshot(), before)
        conn = self.module.get_db()
        conn.execute('UPDATE projects SET owner_id=3 WHERE id=1')
        conn.commit()
        conn.close()
        before = self.database_snapshot()
        self.assertEqual(self.post('/project/1/delete/1').status_code, 302)
        self.assertEqual(self.database_snapshot(), before)

    def test_supervision_reinitialization_and_notification_failure_preserve_records(self):
        from teacher_supervision import initialize_supervision
        pid = self.supervision_empty_project()
        code = self.supervision_code()
        self.assertEqual(self.supervision_request(code, pid).status_code, 302)
        before = self.database_snapshot()
        with patch.dict(os.environ, M_FLOW_DEBUG='0', M_FLOW_TEACHER_USER='', M_FLOW_TEACHER_PASSWORD=''):
            self.module.prepare_database()
        conn = self.module.get_db()
        initialize_supervision(conn)
        conn.commit()
        conn.close()
        self.assertEqual(self.database_snapshot(), before)
        with patch('teacher_supervision.notify_project', side_effect=RuntimeError('Synthetic failure')):
            with self.assertRaises(RuntimeError):
                self.supervision_reply(2)
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(self.supervision_reply(2).status_code, 302)
