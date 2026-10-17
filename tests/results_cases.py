"""Shared SQLite/PostgreSQL regression cases for the existing student home."""
from contextlib import closing
import os
import json
import re
from unittest.mock import patch

from grading import CRITERIA
from student_results import PAGE_SIZE, load_results, result_date
from notifications import notification_target


class StudentResultsCases:
    def result_section(self, query='', user=3):
        self.sign_in(user)
        response = self.client.get('/' + query)
        self.assertEqual(response.status_code, 200)
        return response.text.split('<section id="student-results"')[1].split('</section>')[0]

    def result_keys(self, html):
        return re.findall(r'data-result-key="([^"]+)"', html)

    def seed_result_grade(self, project=1, student=3, teacher=1, state='published', note='Моя личная оценка'):
        with closing(self.module.get_db()) as conn:
            conn.execute('''INSERT INTO project_assessments
                (project_id,student_id,teacher_id,student_name,rubric_version,scores_json,note,state)
                VALUES(?,?,?,?,'school-project-45-v1',?,?,?)''',
                (project,student,teacher,'Личное ФИО',json.dumps({k:m for k,_,m in CRITERIA}),note,state))
            conn.commit()

    def test_results_composition_priority_dates_and_no_report_acceptance(self):
        self.stage_post(3)
        self.stage_post(1, revision=1, action='return', teacher_comment='Уточни цель <script>x</script>')
        self.defense_post()
        self.seed_result_grade()
        with closing(self.module.get_db()) as conn:
            conn.execute("INSERT INTO project_stages(project_id,number,state,reviewed_by,updated_at) VALUES(2,1,'done',1,'2026-10-14 22:30:00')")
            conn.commit()
        before = self.database_snapshot()
        html = self.result_section()
        keys = self.result_keys(html)
        self.assertEqual(set(keys), {'stage-1-1','defense-1-0','grade-1-1','stage-2-1'})
        self.assertEqual(keys[0], 'stage-1-1')
        for text in ('Что исправить','Уточни цель &lt;script&gt;x&lt;/script&gt;','На проверке',
                     'Принято','45 / 45 · оценка 5','15.10.2026 01:30 МСК', 'Решение учителя:',
                     '/project/1/stages#stage-1', 'Доработай результат этапа', '/project/2/stages#stage-2'):
            self.assertIn(text, html)
        self.assertNotIn('<script>x</script>', html)
        self.assertNotIn('alert(&#x27;report&#x27;)', html)
        self.assertEqual(self.database_snapshot(), before)

    def test_results_repeat_submission_replaces_item_and_labels_prior_feedback(self):
        self.stage_post(3)
        self.stage_post(1, revision=1, action='return', teacher_comment='Первое замечание')
        self.assertIn('data-result-state="revision"', self.result_section())
        self.stage_post(3, revision=2)
        html = self.result_section()
        self.assertEqual(self.result_keys(html), ['stage-1-1'])
        self.assertNotIn('data-result-state="revision"', html)
        self.assertIn('Комментарий к предыдущей сдаче — не решение по новой версии', html)
        self.assertIn('Пока её нельзя редактировать', html)
        self.stage_post(1, revision=3, action='accept', teacher_comment='Исправления приняты')
        html = self.result_section()
        self.assertIn('data-result-state="accepted"', html)
        self.assertIn('Исправления приняты', html)
        self.assertNotIn('Первое замечание', html)
        self.assertIn('/project/1/stages#stage-2', html)
        self.assertIn('Все отправленные работы проверены', html)

    def test_results_defense_return_resubmit_accept_without_old_decision(self):
        self.defense_post()
        self.defense_post(user_id=1,revision=1,action='return',comment='Исправь звук')
        html = self.result_section()
        self.assertIn('/project/1/defense', html)
        self.assertIn('Исправь звук', html)
        self.assertIn('data-result-state="revision"', html)
        self.defense_post(revision=2)
        html = self.result_section()
        self.assertEqual(self.result_keys(html), ['defense-1-0'])
        self.assertIn('Комментарий к предыдущей сдаче', html)
        self.defense_post(user_id=1,revision=3,action='accept',comment='Звук исправлен')
        html = self.result_section()
        self.assertIn('data-result-state="accepted"', html)
        self.assertIn('Звук исправлен', html)
        self.assertNotIn('Исправь звук', html)

    def test_results_team_access_and_private_published_grades_only(self):
        self.add_candidate()
        with closing(self.module.get_db()) as conn:
            conn.execute('INSERT INTO project_members VALUES(1,4)')
            conn.commit()
        self.stage_post(3)
        self.stage_post(1,revision=1,action='return',teacher_comment='Общая обратная связь')
        self.seed_result_grade(note='Секретная личная оценка ученика 3')
        self.seed_result_grade(teacher=2,state='draft',note='Секретный черновик учителя')
        self.seed_result_grade(student=4,note='Моя оценка ученика 4')
        html = self.result_section(user=4)
        self.assertIn('Общая обратная связь', html)
        self.assertIn('Моя оценка ученика 4', html)
        self.assertNotIn('Секретная', html)
        self.assertNotIn('Секретный', html)
        html = self.result_section()
        self.assertNotIn('Моя оценка ученика 4', html)
        self.assertNotIn('Секретный', html)
        with closing(self.module.get_db()) as conn:
            conn.execute('DELETE FROM project_members WHERE project_id=1 AND user_id=4')
            conn.commit()
        self.sign_in(4)
        self.assertNotIn('Общая обратная связь', self.client.get('/').text)
        self.assertEqual(self.client.get('/?result_project=1').status_code, 403)

    def test_results_foreign_scope_and_legacy_owner_without_membership(self):
        self.seed_result_grade(project=3,note='Недоступная оценка')
        self.assertNotIn('Недоступная оценка', self.result_section())
        response = self.client.get('/?result_project=3')
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('Private project', response.text)
        with closing(self.module.get_db()) as conn:
            conn.execute('DELETE FROM project_members WHERE project_id=2 AND user_id=3')
            conn.execute("INSERT INTO project_stages(project_id,number,state,result) VALUES(2,1,'progress','Черновик')")
            conn.commit()
        self.assertEqual(self.result_keys(self.result_section('?result_project=2')), ['stage-2-1'])

    def test_results_filters_pagination_bounded_and_stable(self):
        with closing(self.module.get_db()) as conn:
            for pid in range(10,40):
                conn.execute('INSERT INTO projects(id,name,owner_id) VALUES(?,?,3)',(pid,f'Результат {pid}'))
                conn.execute("INSERT INTO project_stages(project_id,number,state,reviewed_by) VALUES(?,1,'progress',1)",(pid,))
            conn.commit()
        html = self.result_section('?result_state=revision&result_page=1')
        first = self.result_keys(html)
        self.assertEqual(len(first), PAGE_SIZE)
        self.assertIn('result_state=revision', html)
        self.assertIn('result_page=2', html)
        second = self.result_keys(self.result_section('?result_state=revision&result_page=2'))
        self.assertFalse(set(first) & set(second))
        self.assertEqual(self.result_keys(self.result_section('?result_state=revision')),first)
        html = self.result_section('?result_project=10&result_state=revision&result_page=999999999')
        self.assertEqual(self.result_keys(html),['stage-10-1'])
        self.assertIn('value="10" selected',html)
        html = self.result_section('?result_state=accepted')
        self.assertIn('Нет результатов по выбранным условиям',html)
        self.assertNotIn('data-result-key=',html)

    def test_results_invalid_parameters_and_expired_session(self):
        plain = self.result_section()
        for query in ('?result_state=bad&result_page=bad','?result_page=-1','?result_page='+'9'*200):
            self.assertEqual(self.result_section(query),plain)
        for value in ('0','-1','bad','99999999999999999999999','%3Cscript%3E'):
            self.assertEqual(self.client.get('/?result_project='+value).status_code,400)
        self.assertEqual(self.client.get('/?result_project=999999').status_code,403)
        with self.client.session_transaction() as session:
            session.clear()
        response = self.client.get('/?result_state=revision')
        self.assertEqual(response.status_code,302)
        self.assertNotIn('Центр результатов',response.text)

    def test_results_missing_dates_comments_drafts_and_no_projects(self):
        with closing(self.module.get_db()) as conn:
            conn.execute("INSERT INTO project_stages(project_id,number,state,reviewed_by,updated_at) VALUES(2,1,'progress',1,'')")
            conn.execute("INSERT INTO project_defenses(project_id,state,updated_at) VALUES(2,'draft','invalid')")
            conn.commit()
        html = self.result_section()
        self.assertIn('Комментарий не указан',html)
        self.assertEqual(html.count('Дата не указана'),2)
        self.assertIn('Ещё не отправлено',html)
        self.assertNotIn('01.01.1970',html)
        for value in ('',None,'bad','9999-12-31 23:59:59','0001-01-01T00:00:00+10:00'):
            self.assertEqual(result_date(dict(updated_at=value,category='revision',kind='stage')),'Дата не указана')
        self.add_candidate()
        self.sign_in(4)
        html = self.client.get('/').text
        self.assertIn('Начни свой проект.',html)
        self.assertIn('Ввести код группы',html)
        self.assertNotIn('id="student-results"',html)

    def test_results_unsubmitted_empty_and_malformed_grade_do_not_invent_completion(self):
        html = self.result_section()
        self.assertIn('Ещё ничего не отправлено',html)
        self.stage_post(3,action='save')
        html = self.result_section()
        self.assertIn('data-result-state="draft"',html)
        self.assertNotIn('Все отправленные работы проверены',html)
        self.seed_result_grade()
        with closing(self.module.get_db()) as conn:
            conn.execute("UPDATE project_assessments SET scores_json='invalid'")
            conn.commit()
        html = self.result_section()
        self.assertIn('Подробности в разделе оценивания',html)
        self.assertIn('Черновик готов к отправке',html)
        self.assertNotIn('Этапы и видеозащита приняты',html)

    def test_results_opening_and_initialization_preserve_records_and_unread(self):
        self.stage_post(3)
        self.stage_post(1,revision=1,action='return',teacher_comment='Нужно исправить')
        self.seed_result_grade()
        before = self.database_snapshot()
        html = self.result_section()
        self.assertIn('Нужно исправить',html)
        self.assertEqual(self.database_snapshot(),before)
        with patch.dict(os.environ, M_FLOW_DEBUG='0'):
            self.module.prepare_database()
            self.module.prepare_database()
        self.assertEqual(self.database_snapshot(),before)
        # Reading notifications is not a review operation either.
        self.post('/notifications/read-all')
        self.assertIn('data-result-state="revision"',self.result_section())

    def test_results_queries_are_constant_and_payload_is_paginated(self):
        self.stage_post(3)
        self.sign_in(3)
        before = self.count_page_queries('/')
        with closing(self.module.get_db()) as conn:
            for pid in range(10,45):
                conn.execute('INSERT INTO projects(id,name,owner_id) VALUES(?,?,3)',(pid,f'Проект {pid}'))
                conn.execute("INSERT INTO project_stages(project_id,number,state,result) VALUES(?,1,'review',?)",(pid,'X'*5000))
            conn.commit()
        self.assertEqual(self.count_page_queries('/'),before)
        self.assertLessEqual(before,13)  # Three bounded reads for the focused project.
        with closing(self.module.get_db()) as conn:
            data = load_results(conn,dict(id=3),dict(project=0,state='all',page=1))
        self.assertEqual(len(data['items']),PAGE_SIZE)
        self.assertEqual(len(data['steps']),PAGE_SIZE)
        self.assertNotIn('X'*5000,self.result_section())

    def test_results_teacher_home_unchanged_and_links_target_existing_forms(self):
        self.sign_in(1)
        self.assertNotIn('id="student-results"',self.client.get('/').text)
        self.stage_post(3)
        html = self.result_section()
        self.assertIn('/project/1/stages#stage-1',html)
        self.assertEqual(self.client.get('/project/1/stages').status_code,200)
        self.assertEqual(self.client.get('/project/1/defense').status_code,200)
        self.assertEqual(self.client.get('/project/1/assessment').status_code,200)

    def test_results_next_step_uses_original_whitespace_and_missing_teacher_rules(self):
        with closing(self.module.get_db()) as conn:
            conn.execute("INSERT INTO project_stages(project_id,number,state,result) VALUES(2,1,'progress',?)", ('\n\t\u00a0',))
            conn.commit()
        html = self.result_section('?result_project=2')
        self.assertIn('Продолжи работу над этапом',html)
        self.assertNotIn('Черновик готов к отправке',html)
        with closing(self.module.get_db()) as conn:
            conn.execute('DELETE FROM project_members WHERE project_id=2 AND user_id=1')
            conn.commit()
        self.assertIn('Принять приглашение',self.result_section('?result_project=2'))

    def test_results_notifications_link_to_specific_stage_without_backfill_or_read(self):
        self.stage_post(3)
        self.stage_post(1,revision=1,action='return',teacher_comment='Замечание')
        self.assertTrue(all(r['target'].endswith('/stages#stage-1') for r in self.notification_rows()))
        with closing(self.module.get_db()) as conn:
            conn.execute("UPDATE notifications SET target='/project/1/stages'")
            conn.commit()
        before = self.database_snapshot()
        self.sign_in(3)
        page = self.client.get('/notifications')
        self.assertIn('href="/project/1/stages#stage-1"',page.text)
        self.assertEqual(self.database_snapshot(),before)
        for key in ('stage:9:1:1','stage:1:6:1','stage:1:1:bad','report:1:1'):
            row = dict(project_id=1,target='/project/1/stages',event_key=key)
            self.assertEqual(notification_target(row),row['target'])
