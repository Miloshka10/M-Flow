"""Integration regressions for the unified stage/task presentation."""
from contextlib import closing
from html.parser import HTMLParser


class WorkParser(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.ids = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if value := dict(attrs).get('id'):
            self.ids.append(value)


class ProjectWorkCases:
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

    def test_work_submission_return_urls_remain_project_scoped(self):
        self.sign_in(3)
        target = '/project/2?mine=1&view_stage=1#stage-1'
        saved = self.post('/project/2/stages', data=dict(action='save',number=1,revision=0,result='Draft',return_to=target))
        self.assertEqual(saved.location, target)
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
