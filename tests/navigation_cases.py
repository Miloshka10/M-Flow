"""Shared navigation and home-state checks on disposable SQLite/PostgreSQL."""
from contextlib import closing
import re


class NavigationCases:
    def test_shared_workspace_covers_all_existing_sections_without_get_writes(self):
        paths = ('/', '/project/1', '/project/1/stages', '/project/1/team', '/project/1/defense',
                 '/project/1/assessment', '/project/1/history', '/groups', '/teams', '/supervision',
                 '/notifications', '/profile', '/settings')
        for uid in (1, 3):
            self.sign_in(uid)
            before = self.database_snapshot()
            for path in paths:
                with self.subTest(user=uid, path=path):
                    page = self.client.get(path)
                    self.assertEqual(page.status_code, 200)
                    self.assertIn('class="mf-v2 ui-authenticated"', page.text)
                    self.assertEqual(page.text.count('/static/interface.css'), 1)
                    self.assertEqual(page.text.count('id="account-panel"'), 1)
                    ids = re.findall(r'(?<![-\w])id="([^"]+)"', page.text)
                    self.assertEqual(len(ids), len(set(ids)), 'Deep-link and form targets must be unique')
                    self.assertIn('action="/logout"', page.text)
                    self.assertIn('data-interface-shell=', page.text)
            self.assertEqual(self.database_snapshot(), before)

    def test_home_group_cards_are_escaped_and_limited_to_own_memberships(self):
        with closing(self.module.get_db()) as conn:
            conn.execute("INSERT INTO learning_groups(id,teacher_id,name) VALUES(700,1,?)", ('<img src=x onerror=alert(1)>',))
            conn.execute("INSERT INTO learning_groups(id,teacher_id,name) VALUES(701,2,'Secret class')")
            conn.execute('INSERT INTO learning_group_members(group_id,student_id,project_id) VALUES(700,3,2)')
            conn.commit()
        self.sign_in(3)
        before = self.database_snapshot()
        page = self.client.get('/').text
        self.assertIn('/groups/700', page)
        self.assertIn('&lt;img src=x onerror=alert(1)&gt;', page)
        self.assertNotIn('<img src=x onerror=alert(1)>', page)
        self.assertNotIn('/groups/701', page)
        self.assertNotIn('Secret class', page)
        self.assertEqual(self.database_snapshot(), before)

    def home_focus(self, project=2):
        self.sign_in(3)
        page = self.client.get(f'/?project={project}')
        self.assertEqual(page.status_code, 200)
        return page.text.split('<section class="home-focus"')[1].split('</section>')[0]

    def test_navigation_roles_and_post_logout_remain_accessible(self):
        for uid, label, home in ((3, 'Мои проекты', '/'), (1, 'Кабинет', '/teacher')):
            self.sign_in(uid)
            page = self.client.get(home).text
            header = page.split('<header class="main-header')[1].split('</header>')[0]
            for text in (label, 'Учебные группы', 'Мой аккаунт', 'Меню', 'Мой профиль',
                         '/settings', 'action="/logout"', 'csrf_token', 'aria-controls="account-panel"'):
                self.assertIn(text, header)
            for text in ('class="avatar"', 'href="/supervision"'):
                self.assertNotIn(text, header)
            trigger = header.split('<details class="account-menu">')[1].split('</summary>')[0]
            self.assertNotIn('⌄', trigger)
            self.assertEqual(self.client.get('/logout').status_code, 405)
            self.assertEqual(self.client.post('/logout').status_code, 400)

    def test_teacher_cabinet_link_is_current_on_both_home_routes(self):
        self.sign_in(1)
        for path in ('/', '/teacher'):
            page = self.client.get(path)
            self.assertEqual(page.status_code, 200)
            header = page.text.split('<header class="main-header')[1].split('</header>')[0]
            self.assertRegex(header, r'<a href="/teacher"[^>]*data-nav-key="home"[^>]*aria-current="page"')
            self.assertIn('Кабинет', header)

    def test_home_switch_rejects_foreign_projects_and_never_writes(self):
        self.sign_in(3)
        before = self.database_snapshot()
        for value in ('0', '-1', 'abc', '999999999999999999999'):
            self.assertEqual(self.client.get('/?project=' + value).status_code, 400)
        for value in ('3', '99999'):
            response = self.client.get('/?project=' + value)
            self.assertEqual(response.status_code, 403)
            self.assertNotIn('Private project', response.text)
        for pid, name in ((1, 'Owned project'), (2, 'Observed project')):
            self.assertIn(name, self.home_focus(pid))
        self.assertEqual(self.database_snapshot(), before)

    def test_home_tracks_return_resubmission_and_next_stage_without_old_feedback(self):
        self.assertIn('data-home-step="start"', self.home_focus())
        self.sign_in(3)
        self.assertEqual(self.post('/project/2/stages', data=dict(number=1, revision=0, action='submit', result='Тема')).status_code, 302)
        self.assertIn('data-home-step="await-review"', self.home_focus())
        self.sign_in(1)
        self.post('/project/2/stages', data=dict(number=1, revision=1, action='return', teacher_comment='Уточни <цель>'))
        focus = self.home_focus()
        self.assertIn('data-home-step="revise"', focus)
        self.assertIn('Уточни &lt;цель&gt;', focus)
        self.sign_in(3)
        self.post('/project/2/stages', data=dict(number=1, revision=2, action='submit', result='Уточнение'))
        focus = self.home_focus()
        self.assertIn('data-home-step="await-review"', focus)
        self.assertNotIn('Уточни &lt;цель&gt;', focus)
        self.sign_in(1)
        self.post('/project/2/stages', data=dict(number=1, revision=3, action='accept'))
        self.assertIn('/project/2/stages#stage-2', self.home_focus())

    def test_home_finished_stages_and_defense_use_only_own_published_grade(self):
        self.add_candidate()
        with closing(self.module.get_db()) as conn:
            conn.execute('INSERT INTO project_members(project_id,user_id) VALUES(2,4)')
            conn.executemany("INSERT INTO project_stages(project_id,number,state) VALUES(2,?,'done')", [(n,) for n in range(1, 6)])
            conn.execute("INSERT INTO project_defenses(project_id,state,teacher_comment) VALUES(2,'returned','Исправь звук')")
            conn.commit()
        self.assertIn('Исправь звук', self.home_focus())
        with closing(self.module.get_db()) as conn:
            conn.execute("UPDATE project_defenses SET state='submitted' WHERE project_id=2")
            conn.commit()
        focus = self.home_focus()
        self.assertIn('data-home-step="defense-wait"', focus)
        self.assertNotIn('Исправь звук', focus)
        with closing(self.module.get_db()) as conn:
            conn.execute("UPDATE project_defenses SET state='accepted' WHERE project_id=2")
            conn.commit()
        self.seed_result_grade(project=2, student=3, state='draft')
        self.seed_result_grade(project=2, student=4, note='Чужая оценка')
        self.assertIn('Опубликованной оценки пока нет', self.home_focus())
        self.assertNotIn('Чужая оценка', self.home_focus())
        with closing(self.module.get_db()) as conn:
            conn.execute("UPDATE project_assessments SET state='published' WHERE project_id=2 AND student_id=3")
            conn.commit()
        self.assertIn('Твои опубликованные баллы', self.home_focus())

    def test_home_empty_entry_and_missing_teacher_keep_workflow_available(self):
        self.add_candidate()
        self.sign_in(4)
        before = self.database_snapshot()
        page = self.client.get('/').text
        for text in ('Начни свой проект.', '/groups#group-code', 'Ввести код группы', 'Создать проект', '/teams'):
            self.assertIn(text, page)
        self.assertIn('id="student-results"', page)  # The sidebar result link must have a target even before the first project.
        self.assertEqual(self.database_snapshot(), before)
        with closing(self.module.get_db()) as conn:
            conn.execute('DELETE FROM project_members WHERE project_id=2 AND user_id=1')
            conn.commit()
        focus = self.home_focus()
        self.assertIn('data-home-step="teacher-missing"', focus)
        self.assertIn('href="/supervision"', focus)
        self.sign_in(3)
        page = self.client.get('/?project=1&result_state=review').text
        self.assertIn('name="project" value="1"', page)
        self.assertIn('<details class="home-details" open>', page)

    def test_home_return_without_stored_comment_offers_a_clear_explanation(self):
        with closing(self.module.get_db()) as conn:
            conn.execute("INSERT INTO project_stages(project_id,number,state,reviewed_by) VALUES(2,1,'progress',1)")
            conn.commit()
        focus = self.home_focus()
        self.assertIn('data-home-step="revise"', focus)
        self.assertIn('Комментарий не указан', focus)
        with closing(self.module.get_db()) as conn:
            conn.execute("UPDATE project_stages SET state='done' WHERE project_id=2")
            conn.executemany("INSERT INTO project_stages(project_id,number,state) VALUES(2,?,'done')", [(n,) for n in range(2,6)])
            conn.execute("INSERT INTO project_defenses(project_id,state,teacher_comment) VALUES(2,'returned','   ')")
            conn.commit()
        self.assertIn('Комментарий не указан', self.home_focus())

    def test_project_navigation_is_shared_and_keeps_access_checks(self):
        self.sign_in(3)
        before = self.database_snapshot()
        for suffix in ('', '/team', '/stages', '/defense', '/assessment'):
            page = self.client.get('/project/2' + suffix)
            self.assertEqual(page.status_code, 200)
            nav = page.text.split('<nav class="project-navigation"')[1].split('</nav>')[0]
            for label in ('Обзор', 'Задачи', 'Этапы', 'Команда'):
                self.assertIn(label, nav)
            self.assertEqual(self.client.get('/project/3' + suffix).status_code, 403)
        page = self.client.get('/project/2').text
        for target in ('/supervision', '/project/2/defense', '/project/2/assessment'):
            self.assertIn(f'href="{target}"', page)
        self.assertEqual(self.database_snapshot(), before)
