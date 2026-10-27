"""Shared integration cases: disposable SQLite and local PostgreSQL only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import re

from defense_preparation import POINTS, material_url, plan_for, save_plan, initialize_preparation


class DefensePreparationCases:
    def prep_post(self, user=3, project=1, **changes):
        self.sign_in(user)
        values = dict(revision='0', action='save', **{name: f'Тезис: {title}' for name, title, *_ in POINTS})
        values.update(changes)
        return self.post(f'/project/{project}/preparation', data=values)

    def prep_domain(self):
        with closing(self.module.get_db()) as conn:
            return {table: [tuple(r) for r in conn.execute(f'SELECT * FROM {table} ORDER BY 1')]
                    for table in ('project_stages', 'project_defenses', 'work_history', 'work_history_sources',
                                  'project_assessments', 'tasks', 'task_updates', 'project_members')}

    def test_preparation_gets_are_read_only_and_use_project_navigation(self):
        for user in (1, 3):
            self.sign_in(user)
            before = self.database_snapshot()
            response = self.client.get('/project/1/preparation')
            self.assertEqual(response.status_code, 200)
            page = response.text
            self.assertIn('Подготовка к защите', page)
            self.assertIn('План выступления', page)
            self.assertIn('Сохранено 0 из 5 тезисов', page)
            self.assertIn('Заблокирован', page)
            self.assertEqual(page.count('<textarea'), 5 if user == 3 else 0)
            self.assertEqual('>Сохранить план</button>' in page, user == 3)
            self.assertIn('href="/project/1/preparation" aria-current="page"', page)
            for part in ('defense', 'assessment', 'history', 'stages#stage-5-result'):
                self.assertIn(f'href="/project/1/{part}"', page)
            ids = re.findall(r'(?<![-\w])id="([^"]+)"', page)
            self.assertEqual(len(ids), len(set(ids)))
            self.assertEqual(self.database_snapshot(), before)

    def test_preparation_save_persists_shared_notes_without_workflow_changes(self):
        before = self.prep_domain()
        self.assertEqual(self.prep_post(problem='Проблема\nи наблюдение').status_code, 302)
        with closing(self.module.get_db()) as conn:
            record = plan_for(conn, 1)
            self.assertEqual(record['problem'], 'Проблема\nи наблюдение')
            self.assertEqual(record['revision'], 1)
            self.assertEqual(record['updated_by'], 3)
            initialize_preparation(conn)
            conn.commit()
            self.assertEqual(plan_for(conn, 1), record)
        for user in (1, 3):
            self.sign_in(user)
            page = self.client.get('/project/1/preparation').text
            self.assertIn('Проблема\nи наблюдение', page)
            self.assertIn('Сохранено 5 из 5 тезисов', page)
            self.assertIn('student-one', page)
        self.assertEqual(self.prep_domain(), before)
        self.assertEqual(self.prep_post(revision='1', goal='').status_code, 302)
        self.assertIn('Сохранено 4 из 5 тезисов', self.client.get('/project/1/preparation').text)

    def test_preparation_rejects_teacher_foreign_access_and_missing_csrf(self):
        before = self.database_snapshot()
        self.assertEqual(self.prep_post(user=1).status_code, 403)
        for user, project in ((2, 1), (3, 3)):
            self.sign_in(user)
            self.assertEqual(self.client.get(f'/project/{project}/preparation').status_code, 403)
            self.assertEqual(self.prep_post(user=user, project=project).status_code, 403)
        self.sign_in(3)
        for token in (None, 'wrong'):
            data = dict(revision='0', action='save', problem='Do not save')
            if token:
                data['csrf_token'] = token
            self.assertEqual(self.client.post('/project/1/preparation', data=data).status_code, 400)
        self.assertEqual(self.database_snapshot(), before)

    def test_preparation_stale_form_keeps_both_versions_and_prevents_overwrite(self):
        self.prep_post(problem='Актуальная версия')
        before = self.database_snapshot()
        response = self.prep_post(problem='Мой несохранённый вариант')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Мой несохранённый вариант', response.text)
        self.assertIn('Актуальная версия', response.text)
        self.assertIn('Актуальный сохранённый план', response.text)
        self.assertIn('name="revision" value="0"', response.text)
        self.assertRegex(response.text, r'<button[^>]*name="action"[^>]* disabled>Сохранить план')
        self.assertEqual(self.database_snapshot(), before)

    def test_preparation_validates_lengths_actions_and_escapes_notes(self):
        before = self.database_snapshot()
        for changes in (dict(problem='я' * 1201), dict(action='accept'), dict(revision='bad')):
            self.assertEqual(self.prep_post(**changes).status_code, 400)
            self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(self.prep_post(problem='<script>alert("test")</script> & текст').status_code, 302)
        for user in (1, 3):
            self.sign_in(user)
            page = self.client.get('/project/1/preparation').text
            self.assertIn('&lt;script&gt;', page)
            self.assertNotIn('<script>alert(', page)

    def test_preparation_membership_is_rechecked_under_write_lock(self):
        with closing(self.module.get_db()) as conn:
            conn.execute('DELETE FROM project_members WHERE project_id=1 AND user_id=3')
            conn.commit()
            with self.assertRaisesRegex(ValueError, 'участники проекта'):
                save_plan(conn, 1, dict(id=3, role='student'), dict(revision='0', action='save', problem='forbidden'))
            self.assertEqual(plan_for(conn, 1)['revision'], 0)

    def test_preparation_concurrent_first_save_has_one_winner(self):
        def save(text):
            with closing(self.module.get_db()) as conn:
                try:
                    save_plan(conn, 1, dict(id=3, role='student'), dict(revision='0', action='save', problem=text))
                    return 'saved'
                except ValueError:
                    return 'stale'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(save, ['Первая версия', 'Вторая версия']))
        self.assertCountEqual(results, ['saved', 'stale'])
        with closing(self.module.get_db()) as conn:
            record = plan_for(conn, 1)
        self.assertEqual(record['revision'], 1)
        self.assertIn(record['problem'], ('Первая версия', 'Вторая версия'))

    def test_preparation_materials_follow_real_data_and_review_state(self):
        with closing(self.module.get_db()) as conn:
            conn.executemany("INSERT INTO project_stages(project_id,number,state) VALUES(1,?,'done')", [(n,) for n in range(1,5)])
            conn.execute("""INSERT INTO project_stages(project_id,number,state,result,presentation_url,teacher_comment)
                VALUES(1,5,'review','Описание итогового проекта','https://example.com/slides','Замечание к прошлой версии')""")
            conn.execute("""INSERT INTO project_defenses(project_id,state,video_url,teacher_comment)
                VALUES(1,'returned','https://example.com/video','Исправьте звук')""")
            conn.commit()
        before = self.database_snapshot()
        self.sign_in(3)
        page = self.client.get('/project/1/preparation').text
        self.assertEqual(page.count('class="prep-material added"'), 3)
        self.assertIn('Замечание к предыдущей сдаче', page)
        self.assertIn('Исправьте звук', page)
        self.assertIn('href="https://example.com/slides" target="_blank" rel="noopener noreferrer"', page)
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(self.prep_post().status_code, 302)
        self.assertEqual(self.prep_domain()['project_defenses'][0][3], 'returned')

    def test_preparation_material_urls_are_safe_without_external_fetches(self):
        for url in ('javascript:alert(1)', '//example.com/video', 'https://u:p@example.com', 'https://', 'https://[', 'https://example.com/a b'):
            self.assertEqual(material_url(url), '')
        self.assertEqual(material_url('https://example.com/video?x=1&y=2'), 'https://example.com/video?x=1&y=2')
        with closing(self.module.get_db()) as conn:
            conn.execute("INSERT INTO project_stages(project_id,number,state,presentation_url) VALUES(1,5,'progress','javascript:alert(1)')")
            conn.commit()
        self.sign_in(3)
        page = self.client.get('/project/1/preparation').text
        self.assertNotIn('href="javascript:', page)
        self.assertNotIn('class="prep-material added"', page)
