"""Integration regressions for the unified stage/task presentation."""
from contextlib import closing
from html.parser import HTMLParser


class WorkParser(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.ids = []
        self.panels = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if number := attrs.get('data-work-panel'):
            self.panels[number] = attrs
        if value := attrs.get('id'):
            self.ids.append(value)


class ProjectWorkCases:
    def test_work_result_default_follows_review_return_accept_and_keeps_future_tasks(self):
        def default(uid, number=1):
            self.sign_in(uid)
            page = self.client.get('/project/2/stages')
            self.assertEqual(page.status_code, 200)
            return WorkParser(page.text).panels[str(number)]['data-default-view']
        self.assertEqual(default(3), 'tasks')
        self.assertEqual(self.post('/project/2/stages', data=dict(number=1, revision=0, action='submit', result='Первая сдача')).status_code, 302)
        for uid in (1, 3):
            self.assertEqual(default(uid), 'result')
        self.sign_in(1)
        self.assertEqual(self.post('/project/2/stages', data=dict(number=1, revision=1, action='return', teacher_comment='Уточните цель')).status_code, 302)
        self.assertEqual(default(3), 'result')
        self.assertEqual(default(3, 2), 'tasks')
        self.assertEqual(self.post('/project/2/stages', data=dict(number=1, revision=2, action='submit', result='Исправленная сдача')).status_code, 302)
        self.sign_in(1)
        self.assertEqual(self.post('/project/2/stages', data=dict(number=1, revision=3, action='accept')).status_code, 302)
        self.assertEqual(default(3), 'result')
        self.assertEqual(default(3, 2), 'tasks')

    def test_work_resubmission_separates_previous_feedback_from_new_decision_and_queue(self):
        self.sign_in(3)
        self.post('/project/2/stages', data=dict(number=1, revision=0, action='submit', result='Первый результат'))
        self.sign_in(1)
        self.post('/project/2/stages', data=dict(number=1, revision=1, action='return', teacher_comment='Старое замечание <b>'))
        self.sign_in(3)
        self.post('/project/2/stages', data=dict(number=1, revision=2, action='submit', result='Исправленный результат <script>'))
        self.sign_in(1)
        before = self.database_snapshot()
        page = self.client.get('/project/2/stages').text
        old = page.split('<details class="work-previous-feedback">', 1)[1].split('</details>', 1)[0]
        self.assertIn('Старое замечание &lt;b&gt;', old)
        self.assertNotIn('Исправленный результат', old)
        self.assertIn('Исправленный результат &lt;script&gt;', page)
        self.assertIn('Новая версия ещё не проверена', old)
        self.assertRegex(page, r'<textarea\b[^>]*name="teacher_comment"[^>]*></textarea>')
        self.assertIn('aria-describedby="review-comment-hint-1"', page)
        queue = self.queue_html(self.client.get('/teacher').text)
        self.assertIn('Есть предыдущее решение', queue)
        self.assertIn('/project/2/history?work=stage-1', queue)
        self.assertNotIn('Private project', queue)
        self.assertIn('href="/teacher#review-queue">К очереди проверки', page)
        self.assertEqual(self.database_snapshot(), before)

    def test_work_unifies_old_stage_and_task_entries_without_get_writes(self):
        self.sign_in(3)
        before = self.database_snapshot()
        for path in ('/project/2', '/project/2/stages', '/project/2?view_stage=5'):
            page = self.client.get(path)
            self.assertEqual(page.status_code, 200)
            self.assertIn('Работа над проектом', page.text)
            self.assertIn('data-workspace="1"', page.text)
            self.assertNotIn('class="kanban-column"', page.text)
            for n in range(1, 6):
                self.assertIn(f'id="stage-{n}"', page.text)
            ids = WorkParser(page.text).ids
            self.assertEqual(len(ids), len(set(ids)), 'Deep links must have unique DOM targets')
        self.assertEqual(before, self.database_snapshot())

    def test_work_forms_keep_csrf_revision_and_authoritative_stage_endpoint(self):
        self.sign_in(3)
        page = self.client.get('/project/2/stages').text
        self.assertIn('action="/project/2/stages"', page)
        self.assertIn('name="revision" value="0"', page)
        self.assertIn('name="csrf_token"', page)
        self.assertIn('class="pw-editor"', page)
        before = self.database_snapshot()
        self.assertEqual(self.client.post('/project/2/stages', data=dict(action='submit', number=1, revision=0, result='No CSRF')).status_code, 400)
        self.assertEqual(before, self.database_snapshot())

    def test_work_stage_validation_retains_text_in_current_form(self):
        self.sign_in(3)
        page = self.post('/project/2/stages', data=dict(action='submit',number=1,revision=0,result='',return_to='/project/2?mine=1&view_stage=1#stage-1'))
        self.assertEqual(page.status_code, 400)
        self.assertIn('data-work-default="1"', page.text)
        self.assertIn('class="pw-editor" open', page.text)
        self.assertIn('Для проверки заполните результат', page.text)
        self.assertIn('name="mine" value="1"', page.text)
        self.assertIn('name="view_stage" value="1"', page.text)

    def test_work_submission_return_urls_remain_project_scoped(self):
        self.sign_in(3)
        target = '/project/2?mine=1&view_stage=1#stage-1'
        saved = self.post('/project/2/stages', data=dict(action='save',number=1,revision=0,result='Draft',return_to=target))
        self.assertEqual(saved.location, target)
        saved_page = self.client.get(saved.location).text
        self.assertIn('data-project-feedback', saved_page)
        self.assertIn('Результат этапа сохранён.', saved_page)
        for malicious in ('https://evil.example', '/project/3#stage-1', '//evil.example'):
            response = self.post('/project/2/stages', data=dict(action='save',number=1,revision=1 if malicious.startswith('https:') else 2 if malicious.startswith('/project') else 3,result='Draft',return_to=malicious))
            self.assertEqual(response.location, '/project/2/stages')

    def test_work_stage_selection_is_validated_and_does_not_filter_other_tasks(self):
        self.sign_in(3)
        with closing(self.module.get_db()) as conn:
            conn.execute("INSERT INTO tasks(id,project_id,title,stage_number,assignee_id) VALUES(22,2,'Future plan',5,3)")
            conn.commit()
        page = self.client.get('/project/2?view_stage=5').text
        self.assertIn('data-selected-stage="5"', page)
        self.assertIn('id="task-3"', page)
        self.assertIn('id="task-22"', page)
        self.assertIn('Откроется после принятия', page)
        for value in ('0', '6', '01', '<script>'):
            self.assertIn('data-selected-stage="1"', self.client.get('/project/2?view_stage='+value).text)

    def test_work_teacher_cannot_manage_task_or_submit_student_result(self):
        self.sign_in(1)
        page = self.client.get('/project/2/stages').text
        self.assertNotIn('class="task-status-select"', page)
        self.assertNotIn('action="/project/2/add"', page)
        self.assertNotIn('class="pw-editor"', page)
        before = self.database_snapshot()
        self.assertEqual(self.post('/project/2/stages',data=dict(action='save',number=1,revision=0,result='Teacher write')).status_code,400)
        self.assertEqual(before,self.database_snapshot())

    def test_work_foreign_project_and_task_details_stay_hidden(self):
        self.sign_in(2)
        for path in ('/project/2/stages', '/project/2?view_stage=1'):
            response = self.client.get(path)
            self.assertEqual(response.status_code,403)
            self.assertNotIn('data-workspace="1"',response.text)
