"""Workflow overview integration on disposable data; no production DB access."""
import unittest
from datetime import date, timedelta
import json
import test_teacher_cabinet as fixtures
from project_workspace import next_step, task_deadline
from project_stages import stages_from_rows
from grading import CRITERIA


class ProjectWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.TeacherCabinetTests.setUpClass()
        cls.module = fixtures.TeacherCabinetTests.module

    @classmethod
    def tearDownClass(cls):
        fixtures.TeacherCabinetTests.tearDownClass()

    def setUp(self):
        self.fixture = fixtures.TeacherCabinetTests()
        self.fixture.setUp()
        self.client = self.fixture.client

    def page(self, user=3, path='/project/2'):
        self.fixture.sign_in(user)
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        return response.text

    def stage(self, user, action='save', number=1, revision=0, **values):
        self.fixture.sign_in(user)
        data = dict(action=action, number=str(number), revision=str(revision), result='Результат работы')
        data.update(values)
        return self.fixture.post('/project/2/stages', data=data)

    def test_empty_project_without_teacher_has_owner_only_guidance_and_no_get_writes(self):
        conn = self.module.get_db()
        conn.execute('DELETE FROM project_members WHERE project_id=2 AND user_id=1')
        conn.execute('DELETE FROM tasks WHERE project_id=2')
        conn.commit(); conn.close()
        before = self.fixture.database_snapshot()
        page = self.page()
        for text in ('data-next-step="teacher-missing"', 'Добавить учителя', 'href="#project-members"',
                     'Нет незавершённых задач со сроком', '0 из 0 задач', 'Принято 0 из 5', 'Участники: 1',
                     'Запись ещё не добавлена', 'Ваша оценка ещё не опубликована'):
            self.assertIn(text, page)
        self.assertEqual(self.fixture.database_snapshot(), before)
        self.assertIn('disabled', self.client.get('/project/2/stages').text)
        self.assertEqual(self.stage(3, 'submit').status_code, 400)
        self.assertEqual(self.fixture.database_snapshot(), before)

    def test_tasks_and_stages_have_independent_progress_and_native_route(self):
        page = self.page()
        self.assertIn('1 из 1 задач выполнено', page)
        self.assertIn('Принято 0 из 5 этапов', page)
        self.assertIn('data-next-step="start"', page)
        self.assertIn('id="task-board"', page)
        self.assertIn('<details class="journey-route">', page)
        self.assertEqual(page.count('class="route-stage'), 5)
        self.assertIn('Откроется после принятия этапа 1 учителем.', page)
        self.assertIn('aria-current="step"', page)
        self.assertEqual(page.count('data-project-progress>'), 1)

    def test_ready_draft_links_to_current_form_without_submitting_from_get(self):
        self.assertEqual(self.stage(3).status_code, 302)
        before = self.fixture.database_snapshot()
        page = self.page()
        self.assertIn('data-next-step="ready"', page)
        self.assertIn('href="/project/2/stages#stage-1"', page)
        self.assertIn('Перейти к отправке', page)
        self.assertEqual(self.fixture.database_snapshot(), before)

    def test_work_in_progress_and_fifth_stage_require_both_description_and_presentation(self):
        stages = stages_from_rows([])
        stages[0].update(state='progress')
        defense = dict(state='draft')
        self.assertEqual(next_step(stages, 'student', False, True, defense)['key'], 'work')
        for stage in stages[:4]:
            stage.update(state='done')
        stages[4].update(state='progress', result='Описание')
        self.assertEqual(next_step(stages, 'student', False, True, defense)['key'], 'work')
        stages[4]['presentation_url'] = 'https://example.com/presentation'
        self.assertEqual(next_step(stages, 'student', False, True, defense)['key'], 'ready')

    def test_complete_return_fix_resubmit_accept_cycle_and_visible_feedback(self):
        self.assertEqual(self.stage(3, 'submit').status_code, 302)
        self.assertIn('data-next-step="await-review"', self.page())
        self.assertIn('data-next-step="review"', self.page(1))
        self.assertEqual(self.stage(1, 'return', revision=1, teacher_comment='Уточните цель <script>x</script>').status_code, 302)
        returned = self.page()
        for text in ('data-next-step="revise"', 'Нужна доработка', 'Что исправить · этап 1',
                     'teacher-one', '&lt;script&gt;x&lt;/script&gt;', 'Последнее обновление этапа:'):
            self.assertIn(text, returned)
        self.assertNotIn('<script>x</script>', returned)
        self.assertIn('data-next-step="await-revision"', self.page(1))
        self.assertEqual(self.stage(3, 'save', revision=2, result='Исправлена цель').status_code, 302)
        self.assertIn('data-next-step="revise"', self.page())
        self.assertEqual(self.stage(3, 'submit', revision=3, result='Исправлена цель').status_code, 302)
        self.assertIn('data-next-step="review"', self.page(1))
        self.assertEqual(self.stage(1, 'accept', revision=4, teacher_comment='Тема согласована').status_code, 302)
        accepted = self.page()
        self.assertIn('Этап 2 /', accepted)
        self.assertIn('Принято 1 из 5', accepted)
        self.assertIn('href="/project/2/stages#stage-2"', accepted)
        self.assertNotIn('class="journey-feedback"', accepted)
        self.assertIn('id="stage-2"', self.client.get('/project/2/stages').text)

    def test_all_stages_accepted_do_not_mark_defense_or_assessment_complete(self):
        for number in range(1, 6):
            fields = dict(presentation_url='https://example.com/slides') if number == 5 else {}
            self.assertEqual(self.stage(3, 'submit', number=number, **fields).status_code, 302)
            self.assertEqual(self.stage(1, 'accept', number=number, revision=1).status_code, 302)
        page = self.page()
        self.assertIn('Принято 5 из 5', page)
        self.assertIn('data-next-step="defense-prepare"', page)
        self.assertIn('Ваша оценка ещё не опубликована', page)
        self.assertNotIn('Проект завершён', page)
        self.assertIn('data-next-step="stages-accepted"', self.page(1))

    def test_role_state_matrix_for_final_work_and_teacher_pending_defense(self):
        stages = stages_from_rows([])
        for stage in stages:
            stage['state'] = 'done'
        for state, key in (('draft','defense-prepare'), ('returned','defense-prepare'),
                           ('submitted','defense-wait'), ('accepted','assessment')):
            with self.subTest(state=state):
                self.assertEqual(next_step(stages, 'student', False, True, dict(state=state))['key'], key)
        self.assertIn('Опубликованной оценки пока нет', next_step(stages, 'student', False, True, dict(state='accepted'))['text'])
        self.assertIn('опубликованные баллы', next_step(stages, 'student', False, True, dict(state='accepted'), True)['text'])
        self.assertEqual(next_step(stages_from_rows([]), 'teacher', False, True, dict(state='submitted'))['key'], 'defense-review')
        self.assertEqual(next_step(stages_from_rows([]), 'teacher', False, True, dict(state='draft'))['key'], 'await-result')

    def test_member_can_work_on_shared_stage_but_cannot_manage_project(self):
        conn = self.module.get_db()
        conn.execute("INSERT INTO users(id,username,password,role) VALUES(4,'student-member',?,'student')", (self.fixture.password,))
        conn.execute('INSERT INTO project_members VALUES(2,4)'); conn.commit(); conn.close()
        page = self.page(4)
        self.assertIn('Подготовить результат', page)
        self.assertNotIn('id="project-members"', page)
        self.assertNotIn('class="add-task-form"', page)
        self.assertEqual(self.stage(4, 'save').status_code, 302)
        self.fixture.sign_in(4)
        before = self.fixture.database_snapshot()
        self.assertEqual(self.fixture.post('/project/2/add_member', data={'username':'teacher-two'}).status_code, 403)
        self.assertEqual(self.fixture.database_snapshot(), before)
        conn = self.module.get_db()
        conn.execute('DELETE FROM project_members WHERE project_id=2 AND user_id=1'); conn.commit(); conn.close()
        page = self.page(4)
        self.assertIn('Попроси владельца', page)
        self.assertNotIn('Добавить учителя</a>', page)
        self.assertEqual(self.stage(4, 'submit', revision=1).status_code, 400)

    def test_teacher_cannot_execute_student_actions_or_accept_unsubmitted_work(self):
        page = self.page(1)
        self.assertIn('data-next-step="await-result"', page)
        self.assertNotIn('class="add-task-form"', page)
        before = self.fixture.database_snapshot()
        for action in ('save', 'submit', 'accept', 'return'):
            self.assertEqual(self.stage(1, action, teacher_comment='Замечание').status_code, 400)
        self.assertEqual(self.fixture.database_snapshot(), before)

    def test_members_include_owner_once_even_for_legacy_missing_membership(self):
        conn = self.module.get_db()
        conn.execute('DELETE FROM project_members WHERE project_id=1 AND user_id=1'); conn.commit(); conn.close()
        page = self.page(3, '/project/1')
        self.assertIn('Участники: 2', page)
        self.assertNotIn('data-next-step="teacher-missing"', page)
        self.assertIn('Добавить', self.page(3, '/project/2'))

    def test_published_grades_are_isolated_and_drafts_are_never_in_overview(self):
        conn = self.module.get_db()
        scores = json.dumps({key: maximum for key, _, maximum in CRITERIA})
        for project, student, teacher, state in ((2,3,1,'published'), (2,3,2,'draft'), (1,3,1,'published'), (2,1,2,'published')):
            conn.execute('''INSERT INTO project_assessments(project_id,student_id,teacher_id,student_name,rubric_version,scores_json,note,state)
                VALUES(?,?,?,?,?,?,?,?)''', (project,student,teacher,'ФИО','school-project-45-v1',scores,'PRIVATE NOTE',state))
        conn.commit(); conn.close()
        page = self.page()
        self.assertEqual(page.count('45 / 45 · оценка 5'), 1)
        self.assertNotIn('teacher-two', page)
        self.assertNotIn('PRIVATE NOTE', page)
        self.assertIn('Опубликовано ваших оценок: 1', self.page(1))

    def test_denied_projects_and_expired_session_do_not_leak_overview(self):
        self.fixture.sign_in(3)
        before = self.fixture.database_snapshot()
        denied = self.client.get('/project/3')
        self.assertEqual(denied.status_code, 403)
        self.assertNotIn('Private project', denied.text)
        self.assertNotIn('project-overview', denied.text)
        self.assertEqual(self.fixture.post('/project/3/stages', data={'action':'submit'}).status_code, 403)
        with self.client.session_transaction() as session:
            session.clear()
        self.assertEqual(self.client.get('/project/2').status_code, 302)
        self.assertEqual(self.client.post('/project/2/stages', data={'action':'submit'}).status_code, 302)
        self.assertEqual(self.fixture.database_snapshot(), before)

    def test_bad_csrf_and_conflicting_revision_leave_existing_result_unchanged(self):
        self.fixture.sign_in(3)
        before = self.fixture.database_snapshot()
        data = dict(action='submit', number='1', revision='0', result='Result')
        for token in (None, 'incorrect', 'old-session-token'):
            fields = dict(data)
            if token is not None:
                fields['csrf_token'] = token
            self.assertEqual(self.client.post('/project/2/stages', data=fields).status_code, 400)
        self.assertEqual(self.fixture.database_snapshot(), before)
        old_token = self.fixture.client.get('/project/2').text
        with self.client.session_transaction() as session:
            session['csrf_token'] = 'rotated-token'
        self.assertIn('test-form-token', old_token)
        self.assertEqual(self.client.post('/project/2/stages', data=dict(data, csrf_token='test-form-token')).status_code, 400)
        self.assertEqual(self.stage(3).status_code, 302)
        before = self.fixture.database_snapshot()
        self.assertEqual(self.stage(3, 'submit', revision=0).status_code, 400)
        self.assertEqual(self.fixture.database_snapshot(), before)

    def test_deadlines_ignore_completed_and_invalid_dates_and_update_after_ajax(self):
        today = date(2026,10,6)
        tasks = [dict(status='todo',deadline='2026-10-15'), dict(status='done',deadline='2026-01-01'),
                 dict(status='todo',deadline='wrong'), dict(status='progress',deadline='2026-10-06')]
        self.assertEqual(task_deadline(tasks,today), 'Срок сегодня: 06.10.2026')
        tasks.append(dict(status='todo',deadline='2026-10-01'))
        self.assertEqual(task_deadline(tasks,today), 'Просроченный срок: 01.10.2026')
        self.assertEqual(task_deadline([],today), 'Нет незавершённых задач со сроком')
        self.assertEqual(task_deadline(tasks[:1],today), 'Ближайший срок: 15.10.2026')
        self.fixture.sign_in(3)
        result = self.fixture.post('/project/1/status', json={'task_id':1,'status':'done'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json['deadline_text'], 'Нет незавершённых задач со сроком')

    def test_query_count_stays_constant_when_tasks_members_and_grades_grow(self):
        before = {}
        for user in (1,3):
            self.fixture.sign_in(user)
            before[user] = self.fixture.count_page_queries('/project/2')
        conn = self.module.get_db()
        for user in range(20,50):
            conn.execute("INSERT INTO users(id,username,password,role) VALUES(?,?,?,'student')", (user,f'participant-{user}',self.fixture.password))
            conn.execute('INSERT INTO project_members VALUES(2,?)', (user,))
            conn.execute('INSERT INTO tasks(project_id,title,assignee_id) VALUES(2,?,?)', ('Task',user))
            conn.execute('''INSERT INTO project_assessments(project_id,student_id,teacher_id,student_name,rubric_version,scores_json,state)
                VALUES(2,?,1,?,'school-project-45-v1',?,'published')''',
                (user,f'participant-{user}',json.dumps({key: maximum for key, _, maximum in CRITERIA})))
        conn.commit(); conn.close()
        for user in (1,3):
            self.fixture.sign_in(user)
            self.assertEqual(self.fixture.count_page_queries('/project/2'), before[user])
            self.assertLessEqual(before[user], 8)

    def test_long_unbroken_user_text_is_escaped_and_feedback_has_keyboard_scroll_access(self):
        title = '<img src=x onerror=alert(1)>' + 'Д' * 50
        comment = '<script>alert(1)</script>' + 'Ц' * 1900
        self.assertEqual(self.stage(3, 'submit').status_code, 302)
        self.assertEqual(self.stage(1, 'return', revision=1, teacher_comment=comment).status_code, 302)
        conn = self.module.get_db()
        conn.execute('UPDATE projects SET name=? WHERE id=2', (title,)); conn.commit(); conn.close()
        before = self.fixture.database_snapshot()
        page = self.page()
        self.assertIn('&lt;img src=x onerror=alert(1)&gt;', page)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', page)
        self.assertNotIn('<img src=x', page)
        self.assertIn('<p tabindex="0" aria-label="Замечание учителя;', page)
        self.assertEqual(self.fixture.database_snapshot(), before)
        with self.client.get('/static/project-workspace.css') as response:
            self.assertIn('overflow-wrap: anywhere', response.text)
            self.assertIn('forced-colors', response.text)

    def test_removed_teacher_does_not_offer_member_an_edit_of_submitted_result(self):
        stages = stages_from_rows([])
        stages[0].update(state='review',result='Sent')
        step = next_step(stages, 'student', False, False, dict(state='draft'))
        self.assertEqual(step['key'], 'teacher-missing')
        self.assertIsNone(step['target'])
        self.assertIsNone(step['action'])

    def test_malformed_legacy_published_score_does_not_break_project_overview(self):
        conn = self.module.get_db()
        conn.execute('''INSERT INTO project_assessments(project_id,student_id,teacher_id,student_name,rubric_version,scores_json,state)
            VALUES(2,3,1,'Ученик','school-project-45-v1','invalid-json','published')''')
        conn.commit(); conn.close()
        before = self.fixture.database_snapshot()
        self.assertIn('Оценка опубликована · подробности в разделе оценивания', self.page())
        self.assertEqual(self.fixture.database_snapshot(), before)


if __name__ == '__main__':
    unittest.main()
