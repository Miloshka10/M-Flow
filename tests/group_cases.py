"""Classroom flows on synthetic SQLite/PostgreSQL fixtures."""
import re
from contextlib import closing
from html import unescape
import os
from types import SimpleNamespace
from unittest.mock import patch


class LearningGroupCases:
    def test_class_analysis_queue_batch_restart_and_version_change(self):
        import class_analysis
        from submission_analysis import AnalysisUnavailable

        gid = self.group_create()
        path = self.assignment_create(gid)
        aid = int(path.rsplit('/', 1)[1])
        with closing(self.module.get_db()) as conn:
            for pid in range(100, 130):
                conn.execute("INSERT INTO users(id,username,password,role) VALUES(?,?,?,'student')",
                             (pid, f'queue-{pid}', self.password))
                conn.execute("INSERT INTO projects(id,name,owner_id) VALUES(?,?,?)", (pid, 'Замеры', pid))
                conn.execute('INSERT INTO project_members(project_id,user_id) VALUES(?,1)', (pid,))
                conn.execute('INSERT INTO learning_group_members(group_id,student_id) VALUES(?,?)', (gid, pid))
                conn.execute('INSERT INTO group_assignment_projects(assignment_id,student_id,project_id) VALUES(?,?,?)',
                             (aid, pid, pid))
                conn.execute("INSERT INTO project_stages(project_id,number,state) VALUES(?,1,'review')", (pid,))
                conn.execute("""INSERT INTO work_history(project_id,kind,number,revision,event,attempt,state)
                    VALUES(?,'stage',1,1,'submit',1,'review')""", (pid,))
            conn.commit()
        with patch.dict(os.environ, M_FLOW_OLLAMA_MODEL='qwen3:4b'):
            ids = re.findall(r'name="history_id"\s+value="(\d+)"', self.client.get(path).text)
            self.assertEqual(len(ids), 30)
            form = dict(action='queue_analysis', history_id=ids)
            self.assertEqual(self.client.post(path, data=form).status_code, 400)
            self.sign_in(2)
            self.assertEqual(self.post(path, data=form).status_code, 403)
            self.sign_in(1)
            self.assertEqual(self.post(path, data=dict(action='queue_analysis', history_id=[ids[0],'999999'])).status_code, 400)
            for _ in range(2):
                self.assertEqual(self.post(path, data=form).status_code, 302)
            with closing(self.module.get_db()) as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM class_analysis_jobs').fetchone()[0], 30)
                interrupted = class_analysis.claim(conn)
                conn.execute('UPDATE class_analysis_jobs SET expires=0 WHERE id=?', (interrupted['id'],))
                conn.commit()
            with patch('submission_analysis.analyze', side_effect=AnalysisUnavailable()):
                self.assertTrue(class_analysis.work_once(self.module.get_db))
            self.assertIn('Ошибка разбора', self.client.get(path).text)
            self.assertEqual(self.post(path, data=form).status_code, 302)

        def finish_while_student_resubmits(*args, **kwargs):
            with closing(self.module.get_db()) as changed:
                changed.execute("UPDATE project_stages SET revision=2 WHERE project_id=100")
                changed.commit()
            return {'summary': 'Проверено'}

        with patch('submission_analysis.analyze', side_effect=finish_while_student_resubmits):
            while class_analysis.work_once(self.module.get_db):
                pass
        with closing(self.module.get_db()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM class_analysis_jobs WHERE state='done'").fetchone()[0], 29)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM class_analysis_jobs WHERE state='stale'").fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM submission_analyses').fetchone()[0], 29)

    def group_create(self, teacher=1, name='10А — исследовательские проекты'):
        self.sign_in(teacher)
        response = self.post('/groups', data=dict(action='create', name=name, description='Работаем над школьными проектами.'))
        self.assertEqual(response.status_code, 302)
        return int(response.headers['Location'].rsplit('/', 1)[1])

    def group_row(self, gid):
        conn = self.module.get_db()
        row = dict(conn.execute('SELECT * FROM learning_groups WHERE id=?', (gid,)).fetchone())
        conn.close()
        return row

    def group_post(self, gid, user=1, action='invite', **fields):
        self.sign_in(user)
        data = dict(action=action, revision=str(self.group_row(gid)['revision']))
        data.update(fields)
        return self.post(f'/groups/{gid}', data=data)

    def group_code(self, gid, teacher=1):
        response = self.group_post(gid, user=teacher)
        self.assertEqual(response.status_code, 200)
        return re.search(r'readonly autocomplete="off" value="([A-Za-z0-9_-]{32})"', response.text).group(1)

    def group_join(self, code, user=3):
        self.sign_in(user)
        return self.post('/groups', data=dict(action='join', code=code))

    def assignment_create(self, gid, title='Измеряем температуру'):
        self.sign_in(1)
        response = self.post(f'/groups/{gid}/assignments', data=dict(
            action='save', title=title, goal='Сравнить показания датчиков',
            due_on='2027-01-15', materials='Три измерения', material_url='https://example.com/guide'))
        self.assertEqual(response.status_code, 302, response.text[:200])
        return response.headers['Location']

    def test_assignment_class_progress_uses_real_submissions_and_separate_consent(self):
        self.add_candidate()
        gid = self.group_create()
        code = self.group_code(gid)
        self.group_join(code)
        self.group_join(code, user=4)
        path = self.assignment_create(gid)
        self.assertIn('Не начал: 2', self.client.get(path).text)
        self.sign_in(2)
        self.assertEqual(self.client.get(path).status_code, 403)
        self.sign_in(3)
        self.assertIn('Три измерения', self.client.get(path).text)
        self.assertNotIn('candidate-engineer', self.client.get(path).text)
        self.assertEqual(self.post(path, data=dict(action='create_project', project_name='Проект датчиков')).status_code, 302)
        pid = int(re.search(r'href="/project/(\d+)">Обзор проекта', self.client.get(path).text)[1])
        before = self.database_snapshot()
        self.assertEqual(self.post(path, data=dict(action='create_project', project_name='Дубликат')).status_code, 400)
        self.assertEqual(self.database_snapshot(), before)
        self.sign_in(1)
        self.assertIn('Нужно согласовать сопровождение: 1', self.client.get(path).text)
        self.assertEqual(self.client.get(f'/project/{pid}').status_code, 403)
        self.sign_in(3)
        self.assertEqual(self.post(path, data=dict(action='propose')).status_code, 302)
        self.assertEqual(self.supervision_reply(1).status_code, 302)
        self.sign_in(3)
        stages = f'/project/{pid}/stages'
        self.assertEqual(self.post(stages, data=dict(action='submit', number='1', revision='0', result='Замеры воздуха')).status_code, 302)
        self.sign_in(1)
        progress = self.client.get(path).text
        self.assertIn('Ждёт проверки: 1', progress)
        self.assertIn('Первая сдача:', progress)
        review_link = unescape(re.search(r'href="([^"]+)">Открыть сдачу', progress)[1])
        self.assertIn(f'group={gid}&assignment=', review_link)
        self.assertIn(f'work=stage-{pid}-1', review_link)
        self.assertEqual(self.client.get(review_link).status_code, 200)
        self.assertEqual(self.center_decide(f'stage-{pid}-1', action='return', comment='Уточните методику').status_code, 302)
        self.assertIn('На доработке: 1', self.client.get(path).text)

    def test_classroom_review_is_scoped_and_team_results_are_personal(self):
        self.add_candidate()
        gid = self.group_create(name='Класс командного проекта')
        code = self.group_code(gid)
        self.group_join(code)
        self.group_join(code, user=4)
        path = self.assignment_create(gid, 'Проверка команды')
        another = self.assignment_create(gid, 'Другое задание')
        self.sign_in(3)
        self.assertEqual(self.post(path, data=dict(action='link_project', project_id='2')).status_code, 302)
        conn = self.module.get_db()
        conn.execute('INSERT INTO project_members(project_id,user_id) VALUES(2,4)')
        conn.execute("INSERT INTO project_stages(project_id,number,state,result) VALUES(2,1,'review','Работа команды')")
        conn.commit()
        conn.close()
        aid = path.rsplit('/', 1)[1]
        other_id = another.rsplit('/', 1)[1]
        self.sign_in(4)
        self.assertIn('Обзор проекта', self.client.get(path).text)
        self.sign_in(1)
        page = self.client.get(path).text
        self.assertIn('Очередь задания: 1', page)
        self.assertIn('Ждёт проверки: 2', page)
        scoped = f'/teacher/review?group={gid}&assignment={aid}&class_view=review'
        self.assertIn('Работа команды', self.client.get(scoped).text)
        self.assertEqual(self.client.get(f'/teacher/review?group={gid}&assignment={other_id}&work=stage-2-1').status_code, 403)
        self.sign_in(2)
        self.assertEqual(self.client.get(scoped).status_code, 403)
        self.sign_in(1)
        conn = self.module.get_db()
        conn.execute("UPDATE project_stages SET state='done' WHERE project_id=2 AND number=1")
        conn.executemany("INSERT INTO project_stages(project_id,number,state) VALUES(2,?,'done')", [(n,) for n in range(2, 6)])
        conn.execute("INSERT INTO project_defenses(project_id,state) VALUES(2,'accepted')")
        conn.commit()
        conn.close()
        self.assertIn('К оцениванию: 2', self.client.get(path + '?view=results').text)
        grade_url = f'/project/2/assessment?student_id=4&group={gid}&assignment={aid}&class_view=to_grade'
        token = re.search(r'name="assessment_version" value="([^"]+)"', self.client.get(grade_url).text)[1]
        data = {**self.assessment_data(), 'student_id': '4', 'assessment_version': token}
        self.assertEqual(self.post(grade_url, data=data).status_code, 302)
        page = self.client.get(path + '?view=results').text
        self.assertIn('К оцениванию: 1', page)
        self.assertIn('Оценка опубликована: 1', page)
        self.sign_in(3)
        self.assertIn('Оценка ещё не опубликована', self.client.get('/project/2/assessment').text)
        self.sign_in(4)
        self.assertIn('45 / 45', self.client.get('/project/2/assessment').text)

    def test_assignment_links_existing_project_and_rejects_invalid_or_stale_actions(self):
        gid = self.group_create()
        self.group_join(self.group_code(gid))
        first = self.assignment_create(gid)
        second = self.assignment_create(gid, title='Другой проект')
        fields = dict(action='save', title='Новые условия', goal='Цель', due_on='2027-01-01', revision='0')
        self.assertEqual(self.post(first, data={**fields, 'material_url': 'javascript:invalid'}).status_code, 400)
        self.assertEqual(self.post(first, data=fields).status_code, 302)
        self.assertEqual(self.post(first, data=fields).status_code, 400)
        self.sign_in(3)
        self.assertIn('Новые условия', self.client.get(first).text)
        self.assertEqual(self.post(first, data=fields).status_code, 403)
        self.assertEqual(self.post(first, data=dict(action='link_project', project_id='1')).status_code, 403)
        self.assertEqual(self.post(first, data=dict(action='link_project', project_id='2')).status_code, 302)
        self.assertEqual(self.post(second, data=dict(action='link_project', project_id='2')).status_code, 400)
        self.assertEqual(self.group_post(gid, action='archive').status_code, 302)
        self.sign_in(3)
        self.assertIn('Обзор проекта', self.client.get(first).text)
        self.assertEqual(self.post(second, data=dict(action='create_project', project_name='Архив')).status_code, 403)

    def test_student_assignment_feed_on_home_and_across_groups(self):
        first_group = self.group_create(name='Класс проектов')
        self.group_join(self.group_code(first_group))
        second_group = self.group_create(name='Инженерная группа')
        self.group_join(self.group_code(second_group))
        hidden_group = self.group_create(name='Чужая группа')
        first = self.assignment_create(first_group, 'Измерение <температуры>')
        second = self.assignment_create(second_group, 'Проектирование модели')
        hidden = self.assignment_create(hidden_group, 'Чужое задание')
        self.assertEqual(self.client.get('/groups/assignments').status_code, 403)
        self.sign_in(3)
        for url in ('/', '/groups/assignments'):
            page = self.client.get(url)
            self.assertEqual(page.status_code, 200)
            self.assertIn('Измерение &lt;температуры&gt;', page.text)
            self.assertIn('Проектирование модели', page.text)
            self.assertIn(f'href="{first}"', page.text)
            self.assertNotIn('Чужое задание', page.text)
        self.assertEqual(self.client.get(hidden).status_code, 403)
        self.assertEqual(self.post(first, data=dict(action='link_project', project_id='2')).status_code, 302)
        with patch.object(self.module.assignments, 'PAGE_SIZE', 1):
            page = self.client.get('/groups/assignments').text
            self.assertIn(f'href="{second}"', page)
            self.assertIn('/groups/assignments?page=2', page)
            page = self.client.get('/groups/assignments?page=2').text
            self.assertIn('Проект связан', page)
            self.assertIn(f'href="{first}"', page)
        self.assertEqual(self.group_post(second_group, action='archive').status_code, 302)
        self.assertEqual(self.group_post(first_group, user=3, action='leave').status_code, 302)
        page = self.client.get('/groups/assignments').text
        self.assertNotIn('Проектирование модели', page)
        self.assertNotIn('Измерение', page)

    def test_archive_is_paginated_and_does_not_exhaust_active_group_limit(self):
        conn = self.module.get_db()
        conn.executemany('INSERT INTO learning_groups(teacher_id,name,archived) VALUES(1,?,1)',
                         [(f'Archived class {index:02}',) for index in range(50)])
        first = conn.execute('SELECT MIN(id) FROM learning_groups').fetchone()[0]
        conn.commit()
        conn.close()
        gid = self.group_create()
        self.assertNotIn('Archived class', self.client.get('/groups').text)
        archive = self.client.get('/groups?archive=1&page=3').text
        self.assertIn('Archived class 00', archive)
        self.assertNotIn('Archived class 49', archive)
        with patch('learning_groups.MAX_GROUPS', 1):
            self.assertEqual(self.group_post(first, action='restore').status_code, 400)
            self.assertEqual(self.group_post(gid, action='archive').status_code, 302)
            self.assertEqual(self.group_post(first, action='restore').status_code, 302)

    def test_group_next_step_precedes_settings_and_student_can_open_submitted_defense(self):
        gid = self.group_create()
        self.group_join(self.group_code(gid))
        self.assertEqual(self.group_post(gid, user=3, action='select', project_id='2').status_code, 302)
        self.assertEqual(self.post('/project/2/defense', data=dict(action='submit', revision='0',
            video_url='https://example.com/recording', description='Запись выступления')).status_code, 302)
        page = self.client.get(f'/groups/{gid}').text
        self.assertLess(page.index('Следующий шаг проекта'), page.index('id="group-project"'))
        self.assertIn('Видеозащита ждёт проверки', page)
        self.assertIn('Открыть видеозащиту', page)
        self.assertNotIn('Проверить видеозащиту', page)

    def test_groups_consent_join_is_reusable_idempotent_and_does_not_grant_projects(self):
        self.add_candidate(discoverable=0)
        gid = self.group_create(teacher=2)
        code = self.group_code(gid, teacher=2)
        self.sign_in(3)
        preview = self.post('/groups', data=dict(action='preview', code=code))
        self.assertEqual(preview.status_code, 200)
        self.assertIn('Подтвердить вступление', preview.text)
        conn = self.module.get_db()
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM learning_group_members').fetchone()[0], 0)
        self.assertNotIn(code, conn.execute('SELECT code_hash FROM learning_group_invitations').fetchone()[0])
        conn.close()
        for user in (3, 4, 3):
            self.assertEqual(self.group_join(code, user).status_code, 302)
        conn = self.module.get_db()
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM learning_group_members WHERE group_id=?', (gid,)).fetchone()[0], 2)
        conn.close()
        self.sign_in(2)
        page = self.client.get(f'/groups/{gid}')
        self.assertIn('student-one', page.text)
        self.assertIn('candidate-engineer', page.text)
        self.assertEqual(self.client.get('/project/2').status_code, 403)
        self.sign_in(3)
        self.assertNotIn('candidate-engineer', self.client.get(f'/groups/{gid}').text)
        self.assertIn('Учебные группы', self.client.get('/').text)
        self.sign_in(4)
        self.assertIn('Создать проект <svg', self.client.get(f'/groups/{gid}').text)
        self.assertNotIn('value="propose"', self.client.get(f'/groups/{gid}').text)

    def test_groups_project_selection_is_owner_only_and_proposal_reuses_supervision(self):
        gid = self.group_create(teacher=2)
        code = self.group_code(gid, teacher=2)
        self.group_join(code)
        for pid in (1, 3, 2**90):
            self.assertIn(self.group_post(gid, user=3, action='select', project_id=str(pid)).status_code, (400, 403))
        self.assertEqual(self.group_post(gid, user=3, action='select', project_id='2').status_code, 302)
        self.sign_in(2)
        page = self.client.get(f'/groups/{gid}').text
        self.assertIn('Observed project', page)
        self.assertNotIn('href="/project/2"', page)
        self.assertEqual(self.client.get('/project/2').status_code, 403)
        self.assertEqual(self.group_post(gid, user=3, action='propose', project_id='2').status_code, 302)
        self.assertEqual(self.supervision_row()['kind'], 'change')
        self.assertEqual(self.supervision_reply(2).status_code, 302)
        self.assertEqual(self.client.get('/project/2').status_code, 403)
        self.assertEqual(self.supervision_reply(1).status_code, 302)
        self.sign_in(2)
        page = self.client.get(f'/groups/{gid}').text
        self.assertIn('href="/project/2"', page)
        self.assertIn('0 из 5 приняты', page)
        self.assertEqual(self.client.get('/project/2').status_code, 200)
        self.assertEqual(self.group_post(gid, user=3, action='propose', project_id='2').status_code, 400)

    def test_groups_initial_supervision_and_selection_are_atomic(self):
        pid = self.supervision_empty_project()
        gid = self.group_create()
        self.group_join(self.group_code(gid))
        before = self.database_snapshot()
        with patch('teacher_supervision.notify_project', side_effect=RuntimeError('synthetic notification failure')):
            with self.assertRaises(RuntimeError):
                self.group_post(gid, user=3, action='propose', project_id=str(pid))
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(self.group_post(gid, user=3, action='propose', project_id=str(pid)).status_code, 302)
        self.assertEqual(self.supervision_row()['kind'], 'connect')
        self.assertEqual(self.supervision_reply(1).status_code, 302)
        self.assertEqual(self.client.get('/project/50').status_code, 200)

    def test_groups_student_next_step_tracks_existing_workflow_and_pending_consent(self):
        pid = self.supervision_empty_project()
        gid = self.group_create()
        self.group_join(self.group_code(gid))
        self.group_post(gid, user=3, action='select', project_id=str(pid))
        self.assertIn('Согласуй сопровождение проекта', self.client.get(f'/groups/{gid}').text)
        self.group_post(gid, user=3, action='propose', project_id=str(pid))
        self.assertIn('Сопровождение ожидает согласования', self.client.get(f'/groups/{gid}').text)
        self.assertNotIn('value="propose"', self.client.get(f'/groups/{gid}').text)
        self.supervision_reply(1)
        self.sign_in(3)
        self.assertIn('Согласуй тему с учителем', self.client.get(f'/groups/{gid}').text)
        self.assertNotIn('value="propose"', self.client.get(f'/groups/{gid}').text)

    def test_groups_validation_and_changed_project_ownership_do_not_disclose_previous_choice(self):
        self.sign_in(1)
        for name, description in (('', ''), ('x' * 121, ''), ('Valid', 'x' * 601)):
            self.assertEqual(self.post('/groups', data=dict(action='create', name=name, description=description)).status_code, 400)
        gid = self.group_create()
        code = self.group_code(gid)
        self.assertEqual(self.group_join(code, user=2).status_code, 403)
        self.group_join(code)
        self.group_post(gid, user=3, action='select', project_id='2')
        self.add_candidate()
        conn = self.module.get_db()
        conn.execute('UPDATE projects SET owner_id=4 WHERE id=2')
        conn.commit()
        conn.close()
        for uid in (1, 3):
            self.sign_in(uid)
            self.assertNotIn('Observed project', self.client.get(f'/groups/{gid}').text)
        self.assertEqual(self.group_post(gid, user=3, action='select', project_id='2').status_code, 403)

    def test_groups_metadata_requires_teacher_access_and_does_not_reveal_private_grades(self):
        self.add_candidate(discoverable=0)
        gid = self.group_create(teacher=2)
        code = self.group_code(gid, teacher=2)
        self.group_join(code)
        self.group_join(code, user=4)
        self.group_post(gid, user=3, action='select', project_id='2')
        self.sign_in(1)
        data = self.assessment_data()
        data['note'] = 'PRIVATE-GRADE-NOTE-DO-NOT-LEAK'
        self.assertEqual(self.post('/project/2/assessment', data=data).status_code, 302)
        conn = self.module.get_db()
        conn.execute("INSERT INTO project_stages(project_id,number,state,result,submitted_by,revision) VALUES(2,1,'review','PRIVATE-WORK-TEXT',3,1)")
        conn.commit()
        conn.close()
        self.sign_in(2)
        page = self.client.get(f'/groups/{gid}').text
        for private in ('PRIVATE-GRADE', 'PRIVATE-WORK', '1 из 5 приняты', '45/45'):
            self.assertNotIn(private, page)
        self.assertNotIn('href="/project/2/stages"', page)
        self.sign_in(4)
        self.assertNotIn('Observed project', self.client.get(f'/groups/{gid}').text)
        self.sign_in(1)
        self.assertEqual(self.client.get(f'/groups/{gid}').status_code, 403)
        self.assertNotIn('10А — исследовательские проекты', self.client.get('/groups').text)

    def test_groups_roster_tracks_existing_stage_reviews_defense_and_own_grade(self):
        gid = self.group_create()
        self.group_join(self.group_code(gid))
        self.group_post(gid, user=3, action='select', project_id='2')
        conn = self.module.get_db()
        conn.execute("INSERT INTO project_stages(project_id,number,state,result,submitted_by,revision) VALUES(2,1,'review','Do not fetch this body',3,1)")
        conn.execute("INSERT INTO project_defenses(project_id,state,author_id,revision) VALUES(2,'submitted',3,1)")
        conn.commit()
        conn.close()
        self.sign_in(1)
        data = self.assessment_data()
        self.assertEqual(self.post('/project/2/assessment', data=data).status_code, 302)
        page = self.client.get(f'/groups/{gid}?view=review').text
        self.assertIn('Ожидают решения: 2', page)
        self.assertIn('5 · 45/45', page)
        self.assertIn('Этап 1 · На проверке', page)
        self.assertNotIn('Do not fetch this body', page)
        self.assertEqual(self.client.get(f'/groups/{gid}?view=unlinked').text.count('data-label="Ученик"'), 0)
        conn = self.module.get_db()
        conn.execute("UPDATE project_stages SET updated_at='2000-01-01 12:00:00' WHERE project_id=2")
        conn.execute("UPDATE project_defenses SET updated_at='2000-01-01 12:00:00' WHERE project_id=2")
        conn.commit()
        conn.close()
        self.assertIn('Без обновлений 14 дней', self.client.get(f'/groups/{gid}?view=stale').text)
        self.sign_in(3)
        self.assertIn('5 · 45/45', self.client.get(f'/groups/{gid}').text)
        self.sign_in(1)
        data['action'] = 'draft'
        self.post('/project/2/assessment', data=data)
        self.assertIn('Черновик оценки', self.client.get(f'/groups/{gid}').text)
        self.sign_in(3)
        self.assertNotIn('Черновик оценки', self.client.get(f'/groups/{gid}').text)
        self.assertNotIn('45/45', self.client.get(f'/groups/{gid}').text)
        conn = self.module.get_db()
        conn.execute("UPDATE project_assessments SET scores_json='[]',state='published' WHERE project_id=2")
        conn.commit()
        conn.close()
        self.assertEqual(self.client.get(f'/groups/{gid}').status_code, 200)

    def test_groups_code_rotation_revocation_expiry_archive_and_stale_settings(self):
        gid = self.group_create()
        code = self.group_code(gid)
        replacement = self.group_code(gid)
        self.assertEqual(self.group_join(code).status_code, 400)
        self.assertEqual(self.group_join(replacement).status_code, 302)
        self.assertEqual(self.group_post(gid, action='edit', revision='0', name='Stale').status_code, 400)
        self.assertEqual(self.group_post(gid, action='edit', name='Новая группа <script>', description='Описание').status_code, 302)
        self.sign_in(1)
        page = self.client.get(f'/groups/{gid}').text
        self.assertIn('Новая группа &lt;script&gt;', page)
        self.assertNotIn('Новая группа <script>', page)
        self.assertEqual(self.group_post(gid, action='archive').status_code, 302)
        self.assertEqual(self.group_join(replacement).status_code, 400)
        self.assertEqual(self.group_post(gid, user=3, action='select', project_id='2').status_code, 403)
        self.assertEqual(self.group_post(gid, action='restore').status_code, 302)
        self.assertEqual(self.group_join(replacement).status_code, 400)
        fresh = self.group_code(gid)
        conn = self.module.get_db()
        conn.execute('UPDATE learning_group_invitations SET expires=0 WHERE group_id=?', (gid,))
        conn.commit()
        conn.close()
        response = self.group_join(fresh)
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(fresh, response.text)
        fresh = self.group_code(gid)
        self.assertEqual(self.group_post(gid, action='revoke').status_code, 302)
        self.assertEqual(self.group_join(fresh).status_code, 400)

    def test_groups_unauthorized_mutations_leave_and_removal_preserve_project_domain(self):
        self.add_candidate()
        gid = self.group_create()
        code = self.group_code(gid)
        self.group_join(code)
        for user in (2, 4):
            self.sign_in(user)
            self.assertEqual(self.client.get(f'/groups/{gid}').status_code, 403)
            self.assertEqual(self.group_post(gid, user=user, action='archive').status_code, 403)
        for action in ('invite', 'revoke', 'edit', 'archive', 'restore', 'remove'):
            self.assertEqual(self.group_post(gid, user=3, action=action, student_id='3').status_code, 403)
        self.sign_in(3)
        self.assertEqual(self.post('/groups', data=dict(action='create', name='No')).status_code, 403)
        def domain():
            conn = self.module.get_db()
            data = tuple((table, tuple(tuple(r) for r in conn.execute(f'SELECT * FROM {table} ORDER BY 1')))
                         for table in ('projects', 'project_members', 'tasks', 'project_stages', 'project_defenses', 'project_assessments'))
            conn.close()
            return data
        before = domain()
        self.assertEqual(self.group_post(gid, user=3, action='leave').status_code, 302)
        self.assertEqual(domain(), before)
        self.assertEqual(self.group_join(code).status_code, 302)
        self.assertEqual(self.group_post(gid, action='remove', student_id='3').status_code, 302)
        self.assertEqual(domain(), before)
        self.sign_in(3)
        self.assertEqual(self.client.get(f'/groups/{gid}').status_code, 403)
        self.assertEqual(self.client.get('/project/2').status_code, 200)

    def test_groups_pagination_filters_limits_and_reinitialization_preserve_members(self):
        gid = self.group_create()
        conn = self.module.get_db()
        conn.executemany('INSERT INTO users(id,username,password,role) VALUES(?,?,?,?)',
            [(100+i, f'group-student-{i:02d}', self.password, 'student') for i in range(35)])
        conn.executemany('INSERT INTO learning_group_members(group_id,student_id) VALUES(?,?)', [(gid, 100+i) for i in range(35)])
        conn.commit()
        conn.close()
        self.sign_in(1)
        first = self.client.get(f'/groups/{gid}')
        self.assertEqual(first.text.count('data-label="Ученик"'), 30)
        self.assertIn('Следующая страница', first.text)
        self.assertEqual(self.client.get(f'/groups/{gid}?page=2').text.count('data-label="Ученик"'), 5)
        self.assertEqual(self.client.get(f'/groups/{gid}?q=student-34').text.count('data-label="Ученик"'), 1)
        for query in ('page=-1', 'page=x', 'page=99999999999', 'view=unknown'):
            self.assertEqual(self.client.get(f'/groups/{gid}?{query}').status_code, 400)
        before = self.database_snapshot()
        with patch.dict(os.environ, M_FLOW_DEBUG='0', M_FLOW_TEACHER_USER='', M_FLOW_TEACHER_PASSWORD=''):
            self.module.prepare_database()
            self.module.prepare_database()
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(self.client.get(f'/groups/{2**90}').status_code, 400)
        with patch('learning_groups.MAX_GROUPS', 1):
            self.assertEqual(self.post('/groups', data=dict(action='create', name='Too many')).status_code, 400)
        code = self.group_code(gid)
        with patch('learning_groups.MAX_MEMBERS', 35):
            self.assertEqual(self.group_join(code).status_code, 400)

    def test_groups_codes_are_rate_limited_without_blocking_the_school_or_project_invites(self):
        self.sign_in(3)
        with patch('auth_security.time', SimpleNamespace(time=lambda: 10000)):
            for _ in range(10):
                self.assertEqual(self.post('/groups', data=dict(action='preview', code='bad')).status_code, 400)
            response = self.post('/groups', data=dict(action='join', code='bad'))
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.headers['Retry-After'], '60')
            from auth_security import reserve_supervision_attempt
            self.assertEqual(reserve_supervision_attempt(self.module.get_db, self.module.SECRET_KEY, 4, '127.0.0.1', purpose='learning-groups'), 0)
            self.assertEqual(reserve_supervision_attempt(self.module.get_db, self.module.SECRET_KEY, 3, '127.0.0.1'), 0)
        with patch('auth_security.time', SimpleNamespace(time=lambda: 10061)):
            self.assertEqual(self.post('/groups', data=dict(action='preview', code='bad')).status_code, 400)

    def test_groups_all_forms_have_csrf_and_rejected_actions_do_not_change_rosters(self):
        from test_teacher_cabinet import FormTokens
        gid = self.group_create()
        code = self.group_code(gid)
        self.group_join(code)
        for uid in (1, 3):
            self.sign_in(uid)
            for path in ('/groups', f'/groups/{gid}'):
                page = self.client.get(path)
                self.assertEqual(page.status_code, 200)
                forms = FormTokens(page.text)
                for form in forms.forms:
                    self.assertEqual(form['tokens'], ['test-form-token'] if form['method'] == 'post' else [])
            before = self.database_snapshot()
            self.assertEqual(self.client.post(f'/groups/{gid}', data={'action': 'leave'}).status_code, 400)
            self.assertEqual(self.database_snapshot(), before)

    def test_groups_membership_revoked_during_request_returns_forbidden(self):
        gid = self.group_create()
        self.group_join(self.group_code(gid))
        def revoke(conn, user, group_id, form):
            conn.execute('DELETE FROM learning_group_members WHERE group_id=? AND student_id=?', (group_id, user['id']))
            conn.commit()
            raise PermissionError('Membership changed during request')
        self.sign_in(3)
        with patch.object(self.module, 'change_group', side_effect=revoke):
            response = self.post(f'/groups/{gid}', data=dict(action='select', project_id='2'))
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('Observed project', response.text)

    def test_groups_upgrade_from_old_schema_keeps_existing_results_and_grades(self):
        self.stage_post(3)
        self.sign_in(1)
        self.post('/project/1/assessment', data=self.assessment_data())
        tables = ('users', 'projects', 'project_members', 'tasks', 'task_updates', 'project_stages',
                  'project_defenses', 'project_assessments', 'notifications', 'teacher_invitations', 'supervision_requests')
        def old_data():
            conn = self.module.get_db()
            rows = tuple((t, tuple(tuple(r) for r in conn.execute(f'SELECT * FROM {t} ORDER BY 1'))) for t in tables)
            conn.close()
            return rows
        before = old_data()
        conn = self.module.get_db()
        classroom_tables = ('group_assignment_projects', 'group_assignments',
                            'learning_group_invitations', 'learning_group_members', 'learning_groups')
        for table in classroom_tables:
            conn.execute(f'DROP TABLE {table}')
        conn.commit()
        conn.close()
        with patch.dict(os.environ, M_FLOW_DEBUG='0', M_FLOW_TEACHER_USER='', M_FLOW_TEACHER_PASSWORD=''):
            self.module.prepare_database()
            self.module.prepare_database()
        self.assertEqual(old_data(), before)
        conn = self.module.get_db()
        for table in classroom_tables:
            self.assertEqual(conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
        conn.close()
